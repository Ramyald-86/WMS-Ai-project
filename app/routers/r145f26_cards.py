"""Authenticated local-card editor. No warehouse quantities are mutated here."""
import uuid
from typing import Literal
from fastapi import APIRouter,Depends,Request,HTTPException
from pydantic import BaseModel,Field
from sqlalchemy import text
from sqlalchemy.orm import Session
from app.core.database import get_db
from app.services import r145f26_cards as service
router=APIRouter(prefix='/r145f26/catalog',tags=['\u041b\u043e\u043a\u0430\u043b\u044c\u043d\u044b\u0435 \u043a\u0430\u0440\u0442\u043e\u0447\u043a\u0438'])
class CardWrite(BaseModel):
 client_id:uuid.UUID
 warehouse_id:uuid.UUID
 barcode:str=Field(min_length=1,max_length=80)
 article:str=Field(min_length=1,max_length=120)
 size:str=Field(min_length=1,max_length=80)
 name:str=Field(default='',max_length=200)
 category:str=Field(min_length=1,max_length=160)
 mode:Literal['create','edit']='create'
 sku_id:uuid.UUID|None=None
 fingerprint:str|None=Field(default=None,min_length=64,max_length=64)
 restore_confirmed:bool=False
 request_id:uuid.UUID

def actor(request):
 u=getattr(request.state,'username',None)
 if not u:raise HTTPException(403,detail='\u0422\u0440\u0435\u0431\u0443\u0435\u0442\u0441\u044f \u0432\u0445\u043e\u0434 \u0432 \u043f\u0440\u043e\u0433\u0440\u0430\u043c\u043c\u0443')
 return str(u)
@router.get('/lookup')
def lookup(client_id:uuid.UUID,warehouse_id:uuid.UUID,barcode_value:str,request:Request,db:Session=Depends(get_db)):
 actor(request);return service.lookup(db,client_id,warehouse_id,barcode_value)
@router.post('/save')
def save(payload:CardWrite,request:Request,db:Session=Depends(get_db)):
 u=actor(request)
 try:return service.save(db,payload,u)
 except Exception:
  db.rollback();raise
@router.get('/list')
def listing(client_id:uuid.UUID,warehouse_id:uuid.UUID,request:Request,search:str='',limit:int=5000,db:Session=Depends(get_db)):
 actor(request);service.scope(db,client_id,warehouse_id)
 from app.routers.r97_local import local_products
 data=local_products(client_id=client_id,search=search,limit=limit,db=db)
 # A manual name correction takes precedence only in the LOCAL card view.
 # MarketplaceProduct and the WB card are never edited.
 edited={str(uuid.UUID(str(x))) for x in db.execute(text("SELECT DISTINCT sku_id FROM fc_r145f26_product_audit WHERE client_id=:c AND action IN ('EDIT','RESTORE')"),{'c':service.dbid(client_id)}).scalars()}
 for r in data.get('rows',[]):
  if str(r.get('id')) in edited:r['title']=r.get('name') or r.get('article') or r.get('title');r['manual_edited']=True
 return data


@router.post('/receive')
def receive(payload:dict,request:Request,db:Session=Depends(get_db)):
    """Reuse the existing stock operation. Protect only manually edited metadata
    during its WB-enrichment step; do not rewrite any stock/reservation logic."""
    actor(request)
    from sqlalchemy import select,event
    from pydantic import ValidationError
    from app.models import SKU,InventoryOperation
    from app.routers.receiving import ManualReceiveBatchRequest,manual_receive_batch
    try:p=ManualReceiveBatchRequest.model_validate(payload)
    except ValidationError:raise HTTPException(422,detail={'code':'INVALID_RECEIPT','safe_to_edit':True,'message':'\u041f\u0440\u043e\u0432\u0435\u0440\u044c\u0442\u0435 \u043f\u043e\u043b\u044f \u043f\u0440\u0438\u0451\u043c\u043a\u0438.'})
    service.scope(db,p.client_id,p.warehouse_id)
    codes={service.normalize_barcode(row.barcode) for row in p.lines}
    # Use the same advisory locks as both creators and the existing receiving flow.
    if db.bind.dialect.name=='postgresql':
        for code in sorted(codes):db.execute(text('SELECT pg_advisory_xact_lock(hashtext(:k))'),{'k':f'fc:local-sku:{p.client_id}:{code}'})
    skus=db.execute(select(SKU).where(SKU.client_id==p.client_id,SKU.barcode.in_(codes)).with_for_update()).scalars().all()
    audited={str(uuid.UUID(str(x))) for x in db.execute(text("SELECT DISTINCT sku_id FROM fc_r145f26_product_audit WHERE client_id=:c AND action IN ('CREATE','EDIT','RESTORE')"),{'c':service.dbid(p.client_id)}).scalars()}
    protected={s.id:(s.article,s.size,s.name) for s in skus if str(s.id) in audited}
    def preserve(session,flush_context,instances):
        for obj in list(session.dirty):
            if isinstance(obj,SKU) and obj.id in protected:
                obj.article,obj.size,obj.name=protected[obj.id]
    event.listen(db,'before_flush',preserve)
    try:return manual_receive_batch(p,db)
    except HTTPException as exc:
        db.rollback()
        if exc.status_code<500:
            op=db.execute(select(InventoryOperation.id).where(InventoryOperation.client_id==p.client_id,InventoryOperation.warehouse_id==p.warehouse_id,InventoryOperation.idempotency_key==p.idempotency_key)).first()
            detail=exc.detail if isinstance(exc.detail,dict) else {'message':str(exc.detail)}
            raise HTTPException(exc.status_code,detail={**detail,'safe_to_edit':not bool(op)})
        raise
    except Exception:
        db.rollback();raise
    finally:event.remove(db,'before_flush',preserve)
