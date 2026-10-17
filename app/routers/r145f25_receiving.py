"""r145f25: explicit client management and bounded receiving operations."""
from __future__ import annotations
import hashlib,json,uuid,re
from datetime import datetime,timezone
from fastapi import APIRouter,Depends,HTTPException,Request,Query
from pydantic import BaseModel,Field
from sqlalchemy import select,text,func,or_
from sqlalchemy.orm import Session
from app.core.database import get_db
from app.models import Client,Warehouse,Location,Container,InventoryBalance
from app.services.r145f25_places import scope,uid,assert_place,box_record,public_code,scoped_locations,readonly_limits,location_record,location_map
router=APIRouter(tags=['r145f25'])


def admin(req):
    actor=getattr(req.state,'username',None)
    if not actor:raise HTTPException(403,detail='\u041d\u0443\u0436\u0435\u043d \u0432\u0445\u043e\u0434 \u0430\u0434\u043c\u0438\u043d\u0438\u0441\u0442\u0440\u0430\u0442\u043e\u0440\u0430')
    return str(actor)

def record(c):return {'id':str(c.id),'name':c.name,'code':c.code,'is_active':c.is_active}

def _ensure_client_audit(db):
    # Lightweight bootstrap: full r145f25_scope_db.install() is not required to create a client.
    db.execute(text('''CREATE TABLE IF NOT EXISTS fc_r145f25_client_audit (
 id uuid PRIMARY KEY, client_id uuid NOT NULL, action text NOT NULL,
 old_name text, new_name text, actor text NOT NULL, created_at timestamptz NOT NULL DEFAULT now())'''))

def audit(db,c,action,old,new,actor):
    _ensure_client_audit(db)
    db.execute(text('INSERT INTO fc_r145f25_client_audit(id,client_id,action,old_name,new_name,actor) VALUES(:id,:c,:a,:o,:n,:u)'),{'id':str(uuid.uuid4()),'c':str(c),'a':action,'o':old,'n':new,'u':actor})

@router.get('/masterdata/clients')
@router.get('/r145f25/clients')
def clients(include_archived:bool=False,db:Session=Depends(get_db)):
    s=select(Client).order_by(Client.name,Client.id)
    if not include_archived:s=s.where(Client.is_active.is_(True))
    return [record(c) for c in db.execute(s).scalars()]

class ClientInput(BaseModel):
    name:str=Field(min_length=1,max_length=255)
    code:str|None=Field(default=None,max_length=64)

@router.post('/masterdata/clients')
@router.post('/r145f25/clients')
def create_client(payload:ClientInput,request:Request,db:Session=Depends(get_db)):
    actor=admin(request);name=payload.name.strip()
    if not name:raise HTTPException(422,detail='\u0423\u043a\u0430\u0436\u0438\u0442\u0435 \u0438\u043c\u044f \u043a\u043b\u0438\u0435\u043d\u0442\u0430')
    c=Client(id=uuid.uuid4(),code='CL_'+uuid.uuid4().hex[:20].upper(),name=name,is_active=True);db.add(c);db.flush();audit(db,c.id,'CREATE',None,name,actor);db.commit();return record(c)

@router.patch('/r145f25/clients/{client_id}')
def rename_client(client_id:uuid.UUID,payload:ClientInput,request:Request,db:Session=Depends(get_db)):
    actor=admin(request);c=db.execute(select(Client).where(Client.id==client_id).with_for_update()).scalar_one_or_none()
    if not c:raise HTTPException(404,detail='\u041a\u043b\u0438\u0435\u043d\u0442 \u043d\u0435 \u043d\u0430\u0439\u0434\u0435\u043d')
    name=payload.name.strip()
    if not name:raise HTTPException(422,detail='\u0423\u043a\u0430\u0436\u0438\u0442\u0435 \u0438\u043c\u044f')
    old=c.name;c.name=name;audit(db,c.id,'RENAME',old,name,actor);db.commit();return record(c)


