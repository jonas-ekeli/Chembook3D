"""history entries copied by a playground's merge

Revision ID: 0033
Revises: 0032
Create Date: 2026-11-16

D235h: merging a playground copies its history entries into the original, each keeping its
source and pointing to the one "Merged from playground" entry the merge writes first. No
foreign key: the entries are append-only and the pointer is information only.
"""

import sqlalchemy as sa
from alembic import op

revision = "0033"
down_revision = "0032"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("history", schema=None) as batch_op:
        batch_op.add_column(sa.Column("merge_entry_id", sa.Integer(), nullable=True))
        batch_op.create_index(
            batch_op.f("ix_history_merge_entry_id"), ["merge_entry_id"], unique=False
        )


def downgrade() -> None:
    with op.batch_alter_table("history", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_history_merge_entry_id"))
        batch_op.drop_column("merge_entry_id")
