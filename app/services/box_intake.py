"""Box receipts and saved box picking sheets. Receiving a box never invents SKU stock."""
import json
import threading
import uuid
from fastapi import HTTPException
from sqlalchemy import text
from app.services.box_intake_rules import box_code, position
from app.services.workflow_rules import fingerprint, text_value
from app.services.warehouse_availability import lock_scope
from app.services.box_routes import set_box_route, enrich
from app.services.picking_route import route_key

DDL=[
'''CREATE TABLE IF NOT EXISTS fc_box_intakes (
 id varchar(36) PRIMARY KEY, client_id varchar(36) NOT NULL, warehouse_id varchar(36) NOT NULL,
 name varchar(240) NOT NULL, to_pallets boolean NOT NULL, status varchar(20) NOT NULL DEFAULT 'OPEN',
 next_number integer NOT NULL DEFAULT 1, current_pallet_id varchar(36),
 default_section varchar(80) NOT NULL DEFAULT '', first_row integer,
 actor varchar(120), created_at timestamptz NOT NULL DEFAULT now(), closed_at timestamptz,
 CHECK(first_row IS NULL OR first_row BETWEEN 1 AND 900))''',
'''CREATE INDEX IF NOT EXISTS ix_fc_box_intakes_scope ON fc_box_intakes(client_id,warehouse_id,created_at DESC)''',
'''CREATE TABLE IF NOT EXISTS fc_box_intake_pallets (
 id varchar(36) PRIMARY KEY, receipt_id varchar(36) NOT NULL REFERENCES fc_box_intakes(id),
 number integer NOT NULL, location_id varchar(36) NOT NULL, section varchar(80) NOT NULL DEFAULT '',
 first_row integer, capacity integer NOT NULL DEFAULT 16, is_closed boolean NOT NULL DEFAULT false,
 created_at timestamptz NOT NULL DEFAULT now(), UNIQUE(receipt_id,number),
 CHECK(capacity BETWEEN 1 AND 800), CHECK(first_row IS NULL OR first_row BETWEEN 1 AND 900))''',
'''CREATE TABLE IF NOT EXISTS fc_box_intake_boxes (
 id varchar(36) PRIMARY KEY, receipt_id varchar(36) NOT NULL REFERENCES fc_box_intakes(id),
 pallet_id varchar(36) REFERENCES fc_box_intake_pallets(id), container_id varchar(36) NOT NULL,
 box_code varchar(120) NOT NULL, sequence_no integer NOT NULL, actor varchar(120), created_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE(receipt_id,container_id), UNIQUE(receipt_id,box_code))''',
'''CREATE INDEX IF NOT EXISTS ix_fc_box_intake_boxes_box ON fc_box_intake_boxes(container_id)''',
'''CREATE TABLE IF NOT EXISTS fc_box_intake_audit (
 id varchar(36) PRIMARY KEY, receipt_id varchar(36), client_id varchar(36) NOT NULL,
 warehouse_id varchar(36) NOT NULL, action varchar(40) NOT NULL, actor varchar(120), reason text,
 before_json jsonb, after_json jsonb, created_at timestamptz NOT NULL DEFAULT now())''',
'''CREATE INDEX IF NOT EXISTS ix_fc_box_intake_audit_receipt ON fc_box_intake_audit(receipt_id,created_at)''',
'''CREATE TABLE IF NOT EXISTS fc_box_sheets (
 id varchar(36) PRIMARY KEY, client_id varchar(36) NOT NULL, warehouse_id varchar(36) NOT NULL,
 number varchar(120) NOT NULL, source_hash varchar(64) NOT NULL, result_json jsonb NOT NULL,
 actor varchar(120), created_at timestamptz NOT NULL DEFAULT now(), UNIQUE(client_id,warehouse_id,number))''',
'''CREATE TABLE IF NOT EXISTS fc_box_layout_plans (
 id varchar(36) PRIMARY KEY, receipt_id varchar(36) NOT NULL REFERENCES fc_box_intakes(id),
 client_id varchar(36) NOT NULL, warehouse_id varchar(36) NOT NULL, content_hash varchar(64) NOT NULL,
 rows_json jsonb NOT NULL, fingerprint varchar(64) NOT NULL, status varchar(20) NOT NULL DEFAULT 'PREVIEW',
 actor varchar(120), operation_id varchar(36), created_at timestamptz NOT NULL DEFAULT now())''',
# Expand only route metadata for deliberately extended pallets; stock tables are untouched.
'ALTER TABLE fc_box_routes DROP CONSTRAINT IF EXISTS fc_box_routes_row_on_pallet_check',
'ALTER TABLE fc_box_routes ADD CONSTRAINT fc_box_routes_row_on_pallet_check CHECK(row_on_pallet BETWEEN 1 AND 999)',
]

# FC_R145F7_TSD_BOX_RECEIVING_BATCH_PALLET
_R145F7_SCHEMA_READY=False
_R145F7_SCHEMA_LOCK=threading.Lock()

def ensure_r145f7_schema(db):
    global _R145F7_SCHEMA_READY
    if _R145F7_SCHEMA_READY:return
    with _R145F7_SCHEMA_LOCK:
        if _R145F7_SCHEMA_READY:return
        db.execute(text("ALTER TABLE fc_box_intakes ADD COLUMN IF NOT EXISTS manual_pallets boolean NOT NULL DEFAULT false"))
        db.execute(text("ALTER TABLE fc_box_intake_pallets ADD COLUMN IF NOT EXISTS public_code varchar(120)"))
        db.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS uq_fc_box_intake_pallet_public ON fc_box_intake_pallets(receipt_id,LOWER(public_code)) WHERE public_code IS NOT NULL"))
        db.execute(text("CREATE INDEX IF NOT EXISTS ix_fc_box_intake_open_name ON fc_box_intakes(client_id,warehouse_id,LOWER(name),status)"))
        db.commit();_R145F7_SCHEMA_READY=True