def archive_preview(db,c):
    blockers=[]
    n=db.execute(select(func.count()).select_from(InventoryBalance).where(InventoryBalance.client_id==c.id,InventoryBalance.quantity!=0)).scalar()
    if n:blockers.append({'kind':'stock','name':'\u041e\u0441\u0442\u0430\u0442\u043a\u0438 \u0432\u043e \u0432\u0441\u0435\u0445 \u0441\u043a\u043b\u0430\u0434\u0430\u0445','count':int(n)})
    n=db.execute(select(func.count()).select_from(Container).where(Container.client_id==c.id,Container.status.in_(['ACTIVE','DEFECT_ACTIVE']))).scalar()
    if n:blockers.append({'kind':'boxes','name':'\u0410\u043a\u0442\u0438\u0432\u043d\u044b\u0435 \u043a\u043e\u0440\u043e\u0431\u0430 (\u0432\u043a\u043b\u044e\u0447\u0430\u044f \u043f\u0443\u0441\u0442\u044b\u0435)','count':int(n)})
    if db.bind.dialect.name=='postgresql':
        tests=[('fc_stock_reservations',"status IN ('ACTIVE','SALE_RESERVED','CONSUMING','CANCELLING','PACKING_WRITE_OFF')",'\u0420\u0435\u0437\u0435\u0440\u0432\u044b'),
          ('fc_stocks_v2','storage_only<>0 OR fbs_available<>0 OR reserved<>0','\u0423\u0447\u0451\u0442 MAIN/FBS'),
          ('fc_r145f19_tsd_receipts',"status='DRAFT'",'\u0427\u0435\u0440\u043d\u043e\u0432\u0438\u043a\u0438 \u0422\u0421\u0414'),
          ('fc_pvz_receiving_sessions',"status='DRAFT'",'\u0427\u0435\u0440\u043d\u043e\u0432\u0438\u043a\u0438 \u041f\u0412\u0417'),
          ('fc_box_intakes',"status='OPEN'",'\u041d\u0435\u0437\u0430\u043a\u0440\u044b\u0442\u044b\u0435 \u043f\u0440\u0438\u0451\u043c\u043a\u0438 \u043a\u043e\u0440\u043e\u0431\u043e\u0432'),
          ('fc_wh_pick_tasks',"status IN ('DRAFT','ASSIGNED','IN_PROGRESS','READY_FOR_ADMIN')",'\u0421\u0431\u043e\u0440\u043a\u0438'),
          ('fc_transit_batches',"status='OPEN'",'\u0422\u0440\u0430\u043d\u0437\u0438\u0442')]
        for table,where,name in tests:
            if db.execute(text('SELECT to_regclass(:t)'),{'t':table}).scalar():
                n=db.execute(text(f'SELECT count(*) FROM {table} WHERE CAST(client_id AS text)=:c AND ({where})'),{'c':str(c.id)}).scalar()
                if n:blockers.append({'kind':table,'name':name,'count':int(n)})
    if db.bind.dialect.name=='postgresql' and db.execute(text("SELECT to_regclass('fc_pvz_lines')")).scalar():
        n=db.execute(text("SELECT count(*) FROM fc_pvz_lines l JOIN fc_pvz_batches b ON b.id=l.batch_id WHERE b.client_id=:c AND b.kind='DEFECT' AND b.status<>'CANCELLED' AND b.deleted_at IS NULL AND l.deleted_at IS NULL AND l.quantity>l.written_off_qty"),{'c':str(c.id)}).scalar()
        if n:blockers.append({'kind':'defect','name':'\u041e\u0441\u0442\u0430\u0442\u043e\u043a \u0431\u0440\u0430\u043a\u0430','count':int(n)})
    data={'client_id':str(c.id),'name':c.name,'is_active':c.is_active,'blockers':blockers}
    data['fingerprint']=hashlib.sha256(json.dumps(data,sort_keys=True,ensure_ascii=True).encode()).hexdigest();data['can_archive']=not blockers
    return data

@router.get('/r145f25/clients/{client_id}/archive-preview')
def preview(client_id:uuid.UUID,request:Request,db:Session=Depends(get_db)):
    admin(request);c=db.get(Client,client_id)
    if not c:raise HTTPException(404,detail='NOT_FOUND')
    return archive_preview(db,c)

class Archive(BaseModel):
    confirmed:bool=False
    fingerprint:str=Field(min_length=64,max_length=64)

@router.post('/r145f25/clients/{client_id}/archive')
def archive(client_id:uuid.UUID,payload:Archive,request:Request,db:Session=Depends(get_db)):
    actor=admin(request);readonly_limits(db)
    c=db.execute(select(Client).where(Client.id==client_id).with_for_update()).scalar_one_or_none()
    if not c:raise HTTPException(404,detail='NOT_FOUND')
    if not payload.confirmed:raise HTTPException(422,detail='\u041d\u0443\u0436\u043d\u043e \u043f\u043e\u0434\u0442\u0432\u0435\u0440\u0436\u0434\u0435\u043d\u0438\u0435')
    if not c.is_active:return {'ok':True,'already_archived':True}
    check=archive_preview(db,c)
    if check['blockers']:raise HTTPException(409,detail={'message':'\u0423 \u043a\u043b\u0438\u0435\u043d\u0442\u0430 \u0435\u0441\u0442\u044c \u043e\u0441\u0442\u0430\u0442\u043a\u0438, \u043a\u043e\u0440\u043e\u0431\u0430 \u0438\u043b\u0438 \u043d\u0435\u0437\u0430\u043a\u0440\u044b\u0442\u044b\u0435 \u0437\u0430\u0434\u0430\u043d\u0438\u044f. \u0414\u0430\u043d\u043d\u044b\u0435 \u043d\u0435 \u0443\u0434\u0430\u043b\u044f\u043b\u0438\u0441\u044c.','blockers':check['blockers']})
    if check['fingerprint']!=payload.fingerprint:raise HTTPException(409,detail='\u0414\u0430\u043d\u043d\u044b\u0435 \u0438\u0437\u043c\u0435\u043d\u0438\u043b\u0438\u0441\u044c. \u041e\u0431\u043d\u043e\u0432\u0438\u0442\u0435 \u043f\u0440\u043e\u0432\u0435\u0440\u043a\u0443.')
    c.is_active=False;audit(db,c.id,'ARCHIVE',c.name,c.name,actor);db.commit();return {'ok':True,'archived':True,'history_preserved':True}

