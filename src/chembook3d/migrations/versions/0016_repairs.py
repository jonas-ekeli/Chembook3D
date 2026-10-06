"""repairs

Revision ID: 0016
Revises: 0015
Create Date: 2026-10-06

D100: an investigation remembers which one-off repairs (services/repairs.py) it has had. An
existing investigation has had none, so the geometry-level repair runs when it is opened after
this migration, on a database that was backed up first.
"""

import sqlalchemy as sa
from alembic import op

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("investigation_info", schema=None) as batch_op:
        batch_op.add_column(sa.Column("repairs", sa.JSON(), nullable=False, server_default="[]"))


def downgrade() -> None:
    with op.batch_alter_table("investigation_info", schema=None) as batch_op:
        batch_op.drop_column("repairs")
