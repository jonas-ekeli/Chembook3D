"""concentration of a free species

Revision ID: 0024
Revises: 0023
Create Date: 2026-10-21

D160: a free species may be given its own concentration (1 M, neat solvent or any molarity),
which its G and G_qh are taken at wherever it balances a pathway. Existing species have none,
so they keep following the investigation's standard state and no value shifts.
"""

import sqlalchemy as sa
from alembic import op

revision = "0024"
down_revision = "0023"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("nodes", schema=None) as batch_op:
        batch_op.add_column(sa.Column("concentration", sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("nodes", schema=None) as batch_op:
        batch_op.drop_column("concentration")
