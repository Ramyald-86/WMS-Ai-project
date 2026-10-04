"""Local product views and fast warehouse-history export.

Marketplace card pages in this router never call WB. WB is contacted only by the
explicit import/supplement actions in the marketplace router. The local Product
Base is SKU-centric and remains independent from whether a WB card is visible.
"""
import hashlib
import io
import json
import math
import os
import re
import threading
import time
import uuid
import zipfile
from xml.etree import ElementTree as ET
from copy import copy
from datetime import date, datetime
from pathlib import Path

import httpx
from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, Response
from openpyxl import Workbook, load_workbook
from openpyxl.utils import get_column_letter
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.comments import Comment
from sqlalchemy import String, cast, func, or_, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, load_only
from starlette.concurrency import run_in_threadpool

from app.core.database import get_db
from app.models import Client, MarketplaceProduct, SKU
from app.services.local_catalog_rules import barcode as normalize_product_barcode

router = APIRouter(prefix="/warehouse-ui", tags=["Локальные карточки и база товаров"])


@router.get("/catalog/local-products")
def local_products(
    client_id: uuid.UUID,
    search: str = "",
    limit: int = 5000,
    db: Session = Depends(get_db),
):
    """Card-oriented Product Base built only from trusted local identities.

    Rows are included when their barcode is present in a downloaded WB card or
    when the SKU was consciously registered as LOCAL-*. Legacy technical SKU
    rows that are not backed by either source are intentionally hidden from this
    operator-facing directory; stock/history are not modified.
    """
    if not db.get(Client, client_id):
        raise HTTPException(status_code=404, detail="Клиент не найден")
    limit = min(max(int(limit or 5000), 1), 20000)
    q = str(search or "").strip().casefold()

    products = _latest_wb_products_for_product_base(db, client_id)
    barcode_products = {}
    ambiguous = set()
    for product in products:
        for code in _product_barcodes(product):
            if code in barcode_products and _card_key(barcode_products[code]) != _card_key(product):
                ambiguous.add(code)
            else:
                barcode_products[code] = product
    for code in ambiguous:
        barcode_products.pop(code, None)

    hidden = _hidden_product_base_keys(db, client_id)
    stmt = select(SKU).where(SKU.client_id == client_id, SKU.is_active == True)  # noqa: E712
    rows = db.execute(stmt.order_by(SKU.article.asc().nulls_last(), SKU.size.asc().nulls_last(), SKU.barcode.asc()).limit(50000)).scalars().all()
    result = []
    for row in rows:
        code = str(row.barcode or "").strip()
        if not code:
            continue
        product = barcode_products.get(code)
        sku_code = str(getattr(row, "sku_code", "") or "")
        # Product Base is intentionally stricter than raw SKUs: only WB-backed
        # identities or deliberately registered local goods are shown.
        if product is None and not sku_code.startswith("LOCAL-"):
            continue
        if product is not None:
            card_key = "wb:" + _card_key(product)
            photos = getattr(product, "photos", None) or []
            source = "WB"
            product_id = str(product.id)
            nm_id = getattr(product, "external_product_id", None)
            title = str(getattr(product, "title", "") or row.name or row.article or code)
            brand = str(getattr(product, "brand", "") or "")
            subject_name = str(getattr(product, "subject_name", "") or "")
        else:
            article_key = str(row.article or "").strip().casefold()
            name_key = str(row.name or "").strip().casefold()
            card_key = f"local:{article_key}|{name_key}"
            photos = []
            source = "LOCAL"
            product_id = None
            nm_id = None
            title = str(row.name or row.article or code)
            brand = ""
            subject_name = ""
        if card_key in hidden:
            continue
        item = {
            "id": str(row.id),
            "card_key": card_key,
            "barcode": code,
            "article": str(row.article or ""),
            "size": str(row.size or ""),
            "name": str(row.name or ""),
            "title": title,
            "source": source,
            "product_id": product_id,
            "nm_id": nm_id,
            "brand": brand,
            "subject_name": subject_name,
            "photos": photos[:1],
        }
        if q and q not in " ".join(str(item.get(k) or "") for k in ("barcode", "article", "size", "name", "title", "brand", "nm_id")).casefold():
            continue
        result.append(item)
    total = len(result)
    return {
        "rows": result[:limit],
        "total": total,
        "truncated": total > limit,
        "hidden_count": len(hidden),
        "ambiguous_wb_barcodes": len(ambiguous),
        "source": "LOCAL_DB",
        "wb_api_called": False,
    }


def _first_product_photo(product):
    photos = getattr(product, "photos", None) or []
    first = photos[0] if photos else None
    if isinstance(first, str):
        return first.strip()
    if isinstance(first, dict):
        return str(
            first.get("big")
            or first.get("c516x688")
            or first.get("c246x328")
            or first.get("square")
            or ""
        ).strip()
    return ""


@router.get("/catalog/product-base/lookup")
def product_base_lookup(
    client_id: uuid.UUID,
    barcode_value: str,
    db: Session = Depends(get_db),
):
    """Exact browser lookup against the local Product Base only.

    This endpoint never calls WB. An already-saved local WB snapshot may enrich
    an existing local SKU with title/brand/photo metadata, but a WB-only card
    without a local SKU is deliberately not treated as a Product Base hit.
    """
    if not db.get(Client, client_id):
        raise HTTPException(status_code=404, detail="Клиент не найден")
    try:
        code = normalize_product_barcode(barcode_value)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    skus = db.execute(
        select(SKU).where(
            SKU.client_id == client_id,
            SKU.is_active == True,  # noqa: E712
            SKU.barcode == code,
        )
    ).scalars().all()
    if not skus:
        return {
            "found": False,
            "barcode": code,
            "source": "LOCAL_PRODUCT_BASE",
            "wb_api_called": False,
        }
    if len(skus) > 1:
        return {
            "found": False,
            "barcode": code,
            "source": "LOCAL_PRODUCT_BASE",
            "conflict": True,
            "warning": "ШК привязан к нескольким локальным SKU. Проверьте Базу товаров перед приёмкой.",
            "wb_api_called": False,
        }

    sku = skus[0]
    like_code = f"%{code}%"
    candidates = db.execute(
        select(MarketplaceProduct).options(load_only(
            MarketplaceProduct.id,
            MarketplaceProduct.client_id,
            MarketplaceProduct.provider,
            MarketplaceProduct.external_product_id,
            MarketplaceProduct.vendor_code,
            MarketplaceProduct.title,
            MarketplaceProduct.brand,
            MarketplaceProduct.subject_name,
            MarketplaceProduct.photos,
            MarketplaceProduct.sizes,
            MarketplaceProduct.barcodes,
            MarketplaceProduct.updated_at,
            MarketplaceProduct.created_at,
        )).where(
            MarketplaceProduct.client_id == client_id,
            MarketplaceProduct.provider == "WB",
            or_(
                cast(MarketplaceProduct.barcodes, String).like(like_code),
                cast(MarketplaceProduct.sizes, String).like(like_code),
            ),
        ).order_by(MarketplaceProduct.updated_at.desc(), MarketplaceProduct.created_at.desc())
        .limit(50)
    ).scalars().all()

    logical = {}
    for product in candidates:
        if code not in _product_barcodes(product):
            continue
        logical.setdefault(_card_key(product), product)

    product = next(iter(logical.values())) if len(logical) == 1 else None
    article_key = str(sku.article or "").strip().casefold()
    name_key = str(sku.name or "").strip().casefold()
    card_key = "wb:" + _card_key(product) if product is not None else f"local:{article_key}|{name_key}"
    hidden = _hidden_product_base_keys(db, client_id)
    if card_key in hidden:
        return {
            "found": False,
            "barcode": code,
            "hidden": True,
            "source": "LOCAL_PRODUCT_BASE",
            "wb_api_called": False,
        }

    photo = _first_product_photo(product) if product is not None else ""
    title = (
        str(getattr(product, "title", "") or sku.name or sku.article or code)
        if product is not None else str(sku.name or sku.article or code)
    )
    brand = str(getattr(product, "brand", "") or "") if product is not None else ""
    subject_name = str(getattr(product, "subject_name", "") or "") if product is not None else ""
    warning = ""
    if len(logical) > 1:
        warning = "ШК найден в нескольких локально сохранённых WB-карточках. Фото и WB-метаданные скрыты до устранения конфликта."

    return {
        "found": True,
        "barcode": code,
        "article": str(sku.article or ""),
        "size": str(sku.size or ""),
        "name": title,
        "local_name": str(sku.name or ""),
        "brand": brand,
        "subject_name": subject_name,
        "photo": photo,
        "has_photo": bool(photo),
        "enriched": product is not None,
        "source": "LOCAL_PRODUCT_BASE",
        "warning": warning,
        "wb_api_called": False,
    }


def _product_barcodes(product):
    direct = [str(x or "").strip() for x in (getattr(product, "barcodes", None) or [])]
    from_sizes = []
    for size in (getattr(product, "sizes", None) or []):
        if isinstance(size, dict):
            from_sizes.extend(str(x or "").strip() for x in (size.get("skus") or []))
    return list(dict.fromkeys(x for x in [*direct, *from_sizes] if x))


def _serialize_product(product):
    barcodes = _product_barcodes(product)
    return {
        "id": str(product.id),
        "nm_id": getattr(product, "external_product_id", None),
        "vendor_code": str(getattr(product, "vendor_code", "") or ""),
        "title": str(getattr(product, "title", "") or ""),
        "brand": str(getattr(product, "brand", "") or ""),
        "subject_name": str(getattr(product, "subject_name", "") or ""),
        "photos": getattr(product, "photos", None) or [],
        "sizes": getattr(product, "sizes", None) or [],
        "barcodes": barcodes,
        "description": str(getattr(product, "description", "") or ""),
        "composition": getattr(product, "composition", None),
        "color": getattr(product, "color", None),
        "country": getattr(product, "country", None),
        "characteristics": getattr(product, "characteristics", None) or [],
        "inventory": {"quantity": 0, "sku_count": 0, "not_loaded": True},
        "local_only": False,
    }


