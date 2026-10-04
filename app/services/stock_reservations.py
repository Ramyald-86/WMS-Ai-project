from __future__ import annotations

import threading
import uuid
from collections import defaultdict
from decimal import Decimal
from typing import Any, Iterable

from fastapi import HTTPException
from sqlalchemy import bindparam, func, or_, select, text
from sqlalchemy.orm import Session

from app.models import Container, InventoryBalance, Location, MarketplaceOrder, MarketplacePickingList, SKU
from app.schemas.inventory import OperationCreate, StockLine
from app.services.inventory import execute_operation
from app.services.runtime_reliability import SYSTEM_LOCATION_CODES, distributed_lock
from app.services.stock_core_v2 import (
    consume_marketplace_reservation, ensure_canonical_marketplace_order, ensure_stock_core_v2_schema,
    canonical_stock_identity_lock_key, canonical_stock_identity_sku_ids,
    effective_fbs_capacity_for_sku, enqueue_marketplace_stock, reconcile_stock_bucket,
    release_or_restore_marketplace_reservation, reserve_marketplace_unit,
)


EPSILON = Decimal("0.000001")
_ACTIVE_STATES = ("SALE_RESERVED", "ACTIVE", "CONSUMING", "CANCELLING", "PACKING_WRITE_OFF")
_SCHEMA_READY = False
_SCHEMA_GUARD = threading.Lock()


def _d(value: Any) -> Decimal:
    try:
        return Decimal(str(value or 0))
    except Exception:
        return Decimal(0)


def reservation_schema_ready() -> bool:
    return _SCHEMA_READY


def install_stock_reservation_schema(db: Session) -> None:
    """Install the additive reservation ledger used by sale-first FBS accounting.

    SALE_RESERVED has no physical address. ACTIVE is the same promise after a picking
    list binds it to one concrete location/container. Legacy rows remain untouched.
    """
    global _SCHEMA_READY
    if _SCHEMA_READY:
        return
    with _SCHEMA_GUARD:
        if _SCHEMA_READY:
            return
        statements = [
            """
            CREATE TABLE IF NOT EXISTS fc_stock_reservations (
                id VARCHAR(36) PRIMARY KEY,
                order_id VARCHAR(36) NOT NULL,
                external_order_id VARCHAR(64) NULL,
                picking_list_id VARCHAR(36) NULL,
                client_id VARCHAR(36) NOT NULL,
                warehouse_id VARCHAR(36) NOT NULL,
                sku_id VARCHAR(36) NOT NULL,
                source_location_id VARCHAR(36) NULL,
                source_container_id VARCHAR(36) NULL,
                quantity NUMERIC(18,3) NOT NULL DEFAULT 1,
                status VARCHAR(32) NOT NULL DEFAULT 'ACTIVE',
                reservation_mode VARCHAR(32) NOT NULL DEFAULT 'LEGACY',
                source_bucket VARCHAR(24) NULL,
                stock_core_order_id VARCHAR(36) NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
                consumed_at TIMESTAMPTZ NULL,
                receiving_at TIMESTAMPTZ NULL,
                released_at TIMESTAMPTZ NULL,
                note TEXT NULL
            )
            """,
            "ALTER TABLE fc_stock_reservations ALTER COLUMN source_location_id DROP NOT NULL",
            "ALTER TABLE fc_stock_reservations ADD COLUMN IF NOT EXISTS reservation_mode VARCHAR(32) NOT NULL DEFAULT 'LEGACY'",
            "ALTER TABLE fc_stock_reservations ADD COLUMN IF NOT EXISTS source_bucket VARCHAR(24) NULL",
            "ALTER TABLE fc_stock_reservations ADD COLUMN IF NOT EXISTS stock_core_order_id VARCHAR(36) NULL",
            "CREATE INDEX IF NOT EXISTS ix_fc_stock_reservations_bucket ON fc_stock_reservations(client_id, warehouse_id, sku_id, source_location_id, source_container_id, status)",
            "CREATE INDEX IF NOT EXISTS ix_fc_stock_reservations_order ON fc_stock_reservations(order_id, created_at)",
            "CREATE INDEX IF NOT EXISTS ix_fc_stock_reservations_receiving ON fc_stock_reservations(client_id, warehouse_id, sku_id, status, receiving_at)",
            "CREATE INDEX IF NOT EXISTS ix_fc_stock_reservations_sale_pool ON fc_stock_reservations(client_id, warehouse_id, sku_id, status) WHERE status='SALE_RESERVED'",
            "DROP INDEX IF EXISTS uq_fc_stock_reservations_live_order",
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_fc_stock_reservations_live_order ON fc_stock_reservations(order_id) WHERE status IN ('SALE_RESERVED','ACTIVE','CONSUMING','CANCELLING','PACKING_WRITE_OFF')",
        ]
        try:
            for statement in statements:
                db.execute(text(statement))
            db.commit()
            ensure_stock_core_v2_schema(db)
            _SCHEMA_READY = True
        except Exception:
            db.rollback()
            raise



def ensure_stock_reservation_schema(db: Session) -> None:
    if not _SCHEMA_READY:
        install_stock_reservation_schema(db)


def _order_keys(order: MarketplaceOrder) -> set[str]:
    keys = {str(order.external_order_id or "").strip()}
    if order.external_order_num is not None:
        keys.add(str(order.external_order_num).strip())
    return {x for x in keys if x}


def _reservation_query_for_orders(order_ids: Iterable[Any], statuses: Iterable[str] | None = None):
    ids = [str(x) for x in order_ids if x]
    stmt = text(
        """
        SELECT id, order_id, external_order_id, picking_list_id, client_id, warehouse_id,
               sku_id, source_location_id, source_container_id, quantity, status,
               reservation_mode, source_bucket, stock_core_order_id,
               created_at, updated_at, consumed_at, receiving_at, released_at, note
        FROM fc_stock_reservations
        WHERE order_id IN :order_ids
        """ + (" AND status IN :statuses" if statuses else "") + " ORDER BY created_at DESC"
    ).bindparams(bindparam("order_ids", expanding=True))
    params: dict[str, Any] = {"order_ids": ids}
    if statuses:
        vals = [str(x) for x in statuses]
        stmt = stmt.bindparams(bindparam("statuses", expanding=True))
        params["statuses"] = vals
    return stmt, params



def latest_reservations_for_orders(
    db: Session,
    order_ids: Iterable[Any],
    statuses: Iterable[str] | None = None,
) -> dict[str, dict[str, Any]]:
    ensure_stock_reservation_schema(db)
    ids = [str(x) for x in order_ids if x]
    if not ids:
        return {}
    stmt, params = _reservation_query_for_orders(ids, statuses)
    rows = db.execute(stmt, params).mappings().all()
    result: dict[str, dict[str, Any]] = {}
    for row in rows:
        key = str(row["order_id"])
        if key not in result:
            result[key] = dict(row)
    return result


def active_reserved_totals(
    db: Session,
    *,
    client_id: Any,
    warehouse_id: Any,
    sku_ids: Iterable[Any] | None = None,
) -> dict[tuple[str, str, str], Decimal]:
    """Return live reserved quantities keyed by (sku, location, container)."""
    ensure_stock_reservation_schema(db)
    # FC_G5A8K4_LEDGER_RESERVE_TRUTH
    sql = """
        SELECT r.sku_id, r.source_location_id, COALESCE(r.source_container_id, '') AS source_container_id,
               COALESCE(SUM(r.quantity), 0) AS quantity
        FROM fc_stock_reservations r
        WHERE r.client_id=:client_id AND r.warehouse_id=:warehouse_id
          AND r.status IN ('ACTIVE','CONSUMING','CANCELLING')
    """
    params: dict[str, Any] = {"client_id": str(client_id), "warehouse_id": str(warehouse_id)}
    ids = [str(x) for x in (sku_ids or []) if x]
    if ids:
        sql += " AND sku_id IN :sku_ids"
        stmt = text(sql + " GROUP BY sku_id, source_location_id, COALESCE(source_container_id, '')").bindparams(bindparam("sku_ids", expanding=True))
        params["sku_ids"] = ids
    else:
        stmt = text(sql + " GROUP BY sku_id, source_location_id, COALESCE(source_container_id, '')")
    result: dict[tuple[str, str, str], Decimal] = {}
    for row in db.execute(stmt, params).mappings().all():
        result[(str(row["sku_id"]), str(row["source_location_id"]), str(row["source_container_id"] or ""))] = _d(row["quantity"])
    return result


def active_reserved_for_bucket(
    db: Session,
    *,
    client_id: Any,
    warehouse_id: Any,
    sku_id: Any,
    location_id: Any,
    container_id: Any | None,
) -> Decimal:
    if not _SCHEMA_READY:
        return Decimal(0)
    row = db.execute(
        text(
            """
            SELECT COALESCE(SUM(r.quantity), 0)
            FROM fc_stock_reservations r
            WHERE r.client_id=:client_id AND r.warehouse_id=:warehouse_id AND r.sku_id=:sku_id
              AND r.source_location_id=:location_id
              AND COALESCE(r.source_container_id, '')=:container_id
              AND r.status='ACTIVE'
            """
        ),
        {
            "client_id": str(client_id),
            "warehouse_id": str(warehouse_id),
            "sku_id": str(sku_id),
            "location_id": str(location_id),
            "container_id": str(container_id) if container_id else "",
        },
    ).scalar_one()
    return _d(row)


def live_reserved_for_bucket(
    db: Session,
    *,
    client_id: Any,
    warehouse_id: Any,
    sku_id: Any,
    location_id: Any,
    container_id: Any | None,
) -> Decimal:
    """Count every live reservation for allocation safety.

    The before_flush warehouse guard deliberately uses ACTIVE-only via
    active_reserved_for_bucket(), because the reservation being packed/cancelled is
    moved to a transient state before its intentional stock operation. New picking
    lists, however, must keep treating those transient states as unavailable.
    """
    ensure_stock_reservation_schema(db)
    row = db.execute(
        text(
            """
            SELECT COALESCE(SUM(r.quantity), 0)
            FROM fc_stock_reservations r
            WHERE r.client_id=:client_id AND r.warehouse_id=:warehouse_id AND r.sku_id=:sku_id
              AND r.source_location_id=:location_id
              AND COALESCE(r.source_container_id, '')=:container_id
              AND r.status IN ('ACTIVE','CONSUMING','CANCELLING')
            """
        ),
        {
            "client_id": str(client_id),
            "warehouse_id": str(warehouse_id),
            "sku_id": str(sku_id),
            "location_id": str(location_id),
            "container_id": str(container_id) if container_id else "",
        },
    ).scalar_one()
    return _d(row)