@router.post('/r145f25/clients/{client_id}/restore')
def restore(client_id:uuid.UUID,request:Request,db:Session=Depends(get_db)):
    actor=admin(request);c=db.execute(select(Client).where(Client.id==client_id).with_for_update()).scalar_one_or_none()
    if not c:raise HTTPException(404,detail='NOT_FOUND')
    c.is_active=True;audit(db,c.id,'RESTORE',c.name,c.name,actor);db.commit();return record(c)

@router.get('/r145f25/locations')
def locations(client_id:uuid.UUID,warehouse_id:uuid.UUID,q:str|None=None,db:Session=Depends(get_db)):
    return scoped_locations(db,client_id,warehouse_id,q)

class BoxOpen(BaseModel):
    client_id:uuid.UUID;warehouse_id:uuid.UUID
    box_value:str=Field(min_length=1,max_length=255)
    box_mode:str='SCAN';section:str|None=None;aisle:str|None=None;pallet:str|None=None;row_on_pallet:int|None=Field(default=None,ge=1,le=999)
    force_move:bool=False


def manual_pallet_payload(db,payload):
    raw=str(payload.pallet or '').strip()
    if not raw:return payload
    code='PAL-'+str(int(raw)).zfill(3) if raw.isdigit() else raw
    m=re.fullmatch(r'PAL-0*(\d+)',code,re.I)
    if m:code='PAL-'+str(int(m.group(1))).zfill(3)
    candidates=db.execute(select(Location).where(Location.warehouse_id==payload.warehouse_id,
        or_(func.lower(Location.code)==code.lower(),func.lower(Location.barcode)==raw.lower()))).scalars().all()
    if re.fullmatch(r'PAL-\d+',code,re.I):
        legacy=db.execute(select(Location).where(Location.warehouse_id==payload.warehouse_id,Location.code.ilike('%-'+code))).scalars().all()
        candidates=list({str(x.id):x for x in candidates+[x for x in legacy if public_code(x).casefold()==code.casefold()]}.values())
    if len(candidates)>1:raise HTTPException(409,detail='\u041d\u043e\u043c\u0435\u0440 \u043f\u0430\u043b\u0435\u0442\u044b \u043d\u0435\u043e\u0434\u043d\u043e\u0437\u043d\u0430\u0447\u0435\u043d. \u0421\u043a\u0430\u043d\u0438\u0440\u0443\u0439\u0442\u0435 QR \u043d\u0443\u0436\u043d\u043e\u0439 \u043f\u0430\u043b\u0435\u0442\u044b.')
    if candidates:
        loc=candidates[0]
        if loc.location_type not in ('PALLET','PALLET_PLACE'):raise HTTPException(409,detail='\u042d\u0442\u043e \u043d\u0435 \u043f\u0430\u043b\u0435\u0442\u0430')
        assert_place(db,payload.client_id,payload.warehouse_id,loc.id)
        return payload.model_copy(update={'pallet':loc.code})
    return payload.model_copy(update={'pallet':code})

