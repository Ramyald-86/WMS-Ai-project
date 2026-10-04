"""r145f37: clean exact manual receiving lifecycle.

Rules:
- A worker has exactly one resumable clean DRAFT.
- Any old DRAFT/POSTING that already produced stock is never resumed.
- Finalization freezes one server snapshot and posts exactly that snapshot once.
- No delta recovery of old receipts: dirty old drafts are closed without stock changes.
- A receipt becomes COMPLETED only after the created inventory operation exactly
  matches draft quantity and sku/location/container composition.
- After completion a brand new empty receipt id is created for the same worker.
"""
from __future__ import annotations

from collections import defaultdict
from decimal import Decimal
import hashlib
import json
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select, text
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models import Container, Location, SKU
from app.routers import r145f30_receiving as fast
from app.routers.r145f26_cards import receive as receive_manual
from app.services.r145f25_places import assert_place, scope

router = APIRouter(tags=["r145f37"])


def _s(v, limit=255):
    return str(v or "").strip()[:limit]


def _dec(v):
    return Decimal(str(v or 0))


def _normal_box(box: Container) -> bool:
    return str(getattr(box, "container_type", "") or "").upper() not in {"DEFECT_BOX", "TRANSIT_BOX", "FBO_BOX"}


def _box_name(box: Container) -> str:
    return str(getattr(box, "name", None) or getattr(box, "barcode", None) or getattr(box, "id", ""))


def _resolve_sku(db: Session, client_id, row):
    raw_pid = _s(row.get("product_id"), 36)
    if raw_pid:
        try:
            sku = db.execute(select(SKU).where(SKU.id == uuid.UUID(raw_pid), SKU.client_id == client_id)).scalar_one_or_none()
            if sku:
                return sku
        except Exception:
            pass
    barcode = _s(row.get("barcode"))
    skus = db.execute(select(SKU).where(SKU.client_id == client_id, SKU.barcode == barcode)).scalars().all()
    if len(skus) == 1:
        return skus[0]
    if len(skus) > 1:
        raise HTTPException(409, detail={"code": "AMBIGUOUS_SKU", "message": f"ШК {barcode} привязан к нескольким товарам. Исправьте карточку до завершения приёмки."})
    raise HTTPException(409, detail={"code": "SKU_NOT_FOUND", "message": f"ШК {barcode} не найден в базе товаров. Приёмка оставлена открытой; остатки не изменены."})


def _resolve_box(db: Session, client_id, warehouse_id, row):
    # FC_R145F44_BOX_CODE_AUTHORITATIVE
    # Visible/scanned box code belongs to the physical action. A cached UUID may
    # be stale after a fast box switch, so explicit code/name resolves first.
    code = _s(row.get("container_code") or row.get("container_name"))
    box = None
    if code:
        stmt = select(Container).where(
            Container.client_id == client_id,
            Container.warehouse_id == warehouse_id,
            or_(func.lower(Container.name) == code.casefold(), func.lower(Container.barcode) == code.casefold()),
        )
        if db.bind.dialect.name == "postgresql":
            stmt = stmt.with_for_update()
        boxes = db.execute(stmt.limit(100)).scalars().all()
        boxes = sorted(boxes, key=lambda b: (
            str(getattr(b, "status", "") or "").upper() != "ACTIVE",
            getattr(b, "location_id", None) is None,
            str(b.id),
        ))
        box = boxes[0] if boxes else None
    if not box:
        raw_id = _s(row.get("container_id"), 36)
        if raw_id:
            try:
                stmt = select(Container).where(
                    Container.id == uuid.UUID(raw_id),
                    Container.client_id == client_id,
                    Container.warehouse_id == warehouse_id,
                )
                if db.bind.dialect.name == "postgresql":
                    stmt = stmt.with_for_update()
                box = db.execute(stmt).scalar_one_or_none()
            except Exception:
                box = None
    if code and not box:
        raise HTTPException(409, detail={"code": "BOX_NOT_FOUND", "message": f"Короб {code} не найден у выбранного клиента на этом складе."})
    if box and not _normal_box(box):
        raise HTTPException(409, detail=f"Короб {_box_name(box)} относится к специальному контуру")
    if box and str(getattr(box, "status", "") or "").upper() != "ACTIVE":
        box.status = "ACTIVE"
    return box


