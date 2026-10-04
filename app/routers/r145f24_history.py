"""Warehouse history/report routes. All business-table access is read-only.

Export jobs write only temporary XLSX/JSON files. No migrations, reset, release,
allocation, receipt cancellation, stock reconciliation or marketplace API calls.
"""
from __future__ import annotations
import json, logging, os, tempfile, threading, time, uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal
from urllib.parse import quote
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse
from sqlalchemy import text
from sqlalchemy.orm import Session
from app.core.database import get_db, SessionLocal
from app.services.r145f24_history import (physical_row,reservation_row,history_workbook,
    parse_utc,iso,stamp,qty,number)

router=APIRouter(prefix='/r145f24',tags=['History MSK / FBS zero check'])
log=logging.getLogger(__name__)
Mode=Literal['MOVEMENTS','TECHNICAL']
MAX_EXPORT=100000
TECH="((l.source_location_id IS NULL AND l.destination_location_id IS NULL) OR (o.operation_type='RECONCILIATION' AND l.source_location_id IS NOT NULL AND l.destination_location_id IS NOT NULL AND l.source_location_id=l.destination_location_id AND COALESCE(CAST(l.source_container_id AS text),'')=COALESCE(CAST(l.destination_container_id AS text),'')))"
CORE_FROM="""FROM inventory_operations o
 JOIN inventory_operation_lines l ON l.operation_id=o.id
 JOIN skus s ON s.id=l.sku_id
 LEFT JOIN locations sl ON sl.id=l.source_location_id
 LEFT JOIN locations dl ON dl.id=l.destination_location_id
 LEFT JOIN containers sc ON sc.id=l.source_container_id
 LEFT JOIN containers dc ON dc.id=l.destination_container_id"""
CORE_SELECT="""SELECT CAST(o.id AS text) operation_id, o.operation_type,o.status,o.performed_by,o.reference,
 o.idempotency_key,o.comment,o.created_at,CAST(l.id AS text) line_id,CAST(l.sku_id AS text) sku_id,
 l.quantity,l.reason,s.article,s.name sku_name,s.size,s.barcode,
 CAST(l.source_location_id AS text) source_location_id,sl.code source_location,
 CAST(l.destination_location_id AS text) destination_location_id,dl.code destination_location,
 CAST(l.source_container_id AS text) source_container_id,COALESCE(sc.name,sc.barcode) source_container,
 CAST(l.destination_container_id AS text) destination_container_id,COALESCE(dc.name,dc.barcode) destination_container"""

def begin_read(db):
    db.execute(text('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY'))
    db.execute(text("SET LOCAL statement_timeout='60s'"))
    db.execute(text("SET LOCAL lock_timeout='3s'"))

def login(request):
    owner=getattr(request.state,'username',None)
    if not owner:raise HTTPException(403,detail='\u0422\u0440\u0435\u0431\u0443\u0435\u0442\u0441\u044f \u0432\u0445\u043e\u0434')
    return str(owner)

def scope(db,c,w):
    c=uuid.UUID(str(c));w=uuid.UUID(str(w))
    client=db.execute(text('SELECT name FROM clients WHERE id=:c'),{'c':str(c)}).scalar()
    warehouse=db.execute(text('SELECT name FROM warehouses WHERE id=:w'),{'w':str(w)}).scalar()
    if client is None or warehouse is None:raise HTTPException(404,detail='\u041a\u043b\u0438\u0435\u043d\u0442/\u0441\u043a\u043b\u0430\u0434 \u043d\u0435 \u043d\u0430\u0439\u0434\u0435\u043d')
    return str(client),str(warehouse)

def exists(db,table):return bool(db.execute(text('SELECT to_regclass(:t) IS NOT NULL'),{'t':table}).scalar())

