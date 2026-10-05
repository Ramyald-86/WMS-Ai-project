"""Platform service for connecting and managing fulfillments (tenants).

Design constraints:
- Additive only: does not rewrite existing warehouse / packing / receiving paths.
- Existing single-instance data is linked into a seeded default fulfillment.
- Per-FC print tokens are stored for future agent scoping; global PRINT_AGENT_TOKEN
  continues to authorize current Print Agents.
"""

from __future__ import annotations

import copy
import secrets
import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import HTTPException
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.auth import hash_password
from app.core.crypto import decrypt_secret, encrypt_secret
from app.models import Client, MarketplaceCredential, Warehouse
from app.models.fulfillment import (
    FULFILLMENT_STATUSES,
    USER_ROLES,
    WAREHOUSE_KINDS,
    Fulfillment,
    FulfillmentClient,
    FulfillmentEvent,
    FulfillmentPrintStation,
    FulfillmentUser,
    FulfillmentWarehouse,
)
from app.schemas.fulfillment import (
    ClientLinkCreate,
    ClientLinkUpdate,
    FeatureProfilePatch,
    FulfillmentCreate,
    FulfillmentUpdate,
    MarketplaceCredentialAttach,
    PrintStationCreate,
    PrintStationUpdate,
    ReadinessCheck,
    ReadinessReport,
    UserCreate,
    UserUpdate,
    WarehouseLinkCreate,
    WarehouseLinkUpdate,
)
from app.services.runtime_reliability import normalize_station_code, station_token

DEFAULT_FULFILLMENT_CODE = "DEFAULT"
DEFAULT_TEMPLATE_VERSION = "0.9.4.15"

