"""r145f33: safely finalize a TSD receiving draft even if a normal box became inactive.

The receipt lines are the source of truth for participation in the current DRAFT.
Only containers already referenced by saved receipt lines may be restored to ACTIVE.
No cross-client/cross-warehouse repair is allowed, and special-purpose boxes are blocked.
"""
from __future__ import annotations

from collections import defaultdict
from decimal import Decimal
import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models import Container, Location
from app.routers import r145f19_finance as legacy
from app.services.r145f25_places import assert_place, scope

router = APIRouter(tags=["r145f33"])


class TsdComplete33(BaseModel):
    client_id: uuid.UUID
    warehouse_id: uuid.UUID
    worker: str = Field(min_length=1, max_length=255)
    receipt_id: uuid.UUID | None = None


def _receipt_by_payload(db: Session, payload: TsdComplete33):
    legacy.install_schema(db)
    params = {"c": str(payload.client_id), "w": str(payload.warehouse_id), "p": payload.worker}
    if payload.receipt_id:
        params["id"] = str(payload.receipt_id)
        row = db.execute(text("""SELECT * FROM fc_r145f19_tsd_receipts
            WHERE id=:id AND client_id=:c AND warehouse_id=:w AND lower(worker)=lower(:p)
            LIMIT 1"""), params).mappings().first()
        return row
    return legacy._active_receipt(db, payload.client_id, payload.warehouse_id, payload.worker)


def _line_count(db: Session, receipt_id) -> int:
    return int(db.execute(text("SELECT COALESCE(SUM(quantity),0) FROM fc_r145f19_tsd_lines WHERE receipt_id=:r"),
                          {"r": str(receipt_id)}).scalar() or 0)


def _normal_box(box: Container) -> bool:
    ctype = str(getattr(box, "container_type", "") or "").upper()
    return ctype not in {"DEFECT_BOX", "TRANSIT_BOX", "FBO_BOX"}


def _display_box(box: Container) -> str:
    return str(getattr(box, "name", None) or getattr(box, "barcode", None) or getattr(box, "id", ""))


