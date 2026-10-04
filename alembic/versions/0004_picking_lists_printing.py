"""Picking lists, PACKED status and print queue

Revision ID: 0004_picking_lists_printing
Revises: 0003_wb_packing
Create Date: 2026-08-22
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0004_picking_lists_printing"
down_revision = "0003_wb_packing"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("marketplace_orders", sa.Column("picking_list_generated_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("marketplace_orders", sa.Column("last_picking_list_id", sa.String(length=64), nullable=True))
    op.add_column("marketplace_orders", sa.Column("packed_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index("ix_marketplace_orders_last_picking_list_id", "marketplace_orders", ["last_picking_list_id"], unique=False)

    op.execute("UPDATE marketplace_orders SET local_status='PACKED', packed_at=COALESCE(assembled_at, label_generated_at, updated_at) WHERE local_status='COLLECTED'")
    op.execute("UPDATE marketplace_orders SET picking_list_generated_at=COALESCE(picking_list_generated_at, updated_at) WHERE local_status IN ('PICKING','PACKED')")

    op.create_table(
        "marketplace_picking_lists",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("credential_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("client_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("warehouse_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("file_name", sa.String(length=255), nullable=False),
        sa.Column("order_ids", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("rows", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("supply_ids", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_by", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["client_id"], ["clients.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["credential_id"], ["marketplace_credentials.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["warehouse_id"], ["warehouses.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_marketplace_picking_lists_credential_created", "marketplace_picking_lists", ["credential_id", "created_at"], unique=False)
    op.create_index("ix_marketplace_picking_lists_client_id", "marketplace_picking_lists", ["client_id"], unique=False)
    op.create_index("ix_marketplace_picking_lists_credential_id", "marketplace_picking_lists", ["credential_id"], unique=False)
    op.create_index("ix_marketplace_picking_lists_warehouse_id", "marketplace_picking_lists", ["warehouse_id"], unique=False)

    op.create_table(
        "print_jobs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("client_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("station_code", sa.String(length=64), nullable=False),
        sa.Column("document_type", sa.String(length=64), nullable=False),
        sa.Column("object_type", sa.String(length=64), nullable=True),
        sa.Column("object_id", sa.String(length=128), nullable=True),
        sa.Column("file_name", sa.String(length=255), nullable=False),
        sa.Column("content_type", sa.String(length=128), nullable=False),
        sa.Column("payload", sa.LargeBinary(), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["client_id"], ["clients.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_print_jobs_client_id", "print_jobs", ["client_id"], unique=False)
    op.create_index("ix_print_jobs_station_code", "print_jobs", ["station_code"], unique=False)
    op.create_index("ix_print_jobs_station_status_created", "print_jobs", ["station_code", "status", "created_at"], unique=False)


def downgrade():
    op.drop_index("ix_print_jobs_station_status_created", table_name="print_jobs")
    op.drop_index("ix_print_jobs_station_code", table_name="print_jobs")
    op.drop_index("ix_print_jobs_client_id", table_name="print_jobs")
    op.drop_table("print_jobs")

    op.drop_index("ix_marketplace_picking_lists_warehouse_id", table_name="marketplace_picking_lists")
    op.drop_index("ix_marketplace_picking_lists_credential_id", table_name="marketplace_picking_lists")
    op.drop_index("ix_marketplace_picking_lists_client_id", table_name="marketplace_picking_lists")
    op.drop_index("ix_marketplace_picking_lists_credential_created", table_name="marketplace_picking_lists")
    op.drop_table("marketplace_picking_lists")

    op.drop_index("ix_marketplace_orders_last_picking_list_id", table_name="marketplace_orders")
    op.drop_column("marketplace_orders", "packed_at")
    op.drop_column("marketplace_orders", "last_picking_list_id")
    op.drop_column("marketplace_orders", "picking_list_generated_at")