def physical_query(db,c,w,mode,filters,limit):
    where=['o.client_id=:c','o.warehouse_id=:w',TECH if mode=='TECHNICAL' else 'NOT '+TECH]
    p={'c':str(c),'w':str(w),'n':limit}
    for key,expr in [('barcode','s.barcode=:barcode'),('location_id','(l.source_location_id=:location_id OR l.destination_location_id=:location_id)'),('container_id','(l.source_container_id=:container_id OR l.destination_container_id=:container_id)')]:
        if filters.get(key):where.append(expr);p[key]=str(filters[key])
    if filters.get('order_id'):where.append("COALESCE(o.reference,'') ILIKE :ord");p['ord']='%'+filters['order_id']+'%'
    if filters.get('operation_type') and filters['operation_type'] not in ('RECEIVING_ALL','ALL'):
        where.append('o.operation_type=:typ');p['typ']=filters['operation_type']
    for key,expr in [('from_at','o.created_at>=:from_at'),('to_at','o.created_at<:to_at')]:
        if filters.get(key):where.append(expr);p[key]=parse_utc(filters[key])
    sql=CORE_SELECT+' '+CORE_FROM+' WHERE '+' AND '.join(where)+' ORDER BY o.created_at DESC,l.id DESC LIMIT :n'
    rows=[dict(r) for r in db.execute(text(sql),p).mappings().all()]
    return [physical_row(r) for r in rows]

def add_scan_times(db,c,w,rows):
    # At most two scope-batched reads; no query per history line.
    keys={r.get('idempotency_key') for r in rows if str(r.get('idempotency_key') or '').startswith('R145F19:TSD:')}
    if keys and exists(db,'fc_r145f19_tsd_receipts') and exists(db,'fc_r145f19_tsd_lines') and exists(db,'fc_r145f19_tsd_places'):
        raw=db.execute(text("""SELECT 'R145F19:TSD:'||CAST(t.id AS text) idem,
          CAST(l.sku_id AS text) sku_id,CAST(p.location_id AS text) location_id,
          COALESCE(CAST(p.container_id AS text),'') container_id,
          MIN(l.scanned_at) first_at,MAX(l.scanned_at) last_at
          FROM fc_r145f19_tsd_receipts t JOIN fc_r145f19_tsd_lines l ON l.receipt_id=t.id
          JOIN fc_r145f19_tsd_places p ON p.id=l.place_id
          WHERE t.client_id=:c AND t.warehouse_id=:w
          GROUP BY t.id,l.sku_id,p.location_id,p.container_id"""),{'c':str(c),'w':str(w)}).mappings().all()
        times={(r['idem'],r['sku_id'],r['location_id'],r['container_id']):r for r in raw}
        for r in rows:
            key=(r.get('idempotency_key'),r.get('sku_id'),r.get('destination_location_id'),r.get('destination_container_id') or '')
            t=times.get(key)
            if t:
                for field,col in [('first_scan_at','first_at'),('last_scan_at','last_at')]:r[field]=iso(t[col]);r[field+'_msk']=stamp(t[col])
    if any(str(r.get('idempotency_key') or '').startswith('tsd-work-') for r in rows) and exists(db,'fc_tsd_work'):
        raw=db.execute(text('SELECT id,data FROM fc_tsd_work WHERE client_id=:c AND warehouse_id=:w'),{'c':str(c),'w':str(w)}).mappings().all()
        works={'tsd-work-'+str(r['id']):r['data'] for r in raw}
        for r in rows:
            t=works.get(r.get('idempotency_key'))
            if isinstance(t,str):
                try:t=json.loads(t)
                except (ValueError,TypeError):t={}
            if not isinstance(t,dict):continue
            lines=t.get('lines') or {};line=lines.get(r.get('sku_id'),{}) if isinstance(lines,dict) else {}
            if not isinstance(line,dict):line={}
            for field,col,boxcol in [('first_scan_at','first_scanned_at','first_item_at'),('last_scan_at','last_scanned_at','last_item_at')]:
                v=line.get(col) or t.get(boxcol);r[field]=iso(v);r[field+'_msk']=stamp(v)

