"""what an edge Claude proposed keeps until the user accepts it

Revision ID: 0039
Revises: 0038
Create Date: 2026-11-25

D264: an edge Claude proposes (`propose_mechanism`) stores its confidence (high, medium, low),
the evidence that settled it and whether it is still open or accepted (`review`); `review` is
NULL on an ordinary edge, never proposed.
"""

import sqlalchemy as sa
from alembic import op

revision = "0039"
down_revision = "0038"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("transitions", schema=None) as batch_op:
        batch_op.add_column(sa.Column("confidence", sa.String(length=8), nullable=True))
        batch_op.add_column(
            sa.Column("evidence", sa.Text(), nullable=False, server_default=sa.text("''"))
        )
        batch_op.add_column(sa.Column("review", sa.String(length=16), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("transitions", schema=None) as batch_op:
        batch_op.drop_column("review")
        batch_op.drop_column("evidence")
        batch_op.drop_column("confidence")
