"""Isolation tests: tenant subdomain may only see/use own warehouses and clients."""

from __future__ import annotations

import uuid

import pytest
from fastapi import FastAPI, HTTPException, Request
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from app.core.database import get_db
from app.models import Client, Warehouse
from app.routers import masterdata as masterdata_router_module
from app.schemas.fulfillment import FulfillmentCreate, PrintStationCreate
from app.services import fulfillment_platform as svc
from app.services import tenant_host as th
from app.services.tenant_scope import (
    FulfillmentScope,
    assert_ids_in_scope,
    collect_scope_ids,
    load_fulfillment_scope,
    validate_request_against_scope,
)


def _activate_fc(db, code: str, subdomain: str):
    fc = svc.create_fulfillment(
        db,
        FulfillmentCreate(
            code=code,
            name=code,
            subdomain=subdomain,
            admin_username=f"admin-{code.lower()}",
            admin_password="Password123",
            primary_warehouse_code="MAIN",
            primary_warehouse_name="Main",
        ),
        actor="t",
    )
    svc.add_print_station(db, fc, PrintStationCreate(station_code="PACK-01"), actor="t")
    fc, _ = svc.activate_fulfillment(db, fc, actor="t")
    return fc


class TestScopeHelpers:
    def test_collect_nested_ids(self):
        clients, warehouses, credentials, skus, containers, locations = collect_scope_ids(
            {
                "client_id": "11111111-1111-1111-1111-111111111111",
                "nested": {"warehouse_id": "22222222-2222-2222-2222-222222222222"},
                "credential_id": "33333333-3333-3333-3333-333333333333",
            }
        )
        assert uuid.UUID("11111111-1111-1111-1111-111111111111") in clients
        assert uuid.UUID("22222222-2222-2222-2222-222222222222") in warehouses
        assert uuid.UUID("33333333-3333-3333-3333-333333333333") in credentials

    def test_assert_ids_in_scope_blocks_foreign(self):
        scope = FulfillmentScope(
            fulfillment_id=uuid.uuid4(),
            warehouse_ids={uuid.uuid4()},
            client_ids={uuid.uuid4()},
        )
        with pytest.raises(HTTPException) as exc:
            assert_ids_in_scope(scope, client_ids=[uuid.uuid4()])
        assert exc.value.status_code == 403


class TestScopeLoadAndValidate:
    def test_load_scope_for_fulfillment(self, db):
        fc = _activate_fc(db, "ISO1", "fc-iso1")
        other = Warehouse(id=uuid.uuid4(), code="OTHER-WH", name="Other", is_active=True)
        db.add(other)
        db.commit()

        scope = load_fulfillment_scope(db, fc.id)
        assert scope.warehouse_ids
        assert other.id not in scope.warehouse_ids

        foreign_client = Client(id=uuid.uuid4(), code="FOREIGN", name="Foreign", is_active=True)
        db.add(foreign_client)
        db.commit()
        with pytest.raises(HTTPException):
            assert_ids_in_scope(scope, client_ids=[foreign_client.id])

    def test_validate_query_against_scope(self, db):
        fc = _activate_fc(db, "ISO2", "fc-iso2")
        scope = load_fulfillment_scope(db, fc.id)
        own_wh = next(iter(scope.warehouse_ids))
        foreign_wh = uuid.uuid4()

        class FakeRequest:
            query_params = type("Q", (), {"multi_items": lambda self: [("warehouse_id", str(foreign_wh))]})()
            headers = {}

        with pytest.raises(HTTPException):
            validate_request_against_scope(FakeRequest(), scope, None, db)

        class OkRequest:
            query_params = type("Q", (), {"multi_items": lambda self: [("warehouse_id", str(own_wh))]})()
            headers = {}

        validate_request_against_scope(OkRequest(), scope, None, db)


class TestMasterdataIsolationApi:
    def test_lists_and_cross_tenant_blocked(self, db_engine, db, monkeypatch):
        monkeypatch.setattr(th.settings, "platform_base_domain", "wms.example.ru")
        monkeypatch.setattr(th.settings, "tenant_host_mode", "strict")

        fc_a = _activate_fc(db, "AAA", "fc-aaa")
        fc_b = _activate_fc(db, "BBB", "fc-bbb")
        scope_b = load_fulfillment_scope(db, fc_b.id)
        wh_b = next(iter(scope_b.warehouse_ids))

        SessionLocal = sessionmaker(bind=db_engine, autoflush=False, expire_on_commit=False)

        app = FastAPI()
        app.include_router(masterdata_router_module.router)

        @app.middleware("http")
        async def inject_tenant_scope(request: Request, call_next):
            host = (request.headers.get("host") or "").split(":")[0].lower()
            session = SessionLocal()
            try:
                from app.services.tenant_host import resolve_tenant_host
                from app.services.tenant_scope import attach_scope_to_request, load_fulfillment_scope, validate_request_against_scope

                resolution = resolve_tenant_host(session, host)
                request.state.tenant_resolution = resolution
                request.state.fulfillment_id = resolution.fulfillment_id
                body = b""
                if request.method in {"POST", "PUT", "PATCH"}:
                    body = await request.body()

                    async def _replay():
                        return {"type": "http.request", "body": body, "more_body": False}

                    request = Request(request.scope, _replay)
                if resolution.kind in {"subdomain", "custom_domain"} and resolution.fulfillment_id:
                    scope = load_fulfillment_scope(session, resolution.fulfillment_id)
                    attach_scope_to_request(request, scope)
                    try:
                        validate_request_against_scope(request, scope, body, session)
                    except HTTPException as exc:
                        from fastapi.responses import JSONResponse

                        return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})
                else:
                    attach_scope_to_request(request, None)
            finally:
                session.close()
            return await call_next(request)

        def _override_db():
            session = SessionLocal()
            try:
                yield session
            finally:
                session.close()

        app.dependency_overrides[get_db] = _override_db
        client = TestClient(app)

        listed = client.get("/masterdata/warehouses", headers={"Host": "fc-aaa.wms.example.ru"})
        assert listed.status_code == 200
        codes = {row["code"] for row in listed.json()}
        assert any(code.endswith("MAIN") or code == "MAIN" or "AAA" in code or code == "MAIN" for code in codes) or codes
        # Must not include BBB warehouse id
        ids = {row["id"] for row in listed.json()}
        assert str(wh_b) not in ids

        blocked = client.get(
            f"/masterdata/locations?warehouse_id={wh_b}",
            headers={"Host": "fc-aaa.wms.example.ru"},
        )
        assert blocked.status_code == 403

        # Creating warehouse on tenant host auto-links into that FC
        created = client.post(
            "/masterdata/warehouses",
            headers={"Host": "fc-aaa.wms.example.ru", "Content-Type": "application/json"},
            json={"code": "NEW-A", "name": "New A"},
        )
        assert created.status_code == 200, created.text
        new_id = created.json()["id"]
        again = client.get("/masterdata/warehouses", headers={"Host": "fc-aaa.wms.example.ru"})
        assert new_id in {row["id"] for row in again.json()}

        # Foreign host does not see it
        other_list = client.get("/masterdata/warehouses", headers={"Host": "fc-bbb.wms.example.ru"})
        assert new_id not in {row["id"] for row in other_list.json()}

        # Apex/default style host without subdomain isolation sees unscoped lists
        monkeypatch.setattr(th.settings, "tenant_host_mode", "soft")
        apex = client.get("/masterdata/warehouses", headers={"Host": "wms.example.ru"})
        assert apex.status_code == 200
        assert len(apex.json()) >= 2
