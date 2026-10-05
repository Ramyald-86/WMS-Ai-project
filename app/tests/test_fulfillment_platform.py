"""Full coverage for platform fulfillment onboarding (service + API).

Run:
    pytest app/tests/test_fulfillment_platform.py -q
"""

from __future__ import annotations

import uuid

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.core.auth import verify_password
from app.core.crypto import decrypt_secret
from app.models import Client, MarketplaceCredential, Warehouse
from app.models.fulfillment import Fulfillment, FulfillmentUser, FulfillmentWarehouse
from app.schemas.fulfillment import (
    ClientLinkCreate,
    FeatureProfilePatch,
    FulfillmentCreate,
    FulfillmentUpdate,
    MarketplaceCredentialAttach,
    PrintStationCreate,
    PrintStationUpdate,
    UserCreate,
    UserUpdate,
    WarehouseLinkCreate,
    WarehouseLinkUpdate,
)
from app.services import fulfillment_platform as svc


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _create_ready_fulfillment(db, *, code: str = "MSK-01", activate: bool = False) -> Fulfillment:
    fc = svc.create_fulfillment(
        db,
        FulfillmentCreate(
            code=code,
            name=f"Fulfillment {code}",
            timezone="Europe/Moscow",
            admin_username=f"admin-{code.lower()}",
            admin_display_name="FC Admin",
            admin_password="SecurePass123",
            primary_warehouse_code="MAIN",
            primary_warehouse_name="Main warehouse",
            primary_warehouse_kind="MAIN",
        ),
        actor="tester",
    )
    svc.add_print_station(
        db,
        fc,
        PrintStationCreate(station_code="PACK-01", name="Pack desk 1"),
        actor="tester",
    )
    if activate:
        fc, _ = svc.activate_fulfillment(db, fc, actor="tester")
    return fc


# ---------------------------------------------------------------------------
# Seed / default fulfillment
# ---------------------------------------------------------------------------


class TestDefaultFulfillment:
    def test_ensure_default_links_legacy_masterdata(self, db, seed_legacy):
        fc = svc.ensure_default_fulfillment(db)
        db.commit()

        assert fc.code == "DEFAULT"
        assert fc.is_default is True
        assert fc.status == "ACTIVE"
        assert fc.encrypted_print_agent_token
        decrypt_secret(fc.encrypted_print_agent_token)

        warehouses = svc.list_warehouses(db, fc)
        clients = svc.list_clients(db, fc)
        assert any(w["warehouse_code"] == "LEGACY-WH" for w in warehouses)
        assert any(c["client_code"] == "LEGACY-CL" for c in clients)
        assert any(w["is_primary"] for w in warehouses)

    def test_ensure_default_is_idempotent(self, db, seed_legacy):
        first = svc.ensure_default_fulfillment(db)
        db.commit()
        second = svc.ensure_default_fulfillment(db)
        db.commit()
        assert first.id == second.id
        assert len(svc.list_warehouses(db, second)) == 1
        assert len(svc.list_clients(db, second)) == 1


# ---------------------------------------------------------------------------
# Create / update / list
# ---------------------------------------------------------------------------


