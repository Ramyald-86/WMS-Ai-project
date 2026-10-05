"""Authenticate platform admin (apex) and fulfillment users (tenant hosts).

Apex / DEFAULT host keeps the existing single ADMIN_USERNAME login.
Tenant subdomain / custom_domain accepts FulfillmentUser of that FC, and may
also accept the platform admin for support access.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.auth import create_session_token, verify_password
from app.core.config import settings
from app.models.fulfillment import FulfillmentUser
from app.services.tenant_scope import SCOPE_KINDS

PLATFORM_ROLE = "PLATFORM_ADMIN"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def is_tenant_auth_host(resolution) -> bool:
    return bool(resolution and resolution.kind in SCOPE_KINDS and resolution.fulfillment_id)


def authenticate_platform_admin(username: str, password: str) -> dict[str, Any]:
    if not settings.admin_password_hash:
        raise HTTPException(status_code=503, detail="Пароль администратора не настроен")
    ok_user = username.strip() == settings.admin_username
    ok_pass = verify_password(password, settings.admin_password_hash)
    if not (ok_user and ok_pass):
        raise HTTPException(status_code=401, detail="Неверный логин или пароль")
    token = create_session_token(
        settings.admin_username,
        settings.app_secret_key,
        settings.session_ttl_hours,
        principal="platform",
        role=PLATFORM_ROLE,
    )
    return {
        "ok": True,
        "principal": "platform",
        "username": settings.admin_username,
        "role": PLATFORM_ROLE,
        "fulfillment_id": None,
        "user_id": None,
        "must_change_password": False,
        "token": token,
    }


def authenticate_fulfillment_user(
    db: Session,
    *,
    fulfillment_id: uuid.UUID,
    username: str,
    password: str,
) -> dict[str, Any]:
    login = username.strip().lower()
    user = db.execute(
        select(FulfillmentUser).where(
            FulfillmentUser.fulfillment_id == fulfillment_id,
            FulfillmentUser.username == login,
            FulfillmentUser.is_active.is_(True),
        )
    ).scalars().first()
    if not user or not verify_password(password, user.password_hash):
        raise HTTPException(status_code=401, detail="Неверный логин или пароль")

    user.last_login_at = _now()
    db.commit()
    db.refresh(user)

    token = create_session_token(
        user.username,
        settings.app_secret_key,
        settings.session_ttl_hours,
        principal="fc",
        fulfillment_id=str(user.fulfillment_id),
        role=user.role,
        user_id=str(user.id),
    )
    return {
        "ok": True,
        "principal": "fc",
        "username": user.username,
        "role": user.role,
        "fulfillment_id": str(user.fulfillment_id),
        "user_id": str(user.id),
        "display_name": user.display_name,
        "must_change_password": bool(user.must_change_password),
        "token": token,
    }


def login_for_host(
    db: Session,
    *,
    username: str,
    password: str,
    resolution,
) -> dict[str, Any]:
    """Host-aware login entrypoint.

    - Apex / platform / DEFAULT: only platform admin (legacy behaviour).
    - Tenant host: FC user of that fulfillment; platform admin also accepted.
    """
    if is_tenant_auth_host(resolution):
        # Prefer FC user when credentials match a local account; otherwise fall
        # back to platform admin so support access still works on subdomains.
        login = username.strip().lower()
        candidate = db.execute(
            select(FulfillmentUser.id).where(
                FulfillmentUser.fulfillment_id == resolution.fulfillment_id,
                FulfillmentUser.username == login,
                FulfillmentUser.is_active.is_(True),
            )
        ).scalars().first()
        if candidate:
            return authenticate_fulfillment_user(
                db,
                fulfillment_id=resolution.fulfillment_id,
                username=username,
                password=password,
            )
        return authenticate_platform_admin(username, password)

    # Apex and non-tenant hosts keep the original single-admin gate.
    return authenticate_platform_admin(username, password)


def session_public_view(session: dict[str, Any]) -> dict[str, Any]:
    from app.core.auth import session_principal

    principal = session_principal(session)
    return {
        "username": session.get("u"),
        "principal": principal,
        "role": session.get("role") or (PLATFORM_ROLE if principal == "platform" else None),
        "fulfillment_id": session.get("fid"),
        "user_id": session.get("uid"),
    }