DEFAULT_FEATURE_PROFILE: dict[str, Any] = {
    "template": "full_fulfillcore",
    "template_version": DEFAULT_TEMPLATE_VERSION,
    "marketplaces": ["WB", "OZON"],
    "modules": {
        "receiving": True,
        "tsd": True,
        "inventory": True,
        "fbs_reserve_publish": True,
        "wb_packing_chz": True,
        "picking_lists": True,
        "pvz": True,
        "defect": True,
        "transit": True,
        "fbo": True,
        "print_agent": True,
        "client_portal": True,
        "finance": True,
        "excel_exports": True,
    },
    "policies": {
        "chz_required_on_pack": True,
        "reserve_on_picking_list": True,
        "writeoff_on_pack": True,
    },
    "limits": {
        "max_warehouses": 20,
        "max_print_stations": 50,
        "max_clients": 500,
        "max_users": 200,
    },
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _merge_feature_profile(
    base: dict[str, Any] | None,
    patch: FeatureProfilePatch | dict[str, Any] | None,
) -> dict[str, Any]:
    profile = copy.deepcopy(DEFAULT_FEATURE_PROFILE)
    if isinstance(base, dict):
        for key in ("template", "template_version", "marketplaces"):
            if key in base and base[key] is not None:
                profile[key] = copy.deepcopy(base[key])
        for section in ("modules", "policies", "limits"):
            if isinstance(base.get(section), dict):
                profile[section].update(base[section])

    if patch is None:
        return profile

    data = patch.model_dump(exclude_unset=True) if isinstance(patch, FeatureProfilePatch) else dict(patch)
    for key in ("template", "template_version", "marketplaces"):
        if key in data and data[key] is not None:
            profile[key] = copy.deepcopy(data[key])
    for section in ("modules", "policies", "limits"):
        if isinstance(data.get(section), dict):
            profile[section].update(data[section])
    return profile


def install_schema(db: Session) -> dict[str, Any]:
    """Idempotent DDL + seed for the current single-instance deployment."""
    statements = [
        """
        CREATE TABLE IF NOT EXISTS fulfillments (
            id UUID PRIMARY KEY,
            code VARCHAR(64) NOT NULL UNIQUE,
            name VARCHAR(255) NOT NULL,
            legal_name VARCHAR(255) NULL,
            timezone VARCHAR(64) NOT NULL DEFAULT 'Europe/Moscow',
            locale VARCHAR(16) NOT NULL DEFAULT 'ru',
            subdomain VARCHAR(128) NULL UNIQUE,
            custom_domain VARCHAR(255) NULL UNIQUE,
            status VARCHAR(32) NOT NULL DEFAULT 'DRAFT',
            is_default BOOLEAN NOT NULL DEFAULT FALSE,
            feature_profile JSONB NOT NULL DEFAULT '{}'::jsonb,
            encrypted_print_agent_token TEXT NULL,
            notes TEXT NULL,
            activated_at TIMESTAMPTZ NULL,
            suspended_at TIMESTAMPTZ NULL,
            archived_at TIMESTAMPTZ NULL,
            created_by VARCHAR(120) NULL,
            updated_by VARCHAR(120) NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """,
        "CREATE INDEX IF NOT EXISTS ix_fulfillments_code ON fulfillments(code)",
        "CREATE INDEX IF NOT EXISTS ix_fulfillments_status ON fulfillments(status)",
        "CREATE INDEX IF NOT EXISTS ix_fulfillments_is_default ON fulfillments(is_default)",
        """
        CREATE TABLE IF NOT EXISTS fulfillment_warehouses (
            id UUID PRIMARY KEY,
            fulfillment_id UUID NOT NULL REFERENCES fulfillments(id) ON DELETE CASCADE,
            warehouse_id UUID NOT NULL REFERENCES warehouses(id) ON DELETE RESTRICT,
            warehouse_kind VARCHAR(32) NOT NULL DEFAULT 'MAIN',
            is_primary BOOLEAN NOT NULL DEFAULT FALSE,
            address VARCHAR(500) NULL,
            contact_phone VARCHAR(64) NULL,
            contact_email VARCHAR(255) NULL,
            is_active BOOLEAN NOT NULL DEFAULT TRUE,
            created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            CONSTRAINT uq_fulfillment_warehouse UNIQUE (fulfillment_id, warehouse_id),
            CONSTRAINT uq_fulfillment_warehouse_exclusive UNIQUE (warehouse_id)
        )
        """,
        "CREATE INDEX IF NOT EXISTS ix_fulfillment_warehouses_fulfillment ON fulfillment_warehouses(fulfillment_id)",
        "CREATE INDEX IF NOT EXISTS ix_fulfillment_warehouses_warehouse ON fulfillment_warehouses(warehouse_id)",
        """
        CREATE TABLE IF NOT EXISTS fulfillment_clients (
            id UUID PRIMARY KEY,
            fulfillment_id UUID NOT NULL REFERENCES fulfillments(id) ON DELETE CASCADE,
            client_id UUID NOT NULL REFERENCES clients(id) ON DELETE RESTRICT,
            is_pilot BOOLEAN NOT NULL DEFAULT FALSE,
            is_active BOOLEAN NOT NULL DEFAULT TRUE,
            created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            CONSTRAINT uq_fulfillment_client UNIQUE (fulfillment_id, client_id),
            CONSTRAINT uq_fulfillment_client_exclusive UNIQUE (client_id)
        )
        """,
        "CREATE INDEX IF NOT EXISTS ix_fulfillment_clients_fulfillment ON fulfillment_clients(fulfillment_id)",
        "CREATE INDEX IF NOT EXISTS ix_fulfillment_clients_client ON fulfillment_clients(client_id)",
        # Hot-deploy safety: tables may already exist from an earlier draft without exclusive uniques.
        """
        DO $$ BEGIN
            ALTER TABLE fulfillment_warehouses
                ADD CONSTRAINT uq_fulfillment_warehouse_exclusive UNIQUE (warehouse_id);
        EXCEPTION WHEN duplicate_object THEN NULL;
        END $$
        """,
        """
        DO $$ BEGIN
            ALTER TABLE fulfillment_clients
                ADD CONSTRAINT uq_fulfillment_client_exclusive UNIQUE (client_id);
        EXCEPTION WHEN duplicate_object THEN NULL;
        END $$
        """,
        """
        CREATE TABLE IF NOT EXISTS fulfillment_users (
            id UUID PRIMARY KEY,
            fulfillment_id UUID NOT NULL REFERENCES fulfillments(id) ON DELETE CASCADE,
            username VARCHAR(100) NOT NULL,
            display_name VARCHAR(255) NOT NULL,
            email VARCHAR(255) NULL,
            role VARCHAR(32) NOT NULL DEFAULT 'FC_ADMIN',
            password_hash TEXT NOT NULL,
            is_active BOOLEAN NOT NULL DEFAULT TRUE,
            must_change_password BOOLEAN NOT NULL DEFAULT TRUE,
            last_login_at TIMESTAMPTZ NULL,
            created_by VARCHAR(120) NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            CONSTRAINT uq_fulfillment_user_username UNIQUE (fulfillment_id, username)
        )
        """,
        "CREATE INDEX IF NOT EXISTS ix_fulfillment_users_fulfillment ON fulfillment_users(fulfillment_id)",
        """
        CREATE TABLE IF NOT EXISTS fulfillment_print_stations (
            id UUID PRIMARY KEY,
            fulfillment_id UUID NOT NULL REFERENCES fulfillments(id) ON DELETE CASCADE,
            station_code VARCHAR(64) NOT NULL,
            name VARCHAR(255) NULL,
            printer_name VARCHAR(255) NULL,
            label_format VARCHAR(32) NOT NULL DEFAULT '58x40',
            is_active BOOLEAN NOT NULL DEFAULT TRUE,
            last_seen_at TIMESTAMPTZ NULL,
            notes TEXT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            CONSTRAINT uq_fulfillment_print_station UNIQUE (fulfillment_id, station_code)
        )
        """,
        "CREATE INDEX IF NOT EXISTS ix_fulfillment_print_stations_fulfillment ON fulfillment_print_stations(fulfillment_id)",
        """
        CREATE TABLE IF NOT EXISTS fulfillment_events (
            id UUID PRIMARY KEY,
            fulfillment_id UUID NOT NULL REFERENCES fulfillments(id) ON DELETE CASCADE,
            event_type VARCHAR(64) NOT NULL,
            actor VARCHAR(120) NULL,
            message VARCHAR(500) NULL,
            data JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """,
        "CREATE INDEX IF NOT EXISTS ix_fulfillment_events_fulfillment_created ON fulfillment_events(fulfillment_id, created_at)",
    ]
    for sql in statements:
        db.execute(text(sql))
    db.flush()
    seeded = ensure_default_fulfillment(db)
    db.commit()
    return {"ok": True, "default_fulfillment_id": str(seeded.id) if seeded else None}


def ensure_default_fulfillment(db: Session) -> Fulfillment:
    """Link legacy warehouses/clients into one default ACTIVE fulfillment."""
    fc = db.execute(
        select(Fulfillment).where(Fulfillment.is_default.is_(True)).order_by(Fulfillment.created_at.asc())
    ).scalars().first()
    if not fc:
        fc = db.execute(
            select(Fulfillment).where(Fulfillment.code == DEFAULT_FULFILLMENT_CODE)
        ).scalars().first()
    if not fc:
        token = secrets.token_urlsafe(32)
        fc = Fulfillment(
            id=uuid.uuid4(),
            code=DEFAULT_FULFILLMENT_CODE,
            name="Текущий фулфилмент",
            timezone="Europe/Moscow",
            locale="ru",
            status="ACTIVE",
            is_default=True,
            feature_profile=copy.deepcopy(DEFAULT_FEATURE_PROFILE),
            encrypted_print_agent_token=encrypt_secret(token),
            activated_at=_now(),
            created_by="system",
            updated_by="system",
        )
        db.add(fc)
        db.flush()
        _add_event(db, fc.id, "SEEDED_DEFAULT", "system", "Создан default-фулфилмент для текущего инстанса")
    else:
        if not fc.is_default:
            fc.is_default = True
        if fc.status not in FULFILLMENT_STATUSES:
            fc.status = "ACTIVE"
        if not fc.feature_profile:
            fc.feature_profile = copy.deepcopy(DEFAULT_FEATURE_PROFILE)
        if not fc.encrypted_print_agent_token:
            fc.encrypted_print_agent_token = encrypt_secret(secrets.token_urlsafe(32))

    # Only claim orphan masterdata. Warehouses/clients already owned by another
    # fulfillment must stay exclusive (uq_fulfillment_*_exclusive).
    owned_warehouses = {
        row.warehouse_id for row in db.execute(select(FulfillmentWarehouse)).scalars().all()
    }
    owned_clients = {
        row.client_id for row in db.execute(select(FulfillmentClient)).scalars().all()
    }
    has_primary = bool(
        db.execute(
            select(FulfillmentWarehouse.id).where(
                FulfillmentWarehouse.fulfillment_id == fc.id,
                FulfillmentWarehouse.is_primary.is_(True),
            )
        ).scalars().first()
    )
    warehouses = db.execute(select(Warehouse).order_by(Warehouse.created_at.asc())).scalars().all()
    for warehouse in warehouses:
        if warehouse.id in owned_warehouses:
            continue
        link = FulfillmentWarehouse(
            id=uuid.uuid4(),
            fulfillment_id=fc.id,
            warehouse_id=warehouse.id,
            warehouse_kind="MAIN",
            is_primary=(not has_primary),
            is_active=bool(warehouse.is_active),
        )
        if link.is_primary:
            has_primary = True
        db.add(link)
        owned_warehouses.add(warehouse.id)

    clients = db.execute(select(Client).order_by(Client.created_at.asc())).scalars().all()
    for client in clients:
        if client.id in owned_clients:
            continue
        db.add(
            FulfillmentClient(
                id=uuid.uuid4(),
                fulfillment_id=fc.id,
                client_id=client.id,
                is_pilot=False,
                is_active=bool(client.is_active),
            )
        )
        owned_clients.add(client.id)
    db.flush()
    return fc


def _add_event(
    db: Session,
    fulfillment_id: uuid.UUID,
    event_type: str,
    actor: str | None,
    message: str | None = None,
    data: dict[str, Any] | None = None,
) -> FulfillmentEvent:
    event = FulfillmentEvent(
        id=uuid.uuid4(),
        fulfillment_id=fulfillment_id,
        event_type=event_type,
        actor=actor,
        message=(message or "")[:500] or None,
        data=data or {},
        created_at=_now(),
    )
    db.add(event)
    return event


def get_fulfillment_or_404(db: Session, fulfillment_id: uuid.UUID) -> Fulfillment:
    fc = db.get(Fulfillment, fulfillment_id)
    if not fc:
        raise HTTPException(status_code=404, detail="Фулфилмент не найден")
    return fc


def fulfillment_to_read(fc: Fulfillment) -> dict[str, Any]:
    return {
        "id": fc.id,
        "code": fc.code,
        "name": fc.name,
        "legal_name": fc.legal_name,
        "timezone": fc.timezone,
        "locale": fc.locale,
        "subdomain": fc.subdomain,
        "custom_domain": fc.custom_domain,
        "status": fc.status,
        "is_default": fc.is_default,
        "feature_profile": fc.feature_profile or copy.deepcopy(DEFAULT_FEATURE_PROFILE),
        "notes": fc.notes,
        "activated_at": fc.activated_at,
        "suspended_at": fc.suspended_at,
        "archived_at": fc.archived_at,
        "created_by": fc.created_by,
        "updated_by": fc.updated_by,
        "created_at": fc.created_at,
        "updated_at": fc.updated_at,
        "has_print_agent_token": bool(fc.encrypted_print_agent_token),
    }


def list_fulfillment_summaries(db: Session) -> list[dict[str, Any]]:
    rows = db.execute(select(Fulfillment).order_by(Fulfillment.created_at.desc())).scalars().all()
    result: list[dict[str, Any]] = []
    for fc in rows:
        warehouses_count = db.execute(
            select(func.count()).select_from(FulfillmentWarehouse).where(
                FulfillmentWarehouse.fulfillment_id == fc.id,
                FulfillmentWarehouse.is_active.is_(True),
            )
        ).scalar_one()
        clients_count = db.execute(
            select(func.count()).select_from(FulfillmentClient).where(
                FulfillmentClient.fulfillment_id == fc.id,
                FulfillmentClient.is_active.is_(True),
            )
        ).scalar_one()
        users_count = db.execute(
            select(func.count()).select_from(FulfillmentUser).where(
                FulfillmentUser.fulfillment_id == fc.id,
                FulfillmentUser.is_active.is_(True),
            )
        ).scalar_one()
        stations = db.execute(
            select(FulfillmentPrintStation).where(FulfillmentPrintStation.fulfillment_id == fc.id)
        ).scalars().all()
        result.append(
            {
                "id": fc.id,
                "code": fc.code,
                "name": fc.name,
                "status": fc.status,
                "is_default": fc.is_default,
                "subdomain": fc.subdomain,
                "timezone": fc.timezone,
                "warehouses_count": int(warehouses_count or 0),
                "clients_count": int(clients_count or 0),
                "users_count": int(users_count or 0),
                "print_stations_count": len(stations),
                "print_stations_active_count": sum(1 for s in stations if s.is_active),
                "created_at": fc.created_at,
                "updated_at": fc.updated_at,
                "activated_at": fc.activated_at,
            }
        )
    return result


def create_fulfillment(db: Session, payload: FulfillmentCreate, actor: str | None) -> Fulfillment:
    existing = db.execute(select(Fulfillment).where(Fulfillment.code == payload.code)).scalars().first()
    if existing:
        raise HTTPException(status_code=409, detail=f"Фулфилмент с кодом {payload.code} уже существует")

    if payload.subdomain:
        from app.services.tenant_host import assert_subdomain_available

        assert_subdomain_available(db, payload.subdomain)
    if payload.custom_domain:
        clash = db.execute(
            select(Fulfillment).where(Fulfillment.custom_domain == payload.custom_domain)
        ).scalars().first()
        if clash:
            raise HTTPException(status_code=409, detail="Такой custom_domain уже занят")

    token = secrets.token_urlsafe(32)
    fc = Fulfillment(
        id=uuid.uuid4(),
        code=payload.code,
        name=payload.name.strip(),
        legal_name=(payload.legal_name or "").strip() or None,
        timezone=payload.timezone.strip() or "Europe/Moscow",
        locale=payload.locale.strip() or "ru",
        subdomain=payload.subdomain,
        custom_domain=payload.custom_domain,
        status="DRAFT",
        is_default=False,
        feature_profile=_merge_feature_profile(None, payload.feature_profile),
        encrypted_print_agent_token=encrypt_secret(token),
        notes=payload.notes,
        created_by=actor,
        updated_by=actor,
    )
    db.add(fc)
    db.flush()
    _add_event(db, fc.id, "CREATED", actor, "Черновик фулфилмента создан", {"code": fc.code})

    if payload.admin_username and payload.admin_password:
        add_user(
            db,
            fc,
            UserCreate(
                username=payload.admin_username,
                display_name=payload.admin_display_name or payload.admin_username,
                email=payload.admin_email,
                password=payload.admin_password,
                role="FC_ADMIN",
                must_change_password=True,
            ),
            actor,
            commit=False,
        )

    if payload.primary_warehouse_code:
        add_warehouse(
            db,
            fc,
            WarehouseLinkCreate(
                code=payload.primary_warehouse_code,
                name=payload.primary_warehouse_name or payload.primary_warehouse_code,
                warehouse_kind=payload.primary_warehouse_kind,
                is_primary=True,
            ),
            actor,
            commit=False,
        )

    # Wizard progress state.
    if payload.admin_username or payload.primary_warehouse_code:
        fc.status = "ONBOARDING"
        _add_event(db, fc.id, "ONBOARDING_STARTED", actor, "Онбординг начат")

    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="Конфликт уникальности при создании фулфилмента") from exc
    db.refresh(fc)
    return fc


