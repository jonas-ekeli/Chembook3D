"""removed scan path points

Revision ID: 0019
Revises: 0018
Create Date: 2026-10-09

D117: a scan path's points can be left out of the path by hand. The calculation lists the
points removed; existing calculations start with none removed and show as before.
"""

import sqlalchemy as sa
from alembic import op

revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("calculations", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("removed_points", sa.JSON(), nullable=False, server_default="[]")
        )


def downgrade() -> None:
    with op.batch_alter_table("calculations", schema=None) as batch_op:
        batch_op.drop_column("removed_points")
