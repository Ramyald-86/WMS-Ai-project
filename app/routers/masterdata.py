import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import bindparam, or_, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.services.stock_reservations import active_reserved_totals
from app.services.stock_core_v2 import stock_truth_for_sku
from app.services.tenant_scope import (
    ensure_client_in_request_scope,
    ensure_warehouse_in_request_scope,
    get_request_scope,
    link_client_to_fulfillment,
    link_warehouse_to_fulfillment,
)
from app.models import Client, Container, InventoryBalance, Location, SKU, Warehouse
from app.schemas.masterdata import (
    ClientCreate,
    ClientRead,
    ContainerCreate,
    ContainerRead,
    ContainerUpdate,
    LocationCreate,
    LocationRead,
    SKUCreate,
    SKURead,
    WarehouseCreate,
    WarehouseRead,
)

router = APIRouter(prefix="/masterdata", tags=["Справочники"])


def _commit_or_409(db: Session, obj):
    try:
        db.add(obj)
        db.commit()
        db.refresh(obj)
        return obj
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="Объект с таким кодом/штрихкодом уже существует") from exc


@router.post("/clients", response_model=ClientRead)
def create_client(payload: ClientCreate, request: Request, db: Session = Depends(get_db)):
    obj = Client(**payload.model_dump())
    scope = get_request_scope(request)
    try:
        db.add(obj)
        db.flush()
        if scope and scope.enforced:
            link_client_to_fulfillment(db, scope.fulfillment_id, obj.id)
            # Keep request-local allow-list in sync for subsequent ops in rare chained calls.
            scope.client_ids.add(obj.id)
        db.commit()
        db.refresh(obj)
        return obj
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="Объект с таким кодом/штрихкодом уже существует") from exc


@router.get("/clients", response_model=list[ClientRead])
def list_clients(request: Request, db: Session = Depends(get_db)):
    stmt = select(Client).order_by(Client.name)
    scope = get_request_scope(request)
    if scope and scope.enforced:
        if not scope.client_ids:
            return []
        stmt = stmt.where(Client.id.in_(scope.client_ids))
    return db.execute(stmt).scalars().all()


@router.post("/warehouses", response_model=WarehouseRead)
def create_warehouse(payload: WarehouseCreate, request: Request, db: Session = Depends(get_db)):
    name = payload.name.strip()
    if not name:
        raise HTTPException(status_code=422, detail="Укажите название склада")
    code = (payload.code or "").strip() or f"WH_{uuid.uuid4().hex[:20].upper()}"
    obj = Warehouse(code=code[:64], name=name)
    scope = get_request_scope(request)
    try:
        db.add(obj)
        db.flush()
        if scope and scope.enforced:
            link_warehouse_to_fulfillment(db, scope.fulfillment_id, obj.id)
            scope.warehouse_ids.add(obj.id)
        db.commit()
        db.refresh(obj)
        return obj
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="Объект с таким кодом/штрихкодом уже существует") from exc


@router.get("/warehouses", response_model=list[WarehouseRead])
def list_warehouses(request: Request, db: Session = Depends(get_db)):
    stmt = select(Warehouse).order_by(Warehouse.name)
    scope = get_request_scope(request)
    if scope and scope.enforced:
        if not scope.warehouse_ids:
            return []
        stmt = stmt.where(Warehouse.id.in_(scope.warehouse_ids))
    return db.execute(stmt).scalars().all()


@router.post("/locations", response_model=LocationRead)
def create_location(payload: LocationCreate, request: Request, db: Session = Depends(get_db)):
    ensure_warehouse_in_request_scope(request, payload.warehouse_id)
    warehouse = db.get(Warehouse, payload.warehouse_id)
    if not warehouse:
        raise HTTPException(status_code=404, detail="Склад не найден")
    if payload.parent_id and not db.get(Location, payload.parent_id):
        raise HTTPException(status_code=404, detail="Родительское местоположение не найдено")
    return _commit_or_409(db, Location(**payload.model_dump()))


@router.get("/locations", response_model=list[LocationRead])
def list_locations(
    warehouse_id: uuid.UUID,
    request: Request,
    q: str | None = Query(default=None),
    db: Session = Depends(get_db),
):
    ensure_warehouse_in_request_scope(request, warehouse_id)
    stmt = select(Location).where(Location.warehouse_id == warehouse_id)
    if q:
        like = f"%{q}%"
        stmt = stmt.where(or_(Location.code.ilike(like), Location.barcode.ilike(like), Location.name.ilike(like)))
    return db.execute(stmt.order_by(Location.code)).scalars().all()


