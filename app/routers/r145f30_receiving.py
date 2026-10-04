"""r145f30: fast manual receiving + durable receipt ownership by worker name.

Confirmed scans are saved server-side immediately.  Warehouse stock is still
posted only by the existing final batch commit.  The fast scan endpoint returns
only the changed line and counters, avoiding a full receipt snapshot after each
physical scan.
"""
from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models import SKU
from app.routers.r145f27_receiving import actor
from app.services import r145f27_receiving as legacy
from app.services.r145f25_places import scope

router = APIRouter(tags=["r145f30"])


def _s(value: Any, limit: int = 255) -> str:
    return str(value or "").strip()[:limit]


def _now(db: Session) -> str:
    return "now()" if db.bind.dialect.name == "postgresql" else "CURRENT_TIMESTAMP"


def install_schema(db: Session) -> None:
    legacy.install_schema(db)
    if db.bind.dialect.name == "postgresql":
        db.execute(text("ALTER TABLE fc_r145f27_manual_receipts ADD COLUMN IF NOT EXISTS worker varchar(255)"))
        db.execute(text("CREATE INDEX IF NOT EXISTS ix_fc_r145f30_manual_worker ON fc_r145f27_manual_receipts(client_id,warehouse_id,lower(worker),status,updated_at DESC)"))
    else:
        cols = {str(r[1]) for r in db.execute(text("PRAGMA table_info(fc_r145f27_manual_receipts)")).all()}
        if "worker" not in cols:
            db.execute(text("ALTER TABLE fc_r145f27_manual_receipts ADD COLUMN worker varchar(255)"))
        db.execute(text("CREATE INDEX IF NOT EXISTS ix_fc_r145f30_manual_worker ON fc_r145f27_manual_receipts(client_id,warehouse_id,worker,status,updated_at)"))
    db.commit()


def _receipt(db: Session, rid: uuid.UUID | str, client_id, warehouse_id, lock: bool = False) -> dict:
    sql = "SELECT * FROM fc_r145f27_manual_receipts WHERE id=:id AND client_id=:c AND warehouse_id=:w"
    if lock and db.bind.dialect.name == "postgresql":
        sql += " FOR UPDATE"
    row = db.execute(text(sql), {"id": str(rid), "c": str(client_id), "w": str(warehouse_id)}).mappings().first()
    if not row:
        raise HTTPException(404, detail="Черновик приёмки не найден на выбранном складе")
    return dict(row)


def _compact_snapshot(db: Session, r: dict) -> dict:
    rows = [dict(x) for x in db.execute(text("""SELECT * FROM fc_r145f27_manual_lines
        WHERE receipt_id=:r AND quantity>0 ORDER BY created_at,id"""), {"r": str(r["id"])}).mappings().all()]
    barcodes = sorted({str(x.get("barcode") or "").strip() for x in rows if x.get("barcode")})
    current: dict[str, SKU] = {}
    if barcodes:
        try:
            cid = uuid.UUID(str(r["client_id"]))
            for sku in db.execute(select(SKU).where(SKU.client_id == cid, SKU.barcode.in_(barcodes))).scalars().all():
                current[str(sku.barcode or "").strip()] = sku
        except Exception:
            current = {}
    for row in rows:
        sku = current.get(str(row.get("barcode") or "").strip())
        if sku:
            row["product_id"] = str(sku.id)
            row["article"] = sku.article or sku.sku_code or row.get("article")
            row["size"] = sku.size or row.get("size")
            row["name"] = sku.name or row.get("name")
            row["product_found"] = True
    active = [x for x in rows if not x.get("posted_at") and int(x.get("quantity") or 0) > 0]
    return {
        "id": str(r["id"]),
        "client_id": str(r["client_id"]),
        "warehouse_id": str(r["warehouse_id"]),
        "actor": r.get("actor") or "",
        "worker": r.get("worker") or "",
        "reference": r.get("reference") or "",
        "status": r.get("status") or "DRAFT",
        "created_at": r.get("created_at"),
        "updated_at": r.get("updated_at"),
        "completed_at": r.get("completed_at"),
        "rows": rows,
        "units": sum(int(x.get("quantity") or 0) for x in active),
        "positions": len(active),
        "saved_on_server": True,
        "compact": True,
    }


