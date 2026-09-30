"""node view rotation

Revision ID: 0007
Revises: 0006
Create Date: 2026-09-30

A node can keep the orientation saved from its 3D view, so its structure-mode card is drawn
the same way. Existing nodes keep the default orientation.
"""

import sqlalchemy as sa
from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("nodes", schema=None) as batch_op:
        batch_op.add_column(sa.Column("view_rotation", sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("nodes", schema=None) as batch_op:
        batch_op.drop_column("view_rotation")
