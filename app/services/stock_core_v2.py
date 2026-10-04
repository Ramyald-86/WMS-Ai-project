from __future__ import annotations

import threading
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

from fastapi import HTTPException
from sqlalchemy import and_, bindparam, case, func, or_, select, text
from sqlalchemy.orm import Session

from app.models import InventoryBalance, InventoryOperation, InventoryOperationLine, MarketplaceCredential, MarketplaceOrder, MarketplacePickingList, SKU
from app.schemas.inventory import OperationCreate, StockLine
from app.services.inventory import execute_operation

EPS = Decimal("0.000001")
_SCHEMA_READY = False
_SCHEMA_GUARD = threading.Lock()


def _d(value: Any) -> Decimal:
    try:
        return Decimal(str(value or 0))
    except Exception:
        return Decimal(0)


def _is_internal_wb_product_key(value: Any) -> bool:
    """Reject technical warehouse/container codes before they ever enter WB outbox."""
    code = str(value or "").strip().upper()
    return bool(code and (code.startswith("BOX_") or code.startswith("FCBOX_") or code.startswith("FCLOC-")))


def install_stock_core_v2_schema(db: Session) -> None:
    global _SCHEMA_READY
    if _SCHEMA_READY:
        return
    with _SCHEMA_GUARD:
        if _SCHEMA_READY:
            return
        migration = Path(__file__).resolve().parents[1] / "migrations" / "0_9_0_stock_core_v2.sql"
        raw = migration.read_text(encoding="utf-8")
        # Migration contains only additive statements and no procedural blocks.
        for statement in (chunk.strip() for chunk in raw.split(";") if chunk.strip()):
            db.execute(text(statement))
        db.commit()
        _SCHEMA_READY = True


def ensure_stock_core_v2_schema(db: Session) -> None:
    if not _SCHEMA_READY:
        install_stock_core_v2_schema(db)


def canonical_created_at(order: MarketplaceOrder) -> datetime:
    value = getattr(order, "external_created_at", None) or getattr(order, "created_at", None)
    if value is None:
        return datetime.now(timezone.utc)
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def marketplace_order_age_hours(order: MarketplaceOrder, now: datetime | None = None) -> float:
    now = now or datetime.now(timezone.utc)
    return max(0.0, (now - canonical_created_at(order)).total_seconds() / 3600.0)


def order_type_for_marketplace(order: MarketplaceOrder) -> str:
    provider = str(getattr(order, "provider", "WB") or "WB").upper()
    return "FBS_OZON" if provider == "OZON" else "FBS_WB"


def ensure_canonical_marketplace_order(
    db: Session,
    order: MarketplaceOrder,
    warehouse_id: Any,
    sku_id: Any | None = None,
) -> str:
    ensure_stock_core_v2_schema(db)
    existing = db.execute(
        text("SELECT id FROM fc_orders_v2 WHERE source_order_id=:source"),
        {"source": str(order.id)},
    ).scalar_one_or_none()
    order_id = str(existing or uuid.uuid4())
    db.execute(
        text(
            """
            INSERT INTO fc_orders_v2(
                id,source_order_id,client_id,warehouse_id,external_order_id,
                order_type,status,created_at,credential_id,external_warehouse_id
            ) VALUES(
                :id,:source,:client,:warehouse,:external,:order_type,'NEW',:created,:credential,:external_wh
            )
            ON CONFLICT (id) DO UPDATE SET
                created_at=EXCLUDED.created_at,
                external_order_id=EXCLUDED.external_order_id,
                credential_id=EXCLUDED.credential_id,
                external_warehouse_id=EXCLUDED.external_warehouse_id
            """
        ),
        {
            "id": order_id,
            "source": str(order.id),
            "client": str(order.client_id),
            "warehouse": str(warehouse_id),
            "external": str(getattr(order, "external_order_num", None) or getattr(order, "external_order_id", None) or ""),
            "order_type": order_type_for_marketplace(order),
            "created": canonical_created_at(order),
            "credential": str(getattr(order, "credential_id", None) or "") or None,
            "external_wh": str(getattr(order, "wb_warehouse_id", None) or "") or None,
        },
    )
    if sku_id:
        exists = db.execute(
            text("SELECT 1 FROM fc_order_lines_v2 WHERE order_id=:order_id AND sku_id=:sku LIMIT 1"),
            {"order_id": order_id, "sku": str(sku_id)},
        ).scalar_one_or_none()
        if not exists:
            db.execute(
                text("INSERT INTO fc_order_lines_v2(id,order_id,sku_id,quantity) VALUES(:id,:order_id,:sku,1)"),
                {"id": str(uuid.uuid4()), "order_id": order_id, "sku": str(sku_id)},
            )
    return order_id


def _balance(
    db: Session,
    *,
    client_id: Any,
    warehouse_id: Any,
    sku_id: Any,
    location_id: Any,
    container_id: Any | None,
    lock: bool,
) -> InventoryBalance | None:
    stmt = select(InventoryBalance).where(
        InventoryBalance.client_id == client_id,
        InventoryBalance.warehouse_id == warehouse_id,
        InventoryBalance.sku_id == sku_id,
        InventoryBalance.location_id == location_id,
        InventoryBalance.container_id == container_id if container_id else InventoryBalance.container_id.is_(None),
    )
    if lock:
        stmt = stmt.with_for_update()
    return db.execute(stmt).scalars().first()


def reconcile_stock_bucket(
    db: Session,
    *,
    client_id: Any,
    warehouse_id: Any,
    sku_id: Any,
    location_id: Any,
    container_id: Any | None,
    preferred_bucket: str = "FBS_AVAILABLE",
    lock: bool = True,
) -> dict[str, Decimal]:
    """Make category sum equal the existing physical InventoryBalance.

    Existing stock is lazily classified. Marketplace use defaults to FBS_AVAILABLE;
    Manual use defaults to STORAGE_ONLY. Positive future receiving deltas are assigned
    to the requested preferred bucket. Reserved is never silently reduced.
    """
    ensure_stock_core_v2_schema(db)
    physical_row = _balance(
        db,
        client_id=client_id,
        warehouse_id=warehouse_id,
        sku_id=sku_id,
        location_id=location_id,
        container_id=container_id,
        lock=lock,
    )
    physical = _d(physical_row.quantity if physical_row else 0)
    params = {
        "client": str(client_id),
        "warehouse": str(warehouse_id),
        "sku": str(sku_id),
        "location": str(location_id),
        "container": str(container_id) if container_id else "",
    }
    row = db.execute(
        text(
            """
            SELECT fbs_available,storage_only,reserved,physical_snapshot
            FROM fc_stocks_v2
            WHERE client_id=:client AND warehouse_id=:warehouse AND sku_id=:sku
              AND location_id=:location AND container_key=:container
            FOR UPDATE
            """
        ),
        params,
    ).mappings().first()
    if not row:
        fbs = physical if preferred_bucket == "FBS_AVAILABLE" else Decimal(0)
        storage = physical if preferred_bucket == "STORAGE_ONLY" else Decimal(0)
        db.execute(
            text(
                """
                INSERT INTO fc_stocks_v2(
                    client_id,warehouse_id,sku_id,location_id,container_key,
                    fbs_available,storage_only,reserved,physical_snapshot
                ) VALUES(:client,:warehouse,:sku,:location,:container,:fbs,:storage,0,:physical)
                """
            ),
            {**params, "fbs": fbs, "storage": storage, "physical": physical},
        )
        return {"fbs_available": fbs, "storage_only": storage, "reserved": Decimal(0), "physical": physical}

    fbs = _d(row["fbs_available"])
    storage = _d(row["storage_only"])
    reserved = _d(row["reserved"])
    delta = physical - (fbs + storage + reserved)
    if delta > EPS:
        if preferred_bucket == "STORAGE_ONLY":
            storage += delta
        else:
            fbs += delta
    elif delta < -EPS:
        missing = -delta
        for name in ("storage", "fbs"):
            current = storage if name == "storage" else fbs
            take = min(current, missing)
            current -= take
            missing -= take
            if name == "storage":
                storage = current
            else:
                fbs = current
        if missing > EPS:
            pass  # FulfillCore: reserve mismatch is diagnostic only; never block operator workflow

    db.execute(
        text(
            """
            UPDATE fc_stocks_v2
            SET fbs_available=:fbs,storage_only=:storage,reserved=:reserved,
                physical_snapshot=:physical,version=version+1,updated_at=CURRENT_TIMESTAMP
            WHERE client_id=:client AND warehouse_id=:warehouse AND sku_id=:sku
              AND location_id=:location AND container_key=:container
            """
        ),
        {**params, "fbs": fbs, "storage": storage, "reserved": reserved, "physical": physical},
    )
    return {"fbs_available": fbs, "storage_only": storage, "reserved": reserved, "physical": physical}


def fbs_available_for_bucket(
    db: Session,
    *,
    client_id: Any,
    warehouse_id: Any,
    sku_id: Any,
    location_id: Any,
    container_id: Any | None,
) -> Decimal:
    return reconcile_stock_bucket(
        db,
        client_id=client_id,
        warehouse_id=warehouse_id,
        sku_id=sku_id,
        location_id=location_id,
        container_id=container_id,
        preferred_bucket="FBS_AVAILABLE",
        lock=False,
    )["fbs_available"]


# FC_FBS_CANONICAL_FREE_TRUTH_09415G5A8F_20260904
# The old fc_stocks_v2.reserved column may contain historical residue from pre-ledger
# builds.  Warehouse truth now comes from physical InventoryBalance + explicit
# STORAGE_ONLY + the live reservation ledger.  Never let a stale bucket counter make
# sellable stock smaller (or larger) than that canonical equation.
_LIVE_FBS_RESERVATION_STATES = (
    "SALE_RESERVED", "ACTIVE", "CONSUMING", "CANCELLING", "PACKING_WRITE_OFF"
)
_ADDRESS_FBS_RESERVATION_STATES = (
    "ACTIVE", "CONSUMING", "CANCELLING", "PACKING_WRITE_OFF"
)


def _storage_only_for_bucket(
    db: Session, *, client_id: Any, warehouse_id: Any, sku_id: Any,
    location_id: Any, container_id: Any | None,
) -> Decimal:
    ensure_stock_core_v2_schema(db)
    value = db.execute(
        text(
            "SELECT COALESCE(storage_only,0) FROM fc_stocks_v2 "
            "WHERE client_id=:client AND warehouse_id=:warehouse AND sku_id=:sku "
            "AND location_id=:location AND container_key=:container"
        ),
        {
            "client": str(client_id), "warehouse": str(warehouse_id), "sku": str(sku_id),
            "location": str(location_id), "container": str(container_id) if container_id else "",
        },
    ).scalar_one_or_none()
    return max(Decimal(0), _d(value))


