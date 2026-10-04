"""r145f27 API: server drafts and explicit section/pallet addressing."""
from __future__ import annotations
import re, uuid
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import bindparam, func, or_, select, text
from sqlalchemy.orm import Session
from app.core.database import get_db
from app.models import Container, Location
from app.services import r145f27_receiving as draft_service
from app.services.r145f25_places import assert_place, public_code, scope, uid

router = APIRouter(tags=["r145f27"])

def actor(request: Request) -> str:
    value = getattr(request.state, "username", None)
    if not value: raise HTTPException(403, detail="Требуется вход")
    return str(value)

def sec_token(raw) -> str:
    value = re.sub(r"^SEC[-_ ]*", "", str(raw or "").strip(), flags=re.I).strip()
    return str(int(value)) if value.isdigit() else value

def section_code(raw) -> str:
    token = sec_token(raw)
    if not token: return ""
    safe = token.zfill(3) if token.isdigit() else re.sub(r"[^0-9A-Za-zА-Яа-яЁё_-]+", "-", token).strip("-").upper()
    return "SEC-" + safe

def ensure_section(db: Session, warehouse_id, raw) -> Location:
    code = section_code(raw)
    if not code: raise HTTPException(422, detail="Укажите секцию, например 6")
    section = db.execute(select(Location).where(Location.warehouse_id==warehouse_id, Location.is_active.is_(True), Location.code==code)).scalar_one_or_none()
    if section:
        if str(section.location_type or "").upper() != "SECTION": raise HTTPException(409, detail=f"Код {code} занят адресом другого типа")
        return section
    section = Location(id=uuid.uuid4(), warehouse_id=warehouse_id, parent_id=None, code=code, barcode=code,
                       name=f"Секция {sec_token(raw)}", location_type="SECTION", is_active=True)
    db.add(section); db.flush(); return section

def ancestor_section(db: Session, loc: Location):
    current = loc
    for _ in range(8):
        if not current: return None
        if str(current.location_type or "").upper() == "SECTION": return current
        current = db.get(Location, current.parent_id) if current.parent_id else None
    return None

def pallet_public(raw) -> str:
    value = str(raw or "").strip()
    match = re.fullmatch(r"(?:PAL[-_ ]*)?0*(\d+)", value, re.I)
    return "PAL-" + match.group(1).zfill(3) if match else value

def pallet_matches(db: Session, warehouse_id, raw, section):
    scan = str(raw or "").strip(); public = pallet_public(scan)
    direct = db.execute(select(Location).where(Location.warehouse_id==warehouse_id, Location.is_active.is_(True),
        or_(func.lower(Location.code)==scan.lower(), func.lower(Location.barcode)==scan.lower()))).scalars().all()
    if direct and re.match(r"^(?:FCLOC-|IN-[0-9A-F]+-|[0-9A-F]{8}-)", scan, re.I): return direct
    candidates = db.execute(select(Location).where(Location.warehouse_id==warehouse_id, Location.is_active.is_(True),
        Location.location_type.in_(["PALLET","PALLET_PLACE"]))).scalars().all()
    result = []
    for loc in direct + candidates:
        if public_code(loc).casefold()!=public.casefold() and str(loc.code or "").casefold()!=scan.casefold() and str(loc.barcode or "").casefold()!=scan.casefold(): continue
        sec = ancestor_section(db, loc)
        if section and (not sec or section_code(sec.code).casefold()!=section_code(section).casefold()): continue
        result.append(loc)
    return list({str(x.id):x for x in result}.values())

class PalletSelect(BaseModel):
    client_id: uuid.UUID
    warehouse_id: uuid.UUID
    section: str = Field(min_length=1,max_length=80)
    pallet_code: str = Field(min_length=1,max_length=120)
    first_row: int|None = Field(default=None,ge=1,le=900)
    receiver: str|None = None

