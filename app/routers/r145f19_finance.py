from __future__ import annotations

import uuid
import re
import httpx
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select, text, or_
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models import Container, Location, SKU
from app.routers.warehouse_ui import scope
from app.schemas.inventory import OperationCreate, StockLine
from app.services.inventory import execute_operation
from app.services.stock_core_v2 import reconcile_stock_bucket
from app.services.r145f20_location_display import location_label, enrich_receipt, load_location_map

router = APIRouter(prefix="/finance", tags=["Финансы и аналитика"])
compat_router = APIRouter(prefix="/r145f19", tags=["r145f19 compatibility"])

# FC_R145F19_FINANCE_CATEGORY_HISTORY_STORAGE_TSD_SUPPLY
SERVICE_LABELS = {
    "RECEIVING": "Приёмка товара",
    "PVZ_GOOD": "Приёмка ПВЗ",
    "PVZ_DEFECT": "Брак ПВЗ",
    "TRANSIT_RECEIVE": "Приёмка транзита",
    "FBO": "FBO",
    "SHIPPING": "Отгрузка / списание",
    "STORAGE_BOX_DAY": "Хранение · коробо-день",
}


class CategoryUpsert(BaseModel):
    client_id: uuid.UUID
    sku_id: uuid.UUID | None = None
    barcode: str = Field(default="", max_length=120)
    article: str = Field(default="", max_length=180)
    category: str = Field(min_length=1, max_length=160)
    source: str = Field(default="MANUAL", max_length=32)


class TariffUpsert(BaseModel):
    client_id: uuid.UUID
    service_code: str = Field(min_length=1, max_length=48)
    category: str = Field(default="*", max_length=160)
    rate: Decimal = Field(ge=0)
    unit: str = Field(default="шт", max_length=32)
    note: str = Field(default="", max_length=500)


class TsdPlaceOpen(BaseModel):
    client_id: uuid.UUID
    warehouse_id: uuid.UUID
    worker: str = Field(min_length=1, max_length=160)
    reference: str = Field(min_length=1, max_length=200)
    code: str = Field(min_length=1, max_length=160)
    target_type: str = Field(default="AUTO", max_length=16)


class TsdScan(BaseModel):
    client_id: uuid.UUID
    warehouse_id: uuid.UUID
    worker: str = Field(min_length=1, max_length=160)
    barcode: str = Field(min_length=1, max_length=120)


class TsdRegister(BaseModel):
    client_id: uuid.UUID
    warehouse_id: uuid.UUID
    worker: str = Field(min_length=1, max_length=160)
    barcode: str = Field(min_length=1, max_length=120)
    article: str = Field(min_length=1, max_length=180)
    size: str = Field(min_length=1, max_length=100)
    name: str = Field(default="", max_length=240)
    category: str = Field(default="Без категории", max_length=160)


class TsdReceiptAction(BaseModel):
    client_id: uuid.UUID
    warehouse_id: uuid.UUID
    worker: str = Field(min_length=1, max_length=160)


class SupplyMove(BaseModel):
    credential_id: uuid.UUID
    warehouse_id: uuid.UUID
    source_supply_id: str = Field(min_length=1, max_length=120)
    target_supply_id: str = Field(min_length=1, max_length=120)
    order_ids: list[str] = Field(min_length=1, max_length=1000)
    wb_warehouse_id: int | None = None


def _exists(db: Session, table: str) -> bool:
    return bool(db.execute(text("SELECT to_regclass(:t)"), {"t": f"public.{table}"}).scalar())


def install_schema(db: Session) -> None:
    db.execute(text("""
      CREATE TABLE IF NOT EXISTS fc_product_categories (
        id uuid PRIMARY KEY,
        client_id uuid NOT NULL,
        sku_id uuid NULL,
        barcode varchar(120) NOT NULL DEFAULT '',
        article varchar(180) NOT NULL DEFAULT '',
        category varchar(160) NOT NULL,
        source varchar(32) NOT NULL DEFAULT 'MANUAL',
        created_at timestamptz NOT NULL DEFAULT now(),
        updated_at timestamptz NOT NULL DEFAULT now()
      )
    """))
    db.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS ux_fc_product_categories_sku ON fc_product_categories(client_id,sku_id) WHERE sku_id IS NOT NULL"))
    db.execute(text("CREATE INDEX IF NOT EXISTS ix_fc_product_categories_barcode ON fc_product_categories(client_id,barcode)"))
    db.execute(text("CREATE INDEX IF NOT EXISTS ix_fc_product_categories_article ON fc_product_categories(client_id,article)"))
    db.execute(text("""
      CREATE TABLE IF NOT EXISTS fc_finance_tariffs (
        id uuid PRIMARY KEY,
        client_id uuid NOT NULL,
        service_code varchar(48) NOT NULL,
        category varchar(160) NOT NULL DEFAULT '*',
        rate numeric(18,4) NOT NULL DEFAULT 0,
        unit varchar(32) NOT NULL DEFAULT 'шт',
        note varchar(500) NOT NULL DEFAULT '',
        updated_at timestamptz NOT NULL DEFAULT now(),
        UNIQUE(client_id,service_code,category)
      )
    """))
    db.execute(text("""
      CREATE TABLE IF NOT EXISTS fc_storage_daily_snapshots (
        id uuid PRIMARY KEY,
        client_id uuid NOT NULL,
        warehouse_id uuid NOT NULL,
        snapshot_date date NOT NULL,
        box_places integer NOT NULL DEFAULT 0,
        created_at timestamptz NOT NULL DEFAULT now(),
        UNIQUE(client_id,warehouse_id,snapshot_date)
      )
    """))
    db.execute(text("""
      CREATE TABLE IF NOT EXISTS fc_r145f19_tsd_receipts (
        id uuid PRIMARY KEY,
        client_id uuid NOT NULL,
        warehouse_id uuid NOT NULL,
        worker varchar(160) NOT NULL,
        reference varchar(200) NOT NULL,
        status varchar(20) NOT NULL DEFAULT 'DRAFT',
        created_at timestamptz NOT NULL DEFAULT now(),
        completed_at timestamptz NULL
      )
    """))
    db.execute(text("CREATE INDEX IF NOT EXISTS ix_fc_r145f19_tsd_receipts_scope ON fc_r145f19_tsd_receipts(client_id,warehouse_id,worker,status,created_at DESC)"))
    db.execute(text("""
      CREATE TABLE IF NOT EXISTS fc_r145f19_tsd_places (
        id uuid PRIMARY KEY,
        receipt_id uuid NOT NULL REFERENCES fc_r145f19_tsd_receipts(id) ON DELETE CASCADE,
        target_kind varchar(16) NOT NULL,
        target_code varchar(160) NOT NULL,
        location_id uuid NOT NULL,
        container_id uuid NULL,
        status varchar(20) NOT NULL DEFAULT 'OPEN',
        created_at timestamptz NOT NULL DEFAULT now()
      )
    """))
    db.execute(text("""
      CREATE TABLE IF NOT EXISTS fc_r145f19_tsd_lines (
        id uuid PRIMARY KEY,
        receipt_id uuid NOT NULL REFERENCES fc_r145f19_tsd_receipts(id) ON DELETE CASCADE,
        place_id uuid NOT NULL REFERENCES fc_r145f19_tsd_places(id) ON DELETE CASCADE,
        sku_id uuid NOT NULL,
        barcode varchar(120) NOT NULL,
        article varchar(180) NOT NULL DEFAULT '',
        size varchar(100) NOT NULL DEFAULT '',
        name varchar(240) NOT NULL DEFAULT '',
        quantity integer NOT NULL DEFAULT 1,
        scanned_at timestamptz NOT NULL DEFAULT now()
      )
    """))
    db.commit()