def _live_reserved_for_bucket_truth(
    db: Session, *, client_id: Any, warehouse_id: Any, sku_id: Any,
    location_id: Any, container_id: Any | None,
) -> Decimal:
    value = db.execute(
        text(
            """
            SELECT COALESCE(SUM(CASE
              WHEN r.status='PACKING_WRITE_OFF' AND io.id IS NOT NULL THEN 0
              ELSE r.quantity END),0)
            FROM fc_stock_reservations r
            LEFT JOIN marketplace_orders o ON CAST(o.id AS varchar)=r.order_id
            LEFT JOIN inventory_operations io
              ON CAST(io.client_id AS varchar)=CAST(r.client_id AS varchar) AND COALESCE(io.status,'COMPLETED')='COMPLETED'
             AND io.idempotency_key=('stock-core-v2-pack-' || CAST(o.id AS varchar) || '-' || CAST(r.id AS varchar))
            WHERE r.client_id=:client AND r.warehouse_id=:warehouse AND r.sku_id=:sku
              AND r.source_location_id=:location
              AND COALESCE(r.source_container_id,'')=:container
              AND r.status IN ('ACTIVE','CONSUMING','CANCELLING','PACKING_WRITE_OFF')
            """
        ),
        {
            "client": str(client_id), "warehouse": str(warehouse_id), "sku": str(sku_id),
            "location": str(location_id), "container": str(container_id) if container_id else "",
        },
    ).scalar_one()
    return max(Decimal(0), _d(value))


# FC_K9R37_EXTERNAL_PICK_HOLD_TRUTH
def _external_pick_reserved_for_bucket_truth(
    db: Session, *, client_id: Any, warehouse_id: Any, sku_id: Any,
    location_id: Any, container_id: Any | None,
) -> Decimal:
    """Return ACTIVE external-picking allocation for one exact physical bucket.

    The r37 upload flow reserves free stock before a picker physically removes it.
    It is intentionally separate from marketplace-order reservations, but both are
    equally unavailable to new orders, normal picking lists and WB FBS publication.
    """
    try:
        value = db.execute(text(
            """
            SELECT COALESCE(SUM(quantity),0)
            FROM fc_external_pick_allocations
            WHERE client_id=:client AND warehouse_id=:warehouse AND sku_id=:sku
              AND location_id=:location AND COALESCE(container_id,'')=:container
              AND status='ACTIVE'
            """
        ), {
            "client": str(client_id), "warehouse": str(warehouse_id), "sku": str(sku_id),
            "location": str(location_id), "container": str(container_id) if container_id else "",
        }).scalar_one()
        return max(Decimal(0), _d(value))
    except Exception:
        # Startup/rollback compatibility: before the additive r37 runtime table exists,
        # external-pick hold is simply zero.
        return Decimal(0)


def _external_pick_reserved_for_identity(
    db: Session, *, client_id: Any, warehouse_id: Any, sku_ids: list[str],
) -> Decimal:
    if not sku_ids:
        return Decimal(0)
    try:
        stmt = text(
            "SELECT COALESCE(SUM(quantity),0) FROM fc_external_pick_allocations "
            "WHERE client_id=:client AND warehouse_id=:warehouse AND sku_id IN :ids AND status='ACTIVE'"
        ).bindparams(bindparam('ids', expanding=True))
        return max(Decimal(0), _d(db.execute(stmt, {
            'client': str(client_id), 'warehouse': str(warehouse_id), 'ids': list(sku_ids),
        }).scalar_one()))
    except Exception:
        return Decimal(0)


def effective_fbs_free_for_bucket(
    db: Session, *, client_id: Any, warehouse_id: Any, sku_id: Any,
    location_id: Any, container_id: Any | None,
) -> Decimal:
    physical_row = _balance(
        db, client_id=client_id, warehouse_id=warehouse_id, sku_id=sku_id,
        location_id=location_id, container_id=container_id, lock=False,
    )
    physical = max(Decimal(0), _d(physical_row.quantity if physical_row else 0))
    storage = min(physical, _storage_only_for_bucket(
        db, client_id=client_id, warehouse_id=warehouse_id, sku_id=sku_id,
        location_id=location_id, container_id=container_id,
    ))
    reserved = _live_reserved_for_bucket_truth(
        db, client_id=client_id, warehouse_id=warehouse_id, sku_id=sku_id,
        location_id=location_id, container_id=container_id,
    )
    external_pick = _external_pick_reserved_for_bucket_truth(
        db, client_id=client_id, warehouse_id=warehouse_id, sku_id=sku_id,
        location_id=location_id, container_id=container_id,
    )
    return max(Decimal(0), physical - storage - reserved - external_pick)


def canonical_stock_identity_sku_ids(
    db: Session, *, client_id: Any, sku_id: Any,
) -> list[str]:
    """Return local SKU ids that represent one physical product identity.

    FulfillCore historically allowed more than one local SKU row for the same WB
    barcode/article-size.  Reservation and WB sellable calculations must aggregate
    those aliases or two local rows can each believe the same physical units are free.
    Exact barcode and exact article+size matches inside one client are intentionally
    treated as one stock identity.
    """
    try:
        key = uuid.UUID(str(sku_id))
    except Exception:
        return [str(sku_id)]
    sku = db.get(SKU, key)
    if not sku or str(sku.client_id) != str(client_id):
        return [str(sku_id)]
    barcode = str(sku.barcode or "").strip()
    article = str(sku.article or sku.sku_code or "").strip()
    size = str(sku.size or "").strip()
    clauses = [SKU.id == sku.id]
    # Barcode is the strongest stock identity and must not be broadened to another
    # barcode merely because article+size happen to match.  Article+size is only a
    # fallback for historical SKU rows where barcode is absent.
    if barcode:
        clauses.append(func.trim(SKU.barcode) == barcode)
    elif article and size:
        clauses.append(and_(
            or_(func.trim(SKU.article) == article, func.trim(SKU.sku_code) == article),
            func.trim(SKU.size) == size,
        ))
    rows = db.execute(
        select(SKU.id).where(SKU.client_id == sku.client_id, or_(*clauses))
    ).scalars().all()
    return sorted({str(x) for x in rows} | {str(sku.id)})



_WB_PHYSICAL_SETTINGS_KEY = "__fc_physical_warehouses__"


def _work_wb_warehouses_by_credential(
    db: Session, *, client_id: Any, warehouse_id: Any,
) -> dict[str, set[int]]:
    """Resolve WORK seller warehouses for one physical FulfillCore warehouse.

    Credential rows are grouped by visible cabinet name so old disconnected API-key
    generations inherit the current physical-warehouse scope. This mirrors the order
    workspace lineage without importing router-private helpers.
    """
    rows = db.execute(
        select(MarketplaceCredential).where(
            MarketplaceCredential.client_id == client_id,
            MarketplaceCredential.provider == "WB",
        ).order_by(MarketplaceCredential.created_at.asc())
    ).scalars().all()
    groups: dict[str, list[MarketplaceCredential]] = {}
    for cred in rows:
        groups.setdefault(str(cred.name or cred.id), []).append(cred)
    result: dict[str, set[int]] = {}
    for group in groups.values():
        work: set[int] = set()
        explicit_scope_found = False
        # Prefer explicit per-physical-warehouse settings. Merge WORK rows across the
        # credential lineage; extra stale WORK rows can only make the guard stricter.
        for cred in reversed(group):
            raw = cred.warehouse_settings or {}
            scopes = raw.get(_WB_PHYSICAL_SETTINGS_KEY) if isinstance(raw, dict) else None
            scoped = scopes.get(str(warehouse_id)) if isinstance(scopes, dict) else None
            if isinstance(scoped, dict):
                explicit_scope_found = True
                for seller_id, value in scoped.items():
                    mode = str(value.get("mode") if isinstance(value, dict) else value or "HIDDEN").upper()
                    if mode == "WORK":
                        try: work.add(int(seller_id))
                        except (TypeError, ValueError): pass
        # Legacy single-warehouse installations used one flat map. Only use it when
        # this cabinet never had per-physical scopes, so a newly added warehouse does
        # not inherit another warehouse's orders.
        if not explicit_scope_found and not any(
            isinstance((c.warehouse_settings or {}).get(_WB_PHYSICAL_SETTINGS_KEY), dict) for c in group
        ):
            for cred in reversed(group):
                raw = cred.warehouse_settings or {}
                if not isinstance(raw, dict):
                    continue
                for seller_id, value in raw.items():
                    if str(seller_id).startswith("__"):
                        continue
                    mode = str(value.get("mode") if isinstance(value, dict) else value or "HIDDEN").upper()
                    if mode == "WORK":
                        try: work.add(int(seller_id))
                        except (TypeError, ValueError): pass
                if work:
                    break
        for cred in group:
            result[str(cred.id)] = set(work)
    return result


