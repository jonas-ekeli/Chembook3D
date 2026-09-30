"""transition sides

Revision ID: 0008
Revises: 0007
Create Date: 2026-09-30

A transition keeps the side of each box its arrow leaves from and arrives at (D76). Existing
transitions keep the old drawing: out on the right, in on the left.
"""

import sqlalchemy as sa
from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("transitions", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("source_side", sa.String(length=8), nullable=False, server_default="right")
        )
        batch_op.add_column(
            sa.Column("target_side", sa.String(length=8), nullable=False, server_default="left")
        )


def downgrade() -> None:
    with op.batch_alter_table("transitions", schema=None) as batch_op:
        batch_op.drop_column("target_side")
        batch_op.drop_column("source_side")
