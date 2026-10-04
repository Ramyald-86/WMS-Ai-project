"""r145f26. Exact local card writes; never writes stock or calls a marketplace.

A SKU ID is stable across rename/restore. Creation uses the same advisory lock
as the legacy creator. Explicit edits require the last-read fingerprint. A saved
request ID can be retried after a lost response without repeating the mutation.
"""
from __future__ import annotations
import hashlib,json,uuid
from datetime import datetime,timezone
from fastapi import HTTPException
from sqlalchemy import select,text,cast
from sqlalchemy.dialects.postgresql import JSONB
from app.models import SKU,MarketplaceProduct
from app.services.local_catalog_rules import barcode as normalize_barcode
from app.services.r145f25_places import scope,readonly_limits

MESSAGES = {
 'BAD_FIELDS':'\u0423\u043a\u0430\u0436\u0438\u0442\u0435 \u0430\u0440\u0442\u0438\u043a\u0443\u043b, \u0440\u0430\u0437\u043c\u0435\u0440 \u0438 \u043a\u0430\u0442\u0435\u0433\u043e\u0440\u0438\u044e.',
 'DUPLICATE_SKU':'\u042d\u0442\u043e\u0442 \u0428\u041a \u0441\u0432\u044f\u0437\u0430\u043d \u0441 \u043d\u0435\u0441\u043a\u043e\u043b\u044c\u043a\u0438\u043c\u0438 SKU \u043a\u043b\u0438\u0435\u043d\u0442\u0430. \u0410\u0432\u0442\u043e\u043c\u0430\u0442\u0438\u0447\u0435\u0441\u043a\u043e\u0435 \u043e\u0431\u044a\u0435\u0434\u0438\u043d\u0435\u043d\u0438\u0435 \u0437\u0430\u043f\u0440\u0435\u0449\u0435\u043d\u043e.',
 'CARD_CHANGED':'\u041a\u0430\u0440\u0442\u043e\u0447\u043a\u0430 \u0443\u0436\u0435 \u0438\u0437\u043c\u0435\u043d\u0435\u043d\u0430. \u041e\u0442\u043a\u0440\u043e\u0439\u0442\u0435 \u0435\u0451 \u0437\u0430\u043d\u043e\u0432\u043e \u0438 \u0441\u0432\u0435\u0440\u044c\u0442\u0435 \u0434\u0430\u043d\u043d\u044b\u0435.',
 'EXISTS':'\u0422\u043e\u0432\u0430\u0440 \u0441 \u0442\u0430\u043a\u0438\u043c \u0428\u041a \u0443\u0436\u0435 \u0435\u0441\u0442\u044c. \u041e\u0442\u043a\u0440\u043e\u0439\u0442\u0435 \u0435\u0433\u043e \u043a\u0430\u0440\u0442\u043e\u0447\u043a\u0443 \u0434\u043b\u044f \u0440\u0435\u0434\u0430\u043a\u0442\u0438\u0440\u043e\u0432\u0430\u043d\u0438\u044f.',
 'RESTORE_CONFIRM':'\u041a\u0430\u0440\u0442\u043e\u0447\u043a\u0430 \u0443\u0434\u0430\u043b\u0435\u043d\u0430 \u0438\u043b\u0438 \u0430\u0440\u0445\u0438\u0432\u043d\u0430\u044f. \u041f\u043e\u0434\u0442\u0432\u0435\u0440\u0434\u0438\u0442\u0435 \u0432\u043e\u0441\u0441\u0442\u0430\u043d\u043e\u0432\u043b\u0435\u043d\u0438\u0435 \u0431\u0435\u0437 \u0441\u043e\u0437\u0434\u0430\u043d\u0438\u044f \u0434\u0443\u0431\u043b\u044f.',
 'SCOPE':'\u041a\u0430\u0440\u0442\u043e\u0447\u043a\u0430 \u043d\u0435 \u043d\u0430\u0439\u0434\u0435\u043d\u0430 \u0443 \u0432\u044b\u0431\u0440\u0430\u043d\u043d\u043e\u0433\u043e \u043a\u043b\u0438\u0435\u043d\u0442\u0430.',
 'REQUEST_REUSED':'\u041a\u043e\u0434 \u0437\u0430\u043f\u0440\u043e\u0441\u0430 \u0443\u0436\u0435 \u0438\u0441\u043f\u043e\u043b\u044c\u0437\u043e\u0432\u0430\u043d \u0434\u043b\u044f \u0434\u0440\u0443\u0433\u0438\u0445 \u0434\u0430\u043d\u043d\u044b\u0445.',
 'WB_CONFLICT':'\u0428\u041a \u0432\u0441\u0442\u0440\u0435\u0447\u0430\u0435\u0442\u0441\u044f \u0432 \u0440\u0430\u0437\u043d\u044b\u0445 \u043a\u0430\u0440\u0442\u043e\u0447\u043a\u0430\u0445 WB. \u041f\u0440\u043e\u0432\u0435\u0440\u044c\u0442\u0435 \u0448\u0442\u0440\u0438\u0445\u043a\u043e\u0434.',
}
def fail(code,status=409):raise HTTPException(status,detail={'code':code,'message':MESSAGES[code]})
def dbid(value):return uuid.UUID(str(value)).hex
def dumps(value):return json.dumps(value,ensure_ascii=True,sort_keys=True,separators=(',',':'),default=str)
def digest(value):return hashlib.sha256(dumps(value).encode()).hexdigest()
def exists(db,name):
 if db.bind.dialect.name=='postgresql':return bool(db.execute(text('SELECT to_regclass(:t)'),{'t':name}).scalar())
 return bool(db.execute(text("SELECT name FROM sqlite_master WHERE type='table' AND name=:n"),{'n':name}).scalar())

