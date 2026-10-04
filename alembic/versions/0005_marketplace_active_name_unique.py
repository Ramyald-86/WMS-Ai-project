"""Allow reusing marketplace cabinet names after API disconnect.

Revision ID: 0005_marketplace_unique
Revises: 0004_picking_lists_printing
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0005_marketplace_unique"
down_revision: Union[str, None] = "0004_picking_lists_printing"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_index("uq_marketplace_credential", table_name="marketplace_credentials")
    op.create_index(
        "uq_marketplace_credential",
        "marketplace_credentials",
        ["client_id", "provider", "name"],
        unique=True,
        postgresql_where=sa.text("is_active = true"),
    )


def downgrade() -> None:
    # If multiple inactive rows reuse the same name, restoring the old global
    # uniqueness would fail. Keep downgrade explicit so an operator must first
    # resolve duplicates instead of silently deleting history.
    op.drop_index("uq_marketplace_credential", table_name="marketplace_credentials")
    op.create_index(
        "uq_marketplace_credential",
        "marketplace_credentials",
        ["client_id", "provider", "name"],
        unique=True,
    )
