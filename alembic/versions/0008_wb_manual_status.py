"""Persist manual WB workflow status override.

Revision ID: 0008_wb_manual_status
Revises: 0007_marketplace_products

Idempotent hotfix: the column is added only when it is not already present.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0008_wb_manual_status"
down_revision: Union[str, None] = "0007_marketplace_products"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_column(table_name: str, column_name: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return any(c["name"] == column_name for c in inspector.get_columns(table_name))


def upgrade() -> None:
    if not _has_column("marketplace_orders", "manual_status_override"):
        op.add_column(
            "marketplace_orders",
            sa.Column(
                "manual_status_override",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            ),
        )


def downgrade() -> None:
    if _has_column("marketplace_orders", "manual_status_override"):
        op.drop_column("marketplace_orders", "manual_status_override")
