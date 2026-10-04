import base64
import hashlib
import hmac
import json
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.auth import read_session_token
from app.core.config import settings
from app.core.database import get_db

router = APIRouter(prefix="/commercial", tags=["Commercial"])
_SCHEMA_READY = False
COOKIE_NAME = "fc_commercial_session"


def _utcnow():
    return datetime.now(timezone.utc)


def ensure_commercial_schema(db: Session):
    global _SCHEMA_READY
    if _SCHEMA_READY:
        return
    db.execute(text("""
        CREATE TABLE IF NOT EXISTS fc_fulfillment_tenants (
            id UUID PRIMARY KEY,
            name TEXT NOT NULL,
            status VARCHAR(20) NOT NULL DEFAULT 'ACTIVE',
            subscription_until TIMESTAMPTZ NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """))
    db.execute(text("""
        CREATE TABLE IF NOT EXISTS fc_tenant_users (
            id UUID PRIMARY KEY,
            tenant_id UUID NOT NULL REFERENCES fc_fulfillment_tenants(id) ON DELETE CASCADE,
            username VARCHAR(190) NOT NULL UNIQUE,
            password_hash VARCHAR(128) NOT NULL,
            password_salt VARCHAR(64) NOT NULL,
            is_owner BOOLEAN NOT NULL DEFAULT TRUE,
            is_active BOOLEAN NOT NULL DEFAULT TRUE,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """))
    db.execute(text("""
        CREATE TABLE IF NOT EXISTS fc_tenant_clients (
            tenant_id UUID NOT NULL REFERENCES fc_fulfillment_tenants(id) ON DELETE CASCADE,
            client_id UUID NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            PRIMARY KEY (tenant_id, client_id)
        )
    """))
    db.execute(text("""
        CREATE TABLE IF NOT EXISTS fc_tenant_warehouses (
            tenant_id UUID NOT NULL REFERENCES fc_fulfillment_tenants(id) ON DELETE CASCADE,
            warehouse_id UUID NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            PRIMARY KEY (tenant_id, warehouse_id)
        )
    """))
    db.execute(text("CREATE INDEX IF NOT EXISTS ix_fc_tenant_clients_client ON fc_tenant_clients(client_id)"))
    db.execute(text("CREATE INDEX IF NOT EXISTS ix_fc_tenant_wh_wh ON fc_tenant_warehouses(warehouse_id)"))
    db.commit()
    _SCHEMA_READY = True


def _password_hash(password: str, salt_hex: str | None = None) -> tuple[str, str]:
    salt = bytes.fromhex(salt_hex) if salt_hex else secrets.token_bytes(24)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 240_000)
    return digest.hex(), salt.hex()


