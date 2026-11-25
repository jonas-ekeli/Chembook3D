"""what a merge of two nodes keeps on each calculation

Revision ID: 0038
Revises: 0037
Create Date: 2026-11-25

D262: a calculation merged from another node stores the map from its node's numbering to its
file's (`atom_map`), where it came from (`merged_from`) and the composite levels at which it was
set aside (`set_aside`).
"""

import sqlalchemy as sa
from alembic import op

revision = "0038"
down_revision = "0037"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("calculations", schema=None) as batch_op:
        batch_op.add_column(sa.Column("atom_map", sa.JSON(), nullable=True))
        batch_op.add_column(sa.Column("merged_from", sa.JSON(), nullable=True))
        batch_op.add_column(sa.Column("set_aside", sa.JSON(), nullable=False, server_default="[]"))


def downgrade() -> None:
    with op.batch_alter_table("calculations", schema=None) as batch_op:
        batch_op.drop_column("set_aside")
        batch_op.drop_column("merged_from")
        batch_op.drop_column("atom_map")
