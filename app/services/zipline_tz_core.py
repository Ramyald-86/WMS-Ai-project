"""ZIPLINE OS TZ completion layer — schema + helpers for partial gaps."""

from __future__ import annotations

import hashlib
import json
import secrets
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.services.zipline_permissions import permissions_for_role

_SCHEMA_READY = False

DEFAULT_SUBSCRIPTION_FEE_RUB = 10_000
PHYSICAL_TYPES = ("PALLET", "CARTON", "BAG", "ITEM")
BUSINESS_PURPOSES = (
    "PVZ_RETURN",
    "FBS",
    "FBO",
    "GENERAL_STORAGE",
    "DEFECT",
    "QUARANTINE",
    "CROSS_DOCK",
)
RETURN_STATES = (
    "RETURN_RECEIVED",
    "QC_OK",
    "REPACK_REQUIRED",
    "DEFECT",
    "UNKNOWN",
    "AVAILABLE",
    "QUARANTINE",
    "RETURN_TO_CLIENT",
)
DELIVERY_METHODS = ("CLIENT_SELF", "ZIPLINE_PICKUP")
CARGO_TYPES = ("PALLET", "CARTON", "BAG", "ITEM", "MIX")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def install_zipline_tz_schema(db: Session) -> dict[str, Any]:
    global _SCHEMA_READY
    if _SCHEMA_READY:
        return {"ok": True, "cached": True}

    statements = [
        """
        CREATE TABLE IF NOT EXISTS zipline_platform_settings (
            key VARCHAR(64) PRIMARY KEY,
            value JSONB NOT NULL DEFAULT '{}'::jsonb,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_by VARCHAR(120) NULL
        )
        """,
        """
        ALTER TABLE fulfillment_users
            ADD COLUMN IF NOT EXISTS permissions JSONB NOT NULL DEFAULT '[]'::jsonb
        """,
        """
        ALTER TABLE fulfillment_users
            ADD COLUMN IF NOT EXISTS pin_hash TEXT NULL
        """,
        """
        ALTER TABLE fulfillment_users
            ADD COLUMN IF NOT EXISTS modules JSONB NOT NULL DEFAULT '[]'::jsonb
        """,
        """
        CREATE TABLE IF NOT EXISTS zipline_inbound_requests (
            id UUID PRIMARY KEY,
            tenant_id UUID NULL,
            client_id UUID NOT NULL,
            warehouse_id UUID NULL,
            delivery_method VARCHAR(32) NOT NULL,
            cargo_types JSONB NOT NULL DEFAULT '[]'::jsonb,
            volume_m3 NUMERIC(12,3) NULL,
            weight_kg NUMERIC(12,3) NULL,
            delivery_date DATE NULL,
            time_window VARCHAR(64) NULL,
            contact VARCHAR(255) NULL,
            access_notes TEXT NULL,
            marking_status VARCHAR(32) NOT NULL DEFAULT 'UNMARKED',
            estimated_price NUMERIC(12,2) NULL,
            status VARCHAR(32) NOT NULL DEFAULT 'DRAFT',
            lines JSONB NOT NULL DEFAULT '[]'::jsonb,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            created_by VARCHAR(120) NULL
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS zipline_buffer_units (
            id UUID PRIMARY KEY,
            inbound_request_id UUID NULL,
            warehouse_id UUID NULL,
            client_id UUID NULL,
            barcode VARCHAR(120) NOT NULL,
            physical_type VARCHAR(32) NOT NULL,
            business_purpose VARCHAR(32) NOT NULL,
            route_label VARCHAR(120) NULL,
            status VARCHAR(32) NOT NULL DEFAULT 'IN_BUFFER',
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS zipline_return_qc (
            id UUID PRIMARY KEY,
            warehouse_id UUID NULL,
            client_id UUID NULL,
            order_id UUID NULL,
            shipment_id UUID NULL,
            sku_id UUID NULL,
            barcode VARCHAR(255) NULL,
            origin VARCHAR(64) NULL,
            reason TEXT NULL,
            photo_url TEXT NULL,
            video_url TEXT NULL,
            state VARCHAR(32) NOT NULL DEFAULT 'RETURN_RECEIVED',
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            decided_by VARCHAR(120) NULL
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS zipline_defect_reviews (
            id UUID PRIMARY KEY,
            warehouse_id UUID NULL,
            client_id UUID NULL,
            sku_id UUID NULL,
            barcode VARCHAR(255) NULL,
            short_reason VARCHAR(255) NOT NULL,
            state VARCHAR(32) NOT NULL DEFAULT 'DEFECT_PENDING_REVIEW',
            disposition VARCHAR(64) NULL,
            detail_reason TEXT NULL,
            evidence JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            created_by VARCHAR(120) NULL,
            reviewed_by VARCHAR(120) NULL
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS zipline_shipping_hu (
            id UUID PRIMARY KEY,
            order_id UUID NULL,
            warehouse_id UUID NULL,
            client_id UUID NULL,
            hu_code VARCHAR(120) NOT NULL UNIQUE,
            shipping_barcode VARCHAR(190) NULL,
            place_index INT NOT NULL DEFAULT 1,
            place_total INT NOT NULL DEFAULT 1,
            status VARCHAR(32) NOT NULL DEFAULT 'OPEN',
            weight_kg NUMERIC(12,3) NULL,
            contents JSONB NOT NULL DEFAULT '[]'::jsonb,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            closed_at TIMESTAMPTZ NULL,
            packing_station VARCHAR(64) NULL
        )
        """,
        """
        CREATE UNIQUE INDEX IF NOT EXISTS uq_zipline_hu_active_shipping_barcode
        ON zipline_shipping_hu(shipping_barcode)
        WHERE shipping_barcode IS NOT NULL AND status IN ('OPEN','CLOSED','STAGED','LOADED')
        """,
        """
        CREATE TABLE IF NOT EXISTS zipline_pack_stations (
            station_code VARCHAR(64) PRIMARY KEY,
            warehouse_id UUID NULL,
            capabilities JSONB NOT NULL DEFAULT '{}'::jsonb,
            busy_order_id UUID NULL,
            last_assigned_at TIMESTAMPTZ NULL,
            is_active BOOLEAN NOT NULL DEFAULT TRUE
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS zipline_shipments (
            id UUID PRIMARY KEY,
            warehouse_id UUID NULL,
            channel VARCHAR(32) NOT NULL DEFAULT 'INTERNAL',
            status VARCHAR(32) NOT NULL DEFAULT 'DRAFT',
            marketplace_status VARCHAR(64) NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            completed_at TIMESTAMPTZ NULL,
            created_by VARCHAR(120) NULL
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS zipline_shipment_hus (
            shipment_id UUID NOT NULL REFERENCES zipline_shipments(id) ON DELETE CASCADE,
            hu_id UUID NOT NULL REFERENCES zipline_shipping_hu(id) ON DELETE RESTRICT,
            staged_at TIMESTAMPTZ NULL,
            staging_location VARCHAR(120) NULL,
            loaded_at TIMESTAMPTZ NULL,
            load_scan_count INT NOT NULL DEFAULT 0,
            PRIMARY KEY (shipment_id, hu_id)
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS zipline_marking_codes (
            id UUID PRIMARY KEY,
            client_id UUID NULL,
            sku_id UUID NULL,
            article VARCHAR(120) NULL,
            code VARCHAR(255) NOT NULL UNIQUE,
            source VARCHAR(32) NOT NULL DEFAULT 'POOL',
            legal_status VARCHAR(64) NULL,
            workflow_status VARCHAR(32) NOT NULL DEFAULT 'AVAILABLE',
            order_id UUID NULL,
            hu_id UUID NULL,
            reserved_at TIMESTAMPTZ NULL,
            scanned_at TIMESTAMPTZ NULL,
            used_at TIMESTAMPTZ NULL
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS zipline_mobile_print_links (
            id UUID PRIMARY KEY,
            printer_qr VARCHAR(120) NOT NULL UNIQUE,
            station_code VARCHAR(64) NOT NULL,
            printer_name VARCHAR(255) NULL,
            bound_device_id VARCHAR(120) NULL,
            bound_employee_id UUID NULL,
            bound_until TIMESTAMPTZ NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS zipline_finance_opening (
            id UUID PRIMARY KEY,
            tenant_id UUID NULL,
            snapshot JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            created_by VARCHAR(120) NULL,
            locked BOOLEAN NOT NULL DEFAULT TRUE
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS zipline_billing_charges (
            id UUID PRIMARY KEY,
            tenant_id UUID NULL,
            client_id UUID NULL,
            source_event_id UUID NULL,
            tariff_version VARCHAR(64) NOT NULL,
            amount NUMERIC(14,2) NOT NULL,
            currency VARCHAR(8) NOT NULL DEFAULT 'RUB',
            category VARCHAR(64) NOT NULL,
            evidence JSONB NOT NULL DEFAULT '{}'::jsonb,
            status VARCHAR(32) NOT NULL DEFAULT 'POSTED',
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS zipline_operation_guides (
            id VARCHAR(64) PRIMARY KEY,
            title VARCHAR(255) NOT NULL,
            steps JSONB NOT NULL DEFAULT '[]'::jsonb,
            roles JSONB NOT NULL DEFAULT '[]'::jsonb,
            module_id VARCHAR(64) NULL
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS zipline_tsd_pin_tokens (
            token VARCHAR(64) PRIMARY KEY,
            user_id UUID NOT NULL,
            fulfillment_id UUID NOT NULL,
            expires_at TIMESTAMPTZ NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """,
    ]
    for sql in statements:
        db.execute(text(sql))

    # Seed subscription fee if missing
    exists = db.execute(
        text("SELECT 1 FROM zipline_platform_settings WHERE key = 'partner_subscription_fee_rub'")
    ).scalar()
    if not exists:
        db.execute(
            text(
                """
                INSERT INTO zipline_platform_settings(key, value, updated_by)
                VALUES ('partner_subscription_fee_rub', CAST(:v AS jsonb), 'system')
                """
            ),
            {"v": json.dumps({"amount": DEFAULT_SUBSCRIPTION_FEE_RUB, "currency": "RUB"})},
        )

    # Seed pack stations
    for code in ("PACK-01", "PACK-02", "PACK-03"):
        db.execute(
            text(
                """
                INSERT INTO zipline_pack_stations(station_code, capabilities, is_active)
                VALUES (:c, CAST(:cap AS jsonb), TRUE)
                ON CONFLICT (station_code) DO NOTHING
                """
            ),
            {"c": code, "cap": json.dumps({"printer": True, "scale": True, "camera": True})},
        )

    # Seed operation guides
    guides = [
        (
            "receiving",
            "Приёмка",
            ["palletreceive", "receiving_terminal"],
            ["OPERATOR", "WAREHOUSE", "SUPERVISOR", "FC_ADMIN"],
            [
                {"title": "Откройте Приёмку", "module_id": "palletreceive", "highlight": "НАЧАТЬ ПРИЁМКУ"},
                {"title": "Создайте короб", "module_id": "receiving_terminal", "highlight": "НОВЫЙ КОРОБ"},
                {"title": "Сканируйте товар", "module_id": "receiving_terminal", "highlight": "СКАН ШК / ЧЗ"},
                {"title": "Закройте короб и разместите", "module_id": "receiving_terminal", "highlight": "ПРИНЯТЬ И РАЗМЕСТИТЬ"},
            ],
        ),
        (
            "picking_fbs",
            "Сборка FBS",
            ["warehousework", "tsdflow"],
            ["OPERATOR", "PICKER", "SUPERVISOR", "FC_ADMIN"],
            [
                {"title": "Откройте Сборку", "module_id": "warehousework", "highlight": "МОИ ЗАДАНИЯ"},
                {"title": "Начните задание", "module_id": "tsdflow", "highlight": "НАЧАТЬ"},
                {"title": "Сканируйте товар / ЧЗ", "module_id": "tsdflow", "highlight": "СКАН"},
                {"title": "При проблеме укажите причину", "module_id": "tsdflow", "highlight": "ПРОБЛЕМА"},
            ],
        ),
        (
            "packing",
            "Упаковка",
            ["supplies"],
            ["OPERATOR", "PACKER", "SUPERVISOR", "FC_ADMIN"],
            [
                {"title": "Откройте упаковку", "module_id": "supplies", "highlight": "НАЧАТЬ УПАКОВКУ"},
                {"title": "Сканируйте ШК / ЧЗ", "module_id": "supplies", "highlight": "СКАН"},
                {"title": "Закройте грузовое место", "module_id": "supplies", "highlight": "ЗАКРЫТЬ МЕСТО"},
                {"title": "Завершите упаковку", "module_id": "supplies", "highlight": "ЗАВЕРШИТЬ УПАКОВКУ"},
            ],
        ),
        (
            "outbound",
            "Отгрузка",
            ["supplies", "ship"],
            ["OPERATOR", "SUPERVISOR", "FC_ADMIN", "WB_OPERATOR"],
            [
                {"title": "Создайте отгрузку из упакованных", "module_id": "supplies", "highlight": "СОЗДАТЬ ОТГРУЗКУ ИЗ УПАКОВАННЫХ"},
                {"title": "Staging: HU → STAGE", "module_id": "ship", "highlight": "СТЕЙДЖИНГ"},
                {"title": "Погрузка сканом HU", "module_id": "ship", "highlight": "СКАН HU"},
            ],
        ),
    ]
    for gid, title, modules, roles, steps in guides:
        db.execute(
            text(
                """
                INSERT INTO zipline_operation_guides(id, title, steps, roles, module_id)
                VALUES (:id, :title, CAST(:steps AS jsonb), CAST(:roles AS jsonb), :module_id)
                ON CONFLICT (id) DO UPDATE SET
                    title = EXCLUDED.title,
                    steps = EXCLUDED.steps,
                    roles = EXCLUDED.roles,
                    module_id = EXCLUDED.module_id
                """
            ),
            {
                "id": gid,
                "title": title,
                "steps": json.dumps(steps, ensure_ascii=False),
                "roles": json.dumps(roles, ensure_ascii=False),
                "module_id": modules[0],
            },
        )

    db.commit()
    _SCHEMA_READY = True
    return {"ok": True, "cached": False}


