from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.core.crypto import decrypt_secret
from app.core.database import get_db
from app.models import InventoryBalance, MarketplaceCredential, MarketplaceOrder
from app.schemas.inventory import OperationCreate, StockLine
from app.services.inventory import execute_operation
from app.services.marketplaces import OzonClient, WBClient
from app.services.stock_core_v2 import ensure_stock_core_v2_schema, reconcile_stock_bucket
from app.services.stock_outbox_worker_v2 import process_stock_outbox_v2

router = APIRouter(prefix="/stock-core", tags=["Stock Core v2"])


class ManualLine(BaseModel):
    sku_id: uuid.UUID
    quantity: int = Field(ge=1)


class ManualOrderCreate(BaseModel):
    client_id: uuid.UUID
    warehouse_id: uuid.UUID
    created_at: datetime | None = None
    external_order_id: str | None = None
    lines: list[ManualLine]


class ManualReserveRequest(BaseModel):
    allow_fbs_fallback: bool = True


class OutboxRunRequest(BaseModel):
    limit: int = Field(default=50, ge=1, le=500)


@router.post("/manual/orders")
def create_manual_order(payload: ManualOrderCreate, db: Session = Depends(get_db)):
    ensure_stock_core_v2_schema(db)
    order_id = str(uuid.uuid4())
    created_at = payload.created_at or datetime.now(timezone.utc)
    db.execute(
        text(
            "INSERT INTO fc_orders_v2(id,client_id,warehouse_id,external_order_id,order_type,status,created_at) "
            "VALUES(:id,:client,:warehouse,:external,'MANUAL','NEW',:created)"
        ),
        {
            "id": order_id,
            "client": str(payload.client_id),
            "warehouse": str(payload.warehouse_id),
            "external": payload.external_order_id,
            "created": created_at,
        },
    )
    for line in payload.lines:
        db.execute(
            text("INSERT INTO fc_order_lines_v2(id,order_id,sku_id,quantity) VALUES(:id,:order_id,:sku,:quantity)"),
            {"id": str(uuid.uuid4()), "order_id": order_id, "sku": str(line.sku_id), "quantity": line.quantity},
        )
    db.commit()
    return {"ok": True, "order_id": order_id, "status": "NEW"}


@router.post("/manual/orders/{order_id}/reserve")
def reserve_manual(order_id: str, payload: ManualReserveRequest, db: Session = Depends(get_db)):
    ensure_stock_core_v2_schema(db)
    order = db.execute(
        text("SELECT * FROM fc_orders_v2 WHERE id=:id AND order_type='MANUAL' FOR UPDATE"),
        {"id": order_id},
    ).mappings().first()
    if not order:
        raise HTTPException(404, "Manual-заказ не найден")
    if order["status"] in ("RESERVED", "SCANNING", "READY_WRITE_OFF"):
        return {"ok": True, "idempotent": True, "status": order["status"]}

    lines = db.execute(
        text("SELECT * FROM fc_order_lines_v2 WHERE order_id=:id ORDER BY id FOR UPDATE"),
        {"id": order_id},
    ).mappings().all()
    for line in lines:
        need = Decimal(str(line["quantity"]))
        balances = db.execute(
            select(InventoryBalance)
            .where(
                InventoryBalance.client_id == uuid.UUID(order["client_id"]),
                InventoryBalance.warehouse_id == uuid.UUID(order["warehouse_id"]),
                InventoryBalance.sku_id == uuid.UUID(line["sku_id"]),
                InventoryBalance.quantity > 0,
            )
            .order_by(InventoryBalance.updated_at)
        ).scalars().all()
        priorities = ["STORAGE_ONLY", "FBS_AVAILABLE"] if payload.allow_fbs_fallback else ["STORAGE_ONLY"]
        for preferred in priorities:
            for balance in balances:
                if need <= 0:
                    break
                row = reconcile_stock_bucket(
                    db,
                    client_id=balance.client_id,
                    warehouse_id=balance.warehouse_id,
                    sku_id=balance.sku_id,
                    location_id=balance.location_id,
                    container_id=balance.container_id,
                    preferred_bucket=preferred,
                    lock=True,
                )
                column = "storage_only" if preferred == "STORAGE_ONLY" else "fbs_available"
                take = min(need, row[column])
                if take <= 0:
                    continue
                db.execute(
                    text(
                        f"UPDATE fc_stocks_v2 SET {column}={column}-:quantity,reserved=reserved+:quantity,"
                        "version=version+1,updated_at=CURRENT_TIMESTAMP "
                        "WHERE client_id=:client AND warehouse_id=:warehouse AND sku_id=:sku "
                        "AND location_id=:location AND container_key=:container"
                    ),
                    {
                        "quantity": take,
                        "client": str(balance.client_id),
                        "warehouse": str(balance.warehouse_id),
                        "sku": str(balance.sku_id),
                        "location": str(balance.location_id),
                        "container": str(balance.container_id) if balance.container_id else "",
                    },
                )
                db.execute(
                    text(
                        "INSERT INTO fc_stock_reservations("
                        "id,order_id,client_id,warehouse_id,sku_id,source_location_id,source_container_id,"
                        "quantity,status,note,source_bucket,stock_core_order_id) "
                        "VALUES(:id,:order_id,:client,:warehouse,:sku,:location,:container,:quantity,'ACTIVE',"
                        "'MANUAL_STOCK_CORE_V2',:source,:stock_core_order_id)"
                    ),
                    {
                        "id": str(uuid.uuid4()),
                        "order_id": order_id,
                        "client": order["client_id"],
                        "warehouse": order["warehouse_id"],
                        "sku": line["sku_id"],
                        "location": str(balance.location_id),
                        "container": str(balance.container_id) if balance.container_id else None,
                        "quantity": take,
                        "source": preferred,
                        "stock_core_order_id": order_id,
                    },
                )
                need -= take
            if need <= 0:
                break
        if need > 0:
            raise HTTPException(409, f"Недостаточно остатка SKU {line['sku_id']}: не хватает {need}")

    db.execute(text("UPDATE fc_orders_v2 SET status='RESERVED' WHERE id=:id"), {"id": order_id})
    db.commit()
    return {"ok": True, "status": "RESERVED"}