def _active_order_floor_cache(
    db: Session, *, client_id: Any, warehouse_id: Any, wb_warehouse_id: Any | None = None,
) -> dict[str, Any]:
    """Active NEW/PICKING order demand resolved to one physical warehouse.

    The reservation ledger normally mirrors this demand, but order demand is the
    safety floor when a historical PICKING row lost its reservation. We resolve the
    physical warehouse by (1) a real picking list, (2) any live reservation already
    attached to this warehouse, or (3) the configured WORK seller warehouse.
    """
    selected_wb_id: int | None = None
    if wb_warehouse_id not in (None, ""):
        try:
            selected_wb_id = int(wb_warehouse_id)
        except (TypeError, ValueError):
            selected_wb_id = None
    key = ("fc_active_order_floor", str(client_id), str(warehouse_id), selected_wb_id)
    cached = db.info.get(key)
    if cached is not None:
        return cached

    work_by_cred = {} if selected_wb_id is not None else _work_wb_warehouses_by_credential(db, client_id=client_id, warehouse_id=warehouse_id)
    picking_lists = {str(x) for x in db.execute(
        select(MarketplacePickingList.id).where(
            MarketplacePickingList.client_id == client_id,
            MarketplacePickingList.warehouse_id == warehouse_id,
        )
    ).scalars().all()}
    try:
        reserved_orders = {str(x) for x in db.execute(text(
            "SELECT DISTINCT order_id FROM fc_stock_reservations "
            "WHERE client_id=:client AND warehouse_id=:warehouse "
            "AND status IN ('SALE_RESERVED','ACTIVE','CONSUMING','CANCELLING','PACKING_WRITE_OFF')"
        ), {"client": str(client_id), "warehouse": str(warehouse_id)}).scalars().all()}
    except Exception:
        reserved_orders = set()

    by_sku = {"NEW": {}, "PICKING": {}}
    by_barcode = {"NEW": {}, "PICKING": {}}
    orders = db.execute(select(
        MarketplaceOrder.id, MarketplaceOrder.local_sku_id, MarketplaceOrder.local_status,
        MarketplaceOrder.credential_id, MarketplaceOrder.wb_warehouse_id,
        MarketplaceOrder.barcodes, MarketplaceOrder.last_picking_list_id,
    ).where(
        MarketplaceOrder.client_id == client_id,
        MarketplaceOrder.provider == "WB",
        MarketplaceOrder.local_status.in_(["NEW", "PICKING"]),
    )).all()
    for oid, local_sku_id, local_status, credential_id, wb_warehouse_id, barcodes, picking_list_id in orders:
        status = str(local_status or "").upper()
        oid_s = str(oid)
        in_scope = oid_s in reserved_orders
        if not in_scope and picking_list_id:
            in_scope = str(picking_list_id) in picking_lists
        if not in_scope and wb_warehouse_id is not None:
            try:
                if selected_wb_id is not None:
                    # Explicit seller-warehouse context from the current workstation is
                    # authoritative. This removes the ambiguous physical->seller mapping
                    # gap that could make inventory show reserve=0 for real WB orders.
                    in_scope = int(wb_warehouse_id) == selected_wb_id
                else:
                    in_scope = int(wb_warehouse_id) in work_by_cred.get(str(credential_id), set())
            except (TypeError, ValueError):
                in_scope = False
        if not in_scope:
            continue
        if local_sku_id:
            by_sku[status].setdefault(str(local_sku_id), set()).add(oid_s)
        for code in (barcodes or []):
            clean = str(code or "").strip()
            if clean:
                by_barcode[status].setdefault(clean, set()).add(oid_s)
    cached = {"by_sku": by_sku, "by_barcode": by_barcode}
    db.info[key] = cached
    return cached


def active_order_floor_for_identity(
    db: Session, *, client_id: Any, warehouse_id: Any,
    sku_ids: list[Any] | tuple[Any, ...] | set[Any], barcodes: list[str] | tuple[str, ...] | set[str] = (),
    wb_warehouse_id: Any | None = None,
) -> dict[str, Decimal]:
    cache = _active_order_floor_cache(
        db, client_id=client_id, warehouse_id=warehouse_id, wb_warehouse_id=wb_warehouse_id
    )
    result: dict[str, Decimal] = {}
    for status in ("NEW", "PICKING"):
        order_ids: set[str] = set()
        for sku_id in sku_ids or []:
            order_ids.update(cache["by_sku"][status].get(str(sku_id), set()))
        for barcode in barcodes or []:
            order_ids.update(cache["by_barcode"][status].get(str(barcode or "").strip(), set()))
        result[status.lower()] = Decimal(len(order_ids))
    result["total"] = result["new"] + result["picking"]
    return result


def active_order_floor_for_sku(
    db: Session, *, client_id: Any, warehouse_id: Any, sku_id: Any, wb_warehouse_id: Any | None = None,
) -> dict[str, Decimal]:
    ids = canonical_stock_identity_sku_ids(db, client_id=client_id, sku_id=sku_id)
    barcodes = {str(x or "").strip() for x in db.execute(
        select(SKU.barcode).where(SKU.id.in_([uuid.UUID(str(x)) for x in ids]))
    ).scalars().all() if str(x or "").strip()}
    return active_order_floor_for_identity(
        db, client_id=client_id, warehouse_id=warehouse_id, sku_ids=ids, barcodes=barcodes,
        wb_warehouse_id=wb_warehouse_id,
    )


def _reservation_truth_for_identity(
    db: Session, *, client_id: Any, warehouse_id: Any, sku_ids: list[str],
) -> dict[str, Decimal]:
    """Classify live ledger rows and ignore already-accounted terminal bookkeeping.

    PACKING_WRITE_OFF + an existing completed stock-core-v2-pack operation means the
    physical unit was already removed. Such a stale ledger row is safe to close and
    must not reduce sellable stock a second time.
    """
    stmt = text("""
        SELECT
          COALESCE(SUM(CASE WHEN o.local_status='NEW' THEN r.quantity ELSE 0 END),0) AS ledger_new,
          COALESCE(SUM(CASE WHEN o.local_status='PICKING' THEN r.quantity ELSE 0 END),0) AS ledger_picking,
          COALESCE(SUM(CASE WHEN COALESCE(o.local_status,'') NOT IN ('NEW','PICKING')
                 AND r.status='PACKING_WRITE_OFF' AND io.id IS NOT NULL THEN r.quantity ELSE 0 END),0) AS accounted_terminal,
          COALESCE(SUM(CASE WHEN COALESCE(o.local_status,'') NOT IN ('NEW','PICKING')
                 AND NOT (r.status='PACKING_WRITE_OFF' AND io.id IS NOT NULL) THEN r.quantity ELSE 0 END),0) AS unaccounted_terminal,
          COALESCE(SUM(CASE WHEN r.source_location_id IS NOT NULL
                 AND NOT (COALESCE(o.local_status,'') NOT IN ('NEW','PICKING')
                          AND r.status='PACKING_WRITE_OFF' AND io.id IS NOT NULL)
                 THEN r.quantity ELSE 0 END),0) AS bound_unavailable
        FROM fc_stock_reservations r
        LEFT JOIN marketplace_orders o ON CAST(o.id AS varchar)=r.order_id
        LEFT JOIN inventory_operations io
          ON CAST(io.client_id AS varchar)=CAST(r.client_id AS varchar) AND COALESCE(io.status,'COMPLETED')='COMPLETED'
         AND io.idempotency_key=('stock-core-v2-pack-' || CAST(o.id AS varchar) || '-' || CAST(r.id AS varchar))
        WHERE r.client_id=:client AND r.warehouse_id=:warehouse AND r.sku_id IN :ids
          AND r.status IN ('SALE_RESERVED','ACTIVE','CONSUMING','CANCELLING','PACKING_WRITE_OFF')
    """).bindparams(bindparam("ids", expanding=True))
    row = db.execute(stmt, {"client": str(client_id), "warehouse": str(warehouse_id), "ids": list(sku_ids)}).mappings().first() or {}
    return {k: max(Decimal(0), _d(row.get(k))) for k in (
        "ledger_new", "ledger_picking", "accounted_terminal", "unaccounted_terminal", "bound_unavailable"
    )}


def _canonical_sale_state(
    db: Session, *, client_id: Any, warehouse_id: Any, sku_id: Any, wb_warehouse_id: Any | None = None,
) -> dict[str, Any]:
    ids = canonical_stock_identity_sku_ids(db, client_id=client_id, sku_id=sku_id)
    params = {"client": str(client_id), "warehouse": str(warehouse_id), "ids": ids}
    physical = _d(db.execute(text(
        "SELECT COALESCE(SUM(quantity),0) FROM inventory_balances "
        "WHERE client_id=:client AND warehouse_id=:warehouse AND sku_id IN :ids AND quantity>0"
    ).bindparams(bindparam("ids", expanding=True)), params).scalar_one() or 0)
    storage = _d(db.execute(text(
        "SELECT COALESCE(SUM(storage_only),0) FROM fc_stocks_v2 "
        "WHERE client_id=:client AND warehouse_id=:warehouse AND sku_id IN :ids"
    ).bindparams(bindparam("ids", expanding=True)), params).scalar_one() or 0)
    storage = min(max(Decimal(0), physical), max(Decimal(0), storage))
    ledger = _reservation_truth_for_identity(
        db, client_id=client_id, warehouse_id=warehouse_id, sku_ids=ids
    )
    floor = active_order_floor_for_sku(
        db, client_id=client_id, warehouse_id=warehouse_id, sku_id=sku_id,
        wb_warehouse_id=wb_warehouse_id,
    )
    new_reserved = max(ledger["ledger_new"], floor["new"])
    picking_reserved = max(ledger["ledger_picking"], floor["picking"])
    reserved = new_reserved + picking_reserved
    pending = ledger["unaccounted_terminal"]
    external_pick = _external_pick_reserved_for_identity(
        db, client_id=client_id, warehouse_id=warehouse_id, sku_ids=ids,
    )
    unavailable = reserved + pending + external_pick
    free = max(Decimal(0), physical - storage - unavailable)
    return {
        "identity_sku_ids": ids, "physical": physical, "storage_only": storage,
        "sale_reserved": new_reserved, "picking_reserved": picking_reserved,
        "reserved": reserved, "external_pick_reserved": external_pick, "accounting_pending": pending,
        "accounted_terminal": ledger["accounted_terminal"],
        "ledger_sale_reserved": ledger["ledger_new"],
        "ledger_picking_reserved": ledger["ledger_picking"],
        "order_new": floor["new"], "order_picking": floor["picking"],
        "reservation_gap": max(Decimal(0), floor["total"] - (ledger["ledger_new"] + ledger["ledger_picking"])),
        "live_reserved": unavailable, "available_to_sell": free,
        "bound_unavailable": ledger["bound_unavailable"],
    }

def canonical_stock_identity_lock_key(
    db: Session, *, client_id: Any, sku_id: Any,
) -> str:
    ids = canonical_stock_identity_sku_ids(db, client_id=client_id, sku_id=sku_id)
    return ids[0] if ids else str(sku_id)


def _identity_sum(
    db: Session, sql: str, *, client_id: Any, warehouse_id: Any, sku_id: Any,
) -> Decimal:
    ids = canonical_stock_identity_sku_ids(db, client_id=client_id, sku_id=sku_id)
    stmt = text(sql).bindparams(bindparam("ids", expanding=True))
    return _d(db.execute(stmt, {"client": str(client_id), "warehouse": str(warehouse_id), "ids": ids}).scalar_one() or 0)


