import uuid
from datetime import datetime, timezone
from decimal import Decimal

from fastapi import HTTPException, status
from sqlalchemy import bindparam, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.models import Container, InventoryBalance, InventoryOperation, InventoryOperationLine, Location, MarketplaceOrder, SKU
from app.schemas.inventory import OperationCreate, StockLine
from app.services.runtime_reliability import distributed_lock


def _bucket_key(client_id: uuid.UUID, warehouse_id: uuid.UUID, sku_id: uuid.UUID, location_id: uuid.UUID, container_id: uuid.UUID | None) -> str:
    return f"{client_id}:{warehouse_id}:{sku_id}:{location_id}:{container_id or 'NONE'}"


def _assert_context(db: Session, client_id: uuid.UUID, warehouse_id: uuid.UUID, line: StockLine) -> None:
    sku = db.get(SKU, line.sku_id)
    if not sku or sku.client_id != client_id or not sku.is_active:
        raise HTTPException(status_code=400, detail="SKU не найден или принадлежит другому клиенту")

    for location_id in (line.source_location_id, line.destination_location_id):
        if location_id is None:
            continue
        location = db.get(Location, location_id)
        if not location or location.warehouse_id != warehouse_id or not location.is_active:
            raise HTTPException(status_code=400, detail="Ячейка не найдена, неактивна или относится к другому складу")

    for role, container_id, location_id in (
        ("source", line.source_container_id, line.source_location_id),
        ("destination", line.destination_container_id, line.destination_location_id),
    ):
        if container_id is None:
            continue
        container = db.execute(
            select(Container).where(Container.id == container_id).with_for_update()
        ).scalar_one_or_none()
        if not container:
            raise HTTPException(status_code=400, detail="Контейнер не найден")
        if container.client_id != client_id or container.warehouse_id != warehouse_id:
            raise HTTPException(status_code=400, detail="Контейнер относится к другому клиенту или складу")
        if container.status != "ACTIVE":
            raise HTTPException(status_code=400, detail="Контейнер не активен")
        if role == "source" and container.location_id is None:
            raise HTTPException(status_code=409, detail="У контейнера не задана текущая ячейка")
        if role == "destination" and container.location_id is None and location_id is not None:
            container.location_id = location_id
        elif container.location_id is not None and location_id is not None and container.location_id != location_id:
            raise HTTPException(status_code=409, detail="Контейнер физически находится в другой ячейке")


def _lock_balance(
    db: Session,
    client_id: uuid.UUID,
    warehouse_id: uuid.UUID,
    sku_id: uuid.UUID,
    location_id: uuid.UUID,
    container_id: uuid.UUID | None,
) -> InventoryBalance:
    key = _bucket_key(client_id, warehouse_id, sku_id, location_id, container_id)
    db.execute(
        pg_insert(InventoryBalance)
        .values(
            bucket_key=key,
            client_id=client_id,
            warehouse_id=warehouse_id,
            sku_id=sku_id,
            location_id=location_id,
            container_id=container_id,
            quantity=Decimal("0"),
            version=1,
        )
        .on_conflict_do_nothing(index_elements=[InventoryBalance.bucket_key])
    )
    return db.execute(
        select(InventoryBalance).where(InventoryBalance.bucket_key == key).with_for_update()
    ).scalar_one()


def _change_balance(balance: InventoryBalance, delta: Decimal) -> None:
    new_qty = Decimal(balance.quantity) + Decimal(delta)
    if new_qty < 0:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Недостаточный остаток. Доступно {balance.quantity}, изменение {delta}",
        )
    balance.quantity = new_qty
    balance.version += 1


