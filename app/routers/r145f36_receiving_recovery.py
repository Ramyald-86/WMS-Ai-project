"""r145f36: recover a stuck manual/PC receiving draft exactly.

The server draft is the source of truth.  Unlike the legacy finalizer this
module re-resolves *all* positive draft rows against the current SKU catalog,
even rows that were originally saved with product_found=false.  Existing
completed inventory operations are subtracted by exact sku/place/container,
so retries post only the missing delta.  The draft is closed only after exact
parity is proven.
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

router = APIRouter(tags=["r145f36"])


def _s(v, limit=255):
    return str(v or "").strip()[:limit]


def _dec(v):
    return Decimal(str(v or 0))


def _normal_box(box: Container) -> bool:
    return str(getattr(box, "container_type", "") or "").upper() not in {"DEFECT_BOX", "TRANSIT_BOX", "FBO_BOX"}


def _box_name(box: Container) -> str:
    return str(getattr(box, "name", None) or getattr(box, "barcode", None) or getattr(box, "id", ""))


def _sum_groups(rows):
    out = defaultdict(Decimal)
    opids = []
    for r in rows:
        key = (str(r.get("sku_id") or ""), str(r.get("location_id") or ""), str(r.get("container_id") or ""))
        out[key] += _dec(r.get("quantity"))
        opid = str(r.get("operation_id") or "")
        if opid and opid not in opids:
            opids.append(opid)
    return dict(out), opids


def _group_units(groups):
    return int(sum(groups.values(), Decimal(0)))


def _compare(desired, actual):
    missing, excess = {}, {}
    for k in set(desired) | set(actual):
        d, a = desired.get(k, Decimal(0)), actual.get(k, Decimal(0))
        if d > a:
            missing[k] = d - a
        elif a > d:
            excess[k] = a - d
    return missing, excess


def _signature(groups):
    payload = [[list(k), str(groups[k])] for k in sorted(groups)]
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:20]


def _posted_groups(db: Session, client_id, warehouse_id, rid):
    """Only operations belonging to this exact receipt are counted.

    Do not rely on draft line posted_at here: the original failure can happen
    after the stock operation was committed but before the draft rows were
    marked posted.  Idempotency keys are therefore the durable link.
    """
    rows = db.execute(text("""SELECT CAST(o.id AS text) operation_id,
          CAST(l.sku_id AS text) sku_id,
          CAST(l.destination_location_id AS text) location_id,
          COALESCE(CAST(l.destination_container_id AS text),'') container_id,
          l.quantity
        FROM inventory_operations o
        JOIN inventory_operation_lines l ON l.operation_id=o.id
        WHERE CAST(o.client_id AS text)=:c AND CAST(o.warehouse_id AS text)=:w
          AND UPPER(COALESCE(o.status,'COMPLETED'))='COMPLETED'
          AND (
             COALESCE(o.idempotency_key,'')=:legacy
             OR COALESCE(o.idempotency_key,'') LIKE :r35
             OR COALESCE(o.idempotency_key,'') LIKE :r36
          )
        ORDER BY o.created_at,o.id,l.id"""), {
            "c": str(client_id), "w": str(warehouse_id),
            "legacy": f"R145F27:MANUAL:{rid}:known",
            "r35": f"R145F35:MANUAL:{rid}:known:%",
            "r36": f"R145F36:MANUAL:{rid}:known:%",
        }).mappings().all()
    return _sum_groups(rows)


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
        raise HTTPException(409, detail={"code": "AMBIGUOUS_SKU", "message": f"ШК {barcode} привязан к нескольким товарам. Исправьте карточку."})
    return None


def _resolve_box(db: Session, client_id, warehouse_id, row):
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
            if box:
                return box
        except Exception:
            pass
    code = _s(row.get("container_code") or row.get("container_name"))
    if not code:
        return None
    stmt = select(Container).where(
        Container.client_id == client_id,
        Container.warehouse_id == warehouse_id,
        or_(func.lower(Container.name) == code.casefold(), func.lower(Container.barcode) == code.casefold()),
    )
    if db.bind.dialect.name == "postgresql":
        stmt = stmt.with_for_update()
    boxes = db.execute(stmt.limit(100)).scalars().all()
    if not boxes:
        return None
    # Old data may contain duplicate public names. Prefer ACTIVE, addressed,
    # then deterministic id instead of blocking a known receipt.
    boxes = sorted(boxes, key=lambda b: (
        str(getattr(b, "status", "") or "").upper() != "ACTIVE",
        getattr(b, "location_id", None) is None,
        str(b.id),
    ))
    return boxes[0]


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
    return None


def _desired_all(db: Session, receipt, client_id, warehouse_id):
    rows = [dict(x) for x in db.execute(text("""SELECT * FROM fc_r145f27_manual_lines
        WHERE receipt_id=:r AND quantity>0 ORDER BY created_at,id"""), {"r": str(receipt["id"])}).mappings().all()]
    if not rows:
        raise HTTPException(409, detail="В серверном черновике нет товара")

    desired = defaultdict(Decimal)
    meta = {}
    resolved = []
    unresolved = []
    corrected = []
    reactivated = []
    for row in rows:
        sku = _resolve_sku(db, client_id, row)
        if not sku:
            unresolved.append({
                "barcode": _s(row.get("barcode")),
                "quantity": int(row.get("quantity") or 0),
                "container": _s(row.get("container_name") or row.get("container_code")),
            })
            continue
        box = _resolve_box(db, client_id, warehouse_id, row) if (row.get("container_id") or row.get("container_code") or row.get("container_name")) else None
        if (row.get("container_id") or row.get("container_code") or row.get("container_name")) and not box:
            raise HTTPException(409, detail={"code": "BOX_NOT_FOUND", "message": f"Короб {_s(row.get('container_name') or row.get('container_code'))} не найден в выбранном клиенте/складе."})
        if box:
            if not _normal_box(box):
                raise HTTPException(409, detail=f"Короб {_box_name(box)} относится к специальному контуру")
            if str(getattr(box, "status", "") or "").upper() != "ACTIVE":
                box.status = "ACTIVE"
                if _box_name(box) not in reactivated:
                    reactivated.append(_box_name(box))
        loc = _resolve_location(db, client_id, warehouse_id, row, box)
        if not loc:
            raise HTTPException(409, detail={"code": "LOCATION_NOT_FOUND", "message": f"Не найден адрес для ШК {_s(row.get('barcode'))} / короб {_s(row.get('container_name') or row.get('container_code'))}."})
        box_id = box.id if box else None
        key = (str(sku.id), str(loc.id), str(box_id or ""))
        desired[key] += _dec(row.get("quantity"))
        meta[key] = {
            "barcode": _s(row.get("barcode")),
            "location_code": str(loc.code),
            "container_code": str((box.barcode or box.name) if box else "") or None,
        }
        if row.get("product_found") is False or _s(row.get("product_id"), 36) != str(sku.id):
            corrected.append({"barcode": _s(row.get("barcode")), "quantity": int(row.get("quantity") or 0)})
        resolved.append((row, sku, loc, box))

    if unresolved:
        first = unresolved[0]
        raise HTTPException(409, detail={
            "code": "UNRESOLVED_RECEIPT_ROWS",
            "message": f"ШК {first['barcode']} ({first['quantity']} шт.) ещё отсутствует в базе товаров. Создайте/исправьте карточку — приёмка оставлена открытой, остатки не изменены.",
            "rows": unresolved[:20],
        })
    db.flush()
    return dict(desired), meta, resolved, corrected, reactivated


class Recover36(BaseModel):
    client_id: uuid.UUID
    warehouse_id: uuid.UUID
    worker: str = Field(min_length=1, max_length=255)
    reference: str | None = Field(default=None, max_length=255)
    performed_by: str | None = Field(default=None, max_length=255)
    stock_class: str = Field(default="MAIN", max_length=16)


@router.post("/r145f36/manual-drafts/{rid}/recover-complete")
def recover_complete(rid: uuid.UUID, payload: Recover36, request: Request, db: Session = Depends(get_db)):
    scope(db, payload.client_id, payload.warehouse_id)
    fast.install_schema(db)
    if db.bind.dialect.name == "postgresql":
        db.execute(text("SELECT pg_advisory_xact_lock(hashtext(:k))"), {"k": f"fc:r145f36:manual:{rid}"})

    receipt = fast._receipt(db, rid, payload.client_id, payload.warehouse_id, True)
    if _s(receipt.get("worker")).casefold() != _s(payload.worker).casefold():
        raise HTTPException(409, detail="Эта приёмка принадлежит другому приёмщику")
    status = str(receipt.get("status") or "").upper()
    if status not in {"DRAFT", "POSTING", "COMPLETED"}:
        raise HTTPException(409, detail=f"Приёмка имеет статус {status or 'UNKNOWN'}")

    desired, meta, resolved, corrected, reactivated = _desired_all(
        db, receipt, payload.client_id, payload.warehouse_id
    )
    expected_units = _group_units(desired)
    expected_positions = int(db.execute(text("SELECT COUNT(*) FROM fc_r145f27_manual_lines WHERE receipt_id=:r AND quantity>0"), {"r": str(rid)}).scalar() or 0)

    actual, old_opids = _posted_groups(db, payload.client_id, payload.warehouse_id, rid)
    missing, excess = _compare(desired, actual)
    if excess:
        examples = []
        for key, qty in list(excess.items())[:8]:
            examples.append(f"{meta.get(key, {}).get('barcode') or key[0]} +{int(qty)}")
        raise HTTPException(409, detail={
            "code": "POSTED_EXCEEDS_DRAFT",
            "message": "В проведённых операциях уже больше товара, чем в текущем черновике. Автоматическое списание запрещено.",
            "expected_units": expected_units,
            "posted_units": _group_units(actual),
            "examples": examples,
        })

    repaired_units = 0
    repair_opid = ""
    last_result = {}
    if missing:
        repaired_units = _group_units(missing)
        lines = []
        for key in sorted(missing):
            m = meta.get(key)
            if not m:
                raise HTTPException(409, detail="Не удалось сопоставить недостающую строку с серверным черновиком")
            lines.append({
                "barcode": m["barcode"],
                "quantity": int(missing[key]),
                "location_code": m["location_code"],
                "container_code": m["container_code"],
            })
        idem = f"R145F36:MANUAL:{rid}:known:{_signature(missing)}"
        body = {
            "client_id": str(payload.client_id),
            "warehouse_id": str(payload.warehouse_id),
            "idempotency_key": idem,
            "performed_by": payload.performed_by or payload.worker,
            "reference": payload.reference or receipt.get("reference") or None,
            "wb_warehouse_id": None,
            "internal_only": False,
            "stock_class": payload.stock_class if payload.stock_class in ("MAIN", "FBS") else "MAIN",
            "lines": lines,
        }
        try:
            last_result = receive_manual(body, request, db)
            repair_opid = str(last_result.get("operation_id") or last_result.get("id") or "")
        except Exception:
            db.rollback()
            try:
                db.execute(text("UPDATE fc_r145f27_manual_receipts SET status='DRAFT',updated_at=now() WHERE id=:id"), {"id": str(rid)})
                db.commit()
            except Exception:
                db.rollback()
            raise

    actual2, opids2 = _posted_groups(db, payload.client_id, payload.warehouse_id, rid)
    missing2, excess2 = _compare(desired, actual2)
    if missing2 or excess2:
        db.execute(text("UPDATE fc_r145f27_manual_receipts SET status='DRAFT',updated_at=now() WHERE id=:id"), {"id": str(rid)})
        db.commit()
        raise HTTPException(409, detail={
            "code": "FINAL_PARITY_FAILED",
            "message": "Сверка после проведения не совпала. Приёмка оставлена открытой — повторное начисление не выполнялось.",
            "expected_units": expected_units,
            "posted_units": _group_units(actual2),
            "missing_units": _group_units(missing2),
            "excess_units": _group_units(excess2),
        })

    # Exact parity is proven for every line, including lines that were originally
    # product_found=false.  Only now close the draft.
    for row, sku, loc, box in resolved:
        db.execute(text("""UPDATE fc_r145f27_manual_lines SET
            posted_at=COALESCE(posted_at,now()),
            product_found=true,product_id=:prod,article=:a,size=:z,name=:n,
            location_id=:loc,container_id=:box,
            container_name=COALESCE(:box_name,container_name),updated_at=now()
            WHERE id=:id AND receipt_id=:r"""), {
                "prod": str(sku.id), "a": sku.article or "", "z": sku.size or "", "n": sku.name or "",
                "loc": str(loc.id), "box": str(box.id) if box else None,
                "box_name": _box_name(box) if box else None,
                "id": str(row["id"]), "r": str(rid),
            })

    all_ops = []
    for x in old_opids + opids2 + ([repair_opid] if repair_opid else []):
        if x and x not in all_ops:
            all_ops.append(x)
    ref = _s(payload.reference or receipt.get("reference"))
    db.execute(text("""UPDATE fc_r145f27_manual_receipts SET
        status='COMPLETED',operation_ids=:ops,reference=:ref,updated_at=now(),completed_at=COALESCE(completed_at,now())
        WHERE id=:id"""), {"ops": ",".join(all_ops), "ref": ref, "id": str(rid)})
    db.commit()

    completed = fast._receipt(db, rid, payload.client_id, payload.warehouse_id)
    next_receipt, _ = fast._worker_receipt(
        db, payload.client_id, payload.warehouse_id, payload.worker,
        payload.performed_by or payload.worker, reference=""
    )
    next_snap = fast._compact_snapshot(db, next_receipt)
    return {
        "ok": True,
        "completed": True,
        "receipt_id": str(rid),
        "expected_units": expected_units,
        "expected_positions": expected_positions,
        "posted_units": _group_units(actual2),
        "repaired_units": repaired_units,
        "corrected_old_false_rows": len(corrected),
        "corrected_old_false_units": sum(int(x.get("quantity") or 0) for x in corrected),
        "reactivated_boxes": reactivated,
        "operation_ids": all_ops,
        "next_receipt": next_snap,
        "result": last_result or {"quantity_received": expected_units},
        "closed_receipt": fast._compact_snapshot(db, completed),
    }