def stock_movement_totals_for_sku(
    db: Session, *, client_id: Any, warehouse_id: Any, sku_id: Any,
) -> dict[str, Decimal]:
    """Aggregate the immutable movement ledger for one canonical product identity.

    Direction, not a UI status, defines stock movement:
    - destination only => stock entered the warehouse;
    - source only => stock left the warehouse;
    - source + destination => internal move, net zero.

    RECEIVING/RECEIVING_IMPORT and SHIPPING are exposed separately so every screen can
    show the same human-readable equation: received -> written off -> current -> reserve -> free.
    Manual ADJUSTMENT is kept separate instead of pretending it was receiving/shipping.
    """
    ids = canonical_stock_identity_sku_ids(db, client_id=client_id, sku_id=sku_id)
    stmt = text("""
        SELECT
          COALESCE(SUM(CASE WHEN io.operation_type IN ('RECEIVING','RECEIVING_IMPORT')
              AND iol.source_location_id IS NULL AND iol.destination_location_id IS NOT NULL
              THEN iol.quantity ELSE 0 END),0) AS received,
          COALESCE(SUM(CASE WHEN io.operation_type='SHIPPING'
              AND iol.source_location_id IS NOT NULL AND iol.destination_location_id IS NULL
              THEN iol.quantity ELSE 0 END),0) AS shipped,
          COALESCE(SUM(CASE WHEN io.operation_type='ADJUSTMENT'
              AND iol.source_location_id IS NULL AND iol.destination_location_id IS NOT NULL
              THEN iol.quantity ELSE 0 END),0) AS adjustment_in,
          COALESCE(SUM(CASE WHEN io.operation_type='ADJUSTMENT'
              AND iol.source_location_id IS NOT NULL AND iol.destination_location_id IS NULL
              THEN iol.quantity ELSE 0 END),0) AS adjustment_out,
          COALESCE(SUM(CASE WHEN io.operation_type<>'RECONCILIATION' AND iol.source_location_id IS NULL AND iol.destination_location_id IS NOT NULL
              THEN iol.quantity ELSE 0 END),0) AS inbound_total,
          COALESCE(SUM(CASE WHEN io.operation_type<>'RECONCILIATION' AND iol.source_location_id IS NOT NULL AND iol.destination_location_id IS NULL
              THEN iol.quantity ELSE 0 END),0) AS outbound_total
        FROM inventory_operation_lines iol
        JOIN inventory_operations io ON io.id=iol.operation_id
        WHERE io.client_id=:client AND io.warehouse_id=:warehouse
          AND COALESCE(io.status,'COMPLETED')='COMPLETED' AND iol.sku_id IN :ids
    """).bindparams(bindparam('ids', expanding=True))
    row = db.execute(stmt, {
        'client': str(client_id), 'warehouse': str(warehouse_id), 'ids': ids,
    }).mappings().first() or {}
    result = {name: max(Decimal(0), _d(row.get(name))) for name in (
        'received','shipped','adjustment_in','adjustment_out','inbound_total','outbound_total'
    )}
    result['recorded_net'] = result['inbound_total'] - result['outbound_total']
    return result


def stock_truth_for_sku(
    db: Session, *, client_id: Any, warehouse_id: Any, sku_id: Any, wb_warehouse_id: Any | None = None,
) -> dict[str, Any]:
    """One canonical stock decomposition used by every UI block and WB guard.

    NEW/PICKING order demand is a safety floor even if a historical reservation row is
    missing. Already-accounted PACKING_WRITE_OFF rows are bookkeeping residue only and
    are excluded from sellable-stock math; unaccounted terminal rows remain a hard hold.
    """
    ensure_stock_core_v2_schema(db)
    state = _canonical_sale_state(
        db, client_id=client_id, warehouse_id=warehouse_id, sku_id=sku_id,
        wb_warehouse_id=wb_warehouse_id,
    )
    movement = stock_movement_totals_for_sku(
        db, client_id=client_id, warehouse_id=warehouse_id, sku_id=sku_id
    )
    return {
        **state,
        "safety_hold": state["accounting_pending"],
        "received": movement["received"], "shipped": movement["shipped"],
        "adjustment_in": movement["adjustment_in"], "adjustment_out": movement["adjustment_out"],
        "inbound_total": movement["inbound_total"], "outbound_total": movement["outbound_total"],
        "recorded_net": movement["recorded_net"],
        # Diagnostic only: legacy opening balance may make this non-zero.
        "opening_balance_estimate": state["physical"] - movement["recorded_net"],
    }