@router.post('/r145f25/receiving/box')
def fast_box(payload:BoxOpen,request:Request,db:Session=Depends(get_db)):
    scope(db,payload.client_id,payload.warehouse_id);readonly_limits(db)
    from app.services.box_intake_rules import normalize_box_scan
    raw=payload.box_value.strip();scan=normalize_box_scan(raw) if payload.box_mode=='SCAN' else raw
    matches=db.execute(select(Container).where(Container.client_id==payload.client_id,Container.warehouse_id==payload.warehouse_id,Container.status=='ACTIVE',or_(func.lower(Container.name).in_([raw.lower(),scan.lower()]),func.lower(Container.barcode).in_([raw.lower(),scan.lower()]))).limit(3)).scalars().all()
    if len(matches)>1:raise HTTPException(409,detail='\u041d\u0430\u0437\u0432\u0430\u043d\u0438\u0435 \u043a\u043e\u0440\u043e\u0431\u0430 \u043d\u0435\u043e\u0434\u043d\u043e\u0437\u043d\u0430\u0447\u043d\u043e. \u0421\u043a\u0430\u043d\u0438\u0440\u0443\u0439\u0442\u0435 QR \u043a\u043e\u043d\u043a\u0440\u0435\u0442\u043d\u043e\u0433\u043e \u043a\u043e\u0440\u043e\u0431\u0430.')
    if matches:
        box=matches[0]
        if box.container_type in ('DEFECT_BOX','TRANSIT_BOX','FBO_BOX'):raise HTTPException(409,detail='\u042d\u0442\u043e \u043a\u043e\u0440\u043e\u0431 \u0441\u043f\u0435\u0446\u0438\u0430\u043b\u044c\u043d\u043e\u0433\u043e \u0445\u0440\u0430\u043d\u0435\u043d\u0438\u044f')
        if not box.location_id:raise HTTPException(409,detail='\u0421\u043d\u0430\u0447\u0430\u043b\u0430 \u0443\u043a\u0430\u0436\u0438\u0442\u0435 \u0430\u0434\u0440\u0435\u0441 \u043a\u043e\u0440\u043e\u0431\u0430')
        assert_place(db,payload.client_id,payload.warehouse_id,box.location_id)
        # Open exactly this existing box. Old section/pallet form values must never move it.
        result=box_record(db,box,payload.warehouse_id);db.rollback();return result
    foreign=db.execute(select(Container.id).where(Container.warehouse_id==payload.warehouse_id,Container.client_id!=payload.client_id,Container.status.in_(['ACTIVE','DEFECT_ACTIVE']),or_(func.lower(Container.name).in_([raw.lower(),scan.lower()]),func.lower(Container.barcode).in_([raw.lower(),scan.lower()]))).limit(1)).first()
    if foreign:raise HTTPException(409,detail='\u041a\u043e\u0440\u043e\u0431 \u0437\u0430\u043d\u044f\u0442 \u0434\u0440\u0443\u0433\u0438\u043c \u043a\u043b\u0438\u0435\u043d\u0442\u043e\u043c \u043d\u0430 \u044d\u0442\u043e\u043c \u0441\u043a\u043b\u0430\u0434\u0435. \u0412\u044b\u0431\u0435\u0440\u0438\u0442\u0435 \u0441\u0432\u043e\u0439 \u043a\u043e\u0440\u043e\u0431.')
    if payload.box_mode=='SCAN':
        elsewhere=db.execute(select(Container.id).where(Container.client_id==payload.client_id,Container.warehouse_id!=payload.warehouse_id,Container.status.in_(['ACTIVE','DEFECT_ACTIVE']),func.lower(Container.barcode).in_([raw.lower(),scan.lower()]))).first()
        if elsewhere:raise HTTPException(409,detail='\u042d\u0442\u043e\u0442 QR \u043a\u043e\u0440\u043e\u0431\u0430 \u043f\u0440\u0438\u043d\u0430\u0434\u043b\u0435\u0436\u0438\u0442 \u0434\u0440\u0443\u0433\u043e\u043c\u0443 \u0441\u043a\u043b\u0430\u0434\u0443. \u0414\u0430\u043d\u043d\u044b\u0435 \u043d\u0435 \u043f\u0435\u0440\u0435\u043d\u0435\u0441\u0435\u043d\u044b.')
    if re.match(r'^(?:FCBOX-|FCLOC-|[0-9a-f]{8}-[0-9a-f-]{27})',scan,re.I):raise HTTPException(404,detail='\u041a\u043e\u0440\u043e\u0431 \u043d\u0435 \u043d\u0430\u0439\u0434\u0435\u043d \u0443 \u044d\u0442\u043e\u0433\u043e \u043a\u043b\u0438\u0435\u043d\u0442\u0430 \u043d\u0430 \u044d\u0442\u043e\u043c \u0441\u043a\u043b\u0430\u0434\u0435')
    # Create path retains the installed receiving logic and its exact 58x40 label support.
    payload=manual_pallet_payload(db,payload)
    from app.routers.receiving import ensure_manual_storage_box,ManualStorageBoxEnsureRequest
    result=ensure_manual_storage_box(ManualStorageBoxEnsureRequest(**payload.model_dump()),request,db)
    box=db.get(Container,uid(result['container_id']));return {**result,**box_record(db,box,payload.warehouse_id,created=bool(result.get('created')))}

class Pallet(BaseModel):
    client_id:uuid.UUID;warehouse_id:uuid.UUID
    pallet_code:str=Field(min_length=1,max_length=120)
    receiver:str|None=None

