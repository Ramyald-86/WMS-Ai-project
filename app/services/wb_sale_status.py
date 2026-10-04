"""WB sale policy, verified observations and stopped-stock operations.

Classification in FBS is never evidence of publication. Observations are keyed by
credential, physical warehouse, external warehouse and WB size, not receipt lot.
"""
import json
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from fastapi import HTTPException
from sqlalchemy import text, select, bindparam
from app.models import SKU, MarketplaceCredential, InventoryBalance
from app.services import wb_sales_control as policy, stock_actions as stock

FRESH_SECONDS = 120

def install_schema(db):
    db.execute(text('''CREATE TABLE IF NOT EXISTS fc_wb_verified_stock (
        client_id varchar(36) NOT NULL,warehouse_id varchar(36) NOT NULL,
        credential_id varchar(36) NOT NULL,external_warehouse_id bigint NOT NULL,chrt_id bigint NOT NULL,
        amount bigint,checked_at timestamptz,attempted_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        error text,source text,PRIMARY KEY(client_id,warehouse_id,credential_id,external_warehouse_id,chrt_id))'''))
    db.execute(text('''CREATE TABLE IF NOT EXISTS fc_wb_sale_events (
        id varchar(36) PRIMARY KEY,client_id varchar(36) NOT NULL,warehouse_id varchar(36) NOT NULL,
        request_hash varchar(64) NOT NULL,result_json jsonb NOT NULL,actor text,
        created_at timestamptz NOT NULL DEFAULT now())'''))

def resolve(db,c,w):
    from app.routers.receiving import _work_wb_warehouse_id
    creds=db.execute(select(MarketplaceCredential).where(MarketplaceCredential.client_id==uuid.UUID(str(c)),
        MarketplaceCredential.provider=='WB',MarketplaceCredential.is_active.is_(True))).scalars().all()
    if len(creds)!=1:raise HTTPException(409,detail='Укажите одно активное подключение WB для клиента' if creds else 'Подключение WB не настроено')
    wid=_work_wb_warehouse_id(creds[0],None,w)
    if not wid:raise HTTPException(409,detail='Не выбран рабочий склад WB для этого физического склада')
    scopes=(creds[0].warehouse_settings or {}).get('__fc_physical_warehouses__',{})
    works=[key for key,val in (scopes.get(str(w)) or {}).items() if str(val.get('mode') if isinstance(val,dict) else val).upper()=='WORK']
    if len(works)!=1:raise HTTPException(409,detail='У физического склада должен быть один рабочий склад WB')
    return creds[0],int(wid)

def record_observations(db,c,w,credential,wid,amounts,source='READ',error=None,observed_at=None):
    stamp=observed_at or datetime.now(timezone.utc)
    for chrt,amount in amounts.items():
        db.execute(text('''INSERT INTO fc_wb_verified_stock
            (client_id,warehouse_id,credential_id,external_warehouse_id,chrt_id,amount,checked_at,attempted_at,error,source)
            VALUES(:c,:w,:cred,:wid,:chrt,:amount,:checked,:stamp,:error,:source)
            ON CONFLICT(client_id,warehouse_id,credential_id,external_warehouse_id,chrt_id) DO UPDATE SET
              amount=CASE WHEN EXCLUDED.error IS NULL THEN EXCLUDED.amount ELSE fc_wb_verified_stock.amount END,
              checked_at=CASE WHEN EXCLUDED.error IS NULL THEN EXCLUDED.checked_at ELSE fc_wb_verified_stock.checked_at END,
              attempted_at=EXCLUDED.attempted_at,error=EXCLUDED.error,source=EXCLUDED.source
            WHERE fc_wb_verified_stock.attempted_at<=EXCLUDED.attempted_at'''),
            {'c':str(c),'w':str(w),'cred':str(credential),'wid':int(wid),'chrt':int(chrt),
             'amount':None if error else int(amount),'checked':None if error else stamp,'stamp':stamp,'error':error,'source':source})

def state_for(paused,target,observation,queue,now=None,config_error=''):
    now=now or datetime.now(timezone.utc);obs=observation or {};q=queue or {}
    checked=obs.get('checked_at');error=config_error or obs.get('error') or (q.get('last_error') if q.get('status')=='FAILED' else None)
    fresh=bool(checked and not obs.get('error') and 0 <= (now-checked).total_seconds()<=FRESH_SECONDS)
    amount=obs.get('amount');queued=q.get('status') in ('PENDING','PROCESSING')
    if paused:status='PAUSED'
    elif error:status='ERROR'
    elif queued:status='PENDING'
    elif not fresh:status='STALE' if checked else 'UNVERIFIED'
    elif target is None:status='ERROR'
    elif amount!=target:status='MISMATCH'
    else:status='ACTIVE' if amount>0 else 'EMPTY'
    return {'status':status,'paused':paused,'target':target,'confirmed_amount':int(amount) if amount is not None else None,
        'checked_at':checked,'fresh':fresh,'error':error,'queue_status':q.get('status'),
        'queue_error':q.get('last_error') if q.get('status')=='FAILED' else None,
        'stop_confirmed':bool(paused and fresh and amount==0),
        'stop_pending':bool(paused and (not fresh or amount!=0)),
        'can_remove':bool(paused and fresh and amount==0 and not config_error)}