def _serialize_product_summary(product):
    # Keep the list/index response deliberately small. Full descriptions,
    # characteristics and complete size JSON are read only when a card is opened.
    raw_sizes = getattr(product, "sizes", None) or []
    size_labels = []
    for size in raw_sizes:
        if not isinstance(size, dict):
            continue
        tech = str(size.get("techSize") or "").strip()
        wb = str(size.get("wbSize") or "").strip()
        key = (tech, wb)
        if key not in {(x.get("techSize", ""), x.get("wbSize", "")) for x in size_labels}:
            size_labels.append({"techSize": tech, "wbSize": wb})
        if len(size_labels) >= 20:
            break
    photos = getattr(product, "photos", None) or []
    stamp = getattr(product, "updated_at", None) or getattr(product, "created_at", None)
    return {
        "id": str(product.id),
        "nm_id": getattr(product, "external_product_id", None),
        "vendor_code": str(getattr(product, "vendor_code", "") or ""),
        "title": str(getattr(product, "title", "") or ""),
        "brand": str(getattr(product, "brand", "") or ""),
        "subject_name": str(getattr(product, "subject_name", "") or ""),
        "photos": photos[:1],
        "sizes": size_labels,
        "barcodes": _product_barcodes(product),
        "inventory": {"quantity": 0, "sku_count": 0, "not_loaded": True},
        "local_only": False,
        "updated_at": stamp.isoformat() if stamp else None,
    }


def _card_key(product) -> str:
    nm = str(getattr(product, "external_product_id", "") or "").strip()
    if nm:
        return f"nm:{nm}"
    vendor = str(getattr(product, "vendor_code", "") or "").strip().casefold()
    return f"vendor:{vendor}"


def _ensure_card_visibility_schema(db: Session):
    # Metadata only. It does not touch stock, reserve, orders, receipts or MarketplaceProduct.
    db.execute(text("""
        CREATE TABLE IF NOT EXISTS fc_wb_card_visibility (
            client_id UUID NOT NULL,
            card_key TEXT NOT NULL,
            hidden BOOLEAN NOT NULL DEFAULT FALSE,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (client_id, card_key)
        )
    """))
    db.commit()


def _hidden_card_keys(db: Session, client_id: uuid.UUID) -> set[str]:
    _ensure_card_visibility_schema(db)
    rows = db.execute(text("""
        SELECT card_key FROM fc_wb_card_visibility
        WHERE client_id=:client_id AND hidden=TRUE
    """), {"client_id": client_id}).scalars().all()
    return {str(x) for x in rows}


def _ensure_product_base_visibility_schema(db: Session):
    db.execute(text("""
        CREATE TABLE IF NOT EXISTS fc_product_base_visibility (
            client_id UUID NOT NULL,
            card_key TEXT NOT NULL,
            hidden BOOLEAN NOT NULL DEFAULT FALSE,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (client_id, card_key)
        )
    """))
    db.commit()


def _hidden_product_base_keys(db: Session, client_id: uuid.UUID) -> set[str]:
    _ensure_product_base_visibility_schema(db)
    rows = db.execute(text("""
        SELECT card_key FROM fc_product_base_visibility
        WHERE client_id=:client_id AND hidden=TRUE
    """), {"client_id": client_id}).scalars().all()
    return {str(x) for x in rows}


def _latest_wb_products_for_product_base(db: Session, client_id: uuid.UUID):
    """Newest local WB snapshot for metadata; independent from WB-card UI hiding."""
    rows = db.execute(
        select(MarketplaceProduct).where(
            MarketplaceProduct.client_id == client_id,
            MarketplaceProduct.provider == "WB",
        ).order_by(MarketplaceProduct.updated_at.desc(), MarketplaceProduct.created_at.desc())
    ).scalars().all()
    result, seen = [], set()
    for product in rows:
        key = _card_key(product)
        if key in seen:
            continue
        seen.add(key)
        result.append(product)
    return result


@router.post("/catalog/product-base/hide")
def hide_product_base_card(client_id: uuid.UUID, card_key: str, db: Session = Depends(get_db)):
    if not db.get(Client, client_id):
        raise HTTPException(status_code=404, detail="Клиент не найден")
    key = str(card_key or "").strip()
    if not key or len(key) > 500:
        raise HTTPException(status_code=422, detail="Некорректная карточка")
    _ensure_product_base_visibility_schema(db)
    db.execute(text("""
        INSERT INTO fc_product_base_visibility(client_id,card_key,hidden,updated_at)
        VALUES (:client_id,:card_key,TRUE,now())
        ON CONFLICT (client_id,card_key)
        DO UPDATE SET hidden=TRUE,updated_at=now()
    """), {"client_id": client_id, "card_key": key})
    db.commit()
    return {
        "ok": True,
        "hidden": True,
        "card_key": key,
        "note": "Карточка убрана только из Базы товаров. Остатки, история и карточки WB не изменены.",
    }


def _visible_wb_products(db: Session, client_id: uuid.UUID):
    """Newest visible snapshot of every logical WB card, from local PostgreSQL only."""
    rows = db.execute(
        select(MarketplaceProduct).options(load_only(
            MarketplaceProduct.id,
            MarketplaceProduct.client_id,
            MarketplaceProduct.provider,
            MarketplaceProduct.external_product_id,
            MarketplaceProduct.vendor_code,
            MarketplaceProduct.title,
            MarketplaceProduct.sizes,
            MarketplaceProduct.barcodes,
            MarketplaceProduct.updated_at,
            MarketplaceProduct.created_at,
        )).where(
            MarketplaceProduct.client_id == client_id,
            MarketplaceProduct.provider == "WB",
        ).order_by(MarketplaceProduct.updated_at.desc(), MarketplaceProduct.created_at.desc())
    ).scalars().all()
    hidden = _hidden_card_keys(db, client_id)
    result = []
    seen = set()
    for product in rows:
        key = _card_key(product)
        if key in seen:
            continue
        seen.add(key)
        if key in hidden:
            continue
        result.append(product)
    return result


def _wb_card_sku_candidates(products):
    """Return one unambiguous WB identity per ordinary product barcode.

    This helper is intentionally stock-agnostic: it only translates the already
    downloaded WB card snapshot into candidate rows for the local Product Base.
    """
    buckets = {}
    invalid = 0
    for product in products:
        nm_id = str(getattr(product, "external_product_id", "") or "").strip()
        article = str(getattr(product, "vendor_code", "") or "").strip() or nm_id
        name = str(getattr(product, "title", "") or "").strip() or article
        sizes = getattr(product, "sizes", None) or []
        covered = set()
        for size in sizes:
            if not isinstance(size, dict):
                continue
            size_name = str(size.get("techSize") or size.get("wbSize") or "Без размера").strip() or "Без размера"
            for raw in (size.get("skus") or []):
                try:
                    code = normalize_product_barcode(raw)
                except ValueError:
                    invalid += 1
                    continue
                covered.add(code)
                signature = (nm_id, article.casefold(), size_name.casefold())
                buckets.setdefault(code, {}).setdefault(signature, {
                    "barcode": code,
                    "article": article or code,
                    "size": size_name,
                    "name": name or article or code,
                    "nm_id": nm_id,
                })
        # A few historical WB snapshots keep a barcode at card level without a
        # populated size row. Do not lose it: import it as "Без размера".
        for raw in (getattr(product, "barcodes", None) or []):
            try:
                code = normalize_product_barcode(raw)
            except ValueError:
                invalid += 1
                continue
            if code in covered:
                continue
            size_name = "Без размера"
            signature = (nm_id, article.casefold(), size_name.casefold())
            buckets.setdefault(code, {}).setdefault(signature, {
                "barcode": code,
                "article": article or code,
                "size": size_name,
                "name": name or article or code,
                "nm_id": nm_id,
            })
    candidates = {}
    conflicts = {}
    for code, variants in buckets.items():
        if len(variants) == 1:
            candidates[code] = next(iter(variants.values()))
        else:
            conflicts[code] = list(variants.values())
    return candidates, conflicts, invalid