def install_schema(db):
    db.execute(text("SET LOCAL lock_timeout='5s'"))
    for sql in DDL:db.execute(text(sql))


def params(c,w):return {'c':str(c),'w':str(w)}

def object_json(value):return json.loads(value) if isinstance(value,str) else value

def audit(db,c,w,rid,action,by,before=None,after=None,reason=''):
    db.execute(text('''INSERT INTO fc_box_intake_audit(id,receipt_id,client_id,warehouse_id,action,actor,reason,before_json,after_json)
      VALUES(:id,:r,:c,:w,:action,:by,:reason,CAST(:before AS jsonb),CAST(:after AS jsonb))'''),
      {**params(c,w),'id':str(uuid.uuid4()),'r':rid,'action':action,'by':str(by or '')[:120], 'reason':reason,
       'before':json.dumps(before,ensure_ascii=False,default=str) if before is not None else None,
       'after':json.dumps(after,ensure_ascii=False,default=str) if after is not None else None})


def receipt(db,rid,c,w,lock=False):
    r=db.execute(text('SELECT * FROM fc_box_intakes WHERE id=:id AND client_id=:c AND warehouse_id=:w'+(' FOR UPDATE' if lock else '')),
                 {**params(c,w),'id':str(rid)}).mappings().first()
    if not r:raise HTTPException(404,detail='Приёмка не найдена на выбранном складе')
    return dict(r)


def active_receipt(db,rid,c,w):
    lock_scope(db,c,w);r=receipt(db,rid,c,w,True)
    if r['status']!='OPEN':raise HTTPException(409,detail='Сканирование завершено. Начните новую приёмку')
    return r


def new_receipt(db,rid,c,w,name,to_pallets,section,first_row,by,manual_pallets=False):
    lock_scope(db,c,w)
    section=text_value(section,80);name=text_value(name,240) or 'Приёмка коробов'
    old=db.execute(text('SELECT * FROM fc_box_intakes WHERE id=:id'),{'id':str(rid)}).mappings().first()
    if old:
        if old['client_id']!=str(c) or old['warehouse_id']!=str(w):raise HTTPException(409,detail='Номер приёмки уже занят')
        if old['name']!=name or old['to_pallets']!=to_pallets:raise HTTPException(409,detail='Приёмка с этим идентификатором уже создана с другими параметрами')
        return dict(old)
    if first_row is not None:position(1,first_row)
    db.execute(text('''INSERT INTO fc_box_intakes(id,client_id,warehouse_id,name,to_pallets,default_section,first_row,actor,manual_pallets)
       VALUES(:id,:c,:w,:name,:pal,:section,:row,:by,:manual)'''),
       {**params(c,w),'id':str(rid),'name':name,'pal':to_pallets,'section':section,'row':first_row,'by':by,'manual':bool(manual_pallets)})
    audit(db,c,w,str(rid),'CREATE',by,after={'name':name,'to_pallets':to_pallets,'section':section,'first_row':first_row,'manual_pallets':bool(manual_pallets)})
    return receipt(db,rid,c,w)



def open_or_create_tsd_receipt(db,c,w,name,by):
    ensure_r145f7_schema(db);lock_scope(db,c,w)
    name=text_value(name,240)
    if not name:raise HTTPException(422,detail='Укажите номер или название партии / приёмки')
    row=db.execute(text("SELECT * FROM fc_box_intakes WHERE client_id=:c AND warehouse_id=:w AND status='OPEN' AND LOWER(name)=LOWER(:name) ORDER BY created_at,id LIMIT 1 FOR UPDATE"),{**params(c,w),'name':name}).mappings().first()
    if row:
        r=dict(row)
        if not r.get('manual_pallets'):
            db.execute(text('UPDATE fc_box_intakes SET manual_pallets=true,to_pallets=true WHERE id=:id'),{'id':r['id']})
            r=receipt(db,r['id'],c,w,True)
        audit(db,c,w,r['id'],'TSD_JOIN',by,after={'name':name})
        return {**r,'reused':True}
    rid=uuid.uuid4()
    r=new_receipt(db,rid,c,w,name,True,'',None,by,manual_pallets=True)
    return {**r,'reused':False}


def _normalize_place_code(value):
    value=text_value(value,120)
    if not value:raise HTTPException(422,detail='Отсканируйте или введите палету')
    ru='ёйцукенгшщзхъфывапролджэячсмитьбюЁЙЦУКЕНГШЩЗХЪФЫВАПРОЛДЖЭЯЧСМИТЬБЮ'
    en='`qwertyuiop[]asdfghjkl;\'zxcvbnm,.~QWERTYUIOP{}ASDFGHJKL:"ZXCVBNM<>'
    return ''.join(en[ru.index(ch)] if ch in ru else ch for ch in value)


