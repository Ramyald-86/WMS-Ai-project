"""r65: local stock screens and exact box navigation; no stock mutations here."""
import uuid
from datetime import datetime, timezone
from typing import Literal
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from app.core.database import get_db
from app.models import Client, Warehouse, Container, Location, SKU
from app.services.local_catalog import catalog
from app.services.local_catalog_rules import barcode
from app.services.warehouse_availability import available_buckets
from app.services.place_history import place_history
from app.services.box_routes import enrich as route_enrich, set_box_route
from app.services.picking_route import route_key
from app.services.warehouse_availability import lock_scope

router = APIRouter(prefix='/warehouse-ui', tags=['Остатки и локальный справочник'])


def scope(db, client_id, warehouse_id=None):
    if not db.get(Client, client_id) or (warehouse_id and not db.get(Warehouse, warehouse_id)):
        raise HTTPException(404, detail='Клиент или склад не найден')


@router.get('/catalog')
def local_catalog(client_id: uuid.UUID, db: Session = Depends(get_db)):
    scope(db, client_id)
    return {'rows': catalog(db, client_id), 'generated_at': datetime.now(timezone.utc).isoformat()}


@router.get('/catalog/lookup')
def lookup(client_id: uuid.UUID, barcode_value: str, db: Session = Depends(get_db)):
    scope(db, client_id)
    try:
        code = barcode(barcode_value)
    except ValueError as e:
        raise HTTPException(422, detail=str(e))
    rows = catalog(db, client_id, code)
    return rows[0] if rows else {'found': False, 'barcode': code, 'wb_found': False}


class LocalProduct(BaseModel):
    client_id: uuid.UUID
    barcode: str = Field(min_length=1, max_length=80)
    article: str = Field(min_length=1, max_length=120)
    size: str = Field(min_length=1, max_length=80)
    name: str = Field(default='', max_length=200)


@router.post('/catalog/local')
def create_local(payload: LocalProduct, db: Session = Depends(get_db)):
    scope(db, payload.client_id)
    try:
        code = barcode(payload.barcode)
    except ValueError as e:
        raise HTTPException(422, detail=str(e))
    article, size = payload.article.strip(), payload.size.strip()
    if not article or not size:
        raise HTTPException(422, detail='Укажите артикул и размер; для безразмерного — «Без размера»')
    db.execute(text('SELECT pg_advisory_xact_lock(hashtext(:key))'),
               {'key': f'fc:local-sku:{payload.client_id}:{code}'})
    existing = db.execute(select(SKU).where(SKU.client_id == payload.client_id,
                                           SKU.barcode == code).with_for_update()).scalars().all()
    if len(existing) > 1:
        raise HTTPException(409, detail='ШК привязан к нескольким SKU. Исправьте справочник')
    created = not existing
    sku = existing[0] if existing else None
    if sku and not sku.is_active:
        raise HTTPException(409, detail='Товар с этим ШК архивный. Восстановите его в справочнике')
    if sku:
        for attr, value in [('article', article), ('size', size)]:
            current = str(getattr(sku, attr) or '').strip()
            if current and current.casefold() != value.casefold():
                raise HTTPException(409, detail='Этот ШК уже сохранён с другим артикулом или размером. Обновите карточку на экране')
            if not current:
                setattr(sku, attr, value)
    else:
        known = catalog(db, payload.client_id, code)
        if known and known[0].get('wb_conflict'):
            raise HTTPException(409, detail=known[0]['warning'])
        if known and known[0].get('wb_found'):
            for attr, value in [('article', article), ('size', size)]:
                current = str(known[0].get(attr) or '').strip()
                if current and current.casefold() != value.casefold():
                    raise HTTPException(409, detail='ШК уже есть в сохранённой карточке WB с другими данными. Обновите карточку на экране')
        sku = SKU(id=uuid.uuid4(), client_id=payload.client_id,
                  sku_code='LOCAL-' + uuid.uuid5(payload.client_id, code).hex,
                  barcode=code, article=article, size=size,
                  name=payload.name.strip() or article, unit='PCS', is_active=True)
        db.add(sku)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, detail='Товар уже изменён другим сотрудником. Повторите поиск по ШК')
    row = catalog(db, payload.client_id, code)[0]
    return {**row, 'created': created}


