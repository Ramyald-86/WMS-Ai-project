"""r145f46: resilient zero-wait manual receiving recovery path.

Design goals:
- local IndexedDB queue is always usable/renderable even if the API has a transient error;
- opening/resolving a worker draft is a tiny receipt-table query, not a normalize/snapshot job;
- pending local scans from the latest local receipt group can be safely rebound to a fresh DRAFT;
- actual persistence reuses the proven r145f41 exact-receipt batch logic and cross-receipt request_id dedupe;
- no stock movement occurs here; stock still moves only in the existing final exact commit.
"""
from __future__ import annotations

import time
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.routers import r145f41_receiving as r41
from app.services.r145f25_places import scope

router = APIRouter(tags=["r145f46"])


def _s(v, limit=512):
    return str(v or "").strip()[:limit]


def _fold(v):
    return _s(v).casefold()


class Ensure46(BaseModel):
    client_id: uuid.UUID
    warehouse_id: uuid.UUID
    worker: str = Field(min_length=1, max_length=255)
    reference: str | None = Field(default=None, max_length=255)
    preferred_receipt_id: uuid.UUID | None = None


class Rows46(Ensure46):
    receipt_id: uuid.UUID


class Sync46(Ensure46):
    events: list[r41.BatchEvent] = Field(default_factory=list, max_length=100)


def _row_by_id(db: Session, p: Ensure46, rid) -> dict | None:
    row = db.execute(text("""
        SELECT * FROM fc_r145f27_manual_receipts
        WHERE id=:id AND client_id=:c AND warehouse_id=:w
        LIMIT 1
    """), {"id": str(rid), "c": str(p.client_id), "w": str(p.warehouse_id)}).mappings().first()
    return dict(row) if row else None


def _has_stock_operation(db: Session, p: Ensure46, rid) -> bool:
    row = db.execute(text("""
        SELECT 1 FROM inventory_operations
        WHERE CAST(client_id AS text)=:c AND CAST(warehouse_id AS text)=:w
          AND UPPER(COALESCE(status,'COMPLETED'))='COMPLETED'
          AND COALESCE(idempotency_key,'') LIKE :pat
        LIMIT 1
    """), {
        "c": str(p.client_id), "w": str(p.warehouse_id), "pat": f"%:MANUAL:{rid}:%",
    }).first()
    return bool(row)


def _cancel_dirty(db: Session, rid) -> None:
    db.execute(text("""UPDATE fc_r145f27_manual_receipts
        SET status='CANCELLED', updated_at=CURRENT_TIMESTAMP,
            completed_at=COALESCE(completed_at,CURRENT_TIMESTAMP)
        WHERE id=:id AND status IN ('DRAFT','POSTING')"""), {"id": str(rid)})


def _latest_draft(db: Session, p: Ensure46) -> dict | None:
    rows = db.execute(text("""
        SELECT * FROM fc_r145f27_manual_receipts
        WHERE client_id=:c AND warehouse_id=:w
          AND lower(COALESCE(worker,''))=lower(:worker)
          AND status='DRAFT'
        ORDER BY updated_at DESC, created_at DESC, id DESC
        LIMIT 8
    """), {
        "c": str(p.client_id), "w": str(p.warehouse_id), "worker": _s(p.worker, 255),
    }).mappings().all()
    changed = False
    for raw in rows:
        row = dict(raw)
        if _has_stock_operation(db, p, row["id"]):
            _cancel_dirty(db, row["id"]); changed = True; continue
        if changed: db.commit()
        return row
    if changed: db.commit()
    return None


def _ensure_fast(db: Session, p: Ensure46, request: Request) -> dict:
    scope(db, p.client_id, p.warehouse_id)
    worker = _s(p.worker, 255)
    if not worker:
        raise HTTPException(422, detail="Укажите имя приёмщика")

    if db.bind.dialect.name == "postgresql":
        db.execute(text("SELECT pg_advisory_xact_lock(hashtext(:k))"), {
            "k": f"fc:r145f46:worker:{p.client_id}:{p.warehouse_id}:{worker.casefold()}"
        })

    if p.preferred_receipt_id:
        preferred = _row_by_id(db, p, p.preferred_receipt_id)
        if preferred and _fold(preferred.get("worker")) == _fold(worker) and str(preferred.get("status") or "").upper() == "DRAFT":
            if not _has_stock_operation(db, p, preferred["id"]):
                return preferred
            _cancel_dirty(db, preferred["id"]); db.commit()

    row = _latest_draft(db, p)
    if row:
        return row

    rid = str(uuid.uuid4())
    actor = "admin"
    try:
        # Keep the established audit actor when available without depending on it.
        from app.routers import r145f30_receiving as fast
        actor = _s(fast.actor(request), 255) or "admin"
    except Exception:
        actor = "admin"

    db.execute(text("""
        INSERT INTO fc_r145f27_manual_receipts
          (id,client_id,warehouse_id,actor,worker,reference,status)
        VALUES(:id,:c,:w,:actor,:worker,:ref,'DRAFT')
    """), {
        "id": rid, "c": str(p.client_id), "w": str(p.warehouse_id),
        "actor": actor, "worker": worker, "ref": _s(p.reference, 255),
    })
    db.commit()
    created = _row_by_id(db, p, rid)
    if not created:
        raise HTTPException(500, detail="Не удалось открыть новый черновик приёмки")
    return created