def _clean(v: Any) -> str:
    return " ".join(str(v or "").split())


def _category_map(db: Session, client_id: uuid.UUID) -> dict[str, str]:
    rows = db.execute(text("""SELECT CAST(sku_id AS text) sku_id,barcode,article,category
      FROM fc_product_categories WHERE client_id=:c ORDER BY updated_at DESC"""), {"c": str(client_id)}).mappings().all()
    out: dict[str, str] = {}
    for r in rows:
        cat = _clean(r["category"]) or "Без категории"
        for key in (r.get("sku_id"), r.get("barcode"), r.get("article")):
            if key:
                out.setdefault(str(key), cat)
    return out


def _find_wb_subject_rows(db: Session, client_id: uuid.UUID) -> list[dict]:
    """Best-effort WB category discovery across historical MarketplaceProduct schemas."""
    tables = db.execute(text("""
      SELECT DISTINCT table_name FROM information_schema.columns
      WHERE table_schema='public' AND column_name IN ('subject_name','subject','subjectName')
      ORDER BY table_name
    """)).scalars().all()
    result: list[dict] = []
    for table in tables:
        if not str(table).replace('_','').isalnum():
            continue
        try:
            cols = set(db.execute(text("SELECT column_name FROM information_schema.columns WHERE table_schema='public' AND table_name=:t"), {"t": table}).scalars().all())
            q = f'SELECT to_jsonb(t) j FROM "{table}" t'
            params = {}
            if 'client_id' in cols:
                q += " WHERE CAST(client_id AS text)=:c"
                params['c'] = str(client_id)
            rows = db.execute(text(q + " LIMIT 50000"), params).scalars().all()
            for j in rows:
                if not isinstance(j, dict):
                    continue
                cat = _clean(j.get('subject_name') or j.get('subject') or j.get('subjectName'))
                if not cat:
                    continue
                barcodes = j.get('barcodes') or j.get('barcode') or []
                if isinstance(barcodes, str):
                    barcodes = [barcodes]
                if isinstance(barcodes, dict):
                    barcodes = list(barcodes.values())
                result.append({
                    'category': cat,
                    'barcodes': [str(x).strip() for x in (barcodes or []) if str(x).strip()],
                    'article': _clean(j.get('vendor_code') or j.get('vendorCode') or j.get('article')),
                })
        except Exception:
            db.rollback()
            install_schema(db)
            continue
    return result


def sync_wb_categories(db: Session, client_id: uuid.UUID) -> int:
    install_schema(db)
    by_barcode: dict[str, str] = {}
    by_article: dict[str, str] = {}
    for row in _find_wb_subject_rows(db, client_id):
        for b in row['barcodes']:
            by_barcode.setdefault(b, row['category'])
        if row['article']:
            by_article.setdefault(row['article'], row['category'])
    skus = db.execute(select(SKU).where(SKU.client_id == client_id)).scalars().all()
    count = 0
    for sku in skus:
        cat = by_barcode.get(str(sku.barcode or '').strip()) or by_article.get(str(sku.article or '').strip())
        if not cat:
            continue
        existing = db.execute(text("SELECT id,source FROM fc_product_categories WHERE client_id=:c AND sku_id=:s"), {'c':str(client_id),'s':str(sku.id)}).mappings().first()
        if existing and str(existing.get('source') or '').upper() == 'MANUAL':
            continue
        if existing:
            db.execute(text("UPDATE fc_product_categories SET category=:cat,barcode=:b,article=:a,source='WB',updated_at=now() WHERE id=:id"), {'cat':cat,'b':sku.barcode or '','a':sku.article or '','id':existing['id']})
        else:
            db.execute(text("INSERT INTO fc_product_categories(id,client_id,sku_id,barcode,article,category,source) VALUES(:id,:c,:s,:b,:a,:cat,'WB')"), {'id':str(uuid.uuid4()),'c':str(client_id),'s':str(sku.id),'b':sku.barcode or '','a':sku.article or '','cat':cat})
        count += 1
    db.commit()
    return count


@router.post('/categories/sync-wb')
def categories_sync_wb(client_id: uuid.UUID, db: Session = Depends(get_db)):
    install_schema(db)
    return {'ok': True, 'updated': sync_wb_categories(db, client_id)}


@router.get('/categories')
def categories(client_id: uuid.UUID, db: Session = Depends(get_db)):
    install_schema(db)
    sync_wb_categories(db, client_id)
    rows = db.execute(text("""SELECT CAST(sku_id AS text) sku_id,barcode,article,category,source,updated_at
      FROM fc_product_categories WHERE client_id=:c ORDER BY category,article,barcode"""), {'c':str(client_id)}).mappings().all()
    names = sorted({str(r['category']) for r in rows if r.get('category')})
    return {'rows':[dict(r) for r in rows], 'categories':names}


