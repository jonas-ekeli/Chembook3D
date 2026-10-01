"""steric profiles

Revision ID: 0010
Revises: 0009
Create Date: 2026-10-01

D81: named steric profiles for buried volume and steric maps keep their parameters and,
per node, the centre, orientation and excluded atoms with the last result. Existing
investigations start with none.
"""

import sqlalchemy as sa
from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "steric_profiles",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("radius", sa.Double(), nullable=False),
        sa.Column("radii", sa.String(length=16), nullable=False),
        sa.Column("radii_scale", sa.Double(), nullable=False),
        sa.Column("include_hydrogens", sa.Boolean(), nullable=False),
        sa.Column("mesh", sa.Double(), nullable=False),
        sa.Column("map_limit", sa.Double(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
    )
    op.create_table(
        "steric_profile_atoms",
        sa.Column("profile_id", sa.String(length=32), nullable=False),
        sa.Column("node_id", sa.String(length=32), nullable=False),
        sa.Column("centre", sa.JSON(), nullable=False),
        sa.Column("z_axis", sa.JSON(), nullable=False),
        sa.Column("xz_plane", sa.JSON(), nullable=False),
        sa.Column("excluded", sa.JSON(), nullable=False),
        sa.Column("result", sa.JSON(), nullable=True),
        sa.Column("result_inputs", sa.JSON(), nullable=True),
        sa.Column("computed_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["node_id"], ["nodes.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["profile_id"], ["steric_profiles.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("profile_id", "node_id"),
    )
    with op.batch_alter_table("steric_profile_atoms", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_steric_profile_atoms_node_id"), ["node_id"], unique=False
        )


def downgrade() -> None:
    op.drop_table("steric_profile_atoms")
    op.drop_table("steric_profiles")
