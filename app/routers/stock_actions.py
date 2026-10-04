import json
import uuid
from typing import Literal
from fastapi import APIRouter,Depends,Request,HTTPException
from pydantic import BaseModel,Field,model_validator
from sqlalchemy.orm import Session
from app.core.database import get_db
from app.routers.warehouse_ui import scope
from app.services import stock_actions as service, stock_accounting as accounting
from app.services.container_lifecycle import archive_consumed_empty_containers

router=APIRouter(prefix='/warehouse-ui',tags=['Действия с товаром'])

class Context(BaseModel):
    client_id:uuid.UUID
    warehouse_id:uuid.UUID

class Action(Context):
    action:Literal['ADD','REMOVE','REMOVE_ALL','MOVE']
    stock_class:Literal['MAIN','FBS']
    sku_ids:list[uuid.UUID]=Field(min_length=1,max_length=100)
    location_id:uuid.UUID|None=None
    container_id:uuid.UUID|None=None
    destination_location_id:uuid.UUID|None=None
    destination_container_id:uuid.UUID|None=None
    quantity:int|None=Field(default=None,ge=1,le=1000000)
    reason:str=Field(min_length=1,max_length=500)
    wb_warehouse_id:int|None=None
    @model_validator(mode='after')
    def validate_action(self):
        self.reason=self.reason.strip()
        if not self.reason:raise ValueError('Укажите причину')
        if self.action!='REMOVE_ALL' and (self.quantity is None or self.location_id is None):raise ValueError('Укажите место и количество')
        if self.action=='MOVE' and not self.destination_location_id:raise ValueError('Укажите место назначения')
        return self

class Confirm(Context):
    fingerprint:str=Field(min_length=64,max_length=64)
    confirmed_missing:bool=False

class AccountingPreview(Context):
    sku_ids:list[uuid.UUID]=Field(default_factory=list,max_length=100)

def actor(request):return getattr(request.state,'username',None)

def _json_value(value):
    if isinstance(value,str):
        try:return json.loads(value)
        except Exception:return value
    return value


def _container_ids(value):
    value=_json_value(value);out=[]
    if isinstance(value,dict):
        for key in ('container_id','source_container_id'):
            if value.get(key):out.append(value.get(key))
        if isinstance(value.get('source'),dict) and value['source'].get('container_id'):
            out.append(value['source'].get('container_id'))
        for key in ('items','rows','sources'):
            if key in value:out.extend(_container_ids(value.get(key)))
    elif isinstance(value,list):
        for item in value:out.extend(_container_ids(item))
    return out


def _cleanup_empty_boxes(db,c,w,value,who):
    ids=_container_ids(value)
    if not ids:return 0
    return int(archive_consumed_empty_containers(db,c,w,ids,actor=who,reason='STOCK_ACTION_EMPTY_BOX').get('count',0))


def _queue_fbs_now(db,c,w,ids,requested=None):
    """Use the existing durable WB publisher; never report queued work as confirmed WB stock."""
    from app.models import SKU,MarketplaceCredential
    from sqlalchemy import select
    from app.routers.receiving import _active_wb_credential,_work_wb_warehouse_id
    from app.services.stock_core_v2 import enqueue_wb_stock_for_sku
    from app.services.local_catalog import catalog
    active=db.execute(select(MarketplaceCredential.id).where(MarketplaceCredential.client_id==uuid.UUID(str(c)),MarketplaceCredential.provider=='WB',MarketplaceCredential.is_active.is_(True))).scalars().all()
    if len(active)>1:return 'Остаток сохранён. Подключено несколько кабинетов WB: выберите нужный кабинет и обновите остатки в разделе WB FBS'
    cred=_active_wb_credential(db,uuid.UUID(str(c)))
    if not cred:return 'WB не подключён; сохранён локальный остаток'
    try:wid=_work_wb_warehouse_id(cred,None,uuid.UUID(str(w)))
    except HTTPException:return 'Не выбран рабочий склад WB; выполните публикацию после настройки'
    if requested and wid and int(requested)!=int(wid):return 'Остаток сохранён. Выбранный склад WB отличается от рабочего: проверьте привязку и обновите WB FBS'
    if not wid:return 'Не выбран рабочий склад WB; выполните публикацию после настройки'
    count=0
    for sid in sorted(set(ids)):
        sku=db.get(SKU,uuid.UUID(str(sid)))
        known=catalog(db,c,sku.barcode) if sku else []
        if not sku or not any(r.get('wb_found') and not r.get('wb_conflict') and not r.get('duplicate_sku') for r in known):continue
        enqueue_wb_stock_for_sku(db,client_id=c,warehouse_id=w,sku=sku,credential_id=cred.id,external_warehouse_id=wid,reason='R69_STOCK_CHANGE')
        count+=1
    return 'Изменение WB поставлено в очередь' if count else 'Локальный товар: карточки WB пока нет'

