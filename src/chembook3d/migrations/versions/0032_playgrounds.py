"""an investigation id and playground copies

Revision ID: 0032
Revises: 0031
Create Date: 2026-11-16

D235: every investigation gets an id of its own, apart from its folder, and a playground (a
working copy at <original>/playgrounds/<name>/) records which investigation it was made from,
the path back to it, its name, when it was made, the original's last history entry then and,
once merged, when.
"""

import sqlalchemy as sa
from alembic import op

revision = "0032"
down_revision = "0031"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("investigation_info", schema=None) as batch_op:
        batch_op.add_column(sa.Column("uuid", sa.String(length=32), nullable=True))
        batch_op.add_column(sa.Column("playground_of", sa.String(length=32), nullable=True))
        batch_op.add_column(sa.Column("playground_original", sa.String(length=200), nullable=True))
        batch_op.add_column(sa.Column("playground_name", sa.String(length=200), nullable=True))
        batch_op.add_column(sa.Column("playground_created_at", sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column("playground_history_id", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("playground_merged_at", sa.DateTime(), nullable=True))
    op.execute("UPDATE investigation_info SET uuid = lower(hex(randomblob(16))) WHERE uuid IS NULL")


def downgrade() -> None:
    with op.batch_alter_table("investigation_info", schema=None) as batch_op:
        for column in (
            "playground_merged_at",
            "playground_history_id",
            "playground_created_at",
            "playground_name",
            "playground_original",
            "playground_of",
            "uuid",
        ):
            batch_op.drop_column(column)