@router.post("/catalog/product-base/import-wb-cards")
def import_wb_cards_into_product_base(client_id: uuid.UUID, db: Session = Depends(get_db)):
    """Materialise local WB-card barcodes into SKU rows without touching stock.

    The WB cabinet is *not* called here. Operators first refresh "Карточки WB"
    explicitly, then this action copies their barcode/article/size identities into
    the local Product Base. Existing warehouse SKU identity wins on disagreement.
    """
    if not db.get(Client, client_id):
        raise HTTPException(status_code=404, detail="Клиент не найден")
    products = _visible_wb_products(db, client_id)
    if not products:
        return {
            "ok": True, "cards": 0, "barcodes": 0, "created": 0, "enriched": 0,
            "already_present": 0, "skipped_conflicts": 0, "skipped_existing_conflicts": 0,
            "skipped_archived": 0, "invalid_barcodes": 0,
            "message": "Локальных карточек WB пока нет. Сначала откройте «Карточки WB» и нажмите «Дополнить из WB».",
        }
    candidates, conflicts, invalid = _wb_card_sku_candidates(products)
    codes = list(candidates)
    existing = []
    # Keep PostgreSQL bind lists bounded on large catalogues.
    for start in range(0, len(codes), 4000):
        chunk = codes[start:start + 4000]
        if not chunk:
            continue
        existing.extend(db.execute(select(SKU).where(
            SKU.client_id == client_id,
            SKU.barcode.in_(chunk),
        )).scalars().all())
    by_code = {}
    for sku in existing:
        by_code.setdefault(str(sku.barcode or ""), []).append(sku)

    created = enriched = already = archived = existing_conflicts = 0
    conflict_examples = []
    for code, row in candidates.items():
        matches = by_code.get(code, [])
        active = [sku for sku in matches if bool(getattr(sku, "is_active", True))]
        if len(active) > 1:
            existing_conflicts += 1
            if len(conflict_examples) < 20:
                conflict_examples.append({"barcode": code, "reason": "ШК уже привязан к нескольким активным SKU"})
            continue
        if not active and matches:
            archived += 1
            continue
        if active:
            sku = active[0]
            current_article = str(getattr(sku, "article", "") or "").strip()
            current_size = str(getattr(sku, "size", "") or "").strip()
            incoming_article = str(row["article"] or "").strip()
            incoming_size = str(row["size"] or "").strip()
            # Never silently rewrite the warehouse identity of an SKU that already
            # has stock/history. Conflicts are reported for manual review.
            article_placeholder = current_article in {"", code, str(getattr(sku, "sku_code", "") or ""), str(row.get("nm_id") or "")}
            if current_article and incoming_article and not article_placeholder and current_article.casefold() != incoming_article.casefold():
                existing_conflicts += 1
                if len(conflict_examples) < 20:
                    conflict_examples.append({"barcode": code, "reason": f"Артикул в базе: {current_article}; в WB: {incoming_article}"})
                continue
            size_placeholder = current_size.casefold() in {"", "без размера"}
            if current_size and incoming_size and not size_placeholder and current_size.casefold() != incoming_size.casefold():
                existing_conflicts += 1
                if len(conflict_examples) < 20:
                    conflict_examples.append({"barcode": code, "reason": f"Размер в базе: {current_size}; в WB: {incoming_size}"})
                continue
            changed = False
            if article_placeholder and incoming_article:
                sku.article = incoming_article
                changed = True
            if size_placeholder and incoming_size:
                sku.size = incoming_size
                changed = True
            current_name = str(getattr(sku, "name", "") or "").strip()
            placeholders = {"", code, str(getattr(sku, "sku_code", "") or ""), current_article}
            if row["name"] and current_name in placeholders:
                sku.name = row["name"]
                changed = True
            if changed:
                enriched += 1
            else:
                already += 1
            continue
        sku = SKU(
            id=uuid.uuid4(),
            client_id=client_id,
            sku_code=f"WB-{code}",
            barcode=code,
            article=row["article"] or code,
            size=row["size"] or "Без размера",
            name=row["name"] or row["article"] or code,
            unit="PCS",
            is_active=True,
        )
        db.add(sku)
        created += 1
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="База товаров изменилась параллельно. Повторите загрузку из карточек WB.") from exc
    return {
        "ok": True,
        "cards": len(products),
        "barcodes": len(candidates),
        "created": created,
        "enriched": enriched,
        "already_present": already,
        "skipped_conflicts": len(conflicts),
        "skipped_existing_conflicts": existing_conflicts,
        "skipped_archived": archived,
        "invalid_barcodes": invalid,
        "conflict_examples": conflict_examples,
        "wb_api_called": False,
    }



_PRODUCT_BASE_XLSX_MAX_BYTES = 15 * 1024 * 1024
_PRODUCT_BASE_XLSX_MAX_ROWS = 100000
_PRODUCT_BASE_HEADER_ALIASES = {
    "barcode": {"шк", "штрихкод", "штрихкодтовара", "barcode", "barcodes"},
    "article": {"артикул", "арт", "article", "vendorcode", "vendor"},
    "size": {"размер", "size", "techsize", "wbsize"},
}


def _norm_product_base_header(value) -> str:
    return re.sub(r"[^a-zа-я0-9]+", "", str(value or "").strip().casefold().replace("ё", "е"))


def _plain_excel_text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float) and math.isfinite(value) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def _excel_barcode_text(cell) -> str:
    value = cell.value
    if value is None:
        return ""
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, int):
        raw = str(value)
    elif isinstance(value, float) and math.isfinite(value) and value.is_integer():
        raw = str(int(value))
    else:
        raw = str(value).strip()
        if re.fullmatch(r"\d+\.0", raw):
            raw = raw[:-2]
    # If Excel stored a numeric barcode with a fixed all-zero number format,
    # reconstruct leading zeros. The official template marks the barcode column as text.
    fmt = str(getattr(cell, "number_format", "") or "").strip()
    if raw.isdigit() and re.fullmatch(r"0{8}|0{12}|0{13}|0{14}", fmt):
        raw = raw.zfill(len(fmt))
    return raw


def _find_product_base_header(ws):
    for row_num, row in enumerate(ws.iter_rows(min_row=1, max_row=min(ws.max_row, 20)), start=1):
        normalized = [_norm_product_base_header(cell.value) for cell in row]
        mapping = {}
        for field, aliases in _PRODUCT_BASE_HEADER_ALIASES.items():
            for idx, value in enumerate(normalized):
                if value in aliases:
                    mapping[field] = idx
                    break
        if len(mapping) == 3:
            return row_num, mapping
    return None, None


def _parse_product_base_xlsx(content: bytes):
    if not content:
        raise ValueError("Файл пустой")
    if len(content) > _PRODUCT_BASE_XLSX_MAX_BYTES:
        raise ValueError("Файл слишком большой. Максимум 15 МБ")
    if not content.startswith(b"PK"):
        raise ValueError("Нужен файл Excel .xlsx")
    try:
        wb = load_workbook(io.BytesIO(content), read_only=True, data_only=True)
    except Exception as exc:
        raise ValueError("Не удалось прочитать XLSX. Скачайте шаблон и сохраните файл как .xlsx") from exc
    try:
        selected = None
        for ws in wb.worksheets:
            header_row, mapping = _find_product_base_header(ws)
            if mapping:
                selected = (ws, header_row, mapping)
                break
        if not selected:
            raise ValueError("Не найдены обязательные колонки: ШК, Артикул, Размер")
        ws, header_row, mapping = selected
        rows, errors = [], []
        nonblank = 0
        for excel_row, cells in enumerate(ws.iter_rows(min_row=header_row + 1), start=header_row + 1):
            raw_code = _excel_barcode_text(cells[mapping["barcode"]]) if mapping["barcode"] < len(cells) else ""
            article = _plain_excel_text(cells[mapping["article"]].value) if mapping["article"] < len(cells) else ""
            size = _plain_excel_text(cells[mapping["size"]].value) if mapping["size"] < len(cells) else ""
            if not raw_code and not article and not size:
                continue
            nonblank += 1
            if nonblank > _PRODUCT_BASE_XLSX_MAX_ROWS:
                raise ValueError(f"В одном файле можно импортировать не более {_PRODUCT_BASE_XLSX_MAX_ROWS} строк")
            try:
                code = normalize_product_barcode(raw_code)
            except ValueError as exc:
                errors.append({"row": excel_row, "barcode": raw_code, "error": str(exc)})
                continue
            if not article:
                errors.append({"row": excel_row, "barcode": code, "error": "Не указан артикул"})
                continue
            if not size:
                errors.append({"row": excel_row, "barcode": code, "error": "Не указан размер; для безразмерного укажите «Без размера»"})
                continue
            if len(article) > 128:
                errors.append({"row": excel_row, "barcode": code, "error": "Артикул длиннее 128 символов"})
                continue
            if len(size) > 128:
                errors.append({"row": excel_row, "barcode": code, "error": "Размер длиннее 128 символов"})
                continue
            rows.append({"row": excel_row, "barcode": code, "article": article, "size": size})
        return {"sheet": ws.title, "rows_total": nonblank, "rows": rows, "errors": errors}
    finally:
        wb.close()


def _build_product_base_template() -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = "Импорт"
    ws.sheet_view.showGridLines = False
    headers = ["ШК", "Артикул", "Размер"]
    fill = PatternFill("solid", fgColor="1F4E78")
    for col, value in enumerate(headers, start=1):
        cell = ws.cell(1, col, value)
        cell.font = Font(color="FFFFFF", bold=True)
        cell.fill = fill
        cell.alignment = Alignment(horizontal="center", vertical="center")
    ws["A1"].comment = Comment("Обычный товарный ШК: 8, 12, 13 или 14 цифр. Колонка подготовлена как текст, чтобы сохранить ведущие нули.", "FulfillCore")
    ws["B1"].comment = Comment("Артикул товара. Обязательное поле для новых ШК.", "FulfillCore")
    ws["C1"].comment = Comment("Размер товара. Если размера нет, напишите: Без размера", "FulfillCore")
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = "A1:C1"
    ws.column_dimensions["A"].width = 23
    ws.column_dimensions["B"].width = 28
    ws.column_dimensions["C"].width = 22
    ws.row_dimensions[1].height = 26
    # Prepare many blank rows as text inputs so Google/Excel paste keeps barcodes exact.
    for row in range(2, 2002):
        for col in range(1, 4):
            cell = ws.cell(row, col)
            cell.number_format = "@"
            cell.font = Font(color="0000FF")
    info = wb.create_sheet("Инструкция")
    info.sheet_view.showGridLines = False
    lines = [
        ("Импорт в локальную Базу товаров FulfillCore", True),
        ("1. В листе «Импорт» используются только 3 колонки: ШК, Артикул, Размер.", False),
        ("2. Если ШК уже есть у выбранного клиента, FulfillCore ничего с ним не делает и не меняет артикул/размер.", False),
        ("3. Если ШК новый, создаётся только локальная карточка товара. Остатки, резервы и WB при этом не изменяются.", False),
        ("4. После подключения/импорта карточек WB совпавший ШК автоматически получает данные карточки WB: фото, название, описание и другие доступные поля.", False),
        ("5. Для товара без размера в колонке «Размер» укажите «Без размера».", False),
        ("6. ШК должен быть обычным товарным кодом: 8, 12, 13 или 14 цифр. DataMatrix/Честный знак сюда не загружается.", False),
        ("7. Не меняйте названия трёх колонок. Файл сохраняйте в формате .xlsx.", False),
        ("Пример: 4660407266377 | 184176 | XL-2XL", False),
    ]
    for idx, (text_value, bold) in enumerate(lines, start=1):
        c = info.cell(idx, 1, text_value)
        c.font = Font(bold=bold, color="404040")
        c.alignment = Alignment(wrap_text=True, vertical="top")
    info.column_dimensions["A"].width = 115
    info.row_dimensions[1].height = 28
    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()


