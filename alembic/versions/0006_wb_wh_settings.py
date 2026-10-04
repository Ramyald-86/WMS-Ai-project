"""Add per-cabinet Wildberries warehouse visibility settings.

Revision ID: 0006_wb_wh_settings
Revises: 0005_marketplace_unique
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0006_wb_wh_settings"
down_revision: Union[str, None] = "0005_marketplace_unique"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "marketplace_credentials",
        sa.Column("warehouse_settings", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
    )


def downgrade() -> None:
    op.drop_column("marketplace_credentials", "warehouse_settings")
