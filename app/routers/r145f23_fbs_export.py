"""FBS Excel with stock/reservations from one read-only database snapshot."""
from __future__ import annotations
import logging
import uuid
from datetime import datetime, timezone
from typing import Literal
from urllib.parse import quote
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import Response
from sqlalchemy import text
from sqlalchemy.orm import Session
from app.core.database import get_db
from app.models import Client, Warehouse
from app.services import stock_core_v2, wb_sale_status, wb_sales_control
from app.routers.warehouse_ui import stock_rows, stock_place_routes
from app.services.r145f23_fbs_report import prepare, workbook_bytes

router = APIRouter(prefix='/r145f23', tags=['FBS Excel'])
log=logging.getLogger(__name__)

class ReadOnly:
    """Retain a failed-read flag even when a legacy reader swallows SQL errors."""
    def __init__(self,raw):self.raw=raw;self.failed=False
    def __getattr__(self,name):return getattr(self.raw,name)
    def execute(self,*args,**kwargs):
        try:return self.raw.execute(*args,**kwargs)
        except Exception:
            self.failed=True
            raise
    def commit(self):
        self.failed=True
        raise RuntimeError('FBS_EXPORT_COMMIT_FORBIDDEN')
    def rollback(self):
        self.failed=True
        raise RuntimeError('FBS_EXPORT_HELPER_ROLLBACK_FORBIDDEN')
    def check(self):
        if self.failed:raise RuntimeError('FBS_EXPORT_INCOMPLETE_READ')
        self.raw.execute(text('SELECT 1'))

# Validate the precise reader contract, not a best-effort zero for missing tables.
SCHEMA_READS=(
 'SELECT sku_id,quantity,location_id,container_id,client_id,warehouse_id FROM inventory_balances LIMIT 0',
 'SELECT client_id,warehouse_id,sku_id,location_id,container_key,fbs_available,storage_only,reserved FROM fc_stocks_v2 LIMIT 0',
 'SELECT sku_id,order_id,quantity,status,source_location_id,client_id,warehouse_id FROM fc_stock_reservations LIMIT 0',
 'SELECT id,client_id,local_status FROM marketplace_orders LIMIT 0',
 'SELECT sku_id,quantity,status,client_id,warehouse_id FROM fc_external_pick_allocations LIMIT 0',
)

def read_report(db,client_id,warehouse_id,search='',sale_tab='ALL'):
    client=db.get(Client,client_id);warehouse=db.get(Warehouse,warehouse_id)
    if not client or not warehouse:raise HTTPException(404,detail='\u041a\u043b\u0438\u0435\u043d\u0442 \u0438\u043b\u0438 \u0441\u043a\u043b\u0430\u0434 \u043d\u0435 \u043d\u0430\u0439\u0434\u0435\u043d')
    for sql in SCHEMA_READS:db.execute(text(sql))
    if not getattr(stock_core_v2,'_SCHEMA_READY',False):
        raise RuntimeError('FBS_EXPORT_STOCK_CORE_NOT_READY')
    physical=stock_place_routes(db,client_id,warehouse_id,stock_rows(db,client_id,warehouse_id,'FBS'))
    if len(physical)>20000:raise ValueError('FBS_EXPORT_TOO_MANY_PRODUCTS')
    ids=sorted({str(s) for row in physical for s in row.get('sku_ids',[])})
    # Uses only local observations/policy/queue state. Never calls read_wb().
    sales=wb_sale_status.snapshot(db,client_id,warehouse_id,ids)
    truth=stock_core_v2.stock_truth_for_skus(db,client_id=client_id,warehouse_id=warehouse_id,
        sku_ids=ids,wb_warehouse_id=sales.get('wb_warehouse_id'),include_movement=False)
    aliases={str(s) for t in truth.values() for s in t.get('identity_sku_ids',[])}
    sales['paused_sku_ids']=sorted(wb_sales_control.paused_ids(db,client_id,warehouse_id,aliases))
    db.check()
    return prepare(physical,truth,sales,client=str(client.name),warehouse=str(warehouse.name),
        search=search,sale_tab=sale_tab,generated_at=datetime.now(timezone.utc))

@router.get('/fbs/export.xlsx')
def fbs_export(request:Request,client_id:uuid.UUID,warehouse_id:uuid.UUID,
    search:str=Query(default='',max_length=300),
    sale_tab:Literal['ALL','ACTIVE','PAUSED','ATTENTION']='ALL',db:Session=Depends(get_db)):
    if not getattr(request.state,'username',None):
        raise HTTPException(403,detail='\u0422\u0440\u0435\u0431\u0443\u0435\u0442\u0441\u044f \u0432\u0445\u043e\u0434 \u0432 \u043f\u0440\u043e\u0433\u0440\u0430\u043c\u043c\u0443')
    try:
        # A new dependency Session is used; no prior business transaction is committed.
        db.execute(text('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY'))
        db.execute(text("SET LOCAL statement_timeout='60s'"))
        db.execute(text("SET LOCAL lock_timeout='3s'"))
        guarded=ReadOnly(db)
        report=read_report(guarded,client_id,warehouse_id,search,sale_tab)
        guarded.check()
    except HTTPException:
        raise
    except Exception:
        log.exception('R145F23_FBS_EXPORT_READ_FAILED')
        raise HTTPException(503,detail='\u041d\u0435 \u0443\u0434\u0430\u043b\u043e\u0441\u044c \u043f\u0440\u043e\u0432\u0435\u0440\u0438\u0442\u044c \u043e\u0441\u0442\u0430\u0442\u043a\u0438 \u0438 \u0440\u0435\u0437\u0435\u0440\u0432 FBS. \u0424\u0430\u0439\u043b \u0441 \u043d\u0435\u043f\u043e\u043b\u043d\u044b\u043c\u0438 \u0434\u0430\u043d\u043d\u044b\u043c\u0438 \u043d\u0435 \u0441\u043e\u0437\u0434\u0430\u043d. \u041f\u043e\u0432\u0442\u043e\u0440\u0438\u0442\u0435 \u0432\u044b\u0433\u0440\u0443\u0437\u043a\u0443.')
    finally:
        db.rollback()
    try:
        # Release the snapshot connection before building/compressing the workbook.
        data=workbook_bytes(report)
    except Exception:
        log.exception('R145F23_FBS_EXPORT_XLSX_FAILED')
        raise HTTPException(503,detail='\u041d\u0435 \u0443\u0434\u0430\u043b\u043e\u0441\u044c \u0441\u0444\u043e\u0440\u043c\u0438\u0440\u043e\u0432\u0430\u0442\u044c Excel FBS. \u041e\u0441\u0442\u0430\u0442\u043a\u0438 \u043d\u0435 \u0438\u0437\u043c\u0435\u043d\u0435\u043d\u044b.')
    filename='\u041e\u0441\u0442\u0430\u0442\u043a\u0438_WB_FBS.xlsx'
    return Response(content=data,media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        headers={'Content-Disposition':"attachment; filename*=UTF-8''"+quote(filename),'Cache-Control':'no-store',
                 'X-FulfillCore-Report':'r145f23','X-FulfillCore-Rows':str(len(report['rows']))})