def update_fulfillment(
    db: Session,
    fc: Fulfillment,
    payload: FulfillmentUpdate,
    actor: str | None,
) -> Fulfillment:
    if fc.status == "ARCHIVED":
        raise HTTPException(status_code=409, detail="Архивный фулфилмент нельзя изменять")

    data = payload.model_dump(exclude_unset=True)
    if "feature_profile" in data:
        patch = payload.feature_profile
        fc.feature_profile = _merge_feature_profile(fc.feature_profile, patch)
        data.pop("feature_profile", None)

    if "subdomain" in data and data["subdomain"]:
        from app.services.tenant_host import assert_subdomain_available

        assert_subdomain_available(db, data["subdomain"], exclude_id=fc.id)
    if "custom_domain" in data and data["custom_domain"]:
        clash = db.execute(
            select(Fulfillment).where(
                Fulfillment.custom_domain == data["custom_domain"],
                Fulfillment.id != fc.id,
            )
        ).scalars().first()
        if clash:
            raise HTTPException(status_code=409, detail="Такой custom_domain уже занят")

    for key, value in data.items():
        if key in {"name", "legal_name", "timezone", "locale", "notes"} and isinstance(value, str):
            value = value.strip() or None if key in {"legal_name", "notes"} else value.strip()
        setattr(fc, key, value)

    fc.updated_by = actor
    if fc.status == "DRAFT":
        fc.status = "ONBOARDING"
    _add_event(db, fc.id, "UPDATED", actor, "Параметры фулфилмента обновлены")
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="Конфликт уникальности при обновлении") from exc
    db.refresh(fc)
    return fc