def stock_truth_for_skus(
    db: Session, *, client_id: Any, warehouse_id: Any, sku_ids: list[Any] | tuple[Any, ...] | set[Any],
    wb_warehouse_id: Any | None = None,
    include_movement: bool = True,
) -> dict[str, dict[str, Any]]:
    """Batch variant of :func:`stock_truth_for_sku` for UI pages.

    A location can contain dozens of SKUs. Calling the scalar truth function for every
    row performs several SQL statements per SKU and was the main source of the cell
    ``Request timeout``.  This routine preserves the exact canonical identity rules but
    reads physical stock, storage-only, reservation truth and movement history in four
    aggregate queries. The active NEW/PICKING order floor is loaded once per request.
    """
    ensure_stock_core_v2_schema(db)
    requested: list[str] = []
    for value in sku_ids or []:
        try:
            requested.append(str(uuid.UUID(str(value))))
        except Exception:
            requested.append(str(value))
    requested = list(dict.fromkeys(requested))
    if not requested:
        return {}

    # One client-level SKU read is cheap at warehouse scale (~thousands of rows) and
    # removes the repeated db.get + alias query from the scalar path.
    sku_rows = db.execute(select(
        SKU.id, SKU.barcode, SKU.article, SKU.sku_code, SKU.size
    ).where(SKU.client_id == client_id)).all()
    sku_meta = {
        str(row.id): {
            'id': str(row.id),
            'barcode': str(row.barcode or '').strip(),
            'article': str(row.article or row.sku_code or '').strip(),
            'article_raw': str(row.article or '').strip(),
            'sku_code': str(row.sku_code or '').strip(),
            'size': str(row.size or '').strip(),
        }
        for row in sku_rows
    }

    # FC_K9R4_ALIAS_INDEX: avoid O(requested_skus * all_client_skus) scans.
    # At a few thousand SKUs the previous nested loop was visible on every Inventory and
    # cell open. Build two dictionaries once and resolve aliases in O(1).
    ids_by_barcode: dict[str, list[str]] = {}
    ids_by_article_size: dict[tuple[str, str], list[str]] = {}
    for sid, meta in sku_meta.items():
        if meta['barcode']:
            ids_by_barcode.setdefault(meta['barcode'], []).append(sid)
        article_keys = {x for x in (meta['article_raw'], meta['sku_code']) if x}
        for article_key in article_keys:
            if meta['size']:
                ids_by_article_size.setdefault((article_key, meta['size']), []).append(sid)

    aliases_for: dict[str, list[str]] = {}
    barcodes_for: dict[str, set[str]] = {}
    for requested_id in requested:
        src = sku_meta.get(requested_id)
        if not src:
            aliases_for[requested_id] = [requested_id]
            barcodes_for[requested_id] = set()
            continue
        if src['barcode']:
            aliases = set(ids_by_barcode.get(src['barcode'], [requested_id]))
        elif src['article'] and src['size']:
            aliases = set(ids_by_article_size.get((src['article'], src['size']), [requested_id]))
        else:
            aliases = {requested_id}
        aliases.add(requested_id)
        aliases_for[requested_id] = sorted(aliases)
        barcodes_for[requested_id] = {sku_meta[x]['barcode'] for x in aliases if x in sku_meta and sku_meta[x]['barcode']}

    all_ids = sorted({x for group in aliases_for.values() for x in group})
    if not all_ids:
        return {}

    physical_by: dict[str, Decimal] = {}
    storage_by: dict[str, Decimal] = {}
    external_pick_by: dict[str, Decimal] = {}
    ledger_by: dict[str, dict[str, Decimal]] = {}
    movement_by: dict[str, dict[str, Decimal]] = {}

    # FC_K9R4_INDEXED_UUID_READS: inventory_balances.sku_id is UUID. Casting the
    # column to varchar disabled its index and turned a batch truth read into a table scan.
    uuid_ids = []
    for value in all_ids:
        try:
            uuid_ids.append(uuid.UUID(str(value)))
        except Exception:
            pass
    if uuid_ids:
        physical_rows = db.execute(
            select(InventoryBalance.sku_id, func.coalesce(func.sum(InventoryBalance.quantity), 0).label('quantity'))
            .where(
                InventoryBalance.client_id == client_id,
                InventoryBalance.warehouse_id == warehouse_id,
                InventoryBalance.sku_id.in_(uuid_ids),
                InventoryBalance.quantity > 0,
            )
            .group_by(InventoryBalance.sku_id)
        ).all()
        for sku_id_value, quantity in physical_rows:
            physical_by[str(sku_id_value)] = max(Decimal(0), _d(quantity))

    try:
        storage_stmt = text("""
            SELECT CAST(sku_id AS varchar) AS sku_id,COALESCE(SUM(storage_only),0) AS storage_only
            FROM fc_stocks_v2
            WHERE client_id=:client AND warehouse_id=:warehouse AND CAST(sku_id AS varchar) IN :ids
            GROUP BY CAST(sku_id AS varchar)
        """).bindparams(bindparam('ids', expanding=True))
        for row in db.execute(storage_stmt, {'client':str(client_id),'warehouse':str(warehouse_id),'ids':all_ids}).mappings():
            storage_by[str(row['sku_id'])] = max(Decimal(0), _d(row['storage_only']))
    except Exception:
        storage_by = {}

    # FC_K9R37_EXTERNAL_PICK_BATCH_TRUTH: load external holds once for the whole page.
    try:
        external_stmt = text("""
            SELECT sku_id,COALESCE(SUM(quantity),0) AS quantity
            FROM fc_external_pick_allocations
            WHERE client_id=:client AND warehouse_id=:warehouse AND sku_id IN :ids AND status='ACTIVE'
            GROUP BY sku_id
        """).bindparams(bindparam('ids', expanding=True))
        for row in db.execute(external_stmt, {'client':str(client_id),'warehouse':str(warehouse_id),'ids':all_ids}).mappings():
            external_pick_by[str(row['sku_id'])] = max(Decimal(0), _d(row['quantity']))
    except Exception:
        external_pick_by = {}

    try:
        reserve_stmt = text("""
            SELECT CAST(r.sku_id AS varchar) AS sku_id,
              COALESCE(SUM(CASE WHEN o.local_status='NEW' THEN r.quantity ELSE 0 END),0) AS ledger_new,
              COALESCE(SUM(CASE WHEN o.local_status='PICKING' THEN r.quantity ELSE 0 END),0) AS ledger_picking,
              COALESCE(SUM(CASE WHEN COALESCE(o.local_status,'') NOT IN ('NEW','PICKING')
                     AND r.status='PACKING_WRITE_OFF' AND io.id IS NOT NULL THEN r.quantity ELSE 0 END),0) AS accounted_terminal,
              COALESCE(SUM(CASE WHEN COALESCE(o.local_status,'') NOT IN ('NEW','PICKING')
                     AND NOT (r.status='PACKING_WRITE_OFF' AND io.id IS NOT NULL) THEN r.quantity ELSE 0 END),0) AS unaccounted_terminal,
              COALESCE(SUM(CASE WHEN r.source_location_id IS NOT NULL
                     AND NOT (COALESCE(o.local_status,'') NOT IN ('NEW','PICKING')
                              AND r.status='PACKING_WRITE_OFF' AND io.id IS NOT NULL)
                     THEN r.quantity ELSE 0 END),0) AS bound_unavailable
            FROM fc_stock_reservations r
            LEFT JOIN marketplace_orders o ON CAST(o.id AS varchar)=r.order_id
            LEFT JOIN inventory_operations io
              ON CAST(io.client_id AS varchar)=CAST(r.client_id AS varchar) AND COALESCE(io.status,'COMPLETED')='COMPLETED'
             AND io.idempotency_key=('stock-core-v2-pack-' || CAST(o.id AS varchar) || '-' || CAST(r.id AS varchar))
            WHERE r.client_id=:client AND r.warehouse_id=:warehouse AND CAST(r.sku_id AS varchar) IN :ids
              AND r.status IN ('SALE_RESERVED','ACTIVE','CONSUMING','CANCELLING','PACKING_WRITE_OFF')
            GROUP BY CAST(r.sku_id AS varchar)
        """).bindparams(bindparam('ids', expanding=True))
        for row in db.execute(reserve_stmt, {'client':str(client_id),'warehouse':str(warehouse_id),'ids':all_ids}).mappings():
            ledger_by[str(row['sku_id'])] = {name:max(Decimal(0),_d(row.get(name))) for name in (
                'ledger_new','ledger_picking','accounted_terminal','unaccounted_terminal','bound_unavailable'
            )}
    except Exception:
        ledger_by = {}

    # FC_K9R29_FAST_BULK_WB_QUEUE: stock publication needs only canonical sellable
    # truth. Movement-history aggregation is useful for the Inventory UI but is unrelated
    # to the WB target and can scan a large operation ledger. Mass publish opts out.
    try:
        if include_movement and uuid_ids:
            received_expr = func.coalesce(func.sum(case((and_(
                InventoryOperation.operation_type.in_(['RECEIVING','RECEIVING_IMPORT']),
                InventoryOperationLine.source_location_id.is_(None),
                InventoryOperationLine.destination_location_id.is_not(None),
            ), InventoryOperationLine.quantity), else_=0)), 0).label('received')
            shipped_expr = func.coalesce(func.sum(case((and_(
                InventoryOperation.operation_type == 'SHIPPING',
                InventoryOperationLine.source_location_id.is_not(None),
                InventoryOperationLine.destination_location_id.is_(None),
            ), InventoryOperationLine.quantity), else_=0)), 0).label('shipped')
            adjustment_in_expr = func.coalesce(func.sum(case((and_(
                InventoryOperation.operation_type == 'ADJUSTMENT',
                InventoryOperationLine.source_location_id.is_(None),
                InventoryOperationLine.destination_location_id.is_not(None),
            ), InventoryOperationLine.quantity), else_=0)), 0).label('adjustment_in')
            adjustment_out_expr = func.coalesce(func.sum(case((and_(
                InventoryOperation.operation_type == 'ADJUSTMENT',
                InventoryOperationLine.source_location_id.is_not(None),
                InventoryOperationLine.destination_location_id.is_(None),
            ), InventoryOperationLine.quantity), else_=0)), 0).label('adjustment_out')
            inbound_expr = func.coalesce(func.sum(case((and_(
                InventoryOperation.operation_type != 'RECONCILIATION',
                InventoryOperationLine.source_location_id.is_(None),
                InventoryOperationLine.destination_location_id.is_not(None),
            ), InventoryOperationLine.quantity), else_=0)), 0).label('inbound_total')
            outbound_expr = func.coalesce(func.sum(case((and_(
                InventoryOperation.operation_type != 'RECONCILIATION',
                InventoryOperationLine.source_location_id.is_not(None),
                InventoryOperationLine.destination_location_id.is_(None),
            ), InventoryOperationLine.quantity), else_=0)), 0).label('outbound_total')
            movement_rows = db.execute(
                select(
                    InventoryOperationLine.sku_id, received_expr, shipped_expr,
                    adjustment_in_expr, adjustment_out_expr, inbound_expr, outbound_expr,
                )
                .join(InventoryOperation, InventoryOperation.id == InventoryOperationLine.operation_id)
                .where(
                    InventoryOperation.client_id == client_id,
                    InventoryOperation.warehouse_id == warehouse_id,
                    func.coalesce(InventoryOperation.status, 'COMPLETED') == 'COMPLETED',
                    InventoryOperationLine.sku_id.in_(uuid_ids),
                )
                .group_by(InventoryOperationLine.sku_id)
            ).mappings().all()
            for row in movement_rows:
                movement_by[str(row['sku_id'])] = {name:max(Decimal(0),_d(row.get(name))) for name in (
                    'received','shipped','adjustment_in','adjustment_out','inbound_total','outbound_total'
                )}
    except Exception:
        movement_by = {}

    # Initialize the active-order cache once. Subsequent calls are pure set unions.
    _active_order_floor_cache(
        db, client_id=client_id, warehouse_id=warehouse_id, wb_warehouse_id=wb_warehouse_id
    )
    out: dict[str, dict[str, Any]] = {}
    zero_ledger = {name:Decimal(0) for name in ('ledger_new','ledger_picking','accounted_terminal','unaccounted_terminal','bound_unavailable')}
    zero_move = {name:Decimal(0) for name in ('received','shipped','adjustment_in','adjustment_out','inbound_total','outbound_total')}
    group_cache: dict[tuple[str,...], dict[str,Any]] = {}
    for requested_id in requested:
        aliases = aliases_for.get(requested_id,[requested_id])
        group_key = tuple(aliases)
        state = group_cache.get(group_key)
        if state is None:
            physical=sum((physical_by.get(x,Decimal(0)) for x in aliases),Decimal(0))
            storage=sum((storage_by.get(x,Decimal(0)) for x in aliases),Decimal(0))
            storage=min(max(Decimal(0),physical),max(Decimal(0),storage))
            ledger={name:sum((ledger_by.get(x,zero_ledger).get(name,Decimal(0)) for x in aliases),Decimal(0)) for name in zero_ledger}
            floor=active_order_floor_for_identity(
                db,client_id=client_id,warehouse_id=warehouse_id,
                sku_ids=aliases,barcodes=barcodes_for.get(requested_id,set()),
                wb_warehouse_id=wb_warehouse_id,
            )
            sale=max(ledger['ledger_new'],floor['new'])
            picking=max(ledger['ledger_picking'],floor['picking'])
            reserved=sale+picking
            pending=ledger['unaccounted_terminal']
            external_pick=sum((external_pick_by.get(x,Decimal(0)) for x in aliases),Decimal(0))
            free=max(Decimal(0),physical-storage-reserved-pending-external_pick)
            movement={name:sum((movement_by.get(x,zero_move).get(name,Decimal(0)) for x in aliases),Decimal(0)) for name in zero_move}
            movement['recorded_net']=movement['inbound_total']-movement['outbound_total']
            state={
                'identity_sku_ids':aliases,'physical':physical,'storage_only':storage,
                'sale_reserved':sale,'picking_reserved':picking,'reserved':reserved,
                'external_pick_reserved':external_pick,
                'accounting_pending':pending,'accounted_terminal':ledger['accounted_terminal'],
                'ledger_sale_reserved':ledger['ledger_new'],'ledger_picking_reserved':ledger['ledger_picking'],
                'order_new':floor['new'],'order_picking':floor['picking'],
                'reservation_gap':max(Decimal(0),floor['total']-(ledger['ledger_new']+ledger['ledger_picking'])),
                'live_reserved':reserved+pending+external_pick,'available_to_sell':free,
                'bound_unavailable':ledger['bound_unavailable']+external_pick,'safety_hold':pending,
                **movement,'opening_balance_estimate':physical-movement['recorded_net'],
            }
            group_cache[group_key]=state
        out[requested_id]=dict(state)
    return out

def effective_fbs_capacity_for_sku(
    db: Session, *, client_id: Any, warehouse_id: Any, sku_id: Any,
) -> Decimal:
    """Capacity before warehouse-level SALE_RESERVED promises are allocated.

    This deliberately does not subtract the active-order floor: the allocator needs to
    see physical capacity in order to fund missing NEW/PICKING reservations. Address-
    bound live rows and genuinely unaccounted terminal holds remain unavailable.
    """
    ensure_stock_core_v2_schema(db)
    ids = canonical_stock_identity_sku_ids(db, client_id=client_id, sku_id=sku_id)
    params = {"client": str(client_id), "warehouse": str(warehouse_id), "ids": ids}
    physical = _d(db.execute(text(
        "SELECT COALESCE(SUM(quantity),0) FROM inventory_balances "
        "WHERE client_id=:client AND warehouse_id=:warehouse AND sku_id IN :ids AND quantity>0"
    ).bindparams(bindparam("ids", expanding=True)), params).scalar_one() or 0)
    storage = _d(db.execute(text(
        "SELECT COALESCE(SUM(storage_only),0) FROM fc_stocks_v2 "
        "WHERE client_id=:client AND warehouse_id=:warehouse AND sku_id IN :ids"
    ).bindparams(bindparam("ids", expanding=True)), params).scalar_one() or 0)
    ledger = _reservation_truth_for_identity(
        db, client_id=client_id, warehouse_id=warehouse_id, sku_ids=ids
    )
    external_pick = _external_pick_reserved_for_identity(
        db, client_id=client_id, warehouse_id=warehouse_id, sku_ids=ids
    )
    return max(Decimal(0), physical - min(physical, max(Decimal(0), storage)) - ledger["bound_unavailable"] - external_pick)


