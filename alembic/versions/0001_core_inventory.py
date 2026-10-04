"""core inventory

Revision ID: 0001_core_inventory
Revises:
Create Date: 2026-08-20
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0001_core_inventory"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    uuid_type = postgresql.UUID(as_uuid=True)

    op.create_table(
        "clients",
        sa.Column("code", sa.String(64), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("id", uuid_type, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("code"),
    )
    op.create_index("ix_clients_code", "clients", ["code"])

    op.create_table(
        "warehouses",
        sa.Column("code", sa.String(64), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("id", uuid_type, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("code"),
    )
    op.create_index("ix_warehouses_code", "warehouses", ["code"])

    op.create_table(
        "locations",
        sa.Column("warehouse_id", uuid_type, nullable=False),
        sa.Column("parent_id", uuid_type, nullable=True),
        sa.Column("code", sa.String(128), nullable=False),
        sa.Column("barcode", sa.String(255), nullable=False),
        sa.Column("name", sa.String(255), nullable=True),
        sa.Column("location_type", sa.String(32), nullable=False, server_default="CELL"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("id", uuid_type, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["warehouse_id"], ["warehouses.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["parent_id"], ["locations.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_locations_warehouse_id", "locations", ["warehouse_id"])
    op.create_index("ix_locations_parent_id", "locations", ["parent_id"])
    op.create_index("uq_location_warehouse_code", "locations", ["warehouse_id", "code"], unique=True)
    op.create_index("uq_location_warehouse_barcode", "locations", ["warehouse_id", "barcode"], unique=True)

    op.create_table(
        "skus",
        sa.Column("client_id", uuid_type, nullable=False),
        sa.Column("sku_code", sa.String(128), nullable=False),
        sa.Column("article", sa.String(128), nullable=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("size", sa.String(128), nullable=True),
        sa.Column("barcode", sa.String(255), nullable=True),
        sa.Column("unit", sa.String(32), nullable=False, server_default="PCS"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("id", uuid_type, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["client_id"], ["clients.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_skus_client_id", "skus", ["client_id"])
    op.create_index("uq_sku_client_code", "skus", ["client_id", "sku_code"], unique=True)
    op.create_index("ix_sku_client_barcode", "skus", ["client_id", "barcode"])
    op.create_index("ix_sku_client_article_size", "skus", ["client_id", "article", "size"])

    op.create_table(
        "containers",
        sa.Column("client_id", uuid_type, nullable=False),
        sa.Column("warehouse_id", uuid_type, nullable=False),
        sa.Column("location_id", uuid_type, nullable=True),
        sa.Column("barcode", sa.String(255), nullable=False),
        sa.Column("container_type", sa.String(32), nullable=False, server_default="BOX"),
        sa.Column("status", sa.String(32), nullable=False, server_default="ACTIVE"),
        sa.Column("id", uuid_type, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["client_id"], ["clients.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["warehouse_id"], ["warehouses.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["location_id"], ["locations.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_containers_client_id", "containers", ["client_id"])
    op.create_index("ix_containers_warehouse_id", "containers", ["warehouse_id"])
    op.create_index("ix_containers_location_id", "containers", ["location_id"])
    op.create_index("uq_container_client_barcode", "containers", ["client_id", "barcode"], unique=True)

    op.create_table(
        "inventory_balances",
        sa.Column("bucket_key", sa.String(255), nullable=False),
        sa.Column("client_id", uuid_type, nullable=False),
        sa.Column("warehouse_id", uuid_type, nullable=False),
        sa.Column("sku_id", uuid_type, nullable=False),
        sa.Column("location_id", uuid_type, nullable=False),
        sa.Column("container_id", uuid_type, nullable=True),
        sa.Column("quantity", sa.Numeric(18, 3), nullable=False, server_default="0"),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("id", uuid_type, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["client_id"], ["clients.id"]),
        sa.ForeignKeyConstraint(["warehouse_id"], ["warehouses.id"]),
        sa.ForeignKeyConstraint(["sku_id"], ["skus.id"]),
        sa.ForeignKeyConstraint(["location_id"], ["locations.id"]),
        sa.ForeignKeyConstraint(["container_id"], ["containers.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("uq_inventory_bucket_key", "inventory_balances", ["bucket_key"], unique=True)
    op.create_index("ix_inventory_client_sku", "inventory_balances", ["client_id", "sku_id"])
    op.create_index("ix_inventory_location", "inventory_balances", ["location_id"])
    op.create_index("ix_inventory_container", "inventory_balances", ["container_id"])

    op.create_table(
        "inventory_operations",
        sa.Column("client_id", uuid_type, nullable=False),
        sa.Column("warehouse_id", uuid_type, nullable=False),
        sa.Column("operation_type", sa.String(32), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="COMPLETED"),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column("performed_by", sa.String(255), nullable=True),
        sa.Column("reference", sa.String(255), nullable=True),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", uuid_type, nullable=False),
        sa.ForeignKeyConstraint(["client_id"], ["clients.id"]),
        sa.ForeignKeyConstraint(["warehouse_id"], ["warehouses.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_inventory_operations_client_id", "inventory_operations", ["client_id"])
    op.create_index("ix_inventory_operations_warehouse_id", "inventory_operations", ["warehouse_id"])
    op.create_index("uq_operation_client_idempotency", "inventory_operations", ["client_id", "idempotency_key"], unique=True)
    op.create_index("ix_operation_created_at", "inventory_operations", ["created_at"])
    op.create_index("ix_operation_type", "inventory_operations", ["operation_type"])

    op.create_table(
        "inventory_operation_lines",
        sa.Column("operation_id", uuid_type, nullable=False),
        sa.Column("sku_id", uuid_type, nullable=False),
        sa.Column("quantity", sa.Numeric(18, 3), nullable=False),
        sa.Column("source_location_id", uuid_type, nullable=True),
        sa.Column("source_container_id", uuid_type, nullable=True),
        sa.Column("destination_location_id", uuid_type, nullable=True),
        sa.Column("destination_container_id", uuid_type, nullable=True),
        sa.Column("reason", sa.String(255), nullable=True),
        sa.Column("id", uuid_type, nullable=False),
        sa.ForeignKeyConstraint(["operation_id"], ["inventory_operations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["sku_id"], ["skus.id"]),
        sa.ForeignKeyConstraint(["source_location_id"], ["locations.id"]),
        sa.ForeignKeyConstraint(["source_container_id"], ["containers.id"]),
        sa.ForeignKeyConstraint(["destination_location_id"], ["locations.id"]),
        sa.ForeignKeyConstraint(["destination_container_id"], ["containers.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_operation_line_operation", "inventory_operation_lines", ["operation_id"])
    op.create_index("ix_operation_line_sku", "inventory_operation_lines", ["sku_id"])


def downgrade() -> None:
    op.drop_table("inventory_operation_lines")
    op.drop_table("inventory_operations")
    op.drop_table("inventory_balances")
    op.drop_table("containers")
    op.drop_table("skus")
    op.drop_table("locations")
    op.drop_table("warehouses")
    op.drop_table("clients")