def _limits(fc: Fulfillment) -> dict[str, int]:
    profile = fc.feature_profile or DEFAULT_FEATURE_PROFILE
    limits = profile.get("limits") if isinstance(profile, dict) else {}
    if not isinstance(limits, dict):
        limits = {}
    return {
        "max_warehouses": int(limits.get("max_warehouses") or DEFAULT_FEATURE_PROFILE["limits"]["max_warehouses"]),
        "max_print_stations": int(
            limits.get("max_print_stations") or DEFAULT_FEATURE_PROFILE["limits"]["max_print_stations"]
        ),
        "max_clients": int(limits.get("max_clients") or DEFAULT_FEATURE_PROFILE["limits"]["max_clients"]),
        "max_users": int(limits.get("max_users") or DEFAULT_FEATURE_PROFILE["limits"]["max_users"]),
    }


def _count_active_links(db: Session, model, fulfillment_id: uuid.UUID) -> int:
    return int(
        db.execute(
            select(func.count()).select_from(model).where(
                model.fulfillment_id == fulfillment_id,
                model.is_active.is_(True),
            )
        ).scalar_one()
        or 0
    )


def add_warehouse(
    db: Session,
    fc: Fulfillment,
    payload: WarehouseLinkCreate,
    actor: str | None,
    *,
    commit: bool = True,
) -> dict[str, Any]:
    if fc.status == "ARCHIVED":
        raise HTTPException(status_code=409, detail="Архивный фулфилмент нельзя изменять")
    if payload.warehouse_kind not in WAREHOUSE_KINDS:
        raise HTTPException(status_code=400, detail="Недопустимый тип склада")

    limits = _limits(fc)
    if _count_active_links(db, FulfillmentWarehouse, fc.id) >= limits["max_warehouses"]:
        raise HTTPException(status_code=409, detail=f"Достигнут лимит складов ({limits['max_warehouses']})")

    if payload.existing_warehouse_id:
        warehouse = db.get(Warehouse, payload.existing_warehouse_id)
        if not warehouse:
            raise HTTPException(status_code=404, detail="Склад не найден")
        already = db.execute(
            select(FulfillmentWarehouse).where(
                FulfillmentWarehouse.fulfillment_id == fc.id,
                FulfillmentWarehouse.warehouse_id == warehouse.id,
            )
        ).scalars().first()
        if already:
            raise HTTPException(status_code=409, detail="Склад уже привязан к этому фулфилменту")
        # Prevent one warehouse belonging to two fulfillments.
        other = db.execute(
            select(FulfillmentWarehouse).where(FulfillmentWarehouse.warehouse_id == warehouse.id)
        ).scalars().first()
        if other and other.fulfillment_id != fc.id:
            raise HTTPException(status_code=409, detail="Склад уже принадлежит другому фулфилменту")
    else:
        # Keep warehouse codes globally unique (current DB constraint). Prefix with FC code
        # when a bare code would collide, so onboarding stays frictionless.
        code = payload.code
        clash = db.execute(select(Warehouse).where(Warehouse.code == code)).scalars().first()
        if clash:
            prefixed = f"{fc.code}-{payload.code}"[:64]
            clash2 = db.execute(select(Warehouse).where(Warehouse.code == prefixed)).scalars().first()
            if clash2:
                raise HTTPException(
                    status_code=409,
                    detail=f"Код склада занят. Попробуйте другой код (например {prefixed})",
                )
            code = prefixed
        warehouse = Warehouse(id=uuid.uuid4(), code=code, name=payload.name.strip(), is_active=True)
        db.add(warehouse)
        db.flush()

    if payload.is_primary:
        for row in db.execute(
            select(FulfillmentWarehouse).where(FulfillmentWarehouse.fulfillment_id == fc.id)
        ).scalars().all():
            row.is_primary = False

    link = FulfillmentWarehouse(
        id=uuid.uuid4(),
        fulfillment_id=fc.id,
        warehouse_id=warehouse.id,
        warehouse_kind=payload.warehouse_kind,
        is_primary=payload.is_primary or _count_active_links(db, FulfillmentWarehouse, fc.id) == 0,
        address=payload.address,
        contact_phone=payload.contact_phone,
        contact_email=payload.contact_email,
        is_active=True,
    )
    db.add(link)
    if fc.status == "DRAFT":
        fc.status = "ONBOARDING"
    fc.updated_by = actor
    _add_event(
        db,
        fc.id,
        "WAREHOUSE_LINKED",
        actor,
        f"Склад {warehouse.code} привязан",
        {"warehouse_id": str(warehouse.id), "warehouse_kind": link.warehouse_kind},
    )
    if commit:
        try:
            db.commit()
        except IntegrityError as exc:
            db.rollback()
            raise HTTPException(status_code=409, detail="Не удалось привязать склад") from exc
        db.refresh(link)
        db.refresh(warehouse)
    return _warehouse_link_read(db, link)


