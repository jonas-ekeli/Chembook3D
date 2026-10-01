"""node notes

Revision ID: 0013
Revises: 0012
Create Date: 2026-10-01

D85: notes pinned to a corner of a node's card, and the pictures in them. Existing
investigations start with none.
"""

import sqlalchemy as sa
from alembic import op

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "node_notes",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("node_id", sa.String(length=32), nullable=False),
        sa.Column("corner", sa.String(length=16), nullable=False),
        sa.Column("colour", sa.String(length=16), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("collapsed", sa.Boolean(), nullable=False),
        sa.Column("width", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["node_id"], ["nodes.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("node_notes", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_node_notes_node_id"), ["node_id"], unique=False)
    op.create_table(
        "note_images",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("media_type", sa.String(length=32), nullable=False),
        sa.Column("data", sa.LargeBinary(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    op.drop_table("note_images")
    op.drop_table("node_notes")