@router.post('/category')
def category_upsert(payload: CategoryUpsert, db: Session = Depends(get_db)):
    install_schema(db)
    sku = None
    if payload.sku_id:
        sku = db.execute(select(SKU).where(SKU.id == payload.sku_id, SKU.client_id == payload.client_id)).scalars().first()
    if sku is None and payload.barcode.strip():
        sku = db.execute(select(SKU).where(SKU.client_id == payload.client_id, SKU.barcode == payload.barcode.strip())).scalars().first()
    if sku is None and payload.article.strip():
        sku = db.execute(select(SKU).where(SKU.client_id == payload.client_id, SKU.article == payload.article.strip())).scalars().first()
    sid = str(sku.id) if sku else None
    barcode = _clean(sku.barcode if sku else payload.barcode)
    article = _clean(sku.article if sku else payload.article)
    cat = _clean(payload.category)
    if sid:
        row = db.execute(text("SELECT id FROM fc_product_categories WHERE client_id=:c AND sku_id=:s"), {'c':str(payload.client_id),'s':sid}).mappings().first()
    else:
        row = db.execute(text("SELECT id FROM fc_product_categories WHERE client_id=:c AND barcode=:b AND article=:a ORDER BY updated_at DESC LIMIT 1"), {'c':str(payload.client_id),'b':barcode,'a':article}).mappings().first()
    if row:
        db.execute(text("UPDATE fc_product_categories SET category=:cat,source=:src,barcode=:b,article=:a,updated_at=now() WHERE id=:id"), {'cat':cat,'src':payload.source,'b':barcode,'a':article,'id':row['id']})
    else:
        db.execute(text("INSERT INTO fc_product_categories(id,client_id,sku_id,barcode,article,category,source) VALUES(:id,:c,:s,:b,:a,:cat,:src)"), {'id':str(uuid.uuid4()),'c':str(payload.client_id),'s':sid,'b':barcode,'a':article,'cat':cat,'src':payload.source})
    db.commit()
    return {'ok':True,'sku_id':sid,'barcode':barcode,'article':article,'category':cat}


@router.get('/tariffs')
def tariffs(client_id: uuid.UUID, db: Session = Depends(get_db)):
    install_schema(db)
    rows = db.execute(text("SELECT CAST(id AS text) id,service_code,category,rate,unit,note,updated_at FROM fc_finance_tariffs WHERE client_id=:c ORDER BY service_code,category"), {'c':str(client_id)}).mappings().all()
    return {'rows':[dict(r) for r in rows], 'service_labels':SERVICE_LABELS}


@router.post('/tariffs')
def tariff_upsert(payload: TariffUpsert, db: Session = Depends(get_db)):
    install_schema(db)
    service = _clean(payload.service_code).upper()
    category = _clean(payload.category) or '*'
    db.execute(text("""INSERT INTO fc_finance_tariffs(id,client_id,service_code,category,rate,unit,note)
      VALUES(:id,:c,:s,:cat,:rate,:unit,:note)
      ON CONFLICT(client_id,service_code,category) DO UPDATE SET rate=excluded.rate,unit=excluded.unit,note=excluded.note,updated_at=now()"""),
      {'id':str(uuid.uuid4()),'c':str(payload.client_id),'s':service,'cat':category,'rate':payload.rate,'unit':payload.unit,'note':payload.note})
    db.commit()
    return {'ok':True}


def _tariff_dict(db: Session, client_id: uuid.UUID) -> dict[tuple[str,str], Decimal]:
    out = {}
    for r in db.execute(text("SELECT service_code,category,rate FROM fc_finance_tariffs WHERE client_id=:c"), {'c':str(client_id)}).mappings():
        out[(str(r['service_code']).upper(), str(r['category']))] = Decimal(str(r['rate'] or 0))
    return out


def _rate(tariffs, service, category):
    return tariffs.get((service, category), tariffs.get((service, '*'), Decimal('0')))


def _category_for(catmap, sku_id='', barcode='', article=''):
    return catmap.get(str(sku_id or '')) or catmap.get(str(barcode or '')) or catmap.get(str(article or '')) or 'Без категории'


def _date_range(date_from: date | None, date_to: date | None):
    today = datetime.now(timezone.utc).date()
    start = date_from or (today - timedelta(days=30))
    end = date_to or today
    if end < start:
        raise HTTPException(422, detail='Дата окончания раньше даты начала')
    return start, end


def _storage_place_count(db: Session, client_id, warehouse_id) -> int:
    # Same business rule as Storage: a physical box with product = one place;
    # a direct occupied cell/pallet without a box = one place. Client scope is carried
    # by inventory_balances; locations historically do not have client_id.
    boxes = db.execute(text("""SELECT COUNT(*) FROM (
      SELECT container_id FROM inventory_balances
      WHERE CAST(client_id AS text)=:c AND CAST(warehouse_id AS text)=:w AND container_id IS NOT NULL AND quantity>0
      GROUP BY container_id HAVING SUM(quantity)>0) x"""), {'c':str(client_id),'w':str(warehouse_id)}).scalar() or 0
    cells = db.execute(text("""SELECT COUNT(*) FROM (
      SELECT location_id FROM inventory_balances
      WHERE CAST(client_id AS text)=:c AND CAST(warehouse_id AS text)=:w AND container_id IS NULL AND location_id IS NOT NULL AND quantity>0
      GROUP BY location_id HAVING SUM(quantity)>0) x"""), {'c':str(client_id),'w':str(warehouse_id)}).scalar() or 0
    transit = 0
    if _exists(db, 'fc_transit_boxes') and _exists(db, 'fc_transit_box_items') and _exists(db, 'fc_transit_batches'):
        transit = db.execute(text("""SELECT COUNT(*) FROM (
          SELECT b.id FROM fc_transit_boxes b JOIN fc_transit_batches a ON a.id=b.batch_id
          JOIN fc_transit_box_items i ON i.box_id=b.id
          WHERE a.client_id=:c AND a.warehouse_id=:w AND a.status='OPEN' AND b.status IN ('OPEN','CLOSED')
          GROUP BY b.id HAVING SUM(i.quantity)>0) x"""), {'c':str(client_id),'w':str(warehouse_id)}).scalar() or 0
    manual = 0
    if _exists(db, 'fc_storage_manual_boxes'):
        manual = db.execute(text("SELECT COALESCE(SUM(box_count),0) FROM fc_storage_manual_boxes WHERE CAST(client_id AS text)=:c AND CAST(warehouse_id AS text)=:w AND status='ACTIVE'"), {'c':str(client_id),'w':str(warehouse_id)}).scalar() or 0
    return int(boxes) + int(cells) + int(transit) + int(manual)


