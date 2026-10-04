"""imports, marketplaces and container name

Revision ID: 0002_imports_marketplaces
Revises: 0001_core_inventory
Create Date: 2026-08-22
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0002_imports_marketplaces"
down_revision: Union[str, None] = "0001_core_inventory"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    uuid_type = postgresql.UUID(as_uuid=True)

    op.add_column("containers", sa.Column("name", sa.String(255), nullable=True))
    op.execute("UPDATE containers SET name = barcode WHERE name IS NULL")

    op.create_table(
        "marketplace_credentials",
        sa.Column("client_id", uuid_type, nullable=False),
        sa.Column("provider", sa.String(16), nullable=False),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("external_client_id", sa.String(255), nullable=True),
        sa.Column("encrypted_api_key", sa.Text(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("last_tested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_test_ok", sa.Boolean(), nullable=True),
        sa.Column("last_test_message", sa.String(500), nullable=True),
        sa.Column("id", uuid_type, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["client_id"], ["clients.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("uq_marketplace_credential", "marketplace_credentials", ["client_id", "provider", "name"], unique=True)
    op.create_index("ix_marketplace_credential_client_provider", "marketplace_credentials", ["client_id", "provider"])
    op.create_index("ix_marketplace_credentials_client_id", "marketplace_credentials", ["client_id"])

    op.create_table(
        "import_batches",
        sa.Column("client_id", uuid_type, nullable=False),
        sa.Column("warehouse_id", uuid_type, nullable=False),
        sa.Column("file_name", sa.String(255), nullable=False),
        sa.Column("checksum", sa.String(64), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="COMPLETED"),
        sa.Column("rows_total", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("rows_imported", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("rows_skipped", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_skus", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_containers", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_locations", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("operation_id", uuid_type, nullable=True),
        sa.Column("performed_by", sa.String(255), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("id", uuid_type, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["client_id"], ["clients.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["warehouse_id"], ["warehouses.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["operation_id"], ["inventory_operations.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_import_batch_client_warehouse", "import_batches", ["client_id", "warehouse_id"])
    op.create_index("ix_import_batch_checksum", "import_batches", ["client_id", "warehouse_id", "checksum"])


def downgrade() -> None:
    op.drop_index("ix_import_batch_checksum", table_name="import_batches")
    op.drop_index("ix_import_batch_client_warehouse", table_name="import_batches")
    op.drop_table("import_batches")
    op.drop_index("ix_marketplace_credentials_client_id", table_name="marketplace_credentials")
    op.drop_index("ix_marketplace_credential_client_provider", table_name="marketplace_credentials")
    op.drop_index("uq_marketplace_credential", table_name="marketplace_credentials")
    op.drop_table("marketplace_credentials")
    op.drop_column("containers", "name")
