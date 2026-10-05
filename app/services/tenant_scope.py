"""Fulfillment (tenant) data scope helpers.

On subdomain / custom_domain hosts, warehouse and client IDs must belong to the
resolved fulfillment. Apex / DEFAULT hosts keep legacy unscoped behaviour.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from typing import Any, Iterable

from fastapi import HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Container, Location, SKU
from app.models.fulfillment import FulfillmentClient, FulfillmentWarehouse
from app.models.integration import MarketplaceCredential

SCOPE_KINDS = frozenset({"subdomain", "custom_domain"})

SKIP_PREFIXES = (
    "/health",
    "/static/",
    "/auth/",
    "/tenant/",
    "/platform/",
    "/internal/",
    "/downloads/",
    "/help/",
    "/client/",
)
SKIP_EXACT = frozenset(
    {
        "/login",
        "/client",
        "/client/login",
        "/tsd/login",
        "/tsd/install",
        "/tsd/app-info",
        "/tsd/install-qr.png",
        "/",
    }
)

CLIENT_KEYS = frozenset({"client_id", "clientId", "clientID"})
WAREHOUSE_KEYS = frozenset({"warehouse_id", "warehouseId", "warehouseID", "physical_warehouse_id"})
CLIENT_LIST_KEYS = frozenset({"client_ids", "clientIds"})
WAREHOUSE_LIST_KEYS = frozenset({"warehouse_ids", "warehouseIds"})
CREDENTIAL_KEYS = frozenset({"credential_id", "credentialId"})
SKU_KEYS = frozenset({"sku_id", "skuId"})
CONTAINER_KEYS = frozenset({"container_id", "containerId", "source_container_id"})
LOCATION_KEYS = frozenset({"location_id", "locationId", "source_location_id", "parent_id"})


@dataclass
class FulfillmentScope:
    fulfillment_id: uuid.UUID
    warehouse_ids: set[uuid.UUID] = field(default_factory=set)
    client_ids: set[uuid.UUID] = field(default_factory=set)
    enforced: bool = True

    def allows_warehouse(self, warehouse_id: uuid.UUID | str | None) -> bool:
        if warehouse_id is None or warehouse_id == "":
            return True
        try:
            value = warehouse_id if isinstance(warehouse_id, uuid.UUID) else uuid.UUID(str(warehouse_id))
        except Exception:
            return False
        return value in self.warehouse_ids

    def allows_client(self, client_id: uuid.UUID | str | None) -> bool:
        if client_id is None or client_id == "":
            return True
        try:
            value = client_id if isinstance(client_id, uuid.UUID) else uuid.UUID(str(client_id))
        except Exception:
            return False
        return value in self.client_ids


def path_skips_scope(path: str) -> bool:
    if path in SKIP_EXACT:
        return True
    return any(path.startswith(prefix) for prefix in SKIP_PREFIXES)


def is_tenant_isolated(request: Request) -> bool:
    resolution = getattr(request.state, "tenant_resolution", None)
    if resolution is None:
        return False
    return resolution.kind in SCOPE_KINDS and bool(resolution.fulfillment_id)


def load_fulfillment_scope(db: Session, fulfillment_id: uuid.UUID) -> FulfillmentScope:
    warehouses = db.execute(
        select(FulfillmentWarehouse.warehouse_id).where(
            FulfillmentWarehouse.fulfillment_id == fulfillment_id,
            FulfillmentWarehouse.is_active.is_(True),
        )
    ).scalars().all()
    clients = db.execute(
        select(FulfillmentClient.client_id).where(
            FulfillmentClient.fulfillment_id == fulfillment_id,
            FulfillmentClient.is_active.is_(True),
        )
    ).scalars().all()
    return FulfillmentScope(
        fulfillment_id=fulfillment_id,
        warehouse_ids={uuid.UUID(str(x)) for x in warehouses},
        client_ids={uuid.UUID(str(x)) for x in clients},
        enforced=True,
    )


def attach_scope_to_request(request: Request, scope: FulfillmentScope | None) -> None:
    request.state.fc_scope = scope
    request.state.fc_scope_enforced = bool(scope and scope.enforced)
    request.state.fc_warehouse_ids = set(scope.warehouse_ids) if scope else set()
    request.state.fc_client_ids = set(scope.client_ids) if scope else set()


def get_request_scope(request: Request) -> FulfillmentScope | None:
    return getattr(request.state, "fc_scope", None)


def _as_uuid(value: Any) -> uuid.UUID | None:
    if value is None or value == "":
        return None
    if isinstance(value, uuid.UUID):
        return value
    try:
        return uuid.UUID(str(value))
    except Exception:
        return None


def collect_scope_ids(payload: Any) -> tuple[set[uuid.UUID], set[uuid.UUID], set[uuid.UUID], set[uuid.UUID], set[uuid.UUID], set[uuid.UUID]]:
    """Walk JSON-like structures and collect scope-related UUID references."""
    clients: set[uuid.UUID] = set()
    warehouses: set[uuid.UUID] = set()
    credentials: set[uuid.UUID] = set()
    skus: set[uuid.UUID] = set()
    containers: set[uuid.UUID] = set()
    locations: set[uuid.UUID] = set()

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                if key in CLIENT_KEYS:
                    uid = _as_uuid(value)
                    if uid:
                        clients.add(uid)
                elif key in WAREHOUSE_KEYS:
                    uid = _as_uuid(value)
                    if uid:
                        warehouses.add(uid)
                elif key in CLIENT_LIST_KEYS and isinstance(value, list):
                    for item in value:
                        uid = _as_uuid(item)
                        if uid:
                            clients.add(uid)
                elif key in WAREHOUSE_LIST_KEYS and isinstance(value, list):
                    for item in value:
                        uid = _as_uuid(item)
                        if uid:
                            warehouses.add(uid)
                elif key in CREDENTIAL_KEYS:
                    uid = _as_uuid(value)
                    if uid:
                        credentials.add(uid)
                elif key in SKU_KEYS:
                    uid = _as_uuid(value)
                    if uid:
                        skus.add(uid)
                elif key in CONTAINER_KEYS:
                    uid = _as_uuid(value)
                    if uid:
                        containers.add(uid)
                elif key in LOCATION_KEYS:
                    uid = _as_uuid(value)
                    if uid:
                        locations.add(uid)
                else:
                    walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(payload)
    return clients, warehouses, credentials, skus, containers, locations


def collect_query_scope_ids(
    request: Request,
) -> tuple[set[uuid.UUID], set[uuid.UUID], set[uuid.UUID], set[uuid.UUID], set[uuid.UUID], set[uuid.UUID]]:
    clients: set[uuid.UUID] = set()
    warehouses: set[uuid.UUID] = set()
    credentials: set[uuid.UUID] = set()
    skus: set[uuid.UUID] = set()
    containers: set[uuid.UUID] = set()
    locations: set[uuid.UUID] = set()
    for key, value in request.query_params.multi_items():
        uid = _as_uuid(value)
        if not uid:
            continue
        if key in CLIENT_KEYS or key in CLIENT_LIST_KEYS:
            clients.add(uid)
        elif key in WAREHOUSE_KEYS or key in WAREHOUSE_LIST_KEYS:
            warehouses.add(uid)
        elif key in CREDENTIAL_KEYS:
            credentials.add(uid)
        elif key in SKU_KEYS:
            skus.add(uid)
        elif key in CONTAINER_KEYS:
            containers.add(uid)
        elif key in LOCATION_KEYS:
            locations.add(uid)
    return clients, warehouses, credentials, skus, containers, locations


def _expand_indirect_ids(
    db: Session,
    *,
    credentials: set[uuid.UUID],
    skus: set[uuid.UUID],
    containers: set[uuid.UUID],
    locations: set[uuid.UUID],
) -> tuple[set[uuid.UUID], set[uuid.UUID]]:
    """Resolve indirect object IDs into client/warehouse ownership checks."""
    clients: set[uuid.UUID] = set()
    warehouses: set[uuid.UUID] = set()
    if credentials:
        for client_id in db.execute(
            select(MarketplaceCredential.client_id).where(MarketplaceCredential.id.in_(credentials))
        ).scalars().all():
            clients.add(uuid.UUID(str(client_id)))
    if skus:
        for client_id in db.execute(select(SKU.client_id).where(SKU.id.in_(skus))).scalars().all():
            clients.add(uuid.UUID(str(client_id)))
    if containers:
        rows = db.execute(
            select(Container.client_id, Container.warehouse_id).where(Container.id.in_(containers))
        ).all()
        for client_id, warehouse_id in rows:
            clients.add(uuid.UUID(str(client_id)))
            warehouses.add(uuid.UUID(str(warehouse_id)))
    if locations:
        for warehouse_id in db.execute(
            select(Location.warehouse_id).where(Location.id.in_(locations))
        ).scalars().all():
            warehouses.add(uuid.UUID(str(warehouse_id)))
    return clients, warehouses


def assert_ids_in_scope(
    scope: FulfillmentScope,
    *,
    client_ids: Iterable[uuid.UUID] = (),
    warehouse_ids: Iterable[uuid.UUID] = (),
) -> None:
    bad_clients = [str(x) for x in client_ids if x not in scope.client_ids]
    bad_warehouses = [str(x) for x in warehouse_ids if x not in scope.warehouse_ids]
    if bad_clients or bad_warehouses:
        raise HTTPException(
            status_code=403,
            detail={
                "message": "Объект не принадлежит текущему фулфилменту",
                "code": "FULFILLMENT_SCOPE",
                "fulfillment_id": str(scope.fulfillment_id),
                "forbidden_client_ids": bad_clients,
                "forbidden_warehouse_ids": bad_warehouses,
            },
        )


def validate_request_against_scope(
    request: Request,
    scope: FulfillmentScope,
    body: bytes | None,
    db: Session,
) -> None:
    clients, warehouses, credentials, skus, containers, locations = collect_query_scope_ids(request)
    if body:
        content_type = (request.headers.get("content-type") or "").lower()
        if "application/json" in content_type:
            payload = parse_json_body(body)
            if payload is not None:
                c2, w2, cred2, sku2, cont2, loc2 = collect_scope_ids(payload)
                clients |= c2
                warehouses |= w2
                credentials |= cred2
                skus |= sku2
                containers |= cont2
                locations |= loc2
    extra_clients, extra_warehouses = _expand_indirect_ids(
        db,
        credentials=credentials,
        skus=skus,
        containers=containers,
        locations=locations,
    )
    clients |= extra_clients
    warehouses |= extra_warehouses
    assert_ids_in_scope(scope, client_ids=clients, warehouse_ids=warehouses)


def ensure_client_in_request_scope(request: Request, client_id: uuid.UUID | str | None) -> None:
    scope = get_request_scope(request)
    if not scope or not scope.enforced:
        return
    if client_id is None:
        return
    assert_ids_in_scope(scope, client_ids=[_as_uuid(client_id)] if _as_uuid(client_id) else [])


def ensure_warehouse_in_request_scope(request: Request, warehouse_id: uuid.UUID | str | None) -> None:
    scope = get_request_scope(request)
    if not scope or not scope.enforced:
        return
    if warehouse_id is None:
        return
    uid = _as_uuid(warehouse_id)
    if uid:
        assert_ids_in_scope(scope, warehouse_ids=[uid])


def link_client_to_fulfillment(db: Session, fulfillment_id: uuid.UUID, client_id: uuid.UUID) -> None:
    exists = db.execute(
        select(FulfillmentClient.id).where(
            FulfillmentClient.fulfillment_id == fulfillment_id,
            FulfillmentClient.client_id == client_id,
        )
    ).scalars().first()
    if exists:
        return
    db.add(
        FulfillmentClient(
            id=uuid.uuid4(),
            fulfillment_id=fulfillment_id,
            client_id=client_id,
            is_pilot=False,
            is_active=True,
        )
    )


def link_warehouse_to_fulfillment(
    db: Session,
    fulfillment_id: uuid.UUID,
    warehouse_id: uuid.UUID,
    *,
    warehouse_kind: str = "MAIN",
) -> None:
    exists = db.execute(
        select(FulfillmentWarehouse.id).where(
            FulfillmentWarehouse.fulfillment_id == fulfillment_id,
            FulfillmentWarehouse.warehouse_id == warehouse_id,
        )
    ).scalars().first()
    if exists:
        return
    has_primary = db.execute(
        select(FulfillmentWarehouse.id).where(
            FulfillmentWarehouse.fulfillment_id == fulfillment_id,
            FulfillmentWarehouse.is_primary.is_(True),
            FulfillmentWarehouse.is_active.is_(True),
        )
    ).scalars().first()
    db.add(
        FulfillmentWarehouse(
            id=uuid.uuid4(),
            fulfillment_id=fulfillment_id,
            warehouse_id=warehouse_id,
            warehouse_kind=warehouse_kind,
            is_primary=not bool(has_primary),
            is_active=True,
        )
    )


def parse_json_body(body: bytes) -> Any:
    if not body:
        return None
    try:
        return json.loads(body.decode("utf-8"))
    except Exception:
        return None