@router.post('/r145f25/box-intake/{rid}/pallet')
def choose_pallet(rid:uuid.UUID,payload:Pallet,request:Request,db:Session=Depends(get_db)):
    scope(db,payload.client_id,payload.warehouse_id);readonly_limits(db)
    from app.services import box_intake as service
    service.ensure_r145f7_schema(db)
    r=service.active_receipt(db,rid,payload.client_id,payload.warehouse_id)
    raw=service._normalize_place_code(payload.pallet_code).strip()
    code='PAL-'+raw.zfill(3) if raw.isdigit() else raw
    m=re.fullmatch(r'PAL-0*(\d+)',code,re.I)
    if m:code='PAL-'+m.group(1).zfill(3)
    candidates=db.execute(select(Location).where(Location.warehouse_id==payload.warehouse_id,Location.is_active.is_(True),or_(func.lower(Location.code)==code.lower(),func.lower(Location.barcode)==code.lower()))).scalars().all()
    # Legacy generated codes are aliases only when unambiguous in this warehouse.
    if not re.match(r'^(?:FCLOC-|IN-[0-9a-f]{6,}-|[0-9a-f]{8}-)',raw,re.I):
        legacy=db.execute(select(Location).where(Location.warehouse_id==payload.warehouse_id,Location.is_active.is_(True),Location.code.ilike('%-'+code))).scalars().all()
        candidates=list({str(x.id):x for x in candidates+[x for x in legacy if public_code(x).casefold()==code.casefold()]}.values())
    if len(candidates)>1:raise HTTPException(409,detail='\u041d\u0430 \u0441\u043a\u043b\u0430\u0434\u0435 \u043d\u0435\u0441\u043a\u043e\u043b\u044c\u043a\u043e \u043f\u0430\u043b\u0435\u0442 \u0441 \u0442\u0430\u043a\u0438\u043c \u043d\u043e\u043c\u0435\u0440\u043e\u043c. \u0421\u043a\u0430\u043d\u0438\u0440\u0443\u0439\u0442\u0435 QR \u043d\u0443\u0436\u043d\u043e\u0439 \u043f\u0430\u043b\u0435\u0442\u044b.')
    if candidates:
        loc=candidates[0]
        if loc.location_type not in ('PALLET','PALLET_PLACE'):raise HTTPException(409,detail='\u0412\u044b\u0431\u0435\u0440\u0438\u0442\u0435 \u043f\u0430\u043b\u0435\u0442\u0443, \u0430 \u043d\u0435 \u044f\u0447\u0435\u0439\u043a\u0443')
        assert_place(db,payload.client_id,payload.warehouse_id,loc.id)
        code=loc.code
    db.execute(text('UPDATE fc_box_intakes SET manual_pallets=true,to_pallets=true WHERE id=:id'),{'id':str(rid)})
    if candidates:
        loc=candidates[0]
        p=db.execute(text('SELECT * FROM fc_box_intake_pallets WHERE receipt_id=:r AND location_id=:l FOR UPDATE'),{'r':str(rid),'l':str(loc.id)}).mappings().first()
        if p:
            p=dict(p);count=service.pallet_count(db,p['id'])
            if count>=int(p['capacity']):raise HTTPException(409,detail='\u041f\u0430\u043b\u0435\u0442\u0430 \u0437\u0430\u043f\u043e\u043b\u043d\u0435\u043d\u0430. \u0414\u043e\u0431\u0430\u0432\u044c\u0442\u0435 \u043c\u0435\u0441\u0442\u0430 \u0438\u043b\u0438 \u0432\u044b\u0431\u0435\u0440\u0438\u0442\u0435 \u0434\u0440\u0443\u0433\u0443\u044e \u043f\u0430\u043b\u0435\u0442\u0443.')
            db.execute(text('UPDATE fc_box_intake_pallets SET is_closed=false WHERE id=:p'),{'p':p['id']})
        else:
            other=db.execute(text("SELECT 1 FROM fc_box_intake_pallets p JOIN fc_box_intakes r ON r.id=p.receipt_id WHERE p.location_id=:l AND r.status='OPEN' AND r.id<>:r LIMIT 1"),{'l':str(loc.id),'r':str(rid)}).first()
            if other:raise HTTPException(409,detail='\u041f\u0430\u043b\u0435\u0442\u0430 \u0437\u0430\u043d\u044f\u0442\u0430 \u0434\u0440\u0443\u0433\u043e\u0439 \u043e\u0442\u043a\u0440\u044b\u0442\u043e\u0439 \u043f\u0440\u0438\u0451\u043c\u043a\u043e\u0439')
            p={'id':str(uuid.uuid4()),'number':int(r['next_number']),'location_id':str(loc.id),'public_code':public_code(loc),'section':r.get('default_section') or '', 'first_row':r.get('first_row'),'capacity':16}
            existing=db.execute(select(func.count()).select_from(Container).where(Container.location_id==loc.id,Container.status=='ACTIVE')).scalar()
            if existing>=16:raise HTTPException(409,detail='\u041d\u0430 \u043f\u0430\u043b\u0435\u0442\u0435 \u0443\u0436\u0435 16 \u043a\u043e\u0440\u043e\u0431\u043e\u0432. \u041f\u0440\u043e\u0432\u0435\u0440\u044c\u0442\u0435 \u0435\u0451 \u0437\u0430\u043f\u043e\u043b\u043d\u0435\u043d\u0438\u0435.')
            db.execute(text("INSERT INTO fc_box_intake_pallets(id,receipt_id,number,location_id,section,first_row,capacity,is_closed,public_code) VALUES(:id,:r,:number,:location_id,:section,:first_row,16,false,:public_code)"),{**p,'r':str(rid)})
            db.execute(text('UPDATE fc_box_intakes SET next_number=:n WHERE id=:r'),{'n':p['number']+1,'r':str(rid)})
        db.execute(text('UPDATE fc_box_intakes SET current_pallet_id=:p WHERE id=:r'),{'p':p['id'],'r':str(rid)})
        service.audit(db,payload.client_id,payload.warehouse_id,str(rid),'PALLET_SELECT',payload.receiver or admin(request),after={'pallet_id':p['id'],'public_code':public_code(loc)})
    else:
        p=service.select_tsd_pallet(db,rid,payload.client_id,payload.warehouse_id,code,payload.receiver or admin(request))
        loc=db.get(Location,uid(p['location_id']))
        if r.get('default_section') and not loc.parent_id:
            loc.parent_id=service.make_section(db,payload.warehouse_id,r['default_section']);db.flush()
    assert_place(db,payload.client_id,payload.warehouse_id,loc.id)
    # Preserve requested section and side on newly selected pallets, without moving existing boxes.
    if not p.get('section') and r.get('default_section'):
        db.execute(text('UPDATE fc_box_intake_pallets SET section=:s,first_row=:row WHERE id=:p'),{'s':r['default_section'],'row':r.get('first_row'),'p':p['id']})
    db.commit();d=service.detail(db,rid,payload.client_id,payload.warehouse_id)
    return {'ok':True,'pallet':{**p,'display_code':public_code(loc)},'receipt_snapshot':d}

