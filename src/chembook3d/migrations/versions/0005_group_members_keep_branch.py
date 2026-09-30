"""group members keep their branch

Revision ID: 0005
Revises: 0004
Create Date: 2026-09-30

D66: group members keep their branch. Members that lost it when they joined a group go back
to the branch they came from (A22); members that never had one (CREST ensembles) stay
without. Like other migrations, this writes no history entries.
"""

from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "UPDATE nodes SET branch_id = origin_branch_id"
        " WHERE group_id IS NOT NULL AND branch_id IS NULL AND origin_branch_id IS NOT NULL"
        " AND origin_branch_id IN (SELECT id FROM branches)"
    )


def downgrade() -> None:
    op.execute("UPDATE nodes SET branch_id = NULL WHERE group_id IS NOT NULL")