def get_setting(db: Session, key: str, default: Any = None) -> Any:
    install_zipline_tz_schema(db)
    row = db.execute(
        text("SELECT value FROM zipline_platform_settings WHERE key = :k"),
        {"k": key},
    ).scalar()
    return row if row is not None else default


def set_setting(db: Session, key: str, value: Any, updated_by: str | None = None) -> dict[str, Any]:
    install_zipline_tz_schema(db)
    db.execute(
        text(
            """
            INSERT INTO zipline_platform_settings(key, value, updated_at, updated_by)
            VALUES (:k, CAST(:v AS jsonb), NOW(), :u)
            ON CONFLICT (key) DO UPDATE SET
                value = EXCLUDED.value,
                updated_at = NOW(),
                updated_by = EXCLUDED.updated_by
            """
        ),
        {"k": key, "v": json.dumps(value, ensure_ascii=False), "u": updated_by},
    )
    db.commit()
    return {"key": key, "value": value}


def subscription_fee(db: Session) -> dict[str, Any]:
    value = get_setting(db, "partner_subscription_fee_rub") or {
        "amount": DEFAULT_SUBSCRIPTION_FEE_RUB,
        "currency": "RUB",
    }
    if isinstance(value, dict) and "amount" in value:
        return value
    return {"amount": DEFAULT_SUBSCRIPTION_FEE_RUB, "currency": "RUB"}


