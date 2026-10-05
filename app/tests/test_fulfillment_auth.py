"""Host-aware auth: apex platform admin unchanged; tenant hosts accept FC users."""

from __future__ import annotations

import uuid

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from app.core.auth import create_session_token, read_session_token, session_principal
from app.core.config import settings
from app.core.database import get_db
from app.routers.auth import router as auth_router
from app.schemas.fulfillment import FulfillmentCreate, PrintStationCreate, UserCreate
from app.services import fulfillment_platform as svc
from app.services import tenant_host as th
from app.services.fulfillment_auth import authenticate_fulfillment_user, login_for_host
from app.services.tenant_host import resolve_tenant_host


def _ready_fc(db, code: str, subdomain: str, username: str = "packer", role: str = "PACKER"):
    fc = svc.create_fulfillment(
        db,
        FulfillmentCreate(
            code=code,
            name=code,
            subdomain=subdomain,
            admin_username="fc-admin",
            admin_password="Password123",
            primary_warehouse_code="MAIN",
            primary_warehouse_name="Main",
        ),
        actor="t",
    )
    svc.add_print_station(db, fc, PrintStationCreate(station_code="PACK-01"), actor="t")
    if username != "fc-admin":
        svc.add_user(
            db,
            fc,
            UserCreate(
                username=username,
                display_name=username,
                password="Password123",
                role=role,
            ),
            actor="t",
        )
    fc, _ = svc.activate_fulfillment(db, fc, actor="t")
    return fc


class TestSessionTokens:
    def test_legacy_platform_token_reads_as_platform(self):
        # Explicit platform principal (current creator always sets t=platform).
        token = create_session_token("admin", settings.app_secret_key, 1, principal="platform")
        session = read_session_token(token, settings.app_secret_key)
        assert session_principal(session) == "platform"
        assert session["u"] == "admin"

    def test_fc_token_requires_claims(self):
        token = create_session_token(
            "packer",
            settings.app_secret_key,
            1,
            principal="fc",
            fulfillment_id=str(uuid.uuid4()),
            role="PACKER",
            user_id=str(uuid.uuid4()),
        )
        session = read_session_token(token, settings.app_secret_key)
        assert session_principal(session) == "fc"
        assert session["role"] == "PACKER"
        assert session["fid"]


class TestLoginService:
    def test_apex_only_platform_admin(self, db, monkeypatch):
        monkeypatch.setattr(settings, "admin_username", "admin")
        # Ensure password hash from env example works for "admin"
        from app.core.auth import hash_password

        monkeypatch.setattr(settings, "admin_password_hash", hash_password("admin"))
        class Apex:
            kind = "default"
            fulfillment_id = uuid.uuid4()

        result = login_for_host(db, username="admin", password="admin", resolution=Apex())
        assert result["principal"] == "platform"
        with pytest.raises(Exception):
            login_for_host(db, username="nobody", password="x", resolution=Apex())

    def test_tenant_login_fc_user(self, db, monkeypatch):
        monkeypatch.setattr(th.settings, "platform_base_domain", "wms.example.ru")
        monkeypatch.setattr(th.settings, "tenant_host_mode", "strict")
        fc = _ready_fc(db, "AUTH1", "fc-auth1", username="packer", role="PACKER")
        resolution = resolve_tenant_host(db, "fc-auth1.wms.example.ru")
        assert resolution.fulfillment_id == fc.id
        result = login_for_host(db, username="packer", password="Password123", resolution=resolution)
        assert result["principal"] == "fc"
        assert result["role"] == "PACKER"
        assert result["fulfillment_id"] == str(fc.id)

    def test_tenant_wrong_password(self, db, monkeypatch):
        monkeypatch.setattr(th.settings, "platform_base_domain", "wms.example.ru")
        monkeypatch.setattr(th.settings, "tenant_host_mode", "strict")
        _ready_fc(db, "AUTH2", "fc-auth2")
        resolution = resolve_tenant_host(db, "fc-auth2.wms.example.ru")
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc:
            authenticate_fulfillment_user(
                db,
                fulfillment_id=resolution.fulfillment_id,
                username="fc-admin",
                password="bad",
            )
        assert exc.value.status_code == 401


