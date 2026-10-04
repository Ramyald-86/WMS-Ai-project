"""r145f43: ultra-fast PVZ local product lookup and compact draft scan.

The normal Product Base/catalog endpoints enrich cards and are intentionally broader.
Scanner receiving only needs exact local identity, so this router uses direct indexed SKU
queries and returns a compact delta after a scan instead of re-reading the whole receipt.
"""
from __future__ import annotations

import time
import uuid
from datetime import datetime, timezone, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models import SKU
from app.routers.warehouse_ui import scope
from app.routers.r142_pvz_batch import (
    SessionScan,
    _assert_worker,
    _ensure_live_storage_box,
    _ensure_schema,
    _get_or_create_session_box,
    _session,
)
from app.services.local_catalog_rules import barcode as normalize_product_barcode


router = APIRouter(prefix="/pvz", tags=["ПВЗ · быстрый локальный скан"])
MARKER = "FC_R145F43_PVZ_INSTANT_LOCAL_RECEIVING"


def _normalize(value: str) -> str:
    try:
        return normalize_product_barcode(str(value or "").strip())
    except ValueError as exc:
        raise HTTPException(422, detail=str(exc))


def _rows_for_barcode(db: Session, client_id: uuid.UUID, barcode: str):
    return db.execute(
        select(SKU).where(SKU.client_id == client_id, SKU.barcode == barcode).limit(3)
    ).scalars().all()


def _serialize_product(sku: SKU) -> dict:
    return {
        "found": True,
        "id": str(sku.id),
        "sku_id": str(sku.id),
        "sku_code": str(sku.sku_code or ""),
        "barcode": str(sku.barcode or ""),
        "article": str(sku.article or ""),
        "size": str(sku.size or ""),
        "name": str(sku.name or ""),
        "source": "LOCAL_SKU_INDEX",
        "wb_api_called": False,
    }


def _fast_product(db: Session, payload: SessionScan) -> tuple[SKU, bool]:
    barcode = _normalize(payload.barcode)
    rows = _rows_for_barcode(db, payload.client_id, barcode)
    if len(rows) > 1:
        raise HTTPException(409, detail="Этот ШК привязан к нескольким SKU. Исправьте Базу товаров")
    if rows:
        sku = rows[0]
        changed = False
        article = str(payload.article or "").strip()
        size = str(payload.size or "").strip()
        name = str(payload.name or "").strip()
        if article and not str(sku.article or "").strip():
            sku.article = article; changed = True
        if size and not str(sku.size or "").strip():
            sku.size = size; changed = True
        if name and not str(sku.name or "").strip():
            sku.name = name; changed = True
        if not bool(sku.is_active):
            sku.is_active = True; changed = True
        if changed:
            db.flush()
        return sku, False

    article = str(payload.article or "").strip()
    size = str(payload.size or "").strip()
    name = str(payload.name or article).strip()
    if not article or not size:
        raise HTTPException(
            422,
            detail="ШК не найден в Базе товаров. Укажите артикул и размер вручную (для безразмерного — «Без размера»).",
        )
    sku = SKU(
        id=uuid.uuid4(),
        client_id=payload.client_id,
        sku_code="LOCAL-" + uuid.uuid5(payload.client_id, barcode).hex,
        barcode=barcode,
        article=article,
        size=size,
        name=name or article,
        unit="PCS",
        is_active=True,
    )
    db.add(sku)
    db.flush()
    return sku, True


@router.get("/product-lookup-fast")
def product_lookup_fast(
    client_id: uuid.UUID,
    barcode_value: str = Query(min_length=1, max_length=120),
    db: Session = Depends(get_db),
):
    started = time.perf_counter()
    barcode = _normalize(barcode_value)
    rows = _rows_for_barcode(db, client_id, barcode)
    if len(rows) > 1:
        raise HTTPException(409, detail="Этот ШК привязан к нескольким SKU. Исправьте Базу товаров")
    elapsed_ms = int(round((time.perf_counter() - started) * 1000))
    if not rows:
        return {
            "found": False,
            "barcode": barcode,
            "source": "LOCAL_SKU_INDEX",
            "elapsed_ms": elapsed_ms,
            "wb_api_called": False,
        }
    out = _serialize_product(rows[0])
    out["elapsed_ms"] = elapsed_ms
    return out