def queue_fbs(db,c,w,ids,requested=None):
    # Operator stock edits must return immediately after the local transaction.  The
    # durable recovery worker publishes WB in the background; never make the button
    # wait for catalog/target preparation.
    import json as _json
    from app.services import stock_recovery
    stock_recovery.install_schema(db)
    payload={'client_id':str(c),'warehouse_id':str(w),
        'sku_ids':sorted({str(s) for s in ids}),'requested':requested}
    identity=','.join(payload['sku_ids'])
    key=f"MANUAL:{payload['client_id']}:{payload['warehouse_id']}:{identity}"
    db.execute(text('''INSERT INTO fc_stock_publish_retry(job_key,kind,payload)
      VALUES(:key,'MANUAL',CAST(:payload AS jsonb)) ON CONFLICT(job_key) DO UPDATE
      SET payload=EXCLUDED.payload,next_attempt_at=now(),last_error=NULL,updated_at=now()'''),
      {'key':key,'payload':_json.dumps(payload)})
    return 'Остаток изменён. Обновление WB выполняется в фоне'

@router.post('/actions/preview')
def preview(payload:Action,request:Request,db:Session=Depends(get_db)):
    scope(db,payload.client_id,payload.warehouse_id)
    try:
        result=service.create_preview(db,str(payload.client_id),str(payload.warehouse_id),payload.model_dump(mode='json'),actor(request))
        db.commit();return result
    except Exception:db.rollback();raise

@router.post('/actions/{plan_id}/confirm')
def confirm(plan_id:uuid.UUID,payload:Confirm,request:Request,db:Session=Depends(get_db)):
    scope(db,payload.client_id,payload.warehouse_id)
    try:
        result=service.confirm(db,payload.client_id,payload.warehouse_id,plan_id,payload.fingerprint,actor(request))
        from sqlalchemy import text
        plan_request=_json_value(db.execute(text('SELECT request_json FROM fc_stock_action_plans WHERE id=:id'),{'id':str(plan_id)}).scalar() or {})
        if not result.get('replayed'):
            result['archived_empty_boxes']=_cleanup_empty_boxes(db,payload.client_id,payload.warehouse_id,plan_request,actor(request))
            if result.get('stock_class')=='FBS':
                result['wb']=queue_fbs(db,payload.client_id,payload.warehouse_id,plan_request.get('sku_ids') or [],plan_request.get('wb_warehouse_id'))
        db.commit();return result
    except Exception:db.rollback();raise

@router.post('/accounting/preview')
def accounting_preview(payload:AccountingPreview,request:Request,db:Session=Depends(get_db)):
    scope(db,payload.client_id,payload.warehouse_id)
    try:
        result=accounting.preview(db,payload.client_id,payload.warehouse_id,payload.sku_ids,actor(request));db.commit();return result
    except Exception:db.rollback();raise

@router.post('/accounting/{plan_id}/confirm')
def accounting_confirm(plan_id:uuid.UUID,payload:Confirm,request:Request,db:Session=Depends(get_db)):
    scope(db,payload.client_id,payload.warehouse_id)
    try:
        result=accounting.confirm(db,payload.client_id,payload.warehouse_id,plan_id,payload.fingerprint,actor(request),payload.confirmed_missing)
        from sqlalchemy import text
        p=_json_value(db.execute(text('SELECT preview_json FROM fc_stock_action_plans WHERE id=:id'),{'id':str(plan_id)}).scalar() or [])
        if not result.get('replayed'):
            result['archived_empty_boxes']=_cleanup_empty_boxes(db,payload.client_id,payload.warehouse_id,p,actor(request))
            result['wb']=queue_fbs(db,payload.client_id,payload.warehouse_id,[x['source']['sku_id'] for x in p if isinstance(x,dict) and isinstance(x.get('source'),dict) and x['source'].get('sku_id')])
        db.commit();return result
    except Exception:db.rollback();raise


class Apply(Action):
    request_id:uuid.UUID