def _snapshot_today(db: Session, client_id, warehouse_id) -> int:
    places = _storage_place_count(db, client_id, warehouse_id)
    today = datetime.now(timezone.utc).date()
    db.execute(text("""INSERT INTO fc_storage_daily_snapshots(id,client_id,warehouse_id,snapshot_date,box_places)
      VALUES(:id,:c,:w,:d,:q) ON CONFLICT(client_id,warehouse_id,snapshot_date) DO UPDATE SET box_places=excluded.box_places"""),
      {'id':str(uuid.uuid4()),'c':str(client_id),'w':str(warehouse_id),'d':today,'q':places})
    db.commit()
    return places


def _finance_events(db: Session, client_id, warehouse_id, start: date, end: date) -> list[dict]:
    events: list[dict] = []
    end_exclusive = end + timedelta(days=1)
    catmap = _category_map(db, client_id)

    # PVZ GOOD / DEFECT are explicit so they are not mixed with ordinary receiving.
    if _exists(db,'fc_pvz_batches') and _exists(db,'fc_pvz_lines'):
        rows = db.execute(text("""SELECT b.id,b.kind,b.reference,b.performed_by,b.created_at,
          CAST(l.sku_id AS text) sku_id,l.barcode,l.article,l.size,l.name,l.quantity
          FROM fc_pvz_batches b JOIN fc_pvz_lines l ON l.batch_id=b.id
          WHERE CAST(b.client_id AS text)=:c AND CAST(b.warehouse_id AS text)=:w
            AND b.created_at>=:a AND b.created_at<:z AND COALESCE(b.status,'COMPLETED')<>'CANCELLED'"""),
          {'c':str(client_id),'w':str(warehouse_id),'a':start,'z':end_exclusive}).mappings().all()
        for r in rows:
            service = 'PVZ_DEFECT' if str(r['kind']).upper()=='DEFECT' else 'PVZ_GOOD'
            events.append({'created_at':r['created_at'],'service_code':service,'reference':r['reference'] or '', 'worker':r['performed_by'] or '',
                           'sku_id':r['sku_id'] or '', 'barcode':r['barcode'] or '', 'article':r['article'] or '', 'size':r['size'] or '', 'name':r['name'] or '',
                           'category':_category_for(catmap,r['sku_id'],r['barcode'],r['article']), 'quantity':Decimal(str(r['quantity'] or 0))})

    # Transit intake by box item. Created_at reflects when the item first entered transit.
    if _exists(db,'fc_transit_batches') and _exists(db,'fc_transit_boxes') and _exists(db,'fc_transit_box_items'):
        rows = db.execute(text("""SELECT a.number reference,a.created_by worker,i.created_at,CAST(i.sku_id AS text) sku_id,i.barcode,i.article,i.size,i.name,i.quantity
          FROM fc_transit_batches a JOIN fc_transit_boxes b ON b.batch_id=a.id JOIN fc_transit_box_items i ON i.box_id=b.id
          WHERE a.client_id=:c AND a.warehouse_id=:w AND i.created_at>=:a AND i.created_at<:z AND a.status<>'CANCELLED' AND i.quantity>0"""),
          {'c':str(client_id),'w':str(warehouse_id),'a':start,'z':end_exclusive}).mappings().all()
        for r in rows:
            events.append({'created_at':r['created_at'],'service_code':'TRANSIT_RECEIVE','reference':r['reference'] or '', 'worker':r['worker'] or '',
                           'sku_id':r['sku_id'] or '', 'barcode':r['barcode'] or '', 'article':r['article'] or '', 'size':r['size'] or '', 'name':r['name'] or '',
                           'category':_category_for(catmap,r['sku_id'],r['barcode'],r['article']), 'quantity':Decimal(str(r['quantity'] or 0))})

    # FBO packing/assembly: quantity handled in FBO boxes. It does not change stock by itself,
    # but it is still billable warehouse work and therefore belongs in finance analytics.
    if _exists(db,'fc_fbo_batches') and _exists(db,'fc_fbo_items'):
        rows = db.execute(text("""SELECT b.name reference,b.performed_by worker,i.created_at,CAST(i.sku_id AS text) sku_id,
          i.barcode,i.article,i.size,i.product_name name,i.quantity
          FROM fc_fbo_batches b JOIN fc_fbo_items i ON i.batch_id=b.id
          WHERE CAST(b.client_id AS text)=:c AND CAST(b.warehouse_id AS text)=:w
            AND i.created_at>=:a AND i.created_at<:z AND COALESCE(b.status,'ACTIVE')<>'CANCELLED' AND i.quantity>0"""),
          {'c':str(client_id),'w':str(warehouse_id),'a':start,'z':end_exclusive}).mappings().all()
        for r in rows:
            events.append({'created_at':r['created_at'],'service_code':'FBO','reference':r['reference'] or '', 'worker':r['worker'] or '',
                           'sku_id':r['sku_id'] or '', 'barcode':r['barcode'] or '', 'article':r['article'] or '', 'size':r['size'] or '', 'name':r['name'] or '',
                           'category':_category_for(catmap,r['sku_id'],r['barcode'],r['article']), 'quantity':Decimal(str(r['quantity'] or 0))})

    # Canonical inventory receiving catches manual / Excel / TSD and other standard receiving.
    if _exists(db,'inventory_operations') and _exists(db,'inventory_operation_lines'):
        rows = db.execute(text("""SELECT o.id,o.created_at,o.operation_type,o.reference,o.performed_by,
          CAST(l.sku_id AS text) sku_id,l.quantity,
          COALESCE(to_jsonb(s)->>'barcode','') barcode,COALESCE(to_jsonb(s)->>'article','') article,
          COALESCE(to_jsonb(s)->>'size','') size,COALESCE(to_jsonb(s)->>'name','') name,
          COALESCE(to_jsonb(l)->>'reason','') reason
          FROM inventory_operations o JOIN inventory_operation_lines l ON l.operation_id=o.id
          LEFT JOIN skus s ON s.id=l.sku_id
          WHERE CAST(o.client_id AS text)=:c AND CAST(o.warehouse_id AS text)=:w
            AND o.created_at>=:a AND o.created_at<:z AND UPPER(COALESCE(o.status,'COMPLETED'))='COMPLETED'
            AND UPPER(o.operation_type) IN ('RECEIVING','RECEIVING_IMPORT','SHIPPING')"""),
          {'c':str(client_id),'w':str(warehouse_id),'a':start,'z':end_exclusive}).mappings().all()
        for r in rows:
            marker = (str(r.get('reason') or '') + ' ' + str(r.get('reference') or '')).upper()
            if 'PVZ' in marker or 'ПВЗ' in marker:
                continue
            service_code='SHIPPING' if str(r['operation_type']).upper()=='SHIPPING' else 'RECEIVING'
            events.append({'created_at':r['created_at'],'service_code':service_code,'reference':r['reference'] or '', 'worker':r['performed_by'] or '',
                           'sku_id':r['sku_id'] or '', 'barcode':r['barcode'] or '', 'article':r['article'] or '', 'size':r['size'] or '', 'name':r['name'] or '',
                           'category':_category_for(catmap,r['sku_id'],r['barcode'],r['article']), 'quantity':abs(Decimal(str(r['quantity'] or 0)))})
    return events


