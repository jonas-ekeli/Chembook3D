"""free species

Revision ID: 0006
Revises: 0005
Create Date: 2026-09-30

D69: a node can be a free species (a substrate or fragment that joins or leaves on a
transition). Existing nodes stay pathway nodes. A transition lists the species that join or
leave on it.
"""

import sqlalchemy as sa
from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("nodes", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("kind", sa.String(length=16), server_default="node", nullable=False)
        )
    op.create_table(
        "transition_species",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("transition_id", sa.String(length=32), nullable=False),
        sa.Column("species_id", sa.String(length=32), nullable=False),
        sa.Column("direction", sa.String(length=8), nullable=False),
        sa.Column("count", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint("direction IN ('joins', 'leaves')", name="direction_known"),
        sa.CheckConstraint("count >= 1", name="count_positive"),
        sa.ForeignKeyConstraint(["species_id"], ["nodes.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["transition_id"], ["transitions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("transition_id", "species_id", name="one_entry_per_species"),
    )
    with op.batch_alter_table("transition_species", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_transition_species_species_id"), ["species_id"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_transition_species_transition_id"), ["transition_id"], unique=False
        )


def downgrade() -> None:
    op.drop_table("transition_species")
    with op.batch_alter_table("nodes", schema=None) as batch_op:
        batch_op.drop_column("kind")
