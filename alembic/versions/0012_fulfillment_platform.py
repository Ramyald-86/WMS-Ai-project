"""Platform fulfillments (multi-tenant onboarding tables).

Revision ID: 0012_fulfillment_platform
Revises: 0011_wb_sales_pause_fast_scan

Additive only. Existing clients/warehouses/orders are untouched; a default
fulfillment row is seeded and existing masterdata is linked into it.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0012_fulfillment_platform"
down_revision: Union[str, None] = "0011_wb_sales_pause_fast_scan"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

DEFAULT_FEATURE_PROFILE = {
    "template": "full_fulfillcore",
    "template_version": "0.9.4.15",
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


def upgrade() -> None:
    uuid_type = postgresql.UUID(as_uuid=True)
    jsonb = postgresql.JSONB(astext_type=sa.Text())

    op.create_table(
        "fulfillments",
        sa.Column("id", uuid_type, nullable=False),
        sa.Column("code", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("legal_name", sa.String(length=255), nullable=True),
        sa.Column("timezone", sa.String(length=64), nullable=False, server_default="Europe/Moscow"),
        sa.Column("locale", sa.String(length=16), nullable=False, server_default="ru"),
        sa.Column("subdomain", sa.String(length=128), nullable=True),
        sa.Column("custom_domain", sa.String(length=255), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="DRAFT"),
        sa.Column("is_default", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("feature_profile", jsonb, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("encrypted_print_agent_token", sa.Text(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("activated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("suspended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", sa.String(length=120), nullable=True),
        sa.Column("updated_by", sa.String(length=120), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("code"),
        sa.UniqueConstraint("subdomain"),
        sa.UniqueConstraint("custom_domain"),
    )
    op.create_index("ix_fulfillments_code", "fulfillments", ["code"])
    op.create_index("ix_fulfillments_status", "fulfillments", ["status"])
    op.create_index("ix_fulfillments_is_default", "fulfillments", ["is_default"])

    op.create_table(
        "fulfillment_warehouses",
        sa.Column("id", uuid_type, nullable=False),
        sa.Column("fulfillment_id", uuid_type, nullable=False),
        sa.Column("warehouse_id", uuid_type, nullable=False),
        sa.Column("warehouse_kind", sa.String(length=32), nullable=False, server_default="MAIN"),
        sa.Column("is_primary", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("address", sa.String(length=500), nullable=True),
        sa.Column("contact_phone", sa.String(length=64), nullable=True),
        sa.Column("contact_email", sa.String(length=255), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["fulfillment_id"], ["fulfillments.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["warehouse_id"], ["warehouses.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("fulfillment_id", "warehouse_id", name="uq_fulfillment_warehouse"),
        sa.UniqueConstraint("warehouse_id", name="uq_fulfillment_warehouse_exclusive"),
    )
    op.create_index("ix_fulfillment_warehouses_fulfillment", "fulfillment_warehouses", ["fulfillment_id"])
    op.create_index("ix_fulfillment_warehouses_warehouse", "fulfillment_warehouses", ["warehouse_id"])

    op.create_table(
        "fulfillment_clients",
        sa.Column("id", uuid_type, nullable=False),
        sa.Column("fulfillment_id", uuid_type, nullable=False),
        sa.Column("client_id", uuid_type, nullable=False),
        sa.Column("is_pilot", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["fulfillment_id"], ["fulfillments.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["client_id"], ["clients.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("fulfillment_id", "client_id", name="uq_fulfillment_client"),
        sa.UniqueConstraint("client_id", name="uq_fulfillment_client_exclusive"),
    )
    op.create_index("ix_fulfillment_clients_fulfillment", "fulfillment_clients", ["fulfillment_id"])
    op.create_index("ix_fulfillment_clients_client", "fulfillment_clients", ["client_id"])

    op.create_table(
        "fulfillment_users",
        sa.Column("id", uuid_type, nullable=False),
        sa.Column("fulfillment_id", uuid_type, nullable=False),
        sa.Column("username", sa.String(length=100), nullable=False),
        sa.Column("display_name", sa.String(length=255), nullable=False),
        sa.Column("email", sa.String(length=255), nullable=True),
        sa.Column("role", sa.String(length=32), nullable=False, server_default="FC_ADMIN"),
        sa.Column("password_hash", sa.Text(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("must_change_password", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", sa.String(length=120), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["fulfillment_id"], ["fulfillments.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("fulfillment_id", "username", name="uq_fulfillment_user_username"),
    )
    op.create_index("ix_fulfillment_users_fulfillment", "fulfillment_users", ["fulfillment_id"])

    op.create_table(
        "fulfillment_print_stations",
        sa.Column("id", uuid_type, nullable=False),
        sa.Column("fulfillment_id", uuid_type, nullable=False),
        sa.Column("station_code", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=True),
        sa.Column("printer_name", sa.String(length=255), nullable=True),
        sa.Column("label_format", sa.String(length=32), nullable=False, server_default="58x40"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["fulfillment_id"], ["fulfillments.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("fulfillment_id", "station_code", name="uq_fulfillment_print_station"),
    )
    op.create_index(
        "ix_fulfillment_print_stations_fulfillment",
        "fulfillment_print_stations",
        ["fulfillment_id"],
    )

    op.create_table(
        "fulfillment_events",
        sa.Column("id", uuid_type, nullable=False),
        sa.Column("fulfillment_id", uuid_type, nullable=False),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("actor", sa.String(length=120), nullable=True),
        sa.Column("message", sa.String(length=500), nullable=True),
        sa.Column("data", jsonb, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["fulfillment_id"], ["fulfillments.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_fulfillment_events_fulfillment_created",
        "fulfillment_events",
        ["fulfillment_id", "created_at"],
    )

    # Seed default fulfillment and soft-link existing masterdata. Token is filled
    # later by application startup (encrypt_secret needs APP_SECRET_KEY runtime).
    bind = op.get_bind()
    now = datetime.now(timezone.utc)
    fc_id = uuid.uuid4()
    bind.execute(
        sa.text(
            """
            INSERT INTO fulfillments (
                id, code, name, timezone, locale, status, is_default, feature_profile,
                activated_at, created_by, updated_by, created_at, updated_at
            ) VALUES (
                :id, 'DEFAULT', 'Текущий фулфилмент', 'Europe/Moscow', 'ru', 'ACTIVE', TRUE,
                CAST(:profile AS jsonb), :activated_at, 'system', 'system', :created_at, :updated_at
            )
            ON CONFLICT (code) DO NOTHING
            """
        ),
        {
            "id": str(fc_id),
            "profile": json.dumps(DEFAULT_FEATURE_PROFILE, ensure_ascii=False),
            "activated_at": now,
            "created_at": now,
            "updated_at": now,
        },
    )
    row = bind.execute(sa.text("SELECT id FROM fulfillments WHERE code='DEFAULT' LIMIT 1")).first()
    if row:
        default_id = str(row[0])
        bind.execute(
            sa.text(
                """
                INSERT INTO fulfillment_warehouses (
                    id, fulfillment_id, warehouse_id, warehouse_kind, is_primary, is_active, created_at, updated_at
                )
                SELECT
                    gen_random_uuid(),
                    CAST(:fc AS uuid),
                    w.id,
                    'MAIN',
                    CASE WHEN ROW_NUMBER() OVER (ORDER BY w.created_at ASC) = 1 THEN TRUE ELSE FALSE END,
                    w.is_active,
                    :ts,
                    :ts
                FROM warehouses w
                WHERE NOT EXISTS (
                    SELECT 1 FROM fulfillment_warehouses fw
                    WHERE fw.fulfillment_id = CAST(:fc AS uuid) AND fw.warehouse_id = w.id
                )
                """
            ),
            {"fc": default_id, "ts": now},
        )
        bind.execute(
            sa.text(
                """
                INSERT INTO fulfillment_clients (
                    id, fulfillment_id, client_id, is_pilot, is_active, created_at, updated_at
                )
                SELECT
                    gen_random_uuid(),
                    CAST(:fc AS uuid),
                    c.id,
                    FALSE,
                    c.is_active,
                    :ts,
                    :ts
                FROM clients c
                WHERE NOT EXISTS (
                    SELECT 1 FROM fulfillment_clients fc
                    WHERE fc.fulfillment_id = CAST(:fc AS uuid) AND fc.client_id = c.id
                )
                """
            ),
            {"fc": default_id, "ts": now},
        )
        bind.execute(
            sa.text(
                """
                INSERT INTO fulfillment_events (id, fulfillment_id, event_type, actor, message, data, created_at)
                VALUES (
                    :id, CAST(:fc AS uuid), 'SEEDED_DEFAULT', 'system',
                    'Создан default-фулфилмент миграцией 0012', CAST('{}' AS jsonb), :ts
                )
                """
            ),
            {"id": str(uuid.uuid4()), "fc": default_id, "ts": now},
        )


def downgrade() -> None:
    op.drop_index("ix_fulfillment_events_fulfillment_created", table_name="fulfillment_events")
    op.drop_table("fulfillment_events")
    op.drop_index("ix_fulfillment_print_stations_fulfillment", table_name="fulfillment_print_stations")
    op.drop_table("fulfillment_print_stations")
    op.drop_index("ix_fulfillment_users_fulfillment", table_name="fulfillment_users")
    op.drop_table("fulfillment_users")
    op.drop_index("ix_fulfillment_clients_client", table_name="fulfillment_clients")
    op.drop_index("ix_fulfillment_clients_fulfillment", table_name="fulfillment_clients")
    op.drop_table("fulfillment_clients")
    op.drop_index("ix_fulfillment_warehouses_warehouse", table_name="fulfillment_warehouses")
    op.drop_index("ix_fulfillment_warehouses_fulfillment", table_name="fulfillment_warehouses")
    op.drop_table("fulfillment_warehouses")
    op.drop_index("ix_fulfillments_is_default", table_name="fulfillments")
    op.drop_index("ix_fulfillments_status", table_name="fulfillments")
    op.drop_index("ix_fulfillments_code", table_name="fulfillments")
    op.drop_table("fulfillments")