@router.get('/report')
def finance_report(client_id: uuid.UUID, warehouse_id: uuid.UUID, date_from: date | None = None, date_to: date | None = None, db: Session = Depends(get_db)):
    scope(db, client_id, warehouse_id); install_schema(db); sync_wb_categories(db, client_id)
    start,end = _date_range(date_from,date_to)
    _snapshot_today(db,client_id,warehouse_id)
    tariffs = _tariff_dict(db,client_id)
    events = _finance_events(db,client_id,warehouse_id,start,end)
    rows=[]; total=Decimal('0'); grouped=defaultdict(lambda:{'quantity':Decimal('0'),'amount':Decimal('0')})
    for e in events:
        rate=_rate(tariffs,e['service_code'],e['category']); amount=e['quantity']*rate; total+=amount
        grouped[(e['service_code'],e['category'])]['quantity']+=e['quantity']; grouped[(e['service_code'],e['category'])]['amount']+=amount
        rows.append({**e,'quantity':float(e['quantity']),'rate':float(rate),'amount':float(amount),'service':SERVICE_LABELS.get(e['service_code'],e['service_code'])})
    storage_rows=db.execute(text("""SELECT snapshot_date,box_places FROM fc_storage_daily_snapshots
      WHERE client_id=:c AND warehouse_id=:w AND snapshot_date>=:a AND snapshot_date<=:z ORDER BY snapshot_date"""), {'c':str(client_id),'w':str(warehouse_id),'a':start,'z':end}).mappings().all()
    storage_rate=_rate(tariffs,'STORAGE_BOX_DAY','*'); storage_amount=sum(Decimal(str(r['box_places']))*storage_rate for r in storage_rows); total+=storage_amount
    summary=[{'service_code':k[0],'service':SERVICE_LABELS.get(k[0],k[0]),'category':k[1],'quantity':float(v['quantity']),'amount':float(v['amount'])} for k,v in sorted(grouped.items())]
    if storage_rows:
        summary.append({'service_code':'STORAGE_BOX_DAY','service':SERVICE_LABELS['STORAGE_BOX_DAY'],'category':'*','quantity':sum(int(r['box_places']) for r in storage_rows),'amount':float(storage_amount)})
    return {'date_from':str(start),'date_to':str(end),'total_amount':float(total),'summary':summary,'rows':rows,
            'storage':{'today_places':_storage_place_count(db,client_id,warehouse_id),'days':[dict(r) for r in storage_rows],'rate':float(storage_rate),'amount':float(storage_amount)}}


@compat_router.get('/history-safe')
def history_safe(client_id: uuid.UUID, warehouse_id: uuid.UUID, limit: int = 1000, db: Session = Depends(get_db)):
    """Schema-tolerant replacement for the UI history list. Excel export remains unchanged."""
    scope(db, client_id, warehouse_id)
    if not (_exists(db,'inventory_operations') and _exists(db,'inventory_operation_lines')):
        return {'rows':[]}
    rows=db.execute(text("""SELECT o.created_at,o.operation_type,o.status,o.reference,o.performed_by,
      COALESCE(to_jsonb(o)->>'comment','') comment,CAST(l.sku_id AS text) sku_id,l.quantity,
      COALESCE(to_jsonb(src_loc)->>'code','') source_location,COALESCE(to_jsonb(dst_loc)->>'code','') destination_location,
      COALESCE(NULLIF(to_jsonb(src_box)->>'name',''),NULLIF(to_jsonb(src_box)->>'barcode',''),'') source_container,
      COALESCE(NULLIF(to_jsonb(dst_box)->>'name',''),NULLIF(to_jsonb(dst_box)->>'barcode',''),'') destination_container,
      COALESCE(to_jsonb(s)->>'barcode','') barcode,COALESCE(to_jsonb(s)->>'article','') article,
      COALESCE(to_jsonb(s)->>'size','') size,COALESCE(to_jsonb(s)->>'name','') name
      FROM inventory_operations o
      JOIN inventory_operation_lines l ON l.operation_id=o.id
      LEFT JOIN skus s ON s.id=l.sku_id
      LEFT JOIN locations src_loc ON CAST(src_loc.id AS text)=COALESCE(to_jsonb(l)->>'source_location_id','')
      LEFT JOIN locations dst_loc ON CAST(dst_loc.id AS text)=COALESCE(to_jsonb(l)->>'destination_location_id','')
      LEFT JOIN containers src_box ON CAST(src_box.id AS text)=COALESCE(to_jsonb(l)->>'source_container_id','')
      LEFT JOIN containers dst_box ON CAST(dst_box.id AS text)=COALESCE(to_jsonb(l)->>'destination_container_id','')
      WHERE CAST(o.client_id AS text)=:c AND CAST(o.warehouse_id AS text)=:w
      ORDER BY o.created_at DESC LIMIT :limit"""),
      {'c':str(client_id),'w':str(warehouse_id),'limit':min(max(limit,1),5000)}).mappings().all()
    out=[]
    for r in rows:
        qty=Decimal(str(r['quantity'] or 0)); typ=str(r['operation_type'] or '')
        physical_delta=float(qty if typ.upper() in ('RECEIVING','RECEIVING_IMPORT') else (-qty if typ.upper() in ('SHIPPING',) else Decimal('0')))
        out.append({'created_at':r['created_at'],'event_type':typ,'status':r['status'],'article':r['article'],'size':r['size'],'barcode':r['barcode'],'name':r['name'],
                    'physical_delta':physical_delta,'reserve_delta':0,'source_location':r['source_location'] or '',
                    'destination_location':r['destination_location'] or '','source_container':r['source_container'] or '',
                    'destination_container':r['destination_container'] or '',
                    'reference':r['reference'] or r['comment'] or '', 'actor':r['performed_by'] or ''})
    return {'rows':out}


