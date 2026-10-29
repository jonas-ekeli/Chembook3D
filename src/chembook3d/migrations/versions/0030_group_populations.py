"""population % on a group's members

Revision ID: 0030
Revises: 0029
Create Date: 2026-10-29

D197: a group can show its members' Boltzmann populations on their cards. Existing groups
start without.
"""

import sqlalchemy as sa
from alembic import op

revision = "0030"
down_revision = "0029"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("group_nodes", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("show_populations", sa.Boolean(), nullable=False, server_default=sa.text("0"))
        )


def downgrade() -> None:
    with op.batch_alter_table("group_nodes", schema=None) as batch_op:
        batch_op.drop_column("show_populations")
