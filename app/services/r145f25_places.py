"""r145f25: public addresses, exact scope and bounded reads. Never changes stock."""
from __future__ import annotations
import re, uuid
from collections import defaultdict
from decimal import Decimal
from sqlalchemy import select, text
from fastapi import HTTPException
from app.models import Client, Warehouse, Location, Container, InventoryBalance, SKU

TECH=re.compile(r'^(?:IN-[0-9A-F]{6,}-|FCLOC-|FCBOX-|[0-9a-f]{8}-[0-9a-f-]{27,}$)',re.I)
PAL=re.compile(r'(?:^|-)PAL-(\d+)$',re.I)
SEC=re.compile(r'(?:^|-)SEC-([A-Za-z0-9]+)',re.I)

def uid(value):
    try:return uuid.UUID(str(value))
    except (ValueError,TypeError,AttributeError):raise HTTPException(422,detail='INVALID_ID')

def value(obj,key,default=''):
    return obj.get(key,default) if isinstance(obj,dict) else getattr(obj,key,default)

def public_code(loc):
    if not loc:return '\u0410\u0434\u0440\u0435\u0441 \u043d\u0435 \u0443\u043a\u0430\u0437\u0430\u043d'
    explicit=str(value(loc,'public_code') or value(loc,'display_code') or '').strip()
    if explicit and not TECH.match(explicit):return explicit
    code=str(value(loc,'code') or '').strip(); name=str(value(loc,'name') or '').strip()
    kind=str(value(loc,'location_type') or '').upper()
    pal=PAL.search(code)
    if pal:return 'PAL-'+str(int(pal.group(1))).zfill(3)
    sec_only=re.search(r'(?:^|-)SEC-(\d+)$',code,re.I)
    if sec_only:return 'SEC-'+str(int(sec_only.group(1))).zfill(3)
    row_only=re.search(r'(?:^|-)ROW-(\d+)$',code,re.I)
    if row_only:return '\u0420\u044f\u0434 '+str(int(row_only.group(1)))
    aisle_only=re.search(r'(?:^|-)AISLE-(\d+)$',code,re.I)
    if aisle_only:return '\u041f\u0440\u043e\u0445\u043e\u0434 '+str(int(aisle_only.group(1)))
    if not TECH.match(code) and code:
        if kind=='SECTION' and re.fullmatch(r'SEC-\d+',code,re.I):return 'SEC-'+code.split('-')[-1].zfill(3)
        return code
    if name and not TECH.match(name) and not re.search(r'IN-[0-9A-F]{6,}-|FCLOC-',name,re.I):return name
    return '\u0410\u0434\u0440\u0435\u0441 \u0431\u0435\u0437 \u043d\u0430\u0437\u0432\u0430\u043d\u0438\u044f'

def address_label(loc,parents=None,row=None,position=None,box=None):
    parents=parents or {};parts=[];seen=set();cur=loc
    while cur and str(value(cur,'id')) not in seen and len(seen)<20:
        seen.add(str(value(cur,'id')));parts.append(public_code(cur));cur=parents.get(str(value(cur,'parent_id') or ''))
    parts.reverse()
    code=str(value(loc,'code') or '') if loc else ''
    sec=SEC.search(code)
    if sec and not any(str(x).upper().startswith('SEC-') for x in parts):parts.insert(0,'SEC-'+sec.group(1).zfill(3))
    aisle=re.search(r'(?:^|-)AISLE-([A-Za-z0-9]+)',code,re.I)
    if aisle:parts.insert(max(0,len(parts)-1),'\u041f\u0440\u043e\u0445\u043e\u0434 '+aisle.group(1))
    if row:parts.append('\u0420\u044f\u0434 '+str(row)+(f' / {position}' if position else ''))
    if box:parts.append(str(box))
    return ' \u2192 '.join(dict.fromkeys(x for x in parts if x))

def scope(db,c,w,active=True):
    c,w=uid(c),uid(w)
    cl=db.get(Client,c);wh=db.get(Warehouse,w)
    if not cl or not wh:raise HTTPException(404,detail='\u041a\u043b\u0438\u0435\u043d\u0442 \u0438\u043b\u0438 \u0441\u043a\u043b\u0430\u0434 \u043d\u0435 \u043d\u0430\u0439\u0434\u0435\u043d')
    if active and (not cl.is_active or not wh.is_active):raise HTTPException(409,detail='\u041a\u043b\u0438\u0435\u043d\u0442 \u0438\u043b\u0438 \u0441\u043a\u043b\u0430\u0434 \u0432 \u0430\u0440\u0445\u0438\u0432\u0435')
    return cl,wh

def readonly_limits(db):
    if db.bind.dialect.name=='postgresql':
        db.execute(text("SET LOCAL statement_timeout='12000ms'"));db.execute(text("SET LOCAL lock_timeout='2500ms'"))

def location_map(db,w,ids):
    pending={uid(x) for x in ids if x};out={}
    for _ in range(20):
        if not pending:break
        rows=db.execute(select(Location).where(Location.warehouse_id==uid(w),Location.id.in_(sorted(pending,key=str)))).scalars().all();pending=set()
        for x in rows:
            out[str(x.id)]=x
            if x.parent_id and str(x.parent_id) not in out:pending.add(x.parent_id)
    return out