def _supply_delete_route(request: Request):
    candidates=[]
    for route in request.app.routes:
        path=str(getattr(route,'path','') or '')
        methods={str(x).upper() for x in (getattr(route,'methods',set()) or set())}
        if 'DELETE' in methods and 'suppl' in path.lower() and 'order' in path.lower():
            candidates.append(path)
    # Prefer the standard WB form: supply + one order id.
    candidates.sort(key=lambda x: (0 if x.count('{')>=2 else 1, len(x)))
    return candidates[0] if candidates else None


@compat_router.get('/wb/supply-move/capabilities')
def wb_supply_move_capabilities(request: Request):
    route=_supply_delete_route(request)
    return {'safe_detach_supported':bool(route),'delete_route':route or '',
            'message':'Безопасный перенос доступен' if route else 'В текущем backend нет подтверждённого DELETE-маршрута снятия заказа с поставки'}


def _fill_route(path: str, supply_id: str, order_id: str) -> str:
    names=re.findall(r'\{([^}:]+)(?::[^}]+)?\}',path)
    values=[]
    for name in names:
        n=name.lower()
        if 'supply' in n:
            values.append((name,supply_id))
        elif 'order' in n:
            values.append((name,order_id))
        else:
            raise HTTPException(501,detail=f'Неизвестный параметр DELETE-маршрута: {name}')
    out=path
    for name,value in values:
        out=re.sub(r'\{'+re.escape(name)+r'(?::[^}]+)?\}',str(value),out)
    return out


@compat_router.post('/wb/supply-move')
async def wb_supply_move(payload: SupplyMove, request: Request):
    """Move WB orders only when the installed backend exposes a real detach route.

    We deliberately do not modify local supply linkage directly. WB must confirm detach and
    attach. If attach fails after detach, a best-effort rollback adds the order back to source.
    """
    delete_route=_supply_delete_route(request)
    if not delete_route:
        raise HTTPException(501,detail='Перенос заблокирован: сервер не имеет подтверждённого маршрута снятия заказа с поставки WB. Локальную связь без WB не меняем.')
    source=_clean(payload.source_supply_id); target=_clean(payload.target_supply_id)
    if not source or not target or source==target:
        raise HTTPException(422,detail='Укажите разные исходную и новую поставки')
    order_ids=list(dict.fromkeys(_clean(x) for x in payload.order_ids if _clean(x)))
    if not order_ids:
        raise HTTPException(422,detail='Не указаны заказы для переноса')
    cookie=request.headers.get('cookie','')
    headers={'cookie':cookie,'content-type':'application/json'}
    query={'warehouse_id':str(payload.warehouse_id),'credential_id':str(payload.credential_id)}
    if payload.wb_warehouse_id is not None:
        query['wb_warehouse_id']=str(payload.wb_warehouse_id)
    base='http://127.0.0.1:8000'
    moved=[]; failures=[]
    async with httpx.AsyncClient(timeout=45.0,headers=headers) as client:
        for oid in order_ids:
            del_path=_fill_route(delete_route,source,oid)
            # Supply APIs across installed generations accepted credential/scope either in query or body; send both.
            detach=await client.request('DELETE',base+del_path,params=query,json={'credential_id':str(payload.credential_id),'warehouse_id':str(payload.warehouse_id),'order_id':oid,'order_ids':[oid]})
            if detach.status_code>=300:
                failures.append({'order_id':oid,'stage':'detach','status':detach.status_code,'detail':detach.text[:500]})
                continue
            add_q={'warehouse_id':str(payload.warehouse_id)}
            if payload.wb_warehouse_id is not None:add_q['wb_warehouse_id']=str(payload.wb_warehouse_id)
            attach=await client.patch(base+f'/marketplaces/wb/supplies/{target}/orders',params=add_q,json={'credential_id':str(payload.credential_id),'order_ids':[oid]})
            if attach.status_code<300:
                moved.append(oid);continue
            # Roll back to original supply if target attach did not confirm.
            rollback=await client.patch(base+f'/marketplaces/wb/supplies/{source}/orders',params=add_q,json={'credential_id':str(payload.credential_id),'order_ids':[oid]})
            failures.append({'order_id':oid,'stage':'attach','status':attach.status_code,'detail':attach.text[:500],'rollback_ok':rollback.status_code<300})
    if failures and not moved:
        raise HTTPException(409,detail={'message':'WB не подтвердил перенос ни одного заказа','failures':failures})
    return {'ok':not failures,'source_supply_id':source,'target_supply_id':target,'moved':moved,'failures':failures}


def _normalize_scan(raw: str) -> str:
    ru='ёйцукенгшщзхъфывапролджэячсмитьбюЁЙЦУКЕНГШЩЗХЪФЫВАПРОЛДЖЭЯЧСМИТЬБЮ'
    en='`qwertyuiop[]asdfghjkl;\'zxcvbnm,.~QWERTYUIOP{}ASDFGHJKL:"ZXCVBNM<>'
    return ''.join(en[ru.index(c)] if c in ru else c for c in str(raw or '').strip())


