"""thermochemistry settings of the investigation

Revision ID: 0023
Revises: 0022
Create Date: 2026-10-21

D159: the G_qh temperature and cutoff and the standard state of G and G_qh belong to the
investigation, so a synced copy shows the same values and a change is in its history. Existing
investigations start with none; the repair `thermochemistry` sets them once from the app's
settings when the investigation is next opened, so no value shifts.
"""

import sqlalchemy as sa
from alembic import op

revision = "0023"
down_revision = "0022"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("investigation_info", schema=None) as batch_op:
        batch_op.add_column(sa.Column("qh_temperature", sa.Double(), nullable=True))
        batch_op.add_column(sa.Column("qh_cutoff", sa.Double(), nullable=True))
        batch_op.add_column(sa.Column("standard_state", sa.String(length=16), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("investigation_info", schema=None) as batch_op:
        batch_op.drop_column("standard_state")
        batch_op.drop_column("qh_cutoff")
        batch_op.drop_column("qh_temperature")
