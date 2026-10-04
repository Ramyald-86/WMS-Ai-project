"""WB packing workflow, CHZ and local order projection

Revision ID: 0003_wb_packing
Revises: 0002_imports_marketplaces
Create Date: 2026-08-22
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0003_wb_packing"
down_revision: Union[str, None] = "0002_imports_marketplaces"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    uuid_type = postgresql.UUID(as_uuid=True)
    jsonb = postgresql.JSONB(astext_type=sa.Text())

    op.create_table(
        "marketplace_orders",
        sa.Column("credential_id", uuid_type, nullable=False),
        sa.Column("client_id", uuid_type, nullable=False),
        sa.Column("provider", sa.String(16), nullable=False, server_default="WB"),
        sa.Column("external_order_id", sa.String(64), nullable=False),
        sa.Column("external_order_num", sa.BigInteger(), nullable=True),
        sa.Column("order_uid", sa.String(128), nullable=True),
        sa.Column("article", sa.String(255), nullable=True),
        sa.Column("barcodes", jsonb, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("required_meta", jsonb, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("optional_meta", jsonb, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("wb_warehouse_id", sa.BigInteger(), nullable=True),
        sa.Column("cargo_type", sa.Integer(), nullable=True),
        sa.Column("local_sku_id", uuid_type, nullable=True),
        sa.Column("supplier_status", sa.String(64), nullable=True),
        sa.Column("wb_status", sa.String(64), nullable=True),
        sa.Column("is_cancellable", sa.Boolean(), nullable=True),
        sa.Column("local_status", sa.String(32), nullable=False, server_default="NEW"),
        sa.Column("supply_id", sa.String(64), nullable=True),
        sa.Column("product_scanned_barcode", sa.String(255), nullable=True),
        sa.Column("product_scanned_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("chz_code", sa.Text(), nullable=True),
        sa.Column("chz_attached_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("label_generated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("label_print_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("assembled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("external_created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("raw_data", jsonb, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("id", uuid_type, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["credential_id"], ["marketplace_credentials.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["client_id"], ["clients.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["local_sku_id"], ["skus.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("uq_marketplace_order_external", "marketplace_orders", ["credential_id", "provider", "external_order_id"], unique=True)
    op.create_index("ix_marketplace_order_client_provider", "marketplace_orders", ["client_id", "provider"])
    op.create_index("ix_marketplace_order_credential_status", "marketplace_orders", ["credential_id", "local_status"])
    op.create_index("uq_marketplace_order_chz", "marketplace_orders", ["credential_id", "chz_code"], unique=True)
    op.create_index("ix_marketplace_orders_credential_id", "marketplace_orders", ["credential_id"])
    op.create_index("ix_marketplace_orders_client_id", "marketplace_orders", ["client_id"])
    op.create_index("ix_marketplace_orders_local_sku_id", "marketplace_orders", ["local_sku_id"])
    op.create_index("ix_marketplace_orders_supply_id", "marketplace_orders", ["supply_id"])

    op.create_table(
        "marketplace_order_events",
        sa.Column("marketplace_order_id", uuid_type, nullable=False),
        sa.Column("event_type", sa.String(64), nullable=False),
        sa.Column("value_text", sa.Text(), nullable=True),
        sa.Column("data", jsonb, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", uuid_type, nullable=False),
        sa.ForeignKeyConstraint(["marketplace_order_id"], ["marketplace_orders.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_marketplace_order_event_order_time", "marketplace_order_events", ["marketplace_order_id", "created_at"])
    op.create_index("ix_marketplace_order_events_marketplace_order_id", "marketplace_order_events", ["marketplace_order_id"])


def downgrade() -> None:
    op.drop_index("ix_marketplace_order_events_marketplace_order_id", table_name="marketplace_order_events")
    op.drop_index("ix_marketplace_order_event_order_time", table_name="marketplace_order_events")
    op.drop_table("marketplace_order_events")
    op.drop_index("ix_marketplace_orders_supply_id", table_name="marketplace_orders")
    op.drop_index("ix_marketplace_orders_local_sku_id", table_name="marketplace_orders")
    op.drop_index("ix_marketplace_orders_client_id", table_name="marketplace_orders")
    op.drop_index("ix_marketplace_orders_credential_id", table_name="marketplace_orders")
    op.drop_index("uq_marketplace_order_chz", table_name="marketplace_orders")
    op.drop_index("ix_marketplace_order_credential_status", table_name="marketplace_orders")
    op.drop_index("ix_marketplace_order_client_provider", table_name="marketplace_orders")
    op.drop_index("uq_marketplace_order_external", table_name="marketplace_orders")
    op.drop_table("marketplace_orders")