def schema_statements(temp=False):
 p='TEMP ' if temp else ''
 return [f'''CREATE {p}TABLE IF NOT EXISTS fc_r145f26_product_audit(
 id uuid PRIMARY KEY,client_id uuid NOT NULL,sku_id uuid NOT NULL,request_id varchar(80) NOT NULL,
 request_hash varchar(64) NOT NULL,action varchar(32) NOT NULL,actor varchar(160) NOT NULL,
 before_json text NOT NULL,after_json text NOT NULL,response_json text NOT NULL,
 created_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,UNIQUE(client_id,request_id))''',
 '''CREATE INDEX IF NOT EXISTS ix_fc_r145f26_product_audit_sku ON fc_r145f26_product_audit(client_id,sku_id)''',
 f'''CREATE {p}TABLE IF NOT EXISTS fc_product_base_visibility(client_id uuid NOT NULL,card_key text NOT NULL,
 hidden boolean NOT NULL DEFAULT FALSE,updated_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,PRIMARY KEY(client_id,card_key))''']

def install_schema(db,commit=True):
 for sql in schema_statements():db.execute(text(sql))
 if commit:db.commit()

def category(db,c,s,code):
 if not exists(db,'fc_product_categories'):return ''
 row=db.execute(text('''SELECT category FROM fc_product_categories WHERE client_id=:c
 AND (sku_id=:s OR (sku_id IS NULL AND barcode=:b))
 ORDER BY CASE WHEN sku_id=:s THEN 0 ELSE 1 END,updated_at DESC LIMIT 1'''),{'c':dbid(c),'s':dbid(s),'b':code}).first()
 return str(row[0]) if row else ''

def local_key(s):return 'local:'+str(s.article or '').strip().casefold()+'|'+str(s.name or '').strip().casefold()