def occupants(db,w,loc_ids):
    ids=[uid(x) for x in loc_ids];out=defaultdict(set)
    if not ids:return out
    for loc,c in db.execute(select(InventoryBalance.location_id,InventoryBalance.client_id).where(InventoryBalance.warehouse_id==uid(w),InventoryBalance.location_id.in_(ids),InventoryBalance.quantity>0).distinct()):out[str(loc)].add(str(c))
    for loc,c in db.execute(select(Container.location_id,Container.client_id).where(Container.warehouse_id==uid(w),Container.location_id.in_(ids),Container.status.in_(['ACTIVE','DEFECT_ACTIVE'])).distinct()):out[str(loc)].add(str(c))
    return out

def assert_place(db,c,w,location_id,lock=True):
    c,w,l=uid(c),uid(w),uid(location_id);scope(db,c,w)
    stmt=select(Location).where(Location.id==l,Location.warehouse_id==w,Location.is_active.is_(True))
    if lock:stmt=stmt.with_for_update()
    loc=db.execute(stmt).scalar_one_or_none()
    if not loc:raise HTTPException(404,detail='\u041c\u0435\u0441\u0442\u043e \u043d\u0435 \u043d\u0430\u0439\u0434\u0435\u043d\u043e \u043d\u0430 \u0432\u044b\u0431\u0440\u0430\u043d\u043d\u043e\u043c \u0441\u043a\u043b\u0430\u0434\u0435')
    from app.services.client_location_guard import SHARED_SYSTEM_LOCATIONS
    if str(loc.code or '').upper() in SHARED_SYSTEM_LOCATIONS:return loc
    others=occupants(db,w,[l]).get(str(l),set())-{str(c)}
    if others:
        names=db.execute(select(Client.name).where(Client.id.in_([uid(x) for x in others]))).scalars().all()
        raise HTTPException(409,detail={'code':'OTHER_CLIENT_OCCUPIED','message':f'\u041c\u0435\u0441\u0442\u043e {public_code(loc)} \u0437\u0430\u043d\u044f\u0442\u043e \u0434\u0440\u0443\u0433\u0438\u043c \u043a\u043b\u0438\u0435\u043d\u0442\u043e\u043c: '+', '.join(names)+'. \u0412\u044b\u0431\u0435\u0440\u0438\u0442\u0435 \u0434\u0440\u0443\u0433\u043e\u0435 \u043c\u0435\u0441\u0442\u043e.'})
    return loc

def location_record(loc,parents=None):
    return {'id':str(loc.id),'warehouse_id':str(loc.warehouse_id),'parent_id':str(loc.parent_id) if loc.parent_id else None,'code':loc.code,'barcode':loc.barcode,'name':loc.name,'location_type':loc.location_type,'is_active':loc.is_active,'display_code':public_code(loc),'display_name':address_label(loc,parents)}

def scoped_locations(db,c,w,q=None):
    scope(db,c,w);readonly_limits(db)
    rows=db.execute(select(Location).where(Location.warehouse_id==uid(w),Location.is_active.is_(True)).order_by(Location.code)).scalars().all()
    owners=occupants(db,w,[x.id for x in rows]);byid={str(x.id):x for x in rows}
    from app.services.client_location_guard import SHARED_SYSTEM_LOCATIONS
    allowed=set()
    for x in rows:
        own=owners.get(str(x.id),set())
        if not own or own=={str(c)} or x.code.upper() in SHARED_SYSTEM_LOCATIONS:allowed.add(str(x.id))
    # Structure nodes remain navigable; foreign occupied leaf addresses are not listed as ours.
    out=[]
    for x in rows:
        if str(x.id) not in allowed:continue
        r=location_record(x,byid)
        if q and q.casefold() not in ' '.join(str(r[k] or '') for k in ('code','barcode','name','display_name')).casefold():continue
        out.append(r)
    return out

def box_record(db,box,w,created=False):
    locs=location_map(db,w,[box.location_id]);loc=locs.get(str(box.location_id));row=position=None
    if db.bind.dialect.name=='postgresql' and db.execute(text("SELECT to_regclass('fc_box_routes')")).scalar():
        r=db.execute(text('SELECT row_on_pallet,row_position FROM fc_box_routes WHERE container_id=:b AND client_id=:c AND warehouse_id=:w AND location_id=:l'),{'b':str(box.id),'c':str(box.client_id),'w':str(w),'l':str(box.location_id)}).first()
        if r:row,position=r
    return {'ok':True,'created':created,'generated_qr':created,'client_id':str(box.client_id),'warehouse_id':str(w),
      'container_id':str(box.id),'container_name':box.name or box.barcode,'container_barcode':box.barcode,
      'location_id':str(box.location_id) if box.location_id else None,'location_code':loc.code if loc else '',
      'location_name':address_label(loc,locs),'location_display':address_label(loc,locs,row,position),
      'display_code':public_code(loc),'row_on_pallet':row,'row_position':position,
      'resolved_from_qr':True,'location':location_record(loc,locs) if loc else None}
