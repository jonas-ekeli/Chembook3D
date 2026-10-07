"""selectivity reference

Revision ID: 0017
Revises: 0016
Create Date: 2026-10-07

D103: a selectivity may name a reference node or group; each transition state is then
balanced by the free species along its route from it (D72). Existing selectivities have
none and are computed as before.
"""

import sqlalchemy as sa
from alembic import op

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("selectivities", schema=None) as batch_op:
        batch_op.add_column(sa.Column("reference_id", sa.String(length=32), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("selectivities", schema=None) as batch_op:
        batch_op.drop_column("reference_id")