def reserve_query(db,c,w,filters,limit):
    if filters.get('operation_type') not in (None,'','ALL','RECEIVING_ALL','RESERVATION_STATE'):return []
    if not exists(db,'fc_stock_reservations'):return []
    where=['r.client_id=:c','r.warehouse_id=:w'];p={'c':str(c),'w':str(w),'n':limit}
    for key,expr in [('barcode','s.barcode=:barcode'),('location_id','r.source_location_id=:location_id'),('container_id','r.source_container_id=:container_id')]:
        if filters.get(key):where.append(expr);p[key]=str(filters[key])
    if filters.get('order_id'):where.append("COALESCE(r.external_order_id,o.external_order_id,'') ILIKE :ord");p['ord']='%'+filters['order_id']+'%'
    for key,expr in [('from_at','r.updated_at>=:from_at'),('to_at','r.updated_at<:to_at')]:
        if filters.get(key):where.append(expr);p[key]=parse_utc(filters[key])
    sql="""SELECT CAST(r.id AS text) reservation_id,CAST(r.sku_id AS text) sku_id,
      r.status,r.quantity,r.note,r.updated_at,r.created_at reservation_created_at,r.consumed_at,r.released_at,
      CAST(r.source_location_id AS text) source_location_id,sl.code source_location,
      CAST(r.source_container_id AS text) source_container_id,COALESCE(sc.name,sc.barcode) source_container,
      s.barcode,s.article,s.size,s.name sku_name,COALESCE(r.external_order_id,o.external_order_id) reference,o.local_status order_status
      FROM fc_stock_reservations r LEFT JOIN skus s ON CAST(s.id AS text)=r.sku_id
      LEFT JOIN locations sl ON CAST(sl.id AS text)=r.source_location_id
      LEFT JOIN containers sc ON CAST(sc.id AS text)=r.source_container_id
      LEFT JOIN marketplace_orders o ON CAST(o.id AS text)=r.order_id
      WHERE """+' AND '.join(where)+' ORDER BY r.updated_at DESC,r.id DESC LIMIT :n'
    return [reservation_row(dict(r)) for r in db.execute(text(sql),p).mappings().all()]

def read_history(db,c,w,mode='MOVEMENTS',filters=None,limit=500,offset=0,export=False):
    if mode not in ('MOVEMENTS','TECHNICAL'):raise ValueError('BAD_HISTORY_MODE')
    filters=filters or {}
    if filters.get('from_at') and filters.get('to_at') and parse_utc(filters['from_at'])>=parse_utc(filters['to_at']):raise HTTPException(422,detail='INVALID_DATE_RANGE')
    names=scope(db,c,w);n=min(MAX_EXPORT+1,offset+limit+1)
    rows=physical_query(db,c,w,mode,filters,n)
    if mode=='TECHNICAL':rows+=reserve_query(db,c,w,filters,n)
    rows.sort(key=lambda r:(r.get('created_at') or '',str(r.get('line_id') or r.get('reservation_id') or '')),reverse=True)
    more=len(rows)>offset+limit
    if export and more:raise ValueError('HISTORY_EXPORT_TOO_LARGE_USE_DATES')
    rows=rows[offset:offset+limit]
    add_scan_times(db,c,w,rows)
    return {'rows':rows,'has_more':more,'offset':offset,'limit':limit,'mode':mode,
       'client':names[0],'warehouse':names[1],'timezone':'Europe/Moscow','read_only':True,
       'generated_at':datetime.now(timezone.utc).isoformat()}

