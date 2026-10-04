from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models import MarketplaceOrder
from app.routers.marketplaces import (
    _assert_wb_work,
    _event,
    _logical_credential_ids,
    _wb,
    _wb_supply_add_confirmed,
)
from app.services.runtime_reliability import distributed_lock
from app.services.wb_supply_registry import registry_supply, set_wb_supply_error, upsert_wb_supply

router = APIRouter(prefix="/warehouse-ui", tags=["WB supply attach r105"])


class AttachSupplyRequest(BaseModel):
    credential_id: uuid.UUID
    warehouse_id: uuid.UUID
    supply_id: str
    wb_warehouse_id: int | None = None


@router.patch("/wb/orders/{local_order_id}/attach-supply")
def attach_order_to_supply(
    local_order_id: uuid.UUID,
    payload: AttachSupplyRequest,
    db: Session = Depends(get_db),
):
    """Attach one FulfillCore WB order to an active WB supply without changing warehouse stock.

    This endpoint exists separately from the legacy bulk endpoint because a physically packed
    order can still be missing ``supply_id``.  The local PACKED state must not be demoted back
    to NEW/PICKING merely because WB confirms supply membership.
    """
    supply_id = str(payload.supply_id or "").strip().upper()
    if not supply_id:
        raise HTTPException(status_code=400, detail="Выберите поставку WB")

    cred, client = _wb(db, payload.credential_id)
    logical_ids = set(_logical_credential_ids(db, cred))
    order = db.execute(
        select(MarketplaceOrder).where(
            MarketplaceOrder.id == local_order_id,
            MarketplaceOrder.provider == "WB",
        )
    ).scalar_one_or_none()
    if order is None or order.credential_id not in logical_ids:
        raise HTTPException(status_code=404, detail="Заказ WB не найден в выбранном кабинете")

    if order.supply_id:
        if str(order.supply_id) == supply_id:
            return {
                "ok": True,
                "already_attached": True,
                "supply_id": supply_id,
                "order_id": order.external_order_id,
                "local_status": order.local_status,
            }
        raise HTTPException(
            status_code=409,
            detail=f"Заказ уже привязан к другой поставке: {order.supply_id}",
        )

    local_status = str(order.local_status or "").upper()
    supplier_status = str(order.supplier_status or "").lower()
    if local_status in {"SENT", "CANCELLED"} or supplier_status in {"complete", "cancel", "cancelled"}:
        raise HTTPException(
            status_code=409,
            detail="Этот заказ уже отправлен/закрыт или отменён. Обычная привязка к активной поставке недоступна.",
        )
    if order.external_order_num is None:
        raise HTTPException(status_code=409, detail="У заказа нет числового orderId WB. Сначала обновите заказы")

    _assert_wb_work(cred, order, payload.warehouse_id)
    if payload.wb_warehouse_id is not None and order.wb_warehouse_id is not None:
        if int(payload.wb_warehouse_id) != int(order.wb_warehouse_id):
            raise HTTPException(status_code=409, detail="Поставка выбрана для другого склада WB")

    cached = registry_supply(db, client_id=cred.client_id, cabinet_name=cred.name, supply_id=supply_id)
    if cached and bool(cached.get("done")):
        raise HTTPException(status_code=409, detail="Поставка уже закрыта. Добавить заказ нельзя")
    cached_wb = (cached or {}).get("_fc_wb_warehouse_id")
    if cached_wb is not None and order.wb_warehouse_id is not None and int(cached_wb) != int(order.wb_warehouse_id):
        raise HTTPException(status_code=409, detail="Поставка относится к другому складу WB")

    with distributed_lock(db, f"wb-supply:{cred.client_id}:{supply_id}", blocking=True):
        linked, membership, error = _wb_supply_add_confirmed(client, supply_id, [int(order.external_order_num)])
        if not linked:
            set_wb_supply_error(
                db,
                client_id=cred.client_id,
                cabinet_name=cred.name,
                supply_id=supply_id,
                error=error,
            )
            db.commit()
            raise HTTPException(
                status_code=409,
                detail=f"WB не подтвердил добавление заказа в {supply_id}: {error}",
            )

        # WB changes supplierStatus to confirm when an order is added to a supply.
        # Keep FulfillCore's warehouse state (especially PACKED) exactly as it is.
        previous_local_status = order.local_status
        order.supply_id = supply_id
        order.supplier_status = "confirm"
        order.local_status = previous_local_status
        _event(db, order, "ATTACHED_TO_SUPPLY_FROM_FULFILLCORE", value=supply_id)
        upsert_wb_supply(
            db,
            client_id=cred.client_id,
            cabinet_name=cred.name,
            supply_id=supply_id,
            credential_id=cred.id,
            physical_warehouse_id=payload.warehouse_id,
            wb_warehouse_id=order.wb_warehouse_id,
            supply_name=(cached or {}).get("name") or supply_id,
            done=False,
            order_count=len(set(membership) or {int(order.external_order_num)}),
            source=(cached or {}).get("_fc_source") or "FULFILLCORE",
            last_error=None,
        )
        db.commit()

    return {
        "ok": True,
        "supply_id": supply_id,
        "order_id": order.external_order_id,
        "local_status": order.local_status,
        "wb_confirmed": True,
    }