def _find_allocation(order: MarketplaceOrder, rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    keys = _order_keys(order)
    for row in rows or []:
        row_ids = {str(x).strip() for x in (row.get("order_ids") or []) if str(x).strip()}
        if keys & row_ids:
            return row
    return None


def _resolve_allocation_ids(
    db: Session,
    *,
    warehouse_id: Any,
    client_id: Any,
    allocation: dict[str, Any],
) -> tuple[Any | None, Any | None]:
    location_id = allocation.get("location_id")
    container_id = allocation.get("container_id")
    if not location_id:
        code = str(allocation.get("location") or "").strip()
        if code:
            loc = db.execute(
                select(Location).where(
                    Location.warehouse_id == warehouse_id,
                    or_(Location.code == code, Location.barcode == code),
                )
            ).scalars().first()
            location_id = loc.id if loc else None
    if location_id and not container_id:
        code = str(allocation.get("container") or "").strip()
        if code:
            cont = db.execute(
                select(Container).where(
                    Container.client_id == client_id,
                    Container.warehouse_id == warehouse_id,
                    or_(Container.barcode == code, getattr(Container, "name", Container.barcode) == code),
                )
            ).scalars().first()
            container_id = cont.id if cont else None
    return location_id, container_id


def _bucket_balance(
    db: Session,
    *,
    client_id: Any,
    warehouse_id: Any,
    sku_id: Any,
    location_id: Any,
    container_id: Any | None,
    lock: bool = True,
) -> InventoryBalance | None:
    stmt = select(InventoryBalance).where(
        InventoryBalance.client_id == client_id,
        InventoryBalance.warehouse_id == warehouse_id,
        InventoryBalance.sku_id == sku_id,
        InventoryBalance.location_id == location_id,
        InventoryBalance.container_id == container_id,
        InventoryBalance.quantity > 0,
    )
    if lock:
        stmt = stmt.with_for_update()
    return db.execute(stmt).scalars().first()


def sale_reserved_total(
    db: Session,
    *,
    client_id: Any,
    warehouse_id: Any,
    sku_id: Any,
) -> Decimal:
    """Warehouse-level promises not yet bound to a picking address."""
    ensure_stock_reservation_schema(db)
    ids = canonical_stock_identity_sku_ids(db, client_id=client_id, sku_id=sku_id)
    stmt = text(
        """
        SELECT COALESCE(SUM(r.quantity),0)
        FROM fc_stock_reservations r
        WHERE r.client_id=:client AND r.warehouse_id=:warehouse AND r.sku_id IN :ids
          AND r.status='SALE_RESERVED'
        """
    ).bindparams(bindparam("ids", expanding=True))
    return _d(db.execute(stmt, {
        "client": str(client_id), "warehouse": str(warehouse_id), "ids": ids,
    }).scalar_one() or 0)


def _reconciled_fbs_total(
    db: Session,
    *,
    client_id: Any,
    warehouse_id: Any,
    sku_id: Any,
) -> Decimal:
    """Return canonical FBS capacity before SALE_RESERVED promises.

    g5a8f stops trusting the historical fc_stocks_v2.fbs_available/reserved counters
    for sales capacity. Physical stock, explicit STORAGE_ONLY and the live address-bound
    reservation ledger are the authoritative inputs. The caller subtracts the
    warehouse-level SALE_RESERVED pool exactly once.
    """
    return effective_fbs_capacity_for_sku(
        db, client_id=client_id, warehouse_id=warehouse_id, sku_id=sku_id
    )


def reserve_new_orders_sale_first(
    db: Session,
    *,
    orders: list[MarketplaceOrder],
    warehouse_id: Any,
) -> dict[str, int]:
    """Reserve sales capacity at order arrival without choosing a physical address.

    Oldest orders win. NEW and already-PICKING orders are both healed. The physical InventoryBalance is untouched. Marketplace available
    quantity is reduced through the durable stock outbox. A picking list later converts
    SALE_RESERVED -> ACTIVE and binds the same reservation row to a source bucket.
    """
    ensure_stock_reservation_schema(db)
    ensure_stock_core_v2_schema(db)
    candidates = [o for o in orders if o.local_sku_id and str(o.local_status or '').upper() in {'NEW', 'PICKING'}]
    if not candidates:
        return {"created": 0, "kept": 0, "already_consumed": 0, "shortage": 0}
    candidates.sort(key=lambda o: (o.external_created_at or o.created_at, str(o.id)))
    latest = latest_reservations_for_orders(db, [o.id for o in candidates])
    by_sku: dict[str, list[MarketplaceOrder]] = defaultdict(list)
    for order in candidates:
        by_sku[str(order.local_sku_id)].append(order)

    created = kept = consumed = shortage = 0
    affected: dict[str, MarketplaceOrder] = {}
    for sku_key, sku_orders in by_sku.items():
        sku_id = uuid.UUID(sku_key)
        identity_key = canonical_stock_identity_lock_key(
            db, client_id=sku_orders[0].client_id, sku_id=sku_id
        )
        with distributed_lock(db, f"sale-reserve:{sku_orders[0].client_id}:{warehouse_id}:{identity_key}", blocking=True):
            fbs_total = _reconciled_fbs_total(
                db, client_id=sku_orders[0].client_id, warehouse_id=warehouse_id, sku_id=sku_id
            )
            unallocated = sale_reserved_total(
                db, client_id=sku_orders[0].client_id, warehouse_id=warehouse_id, sku_id=sku_id
            )
            capacity = max(Decimal(0), fbs_total - unallocated)
            for order in sku_orders:
                previous = latest.get(str(order.id))
                if previous:
                    status = str(previous.get("status") or "").upper()
                    same_sku = str(previous.get("sku_id") or "") == str(order.local_sku_id or "")
                    if same_sku and status in _ACTIVE_STATES:
                        kept += 1
                        continue
                    if status == "CONSUMED":
                        consumed += 1
                        continue
                    if status in {"RELEASED", "RESTORED", "CANCELLED_TO_RECEIVING", "RETURNED_TO_RECEIVING"}:
                        previous = None
                    elif not same_sku and status in _ACTIVE_STATES:
                        release_order_reservation(db, order, note="SALE_RESERVE_STRICT_SKU_REPAIR")
                        previous = None
                    else:
                        kept += 1
                        continue
                if capacity < Decimal(1) - EPSILON:
                    shortage += 1
                    continue
                rid = str(uuid.uuid4())
                core_order_id = ensure_canonical_marketplace_order(db, order, warehouse_id, order.local_sku_id)
                db.execute(
                    text(
                        """
                        INSERT INTO fc_stock_reservations(
                            id,order_id,external_order_id,picking_list_id,client_id,warehouse_id,
                            sku_id,source_location_id,source_container_id,quantity,status,
                            reservation_mode,source_bucket,stock_core_order_id,note
                        ) VALUES(
                            :id,:order_id,:external,NULL,:client,:warehouse,
                            :sku,NULL,NULL,1,'SALE_RESERVED',
                            'SALE_FIRST_V1','FBS_AVAILABLE',:core,'SALE_RESERVED_ON_ORDER_ARRIVAL'
                        )
                        ON CONFLICT DO NOTHING
                        """
                    ),
                    {
                        "id": rid,
                        "order_id": str(order.id),
                        "external": str(order.external_order_num or order.external_order_id or "")[:64],
                        "client": str(order.client_id),
                        "warehouse": str(warehouse_id),
                        "sku": str(order.local_sku_id),
                        "core": core_order_id,
                    },
                )
                current = latest_reservations_for_orders(db, [order.id]).get(str(order.id))
                if not current or str(current.get("status") or "").upper() != "SALE_RESERVED":
                    kept += 1
                    continue
                db.execute(text("UPDATE fc_orders_v2 SET status='RESERVED' WHERE id=:id"), {"id": core_order_id})
                capacity -= Decimal(1)
                created += 1
                affected[sku_key] = order

    # One durable publication target per changed SKU. The outbox worker performs WB I/O
    # after this short inventory transaction commits.
    for sku_key, order in affected.items():
        sku = db.get(SKU, uuid.UUID(sku_key))
        if sku:
            enqueue_marketplace_stock(db, order=order, warehouse_id=warehouse_id, sku=sku, reason="SALE_RESERVE_ORDER_ARRIVAL")
    return {"created": created, "kept": kept, "already_consumed": consumed, "shortage": shortage}


def reserve_orders_from_picking_rows(
    db: Session,
    *,
    orders: list[MarketplaceOrder],
    picking_list: MarketplacePickingList,
    rows: list[dict[str, Any]],
    precomputed_bind_only: bool = False,
) -> dict[str, int]:
    """Bind picking addresses with a fast path for existing SALE_RESERVED rows.

    NEW WB orders are already protected from oversell at arrival by one warehouse-level
    SALE_RESERVED row.  Starting a picking list should not rebuild that promise or queue
    another WB stock publication.  The common case is therefore bound in batches:
    one physical-bucket write per cell/SKU and one executemany reservation update.

    Orders missing an arrival reserve keep the older direct-reserve fallback, preserving
    compatibility with historical/manual orders without weakening stock safety.
    """
    ensure_stock_reservation_schema(db)
    if not picking_list.warehouse_id:
        raise HTTPException(status_code=409, detail="Для резервирования нужен физический склад FulfillCore")

    # FC_K9R16_PRECOMPUTED_SALE_RESERVE_BIND_ONLY
    # The full picking plan was already computed read-only for the complete NEW scope.
    # Every normal NEW order already owns one warehouse-level SALE_RESERVED promise.
    # In cached commit batches we therefore do not lock/recount InventoryBalance or
    # fc_stocks_v2 again: doing so caused the first 75-order batch to wait behind hot
    # stock/reconcile writers.  Lock only the reservation rows themselves, validate the
    # precomputed destination ids, and bind that existing promise to its address.
    if precomputed_bind_only:
        latest = latest_reservations_for_orders(db, [str(o.id) for o in orders])
        allocation_by_key: dict[str, dict[str, Any]] = {}
        for row in rows or []:
            for value in row.get("order_ids") or []:
                allocation_by_key[str(value)] = row

        def allocation_for_fast(order: MarketplaceOrder) -> dict[str, Any] | None:
            for key in _order_keys(order):
                found = allocation_by_key.get(str(key))
                if found is not None:
                    return found
            return None

        bind_items: list[tuple[MarketplaceOrder, dict[str, Any], dict[str, Any]]] = []
        attach_existing: list[dict[str, Any]] = []
        consumed = kept = 0
        location_ids: set[uuid.UUID] = set()
        missing_reserve: list[str] = []
        for order in orders:
            previous = latest.get(str(order.id))
            status = str((previous or {}).get("status") or "").upper()
            if previous and status in {"ACTIVE", "CONSUMING", "PACKING_WRITE_OFF"}:
                attach_existing.append({"plist": str(picking_list.id), "id": str(previous["id"])})
                kept += 1
                continue
            if previous and status == "CONSUMED":
                consumed += 1
                continue
            if not previous or status != "SALE_RESERVED":
                missing_reserve.append(str(order.external_order_num or order.external_order_id or order.id))
                continue
            allocation = allocation_for_fast(order)
            if not allocation or str(allocation.get("status") or "").upper() != "OK":
                kept += 1
                continue
            location_id = allocation.get("location_id")
            container_id = allocation.get("container_id")
            try:
                location_uuid = uuid.UUID(str(location_id)) if location_id else None
            except Exception:
                location_uuid = None
            if not location_uuid or not order.local_sku_id:
                kept += 1
                continue
            location_ids.add(location_uuid)
            bind_items.append((order, previous, {
                "location": str(location_uuid),
                "container": str(container_id) if container_id else None,
                "sku": str(order.local_sku_id),
            }))

        # FC_K9R18_LEGACY_MISSING_RESERVE_RECOVERY_SAFE
        # A small number of historical/manual NEW orders may predate SALE_RESERVED-on-arrival.
        # Do not abort the whole 75-order batch.  Recover only the exceptional order from
        # the exact address already chosen by the read-only plan.  We lock that physical
        # bucket NOWAIT, verify one genuinely free unit against the live bound ledger, and
        # create the final ACTIVE reservation directly.  No WB call, no fc_stocks_v2 mass
        # rebuild, and no fake reserve when physical stock is absent.  Orders that cannot
        # be healed stay NEW and are returned as skipped/deficit while the rest of the batch
        # commits normally.
        recovery_candidates: list[tuple[MarketplaceOrder, dict[str, Any], dict[str, Any]]] = []
        skipped_order_ids: list[str] = []
        if missing_reserve:
            missing_set = set(missing_reserve)
            for order in orders:
                label = str(order.external_order_num or order.external_order_id or order.id)
                if label not in missing_set:
                    continue
                allocation = allocation_for_fast(order)
                if not allocation or str(allocation.get("status") or "").upper() != "OK" or not order.local_sku_id:
                    skipped_order_ids.append(str(order.id))
                    if allocation is not None:
                        allocation.update({"status":"НЕ ХВАТАЕТ","synthetic_shortage":True})
                    continue
                try:
                    location_uuid = uuid.UUID(str(allocation.get("location_id"))) if allocation.get("location_id") else None
                    container_uuid = uuid.UUID(str(allocation.get("container_id"))) if allocation.get("container_id") else None
                except Exception:
                    location_uuid = None
                    container_uuid = None
                if not location_uuid:
                    skipped_order_ids.append(str(order.id))
                    allocation.update({"status":"НЕ ХВАТАЕТ","synthetic_shortage":True})
                    continue
                location_ids.add(location_uuid)
                recovery_candidates.append((order, allocation, {
                    "location": str(location_uuid),
                    "container": str(container_uuid) if container_uuid else None,
                    "sku": str(order.local_sku_id),
                }))

        if location_ids:
            valid_locations = {str(x.id): x for x in db.execute(
                select(Location).where(
                    Location.id.in_(sorted(location_ids, key=str)),
                    Location.warehouse_id == picking_list.warehouse_id,
                )
            ).scalars().all()}
            for order, _previous, params in bind_items:
                loc = valid_locations.get(params["location"])
                code = str(loc.code or "").strip().upper() if loc else ""
                if not loc or (code in SYSTEM_LOCATION_CODES and code != "ПРИЕМКА"):
                    raise HTTPException(
                        status_code=409,
                        detail=f"Заказ WB №{order.external_order_num or order.external_order_id}: адрес сборки недоступен.",
                    )

        recovered_missing = 0
        if recovery_candidates:
            dialect = str(getattr(getattr(db.get_bind(), "dialect", None), "name", "") or "").lower()
            for order, allocation, params in recovery_candidates:
                # Transaction-scoped advisory lock serializes legacy healing per canonical SKU
                # without leaking a session lock back into SQLAlchemy's pool.
                if dialect == "postgresql":
                    lock_key = f"fulfillcore:picking-missing-reserve:{order.client_id}:{picking_list.warehouse_id}:{params['sku']}"
                    got = bool(db.execute(text("SELECT pg_try_advisory_xact_lock(hashtext(:key))"), {"key": lock_key}).scalar())
                    if not got:
                        raise HTTPException(409, detail="Один из старых резервов сейчас восстанавливается параллельно. Повторите через несколько секунд.")
                location_uuid = uuid.UUID(params["location"])
                container_uuid = uuid.UUID(params["container"]) if params.get("container") else None
                balance_stmt = select(InventoryBalance).where(
                    InventoryBalance.client_id == order.client_id,
                    InventoryBalance.warehouse_id == picking_list.warehouse_id,
                    InventoryBalance.sku_id == order.local_sku_id,
                    InventoryBalance.location_id == location_uuid,
                    InventoryBalance.container_id == container_uuid,
                    InventoryBalance.quantity > 0,
                )
                if dialect == "postgresql":
                    balance_stmt = balance_stmt.with_for_update(nowait=True)
                balance = db.execute(balance_stmt).scalars().first()
                physical = _d(balance.quantity if balance else 0)
                already_bound = live_reserved_for_bucket(
                    db, client_id=order.client_id, warehouse_id=picking_list.warehouse_id,
                    sku_id=order.local_sku_id, location_id=location_uuid, container_id=container_uuid,
                )
                if physical - already_bound < Decimal(1) - EPSILON:
                    skipped_order_ids.append(str(order.id))
                    allocation.update({
                        "status":"НЕ ХВАТАЕТ","synthetic_shortage":True,
                        "location":"","location_id":None,"container":"","container_id":None,
                        "available_quantity":0.0,"available":0.0,
                    })
                    continue
                rid = str(uuid.uuid4())
                result = db.execute(text(
                    """
                    INSERT INTO fc_stock_reservations(
                        id,order_id,external_order_id,picking_list_id,client_id,warehouse_id,sku_id,
                        source_location_id,source_container_id,quantity,status,reservation_mode,source_bucket,note
                    ) VALUES(
                        :id,:order_id,:external,:plist,:client,:warehouse,:sku,
                        :location,:container,1,'ACTIVE','SALE_FIRST_V1','FBS_AVAILABLE',
                        'LEGACY_MISSING_SALE_RESERVED_RECOVERED_R18'
                    )
                    ON CONFLICT DO NOTHING
                    """
                ), {
                    "id":rid,"order_id":str(order.id),
                    "external":str(order.external_order_num or order.external_order_id or "")[:64],
                    "plist":str(picking_list.id),"client":str(order.client_id),
                    "warehouse":str(picking_list.warehouse_id),"sku":str(order.local_sku_id),
                    "location":str(location_uuid),"container":str(container_uuid) if container_uuid else None,
                })
                current = latest_reservations_for_orders(db, [order.id]).get(str(order.id))
                if not current or str(current.get("status") or "").upper() not in {"ACTIVE","CONSUMING","PACKING_WRITE_OFF"}:
                    skipped_order_ids.append(str(order.id))
                    allocation.update({"status":"НЕ ХВАТАЕТ","synthetic_shortage":True})
                    continue
                recovered_missing += 1

        sale_ids = [str(previous["id"]) for _order, previous, _params in bind_items]
        locked_ids: set[str] = set()
        if sale_ids:
            lock_stmt = text(
                "SELECT id FROM fc_stock_reservations WHERE id IN :ids AND status='SALE_RESERVED' ORDER BY id FOR UPDATE NOWAIT"
            ).bindparams(bindparam("ids", expanding=True))
            locked_ids = {str(x) for x in db.execute(lock_stmt, {"ids": sale_ids}).scalars().all()}
            if locked_ids != set(sale_ids):
                raise HTTPException(status_code=409, detail="Резерв одного из заказов уже изменился. Повторите операцию.")

        bind_params = []
        for _order, previous, params in bind_items:
            bind_params.append({
                "plist": str(picking_list.id), "location": params["location"],
                "container": params["container"], "sku": params["sku"], "id": str(previous["id"]),
            })
        if bind_params:
            result = db.execute(text(
                """
                UPDATE fc_stock_reservations
                SET picking_list_id=:plist,source_location_id=:location,source_container_id=:container,
                    sku_id=:sku,status='ACTIVE',reservation_mode='SALE_FIRST_V1',
                    source_bucket='FBS_AVAILABLE',updated_at=CURRENT_TIMESTAMP,
                    note='SALE_RESERVE_ADDRESS_BOUND_R16_FAST'
                WHERE id=:id AND status='SALE_RESERVED'
                """
            ), bind_params)
            if result.rowcount not in (-1, len(bind_params)):
                raise HTTPException(status_code=409, detail="Не удалось быстро закрепить все резервы. Повторите операцию.")
        if attach_existing:
            db.execute(text(
                "UPDATE fc_stock_reservations SET picking_list_id=:plist,updated_at=CURRENT_TIMESTAMP WHERE id=:id"
            ), attach_existing)
        return {
            "created": recovered_missing, "kept": kept + len(bind_params),
            "already_consumed": consumed, "fast_bound": len(bind_params),
            "recovered_missing_sale_reserved": recovered_missing,
            "skipped_order_ids": sorted(set(skipped_order_ids)),
        }

    created = kept = consumed = fast_bound = 0
    latest = latest_reservations_for_orders(db, [str(o.id) for o in orders])
    capacity_cache: dict[str, Decimal] = {}

    def shortage(allocation: dict[str, Any] | None) -> None:
        if allocation is not None:
            allocation.update({
                "status": "НЕ ХВАТАЕТ", "location": "", "location_id": None,
                "location_name": "", "location_type": "", "container": "",
                "container_id": None, "container_barcode": "", "available_quantity": 0.0,
                "available": 0.0, "under_zero": False,
            })

    # O(orders + rows), not O(orders * rows).
    allocation_by_key: dict[str, dict[str, Any]] = {}
    for row in rows or []:
        for value in row.get("order_ids") or []:
            allocation_by_key[str(value)] = row

    def allocation_for(order: MarketplaceOrder) -> dict[str, Any] | None:
        for key in _order_keys(order):
            found = allocation_by_key.get(str(key))
            if found is not None:
                return found
        return None

    # Resolve location/container ids first. In current picking rows they are already ids,
    # so this normally performs no lookup. Locations are then validated in one batch.
    prepared: list[dict[str, Any]] = []
    location_keys: set[str] = set()
    for order in orders:
        previous = latest.get(str(order.id))
        previous_status = str((previous or {}).get("status") or "").upper()
        current_sku = str(order.local_sku_id or "")
        previous_sku = str((previous or {}).get("sku_id") or "")

        if previous and previous_status in {"ACTIVE", "CONSUMING", "PACKING_WRITE_OFF"}:
            if current_sku and previous_sku and previous_sku != current_sku:
                # Historical address-bound identity mismatch is exceptional. Preserve the
                # old safe repair path rather than migrating an already-bound reservation.
                release_order_reservation(db, order, note="STRICT_SKU_MISMATCH_REPAIR")
                previous = None
                previous_status = ""
            else:
                db.execute(
                    text("UPDATE fc_stock_reservations SET picking_list_id=:plist,updated_at=CURRENT_TIMESTAMP WHERE id=:id"),
                    {"plist": str(picking_list.id) if picking_list.id else None, "id": str(previous["id"])},
                )
                kept += 1
                continue
        if previous and previous_status == "CONSUMED":
            consumed += 1
            continue

        allocation = allocation_for(order)
        if not allocation or str(allocation.get("status") or "").upper() != "OK":
            kept += 1
            continue
        location_id, container_id = _resolve_allocation_ids(
            db,
            warehouse_id=picking_list.warehouse_id,
            client_id=order.client_id,
            allocation=allocation,
        )
        if not location_id or not order.local_sku_id:
            shortage(allocation)
            kept += 1
            continue
        location_keys.add(str(location_id))
        prepared.append({
            "order": order,
            "previous": previous,
            "previous_status": previous_status,
            "allocation": allocation,
            "location_id": location_id,
            "container_id": container_id,
        })

    location_map: dict[str, Location] = {}
    if location_keys:
        location_ids = []
        for raw in location_keys:
            try:
                location_ids.append(uuid.UUID(raw))
            except Exception:
                pass
        if location_ids:
            location_map = {str(x.id): x for x in db.execute(
                select(Location).where(Location.id.in_(location_ids))
            ).scalars().all()}

    usable: list[dict[str, Any]] = []
    for item in prepared:
        order = item["order"]
        location = location_map.get(str(item["location_id"]))
        location_code = str(location.code or "").strip().upper() if location else ""
        if not location or (location_code in SYSTEM_LOCATION_CODES and location_code != "ПРИЕМКА"):
            raise HTTPException(
                status_code=409,
                detail=f"Заказ WB №{order.external_order_num or order.external_order_id}: товар находится в недоступной системной зоне.",
            )
        usable.append(item)

    # FC_K9R9_SALE_RESERVE_BIND_ONLY
    # The order already owns SALE_RESERVED from arrival.  The picking transition must
    # only bind that promise to the precomputed physical address and flip it to ACTIVE.
    # Revalidate all affected physical buckets in a few batched queries, then update all
    # reservation rows in one executemany.  This removes the old per-cell stock-core
    # rebuild/lock loop that made large NEW -> PICKING requests hit Internal Server Error.
    sale_items = [x for x in usable if x["previous"] and x["previous_status"] == "SALE_RESERVED"]
    locked_sale_ids: set[str] = set()
    sale_ids = sorted({str(x["previous"]["id"]) for x in sale_items})
    if sale_ids:
        lock_stmt = text(
            "SELECT id FROM fc_stock_reservations WHERE id IN :ids AND status='SALE_RESERVED' ORDER BY id FOR UPDATE"
        ).bindparams(bindparam("ids", expanding=True))
        locked_sale_ids = {str(x) for x in db.execute(lock_stmt, {"ids": sale_ids}).scalars().all()}

    needed_by_bucket: dict[tuple[str, str, str, str, str], Decimal] = defaultdict(Decimal)
    bucket_for_reservation: dict[str, tuple[str, str, str, str, str]] = {}
    sample_by_bucket: dict[tuple[str, str, str, str, str], dict[str, Any]] = {}
    for item in sale_items:
        rid = str(item["previous"]["id"])
        if rid not in locked_sale_ids:
            raise HTTPException(
                status_code=409,
                detail="Резерв заказа изменился параллельно. Повторите сборочный лист через несколько секунд.",
            )
        order = item["order"]
        key = (
            str(order.client_id),
            str(picking_list.warehouse_id),
            str(order.local_sku_id),
            str(item["location_id"]),
            str(item["container_id"] or ""),
        )
        needed_by_bucket[key] += Decimal(1)
        bucket_for_reservation[rid] = key
        sample_by_bucket.setdefault(key, item)

    physical_by_bucket: dict[tuple[str, str, str, str, str], Decimal] = defaultdict(Decimal)
    storage_by_bucket: dict[tuple[str, str, str, str, str], Decimal] = {}
    bound_by_bucket: dict[tuple[str, str, str, str, str], Decimal] = defaultdict(Decimal)
    technical_existing: set[tuple[str, str, str, str, str]] = set()

    if needed_by_bucket:
        sku_uuids = sorted({uuid.UUID(key[2]) for key in needed_by_bucket}, key=str)
        location_uuids = sorted({uuid.UUID(key[3]) for key in needed_by_bucket}, key=str)

        # Lock physical rows once for the entire list.  This preserves atomic stock safety
        # without repeating the same lock/read work for every cell/SKU group.
        balance_rows = db.execute(
            select(InventoryBalance)
            .where(
                InventoryBalance.client_id == orders[0].client_id,
                InventoryBalance.warehouse_id == picking_list.warehouse_id,
                InventoryBalance.sku_id.in_(sku_uuids),
                InventoryBalance.location_id.in_(location_uuids),
            )
            .order_by(InventoryBalance.sku_id, InventoryBalance.location_id, InventoryBalance.container_id)
            .with_for_update()
        ).scalars().all()
        for balance in balance_rows:
            key = (
                str(balance.client_id), str(balance.warehouse_id), str(balance.sku_id),
                str(balance.location_id), str(balance.container_id or ""),
            )
            if key in needed_by_bucket:
                physical_by_bucket[key] += max(Decimal(0), _d(balance.quantity))

        ids = [str(x) for x in sku_uuids]
        locs = [str(x) for x in location_uuids]
        technical_stmt = text(
            """
            SELECT CAST(sku_id AS varchar) AS sku_id,CAST(location_id AS varchar) AS location_id,
                   COALESCE(container_key,'') AS container_key,COALESCE(storage_only,0) AS storage_only
            FROM fc_stocks_v2
            WHERE client_id=:client AND warehouse_id=:warehouse
              AND CAST(sku_id AS varchar) IN :ids AND CAST(location_id AS varchar) IN :locs
            ORDER BY CAST(sku_id AS varchar),CAST(location_id AS varchar),COALESCE(container_key,'')
            FOR UPDATE
            """
        ).bindparams(bindparam("ids", expanding=True), bindparam("locs", expanding=True))
        for row in db.execute(technical_stmt, {
            "client": str(orders[0].client_id), "warehouse": str(picking_list.warehouse_id),
            "ids": ids, "locs": locs,
        }).mappings():
            key = (
                str(orders[0].client_id), str(picking_list.warehouse_id), str(row["sku_id"]),
                str(row["location_id"]), str(row["container_key"] or ""),
            )
            if key in needed_by_bucket:
                technical_existing.add(key)
                storage_by_bucket[key] = max(Decimal(0), _d(row["storage_only"]))

        # Lock already address-bound reservation rows for these buckets before the
        # capacity check.  Counting PACKING_WRITE_OFF conservatively during the short
        # transaction is safer than letting a concurrent packing/cancel race oversubscribe.
        bound_stmt = text(
            """
            SELECT id,CAST(sku_id AS varchar) AS sku_id,CAST(source_location_id AS varchar) AS location_id,
                   COALESCE(source_container_id,'') AS container_key,quantity
            FROM fc_stock_reservations
            WHERE client_id=:client AND warehouse_id=:warehouse
              AND CAST(sku_id AS varchar) IN :ids AND CAST(source_location_id AS varchar) IN :locs
              AND source_location_id IS NOT NULL
              AND status IN ('ACTIVE','CONSUMING','CANCELLING','PACKING_WRITE_OFF')
            ORDER BY id FOR UPDATE
            """
        ).bindparams(bindparam("ids", expanding=True), bindparam("locs", expanding=True))
        for row in db.execute(bound_stmt, {
            "client": str(orders[0].client_id), "warehouse": str(picking_list.warehouse_id),
            "ids": ids, "locs": locs,
        }).mappings():
            key = (
                str(orders[0].client_id), str(picking_list.warehouse_id), str(row["sku_id"]),
                str(row["location_id"]), str(row["container_key"] or ""),
            )
            if key in needed_by_bucket:
                bound_by_bucket[key] += max(Decimal(0), _d(row["quantity"]))

        for key, needed in needed_by_bucket.items():
            physical = max(Decimal(0), physical_by_bucket.get(key, Decimal(0)))
            storage = min(physical, max(Decimal(0), storage_by_bucket.get(key, Decimal(0))))
            already_bound = max(Decimal(0), bound_by_bucket.get(key, Decimal(0)))
            free = max(Decimal(0), physical - storage - already_bound)
            if free + EPSILON < needed:
                item = sample_by_bucket[key]
                shortage(item.get("allocation"))
                order = item["order"]
                raise HTTPException(
                    status_code=409,
                    detail=(
                        f"Заказ WB №{order.external_order_num or order.external_order_id}: "
                        f"в выбранной ячейке нужно {needed}, свободно {free}. "
                        "Обновите сборочный лист — статус не изменён."
                    ),
                )

    fast_bound_ids: set[str] = set()
    alias_publish_by_sku: dict[str, MarketplaceOrder] = {}
    bind_params: list[dict[str, Any]] = []
    for item in sale_items:
        previous = item["previous"]
        order = item["order"]
        bind_params.append({
            "plist": str(picking_list.id) if picking_list.id else None,
            "location": str(item["location_id"]),
            "container": str(item["container_id"]) if item["container_id"] else None,
            "sku": str(order.local_sku_id),
            "id": str(previous["id"]),
        })
        fast_bound_ids.add(str(order.id))
        previous_sku = str((previous or {}).get("sku_id") or "")
        current_sku = str(order.local_sku_id or "")
        if previous_sku and current_sku and previous_sku != current_sku:
            alias_publish_by_sku.setdefault(current_sku, order)

    if bind_params:
        result = db.execute(
            text(
                """
                UPDATE fc_stock_reservations
                SET picking_list_id=:plist,source_location_id=:location,source_container_id=:container,
                    sku_id=:sku,status='ACTIVE',reservation_mode='SALE_FIRST_V1',
                    source_bucket='FBS_AVAILABLE',updated_at=CURRENT_TIMESTAMP,
                    note='SALE_RESERVE_ADDRESS_BOUND_V2'
                WHERE id=:id AND status='SALE_RESERVED'
                """
            ),
            bind_params,
        )
        if result.rowcount not in (-1, len(bind_params)):
            raise HTTPException(
                status_code=409,
                detail="Не удалось атомарно закрепить все резервы за ячейками. Повторите операцию.",
            )
        fast_bound += len(bind_params)
        kept += len(bind_params)

    # Keep the old fc_stocks_v2 counter only as a compatibility mirror for packing and
    # cancellation.  Canonical reserve truth remains fc_stock_reservations.  Mirror all
    # affected buckets in two executemany statements instead of rebuilding each bucket.
    update_mirror: list[dict[str, Any]] = []
    insert_mirror: list[dict[str, Any]] = []
    for key, needed in needed_by_bucket.items():
        client_key, warehouse_key, sku_key, location_key, container_key = key
        physical = max(Decimal(0), physical_by_bucket.get(key, Decimal(0)))
        storage = min(physical, max(Decimal(0), storage_by_bucket.get(key, Decimal(0))))
        reserved_after = max(Decimal(0), bound_by_bucket.get(key, Decimal(0))) + needed
        fbs_after = max(Decimal(0), physical - storage - reserved_after)
        params = {
            "client": client_key, "warehouse": warehouse_key, "sku": sku_key,
            "location": location_key, "container": container_key,
            "fbs": fbs_after, "storage": storage, "reserved": reserved_after,
            "physical": physical,
        }
        (update_mirror if key in technical_existing else insert_mirror).append(params)
    # Insert only missing mirrors first.  ON CONFLICT keeps a rare concurrent lazy
    # classifier from turning an otherwise valid picking transition into HTTP 500.
    if insert_mirror:
        db.execute(text(
            """
            INSERT INTO fc_stocks_v2(
                client_id,warehouse_id,sku_id,location_id,container_key,
                fbs_available,storage_only,reserved,physical_snapshot
            ) VALUES(
                :client,:warehouse,:sku,:location,:container,
                :fbs,:storage,:reserved,:physical
            )
            ON CONFLICT DO NOTHING
            """
        ), insert_mirror)
    all_mirror = update_mirror + insert_mirror
    if all_mirror:
        db.execute(text(
            """
            UPDATE fc_stocks_v2
            SET fbs_available=:fbs,storage_only=:storage,reserved=:reserved,
                physical_snapshot=:physical,version=version+1,updated_at=CURRENT_TIMESTAMP
            WHERE client_id=:client AND warehouse_id=:warehouse AND sku_id=:sku
              AND location_id=:location AND container_key=:container
            """
        ), all_mirror)

    # Alias migration changes which physical barcode carries this reservation.  Queue
    # one local outbox target per changed SKU; no WB network I/O occurs in this request.
    for sku_key, order in alias_publish_by_sku.items():
        try:
            sku = db.get(SKU, uuid.UUID(sku_key))
        except Exception:
            sku = None
        if sku:
            enqueue_marketplace_stock(
                db, order=order, warehouse_id=picking_list.warehouse_id, sku=sku,
                reason="PICKING_ALIAS_RESERVATION_MIGRATION",
            )

    # Legacy/direct fallback only for orders that truly have no arrival SALE_RESERVED.
    for item in usable:
        order = item["order"]
        if str(order.id) in fast_bound_ids:
            continue
        previous = item["previous"]
        previous_status = item["previous_status"]
        allocation = item["allocation"]
        location_id = item["location_id"]
        container_id = item["container_id"]

        balance = _bucket_balance(
            db,
            client_id=order.client_id,
            warehouse_id=picking_list.warehouse_id,
            sku_id=order.local_sku_id,
            location_id=location_id,
            container_id=container_id,
            lock=True,
        )
        physical = _d(balance.quantity if balance else 0)
        reserved = live_reserved_for_bucket(
            db,
            client_id=order.client_id,
            warehouse_id=picking_list.warehouse_id,
            sku_id=order.local_sku_id,
            location_id=location_id,
            container_id=container_id,
        )
        if physical - reserved < Decimal(1) - EPSILON:
            shortage(allocation)
            kept += 1
            continue

        sku_key = str(order.local_sku_id)
        if sku_key not in capacity_cache:
            fbs_total = _reconciled_fbs_total(
                db,
                client_id=order.client_id,
                warehouse_id=picking_list.warehouse_id,
                sku_id=order.local_sku_id,
            )
            capacity_cache[sku_key] = max(Decimal(0), fbs_total - sale_reserved_total(
                db,
                client_id=order.client_id,
                warehouse_id=picking_list.warehouse_id,
                sku_id=order.local_sku_id,
            ))
        if capacity_cache[sku_key] < Decimal(1) - EPSILON:
            shortage(allocation)
            kept += 1
            continue

        rid = str(uuid.uuid4())
        db.execute(
            text(
                """
                INSERT INTO fc_stock_reservations(
                    id,order_id,external_order_id,picking_list_id,client_id,warehouse_id,
                    sku_id,source_location_id,source_container_id,quantity,status,reservation_mode,note
                ) VALUES(
                    :id,:order_id,:external,:plist,:client,:warehouse,
                    :sku,:location,:container,1,'CONSUMING','SALE_FIRST_V1','PICKING_DIRECT_RESERVE_V1'
                )
                """
            ),
            {
                "id": rid,
                "order_id": str(order.id),
                "external": str(order.external_order_num or order.external_order_id or "")[:64],
                "plist": str(picking_list.id) if picking_list.id else None,
                "client": str(order.client_id),
                "warehouse": str(picking_list.warehouse_id),
                "sku": str(order.local_sku_id),
                "location": str(location_id),
                "container": str(container_id) if container_id else None,
            },
        )
        reserve_marketplace_unit(
            db,
            order=order,
            warehouse_id=picking_list.warehouse_id,
            sku_id=order.local_sku_id,
            location_id=location_id,
            container_id=container_id,
            reservation_id=rid,
        )
        capacity_cache[sku_key] -= Decimal(1)
        created += 1
        consumed += 1

    return {
        "created": created,
        "kept": kept,
        "already_consumed": consumed,
        "fast_bound": fast_bound,
    }


def _legacy_allocation_for_order(db: Session, order: MarketplaceOrder) -> tuple[MarketplacePickingList, dict[str, Any]] | None:
    if not order.last_picking_list_id or not order.local_sku_id:
        return None
    try:
        plist = db.get(MarketplacePickingList, uuid.UUID(str(order.last_picking_list_id)))
    except Exception:
        return None
    if not plist or not plist.warehouse_id:
        return None
    allocation = _find_allocation(order, list(plist.rows or []))
    if not allocation or str(allocation.get("status") or "").upper() != "OK":
        return None
    return plist, allocation


def ensure_legacy_reservation_for_order(db: Session, order: MarketplaceOrder) -> dict[str, Any] | None:
    """Lazily upgrade a 0.7.8/0.7.9.0 PICKING/PACKED order without changing stock."""
    ensure_stock_reservation_schema(db)
    latest = latest_reservations_for_orders(db, [order.id]).get(str(order.id))
    if latest and str(latest.get("status") or "").upper() in {"ACTIVE", "CONSUMED", "CANCELLED_TO_RECEIVING", "RETURNED_TO_RECEIVING"}:
        return latest
    legacy = _legacy_allocation_for_order(db, order)
    if not legacy:
        return latest
    plist, allocation = legacy
    location_id, container_id = _resolve_allocation_ids(
        db, warehouse_id=plist.warehouse_id, client_id=order.client_id, allocation=allocation
    )
    if not location_id:
        return latest
    location = db.get(Location, location_id)
    if not location or str(location.code or "").strip().upper() in SYSTEM_LOCATION_CODES:
        return latest
    balance = _bucket_balance(
        db,
        client_id=order.client_id,
        warehouse_id=plist.warehouse_id,
        sku_id=order.local_sku_id,
        location_id=location_id,
        container_id=container_id,
        lock=True,
    )
    physical = _d(balance.quantity if balance else 0)
    reserved = live_reserved_for_bucket(
        db,
        client_id=order.client_id,
        warehouse_id=plist.warehouse_id,
        sku_id=order.local_sku_id,
        location_id=location_id,
        container_id=container_id,
    )
    if physical - reserved < Decimal(1) - EPSILON:
        return latest
    rid = str(uuid.uuid4())
    with db.no_autoflush:
        db.execute(
            text(
                """
                INSERT INTO fc_stock_reservations(
                    id, order_id, external_order_id, picking_list_id, client_id, warehouse_id,
                    sku_id, source_location_id, source_container_id, quantity, status, note
                ) VALUES (
                    :id, :order_id, :external_order_id, :picking_list_id, :client_id, :warehouse_id,
                    :sku_id, :source_location_id, :source_container_id, 1, 'ACTIVE', 'LEGACY_PICKING_BACKFILL'
                )
                """
            ),
            {
                "id": rid,
                "order_id": str(order.id),
                "external_order_id": str(order.external_order_num or order.external_order_id or "")[:64],
                "picking_list_id": str(plist.id),
                "client_id": str(order.client_id),
                "warehouse_id": str(plist.warehouse_id),
                "sku_id": str(order.local_sku_id),
                "source_location_id": str(location_id),
                "source_container_id": str(container_id) if container_id else None,
            },
        )
    return latest_reservations_for_orders(db, [order.id], statuses=["ACTIVE"]).get(str(order.id))


def backfill_legacy_reservations(db: Session, limit: int = 5000) -> dict[str, int]:
    ensure_stock_reservation_schema(db)
    with distributed_lock(db, "stock-reservation-backfill", blocking=True):
        orders = db.execute(
            select(MarketplaceOrder)
            .where(
                MarketplaceOrder.provider == "WB",
                MarketplaceOrder.local_status.in_(["PICKING", "PACKED"]),
                MarketplaceOrder.last_picking_list_id.is_not(None),
                MarketplaceOrder.local_sku_id.is_not(None),
            )
            .order_by(MarketplaceOrder.created_at.asc())
            .limit(max(1, min(int(limit), 20000)))
        ).scalars().all()
        created = skipped = 0
        for order in orders:
            before = latest_reservations_for_orders(db, [order.id]).get(str(order.id))
            if before:
                skipped += 1
                continue
            result = ensure_legacy_reservation_for_order(db, order)
            created += int(bool(result and str(result.get("status") or "").upper() == "ACTIVE"))
            skipped += int(not result)
        db.commit()
        return {"created": created, "skipped": skipped, "checked": len(orders)}


def reservation_row_for_picking(db: Session, order: MarketplaceOrder) -> dict[str, Any] | None:
    """Return the immutable source row for an already picked/reserved order."""
    latest = latest_reservations_for_orders(db, [order.id]).get(str(order.id))
    if not latest:
        latest = ensure_legacy_reservation_for_order(db, order)
    if not latest or str(latest.get("status") or "").upper() not in {"ACTIVE", "CONSUMED"}:
        return None
    loc = db.get(Location, uuid.UUID(str(latest["source_location_id"]))) if latest.get("source_location_id") else None
    cont = None
    if latest.get("source_container_id"):
        try:
            cont = db.get(Container, uuid.UUID(str(latest["source_container_id"])))
        except Exception:
            cont = None
    sku = db.get(SKU, order.local_sku_id) if order.local_sku_id else None
    container_total_quantity = 0.0
    if cont is not None:
        container_total_quantity = float(db.execute(
            select(func.coalesce(func.sum(InventoryBalance.quantity), 0)).where(
                InventoryBalance.container_id == cont.id,
                InventoryBalance.quantity > 0,
            )
        ).scalar_one() or 0)
    return {
        "location": loc.code if loc else "",
        "location_id": str(loc.id) if loc else latest.get("source_location_id"),
        "location_name": loc.name if loc else "",
        "location_type": loc.location_type if loc else "",
        "container": (cont.name or cont.barcode) if cont else "",
        "container_id": str(cont.id) if cont else latest.get("source_container_id"),
        "container_barcode": cont.barcode if cont else "",
        "container_total_quantity": container_total_quantity,
        "article": order.article or (sku.article if sku else ""),
        "size": sku.size if sku else "",
        "barcode": (order.barcodes or [None])[0] or (sku.barcode if sku else ""),
        "name": sku.name if sku else "",
        "required_quantity": 1,
        "available_quantity": 1,
        "order_ids": [str(order.external_order_num or order.external_order_id)],
        "status": "OK",
        "under_zero": False,
        "quantity": 1,
        "required": 1,
        "available": 1,
        "reservation_status": str(latest.get("status") or ""),
    }



def _fifo_reassign_newer_reservation_to_older_order(db: Session, order: MarketplaceOrder) -> dict[str, Any] | None:
    """Repair reservation drift so the oldest physical order wins at packing.

    A later order may own the only ACTIVE unit because reservations are created by
    background synchronization/picking-list timing. If an older order for the exact
    same SKU is scanned first, warehouse FIFO must win: move one untouched reservation
    from a newer NEW/PICKING order to this older order. The physical source cell/container
    does not move and total reserved stock does not change.
    """
    if not order.local_sku_id or not order.last_picking_list_id:
        return None
    try:
        picking_id = uuid.UUID(str(order.last_picking_list_id))
        picking = db.get(MarketplacePickingList, picking_id)
    except Exception:
        picking = None
    if not picking or not getattr(picking, "warehouse_id", None):
        return None

    target_time = order.external_created_at or order.created_at
    with db.no_autoflush:
        candidates = db.execute(
            text(
                """
                SELECT id,order_id,external_order_id,picking_list_id,client_id,warehouse_id,
                       sku_id,source_location_id,source_container_id,quantity,status,created_at,
                       updated_at,consumed_at,receiving_at,released_at,note
                FROM fc_stock_reservations
                WHERE client_id=:client AND warehouse_id=:warehouse AND sku_id=:sku
                  AND status='ACTIVE' AND order_id<>:target
                ORDER BY created_at ASC
                FOR UPDATE
                """
            ),
            {
                "client": str(order.client_id),
                "warehouse": str(picking.warehouse_id),
                "sku": str(order.local_sku_id),
                "target": str(order.id),
            },
        ).mappings().all()

    for row in candidates:
        try:
            donor = db.get(MarketplaceOrder, uuid.UUID(str(row["order_id"])))
        except Exception:
            donor = None
        if not donor or donor.provider != "WB":
            continue
        if str(donor.local_status or "").upper() not in {"NEW", "PICKING"}:
            continue
        # Never take a unit from an order whose packing has already physically started.
        if donor.product_scanned_at or donor.chz_code or donor.packed_at:
            continue
        donor_time = donor.external_created_at or donor.created_at
        if target_time and donor_time and donor_time <= target_time:
            continue

        result = db.execute(
            text(
                """
                UPDATE fc_stock_reservations
                SET order_id=:target,external_order_id=:external,picking_list_id=:picking,
                    updated_at=CURRENT_TIMESTAMP,note='FIFO_REASSIGNED_TO_OLDER_ORDER'
                WHERE id=:id AND status='ACTIVE' AND order_id=:donor
                """
            ),
            {
                "target": str(order.id),
                "external": str(order.external_order_id or order.external_order_num or ""),
                "picking": str(order.last_picking_list_id),
                "id": str(row["id"]),
                "donor": str(donor.id),
            },
        )
        if result.rowcount:
            healed = dict(row)
            healed.update({
                "order_id": str(order.id),
                "external_order_id": str(order.external_order_id or order.external_order_num or ""),
                "picking_list_id": str(order.last_picking_list_id),
                "note": "FIFO_REASSIGNED_TO_OLDER_ORDER",
            })
            return healed
    return None

def _recover_missing_reservation_from_free_stock(db: Session, order: MarketplaceOrder) -> dict[str, Any] | None:
    """0.9.4.4: recreate a missing reservation from one truly free physical unit.

    This is used only at packing time for a real picking-list order. It never creates
    negative stock and never steals an ACTIVE/CONSUMING/CANCELLING unit. Real cells are
    preferred; virtual ПРИЕМКА is a last-resort source, matching the established 0.8.1 rule.
    """
    if not order.last_picking_list_id or not order.local_sku_id:
        return None
    try:
        plist = db.get(MarketplacePickingList, uuid.UUID(str(order.last_picking_list_id)))
    except Exception:
        plist = None
    if not plist or not plist.warehouse_id:
        return None

    lock_key = f"packing-reservation-recover:{order.client_id}:{plist.warehouse_id}:{order.local_sku_id}"
    with distributed_lock(db, lock_key, blocking=False) as acquired:
        if not acquired:
            return None
        rows = db.execute(
            select(InventoryBalance, Location)
            .join(Location, Location.id == InventoryBalance.location_id)
            .where(
                InventoryBalance.client_id == order.client_id,
                InventoryBalance.warehouse_id == plist.warehouse_id,
                InventoryBalance.sku_id == order.local_sku_id,
                InventoryBalance.quantity > 0,
            )
            .with_for_update(of=InventoryBalance, skip_locked=True)
        ).all()
        # Real storage first, ПРИЕМКА last, forbidden system zones never.
        rows.sort(key=lambda pair: 1 if str(pair[1].code or "").strip().upper() == "ПРИЕМКА" else 0)
        for balance, location in rows:
            code = str(location.code or "").strip().upper()
            if code in SYSTEM_LOCATION_CODES and code != "ПРИЕМКА":
                continue
            reserved = live_reserved_for_bucket(
                db,
                client_id=order.client_id,
                warehouse_id=plist.warehouse_id,
                sku_id=order.local_sku_id,
                location_id=balance.location_id,
                container_id=balance.container_id,
            )
            if _d(balance.quantity) - reserved < Decimal(1) - EPSILON:
                continue
            rid = str(uuid.uuid4())
            db.execute(
                text(
                    """
                    INSERT INTO fc_stock_reservations(
                        id,order_id,external_order_id,picking_list_id,client_id,warehouse_id,
                        sku_id,source_location_id,source_container_id,quantity,status,note
                    ) VALUES (
                        :id,:order_id,:external,:picking,:client,:warehouse,
                        :sku,:location,:container,1,'ACTIVE','PACKING_RECOVER_FREE_STOCK_0_9_4_4'
                    )
                    ON CONFLICT DO NOTHING
                    """
                ),
                {
                    "id": rid,
                    "order_id": str(order.id),
                    "external": str(order.external_order_num or order.external_order_id or "")[:64],
                    "picking": str(order.last_picking_list_id),
                    "client": str(order.client_id),
                    "warehouse": str(plist.warehouse_id),
                    "sku": str(order.local_sku_id),
                    "location": str(balance.location_id),
                    "container": str(balance.container_id) if balance.container_id else None,
                },
            )
            recovered = latest_reservations_for_orders(db, [order.id], statuses=["ACTIVE"]).get(str(order.id))
            if recovered:
                return recovered
    return None

def ensure_packing_reservation_for_scan(
    db: Session,
    order: MarketplaceOrder,
    warehouse_id: Any,
    *,
    eligible_credential_ids: Iterable[Any] | None = None,
) -> dict[str, Any] | None:
    """Return/heal an address-bound reservation only after a real picking list exists.

    SALE_FIRST_V1 has a strict warehouse boundary:
      NEW order -> SALE_RESERVED (warehouse-level, no address);
      real FulfillCore picking list -> ACTIVE (exact location/container);
      packing -> physical write-off.

    Packing scans are never allowed to invent a physical address for an order that has
    not crossed the picking-list boundary. This keeps cancellation semantics truthful:
    before a list we release in place; after a list we may move the picked unit to the
    virtual receiving zone.
    """
    ensure_stock_reservation_schema(db)
    if not order.local_sku_id or not warehouse_id:
        return None

    latest = latest_reservations_for_orders(
        db, [order.id], statuses=["SALE_RESERVED", "ACTIVE", "CONSUMING", "CONSUMED"]
    ).get(str(order.id))

    # No real list means no address allocation, by design. A SALE_RESERVED row is a
    # valid sale promise but not a packing allocation.
    if not order.last_picking_list_id:
        return latest

    if latest and str(latest.get("status") or "").upper() in {"ACTIVE", "CONSUMING", "CONSUMED"}:
        return latest

    # Genuine historical picking-list rows may predate Stock Core v2. Heal only from
    # the allocation that is already stored in that real list; never scan arbitrary
    # warehouse stock here.
    healed = ensure_legacy_reservation_for_order(db, order)
    if not healed:
        healed = _fifo_reassign_newer_reservation_to_older_order(db, order)
    if not healed:
        healed = _recover_missing_reservation_from_free_stock(db, order)
    return healed

def assert_order_reservation_ready_for_packing(db: Session, order: MarketplaceOrder) -> dict[str, Any] | None:
    """Validate a reservation only when this order belongs to a real picking list.

    A local/manual PICKING status is only a workflow bucket. It must never reserve,
    move or consume an address balance by itself. The physical-stock boundary starts
    only after FulfillCore has generated a picking list and stored its id on the order.
    """
    if not order.last_picking_list_id:
        return None
    latest = latest_reservations_for_orders(db, [order.id]).get(str(order.id))
    if not latest:
        latest = ensure_legacy_reservation_for_order(db, order)
    if not latest:
        # 0.9.4.3 PACKING FIFO STABILITY: shortage across several same-SKU orders
        # must never block the oldest order that the operator physically scans first.
        # Heal a newer untouched reservation onto this older order without changing
        # total physical/reserved stock. The newer order becomes the deficit instead.
        latest = _fifo_reassign_newer_reservation_to_older_order(db, order)
    if not latest:
        # 0.9.4.4: reservation rows can be missing after old picking-list/sync failures.
        # If one genuinely FREE exact-SKU unit exists, atomically bind it to this order
        # instead of blocking the packing table with “Резерв Stock Core v2 отсутствует”.
        latest = _recover_missing_reservation_from_free_stock(db, order)
    if not latest:
        # Manually toggled PICKING orders historically had no physical allocation.
        # Keep that legacy path working; only real picking-list allocations are strict.
        if _legacy_allocation_for_order(db, order):
            raise HTTPException(status_code=409, detail="Не удалось восстановить резерв этого заказа. Обновите остатки/сборочный лист до сканирования ЧЗ.")
        return None
    status = str(latest.get("status") or "").upper()
    if status == "CONSUMED":
        return latest
    if status == "CONSUMING":
        # Recover an interrupted previous packing request by replaying the same
        # idempotent inventory operation, then continue only after CONSUMED.
        consume_order_reservation(db, order, note="WB_PACKING_RECOVER_CONSUMING")
        latest = latest_reservations_for_orders(db, [order.id]).get(str(order.id)) or latest
        if str(latest.get("status") or "").upper() == "CONSUMED":
            return latest
        raise HTTPException(status_code=409, detail="Предыдущее списание резерва ещё не завершено. Повторите сканирование через несколько секунд.")
    if status != "ACTIVE":
        raise HTTPException(status_code=409, detail=f"Резерв заказа имеет состояние {status}. Обновите заказ перед упаковкой.")
    try:
        loc_id = uuid.UUID(str(latest["source_location_id"]))
        cont_id = uuid.UUID(str(latest["source_container_id"])) if latest.get("source_container_id") else None
    except Exception:
        raise HTTPException(status_code=409, detail="Повреждён адрес резерва заказа. Сформируйте сборочный лист заново.")
    balance = _bucket_balance(
        db,
        client_id=order.client_id,
        warehouse_id=uuid.UUID(str(latest["warehouse_id"])),
        sku_id=order.local_sku_id,
        location_id=loc_id,
        container_id=cont_id,
        lock=True,
    )
    if not balance or _d(balance.quantity) < Decimal(1) - EPSILON:
        # Compatibility with picking lists that already removed the unit from the
        # source balance (or an operator completed the pick before this upgrade).
        # The packer has just scanned the physical product, so subtracting again is
        # wrong. Reconcile the reservation as already consumed and continue packing.
        with db.no_autoflush:
            db.execute(
                text("UPDATE fc_stock_reservations SET status='CONSUMED', consumed_at=COALESCE(consumed_at,CURRENT_TIMESTAMP), updated_at=CURRENT_TIMESTAMP, note='PACKING_RECONCILED_ALREADY_OUT_OF_SOURCE' WHERE id=:id AND status='ACTIVE'"),
                {"id": str(latest["id"])},
            )
        latest = dict(latest)
        latest["status"] = "CONSUMED"
        latest["note"] = "PACKING_RECONCILED_ALREADY_OUT_OF_SOURCE"
        return latest
    return latest


def _write_off_unallocated_physical_unit(
    db: Session,
    *,
    order: MarketplaceOrder,
    warehouse_id: Any,
    note: str,
) -> dict[str, Any]:
    """Best-effort one-piece write-off for a real WB order without a picking address.

    Packing is an order-fulfilment action, not an inventory-validation gate. If the WMS
    knows a free physical unit, remove one deterministic free unit without touching any
    ACTIVE reservation. If recorded stock is already zero, keep it at zero and return an
    audited shortage instead of creating negative stock or blocking the parcel.
    """
    if not order.local_sku_id or not warehouse_id:
        return {"changed": False, "reason": "no_local_sku_or_warehouse", "stock_shortage": True}
    wid = uuid.UUID(str(warehouse_id))
    sku_id = uuid.UUID(str(order.local_sku_id))
    identity_ids = [uuid.UUID(x) for x in canonical_stock_identity_sku_ids(
        db, client_id=order.client_id, sku_id=sku_id
    )]
    reserved = active_reserved_totals(
        db, client_id=order.client_id, warehouse_id=wid, sku_ids=identity_ids
    )
    rows = db.execute(
        select(InventoryBalance, Location)
        .join(Location, Location.id == InventoryBalance.location_id)
        .where(
            InventoryBalance.client_id == order.client_id,
            InventoryBalance.warehouse_id == wid,
            InventoryBalance.sku_id.in_(identity_ids),
            InventoryBalance.quantity > 0,
        )
        .order_by(Location.code.asc(), InventoryBalance.updated_at.asc())
        .with_for_update()
    ).all()
    chosen = None
    for balance, location in rows:
        if str(location.code or "").strip().upper() in SYSTEM_LOCATION_CODES:
            continue
        key = (str(balance.sku_id), str(balance.location_id), str(balance.container_id) if balance.container_id else "")
        free = _d(balance.quantity) - _d(reserved.get(key, 0))
        if free >= Decimal(1) - EPSILON:
            chosen = (balance, location)
            break
    if chosen is None:
        return {"changed": False, "reason": "recorded_stock_zero_or_fully_reserved", "stock_shortage": True}
    balance, location = chosen
    operation = execute_operation(
        db,
        OperationCreate(
            client_id=order.client_id,
            warehouse_id=wid,
            operation_type="SHIPPING",
            idempotency_key=f"pack-unallocated-{order.id}",
            reference=f"FBS {getattr(order, 'external_order_num', None) or order.external_order_id}",
            comment="Упаковка существующего WB-заказа без адресного резерва",
            lines=[StockLine(
                sku_id=balance.sku_id,
                quantity=Decimal(1),
                source_location_id=balance.location_id,
                source_container_id=balance.container_id,
                reason="WB_PACKING_UNALLOCATED_WRITE_OFF",
            )],
        ),
        commit=False,
    )
    reconcile_stock_bucket(
        db, client_id=order.client_id, warehouse_id=wid, sku_id=balance.sku_id,
        location_id=balance.location_id, container_id=balance.container_id,
        preferred_bucket="FBS_AVAILABLE", lock=True,
    )
    return {
        "changed": True, "operation_id": str(operation.id), "stock_shortage": False,
        "source_sku_id": str(balance.sku_id),
        "source_location_id": str(balance.location_id),
        "source_container_id": str(balance.container_id) if balance.container_id else None,
        "note": note,
    }


def finalize_order_stock_for_packing(
    db: Session,
    order: MarketplaceOrder,
    *,
    warehouse_id: Any | None = None,
    note: str = "WB_PACKED",
) -> dict[str, Any]:
    """Finish local stock accounting without ever blocking a real WB order.

    ACTIVE -> exact source write-off. SALE_RESERVED -> best-effort free-unit write-off then
    consume the warehouse-level promise. Missing reserve/zero recorded stock -> audit the
    discrepancy and still let the existing marketplace order be packed.
    """
    ensure_stock_reservation_schema(db)
    latest = latest_reservations_for_orders(db, [order.id]).get(str(order.id))
    status = str((latest or {}).get("status") or "").upper()
    if status in {"ACTIVE", "CONSUMING"}:
        result = consume_order_reservation(db, order, note=note)
        return {**result, "mode": "ADDRESS_BOUND", "stock_shortage": False}
    if status == "CONSUMED":
        return {"changed": False, "reason": "already_consumed", "reservation": latest, "mode": "ALREADY_DONE"}

    effective_warehouse = warehouse_id or ((latest or {}).get("warehouse_id"))
    writeoff = _write_off_unallocated_physical_unit(
        db, order=order, warehouse_id=effective_warehouse, note=note
    ) if effective_warehouse else {"changed": False, "reason": "warehouse_unknown", "stock_shortage": True}

    if status == "SALE_RESERVED":
        db.execute(
            text(
                "UPDATE fc_stock_reservations SET status='CONSUMED',consumed_at=CURRENT_TIMESTAMP,"
                "updated_at=CURRENT_TIMESTAMP,note=:note WHERE id=:id AND status='SALE_RESERVED'"
            ),
            {"id": str(latest["id"]), "note": ("PACKED_WITH_STOCK_SHORTAGE:" if writeoff.get("stock_shortage") else "PACKED_UNALLOCATED:") + note},
        )
        db.execute(
            text(
                "UPDATE fc_orders_v2 SET status='WRITTEN_OFF',scan_completed_at=COALESCE(scan_completed_at,CURRENT_TIMESTAMP),"
                "written_off_at=CURRENT_TIMESTAMP WHERE source_order_id=:source"
            ),
            {"source": str(order.id)},
        )
        if order.local_sku_id and effective_warehouse:
            sku = db.get(SKU, order.local_sku_id)
            if sku:
                enqueue_marketplace_stock(db, order=order, warehouse_id=effective_warehouse, sku=sku, reason="PACKED_UNALLOCATED_RECONCILE")
        return {**writeoff, "reservation_id": str(latest.get("id") or ""), "reservation_consumed": True, "mode": "SALE_RESERVED_FALLBACK"}

    if order.local_sku_id and effective_warehouse and writeoff.get("changed"):
        sku = db.get(SKU, order.local_sku_id)
        if sku:
            enqueue_marketplace_stock(db, order=order, warehouse_id=effective_warehouse, sku=sku, reason="PACKED_WITHOUT_RESERVATION_RECONCILE")
    return {**writeoff, "reservation_consumed": False, "mode": "NO_RESERVATION_FALLBACK"}


def consume_order_reservation(db: Session, order: MarketplaceOrder, *, note: str = "WB_PACKED") -> dict[str, Any]:
    """Final FBS write-off, exactly once, including packing-time reservations.

    0.9.4.8b: a picking-list id is no longer a prerequisite. If a real reservation
    exists, consume it; if none exists, report no_reservation and let the strict print
    finalizer audit that accounting gap without freezing the packing table.
    """
    ensure_stock_reservation_schema(db)
    with distributed_lock(db, f"stock-reservation-order:{order.id}", blocking=True):
        latest = latest_reservations_for_orders(db, [order.id]).get(str(order.id))
        if not latest and order.last_picking_list_id:
            latest = ensure_legacy_reservation_for_order(db, order)
        if not latest:
            return {"changed": False, "reason": "no_reservation"}
        status = str(latest.get("status") or "").upper()
        if status == "CONSUMED":
            return {"changed": False, "reason": "already_consumed", "reservation": latest}
        if status not in {"ACTIVE", "CONSUMING"}:
            return {"changed": False, "reason": f"status_{status.lower()}", "reservation": latest}
        return consume_marketplace_reservation(db, order=order, reservation=latest, note=note)


def _ensure_receiving_location(db: Session, warehouse_id: Any) -> Location:
    # Two cancellations of different orders may arrive at the same millisecond.
    # Serialize first creation per warehouse so only one virtual ПРИЕМКА can appear.
    with distributed_lock(db, f"system-location-receiving:{warehouse_id}", blocking=True):
        loc = db.execute(
            select(Location).where(
                Location.warehouse_id == warehouse_id,
                or_(Location.code == "ПРИЕМКА", Location.barcode == "ПРИЕМКА"),
            )
        ).scalars().first()
        if loc:
            return loc
        loc = Location(
            warehouse_id=warehouse_id,
            parent_id=None,
            code="ПРИЕМКА",
            barcode="ПРИЕМКА",
            name="ПРИЕМКА",
            location_type="CELL",
            is_active=True,
        )
        db.add(loc)
        db.flush()
        return loc


def cancel_order_to_receiving(db: Session, order: MarketplaceOrder, previous_status: str) -> dict[str, Any]:
    """Handle WB cancellation according to the last physical FulfillCore state.

    A real picking-list link means the unit has been handed to picking. If WB cancels
    before packing, it leaves the old address and waits in virtual ПРИЕМКА for a real
    employee placement. A reservation without a picking list is released in place.

    PACKED is different: packing is the warehouse write-off boundary. The parcel is
    considered outbound and must stay written off even if WB cancels it afterwards.
    Nothing is added to ПРИЕМКА automatically. If staff physically remove such a parcel
    from dispatch, they receive it later through the normal receiving flow.
    """
    # The immutable physical truth is the picking-list link, not the transient local
    # status seen by the WB sync. A cancellation can arrive after a background refresh
    # has already changed the local bucket, so relying on previous_status could silently
    # skip a unit that really left its source cell. Once a real picking list exists, the
    # old source address must never be restored automatically.
    if not order.last_picking_list_id:
        return {"changed": False, "reason": "no_picking_list"}
    ensure_stock_reservation_schema(db)

    packed_physically = bool(order.packed_at) or str(previous_status or "").upper() == "PACKED"
    if packed_physically:
        # 0.7.9.2: PACKED is authoritative physical write-off. New 0.7.9.1 orders are
        # already CONSUMED here. Legacy PACKED orders can still have an ACTIVE backfill;
        # consume it once so cancellation can never resurrect stock or create a phantom
        # unit in ПРИЕМКА. The packing operation is idempotent.
        consumed = consume_order_reservation(
            db, order, note="WB_CANCEL_AFTER_PACKED_KEEP_WRITTEN_OFF"
        )
        latest = latest_reservations_for_orders(db, [order.id]).get(str(order.id))
        if latest and str(latest.get("status") or "").upper() == "CONSUMED":
            with db.no_autoflush:
                db.execute(
                    text(
                        "UPDATE fc_stock_reservations SET updated_at=CURRENT_TIMESTAMP, note='WB_CANCEL_AFTER_PACKED_KEEP_WRITTEN_OFF' WHERE id=:id"
                    ),
                    {"id": str(latest["id"])},
                )
            return {
                "changed": bool(consumed.get("changed")),
                "reason": "packed_kept_written_off",
                "action": "KEEP_WRITTEN_OFF",
                "reservation_id": str(latest["id"]),
                "operation_id": consumed.get("operation_id"),
                "from_status": "CONSUMED",
            }
        # If an old reservation was already routed to receiving by 0.7.9.1, do not
        # silently remove that physical balance during an upgrade. It requires a real
        # warehouse decision because the item may already be on the receiving table.
        return {
            "changed": False,
            "reason": consumed.get("reason") or "packed_reservation_review",
            "action": "REVIEW",
            "reservation": latest or consumed.get("reservation"),
        }

    with distributed_lock(db, f"stock-reservation-order:{order.id}", blocking=True):
        latest = latest_reservations_for_orders(db, [order.id]).get(str(order.id))
        if not latest:
            latest = ensure_legacy_reservation_for_order(db, order)
        if not latest:
            return {"changed": False, "reason": "no_reservation"}
        status = str(latest.get("status") or "").upper()
        if status in {"CANCELLED_TO_RECEIVING", "RETURNED_TO_RECEIVING"}:
            return {"changed": False, "reason": "already_receiving", "reservation": latest}
        if status not in {"ACTIVE", "CONSUMED", "CANCELLING"}:
            return {"changed": False, "reason": f"status_{status.lower()}", "reservation": latest}
        source_status = ("CONSUMED" if latest.get("consumed_at") else "ACTIVE") if status == "CANCELLING" else status

        try:
            warehouse_id = uuid.UUID(str(latest["warehouse_id"]))
            sku_id = uuid.UUID(str(latest["sku_id"]))
            location_id = uuid.UUID(str(latest["source_location_id"]))
            container_id = uuid.UUID(str(latest["source_container_id"])) if latest.get("source_container_id") else None
        except Exception:
            return {"changed": False, "reason": "invalid_reservation_ids"}
        receiving = _ensure_receiving_location(db, warehouse_id)

        # 0.7.9.13 picking-list rows are already physically written off from the source.
        # Older ACTIVE rows may also have no source balance because the item was picked
        # before this upgrade. Reconcile those as consumed so cancellation adds the
        # physical item to ПРИЕМКА instead of trying to subtract the source twice.
        if source_status == "ACTIVE":
            source_balance = _bucket_balance(
                db,
                client_id=order.client_id,
                warehouse_id=warehouse_id,
                sku_id=sku_id,
                location_id=location_id,
                container_id=container_id,
                lock=True,
            )
            if not source_balance or _d(source_balance.quantity) < Decimal(1) - EPSILON:
                source_status = "CONSUMED"
                with db.no_autoflush:
                    db.execute(
                        text("UPDATE fc_stock_reservations SET consumed_at=COALESCE(consumed_at,CURRENT_TIMESTAMP), updated_at=CURRENT_TIMESTAMP, note='CANCEL_RECONCILED_ALREADY_OUT_OF_SOURCE' WHERE id=:id"),
                        {"id": str(latest["id"])},
                    )

        transient = "CANCELLING"
        if status != "CANCELLING":
            with db.no_autoflush:
                db.execute(
                    text("UPDATE fc_stock_reservations SET status=:status, updated_at=CURRENT_TIMESTAMP WHERE id=:id"),
                    {"status": transient, "id": str(latest["id"])},
                )
        # On recovery from CANCELLING, consumed_at tells us whether the stock had
        # already been physically written off by packing. The idempotency key below
        # makes replay safe in either case.
        if source_status == "ACTIVE":
            line = StockLine(
                sku_id=sku_id,
                quantity=Decimal(1),
                source_location_id=location_id,
                source_container_id=container_id,
                destination_location_id=receiving.id,
                destination_container_id=None,
                reason="WB_CANCEL_AFTER_PICKING_RECEIVING",
            )
            operation_type = "MOVE"
            comment = "Отмена WB после сборки: товар снят с прежнего адреса и направлен в ПРИЕМКА"
        else:
            line = StockLine(
                sku_id=sku_id,
                quantity=Decimal(1),
                destination_location_id=receiving.id,
                destination_container_id=None,
                reason="WB_CANCEL_AFTER_PACKING_RECEIVING",
            )
            operation_type = "ADJUSTMENT"
            comment = "Отмена WB после упаковки: физическая единица возвращена в ПРИЕМКА для повторного размещения"

        operation = execute_operation(
            db,
            OperationCreate(
                client_id=order.client_id,
                warehouse_id=warehouse_id,
                operation_type=operation_type,
                idempotency_key=f"wb-cancel-receiving-{order.id}-{latest['id']}",
                reference=f"WB {order.external_order_num or order.external_order_id}",
                comment=comment,
                lines=[line],
            ),
        )
        if source_status == "ACTIVE":
            # The picked item physically leaves its source cell, but is deliberately not
            # returned to sale while it waits in virtual ПРИЕМКА. Remove the address-bound
            # RESERVED bucket and classify the receiving balance as STORAGE_ONLY.
            db.execute(
                text(
                    """
                    UPDATE fc_stocks_v2
                    SET reserved=GREATEST(0,reserved-1),
                        physical_snapshot=GREATEST(0,physical_snapshot-1),
                        version=version+1,updated_at=CURRENT_TIMESTAMP
                    WHERE client_id=:client AND warehouse_id=:warehouse AND sku_id=:sku
                      AND location_id=:location AND container_key=:container
                    """
                ),
                {"client": str(order.client_id), "warehouse": str(warehouse_id), "sku": str(sku_id),
                 "location": str(location_id), "container": str(container_id) if container_id else ""},
            )
            reconcile_stock_bucket(
                db, client_id=order.client_id, warehouse_id=warehouse_id, sku_id=sku_id,
                location_id=receiving.id, container_id=None, preferred_bucket="STORAGE_ONLY", lock=True,
            )
        with db.no_autoflush:
            db.execute(
                text(
                    "UPDATE fc_stock_reservations SET status='CANCELLED_TO_RECEIVING', receiving_at=CURRENT_TIMESTAMP, updated_at=CURRENT_TIMESTAMP, note='WB_CANCELLED_TO_RECEIVING' WHERE id=:id"
                ),
                {"id": str(latest["id"])},
            )
        return {
            "changed": True,
            "reservation_id": str(latest["id"]),
            "operation_id": str(operation.id),
            "from_status": source_status,
            "receiving_location_id": str(receiving.id),
        }


def release_order_reservation(db: Session, order: MarketplaceOrder, *, note: str = "WORKFLOW_RETURNED_TO_NEW") -> dict[str, Any]:
    """Release a warehouse-level sale promise or an address-bound reservation safely."""
    ensure_stock_reservation_schema(db)
    latest = latest_reservations_for_orders(db, [order.id]).get(str(order.id))
    if not latest:
        return {"changed": False, "reason": "no_reservation"}
    status = str(latest.get("status") or "").upper()
    if status == "SALE_RESERVED":
        db.execute(
            text(
                "UPDATE fc_stock_reservations SET status='RELEASED',released_at=CURRENT_TIMESTAMP,"
                "updated_at=CURRENT_TIMESTAMP,note=:note WHERE id=:id AND status='SALE_RESERVED'"
            ),
            {"id": str(latest["id"]), "note": note},
        )
        sku = db.get(SKU, uuid.UUID(str(latest["sku_id"]))) if latest.get("sku_id") else None
        if sku:
            enqueue_marketplace_stock(
                db, order=order, warehouse_id=uuid.UUID(str(latest["warehouse_id"])), sku=sku,
                reason="SALE_RESERVE_RELEASE",
            )
        db.execute(
            text("UPDATE fc_orders_v2 SET status='CANCELLED',cancelled_at=CURRENT_TIMESTAMP WHERE source_order_id=:source"),
            {"source": str(order.id)},
        )
        return {"changed": True, "reservation_id": str(latest["id"]), "released_sale_reserve": True}
    if status not in {"ACTIVE", "CONSUMED"}:
        return {"changed": False, "reason": f"status_{status.lower()}"}
    if status == "CONSUMED" and (order.packed_at or str(order.local_status or "").upper() in {"PACKED", "SENT"}):
        return {"changed": False, "reason": "explicit_undo_required"}
    return release_or_restore_marketplace_reservation(
        db, order=order, reservation=latest, restore_consumed=(status == "CONSUMED"), note=note
    )


def repair_cancelled_picking_to_receiving(
    db: Session,
    *,
    client_id: Any,
    warehouse_id: Any,
    limit: int = 300,
) -> dict[str, int]:
    """Repair historical cancellations that missed ПРИЕМКА because status changed first.

    Only un-packed orders with a real FulfillCore picking list are eligible. PACKED/SENT
    parcels remain written off. The reservation operation is idempotent, so opening the
    placement screen cannot duplicate stock.
    """
    ensure_stock_reservation_schema(db)
    rows = db.execute(
        select(MarketplaceOrder).where(
            MarketplaceOrder.client_id == client_id,
            MarketplaceOrder.provider == "WB",
            MarketplaceOrder.local_status == "CANCELLED",
            MarketplaceOrder.last_picking_list_id.is_not(None),
            MarketplaceOrder.packed_at.is_(None),
        ).order_by(MarketplaceOrder.created_at.desc()).limit(max(1, min(int(limit), 1000)))
    ).scalars().all()
    repaired = skipped = 0
    for order in rows:
        latest = latest_reservations_for_orders(db, [order.id]).get(str(order.id))
        if latest and str(latest.get("warehouse_id") or "") != str(warehouse_id):
            skipped += 1
            continue
        result = cancel_order_to_receiving(db, order, "PICKING")
        if result.get("changed"):
            repaired += 1
        else:
            skipped += 1
    if repaired:
        db.commit()
    return {"repaired": repaired, "skipped": skipped, "checked": len(rows)}


def receiving_queue(
    db: Session,
    *,
    client_id: Any,
    warehouse_id: Any,
) -> dict[str, Any]:
    """Return the *current* stock in ПРИЕМКА enriched with recent WB-cancellation facts."""
    ensure_stock_reservation_schema(db)
    # Heal cancellations created by older builds before reading the virtual placement
    # zone. This is idempotent and only affects real picking-list units that were never
    # packed; they must wait in ПРИЕМКА, never jump back to the old cell.
    repair_cancelled_picking_to_receiving(db, client_id=client_id, warehouse_id=warehouse_id)
    receiving = db.execute(
        select(Location).where(
            Location.warehouse_id == warehouse_id,
            or_(Location.code == "ПРИЕМКА", Location.barcode == "ПРИЕМКА"),
        )
    ).scalars().first()
    if not receiving:
        return {"location_id": None, "location_code": "ПРИЕМКА", "quantity": 0, "items": []}

    balances = db.execute(
        select(InventoryBalance, SKU, Container)
        .join(SKU, SKU.id == InventoryBalance.sku_id)
        .outerjoin(Container, Container.id == InventoryBalance.container_id)
        .where(
            InventoryBalance.client_id == client_id,
            InventoryBalance.warehouse_id == warehouse_id,
            InventoryBalance.location_id == receiving.id,
            InventoryBalance.quantity > 0,
        )
        .order_by(SKU.article, SKU.size, SKU.barcode)
    ).all()
    sku_ids = [str(balance.sku_id) for balance, _, _ in balances]
    cancellation_by_sku: dict[str, list[dict[str, Any]]] = defaultdict(list)
    if sku_ids:
        stmt = text(
            """
            SELECT sku_id, external_order_id, source_location_id, source_container_id, receiving_at, created_at
            FROM fc_stock_reservations
            WHERE client_id=:client_id AND warehouse_id=:warehouse_id
              AND status='CANCELLED_TO_RECEIVING' AND sku_id IN :sku_ids
            ORDER BY COALESCE(receiving_at, created_at) DESC
            """
        ).bindparams(bindparam("sku_ids", expanding=True))
        rows = db.execute(
            stmt,
            {"client_id": str(client_id), "warehouse_id": str(warehouse_id), "sku_ids": list(dict.fromkeys(sku_ids))},
        ).mappings().all()
        for row in rows:
            cancellation_by_sku[str(row["sku_id"])].append(dict(row))

    from app.services.product_identity import resolved_sku_identity
    items = []
    total = Decimal(0)
    for balance, sku, container in balances:
        qty = _d(balance.quantity)
        total += qty
        cancellations = cancellation_by_sku.get(str(balance.sku_id), [])
        # Do not claim more cancelled orders than physical units currently waiting in ПРИЕМКА.
        max_refs = max(0, int(qty))
        relevant = cancellations[:max_refs] if max_refs else []
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
                "container_id": str(container.id) if container else None,
                "container": (container.name or container.barcode) if container else None,
                "reason": "Отмена WB после сборки до упаковки" if relevant else "Приёмка · требуется размещение",
                "orders": [str(x.get("external_order_id") or "") for x in relevant if x.get("external_order_id")],
                "received_at": (relevant[0].get("receiving_at") or relevant[0].get("created_at")) if relevant else None,
            }
        )
    return {"location_id": str(receiving.id), "location_code": receiving.code, "quantity": float(total), "items": items}