@router.get("/catalog/product-base/import-template.xlsx")
def product_base_import_template():
    content = _build_product_base_template()
    return Response(
        content=content,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": 'attachment; filename="FulfillCore_Product_Base_Import.xlsx"'},
    )


@router.post("/catalog/product-base/import-xlsx")
async def import_product_base_xlsx(
    client_id: uuid.UUID,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    """Add only missing barcode identities from an XLSX file.

    Existing barcodes are deliberately immutable in this import. The route creates
    no inventory balances and calls no marketplace API. A later local WB-card import
    enriches the Product Base by barcode through the existing local-card projection.
    """
    if not db.get(Client, client_id):
        raise HTTPException(status_code=404, detail="Клиент не найден")
    filename = str(file.filename or "").strip()
    if filename and not filename.casefold().endswith(".xlsx"):
        raise HTTPException(status_code=422, detail="Загрузите файл .xlsx по шаблону FulfillCore")
    content = await file.read(_PRODUCT_BASE_XLSX_MAX_BYTES + 1)
    if len(content) > _PRODUCT_BASE_XLSX_MAX_BYTES:
        raise HTTPException(status_code=413, detail="Файл слишком большой. Максимум 15 МБ")
    try:
        parsed = await run_in_threadpool(_parse_product_base_xlsx, content)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    unique = {}
    duplicate_rows = []
    for row in parsed["rows"]:
        code = row["barcode"]
        if code in unique:
            duplicate_rows.append({"row": row["row"], "barcode": code, "error": f"Дубликат ШК в файле; использована строка {unique[code]['row']}"})
            continue
        unique[code] = row

    db.execute(text("SELECT pg_advisory_xact_lock(hashtext(:key))"), {"key": f"fc:product-base-xlsx:{client_id}"})
    codes = list(unique)
    existing_codes = set()
    for start in range(0, len(codes), 4000):
        chunk = codes[start:start + 4000]
        if chunk:
            existing_codes.update(str(x or "") for x in db.execute(select(SKU.barcode).where(
                SKU.client_id == client_id,
                SKU.barcode.in_(chunk),
            )).scalars().all())

    created = 0
    for code, row in unique.items():
        if code in existing_codes:
            continue
        db.add(SKU(
            id=uuid.uuid4(),
            client_id=client_id,
            sku_code="LOCAL-" + uuid.uuid5(client_id, code).hex,
            barcode=code,
            article=row["article"],
            size=row["size"],
            name=row["article"],
            unit="PCS",
            is_active=True,
        ))
        created += 1
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="База товаров изменилась параллельно. Повторите импорт Excel — существующие ШК будут безопасно пропущены.") from exc

    errors = [*parsed["errors"], *duplicate_rows]
    skipped_existing = sum(1 for code in unique if code in existing_codes)
    return {
        "ok": True,
        "file_name": filename,
        "sheet": parsed["sheet"],
        "rows_total": parsed["rows_total"],
        "valid_unique_barcodes": len(unique),
        "created": created,
        "skipped_existing": skipped_existing,
        "skipped_duplicate_in_file": len(duplicate_rows),
        "invalid_rows": len(parsed["errors"]),
        "errors": errors[:100],
        "errors_truncated": len(errors) > 100,
        "stock_changed": False,
        "wb_api_called": False,
        "note": "Существующие ШК не изменялись. Новые ШК добавлены только в локальную Базу товаров; остатки и резервы не затронуты.",
    }

@router.get("/catalog/wb-cards")
def wb_cards_local(
    client_id: uuid.UUID,
    search: str = "",
    limit: int = 10000,
    db: Session = Depends(get_db),
):
    """Local WB-card snapshot for instant in-browser search; never calls WB."""
    if not db.get(Client, client_id):
        raise HTTPException(status_code=404, detail="Клиент не найден")
    limit = min(max(int(limit or 10000), 1), 20000)
    q = str(search or "").strip().casefold()
    stmt = select(MarketplaceProduct).options(load_only(
        MarketplaceProduct.id,
        MarketplaceProduct.client_id,
        MarketplaceProduct.provider,
        MarketplaceProduct.external_product_id,
        MarketplaceProduct.vendor_code,
        MarketplaceProduct.title,
        MarketplaceProduct.brand,
        MarketplaceProduct.subject_name,
        MarketplaceProduct.photos,
        MarketplaceProduct.sizes,
        MarketplaceProduct.barcodes,
        MarketplaceProduct.updated_at,
        MarketplaceProduct.created_at,
    )).where(
        MarketplaceProduct.client_id == client_id,
        MarketplaceProduct.provider == "WB",
    )
    if q:
        like = f"%{q}%"
        stmt = stmt.where(or_(
            func.lower(func.coalesce(MarketplaceProduct.vendor_code, "")).like(like),
            func.lower(func.coalesce(MarketplaceProduct.title, "")).like(like),
            func.lower(func.coalesce(MarketplaceProduct.brand, "")).like(like),
            func.lower(cast(MarketplaceProduct.external_product_id, String)).like(like),
            func.lower(cast(MarketplaceProduct.barcodes, String)).like(like),
            func.lower(cast(MarketplaceProduct.sizes, String)).like(like),
        ))
    # There can be historical credential snapshots of the same nmID. Read newest first,
    # collapse duplicates, and keep the UI index bounded without deleting source records.
    raw_limit = min(limit * 3, 60000)
    rows = db.execute(
        stmt.order_by(MarketplaceProduct.updated_at.desc(), MarketplaceProduct.created_at.desc()).limit(raw_limit)
    ).scalars().all()
    hidden = _hidden_card_keys(db, client_id)
    products = []
    seen = set()
    hidden_count = 0
    last_import = None
    for product in rows:
        key = _card_key(product)
        if key in seen:
            continue
        seen.add(key)
        stamp = getattr(product, "updated_at", None) or getattr(product, "created_at", None)
        if stamp and (last_import is None or stamp > last_import):
            last_import = stamp
        if key in hidden:
            hidden_count += 1
            continue
        products.append(_serialize_product_summary(product))
        if len(products) >= limit:
            break
    return {
        "products": products,
        "total": len(products),
        "hidden_count": hidden_count,
        "truncated": len(rows) >= raw_limit and len(products) >= limit,
        "last_import_at": last_import.isoformat() if last_import else None,
        "source": "LOCAL_DB",
        "wb_api_called": False,
    }


@router.get("/catalog/wb-cards/{product_id}")
def wb_card_local_detail(product_id: uuid.UUID, client_id: uuid.UUID, db: Session = Depends(get_db)):
    if not db.get(Client, client_id):
        raise HTTPException(status_code=404, detail="Клиент не найден")
    product = db.get(MarketplaceProduct, product_id)
    if not product or product.client_id != client_id or str(product.provider).upper() != "WB":
        raise HTTPException(status_code=404, detail="Локальная карточка WB не найдена")
    return {"product": _serialize_product(product), "source": "LOCAL_DB", "wb_api_called": False}


@router.post("/catalog/wb-cards/{product_id}/hide")
def hide_wb_card_local(product_id: uuid.UUID, client_id: uuid.UUID, db: Session = Depends(get_db)):
    if not db.get(Client, client_id):
        raise HTTPException(status_code=404, detail="Клиент не найден")
    product = db.get(MarketplaceProduct, product_id)
    if not product or product.client_id != client_id or str(product.provider).upper() != "WB":
        raise HTTPException(status_code=404, detail="Локальная карточка WB не найдена")
    _ensure_card_visibility_schema(db)
    db.execute(text("""
        INSERT INTO fc_wb_card_visibility(client_id,card_key,hidden,updated_at)
        VALUES (:client_id,:card_key,TRUE,now())
        ON CONFLICT (client_id,card_key)
        DO UPDATE SET hidden=TRUE,updated_at=now()
    """), {"client_id": client_id, "card_key": _card_key(product)})
    db.commit()
    return {
        "ok": True,
        "hidden": True,
        "product_id": str(product.id),
        "note": "Карточка скрыта только из локального списка. WB, остатки, история и База товаров не изменены.",
    }


@router.post("/catalog/wb-cards/{product_id}/show")
def show_wb_card_local(product_id: uuid.UUID, client_id: uuid.UUID, db: Session = Depends(get_db)):
    if not db.get(Client, client_id):
        raise HTTPException(status_code=404, detail="Клиент не найден")
    product = db.get(MarketplaceProduct, product_id)
    if not product or product.client_id != client_id or str(product.provider).upper() != "WB":
        raise HTTPException(status_code=404, detail="Локальная карточка WB не найдена")
    _ensure_card_visibility_schema(db)
    db.execute(text("""
        INSERT INTO fc_wb_card_visibility(client_id,card_key,hidden,updated_at)
        VALUES (:client_id,:card_key,FALSE,now())
        ON CONFLICT (client_id,card_key)
        DO UPDATE SET hidden=FALSE,updated_at=now()
    """), {"client_id": client_id, "card_key": _card_key(product)})
    db.commit()
    return {"ok": True, "hidden": False, "product_id": str(product.id)}