@router.post("/receiving-sessions/{session_id}/scan-fast")
def scan_fast(session_id: uuid.UUID, payload: SessionScan, db: Session = Depends(get_db)):
    started = time.perf_counter()
    scope(db, payload.client_id, payload.warehouse_id)
    _ensure_schema(db)
    session = _session(db, session_id, payload.client_id, payload.warehouse_id, lock=True)
    if session["status"] != "DRAFT":
        raise HTTPException(409, detail="Эта приёмка уже завершена")
    worker = _assert_worker(session, payload.performed_by)

    sku, product_created = _fast_product(db, payload)
    clean_container = str(payload.container_code or "").strip()
    live_box = None
    container_created = False
    if clean_container:
        live_box, container_created = _ensure_live_storage_box(
            db, payload.client_id, payload.warehouse_id, clean_container, payload.location_code
        )
    box = _get_or_create_session_box(db, session, clean_container, payload.location_code) if clean_container else None

    base_time = datetime.now(timezone.utc)
    added = []
    qty = int(payload.quantity)
    for i in range(qty):
        scan_id = uuid.uuid4()
        scanned_at = base_time + timedelta(microseconds=i)
        db.execute(text('''INSERT INTO fc_pvz_receiving_scans(
            id,session_id,box_id,sku_id,barcode,article,size,name,location_code,container_code,comment,source,performed_by,scanned_at)
            VALUES(:id,:s,:box,:sku,:bc,:a,:z,:n,:loc,:cc,:comment,:source,:p,:at)'''), {
            "id": str(scan_id), "s": str(session_id), "box": str(box["id"]) if box else None,
            "sku": str(sku.id), "bc": str(sku.barcode or ""), "a": str(sku.article or ""),
            "z": str(sku.size or ""), "n": str(sku.name or ""),
            "loc": str(payload.location_code or "ПРИЕМКА").strip() or "ПРИЕМКА",
            "cc": str(box["container_code"]) if box else "", "comment": payload.comment.strip(),
            "source": payload.source.strip().upper() or "SCAN", "p": worker, "at": scanned_at,
        })
        added.append({
            "id": str(scan_id), "session_id": str(session_id), "box_id": str(box["id"]) if box else None,
            "sku_id": str(sku.id), "barcode": str(sku.barcode or ""), "article": str(sku.article or ""),
            "size": str(sku.size or ""), "name": str(sku.name or ""),
            "location_code": str(payload.location_code or "ПРИЕМКА").strip() or "ПРИЕМКА",
            "container_code": str(box["container_code"]) if box else "",
            "container_name": str((box.get("container_name") or box.get("container_code")) if box else ""),
            "comment": payload.comment.strip(), "source": payload.source.strip().upper() or "SCAN",
            "performed_by": worker, "scanned_at": scanned_at,
            "box_sequence": int(box["sequence"]) if box else None,
        })

    stat = db.execute(text('''SELECT
      (SELECT COUNT(*)::int FROM fc_pvz_receiving_scans WHERE session_id=:s) AS units,
      (SELECT COUNT(*)::int FROM fc_pvz_receiving_scans WHERE session_id=:s AND box_id IS NULL) AS cell_units,
      (SELECT COUNT(*)::int FROM fc_pvz_receiving_boxes WHERE session_id=:s) AS box_count,
      (SELECT COUNT(*)::int FROM fc_pvz_receiving_scans WHERE box_id=:b) AS box_units'''), {
        "s": str(session_id), "b": str(box["id"]) if box else str(uuid.UUID(int=0)),
    }).mappings().one()
    db.commit()

    box_out = None
    if box:
        box_out = {
            "id": str(box["id"]), "session_id": str(session_id), "sequence": int(box["sequence"]),
            "container_code": str(box.get("container_code") or ""),
            "container_name": str(box.get("container_name") or box.get("container_code") or ""),
            "location_code": str(box.get("location_code") or payload.location_code or "ПРИЕМКА"),
            "units": int(stat.get("box_units") or 0),
        }
    live_out = None
    if live_box is not None:
        live_out = {
            "id": str(live_box.id), "name": str(live_box.name or ""), "barcode": str(live_box.barcode or ""),
            "location_id": str(live_box.location_id) if live_box.location_id else None,
            "container_type": str(live_box.container_type or ""), "status": str(live_box.status or "ACTIVE"),
        }
    elapsed_ms = int(round((time.perf_counter() - started) * 1000))
    return {
        "ok": True,
        "added": len(added),
        "scans": added,
        "scanned_at": base_time,
        "barcode": str(sku.barcode or ""),
        "product": _serialize_product(sku),
        "product_created": product_created,
        "box": box_out,
        "box_sequence": int(box["sequence"]) if box else None,
        "container": (box.get("container_name") or box.get("container_code")) if box else None,
        "container_created": container_created,
        "live_container": live_out,
        "storage_kind": "BOX" if box else "LOCATION",
        "location_code": str(payload.location_code or "ПРИЕМКА").strip() or "ПРИЕМКА",
        "units": int(stat.get("units") or 0),
        "cell_units": int(stat.get("cell_units") or 0),
        "box_count": int(stat.get("box_count") or 0),
        "elapsed_ms": elapsed_ms,
        "compact": True,
        "marker": MARKER,
    }