@router.post("/r145f27/box-intake/{rid}/pallet")
def select_pallet(rid: uuid.UUID, payload: PalletSelect, request: Request, db: Session=Depends(get_db)):
    scope(db,payload.client_id,payload.warehouse_id)
    from app.services import box_intake as legacy
    legacy.ensure_r145f7_schema(db)
    receipt = legacy.active_receipt(db,rid,payload.client_id,payload.warehouse_id)
    section = ensure_section(db,payload.warehouse_id,payload.section)
    matches = pallet_matches(db,payload.warehouse_id,payload.pallet_code,payload.section)
    if len(matches)>1:
        raise HTTPException(409,detail=f"В секции {sec_token(payload.section)} несколько палет {pallet_public(payload.pallet_code)}. Сканируйте QR нужной палеты.")
    if matches:
        loc=matches[0]
        if str(loc.location_type or "").upper() not in ("PALLET","PALLET_PLACE"): raise HTTPException(409,detail="Выбранный код не является палетой")
        assert_place(db,payload.client_id,payload.warehouse_id,loc.id)
    else:
        pub=pallet_public(payload.pallet_code)
        if not re.fullmatch(r"PAL-\d{3,}",pub,re.I): raise HTTPException(422,detail="Введите PAL-006 или отсканируйте QR палеты")
        internal=f"IN-{uuid.uuid4().hex[:10].upper()}-{section.code}-{pub}"
        loc=Location(id=uuid.uuid4(),warehouse_id=payload.warehouse_id,parent_id=section.id,code=internal,barcode=internal,
                     name=f"Секция {sec_token(payload.section)} · Палета {pub}",location_type="PALLET",is_active=True)
        db.add(loc);db.flush()
    actual=ancestor_section(db,loc)
    if actual and actual.id!=section.id:
        box_count=int(db.execute(select(func.count()).select_from(Container).where(Container.location_id==loc.id,Container.status=='ACTIVE')).scalar() or 0)
        if box_count: raise HTTPException(409,detail=f"{public_code(loc)} уже находится в секции {sec_token(actual.code)} и содержит {box_count} коробов. Используйте перемещение палеты.")
        loc.parent_id=section.id;loc.name=f"Секция {sec_token(payload.section)} · Палета {public_code(loc)}";db.flush()
    db.execute(text("UPDATE fc_box_intakes SET manual_pallets=true,to_pallets=true,default_section=:s,first_row=:row WHERE id=:id"),
               {"s":sec_token(payload.section),"row":payload.first_row,"id":str(rid)})
    p=db.execute(text("SELECT * FROM fc_box_intake_pallets WHERE receipt_id=:r AND location_id=:l FOR UPDATE"),
                 {"r":str(rid),"l":str(loc.id)}).mappings().first()
    if p:
        p=dict(p);db.execute(text("UPDATE fc_box_intake_pallets SET section=:s,first_row=:row,is_closed=false,public_code=:pub WHERE id=:id"),
                            {"s":sec_token(payload.section),"row":payload.first_row,"pub":public_code(loc),"id":p['id']})
        p={**p,"section":sec_token(payload.section),"first_row":payload.first_row,"public_code":public_code(loc)}
    else:
        number=int(receipt.get("next_number") or 1);pid=str(uuid.uuid4())
        db.execute(text("""INSERT INTO fc_box_intake_pallets(id,receipt_id,number,location_id,section,first_row,capacity,is_closed,public_code)
          VALUES(:id,:r,:n,:l,:s,:row,16,false,:pub)"""),
          {"id":pid,"r":str(rid),"n":number,"l":str(loc.id),"s":sec_token(payload.section),"row":payload.first_row,"pub":public_code(loc)})
        db.execute(text("UPDATE fc_box_intakes SET next_number=:n WHERE id=:r"),{"n":number+1,"r":str(rid)})
        p={"id":pid,"receipt_id":str(rid),"number":number,"location_id":str(loc.id),"section":sec_token(payload.section),"first_row":payload.first_row,"capacity":16,"is_closed":False,"public_code":public_code(loc)}
    db.execute(text("UPDATE fc_box_intakes SET current_pallet_id=:p WHERE id=:r"),{"p":p['id'],"r":str(rid)})
    legacy.audit(db,payload.client_id,payload.warehouse_id,str(rid),"PALLET_SELECT",payload.receiver or actor(request),
                 after={"pallet_id":p['id'],"public_code":public_code(loc),"section":sec_token(payload.section),"first_row":payload.first_row})
    db.commit()
    return {"ok":True,"pallet":{**p,"display_code":public_code(loc)},"receipt_snapshot":legacy.detail(db,rid,payload.client_id,payload.warehouse_id)}

class PalletAddress(BaseModel):
    client_id:uuid.UUID; warehouse_id:uuid.UUID
    section:str=Field(min_length=1,max_length=80)
    first_row:int|None=Field(default=None,ge=1,le=900)