def _apply_line(db: Session, op: InventoryOperation, line: StockLine) -> None:
    _assert_context(db, op.client_id, op.warehouse_id, line)

    if line.source_location_id is not None:
        source = _lock_balance(
            db,
            op.client_id,
            op.warehouse_id,
            line.sku_id,
            line.source_location_id,
            line.source_container_id,
        )
        _change_balance(source, -line.quantity)

    if line.destination_location_id is not None:
        destination = _lock_balance(
            db,
            op.client_id,
            op.warehouse_id,
            line.sku_id,
            line.destination_location_id,
            line.destination_container_id,
        )
        _change_balance(destination, line.quantity)

    db.add(
        InventoryOperationLine(
            operation_id=op.id,
            sku_id=line.sku_id,
            quantity=line.quantity,
            source_location_id=line.source_location_id,
            source_container_id=line.source_container_id,
            destination_location_id=line.destination_location_id,
            destination_container_id=line.destination_container_id,
            reason=line.reason,
        )
    )


def _execute_bulk_receiving_operation(
    db: Session, payload: OperationCreate, *, commit: bool
) -> InventoryOperation | None:
    """Fast PostgreSQL path for destination-only receiving operations.

    The historical implementation validated and updated every line with several SQL
    round-trips. A 10k-line receiving could therefore issue tens of thousands of queries
    while holding locks. This path keeps the same validation rules but validates entities
    in sets, aggregates balance buckets, performs one atomic UPSERT, and bulk-inserts the
    operation history. Non-PostgreSQL and non-receiving operations keep the old path.
    """
    bind = db.get_bind()
    dialect = str(getattr(getattr(bind, "dialect", None), "name", "") or "").lower()
    op_type = str(payload.operation_type or "").upper()
    lines = list(payload.lines or [])
    if dialect != "postgresql" or op_type not in {"RECEIVING", "RECEIVING_IMPORT"} or len(lines) < 25:
        return None
    if any(line.source_location_id is not None or line.source_container_id is not None or line.destination_location_id is None for line in lines):
        return None

    existing = db.execute(
        select(InventoryOperation).where(
            InventoryOperation.client_id == payload.client_id,
            InventoryOperation.idempotency_key == payload.idempotency_key,
        )
    ).scalar_one_or_none()
    if existing:
        return existing

    sku_ids = {line.sku_id for line in lines}
    location_ids = {line.destination_location_id for line in lines if line.destination_location_id is not None}
    container_ids = {line.destination_container_id for line in lines if line.destination_container_id is not None}

    sku_rows = db.execute(
        select(SKU.id, SKU.client_id, SKU.is_active).where(SKU.id.in_(list(sku_ids)))
    ).all()
    valid_skus = {row.id for row in sku_rows if row.client_id == payload.client_id and bool(row.is_active)}
    if valid_skus != sku_ids:
        raise HTTPException(status_code=400, detail="Один или несколько SKU не найдены, неактивны или относятся к другому клиенту")

    location_rows = db.execute(
        select(Location.id, Location.warehouse_id, Location.is_active).where(Location.id.in_(list(location_ids)))
    ).all()
    valid_locations = {row.id for row in location_rows if row.warehouse_id == payload.warehouse_id and bool(row.is_active)}
    if valid_locations != location_ids:
        raise HTTPException(status_code=400, detail="Один или несколько адресов приёмки не найдены, неактивны или относятся к другому складу")

    containers: dict[uuid.UUID, Container] = {}
    if container_ids:
        locked = db.execute(
            select(Container).where(Container.id.in_(list(container_ids))).with_for_update()
        ).scalars().all()
        containers = {row.id: row for row in locked}
        if set(containers) != container_ids:
            raise HTTPException(status_code=400, detail="Один или несколько коробов приёмки не найдены")

    target_by_container: dict[uuid.UUID, uuid.UUID] = {}
    for line in lines:
        container_id = line.destination_container_id
        location_id = line.destination_location_id
        if container_id is None:
            continue
        container = containers[container_id]
        if container.client_id != payload.client_id or container.warehouse_id != payload.warehouse_id:
            raise HTTPException(status_code=400, detail="Короб относится к другому клиенту или складу")
        if str(container.status or "").upper() != "ACTIVE":
            raise HTTPException(status_code=400, detail="Короб не активен")
        previous_target = target_by_container.setdefault(container_id, location_id)
        if previous_target != location_id:
            raise HTTPException(status_code=409, detail="Один короб указан сразу в нескольких адресах")
        if container.location_id is None:
            container.location_id = location_id
        elif container.location_id != location_id:
            raise HTTPException(status_code=409, detail="Короб физически находится в другой ячейке")

    op = InventoryOperation(
        client_id=payload.client_id,
        warehouse_id=payload.warehouse_id,
        operation_type=op_type,
        idempotency_key=payload.idempotency_key,
        performed_by=payload.performed_by,
        reference=payload.reference,
        comment=payload.comment,
    )
    db.add(op)
    db.flush()

    now = datetime.now(timezone.utc)
    aggregated: dict[tuple[uuid.UUID, uuid.UUID, uuid.UUID | None], Decimal] = {}
    for line in lines:
        key = (line.sku_id, line.destination_location_id, line.destination_container_id)
        aggregated[key] = aggregated.get(key, Decimal("0")) + Decimal(line.quantity)

    balance_rows = []
    for (sku_id, location_id, container_id), quantity in aggregated.items():
        balance_rows.append({
            "id": uuid.uuid4(),
            "bucket_key": _bucket_key(payload.client_id, payload.warehouse_id, sku_id, location_id, container_id),
            "client_id": payload.client_id,
            "warehouse_id": payload.warehouse_id,
            "sku_id": sku_id,
            "location_id": location_id,
            "container_id": container_id,
            "quantity": quantity,
            "version": 1,
            "created_at": now,
            "updated_at": now,
        })
    if balance_rows:
        stmt = pg_insert(InventoryBalance).values(balance_rows)
        stmt = stmt.on_conflict_do_update(
            index_elements=[InventoryBalance.bucket_key],
            set_={
                "quantity": InventoryBalance.quantity + stmt.excluded.quantity,
                "version": InventoryBalance.version + 1,
                "updated_at": now,
            },
        )
        db.execute(stmt)

    history_rows = [{
        "id": uuid.uuid4(),
        "operation_id": op.id,
        "sku_id": line.sku_id,
        "quantity": Decimal(line.quantity),
        "source_location_id": None,
        "source_container_id": None,
        "destination_location_id": line.destination_location_id,
        "destination_container_id": line.destination_container_id,
        "reason": line.reason,
    } for line in lines]
    if history_rows:
        db.execute(InventoryOperationLine.__table__.insert(), history_rows)

    if commit:
        db.commit()
        db.refresh(op)
    else:
        db.flush()
    return op


