"""view state

Revision ID: 0018
Revises: 0017
Create Date: 2026-10-08

D105: an investigation remembers what it showed when last used (energy level and type,
reference, filters, expanded groups, the energy drawer's pathways). Existing investigations
start with nothing remembered and open as before.
"""

import sqlalchemy as sa
from alembic import op

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("investigation_info", schema=None) as batch_op:
        batch_op.add_column(sa.Column("view_state", sa.JSON(), nullable=False, server_default="{}"))


def downgrade() -> None:
    with op.batch_alter_table("investigation_info", schema=None) as batch_op:
        batch_op.drop_column("view_state")
