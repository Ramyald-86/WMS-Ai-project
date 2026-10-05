"""Tests for subdomain / Host-based fulfillment resolution."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from app.core.database import get_db
from app.routers.tenant import router as tenant_router
from app.schemas.fulfillment import (
    FulfillmentCreate,
    FulfillmentUpdate,
    PrintStationCreate,
    UserCreate,
    WarehouseLinkCreate,
)
from app.services import fulfillment_platform as svc
from app.services import tenant_host as th


@pytest.fixture()
def base_domain(monkeypatch):
    monkeypatch.setattr(th.settings, "platform_base_domain", "wms.example.ru")
    monkeypatch.setattr(th.settings, "platform_host_aliases", "")
    monkeypatch.setattr(th.settings, "tenant_host_mode", "strict")
    return "wms.example.ru"


@pytest.fixture()
def soft_mode(monkeypatch):
    monkeypatch.setattr(th.settings, "platform_base_domain", "wms.example.ru")
    monkeypatch.setattr(th.settings, "tenant_host_mode", "soft")


class TestHostParsing:
    def test_normalize_host_strips_port_and_case(self):
        assert th.normalize_host("FC-SPB.Example.RU:443") == "fc-spb.example.ru"

    def test_extract_subdomain(self):
        assert th.extract_subdomain("fc-spb.wms.example.ru", "wms.example.ru") == "fc-spb"
        assert th.extract_subdomain("wms.example.ru", "wms.example.ru") is None
        assert th.extract_subdomain("a.b.wms.example.ru", "wms.example.ru") is None

    def test_request_host_prefers_forwarded(self):
        assert th.request_host("api:8000", "fc-spb.wms.example.ru, other") == "fc-spb.wms.example.ru"

    def test_reserved_subdomain(self):
        assert th.is_reserved_subdomain("www") is True
        assert th.is_reserved_subdomain("fc-spb") is False


class TestResolveTenant:
    def test_platform_apex_uses_default(self, db, seed_legacy, base_domain):
        svc.ensure_default_fulfillment(db)
        db.commit()
        resolution = th.resolve_tenant_host(db, "wms.example.ru")
        assert resolution.kind in {"platform", "default"}
        assert resolution.allowed is True
        assert resolution.fulfillment is not None
        assert resolution.fulfillment.is_default is True

    def test_subdomain_resolves_active_fulfillment(self, db, base_domain):
        fc = svc.create_fulfillment(
            db,
            FulfillmentCreate(
                code="SPB",
                name="SPb",
                subdomain="fc-spb",
                admin_username="a",
                admin_password="Password123",
                primary_warehouse_code="MAIN",
                primary_warehouse_name="Main",
            ),
            actor="t",
        )
        svc.add_print_station(db, fc, PrintStationCreate(station_code="PACK-01"), actor="t")
        fc, _ = svc.activate_fulfillment(db, fc, actor="t")

        resolution = th.resolve_tenant_host(db, "fc-spb.wms.example.ru")
        assert resolution.kind == "subdomain"
        assert resolution.subdomain == "fc-spb"
        assert resolution.fulfillment_id == fc.id
        assert resolution.allowed is True

    def test_unknown_subdomain_strict_denied(self, db, base_domain):
        resolution = th.resolve_tenant_host(db, "missing.wms.example.ru")
        assert resolution.kind == "unknown"
        assert resolution.allowed is False

    def test_unknown_subdomain_soft_allowed(self, db, soft_mode):
        resolution = th.resolve_tenant_host(db, "missing.wms.example.ru")
        assert resolution.kind == "unknown"
        assert resolution.allowed is True

    def test_suspended_denied_in_strict(self, db, base_domain):
        fc = svc.create_fulfillment(
            db,
            FulfillmentCreate(
                code="SUS",
                name="Sus",
                subdomain="fc-sus",
                admin_username="a",
                admin_password="Password123",
                primary_warehouse_code="MAIN",
                primary_warehouse_name="Main",
            ),
            actor="t",
        )
        svc.add_print_station(db, fc, PrintStationCreate(station_code="PACK-01"), actor="t")
        fc, _ = svc.activate_fulfillment(db, fc, actor="t")
        fc = svc.suspend_fulfillment(db, fc, actor="t")

        resolution = th.resolve_tenant_host(db, "fc-sus.wms.example.ru")
        assert resolution.fulfillment_id == fc.id
        assert resolution.allowed is False
        assert resolution.reason

    def test_custom_domain(self, db, base_domain):
        fc = svc.create_fulfillment(
            db,
            FulfillmentCreate(
                code="CUST",
                name="Custom",
                custom_domain="wms.client.ru",
                admin_username="a",
                admin_password="Password123",
                primary_warehouse_code="MAIN",
                primary_warehouse_name="Main",
            ),
            actor="t",
        )
        svc.add_print_station(db, fc, PrintStationCreate(station_code="PACK-01"), actor="t")
        fc, _ = svc.activate_fulfillment(db, fc, actor="t")

        resolution = th.resolve_tenant_host(db, "wms.client.ru")
        assert resolution.kind == "custom_domain"
        assert resolution.fulfillment_id == fc.id

    def test_reserved_subdomain_treated_as_platform(self, db, seed_legacy, base_domain):
        svc.ensure_default_fulfillment(db)
        db.commit()
        resolution = th.resolve_tenant_host(db, "www.wms.example.ru")
        assert resolution.kind == "platform"
        assert resolution.allowed is True

    def test_create_rejects_reserved_subdomain(self, db, base_domain):
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc:
            svc.create_fulfillment(
                db,
                FulfillmentCreate(code="BAD", name="Bad", subdomain="www"),
                actor="t",
            )
        assert exc.value.status_code == 400

    def test_caddy_ask(self, db, base_domain):
        fc = svc.create_fulfillment(
            db,
            FulfillmentCreate(code="ASK", name="Ask", subdomain="fc-ask"),
            actor="t",
        )
        assert th.caddy_ask_allows_host(db, "fc-ask.wms.example.ru") is True
        assert th.caddy_ask_allows_host(db, "nope.wms.example.ru") is False
        assert th.caddy_ask_allows_host(db, "wms.example.ru") is True

        svc.add_user(
            db,
            fc,
            UserCreate(username="a", display_name="A", password="Password123", role="FC_ADMIN"),
            actor="t",
        )
        svc.add_warehouse(
            db,
            fc,
            WarehouseLinkCreate(code="MAIN", name="Main", is_primary=True),
            actor="t",
        )
        svc.add_print_station(db, fc, PrintStationCreate(station_code="PACK-01"), actor="t")
        fc, _ = svc.activate_fulfillment(db, fc, actor="t")
        fc = svc.suspend_fulfillment(db, fc, actor="t")
        fc = svc.archive_fulfillment(db, fc, actor="t")
        assert th.caddy_ask_allows_host(db, "fc-ask.wms.example.ru") is False


class TestTenantApi:
    def test_tenant_current_and_caddy_ask(self, db_engine, db, base_domain, monkeypatch):
        monkeypatch.setattr(th.settings, "platform_base_domain", "wms.example.ru")
        monkeypatch.setattr(th.settings, "tenant_host_mode", "strict")

        fc = svc.create_fulfillment(
            db,
            FulfillmentCreate(
                code="APIHOST",
                name="API Host",
                subdomain="fc-api",
                admin_username="a",
                admin_password="Password123",
                primary_warehouse_code="MAIN",
                primary_warehouse_name="Main",
            ),
            actor="t",
        )
        svc.add_print_station(db, fc, PrintStationCreate(station_code="PACK-01"), actor="t")
        svc.activate_fulfillment(db, fc, actor="t")

        SessionLocal = sessionmaker(bind=db_engine, autoflush=False, expire_on_commit=False)
        app = FastAPI()
        app.include_router(tenant_router)

        def _override_db():
            session = SessionLocal()
            try:
                yield session
            finally:
                session.close()

        app.dependency_overrides[get_db] = _override_db
        client = TestClient(app)

        current = client.get("/tenant/current", headers={"Host": "fc-api.wms.example.ru"})
        assert current.status_code == 200, current.text
        body = current.json()
        assert body["kind"] == "subdomain"
        assert body["subdomain"] == "fc-api"
        assert body["fulfillment"]["code"] == "APIHOST"

        resolved = client.get("/tenant/resolve", params={"host": "fc-api.wms.example.ru"})
        assert resolved.status_code == 200
        assert resolved.json()["fulfillment"]["id"] == str(fc.id)

        ask_ok = client.get("/internal/caddy-ask", params={"domain": "fc-api.wms.example.ru"})
        assert ask_ok.status_code == 200
        ask_deny = client.get("/internal/caddy-ask", params={"domain": "ghost.wms.example.ru"})
        assert ask_deny.status_code == 404

    def test_update_subdomain_reserved(self, db, base_domain):
        from fastapi import HTTPException

        fc = svc.create_fulfillment(
            db,
            FulfillmentCreate(code="UPDSUB", name="U", subdomain="fc-old"),
            actor="t",
        )
        with pytest.raises(HTTPException) as exc:
            svc.update_fulfillment(db, fc, FulfillmentUpdate(subdomain="admin"), actor="t")
        assert exc.value.status_code == 400