@router.patch("/r145f27/box-intake/{rid}/pallets/{pid}/address")
def pallet_address(rid:uuid.UUID,pid:uuid.UUID,payload:PalletAddress,request:Request,db:Session=Depends(get_db)):
    scope(db,payload.client_id,payload.warehouse_id)
    from app.services import box_intake as legacy
    legacy.active_receipt(db,rid,payload.client_id,payload.warehouse_id)
    p=legacy.pallet(db,str(pid),str(rid));loc=db.get(Location,uid(p['location_id']))
    if not loc:raise HTTPException(404,detail="Адрес палеты не найден")
    assert_place(db,payload.client_id,payload.warehouse_id,loc.id)
    section=ensure_section(db,payload.warehouse_id,payload.section)
    loc.parent_id=section.id;loc.name=f"Секция {sec_token(payload.section)} · Палета {public_code(loc)}"
    db.execute(text("UPDATE fc_box_intake_pallets SET section=:s,first_row=:row WHERE id=:id"),
               {"s":sec_token(payload.section),"row":payload.first_row,"id":str(pid)})
    legacy.audit(db,payload.client_id,payload.warehouse_id,str(rid),"PALLET_ADDRESS",actor(request),
                 after={"pallet_id":str(pid),"section":sec_token(payload.section),"first_row":payload.first_row,"public_code":public_code(loc)})
    db.commit();return {"ok":True,"receipt_snapshot":legacy.detail(db,rid,payload.client_id,payload.warehouse_id)}

class DraftOpen(BaseModel):
    client_id:uuid.UUID;warehouse_id:uuid.UUID;id:uuid.UUID|None=None;reference:str|None=Field(default=None,max_length=255)
class DraftScan(BaseModel):
    client_id:uuid.UUID;warehouse_id:uuid.UUID;request_id:str=Field(min_length=1,max_length=96);barcode:str=Field(min_length=1,max_length=255)
    quantity:int=Field(default=1,ge=1,le=100000);location_code:str=Field(min_length=1,max_length=255);container_code:str|None=Field(default=None,max_length=255)
    article:str|None=None;size:str|None=None;name:str|None=None;product_found:bool=True;migrate:bool=False;location_id:str|None=None;container_id:str|None=None;container_name:str|None=None;section:str|None=None;aisle:str|None=None;pallet:str|None=None;storage_display:str|None=None;duplicated_from:str|None=None;product_id:str|None=None
class DraftQty(BaseModel):
    client_id:uuid.UUID;warehouse_id:uuid.UUID;request_id:str=Field(min_length=1,max_length=96);quantity:int=Field(ge=1,le=100000)
class DraftDelete(BaseModel):
    client_id:uuid.UUID;warehouse_id:uuid.UUID;request_id:str=Field(min_length=1,max_length=96)
class DraftCommit(BaseModel):
    client_id:uuid.UUID;warehouse_id:uuid.UUID;reference:str|None=Field(default=None,max_length=255);performed_by:str|None=Field(default=None,max_length=255)
    wb_warehouse_id:int|None=None;lane:str='known';stock_class:str='MAIN'

@router.post('/r145f27/manual-drafts/open')
def draft_open(payload:DraftOpen,request:Request,db:Session=Depends(get_db)):
    scope(db,payload.client_id,payload.warehouse_id);draft_service.install_schema(db)
    return draft_service.open_receipt(db,payload.client_id,payload.warehouse_id,actor(request),payload.id,payload.reference or '')
@router.get('/r145f27/manual-drafts/active')
def draft_active(client_id:uuid.UUID,warehouse_id:uuid.UUID,request:Request,db:Session=Depends(get_db)):
    scope(db,client_id,warehouse_id);draft_service.install_schema(db);return draft_service.open_receipt(db,client_id,warehouse_id,actor(request))
@router.post('/r145f27/manual-drafts/{rid}/scan')
def draft_scan(rid:uuid.UUID,payload:DraftScan,request:Request,db:Session=Depends(get_db)):
    scope(db,payload.client_id,payload.warehouse_id);return draft_service.add_scan(db,rid,payload.client_id,payload.warehouse_id,actor(request),payload.model_dump())
@router.patch('/r145f27/manual-drafts/{rid}/lines/{line_id}')
def draft_qty(rid:uuid.UUID,line_id:uuid.UUID,payload:DraftQty,request:Request,db:Session=Depends(get_db)):
    scope(db,payload.client_id,payload.warehouse_id);return draft_service.set_quantity(db,rid,line_id,payload.client_id,payload.warehouse_id,actor(request),payload.model_dump())
