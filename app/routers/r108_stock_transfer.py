"""r108: safe bulk MAIN <-> FBS classification transfer.

This moves only classification in fc_stocks_v2. Physical inventory never changes.
MAIN -> FBS uses only MAIN free quantity (active warehouse-pick holds stay MAIN).
FBS -> MAIN uses only fbs_available (active order reservations stay reserved/FBS).
The action can target selected product barcodes or one location subtree (rack/shelf/cell).
"""
from __future__ import annotations

import hashlib
import json
import uuid
from decimal import Decimal
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import bindparam, select, text
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models import Client, Location, SKU, Warehouse
from app.services import stock_core_v2 as stock
from app.services.receipt_fbs import queue_publication
from app.services.warehouse_availability import available_buckets, lock_scope

router = APIRouter(prefix="/warehouse-ui/class-transfer", tags=["Остатки MAIN/FBS"])


class TransferSelector(BaseModel):
    client_id: uuid.UUID
    warehouse_id: uuid.UUID
    source: Literal["MAIN", "FBS"]
    target: Literal["MAIN", "FBS"]
    barcodes: list[str] = Field(default_factory=list, max_length=10000)
    location_query: str | None = Field(default=None, max_length=160)
    include_descendants: bool = True


class TransferApply(TransferSelector):
    request_id: uuid.UUID
    fingerprint: str = Field(min_length=16, max_length=128)
    performed_by: str | None = Field(default=None, max_length=200)
    reason: str = Field(default="Перевод между Основным складом и WB FBS", max_length=500)


def _scope(db: Session, client_id: uuid.UUID, warehouse_id: uuid.UUID) -> None:
    if not db.get(Client, client_id):
        raise HTTPException(404, detail="Клиент не найден")
    if not db.get(Warehouse, warehouse_id):
        raise HTTPException(404, detail="Склад не найден")


def _ensure_event_table(db: Session) -> None:
    db.execute(text("""
        CREATE TABLE IF NOT EXISTS fc_stock_class_transfer_events(
            id varchar(36) PRIMARY KEY,
            client_id varchar(36) NOT NULL,
            warehouse_id varchar(36) NOT NULL,
            request_hash varchar(64) NOT NULL,
            fingerprint varchar(128) NOT NULL,
            source varchar(8) NOT NULL,
            target varchar(8) NOT NULL,
            selector_json jsonb NOT NULL,
            result_json jsonb NOT NULL,
            actor varchar(200),
            reason varchar(500),
            created_at timestamptz NOT NULL DEFAULT now()
        )
    """))
    db.execute(text("""
        CREATE INDEX IF NOT EXISTS ix_fc_stock_class_transfer_scope
        ON fc_stock_class_transfer_events(client_id,warehouse_id,created_at DESC)
    """))