def _warehouse_link_read(db: Session, link: FulfillmentWarehouse) -> dict[str, Any]:
    warehouse = db.get(Warehouse, link.warehouse_id)
    if not warehouse:
        raise HTTPException(status_code=404, detail="Склад не найден")
    return {
        "id": link.id,
        "fulfillment_id": link.fulfillment_id,
        "warehouse_id": warehouse.id,
        "warehouse_code": warehouse.code,
        "warehouse_name": warehouse.name,
        "warehouse_is_active": warehouse.is_active,
        "warehouse_kind": link.warehouse_kind,
        "is_primary": link.is_primary,
        "address": link.address,
        "contact_phone": link.contact_phone,
        "contact_email": link.contact_email,
        "is_active": link.is_active,
        "created_at": link.created_at,
        "updated_at": link.updated_at,
    }


def list_warehouses(db: Session, fc: Fulfillment) -> list[dict[str, Any]]:
    links = db.execute(
        select(FulfillmentWarehouse)
        .where(FulfillmentWarehouse.fulfillment_id == fc.id)
        .order_by(FulfillmentWarehouse.is_primary.desc(), FulfillmentWarehouse.created_at.asc())
    ).scalars().all()
    return [_warehouse_link_read(db, link) for link in links]


def update_warehouse_link(
    db: Session,
    fc: Fulfillment,
    link_id: uuid.UUID,
    payload: WarehouseLinkUpdate,
    actor: str | None,
) -> dict[str, Any]:
    link = db.get(FulfillmentWarehouse, link_id)
    if not link or link.fulfillment_id != fc.id:
        raise HTTPException(status_code=404, detail="Привязка склада не найдена")
    data = payload.model_dump(exclude_unset=True)
    if "warehouse_kind" in data and data["warehouse_kind"] not in WAREHOUSE_KINDS:
        raise HTTPException(status_code=400, detail="Недопустимый тип склада")
    if data.get("is_primary") is True:
        for row in db.execute(
            select(FulfillmentWarehouse).where(FulfillmentWarehouse.fulfillment_id == fc.id)
        ).scalars().all():
            row.is_primary = row.id == link.id
        data.pop("is_primary", None)
        link.is_primary = True
    for key, value in data.items():
        setattr(link, key, value)
    fc.updated_by = actor
    _add_event(db, fc.id, "WAREHOUSE_UPDATED", actor, "Параметры склада обновлены", {"link_id": str(link.id)})
    db.commit()
    db.refresh(link)
    return _warehouse_link_read(db, link)


def deactivate_warehouse_link(
    db: Session,
    fc: Fulfillment,
    link_id: uuid.UUID,
    actor: str | None,
) -> dict[str, Any]:
    link = db.get(FulfillmentWarehouse, link_id)
    if not link or link.fulfillment_id != fc.id:
        raise HTTPException(status_code=404, detail="Привязка склада не найдена")
    if link.is_primary:
        active_others = db.execute(
            select(FulfillmentWarehouse).where(
                FulfillmentWarehouse.fulfillment_id == fc.id,
                FulfillmentWarehouse.id != link.id,
                FulfillmentWarehouse.is_active.is_(True),
            )
        ).scalars().all()
        if not active_others and fc.status == "ACTIVE":
            raise HTTPException(
                status_code=409,
                detail="Нельзя отключить единственный активный основной склад у ACTIVE фулфилмента",
            )
        if active_others:
            active_others[0].is_primary = True
            link.is_primary = False
    link.is_active = False
    fc.updated_by = actor
    _add_event(db, fc.id, "WAREHOUSE_DEACTIVATED", actor, "Склад отключён от фулфилмента", {"link_id": str(link.id)})
    db.commit()
    return _warehouse_link_read(db, link)


def add_client(
    db: Session,
    fc: Fulfillment,
    payload: ClientLinkCreate,
    actor: str | None,
    *,
    commit: bool = True,
) -> dict[str, Any]:
    if fc.status == "ARCHIVED":
        raise HTTPException(status_code=409, detail="Архивный фулфилмент нельзя изменять")
    limits = _limits(fc)
    if _count_active_links(db, FulfillmentClient, fc.id) >= limits["max_clients"]:
        raise HTTPException(status_code=409, detail=f"Достигнут лимит клиентов ({limits['max_clients']})")

    if payload.existing_client_id:
        client = db.get(Client, payload.existing_client_id)
        if not client:
            raise HTTPException(status_code=404, detail="Клиент не найден")
        other = db.execute(
            select(FulfillmentClient).where(FulfillmentClient.client_id == client.id)
        ).scalars().first()
        if other and other.fulfillment_id != fc.id:
            raise HTTPException(status_code=409, detail="Клиент уже принадлежит другому фулфилменту")
        already = db.execute(
            select(FulfillmentClient).where(
                FulfillmentClient.fulfillment_id == fc.id,
                FulfillmentClient.client_id == client.id,
            )
        ).scalars().first()
        if already:
            raise HTTPException(status_code=409, detail="Клиент уже привязан")
    else:
        code = payload.code
        clash = db.execute(select(Client).where(Client.code == code)).scalars().first()
        if clash:
            prefixed = f"{fc.code}-{payload.code}"[:64]
            if db.execute(select(Client).where(Client.code == prefixed)).scalars().first():
                raise HTTPException(status_code=409, detail="Код клиента занят")
            code = prefixed
        client = Client(id=uuid.uuid4(), code=code, name=payload.name.strip(), is_active=True)
        db.add(client)
        db.flush()

    if payload.is_pilot:
        for row in db.execute(
            select(FulfillmentClient).where(FulfillmentClient.fulfillment_id == fc.id)
        ).scalars().all():
            row.is_pilot = False

    link = FulfillmentClient(
        id=uuid.uuid4(),
        fulfillment_id=fc.id,
        client_id=client.id,
        is_pilot=payload.is_pilot,
        is_active=True,
    )
    db.add(link)
    if fc.status == "DRAFT":
        fc.status = "ONBOARDING"
    fc.updated_by = actor
    _add_event(db, fc.id, "CLIENT_LINKED", actor, f"Клиент {client.code} привязан", {"client_id": str(client.id)})
    if commit:
        try:
            db.commit()
        except IntegrityError as exc:
            db.rollback()
            raise HTTPException(status_code=409, detail="Не удалось привязать клиента") from exc
        db.refresh(link)
    return _client_link_read(db, link)