def stock_rows(db, c, w, kind):
    # MAIN is exactly the allocator's view: clamps stale mirrors and deducts
    # active warehouse-task claims, including administrator overplan changes.
    buckets = available_buckets(db, c, w)
    if kind == 'FBS':
        main = {(b['sku_id'], b['location_id'], b['container_id'] or ''): b['main'] for b in buckets}
        rows = db.execute(text('''SELECT CAST(b.sku_id AS varchar) sku_id,
            CAST(b.location_id AS varchar) location_id, CAST(b.container_id AS varchar) container_id,
            s.barcode,s.article,s.size,l.code location_code,c.name container_name,b.quantity
            FROM inventory_balances b JOIN skus s ON s.id=b.sku_id
            JOIN locations l ON l.id=b.location_id LEFT JOIN containers c ON c.id=b.container_id
            WHERE b.client_id=:c AND b.warehouse_id=:w AND b.quantity>0
              AND s.is_active=TRUE AND l.is_active=TRUE
              AND (b.container_id IS NULL OR (c.status='ACTIVE' AND c.location_id=l.id))'''),
            {'c':c, 'w':w}).mappings().all()
        buckets = [{**dict(r), 'main': max(0, r['quantity'] - main.get(
            (r['sku_id'], r['location_id'], r['container_id'] or ''), 0)), 'held': None, 'free': None} for r in rows]
    grouped = {}
    for b in buckets:
        physical_main = float(b['main'])
        free_main = float(b['free']) if kind == 'MAIN' else physical_main
        # Preserve the API contract: `quantity` remains physical MAIN and `free` is the
        # immediately available amount. The stock screen filters by `free`, so a fully
        # allocated pick disappears from available stock without rewriting physical history.
        if (kind == 'MAIN' and free_main <= 0) or (kind != 'MAIN' and physical_main <= 0):
            continue
        qty = physical_main
        # Barcode aliases are displayed together, while every original SKU/place
        # remains in locations. No database identities or balances are merged.
        key = str(b['barcode'] or b['sku_id'])
        r = grouped.setdefault(key, {'barcode': b['barcode'] or '', 'article': b['article'] or '',
            'size': b['size'] or '', 'sku_ids': [], 'quantity': 0, 'physical': 0 if kind == 'MAIN' else None,
            'held': 0 if kind == 'MAIN' else None, 'free': 0 if kind == 'MAIN' else None, 'locations': []})
        if b['sku_id'] not in r['sku_ids']:
            r['sku_ids'].append(b['sku_id'])
        r['quantity'] += qty
        if kind == 'MAIN':
            r['physical'] += physical_main
            r['held'] += float(b['held'])
            r['free'] += free_main
        r['locations'].append({'sku_id':b['sku_id'], 'location_id':b['location_id'],
            'container_id':b['container_id'], 'location':b['location_code'],
            'container':b['container_name'] or '', 'quantity':qty,
            'physical':physical_main if kind == 'MAIN' else None,
            'held':float(b['held']) if kind == 'MAIN' else None,
            'free':free_main if kind == 'MAIN' else None})
    return sorted(grouped.values(), key=lambda r:(r['article'],r['size'],r['barcode']))


@router.get('/stocks')
def stocks(client_id: uuid.UUID, warehouse_id: uuid.UUID,
           stock_class: Literal['MAIN','FBS'], db: Session = Depends(get_db)):
    scope(db, client_id, warehouse_id)
    # Read the revision BEFORE the snapshot: a concurrent commit must trigger
    # the next background refresh, never be acknowledged without its rows.
    from app.services.stock_sync import revision
    before = revision(db, client_id, warehouse_id)
    rows = stock_place_routes(db, client_id, warehouse_id, stock_rows(db, client_id, warehouse_id, stock_class))
    return {'rows':rows, 'client_id':str(client_id), 'warehouse_id':str(warehouse_id),
            'stock_class':stock_class, 'revision':before, 'generated_at':datetime.now(timezone.utc).isoformat()}


@router.get('/boxes/{container_id}')
def box(container_id: uuid.UUID, client_id: uuid.UUID, warehouse_id: uuid.UUID,
        stock_class: Literal['MAIN','FBS'], db: Session = Depends(get_db)):
    scope(db, client_id, warehouse_id)
    c = db.get(Container, container_id)
    if not c or c.client_id != client_id or c.warehouse_id != warehouse_id:
        raise HTTPException(404, detail='Короб не найден у выбранного клиента на этом складе')
    loc = db.get(Location, c.location_id) if c.location_id else None
    rows = []
    for r in stock_rows(db, client_id, warehouse_id, stock_class):
        places = [p for p in r['locations'] if p['container_id'] == str(container_id)]
        if places:
            rows.append({**r, 'locations':places, 'quantity':sum(p['quantity'] for p in places),
                         'held':sum(p['held'] for p in places) if stock_class=='MAIN' else None,
                         'free':sum(p['free'] for p in places) if stock_class=='MAIN' else None})
    return {'id':str(c.id), 'name':c.name or c.barcode, 'barcode':c.barcode,
            'status':c.status, 'location_id':str(loc.id) if loc else None,
            'location':loc.code if loc else 'Без адреса', 'stock_class':stock_class, 'rows':rows,
            'route':route_enrich(db,client_id,warehouse_id,[{'location_id':str(c.location_id or ''),'container_id':str(c.id)}])[0]}


@router.get('/history')
def history(client_id: uuid.UUID, warehouse_id: uuid.UUID, barcode_value: str,
            db: Session = Depends(get_db)):
    scope(db, client_id, warehouse_id)
    ids = db.execute(select(SKU.id).where(SKU.client_id==client_id,
                                         SKU.barcode==barcode_value)).scalars().all()
    return {'events':place_history(db, client_id=client_id, warehouse_id=warehouse_id, sku_ids=ids) if ids else []}