def _resolve_place(db: Session, client_id, warehouse_id, raw: str, target_type='AUTO'):
    # Exact operator names first; keyboard-layout conversion is only an alternative.
    code = str(raw or '').strip()
    codes = {x for x in (code, _normalize_scan(raw)) if x}
    if not codes:
        raise HTTPException(422, detail='Отсканируйте короб или ячейку')
    t = str(target_type or 'AUTO').upper()
    if t not in {'AUTO', 'BOX', 'LOCATION'}:
        raise HTTPException(422, detail='Неизвестный тип места приёмки')
    matches = []
    if t != 'BOX':
        matches = db.execute(select(Location).where(
            Location.warehouse_id == warehouse_id,
            or_(Location.code.in_(codes), Location.barcode.in_(codes))
        ).limit(2)).scalars().all()
        if not matches:
            matches = db.execute(select(Location).where(
                Location.warehouse_id == warehouse_id, Location.name.in_(codes)
            ).limit(2)).scalars().all()
    found = db.execute(select(Container).where(
        Container.client_id == client_id, Container.warehouse_id == warehouse_id,
        or_(Container.barcode.in_(codes), Container.name.in_(codes))
    ).limit(2)).scalars().all() if t != 'LOCATION' else []
    by_id = load_location_map(db, warehouse_id,
        [x.id for x in matches] + [x.location_id for x in found])
    if len(found) > 1 or (not found and len(matches) > 1):
        raise HTTPException(409, detail='Название неоднозначно. Отсканируйте QR конкретного короба или ячейки.')
    if found:
        box = found[0]
        if str(getattr(box,'container_type','') or '').upper() in {'DEFECT_BOX','TRANSIT_BOX','FBO_BOX'}:
            raise HTTPException(409, detail='Это короб специального хранения. Используйте соответствующий раздел приёмки.')
        loc = by_id.get(str(box.location_id))
        if not loc or not getattr(loc, 'is_active', True):
            raise HTTPException(409, detail='У короба нет активной ячейки. Укажите его адрес в основной программе.')
        display = str(getattr(box,'name',None) or getattr(box,'barcode',None) or code)
        return {'kind':'BOX','code':display,'display_name':display,'location_id':str(box.location_id),
                'location_code':str(loc.code or ''),'location_display':location_label(loc,by_id),'container_id':str(box.id)}
    if matches:
        loc = matches[0]
        if not getattr(loc, 'is_active', True):
            raise HTTPException(409, detail='Ячейка неактивна. Выберите активное место хранения.')
        display = location_label(loc,by_id)
        return {'kind':'LOCATION','code':str(loc.code),'display_name':display,'location_id':str(loc.id),
                'location_code':str(loc.code),'location_display':display,'container_id':None}
    raise HTTPException(404,detail='Место не найдено. Сначала создайте короб или ячейку в FulfillCore.')


@compat_router.get('/tsd/place/resolve')
def tsd_resolve_place(client_id: uuid.UUID, warehouse_id: uuid.UUID, code: str, target_type: str='AUTO', db: Session=Depends(get_db)):
    scope(db,client_id,warehouse_id);install_schema(db);return _resolve_place(db,client_id,warehouse_id,code,target_type)


def _active_receipt(db, client_id, warehouse_id, worker):
    return db.execute(text("""SELECT * FROM fc_r145f19_tsd_receipts WHERE client_id=:c AND warehouse_id=:w AND lower(worker)=lower(:p) AND status='DRAFT' ORDER BY created_at DESC LIMIT 1"""),{'c':str(client_id),'w':str(warehouse_id),'p':worker}).mappings().first()


def _receipt_public(db, receipt):
    if not receipt:return None
    places=db.execute(text("SELECT * FROM fc_r145f19_tsd_places WHERE receipt_id=:r ORDER BY created_at"),{'r':str(receipt['id'])}).mappings().all()
    lines=db.execute(text("SELECT * FROM fc_r145f19_tsd_lines WHERE receipt_id=:r ORDER BY scanned_at"),{'r':str(receipt['id'])}).mappings().all()
    result = {'id':str(receipt['id']),'reference':receipt['reference'],'worker':receipt['worker'],'status':receipt['status'],'places':[dict(x) for x in places],'lines':[dict(x) for x in lines],'units':sum(int(x['quantity'] or 0) for x in lines)}
    return enrich_receipt(db, result, receipt['warehouse_id'])


@compat_router.post('/tsd/receipts/open-place')
def tsd_open_place(payload:TsdPlaceOpen,db:Session=Depends(get_db)):
    scope(db,payload.client_id,payload.warehouse_id);install_schema(db)
    place=_resolve_place(db,payload.client_id,payload.warehouse_id,payload.code,payload.target_type)
    receipt=_active_receipt(db,payload.client_id,payload.warehouse_id,payload.worker)
    if receipt and _clean(receipt['reference'])!=_clean(payload.reference):
        raise HTTPException(409,detail=f"У сотрудника уже открыт черновик «{receipt['reference']}». Завершите или удалите его.")
    if not receipt:
        rid=uuid.uuid4();db.execute(text("INSERT INTO fc_r145f19_tsd_receipts(id,client_id,warehouse_id,worker,reference) VALUES(:id,:c,:w,:p,:r)"),{'id':str(rid),'c':str(payload.client_id),'w':str(payload.warehouse_id),'p':payload.worker,'r':payload.reference});receipt=_active_receipt(db,payload.client_id,payload.warehouse_id,payload.worker)
    db.execute(text("UPDATE fc_r145f19_tsd_places SET status='DONE' WHERE receipt_id=:r AND status='OPEN'"),{'r':str(receipt['id'])})
    pid=uuid.uuid4();db.execute(text("INSERT INTO fc_r145f19_tsd_places(id,receipt_id,target_kind,target_code,location_id,container_id) VALUES(:id,:r,:k,:code,:loc,:box)"),{'id':str(pid),'r':str(receipt['id']),'k':place['kind'],'code':place['code'],'loc':place['location_id'],'box':place['container_id']})
    db.commit();return {'ok':True,'place':place,'receipt':_receipt_public(db,_active_receipt(db,payload.client_id,payload.warehouse_id,payload.worker))}


@compat_router.get('/tsd/receipts/active')
def tsd_active(client_id:uuid.UUID,warehouse_id:uuid.UUID,worker:str,db:Session=Depends(get_db)):
    scope(db,client_id,warehouse_id);install_schema(db);return {'receipt':_receipt_public(db,_active_receipt(db,client_id,warehouse_id,worker))}


@compat_router.post('/tsd/receipts/scan')
def tsd_scan(payload:TsdScan,db:Session=Depends(get_db)):
    scope(db,payload.client_id,payload.warehouse_id);install_schema(db);receipt=_active_receipt(db,payload.client_id,payload.warehouse_id,payload.worker)
    if not receipt:raise HTTPException(409,detail='Сначала откройте короб или ячейку')
    place=db.execute(text("SELECT * FROM fc_r145f19_tsd_places WHERE receipt_id=:r AND status='OPEN' ORDER BY created_at DESC LIMIT 1"),{'r':str(receipt['id'])}).mappings().first()
    if not place:raise HTTPException(409,detail='Сначала откройте короб или ячейку')
    barcode=_normalize_scan(payload.barcode)
    skus=db.execute(select(SKU).where(SKU.client_id==payload.client_id,SKU.barcode==barcode)).scalars().all()
    if len(skus)>1:raise HTTPException(409,detail='ШК привязан к нескольким SKU')
    if not skus:raise HTTPException(404,detail={'code':'UNKNOWN_PRODUCT','barcode':barcode,'message':'Новый товар. Укажите артикул, размер, название и категорию.'})
    sku=skus[0];db.execute(text("INSERT INTO fc_r145f19_tsd_lines(id,receipt_id,place_id,sku_id,barcode,article,size,name,quantity) VALUES(:id,:r,:p,:s,:b,:a,:z,:n,1)"),{'id':str(uuid.uuid4()),'r':str(receipt['id']),'p':str(place['id']),'s':str(sku.id),'b':sku.barcode or barcode,'a':sku.article or '','z':sku.size or '','n':sku.name or ''});db.commit()
    return {'ok':True,'receipt':_receipt_public(db,_active_receipt(db,payload.client_id,payload.warehouse_id,payload.worker))}