@router.post("/skus", response_model=SKURead)
def create_sku(payload: SKUCreate, request: Request, db: Session = Depends(get_db)):
    ensure_client_in_request_scope(request, payload.client_id)
    if not db.get(Client, payload.client_id):
        raise HTTPException(status_code=404, detail="Клиент не найден")
    return _commit_or_409(db, SKU(**payload.model_dump()))


@router.get("/skus", response_model=list[SKURead])
def list_skus(
    client_id: uuid.UUID,
    request: Request,
    q: str | None = Query(default=None),
    db: Session = Depends(get_db),
):
    ensure_client_in_request_scope(request, client_id)
    stmt = select(SKU).where(SKU.client_id == client_id)
    if q:
        like = f"%{q}%"
        stmt = stmt.where(
            or_(
                SKU.sku_code.ilike(like),
                SKU.article.ilike(like),
                SKU.name.ilike(like),
                SKU.size.ilike(like),
                SKU.barcode.ilike(like),
            )
        )
    return db.execute(stmt.order_by(SKU.article, SKU.size, SKU.sku_code)).scalars().all()


@router.post("/containers", response_model=ContainerRead)
def create_container(payload: ContainerCreate, request: Request, db: Session = Depends(get_db)):
    ensure_client_in_request_scope(request, payload.client_id)
    ensure_warehouse_in_request_scope(request, payload.warehouse_id)
    if not db.get(Client, payload.client_id):
        raise HTTPException(status_code=404, detail="Клиент не найден")
    warehouse = db.get(Warehouse, payload.warehouse_id)
    if not warehouse:
        raise HTTPException(status_code=404, detail="Склад не найден")
    if payload.location_id:
        location = db.get(Location, payload.location_id)
        if not location or location.warehouse_id != payload.warehouse_id:
            raise HTTPException(status_code=400, detail="Ячейка относится к другому складу")
    data = payload.model_dump()
    data["name"] = data.get("name") or data["barcode"]
    return _commit_or_409(db, Container(**data))


@router.get("/containers", response_model=list[ContainerRead])
def list_containers(
    client_id: uuid.UUID,
    warehouse_id: uuid.UUID,
    request: Request,
    q: str | None = Query(default=None),
    db: Session = Depends(get_db),
):
    ensure_client_in_request_scope(request, client_id)
    ensure_warehouse_in_request_scope(request, warehouse_id)
    stmt = select(Container).where(Container.client_id == client_id, Container.warehouse_id == warehouse_id)
    if q:
        like = f"%{q}%"
        stmt = stmt.where(or_(Container.barcode.ilike(like), Container.name.ilike(like)))
    return db.execute(stmt.order_by(Container.name, Container.barcode)).scalars().all()


@router.patch("/containers/{container_id}", response_model=ContainerRead)
def update_container(
    container_id: uuid.UUID,
    payload: ContainerUpdate,
    request: Request,
    db: Session = Depends(get_db),
):
    container = db.get(Container, container_id)
    if not container:
        raise HTTPException(status_code=404, detail="Короб не найден")
    ensure_client_in_request_scope(request, container.client_id)
    ensure_warehouse_in_request_scope(request, container.warehouse_id)
    if payload.name is not None:
        container.name = payload.name.strip() or container.barcode
    if payload.barcode is not None:
        container.barcode = payload.barcode.strip()
    try:
        db.commit()
        db.refresh(container)
        return container
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="Такой штрихкод короба уже используется") from exc


