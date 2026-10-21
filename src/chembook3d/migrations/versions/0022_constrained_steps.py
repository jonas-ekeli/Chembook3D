"""constrained optimizations and scans

Revision ID: 0022
Revises: 0021
Create Date: 2026-10-21

D157: a calculation lists what held its structure while it was optimized (a scan, frozen
coordinates or atoms). Existing calculations start with none; the repair `constrained_steps`
reads their copied files once to fill it in.
"""

import sqlalchemy as sa
from alembic import op

revision = "0022"
down_revision = "0021"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("calculations", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("constraints", sa.JSON(), nullable=False, server_default="[]")
        )


def downgrade() -> None:
    with op.batch_alter_table("calculations", schema=None) as batch_op:
        batch_op.drop_column("constraints")
