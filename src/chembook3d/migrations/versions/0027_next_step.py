"""the next step of a conformer equilibrium

Revision ID: 0027
Revises: 0026
Create Date: 2026-10-23

D173: a conformer equilibrium can name the TSs of the step that follows, for the Curtin–Hammett
check. Existing ones start with none.
"""

import sqlalchemy as sa
from alembic import op

revision = "0027"
down_revision = "0026"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("conformer_equilibria", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("next_ids", sa.JSON(), nullable=False, server_default=sa.text("'[]'"))
        )


def downgrade() -> None:
    with op.batch_alter_table("conformer_equilibria", schema=None) as batch_op:
        batch_op.drop_column("next_ids")