def zero_check(db,c,w):
    names=scope(db,c,w);p={'c':str(c),'w':str(w)}
    raw=db.execute(text('''SELECT CAST(sku_id AS text) sku_id,CAST(location_id AS text) location_id,
      COALESCE(CAST(container_id AS text),'') container_key,quantity
      FROM inventory_balances WHERE client_id=:c AND warehouse_id=:w'''),p).mappings().all()
    mirrors=db.execute(text('''SELECT CAST(sku_id AS text) sku_id,CAST(location_id AS text) location_id,
      container_key,storage_only,fbs_available,reserved
      FROM fc_stocks_v2 WHERE client_id=:c AND warehouse_id=:w'''),p).mappings().all()
    key=lambda r:(r['sku_id'],r['location_id'],r['container_key'] or '')
    mm={key(r):r for r in mirrors}
    if len(mm)!=len(mirrors):raise ValueError('DUPLICATE_STOCK_MIRROR')
    seen=set();physical=qty(0);main=qty(0);fbs=qty(0);problems=[];fbs_by={}
    for r in raw:
        k=key(r)
        if k in seen:raise ValueError('DUPLICATE_PHYSICAL_BUCKET')
        seen.add(k);n=qty(r['quantity']);physical+=n;m=mm.get(k)
        if m is None:
            if n:problems.append({'sku_id':r['sku_id'],'code':'CLASSIFICATION_MISSING','quantity':number(n)})
            continue
        s,a,h=map(qty,(m['storage_only'],m['fbs_available'],m['reserved']))
        if s+a+h!=n:problems.append({'sku_id':r['sku_id'],'code':'MIRROR_MISMATCH','quantity':number(n)})
        s=min(s,n);main+=s;fbs+=n-s;fbs_by[r['sku_id']]=fbs_by.get(r['sku_id'],qty(0))+n-s
    for k,m in mm.items():
        if k not in seen and sum((qty(m[x]) for x in ('storage_only','fbs_available','reserved')),qty(0)):
            problems.append({'sku_id':m['sku_id'],'code':'MIRROR_WITHOUT_PHYSICAL','quantity':0})
    rr=db.execute(text('''SELECT CAST(r.sku_id AS text) sku_id,CAST(r.id AS text) id,r.status,r.quantity,r.note,
      r.source_location_id,o.local_status,o.external_order_id FROM fc_stock_reservations r
      LEFT JOIN marketplace_orders o ON CAST(o.id AS text)=r.order_id
      WHERE r.client_id=:c AND r.warehouse_id=:w
      AND r.status IN ('SALE_RESERVED','ACTIVE','CONSUMING','CANCELLING','PACKING_WRITE_OFF')'''),p).mappings().all()
    reset_promises=[];no_stock=qty(0)
    for r in rr:
        if fbs_by.get(r['sku_id'],qty(0))==0:
            no_stock+=qty(r['quantity'])
            if 'FBS_CLEAN_RESET' in str(r['note'] or ''):
                reset_promises.append({'order_id':str(r['external_order_id'] or ''),'quantity':number(qty(r['quantity'])),'status':r['status']})
    latest=db.execute(text('''SELECT MAX(o.created_at) FROM inventory_operations o
      WHERE o.client_id=:c AND o.warehouse_id=:w AND COALESCE(o.status,'COMPLETED')='COMPLETED'
      AND EXISTS (SELECT 1 FROM inventory_operation_lines l WHERE l.operation_id=o.id
      AND l.reason='FBS_CLEAN_RESET_TO_ZERO')'''),p).scalar()
    ok=not problems
    return {'client':names[0],'warehouse':names[1],'classification_ok':ok,
      'physical_total':number(physical),'main_physical':number(main) if ok else None,
      'fbs_physical':number(fbs) if ok else None,'zero_verified':ok and fbs==0,
      'physical_fbs_reserved':0 if ok and fbs==0 else None,
      'unbacked_logical_units':number(no_stock),'old_reset_promises':reset_promises,
      'problems':problems[:50],'problems_total':len(problems),'last_reset_at':iso(latest),'last_reset_msk':stamp(latest),
      'read_only':True,'main_changed':False,'checked_at':iso(datetime.now(timezone.utc))}

@router.get('/history')
def history(request:Request,client_id:uuid.UUID,warehouse_id:uuid.UUID,mode:Mode='MOVEMENTS',
    limit:int=Query(500,ge=1,le=2000),offset:int=Query(0,ge=0,le=98000),
    barcode:str=Query('',max_length=120),order_id:str=Query('',max_length=160),
    location_id:uuid.UUID|None=None,container_id:uuid.UUID|None=None,
    operation_type:str=Query('',max_length=60),from_at:datetime|None=None,to_at:datetime|None=None,
    db:Session=Depends(get_db)):
    login(request)
    try:
        begin_read(db)
        return read_history(db,client_id,warehouse_id,mode,
          dict(barcode=barcode,order_id=order_id,location_id=location_id,container_id=container_id,
               operation_type=operation_type,from_at=from_at,to_at=to_at),limit,offset)
    except HTTPException:raise
    except Exception:
        log.exception('R145F24_HISTORY_READ_FAILED')
        raise HTTPException(503,detail='\u0418\u0441\u0442\u043e\u0440\u0438\u044f \u043d\u0435 \u0437\u0430\u0433\u0440\u0443\u0436\u0435\u043d\u0430. \u0414\u0430\u043d\u043d\u044b\u0435 \u043d\u0435 \u0438\u0437\u043c\u0435\u043d\u0435\u043d\u044b; \u043f\u043e\u0432\u0442\u043e\u0440\u0438\u0442\u0435 \u0437\u0430\u043f\u0440\u043e\u0441.')
    finally:db.rollback()

