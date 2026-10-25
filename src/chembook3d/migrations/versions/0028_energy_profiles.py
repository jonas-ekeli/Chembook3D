"""energy profiles

Revision ID: 0028
Revises: 0027
Create Date: 2026-10-25

D190: saved energy profiles, each a named figure of pathways with a tick box per pathway for
whether it is drawn. The pathways an investigation's drawer held become "Profile 1" through
the repair `energy_profiles`.
"""

import sqlalchemy as sa
from alembic import op

revision = "0028"
down_revision = "0027"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "energy_profiles",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("paths", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
    )


def downgrade() -> None:
    op.drop_table("energy_profiles")
