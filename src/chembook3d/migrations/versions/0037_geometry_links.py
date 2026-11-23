"""the calculation a single point or frequency job is at

Revision ID: 0037
Revises: 0036
Create Date: 2026-11-23

D255: a single point or frequency job stores the calculation whose geometry it is at, so it is
no longer worked out again from coordinates on every read. Existing calculations are linked
once when the investigation opens (the repair `geometry_links`), by the rule they were read
with, so no value moves.
"""

import sqlalchemy as sa
from alembic import op

revision = "0037"
down_revision = "0036"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("calculations", schema=None) as batch_op:
        batch_op.add_column(sa.Column("geometry_source_id", sa.String(length=32), nullable=True))
        batch_op.create_foreign_key(
            "fk_calculations_geometry_source_id",
            "calculations",
            ["geometry_source_id"],
            ["id"],
            ondelete="SET NULL",
        )


def downgrade() -> None:
    with op.batch_alter_table("calculations", schema=None) as batch_op:
        batch_op.drop_constraint("fk_calculations_geometry_source_id", type_="foreignkey")
        batch_op.drop_column("geometry_source_id")
