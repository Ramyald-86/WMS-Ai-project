"""Durable idempotency ledger for marketplace stock publication.

Revision ID: 0009_wms_reliability
Revises: 0008_wb_manual_status
"""
from __future__ import annotations

from typing import Union

from alembic import op

revision: str = "0009_wms_reliability"
down_revision: Union[str, None] = "0008_wb_manual_status"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS fc_wb_stock_publication_events (
            client_id VARCHAR(36) NOT NULL,
            warehouse_id VARCHAR(36) NOT NULL,
            idempotency_key VARCHAR(255) NOT NULL,
            barcode VARCHAR(255) NOT NULL,
            wb_warehouse_id BIGINT NULL,
            mode VARCHAR(16) NOT NULL DEFAULT 'ADD',
            requested_quantity INTEGER NOT NULL DEFAULT 0,
            resolved_amount INTEGER NULL,
            status VARCHAR(32) NOT NULL DEFAULT 'PENDING',
            last_error TEXT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            PRIMARY KEY (client_id, warehouse_id, idempotency_key)
        )
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_fc_wb_stock_pub_events_barcode
        ON fc_wb_stock_publication_events(client_id, warehouse_id, barcode, updated_at DESC)
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_fc_wb_stock_pub_events_barcode")
    op.execute("DROP TABLE IF EXISTS fc_wb_stock_publication_events")