def _manual_receipt_has_stock_operation(db: Session, client_id, warehouse_id, rid) -> bool:
    """True when this draft already produced any completed inventory operation.

    A draft with a posted operation must never be reopened as an active worker draft.
    Old releases could leave the receipt status at DRAFT after the stock operation was
    committed; this helper is the durable guard against resurrecting that old receipt.
    """
    row = db.execute(text("""SELECT 1 FROM inventory_operations
        WHERE CAST(client_id AS text)=:c AND CAST(warehouse_id AS text)=:w
          AND UPPER(COALESCE(status,'COMPLETED'))='COMPLETED'
          AND COALESCE(idempotency_key,'') LIKE :pat
          AND COALESCE(idempotency_key,'') NOT LIKE :r37
        LIMIT 1"""), {
        "c": str(client_id), "w": str(warehouse_id),
        "pat": f"%:MANUAL:{rid}:%",
        "r37": f"R145F37:MANUAL:{rid}:%",
    }).first()
    return bool(row)


def _cancel_worker_receipt(db: Session, rid) -> None:
    db.execute(text(f"""UPDATE fc_r145f27_manual_receipts
        SET status='CANCELLED',updated_at={_now(db)},completed_at=COALESCE(completed_at,{_now(db)})
        WHERE id=:id AND status IN ('DRAFT','POSTING')"""), {"id": str(rid)})


def _create_fresh_worker_receipt(db: Session, client_id, warehouse_id, worker: str,
                                 login_actor: str, reference: str = "") -> dict:
    rid = str(uuid.uuid4())
    db.execute(text("""INSERT INTO fc_r145f27_manual_receipts
        (id,client_id,warehouse_id,actor,worker,reference,status)
        VALUES(:id,:c,:w,:actor,:worker,:ref,'DRAFT')"""), {
        "id": rid, "c": str(client_id), "w": str(warehouse_id),
        "actor": _s(login_actor), "worker": _s(worker), "ref": _s(reference),
    })
    db.commit()
    return _receipt(db, rid, client_id, warehouse_id)


def _normalize_worker_drafts(db: Session, client_id, warehouse_id, worker: str):
    """Enforce exactly one resumable draft for a worker.

    - If the newest draft already has a completed stock operation, all active drafts
      for that worker are old/stale and are cancelled.  Caller creates a fresh blank.
    - Otherwise the newest clean draft is kept and older duplicate drafts are cancelled.
    - A POSTING draft without any stock operation is safely returned to DRAFT so a
      previous network/server interruption does not lock the operator out forever.
    """
    rows = [dict(x) for x in db.execute(text("""SELECT * FROM fc_r145f27_manual_receipts
        WHERE client_id=:c AND warehouse_id=:w AND lower(COALESCE(worker,''))=lower(:worker)
          AND status IN ('DRAFT','POSTING')
        ORDER BY updated_at DESC,created_at DESC,id DESC"""), {
        "c": str(client_id), "w": str(warehouse_id), "worker": _s(worker),
    }).mappings().all()]
    if not rows:
        return None, 0, False
    newest = rows[0]
    dirty_newest = _manual_receipt_has_stock_operation(db, client_id, warehouse_id, newest["id"])
    cancelled = 0
    if dirty_newest:
        for r in rows:
            _cancel_worker_receipt(db, r["id"])
            cancelled += 1
        db.commit()
        return None, cancelled, True
    for r in rows[1:]:
        _cancel_worker_receipt(db, r["id"])
        cancelled += 1
    if str(newest.get("status") or "").upper() == "POSTING":
        db.execute(text(f"UPDATE fc_r145f27_manual_receipts SET status='DRAFT',updated_at={_now(db)} WHERE id=:id"), {"id": str(newest["id"])})
    if cancelled or str(newest.get("status") or "").upper() == "POSTING":
        db.commit()
    return _receipt(db, newest["id"], client_id, warehouse_id), cancelled, False