@router.get("/catalog/products")
def product_base(
    client_id: uuid.UUID,
    search: str = "",
    limit: int = 300,
    db: Session = Depends(get_db),
):
    """Backward-compatible combined view. New UI uses separate WB Cards and Product Base pages."""
    if not db.get(Client, client_id):
        raise HTTPException(status_code=404, detail="Клиент не найден")
    limit = min(max(int(limit or 300), 1), 500)
    q = str(search or "").strip().casefold()
    card_stmt = select(MarketplaceProduct).where(
        MarketplaceProduct.client_id == client_id,
        MarketplaceProduct.provider == "WB",
    )
    if q:
        like = f"%{q}%"
        card_stmt = card_stmt.where(or_(
            func.lower(func.coalesce(MarketplaceProduct.vendor_code, "")).like(like),
            func.lower(func.coalesce(MarketplaceProduct.title, "")).like(like),
            func.lower(func.coalesce(MarketplaceProduct.brand, "")).like(like),
            func.lower(cast(MarketplaceProduct.external_product_id, String)).like(like),
            func.lower(cast(MarketplaceProduct.barcodes, String)).like(like),
            func.lower(cast(MarketplaceProduct.sizes, String)).like(like),
        ))
    cards = db.execute(
        card_stmt.order_by(MarketplaceProduct.updated_at.desc(), MarketplaceProduct.created_at.desc()).limit(limit)
    ).scalars().all()
    products = []
    seen_cards = set()
    hidden = _hidden_card_keys(db, client_id)
    for product in cards:
        key = _card_key(product)
        if key in seen_cards or key in hidden:
            continue
        seen_cards.add(key)
        products.append(_serialize_product(product))
    imported_barcodes = {code for product in products for code in product["barcodes"]}
    sku_stmt = select(SKU).where(SKU.client_id == client_id, SKU.is_active == True)  # noqa: E712
    if q:
        like = f"%{q}%"
        sku_stmt = sku_stmt.where(or_(
            func.lower(func.coalesce(SKU.barcode, "")).like(like),
            func.lower(func.coalesce(SKU.article, "")).like(like),
            func.lower(func.coalesce(SKU.size, "")).like(like),
            func.lower(func.coalesce(SKU.name, "")).like(like),
        ))
    sku_rows = db.execute(sku_stmt.order_by(SKU.article.asc().nulls_last(), SKU.size.asc().nulls_last()).limit(limit)).scalars().all()
    manual_rows = [
        {"id": str(row.id), "barcode": str(row.barcode or ""), "article": str(row.article or ""),
         "size": str(row.size or ""), "name": str(row.name or "")}
        for row in sku_rows if str(row.barcode or "") and str(row.barcode or "") not in imported_barcodes
    ]
    last_import = max((getattr(product, "updated_at", None) or getattr(product, "created_at", None) for product in cards), default=None)
    return {
        "products": products,
        "manual_rows": manual_rows,
        "imported_total": len(products),
        "local_sku_total": len(sku_rows),
        "last_import_at": last_import.isoformat() if last_import else None,
        "source": "LOCAL_DB",
        "wb_api_called": False,
    }


