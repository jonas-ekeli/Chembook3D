"""normal modes in a table of their own, compressed

Revision ID: 0035
Revises: 0034
Create Date: 2026-11-22

D247: a frequency job's normal modes were plain JSON on its results row, three quarters of a
real investigation's database (32 of 43 MB at 111 frequency jobs), so the database would pass
GitHub's 100 MB limit at about 250 frequency jobs and sync would stop. They move to
`normal_modes`, one row per calculation that has them, as zlib-compressed JSON: every number
kept as it was, about eight times smaller. The space freed is given back to the disk after the
upgrade (`investigation._migrate`).
"""

import json
import zlib

import sqlalchemy as sa
from alembic import op

revision = "0035"
down_revision = "0034"
branch_labels = None
depends_on = None


def _pack(text: str) -> bytes:
    # The same as models.pack_modes; a migration never imports today's models.
    return zlib.compress(json.dumps(json.loads(text), separators=(",", ":")).encode(), 9)


def upgrade() -> None:
    op.create_table(
        "normal_modes",
        sa.Column("calculation_id", sa.String(length=32), nullable=False),
        sa.Column("data", sa.LargeBinary(), nullable=False),
        sa.ForeignKeyConstraint(["calculation_id"], ["calculations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("calculation_id"),
    )
    connection = op.get_bind()
    rows = connection.execute(
        sa.text(
            "SELECT calculation_id, normal_modes FROM calculation_results "
            "WHERE normal_modes IS NOT NULL AND normal_modes NOT IN ('[]', 'null')"
        )
    )
    insert = sa.text("INSERT INTO normal_modes (calculation_id, data) VALUES (:id, :data)")
    for calculation_id, text in rows.fetchall():
        connection.execute(insert, {"id": calculation_id, "data": _pack(text)})
    with op.batch_alter_table("calculation_results", schema=None) as batch_op:
        batch_op.drop_column("normal_modes")


def downgrade() -> None:
    with op.batch_alter_table("calculation_results", schema=None) as batch_op:
        batch_op.add_column(sa.Column("normal_modes", sa.JSON(), nullable=True))
    connection = op.get_bind()
    connection.execute(sa.text("UPDATE calculation_results SET normal_modes = '[]'"))
    rows = connection.execute(sa.text("SELECT calculation_id, data FROM normal_modes"))
    update = sa.text(
        "UPDATE calculation_results SET normal_modes = :modes WHERE calculation_id = :id"
    )
    for calculation_id, data in rows.fetchall():
        connection.execute(update, {"id": calculation_id, "modes": zlib.decompress(data).decode()})
    with op.batch_alter_table("calculation_results", schema=None) as batch_op:
        batch_op.alter_column("normal_modes", existing_type=sa.JSON(), nullable=False)
    op.drop_table("normal_modes")