def effective_fbs_free_for_sku(
    db: Session, *, client_id: Any, warehouse_id: Any, sku_id: Any, wb_warehouse_id: Any | None = None,
) -> Decimal:
    """Hard publication cap: physical - storage - active order floor - real holds."""
    ensure_stock_core_v2_schema(db)
    return _canonical_sale_state(
        db, client_id=client_id, warehouse_id=warehouse_id, sku_id=sku_id,
        wb_warehouse_id=wb_warehouse_id,
    )["available_to_sell"]


def move_bucket(
    db: Session,
    *,
    client_id: Any,
    warehouse_id: Any,
    sku_id: Any,
    location_id: Any,
    container_id: Any | None,
    source: str,
    target: str,
    quantity: Decimal,
) -> None:
    row = reconcile_stock_bucket(
        db,
        client_id=client_id,
        warehouse_id=warehouse_id,
        sku_id=sku_id,
        location_id=location_id,
        container_id=container_id,
        preferred_bucket=source,
        lock=True,
    )
    source_key = {"FBS_AVAILABLE": "fbs_available", "STORAGE_ONLY": "storage_only", "RESERVED": "reserved"}[source]
    target_key = {"FBS_AVAILABLE": "fbs_available", "STORAGE_ONLY": "storage_only", "RESERVED": "reserved"}[target]
    if row[source_key] + EPS < quantity:
        raise HTTPException(409, detail=f"Недостаточно {source}: нужно {quantity}, доступно {row[source_key]}")
    values = {
        "fbs_available": row["fbs_available"],
        "storage_only": row["storage_only"],
        "reserved": row["reserved"],
    }
    values[source_key] -= quantity
    values[target_key] += quantity
    db.execute(
        text(
            """
            UPDATE fc_stocks_v2
            SET fbs_available=:fbs,storage_only=:storage,reserved=:reserved,
                version=version+1,updated_at=CURRENT_TIMESTAMP
            WHERE client_id=:client AND warehouse_id=:warehouse AND sku_id=:sku
              AND location_id=:location AND container_key=:container
            """
        ),
        {
            "fbs": values["fbs_available"],
            "storage": values["storage_only"],
            "reserved": values["reserved"],
            "client": str(client_id),
            "warehouse": str(warehouse_id),
            "sku": str(sku_id),
            "location": str(location_id),
            "container": str(container_id) if container_id else "",
        },
    )



def bind_marketplace_reservation_bucket(
    db: Session,
    *,
    client_id: Any,
    warehouse_id: Any,
    sku_id: Any,
    location_id: Any,
    container_id: Any | None,
    quantity: Decimal,
) -> dict[str, Decimal]:
    """Bind already-promised SALE_RESERVED units to one physical bucket in one write.

    Order arrival already removed SALE_RESERVED units from marketplace sellable truth.
    Starting a picking list must therefore only attach those promises to addresses; it
    must not enqueue WB stock again.  This helper locks the physical bucket once and
    rewrites the technical fc_stocks_v2 classification from canonical physical/storage/
    address-bound ledger truth.  InventoryBalance is never changed here.
    """
    ensure_stock_core_v2_schema(db)
    qty = max(Decimal(0), _d(quantity))
    physical_row = _balance(
        db,
        client_id=client_id,
        warehouse_id=warehouse_id,
        sku_id=sku_id,
        location_id=location_id,
        container_id=container_id,
        lock=True,
    )
    physical = max(Decimal(0), _d(physical_row.quantity if physical_row else 0))
    storage = min(physical, _storage_only_for_bucket(
        db,
        client_id=client_id,
        warehouse_id=warehouse_id,
        sku_id=sku_id,
        location_id=location_id,
        container_id=container_id,
    ))
    live_before = _live_reserved_for_bucket_truth(
        db,
        client_id=client_id,
        warehouse_id=warehouse_id,
        sku_id=sku_id,
        location_id=location_id,
        container_id=container_id,
    )
    free = max(Decimal(0), physical - storage - live_before)
    if free + EPS < qty:
        raise HTTPException(
            409,
            detail=f"Недостаточно товара в выбранной ячейке: нужно {qty}, свободно {free}",
        )
    reserved_after = live_before + qty
    fbs_after = max(Decimal(0), physical - storage - reserved_after)
    params = {
        "client": str(client_id),
        "warehouse": str(warehouse_id),
        "sku": str(sku_id),
        "location": str(location_id),
        "container": str(container_id) if container_id else "",
        "fbs": fbs_after,
        "storage": storage,
        "reserved": reserved_after,
        "physical": physical,
    }
    # Lock an existing classification row if present, then canonicalize only this bucket.
    existing = db.execute(text(
        "SELECT 1 FROM fc_stocks_v2 WHERE client_id=:client AND warehouse_id=:warehouse "
        "AND sku_id=:sku AND location_id=:location AND container_key=:container FOR UPDATE"
    ), params).first()
    if existing:
        db.execute(text(
            """
            UPDATE fc_stocks_v2
            SET fbs_available=:fbs,storage_only=:storage,reserved=:reserved,
                physical_snapshot=:physical,version=version+1,updated_at=CURRENT_TIMESTAMP
            WHERE client_id=:client AND warehouse_id=:warehouse AND sku_id=:sku
              AND location_id=:location AND container_key=:container
            """
        ), params)
    else:
        db.execute(text(
            """
            INSERT INTO fc_stocks_v2(
                client_id,warehouse_id,sku_id,location_id,container_key,
                fbs_available,storage_only,reserved,physical_snapshot
            ) VALUES(
                :client,:warehouse,:sku,:location,:container,
                :fbs,:storage,:reserved,:physical
            )
            """
        ), params)
    return {
        "physical": physical,
        "storage_only": storage,
        "reserved_before": live_before,
        "reserved_after": reserved_after,
        "fbs_available": fbs_after,
        "free_before": free,
    }


def wb_sales_paused(
    db: Session,
    *,
    client_id: Any,
    warehouse_id: Any,
    sku_id: Any,
) -> bool:
    """Return whether this physical SKU is intentionally blocked from WB sale.

    The flag is local and durable. Reservations keep working while the publish target stays
    at zero, so cancelling an old order cannot accidentally put the item back on sale.
    """
    from app.services.wb_sales_control import paused_ids
    return str(sku_id) in paused_ids(db, client_id, warehouse_id, [sku_id])


def set_wb_sales_paused(
    db: Session,
    *,
    client_id: Any,
    warehouse_id: Any,
    sku_id: Any,
    paused: bool,
    performed_by: str | None = None,
    reason: str | None = None,
) -> None:
    if paused:
        from app.services.wb_sales_control import remove_exception
        remove_exception(db,client_id,warehouse_id,sku_id)
        db.execute(
            text(
                """
                INSERT INTO fc_wb_sales_pauses(client_id,warehouse_id,sku_id,paused,paused_by,reason,paused_at,updated_at)
                VALUES(:client,:warehouse,:sku,TRUE,:by,:reason,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)
                ON CONFLICT(client_id,warehouse_id,sku_id) DO UPDATE SET
                    paused=TRUE,paused_by=EXCLUDED.paused_by,reason=EXCLUDED.reason,
                    paused_at=CURRENT_TIMESTAMP,updated_at=CURRENT_TIMESTAMP
                """
            ),
            {"client": str(client_id), "warehouse": str(warehouse_id), "sku": str(sku_id),
             "by": (performed_by or None), "reason": (reason or None)},
        )
    else:
        db.execute(
            text(
                "UPDATE fc_wb_sales_pauses SET paused=FALSE,updated_at=CURRENT_TIMESTAMP "
                "WHERE client_id=:client AND warehouse_id=:warehouse AND sku_id=:sku"
            ),
            {"client": str(client_id), "warehouse": str(warehouse_id), "sku": str(sku_id)},
        )


def enqueue_wb_stock_for_sku(
    db: Session,
    *,
    client_id: Any,
    warehouse_id: Any,
    sku: SKU,
    credential_id: Any | None,
    external_warehouse_id: Any | None,
    reason: str,
) -> int:
    """Queue one absolute WB stock target using only local database state.

    This helper deliberately performs no WB network I/O.  It is safe to call from
    inventory controls, cancellation handlers and reservation transitions while the
    durable outbox worker publishes the target asynchronously.
    """
    ensure_stock_core_v2_schema(db)
    params = {"client": str(client_id), "warehouse": str(warehouse_id), "sku": str(sku.id)}
    # FC_K9R78_PAUSE_FAST_ZERO: a paused SKU is always zero; skip expensive reserve truth.
    paused = wb_sales_paused(db, client_id=client_id, warehouse_id=warehouse_id, sku_id=sku.id)
    if paused:
        desired = 0
    else:
        canonical_free = effective_fbs_free_for_sku(
            db, client_id=client_id, warehouse_id=warehouse_id, sku_id=sku.id,
            wb_warehouse_id=external_warehouse_id,
        )
        desired = max(0, int(canonical_free))
    product_key = str(sku.barcode or sku.sku_code or sku.article or "").strip()
    if not product_key or _is_internal_wb_product_key(product_key):
        return desired
    dedupe = f"WB:{client_id}:{warehouse_id}:{sku.id}"
    db.execute(
        text(
            """
            INSERT INTO fc_marketplace_stock_outbox_v2(
                id,client_id,warehouse_id,sku_id,provider,credential_id,
                external_warehouse_id,product_key,desired_amount,reason,dedupe_key
            ) VALUES(:id,:client,:warehouse,:sku,'WB',:credential,:external_wh,:product_key,:desired,:reason,:dedupe)
            ON CONFLICT(dedupe_key) DO UPDATE SET
                credential_id=EXCLUDED.credential_id,
                external_warehouse_id=EXCLUDED.external_warehouse_id,
                product_key=EXCLUDED.product_key,
                desired_amount=EXCLUDED.desired_amount,reason=EXCLUDED.reason,
                status='PENDING',attempts=0,next_attempt_at=CURRENT_TIMESTAMP,last_error=NULL,updated_at=CURRENT_TIMESTAMP
            """
        ),
        {
            "id": str(uuid.uuid4()),
            "client": str(client_id),
            "warehouse": str(warehouse_id),
            "sku": str(sku.id),
            "credential": str(credential_id or "") or None,
            "external_wh": str(external_warehouse_id or "") or None,
            "product_key": product_key,
            "desired": desired,
            "reason": str(reason or "INVENTORY_CONTROL")[:80],
            "dedupe": dedupe,
        },
    )
    return desired