def _as_date(value):
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    raw = str(value or "").strip()
    if not raw:
        return None
    normalized = raw.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(normalized).date()
    except Exception:
        pass
    for fmt in ("%d.%m.%Y %H:%M:%S", "%d.%m.%Y %H:%M", "%d.%m.%Y", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(raw, fmt).date()
        except Exception:
            continue
    return None


def add_separate_date_column(xlsx_bytes: bytes) -> bytes:
    """Append a real Date column without shifting the whole workbook.

    r97 inserted a column and copied every row style. On large histories that made
    openpyxl rewrite substantially more XML than necessary. r100 appends one narrow
    Date column, writes only date values/format, and preserves the rest of the workbook.
    """
    if not xlsx_bytes.startswith(b"PK"):
        raise ValueError("Файл истории не является XLSX")
    wb = load_workbook(io.BytesIO(xlsx_bytes))
    changed = False
    for ws in wb.worksheets:
        if ws.max_row < 1 or ws.max_column < 1:
            continue
        headers = [str(ws.cell(1, col).value or "").strip() for col in range(1, ws.max_column + 1)]
        folded = [h.casefold() for h in headers]
        date_only_cols = [i + 1 for i, h in enumerate(folded) if h in {"дата", "date"}]
        datetime_cols = [i + 1 for i, h in enumerate(folded) if ("дата" in h and "врем" in h) or "datetime" in h or "created_at" in h]
        source_col = datetime_cols[0] if datetime_cols else None
        if source_col is None:
            for col in range(1, min(ws.max_column, 4) + 1):
                sample = next((_as_date(ws.cell(row, col).value) for row in range(2, min(ws.max_row, 20) + 1) if ws.cell(row, col).value not in (None, "")), None)
                if sample is not None:
                    source_col = col
                    break
        if source_col is None:
            continue
        if date_only_cols:
            target_col = date_only_cols[0]
            ws.auto_filter.ref = f"A1:{get_column_letter(ws.max_column)}{ws.max_row}"
            ws.column_dimensions[get_column_letter(target_col)].width = max(ws.column_dimensions[get_column_letter(target_col)].width or 0, 13)
            changed = True
            continue
        target_col = ws.max_column + 1
        source_header = ws.cell(1, source_col)
        header = ws.cell(1, target_col)
        header.value = "Дата"
        header._style = copy(source_header._style)
        header.font = copy(source_header.font)
        header.fill = copy(source_header.fill)
        header.border = copy(source_header.border)
        header.alignment = copy(source_header.alignment)
        header.protection = copy(source_header.protection)
        for row in range(2, ws.max_row + 1):
            d = _as_date(ws.cell(row, source_col).value)
            if d is not None:
                cell = ws.cell(row, target_col)
                cell.value = d
                cell.number_format = "dd.mm.yyyy"
        ws.column_dimensions[get_column_letter(target_col)].width = 13
        ws.auto_filter.ref = f"A1:{get_column_letter(target_col)}{ws.max_row}"
        changed = True
    if not changed:
        raise ValueError("В Excel истории не найдена колонка даты/времени")
    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()


_XLSX_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
_X = "{" + _XLSX_NS + "}"
ET.register_namespace("", _XLSX_NS)


def _xlsx_col_number(ref: str) -> int:
    match = re.match(r"([A-Z]+)", str(ref or ""))
    if not match:
        return 0
    result = 0
    for ch in match.group(1):
        result = result * 26 + ord(ch) - 64
    return result


def _xlsx_col_letter(number: int) -> str:
    value = ""
    while number:
        number, remainder = divmod(number - 1, 26)
        value = chr(65 + remainder) + value
    return value


def _xlsx_shared_strings(data: bytes | None) -> list[str]:
    if not data:
        return []
    root = ET.fromstring(data)
    return ["".join(node.text or "" for node in item.iter(_X + "t")) for item in root.findall(_X + "si")]


def _xlsx_cell_text(cell, shared: list[str]) -> str:
    kind = cell.get("t", "")
    if kind == "inlineStr":
        return "".join(node.text or "" for node in cell.iter(_X + "t"))
    value = cell.find(_X + "v")
    raw = value.text if value is not None else ""
    if kind == "s":
        try:
            return shared[int(raw)]
        except Exception:
            return ""
    return raw or ""


def _xlsx_excel_serial(day: date) -> int:
    # Modern Excel uses the 1899-12-30 epoch (including the historic 1900 leap-year quirk).
    return (day - date(1899, 12, 30)).days


def _xlsx_date_serial(cell, shared: list[str]) -> int | None:
    kind = cell.get("t", "")
    value = cell.find(_X + "v")
    raw = value.text if value is not None else ""
    if kind in {"", "n"}:
        try:
            return math.floor(float(raw))
        except Exception:
            return None
    day = _as_date(raw if kind == "d" else _xlsx_cell_text(cell, shared))
    return _xlsx_excel_serial(day) if day else None


def _xlsx_add_date_style(styles_xml: bytes) -> tuple[bytes, int]:
    root = ET.fromstring(styles_xml)
    numfmts = root.find(_X + "numFmts")
    if numfmts is None:
        numfmts = ET.Element(_X + "numFmts", {"count": "0"})
        children = list(root)
        insert_at = next((i for i, node in enumerate(children) if node.tag == _X + "fonts"), 0)
        root.insert(insert_at, numfmts)
    ids = [int(node.get("numFmtId", "0")) for node in numfmts.findall(_X + "numFmt")]
    fmt_id = max([163, *ids]) + 1
    ET.SubElement(numfmts, _X + "numFmt", {"numFmtId": str(fmt_id), "formatCode": "dd.mm.yyyy"})
    numfmts.set("count", str(len(numfmts.findall(_X + "numFmt"))))
    xfs = root.find(_X + "cellXfs")
    if xfs is None or len(xfs) == 0:
        raise ValueError("В XLSX не найден список стилей")
    attrs = dict(xfs[0].attrib)
    attrs["numFmtId"] = str(fmt_id)
    attrs["applyNumberFormat"] = "1"
    ET.SubElement(xfs, _X + "xf", attrs)
    xfs.set("count", str(len(xfs)))
    return ET.tostring(root, encoding="utf-8", xml_declaration=True), len(xfs) - 1


def add_separate_date_column_fast_xml(xlsx_bytes: bytes) -> bytes:
    """Fast XLSX patch: keep Date immediately beside Date/time.

    The worksheet XML is shifted one column to the right from B onward, then a
    real Excel date column is inserted as B. This keeps the operator-visible
    order ``Дата/время | Дата | Операция ...`` and extends AutoFilter without
    loading the full workbook through openpyxl.
    """
    if not xlsx_bytes.startswith(b"PK"):
        raise ValueError("Файл истории не является XLSX")
    source = io.BytesIO(xlsx_bytes)
    try:
        with zipfile.ZipFile(source, "r") as archive:
            infos = archive.infolist()
            files = {info.filename: archive.read(info.filename) for info in infos}
    except zipfile.BadZipFile as exc:
        raise ValueError("Файл истории повреждён") from exc
    styles_name = "xl/styles.xml"
    if styles_name not in files:
        raise ValueError("В XLSX не найдены стили")
    files[styles_name], date_style = _xlsx_add_date_style(files[styles_name])
    shared = _xlsx_shared_strings(files.get("xl/sharedStrings.xml"))
    changed = False
    sheet_pattern = re.compile(r"xl/worksheets/sheet\d+\.xml$")
    for name, xml_data in list(files.items()):
        if not sheet_pattern.fullmatch(name):
            continue
        root = ET.fromstring(xml_data)
        sheet_data = root.find(_X + "sheetData")
        if sheet_data is None:
            continue
        rows = sheet_data.findall(_X + "row")
        if not rows:
            continue
        header = rows[0]
        header_cells = header.findall(_X + "c")
        headers = {_xlsx_col_number(cell.get("r", "")): _xlsx_cell_text(cell, shared).strip() for cell in header_cells}
        if any(value.casefold() in {"дата", "date"} for value in headers.values()):
            # Existing r97/r100 output: leave it intact, but keep filter full-width.
            max_col = max(headers) if headers else 1
            max_row = max(int(row.get("r", "0") or 0) for row in rows)
            auto_filter = root.find(_X + "autoFilter")
            if auto_filter is not None:
                auto_filter.set("ref", f"A1:{_xlsx_col_letter(max_col)}{max_row}")
            files[name] = ET.tostring(root, encoding="utf-8", xml_declaration=True)
            continue
        source_col = next((col for col, value in headers.items() if ("дата" in value.casefold() and "врем" in value.casefold()) or "datetime" in value.casefold() or "created_at" in value.casefold()), None)
        if source_col is None:
            source_col = min(headers) if headers else 1
        insert_col = source_col + 1
        max_col_before = max(headers) if headers else 1
        max_row = max(int(row.get("r", "0") or 0) for row in rows)
        # Shift every existing cell at/after B one column right. Iterate from the
        # end to avoid accidental duplicate references while mutating attributes.
        for row in rows:
            for cell in reversed(row.findall(_X + "c")):
                ref = cell.get("r", "")
                col = _xlsx_col_number(ref)
                if col >= insert_col:
                    m = re.search(r"(\d+)$", ref)
                    if m:
                        cell.set("r", f"{_xlsx_col_letter(col + 1)}{m.group(1)}")
        target_letter = _xlsx_col_letter(insert_col)
        source_letter = _xlsx_col_letter(source_col)
        # Header style follows the Date/time header.
        source_header = next((cell for cell in header.findall(_X + "c") if cell.get("r") == f"{source_letter}1"), None)
        header_cell = ET.Element(_X + "c", {"r": f"{target_letter}1", "t": "inlineStr"})
        if source_header is not None and source_header.get("s"):
            header_cell.set("s", source_header.get("s"))
        inline = ET.SubElement(header_cell, _X + "is")
        ET.SubElement(inline, _X + "t").text = "Дата"
        # Insert in physical XML order directly after the source timestamp cell.
        hcells = list(header.findall(_X + "c"))
        idx = next((i for i,c in enumerate(hcells) if c.get("r") == f"{source_letter}1"), len(hcells)-1)
        header.insert(idx + 1, header_cell)
        for row in rows[1:]:
            row_number = int(row.get("r", "0") or 0)
            source_cell = next((cell for cell in row.findall(_X + "c") if cell.get("r") == f"{source_letter}{row_number}"), None)
            if source_cell is None:
                continue
            serial = _xlsx_date_serial(source_cell, shared)
            if serial is None:
                continue
            cell = ET.Element(_X + "c", {"r": f"{target_letter}{row_number}", "s": str(date_style), "t": "n"})
            ET.SubElement(cell, _X + "v").text = str(serial)
            cells = list(row.findall(_X + "c"))
            idx = next((i for i,c in enumerate(cells) if c.get("r") == f"{source_letter}{row_number}"), len(cells)-1)
            row.insert(idx + 1, cell)
        max_col = max_col_before + 1
        dimension = root.find(_X + "dimension")
        if dimension is not None:
            dimension.set("ref", f"A1:{_xlsx_col_letter(max_col)}{max_row}")
        auto_filter = root.find(_X + "autoFilter")
        if auto_filter is None:
            auto_filter = ET.Element(_X + "autoFilter", {"ref": f"A1:{_xlsx_col_letter(max_col)}{max_row}"})
            children = list(root)
            root.insert(children.index(sheet_data) + 1, auto_filter)
        else:
            auto_filter.set("ref", f"A1:{_xlsx_col_letter(max_col)}{max_row}")
        files[name] = ET.tostring(root, encoding="utf-8", xml_declaration=True)
        changed = True
    if not changed:
        return xlsx_bytes
    out = io.BytesIO()
    info_by_name = {info.filename: info for info in infos}
    with zipfile.ZipFile(out, "w") as archive:
        for name, data in files.items():
            archive.writestr(info_by_name[name], data)
    return out.getvalue()


_HISTORY_EXPORT_DIR = Path(os.getenv("FULFILLCORE_HISTORY_EXPORT_DIR", "/tmp/fulfillcore-history-exports"))
_HISTORY_EXPORT_CACHE_MAX_AGE_SECONDS = 24 * 60 * 60
_HISTORY_EXPORT_CLEANUP_SECONDS = 48 * 60 * 60
_HISTORY_EXPORT_STALE_SECONDS = 30 * 60
_HISTORY_EXPORT_LOCK = threading.Lock()


def _history_job_json(job_id: str) -> Path:
    return _HISTORY_EXPORT_DIR / f"{job_id}.json"


def _history_job_xlsx(job_id: str) -> Path:
    return _HISTORY_EXPORT_DIR / f"{job_id}.xlsx"


def _write_history_job(job_id: str, payload: dict):
    _HISTORY_EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    target = _history_job_json(job_id)
    tmp = target.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    tmp.replace(target)


def _read_history_job(job_id: str):
    path = _history_job_json(job_id)
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _cleanup_history_jobs(now=None):
    now = float(now or time.time())
    try:
        _HISTORY_EXPORT_DIR.mkdir(parents=True, exist_ok=True)
        for meta in _HISTORY_EXPORT_DIR.glob("*.json"):
            try:
                data = json.loads(meta.read_text(encoding="utf-8"))
                stamp = float(data.get("finished_at") or data.get("started_at") or data.get("created_at") or 0)
                if stamp and now - stamp > _HISTORY_EXPORT_CLEANUP_SECONDS:
                    job_id = meta.stem
                    meta.unlink(missing_ok=True)
                    _history_job_xlsx(job_id).unlink(missing_ok=True)
            except Exception:
                continue
    except Exception:
        pass


def _history_query_key(params):
    canonical = "&".join(f"{k}={v}" for k, v in sorted((str(k), str(v)) for k, v in params))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _history_source_watermark(params) -> str:
    """Fast cache watermark for one client/warehouse.

    Inventory operations are append-only in FulfillCore, so the newest operation is
    sufficient to invalidate their side of the cache. Reservation state changes always
    touch ``updated_at`` in the canonical reservation service. With the r122 composite
    indexes these lookups are index-top reads instead of COUNT/GREATEST scans over the
    entire warehouse history.
    """
    from app.core.database import SessionLocal
    q = {str(k): str(v) for k, v in params}
    client_id = q.get("client_id", "").strip()
    warehouse_id = q.get("warehouse_id", "").strip()
    if not client_id or not warehouse_id:
        return "missing-scope"
    db = SessionLocal()
    try:
        op = db.execute(text("""
            SELECT COALESCE(created_at::text,'') AS mx, COALESCE(id::text,'') AS last_id
            FROM inventory_operations
            WHERE client_id=CAST(:c AS uuid) AND warehouse_id=CAST(:w AS uuid)
            ORDER BY created_at DESC, id DESC
            LIMIT 1
        """), {"c": client_id, "w": warehouse_id}).mappings().first() or {"mx":"", "last_id":""}
        res = {"mx":"", "last_id":""}
        if db.execute(text("SELECT to_regclass('fc_stock_reservations') IS NOT NULL")).scalar():
            res = db.execute(text("""
                SELECT COALESCE(updated_at::text,'') AS mx, COALESCE(id,'') AS last_id
                FROM fc_stock_reservations
                WHERE client_id=:c AND warehouse_id=:w
                ORDER BY updated_at DESC, id DESC
                LIMIT 1
            """), {"c": client_id, "w": warehouse_id}).mappings().first() or {"mx":"", "last_id":""}
        raw = "|".join(str(x or "") for x in (op.get("mx"), op.get("last_id"), res.get("mx"), res.get("last_id")))
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()
    finally:
        try:
            if db.in_transaction():
                db.rollback()
        finally:
            db.close()


def _history_stream_filters(q, physical=True):
    """Return SQL WHERE fragments for the export stream without materializing history."""
    params = {
        "client_id": q.get("client_id", "").strip(),
        "warehouse_id": q.get("warehouse_id", "").strip(),
    }
    if physical:
        clauses = [
            "o.client_id=CAST(:client_id AS uuid)",
            "o.warehouse_id=CAST(:warehouse_id AS uuid)",
        ]
        if q.get("barcode"):
            clauses.append("s.barcode=:barcode"); params["barcode"] = q["barcode"].strip()
        if q.get("location_id"):
            clauses.append("(l.source_location_id=CAST(:location_id AS uuid) OR l.destination_location_id=CAST(:location_id AS uuid))"); params["location_id"] = q["location_id"].strip()
        if q.get("container_id"):
            clauses.append("(l.source_container_id=CAST(:container_id AS uuid) OR l.destination_container_id=CAST(:container_id AS uuid))"); params["container_id"] = q["container_id"].strip()
        if q.get("order_id"):
            clauses.append("COALESCE(o.reference,'') ILIKE :order_id"); params["order_id"] = f"%{q['order_id'].strip()}%"
        if q.get("operation_type"):
            clauses.append("o.operation_type=:operation_type"); params["operation_type"] = q["operation_type"].strip().upper()
    else:
        clauses = ["r.client_id=:client_id", "r.warehouse_id=:warehouse_id"]
        if q.get("barcode"):
            clauses.append("s.barcode=:barcode"); params["barcode"] = q["barcode"].strip()
        if q.get("location_id"):
            clauses.append("r.source_location_id=:location_id"); params["location_id"] = q["location_id"].strip()
        if q.get("container_id"):
            clauses.append("r.source_container_id=:container_id"); params["container_id"] = q["container_id"].strip()
        if q.get("order_id"):
            clauses.append("(r.order_id ILIKE :order_id OR COALESCE(r.external_order_id,'') ILIKE :order_id)"); params["order_id"] = f"%{q['order_id'].strip()}%"
    return clauses, params


def _history_physical_stream(db, q):
    clauses, params = _history_stream_filters(q, physical=True)
    sql = rf"""
      WITH rows AS (
        SELECT
          'STOCK'::text AS kind,
          CASE
            WHEN l.reason LIKE 'EXTERNAL_PICK_TASK_WRITE_OFF:%' THEN 'EXTERNAL_PICK_WRITE_OFF'
            WHEN o.operation_type='RECEIVING' AND (
              UPPER(COALESCE(o.idempotency_key,'')) LIKE 'PVZ:%' OR
              UPPER(COALESCE(l.reason,'')) LIKE 'PVZ_RECEIVING:%' OR
              UPPER(COALESCE(o.comment,'')) LIKE '%ПРИЁМКА ПВЗ%'
            ) THEN 'PVZ_RECEIVING'
            ELSE o.operation_type
          END AS event_type,
          CASE
            WHEN o.idempotency_key LIKE 'tsd-work-%'
             AND COALESCE(NULLIF(((tw.data->'lines')->(CAST(l.sku_id AS text)))->>'last_scanned_at',''), NULLIF(tw.data->>'last_item_at',''))
                 ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}'
            THEN COALESCE(NULLIF(((tw.data->'lines')->(CAST(l.sku_id AS text)))->>'last_scanned_at',''), NULLIF(tw.data->>'last_item_at',''))::timestamptz
            ELSE o.created_at
          END AS event_at,
          o.created_at AS server_created_at,
          CASE
            WHEN o.idempotency_key LIKE 'tsd-work-%' THEN NULLIF(COALESCE(((tw.data->'lines')->(CAST(l.sku_id AS text)))->>'first_scanned_at', tw.data->>'first_item_at'),'')
            ELSE NULL
          END AS tsd_first_scanned_at,
          CASE
            WHEN o.idempotency_key LIKE 'tsd-work-%' THEN NULLIF(COALESCE(((tw.data->'lines')->(CAST(l.sku_id AS text)))->>'last_scanned_at', tw.data->>'last_item_at'),'')
            ELSE NULL
          END AS tsd_last_scanned_at,
          o.id AS operation_id, l.id AS line_id, l.sku_id, s.sku_code, s.article, s.name, s.size, s.barcode,
          l.quantity,
          CASE WHEN l.destination_location_id IS NOT NULL AND l.source_location_id IS NULL THEN l.quantity
               WHEN l.source_location_id IS NOT NULL AND l.destination_location_id IS NULL THEN -l.quantity
               ELSE 0 END AS physical_delta,
          0::numeric AS reserve_delta,
          sl.code AS source_location, COALESCE(sc.name,sc.barcode) AS source_container,
          dl.code AS destination_location, COALESCE(dc.name,dc.barcode) AS destination_container,
          o.reference, NULL::text AS order_id, o.performed_by AS actor, o.comment, l.reason, o.status
        FROM inventory_operations o
        JOIN inventory_operation_lines l ON l.operation_id=o.id
        JOIN skus s ON s.id=l.sku_id
        LEFT JOIN locations sl ON sl.id=l.source_location_id
        LEFT JOIN locations dl ON dl.id=l.destination_location_id
        LEFT JOIN containers sc ON sc.id=l.source_container_id
        LEFT JOIN containers dc ON dc.id=l.destination_container_id
        LEFT JOIN fc_tsd_work tw ON tw.id=(CASE WHEN o.idempotency_key LIKE 'tsd-work-%' THEN substring(o.idempotency_key FROM 10) END)
          AND tw.client_id=CAST(o.client_id AS text) AND tw.warehouse_id=CAST(o.warehouse_id AS text)
        WHERE {' AND '.join(clauses)}
      )
      SELECT * FROM rows
      ORDER BY event_at DESC, operation_id DESC, line_id
    """
    return db.execute(text(sql), params, execution_options={"stream_results": True, "yield_per": 2000}).mappings()


def _history_reservation_stream(db, q):
    clauses, params = _history_stream_filters(q, physical=False)
    sql = f"""
      WITH rb AS (
        SELECT r.*, s.sku_code, s.article, s.name, s.size, s.barcode,
               l.code AS source_location, COALESCE(c.name,c.barcode) AS source_container
        FROM fc_stock_reservations r
        LEFT JOIN skus s ON CAST(s.id AS text)=r.sku_id
        LEFT JOIN locations l ON CAST(l.id AS text)=r.source_location_id
        LEFT JOIN containers c ON CAST(c.id AS text)=r.source_container_id
        WHERE {' AND '.join(clauses)}
      ), events AS (
        SELECT rb.*, ev.event_type, ev.event_at, ev.reserve_delta
        FROM rb
        CROSS JOIN LATERAL (VALUES
          ('RESERVE_CREATED'::text, rb.created_at, rb.quantity),
          ('RESERVE_BOUND'::text, CASE WHEN rb.source_location_id IS NOT NULL THEN rb.updated_at END, 0::numeric),
          ('RESERVE_CONSUMED'::text, rb.consumed_at, -rb.quantity),
          ('RESERVE_RELEASED'::text, rb.released_at, -rb.quantity),
          ('RETURN_TO_RECEIVING'::text, rb.receiving_at, -rb.quantity)
        ) AS ev(event_type,event_at,reserve_delta)
        WHERE ev.event_at IS NOT NULL
      )
      SELECT
        'RESERVE'::text AS kind, event_type, event_at, NULL::timestamptz AS server_created_at,
        NULL::text AS tsd_first_scanned_at, NULL::text AS tsd_last_scanned_at,
        NULL::uuid AS operation_id, NULL::uuid AS line_id, sku_id, sku_code, article, name, size, barcode,
        quantity, 0::numeric AS physical_delta, reserve_delta,
        source_location, source_container, NULL::text AS destination_location, NULL::text AS destination_container,
        external_order_id AS reference, order_id, 'SYSTEM'::text AS actor, note AS comment, reservation_mode AS reason, status
      FROM events
      ORDER BY event_at DESC, id DESC
    """
    return db.execute(text(sql), params, execution_options={"stream_results": True, "yield_per": 2000}).mappings()


def _merge_history_streams(physical, reserve):
    """Merge two already-descending DB cursors without holding the ledger in RAM."""
    p_it, r_it = iter(physical), iter(reserve)
    p = next(p_it, None); r = next(r_it, None)
    while p is not None or r is not None:
        if r is None or (p is not None and p.get("event_at") >= r.get("event_at")):
            yield p; p = next(p_it, None)
        else:
            yield r; r = next(r_it, None)

def _find_cached_history_job(query_key: str, source_watermark: str, now=None):
    now = float(now or time.time())
    try:
        for meta in _HISTORY_EXPORT_DIR.glob("*.json"):
            data = _read_history_job(meta.stem)
            if not data or data.get("query_key") != query_key:
                continue
            status = data.get("status")
            if status == "READY" and _history_job_xlsx(meta.stem).is_file():
                finished = float(data.get("finished_at") or 0)
                if data.get("source_watermark") == source_watermark and finished and now - finished <= _HISTORY_EXPORT_CACHE_MAX_AGE_SECONDS:
                    return data
            if status in {"QUEUED", "RUNNING", "PATCHING"}:
                started = float(data.get("started_at") or data.get("created_at") or 0)
                if data.get("source_watermark") == source_watermark and started and now - started <= _HISTORY_EXPORT_STALE_SECONDS:
                    return data
    except Exception:
        return None
    return None


def _history_export_worker(job_id: str, query_key: str, source_watermark: str, params, cookie: str):
    """Stream warehouse history from PostgreSQL straight into XLSX.

    r122 avoids the remaining expensive path from r118-r121: loading up to
    100k physical rows + reservation events into Python lists, sorting the
    combined list, and only then starting XLSX serialization. Two ordered DB
    cursors are merged on the fly, so memory stays nearly constant and the
    first workbook rows are written immediately.
    """
    from openpyxl.cell import WriteOnlyCell
    from app.core.database import SessionLocal
    from app.routers.audit import (
        _counts_in_physical_stock,
        _event_type_ru,
        _receipt_name,
        _status_ru,
    )

    started = time.time()
    base = {
        "job_id": job_id,
        "query_key": query_key,
        "source_watermark": source_watermark,
        "created_at": started,
        "started_at": started,
        "status": "RUNNING",
        "stage": "Открываем поток истории",
    }
    _write_history_job(job_id, base)
    final = _history_job_xlsx(job_id)
    tmp = final.with_suffix(".xlsx.tmp")
    physical_db = None
    reserve_db = None
    try:
        q = {str(k): str(v) for k, v in params}
        client_raw = q.get("client_id", "").strip()
        warehouse_raw = q.get("warehouse_id", "").strip()
        if not client_raw or not warehouse_raw:
            raise RuntimeError("Не выбран клиент или склад")

        wb = Workbook(write_only=True)
        ws = wb.create_sheet("Журнал склада")
        ws.freeze_panes = "A2"
        widths = {
            "A":27,"B":13,"C":24,"D":18,"E":28,"F":20,"G":16,"H":12,"I":36,
            "J":15,"K":15,"L":12,"M":24,"N":24,"O":25,"P":30,"Q":18,"R":38,"S":27,
        }
        for col, width in widths.items():
            ws.column_dimensions[col].width = width
        headers = [
            "Дата/время","Дата","Тип","Статус","Учитывается в физическом остатке",
            "ШК","Артикул","Размер","Товар","Физ. изменение","Резерв изменение","Кол-во",
            "Откуда","Куда","Заказ/основание","Название приёмки","Сотрудник","Комментарий",
            "Выгружено на сервер",
        ]
        header_cells = []
        for value in headers:
            cell = WriteOnlyCell(ws, value=value)
            cell.font = Font(bold=True)
            cell.fill = PatternFill("solid", fgColor="D9E1F2")
            header_cells.append(cell)
        ws.append(header_cells)

        physical_db = SessionLocal()
        physical = _history_physical_stream(physical_db, q)
        reserve_db = SessionLocal()
        has_reservations = bool(reserve_db.execute(text("SELECT to_regclass('fc_stock_reservations') IS NOT NULL")).scalar())
        if reserve_db.in_transaction():
            reserve_db.rollback()
        reserve = _history_reservation_stream(reserve_db, q) if has_reservations else iter(())

        _write_history_job(job_id, {**base, "status":"RUNNING", "stage":"Формируем Excel потоково", "row_count":0})
        row_count = 0

        def timestamp_text(value):
            if value in (None, ""):
                return ""
            if isinstance(value, datetime):
                return value.isoformat()
            return str(value)

        for raw in _merge_history_streams(physical, reserve):
            r = dict(raw)
            event_at = r.get("event_at")
            src = " · ".join(str(x) for x in [r.get("source_location"), r.get("source_container")] if x)
            dst = " · ".join(str(x) for x in [r.get("destination_location"), r.get("destination_container")] if x)
            event_date = event_at.date() if isinstance(event_at, datetime) else None
            values = [
                timestamp_text(event_at), event_date, _event_type_ru(r.get("event_type")), _status_ru(r.get("status")),
                "Да" if _counts_in_physical_stock(r) else "Нет", r.get("barcode"), r.get("article"), r.get("size"), r.get("name"),
                r.get("physical_delta"), r.get("reserve_delta"), r.get("quantity"), src, dst,
                r.get("reference") or r.get("order_id"), _receipt_name(r), r.get("actor"), r.get("comment") or r.get("reason"),
                timestamp_text(r.get("server_created_at")),
            ]
            # Plain strings/numbers are materially faster in write-only mode. Only the
            # real Excel date cell needs a custom number format.
            if event_date is not None:
                date_cell = WriteOnlyCell(ws, value=event_date)
                date_cell.number_format = "dd.mm.yyyy"
                values[1] = date_cell
            ws.append(values)
            row_count += 1
            if row_count % 5000 == 0:
                _write_history_job(job_id, {
                    **base, "status":"RUNNING",
                    "stage":f"Формируем Excel · {row_count:,} строк".replace(",", " "),
                    "row_count":row_count,
                })

        # Release DB cursors before ZIP compression starts, so a download cannot hold
        # long read transactions or compete with ordinary receiving/scanning.
        for db in (physical_db, reserve_db):
            if db is not None:
                try:
                    if db.in_transaction():
                        db.rollback()
                finally:
                    db.close()
        physical_db = reserve_db = None

        ws.auto_filter.ref = f"A1:S{row_count + 1}"
        _write_history_job(job_id, {
            **base, "status":"RUNNING", "stage":f"Сжимаем Excel · {row_count:,} строк".replace(",", " "), "row_count":row_count,
        })
        _HISTORY_EXPORT_DIR.mkdir(parents=True, exist_ok=True)
        from openpyxl.writer.excel import ExcelWriter
        from zipfile import ZipFile, ZIP_DEFLATED
        with ZipFile(tmp, "w", ZIP_DEFLATED, allowZip64=True, compresslevel=1) as archive:
            ExcelWriter(wb, archive).save()
        tmp.replace(final)
        finished = time.time()
        size = final.stat().st_size if final.is_file() else 0
        _write_history_job(job_id, {
            **base,
            "status": "READY",
            "stage": "Готово",
            "finished_at": finished,
            "size": size,
            "row_count": row_count,
            "duration_seconds": round(finished - started, 2),
        })
    except Exception as exc:
        for db in (physical_db, reserve_db):
            if db is not None:
                try:
                    if db.in_transaction():
                        db.rollback()
                except Exception:
                    pass
                try:
                    db.close()
                except Exception:
                    pass
        try:
            tmp.unlink(missing_ok=True)
        except Exception:
            pass
        _write_history_job(job_id, {
            **base,
            "status": "FAILED",
            "stage": "Ошибка",
            "finished_at": time.time(),
            "error": str(exc)[:1200],
        })


@router.post("/history/export-jobs")
def start_warehouse_history_export(request: Request):
    params = [(k, v) for k, v in request.query_params.multi_items() if k != "limit"]
    if not any(k == "client_id" and str(v).strip() for k, v in params):
        raise HTTPException(status_code=422, detail="Не выбран клиент")
    if not any(k == "warehouse_id" and str(v).strip() for k, v in params):
        raise HTTPException(status_code=422, detail="Не выбран склад")
    query_key = _history_query_key(params)
    source_watermark = _history_source_watermark(params)
    now = time.time()
    with _HISTORY_EXPORT_LOCK:
        _cleanup_history_jobs(now)
        cached = _find_cached_history_job(query_key, source_watermark, now)
        if cached:
            return {
                "job_id": cached["job_id"],
                "status": cached["status"],
                "stage": cached.get("stage"),
                "cached": cached["status"] == "READY",
                "source_watermark": source_watermark,
            }
        job_id = uuid.uuid4().hex
        queued = {
            "job_id": job_id,
            "query_key": query_key,
            "source_watermark": source_watermark,
            "created_at": now,
            "status": "QUEUED",
            "stage": "Поставлено в очередь",
        }
        _write_history_job(job_id, queued)
        worker = threading.Thread(
            target=_history_export_worker,
            args=(job_id, query_key, source_watermark, tuple(params), request.headers.get("cookie", "")),
            name=f"fc-history-export-{job_id[:8]}",
            daemon=True,
        )
        worker.start()
    return {"job_id": job_id, "status": "QUEUED", "stage": "Поставлено в очередь", "cached": False}


@router.get("/history/export-jobs/{job_id}")
def warehouse_history_export_status(job_id: str):
    if not re.fullmatch(r"[0-9a-f]{32}", str(job_id or "")):
        raise HTTPException(status_code=404, detail="Задание выгрузки не найдено")
    data = _read_history_job(job_id)
    if not data:
        raise HTTPException(status_code=404, detail="Задание выгрузки не найдено")
    if data.get("status") in {"QUEUED", "RUNNING", "PATCHING"}:
        started = float(data.get("started_at") or data.get("created_at") or 0)
        if started and time.time() - started > _HISTORY_EXPORT_STALE_SECONDS:
            data = {**data, "status": "FAILED", "stage": "Ошибка", "error": "Формирование истории прервалось. Запустите выгрузку ещё раз."}
            _write_history_job(job_id, data)
    return {k: v for k, v in data.items() if k not in {"query_key"}}


@router.get("/history/export-jobs/{job_id}/download")
def download_warehouse_history_export(job_id: str):
    data = _read_history_job(job_id)
    path = _history_job_xlsx(job_id)
    if not data or data.get("status") != "READY" or not path.is_file():
        raise HTTPException(status_code=409, detail="Excel истории ещё не готов")
    return FileResponse(
        path,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename="warehouse_history.xlsx",
    )


@router.post("/history/export-date.xlsx")
def warehouse_history_with_date(file: UploadFile = File(...)):
    """Backward-compatible two-step endpoint. New UI uses background export-jobs."""
    raw = file.file.read(100 * 1024 * 1024 + 1)
    if len(raw) > 100 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="Excel истории больше 100 МБ")
    try:
        data = add_separate_date_column(raw)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return Response(
        data,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": 'attachment; filename="warehouse_history_with_date.xlsx"'},
    )


@router.get("/history/export-fast.xlsx")
async def warehouse_history_fast(request: Request):
    """One browser request: build the existing audit workbook server-side, then append Date.

    This removes the old browser download -> upload round trip and uses the lighter
    append-only date pass above. The canonical audit query/export stays unchanged.
    """
    headers = {
        "accept": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "cookie": request.headers.get("cookie", ""),
    }
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(20 * 60.0, connect=5.0)) as client:
            upstream = await client.get(
                "http://127.0.0.1:8000/audit/export.xlsx",
                params=list(request.query_params.multi_items()),
                headers=headers,
            )
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"Не удалось сформировать исходную историю: {exc}")
    if upstream.status_code != 200:
        detail = upstream.text[:500] if upstream.text else f"HTTP {upstream.status_code}"
        raise HTTPException(status_code=upstream.status_code, detail=detail)
    if len(upstream.content) > 100 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="Excel истории больше 100 МБ")
    try:
        data = await run_in_threadpool(add_separate_date_column_fast_xml, upstream.content)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return Response(
        data,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": 'attachment; filename="warehouse_history.xlsx"'},
    )