def _clean_barcodes(values: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for raw in values or []:
        code = str(raw or "").strip()
        if not code or code in seen:
            continue
        seen.add(code)
        out.append(code)
    return out


def _location_scope(db: Session, warehouse_id: uuid.UUID, raw: str | None, descendants: bool) -> tuple[dict[str, Any] | None, set[str] | None]:
    query = str(raw or "").strip()
    if not query:
        return None, None
    rows = db.execute(text("""
        SELECT CAST(id AS varchar) id,CAST(parent_id AS varchar) parent_id,code,barcode,name,location_type
        FROM locations
        WHERE CAST(warehouse_id AS varchar)=:w AND is_active=TRUE
          AND (lower(code)=lower(:q) OR lower(COALESCE(barcode,''))=lower(:q) OR lower(COALESCE(name,''))=lower(:q))
        ORDER BY CASE WHEN lower(code)=lower(:q) THEN 0 WHEN lower(COALESCE(barcode,''))=lower(:q) THEN 1 ELSE 2 END,id
        LIMIT 3
    """), {"w": str(warehouse_id), "q": query}).mappings().all()
    if not rows:
        raise HTTPException(404, detail=f"Адрес «{query}» не найден. Введите точный код/QR стеллажа, полки, палеты или ячейки")
    if len(rows) > 1 and str(rows[0]["code"]).casefold() != query.casefold() and str(rows[0].get("barcode") or "").casefold() != query.casefold():
        raise HTTPException(409, detail=f"Адрес «{query}» неоднозначен. Введите точный код или QR")
    root = dict(rows[0])
    if not descendants:
        return root, {str(root["id"])}
    ids = db.execute(text("""
        WITH RECURSIVE tree AS (
            SELECT id,parent_id FROM locations WHERE CAST(id AS varchar)=:root AND CAST(warehouse_id AS varchar)=:w AND is_active=TRUE
            UNION ALL
            SELECT l.id,l.parent_id FROM locations l JOIN tree t ON l.parent_id=t.id
            WHERE CAST(l.warehouse_id AS varchar)=:w AND l.is_active=TRUE
        )
        SELECT CAST(id AS varchar) FROM tree
    """), {"root": root["id"], "w": str(warehouse_id)}).scalars().all()
    return root, {str(x) for x in ids}


def _fbs_rows(db: Session, client_id: uuid.UUID, warehouse_id: uuid.UUID) -> list[dict[str, Any]]:
    rows = db.execute(text("""
        SELECT CAST(sv.sku_id AS varchar) sku_id,CAST(sv.location_id AS varchar) location_id,
               NULLIF(sv.container_key,'') container_id,COALESCE(s.barcode,'') barcode,
               COALESCE(s.article,'') article,COALESCE(s.size,'') size,
               COALESCE(l.code,'') location_code,COALESCE(c.name,c.barcode,'') container_name,
               GREATEST(COALESCE(sv.fbs_available,0),0) quantity
        FROM fc_stocks_v2 sv
        JOIN skus s ON CAST(s.id AS varchar)=CAST(sv.sku_id AS varchar)
        JOIN locations l ON CAST(l.id AS varchar)=CAST(sv.location_id AS varchar)
        LEFT JOIN containers c ON CAST(c.id AS varchar)=NULLIF(sv.container_key,'')
        WHERE sv.client_id=:c AND sv.warehouse_id=:w AND COALESCE(sv.fbs_available,0)>0
          AND s.is_active=TRUE AND l.is_active=TRUE
          AND (sv.container_key='' OR (c.status='ACTIVE' AND c.location_id=l.id))
        ORDER BY s.article,s.size,s.barcode,l.code,c.name
    """), {"c": str(client_id), "w": str(warehouse_id)}).mappings().all()
    return [dict(x) for x in rows]


def _collect(db: Session, payload: TransferSelector) -> dict[str, Any]:
    if payload.source == payload.target:
        raise HTTPException(422, detail="Источник и назначение должны отличаться")
    codes = _clean_barcodes(payload.barcodes)
    location, location_ids = _location_scope(db, payload.warehouse_id, payload.location_query, payload.include_descendants)
    if not codes and not location_ids:
        raise HTTPException(422, detail="Выберите товары или укажите стеллаж / полку / ячейку")
    code_set = set(codes)
    if payload.source == "MAIN":
        raw = []
        for b in available_buckets(db, payload.client_id, payload.warehouse_id):
            if code_set and str(b.get("barcode") or "") not in code_set:
                continue
            if location_ids is not None and str(b.get("location_id") or "") not in location_ids:
                continue
            q = int(b.get("free") or 0)
            if q <= 0:
                continue
            raw.append({
                "sku_id": str(b["sku_id"]), "barcode": str(b.get("barcode") or ""),
                "article": str(b.get("article") or ""), "size": str(b.get("size") or ""),
                "location_id": str(b["location_id"]), "location": str(b.get("location_code") or ""),
                "container_id": str(b.get("container_id") or "") or None,
                "container": str(b.get("container_name") or ""), "quantity": q,
                "protected": int(b.get("held") or 0),
            })
    else:
        raw = []
        for b in _fbs_rows(db, payload.client_id, payload.warehouse_id):
            if code_set and str(b.get("barcode") or "") not in code_set:
                continue
            if location_ids is not None and str(b.get("location_id") or "") not in location_ids:
                continue
            q = int(Decimal(str(b.get("quantity") or 0)))
            if q <= 0:
                continue
            raw.append({
                "sku_id": str(b["sku_id"]), "barcode": str(b.get("barcode") or ""),
                "article": str(b.get("article") or ""), "size": str(b.get("size") or ""),
                "location_id": str(b["location_id"]), "location": str(b.get("location_code") or ""),
                "container_id": str(b.get("container_id") or "") or None,
                "container": str(b.get("container_name") or ""), "quantity": q,
                "protected": 0,
            })
    if not raw:
        label = "Основном складе" if payload.source == "MAIN" else "WB FBS"
        raise HTTPException(409, detail=f"В выбранном составе нет свободного товара на {label}. Резервы и активная сборка не переводятся")
    lines = sorted(raw, key=lambda x: (x["barcode"], x["sku_id"], x["location_id"], x.get("container_id") or ""))
    stable = {
        "source": payload.source, "target": payload.target,
        "barcodes": sorted(code_set), "location_id": location.get("id") if location else None,
        "lines": [{k: x[k] for k in ("sku_id", "location_id", "container_id", "quantity")} for x in lines],
    }
    fingerprint = hashlib.sha256(json.dumps(stable, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
    positions = len({(x["barcode"], x["sku_id"]) for x in lines})
    total = sum(int(x["quantity"]) for x in lines)
    protected = sum(int(x.get("protected") or 0) for x in lines)
    return {
        "fingerprint": fingerprint, "source": payload.source, "target": payload.target,
        "barcodes": codes, "location": location, "lines": lines,
        "positions": positions, "places": len(lines), "quantity": total,
        "protected_quantity": protected,
    }


def _request_hash(payload: TransferApply) -> str:
    data = payload.model_dump(mode="json") if hasattr(payload, "model_dump") else payload.dict()
    return hashlib.sha256(json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


@router.post("/preview")
def preview(payload: TransferSelector, db: Session = Depends(get_db)):
    _scope(db, payload.client_id, payload.warehouse_id)
    data = _collect(db, payload)
    # Keep preview compact: browser only needs a small sample for confirmation.
    sample = data["lines"][:12]
    return {k: v for k, v in data.items() if k != "lines"} | {"sample": sample, "truncated": len(data["lines"]) > len(sample)}


@router.post("/apply")
def apply_transfer(payload: TransferApply, request: Request, db: Session = Depends(get_db)):
    _scope(db, payload.client_id, payload.warehouse_id)
    _ensure_event_table(db)
    rid = str(payload.request_id)
    request_hash = _request_hash(payload)
    previous = db.execute(text("SELECT * FROM fc_stock_class_transfer_events WHERE id=:id FOR UPDATE"), {"id": rid}).mappings().first()
    if previous:
        if previous["client_id"] != str(payload.client_id) or previous["warehouse_id"] != str(payload.warehouse_id) or previous["request_hash"] != request_hash:
            raise HTTPException(409, detail="Этот request_id уже использован с другим переводом")
        return {**dict(previous["result_json"]), "replayed": True}

    # Same warehouse-level lock as pick task creation: MAIN free quantity cannot become held mid-transfer.
    lock_scope(db, payload.client_id, payload.warehouse_id)
    initial = _collect(db, payload)
    sku_ids = sorted({str(x["sku_id"]) for x in initial["lines"]})
    stock.locks(db, str(payload.client_id), str(payload.warehouse_id), sku_ids)
    current = _collect(db, payload)
    if current["fingerprint"] != payload.fingerprint:
        raise HTTPException(409, detail="Остаток, резерв или состав адреса изменился. Обновите остатки и повторите перевод")

    source_bucket = "STORAGE_ONLY" if payload.source == "MAIN" else "FBS_AVAILABLE"
    target_bucket = "FBS_AVAILABLE" if payload.target == "FBS" else "STORAGE_ONLY"
    for line in current["lines"]:
        stock.move_bucket(
            db,
            client_id=payload.client_id,
            warehouse_id=payload.warehouse_id,
            sku_id=line["sku_id"],
            location_id=line["location_id"],
            container_id=line.get("container_id"),
            source=source_bucket,
            target=target_bucket,
            quantity=Decimal(int(line["quantity"])),
        )

    # Recalculate WB targets asynchronously. No network I/O is done in this request.
    publication = queue_publication(db, payload.client_id, payload.warehouse_id, sku_ids, None)
    actor = str(payload.performed_by or getattr(request.state, "username", "") or "Администратор")[:200]
    result = {
        "ok": True, "source": payload.source, "target": payload.target,
        "quantity": current["quantity"], "positions": current["positions"], "places": current["places"],
        "location": current["location"], "publication": publication, "publication_queued": sum(1 for x in publication if str(x.get("status") or "") in {"PENDING", "PAUSED"}), "replayed": False,
    }
    selector = {
        "barcodes": current["barcodes"], "location": current["location"],
        "include_descendants": payload.include_descendants,
    }
    db.execute(text("""
        INSERT INTO fc_stock_class_transfer_events(
            id,client_id,warehouse_id,request_hash,fingerprint,source,target,selector_json,result_json,actor,reason
        ) VALUES(:id,:c,:w,:hash,:fp,:source,:target,CAST(:selector AS jsonb),CAST(:result AS jsonb),:actor,:reason)
    """), {
        "id": rid, "c": str(payload.client_id), "w": str(payload.warehouse_id),
        "hash": request_hash, "fp": payload.fingerprint, "source": payload.source, "target": payload.target,
        "selector": json.dumps(selector, ensure_ascii=False), "result": json.dumps(result, ensure_ascii=False),
        "actor": actor, "reason": str(payload.reason or "")[:500],
    })
    db.commit()
    try:
        from app.routers.receiving import _wake_wb_stock_outbox
        _wake_wb_stock_outbox()
    except Exception:
        pass
    return result
