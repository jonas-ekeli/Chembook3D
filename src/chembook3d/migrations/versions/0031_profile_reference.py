"""each energy profile's own reference

Revision ID: 0031
Revises: 0030
Create Date: 2026-10-30

D199: an energy profile keeps its own reference node, shared by all its pathways and apart
from the reference of the node cards. Existing profiles start from the reference the energy
view had, which they used until now; one not on a profile's pathways is ignored when read.
"""

import sqlalchemy as sa
from alembic import op

revision = "0031"
down_revision = "0030"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("energy_profiles", schema=None) as batch_op:
        batch_op.add_column(sa.Column("reference_id", sa.String(length=32), nullable=True))
    op.execute(
        "UPDATE energy_profiles SET reference_id = ("
        "SELECT json_extract(view_state, '$.reference_id') FROM investigation_info "
        "WHERE id = 1 AND json_valid(view_state) "
        "AND json_type(view_state, '$.reference_id') = 'text')"
    )


def downgrade() -> None:
    with op.batch_alter_table("energy_profiles", schema=None) as batch_op:
        batch_op.drop_column("reference_id")