@router.post('/actions/apply')
def apply(payload:Apply,request:Request,db:Session=Depends(get_db)):
    from sqlalchemy import text
    scope(db,payload.client_id,payload.warehouse_id)
    full_req=payload.model_dump(mode='json');rid=full_req['request_id'];req={k:v for k,v in full_req.items() if k!='request_id'}
    try:
        # r139 fast path: an exact one-place write-off no longer builds and validates
        # the same preview twice. Other actions keep the proven legacy path.
        if payload.action=='REMOVE' and len(payload.sku_ids)==1 and payload.location_id:
            result=service.fast_remove_apply(db,payload.client_id,payload.warehouse_id,full_req,actor(request))
            if not result.get('replayed'):
                result['archived_empty_boxes']=_cleanup_empty_boxes(db,payload.client_id,payload.warehouse_id,req,actor(request))
                if result.get('stock_class')=='FBS':
                    result['wb']=queue_fbs(db,payload.client_id,payload.warehouse_id,payload.sku_ids,payload.wb_warehouse_id)
            db.commit();return result
        from app.services.stock_core_v2 import ensure_stock_core_v2_schema
        ensure_stock_core_v2_schema(db)
        db.execute(text("SELECT pg_advisory_xact_lock(hashtext(:k))"),{'k':'r70-action:'+str(payload.client_id)+':'+rid})
        previous=db.execute(text('SELECT * FROM fc_stock_action_plans WHERE id=:id AND client_id=:c AND warehouse_id=:w'),{'id':rid,'c':str(payload.client_id),'w':str(payload.warehouse_id)}).mappings().first()
        if previous:
            if service.fingerprint(previous['request_json'])!=service.fingerprint(req):raise HTTPException(409,detail='Этот запрос уже применён с другими данными')
            plan={'id':rid,'fingerprint':previous['fingerprint']}
        else:
            plan=service.create_preview(db,str(payload.client_id),str(payload.warehouse_id),req,actor(request))
            db.execute(text('UPDATE fc_stock_action_plans SET id=:rid WHERE id=:id'),{'rid':rid,'id':plan['id']})
            plan['id']=rid
        result=service.confirm(db,payload.client_id,payload.warehouse_id,plan['id'],plan['fingerprint'],actor(request))
        if not result.get('replayed'):
            result['archived_empty_boxes']=_cleanup_empty_boxes(db,payload.client_id,payload.warehouse_id,req,actor(request))
            if result.get('stock_class')=='FBS':
                result['wb']=queue_fbs(db,payload.client_id,payload.warehouse_id,payload.sku_ids,payload.wb_warehouse_id)
        db.commit();return result
    except Exception:db.rollback();raise

@router.get('/revision')
def revision(client_id:uuid.UUID,warehouse_id:uuid.UUID,db:Session=Depends(get_db)):
    from app.services.stock_sync import revision as current_revision
    scope(db,client_id,warehouse_id)
    return {'client_id':str(client_id),'warehouse_id':str(warehouse_id),'revision':current_revision(db,client_id,warehouse_id)}

class RemoveLine(BaseModel):
    sku_id:uuid.UUID
    location_id:uuid.UUID
    container_id:uuid.UUID|None=None
    quantity:int=Field(ge=1,le=1000000)

class BulkRemove(Context):
    request_id:uuid.UUID
    stock_class:Literal['MAIN','FBS']
    reason:str=Field(min_length=1,max_length=500)
    items:list[RemoveLine]=Field(min_length=1,max_length=5000)

@router.post('/actions/bulk-remove')
def bulk_remove(payload:BulkRemove,request:Request,db:Session=Depends(get_db)):
    scope(db,payload.client_id,payload.warehouse_id)
    if not payload.reason.strip():raise HTTPException(422,detail='Укажите причину')
    try:
        request_data=payload.model_dump(mode='json')
        result=service.bulk_remove(db,payload.client_id,payload.warehouse_id,request_data,actor(request))
        if not result.get('replayed'):
            result['archived_empty_boxes']=_cleanup_empty_boxes(db,payload.client_id,payload.warehouse_id,request_data,actor(request))
            if payload.stock_class=='FBS':
                result['wb']=queue_fbs(db,payload.client_id,payload.warehouse_id,[x.sku_id for x in payload.items])
        db.commit();return result
    except Exception:db.rollback();raise

@router.get('/actions/bulk-options')
def bulk_options(client_id:uuid.UUID,warehouse_id:uuid.UUID,stock_class:Literal['MAIN','FBS'],db:Session=Depends(get_db)):
    from app.routers.warehouse_ui import stock_rows
    from app.services.stock_core_v2 import effective_fbs_free_for_sku,canonical_stock_identity_lock_key
    scope(db,client_id,warehouse_id)
    result=[];budgets={}
    for row in stock_rows(db,client_id,warehouse_id,stock_class):
        for p in row['locations']:
            b=service.bucket(db,str(client_id),str(warehouse_id),p['sku_id'],p['location_id'],p['container_id'],read_only=True)
            qty=max(0,b['main']-b['main_held']) if stock_class=='MAIN' else max(0,b['fbs']-b['fbs_held'])
            if stock_class=='FBS':
                identity=canonical_stock_identity_lock_key(db,client_id=client_id,sku_id=p['sku_id'])
                if identity not in budgets:budgets[identity]=service.number(effective_fbs_free_for_sku(db,client_id=client_id,warehouse_id=warehouse_id,sku_id=p['sku_id']))
                qty=min(qty,max(0,budgets[identity]));budgets[identity]-=qty
            result.append({**p,'article':row['article'],'size':row['size'],'barcode':row['barcode'],'free':int(qty)})
    return {'rows':result}