class TestFulfillmentCrud:
    def test_create_draft_minimal(self, db):
        fc = svc.create_fulfillment(
            db,
            FulfillmentCreate(code="spb-01", name="  СПб Фулфилмент  "),
            actor="platform",
        )
        assert fc.code == "SPB-01"
        assert fc.name == "СПб Фулфилмент"
        assert fc.status == "DRAFT"
        assert fc.is_default is False
        assert fc.feature_profile["template"] == "full_fulfillcore"
        assert fc.has_print_agent_token if False else bool(fc.encrypted_print_agent_token)
        events = svc.list_events(db, fc)
        assert any(e.event_type == "CREATED" for e in events)

    def test_create_with_bootstrap_moves_to_onboarding(self, db):
        fc = svc.create_fulfillment(
            db,
            FulfillmentCreate(
                code="KZN-01",
                name="Kazan",
                admin_username="kzn-admin",
                admin_password="Password123",
                primary_warehouse_code="MAIN",
                primary_warehouse_name="Main",
            ),
            actor="platform",
        )
        assert fc.status == "ONBOARDING"
        assert len(svc.list_users(db, fc)) == 1
        assert len(svc.list_warehouses(db, fc)) == 1
        assert svc.list_warehouses(db, fc)[0]["is_primary"] is True

    def test_duplicate_code_rejected(self, db):
        svc.create_fulfillment(db, FulfillmentCreate(code="DUP-1", name="One"), actor="a")
        with pytest.raises(HTTPException) as exc:
            svc.create_fulfillment(db, FulfillmentCreate(code="dup-1", name="Two"), actor="a")
        assert exc.value.status_code == 409

    def test_duplicate_subdomain_rejected(self, db):
        svc.create_fulfillment(
            db,
            FulfillmentCreate(code="A1", name="A", subdomain="fc-a"),
            actor="a",
        )
        with pytest.raises(HTTPException) as exc:
            svc.create_fulfillment(
                db,
                FulfillmentCreate(code="B1", name="B", subdomain="fc-a"),
                actor="a",
            )
        assert exc.value.status_code == 409

    def test_update_identity_and_feature_profile(self, db):
        fc = svc.create_fulfillment(db, FulfillmentCreate(code="UPD-1", name="Old"), actor="a")
        updated = svc.update_fulfillment(
            db,
            fc,
            FulfillmentUpdate(
                name="New name",
                timezone="Asia/Yekaterinburg",
                feature_profile=FeatureProfilePatch(
                    modules={"fbo": False},
                    limits={"max_warehouses": 3},
                ),
            ),
            actor="editor",
        )
        assert updated.name == "New name"
        assert updated.timezone == "Asia/Yekaterinburg"
        assert updated.feature_profile["modules"]["fbo"] is False
        assert updated.feature_profile["modules"]["receiving"] is True
        assert updated.feature_profile["limits"]["max_warehouses"] == 3
        assert updated.status == "ONBOARDING"

    def test_list_summaries_counts(self, db):
        fc = _create_ready_fulfillment(db, code="SUM-1")
        rows = svc.list_fulfillment_summaries(db)
        row = next(r for r in rows if r["id"] == fc.id)
        assert row["warehouses_count"] == 1
        assert row["users_count"] == 1
        assert row["print_stations_count"] == 1
        assert row["print_stations_active_count"] == 1


# ---------------------------------------------------------------------------
# Warehouses / clients / users / stations
# ---------------------------------------------------------------------------


class TestWarehouseLinks:
    def test_add_and_update_warehouse(self, db):
        fc = svc.create_fulfillment(db, FulfillmentCreate(code="WH-1", name="W"), actor="a")
        link = svc.add_warehouse(
            db,
            fc,
            WarehouseLinkCreate(code="MAIN", name="Main", warehouse_kind="MAIN", is_primary=True),
            actor="a",
        )
        assert link["warehouse_code"] == "MAIN"
        assert link["is_primary"] is True

        second = svc.add_warehouse(
            db,
            fc,
            WarehouseLinkCreate(code="FBS", name="FBS", warehouse_kind="FBS"),
            actor="a",
        )
        assert second["is_primary"] is False

        updated = svc.update_warehouse_link(
            db,
            fc,
            second["id"],
            WarehouseLinkUpdate(is_primary=True, address="Street 1"),
            actor="a",
        )
        assert updated["is_primary"] is True
        assert updated["address"] == "Street 1"
        first = next(w for w in svc.list_warehouses(db, fc) if w["id"] == link["id"])
        assert first["is_primary"] is False

    def test_warehouse_code_collision_gets_prefixed(self, db):
        db.add(Warehouse(id=uuid.uuid4(), code="MAIN", name="Existing", is_active=True))
        db.commit()
        fc = svc.create_fulfillment(db, FulfillmentCreate(code="PREF-1", name="P"), actor="a")
        link = svc.add_warehouse(
            db,
            fc,
            WarehouseLinkCreate(code="MAIN", name="New main"),
            actor="a",
        )
        assert link["warehouse_code"] == "PREF-1-MAIN"

    def test_warehouse_cannot_belong_to_two_fulfillments(self, db):
        shared = Warehouse(id=uuid.uuid4(), code="SHARED", name="Shared", is_active=True)
        db.add(shared)
        db.commit()
        a = svc.create_fulfillment(db, FulfillmentCreate(code="A-WH", name="A"), actor="a")
        b = svc.create_fulfillment(db, FulfillmentCreate(code="B-WH", name="B"), actor="a")
        svc.add_warehouse(
            db,
            a,
            WarehouseLinkCreate(code="X", name="X", existing_warehouse_id=shared.id),
            actor="a",
        )
        with pytest.raises(HTTPException) as exc:
            svc.add_warehouse(
                db,
                b,
                WarehouseLinkCreate(code="Y", name="Y", existing_warehouse_id=shared.id),
                actor="a",
            )
        assert exc.value.status_code == 409

    def test_deactivate_primary_promotes_another(self, db):
        fc = _create_ready_fulfillment(db, code="WH-DEACT", activate=True)
        second = svc.add_warehouse(
            db,
            fc,
            WarehouseLinkCreate(code="FBS-2", name="FBS", warehouse_kind="FBS"),
            actor="a",
        )
        primary = next(w for w in svc.list_warehouses(db, fc) if w["is_primary"])
        svc.deactivate_warehouse_link(db, fc, primary["id"], actor="a")
        rows = svc.list_warehouses(db, fc)
        assert next(w for w in rows if w["id"] == primary["id"])["is_active"] is False
        assert next(w for w in rows if w["id"] == second["id"])["is_primary"] is True

    def test_cannot_deactivate_last_primary_on_active(self, db):
        fc = _create_ready_fulfillment(db, code="WH-LAST", activate=True)
        primary = next(w for w in svc.list_warehouses(db, fc) if w["is_primary"])
        with pytest.raises(HTTPException) as exc:
            svc.deactivate_warehouse_link(db, fc, primary["id"], actor="a")
        assert exc.value.status_code == 409

    def test_warehouse_limit(self, db):
        fc = svc.create_fulfillment(
            db,
            FulfillmentCreate(
                code="LIM-WH",
                name="L",
                feature_profile=FeatureProfilePatch(limits={"max_warehouses": 1}),
            ),
            actor="a",
        )
        svc.add_warehouse(db, fc, WarehouseLinkCreate(code="MAIN", name="Main"), actor="a")
        with pytest.raises(HTTPException) as exc:
            svc.add_warehouse(db, fc, WarehouseLinkCreate(code="FBS", name="FBS"), actor="a")
        assert exc.value.status_code == 409