def _snapshot_rows_fast(db: Session, p: Rows46) -> dict:
    row = _row_by_id(db, p, p.receipt_id)
    if not row:
        raise HTTPException(404, detail="Черновик приёмки не найден")
    if _fold(row.get("worker")) != _fold(p.worker):
        raise HTTPException(409, detail="Эта приёмка принадлежит другому приёмщику")
    status = str(row.get("status") or "").upper()
    if status not in {"DRAFT", "POSTING"}:
        raise HTTPException(409, detail={"code": "RECEIPT_NOT_DRAFT", "message": "Приёмка уже завершена", "status": status})
    rows = [dict(x) for x in db.execute(text("""
        SELECT * FROM fc_r145f27_manual_lines
        WHERE receipt_id=:r AND quantity>0 AND posted_at IS NULL
        ORDER BY created_at,id
    """), {"r": str(p.receipt_id)}).mappings().all()]
    return {
        "id": str(row["id"]), "client_id": str(row["client_id"]), "warehouse_id": str(row["warehouse_id"]),
        "actor": row.get("actor") or "", "worker": row.get("worker") or "", "reference": row.get("reference") or "",
        "status": row.get("status") or "DRAFT", "created_at": row.get("created_at"), "updated_at": row.get("updated_at"),
        "completed_at": row.get("completed_at"), "rows": rows,
        "units": sum(int(x.get("quantity") or 0) for x in rows), "positions": len(rows),
        "saved_on_server": True, "compact": True, "r145f46_fast": True,
    }


@router.post("/r145f46/manual-drafts/ensure-fast")
def ensure_fast(payload: Ensure46, request: Request, db: Session = Depends(get_db)):
    started = time.perf_counter()
    row = _ensure_fast(db, payload, request)
    return {
        "ok": True, "receipt_id": str(row["id"]), "status": str(row.get("status") or "DRAFT"),
        "worker": row.get("worker") or "", "server_elapsed_ms": int(round((time.perf_counter() - started) * 1000)),
        "marker": "FC_R145F46_ENSURE_FAST",
    }


@router.post("/r145f46/manual-drafts/rows-fast")
def rows_fast(payload: Rows46, request: Request, db: Session = Depends(get_db)):
    started = time.perf_counter()
    scope(db, payload.client_id, payload.warehouse_id)
    snap = _snapshot_rows_fast(db, payload)
    return {
        "ok": True, "snapshot": snap,
        "server_elapsed_ms": int(round((time.perf_counter() - started) * 1000)),
        "marker": "FC_R145F46_ROWS_FAST",
    }


@router.post("/r145f46/manual-drafts/sync-rescue")
def sync_rescue(payload: Sync46, request: Request, db: Session = Depends(get_db)):
    """Persist up to 100 queued scans without the r145f45 state/normalize gate.

    If the browser's old preferred receipt has already been closed, a fresh/current
    DRAFT is selected.  r145f41 then de-duplicates every request_id across receipts,
    so an already-saved retry is acknowledged but never inserted a second time.
    """
    started = time.perf_counter()
    target = _ensure_fast(db, payload, request)
    rid = uuid.UUID(str(target["id"]))
    batch = r41.FastBatch41(
        client_id=payload.client_id,
        warehouse_id=payload.warehouse_id,
        receipt_id=rid,
        worker=payload.worker,
        reference=payload.reference,
        events=payload.events[:100],
        want_snapshot=False,
    )
    result = r41.sync_batch_fast(batch, request, db)
    result["target_receipt_id"] = str(rid)
    result["server_elapsed_ms"] = int(round((time.perf_counter() - started) * 1000))
    result["marker"] = "FC_R145F46_SYNC_RESCUE"
    return result