def snapshot(db,c,w,sku_ids=None):
    from app.services.stock_outbox_worker import _wb_size_targets
    stmt=select(SKU).where(SKU.client_id==uuid.UUID(str(c)))
    if sku_ids is not None:stmt=stmt.where(SKU.id.in_([uuid.UUID(str(x)) for x in sku_ids]))
    else:stmt=stmt.where(SKU.id.in_(select(SKU.id).join(InventoryBalance,
        InventoryBalance.sku_id==SKU.id).where(
        InventoryBalance.warehouse_id==uuid.UUID(str(w)),
        InventoryBalance.quantity>0)))
    skus=db.execute(stmt).scalars().all();codes=sorted({s.barcode for s in skus if s.barcode})
    paused=policy.paused_ids(db,c,w,[s.id for s in skus]);config_error='';cred=None;wid=None;targets={};siblings={};chrts={};observations={}
    try:cred,wid=resolve(db,c,w)
    except HTTPException as e:config_error=e.detail
    if cred and codes:
        targets,siblings,chrts=_wb_size_targets(db,client_id=str(c),warehouse_id=str(w),external_wh=str(wid),codes=codes)
        observations={int(x['chrt_id']):dict(x) for x in db.execute(text('''SELECT * FROM fc_wb_verified_stock
            WHERE client_id=:c AND warehouse_id=:w AND credential_id=:cred AND external_warehouse_id=:wid'''),
            {'c':str(c),'w':str(w),'cred':str(cred.id),'wid':wid}).mappings()} if db.execute(text("SELECT to_regclass('fc_wb_verified_stock')")).scalar() else {}
    all_codes=sorted(set(codes).union(*(set(v) for v in siblings.values())))
    members_all=db.execute(select(SKU).where(SKU.client_id==uuid.UUID(str(c)),SKU.barcode.in_(all_codes))).scalars().all() if all_codes else []
    paused=policy.paused_ids(db,c,w,[s.id for s in members_all])
    queues={}
    if all_codes and cred:
        for q in db.execute(text('''SELECT * FROM fc_marketplace_stock_outbox_v2 WHERE client_id=:c AND warehouse_id=:w
            AND provider='WB' AND credential_id=:cred AND external_warehouse_id=:wid AND product_key IN :codes
            ORDER BY updated_at,id''').bindparams(bindparam('codes',expanding=True)),
            {'c':str(c),'w':str(w),'cred':str(cred.id),'wid':str(wid),'codes':all_codes}).mappings():
            if not str(q['last_error'] or '').startswith('SUPERSEDED:'):queues[q['product_key']]=dict(q)
    rows=[]
    for code in codes:
        members=[s for s in members_all if s.barcode in siblings.get(code,{code})]
        is_paused=any(str(s.id) in paused for s in members)
        qs=[queues[x] for x in siblings.get(code,{code}) if x in queues]
        q=max(qs,key=lambda x:x['updated_at']) if qs else None
        error=config_error or ('Нет карточки WB с идентификатором размера для этого ШК' if not chrts.get(code) else '')
        obs=observations.get(chrts.get(code))
        row=state_for(is_paused,targets.get(code),obs,q,config_error=error)
        rows.append({'barcode':code,'sku_ids':[str(s.id) for s in skus if s.barcode==code],
            'chrt_id':chrts.get(code),'siblings':sorted(siblings.get(code,{code})),**row})
    return {'rows':rows,'global_paused':policy.global_paused(db,c,w),'wb_warehouse_id':wid,
        'configuration_error':config_error,'generated_at':datetime.now(timezone.utc).isoformat()}

def read_wb(db,c,w,codes):
    """No stock writes; release DB transaction before marketplace I/O."""
    from app.routers.receiving import _wb_chrt_map,_wb_read_chrt_stocks,WBClient,decrypt_secret
    cred,wid=resolve(db,c,w);cid=str(cred.id);token=decrypt_secret(cred.encrypted_api_key)
    chrt_map=_wb_chrt_map(db,client_id=uuid.UUID(str(c)),barcodes=codes)
    db.rollback();client=WBClient(token);stamp=datetime.now(timezone.utc)
    try:
        amounts=_wb_read_chrt_stocks(client,wid,sorted(set(chrt_map.values())))
        record_observations(db,c,w,cid,wid,amounts,observed_at=stamp);db.commit()
        return {'ok':True,'checked':len(amounts),'missing':[x for x in codes if x not in chrt_map]}
    except Exception as e:
        db.rollback();record_observations(db,c,w,cid,wid,{x:0 for x in chrt_map.values()},error=str(e)[:1000],observed_at=stamp);db.commit()
        raise HTTPException(502,detail='WB не подтвердил остатки: '+str(e)[:500]) from e
    finally:client.close()