def _worker_receipt(db: Session, client_id, warehouse_id, worker: str, login_actor: str, requested_id=None, reference="") -> tuple[dict, bool]:
    worker = _s(worker)
    if not worker:
        raise HTTPException(422, detail="Укажите имя приёмщика")
    if db.bind.dialect.name == "postgresql":
        db.execute(text("SELECT pg_advisory_xact_lock(hashtext(:k))"), {
            "k": f"fc:r145f37:worker-draft:{client_id}:{warehouse_id}:{worker.casefold()}"
        })
    row, _cancelled, hard_reset = _normalize_worker_drafts(db, client_id, warehouse_id, worker)
    adopted = False

    # Legacy adoption is allowed only when there was no dirty/posted worker draft.
    # After a dirty draft was detected we intentionally start a brand new receipt so
    # an older technical draft can never reappear after completion.
    if not row and not hard_reset:
        legacy_row = None
        if requested_id:
            candidate = db.execute(text("""SELECT * FROM fc_r145f27_manual_receipts
                WHERE id=:id AND client_id=:c AND warehouse_id=:w AND status IN ('DRAFT','POSTING')
                  AND COALESCE(worker,'')='' LIMIT 1"""), {
                "id": str(requested_id), "c": str(client_id), "w": str(warehouse_id),
            }).mappings().first()
            if candidate and not _manual_receipt_has_stock_operation(db, client_id, warehouse_id, candidate["id"]):
                legacy_row = candidate
        if not legacy_row:
            candidates = db.execute(text("""SELECT * FROM fc_r145f27_manual_receipts
                WHERE client_id=:c AND warehouse_id=:w AND lower(actor)=lower(:actor)
                  AND status IN ('DRAFT','POSTING') AND COALESCE(worker,'')=''
                ORDER BY updated_at DESC LIMIT 10"""), {
                "c": str(client_id), "w": str(warehouse_id), "actor": _s(login_actor),
            }).mappings().all()
            for candidate in candidates:
                if not _manual_receipt_has_stock_operation(db, client_id, warehouse_id, candidate["id"]):
                    legacy_row = candidate
                    break
        if legacy_row:
            db.execute(text(f"UPDATE fc_r145f27_manual_receipts SET worker=:worker,status='DRAFT',updated_at={_now(db)} WHERE id=:id"), {
                "worker": worker, "id": str(legacy_row["id"]),
            })
            db.commit()
            row = _receipt(db, legacy_row["id"], client_id, warehouse_id)
            adopted = True

    if not row:
        row = _create_fresh_worker_receipt(db, client_id, warehouse_id, worker, login_actor, reference)
    else:
        row = dict(row)
        updates = []
        params = {"id": str(row["id"])}
        if reference and not _s(row.get("reference")):
            updates.append("reference=:ref")
            params["ref"] = _s(reference)
        if _s(row.get("worker")) != worker:
            updates.append("worker=:worker")
            params["worker"] = worker
        if updates:
            updates.append(f"updated_at={_now(db)}")
            db.execute(text("UPDATE fc_r145f27_manual_receipts SET " + ",".join(updates) + " WHERE id=:id"), params)
            db.commit()
            row = _receipt(db, row["id"], client_id, warehouse_id)
    return dict(row), adopted


class WorkerOpen(BaseModel):
    client_id: uuid.UUID
    warehouse_id: uuid.UUID
    worker: str = Field(min_length=1, max_length=255)
    id: uuid.UUID | None = None
    reference: str | None = Field(default=None, max_length=255)


class FastScan(BaseModel):
    client_id: uuid.UUID
    warehouse_id: uuid.UUID
    worker: str = Field(min_length=1, max_length=255)
    request_id: str = Field(min_length=1, max_length=96)
    barcode: str = Field(min_length=1, max_length=255)
    quantity: int = Field(default=1, ge=1, le=100000)
    location_code: str = Field(min_length=1, max_length=255)
    container_code: str | None = Field(default=None, max_length=255)
    article: str | None = None
    size: str | None = None
    name: str | None = None
    product_found: bool = True
    location_id: str | None = None
    container_id: str | None = None
    container_name: str | None = None
    section: str | None = None
    aisle: str | None = None
    pallet: str | None = None
    storage_display: str | None = None
    duplicated_from: str | None = None
    product_id: str | None = None