class TestClientLinks:
    def test_add_pilot_client_and_marketplace(self, db):
        fc = svc.create_fulfillment(db, FulfillmentCreate(code="CL-1", name="C"), actor="a")
        link = svc.add_client(
            db,
            fc,
            ClientLinkCreate(code="SELLER", name="Seller LLC", is_pilot=True),
            actor="a",
        )
        assert link["is_pilot"] is True
        cred = svc.attach_marketplace_credential(
            db,
            fc,
            link["id"],
            MarketplaceCredentialAttach(provider="WB", name="Main WB", api_key="wb-token-12345"),
            actor="a",
        )
        assert cred["provider"] == "WB"
        assert cred["is_active"] is True
        refreshed = svc.list_clients(db, fc)[0]
        assert refreshed["marketplace_credentials_count"] == 1

        # stored encrypted
        row = db.get(MarketplaceCredential, cred["credential_id"])
        assert row is not None
        assert decrypt_secret(row.encrypted_api_key) == "wb-token-12345"

    def test_ozon_requires_client_id_schema(self):
        with pytest.raises(Exception):
            MarketplaceCredentialAttach(provider="OZON", name="Ozon", api_key="ozon-key-123")

    def test_client_exclusive_across_fulfillments(self, db):
        shared = Client(id=uuid.uuid4(), code="SHARED-CL", name="Shared", is_active=True)
        db.add(shared)
        db.commit()
        a = svc.create_fulfillment(db, FulfillmentCreate(code="A-CL", name="A"), actor="a")
        b = svc.create_fulfillment(db, FulfillmentCreate(code="B-CL", name="B"), actor="a")
        svc.add_client(
            db,
            a,
            ClientLinkCreate(code="X", name="X", existing_client_id=shared.id),
            actor="a",
        )
        with pytest.raises(HTTPException) as exc:
            svc.add_client(
                db,
                b,
                ClientLinkCreate(code="Y", name="Y", existing_client_id=shared.id),
                actor="a",
            )
        assert exc.value.status_code == 409


