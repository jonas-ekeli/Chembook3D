"""scan paths shown on their edges

Revision ID: 0020
Revises: 0019
Create Date: 2026-10-10

D121: a scan path node remembers the edge it was run on, which shows it as a chip, and can be
shown on that edge only (its box off the canvas). Existing nodes start unlinked and on the
canvas; path nodes from cloud jobs are linked once when the investigation opens (a repair).
"""

import sqlalchemy as sa
from alembic import op

revision = "0020"
down_revision = "0019"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("nodes", schema=None) as batch_op:
        batch_op.add_column(sa.Column("path_edge_id", sa.String(length=32), nullable=True))
        batch_op.add_column(
            sa.Column("on_edge_only", sa.Boolean(), nullable=False, server_default=sa.text("0"))
        )
        batch_op.create_foreign_key(
            "fk_nodes_path_edge_id", "transitions", ["path_edge_id"], ["id"], ondelete="SET NULL"
        )


def downgrade() -> None:
    with op.batch_alter_table("nodes", schema=None) as batch_op:
        batch_op.drop_constraint("fk_nodes_path_edge_id", type_="foreignkey")
        batch_op.drop_column("on_edge_only")
        batch_op.drop_column("path_edge_id")
