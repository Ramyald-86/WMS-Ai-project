from __future__ import annotations

import json
import time
import uuid
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.services.runtime_reliability import ensure_runtime_schema


def _cabinet_name(value: str | None) -> str:
    return str(value or "").strip()[:255]


def _id_text(value: Any) -> str | None:
    raw = str(value or "").strip()
    return raw or None


def _raw_json(value: dict[str, Any] | None) -> str | None:
    if not isinstance(value, dict):
        return None
    try:
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)
    except Exception:
        return None


def _decode_json(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if not value:
        return {}
    try:
        parsed = json.loads(str(value))
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        return {}


def upsert_wb_supply(
    db: Session,
    *,
    client_id: uuid.UUID | str,
    cabinet_name: str | None,
    supply_id: str,
    credential_id: uuid.UUID | str | None = None,
    physical_warehouse_id: uuid.UUID | str | None = None,
    wb_warehouse_id: int | None = None,
    supply_name: str | None = None,
    done: bool | None = None,
    order_count: int | None = None,
    source: str | None = None,
    raw_item: dict[str, Any] | None = None,
    last_error: str | None = None,
    live_sync: bool = False,
    commit: bool = False,
) -> None:
    """Persist a WB supply in the central FulfillCore DB.

    The registry is intentionally additive: it does not replace WB as the source of
    truth. It gives every browser the same supply list immediately and keeps the last
    known state available during a temporary WB/API failure.
    """
    ensure_runtime_schema(db)
    sid = str(supply_id or "").strip()
    if not sid:
        return
    now = int(time.time())
    raw = dict(raw_item or {})
    if supply_name is None:
        supply_name = raw.get("name")
    if done is None and "done" in raw:
        done = bool(raw.get("done"))
    created_raw = raw.get("createdAt") or raw.get("created_at")
    closed_raw = raw.get("closedAt") or raw.get("closed_at")
    db.execute(
        text(
            """
            INSERT INTO fc_wb_supply_registry(
                client_id, cabinet_name, supply_id, credential_id,
                physical_warehouse_id, wb_warehouse_id, supply_name, done,
                order_count, source, raw_json, last_error,
                created_at_text, closed_at_text, last_live_sync_epoch, updated_epoch
            ) VALUES (
                :client_id, :cabinet_name, :supply_id, :credential_id,
                :physical_warehouse_id, :wb_warehouse_id, :supply_name, :done,
                :order_count, :source, :raw_json, :last_error,
                :created_at_text, :closed_at_text, :last_live_sync_epoch, :updated_epoch
            )
            ON CONFLICT (client_id, cabinet_name, supply_id) DO UPDATE SET
                credential_id=COALESCE(EXCLUDED.credential_id, fc_wb_supply_registry.credential_id),
                physical_warehouse_id=COALESCE(EXCLUDED.physical_warehouse_id, fc_wb_supply_registry.physical_warehouse_id),
                wb_warehouse_id=COALESCE(EXCLUDED.wb_warehouse_id, fc_wb_supply_registry.wb_warehouse_id),
                supply_name=COALESCE(EXCLUDED.supply_name, fc_wb_supply_registry.supply_name),
                done=COALESCE(EXCLUDED.done, fc_wb_supply_registry.done),
                order_count=COALESCE(EXCLUDED.order_count, fc_wb_supply_registry.order_count),
                source=COALESCE(EXCLUDED.source, fc_wb_supply_registry.source),
                raw_json=COALESCE(EXCLUDED.raw_json, fc_wb_supply_registry.raw_json),
                last_error=EXCLUDED.last_error,
                created_at_text=COALESCE(EXCLUDED.created_at_text, fc_wb_supply_registry.created_at_text),
                closed_at_text=COALESCE(EXCLUDED.closed_at_text, fc_wb_supply_registry.closed_at_text),
                last_live_sync_epoch=CASE
                    WHEN EXCLUDED.last_live_sync_epoch > 0 THEN EXCLUDED.last_live_sync_epoch
                    ELSE fc_wb_supply_registry.last_live_sync_epoch
                END,
                updated_epoch=EXCLUDED.updated_epoch
            """
        ),
        {
            "client_id": str(client_id),
            "cabinet_name": _cabinet_name(cabinet_name),
            "supply_id": sid,
            "credential_id": _id_text(credential_id),
            "physical_warehouse_id": _id_text(physical_warehouse_id),
            "wb_warehouse_id": int(wb_warehouse_id) if wb_warehouse_id is not None else None,
            "supply_name": _id_text(supply_name),
            "done": bool(done) if done is not None else None,
            "order_count": max(0, int(order_count)) if order_count is not None else None,
            "source": _id_text(source),
            "raw_json": _raw_json(raw_item),
            "last_error": str(last_error)[:4000] if last_error else None,
            "created_at_text": _id_text(created_raw),
            "closed_at_text": _id_text(closed_raw),
            "last_live_sync_epoch": now if live_sync else 0,
            "updated_epoch": now,
        },
    )
    if commit:
        db.commit()


def set_wb_supply_error(
    db: Session,
    *,
    client_id: uuid.UUID | str,
    cabinet_name: str | None,
    supply_id: str,
    error: str | None,
    commit: bool = False,
) -> None:
    ensure_runtime_schema(db)
    db.execute(
        text(
            """
            UPDATE fc_wb_supply_registry
            SET last_error=:error, updated_epoch=:now
            WHERE client_id=:client_id AND cabinet_name=:cabinet_name AND supply_id=:supply_id
            """
        ),
        {
            "error": str(error)[:4000] if error else None,
            "now": int(time.time()),
            "client_id": str(client_id),
            "cabinet_name": _cabinet_name(cabinet_name),
            "supply_id": str(supply_id or "").strip(),
        },
    )
    if commit:
        db.commit()


def list_wb_supply_registry(
    db: Session,
    *,
    client_id: uuid.UUID | str,
    cabinet_name: str | None,
    physical_warehouse_id: uuid.UUID | str | None = None,
    wb_warehouse_ids: list[int] | set[int] | tuple[int, ...] | None = None,
    strict_wb_warehouse_ids: bool = False,
    include_closed: bool = False,
) -> list[dict[str, Any]]:
    ensure_runtime_schema(db)
    params: dict[str, Any] = {
        "client_id": str(client_id),
        "cabinet_name": _cabinet_name(cabinet_name),
    }
    where = ["client_id=:client_id", "cabinet_name=:cabinet_name"]
    if not include_closed:
        where.append("done=FALSE")
    # A registry row created by FulfillCore is scoped to a physical warehouse. Legacy
    # discovered rows may have NULL while membership is being recovered; do not let a
    # row explicitly assigned to another warehouse leak across warehouse workspaces.
    if physical_warehouse_id is not None:
        params["physical_warehouse_id"] = str(physical_warehouse_id)
        where.append("(physical_warehouse_id IS NULL OR physical_warehouse_id=:physical_warehouse_id)")
    rows = db.execute(
        text(
            f"""
            SELECT client_id, cabinet_name, supply_id, credential_id,
                   physical_warehouse_id, wb_warehouse_id, supply_name, done,
                   order_count, source, raw_json, last_error,
                   created_at_text, closed_at_text, last_live_sync_epoch, updated_epoch
            FROM fc_wb_supply_registry
            WHERE {' AND '.join(where)}
            ORDER BY done ASC, updated_epoch DESC, supply_id DESC
            """
        ),
        params,
    ).mappings().all()
    allowed_wb = {int(x) for x in (wb_warehouse_ids or [])}
    result: list[dict[str, Any]] = []
    for row in rows:
        wb_id = row.get("wb_warehouse_id")
        if allowed_wb:
            # When an operator explicitly selected one WB seller warehouse, rows with
            # an unknown warehouse must not leak into that view. In the broader
            # physical-warehouse view we keep the legacy permissive behaviour so old
            # registry rows can still be recovered and mapped by live membership.
            if strict_wb_warehouse_ids:
                if wb_id is None or int(wb_id) not in allowed_wb:
                    continue
            elif wb_id is not None and int(wb_id) not in allowed_wb:
                continue
        item = _decode_json(row.get("raw_json"))
        item["id"] = str(row.get("supply_id") or item.get("id") or "")
        if row.get("supply_name"):
            item["name"] = str(row.get("supply_name"))
        item["done"] = bool(row.get("done"))
        item["_fc_registry"] = True
        item["_fc_cached"] = True
        item["_fc_physical_warehouse_id"] = row.get("physical_warehouse_id")
        item["_fc_wb_warehouse_id"] = row.get("wb_warehouse_id")
        item["_fc_order_count"] = int(row.get("order_count") or 0)
        item["_fc_source"] = row.get("source") or "WB"
        item["_fc_last_error"] = row.get("last_error")
        item["_fc_last_live_sync_epoch"] = int(row.get("last_live_sync_epoch") or 0)
        item["_fc_updated_epoch"] = int(row.get("updated_epoch") or 0)
        if row.get("created_at_text") and not item.get("createdAt"):
            item["createdAt"] = row.get("created_at_text")
        if row.get("closed_at_text") and not item.get("closedAt"):
            item["closedAt"] = row.get("closed_at_text")
        result.append(item)
    return result


def registry_supply(
    db: Session,
    *,
    client_id: uuid.UUID | str,
    cabinet_name: str | None,
    supply_id: str,
) -> dict[str, Any] | None:
    rows = list_wb_supply_registry(
        db,
        client_id=client_id,
        cabinet_name=cabinet_name,
        include_closed=True,
    )
    sid = str(supply_id or "").strip()
    return next((row for row in rows if str(row.get("id") or "") == sid), None)