def execute_operation(db: Session, payload: OperationCreate, *, commit: bool = True) -> InventoryOperation:
    bulk = _execute_bulk_receiving_operation(db, payload, commit=commit)
    if bulk is not None:
        return bulk

    existing = db.execute(
        select(InventoryOperation).where(
            InventoryOperation.client_id == payload.client_id,
            InventoryOperation.idempotency_key == payload.idempotency_key,
        )
    ).scalar_one_or_none()
    if existing:
        return existing

    with db.begin_nested():
        op = InventoryOperation(
            client_id=payload.client_id,
            warehouse_id=payload.warehouse_id,
            operation_type=payload.operation_type.upper(),
            idempotency_key=payload.idempotency_key,
            performed_by=payload.performed_by,
            reference=payload.reference,
            comment=payload.comment,
        )
        db.add(op)
        db.flush()

        for line in payload.lines:
            _apply_line(db, op, line)

    if commit:
        db.commit()
        db.refresh(op)
    else:
        db.flush()
    return op



# FC_K9R56_CONTAINER_MOVE_RESERVATION_SAFE
_MOVE_RESERVATION_STATES = ("SALE_RESERVED", "ACTIVE", "CONSUMING", "CANCELLING", "PACKING_WRITE_OFF")

def _move_qty(value) -> Decimal:
    try:
        return Decimal(str(value or 0))
    except Exception:
        return Decimal(0)

def _public_container_code(container: Container) -> str:
    barcode = str(getattr(container, "barcode", "") or "").strip()
    name = str(getattr(container, "name", "") or "").strip()
    if barcode.upper().startswith("FCBOX-") and name:
        return name
    if name.upper().startswith("FCBOX-") and barcode:
        return barcode
    return barcode or name or str(container.id)