@router.get('/r145f25/scope-check')
def scope_check(request:Request,db:Session=Depends(get_db)):
    admin(request);readonly_limits(db)
    from app.services.r145f25_scope_db import conflict_counts
    return {'read_only':True,'conflicts':conflict_counts(db)}

@router.get('/r145f25/print-agent/status')
def printer_status(station_code:str,request:Request,db:Session=Depends(get_db)):
    admin(request)
    from app.models import PrintJob
    rows=db.execute(select(PrintJob).where(PrintJob.station_code==station_code.strip().upper()).order_by(PrintJob.created_at.desc()).limit(10)).scalars().all()
    return {'station':station_code.strip().upper(),'jobs':[{'id':str(x.id),'status':x.status,'error':x.error_message,'completed_at':x.completed_at,'created_at':x.created_at} for x in rows]}


class IntakeStart(BaseModel):
    id:uuid.UUID;client_id:uuid.UUID;warehouse_id:uuid.UUID
    name:str=Field(min_length=1,max_length=240)
    to_pallets:bool=True
    section:str=Field(default='',max_length=80)
    first_row:int|None=Field(default=1,ge=1,le=900)

@router.post('/r145f25/box-intake')
def create_intake(payload:IntakeStart,request:Request,db:Session=Depends(get_db)):
    scope(db,payload.client_id,payload.warehouse_id)
    from app.services import box_intake as service
    service.ensure_r145f7_schema(db)
    try:r=service.new_receipt(db,payload.id,payload.client_id,payload.warehouse_id,payload.name,payload.to_pallets,payload.section,payload.first_row,admin(request),manual_pallets=True)
    except ValueError as e:raise HTTPException(422,detail=str(e))
    db.commit();return r

class IntakeScan(BaseModel):
    client_id:uuid.UUID;warehouse_id:uuid.UUID
    box_code:str=Field(min_length=1,max_length=120)
    expected_pallet_id:uuid.UUID|None=None