def hash_pin(pin: str) -> str:
    pin = str(pin).strip()
    salt = secrets.token_hex(8)
    digest = hashlib.pbkdf2_hmac("sha256", pin.encode("utf-8"), bytes.fromhex(salt), 120_000)
    return f"pin${salt}${digest.hex()}"


def verify_pin(pin: str, encoded: str | None) -> bool:
    if not encoded or not str(encoded).startswith("pin$"):
        return False
    try:
        _, salt, expected = encoded.split("$", 2)
        digest = hashlib.pbkdf2_hmac("sha256", str(pin).strip().encode("utf-8"), bytes.fromhex(salt), 120_000)
        return secrets.compare_digest(digest.hex(), expected)
    except Exception:
        return False


def detect_scan_kind(code: str) -> dict[str, Any]:
    """Auto-detect barcode / HU / DataMatrix(CHZ) / BOX."""
    raw = str(code or "").strip()
    upper = raw.upper()
    if not raw:
        return {"kind": "EMPTY", "code": raw}
    # DataMatrix / Честный ЗНАК — typically long GS1 or 31+ chars with digits
    compact = raw.replace("\u001d", "").replace(" ", "")
    if len(compact) >= 31 and any(ch.isdigit() for ch in compact):
        return {"kind": "CHZ", "code": raw, "normalized": compact}
    if upper.startswith(("BOX-", "BX-", "HU-", "PLT-", "PAL-")):
        return {"kind": "HU", "code": raw}
    if upper.startswith("ZIP-") or upper.startswith("ZPL-"):
        return {"kind": "ZIPLINE", "code": raw}
    if raw.isdigit() and len(raw) in {8, 12, 13, 14}:
        return {"kind": "EAN", "code": raw}
    if len(raw) >= 6:
        return {"kind": "CODE128", "code": raw}
    return {"kind": "UNKNOWN", "code": raw}