def _move_location_json(location: Location | None) -> dict | None:
    if not location:
        return None
    return {
        "id": str(location.id),
        "code": str(location.code or ""),
        "barcode": str(location.barcode or ""),
        "name": str(location.name or ""),
        "location_type": str(location.location_type or "CELL"),
    }

def _container_move_reservation_plan(
    db: Session,
    *,
    client_id: uuid.UUID,
    warehouse_id: uuid.UUID,
    container: Container,
    balances: list[InventoryBalance],
    lock_rows: bool,
) -> dict:
    """Classify live reservation rows before a whole-box move.

    NEW address-bound reservations may follow the physical box. A reservation already
    bound to this exact box by an active picking flow blocks movement. Reservations
    elsewhere on the same cell/pallet are informational only and never block this box.
    """
    source_location_id = container.location_id
    physical_by_sku: dict[str, Decimal] = {}
    for balance in balances:
        key = str(balance.sku_id)
        physical_by_sku[key] = physical_by_sku.get(key, Decimal(0)) + max(Decimal(0), _move_qty(balance.quantity))
    sku_ids = sorted(physical_by_sku)
    empty = {
        "follow_ids": [], "follow_rows": [], "blockers": [], "warnings": [],
        "follow_quantity": Decimal(0), "blocking_quantity": Decimal(0),
        "warning_quantity": Decimal(0), "physical_by_sku": physical_by_sku,
    }
    if not source_location_id or not sku_ids:
        return empty

    # FC_K9R57_MOVE_PREVIEW_INDEXED_ORDER_LOOKUP
    # r56 joined marketplace_orders through CAST(o.id AS VARCHAR)=r.order_id.  That
    # casts the indexed UUID PK and can turn every scan-preview into a broad order-table
    # scan.  First read the already-narrow reservation rows, then hydrate only their
    # order ids through the native UUID primary-key index.
    sql = """
        SELECT r.id, r.order_id, r.external_order_id, r.picking_list_id, r.sku_id,
               r.source_location_id, r.source_container_id, r.quantity, r.status,
               r.created_at
        FROM fc_stock_reservations r
        WHERE r.client_id=:client AND r.warehouse_id=:warehouse
          AND r.source_location_id=:source
          AND r.sku_id IN :sku_ids
          AND r.status IN :statuses
        ORDER BY r.created_at ASC, r.id ASC
    """
    dialect = str(getattr(getattr(db.get_bind(), "dialect", None), "name", "") or "").lower()
    if lock_rows and dialect == "postgresql":
        sql += " FOR UPDATE"
    stmt = text(sql).bindparams(bindparam("sku_ids", expanding=True), bindparam("statuses", expanding=True))
    raw_rows = db.execute(stmt, {
        "client": str(client_id), "warehouse": str(warehouse_id),
        "source": str(source_location_id), "sku_ids": sku_ids,
        "statuses": list(_MOVE_RESERVATION_STATES),
    }).mappings().all()

    order_uuid_by_text: dict[str, uuid.UUID] = {}
    for raw in raw_rows:
        order_text = str(raw.get("order_id") or "").strip()
        if not order_text or order_text in order_uuid_by_text:
            continue
        try:
            order_uuid_by_text[order_text] = uuid.UUID(order_text)
        except Exception:
            continue
    order_meta: dict[str, dict] = {}
    if order_uuid_by_text:
        order_rows = db.execute(
            select(
                MarketplaceOrder.id,
                MarketplaceOrder.local_status,
                MarketplaceOrder.external_order_num,
                MarketplaceOrder.external_order_id,
            ).where(MarketplaceOrder.id.in_(list(order_uuid_by_text.values())))
        ).all()
        for order_id, local_status, external_order_num, external_order_id in order_rows:
            key = str(order_id)
            order_meta[key] = {
                "local_status": str(local_status or ""),
                "order_label": str(external_order_num or external_order_id or key),
            }

    rows = []
    for raw in raw_rows:
        row = dict(raw)
        meta = order_meta.get(str(row.get("order_id") or ""), {})
        row["local_status"] = str(meta.get("local_status") or "")
        row["order_label"] = str(meta.get("order_label") or row.get("external_order_id") or row.get("order_id") or "")
        rows.append(row)

    exact_new_by_sku: dict[str, list[dict]] = {}
    loose_new_by_sku: dict[str, list[dict]] = {}
    blockers: list[dict] = []
    warnings: list[dict] = []
    container_key = str(container.id)

    for raw in rows:
        row = dict(raw)
        row["quantity"] = max(Decimal(0), _move_qty(row.get("quantity")))
        row["status"] = str(row.get("status") or "").upper()
        row["local_status"] = str(row.get("local_status") or "").upper()
        row["source_container_id"] = str(row.get("source_container_id") or "")
        row["order_label"] = str(row.get("order_label") or row.get("external_order_id") or row.get("order_id") or "")
        exact = row["source_container_id"] == container_key
        loose = not row["source_container_id"]
        is_new = row["local_status"] == "NEW"
        has_pick = bool(str(row.get("picking_list_id") or "").strip())

        if is_new and (exact or loose):
            target = exact_new_by_sku if exact else loose_new_by_sku
            target.setdefault(str(row["sku_id"]), []).append(row)
            continue

        # An exact live reservation that is no longer NEW is a real physical promise.
        # Do not let the box disappear from under an active/packing picking assignment.
        if exact and (has_pick or row["local_status"] in {"PICKING", "PACKED"} or row["status"] in {"CONSUMING", "CANCELLING", "PACKING_WRITE_OFF"}):
            row["block_kind"] = "ACTIVE_PICK_IN_BOX"
            blockers.append(row)
            continue

        # Reservation on the same cell/pallet but not in this box is not a reason to
        # block this box. Keep it at the old address and show an operator warning only.
        if not exact and (has_pick or row["local_status"] in {"PICKING", "PACKED"}):
            warnings.append(row)
            continue

        # Unknown exact ACTIVE legacy rows are kept safe rather than silently orphaned.
        if exact and row["status"] == "ACTIVE":
            row["block_kind"] = "LEGACY_EXACT_RESERVATION"
            blockers.append(row)

    follow_rows: list[dict] = []
    for sku_id, physical in physical_by_sku.items():
        remaining = max(Decimal(0), physical)
        exact_rows = exact_new_by_sku.get(sku_id, [])
        exact_total = sum((_move_qty(row.get("quantity")) for row in exact_rows), Decimal(0))
        if exact_total > remaining + Decimal("0.000001"):
            for row in exact_rows:
                problem = dict(row)
                problem["move_integrity_error"] = True
                problem["block_kind"] = "RESERVATION_EXCEEDS_BOX_STOCK"
                blockers.append(problem)
            continue
        follow_rows.extend(exact_rows)
        remaining -= exact_total
        for row in loose_new_by_sku.get(sku_id, []):
            qty = _move_qty(row.get("quantity"))
            if qty <= remaining + Decimal("0.000001"):
                follow_rows.append(row)
                remaining -= qty

    follow_ids = [str(row["id"]) for row in follow_rows]
    return {
        "follow_ids": follow_ids,
        "follow_rows": follow_rows,
        "blockers": blockers,
        "warnings": warnings,
        "follow_quantity": sum((_move_qty(row.get("quantity")) for row in follow_rows), Decimal(0)),
        "blocking_quantity": sum((_move_qty(row.get("quantity")) for row in blockers), Decimal(0)),
        "warning_quantity": sum((_move_qty(row.get("quantity")) for row in warnings), Decimal(0)),
        "physical_by_sku": physical_by_sku,
    }