class TestUsersAndStations:
    def test_user_password_hashed_and_update(self, db):
        fc = svc.create_fulfillment(db, FulfillmentCreate(code="USR-1", name="U"), actor="a")
        user = svc.add_user(
            db,
            fc,
            UserCreate(
                username="PackerOne",
                display_name="Packer",
                password="Password123",
                role="PACKER",
            ),
            actor="a",
        )
        assert user.username == "packerone"
        assert user.password_hash != "Password123"
        assert verify_password("Password123", user.password_hash)

        updated = svc.update_user(
            db,
            fc,
            user.id,
            UserUpdate(password="NewPassword123", role="PICKER"),
            actor="a",
        )
        assert updated.role == "PICKER"
        assert verify_password("NewPassword123", updated.password_hash)

    def test_cannot_disable_last_admin_on_active(self, db):
        fc = _create_ready_fulfillment(db, code="USR-LAST", activate=True)
        admin = next(u for u in svc.list_users(db, fc) if u.role == "FC_ADMIN")
        with pytest.raises(HTTPException) as exc:
            svc.update_user(db, fc, admin.id, UserUpdate(is_active=False), actor="a")
        assert exc.value.status_code == 409

    def test_print_station_normalize_and_update(self, db):
        fc = svc.create_fulfillment(db, FulfillmentCreate(code="PS-1", name="P"), actor="a")
        station = svc.add_print_station(
            db,
            fc,
            PrintStationCreate(station_code=" pack 02 ", name="Desk"),
            actor="a",
        )
        assert station.station_code == "PACK-02"
        updated = svc.update_print_station(
            db,
            fc,
            station.id,
            PrintStationUpdate(is_active=False, printer_name="XPrinter"),
            actor="a",
        )
        assert updated.is_active is False
        assert updated.printer_name == "XPrinter"

        with pytest.raises(HTTPException) as exc:
            svc.add_print_station(
                db,
                fc,
                PrintStationCreate(station_code="PACK-02"),
                actor="a",
            )
        assert exc.value.status_code == 409


# ---------------------------------------------------------------------------
# Lifecycle / readiness / tokens
# ---------------------------------------------------------------------------


class TestLifecycle:
    def test_readiness_and_activate(self, db):
        fc = svc.create_fulfillment(db, FulfillmentCreate(code="ACT-1", name="A"), actor="a")
        report = svc.readiness_report(db, fc)
        assert report.ready_to_activate is False
        assert any(not c.ok and c.required for c in report.checks)

        with pytest.raises(HTTPException) as exc:
            svc.activate_fulfillment(db, fc, actor="a")
        assert exc.value.status_code == 409

        fc = _create_ready_fulfillment(db, code="ACT-2", activate=False)
        report = svc.readiness_report(db, fc)
        assert report.ready_to_activate is True
        fc, report2 = svc.activate_fulfillment(db, fc, actor="a")
        assert fc.status == "ACTIVE"
        assert fc.activated_at is not None
        assert report2.status == "ACTIVE"

    def test_suspend_resume_archive_rules(self, db):
        fc = _create_ready_fulfillment(db, code="LIFE-1", activate=True)

        with pytest.raises(HTTPException):
            svc.archive_fulfillment(db, fc, actor="a")

        fc = svc.suspend_fulfillment(db, fc, actor="a")
        assert fc.status == "SUSPENDED"
        assert fc.suspended_at is not None

        fc = svc.resume_fulfillment(db, fc, actor="a")
        assert fc.status == "ACTIVE"
        assert fc.suspended_at is None

        fc = svc.suspend_fulfillment(db, fc, actor="a")
        fc = svc.archive_fulfillment(db, fc, actor="a")
        assert fc.status == "ARCHIVED"
        assert fc.archived_at is not None

        with pytest.raises(HTTPException):
            svc.update_fulfillment(db, fc, FulfillmentUpdate(name="Nope"), actor="a")
        with pytest.raises(HTTPException):
            svc.activate_fulfillment(db, fc, actor="a")

    def test_default_cannot_be_suspended_or_archived(self, db, seed_legacy):
        fc = svc.ensure_default_fulfillment(db)
        db.commit()
        with pytest.raises(HTTPException) as exc:
            svc.suspend_fulfillment(db, fc, actor="a")
        assert exc.value.status_code == 409
        with pytest.raises(HTTPException) as exc:
            # force non-active path: archive rejects default even from DRAFT-like misuse
            fc.status = "SUSPENDED"
            db.commit()
            svc.archive_fulfillment(db, fc, actor="a")
        assert exc.value.status_code == 409

    def test_print_token_reveal_and_rotate(self, db):
        fc = _create_ready_fulfillment(db, code="TOK-1")
        revealed = svc.reveal_print_token(fc)
        assert revealed["station_bound"] is False
        assert len(revealed["token"]) >= 20

        station_bound = svc.reveal_print_token(fc, "PACK-01")
        assert station_bound["station_bound"] is True
        assert station_bound["station_code"] == "PACK-01"
        assert station_bound["token"] != revealed["token"]

        new_token = svc.rotate_print_token(db, fc, actor="a")
        assert new_token != revealed["token"]
        assert svc.reveal_print_token(fc)["token"] == new_token


