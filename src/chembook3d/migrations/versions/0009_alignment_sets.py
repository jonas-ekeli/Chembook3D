"""alignment sets

Revision ID: 0009
Revises: 0008
Create Date: 2026-09-30

D80: named alignment sets for overlays keep one list of atom numbers per node. Existing
investigations start with none.
"""

import sqlalchemy as sa
from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "alignment_sets",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
    )
    op.create_table(
        "alignment_set_atoms",
        sa.Column("set_id", sa.String(length=32), nullable=False),
        sa.Column("node_id", sa.String(length=32), nullable=False),
        sa.Column("atoms", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["node_id"], ["nodes.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["set_id"], ["alignment_sets.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("set_id", "node_id"),
    )
    with op.batch_alter_table("alignment_set_atoms", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_alignment_set_atoms_node_id"), ["node_id"], unique=False
        )


def downgrade() -> None:
    op.drop_table("alignment_set_atoms")
    op.drop_table("alignment_sets")