def _verify_password(password: str, expected: str, salt_hex: str) -> bool:
    actual, _ = _password_hash(password, salt_hex)
    return hmac.compare_digest(actual, expected)


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _unb64(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def make_commercial_token(username: str, tenant_id: str, ttl_hours: int = 24) -> str:
    payload = {
        "u": username,
        "t": tenant_id,
        "exp": int((_utcnow() + timedelta(hours=ttl_hours)).timestamp()),
        "v": 1,
    }
    raw = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    body = _b64(raw)
    sig = hmac.new(str(settings.app_secret_key).encode("utf-8"), body.encode("ascii"), hashlib.sha256).digest()
    return body + "." + _b64(sig)


def read_commercial_token(value: str | None) -> dict[str, Any] | None:
    if not value or "." not in value:
        return None
    body, signature = value.rsplit(".", 1)
    expected = hmac.new(str(settings.app_secret_key).encode("utf-8"), body.encode("ascii"), hashlib.sha256).digest()
    try:
        if not hmac.compare_digest(expected, _unb64(signature)):
            return None
        payload = json.loads(_unb64(body).decode("utf-8"))
        if int(payload.get("exp") or 0) < int(_utcnow().timestamp()):
            return None
        return payload
    except Exception:
        return None


def _is_super_admin(request: Request) -> bool:
    return str(getattr(request.state, "username", "")) == str(settings.admin_username)


def commercial_access_for_tenant(db: Session, tenant_id: str) -> dict[str, Any]:
    ensure_commercial_schema(db)
    row = db.execute(text("""
        SELECT id, name, status, subscription_until
        FROM fc_fulfillment_tenants WHERE id=:id
    """), {"id": tenant_id}).mappings().first()
    if not row:
        raise HTTPException(status_code=401, detail="Кабинет FulfillCore не найден")
    active = str(row["status"] or "").upper() == "ACTIVE"
    until = row["subscription_until"]
    not_expired = until is None or until >= _utcnow()
    clients = [str(x) for x in db.execute(text(
        "SELECT client_id FROM fc_tenant_clients WHERE tenant_id=:id ORDER BY created_at"
    ), {"id": tenant_id}).scalars().all()]
    warehouses = [str(x) for x in db.execute(text(
        "SELECT warehouse_id FROM fc_tenant_warehouses WHERE tenant_id=:id ORDER BY created_at"
    ), {"id": tenant_id}).scalars().all()]
    return {
        "tenant_id": str(row["id"]),
        "tenant_name": row["name"],
        "status": row["status"],
        "subscription_until": until.isoformat() if until else None,
        "subscription_active": bool(active and not_expired),
        "allowed_client_ids": clients,
        "allowed_warehouse_ids": warehouses,
    }


def commercial_session_from_request(request: Request, db: Session) -> dict[str, Any] | None:
    payload = read_commercial_token(request.cookies.get(COOKIE_NAME))
    if not payload:
        return None
    access = commercial_access_for_tenant(db, str(payload.get("t") or ""))
    if not access["subscription_active"]:
        return {**access, "username": payload.get("u"), "blocked": True}
    user = db.execute(text("""
        SELECT is_active FROM fc_tenant_users
        WHERE tenant_id=:tenant_id AND username=:username
    """), {"tenant_id": access["tenant_id"], "username": payload.get("u")}).mappings().first()
    if not user or not bool(user["is_active"]):
        return None
    return {**access, "username": payload.get("u"), "blocked": False}


def _require_admin(request: Request):
    if not _is_super_admin(request):
        raise HTTPException(status_code=403, detail="Доступ только для Super Admin")


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=190)
    password: str = Field(min_length=1, max_length=512)


class CreateTenantRequest(BaseModel):
    name: str = Field(min_length=2, max_length=255)
    username: str = Field(min_length=3, max_length=190)
    password: str = Field(min_length=6, max_length=512)
    subscription_days: int = Field(default=30, ge=1, le=3650)


class StatusRequest(BaseModel):
    active: bool


class ExtendRequest(BaseModel):
    days: int = Field(default=30, ge=1, le=3650)


class PasswordRequest(BaseModel):
    password: str = Field(min_length=6, max_length=512)


class AccessRequest(BaseModel):
    client_ids: list[uuid.UUID] = []
    warehouse_ids: list[uuid.UUID] = []


@router.post("/auth/login")
def commercial_login(payload: LoginRequest, response: Response, db: Session = Depends(get_db)):
    ensure_commercial_schema(db)
    row = db.execute(text("""
        SELECT u.username, u.password_hash, u.password_salt, u.is_active,
               t.id AS tenant_id, t.status, t.subscription_until
        FROM fc_tenant_users u
        JOIN fc_fulfillment_tenants t ON t.id=u.tenant_id
        WHERE lower(u.username)=lower(:username)
    """), {"username": payload.username.strip()}).mappings().first()
    if not row or not bool(row["is_active"]) or not _verify_password(payload.password, row["password_hash"], row["password_salt"]):
        raise HTTPException(status_code=401, detail="Неверный логин или пароль")
    if str(row["status"] or "").upper() != "ACTIVE":
        raise HTTPException(status_code=403, detail="Кабинет отключён владельцем FulfillCore")
    until = row["subscription_until"]
    if until is not None and until < _utcnow():
        raise HTTPException(status_code=403, detail="Подписка закончилась. Обратитесь к владельцу FulfillCore.")
    token = make_commercial_token(row["username"], str(row["tenant_id"]))
    response.set_cookie(
        COOKIE_NAME, token, httponly=True, samesite="lax", secure=True, max_age=86400, path="/"
    )
    return {"ok": True, "username": row["username"], "tenant_id": str(row["tenant_id"])}