def container_move_preview(
    db: Session,
    *,
    client_id: uuid.UUID,
    warehouse_id: uuid.UUID,
    container_id: uuid.UUID,
    destination_location_id: uuid.UUID | None = None,
) -> dict:
    container = db.get(Container, container_id)
    if not container or container.client_id != client_id or container.warehouse_id != warehouse_id:
        raise HTTPException(status_code=404, detail="Короб не найден на выбранном складе")
    if container.location_id is None:
        raise HTTPException(status_code=409, detail="У короба не задан текущий адрес")
    source = db.get(Location, container.location_id)
    destination = None
    if destination_location_id is not None:
        destination = db.get(Location, destination_location_id)
        if not destination or destination.warehouse_id != warehouse_id or not destination.is_active:
            raise HTTPException(status_code=400, detail="Адрес назначения недоступен")
    balances = db.execute(select(InventoryBalance).where(
        InventoryBalance.client_id == client_id,
        InventoryBalance.warehouse_id == warehouse_id,
        InventoryBalance.container_id == container_id,
        InventoryBalance.quantity > 0,
    )).scalars().all()
    plan = _container_move_reservation_plan(
        db, client_id=client_id, warehouse_id=warehouse_id, container=container, balances=balances, lock_rows=False
    )
    blocker_orders = list(dict.fromkeys(str(row.get("order_label") or "") for row in plan["blockers"] if row.get("order_label")))
    warning_orders = list(dict.fromkeys(str(row.get("order_label") or "") for row in plan["warnings"] if row.get("order_label")))
    follow_orders = list(dict.fromkeys(str(row.get("order_label") or "") for row in plan["follow_rows"] if row.get("order_label")))
    blocking_quantity = float(plan["blocking_quantity"])
    warning_quantity = float(plan["warning_quantity"])
    follow_quantity = float(plan["follow_quantity"])
    block_message = ""
    if blocking_quantity > 0:
        labels = ", ".join(blocker_orders[:8])
        kinds = {str(row.get("block_kind") or "") for row in plan["blockers"]}
        if "RESERVATION_EXCEEDS_BOX_STOCK" in kinds:
            block_message = "Адресный резерв в этом коробе больше текущего физического остатка. Сначала выполните сверку короба/резерва; перемещение остановлено без изменений."
        elif "LEGACY_EXACT_RESERVATION" in kinds and "ACTIVE_PICK_IN_BOX" not in kinds:
            block_message = "В этом коробе есть старый адресный резерв с неясным статусом заказа. Перемещение остановлено, чтобы не потерять привязку резерва."
        else:
            block_message = (
                f"В этом коробе зарезервировано {blocking_quantity:g} шт. по активному сборочному листу"
                + (f" (заказы: {labels})" if labels else "")
                + ". Сначала упакуйте эти заказы, затем перемещайте короб."
            )
    warning_message = ""
    if warning_quantity > 0:
        warning_message = (
            f"На текущем адресе/палете есть {warning_quantity:g} шт. резерва активного сборочного листа, "
            "но резерв не относится к этому коробу. Перемещение короба разрешено; резерв останется на старом адресе."
        )
    return {
        "can_move": blocking_quantity <= 0,
        "container": {
            "id": str(container.id), "barcode": str(container.barcode or ""),
            "name": str(getattr(container, "name", None) or ""),
            "display_code": _public_container_code(container),
        },
        "source_location": _move_location_json(source),
        "destination_location": _move_location_json(destination),
        "physical_quantity": float(sum((max(Decimal(0), _move_qty(b.quantity)) for b in balances), Decimal(0))),
        "sku_count": len({str(b.sku_id) for b in balances}),
        "new_reservations_follow_quantity": follow_quantity,
        "new_reservations_follow_orders": follow_orders[:20],
        "active_list_other_bucket_quantity": warning_quantity,
        "active_list_other_orders": warning_orders[:20],
        "blocking_quantity": blocking_quantity,
        "blocking_orders": blocker_orders[:20],
        "block_message": block_message,
        "warning_message": warning_message,
    }