def hidden_keys(db,c,s):
 if not exists(db,'fc_product_base_visibility'):return []
 result=set(db.execute(text('''SELECT card_key FROM fc_product_base_visibility
 WHERE client_id=:c AND hidden=TRUE AND card_key IN (:local,:sku)'''),{'c':dbid(c),'local':local_key(s),'sku':'sku:'+str(s.id)}).scalars())
 # Only inspect the exact local WB snapshot when hidden WB cards exist at all.
 hidden_wb=set(db.execute(text("SELECT card_key FROM fc_product_base_visibility WHERE client_id=:c AND hidden=TRUE AND card_key LIKE 'wb:%'"),{'c':dbid(c)}).scalars())
 if hidden_wb:
  query=select(MarketplaceProduct).where(MarketplaceProduct.client_id==c,MarketplaceProduct.provider=='WB')
  if db.bind.dialect.name=='postgresql':query=query.where(cast(MarketplaceProduct.barcodes,JSONB).contains([s.barcode]))
  for p in db.execute(query).scalars():
   if db.bind.dialect.name!='postgresql' and str(s.barcode) not in [str(x) for x in (p.barcodes or [])]:continue
   key='wb:' + ('nm:'+str(p.external_product_id).strip() if p.external_product_id else 'vendor:'+str(p.vendor_code or '').strip().casefold())
   if key in hidden_wb:result.add(key)
 return sorted(result)

def snapshot(db,s):
 hidden=hidden_keys(db,s.client_id,s)
 data={'client_id':str(s.client_id),'sku_id':str(s.id),'barcode':s.barcode or '',
       'article':s.article or '','size':s.size or '','name':s.name or '',
       'is_active':bool(s.is_active),'category':category(db,s.client_id,s.id,s.barcode),'hidden_keys':hidden}
 return data

def public(db,s):
 data=snapshot(db,s)
 return {**data,'found':True,'local':True,'wb_found':False,'created':False,'hidden':bool(data['hidden_keys']),
 'archived':not bool(s.is_active),'fingerprint':digest(data),'card_key':local_key(s),'source':'LOCAL','sku_ids':[str(s.id)]}

def lookup(db,c,w,raw):
 c,w=uuid.UUID(str(c)),uuid.UUID(str(w));scope(db,c,w);readonly_limits(db)
 try:code=normalize_barcode(raw)
 except ValueError as e:raise HTTPException(422,detail=str(e))
 rows=db.execute(select(SKU).where(SKU.client_id==c,SKU.barcode==code)).scalars().all()
 if len(rows)>1:fail('DUPLICATE_SKU')
 if rows:return public(db,rows[0])
 # No external API. Unknown barcodes may be enriched from an already saved WB card.
 from app.services.local_catalog import catalog
 saved=catalog(db,c,code)
 if saved and saved[0].get('wb_conflict'):fail('WB_CONFLICT')
 return {**(saved[0] if saved else {}),'barcode':code,'client_id':str(c),'found':bool(saved and saved[0].get('found')),
         'sku_id':None,'archived':False,'hidden':False,'category':'','fingerprint':None}

def _put_category(db,s,value):
 row=db.execute(text('SELECT id FROM fc_product_categories WHERE client_id=:c AND sku_id=:s'),{'c':dbid(s.client_id),'s':dbid(s.id)}).first()
 params={'id':row[0] if row else uuid.uuid4().hex,'c':dbid(s.client_id),'s':dbid(s.id),'b':s.barcode,'a':s.article,'cat':value}
 if row:
  db.execute(text("UPDATE fc_product_categories SET barcode=:b,article=:a,category=:cat,source='MANUAL',updated_at=CURRENT_TIMESTAMP WHERE id=:id"),params)
 else:
  db.execute(text("INSERT INTO fc_product_categories(id,client_id,sku_id,barcode,article,category,source) VALUES(:id,:c,:s,:b,:a,:cat,'MANUAL')"),params)