@router.post("/orders/{order_id}/scan-complete")
def scan_complete(order_id: str, db: Session = Depends(get_db)):
    ensure_stock_core_v2_schema(db)
    order = db.execute(text("SELECT * FROM fc_orders_v2 WHERE id=:id FOR UPDATE"), {"id": order_id}).mappings().first()
    if not order:
        raise HTTPException(404, "Заказ не найден")
    if order["order_type"] == "MANUAL":
        db.execute(
            text("UPDATE fc_orders_v2 SET status='READY_WRITE_OFF',scan_completed_at=CURRENT_TIMESTAMP WHERE id=:id"),
            {"id": order_id},
        )
        db.commit()
        return {"ok": True, "status": "READY_WRITE_OFF", "manual_button_required": True}

    db.execute(
        text("UPDATE fc_orders_v2 SET status='SCANNING',scan_completed_at=CURRENT_TIMESTAMP WHERE id=:id"),
        {"id": order_id},
    )
    db.commit()
    return {"ok": True, "status": "SCANNING", "automatic_write_off_on_marketplace_pack": True}


@router.post("/manual/orders/{order_id}/write-off")
def manual_write_off(order_id: str, db: Session = Depends(get_db)):
    ensure_stock_core_v2_schema(db)
    order = db.execute(
        text("SELECT * FROM fc_orders_v2 WHERE id=:id AND order_type='MANUAL' FOR UPDATE"),
        {"id": order_id},
    ).mappings().first()
    if not order:
        raise HTTPException(404, "Manual-заказ не найден")
    if order["status"] == "WRITTEN_OFF":
        return {"ok": True, "idempotent": True, "status": "WRITTEN_OFF"}
    if order["status"] != "READY_WRITE_OFF":
        raise HTTPException(409, "Сначала завершите сканирование Manual-заказа")

    reservations = db.execute(
        text("SELECT * FROM fc_stock_reservations WHERE stock_core_order_id=:id AND status='ACTIVE' FOR UPDATE"),
        {"id": order_id},
    ).mappings().all()
    if not reservations:
        raise HTTPException(409, "Нет активного резерва")

    for reservation in reservations:
        execute_operation(
            db,
            OperationCreate(
                client_id=uuid.UUID(order["client_id"]),
                warehouse_id=uuid.UUID(order["warehouse_id"]),
                operation_type="SHIPPING",
                idempotency_key=f"manual-v2-writeoff-{order_id}-{reservation['id']}",
                reference=order.get("external_order_id"),
                comment="Manual: ручная кнопка Списать с остатков",
                lines=[
                    StockLine(
                        sku_id=uuid.UUID(reservation["sku_id"]),
                        quantity=Decimal(str(reservation["quantity"])),
                        source_location_id=uuid.UUID(reservation["source_location_id"]),
                        source_container_id=uuid.UUID(reservation["source_container_id"]) if reservation.get("source_container_id") else None,
                        reason="MANUAL_STOCK_CORE_V2_WRITE_OFF",
                    )
                ],
            ),
        )
        db.execute(
            text(
                "UPDATE fc_stocks_v2 SET reserved=reserved-:quantity,physical_snapshot=GREATEST(0,physical_snapshot-:quantity),"
                "version=version+1 WHERE client_id=:client AND warehouse_id=:warehouse AND sku_id=:sku "
                "AND location_id=:location AND container_key=:container"
            ),
            {
                "quantity": reservation["quantity"],
                "client": order["client_id"],
                "warehouse": order["warehouse_id"],
                "sku": reservation["sku_id"],
                "location": reservation["source_location_id"],
                "container": reservation.get("source_container_id") or "",
            },
        )
        db.execute(
            text("UPDATE fc_stock_reservations SET status='CONSUMED',consumed_at=CURRENT_TIMESTAMP,updated_at=CURRENT_TIMESTAMP WHERE id=:id"),
            {"id": reservation["id"]},
        )

    db.execute(
        text("UPDATE fc_orders_v2 SET status='WRITTEN_OFF',written_off_at=CURRENT_TIMESTAMP WHERE id=:id"),
        {"id": order_id},
    )
    db.commit()
    return {"ok": True, "status": "WRITTEN_OFF"}


