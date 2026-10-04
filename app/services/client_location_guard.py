from __future__ import annotations

from collections import defaultdict
from typing import Any

from fastapi import HTTPException
from sqlalchemy import event, inspect as sa_inspect, select
from sqlalchemy.orm import Session

from app.models import Container, InventoryBalance, Location


EPSILON = 0.000001
from app.services.runtime_reliability import SYSTEM_LOCATION_CODES
from app.services.stock_reservations import active_reserved_for_bucket, reservation_schema_ready

SHARED_SYSTEM_LOCATIONS = set(SYSTEM_LOCATION_CODES)


def _number(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _attribute_changed(obj: Any, name: str) -> bool:
    try:
        return bool(sa_inspect(obj).attrs[name].history.has_changes())
    except Exception:
        # A conservative answer is safer for warehouse ownership: the final owner
        # check still permits the operation when the cell belongs to this client.
        return True


def _balance_enters_location(balance: InventoryBalance, is_new: bool) -> bool:
    current = _number(balance.quantity)
    if current <= EPSILON:
        return False
    if is_new:
        return True
    if any(_attribute_changed(balance, field) for field in ("location_id", "client_id", "warehouse_id")):
        return True
    try:
        history = sa_inspect(balance).attrs["quantity"].history
        if not history.has_changes():
            return False
        if history.deleted:
            return current > _number(history.deleted[0]) + EPSILON
    except Exception:
        pass
    return True


def _container_enters_location(container: Container, is_new: bool) -> bool:
    if not container.location_id or str(container.status or "").upper() != "ACTIVE":
        return False
    if is_new:
        return True
    return any(_attribute_changed(container, field) for field in ("location_id", "client_id", "warehouse_id"))


def _history_old_value(obj: Any, name: str, current: Any) -> Any:
    try:
        history = sa_inspect(obj).attrs[name].history
        if history.deleted:
            return history.deleted[0]
    except Exception:
        pass
    return current


def _guard_reserved_stock_quantities(session: Session) -> None:
    """Never let a normal warehouse operation consume stock promised to a picking list.

    Packing/cancellation first changes its own reservation out of ACTIVE inside the same
    transaction, so the exact reserved unit can still be consumed/moved intentionally.
    """
    if not reservation_schema_ready():
        return
    candidates = []
    for balance in list(session.dirty):
        if not isinstance(balance, InventoryBalance):
            continue
        current_qty = _number(balance.quantity)
        old_qty = _number(_history_old_value(balance, "quantity", balance.quantity))
        old_location = _history_old_value(balance, "location_id", balance.location_id)
        old_container = _history_old_value(balance, "container_id", balance.container_id)
        old_client = _history_old_value(balance, "client_id", balance.client_id)
        old_warehouse = _history_old_value(balance, "warehouse_id", balance.warehouse_id)
        old_sku = _history_old_value(balance, "sku_id", balance.sku_id)
        bucket_changed = any(_attribute_changed(balance, x) for x in ("location_id", "container_id", "client_id", "warehouse_id", "sku_id"))
        projected_old_bucket_qty = 0.0 if bucket_changed else current_qty
        if old_qty > projected_old_bucket_qty + EPSILON and old_client and old_warehouse and old_sku and old_location:
            candidates.append((balance, old_client, old_warehouse, old_sku, old_location, old_container, projected_old_bucket_qty))
    for balance in list(session.deleted):
        if isinstance(balance, InventoryBalance) and balance.client_id and balance.warehouse_id and balance.sku_id and balance.location_id:
            candidates.append((balance, balance.client_id, balance.warehouse_id, balance.sku_id, balance.location_id, balance.container_id, 0.0))

    for _balance, client_id, warehouse_id, sku_id, location_id, container_id, projected_qty in candidates:
        with session.no_autoflush:
            reserved = float(active_reserved_for_bucket(
                session,
                client_id=client_id,
                warehouse_id=warehouse_id,
                sku_id=sku_id,
                location_id=location_id,
                container_id=container_id,
            ))
        if projected_qty + EPSILON >= reserved:
            continue
        with session.no_autoflush:
            location = session.get(Location, location_id)
        label = str(getattr(location, "code", "") or location_id)
        raise HTTPException(
            status_code=409,
            detail=(
                f"В ячейке «{label}» зарезервировано {reserved:g} шт. под активные сборочные листы. "
                f"После этой операции осталось бы {projected_qty:g} шт. Сначала завершите/отмените соответствующие заказы либо используйте свободный остаток."
            ),
        )


def _guard_client_location_ownership(session: Session, _flush_context: Any, _instances: Any) -> None:
    """Prevent two clients from occupying one physical cell at the same time.

    Ownership is derived from real positive balances and boxes, so no database
    migration is required. As soon as a cell has neither stock nor boxes it becomes
    free for another client. Shared virtual addresses remain available to everyone.
    """
    _guard_reserved_stock_quantities(session)

    new_objects = list(session.new)
    dirty_objects = [obj for obj in session.dirty if obj not in session.new]
    touched = new_objects + dirty_objects
    new_ids = {id(obj) for obj in new_objects}

    placements: list[tuple[str, Any, Any]] = []
    for obj in touched:
        if isinstance(obj, InventoryBalance) and _balance_enters_location(obj, id(obj) in new_ids):
            if obj.client_id and obj.warehouse_id and obj.location_id:
                placements.append((str(obj.client_id), obj.warehouse_id, obj.location_id))
        elif isinstance(obj, Container) and _container_enters_location(obj, id(obj) in new_ids):
            if obj.client_id and obj.warehouse_id and obj.location_id:
                placements.append((str(obj.client_id), obj.warehouse_id, obj.location_id))

    if not placements:
        return

    warehouse_ids = {warehouse_id for _, warehouse_id, _ in placements}
    location_ids = {location_id for _, _, location_id in placements}

    # Serialize concurrent placements through the physical Location rows. Without
    # this lock, two terminals could both see an empty cell a few milliseconds apart.
    ordered_location_ids = sorted(location_ids, key=str)
    with session.no_autoflush:
        locations = session.execute(
            select(Location)
            .where(Location.id.in_(ordered_location_ids))
            .order_by(Location.id)
            .with_for_update()
        ).scalars().all()
    location_by_id = {str(row.id): row for row in locations}
    for obj in touched:
        if isinstance(obj, Location) and obj.id:
            location_by_id[str(obj.id)] = obj

    physical_placements: list[tuple[str, Any, Any]] = []
    for client_id, warehouse_id, location_id in placements:
        location = location_by_id.get(str(location_id))
        code = str(getattr(location, "code", "") or "").strip().upper()
        if code not in SHARED_SYSTEM_LOCATIONS:
            physical_placements.append((client_id, warehouse_id, location_id))
    if not physical_placements:
        return

    keys = {(str(warehouse_id), str(location_id)) for _, warehouse_id, location_id in physical_placements}
    owners: dict[tuple[str, str], set[str]] = defaultdict(set)
    deleted = session.deleted

    with session.no_autoflush:
        balances = session.execute(
            select(InventoryBalance).where(
                InventoryBalance.warehouse_id.in_(list(warehouse_ids)),
                InventoryBalance.location_id.in_(ordered_location_ids),
                InventoryBalance.quantity > EPSILON,
            )
        ).scalars().all()
        containers = session.execute(
            select(Container).where(
                Container.warehouse_id.in_(list(warehouse_ids)),
                Container.location_id.in_(ordered_location_ids),
                Container.status == "ACTIVE",
            )
        ).scalars().all()

    # SQL filters use the values currently stored in PostgreSQL. Re-check every
    # returned object in Python because an identity-mapped row may have been moved
    # or emptied inside this same transaction before the flush.
    for balance in balances:
        key = (str(balance.warehouse_id), str(balance.location_id))
        if balance not in deleted and key in keys and balance.client_id and _number(balance.quantity) > EPSILON:
            owners[key].add(str(balance.client_id))
    for container in containers:
        key = (str(container.warehouse_id), str(container.location_id))
        if container not in deleted and key in keys and container.client_id and str(container.status or "").upper() == "ACTIVE":
            owners[key].add(str(container.client_id))

    # New rows and rows moved into the cell are not necessarily returned by the
    # database queries above until after flush, so include their projected state.
    for obj in touched:
        if obj in deleted:
            continue
        if isinstance(obj, InventoryBalance) and obj.client_id and obj.warehouse_id and obj.location_id and _number(obj.quantity) > EPSILON:
            key = (str(obj.warehouse_id), str(obj.location_id))
            if key in keys:
                owners[key].add(str(obj.client_id))
        elif isinstance(obj, Container) and obj.client_id and obj.warehouse_id and obj.location_id and str(obj.status or "").upper() == "ACTIVE":
            key = (str(obj.warehouse_id), str(obj.location_id))
            if key in keys:
                owners[key].add(str(obj.client_id))

    for client_id, warehouse_id, location_id in physical_placements:
        key = (str(warehouse_id), str(location_id))
        other_clients = owners.get(key, set()) - {client_id}
        if not other_clients:
            continue
        location = location_by_id.get(str(location_id))
        label = str(getattr(location, "code", "") or location_id)
        raise HTTPException(
            status_code=409,
            detail=(
                f"Ячейка «{label}» уже занята товаром или коробом другого клиента. "
                "Выберите свободную ячейку либо сначала полностью освободите эту ячейку."
            ),
        )


def install_client_location_guard() -> None:
    """Register the guard once for every SQLAlchemy session in the API process."""
    if not event.contains(Session, "before_flush", _guard_client_location_ownership):
        event.listen(Session, "before_flush", _guard_client_location_ownership)
