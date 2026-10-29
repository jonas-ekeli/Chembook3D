"""sub-investigations

Revision ID: 0029
Revises: 0028
Create Date: 2026-10-29

D193: a box on the canvas holding a canvas of its own (a sub-investigation), at any depth.
Nodes and groups say which canvas they are on; every existing one stays on the main canvas.
"""

import sqlalchemy as sa
from alembic import op

revision = "0029"
down_revision = "0028"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "sub_investigations",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("label", sa.String(length=200), nullable=False),
        sa.Column("notes", sa.Text(), nullable=False),
        sa.Column("parent_id", sa.String(length=32), nullable=True),
        sa.Column("pos_x", sa.Double(), nullable=False),
        sa.Column("pos_y", sa.Double(), nullable=False),
        sa.Column("face_id", sa.String(length=32), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["parent_id"], ["sub_investigations.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["face_id"], ["nodes.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_sub_investigations_parent_id"), "sub_investigations", ["parent_id"], unique=False
    )
    for table in ("nodes", "group_nodes"):
        with op.batch_alter_table(table, schema=None) as batch_op:
            batch_op.add_column(sa.Column("canvas_id", sa.String(length=32), nullable=True))
            batch_op.create_index(batch_op.f(f"ix_{table}_canvas_id"), ["canvas_id"], unique=False)
            batch_op.create_foreign_key(
                f"fk_{table}_canvas_id",
                "sub_investigations",
                ["canvas_id"],
                ["id"],
                ondelete="SET NULL",
            )


def downgrade() -> None:
    for table in ("group_nodes", "nodes"):
        with op.batch_alter_table(table, schema=None) as batch_op:
            batch_op.drop_constraint(f"fk_{table}_canvas_id", type_="foreignkey")
            batch_op.drop_index(batch_op.f(f"ix_{table}_canvas_id"))
            batch_op.drop_column("canvas_id")
    op.drop_index(op.f("ix_sub_investigations_parent_id"), table_name="sub_investigations")
    op.drop_table("sub_investigations")
