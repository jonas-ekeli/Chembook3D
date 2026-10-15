"""IRC parts of TS nodes

Revision ID: 0021
Revises: 0020
Create Date: 2026-10-15

D131: a TS node holds its IRC calculations (the path in each direction and the jobs on its end
points) as calculations marked with their part and direction, which never count as the node's
own. Existing calculations are not IRC parts.
"""

import sqlalchemy as sa
from alembic import op

revision = "0021"
down_revision = "0020"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("calculations", schema=None) as batch_op:
        batch_op.add_column(sa.Column("irc_part", sa.String(length=16), nullable=True))
        batch_op.add_column(sa.Column("irc_direction", sa.String(length=16), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("calculations", schema=None) as batch_op:
        batch_op.drop_column("irc_direction")
        batch_op.drop_column("irc_part")