def _client_link_read(db: Session, link: FulfillmentClient) -> dict[str, Any]:
    client = db.get(Client, link.client_id)
    if not client:
        raise HTTPException(status_code=404, detail="Клиент не найден")
    creds = int(
        db.execute(
            select(func.count()).select_from(MarketplaceCredential).where(
                MarketplaceCredential.client_id == client.id,
                MarketplaceCredential.is_active.is_(True),
            )
        ).scalar_one()
        or 0
    )
    return {
        "id": link.id,
        "fulfillment_id": link.fulfillment_id,
        "client_id": client.id,
        "client_code": client.code,
        "client_name": client.name,
        "client_is_active": client.is_active,
        "is_pilot": link.is_pilot,
        "is_active": link.is_active,
        "marketplace_credentials_count": creds,
        "created_at": link.created_at,
        "updated_at": link.updated_at,
    }


def list_clients(db: Session, fc: Fulfillment) -> list[dict[str, Any]]:
    links = db.execute(
        select(FulfillmentClient)
        .where(FulfillmentClient.fulfillment_id == fc.id)
        .order_by(FulfillmentClient.is_pilot.desc(), FulfillmentClient.created_at.asc())
    ).scalars().all()
    return [_client_link_read(db, link) for link in links]


def update_client_link(
    db: Session,
    fc: Fulfillment,
    link_id: uuid.UUID,
    payload: ClientLinkUpdate,
    actor: str | None,
) -> dict[str, Any]:
    link = db.get(FulfillmentClient, link_id)
    if not link or link.fulfillment_id != fc.id:
        raise HTTPException(status_code=404, detail="Привязка клиента не найдена")
    data = payload.model_dump(exclude_unset=True)
    if data.get("is_pilot") is True:
        for row in db.execute(
            select(FulfillmentClient).where(FulfillmentClient.fulfillment_id == fc.id)
        ).scalars().all():
            row.is_pilot = row.id == link.id
        data.pop("is_pilot", None)
        link.is_pilot = True
    for key, value in data.items():
        setattr(link, key, value)
    fc.updated_by = actor
    _add_event(db, fc.id, "CLIENT_UPDATED", actor, "Параметры клиента обновлены", {"link_id": str(link.id)})
    db.commit()
    db.refresh(link)
    return _client_link_read(db, link)


def attach_marketplace_credential(
    db: Session,
    fc: Fulfillment,
    client_link_id: uuid.UUID,
    payload: MarketplaceCredentialAttach,
    actor: str | None,
) -> dict[str, Any]:
    link = db.get(FulfillmentClient, client_link_id)
    if not link or link.fulfillment_id != fc.id or not link.is_active:
        raise HTTPException(status_code=404, detail="Клиент фулфилмента не найден")
    name = payload.name.strip()
    provider = payload.provider.upper()
    existing = db.execute(
        select(MarketplaceCredential).where(
            MarketplaceCredential.client_id == link.client_id,
            MarketplaceCredential.provider == provider,
            MarketplaceCredential.name == name,
            MarketplaceCredential.is_active.is_(True),
        )
    ).scalars().first()
    if existing:
        raise HTTPException(status_code=409, detail="Активное подключение с таким названием уже существует")

    inactive = db.execute(
        select(MarketplaceCredential).where(
            MarketplaceCredential.client_id == link.client_id,
            MarketplaceCredential.provider == provider,
            MarketplaceCredential.name == name,
            MarketplaceCredential.is_active.is_(False),
        ).order_by(MarketplaceCredential.updated_at.desc())
    ).scalars().first()
    if inactive:
        inactive.external_client_id = (payload.external_client_id or "").strip() or None
        inactive.encrypted_api_key = encrypt_secret(payload.api_key.strip())
        inactive.is_active = True
        inactive.last_tested_at = None
        inactive.last_test_ok = None
        inactive.last_test_message = "API-связь восстановлена через platform onboarding"
        cred = inactive
    else:
        cred = MarketplaceCredential(
            id=uuid.uuid4(),
            client_id=link.client_id,
            provider=provider,
            name=name,
            external_client_id=(payload.external_client_id or "").strip() or None,
            encrypted_api_key=encrypt_secret(payload.api_key.strip()),
            is_active=True,
            warehouse_settings={},
        )
        db.add(cred)

    fc.updated_by = actor
    _add_event(
        db,
        fc.id,
        "MARKETPLACE_ATTACHED",
        actor,
        f"{provider} подключён для клиента",
        {"client_id": str(link.client_id), "provider": provider, "name": name},
    )
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="Не удалось сохранить marketplace credential") from exc
    db.refresh(cred)
    return {
        "credential_id": cred.id,
        "client_id": cred.client_id,
        "fulfillment_id": fc.id,
        "provider": cred.provider,
        "name": cred.name,
        "external_client_id": cred.external_client_id,
        "is_active": cred.is_active,
    }


def add_user(
    db: Session,
    fc: Fulfillment,
    payload: UserCreate,
    actor: str | None,
    *,
    commit: bool = True,
) -> FulfillmentUser:
    if fc.status == "ARCHIVED":
        raise HTTPException(status_code=409, detail="Архивный фулфилмент нельзя изменять")
    if payload.role not in USER_ROLES:
        raise HTTPException(status_code=400, detail="Недопустимая роль")
    limits = _limits(fc)
    if _count_active_links(db, FulfillmentUser, fc.id) >= limits["max_users"]:
        raise HTTPException(status_code=409, detail=f"Достигнут лимит пользователей ({limits['max_users']})")

    exists = db.execute(
        select(FulfillmentUser).where(
            FulfillmentUser.fulfillment_id == fc.id,
            FulfillmentUser.username == payload.username,
        )
    ).scalars().first()
    if exists:
        raise HTTPException(status_code=409, detail="Пользователь с таким username уже есть")

    user = FulfillmentUser(
        id=uuid.uuid4(),
        fulfillment_id=fc.id,
        username=payload.username,
        display_name=payload.display_name.strip(),
        email=(payload.email or "").strip() or None,
        role=payload.role,
        password_hash=hash_password(payload.password),
        is_active=True,
        must_change_password=payload.must_change_password,
        created_by=actor,
    )
    db.add(user)
    if fc.status == "DRAFT":
        fc.status = "ONBOARDING"
    fc.updated_by = actor
    _add_event(db, fc.id, "USER_CREATED", actor, f"Пользователь {user.username} создан", {"role": user.role})
    if commit:
        try:
            db.commit()
        except IntegrityError as exc:
            db.rollback()
            raise HTTPException(status_code=409, detail="Не удалось создать пользователя") from exc
        db.refresh(user)
    return user