@router.post("/r145f30/manual-drafts/open-worker")
def open_worker(payload: WorkerOpen, request: Request, db: Session = Depends(get_db)):
    scope(db, payload.client_id, payload.warehouse_id)
    install_schema(db)
    r, adopted = _worker_receipt(db, payload.client_id, payload.warehouse_id, payload.worker,
                                 actor(request), payload.id, payload.reference or "")
    snap = _compact_snapshot(db, r)
    snap["adopted_legacy"] = adopted
    return snap


@router.get("/r145f30/manual-drafts/active-worker")
def active_worker(client_id: uuid.UUID, warehouse_id: uuid.UUID, worker: str,
                  request: Request, db: Session = Depends(get_db)):
    scope(db, client_id, warehouse_id)
    install_schema(db)
    r, adopted = _worker_receipt(db, client_id, warehouse_id, worker, actor(request))
    snap = _compact_snapshot(db, r)
    snap["adopted_legacy"] = adopted
    return snap


@router.get("/r145f30/manual-drafts/{rid}/snapshot")
def worker_snapshot(rid: uuid.UUID, client_id: uuid.UUID, warehouse_id: uuid.UUID, worker: str,
                    db: Session = Depends(get_db)):
    scope(db, client_id, warehouse_id)
    install_schema(db)
    r = _receipt(db, rid, client_id, warehouse_id)
    if _s(r.get("worker")).casefold() != _s(worker).casefold():
        raise HTTPException(409, detail="Эта приёмка принадлежит другому приёмщику")
    return _compact_snapshot(db, r)