@router.post("/auth/logout")
def commercial_logout(response: Response):
    response.delete_cookie(COOKIE_NAME, path="/")
    return {"ok": True}


@router.get("/session")
def commercial_session(request: Request, db: Session = Depends(get_db)):
    ensure_commercial_schema(db)
    if _is_super_admin(request):
        return {
            "username": str(settings.admin_username),
            "super_admin": True,
            "tenant_id": None,
            "tenant_name": "FulfillCore Super Admin",
            "subscription_active": True,
            "allowed_client_ids": [],
            "allowed_warehouse_ids": [],
        }
    session = commercial_session_from_request(request, db)
    if not session:
        raise HTTPException(status_code=401, detail="Требуется вход")
    if session.get("blocked"):
        raise HTTPException(status_code=403, detail="Кабинет отключён или подписка закончилась")
    return {**session, "super_admin": False}


@router.get("/admin/tenants")
def admin_tenants(request: Request, db: Session = Depends(get_db)):
    _require_admin(request)
    ensure_commercial_schema(db)
    rows = db.execute(text("""
        SELECT t.id, t.name, t.status, t.subscription_until, t.created_at,
               u.username, u.is_active,
               (SELECT count(*) FROM fc_tenant_clients c WHERE c.tenant_id=t.id) AS clients_count,
               (SELECT count(*) FROM fc_tenant_warehouses w WHERE w.tenant_id=t.id) AS warehouses_count
        FROM fc_fulfillment_tenants t
        LEFT JOIN fc_tenant_users u ON u.tenant_id=t.id AND u.is_owner=TRUE
        ORDER BY t.created_at DESC
    """)).mappings().all()
    now = _utcnow()
    return [{
        **{k: (str(v) if isinstance(v, uuid.UUID) else v) for k, v in dict(row).items()},
        "subscription_until": row["subscription_until"].isoformat() if row["subscription_until"] else None,
        "created_at": row["created_at"].isoformat() if row["created_at"] else None,
        "subscription_active": (
            str(row["status"] or "").upper() == "ACTIVE"
            and (row["subscription_until"] is None or row["subscription_until"] >= now)
        ),
    } for row in rows]


@router.post("/admin/tenants")
def create_tenant(payload: CreateTenantRequest, request: Request, db: Session = Depends(get_db)):
    _require_admin(request)
    ensure_commercial_schema(db)
    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    digest, salt = _password_hash(payload.password)
    until = _utcnow() + timedelta(days=payload.subscription_days)
    try:
        db.execute(text("""
            INSERT INTO fc_fulfillment_tenants(id,name,status,subscription_until)
            VALUES(:id,:name,'ACTIVE',:until)
        """), {"id": tenant_id, "name": payload.name.strip(), "until": until})
        db.execute(text("""
            INSERT INTO fc_tenant_users(id,tenant_id,username,password_hash,password_salt,is_owner,is_active)
            VALUES(:id,:tenant_id,:username,:hash,:salt,TRUE,TRUE)
        """), {
            "id": user_id, "tenant_id": tenant_id, "username": payload.username.strip(),
            "hash": digest, "salt": salt,
        })
        db.commit()
    except Exception:
        db.rollback()
        raise
    return {"ok": True, "tenant_id": str(tenant_id), "subscription_until": until.isoformat()}


@router.patch("/admin/tenants/{tenant_id}/status")
def tenant_status(tenant_id: uuid.UUID, payload: StatusRequest, request: Request, db: Session = Depends(get_db)):
    _require_admin(request)
    ensure_commercial_schema(db)
    db.execute(text("""
        UPDATE fc_fulfillment_tenants
        SET status=:status, updated_at=NOW()
        WHERE id=:id
    """), {"id": tenant_id, "status": "ACTIVE" if payload.active else "DISABLED"})
    db.commit()
    return {"ok": True}


