"""turnovers

Revision ID: 0012
Revises: 0011
Create Date: 2026-10-01

D86: saved turnovers, each the TOF of one closed catalytic cycle from the energetic-span
model. Existing investigations start with none.
"""

import sqlalchemy as sa
from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "turnovers",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("path", sa.JSON(), nullable=False),
        sa.Column("level", sa.String(length=80), nullable=True),
        sa.Column("energy_type", sa.String(length=8), nullable=False),
        sa.Column("temperature", sa.Double(), nullable=True),
        sa.Column("compare_id", sa.String(length=32), nullable=True),
        sa.Column("notes", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["compare_id"], ["turnovers.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
    )


def downgrade() -> None:
    op.drop_table("turnovers")
