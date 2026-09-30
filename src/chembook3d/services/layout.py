"""Canvas layout helpers. Positions are layout, not records, so they are not in the history.

"Arrange branch" (FR-CAN-06, D33) lays out one branch left to right in step order and leaves
every other position unchanged. Each reaction step is one column, so branches arranged from
the same starting column line up by step, and a missing step leaves a gap (02 §8 case 1).
Several nodes at one step (e.g. two TS candidates) are stacked. Nodes with no step follow the
last step, in creation order.
"""

from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from chembook3d.models import Branch, GroupNode, Node
from chembook3d.services import branches as branch_service
from chembook3d.services import steps as step_service
from chembook3d.services.records import RecordError, get, number_value

COLUMN_WIDTH = 240.0
ROW_HEIGHT = 170.0


def arrange_branch(session: Session, branch_id: str) -> list[Node | GroupNode]:
    branch = get(session, Branch, branch_id, "Branch")
    on_branch = branch_service.members(session, branch.id)
    # Members are drawn inside their group, so the group is placed instead (A26). A
    # reconnection's group starts its outgoing branch, so it is laid out with it too.
    nodes = [n for n in on_branch if n.group_id is None]
    group_ids = {n.group_id for n in on_branch if n.group_id is not None}
    groups = list(
        session.scalars(
            select(GroupNode)
            .where(or_(GroupNode.outgoing_branch_id == branch.id, GroupNode.id.in_(group_ids)))
            .order_by(GroupNode.seq)
        )
    )
    items: list[Node | GroupNode] = [*groups, *nodes]
    if not items:
        return []

    column_of_step = {s.id: i for i, s in enumerate(step_service.list_steps(session))}
    stepped = [i for i in items if i.step_id in column_of_step]
    unstepped = [i for i in items if i.step_id not in column_of_step]
    last = max((column_of_step[i.step_id] for i in stepped), default=-1)

    columns: dict[int, list[Node | GroupNode]] = {}
    for item in stepped:
        columns.setdefault(column_of_step[item.step_id], []).append(item)
    for offset, item in enumerate(unstepped, start=1):
        columns.setdefault(last + offset, []).append(item)

    # The first item in step order keeps its place and anchors the rest.
    first_column = min(columns)
    anchor = columns[first_column][0]
    x0 = anchor.pos_x - first_column * COLUMN_WIDTH
    y0 = anchor.pos_y
    for column, members in columns.items():
        for row, item in enumerate(members):
            item.pos_x = x0 + column * COLUMN_WIDTH
            item.pos_y = y0 + row * ROW_HEIGHT
    session.flush()
    return items


def set_positions(session: Session, positions: dict[str, Any]) -> int:
    """Store dragged positions for nodes and group nodes: {id: {"x": …, "y": …}}."""
    count = 0
    for record_id, position in positions.items():
        record = session.get(Node, record_id) or session.get(GroupNode, record_id)
        if record is None:
            raise RecordError("unknown node or group")
        if not isinstance(position, dict):
            raise RecordError("each position needs x and y")
        record.pos_x = number_value("x", position.get("x"))
        record.pos_y = number_value("y", position.get("y"))
        count += 1
    session.flush()
    return count