@router.post("/admin/tenants/{tenant_id}/extend")
def tenant_extend(tenant_id: uuid.UUID, payload: ExtendRequest, request: Request, db: Session = Depends(get_db)):
    _require_admin(request)
    ensure_commercial_schema(db)
    current = db.execute(text(
        "SELECT subscription_until FROM fc_fulfillment_tenants WHERE id=:id"
    ), {"id": tenant_id}).scalar_one_or_none()
    if current is None:
        base = _utcnow()
    else:
        base = max(current, _utcnow())
    until = base + timedelta(days=payload.days)
    db.execute(text("""
        UPDATE fc_fulfillment_tenants
        SET subscription_until=:until, status='ACTIVE', updated_at=NOW()
        WHERE id=:id
    """), {"id": tenant_id, "until": until})
    db.commit()
    return {"ok": True, "subscription_until": until.isoformat()}


@router.post("/admin/tenants/{tenant_id}/password")
def tenant_password(tenant_id: uuid.UUID, payload: PasswordRequest, request: Request, db: Session = Depends(get_db)):
    _require_admin(request)
    ensure_commercial_schema(db)
    digest, salt = _password_hash(payload.password)
    result = db.execute(text("""
        UPDATE fc_tenant_users
        SET password_hash=:hash, password_salt=:salt, updated_at=NOW()
        WHERE tenant_id=:tenant_id AND is_owner=TRUE
    """), {"tenant_id": tenant_id, "hash": digest, "salt": salt})
    if not result.rowcount:
        raise HTTPException(status_code=404, detail="Владелец кабинета не найден")
    db.commit()
    return {"ok": True}


@router.get("/admin/tenants/{tenant_id}/access")
def tenant_access(tenant_id: uuid.UUID, request: Request, db: Session = Depends(get_db)):
    _require_admin(request)
    ensure_commercial_schema(db)
    clients = [str(x) for x in db.execute(text(
        "SELECT client_id FROM fc_tenant_clients WHERE tenant_id=:id"
    ), {"id": tenant_id}).scalars().all()]
    warehouses = [str(x) for x in db.execute(text(
        "SELECT warehouse_id FROM fc_tenant_warehouses WHERE tenant_id=:id"
    ), {"id": tenant_id}).scalars().all()]
    return {"client_ids": clients, "warehouse_ids": warehouses}


@router.put("/admin/tenants/{tenant_id}/access")
def tenant_access_update(tenant_id: uuid.UUID, payload: AccessRequest, request: Request, db: Session = Depends(get_db)):
    _require_admin(request)
    ensure_commercial_schema(db)
    db.execute(text("DELETE FROM fc_tenant_clients WHERE tenant_id=:id"), {"id": tenant_id})
    db.execute(text("DELETE FROM fc_tenant_warehouses WHERE tenant_id=:id"), {"id": tenant_id})
    for value in payload.client_ids:
        db.execute(text("""
            INSERT INTO fc_tenant_clients(tenant_id,client_id) VALUES(:tenant_id,:value)
            ON CONFLICT DO NOTHING
        """), {"tenant_id": tenant_id, "value": value})
    for value in payload.warehouse_ids:
        db.execute(text("""
            INSERT INTO fc_tenant_warehouses(tenant_id,warehouse_id) VALUES(:tenant_id,:value)
            ON CONFLICT DO NOTHING
        """), {"tenant_id": tenant_id, "value": value})
    db.commit()
    return {"ok": True}


def link_resource_to_tenant(db: Session, tenant_id: str, kind: str, resource_id: str):
    ensure_commercial_schema(db)
    if kind == "client":
        db.execute(text("""
            INSERT INTO fc_tenant_clients(tenant_id,client_id) VALUES(:tenant_id,:value)
            ON CONFLICT DO NOTHING
        """), {"tenant_id": tenant_id, "value": resource_id})
    elif kind == "warehouse":
        db.execute(text("""
            INSERT INTO fc_tenant_warehouses(tenant_id,warehouse_id) VALUES(:tenant_id,:value)
            ON CONFLICT DO NOTHING
        """), {"tenant_id": tenant_id, "value": resource_id})
    db.commit()