# ---------------------------------------------------------------------------
# API layer
# ---------------------------------------------------------------------------


class TestPlatformApi:
    def test_api_create_get_list_update(self, api_client):
        create = api_client.post(
            "/platform/fulfillments",
            json={
                "code": "api-01",
                "name": "API FC",
                "admin_username": "api-admin",
                "admin_password": "Password123",
                "primary_warehouse_code": "MAIN",
                "primary_warehouse_name": "Main",
            },
        )
        assert create.status_code == 201, create.text
        body = create.json()
        fc_id = body["id"]
        assert body["code"] == "API-01"
        assert body["status"] == "ONBOARDING"
        assert body["has_print_agent_token"] is True

        listed = api_client.get("/platform/fulfillments")
        assert listed.status_code == 200
        assert any(row["id"] == fc_id for row in listed.json())

        got = api_client.get(f"/platform/fulfillments/{fc_id}")
        assert got.status_code == 200
        assert got.json()["name"] == "API FC"

        patched = api_client.patch(
            f"/platform/fulfillments/{fc_id}",
            json={"name": "API FC Renamed", "notes": "demo"},
        )
        assert patched.status_code == 200
        assert patched.json()["name"] == "API FC Renamed"
        assert patched.json()["notes"] == "demo"

    def test_api_full_onboarding_flow(self, api_client):
        created = api_client.post(
            "/platform/fulfillments",
            json={"code": "FLOW-1", "name": "Flow"},
        ).json()
        fc_id = created["id"]

        user = api_client.post(
            f"/platform/fulfillments/{fc_id}/users",
            json={
                "username": "fc-admin",
                "display_name": "Admin",
                "password": "Password123",
                "role": "FC_ADMIN",
            },
        )
        assert user.status_code == 201, user.text

        wh = api_client.post(
            f"/platform/fulfillments/{fc_id}/warehouses",
            json={"code": "MAIN", "name": "Main", "warehouse_kind": "MAIN", "is_primary": True},
        )
        assert wh.status_code == 201, wh.text

        station = api_client.post(
            f"/platform/fulfillments/{fc_id}/print-stations",
            json={"station_code": "PACK-01"},
        )
        assert station.status_code == 201, station.text

        client = api_client.post(
            f"/platform/fulfillments/{fc_id}/clients",
            json={"code": "PILOT", "name": "Pilot Seller", "is_pilot": True},
        )
        assert client.status_code == 201, client.text
        link_id = client.json()["id"]

        cred = api_client.post(
            f"/platform/fulfillments/{fc_id}/clients/{link_id}/marketplace-credentials",
            json={"provider": "WB", "name": "WB Main", "api_key": "wb-secret-key-1"},
        )
        assert cred.status_code == 201, cred.text

        readiness = api_client.get(f"/platform/fulfillments/{fc_id}/readiness")
        assert readiness.status_code == 200
        assert readiness.json()["ready_to_activate"] is True

        activated = api_client.post(f"/platform/fulfillments/{fc_id}/activate")
        assert activated.status_code == 200, activated.text
        assert activated.json()["fulfillment"]["status"] == "ACTIVE"

        token = api_client.get(f"/platform/fulfillments/{fc_id}/print-token")
        assert token.status_code == 200
        assert token.json()["token"]

        rotated = api_client.post(f"/platform/fulfillments/{fc_id}/print-token/rotate")
        assert rotated.status_code == 200
        assert rotated.json()["token"] != token.json()["token"]

        suspended = api_client.post(f"/platform/fulfillments/{fc_id}/suspend")
        assert suspended.status_code == 200
        assert suspended.json()["status"] == "SUSPENDED"

        resumed = api_client.post(f"/platform/fulfillments/{fc_id}/resume")
        assert resumed.status_code == 200
        assert resumed.json()["status"] == "ACTIVE"

        api_client.post(f"/platform/fulfillments/{fc_id}/suspend")
        archived = api_client.post(f"/platform/fulfillments/{fc_id}/archive")
        assert archived.status_code == 200
        assert archived.json()["status"] == "ARCHIVED"

        events = api_client.get(f"/platform/fulfillments/{fc_id}/events")
        assert events.status_code == 200
        types = {e["event_type"] for e in events.json()}
        assert "CREATED" in types
        assert "ACTIVATED" in types
        assert "ARCHIVED" in types

    def test_api_validation_errors(self, api_client):
        bad = api_client.post(
            "/platform/fulfillments",
            json={"code": "X", "name": "Short code maybe ok", "admin_username": "only-user"},
        )
        # admin_username without password -> 422
        assert bad.status_code == 422

        missing = api_client.get(f"/platform/fulfillments/{uuid.uuid4()}")
        assert missing.status_code == 404

    def test_api_duplicate_code_conflict(self, api_client):
        payload = {"code": "DUP-API", "name": "One"}
        assert api_client.post("/platform/fulfillments", json=payload).status_code == 201
        again = api_client.post("/platform/fulfillments", json=payload)
        assert again.status_code == 409

    def test_api_warehouse_and_user_patch_delete(self, api_client):
        fc_id = api_client.post(
            "/platform/fulfillments",
            json={
                "code": "PATCH-1",
                "name": "P",
                "admin_username": "adm",
                "admin_password": "Password123",
                "primary_warehouse_code": "MAIN",
                "primary_warehouse_name": "Main",
            },
        ).json()["id"]

        second = api_client.post(
            f"/platform/fulfillments/{fc_id}/warehouses",
            json={"code": "FBS", "name": "FBS", "warehouse_kind": "FBS"},
        ).json()
        patched = api_client.patch(
            f"/platform/fulfillments/{fc_id}/warehouses/{second['id']}",
            json={"is_primary": True, "address": "Addr"},
        )
        assert patched.status_code == 200
        assert patched.json()["is_primary"] is True

        # deactivate non-primary after switching primary
        warehouses = api_client.get(f"/platform/fulfillments/{fc_id}/warehouses").json()
        non_primary = next(w for w in warehouses if not w["is_primary"])
        deleted = api_client.delete(f"/platform/fulfillments/{fc_id}/warehouses/{non_primary['id']}")
        assert deleted.status_code == 200
        assert deleted.json()["is_active"] is False

        users = api_client.get(f"/platform/fulfillments/{fc_id}/users").json()
        user_id = users[0]["id"]
        # add second admin then disable first
        second_admin = api_client.post(
            f"/platform/fulfillments/{fc_id}/users",
            json={
                "username": "adm2",
                "display_name": "Admin 2",
                "password": "Password123",
                "role": "FC_ADMIN",
            },
        )
        assert second_admin.status_code == 201
        disabled = api_client.patch(
            f"/platform/fulfillments/{fc_id}/users/{user_id}",
            json={"is_active": False},
        )
        assert disabled.status_code == 200
        assert disabled.json()["is_active"] is False


