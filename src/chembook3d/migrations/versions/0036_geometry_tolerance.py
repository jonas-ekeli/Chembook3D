"""the investigation's own geometry tolerance

Revision ID: 0036
Revises: 0035
Create Date: 2026-11-22

D252: the RMSD within which a calculation is at a geometry (ID-7) is kept in the investigation,
so matching does not depend on the computer. Empty until the repair `geometry_tolerance` sets
the app's value.
"""

import sqlalchemy as sa
from alembic import op

revision = "0036"
down_revision = "0035"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("investigation_info", schema=None) as batch_op:
        batch_op.add_column(sa.Column("geometry_tolerance", sa.Double(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("investigation_info", schema=None) as batch_op:
        batch_op.drop_column("geometry_tolerance")