# FC_K9R57_MOVE_SHORT_TX_LOCK_GUARD
def _move_lock_busy(exc: Exception) -> bool:
    message = str(exc or "").lower()
    return any(token in message for token in (
        "lock timeout", "could not obtain lock", "deadlock detected",
        "canceling statement due to lock timeout",
    ))

def _move_begin_fast_guard(db: Session, lock_key: str) -> bool:
    """Acquire the same PostgreSQL advisory namespace as picking, transaction-scoped.

    Unlike r56's session-level distributed_lock this consumes no second pooled DB
    connection and is released automatically on commit/rollback.
    """
    bind = db.get_bind()
    dialect = str(getattr(getattr(bind, "dialect", None), "name", "") or "").lower()
    if dialect == "postgresql":
        db.execute(text("SET LOCAL lock_timeout = '2500ms'"))
        return bool(db.execute(
            text("SELECT pg_try_advisory_xact_lock(hashtext(:key))"),
            {"key": f"fulfillcore:{lock_key}"},
        ).scalar())
    # Development fallback. Production is PostgreSQL.
    with distributed_lock(db, lock_key, blocking=False) as acquired:
        return bool(acquired)


def move_entire_container(
    db: Session,
    *,
    client_id: uuid.UUID,
    warehouse_id: uuid.UUID,
    container_id: uuid.UUID,
    destination_location_id: uuid.UUID,
    idempotency_key: str,
    performed_by: str | None,
    reference: str | None,
    comment: str | None,
) -> InventoryOperation:
    existing = db.execute(
        select(InventoryOperation).where(
            InventoryOperation.client_id == client_id,
            InventoryOperation.idempotency_key == idempotency_key,
        )
    ).scalar_one_or_none()
    if existing:
        return existing

    # FC_K9R57_CONTAINER_MOVE_TIMEOUT_GUARD
    # Keep the r56 safety invariant but never make an operator wait behind a long
    # warehouse transaction. The PostgreSQL transaction advisory lock conflicts with
    # the picking-list session advisory lock under the exact same hash key, while using
    # no second pool connection. Row-lock waits are bounded to 2.5 seconds.
    lock_key = f"picking-stock:{client_id}:{warehouse_id}"
    db.rollback()
    try:
        if not _move_begin_fast_guard(db, lock_key):
            db.rollback()
            raise HTTPException(
                status_code=409,
                detail="Сейчас формируется сборочный лист. Повторите перемещение через несколько секунд.",
            )

        existing = db.execute(
            select(InventoryOperation).where(
                InventoryOperation.client_id == client_id,
                InventoryOperation.idempotency_key == idempotency_key,
            )
        ).scalar_one_or_none()
        if existing:
            db.commit()
            return existing

        container = db.execute(
            select(Container).where(Container.id == container_id).with_for_update()
        ).scalar_one_or_none()
        if not container:
            raise HTTPException(status_code=404, detail="Короб не найден")
        if container.client_id != client_id or container.warehouse_id != warehouse_id:
            raise HTTPException(status_code=400, detail="Короб относится к другому клиенту или складу")
        if container.location_id is None:
            raise HTTPException(status_code=409, detail="У короба не задан текущий адрес")
        if container.location_id == destination_location_id:
            raise HTTPException(status_code=409, detail="Короб уже находится по этому адресу")

        destination = db.get(Location, destination_location_id)
        if not destination or destination.warehouse_id != warehouse_id or not destination.is_active:
            raise HTTPException(status_code=400, detail="Адрес назначения недоступен")

        balances = db.execute(
            select(InventoryBalance)
            .where(
                InventoryBalance.client_id == client_id,
                InventoryBalance.warehouse_id == warehouse_id,
                InventoryBalance.container_id == container_id,
                InventoryBalance.quantity > 0,
            )
            .order_by(InventoryBalance.sku_id.asc())
            .with_for_update()
        ).scalars().all()
        if not balances:
            raise HTTPException(status_code=409, detail="В коробе нет учётного остатка")

        old_location_id = container.location_id
        plan = _container_move_reservation_plan(
            db, client_id=client_id, warehouse_id=warehouse_id, container=container, balances=balances, lock_rows=True
        )
        if plan["blockers"]:
            labels = list(dict.fromkeys(str(row.get("order_label") or "") for row in plan["blockers"] if row.get("order_label")))
            qty = float(plan["blocking_quantity"])
            kinds = {str(row.get("block_kind") or "") for row in plan["blockers"]}
            if "RESERVATION_EXCEEDS_BOX_STOCK" in kinds:
                detail = "Адресный резерв в этом коробе больше физического остатка. Сначала выполните сверку; перемещение отменено без изменений."
            elif "LEGACY_EXACT_RESERVATION" in kinds and "ACTIVE_PICK_IN_BOX" not in kinds:
                detail = "В этом коробе есть старый адресный резерв с неясным статусом. Перемещение отменено, чтобы не потерять привязку."
            else:
                suffix = f" Заказы: {', '.join(labels[:8])}." if labels else ""
                detail = f"В этом коробе зарезервировано {qty:g} шт. по активному сборочному листу.{suffix} Сначала упакуйте эти заказы, затем перемещайте короб."
            raise HTTPException(status_code=409, detail=detail)

        follow_ids = list(plan["follow_ids"])
        if follow_ids:
            update_stmt = text(
                """
                UPDATE fc_stock_reservations
                SET source_location_id=:destination, updated_at=CURRENT_TIMESTAMP,
                    note=CASE
                        WHEN COALESCE(note,'')='' THEN 'MOVE_CONTAINER_NEW_FOLLOW_R56'
                        WHEN POSITION('MOVE_CONTAINER_NEW_FOLLOW_R56' IN note)>0 THEN note
                        ELSE note || ' | MOVE_CONTAINER_NEW_FOLLOW_R56'
                    END
                WHERE id IN :ids
                """
            ).bindparams(bindparam("ids", expanding=True))
            db.execute(update_stmt, {"destination": str(destination_location_id), "ids": follow_ids})

        lines = [
            StockLine(
                sku_id=b.sku_id, quantity=b.quantity,
                source_location_id=old_location_id, source_container_id=container_id,
                destination_location_id=destination_location_id, destination_container_id=container_id,
                reason="MOVE_CONTAINER",
            )
            for b in balances
        ]

        follow_qty = float(plan["follow_quantity"])
        warning_qty = float(plan["warning_quantity"])
        auto_note = []
        if follow_qty > 0:
            auto_note.append(f"NEW резерв перенесён вместе с коробом: {follow_qty:g} шт.")
        if warning_qty > 0:
            auto_note.append(f"Резерв активного листа вне этого короба оставлен на старом адресе: {warning_qty:g} шт.")
        final_comment = " · ".join(x for x in [str(comment or "").strip(), *auto_note] if x) or None

        op = InventoryOperation(
            client_id=client_id, warehouse_id=warehouse_id, operation_type="MOVE_CONTAINER",
            idempotency_key=idempotency_key, performed_by=performed_by, reference=reference, comment=final_comment,
        )
        db.add(op)
        db.flush()

        for line in lines:
            source = _lock_balance(db, client_id, warehouse_id, line.sku_id, old_location_id, container_id)
            _change_balance(source, -line.quantity)
            destination_balance = _lock_balance(db, client_id, warehouse_id, line.sku_id, destination_location_id, container_id)
            _change_balance(destination_balance, line.quantity)
            db.add(
                InventoryOperationLine(
                    operation_id=op.id, sku_id=line.sku_id, quantity=line.quantity,
                    source_location_id=old_location_id, source_container_id=container_id,
                    destination_location_id=destination_location_id, destination_container_id=container_id,
                    reason="MOVE_CONTAINER",
                )
            )

        container.location_id = destination_location_id
        db.commit()
        db.refresh(op)
        return op
    except HTTPException:
        db.rollback()
        raise
    except DBAPIError as exc:
        db.rollback()
        if _move_lock_busy(exc):
            raise HTTPException(
                status_code=409,
                detail="Остаток или резерв этого короба сейчас меняется другой операцией. Ничего не перемещено. Повторите через несколько секунд.",
            ) from None
        raise