# ---------------------------------------------------------------------------
# Schema helpers / invariants
# ---------------------------------------------------------------------------


class TestInvariants:
    def test_fulfillment_to_read_shape(self, db):
        fc = svc.create_fulfillment(db, FulfillmentCreate(code="SHAPE", name="S"), actor="a")
        data = svc.fulfillment_to_read(fc)
        assert set(data) >= {
            "id",
            "code",
            "name",
            "status",
            "feature_profile",
            "has_print_agent_token",
            "is_default",
        }
        assert data["has_print_agent_token"] is True

    def test_get_fulfillment_or_404(self, db):
        with pytest.raises(HTTPException) as exc:
            svc.get_fulfillment_or_404(db, uuid.uuid4())
        assert exc.value.status_code == 404

    def test_created_warehouse_persisted_in_masterdata(self, db):
        fc = svc.create_fulfillment(db, FulfillmentCreate(code="MD-1", name="M"), actor="a")
        link = svc.add_warehouse(
            db,
            fc,
            WarehouseLinkCreate(code="CELL-A", name="Cell A"),
            actor="a",
        )
        warehouse = db.get(Warehouse, link["warehouse_id"])
        assert warehouse is not None
        assert warehouse.code == "CELL-A"
        link_row = db.execute(
            select(FulfillmentWarehouse).where(FulfillmentWarehouse.id == link["id"])
        ).scalar_one()
        assert link_row.fulfillment_id == fc.id