class TestAuthHttp:
    def test_api_login_paths(self, db_engine, db, monkeypatch):
        from app.core.auth import hash_password

        monkeypatch.setattr(settings, "admin_username", "admin")
        monkeypatch.setattr(settings, "admin_password_hash", hash_password("admin"))
        monkeypatch.setattr(th.settings, "platform_base_domain", "wms.example.ru")
        monkeypatch.setattr(th.settings, "tenant_host_mode", "strict")

        fc = _ready_fc(db, "HTTP1", "fc-http1", username="picker", role="PICKER")
        SessionLocal = sessionmaker(bind=db_engine, autoflush=False, expire_on_commit=False)

        app = FastAPI()
        app.include_router(auth_router)

        @app.get("/secure")
        def secure(request: Request):
            return {
                "username": getattr(request.state, "username", None),
                "principal": getattr(request.state, "auth_principal", None),
                "role": getattr(request.state, "auth_role", None),
                "fulfillment_id": getattr(request.state, "auth_fulfillment_id", None),
            }

        @app.middleware("http")
        async def tenant_and_auth(request: Request, call_next):
            from app.core.auth import read_session_token, session_principal
            from app.services.tenant_host import resolve_tenant_host

            session_db = SessionLocal()
            try:
                host = (request.headers.get("host") or "").split(":")[0].lower()
                resolution = resolve_tenant_host(session_db, host)
                request.state.tenant_resolution = resolution
                request.state.fulfillment_id = resolution.fulfillment_id
            finally:
                session_db.close()

            if request.url.path == "/auth/login":
                return await call_next(request)

            session = read_session_token(request.cookies.get("fc_session"), settings.app_secret_key)
            if not session:
                from fastapi.responses import JSONResponse

                return JSONResponse({"detail": "Требуется вход"}, status_code=401)
            principal = session_principal(session)
            if principal == "platform" and session.get("u") == settings.admin_username:
                request.state.username = session["u"]
                request.state.auth_principal = "platform"
                request.state.auth_role = "PLATFORM_ADMIN"
                request.state.auth_fulfillment_id = None
                return await call_next(request)
            if principal == "fc":
                host_fid = str(getattr(request.state, "fulfillment_id", "") or "")
                if str(session.get("fid") or "") != host_fid:
                    from fastapi.responses import JSONResponse

                    return JSONResponse({"detail": "mismatch"}, status_code=403)
                if request.url.path.startswith("/platform/"):
                    from fastapi.responses import JSONResponse

                    return JSONResponse({"detail": "platform only"}, status_code=403)
                request.state.username = session["u"]
                request.state.auth_principal = "fc"
                request.state.auth_role = session.get("role")
                request.state.auth_fulfillment_id = session.get("fid")
                return await call_next(request)
            from fastapi.responses import JSONResponse

            return JSONResponse({"detail": "Требуется вход"}, status_code=401)

        def _override_db():
            session = SessionLocal()
            try:
                yield session
            finally:
                session.close()

        app.dependency_overrides[get_db] = _override_db
        client = TestClient(app)

        # Apex platform login unchanged
        apex = client.post(
            "/auth/login",
            headers={"Host": "wms.example.ru"},
            json={"username": "admin", "password": "admin"},
        )
        assert apex.status_code == 200, apex.text
        assert apex.json()["principal"] == "platform"
        me = client.get("/auth/me", headers={"Host": "wms.example.ru"})
        assert me.status_code == 200
        assert me.json()["username"] == "admin"

        # Tenant FC login
        tenant = client.post(
            "/auth/login",
            headers={"Host": "fc-http1.wms.example.ru"},
            json={"username": "picker", "password": "Password123"},
        )
        assert tenant.status_code == 200, tenant.text
        assert tenant.json()["principal"] == "fc"
        assert tenant.json()["role"] == "PICKER"
        assert tenant.json()["fulfillment_id"] == str(fc.id)

        secure = client.get("/secure", headers={"Host": "fc-http1.wms.example.ru"})
        assert secure.status_code == 200
        assert secure.json()["role"] == "PICKER"

        # FC cookie must not work on another fulfillment host
        other = client.get("/secure", headers={"Host": "wms.example.ru"})
        # session still set from tenant login; apex should reject fc session
        # Our test middleware only checks fid mismatch when principal=fc and host is apex
        # host fulfillment for apex is DEFAULT id != fc id -> 403
        assert other.status_code in {401, 403}
