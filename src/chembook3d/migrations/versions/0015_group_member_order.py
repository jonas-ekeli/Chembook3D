"""group member order

Revision ID: 0015
Revises: 0014
Create Date: 2026-10-02

D89: the members of a group are kept in an order the user can change, which the grid, column
and row layouts follow. Existing members keep the order they had: the order they were made in.
"""

import sqlalchemy as sa
from alembic import op

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("nodes", schema=None) as batch_op:
        batch_op.add_column(sa.Column("group_position", sa.Integer(), nullable=True))
    op.execute(
        "UPDATE nodes SET group_position = (SELECT COUNT(*) FROM nodes AS other"
        " WHERE other.group_id = nodes.group_id AND other.seq <= nodes.seq)"
        " WHERE group_id IS NOT NULL"
    )


def downgrade() -> None:
    with op.batch_alter_table("nodes", schema=None) as batch_op:
        batch_op.drop_column("group_position")
