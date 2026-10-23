"""start geometry of optimizations

Revision ID: 0025
Revises: 0024
Create Date: 2026-10-23

D168: an optimization keeps the geometry it started from, so the app knows which optimization
on a node a later one continued (a pre-optimization, D100, D165). Existing calculations start
with none; the repair `start_geometries` reads the copied files once to fill it in where it
matters.
"""

import sqlalchemy as sa
from alembic import op

revision = "0025"
down_revision = "0024"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("calculations", schema=None) as batch_op:
        batch_op.add_column(sa.Column("start_geometry", sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("calculations", schema=None) as batch_op:
        batch_op.drop_column("start_geometry")
