import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models import Warehouse

router = APIRouter(prefix="/r106", tags=["Справочники r106"])


@router.delete("/warehouses/{warehouse_id}")
def archive_warehouse(warehouse_id: uuid.UUID, db: Session = Depends(get_db)):
    """Hide an erroneous warehouse without destroying historical warehouse operations.

    FulfillCore deliberately uses a soft delete here. Historical inventory operations,
    receipts and shipment references keep their original warehouse_id, while ordinary
    selectors stop showing the archived warehouse.
    """
    warehouse = db.get(Warehouse, warehouse_id)
    if not warehouse:
        raise HTTPException(status_code=404, detail="Склад не найден")
    if not bool(getattr(warehouse, "is_active", True)):
        return {"ok": True, "warehouse_id": str(warehouse.id), "already_archived": True}
    active = db.execute(select(Warehouse).where(Warehouse.is_active.is_(True)).order_by(Warehouse.name)).scalars().all()
    if len(active) <= 1:
        raise HTTPException(status_code=409, detail="Нельзя удалить последний активный склад. Сначала создайте другой склад.")
    warehouse.is_active = False
    db.commit()
    return {"ok": True, "warehouse_id": str(warehouse.id), "name": warehouse.name, "archived": True}
