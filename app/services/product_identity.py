from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import Text, cast, select
from sqlalchemy.orm import Session

from app.models import MarketplaceProduct, SKU


def normalize_product_barcode(value: str | None) -> tuple[str, str | None]:
    """Normalize a scanner value and detect the common double-scan failure.

    Some USB scanners can write the same EAN twice before the browser handles Enter.
    Only an exact repeated numeric half with a normal retail-barcode length is folded,
    so arbitrary GS1/marking-code payloads are never shortened here.
    """

    raw = "".join(str(value or "").strip().split())
    if raw.isdigit() and len(raw) % 2 == 0:
        half = len(raw) // 2
        if 8 <= half <= 14 and raw[:half] == raw[half:]:
            return raw[:half], raw
    return raw, None


def _product_contains_barcode(product: MarketplaceProduct, barcode: str) -> bool:
    if barcode in {str(value).strip() for value in (product.barcodes or []) if value is not None}:
        return True
    for row in product.sizes or []:
        if not isinstance(row, dict):
            continue
        if barcode in {str(value).strip() for value in (row.get("skus") or []) if value is not None}:
            return True
    return False


def _product_size(product: MarketplaceProduct, barcode: str) -> str | None:
    for row in product.sizes or []:
        if not isinstance(row, dict):
            continue
        codes = {str(value).strip() for value in (row.get("skus") or []) if value is not None}
        if barcode in codes:
            value = str(row.get("techSize") or row.get("wbSize") or "").strip()
            return value or None
    return None


def _product_chrt_id(product: MarketplaceProduct, barcode: str) -> int | None:
    """Return WB characteristic/size id for the exact barcode.

    Since 2026 WB seller-stock endpoints are chrtId-first. A barcode can still be
    used to identify the local size, but publishing/reading FBS stock must address
    the concrete size id rather than rely on the deprecated ``skus`` request field.
    """
    for row in product.sizes or []:
        if not isinstance(row, dict):
            continue
        codes = {str(value).strip() for value in (row.get("skus") or []) if value is not None}
        if barcode not in codes:
            continue
        raw = row.get("chrtID") or row.get("chrtId") or row.get("chrt_id")
        try:
            return int(raw) if raw is not None else None
        except (TypeError, ValueError):
            return None
    return None


def wb_product_identity(db: Session, client_id: uuid.UUID, barcode: str | None) -> dict[str, Any] | None:
    """Return the exact WB card/size for a product barcode from the local cache."""

    normalized, duplicated_from = normalize_product_barcode(barcode)
    if not normalized:
        return None
    # JSONB containment differs between deployed SQLAlchemy/PostgreSQL versions.
    # A narrowed text search is portable; exact membership is verified in Python.
    candidates = db.execute(
        select(MarketplaceProduct)
        .where(
            MarketplaceProduct.client_id == client_id,
            MarketplaceProduct.provider == "WB",
            cast(MarketplaceProduct.barcodes, Text).contains(normalized),
        )
        .order_by(MarketplaceProduct.updated_at.desc(), MarketplaceProduct.created_at.desc())
        .limit(50)
    ).scalars().all()
    product = next((row for row in candidates if _product_contains_barcode(row, normalized)), None)
    if product is None:
        return None
    return {
        "product_id": str(product.id),
        "nm_id": product.external_product_id,
        "barcode": normalized,
        "duplicated_from": duplicated_from,
        "article": str(product.vendor_code or "").strip() or None,
        "name": str(product.title or "").strip() or None,
        "size": _product_size(product, normalized),
        "chrt_id": _product_chrt_id(product, normalized),
        "brand": str(product.brand or "").strip() or None,
    }


def wb_product_identity_map(
    db: Session, client_id: uuid.UUID, barcodes: set[str] | list[str] | tuple[str, ...]
) -> dict[str, dict[str, Any]]:
    """Resolve many WB barcodes with one product-cache query.

    Bulk receiving can contain thousands of rows but usually only hundreds/thousands of
    unique barcodes. Calling ``wb_product_identity`` for every barcode creates one SQL
    round-trip per SKU. Loading the client's cached WB cards once and matching exact
    barcode membership in Python preserves the same identity rules with predictable cost.
    """
    requested: set[str] = set()
    duplicated_from: dict[str, str | None] = {}
    for value in barcodes or []:
        normalized, duplicated = normalize_product_barcode(value)
        if normalized:
            requested.add(normalized)
            duplicated_from[normalized] = duplicated
    if not requested:
        return {}

    products = db.execute(
        select(MarketplaceProduct)
        .where(
            MarketplaceProduct.client_id == client_id,
            MarketplaceProduct.provider == "WB",
        )
        .order_by(MarketplaceProduct.updated_at.desc(), MarketplaceProduct.created_at.desc())
    ).scalars().all()

    result: dict[str, dict[str, Any]] = {}
    remaining = set(requested)
    for product in products:
        if not remaining:
            break
        codes = {str(value).strip() for value in (product.barcodes or []) if value is not None}
        for row in product.sizes or []:
            if isinstance(row, dict):
                codes.update(str(value).strip() for value in (row.get("skus") or []) if value is not None)
        for barcode in tuple(remaining.intersection(codes)):
            result[barcode] = {
                "product_id": str(product.id),
                "nm_id": product.external_product_id,
                "barcode": barcode,
                "duplicated_from": duplicated_from.get(barcode),
                "article": str(product.vendor_code or "").strip() or None,
                "name": str(product.title or "").strip() or None,
                "size": _product_size(product, barcode),
                "chrt_id": _product_chrt_id(product, barcode),
                "brand": str(product.brand or "").strip() or None,
            }
            remaining.discard(barcode)
    return result


def resolved_sku_identity(db: Session, client_id: uuid.UUID, sku: SKU) -> dict[str, Any]:
    """Prefer exact WB-card fields, with the local SKU as a complete fallback."""

    normalized, duplicated_from = normalize_product_barcode(sku.barcode)
    product = wb_product_identity(db, client_id, normalized) if normalized else None
    return {
        "sku_id": str(sku.id),
        "sku_code": sku.sku_code,
        "raw_barcode": str(sku.barcode or "").strip() or None,
        "barcode": normalized or str(sku.barcode or "").strip() or None,
        "barcode_was_duplicated": bool(duplicated_from),
        "article": (product or {}).get("article") or sku.article or sku.sku_code,
        "name": (product or {}).get("name") or sku.name or sku.article or sku.sku_code,
        "size": (product or {}).get("size") or sku.size,
        "brand": (product or {}).get("brand"),
        "product_id": (product or {}).get("product_id"),
        "nm_id": (product or {}).get("nm_id"),
    }