def _resolve_location(db: Session, client_id, warehouse_id, row, box):
    if box and getattr(box, "location_id", None):
        loc = db.execute(select(Location).where(
            Location.id == box.location_id,
            Location.warehouse_id == warehouse_id,
            Location.is_active.is_(True),
        )).scalar_one_or_none()
        if loc:
            assert_place(db, client_id, warehouse_id, loc.id)
            return loc
    raw_id = _s(row.get("location_id"), 36)
    if raw_id:
        try:
            loc = db.execute(select(Location).where(
                Location.id == uuid.UUID(raw_id),
                Location.warehouse_id == warehouse_id,
                Location.is_active.is_(True),
            )).scalar_one_or_none()
            if loc:
                assert_place(db, client_id, warehouse_id, loc.id)
                return loc
        except Exception:
            pass
    code = _s(row.get("location_code"))
    if code:
        locs = db.execute(select(Location).where(
            Location.warehouse_id == warehouse_id,
            Location.is_active.is_(True),
            or_(func.lower(Location.code) == code.casefold(), func.lower(Location.barcode) == code.casefold()),
        ).limit(50)).scalars().all()
        if locs:
            loc = sorted(locs, key=lambda x: str(x.id))[0]
            assert_place(db, client_id, warehouse_id, loc.id)
            return loc
    raise HTTPException(409, detail={"code": "LOCATION_NOT_FOUND", "message": f"Не найден действующий адрес для ШК {_s(row.get('barcode'))}. Остатки не изменены."})


def _draft_snapshot(db: Session, receipt, client_id, warehouse_id):
    rows = [dict(x) for x in db.execute(text("""SELECT * FROM fc_r145f27_manual_lines
        WHERE receipt_id=:r AND quantity>0 ORDER BY created_at,id"""), {"r": str(receipt["id"])}).mappings().all()]
    if not rows:
        raise HTTPException(409, detail="В черновике нет товара")
    if any(x.get("posted_at") for x in rows):
        raise HTTPException(409, detail={"code": "DIRTY_OLD_DRAFT", "message": "Это старый технический черновик с уже проведёнными строками. Он не будет допроводиться; обновите страницу для новой приёмки."})

    groups = defaultdict(Decimal)
    payload_lines = []
    row_ids = []
    for row in rows:
        qty = int(row.get("quantity") or 0)
        if qty <= 0:
            continue
        sku = _resolve_sku(db, client_id, row)
        has_box = bool(row.get("container_id") or row.get("container_code") or row.get("container_name"))
        box = _resolve_box(db, client_id, warehouse_id, row) if has_box else None
        loc = _resolve_location(db, client_id, warehouse_id, row, box)
        box_id = str(box.id) if box else ""
        key = (str(sku.id), str(loc.id), box_id)
        groups[key] += Decimal(qty)
        payload_lines.append({
            "barcode": str(sku.barcode or row.get("barcode") or ""),
            "quantity": qty,
            "location_code": str(loc.code),
            "container_code": str((box.barcode or box.name) if box else "") or None,
        })
        row_ids.append(str(row["id"]))
    if not payload_lines:
        raise HTTPException(409, detail="В черновике нет товара для проведения")
    db.flush()
    return rows, row_ids, payload_lines, dict(groups)