def enqueue_marketplace_stock(
    db: Session,
    *,
    order: MarketplaceOrder,
    warehouse_id: Any,
    sku: SKU,
    reason: str,
) -> None:
    ensure_stock_core_v2_schema(db)
    provider = "OZON" if str(getattr(order, "provider", "WB") or "WB").upper() == "OZON" else "WB"
    # Canonical FBS truth: physical - explicit storage-only - every live reservation.
    # This deliberately ignores the legacy fc_stocks_v2.reserved counter, which may
    # retain historical residue after old reservation/recovery flows.
    external_wh = str(getattr(order, "wb_warehouse_id", None) or ((getattr(order, "raw_data", None) or {}).get("warehouse_id")) or "") or None
    canonical_free = effective_fbs_free_for_sku(
        db, client_id=order.client_id, warehouse_id=warehouse_id, sku_id=sku.id,
        wb_warehouse_id=external_wh if provider == "WB" else None,
    )
    desired = 0 if (provider == "WB" and wb_sales_paused(
        db, client_id=order.client_id, warehouse_id=warehouse_id, sku_id=sku.id
    )) else max(0, int(canonical_free))
    product_key = str(sku.barcode or sku.sku_code or sku.article or "").strip()
    if provider == "WB" and _is_internal_wb_product_key(product_key):
        return
    original_credential = db.get(MarketplaceCredential, getattr(order, "credential_id", None)) if getattr(order, "credential_id", None) else None
    active_stmt = select(MarketplaceCredential.id).where(
        MarketplaceCredential.client_id == order.client_id,
        MarketplaceCredential.provider == provider,
        MarketplaceCredential.is_active.is_(True),
    )
    if original_credential and str(original_credential.name or "").strip():
        active_stmt = active_stmt.where(MarketplaceCredential.name == original_credential.name)
    active_credential = db.execute(active_stmt.order_by(MarketplaceCredential.updated_at.desc()).limit(1)).scalar_one_or_none()
    credential = str(active_credential or getattr(order, "credential_id", None) or "") or None
    dedupe = f"{provider}:{order.client_id}:{warehouse_id}:{sku.id}"
    db.execute(
        text(
            """
            INSERT INTO fc_marketplace_stock_outbox_v2(
                id,client_id,warehouse_id,sku_id,provider,credential_id,
                external_warehouse_id,product_key,desired_amount,reason,dedupe_key
            ) VALUES(:id,:client,:warehouse,:sku,:provider,:credential,:external_wh,:product_key,:desired,:reason,:dedupe)
            ON CONFLICT(dedupe_key) DO UPDATE SET
                credential_id=EXCLUDED.credential_id,external_warehouse_id=EXCLUDED.external_warehouse_id,
                product_key=EXCLUDED.product_key,desired_amount=EXCLUDED.desired_amount,reason=EXCLUDED.reason,
                status='PENDING',attempts=0,next_attempt_at=CURRENT_TIMESTAMP,last_error=NULL,updated_at=CURRENT_TIMESTAMP
            """
        ),
        {
            "id": str(uuid.uuid4()),
            "client": str(order.client_id),
            "warehouse": str(warehouse_id),
            "sku": str(sku.id),
            "provider": provider,
            "credential": credential,
            "external_wh": external_wh,
            "product_key": product_key,
            "desired": desired,
            "reason": reason,
            "dedupe": dedupe,
        },
    )


def reserve_marketplace_unit(
    db: Session,
    *,
    order: MarketplaceOrder,
    warehouse_id: Any,
    sku_id: Any,
    location_id: Any,
    container_id: Any | None,
    reservation_id: str,
) -> str:
    order_id = ensure_canonical_marketplace_order(db, order, warehouse_id, sku_id)
    move_bucket(
        db,
        client_id=order.client_id,
        warehouse_id=warehouse_id,
        sku_id=sku_id,
        location_id=location_id,
        container_id=container_id,
        source="FBS_AVAILABLE",
        target="RESERVED",
        quantity=Decimal(1),
    )
    db.execute(
        text(
            "UPDATE fc_stock_reservations SET source_bucket='FBS_AVAILABLE',stock_core_order_id=:order_id,"
            "status='ACTIVE',updated_at=CURRENT_TIMESTAMP,note='STOCK_CORE_V2_RESERVED' WHERE id=:reservation_id"
        ),
        {"order_id": order_id, "reservation_id": reservation_id},
    )
    db.execute(text("UPDATE fc_orders_v2 SET status='RESERVED' WHERE id=:id"), {"id": order_id})
    sku = db.get(SKU, sku_id)
    if sku:
        enqueue_marketplace_stock(db, order=order, warehouse_id=warehouse_id, sku=sku, reason="RESERVE_PICKING_LIST")
    return order_id


def consume_marketplace_reservation(
    db: Session,
    *,
    order: MarketplaceOrder,
    reservation: dict[str, Any],
    note: str,
) -> dict[str, Any]:
    warehouse_id = uuid.UUID(str(reservation["warehouse_id"]))
    sku_id = uuid.UUID(str(reservation["sku_id"]))
    # FC_K9R79_UNBOUND_RESERVE_PACK: after a clean reset the reservation first tries
    # replacement FBS. After "FBS -> MAIN" it may additionally take one unit back from
    # MAIN when no new FBS has arrived, then normal packing performs the one physical write-off.
    if not reservation.get("source_location_id"):
        candidates = db.execute(select(InventoryBalance).where(
            InventoryBalance.client_id == order.client_id,
            InventoryBalance.warehouse_id == warehouse_id,
            InventoryBalance.sku_id == sku_id,
            InventoryBalance.quantity > 0,
        ).order_by(InventoryBalance.updated_at, InventoryBalance.id).with_for_update()).scalars().all()
        rebound = None
        for balance in candidates:
            try:
                bind_marketplace_reservation_bucket(
                    db, client_id=order.client_id, warehouse_id=warehouse_id, sku_id=sku_id,
                    location_id=balance.location_id, container_id=balance.container_id, quantity=Decimal(1),
                )
                rebound = balance
                break
            except HTTPException as exc:
                if exc.status_code != 409:
                    raise
        reserve_note=str(reservation.get("note") or "")
        if rebound is None and "FBS_TO_MAIN:" in reserve_note:
            for balance in candidates:
                row=reconcile_stock_bucket(
                    db,client_id=order.client_id,warehouse_id=warehouse_id,sku_id=sku_id,
                    location_id=balance.location_id,container_id=balance.container_id,
                    preferred_bucket="STORAGE_ONLY",lock=True,
                )
                if row["storage_only"] < Decimal(1) - EPS:
                    continue
                move_bucket(
                    db,client_id=order.client_id,warehouse_id=warehouse_id,sku_id=sku_id,
                    location_id=balance.location_id,container_id=balance.container_id,
                    source="STORAGE_ONLY",target="FBS_AVAILABLE",quantity=Decimal(1),
                )
                bind_marketplace_reservation_bucket(
                    db,client_id=order.client_id,warehouse_id=warehouse_id,sku_id=sku_id,
                    location_id=balance.location_id,container_id=balance.container_id,quantity=Decimal(1),
                )
                rebound=balance
                break
        if rebound is not None:
            db.execute(text(
                "UPDATE fc_stock_reservations SET source_location_id=:loc,source_container_id=:box,"
                "status=CASE WHEN status='SALE_RESERVED' THEN 'ACTIVE' ELSE status END,"
                "updated_at=CURRENT_TIMESTAMP,note=CONCAT('FBS_R79_REBOUND; ',COALESCE(note,'')) WHERE id=:id"
            ), {"loc":str(rebound.location_id),"box":str(rebound.container_id) if rebound.container_id else None,"id":str(reservation["id"])})
            reservation = dict(reservation)
            reservation["source_location_id"] = str(rebound.location_id)
            reservation["source_container_id"] = str(rebound.container_id) if rebound.container_id else None
        elif "FBS_CLEAN_RESET:" in reserve_note or "FBS_FORCE_REMOVE:" in reserve_note:
            db.execute(text(
                "UPDATE fc_stock_reservations SET status='CONSUMED',consumed_at=CURRENT_TIMESTAMP,"
                "updated_at=CURRENT_TIMESTAMP,note=:note WHERE id=:id"
            ), {"note":f"FBS_CLEAN_RESET_ALREADY_PHYSICAL:{note}","id":str(reservation["id"])})
            db.execute(text(
                "UPDATE fc_orders_v2 SET status='WRITTEN_OFF',scan_completed_at=COALESCE(scan_completed_at,CURRENT_TIMESTAMP),"
                "written_off_at=CURRENT_TIMESTAMP WHERE source_order_id=:source"
            ), {"source":str(order.id)})
            return {"changed":True,"reservation_id":str(reservation["id"]),"operation_id":None,"physical_already_removed":True}
        else:
            raise HTTPException(409, detail="Резерв заказа не привязан к ячейке и свободного FBS остатка для перепривязки нет")
    location_id = uuid.UUID(str(reservation["source_location_id"]))
    container_id = uuid.UUID(str(reservation["source_container_id"])) if reservation.get("source_container_id") else None
    row = reconcile_stock_bucket(
        db,
        client_id=order.client_id,
        warehouse_id=warehouse_id,
        sku_id=sku_id,
        location_id=location_id,
        container_id=container_id,
        preferred_bucket="FBS_AVAILABLE",
        lock=True,
    )
    if row["reserved"] < Decimal(1) - EPS:
        raise HTTPException(409, detail="Резерв Stock Core v2 отсутствует")
    # 0.9.4.3 PACKING FIFO STABILITY:
    # The global location guard protects ACTIVE/CONSUMING reservations from manual
    # stock edits. Final packing is different: this very transaction is consuming the
    # reserved unit. Hide this reservation from the guard only inside the current
    # uncommitted transaction before execute_operation() flushes InventoryBalance.
    # PostgreSQL MVCC means other sessions still see the previous ACTIVE row until the
    # final commit. Any exception rolls this temporary state back together with stock.
    db.execute(
        text(
            "UPDATE fc_stock_reservations SET status='PACKING_WRITE_OFF', "
            "updated_at=CURRENT_TIMESTAMP,note=:note "
            "WHERE id=:id AND status IN ('ACTIVE','CONSUMING')"
        ),
        {"id": str(reservation["id"]), "note": f"PACKING_WRITE_OFF:{note}"},
    )
    operation = execute_operation(
        db,
        OperationCreate(
            client_id=order.client_id,
            warehouse_id=warehouse_id,
            operation_type="SHIPPING",
            idempotency_key=f"stock-core-v2-pack-{order.id}-{reservation['id']}",
            reference=f"FBS {getattr(order, 'external_order_num', None) or order.external_order_id}",
            comment="Stock Core v2: финальное списание reserved после завершения сканирования",
            lines=[
                StockLine(
                    sku_id=sku_id,
                    quantity=Decimal(1),
                    source_location_id=location_id,
                    source_container_id=container_id,
                    reason="STOCK_CORE_V2_RESERVED_WRITE_OFF",
                )
            ],
        ),
        commit=False,
    )
    db.execute(
        text(
            """
            UPDATE fc_stocks_v2
            SET reserved=reserved-1,physical_snapshot=GREATEST(0,physical_snapshot-1),
                version=version+1,updated_at=CURRENT_TIMESTAMP
            WHERE client_id=:client AND warehouse_id=:warehouse AND sku_id=:sku
              AND location_id=:location AND container_key=:container AND reserved>=1
            """
        ),
        {
            "client": str(order.client_id),
            "warehouse": str(warehouse_id),
            "sku": str(sku_id),
            "location": str(location_id),
            "container": str(container_id) if container_id else "",
        },
    )
    db.execute(
        text(
            "UPDATE fc_stock_reservations SET status='CONSUMED',consumed_at=CURRENT_TIMESTAMP,"
            "updated_at=CURRENT_TIMESTAMP,note=:note WHERE id=:id"
        ),
        {"note": note, "id": str(reservation["id"])},
    )
    db.execute(
        text(
            "UPDATE fc_orders_v2 SET status='WRITTEN_OFF',scan_completed_at=COALESCE(scan_completed_at,CURRENT_TIMESTAMP),"
            "written_off_at=CURRENT_TIMESTAMP WHERE source_order_id=:source"
        ),
        {"source": str(order.id)},
    )
    return {"changed": True, "reservation_id": str(reservation["id"]), "operation_id": str(operation.id)}