def list_users(db: Session, fc: Fulfillment) -> list[FulfillmentUser]:
    return list(
        db.execute(
            select(FulfillmentUser)
            .where(FulfillmentUser.fulfillment_id == fc.id)
            .order_by(FulfillmentUser.role.asc(), FulfillmentUser.username.asc())
        ).scalars().all()
    )


def update_user(
    db: Session,
    fc: Fulfillment,
    user_id: uuid.UUID,
    payload: UserUpdate,
    actor: str | None,
) -> FulfillmentUser:
    user = db.get(FulfillmentUser, user_id)
    if not user or user.fulfillment_id != fc.id:
        raise HTTPException(status_code=404, detail="Пользователь не найден")
    data = payload.model_dump(exclude_unset=True)
    if "role" in data and data["role"] not in USER_ROLES:
        raise HTTPException(status_code=400, detail="Недопустимая роль")
    if "password" in data:
        user.password_hash = hash_password(data.pop("password"))
    for key, value in data.items():
        if key in {"display_name", "email"} and isinstance(value, str):
            value = value.strip() or None if key == "email" else value.strip()
        setattr(user, key, value)
    if payload.is_active is False and user.role == "FC_ADMIN":
        other_admins = db.execute(
            select(func.count()).select_from(FulfillmentUser).where(
                FulfillmentUser.fulfillment_id == fc.id,
                FulfillmentUser.id != user.id,
                FulfillmentUser.role == "FC_ADMIN",
                FulfillmentUser.is_active.is_(True),
            )
        ).scalar_one()
        if int(other_admins or 0) == 0 and fc.status == "ACTIVE":
            raise HTTPException(status_code=409, detail="Нельзя отключить последнего активного FC_ADMIN")
    fc.updated_by = actor
    _add_event(db, fc.id, "USER_UPDATED", actor, f"Пользователь {user.username} обновлён")
    db.commit()
    db.refresh(user)
    return user


def add_print_station(
    db: Session,
    fc: Fulfillment,
    payload: PrintStationCreate,
    actor: str | None,
) -> FulfillmentPrintStation:
    if fc.status == "ARCHIVED":
        raise HTTPException(status_code=409, detail="Архивный фулфилмент нельзя изменять")
    limits = _limits(fc)
    if _count_active_links(db, FulfillmentPrintStation, fc.id) >= limits["max_print_stations"]:
        raise HTTPException(
            status_code=409,
            detail=f"Достигнут лимит рабочих мест печати ({limits['max_print_stations']})",
        )
    station_code = normalize_station_code(payload.station_code)
    exists = db.execute(
        select(FulfillmentPrintStation).where(
            FulfillmentPrintStation.fulfillment_id == fc.id,
            FulfillmentPrintStation.station_code == station_code,
        )
    ).scalars().first()
    if exists:
        raise HTTPException(status_code=409, detail="Рабочее место с таким кодом уже есть")

    station = FulfillmentPrintStation(
        id=uuid.uuid4(),
        fulfillment_id=fc.id,
        station_code=station_code,
        name=(payload.name or "").strip() or None,
        printer_name=(payload.printer_name or "").strip() or None,
        label_format=(payload.label_format or "58x40").strip() or "58x40",
        notes=payload.notes,
        is_active=True,
    )
    db.add(station)
    if fc.status == "DRAFT":
        fc.status = "ONBOARDING"
    fc.updated_by = actor
    _add_event(db, fc.id, "PRINT_STATION_CREATED", actor, f"Станция {station_code} создана")
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="Не удалось создать рабочее место") from exc
    db.refresh(station)
    return station


def list_print_stations(db: Session, fc: Fulfillment) -> list[FulfillmentPrintStation]:
    return list(
        db.execute(
            select(FulfillmentPrintStation)
            .where(FulfillmentPrintStation.fulfillment_id == fc.id)
            .order_by(FulfillmentPrintStation.station_code.asc())
        ).scalars().all()
    )


def update_print_station(
    db: Session,
    fc: Fulfillment,
    station_id: uuid.UUID,
    payload: PrintStationUpdate,
    actor: str | None,
) -> FulfillmentPrintStation:
    station = db.get(FulfillmentPrintStation, station_id)
    if not station or station.fulfillment_id != fc.id:
        raise HTTPException(status_code=404, detail="Рабочее место не найдено")
    data = payload.model_dump(exclude_unset=True)
    for key, value in data.items():
        if key in {"name", "printer_name", "label_format", "notes"} and isinstance(value, str):
            value = value.strip() or None if key != "label_format" else (value.strip() or "58x40")
        setattr(station, key, value)
    fc.updated_by = actor
    _add_event(db, fc.id, "PRINT_STATION_UPDATED", actor, f"Станция {station.station_code} обновлена")
    db.commit()
    db.refresh(station)
    return station