@router.post('/r145f25/box-intake/{rid}/scan')
def scan_box(rid:uuid.UUID,payload:IntakeScan,request:Request,db:Session=Depends(get_db)):
    scope(db,payload.client_id,payload.warehouse_id);readonly_limits(db)
    from app.services import box_intake as service
    service.ensure_r145f7_schema(db);r=service.active_receipt(db,rid,payload.client_id,payload.warehouse_id)
    if r['to_pallets']:
        if not payload.expected_pallet_id or str(r.get('current_pallet_id') or '')!=str(payload.expected_pallet_id):
            raise HTTPException(409,detail='\u041f\u0430\u043b\u0435\u0442\u0430 \u0438\u0437\u043c\u0435\u043d\u0438\u043b\u0430\u0441\u044c \u0438\u043b\u0438 \u043d\u0435 \u0432\u044b\u0431\u0440\u0430\u043d\u0430. \u041e\u0431\u043d\u043e\u0432\u0438\u0442\u0435 \u043f\u0440\u0438\u0451\u043c\u043a\u0443. \u0421\u043a\u0430\u043d \u043d\u0435 \u043f\u0435\u0440\u0435\u043d\u0430\u0437\u043d\u0430\u0447\u0435\u043d.')
        db.execute(text('UPDATE fc_box_intakes SET manual_pallets=true WHERE id=:r'),{'r':str(rid)})
    try:result=service.scan(db,rid,payload.client_id,payload.warehouse_id,payload.box_code,admin(request))
    except ValueError as e:raise HTTPException(422,detail=str(e))
    db.commit();return result


from app.routers.r145f19_finance import TsdPlaceOpen
@router.post('/r145f19/tsd/receipts/open-place')
def guarded_tsd_open(payload:TsdPlaceOpen,db:Session=Depends(get_db)):
    from app.routers import r145f19_finance as original
    scope(db,payload.client_id,payload.warehouse_id)
    original.install_schema(db)
    p=original._resolve_place(db,payload.client_id,payload.warehouse_id,payload.code,payload.target_type)
    assert_place(db,payload.client_id,payload.warehouse_id,p['location_id'])
    return original.tsd_open_place(payload,db)

@router.get('/printing/agent/ping')
def agent_ping(request:Request):
    # Prefix is authenticated by existing HMAC/station middleware; no job is claimed.
    station=getattr(request.state,'print_agent_station',None)
    if not station:raise HTTPException(401,detail='STATION_AUTH_REQUIRED')
    return {'ok':True,'station':station,'version':'r145f25'}

from app.schemas.printing import PrintResultRequest
@router.post('/printing/agent/jobs/{job_id}/result')
def station_result(job_id:uuid.UUID,payload:PrintResultRequest,request:Request,db:Session=Depends(get_db)):
    from app.models import PrintJob
    from app.routers.printing import print_result
    station=getattr(request.state,'print_agent_station',None)
    job=db.execute(select(PrintJob).where(PrintJob.id==job_id).with_for_update()).scalar_one_or_none()
    if not job:raise HTTPException(404,detail='JOB_NOT_FOUND')
    if not station or str(job.station_code).strip().upper()!=str(station).strip().upper():raise HTTPException(403,detail='WRONG_PRINT_STATION')
    if job.status=='PRINTED':return {'ok':True,'status':'PRINTED','already_completed':True}
    return print_result(job_id,payload,db)

