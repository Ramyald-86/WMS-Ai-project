"""Sale-first warehouse reservations for FBS orders.

Revision ID: 0010_sale_first_reserve
Revises: 0009_wms_reliability
"""
from __future__ import annotations

from typing import Union

from alembic import op

revision: str = "0010_sale_first_reserve"
down_revision: Union[str, None] = "0009_wms_reliability"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # The legacy reservation table was created lazily by the application rather than by
    # Alembic. Create a compatible table when upgrading a clean installation, then evolve
    # it in-place on existing warehouses.
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS fc_stock_reservations (
            id VARCHAR(36) PRIMARY KEY,
            order_id VARCHAR(36) NOT NULL,
            external_order_id VARCHAR(64) NULL,
            picking_list_id VARCHAR(36) NULL,
            client_id VARCHAR(36) NOT NULL,
            warehouse_id VARCHAR(36) NOT NULL,
            sku_id VARCHAR(36) NOT NULL,
            source_location_id VARCHAR(36) NULL,
            source_container_id VARCHAR(36) NULL,
            quantity NUMERIC(18,3) NOT NULL DEFAULT 1,
            status VARCHAR(32) NOT NULL DEFAULT 'ACTIVE',
            reservation_mode VARCHAR(32) NOT NULL DEFAULT 'LEGACY',
            source_bucket VARCHAR(24) NULL,
            stock_core_order_id VARCHAR(36) NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            consumed_at TIMESTAMPTZ NULL,
            receiving_at TIMESTAMPTZ NULL,
            released_at TIMESTAMPTZ NULL,
            note TEXT NULL
        )
        """
    )
    op.execute("ALTER TABLE fc_stock_reservations ALTER COLUMN source_location_id DROP NOT NULL")
    op.execute("ALTER TABLE fc_stock_reservations ADD COLUMN IF NOT EXISTS reservation_mode VARCHAR(32) NOT NULL DEFAULT 'LEGACY'")
    op.execute("ALTER TABLE fc_stock_reservations ADD COLUMN IF NOT EXISTS source_bucket VARCHAR(24) NULL")
    op.execute("ALTER TABLE fc_stock_reservations ADD COLUMN IF NOT EXISTS stock_core_order_id VARCHAR(36) NULL")
    op.execute("DROP INDEX IF EXISTS uq_fc_stock_reservations_live_order")
    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS uq_fc_stock_reservations_live_order
        ON fc_stock_reservations(order_id)
        WHERE status IN ('SALE_RESERVED','ACTIVE','CONSUMING','CANCELLING','PACKING_WRITE_OFF')
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_fc_stock_reservations_sale_pool
        ON fc_stock_reservations(client_id, warehouse_id, sku_id, status)
        WHERE status='SALE_RESERVED'
        """
    )


def downgrade() -> None:
    # Data-safe downgrade: keep the nullable address and mode column because SALE_RESERVED
    # rows cannot be losslessly converted to the legacy address-bound model.
    op.execute("DROP INDEX IF EXISTS ix_fc_stock_reservations_sale_pool")
    op.execute("DROP INDEX IF EXISTS uq_fc_stock_reservations_live_order")
    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS uq_fc_stock_reservations_live_order
        ON fc_stock_reservations(order_id)
        WHERE status IN ('ACTIVE','CONSUMING','CANCELLING')
        """
    )
