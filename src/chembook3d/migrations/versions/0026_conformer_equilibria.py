"""conformer equilibria

Revision ID: 0026
Revises: 0025
Create Date: 2026-10-23

D171: saved conformer equilibria, each the populations of a set of conformers and the rates of
the TSs between them. Existing investigations start with none.
"""

import sqlalchemy as sa
from alembic import op

revision = "0026"
down_revision = "0025"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "conformer_equilibria",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("members", sa.JSON(), nullable=False),
        sa.Column("level", sa.String(length=80), nullable=True),
        sa.Column("energy_type", sa.String(length=8), nullable=False),
        sa.Column("temperature", sa.Double(), nullable=True),
        sa.Column("start_id", sa.String(length=32), nullable=True),
        sa.Column("notes", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
    )


def downgrade() -> None:
    op.drop_table("conformer_equilibria")
