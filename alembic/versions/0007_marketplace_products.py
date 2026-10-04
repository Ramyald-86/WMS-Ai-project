"""Add marketplace product-card projection.

Revision ID: 0007_marketplace_products
Revises: 0006_wb_wh_settings
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0007_marketplace_products"
down_revision: Union[str, None] = "0006_wb_wh_settings"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "marketplace_products",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("credential_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("client_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("provider", sa.String(length=16), nullable=False),
        sa.Column("external_product_id", sa.BigInteger(), nullable=False),
        sa.Column("vendor_code", sa.String(length=255), nullable=True),
        sa.Column("title", sa.String(length=500), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("brand", sa.String(length=255), nullable=True),
        sa.Column("subject_name", sa.String(length=255), nullable=True),
        sa.Column("photos", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("sizes", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("barcodes", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("local_sku_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("raw_data", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["client_id"], ["clients.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["credential_id"], ["marketplace_credentials.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["local_sku_id"], ["skus.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("uq_marketplace_product_external", "marketplace_products", ["credential_id", "provider", "external_product_id"], unique=True)
    op.create_index("ix_marketplace_product_client_provider", "marketplace_products", ["client_id", "provider"], unique=False)
    op.create_index("ix_marketplace_product_vendor_code", "marketplace_products", ["credential_id", "vendor_code"], unique=False)
    op.create_index(op.f("ix_marketplace_products_credential_id"), "marketplace_products", ["credential_id"], unique=False)
    op.create_index(op.f("ix_marketplace_products_client_id"), "marketplace_products", ["client_id"], unique=False)
    op.create_index(op.f("ix_marketplace_products_local_sku_id"), "marketplace_products", ["local_sku_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_marketplace_products_local_sku_id"), table_name="marketplace_products")
    op.drop_index(op.f("ix_marketplace_products_client_id"), table_name="marketplace_products")
    op.drop_index(op.f("ix_marketplace_products_credential_id"), table_name="marketplace_products")
    op.drop_index("ix_marketplace_product_vendor_code", table_name="marketplace_products")
    op.drop_index("ix_marketplace_product_client_provider", table_name="marketplace_products")
    op.drop_index("uq_marketplace_product_external", table_name="marketplace_products")
    op.drop_table("marketplace_products")
