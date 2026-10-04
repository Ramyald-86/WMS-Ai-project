"""r145f35: exact-parity receiving finalization and stuck-draft recovery.

The server draft is the source of truth.  A receipt is marked completed only
when the sum and exact sku/location/container composition recorded in completed
inventory operations matches the draft.  If an older idempotent attempt posted
only a subset, only the positive delta is posted with a new content-derived
idempotency key.  This prevents silent 1100 -> 1083 style completions.
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
from app.routers import r145f19_finance as tsd_legacy
from app.routers import r145f30_receiving as fast
from app.routers.r145f26_cards import receive as receive_manual
from app.services import r145f27_receiving as manual_legacy
from app.services.r145f25_places import assert_place, scope

router = APIRouter(tags=["r145f35"])


def _s(v, limit=255):
    return str(v or "").strip()[:limit]


def _normal_box(box: Container) -> bool:
    return str(getattr(box, "container_type", "") or "").upper() not in {"DEFECT_BOX", "TRANSIT_BOX", "FBO_BOX"}


def _box_name(box: Container) -> str:
    return str(getattr(box, "name", None) or getattr(box, "barcode", None) or getattr(box, "id", ""))


def _dec(v) -> Decimal:
    return Decimal(str(v or 0))


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


def _posted_groups(db: Session, client_id, warehouse_id, exact_key: str, repair_like: str):
    rows = db.execute(text("""SELECT CAST(o.id AS text) operation_id,
          CAST(l.sku_id AS text) sku_id,
          CAST(l.destination_location_id AS text) location_id,
          COALESCE(CAST(l.destination_container_id AS text),'') container_id,
          l.quantity
        FROM inventory_operations o
        JOIN inventory_operation_lines l ON l.operation_id=o.id
        WHERE CAST(o.client_id AS text)=:c AND CAST(o.warehouse_id AS text)=:w
          AND UPPER(COALESCE(o.status,'COMPLETED'))='COMPLETED'
          AND UPPER(o.operation_type) IN ('RECEIVING','RECEIVING_IMPORT')
          AND (COALESCE(o.idempotency_key,'')=:exact OR COALESCE(o.idempotency_key,'') LIKE :repair)
        ORDER BY o.created_at,o.id,l.id"""), {
            "c": str(client_id), "w": str(warehouse_id), "exact": exact_key, "repair": repair_like,
        }).mappings().all()
    return _sum_groups(rows)


def _compare(desired: dict, actual: dict):
    missing = {}
    excess = {}
    keys = set(desired) | set(actual)
    for k in keys:
        d, a = desired.get(k, Decimal(0)), actual.get(k, Decimal(0))
        if d > a:
            missing[k] = d - a
        elif a > d:
            excess[k] = a - d
    return missing, excess


def _signature(groups: dict) -> str:
    payload = [[list(k), str(groups[k])] for k in sorted(groups)]
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:20]


def _group_units(groups: dict) -> int:
    return int(sum(groups.values(), Decimal(0)))


def _format_excess(excess: dict, meta: dict) -> str:
    parts = []
    for k, qty in list(excess.items())[:5]:
        m = meta.get(k, {})
        parts.append(f"{m.get('barcode') or k[0]} · +{int(qty)}")
    return ", ".join(parts)


def _manual_box(db: Session, client_id, warehouse_id, raw_id, raw_code):
    box = None
    if raw_id:
        try:
            box = db.execute(select(Container).where(
                Container.id == uuid.UUID(str(raw_id)),
                Container.client_id == client_id,
                Container.warehouse_id == warehouse_id,
            )).scalar_one_or_none()
        except Exception:
            box = None
    if not box and raw_code:
        rows = db.execute(select(Container).where(
            Container.client_id == client_id,
            Container.warehouse_id == warehouse_id,
            or_(func.lower(Container.name) == str(raw_code).casefold(),
                func.lower(Container.barcode) == str(raw_code).casefold()),
        ).limit(50)).scalars().all()
        rows = sorted(rows, key=lambda b: (str(getattr(b, "status", "") or "").upper() != "ACTIVE",
                                           getattr(b, "location_id", None) is None, str(b.id)))
        box = rows[0] if rows else None
    return box


def _manual_desired(db: Session, receipt: dict, client_id, warehouse_id):
    rows = [dict(x) for x in db.execute(text("""SELECT * FROM fc_r145f27_manual_lines
        WHERE receipt_id=:r AND quantity>0 AND product_found=true ORDER BY created_at,id"""),
        {"r": str(receipt["id"])}).mappings().all()]
    if not rows:
        raise HTTPException(409, detail="В серверном черновике нет найденного товара для проведения")
    unknown = int(db.execute(text("""SELECT COALESCE(SUM(quantity),0) FROM fc_r145f27_manual_lines
        WHERE receipt_id=:r AND quantity>0 AND product_found=false"""), {"r": str(receipt["id"])}).scalar() or 0)

    desired = defaultdict(Decimal)
    meta = {}
    reactivated = []
    sku_cache = {}
    for row in rows:
        barcode = _s(row.get("barcode"))
        if barcode not in sku_cache:
            skus = db.execute(select(SKU).where(SKU.client_id == client_id, SKU.barcode == barcode)).scalars().all()
            if len(skus) != 1:
                raise HTTPException(409, detail=f"ШК {barcode}: найдено SKU {len(skus)}. Приёмка не проведена.")
            sku_cache[barcode] = skus[0]
        sku = sku_cache[barcode]

        box = _manual_box(db, client_id, warehouse_id, row.get("container_id"), row.get("container_code")) if (row.get("container_id") or row.get("container_code")) else None
        if box:
            if not _normal_box(box):
                raise HTTPException(409, detail=f"Короб {_box_name(box)} относится к специальному контуру. Приёмка не проведена.")
            if str(getattr(box, "status", "") or "").upper() != "ACTIVE":
                box.status = "ACTIVE"
                if _box_name(box) not in reactivated:
                    reactivated.append(_box_name(box))
            loc_id = getattr(box, "location_id", None)
            if not loc_id:
                raise HTTPException(409, detail=f"У короба {_box_name(box)} нет адреса хранения")
            loc = db.execute(select(Location).where(Location.id == loc_id, Location.warehouse_id == warehouse_id,
                                                    Location.is_active.is_(True))).scalar_one_or_none()
            if not loc:
                raise HTTPException(409, detail=f"Адрес короба {_box_name(box)} не найден или отключён")
            assert_place(db, client_id, warehouse_id, loc.id)
            box_id = box.id
            container_code = str(box.barcode or box.name or row.get("container_code") or "")
        else:
            raw_loc = _s(row.get("location_code"))
            loc = db.execute(select(Location).where(
                Location.warehouse_id == warehouse_id, Location.is_active.is_(True),
                or_(Location.code == raw_loc, Location.barcode == raw_loc),
            )).scalars().first()
            if not loc:
                raise HTTPException(409, detail=f"Место {raw_loc} не найдено. Приёмка не проведена.")
            assert_place(db, client_id, warehouse_id, loc.id)
            box_id = None
            container_code = ""
        key = (str(sku.id), str(loc.id), str(box_id or ""))
        desired[key] += _dec(row.get("quantity"))
        meta[key] = {
            "barcode": barcode,
            "location_code": str(loc.code),
            "container_code": container_code or None,
        }
    db.flush()
    return dict(desired), meta, rows, unknown, reactivated


class ManualCommit35(BaseModel):
    client_id: uuid.UUID
    warehouse_id: uuid.UUID
    worker: str = Field(min_length=1, max_length=255)
    reference: str | None = Field(default=None, max_length=255)
    performed_by: str | None = Field(default=None, max_length=255)
    wb_warehouse_id: int | None = None
    stock_class: str = "MAIN"


@router.post("/r145f35/manual-drafts/{rid}/commit-verified")
def manual_commit_verified(rid: uuid.UUID, payload: ManualCommit35, request: Request, db: Session = Depends(get_db)):
    scope(db, payload.client_id, payload.warehouse_id)
    fast.install_schema(db)
    if db.bind.dialect.name == "postgresql":
        db.execute(text("SELECT pg_advisory_xact_lock(hashtext(:k))"), {"k": f"fc:r145f35:manual:{rid}"})
    receipt = fast._receipt(db, rid, payload.client_id, payload.warehouse_id, True)
    if _s(receipt.get("worker")).casefold() != _s(payload.worker).casefold():
        raise HTTPException(409, detail="Эта приёмка принадлежит другому приёмщику")
    status = str(receipt.get("status") or "").upper()
    if status not in {"DRAFT", "POSTING", "COMPLETED"}:
        raise HTTPException(409, detail=f"Приёмка имеет статус {status or 'UNKNOWN'} и не может быть проведена")

    desired, meta, draft_rows, unknown_units, reactivated = _manual_desired(
        db, receipt, payload.client_id, payload.warehouse_id
    )
    expected_units = _group_units(desired)
    exact = f"R145F27:MANUAL:{rid}:known"
    repair_like = f"R145F35:MANUAL:{rid}:known:%"
    actual, opids = _posted_groups(db, payload.client_id, payload.warehouse_id, exact, repair_like)
    missing, excess = _compare(desired, actual)
    if excess:
        raise HTTPException(409, detail={
            "code": "POSTED_EXCEEDS_DRAFT",
            "message": "В уже проведённых операциях товара больше, чем в серверном черновике. Автоматическое списание заблокировано.",
            "expected_units": expected_units, "posted_units": _group_units(actual),
            "examples": _format_excess(excess, meta),
        })

    repaired_units = 0
    last_result = None
    if missing:
        repaired_units = _group_units(missing)
        delta_lines = []
        for key in sorted(missing):
            m = meta.get(key)
            if not m:
                raise HTTPException(409, detail="Не удалось сопоставить недостающую строку с черновиком")
            delta_lines.append({
                "barcode": m["barcode"], "quantity": int(missing[key]),
                "location_code": m["location_code"], "container_code": m["container_code"],
            })
        signature = _signature(missing)
        idem = f"R145F35:MANUAL:{rid}:known:{signature}"
        body = {
            "client_id": str(payload.client_id), "warehouse_id": str(payload.warehouse_id),
            "idempotency_key": idem,
            "performed_by": payload.performed_by or payload.worker or fast.actor(request),
            "reference": payload.reference or receipt.get("reference") or None,
            "wb_warehouse_id": payload.wb_warehouse_id,
            "internal_only": False,
            "stock_class": payload.stock_class if payload.stock_class in ("MAIN", "FBS") else "MAIN",
            "lines": delta_lines,
        }
        try:
            last_result = receive_manual(body, request, db)
        except Exception:
            db.rollback()
            # Keep/reopen the draft for a safe retry.  Existing stock operations are
            # not undone here; the next retry recomputes the exact remaining delta.
            db.execute(text("UPDATE fc_r145f27_manual_receipts SET status='DRAFT',updated_at=now() WHERE id=:id"), {"id": str(rid)})
            db.commit()
            raise

    actual2, opids2 = _posted_groups(db, payload.client_id, payload.warehouse_id, exact, repair_like)
    missing2, excess2 = _compare(desired, actual2)
    if missing2 or excess2:
        db.execute(text("UPDATE fc_r145f27_manual_receipts SET status='DRAFT',updated_at=now() WHERE id=:id"), {"id": str(rid)})
        db.commit()
        raise HTTPException(409, detail={
            "code": "FINAL_PARITY_FAILED",
            "message": "Проведение остановлено: черновик и складская операция не совпали. Приёмка оставлена открытой.",
            "expected_units": expected_units, "posted_units": _group_units(actual2),
            "missing_units": _group_units(missing2), "excess_units": _group_units(excess2),
        })

    # Exact equality proven.  Only now mark draft rows posted/complete.
    ids = [str(x["id"]) for x in draft_rows]
    if ids:
        from sqlalchemy import bindparam
        stmt = text("UPDATE fc_r145f27_manual_lines SET posted_at=COALESCE(posted_at,now()),updated_at=now() WHERE id IN :ids").bindparams(bindparam("ids", expanding=True))
        db.execute(stmt, {"ids": ids})
    remaining = int(db.execute(text("SELECT count(*) FROM fc_r145f27_manual_lines WHERE receipt_id=:r AND quantity>0 AND posted_at IS NULL"), {"r": str(rid)}).scalar() or 0)
    all_ops = []
    for x in opids + opids2:
        if x and x not in all_ops:
            all_ops.append(x)
    completed = remaining == 0
    db.execute(text("""UPDATE fc_r145f27_manual_receipts
        SET status=:s,operation_ids=:ops,reference=:ref,updated_at=now(),
            completed_at=CASE WHEN :s='COMPLETED' THEN now() ELSE completed_at END
        WHERE id=:id"""), {
            "s": "COMPLETED" if completed else "DRAFT", "ops": ",".join(all_ops),
            "ref": _s(payload.reference or receipt.get("reference")), "id": str(rid),
        })
    db.commit()

    next_receipt = None
    if completed:
        nr, _ = fast._worker_receipt(db, payload.client_id, payload.warehouse_id, payload.worker,
                                     fast.actor(request), reference="")
        next_receipt = fast._compact_snapshot(db, nr)
    else:
        next_receipt = fast._compact_snapshot(db, fast._receipt(db, rid, payload.client_id, payload.warehouse_id))

    return {
        "ok": True, "receipt_id": str(rid), "completed": completed,
        "expected_units": expected_units, "posted_units": _group_units(actual2),
        "repaired_units": repaired_units, "operation_ids": all_ops,
        "unknown_units_remaining": unknown_units, "reactivated_boxes": reactivated,
        "next_receipt": next_receipt, "result": last_result or {},
    }


class TsdCommit35(BaseModel):
    client_id: uuid.UUID
    warehouse_id: uuid.UUID
    worker: str = Field(min_length=1, max_length=255)
    receipt_id: uuid.UUID | None = None


def _tsd_receipt(db: Session, payload: TsdCommit35):
    tsd_legacy.install_schema(db)
    if payload.receipt_id:
        return db.execute(text("""SELECT * FROM fc_r145f19_tsd_receipts
            WHERE id=:id AND client_id=:c AND warehouse_id=:w AND lower(worker)=lower(:p) LIMIT 1"""),
            {"id": str(payload.receipt_id), "c": str(payload.client_id), "w": str(payload.warehouse_id), "p": payload.worker}).mappings().first()
    return tsd_legacy._active_receipt(db, payload.client_id, payload.warehouse_id, payload.worker)


def _tsd_desired(db: Session, receipt, client_id, warehouse_id):
    rows = [dict(x) for x in db.execute(text("""SELECT l.id line_id,l.sku_id,l.quantity,l.place_id,
        p.location_id receipt_location_id,p.container_id,p.target_code
        FROM fc_r145f19_tsd_lines l JOIN fc_r145f19_tsd_places p ON p.id=l.place_id
        WHERE l.receipt_id=:r ORDER BY l.scanned_at,l.id"""), {"r": str(receipt["id"])}).mappings().all()]
    if not rows:
        raise HTTPException(409, detail="В приёмке нет товара")
    desired = defaultdict(Decimal)
    meta = {}
    boxes = {}
    reactivated = []
    repaired_places = 0
    for row in rows:
        raw = row.get("container_id")
        if raw and str(raw) not in boxes:
            stmt = select(Container).where(Container.id == uuid.UUID(str(raw)))
            if db.bind.dialect.name == "postgresql": stmt = stmt.with_for_update()
            box = db.execute(stmt).scalar_one_or_none()
            if not box:
                raise HTTPException(409, detail=f"Короб из приёмки удалён: {raw}")
            if str(box.client_id) != str(client_id) or str(box.warehouse_id) != str(warehouse_id):
                raise HTTPException(409, detail=f"Короб {_box_name(box)} принадлежит другому клиенту или складу")
            if not _normal_box(box):
                raise HTTPException(409, detail=f"Короб {_box_name(box)} относится к специальному контуру")
            if str(getattr(box, "status", "") or "").upper() != "ACTIVE":
                box.status = "ACTIVE"; reactivated.append(_box_name(box))
            boxes[str(raw)] = box
    for row in rows:
        raw = row.get("container_id")
        if raw:
            box = boxes[str(raw)]
            loc_id = getattr(box, "location_id", None) or row.get("receipt_location_id")
        else:
            box = None; loc_id = row.get("receipt_location_id")
        if not loc_id:
            raise HTTPException(409, detail="У места приёмки нет адреса хранения")
        loc = db.execute(select(Location).where(Location.id == loc_id, Location.warehouse_id == warehouse_id,
                                                Location.is_active.is_(True))).scalar_one_or_none()
        if not loc:
            raise HTTPException(409, detail="Адрес приёмки не найден или отключён")
        assert_place(db, client_id, warehouse_id, loc.id)
        if str(row.get("receipt_location_id") or "") != str(loc.id):
            db.execute(text("UPDATE fc_r145f19_tsd_places SET location_id=:loc WHERE id=:id"),
                       {"loc": str(loc.id), "id": str(row["place_id"])})
            repaired_places += 1
        key = (str(row["sku_id"]), str(loc.id), str(getattr(box, "id", "") or ""))
        desired[key] += _dec(row.get("quantity"))
        meta[key] = {"sku_id": key[0], "location_id": key[1], "container_id": key[2]}
    db.flush()
    return dict(desired), meta, rows, reactivated, repaired_places


@router.post("/r145f35/tsd/receipts/complete-verified")
def tsd_complete_verified(payload: TsdCommit35, db: Session = Depends(get_db)):
    scope(db, payload.client_id, payload.warehouse_id)
    tsd_legacy.install_schema(db)
    receipt = _tsd_receipt(db, payload)
    if not receipt:
        raise HTTPException(404, detail="Черновик приёмки не найден")
    if db.bind.dialect.name == "postgresql":
        db.execute(text("SELECT pg_advisory_xact_lock(hashtext(:k))"), {"k": f"fc:r145f35:tsd:{receipt['id']}"})
        receipt = _tsd_receipt(db, payload)
    desired, meta, rows, reactivated, repaired_places = _tsd_desired(
        db, receipt, payload.client_id, payload.warehouse_id
    )
    expected_units = _group_units(desired)
    exact = f"R145F19:TSD:{receipt['id']}"
    repair_like = f"R145F35:TSD:{receipt['id']}:%"
    actual, opids = _posted_groups(db, payload.client_id, payload.warehouse_id, exact, repair_like)
    missing, excess = _compare(desired, actual)
    if excess:
        raise HTTPException(409, detail={
            "code": "POSTED_EXCEEDS_DRAFT", "message": "В истории уже больше товара, чем в TSD-черновике. Автоматическое списание заблокировано.",
            "expected_units": expected_units, "posted_units": _group_units(actual),
        })
    repaired_units = 0
    if missing:
        repaired_units = _group_units(missing)
        lines = []
        for key in sorted(missing):
            lines.append(tsd_legacy.StockLine(
                sku_id=uuid.UUID(key[0]), quantity=missing[key],
                destination_location_id=uuid.UUID(key[1]),
                destination_container_id=uuid.UUID(key[2]) if key[2] else None,
                reason=f"TSD_RECEIVING:{receipt['id']}",
            ))
        idem = f"R145F35:TSD:{receipt['id']}:{_signature(missing)}"
        tsd_legacy.execute_operation(db, tsd_legacy.OperationCreate(
            client_id=payload.client_id, warehouse_id=payload.warehouse_id,
            operation_type="RECEIVING", idempotency_key=idem,
            performed_by=payload.worker, reference=receipt.get("reference") or "",
            comment="Приёмка ТСД · сверенная дельта · MAIN", lines=lines,
        ), commit=False)
        for key, qty in missing.items():
            tsd_legacy.reconcile_stock_bucket(
                db, client_id=payload.client_id, warehouse_id=payload.warehouse_id,
                sku_id=uuid.UUID(key[0]), location_id=uuid.UUID(key[1]),
                container_id=uuid.UUID(key[2]) if key[2] else None,
                preferred_bucket="STORAGE_ONLY", lock=True,
            )
        db.flush()
    actual2, opids2 = _posted_groups(db, payload.client_id, payload.warehouse_id, exact, repair_like)
    missing2, excess2 = _compare(desired, actual2)
    if missing2 or excess2:
        db.rollback()
        raise HTTPException(409, detail={
            "code": "FINAL_PARITY_FAILED", "message": "TSD-приёмка не закрыта: черновик и проведённый склад не совпали.",
            "expected_units": expected_units, "posted_units": _group_units(actual2),
            "missing_units": _group_units(missing2), "excess_units": _group_units(excess2),
        })
    db.execute(text("UPDATE fc_r145f19_tsd_places SET status='CLOSED' WHERE receipt_id=:r AND status='OPEN'"), {"r": str(receipt["id"])})
    db.execute(text("UPDATE fc_r145f19_tsd_receipts SET status='COMPLETED',completed_at=COALESCE(completed_at,now()) WHERE id=:id"), {"id": str(receipt["id"])})
    db.commit()
    all_ops=[]
    for x in opids+opids2:
        if x and x not in all_ops: all_ops.append(x)
    return {
        "ok": True, "receipt_id": str(receipt["id"]), "reference": receipt.get("reference") or "",
        "units": expected_units, "expected_units": expected_units, "posted_units": _group_units(actual2),
        "repaired_units": repaired_units, "operation_ids": all_ops,
        "reactivated_boxes": reactivated, "repaired_places": repaired_places,
    }