def sla_risk_band(age_hours: float, sla_hours: float = 24.0) -> dict[str, Any]:
    """GREEN / YELLOW / RED by remaining SLA time (TZ §4.3)."""
    age = float(age_hours or 0)
    sla = max(float(sla_hours or 24), 1.0)
    remaining = sla - age
    ratio = age / sla
    if remaining <= 0 or ratio >= 1.0:
        band = "RED"
    elif ratio >= 0.7:
        band = "YELLOW"
    else:
        band = "GREEN"
    return {
        "sla_hours": sla,
        "age_hours": round(age, 2),
        "remaining_hours": round(remaining, 2),
        "risk": band,
        "sla_critical": band == "RED",
    }


def recommend_putaway(
    db: Session,
    *,
    warehouse_id: str | None,
    client_id: str | None,
    prefer_direct: bool = True,
) -> dict[str, Any]:
    """Smart-ish slotting: prefer locations already used by the same client."""
    install_zipline_tz_schema(db)
    params: dict[str, Any] = {}
    client_clause = ""
    wh_clause = ""
    if client_id:
        client_clause = "AND c.client_id = CAST(:client_id AS uuid)"
        params["client_id"] = client_id
    if warehouse_id:
        wh_clause = "AND l.warehouse_id = CAST(:warehouse_id AS uuid)"
        params["warehouse_id"] = warehouse_id

    # Prefer a free neighbor near client's occupied cells.
    row = None
    if client_id:
        row = db.execute(
            text(
                f"""
                SELECT l.id::text AS location_id, l.code AS location_code, l.location_type
                FROM locations l
                WHERE COALESCE(l.is_active, TRUE) = TRUE
                  AND COALESCE(l.location_type, '') NOT IN ('VIRTUAL', 'RECEIVING', 'DEFECT')
                  {wh_clause}
                  AND NOT EXISTS (
                      SELECT 1 FROM containers c2
                      WHERE c2.location_id = l.id
                        AND COALESCE(c2.status, 'ACTIVE') = 'ACTIVE'
                        AND c2.client_id IS NOT NULL
                        AND c2.client_id <> CAST(:client_id AS uuid)
                  )
                  AND EXISTS (
                      SELECT 1 FROM containers c
                      JOIN locations l2 ON l2.id = c.location_id
                      WHERE c.client_id = CAST(:client_id AS uuid)
                        AND COALESCE(c.status, 'ACTIVE') = 'ACTIVE'
                        AND l2.warehouse_id = l.warehouse_id
                        AND split_part(l.code, '-', 1) = split_part(l2.code, '-', 1)
                  )
                  AND NOT EXISTS (
                      SELECT 1 FROM containers c3
                      WHERE c3.location_id = l.id
                        AND COALESCE(c3.status, 'ACTIVE') = 'ACTIVE'
                  )
                ORDER BY l.code
                LIMIT 1
                """
            ),
            params,
        ).mappings().first()

    if not row:
        row = db.execute(
            text(
                f"""
                SELECT l.id::text AS location_id, l.code AS location_code, l.location_type
                FROM locations l
                WHERE COALESCE(l.is_active, TRUE) = TRUE
                  AND COALESCE(l.location_type, '') NOT IN ('VIRTUAL', 'RECEIVING', 'DEFECT')
                  {wh_clause}
                  AND NOT EXISTS (
                      SELECT 1 FROM containers c
                      WHERE c.location_id = l.id
                        AND COALESCE(c.status, 'ACTIVE') = 'ACTIVE'
                  )
                ORDER BY l.code
                LIMIT 1
                """
            ),
            params,
        ).mappings().first()

    if row:
        mode = "DIRECT_PUTAWAY" if prefer_direct else "TWO_STAGE_PUTAWAY"
        return {
            "mode": mode,
            "recommended": True,
            "location_id": row["location_id"],
            "location_code": row["location_code"],
            "location_type": row["location_type"],
            "reason": "client_compactness" if client_id else "first_free",
        }

    return {
        "mode": "TWO_STAGE_PUTAWAY",
        "recommended": False,
        "location_id": None,
        "location_code": None,
        "reason": "no_free_location_keep_buffer",
    }


def ensure_user_permissions_defaults(db: Session) -> int:
    """Backfill empty permissions from role templates."""
    install_zipline_tz_schema(db)
    rows = db.execute(
        text("SELECT id::text, role, permissions FROM fulfillment_users")
    ).mappings().all()
    updated = 0
    for row in rows:
        perms = row.get("permissions") or []
        if perms:
            continue
        defaults = permissions_for_role(row.get("role"))
        db.execute(
            text("UPDATE fulfillment_users SET permissions = CAST(:p AS jsonb) WHERE id = CAST(:id AS uuid)"),
            {"p": json.dumps(defaults), "id": row["id"]},
        )
        updated += 1
    if updated:
        db.commit()
    return updated