@router.get('/boxes/{container_id}/history')
def box_history(container_id: uuid.UUID, client_id: uuid.UUID, warehouse_id: uuid.UUID,
                db: Session = Depends(get_db)):
    scope(db, client_id, warehouse_id)
    c = db.get(Container, container_id)
    if not c or c.client_id != client_id or c.warehouse_id != warehouse_id:
        raise HTTPException(404, detail='Короб не найден у выбранного клиента на этом складе')
    return {'events':place_history(db, client_id=client_id, warehouse_id=warehouse_id,
                                 container_id=container_id)}



def stock_place_routes(db,c,w,rows):
    places=route_enrich(db,c,w,[p for r in rows for p in r['locations']])
    by_key={(p['sku_id'],p['location_id'],p['container_id']):p for p in places}
    return [{**r,'locations':sorted((by_key[(p['sku_id'],p['location_id'],p['container_id'])] for p in r['locations']),key=route_key)} for r in rows]


class BoxRow(BaseModel):
    client_id:uuid.UUID
    warehouse_id:uuid.UUID
    row_on_pallet:int|None=Field(default=None,ge=1,le=999)
    reason:str=Field(min_length=1,max_length=500)


@router.patch('/boxes/{container_id}/row')
def update_box_row(container_id:uuid.UUID,payload:BoxRow,request:Request,db:Session=Depends(get_db)):
    scope(db,payload.client_id,payload.warehouse_id)
    reason=payload.reason.strip()
    if not reason:raise HTTPException(422,detail='Укажите причину изменения ряда')
    lock_scope(db,payload.client_id,payload.warehouse_id)
    row=set_box_route(db,container_id=container_id,client_id=payload.client_id,warehouse_id=payload.warehouse_id,
        row_on_pallet=payload.row_on_pallet,actor=getattr(request.state,'username',None),reason=reason)
    db.commit()
    return {'ok':True,'row':row}

# r77: sale state is an independent local read; explicit reconciliation contacts WB.
class SaleScope(BaseModel):
    client_id:uuid.UUID
    warehouse_id:uuid.UUID
    sku_ids:list[uuid.UUID]|None=Field(default=None,max_length=5000)

class StoppedAction(SaleScope):
    sku_ids:list[uuid.UUID]=Field(min_length=1,max_length=5000)
    request_id:uuid.UUID
    action:Literal['TO_MAIN','WRITE_OFF']
    reason:str=Field(min_length=1,max_length=255)

@router.post('/wb-sales/status')
def sale_status(payload:SaleScope,db:Session=Depends(get_db)):
    from app.services.wb_sale_status import snapshot
    scope(db,payload.client_id,payload.warehouse_id)
    return snapshot(db,payload.client_id,payload.warehouse_id,payload.sku_ids)

@router.post('/wb-sales/check')
def check_sale_status(payload:SaleScope,db:Session=Depends(get_db)):
    from app.services.wb_sale_status import read_wb,snapshot
    scope(db,payload.client_id,payload.warehouse_id)
    data=snapshot(db,payload.client_id,payload.warehouse_id,payload.sku_ids)
    read_wb(db,payload.client_id,payload.warehouse_id,[r['barcode'] for r in data['rows']])
    return snapshot(db,payload.client_id,payload.warehouse_id,payload.sku_ids)

@router.post('/wb-sales/stopped-action')
def stopped_sale_action(payload:StoppedAction,request:Request,db:Session=Depends(get_db)):
    from app.services.wb_sale_status import stopped_action
    scope(db,payload.client_id,payload.warehouse_id)
    req=payload.model_dump(mode='json');req.pop('client_id');req.pop('warehouse_id')
    req['reason']=req['reason'].strip()
    if not req['reason']:raise HTTPException(422,detail='Укажите причину')
    try:
        result=stopped_action(db,payload.client_id,payload.warehouse_id,req,getattr(request.state,'username',None))
        db.commit()
        from app.services.stock_outbox_worker import wake_stock_outbox_worker
        wake_stock_outbox_worker()
        return result
    except Exception:db.rollback();raise

class SaleRetry(BaseModel):
    client_id:uuid.UUID
    warehouse_id:uuid.UUID
    barcodes:list[str]=Field(min_length=1,max_length=5000)

@router.post('/wb-sales/retry')
def retry_sales(payload:SaleRetry,db:Session=Depends(get_db)):
    from app.services.receipt_fbs import queue_publication
    from app.services.stock_actions import locks
    scope(db,payload.client_id,payload.warehouse_id)
    ids=db.execute(select(SKU.id).where(SKU.client_id==payload.client_id,SKU.barcode.in_(payload.barcodes))).scalars().all()
    if not ids:raise HTTPException(404,detail='Товары не найдены')
    try:
        locks(db,str(payload.client_id),str(payload.warehouse_id),ids)
        result=queue_publication(db,payload.client_id,payload.warehouse_id,ids);db.commit()
        from app.services.stock_outbox_worker import wake_stock_outbox_worker
        wake_stock_outbox_worker()
        return {'publication':result,'message':'Пересчёт и отправка поставлены в очередь. Стоп продаж сохранён.'}
    except Exception:db.rollback();raise