def save(db,payload,actor,commit=True):
 """One transaction: SKU + category + restore visibility + before/after audit."""
 c,w=payload.client_id,payload.warehouse_id
 scope(db,c,w);readonly_limits(db)
 try:code=normalize_barcode(payload.barcode)
 except ValueError as e:raise HTTPException(422,detail=str(e))
 article,size,cat=(payload.article.strip(),payload.size.strip(),payload.category.strip())
 name=payload.name.strip() or article
 if not article or not size or not cat:fail('BAD_FIELDS',422)
 body=payload.model_dump(mode='json');request_hash=digest(body)
 if db.bind.dialect.name=='postgresql':
  db.execute(text('SELECT pg_advisory_xact_lock(hashtext(:key))'),{'key':f'fc:local-sku:{c}:{code}'})
 replay=db.execute(text('SELECT request_hash,response_json FROM fc_r145f26_product_audit WHERE client_id=:c AND request_id=:r'),{'c':dbid(c),'r':str(payload.request_id)}).first()
 if replay:
  if replay[0]!=request_hash:fail('REQUEST_REUSED')
  # Safe response to the same request only; the mutation is not repeated.
  saved=json.loads(replay[1]);current=db.get(SKU,uuid.UUID(saved['sku_id']))
  if not current or current.client_id!=c:fail('SCOPE',404)
  return {**saved,**public(db,current),'replayed':True}
 rows=db.execute(select(SKU).where(SKU.client_id==c,SKU.barcode==code).with_for_update()).scalars().all()
 if len(rows)>1:fail('DUPLICATE_SKU')
 s=rows[0] if rows else None
 if payload.sku_id and (s is None or s.id!=payload.sku_id):fail('SCOPE',404)
 old=snapshot(db,s) if s else None
 restored=False
 if s:
  needs_restore=not s.is_active or bool(old['hidden_keys'])
  if needs_restore and not payload.restore_confirmed:fail('RESTORE_CONFIRM')
  same=all(str(getattr(s,k) or '')==v for k,v in [('article',article),('size',size),('name',name)]) and old['category']==cat
  if payload.mode=='create':
   if not same or needs_restore:fail('EXISTS')
   result=public(db,s);action='EXISTING'
  else:
   if not payload.sku_id or payload.fingerprint!=digest(old):fail('CARD_CHANGED')
   s.article,s.size,s.name=article,size,name;s.is_active=True;restored=needs_restore
   _put_category(db,s,cat)
   if payload.restore_confirmed:
    # Restore only the selected client's logical card keys. Other clients never touched.
    for key in old['hidden_keys']+[local_key(s)]:
     db.execute(text('UPDATE fc_product_base_visibility SET hidden=FALSE,updated_at=CURRENT_TIMESTAMP WHERE client_id=:c AND card_key=:k'),{'c':dbid(c),'k':key})
   action='RESTORE' if restored else 'EDIT'
 else:
  if payload.mode!='create':fail('SCOPE',404)
  # Recheck stored WB conflicts under the same barcode lock, not after committing a new SKU.
  from app.services.local_catalog import catalog
  wb=catalog(db,c,code)
  if wb and wb[0].get('wb_conflict'):fail('WB_CONFLICT')
  if wb and wb[0].get('wb_found'):
   for k,v in [('article',article),('size',size)]:
    if str(wb[0].get(k) or '').strip() and str(wb[0][k]).strip().casefold()!=v.casefold():fail('WB_CONFLICT')
  s=SKU(id=uuid.uuid4(),client_id=c,sku_code='LOCAL-'+uuid.uuid5(c,code).hex,
        barcode=code,article=article,size=size,name=name,unit='PCS',is_active=True)
  db.add(s);db.flush();_put_category(db,s,cat);action='CREATE'
 db.flush()
 result={**public(db,s),'ok':True,'created':action=='CREATE','restored':restored,'action':action,'stock_changed':False,'wb_api_called':False}
 db.execute(text('''INSERT INTO fc_r145f26_product_audit(id,client_id,sku_id,request_id,request_hash,action,actor,before_json,after_json,response_json)
 VALUES(:id,:c,:s,:r,:h,:a,:u,:b,:n,:resp)'''),{'id':uuid.uuid4().hex,'c':dbid(c),'s':dbid(s.id),'r':str(payload.request_id),'h':request_hash,
 'a':action,'u':str(actor)[:160],'b':dumps(old),'n':dumps(snapshot(db,s)),'resp':dumps(result)})
 if commit:db.commit()
 return result