@compat_router.post('/tsd/receipts/register')
def tsd_register(payload:TsdRegister,db:Session=Depends(get_db)):
    scope(db,payload.client_id,payload.warehouse_id);install_schema(db);barcode=_normalize_scan(payload.barcode)
    sku=db.execute(select(SKU).where(SKU.client_id==payload.client_id,SKU.barcode==barcode)).scalars().first()
    if not sku:
        sku=SKU(id=uuid.uuid4(),client_id=payload.client_id,sku_code='LOCAL-'+uuid.uuid5(payload.client_id,barcode).hex,barcode=barcode,article=payload.article.strip(),size=payload.size.strip(),name=(payload.name.strip() or payload.article.strip()),unit='PCS',is_active=True);db.add(sku);db.flush()
    category_upsert(CategoryUpsert(client_id=payload.client_id,sku_id=sku.id,barcode=barcode,article=sku.article or '',category=payload.category or 'Без категории'),db)
    return tsd_scan(TsdScan(client_id=payload.client_id,warehouse_id=payload.warehouse_id,worker=payload.worker,barcode=barcode),db)


@compat_router.post('/tsd/receipts/undo')
def tsd_undo(payload:TsdReceiptAction,db:Session=Depends(get_db)):
    scope(db,payload.client_id,payload.warehouse_id);install_schema(db);r=_active_receipt(db,payload.client_id,payload.warehouse_id,payload.worker)
    if not r:raise HTTPException(404,detail='Черновик не найден')
    line=db.execute(text("SELECT id FROM fc_r145f19_tsd_lines WHERE receipt_id=:r ORDER BY scanned_at DESC LIMIT 1"),{'r':str(r['id'])}).scalar()
    if line:db.execute(text("DELETE FROM fc_r145f19_tsd_lines WHERE id=:id"),{'id':str(line)});db.commit()
    return {'ok':True,'receipt':_receipt_public(db,_active_receipt(db,payload.client_id,payload.warehouse_id,payload.worker))}


@compat_router.post('/tsd/receipts/next-place')
def tsd_next(payload:TsdReceiptAction,db:Session=Depends(get_db)):
    scope(db,payload.client_id,payload.warehouse_id);install_schema(db);r=_active_receipt(db,payload.client_id,payload.warehouse_id,payload.worker)
    if not r:raise HTTPException(404,detail='Черновик не найден')
    db.execute(text("UPDATE fc_r145f19_tsd_places SET status='DONE' WHERE receipt_id=:r AND status='OPEN'"),{'r':str(r['id'])});db.commit();return {'ok':True,'receipt':_receipt_public(db,_active_receipt(db,payload.client_id,payload.warehouse_id,payload.worker))}


@compat_router.post('/tsd/receipts/cancel')
def tsd_cancel(payload:TsdReceiptAction,db:Session=Depends(get_db)):
    scope(db,payload.client_id,payload.warehouse_id);install_schema(db);r=_active_receipt(db,payload.client_id,payload.warehouse_id,payload.worker)
    if not r:return {'ok':True}
    db.execute(text("DELETE FROM fc_r145f19_tsd_receipts WHERE id=:id"),{'id':str(r['id'])});db.commit();return {'ok':True}


@compat_router.post('/tsd/receipts/complete')
def tsd_complete(payload:TsdReceiptAction,db:Session=Depends(get_db)):
    scope(db,payload.client_id,payload.warehouse_id);install_schema(db);r=_active_receipt(db,payload.client_id,payload.warehouse_id,payload.worker)
    if not r:raise HTTPException(404,detail='Черновик не найден')
    rows=db.execute(text("""SELECT l.sku_id,l.quantity,p.location_id,p.container_id FROM fc_r145f19_tsd_lines l JOIN fc_r145f19_tsd_places p ON p.id=l.place_id WHERE l.receipt_id=:r ORDER BY l.scanned_at"""),{'r':str(r['id'])}).mappings().all()
    if not rows:raise HTTPException(409,detail='В приёмке нет товара')
    grouped=defaultdict(Decimal)
    for x in rows:grouped[(str(x['sku_id']),str(x['location_id']),str(x['container_id'] or ''))]+=Decimal(str(x['quantity'] or 0))
    lines=[]
    for (sku_id,loc_id,box_id),qty in grouped.items():
        lines.append(StockLine(sku_id=uuid.UUID(sku_id),quantity=qty,destination_location_id=uuid.UUID(loc_id),destination_container_id=uuid.UUID(box_id) if box_id else None,reason=f"TSD_RECEIVING:{r['id']}"))
    op=execute_operation(db,OperationCreate(client_id=payload.client_id,warehouse_id=payload.warehouse_id,operation_type='RECEIVING',idempotency_key=f"R145F19:TSD:{r['id']}",performed_by=payload.worker,reference=r['reference'],comment='Приёмка ТСД · короб или ячейка · MAIN',lines=lines),commit=False)
    for (sku_id,loc_id,box_id),qty in grouped.items():reconcile_stock_bucket(db,client_id=payload.client_id,warehouse_id=payload.warehouse_id,sku_id=uuid.UUID(sku_id),location_id=uuid.UUID(loc_id),container_id=uuid.UUID(box_id) if box_id else None,preferred_bucket='STORAGE_ONLY',lock=True)
    db.execute(text("UPDATE fc_r145f19_tsd_receipts SET status='COMPLETED',completed_at=now() WHERE id=:id"),{'id':str(r['id'])});db.commit();return {'ok':True,'operation_id':str(op.id),'units':sum(int(x['quantity']) for x in rows),'reference':r['reference']}