@router.post("/r145f33/tsd/receipts/complete")
def complete_receipt(payload: TsdComplete33, db: Session = Depends(get_db)):
    """Finalize exactly the saved TSD receipt.

    A normal box that is referenced by saved lines in this DRAFT may be restored
    to ACTIVE before the stock operation.  This fixes the legacy false blocker
    "box is not active" without bypassing client/warehouse/location guards.
    """
    scope(db, payload.client_id, payload.warehouse_id)
    legacy.install_schema(db)
    receipt = _receipt_by_payload(db, payload)
    if not receipt:
        raise HTTPException(404, detail="Черновик приёмки не найден")

    status = str(receipt.get("status") or "").upper()
    if status == "COMPLETED":
        return {
            "ok": True,
            "already_completed": True,
            "units": _line_count(db, receipt["id"]),
            "reference": receipt.get("reference") or "",
            "reactivated_boxes": [],
            "repaired_places": 0,
        }
    if status != "DRAFT":
        raise HTTPException(409, detail=f"Приёмка уже не является черновиком: {status or 'UNKNOWN'}")

    if db.bind.dialect.name == "postgresql":
        db.execute(text("SELECT pg_advisory_xact_lock(hashtext(:k))"), {
            "k": f"fc:r145f33:complete:{receipt['id']}"
        })
        # Re-read after the transaction lock in case another request finished it.
        receipt = _receipt_by_payload(db, payload)
        if not receipt:
            raise HTTPException(404, detail="Черновик приёмки уже закрыт")
        if str(receipt.get("status") or "").upper() == "COMPLETED":
            return {
                "ok": True,
                "already_completed": True,
                "units": _line_count(db, receipt["id"]),
                "reference": receipt.get("reference") or "",
                "reactivated_boxes": [],
                "repaired_places": 0,
            }

    rows = db.execute(text("""SELECT
            l.id AS line_id,l.sku_id,l.quantity,l.place_id,
            p.location_id AS receipt_location_id,p.container_id,p.target_code,p.status AS place_status
        FROM fc_r145f19_tsd_lines l
        JOIN fc_r145f19_tsd_places p ON p.id=l.place_id
        WHERE l.receipt_id=:r
        ORDER BY l.scanned_at,l.id"""), {"r": str(receipt["id"])}).mappings().all()
    if not rows:
        raise HTTPException(409, detail="В приёмке нет товара")

    box_ids = []
    for row in rows:
        raw = row.get("container_id")
        if raw and str(raw) not in {str(x) for x in box_ids}:
            box_ids.append(uuid.UUID(str(raw)))

    boxes: dict[str, Container] = {}
    reactivated: list[str] = []
    repaired_places = 0
    for bid in box_ids:
        stmt = select(Container).where(Container.id == bid)
        if db.bind.dialect.name == "postgresql":
            stmt = stmt.with_for_update()
        box = db.execute(stmt).scalar_one_or_none()
        if not box:
            raise HTTPException(409, detail=f"Короб из приёмки удалён из системы: {bid}. Остатки не изменены.")
        if str(box.client_id) != str(payload.client_id):
            raise HTTPException(409, detail=f"Короб {_display_box(box)} принадлежит другому клиенту. Остатки не изменены.")
        if str(box.warehouse_id) != str(payload.warehouse_id):
            raise HTTPException(409, detail=f"Короб {_display_box(box)} принадлежит другому складу. Остатки не изменены.")
        if not _normal_box(box):
            raise HTTPException(409, detail=f"Короб {_display_box(box)} относится к специальному контуру и не может быть проведён обычной приёмкой.")
        boxes[str(bid)] = box

    # Repair only boxes that are actually referenced by saved lines of this draft.
    for row in rows:
        raw_box = row.get("container_id")
        if not raw_box:
            continue
        box = boxes[str(raw_box)]
        old_status = str(getattr(box, "status", "") or "")
        if old_status.upper() != "ACTIVE":
            box.status = "ACTIVE"
            name = _display_box(box)
            if name not in reactivated:
                reactivated.append(name)

        # The box's current valid address wins over an older place snapshot.
        destination_location_id = getattr(box, "location_id", None) or row.get("receipt_location_id")
        if not destination_location_id:
            raise HTTPException(409, detail=f"У короба {_display_box(box)} нет адреса хранения. Остатки не изменены.")
        loc = db.execute(select(Location).where(
            Location.id == destination_location_id,
            Location.warehouse_id == payload.warehouse_id,
            Location.is_active.is_(True),
        )).scalar_one_or_none()
        if not loc:
            raise HTTPException(409, detail=f"Адрес короба {_display_box(box)} не найден или отключён. Остатки не изменены.")
        assert_place(db, payload.client_id, payload.warehouse_id, loc.id)
        if getattr(box, "location_id", None) is None:
            box.location_id = loc.id
        if str(row.get("receipt_location_id") or "") != str(loc.id):
            db.execute(text("UPDATE fc_r145f19_tsd_places SET location_id=:loc WHERE id=:id"),
                       {"loc": str(loc.id), "id": str(row["place_id"])})
            repaired_places += 1

    db.flush()

    # Re-read destinations after repairs and group the stock operation exactly like
    # the legacy finalizer, but target this exact receipt id.
    rows2 = db.execute(text("""SELECT l.sku_id,l.quantity,p.location_id,p.container_id
        FROM fc_r145f19_tsd_lines l
        JOIN fc_r145f19_tsd_places p ON p.id=l.place_id
        WHERE l.receipt_id=:r
        ORDER BY l.scanned_at,l.id"""), {"r": str(receipt["id"])}).mappings().all()
    grouped = defaultdict(Decimal)
    for row in rows2:
        grouped[(str(row["sku_id"]), str(row["location_id"]), str(row.get("container_id") or ""))] += Decimal(str(row.get("quantity") or 0))

    lines = []
    for (sku_id, loc_id, box_id), qty in grouped.items():
        lines.append(legacy.StockLine(
            sku_id=uuid.UUID(sku_id),
            quantity=qty,
            destination_location_id=uuid.UUID(loc_id),
            destination_container_id=uuid.UUID(box_id) if box_id else None,
            reason=f"TSD_RECEIVING:{receipt['id']}",
        ))

    op = legacy.execute_operation(db, legacy.OperationCreate(
        client_id=payload.client_id,
        warehouse_id=payload.warehouse_id,
        operation_type="RECEIVING",
        idempotency_key=f"R145F19:TSD:{receipt['id']}",
        performed_by=payload.worker,
        reference=receipt.get("reference") or "",
        comment="Приёмка ТСД · короб или ячейка · MAIN",
        lines=lines,
    ), commit=False)

    for (sku_id, loc_id, box_id), qty in grouped.items():
        legacy.reconcile_stock_bucket(
            db,
            client_id=payload.client_id,
            warehouse_id=payload.warehouse_id,
            sku_id=uuid.UUID(sku_id),
            location_id=uuid.UUID(loc_id),
            container_id=uuid.UUID(box_id) if box_id else None,
            preferred_bucket="STORAGE_ONLY",
            lock=True,
        )

    db.execute(text("UPDATE fc_r145f19_tsd_places SET status='CLOSED' WHERE receipt_id=:r AND status='OPEN'"),
               {"r": str(receipt["id"])})
    db.execute(text("UPDATE fc_r145f19_tsd_receipts SET status='COMPLETED',completed_at=now() WHERE id=:id"),
               {"id": str(receipt["id"])})
    db.commit()
    return {
        "ok": True,
        "operation_id": str(op.id),
        "units": sum(int(row.get("quantity") or 0) for row in rows2),
        "reference": receipt.get("reference") or "",
        "reactivated_boxes": reactivated,
        "repaired_places": repaired_places,
    }