def recover_marketplace_packing_write_off(
    db: Session, *, order: MarketplaceOrder, reservation: dict[str, Any], note: str,
) -> dict[str, Any]:
    """Finish a historical PACKING_WRITE_OFF without a second physical write-off.

    Older code committed the SHIPPING operation before committing the reservation state.
    If that operation exists, stock is already physically gone and only ledger/bucket
    bookkeeping is repaired.  If it does not exist, the row is safely returned to ACTIVE
    and consumed through the normal atomic path.
    """
    rid = str(reservation.get("id") or "")
    if not rid:
        return {"changed": False, "reason": "reservation_id_missing"}
    key = f"stock-core-v2-pack-{order.id}-{rid}"
    operation = db.execute(select(InventoryOperation).where(
        InventoryOperation.client_id == order.client_id,
        InventoryOperation.idempotency_key == key,
    )).scalars().first()
    if not operation:
        db.execute(text(
            "UPDATE fc_stock_reservations SET status='ACTIVE',updated_at=CURRENT_TIMESTAMP,note=:note "
            "WHERE id=:id AND status='PACKING_WRITE_OFF'"
        ), {"id": rid, "note": f"PACKING_WRITE_OFF_RETRY:{note}"})
        refreshed = dict(reservation)
        refreshed["status"] = "ACTIVE"
        result = consume_marketplace_reservation(db, order=order, reservation=refreshed, note=note)
        return {**result, "recovered_packing_write_off": True, "physical_operation_replayed": True}

    warehouse_id = uuid.UUID(str(reservation["warehouse_id"]))
    sku_id = uuid.UUID(str(reservation["sku_id"]))
    location_id = uuid.UUID(str(reservation["source_location_id"]))
    container_id = uuid.UUID(str(reservation["source_container_id"])) if reservation.get("source_container_id") else None
    db.execute(text("""
        UPDATE fc_stocks_v2
        SET reserved=GREATEST(0,reserved-1),version=version+1,updated_at=CURRENT_TIMESTAMP
        WHERE client_id=:client AND warehouse_id=:warehouse AND sku_id=:sku
          AND location_id=:location AND container_key=:container
    """), {
        "client": str(order.client_id), "warehouse": str(warehouse_id), "sku": str(sku_id),
        "location": str(location_id), "container": str(container_id) if container_id else "",
    })
    db.execute(text(
        "UPDATE fc_stock_reservations SET status='CONSUMED',consumed_at=COALESCE(consumed_at,CURRENT_TIMESTAMP),"
        "updated_at=CURRENT_TIMESTAMP,note=:note WHERE id=:id AND status='PACKING_WRITE_OFF'"
    ), {"id": rid, "note": f"PACKING_WRITE_OFF_RECOVERED:{note}"})
    db.execute(text(
        "UPDATE fc_orders_v2 SET status='WRITTEN_OFF',scan_completed_at=COALESCE(scan_completed_at,CURRENT_TIMESTAMP),"
        "written_off_at=COALESCE(written_off_at,CURRENT_TIMESTAMP) WHERE source_order_id=:source"
    ), {"source": str(order.id)})
    reconcile_stock_bucket(
        db, client_id=order.client_id, warehouse_id=warehouse_id, sku_id=sku_id,
        location_id=location_id, container_id=container_id, preferred_bucket="FBS_AVAILABLE", lock=True,
    )
    sku = db.get(SKU, sku_id)
    if sku:
        enqueue_marketplace_stock(db, order=order, warehouse_id=warehouse_id, sku=sku, reason="PACKING_WRITE_OFF_RECOVERED")
    return {
        "changed": True, "reservation_id": rid, "operation_id": str(operation.id),
        "recovered_packing_write_off": True, "physical_operation_replayed": False,
    }


def release_or_restore_marketplace_reservation(
    db: Session,
    *,
    order: MarketplaceOrder,
    reservation: dict[str, Any],
    restore_consumed: bool,
    note: str,
) -> dict[str, Any]:
    warehouse_id = uuid.UUID(str(reservation["warehouse_id"]))
    sku_id = uuid.UUID(str(reservation["sku_id"]))
    status = str(reservation.get("status") or "").upper()
    source = str(reservation.get("source_bucket") or "FBS_AVAILABLE").upper()
    reserve_note = str(reservation.get("note") or "")
    if not reservation.get("source_location_id") and status in {"SALE_RESERVED","ACTIVE"} and any(
        marker in reserve_note for marker in ("FBS_CLEAN_RESET:","FBS_TO_MAIN:","FBS_FORCE_REMOVE:")
    ):
        db.execute(text(
            "UPDATE fc_stock_reservations SET status='RELEASED',released_at=CURRENT_TIMESTAMP,"
            "updated_at=CURRENT_TIMESTAMP,note=:note WHERE id=:id"
        ), {"id":str(reservation["id"]),"note":f"R79_UNBOUND_RELEASE:{note}"})
        sku=db.get(SKU,sku_id)
        if sku:
            enqueue_marketplace_stock(db,order=order,warehouse_id=warehouse_id,sku=sku,reason="UNDO_OR_RELEASE_UNBOUND_R79")
        db.execute(text(
            "UPDATE fc_orders_v2 SET status='CANCELLED',cancelled_at=CURRENT_TIMESTAMP WHERE source_order_id=:source"
        ), {"source":str(order.id)})
        return {"changed":True,"reservation_id":str(reservation["id"]),"restored_physical":False,"unbound_reset_release":True}
    if not reservation.get("source_location_id"):
        return {"changed":False,"reason":"unbound_without_reset_marker"}
    location_id = uuid.UUID(str(reservation["source_location_id"]))
    container_id = uuid.UUID(str(reservation["source_container_id"])) if reservation.get("source_container_id") else None
    if status == "ACTIVE":
        move_bucket(
            db,
            client_id=order.client_id,
            warehouse_id=warehouse_id,
            sku_id=sku_id,
            location_id=location_id,
            container_id=container_id,
            source="RESERVED",
            target=source,
            quantity=Decimal(1),
        )
        db.execute(
            text(
                "UPDATE fc_stock_reservations SET status='RELEASED',released_at=CURRENT_TIMESTAMP,"
                "updated_at=CURRENT_TIMESTAMP,note=:note WHERE id=:id"
            ),
            {"id": str(reservation["id"]), "note": note},
        )
    elif status == "CONSUMED" and restore_consumed:
        execute_operation(
            db,
            OperationCreate(
                client_id=order.client_id,
                warehouse_id=warehouse_id,
                operation_type="ADJUSTMENT",
                idempotency_key=f"stock-core-v2-restore-{order.id}-{reservation['id']}",
                reference=f"UNDO {getattr(order, 'external_order_num', None) or order.external_order_id}",
                comment="Отмена списания Stock Core v2",
                lines=[
                    StockLine(
                        sku_id=sku_id,
                        quantity=Decimal(1),
                        destination_location_id=location_id,
                        destination_container_id=container_id,
                        reason="STOCK_CORE_V2_UNDO_WRITE_OFF",
                    )
                ],
            ),
        )
        reconcile_stock_bucket(
            db,
            client_id=order.client_id,
            warehouse_id=warehouse_id,
            sku_id=sku_id,
            location_id=location_id,
            container_id=container_id,
            preferred_bucket=source,
            lock=True,
        )
        db.execute(
            text(
                "UPDATE fc_stock_reservations SET status='RESTORED',released_at=CURRENT_TIMESTAMP,"
                "updated_at=CURRENT_TIMESTAMP,note=:note WHERE id=:id"
            ),
            {"id": str(reservation["id"]), "note": note},
        )
    else:
        return {"changed": False, "reason": f"status_{status.lower()}"}

    sku = db.get(SKU, sku_id)
    if source == "FBS_AVAILABLE" and sku:
        enqueue_marketplace_stock(db, order=order, warehouse_id=warehouse_id, sku=sku, reason="UNDO_OR_RELEASE")
    db.execute(
        text(
            "UPDATE fc_orders_v2 SET status=CAST(:status AS varchar),cancelled_at=CASE WHEN CAST(:status AS varchar)='CANCELLED' THEN CURRENT_TIMESTAMP ELSE cancelled_at END "
            "WHERE source_order_id=:source"
        ),
        {"status": "RESTORED" if status == "CONSUMED" else "CANCELLED", "source": str(order.id)},
    )
    return {"changed": True, "reservation_id": str(reservation["id"]), "restored_physical": status == "CONSUMED"}