@router.delete('/r145f27/manual-drafts/{rid}/lines/{line_id}')
def draft_remove(rid:uuid.UUID,line_id:uuid.UUID,payload:DraftDelete,request:Request,db:Session=Depends(get_db)):
    scope(db,payload.client_id,payload.warehouse_id);return draft_service.remove_line(db,rid,line_id,payload.client_id,payload.warehouse_id,actor(request),payload.request_id)
@router.post('/r145f27/manual-drafts/{rid}/cancel')
def draft_cancel(rid:uuid.UUID,payload:DraftDelete,request:Request,db:Session=Depends(get_db)):
    scope(db,payload.client_id,payload.warehouse_id);return draft_service.cancel(db,rid,payload.client_id,payload.warehouse_id)

@router.post('/r145f27/manual-drafts/{rid}/commit')
def draft_commit(rid:uuid.UUID,payload:DraftCommit,request:Request,db:Session=Depends(get_db)):
    scope(db,payload.client_id,payload.warehouse_id);draft_service.install_schema(db)
    r=draft_service.receipt(db,rid,payload.client_id,payload.warehouse_id,True)
    if r['status']=='COMPLETED':
        return {'ok':True,'already_completed':True,'operation_ids':[x for x in str(r.get('operation_ids') or '').split(',') if x],'receipt_id':str(rid)}
    if r['status'] not in ('DRAFT','POSTING'):raise HTTPException(409,detail='Черновик отменён')
    lane=str(payload.lane or 'known');where='product_found=true' if lane=='known' else 'product_found=false'
    lock=' FOR UPDATE' if db.bind.dialect.name=='postgresql' else ''
    rows=db.execute(text(f"SELECT * FROM fc_r145f27_manual_lines WHERE receipt_id=:r AND quantity>0 AND posted_at IS NULL AND {where} ORDER BY created_at,id{lock}"),{'r':str(rid)}).mappings().all()
    if not rows:raise HTTPException(409,detail='В этой части приёмки нет товара для проведения')
    db.execute(text("UPDATE fc_r145f27_manual_receipts SET status='POSTING',reference=:ref,updated_at=now() WHERE id=:id"),
               {'ref':str(payload.reference or r.get('reference') or '')[:255],'id':str(rid)});db.commit()
    body={'client_id':str(payload.client_id),'warehouse_id':str(payload.warehouse_id),'idempotency_key':f'R145F27:MANUAL:{rid}:{lane}',
          'performed_by':payload.performed_by or actor(request),'reference':payload.reference or r.get('reference') or None,
          'wb_warehouse_id':payload.wb_warehouse_id,'internal_only':lane!='known','stock_class':payload.stock_class if payload.stock_class in ('MAIN','FBS') else 'MAIN',
          'lines':[{'barcode':x['barcode'],'quantity':int(x['quantity']),'location_code':x['location_code'],'container_code':x['container_code']} for x in rows]}
    from app.routers.r145f26_cards import receive
    try: result=receive(body,request,db)
    except Exception:
        try:
            db.rollback();db.execute(text("UPDATE fc_r145f27_manual_receipts SET status='DRAFT',updated_at=now() WHERE id=:id AND status='POSTING'"),{'id':str(rid)});db.commit()
        except Exception: db.rollback()
        raise
    opid=str(result.get('operation_id') or result.get('id') or '')
    ids=[str(x['id']) for x in rows]
    stmt=text("UPDATE fc_r145f27_manual_lines SET posted_at=now(),operation_id=:op,updated_at=now() WHERE id IN :ids").bindparams(bindparam('ids',expanding=True))
    db.execute(stmt,{'op':opid or None,'ids':ids})
    remaining=int(db.execute(text("SELECT count(*) FROM fc_r145f27_manual_lines WHERE receipt_id=:r AND quantity>0 AND posted_at IS NULL"),{'r':str(rid)}).scalar() or 0)
    ops=[x for x in str(r.get('operation_ids') or '').split(',') if x]
    if opid and opid not in ops:ops.append(opid)
    status='DRAFT' if remaining else 'COMPLETED'
    db.execute(text("UPDATE fc_r145f27_manual_receipts SET status=:s,operation_ids=:ops,updated_at=now(),completed_at=CASE WHEN :s='COMPLETED' THEN now() ELSE completed_at END WHERE id=:id"),
               {'s':status,'ops':','.join(ops),'id':str(rid)});db.commit()
    return {'ok':True,'receipt_id':str(rid),'operation_id':opid,'result':result,
            'receipt':draft_service.snapshot(db,draft_service.receipt(db,rid,payload.client_id,payload.warehouse_id)),
            'completed':status=='COMPLETED'}