@router.get('/fbs/zero-check')
def check_fbs(request:Request,client_id:uuid.UUID,warehouse_id:uuid.UUID,db:Session=Depends(get_db)):
    login(request)
    try:begin_read(db);return zero_check(db,client_id,warehouse_id)
    except HTTPException:raise
    except Exception:
        log.exception('R145F24_ZERO_READ_FAILED')
        raise HTTPException(503,detail='\u041f\u0440\u043e\u0432\u0435\u0440\u043a\u0430 FBS \u043d\u0435 \u0432\u044b\u043f\u043e\u043b\u043d\u0435\u043d\u0430. \u041d\u043e\u043b\u0435\u0432\u043e\u0439 \u043e\u0441\u0442\u0430\u0442\u043e\u043a \u043d\u0435 \u043f\u043e\u0434\u0442\u0432\u0435\u0440\u0436\u0434\u0451\u043d.')
    finally:db.rollback()

# Job artifacts survive requests/process routing within the same API container.
# Only reports are cached; a new export creates a new snapshot, never a stale report.
JOB_ROOT=Path(tempfile.gettempdir())/'fulfillcore-r145f24-reports'
_POOL=ThreadPoolExecutor(max_workers=1,thread_name_prefix='fc-history-r24')
_GATE=threading.BoundedSemaphore(2)

def job_path(job_id,suffix):
    ident=str(uuid.UUID(str(job_id)))
    return JOB_ROOT/(ident+suffix)

def save_meta(meta):
    JOB_ROOT.mkdir(mode=0o700,parents=True,exist_ok=True)
    dst=job_path(meta['id'],'.json');tmp=dst.with_suffix('.json.tmp')
    tmp.write_text(json.dumps(meta,ensure_ascii=True));os.chmod(tmp,0o600);os.replace(tmp,dst)

def read_meta(job_id,owner,c,w):
    try:m=json.loads(job_path(job_id,'.json').read_text())
    except (OSError,ValueError):raise HTTPException(404,detail='\u0412\u044b\u0433\u0440\u0443\u0437\u043a\u0430 \u043d\u0435 \u043d\u0430\u0439\u0434\u0435\u043d\u0430; \u0441\u043e\u0437\u0434\u0430\u0439\u0442\u0435 \u043d\u043e\u0432\u0443\u044e')
    if m['owner']!=owner or m['client_id']!=str(c) or m['warehouse_id']!=str(w):raise HTTPException(404,detail='REPORT_SCOPE_MISMATCH')
    if time.time()-m['created']>3600:raise HTTPException(410,detail='\u0421\u0440\u043e\u043a \u0432\u044b\u0433\u0440\u0443\u0437\u043a\u0438 \u0438\u0441\u0442\u0451\u043a; \u0441\u043e\u0437\u0434\u0430\u0439\u0442\u0435 \u043d\u043e\u0432\u0443\u044e')
    return m

def cleanup_jobs():
    if not JOB_ROOT.exists():return
    for p in JOB_ROOT.glob('*.json'):
        try:
            m=json.loads(p.read_text())
            if time.time()-m['created']>3600:
                job_path(m['id'],'.xlsx').unlink(missing_ok=True);p.unlink(missing_ok=True)
        except (OSError,ValueError,KeyError):continue

