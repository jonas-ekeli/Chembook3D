"""a TS guess's mode check on its calculation

Revision ID: 0034
Revises: 0033
Create Date: 2026-11-19

D242: the check of a TS guess's saddle (D238 (b)), a Hessian over the reacting region only, is
kept on the calculation it is imported as: its imaginary wavenumbers and the first one's mode,
apart from the frequencies so it never gives a ZPE or G.
"""

import sqlalchemy as sa
from alembic import op

revision = "0034"
down_revision = "0033"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("calculation_results", schema=None) as batch_op:
        batch_op.add_column(sa.Column("mode_check", sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("calculation_results", schema=None) as batch_op:
        batch_op.drop_column("mode_check")
