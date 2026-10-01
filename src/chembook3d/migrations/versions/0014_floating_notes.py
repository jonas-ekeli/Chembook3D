"""floating notes

Revision ID: 0014
Revises: 0013
Create Date: 2026-10-01

D87: a note can float apart from its card, joined to its corner by a line or not, and can be
given a height. Existing notes stay on their corner and fit their text.
"""

import sqlalchemy as sa
from alembic import op

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("node_notes", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("placement", sa.String(length=16), nullable=False, server_default="corner")
        )
        batch_op.add_column(
            sa.Column("offset_x", sa.Integer(), nullable=False, server_default="40")
        )
        batch_op.add_column(
            sa.Column("offset_y", sa.Integer(), nullable=False, server_default="40")
        )
        batch_op.add_column(sa.Column("height", sa.Integer(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("node_notes", schema=None) as batch_op:
        batch_op.drop_column("height")
        batch_op.drop_column("offset_y")
        batch_op.drop_column("offset_x")
        batch_op.drop_column("placement")
