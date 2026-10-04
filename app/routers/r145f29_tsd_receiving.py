"""r145f29: remove one mistaken TSD scan from any box/place in the active receipt."""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.routers import r145f19_finance as legacy

router = APIRouter(tags=["r145f29"])


class RemoveOneScan(BaseModel):
    client_id: uuid.UUID
    warehouse_id: uuid.UUID
    worker: str = Field(min_length=1, max_length=255)
    line_id: uuid.UUID


@router.post("/r145f19/tsd/receipts/remove-one-v29")
def remove_one_scan(payload: RemoveOneScan, db: Session = Depends(get_db)):
    """Remove exactly one saved scan from any place in the current DRAFT receipt.

    TSD receiving stores one row per physical scan. The row must belong to the
    active receipt for this exact client + warehouse + worker. Warehouse stock is
    not touched because the receipt has not been completed yet.
    """
    legacy.scope(db, payload.client_id, payload.warehouse_id)
    legacy.install_schema(db)
    receipt = legacy._active_receipt(db, payload.client_id, payload.warehouse_id, payload.worker)
    if not receipt:
        raise HTTPException(404, detail="Активная приёмка не найдена")

    sql = """SELECT id,quantity,barcode,place_id FROM fc_r145f19_tsd_lines
             WHERE id=:id AND receipt_id=:r"""
    if db.bind.dialect.name == "postgresql":
        sql += " FOR UPDATE"
    line = db.execute(text(sql), {"id": str(payload.line_id), "r": str(receipt["id"])}).mappings().first()
    if not line:
        raise HTTPException(404, detail="Этот скан уже удалён или относится к другой приёмке")

    quantity = int(line.get("quantity") or 0)
    if quantity <= 0:
        raise HTTPException(409, detail="В выбранной строке уже нет товара")
    # Normal TSD rows are one row per scan. Keep the fallback safe for legacy rows
    # with quantity > 1: subtract one unit rather than deleting the whole row.
    if quantity == 1:
        db.execute(text("DELETE FROM fc_r145f19_tsd_lines WHERE id=:id"), {"id": str(payload.line_id)})
    else:
        db.execute(text("UPDATE fc_r145f19_tsd_lines SET quantity=quantity-1 WHERE id=:id"), {"id": str(payload.line_id)})
    db.commit()

    current = legacy._active_receipt(db, payload.client_id, payload.warehouse_id, payload.worker)
    return {
        "ok": True,
        "removed": 1,
        "line_id": str(payload.line_id),
        "receipt": legacy._receipt_public(db, current),
    }