def select_tsd_pallet(db,rid,c,w,pallet_code,by):
    ensure_r145f7_schema(db);r=active_receipt(db,rid,c,w)
    if not r.get('manual_pallets'):raise HTTPException(409,detail='Эта приёмка не использует режим палет ТСД')
    code=_normalize_place_code(pallet_code)
    existing=db.execute(text("SELECT * FROM fc_box_intake_pallets WHERE receipt_id=:r AND LOWER(COALESCE(public_code,''))=LOWER(:code) FOR UPDATE"),{'r':r['id'],'code':code}).mappings().first()
    if existing:
        p=dict(existing);count=pallet_count(db,p['id'])
        if p.get('is_closed') and count>=int(p.get('capacity') or 16):
            raise HTTPException(409,detail={'code':'PALLET_FULL','message':f'Палета {code} заполнена: {count} из {p.get("capacity") or 16}. Нажмите «Добавить короба на палету» или отсканируйте следующую палету.','pallet_id':p['id'],'pallet_code':code,'box_count':count,'capacity':int(p.get('capacity') or 16)})
        db.execute(text('UPDATE fc_box_intakes SET current_pallet_id=:p WHERE id=:r'),{'p':p['id'],'r':r['id']})
        db.execute(text('UPDATE fc_box_intake_pallets SET is_closed=false WHERE id=:id'),{'id':p['id']})
        audit(db,c,w,r['id'],'PALLET_SELECT',by,after={'pallet_id':p['id'],'public_code':code,'number':p['number']})
        p=pallet(db,p['id'],r['id']);p['box_count']=count;return p
    other=db.execute(text("SELECT p.id,r.name FROM fc_box_intake_pallets p JOIN fc_box_intakes r ON r.id=p.receipt_id WHERE r.client_id=:c AND r.warehouse_id=:w AND r.status='OPEN' AND LOWER(COALESCE(p.public_code,''))=LOWER(:code) LIMIT 1"),{**params(c,w),'code':code}).mappings().first()
    if other:raise HTTPException(409,detail=f'Палета {code} уже используется в открытой приёмке «{other["name"]}»')
    from app.models import Location
    from sqlalchemy import select
    loc=db.execute(select(Location).where(Location.warehouse_id==uuid.UUID(str(w)),Location.is_active.is_(True),((Location.code==code)|(Location.barcode==code)))).scalars().first()
    if loc and str(loc.location_type or '').upper()!='PALLET':raise HTTPException(409,detail=f'Код {code} уже занят адресом типа {loc.location_type}')
    if not loc:
        loc=Location(id=uuid.uuid4(),warehouse_id=uuid.UUID(str(w)),parent_id=None,code=code,barcode=code,name=f'Палета {code} · {r["name"]}',location_type='PALLET',is_active=True)
        db.add(loc);db.flush()
    pid=str(uuid.uuid4());n=int(r['next_number'])
    db.execute(text("INSERT INTO fc_box_intake_pallets(id,receipt_id,number,location_id,section,first_row,capacity,is_closed,public_code) VALUES(:id,:r,:n,:loc,'',NULL,16,false,:code)"),{'id':pid,'r':r['id'],'n':n,'loc':str(loc.id),'code':code})
    db.execute(text('UPDATE fc_box_intakes SET current_pallet_id=:p,next_number=:n WHERE id=:r'),{'p':pid,'n':n+1,'r':r['id']})
    audit(db,c,w,r['id'],'PALLET_CREATE',by,after={'number':n,'location_id':str(loc.id),'public_code':code,'capacity':16})
    p=pallet(db,pid,r['id']);p['box_count']=0;return p

def make_section(db,w,section):
    if not section:return None
    from app.models import Location
    from sqlalchemy import select
    from app.routers.receiving import _manual_storage_token
    code='SEC-'+_manual_storage_token(section)
    existing=db.execute(select(Location).where(Location.warehouse_id==uuid.UUID(str(w)),Location.code==code)).scalar_one_or_none()
    if existing:
        if existing.location_type!='SECTION' or not existing.is_active:raise HTTPException(409,detail='Адрес секции занят другим типом или архивирован')
        return existing.id
    obj=Location(id=uuid.uuid4(),warehouse_id=uuid.UUID(str(w)),parent_id=None,code=code,barcode=code,
                 name='Секция '+section,location_type='SECTION',is_active=True)
    db.add(obj);db.flush();return obj.id


def make_location(db,r,pid,number,section):
    from app.models import Location
    parent=make_section(db,r['warehouse_id'],section)
    code='IN-'+r['id'].replace('-','')[:10].upper()+'-PAL-'+str(number).zfill(3)
    obj=Location(id=uuid.uuid4(),warehouse_id=uuid.UUID(r['warehouse_id']),parent_id=parent,code=code,barcode=code,
                 name=f"Палета {number} · {r['name']}",location_type='PALLET',is_active=True)
    db.add(obj);db.flush();return str(obj.id)


def receiving_location(db,w):
    from app.routers.receiving import _ensure_location
    return str(_ensure_location(db,uuid.UUID(str(w)),'ПРИЕМКА').id)


def make_container(db,c,w,lid,code):
    from app.models import Container
    from app.routers.receiving import _fc_box_storage_barcode
    obj=Container(id=uuid.uuid4(),client_id=uuid.UUID(str(c)),warehouse_id=uuid.UUID(str(w)),location_id=uuid.UUID(str(lid)),
                  name=code,barcode=_fc_box_storage_barcode(db,uuid.UUID(str(c)),uuid.UUID(str(w)),code),container_type='BOX',status='ACTIVE')
    db.add(obj);db.flush();return str(obj.id)


def pallet(db,pid,rid):
    row=db.execute(text('SELECT * FROM fc_box_intake_pallets WHERE id=:id AND receipt_id=:r FOR UPDATE'),{'id':str(pid),'r':str(rid)}).mappings().first()
    if not row:raise HTTPException(404,detail='Палета не найдена в приёмке')
    return dict(row)


def pallet_count(db,pid):
    return int(db.execute(text("""SELECT COUNT(*) FROM containers c JOIN fc_box_intake_pallets p ON CAST(c.location_id AS varchar)=p.location_id
       WHERE p.id=:id AND c.status='ACTIVE'"""),{'id':pid}).scalar() or 0)


