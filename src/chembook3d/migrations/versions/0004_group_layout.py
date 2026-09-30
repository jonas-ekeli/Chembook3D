"""group layout

Revision ID: 0004
Revises: 0003
Create Date: 2026-09-30

Groups remember how their members are laid out when expanded (FR-GRP-07, A21). Existing
groups keep the grid they had.
"""

import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("group_nodes", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("layout", sa.String(length=16), server_default="grid", nullable=False)
        )


def downgrade() -> None:
    with op.batch_alter_table("group_nodes", schema=None) as batch_op:
        batch_op.drop_column("layout")
