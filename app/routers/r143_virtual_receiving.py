from __future__ import annotations

import uuid
from decimal import Decimal

from fastapi import APIRouter, Depends
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models import InventoryBalance, Location, SKU
from app.routers.warehouse_ui import scope
from app.services.product_identity import resolved_sku_identity

router = APIRouter(prefix="/warehouse-ui", tags=["Адресное хранение · виртуальная ПРИЕМКА"])


@router.get("/virtual-receiving-current")
def virtual_receiving_current(
    client_id: uuid.UUID,
    warehouse_id: uuid.UUID,
    db: Session = Depends(get_db),
):
    """Return the *current* loose stock that really waits for placement.

    The old detail screen mixed two different concepts:
      * loose stock in virtual ``ПРИЕМКА`` (really requires placement), and
      * stock already placed in a physical box whose current location happened to
        be ``ПРИЕМКА``.

    Reading the page also used to call the WB cancellation repair endpoint.  A read
    operation must never create/move stock, therefore this route is deliberately
    read-only and only reads indexed inventory buckets with ``container_id IS NULL``.
    """
    scope(db, client_id, warehouse_id)
    receiving = db.execute(
        select(Location).where(
            Location.warehouse_id == warehouse_id,
            or_(Location.code == "ПРИЕМКА", Location.barcode == "ПРИЕМКА"),
        )
    ).scalars().first()
    if not receiving:
        return {
            "location_id": None,
            "location_code": "ПРИЕМКА",
            "quantity": 0,
            "sku_count": 0,
            "items": [],
            "read_only": True,
            "containers_excluded": True,
        }

    rows = db.execute(
        select(InventoryBalance, SKU)
        .join(SKU, SKU.id == InventoryBalance.sku_id)
        .where(
            InventoryBalance.client_id == client_id,
            InventoryBalance.warehouse_id == warehouse_id,
            InventoryBalance.location_id == receiving.id,
            InventoryBalance.container_id.is_(None),
            InventoryBalance.quantity > 0,
            SKU.client_id == client_id,
        )
        .order_by(SKU.article, SKU.size, SKU.barcode)
    ).all()

    total = Decimal("0")
    items = []
    for balance, sku in rows:
        qty = Decimal(str(balance.quantity or 0))
        if qty <= 0:
            continue
        total += qty
        identity = resolved_sku_identity(db, client_id, sku)
        items.append(
            {
                "sku_id": str(sku.id),
                "product_id": identity.get("product_id"),
                "article": sku.article,
                "name": sku.name,
                "size": sku.size,
                "barcode": sku.barcode,
                "quantity": float(qty),
                "container_id": None,
                "container": None,
                "reason": "ПРИЕМКА · требуется размещение",
                "orders": [],
                "received_at": getattr(balance, "updated_at", None) or getattr(balance, "created_at", None),
            }
        )

    return {
        "location_id": str(receiving.id),
        "location_code": receiving.code,
        "quantity": float(total),
        "sku_count": len(items),
        "items": items,
        "read_only": True,
        "containers_excluded": True,
    }