@router.post("/orders/{order_id}/undo-write-off")
def undo_writeoff(order_id: str, db: Session = Depends(get_db)):
    ensure_stock_core_v2_schema(db)
    order = db.execute(text("SELECT * FROM fc_orders_v2 WHERE id=:id FOR UPDATE"), {"id": order_id}).mappings().first()
    if not order:
        raise HTTPException(404, "Заказ не найден")
    reservations = db.execute(
        text("SELECT * FROM fc_stock_reservations WHERE stock_core_order_id=:id AND status IN ('ACTIVE','CONSUMED') FOR UPDATE"),
        {"id": order_id},
    ).mappings().all()
    if not reservations:
        raise HTTPException(409, "Нет резерва/списания для отмены")

    for reservation in reservations:
        source = str(reservation.get("source_bucket") or ("STORAGE_ONLY" if order["order_type"] == "MANUAL" else "FBS_AVAILABLE")).upper()
        target_column = "storage_only" if source == "STORAGE_ONLY" else "fbs_available"
        if str(reservation["status"]).upper() == "ACTIVE":
            db.execute(
                text(
                    f"UPDATE fc_stocks_v2 SET reserved=reserved-:quantity,{target_column}={target_column}+:quantity,version=version+1 "
                    "WHERE client_id=:client AND warehouse_id=:warehouse AND sku_id=:sku AND location_id=:location "
                    "AND container_key=:container AND reserved>=:quantity"
                ),
                {
                    "quantity": reservation["quantity"],
                    "client": order["client_id"],
                    "warehouse": order["warehouse_id"],
                    "sku": reservation["sku_id"],
                    "location": reservation["source_location_id"],
                    "container": reservation.get("source_container_id") or "",
                },
            )
        else:
            execute_operation(
                db,
                OperationCreate(
                    client_id=uuid.UUID(order["client_id"]),
                    warehouse_id=uuid.UUID(order["warehouse_id"]),
                    operation_type="ADJUSTMENT",
                    idempotency_key=f"v2-undo-{order_id}-{reservation['id']}",
                    reference=order.get("external_order_id"),
                    comment="Отмена списания Stock Core v2",
                    lines=[
                        StockLine(
                            sku_id=uuid.UUID(reservation["sku_id"]),
                            quantity=Decimal(str(reservation["quantity"])),
                            destination_location_id=uuid.UUID(reservation["source_location_id"]),
                            destination_container_id=uuid.UUID(reservation["source_container_id"]) if reservation.get("source_container_id") else None,
                            reason="STOCK_CORE_V2_UNDO_WRITE_OFF",
                        )
                    ],
                ),
            )
            reconcile_stock_bucket(
                db,
                client_id=uuid.UUID(order["client_id"]),
                warehouse_id=uuid.UUID(order["warehouse_id"]),
                sku_id=uuid.UUID(reservation["sku_id"]),
                location_id=uuid.UUID(reservation["source_location_id"]),
                container_id=uuid.UUID(reservation["source_container_id"]) if reservation.get("source_container_id") else None,
                preferred_bucket=source,
                lock=True,
            )
        db.execute(
            text("UPDATE fc_stock_reservations SET status='RESTORED',released_at=CURRENT_TIMESTAMP,updated_at=CURRENT_TIMESTAMP WHERE id=:id"),
            {"id": reservation["id"]},
        )

    db.execute(text("UPDATE fc_orders_v2 SET status='RESTORED' WHERE id=:id"), {"id": order_id})
    db.commit()
    return {"ok": True, "status": "RESTORED", "marketplace_sync_required": order["order_type"] != "MANUAL"}


@router.get("/picking-list")
def sla_picking_list(client_id: uuid.UUID, warehouse_id: uuid.UUID, db: Session = Depends(get_db)):
    ensure_stock_core_v2_schema(db)
    rows = db.execute(
        text(
            """
            SELECT o.*,
                   EXTRACT(EPOCH FROM (CURRENT_TIMESTAMP-o.created_at))/3600.0 AS age_hours,
                   CASE WHEN CURRENT_TIMESTAMP-o.created_at>=INTERVAL '300 hours' THEN TRUE ELSE FALSE END AS sla_critical
            FROM fc_orders_v2 o
            WHERE client_id=:client AND warehouse_id=:warehouse
              AND status IN ('NEW','RESERVED','SCANNING')
            ORDER BY
              CASE WHEN CURRENT_TIMESTAMP-o.created_at>=INTERVAL '300 hours' THEN 0 ELSE 1 END,
              o.created_at ASC,
              o.id ASC
            """
        ),
        {"client": str(client_id), "warehouse": str(warehouse_id)},
    ).mappings().all()
    return {"rows": [dict(row) for row in rows]}


@router.post("/outbox/process")
def process_outbox(payload: OutboxRunRequest, db: Session = Depends(get_db)):
    return {"ok": True, **process_stock_outbox_v2(db, limit=payload.limit)}