@router.post("/r145f30/manual-drafts/{rid}/scan-fast")
def scan_fast(rid: uuid.UUID, payload: FastScan, request: Request, db: Session = Depends(get_db)):
    scope(db, payload.client_id, payload.warehouse_id)
    install_schema(db)
    r = _receipt(db, rid, payload.client_id, payload.warehouse_id, True)
    if str(r.get("status") or "") != "DRAFT":
        raise HTTPException(409, detail="Черновик уже проводится или завершён")
    if _s(r.get("worker")).casefold() != _s(payload.worker).casefold():
        raise HTTPException(409, detail="Открыта приёмка другого сотрудника. Введите имя приёмщика заново.")

    existing_event = db.execute(text("SELECT line_id FROM fc_r145f27_manual_events WHERE receipt_id=:r AND request_id=:q"),
                                {"r": str(rid), "q": _s(payload.request_id, 96)}).mappings().first()
    if existing_event:
        line = db.execute(text("SELECT * FROM fc_r145f27_manual_lines WHERE id=:id"),
                          {"id": str(existing_event["line_id"])}).mappings().first()
        return _fast_result(db, r, dict(line) if line else None)

    barcode = _s(payload.barcode)
    qty = int(payload.quantity or 1)
    loc, cont = legacy.validate_place(db, payload.client_id, payload.warehouse_id,
                                      payload.location_code, payload.container_code)
    container_code = _s(payload.container_code) or None
    line_sql = """SELECT * FROM fc_r145f27_manual_lines
        WHERE receipt_id=:r AND posted_at IS NULL AND quantity>0 AND barcode=:b AND location_code=:l
          AND COALESCE(container_code,'')=COALESCE(:box,'') ORDER BY created_at LIMIT 1"""
    if db.bind.dialect.name == "postgresql":
        line_sql += " FOR UPDATE"
    line = db.execute(text(line_sql), {"r": str(rid), "b": barcode, "l": _s(payload.location_code),
                                      "box": container_code}).mappings().first()
    before = int(line["quantity"] or 0) if line else 0
    after = before + qty
    values = {
        "q": after, "a": _s(payload.article) or None, "z": _s(payload.size) or None,
        "n": _s(payload.name, 512) or None, "pf": bool(payload.product_found),
        "lid": _s(payload.location_id, 36) or str(loc.id),
        "cid": _s(payload.container_id, 36) or (str(cont.id) if cont else None),
        "cname": _s(payload.container_name) or (str(cont.name or cont.barcode) if cont else None),
        "sec": _s(payload.section, 80) or None, "aisle": _s(payload.aisle, 80) or None,
        "pal": _s(payload.pallet, 120) or None, "disp": _s(payload.storage_display, 512) or None,
        "dup": _s(payload.duplicated_from) or None, "prod": _s(payload.product_id, 36) or None,
    }
    if line:
        line_id = str(line["id"])
        db.execute(text(f"""UPDATE fc_r145f27_manual_lines SET quantity=:q,article=:a,size=:z,name=:n,
            product_found=:pf,location_id=:lid,container_id=:cid,container_name=:cname,section=:sec,
            aisle=:aisle,pallet=:pal,storage_display=:disp,duplicated_from=:dup,product_id=:prod,
            updated_at={_now(db)} WHERE id=:id"""), {**values, "id": line_id})
    else:
        line_id = str(uuid.uuid4())
        db.execute(text("""INSERT INTO fc_r145f27_manual_lines
          (id,receipt_id,barcode,quantity,location_code,container_code,location_id,container_id,container_name,
           section,aisle,pallet,storage_display,duplicated_from,product_id,article,size,name,product_found)
          VALUES(:id,:r,:b,:q,:l,:box,:lid,:cid,:cname,:sec,:aisle,:pal,:disp,:dup,:prod,:a,:z,:n,:pf)"""),
          {**values, "id": line_id, "r": str(rid), "b": barcode, "l": _s(payload.location_code),
           "box": container_code})
    db.execute(text("""INSERT INTO fc_r145f27_manual_events
      (id,receipt_id,line_id,request_id,action,barcode,delta_qty,before_qty,after_qty,location_code,container_code,actor)
      VALUES(:id,:r,:line,:req,'SCAN',:b,:delta,:before,:after,:l,:box,:actor)"""), {
        "id": str(uuid.uuid4()), "r": str(rid), "line": line_id, "req": _s(payload.request_id, 96),
        "b": barcode, "delta": qty, "before": before, "after": after,
        "l": _s(payload.location_code), "box": container_code, "actor": _s(payload.worker),
    })
    db.execute(text(f"UPDATE fc_r145f27_manual_receipts SET updated_at={_now(db)} WHERE id=:id"), {"id": str(rid)})
    db.commit()
    line = db.execute(text("SELECT * FROM fc_r145f27_manual_lines WHERE id=:id"), {"id": line_id}).mappings().first()
    r = _receipt(db, rid, payload.client_id, payload.warehouse_id)
    return _fast_result(db, r, dict(line) if line else None)


def _fast_result(db: Session, r: dict, line: dict | None) -> dict:
    stats = db.execute(text("""SELECT COALESCE(SUM(quantity),0) AS units, COUNT(*) AS positions
        FROM fc_r145f27_manual_lines WHERE receipt_id=:r AND quantity>0 AND posted_at IS NULL"""),
        {"r": str(r["id"])}).mappings().first()
    place_total = 0
    if line:
        place_total = int(db.execute(text("""SELECT COALESCE(SUM(quantity),0) FROM fc_r145f27_manual_lines
            WHERE receipt_id=:r AND quantity>0 AND posted_at IS NULL AND location_code=:l
              AND COALESCE(container_code,'')=COALESCE(:box,'')"""),
            {"r": str(r["id"]), "l": line.get("location_code"), "box": line.get("container_code")}).scalar() or 0)
    return {
        "ok": True,
        "receipt_id": str(r["id"]),
        "worker": r.get("worker") or "",
        "updated_at": r.get("updated_at"),
        "line": line,
        "units": int(stats.get("units") or 0),
        "positions": int(stats.get("positions") or 0),
        "place_total": place_total,
        "saved_on_server": True,
        "compact": True,
    }
