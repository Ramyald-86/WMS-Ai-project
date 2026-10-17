"""Z-Event façade (TZ §7.1) — append-only operational history.

Corrections are new events; never overwrite or delete prior rows.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

_SCHEMA_READY = False


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def install_z_event_schema(db: Session) -> None:
    global _SCHEMA_READY
    if _SCHEMA_READY:
        return
    db.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS z_events (
                event_id UUID PRIMARY KEY,
                tenant_id UUID NULL,
                warehouse_id UUID NULL,
                client_id UUID NULL,
                actor_type VARCHAR(32) NOT NULL DEFAULT 'employee',
                employee_id UUID NULL,
                device_id VARCHAR(120) NULL,
                workstation_id VARCHAR(120) NULL,
                event_type VARCHAR(80) NOT NULL,
                timestamp_server TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                task_id UUID NULL,
                order_id UUID NULL,
                shipment_id UUID NULL,
                trip_id UUID NULL,
                hu_id VARCHAR(120) NULL,
                sku_id UUID NULL,
                barcode VARCHAR(255) NULL,
                chz VARCHAR(255) NULL,
                before_state JSONB NOT NULL DEFAULT '{}'::jsonb,
                after_state JSONB NOT NULL DEFAULT '{}'::jsonb,
                reason TEXT NULL,
                source_system VARCHAR(64) NOT NULL DEFAULT 'WMS',
                correlation_id VARCHAR(120) NULL,
                idempotency_key VARCHAR(190) NULL,
                payload JSONB NOT NULL DEFAULT '{}'::jsonb
            )
            """
        )
    )
    db.execute(text("CREATE INDEX IF NOT EXISTS ix_z_events_ts ON z_events(timestamp_server DESC)"))
    db.execute(text("CREATE INDEX IF NOT EXISTS ix_z_events_type ON z_events(event_type)"))
    db.execute(text("CREATE INDEX IF NOT EXISTS ix_z_events_order ON z_events(order_id)"))
    db.execute(text("CREATE INDEX IF NOT EXISTS ix_z_events_hu ON z_events(hu_id)"))
    db.execute(
        text(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS uq_z_events_idempotency
            ON z_events(idempotency_key)
            WHERE idempotency_key IS NOT NULL
            """
        )
    )
    db.commit()
    _SCHEMA_READY = True


def emit_z_event(
    db: Session,
    *,
    event_type: str,
    tenant_id: str | uuid.UUID | None = None,
    warehouse_id: str | uuid.UUID | None = None,
    client_id: str | uuid.UUID | None = None,
    actor_type: str = "employee",
    employee_id: str | uuid.UUID | None = None,
    device_id: str | None = None,
    workstation_id: str | None = None,
    task_id: str | uuid.UUID | None = None,
    order_id: str | uuid.UUID | None = None,
    shipment_id: str | uuid.UUID | None = None,
    trip_id: str | uuid.UUID | None = None,
    hu_id: str | None = None,
    sku_id: str | uuid.UUID | None = None,
    barcode: str | None = None,
    chz: str | None = None,
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
    reason: str | None = None,
    source_system: str = "WMS",
    correlation_id: str | None = None,
    idempotency_key: str | None = None,
    payload: dict[str, Any] | None = None,
    commit: bool = False,
) -> dict[str, Any]:
    install_z_event_schema(db)
    if idempotency_key:
        existing = db.execute(
            text("SELECT event_id FROM z_events WHERE idempotency_key = :k LIMIT 1"),
            {"k": idempotency_key},
        ).scalar()
        if existing:
            return {"event_id": str(existing), "deduplicated": True}

    event_id = uuid.uuid4()
    row = {
        "event_id": str(event_id),
        "tenant_id": str(tenant_id) if tenant_id else None,
        "warehouse_id": str(warehouse_id) if warehouse_id else None,
        "client_id": str(client_id) if client_id else None,
        "actor_type": actor_type or "employee",
        "employee_id": str(employee_id) if employee_id else None,
        "device_id": device_id,
        "workstation_id": workstation_id,
        "event_type": str(event_type),
        "timestamp_server": _utcnow(),
        "task_id": str(task_id) if task_id else None,
        "order_id": str(order_id) if order_id else None,
        "shipment_id": str(shipment_id) if shipment_id else None,
        "trip_id": str(trip_id) if trip_id else None,
        "hu_id": hu_id,
        "sku_id": str(sku_id) if sku_id else None,
        "barcode": barcode,
        "chz": chz,
        "before_state": before or {},
        "after_state": after or {},
        "reason": reason,
        "source_system": source_system,
        "correlation_id": correlation_id,
        "idempotency_key": idempotency_key,
        "payload": payload or {},
    }
    db.execute(
        text(
            """
            INSERT INTO z_events (
                event_id, tenant_id, warehouse_id, client_id, actor_type, employee_id,
                device_id, workstation_id, event_type, timestamp_server, task_id, order_id,
                shipment_id, trip_id, hu_id, sku_id, barcode, chz, before_state, after_state,
                reason, source_system, correlation_id, idempotency_key, payload
            ) VALUES (
                CAST(:event_id AS uuid), CAST(:tenant_id AS uuid), CAST(:warehouse_id AS uuid),
                CAST(:client_id AS uuid), :actor_type, CAST(:employee_id AS uuid),
                :device_id, :workstation_id, :event_type, :timestamp_server,
                CAST(:task_id AS uuid), CAST(:order_id AS uuid), CAST(:shipment_id AS uuid),
                CAST(:trip_id AS uuid), :hu_id, CAST(:sku_id AS uuid), :barcode, :chz,
                CAST(:before_state AS jsonb), CAST(:after_state AS jsonb), :reason,
                :source_system, :correlation_id, :idempotency_key, CAST(:payload AS jsonb)
            )
            """
        ),
        {
            **row,
            "before_state": __import__("json").dumps(row["before_state"], ensure_ascii=False),
            "after_state": __import__("json").dumps(row["after_state"], ensure_ascii=False),
            "payload": __import__("json").dumps(row["payload"], ensure_ascii=False),
        },
    )
    if commit:
        db.commit()
    return {"event_id": str(event_id), "deduplicated": False, "event_type": event_type}


def list_z_events(
    db: Session,
    *,
    event_type: str | None = None,
    order_id: str | None = None,
    hu_id: str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    install_z_event_schema(db)
    limit = max(1, min(int(limit or 100), 500))
    clauses = ["1=1"]
    params: dict[str, Any] = {"limit": limit}
    if event_type:
        clauses.append("event_type = :event_type")
        params["event_type"] = event_type
    if order_id:
        clauses.append("order_id = CAST(:order_id AS uuid)")
        params["order_id"] = order_id
    if hu_id:
        clauses.append("hu_id = :hu_id")
        params["hu_id"] = hu_id
    rows = db.execute(
        text(
            f"""
            SELECT event_id, tenant_id, warehouse_id, client_id, actor_type, employee_id,
                   device_id, workstation_id, event_type, timestamp_server, task_id, order_id,
                   shipment_id, trip_id, hu_id, sku_id, barcode, chz, before_state, after_state,
                   reason, source_system, correlation_id, idempotency_key, payload
            FROM z_events
            WHERE {' AND '.join(clauses)}
            ORDER BY timestamp_server DESC
            LIMIT :limit
            """
        ),
        params,
    ).mappings().all()
    out = []
    for r in rows:
        item = dict(r)
        for key in ("event_id", "tenant_id", "warehouse_id", "client_id", "employee_id", "task_id", "order_id", "shipment_id", "trip_id", "sku_id"):
            if item.get(key) is not None:
                item[key] = str(item[key])
        if item.get("timestamp_server") is not None:
            item["timestamp_server"] = item["timestamp_server"].isoformat()
        out.append(item)
    return out