def create_pallet(db,r,by,pid=None):
    pid=str(pid or uuid.uuid4());n=int(r['next_number']);lid=make_location(db,r,pid,n,r['default_section'])
    db.execute(text('''INSERT INTO fc_box_intake_pallets(id,receipt_id,number,location_id,section,first_row)
       VALUES(:id,:r,:n,:loc,:section,:row)'''),{'id':pid,'r':r['id'],'n':n,'loc':lid,'section':r['default_section'],'row':r['first_row']})
    db.execute(text('UPDATE fc_box_intakes SET current_pallet_id=:p,next_number=:n WHERE id=:r'),{'p':pid,'n':n+1,'r':r['id']})
    audit(db,r['client_id'],r['warehouse_id'],r['id'],'PALLET_CREATE',by,after={'number':n,'location_id':lid})
    return pallet(db,pid,r['id'])


def find_box(db,c,w,code,active=True):
    rows=db.execute(text('''SELECT CAST(id AS varchar) id,CAST(location_id AS varchar) location_id,name,barcode,status
      FROM containers WHERE client_id=:native_c AND warehouse_id=:native_w AND (name=:code OR barcode=:code)'''+(" AND status='ACTIVE'" if active else '')),
      {'native_c':c,'native_w':w,'code':code}).mappings().all()
    if len(rows)>1:raise HTTPException(409,detail=f'Название {code} неоднозначно. Исправьте имена коробов')
    return dict(rows[0]) if rows else None


