"""selectivities

Revision ID: 0011
Revises: 0010
Create Date: 2026-10-01

D83: saved selectivities, their named outcomes and the transition states or groups in each
outcome. Existing investigations start with none.
"""

import sqlalchemy as sa
from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "selectivities",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("level", sa.String(length=80), nullable=True),
        sa.Column("energy_type", sa.String(length=8), nullable=False),
        sa.Column("temperature", sa.Double(), nullable=True),
        sa.Column("conformers", sa.String(length=16), nullable=False),
        sa.Column("excess", sa.String(length=8), nullable=False),
        sa.Column("notes", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
    )
    op.create_table(
        "selectivity_outcomes",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("selectivity_id", sa.String(length=32), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("experimental", sa.Double(), nullable=True),
        sa.ForeignKeyConstraint(["selectivity_id"], ["selectivities.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("selectivity_outcomes", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_selectivity_outcomes_selectivity_id"), ["selectivity_id"], unique=False
        )
    op.create_table(
        "selectivity_members",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("outcome_id", sa.String(length=32), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("node_id", sa.String(length=32), nullable=True),
        sa.Column("group_id", sa.String(length=32), nullable=True),
        sa.CheckConstraint("(node_id IS NULL) != (group_id IS NULL)", name="one_member"),
        sa.ForeignKeyConstraint(["group_id"], ["group_nodes.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["node_id"], ["nodes.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["outcome_id"], ["selectivity_outcomes.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("selectivity_members", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_selectivity_members_group_id"), ["group_id"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_selectivity_members_node_id"), ["node_id"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_selectivity_members_outcome_id"), ["outcome_id"], unique=False
        )


def downgrade() -> None:
    op.drop_table("selectivity_members")
    op.drop_table("selectivity_outcomes")
    op.drop_table("selectivities")