def stopped_action(db,c,w,req,actor):
    """Same-transaction accounting + retry identity; never consumes reserved stock."""
    from app.services.receipt_fbs import queue_publication
    from app.services.stock_core_v2 import effective_fbs_free_for_sku,canonical_stock_identity_lock_key
    ids=sorted(set(req['sku_ids']));stock.locks(db,str(c),str(w),ids)
    digest=stock.fingerprint(req);rid=req['request_id']
    old=db.execute(text('SELECT * FROM fc_wb_sale_events WHERE id=:id FOR UPDATE'),{'id':rid}).mappings().first()
    if old:
        if old['client_id']!=str(c) or old['warehouse_id']!=str(w) or old['request_hash']!=digest:raise HTTPException(409,detail='Запрос уже использован с другим составом')
        return {**old['result_json'],'replayed':True}
    data=snapshot(db,c,w,ids);by_id={sid:r for r in data['rows'] for sid in r['sku_ids']}
    if any(s not in by_id for s in ids):raise HTTPException(404,detail='Товар не найден у выбранного клиента')
    if any(not by_id[s]['can_remove'] for s in ids):raise HTTPException(409,detail='Сначала остановите продажи и выполните «Сверить WB». WB должен подтвердить 0 по всем выбранным товарам')
    budgets={};changes=[]
    for sid in ids:
        identity=canonical_stock_identity_lock_key(db,client_id=c,sku_id=sid)
        if identity not in budgets:budgets[identity]=Decimal(str(effective_fbs_free_for_sku(db,client_id=c,warehouse_id=w,sku_id=sid,wb_warehouse_id=data['wb_warehouse_id'])))
        places=db.execute(text('''SELECT CAST(location_id AS text) loc,CAST(container_id AS text) box
            FROM inventory_balances WHERE client_id=:c AND warehouse_id=:w AND sku_id=:s AND quantity>0
            ORDER BY location_id,container_id NULLS FIRST'''),{'c':str(c),'w':str(w),'s':sid}).mappings().all()
        for place in places:
            b=stock.bucket(db,c,w,sid,place['loc'],place['box']);stock.place(db,c,w,place['loc'],place['box'])
            if b['main']!=b['storage_raw']:raise HTTPException(409,detail='Адрес требует сверки остатка')
            q=min(max(Decimal(0),b['fbs']-b['fbs_held']),max(Decimal(0),budgets[identity]));budgets[identity]-=q
            if q!=q.to_integral_value():raise HTTPException(409,detail='Проверьте дробный остаток')
            if q:changes.append((b,q))
    if not changes:raise HTTPException(409,detail='Нет свободного товара для действия; зарезервированный товар остаётся на месте')
    op=None
    if req['action']=='WRITE_OFF':
        op=stock.execute_ledger(db,c,w,'REMOVE',[{'sku_id':b['sku_id'],'quantity':q,'source_location_id':b['location_id'],
            'source_container_id':b['container_id'],'reason':req['reason']} for b,q in changes],
            'r77-stopped-'+rid,req['reason'],actor)
    for b,q in changes:
        physical=b['physical']-(q if op else 0);main=b['main']+(q if not op else 0)
        if stock.bucket(db,c,w,b['sku_id'],b['location_id'],b['container_id'])['physical']!=physical:raise HTTPException(409,detail='Физическое изменение не подтверждено')
        stock.save_mirror(db,c,w,b,physical,main)
    publication=queue_publication(db,c,w,ids)
    result={'ok':True,'action':req['action'],'quantity':sum(int(q) for b,q in changes),'operation_id':op,
        'publication':publication,'replayed':False,'reason':req['reason'],
        'places':[{'sku_id':b['sku_id'],'location_id':b['location_id'],'container_id':b['container_id'],'quantity':int(q)} for b,q in changes]}
    db.execute(text('''INSERT INTO fc_wb_sale_events(id,client_id,warehouse_id,request_hash,result_json,actor)
        VALUES(:id,:c,:w,:hash,CAST(:result AS jsonb),:actor)'''),
        {'id':rid,'c':str(c),'w':str(w),'hash':digest,'result':json.dumps(result,ensure_ascii=False),'actor':actor})
    return result