def readiness_report(db: Session, fc: Fulfillment) -> ReadinessReport:
    warehouses = list_warehouses(db, fc)
    active_warehouses = [w for w in warehouses if w["is_active"] and w["warehouse_is_active"]]
    users = [u for u in list_users(db, fc) if u.is_active]
    admins = [u for u in users if u.role == "FC_ADMIN"]
    stations = [s for s in list_print_stations(db, fc) if s.is_active]
    clients = [c for c in list_clients(db, fc) if c["is_active"]]
    pilot_with_creds = [c for c in clients if c["is_pilot"] and c["marketplace_credentials_count"] > 0]

    checks = [
        ReadinessCheck(
            key="identity",
            ok=bool(fc.code and fc.name and fc.timezone),
            required=True,
            message="Код, название и часовой пояс заданы" if fc.code and fc.name else "Заполните код и название",
        ),
        ReadinessCheck(
            key="admin_user",
            ok=bool(admins),
            required=True,
            message=f"Активных FC_ADMIN: {len(admins)}" if admins else "Создайте хотя бы одного FC_ADMIN",
        ),
        ReadinessCheck(
            key="primary_warehouse",
            ok=any(w["is_primary"] for w in active_warehouses),
            required=True,
            message="Есть основной активный склад"
            if any(w["is_primary"] for w in active_warehouses)
            else "Добавьте основной склад",
        ),
        ReadinessCheck(
            key="print_token",
            ok=bool(fc.encrypted_print_agent_token),
            required=True,
            message="Print Agent token создан" if fc.encrypted_print_agent_token else "Нет токена печати",
        ),
        ReadinessCheck(
            key="print_station",
            ok=bool(stations),
            required=False,
            message=f"Активных станций печати: {len(stations)}"
            if stations
            else "Рекомендуется добавить PACK-станцию",
        ),
        ReadinessCheck(
            key="pilot_client_marketplace",
            ok=bool(pilot_with_creds) or bool(clients),
            required=False,
            message=(
                "Есть пилотный клиент с marketplace credential"
                if pilot_with_creds
                else ("Клиенты есть, marketplace можно подключить позже" if clients else "Клиент ещё не добавлен")
            ),
        ),
        ReadinessCheck(
            key="feature_profile",
            ok=bool((fc.feature_profile or {}).get("template")),
            required=True,
            message="Профиль функций задан" if (fc.feature_profile or {}).get("template") else "Нет feature profile",
        ),
        ReadinessCheck(
            key="not_archived",
            ok=fc.status != "ARCHIVED",
            required=True,
            message="Фулфилмент не в архиве" if fc.status != "ARCHIVED" else "Фулфилмент архивирован",
        ),
    ]
    ready = all(c.ok for c in checks if c.required)
    return ReadinessReport(
        fulfillment_id=fc.id,
        status=fc.status,  # type: ignore[arg-type]
        ready_to_activate=ready and fc.status in {"DRAFT", "ONBOARDING", "SUSPENDED"},
        checks=checks,
    )


def activate_fulfillment(db: Session, fc: Fulfillment, actor: str | None) -> tuple[Fulfillment, ReadinessReport]:
    if fc.status == "ARCHIVED":
        raise HTTPException(status_code=409, detail="Архивный фулфилмент нельзя активировать")
    if fc.status == "ACTIVE":
        report = readiness_report(db, fc)
        return fc, report

    report = readiness_report(db, fc)
    if not all(c.ok for c in report.checks if c.required):
        raise HTTPException(
            status_code=409,
            detail={
                "message": "Фулфилмент не готов к активации",
                "readiness": report.model_dump(mode="json"),
            },
        )
    fc.status = "ACTIVE"
    fc.activated_at = fc.activated_at or _now()
    fc.suspended_at = None
    fc.updated_by = actor
    _add_event(db, fc.id, "ACTIVATED", actor, "Фулфилмент активирован")
    db.commit()
    db.refresh(fc)
    return fc, readiness_report(db, fc)


def suspend_fulfillment(db: Session, fc: Fulfillment, actor: str | None) -> Fulfillment:
    if fc.status != "ACTIVE":
        raise HTTPException(status_code=409, detail="Приостановить можно только ACTIVE фулфилмент")
    if fc.is_default:
        raise HTTPException(status_code=409, detail="Default-фулфилмент текущего инстанса нельзя приостановить")
    fc.status = "SUSPENDED"
    fc.suspended_at = _now()
    fc.updated_by = actor
    _add_event(db, fc.id, "SUSPENDED", actor, "Фулфилмент приостановлен")
    db.commit()
    db.refresh(fc)
    return fc


def resume_fulfillment(db: Session, fc: Fulfillment, actor: str | None) -> Fulfillment:
    if fc.status != "SUSPENDED":
        raise HTTPException(status_code=409, detail="Возобновить можно только SUSPENDED фулфилмент")
    report = readiness_report(db, fc)
    if not all(c.ok for c in report.checks if c.required):
        raise HTTPException(
            status_code=409,
            detail={
                "message": "Нельзя возобновить: не пройдены обязательные проверки",
                "readiness": report.model_dump(mode="json"),
            },
        )
    fc.status = "ACTIVE"
    fc.suspended_at = None
    fc.updated_by = actor
    _add_event(db, fc.id, "RESUMED", actor, "Фулфилмент возобновлён")
    db.commit()
    db.refresh(fc)
    return fc


def archive_fulfillment(db: Session, fc: Fulfillment, actor: str | None) -> Fulfillment:
    if fc.is_default:
        raise HTTPException(status_code=409, detail="Default-фулфилмент текущего инстанса нельзя архивировать")
    if fc.status == "ARCHIVED":
        return fc
    if fc.status == "ACTIVE":
        raise HTTPException(status_code=409, detail="Сначала приостановите ACTIVE фулфилмент")
    fc.status = "ARCHIVED"
    fc.archived_at = _now()
    fc.updated_by = actor
    _add_event(db, fc.id, "ARCHIVED", actor, "Фулфилмент архивирован")
    db.commit()
    db.refresh(fc)
    return fc


def rotate_print_token(db: Session, fc: Fulfillment, actor: str | None) -> str:
    token = secrets.token_urlsafe(32)
    fc.encrypted_print_agent_token = encrypt_secret(token)
    fc.updated_by = actor
    _add_event(db, fc.id, "PRINT_TOKEN_ROTATED", actor, "Токен Print Agent ротирован")
    db.commit()
    db.refresh(fc)
    return token


def reveal_print_token(fc: Fulfillment, station_code: str | None = None) -> dict[str, Any]:
    if not fc.encrypted_print_agent_token:
        raise HTTPException(status_code=409, detail="У фулфилмента нет Print Agent token")
    master = decrypt_secret(fc.encrypted_print_agent_token)
    station = normalize_station_code(station_code) if station_code else None
    value = station_token(master, station) if station else master
    return {
        "fulfillment_id": fc.id,
        "token": value,
        "station_code": station,
        "station_bound": bool(station),
    }


def list_events(db: Session, fc: Fulfillment, limit: int = 100) -> list[FulfillmentEvent]:
    limit = max(1, min(500, int(limit)))
    return list(
        db.execute(
            select(FulfillmentEvent)
            .where(FulfillmentEvent.fulfillment_id == fc.id)
            .order_by(FulfillmentEvent.created_at.desc())
            .limit(limit)
        ).scalars().all()
    )