def run_job(meta,filters):
    try:
        meta.update(status='RUNNING',stage='\u0427\u0442\u0435\u043d\u0438\u0435 \u0438\u0441\u0442\u043e\u0440\u0438\u0438');save_meta(meta)
        with SessionLocal() as db:
            try:
                begin_read(db);report=read_history(db,meta['client_id'],meta['warehouse_id'],meta['mode'],filters,MAX_EXPORT,0,True)
            finally:db.rollback()
        meta.update(stage='\u0424\u043e\u0440\u043c\u0438\u0440\u0443\u0435\u043c Excel');save_meta(meta)
        data=history_workbook(report['rows'],client=report['client'],warehouse=report['warehouse'],mode=meta['mode'],generated_at=report['generated_at'])
        path=job_path(meta['id'],'.xlsx');tmp=path.with_suffix('.xlsx.tmp');tmp.write_bytes(data);os.chmod(tmp,0o600);os.replace(tmp,path)
        meta.update(status='READY',stage='\u0413\u043e\u0442\u043e\u0432\u043e',rows=len(report['rows']),bytes=len(data));save_meta(meta)
    except Exception:
        log.exception('R145F24_HISTORY_EXPORT_FAILED')
        meta.update(status='FAILED',error='\u0412\u044b\u0433\u0440\u0443\u0437\u043a\u0430 \u043d\u0435 \u0441\u043e\u0437\u0434\u0430\u043d\u0430. \u041f\u043e\u0432\u0442\u043e\u0440\u0438\u0442\u0435 \u0438\u043b\u0438 \u0441\u0443\u0437\u044c\u0442\u0435 \u043f\u0435\u0440\u0438\u043e\u0434 (\u043b\u0438\u043c\u0438\u0442 100000 \u0441\u0442\u0440\u043e\u043a).')
        save_meta(meta)
    finally:_GATE.release()

@router.post('/history/export-jobs')
def export_start(request:Request,client_id:uuid.UUID,warehouse_id:uuid.UUID,mode:Mode='MOVEMENTS',
    barcode:str=Query('',max_length=120),order_id:str=Query('',max_length=160),location_id:uuid.UUID|None=None,
    container_id:uuid.UUID|None=None,operation_type:str=Query('',max_length=60),from_at:datetime|None=None,to_at:datetime|None=None,
    db:Session=Depends(get_db)):
    owner=login(request)
    try:begin_read(db);scope(db,client_id,warehouse_id)
    finally:db.rollback()
    if not _GATE.acquire(blocking=False):raise HTTPException(429,detail='\u0414\u043e\u0436\u0434\u0438\u0442\u0435\u0441\u044c \u0442\u0435\u043a\u0443\u0449\u0435\u0439 \u0432\u044b\u0433\u0440\u0443\u0437\u043a\u0438')
    try:
        cleanup_jobs();ident=str(uuid.uuid4());meta={'id':ident,'owner':owner,'client_id':str(client_id),'warehouse_id':str(warehouse_id),'mode':mode,'created':time.time(),'status':'QUEUED'}
        save_meta(meta)
        _POOL.submit(run_job,meta,dict(barcode=barcode,order_id=order_id,location_id=location_id,container_id=container_id,operation_type=operation_type,from_at=from_at,to_at=to_at))
    except Exception:_GATE.release();raise
    return {'job_id':ident,'status':'QUEUED','mode':mode}

@router.get('/history/export-jobs/{job_id}')
def export_status(job_id:uuid.UUID,request:Request,client_id:uuid.UUID,warehouse_id:uuid.UUID):
    m=read_meta(job_id,login(request),client_id,warehouse_id)
    return {k:m.get(k) for k in ('status','stage','error','rows','bytes','mode')}

@router.get('/history/export-jobs/{job_id}/download')
def export_download(job_id:uuid.UUID,request:Request,client_id:uuid.UUID,warehouse_id:uuid.UUID):
    m=read_meta(job_id,login(request),client_id,warehouse_id)
    if m['status']!='READY':raise HTTPException(409,detail='REPORT_NOT_READY')
    return FileResponse(job_path(job_id,'.xlsx'),media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
      headers={'Cache-Control':'no-store','Content-Disposition':"attachment; filename*=UTF-8''"+quote('History_MSK.xlsx')})
