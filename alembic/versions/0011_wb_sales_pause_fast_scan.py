"""WB sales pause controls and local hot-scan indexes.

Revision ID: 0011_wb_sales_pause_fast_scan
Revises: 0010_sale_first_reserve
"""
from __future__ import annotations

from typing import Union

from alembic import op

revision: str = "0011_wb_sales_pause_fast_scan"
down_revision: Union[str, None] = "0010_sale_first_reserve"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS fc_wb_sales_pauses (
            client_id VARCHAR(36) NOT NULL,
            warehouse_id VARCHAR(36) NOT NULL,
            sku_id VARCHAR(36) NOT NULL,
            paused BOOLEAN NOT NULL DEFAULT TRUE,
            paused_by VARCHAR(255) NULL,
            reason TEXT NULL,
            paused_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (client_id, warehouse_id, sku_id)
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_fc_wb_sales_pauses_active "
        "ON fc_wb_sales_pauses(client_id, warehouse_id, sku_id) WHERE paused=TRUE"
    )
    # Product scan is a local-DB hot path. These two indexes make barcode -> oldest
    # fulfilment order deterministic without waiting on the marketplace network.
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_marketplace_orders_wb_pack_hot
        ON marketplace_orders(credential_id, local_status, local_sku_id, external_created_at, created_at)
        WHERE provider='WB' AND local_status IN ('NEW','PICKING')
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_marketplace_orders_barcodes_gin "
        "ON marketplace_orders USING GIN (barcodes)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_marketplace_orders_barcodes_gin")
    op.execute("DROP INDEX IF EXISTS ix_marketplace_orders_wb_pack_hot")
    # Preserve pause state on downgrade. It is safer to keep a local sales block than to
    # silently republish inventory merely because application files were rolled back.
    op.execute("DROP INDEX IF EXISTS ix_fc_wb_sales_pauses_active")