def _signature(groups):
    raw = json.dumps([[list(k), str(groups[k])] for k in sorted(groups)], ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def _operation_groups(db: Session, client_id, warehouse_id, idem):
    rows = db.execute(text("""SELECT CAST(o.id AS text) operation_id,
          CAST(l.sku_id AS text) sku_id,
          CAST(l.destination_location_id AS text) location_id,
          COALESCE(CAST(l.destination_container_id AS text),'') container_id,
          l.quantity
        FROM inventory_operations o
        JOIN inventory_operation_lines l ON l.operation_id=o.id
        WHERE CAST(o.client_id AS text)=:c AND CAST(o.warehouse_id AS text)=:w
          AND UPPER(COALESCE(o.status,'COMPLETED'))='COMPLETED'
          AND COALESCE(o.idempotency_key,'')=:idem
        ORDER BY l.id"""), {"c": str(client_id), "w": str(warehouse_id), "idem": idem}).mappings().all()
    groups = defaultdict(Decimal)
    opid = ""
    for r in rows:
        groups[(str(r["sku_id"]), str(r["location_id"]), str(r.get("container_id") or ""))] += _dec(r["quantity"])
        opid = opid or str(r.get("operation_id") or "")
    return dict(groups), opid


def _linked_old_operation_exists(db: Session, client_id, warehouse_id, rid, exact_idem):
    row = db.execute(text("""SELECT idempotency_key FROM inventory_operations
        WHERE CAST(client_id AS text)=:c AND CAST(warehouse_id AS text)=:w
          AND UPPER(COALESCE(status,'COMPLETED'))='COMPLETED'
          AND COALESCE(idempotency_key,'') LIKE :pat
          AND COALESCE(idempotency_key,'')<>:exact
        LIMIT 1"""), {
        "c": str(client_id), "w": str(warehouse_id), "pat": f"%:MANUAL:{rid}:%", "exact": exact_idem,
    }).first()
    return bool(row)


def _units(groups):
    return int(sum(groups.values(), Decimal(0)))


def _cancel_other_drafts(db: Session, client_id, warehouse_id, worker, keep=None):
    rows = db.execute(text("""SELECT id FROM fc_r145f27_manual_receipts
        WHERE client_id=:c AND warehouse_id=:w AND lower(COALESCE(worker,''))=lower(:worker)
          AND status IN ('DRAFT','POSTING')"""), {
        "c": str(client_id), "w": str(warehouse_id), "worker": _s(worker),
    }).all()
    count = 0
    for (rid,) in rows:
        if keep and str(rid) == str(keep):
            continue
        fast._cancel_worker_receipt(db, rid)
        count += 1
    return count


class Worker37(BaseModel):
    client_id: uuid.UUID
    warehouse_id: uuid.UUID
    worker: str = Field(min_length=1, max_length=255)
    reference: str | None = Field(default=None, max_length=255)


class Commit37(Worker37):
    performed_by: str | None = Field(default=None, max_length=255)
    stock_class: str = Field(default="MAIN", max_length=16)


@router.post("/r145f37/manual-drafts/normalize-worker")
def normalize_worker(payload: Worker37, request: Request, db: Session = Depends(get_db)):
    scope(db, payload.client_id, payload.warehouse_id)
    fast.install_schema(db)
    row, _ = fast._worker_receipt(
        db, payload.client_id, payload.warehouse_id, payload.worker,
        fast.actor(request), reference=payload.reference or "",
    )
    return fast._compact_snapshot(db, row)


@router.post("/r145f37/manual-drafts/reset-worker")
def reset_worker(payload: Worker37, request: Request, db: Session = Depends(get_db)):
    scope(db, payload.client_id, payload.warehouse_id)
    fast.install_schema(db)
    if db.bind.dialect.name == "postgresql":
        db.execute(text("SELECT pg_advisory_xact_lock(hashtext(:k))"), {"k": f"fc:r145f37:worker-reset:{payload.client_id}:{payload.warehouse_id}:{payload.worker.casefold()}"})
    cancelled = _cancel_other_drafts(db, payload.client_id, payload.warehouse_id, payload.worker)
    db.commit()
    fresh = fast._create_fresh_worker_receipt(db, payload.client_id, payload.warehouse_id, payload.worker, fast.actor(request), "")
    return {"ok": True, "cancelled": cancelled, "next_receipt": fast._compact_snapshot(db, fresh)}


@router.post("/r145f37/manual-drafts/{rid}/commit-exact")
def commit_exact(rid: uuid.UUID, payload: Commit37, request: Request, db: Session = Depends(get_db)):
    scope(db, payload.client_id, payload.warehouse_id)
    fast.install_schema(db)
    if db.bind.dialect.name == "postgresql":
        db.execute(text("SELECT pg_advisory_xact_lock(hashtext(:k))"), {"k": f"fc:r145f37:manual:{rid}"})

    receipt = fast._receipt(db, rid, payload.client_id, payload.warehouse_id, True)
    if _s(receipt.get("worker")).casefold() != _s(payload.worker).casefold():
        raise HTTPException(409, detail="Эта приёмка принадлежит другому приёмщику")
    status = str(receipt.get("status") or "").upper()
    if status == "COMPLETED":
        next_row, _ = fast._worker_receipt(db, payload.client_id, payload.warehouse_id, payload.worker, fast.actor(request))
        return {"ok": True, "already_completed": True, "next_receipt": fast._compact_snapshot(db, next_row)}
    if status not in {"DRAFT", "POSTING"}:
        raise HTTPException(409, detail=f"Приёмка имеет статус {status or 'UNKNOWN'}")

    rows, row_ids, receive_lines, desired = _draft_snapshot(db, receipt, payload.client_id, payload.warehouse_id)
    expected_units = _units(desired)
    expected_positions = len(rows)
    signature = _signature(desired)
    idem = f"R145F37:MANUAL:{rid}:{signature}"

    # Old receipts from previous releases are never auto-topped-up anymore.
    # If they already have a different stock operation, close the technical
    # draft without changing stock and return a brand new empty receipt.
    if _linked_old_operation_exists(db, payload.client_id, payload.warehouse_id, rid, idem):
        fast._cancel_worker_receipt(db, rid)
        _cancel_other_drafts(db, payload.client_id, payload.warehouse_id, payload.worker)
        db.commit()
        fresh = fast._create_fresh_worker_receipt(db, payload.client_id, payload.warehouse_id, payload.worker, fast.actor(request), "")
        return {
            "ok": True, "old_draft_closed": True, "posted_units": 0, "expected_units": expected_units,
            "message": "Старый технический черновик закрыт без повторного движения товара. Начата новая пустая приёмка.",
            "next_receipt": fast._compact_snapshot(db, fresh),
        }

    # Freeze the snapshot before the warehouse operation.  scan-fast rejects
    # any further scans while status is POSTING.
    db.execute(text("""UPDATE fc_r145f27_manual_receipts
        SET status='POSTING',reference=:ref,updated_at=now() WHERE id=:id"""), {
        "ref": _s(payload.reference or receipt.get("reference")), "id": str(rid),
    })
    db.commit()

    body = {
        "client_id": str(payload.client_id),
        "warehouse_id": str(payload.warehouse_id),
        "idempotency_key": idem,
        "performed_by": payload.performed_by or payload.worker or fast.actor(request),
        "reference": payload.reference or receipt.get("reference") or None,
        "wb_warehouse_id": None,
        "internal_only": False,
        "stock_class": payload.stock_class if payload.stock_class in ("MAIN", "FBS") else "MAIN",
        "lines": receive_lines,
    }
    try:
        result = receive_manual(body, request, db)
    except Exception:
        db.rollback()
        try:
            db.execute(text("UPDATE fc_r145f27_manual_receipts SET status='DRAFT',updated_at=now() WHERE id=:id AND status='POSTING'"), {"id": str(rid)})
            db.commit()
        except Exception:
            db.rollback()
        raise

    actual, opid = _operation_groups(db, payload.client_id, payload.warehouse_id, idem)
    if actual != desired:
        # Do not hide a mismatch.  Keep the receipt blocked for administrator
        # review.  Reusing the same idempotency key prevents duplicate stock.
        db.execute(text("UPDATE fc_r145f27_manual_receipts SET status='POSTING',updated_at=now() WHERE id=:id"), {"id": str(rid)})
        db.commit()
        raise HTTPException(409, detail={
            "code": "EXACT_PARITY_FAILED",
            "message": "Складская операция не совпала с зафиксированным черновиком. Приёмка не закрыта и повторно не начисляется.",
            "draft_units": expected_units,
            "posted_units": _units(actual),
            "draft_positions": expected_positions,
        })

    from sqlalchemy import bindparam
    stmt = text("""UPDATE fc_r145f27_manual_lines
        SET posted_at=COALESCE(posted_at,now()),operation_id=:op,updated_at=now()
        WHERE id IN :ids""").bindparams(bindparam("ids", expanding=True))
    db.execute(stmt, {"op": opid or None, "ids": row_ids})
    db.execute(text("""UPDATE fc_r145f27_manual_receipts
        SET status='COMPLETED',operation_ids=:ops,updated_at=now(),completed_at=now()
        WHERE id=:id"""), {"ops": opid or "", "id": str(rid)})

    # Exactly one future draft: discard any older technical duplicates and
    # create a completely new empty id.  Never call _worker_receipt here,
    # because old releases could resurrect a stale older DRAFT.
    _cancel_other_drafts(db, payload.client_id, payload.warehouse_id, payload.worker, keep=rid)
    db.commit()
    fresh = fast._create_fresh_worker_receipt(db, payload.client_id, payload.warehouse_id, payload.worker, fast.actor(request), "")

    return {
        "ok": True,
        "completed": True,
        "receipt_id": str(rid),
        "operation_id": opid,
        "draft_units": expected_units,
        "posted_units": _units(actual),
        "draft_positions": expected_positions,
        "next_receipt": fast._compact_snapshot(db, fresh),
        "result": result or {},
    }