# FC_CONTAINER_CONTENTS_STRICT_SCOPE_09415G1_20260903
@router.get("/containers/{container_id}/contents")
def container_contents(
    container_id: uuid.UUID,
    client_id: uuid.UUID,
    warehouse_id: uuid.UUID,
    request: Request,
    db: Session = Depends(get_db),
):
    ensure_client_in_request_scope(request, client_id)
    ensure_warehouse_in_request_scope(request, warehouse_id)
    container = db.execute(select(Container).where(
        Container.id == container_id,
        Container.client_id == client_id,
        Container.warehouse_id == warehouse_id,
    )).scalars().first()
    if not container:
        raise HTTPException(status_code=404, detail="Короб не найден на выбранном складе")
    location = db.execute(select(Location).where(
        Location.id == container.location_id,
        Location.warehouse_id == warehouse_id,
    )).scalars().first() if container.location_id else None
    rows = db.execute(
        select(InventoryBalance, SKU)
        .join(SKU, SKU.id == InventoryBalance.sku_id)
        .where(
            InventoryBalance.container_id == container_id,
            InventoryBalance.client_id == client_id,
            InventoryBalance.warehouse_id == warehouse_id,
            InventoryBalance.quantity > 0,
            SKU.client_id == client_id,
        )
        .order_by(SKU.article, SKU.size, SKU.name)
    ).all()
    # User-visible reserve is only an active order in NEW/PICKING.  A live ledger
    # row attached to PACKED/SENT/DELIVERED is a historical accounting hold: keep it
    # unavailable for sale, but show it separately instead of calling it reserve.
    hold_map: dict[tuple[str, str, str], tuple[float, float]] = {}
    if rows:
        ids = [str(sku.id) for _, sku in rows]
        stmt = text("""
            SELECT r.sku_id,r.source_location_id,COALESCE(r.source_container_id,'') AS container_key,
              COALESCE(SUM(CASE WHEN o.local_status IN ('NEW','PICKING') THEN r.quantity ELSE 0 END),0) AS reserved,
              COALESCE(SUM(CASE WHEN COALESCE(o.local_status,'') NOT IN ('NEW','PICKING') AND NOT (r.status='PACKING_WRITE_OFF' AND io.id IS NOT NULL) THEN r.quantity ELSE 0 END),0) AS pending
            FROM fc_stock_reservations r
            LEFT JOIN marketplace_orders o ON CAST(o.id AS varchar)=r.order_id
            LEFT JOIN inventory_operations io
              ON CAST(io.client_id AS varchar)=CAST(r.client_id AS varchar) AND COALESCE(io.status,'COMPLETED')='COMPLETED'
             AND io.idempotency_key=('stock-core-v2-pack-' || CAST(o.id AS varchar) || '-' || CAST(r.id AS varchar))
            WHERE r.client_id=:client AND r.warehouse_id=:warehouse AND r.sku_id IN :ids
              AND r.source_location_id IS NOT NULL
              AND r.status IN ('ACTIVE','CONSUMING','CANCELLING','PACKING_WRITE_OFF')
            GROUP BY r.sku_id,r.source_location_id,COALESCE(r.source_container_id,'')
        """).bindparams(bindparam("ids", expanding=True))
        try:
            for hold in db.execute(stmt,{"client":str(client_id),"warehouse":str(warehouse_id),"ids":ids}).mappings():
                hold_map[(str(hold["sku_id"]),str(hold["source_location_id"]),str(hold["container_key"] or ""))]=(float(hold["reserved"] or 0),float(hold["pending"] or 0))
        except Exception:
            hold_map = {}
    warehouse_truth: dict[str, dict] = {}
    for _, sku in rows:
        if str(sku.id) in warehouse_truth:
            continue
        try:
            warehouse_truth[str(sku.id)] = stock_truth_for_sku(
                db, client_id=client_id, warehouse_id=warehouse_id, sku_id=sku.id
            )
        except Exception:
            warehouse_truth[str(sku.id)] = {}
    items = []
    for balance, sku in rows:
        physical = float(balance.quantity or 0)
        reserved, pending = hold_map.get((str(sku.id), str(balance.location_id), str(container.id)), (0.0, 0.0))
        truth = warehouse_truth.get(str(sku.id), {})
        warehouse_physical = float(truth.get("physical", physical) or 0)
        warehouse_reserved = float(truth.get("reserved", 0) or 0)
        warehouse_pending = float(truth.get("accounting_pending", 0) or 0)
        warehouse_free = float(truth.get("available_to_sell", max(0.0, warehouse_physical - warehouse_reserved - warehouse_pending)) or 0)
        items.append({
            "sku_id": str(sku.id),
            "sku_code": sku.sku_code,
            "article": sku.article,
            "name": sku.name,
            "size": sku.size,
            "barcode": sku.barcode,
            "quantity": physical,
            "reserved_quantity": reserved,
            "accounting_pending_quantity": pending,
            "free_quantity": max(0.0, physical - reserved - pending),
            "warehouse_quantity": warehouse_physical,
            "warehouse_received_quantity": float(truth.get("received", 0) or 0),
            "warehouse_shipped_quantity": float(truth.get("shipped", 0) or 0),
            "warehouse_adjustment_in_quantity": float(truth.get("adjustment_in", 0) or 0),
            "warehouse_adjustment_out_quantity": float(truth.get("adjustment_out", 0) or 0),
            "warehouse_opening_balance_estimate": float(truth.get("opening_balance_estimate", 0) or 0),
            "warehouse_sale_reserved_quantity": float(truth.get("sale_reserved", 0) or 0),
            "warehouse_picking_reserved_quantity": float(truth.get("picking_reserved", 0) or 0),
            "warehouse_reserved_quantity": warehouse_reserved,
            "warehouse_accounting_pending_quantity": warehouse_pending,
            "warehouse_free_quantity": warehouse_free,
        })
    return {
        "container": ContainerRead.model_validate(container).model_dump(mode="json"),
        "location": {"id": str(location.id), "code": location.code, "barcode": location.barcode, "name": location.name} if location else None,
        "items": items,
    }