@router.get('/r145f25/warehouses/{wid}/archive-preview')
def warehouse_preview(wid:uuid.UUID,request:Request,db:Session=Depends(get_db)):
    admin(request);w=db.get(Warehouse,wid)
    if not w:raise HTTPException(404,detail='NOT_FOUND')
    blockers=[]
    for model,condition,name in [(InventoryBalance,InventoryBalance.quantity!=0,'\u041e\u0441\u0442\u0430\u0442\u043a\u0438'),(Container,Container.status.in_(['ACTIVE','DEFECT_ACTIVE']),'\u0410\u043a\u0442\u0438\u0432\u043d\u044b\u0435 \u043a\u043e\u0440\u043e\u0431\u0430')]:
        n=db.execute(select(func.count()).select_from(model).where(model.warehouse_id==wid,condition)).scalar()
        if n:blockers.append({'name':name,'count':int(n)})
    if db.bind.dialect.name=='postgresql':
        for table in ('fc_box_intakes','fc_r145f19_tsd_receipts','fc_pvz_receiving_sessions','fc_wh_pick_tasks','fc_transit_batches'):
            if db.execute(text('SELECT to_regclass(:t)'),{'t':table}).scalar():
                n=db.execute(text(f"SELECT count(*) FROM {table} WHERE CAST(warehouse_id AS text)=:w AND status NOT IN ('COMPLETED','CANCELLED','CLOSED','ARCHIVED','POSTED','SENT','SHIPPED')"),{'w':str(wid)}).scalar()
                if n:blockers.append({'name':'\u041d\u0435\u0437\u0430\u043a\u0440\u044b\u0442\u044b\u0435 \u043e\u043f\u0435\u0440\u0430\u0446\u0438\u0438','count':int(n)})
    if w.is_active and db.execute(select(func.count()).select_from(Warehouse).where(Warehouse.is_active.is_(True))).scalar() <= 1:
        blockers.append({'name':'\u041f\u043e\u0441\u043b\u0435\u0434\u043d\u0438\u0439 \u0430\u043a\u0442\u0438\u0432\u043d\u044b\u0439 \u0441\u043a\u043b\u0430\u0434','count':1})
    if db.bind.dialect.name=='postgresql':
        for table,where,name in [
          ('fc_stocks_v2','storage_only<>0 OR fbs_available<>0 OR reserved<>0','\u0423\u0447\u0451\u0442 MAIN/FBS'),
          ('fc_stock_reservations',"status IN ('ACTIVE','SALE_RESERVED','CONSUMING','CANCELLING','PACKING_WRITE_OFF')",'\u0414\u0435\u0439\u0441\u0442\u0432\u0443\u044e\u0449\u0438\u0435 \u0440\u0435\u0437\u0435\u0440\u0432\u044b')]:
            if db.execute(text('SELECT to_regclass(:t)'),{'t':table}).scalar():
                n=db.execute(text(f'SELECT count(*) FROM {table} WHERE CAST(warehouse_id AS text)=:w AND ({where})'),{'w':str(wid)}).scalar()
                if n:blockers.append({'name':name,'count':int(n)})
        if db.execute(text("SELECT to_regclass('fc_pvz_lines')")).scalar():
            n=db.execute(text("SELECT count(*) FROM fc_pvz_lines l JOIN fc_pvz_batches b ON b.id=l.batch_id WHERE b.warehouse_id=:w AND b.kind='DEFECT' AND b.status<>'CANCELLED' AND b.deleted_at IS NULL AND l.deleted_at IS NULL AND l.quantity>l.written_off_qty"),{'w':str(wid)}).scalar()
            if n:blockers.append({'name':'\u041e\u0441\u0442\u0430\u0442\u043e\u043a \u0431\u0440\u0430\u043a\u0430','count':int(n)})
    data={'warehouse_id':str(w.id),'name':w.name,'is_active':w.is_active,'blockers':blockers}
    data['fingerprint']=hashlib.sha256(json.dumps(data,sort_keys=True,ensure_ascii=True).encode()).hexdigest();return data

@router.post('/r145f25/warehouses/{wid}/archive')
def archive_warehouse(wid:uuid.UUID,payload:Archive,request:Request,db:Session=Depends(get_db)):
    admin(request)
    if db.bind.dialect.name=='postgresql':db.execute(text('SELECT pg_advisory_xact_lock(14525,1)'))
    db.execute(select(Warehouse).where(Warehouse.id==wid).with_for_update()).scalar_one_or_none()
    w=db.get(Warehouse,wid)
    if w and not w.is_active:return {'ok':True,'already_archived':True}
    d=warehouse_preview(wid,request,db)
    if not payload.confirmed or d['blockers'] or d['fingerprint']!=payload.fingerprint:raise HTTPException(409,detail='\u0421\u043a\u043b\u0430\u0434 \u043d\u0435 \u043f\u0443\u0441\u0442 \u0438\u043b\u0438 \u0434\u0430\u043d\u043d\u044b\u0435 \u0438\u0437\u043c\u0435\u043d\u0438\u043b\u0438\u0441\u044c. \u041f\u043e\u0432\u0442\u043e\u0440\u0438\u0442\u0435 \u043f\u0440\u043e\u0432\u0435\u0440\u043a\u0443.')
    w=db.get(Warehouse,wid);w.is_active=False;db.commit();return {'ok':True,'history_preserved':True}


@router.delete('/r106/warehouses/{wid}')
def old_warehouse_delete(wid:uuid.UUID,request:Request):
    admin(request)
    raise HTTPException(409,detail='\u041e\u0431\u043d\u043e\u0432\u0438\u0442\u0435 \u0441\u0442\u0440\u0430\u043d\u0438\u0446\u0443. \u0423\u0434\u0430\u043b\u0435\u043d\u0438\u0435 \u0441\u043a\u043b\u0430\u0434\u0430 \u0442\u0435\u043f\u0435\u0440\u044c \u0432\u044b\u043f\u043e\u043b\u043d\u044f\u0435\u0442\u0441\u044f \u0438\u0437 \u0441\u043f\u0438\u0441\u043a\u0430 \u0441 \u043f\u0440\u043e\u0432\u0435\u0440\u043a\u043e\u0439 \u043e\u0441\u0442\u0430\u0442\u043a\u043e\u0432.')