def scan(db,rid,c,w,code,by):
    code=box_code(code);r=active_receipt(db,rid,c,w)
    old=db.execute(text('''SELECT b.*,p.number,p.public_code FROM fc_box_intake_boxes b LEFT JOIN fc_box_intake_pallets p ON p.id=b.pallet_id
       WHERE b.receipt_id=:r AND b.box_code=:code'''),{'r':r['id'],'code':code}).mappings().first()
    if old and db.execute(text("SELECT status FROM containers WHERE id=:id"),{'id':old['container_id']}).scalar()!='ACTIVE':
        raise HTTPException(409,detail='Этот короб был удалён. Для повторного использования нажмите «Восстановить» в списке удалённых коробов')
    if old:return {'added':False,'box_code':code,'pallet_number':old['number'],'pallet_code':old.get('public_code') or str(old['number'] or ''),'container_id':old['container_id']}
    if find_box(db,c,w,code,active=False):raise HTTPException(409,detail=f'Короб {code} уже существует. Он не принят второй раз. Откройте его в разделе «Короба»')
    if int(db.execute(text('SELECT COUNT(*) FROM fc_box_intake_boxes WHERE receipt_id=:r'),{'r':r['id']}).scalar() or 0)>=10000:
        raise HTTPException(422,detail='В одной приёмке не более 10 000 коробов')
    p=None
    if r['to_pallets']:
        p=pallet(db,r['current_pallet_id'],r['id']) if r['current_pallet_id'] else None
        if r.get('manual_pallets'):
            if not p:raise HTTPException(409,detail={'code':'PALLET_REQUIRED','message':'Сначала отсканируйте или введите палету'})
            count_before=pallet_count(db,p['id'])
            if p['is_closed'] or count_before>=p['capacity']:
                db.execute(text('UPDATE fc_box_intake_pallets SET is_closed=true WHERE id=:id'),{'id':p['id']})
                db.execute(text('UPDATE fc_box_intakes SET current_pallet_id=NULL WHERE id=:id'),{'id':r['id']})
                raise HTTPException(409,detail={'code':'PALLET_FULL','message':f'Палета {p.get("public_code") or p["number"]} заполнена: {count_before} из {p["capacity"]}. Отсканируйте следующую палету или добавьте места.','pallet_id':p['id'],'pallet_code':p.get('public_code') or str(p['number']),'box_count':count_before,'capacity':p['capacity']})
        else:
            if not p or p['is_closed'] or pallet_count(db,p['id'])>=p['capacity']:p=create_pallet(db,r,by)
        lid=p['location_id']
    else:lid=receiving_location(db,w)
    bid=make_container(db,c,w,lid,code)
    if p and p['first_row'] is not None:
        used={int(x[0]):int(x[1]) for x in db.execute(text('SELECT row_on_pallet,COUNT(*) FROM fc_box_routes WHERE location_id=:l GROUP BY row_on_pallet'),{'l':lid}).all()}
        side=next((s for s in range(p['first_row'],p['first_row']+(p['capacity']+7)//8) if used.get(s,0)<8),None)
        if side is None:raise HTTPException(409,detail='Все размеченные ряды заняты. Добавьте места или начните следующую палету')
        set_box_route(db,container_id=bid,client_id=c,warehouse_id=w,row_on_pallet=side,actor=by,reason='Сканирование при приёмке коробов')
    db.execute(text('''INSERT INTO fc_box_intake_boxes(id,receipt_id,pallet_id,container_id,box_code,sequence_no,actor)
       VALUES(:id,:r,:p,:b,:code,(SELECT COALESCE(MAX(sequence_no),0)+1 FROM fc_box_intake_boxes WHERE receipt_id=:lookup_receipt),:by)'''),{'id':str(uuid.uuid4()),'r':r['id'],'lookup_receipt':r['id'],'p':p['id'] if p else None,'b':bid,'code':code,'by':by})
    count=pallet_count(db,p['id']) if p else 0
    if p and count>=p['capacity']:
        db.execute(text('UPDATE fc_box_intake_pallets SET is_closed=true WHERE id=:id'),{'id':p['id']})
        if r.get('manual_pallets'):
            db.execute(text('UPDATE fc_box_intakes SET current_pallet_id=NULL WHERE id=:id'),{'id':r['id']})
    audit(db,c,w,r['id'],'BOX_SCAN',by,after={'container_id':bid,'box':code,'pallet_number':p['number'] if p else None,'location_id':lid})
    return {'added':True,'box_code':code,'container_id':bid,'pallet_number':p['number'] if p else None,'pallet_code':(p.get('public_code') or str(p['number'])) if p else None,'box_count':count,'pallet_capacity':int(p['capacity']) if p else None,'pallet_id':p['id'] if p else None,'pallet_full':bool(p and count>=p['capacity'])}


def next_pallet(db,rid,c,w,by):
    r=active_receipt(db,rid,c,w)
    if not r['to_pallets']:raise HTTPException(422,detail='В этой приёмке размещение на палеты выключено')
    if r['current_pallet_id']:
        db.execute(text('UPDATE fc_box_intake_pallets SET is_closed=true WHERE id=:id'),{'id':r['current_pallet_id']})
        db.execute(text('UPDATE fc_box_intakes SET current_pallet_id=NULL WHERE id=:id'),{'id':r['id']})
        audit(db,c,w,r['id'],'NEXT_PALLET',by,after={'next_number':r['next_number']})


def extend_pallet(db,rid,pid,c,w,count,by):
    lock_scope(db,c,w);r=receipt(db,rid,c,w,True);p=pallet(db,pid,r['id'])
    if r['status']=='CANCELLED':raise HTTPException(409,detail='Приёмка удалена')
    if not db.execute(text('SELECT 1 FROM locations WHERE id=:id AND is_active=true'),{'id':p['location_id']}).first():raise HTTPException(409,detail='Палета удалена')
    if type(count) is not int or not 1<=count<=800 or p['capacity']+count>800:raise HTTPException(422,detail='На одной палете можно зарегистрировать не более 800 коробов')
    db.execute(text('UPDATE fc_box_intake_pallets SET capacity=capacity+:n,is_closed=false WHERE id=:id'),{'n':count,'id':p['id']})
    db.execute(text('UPDATE fc_box_intakes SET current_pallet_id=:p WHERE id=:r'),{'p':p['id'],'r':r['id']})
    audit(db,c,w,r['id'],'PALLET_EXTEND',by,before={'capacity':p['capacity']},after={'capacity':p['capacity']+count,'pallet_number':p['number']})


def edit_pallet(db,rid,pid,c,w,section,first_row,by):
    lock_scope(db,c,w);r=receipt(db,rid,c,w,True);p=pallet(db,pid,r['id']);section=text_value(section,80)
    if r['status']=='CANCELLED' or not db.execute(text('SELECT 1 FROM locations WHERE id=:id AND is_active=true'),{'id':p['location_id']}).first():raise HTTPException(409,detail='Приёмка или палета удалена')
    if first_row is not None:position(1,first_row)
    boxes=db.execute(text('''SELECT b.container_id,c.location_id FROM fc_box_intake_boxes b
       JOIN containers c ON CAST(c.id AS varchar)=b.container_id
       WHERE b.pallet_id=:p AND c.status='ACTIVE' ORDER BY b.sequence_no,b.id'''),{'p':p['id']}).mappings().all()
    here=[b for b in boxes if str(b['location_id'])==p['location_id']]
    if db.execute(text("""SELECT 1 FROM fc_wh_pick_lines l JOIN fc_wh_pick_tasks t ON t.id=l.task_id
       WHERE l.container_id IN (SELECT container_id FROM fc_box_intake_boxes WHERE pallet_id=:p)
         AND t.status IN ('DRAFT','ASSIGNED','IN_PROGRESS','READY_FOR_ADMIN') LIMIT 1"""),{'p':p['id']}).first():
        raise HTTPException(409,detail='Короба палеты используются в незакрытой сборке. Сначала завершите или отмените её')
    # Checks and changes stay in the same transaction; active picks block re-routing.
    for b in here:set_box_route(db,container_id=b['container_id'],client_id=c,warehouse_id=w,row_on_pallet=None,actor=by,reason='Изменение разметки палеты')
    parent=make_section(db,w,section)
    db.execute(text('UPDATE locations SET parent_id=:parent WHERE id=:loc AND warehouse_id=:native_w'),{'parent':parent,'loc':p['location_id'],'native_w':w})
    if first_row is not None:
        for i,b in enumerate(here,1):
            side,_=position(i,first_row)
            set_box_route(db,container_id=b['container_id'],client_id=c,warehouse_id=w,row_on_pallet=side,actor=by,reason='Изменение разметки палеты')
    db.execute(text('UPDATE fc_box_intake_pallets SET section=:s,first_row=:row WHERE id=:p'),{'s':section,'row':first_row,'p':p['id']})
    db.execute(text('UPDATE fc_box_intakes SET default_section=:s,first_row=:row WHERE id=:r'),{'s':section,'row':first_row,'r':r['id']})
    audit(db,c,w,r['id'],'PALLET_ADDRESS',by,before=p,after={'pallet_id':pid,'section':section,'first_row':first_row})


def close_receipt(db,rid,c,w,by):
    lock_scope(db,c,w);r=receipt(db,rid,c,w,True)
    if r['status']=='CANCELLED':raise HTTPException(409,detail='Приёмка удалена')
    if r['status']=='RECEIVED':return
    db.execute(text("UPDATE fc_box_intakes SET status='RECEIVED',closed_at=now() WHERE id=:r"),{'r':r['id']})
    audit(db,c,w,r['id'],'CLOSE_SCANNING',by,before={'status':r['status']},after={'status':'RECEIVED'})


def detail(db,rid,c,w):
    r=receipt(db,rid,c,w)
    pallets=[dict(x) for x in db.execute(text('''SELECT p.*,l.code location_code FROM fc_box_intake_pallets p
      JOIN locations l ON CAST(l.id AS varchar)=p.location_id WHERE p.receipt_id=:r AND l.is_active=true ORDER BY p.number'''),{'r':r['id']}).mappings().all()]
    rows=[dict(x) for x in db.execute(text('''SELECT b.*,c.status,c.name container_name,c.barcode container_barcode,
      CAST(c.location_id AS varchar) location_id,l.code location_code,p.number pallet_number,
      COALESCE(q.quantity,0) quantity FROM fc_box_intake_boxes b
      JOIN containers c ON CAST(c.id AS varchar)=b.container_id LEFT JOIN locations l ON l.id=c.location_id
      LEFT JOIN fc_box_intake_pallets p ON p.id=b.pallet_id
      LEFT JOIN (SELECT container_id,SUM(quantity) quantity FROM inventory_balances
          WHERE client_id=:native_c AND warehouse_id=:native_w GROUP BY container_id) q ON q.container_id=c.id
      WHERE b.receipt_id=:r ORDER BY b.sequence_no DESC,b.id'''),{'r':r['id'],'native_c':c,'native_w':w}).mappings().all()]
    rows=enrich(db,c,w,rows)
    for p in pallets:p['box_count']=pallet_count(db,p['id'])
    events=[dict(x) for x in db.execute(text('SELECT * FROM fc_box_intake_audit WHERE receipt_id=:r ORDER BY created_at DESC,id DESC LIMIT 100'),{'r':r['id']}).mappings().all()]
    return {'receipt':r,'pallets':pallets,'boxes':rows,'audit':events}


def list_receipts(db,c,w,include_deleted=False):
    return [dict(x) for x in db.execute(text('''SELECT r.*,COUNT(b.id) FILTER(WHERE cont.status='ACTIVE') box_count FROM fc_box_intakes r
      LEFT JOIN fc_box_intake_boxes b ON b.receipt_id=r.id
      LEFT JOIN containers cont ON CAST(cont.id AS varchar)=b.container_id
      WHERE r.client_id=:c AND r.warehouse_id=:w AND (:all_rows OR r.status<>'CANCELLED')
      GROUP BY r.id ORDER BY LOWER(r.name),r.created_at DESC LIMIT 100'''),{**params(c,w),'all_rows':include_deleted}).mappings().all()]


def make_box_sheet(db,c,w,number,requests,by):
    lock_scope(db,c,w);number=text_value(number,120)
    if not number:raise HTTPException(422,detail='Укажите номер задания')
    digest=fingerprint(requests)
    old=db.execute(text('SELECT * FROM fc_box_sheets WHERE client_id=:c AND warehouse_id=:w AND number=:n'),{**params(c,w),'n':number}).mappings().first()
    if old:
        if old['source_hash']!=digest:raise HTTPException(409,detail='Задание с таким номером уже создано с другим списком коробов')
        return {'id':old['id'],**object_json(old['result_json'])}
    rows=[];errors=[];seen=set()
    for req in requests:
        b=find_box(db,c,w,req['container'])
        if not b or not b['location_id']:
            errors.append({**req,'error':'Короб не найден' if not b else 'У короба не указан адрес'});continue
        if b['id'] in seen:raise HTTPException(422,detail=f"Один короб указан дважды разными кодами: {req['container']}")
        seen.add(b['id'])
        loc=db.execute(text('SELECT code,is_active FROM locations WHERE id=:id AND warehouse_id=:native_w'),{'id':b['location_id'],'native_w':w}).mappings().first()
        if not loc or not loc['is_active']:
            errors.append({**req,'error':'Адрес короба архивирован или отсутствует'});continue
        rows.append({**req,'container_id':b['id'],'container_name':b['name'] or b['barcode'],'location_id':b['location_id'],'location_code':loc['code']})
    rows=sorted(enrich(db,c,w,rows),key=route_key)
    result={'number':number,'rows':rows,'errors':errors,'requested':len(requests),'found':len(rows)};sid=str(uuid.uuid4())
    db.execute(text('''INSERT INTO fc_box_sheets(id,client_id,warehouse_id,number,source_hash,result_json,actor)
      VALUES(:id,:c,:w,:n,:hash,CAST(:result AS jsonb),:by)'''),
      {**params(c,w),'id':sid,'n':number,'hash':digest,'result':json.dumps(result,ensure_ascii=False),'by':by})
    audit(db,c,w,None,'BOX_PICK_SHEET',by,after={'id':sid,'number':number,'requested':len(requests),'found':len(rows)})
    return {'id':sid,**result}


def get_box_sheet(db,sid,c,w):
    r=db.execute(text('SELECT * FROM fc_box_sheets WHERE id=:id AND client_id=:c AND warehouse_id=:w'),{**params(c,w),'id':str(sid)}).mappings().first()
    if not r:raise HTTPException(404,detail='Сборочный лист не найден')
    return {'id':r['id'],'created_at':r['created_at'],**object_json(r['result_json'])}


def layout_snapshot(db,rid,c,w,requests):
    from app.services.local_catalog import catalog
    receipt(db,rid,c,w)
    known={};identities={};out=[];occupied=set();membership={r[0] for r in db.execute(text('SELECT container_id FROM fc_box_intake_boxes WHERE receipt_id=:r'),{'r':str(rid)}).all()}
    for req in requests:
        b=find_box(db,c,w,req['container'])
        if not b or b['id'] not in membership or not b['location_id']:
            raise HTTPException(422,detail=f"Строка {req['row']}: короб {req['container']} не принят в этой приёмке или уже убран")
        loc=db.execute(text('SELECT code,is_active FROM locations WHERE id=:id AND warehouse_id=:native_w'),{'id':b['location_id'],'native_w':w}).mappings().first()
        if not loc or not loc['is_active']:raise HTTPException(409,detail='Адрес короба недоступен')
        if req['barcode'] not in known:
            products=catalog(db,c,req['barcode'])
            if len(products)>1 or (products and products[0].get('wb_conflict')):raise HTTPException(409,detail=f"ШК {req['barcode']} неоднозначен в справочнике")
            known[req['barcode']]=products[0] if products else {}
        product=known[req['barcode']]
        article=str(product.get('article') or req['article'] or '').strip()
        size=str(product.get('size') or req['size'] or '').strip()
        for key in ('article','size'):
            if req.get(key) and product.get(key) and str(req[key]).casefold()!=str(product[key]).casefold():
                raise HTTPException(409,detail=f"Строка {req['row']}: артикул/размер расходится с сохранённым товаром {req['barcode']}")
        if not article or not size:raise HTTPException(422,detail=f"Строка {req['row']}: для нового товара укажите артикул и размер")
        identity=(article.casefold(),size.casefold())
        if req['barcode'] in identities and identities[req['barcode']]!=identity:raise HTTPException(422,detail=f"Строка {req['row']}: один ШК указан с разными артикулами или размерами")
        identities[req['barcode']]=identity
        # catalog may group aliases: local registration and this importer require one exact local SKU.
        skus=db.execute(text('SELECT CAST(id AS varchar) id,is_active FROM skus WHERE client_id=:native_c AND barcode=:bc'),{'native_c':c,'bc':req['barcode']}).mappings().all()
        if len(skus)>1 or (skus and not skus[0]['is_active']):raise HTTPException(409,detail='ШК дублируется или товар архивирован: '+req['barcode'])
        amount=db.execute(text('SELECT COALESCE(SUM(quantity),0) FROM inventory_balances WHERE client_id=:native_c AND warehouse_id=:native_w AND container_id=:native_box'),{'native_c':c,'native_w':w,'native_box':b['id']}).scalar() or 0
        if amount:occupied.add(b['name'] or b['barcode'])
        out.append({**req,'article':article,'size':size,'sku_id':skus[0]['id'] if skus else '',
                    'container_id':b['id'],'container_name':b['name'] or b['barcode'],'location_id':b['location_id'],
                    'location_code':loc['code'],'box_quantity_before':str(amount)})
    return {'rows':out,'occupied_boxes':sorted(occupied)}


def preview_layout(db,rid,c,w,requests,by):
    lock_scope(db,c,w);r=receipt(db,rid,c,w,True)
    if r['status']=='CANCELLED':raise HTTPException(409,detail='Приёмка удалена')
    digest=fingerprint(requests)
    existing=db.execute(text("SELECT * FROM fc_box_layout_plans WHERE receipt_id=:r AND content_hash=:hash AND status='POSTED'"),{'r':str(rid),'hash':digest}).mappings().first()
    if existing:
        status=db.execute(text('SELECT status FROM inventory_operations WHERE id=:native_op'),{'native_op':existing['operation_id']}).scalar()
        if status is None or str(status).upper()=='CANCELLED':raise HTTPException(409,detail='Эта раскладка уже проводилась и была отменена. Создайте новый документ приёмки')
        return {'id':existing['id'],'status':'POSTED','operation_id':existing['operation_id'],'already_posted':True,**object_json(existing['rows_json'])['plan']}
    plan=layout_snapshot(db,rid,c,w,requests);pid=str(uuid.uuid4())
    db.execute(text('''INSERT INTO fc_box_layout_plans(id,receipt_id,client_id,warehouse_id,content_hash,rows_json,fingerprint,actor)
      VALUES(:id,:r,:c,:w,:hash,CAST(:rows AS jsonb),:fingerprint,:by)'''),
      {**params(c,w),'id':pid,'r':str(rid),'hash':digest,'rows':json.dumps({'input':requests,'plan':plan},ensure_ascii=False),'fingerprint':fingerprint(plan),'by':by})
    return {'id':pid,'status':'PREVIEW',**plan}


def commit_layout(db,pid,c,w,confirm_occupied,by):
    from decimal import Decimal
    from app.models import SKU, InventoryOperation
    from app.schemas.inventory import OperationCreate, StockLine
    from app.services.inventory import execute_operation
    from app.services.stock_core_v2 import reconcile_stock_bucket
    lock_scope(db,c,w)
    plan=db.execute(text('SELECT * FROM fc_box_layout_plans WHERE id=:id AND client_id=:c AND warehouse_id=:w FOR UPDATE'),{**params(c,w),'id':str(pid)}).mappings().first()
    if not plan:raise HTTPException(404,detail='Предпросмотр не найден')
    if plan['status']=='POSTED':
        op=db.get(InventoryOperation,uuid.UUID(plan['operation_id']))
        if not op or str(op.status or 'COMPLETED').upper()=='CANCELLED':raise HTTPException(409,detail='Эта раскладка уже проводилась и была отменена. Создайте новый документ приёмки')
        return {'operation_id':plan['operation_id'],'already_posted':True}
    r=receipt(db,plan['receipt_id'],c,w,True)
    if r['status']=='CANCELLED':raise HTTPException(409,detail='Приёмка удалена')
    content=object_json(plan['rows_json']);requests=content['input']
    for code in sorted({r['barcode'] for r in requests}):
        db.execute(text('SELECT pg_advisory_xact_lock(hashtext(:key))'),{'key':f'fc:local-sku:{c}:{code}'})
    fresh=layout_snapshot(db,plan['receipt_id'],c,w,requests)
    if fingerprint(fresh)!=plan['fingerprint']:raise HTTPException(409,detail='Короба, адреса, остатки или справочник изменились. Загрузите раскладку заново и проверьте её')
    if fresh['occupied_boxes'] and not confirm_occupied:raise HTTPException(409,detail='В коробах уже есть учтённый товар. Подтвердите добавление к остаткам')
    skus={};amounts={};before={}
    for r in fresh['rows']:
        if r['barcode'] not in skus:
            if r['sku_id']:sid=uuid.UUID(r['sku_id'])
            else:
                sid=uuid.uuid4();obj=SKU(id=sid,client_id=c,sku_code='LOCAL-'+uuid.uuid5(c,r['barcode']).hex,
                    barcode=r['barcode'],article=r['article'],size=r['size'],name=r['article'],unit='PCS',is_active=True)
                db.add(obj);db.flush()
            skus[r['barcode']]=sid
        key=(skus[r['barcode']],uuid.UUID(r['location_id']),uuid.UUID(r['container_id']))
        amounts[key]=amounts.get(key,0)+r['qty']
        if amounts[key]>10000000:raise HTTPException(422,detail='Слишком большое количество одного товара в коробе')
    lines=[]
    for key,qty in sorted(amounts.items(),key=lambda x:tuple(map(str,x[0]))):
        sid,lid,bid=key
        value=reconcile_stock_bucket(db,client_id=c,warehouse_id=w,sku_id=sid,location_id=lid,container_id=bid,preferred_bucket='FBS_AVAILABLE',lock=True)
        before[key]=Decimal(value['physical'])
        lines.append(StockLine(sku_id=sid,quantity=Decimal(qty),destination_location_id=lid,destination_container_id=bid,reason='BOX_LAYOUT_MAIN'))
    op=execute_operation(db,OperationCreate(client_id=c,warehouse_id=w,operation_type='RECEIVING',
       idempotency_key='box-layout-'+str(pid),performed_by=by,reference='Раскладка '+plan['receipt_id'][:8],
       comment='Приёмка товара в принятые короба · ЗОНА:MAIN',lines=lines),commit=False)
    db.flush()
    for key,qty in amounts.items():
        sid,lid,bid=key
        value=reconcile_stock_bucket(db,client_id=c,warehouse_id=w,sku_id=sid,location_id=lid,container_id=bid,preferred_bucket='STORAGE_ONLY',lock=True)
        if Decimal(value['physical'])!=before[key]+qty:raise HTTPException(409,detail='Контроль количества не пройден. Приёмка отменена')
    db.execute(text("UPDATE fc_box_layout_plans SET status='POSTED',operation_id=:op WHERE id=:id"),{'op':str(op.id),'id':str(pid)})
    audit(db,c,w,plan['receipt_id'],'LAYOUT_POST',by,after={'plan_id':str(pid),'operation_id':str(op.id),'units':sum(amounts.values())})
    return {'operation_id':str(op.id),'units':sum(amounts.values()),'already_posted':False}


def empty_box_change(db,bid,c,w,by,destination=None,archive=False):
    lock_scope(db,c,w)
    box=db.execute(text('''SELECT CAST(id AS varchar) id,CAST(location_id AS varchar) location_id,name,barcode,status
       FROM containers WHERE id=:native_box AND client_id=:native_c AND warehouse_id=:native_w FOR UPDATE'''),
       {'native_box':bid,'native_c':c,'native_w':w}).mappings().first()
    if not box:raise HTTPException(404,detail='Короб не найден на выбранном складе')
    if box['status']!='ACTIVE':
        if archive:return {'ok':True,'archived':True}
        raise HTTPException(409,detail='Короб архивный')
    if db.execute(text('SELECT 1 FROM inventory_balances WHERE container_id=:native_box AND quantity<>0 LIMIT 1'),{'native_box':bid}).first():
        raise HTTPException(409,detail='В коробе есть учтённый товар. Сначала перенесите или спишите его через состав короба, затем удалите пустой короб')
    if db.execute(text('''SELECT 1 FROM fc_wh_pick_lines l JOIN fc_wh_pick_tasks t ON t.id=l.task_id WHERE l.container_id=:box
      AND t.status IN ('DRAFT','ASSIGNED','IN_PROGRESS','READY_FOR_ADMIN') LIMIT 1'''),{'box':str(bid)}).first():
        raise HTTPException(409,detail='Короб используется в незакрытом сборочном задании')
    if db.execute(text('''SELECT 1 FROM fc_stock_reservations WHERE source_container_id=:box
      AND status IN ('ACTIVE','CONSUMING','CANCELLING','PACKING_WRITE_OFF','SALE_RESERVED') AND quantity>0 LIMIT 1'''),{'box':str(bid)}).first():
        raise HTTPException(409,detail='У короба остался активный резерв. Сначала завершите сверку')
    if archive:
        db.execute(text("UPDATE containers SET status='ARCHIVED' WHERE id=:native_box"),{'native_box':bid})
        after={'status':'ARCHIVED','box':box['name'] or box['barcode'],'container_id':str(bid)}
    else:
        loc=db.execute(text('SELECT code FROM locations WHERE id=:loc AND warehouse_id=:native_w AND is_active=true'),{'loc':destination,'native_w':w}).mappings().first()
        if not loc:raise HTTPException(404,detail='Адрес назначения не найден на выбранном складе')
        if db.execute(text("SELECT 1 FROM containers WHERE location_id=:loc AND client_id<>:native_c AND status='ACTIVE' LIMIT 1"),{'loc':destination,'native_c':c}).first():raise HTTPException(409,detail='На этом адресе есть короба другого клиента')
        if db.execute(text('SELECT 1 FROM inventory_balances WHERE location_id=:loc AND client_id<>:native_c AND quantity>0 LIMIT 1'),{'loc':destination,'native_c':c}).first():raise HTTPException(409,detail='На этом адресе есть товар другого клиента')
        db.execute(text('UPDATE containers SET location_id=:loc WHERE id=:native_box'),{'loc':destination,'native_box':bid})
        after={'location_id':str(destination),'location_code':loc['code']}
    rid=db.execute(text('SELECT receipt_id FROM fc_box_intake_boxes WHERE container_id=:b ORDER BY created_at DESC LIMIT 1'),{'b':str(bid)}).scalar()
    audit(db,c,w,rid,'BOX_ARCHIVE' if archive else 'BOX_MOVE',by,before=dict(box),after=after)
    return {'ok':True,**after}
