"""Concrete transitions (FR-EDGE-01…03, D3): directed edges between nodes or group nodes.

- Edges between nodes of different branches are allowed and never change membership (D17,
  INV-4); nothing here touches a node's branch.
- A transition where neither end is a transition-state node is a direct connection, shown
  as "no TS" (D53, FR-EDGE-03). Group nodes are not TS nodes, so an edge into a reconnection
  without a TS is a direct connection too (D14).
- Each end is drawn on one side of its box (D76); the sides are layout, not history (A29).
"""

from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from chembook3d.models import GroupNode, Node, NodeKind, Role, Transition
from chembook3d.services import history
from chembook3d.services.records import RecordError, get, status_value, text_value

Endpoint = Node | GroupNode

SIDES = ("top", "right", "bottom", "left")
SIDE_FIELDS = ("source_side", "target_side")


def side_value(field: str, value: Any) -> str:
    if value not in SIDES:
        raise RecordError(f"{field} must be one of {', '.join(SIDES)}")
    return value


def snapshot(transition: Transition) -> dict[str, Any]:
    return {
        "source_id": transition.source_id,
        "target_id": transition.target_id,
        "status": transition.status,
        "notes": transition.notes,
        "species": [
            {"species_id": e.species_id, "direction": e.direction, "count": e.count}
            for e in transition.species
        ],
    }


def endpoint(session: Session, record_id: str) -> Endpoint:
    found = session.get(Node, record_id) or session.get(GroupNode, record_id)
    if found is None:
        raise RecordError("Each end of a transition must be a node or a group node")
    return found


def list_transitions(session: Session) -> list[Transition]:
    return list(session.scalars(select(Transition).order_by(Transition.seq)))


def touching(session: Session, record_ids: list[str]) -> list[Transition]:
    """Transitions with either end in `record_ids` (nodes or groups)."""
    if not record_ids:
        return []
    query = (
        select(Transition)
        .where(
            or_(
                Transition.source_node_id.in_(record_ids),
                Transition.target_node_id.in_(record_ids),
                Transition.source_group_id.in_(record_ids),
                Transition.target_group_id.in_(record_ids),
            )
        )
        .order_by(Transition.seq)
    )
    return list(session.scalars(query))


def is_ts(end: Endpoint) -> bool:
    return isinstance(end, Node) and end.role == Role.TRANSITION_STATE


def branches_of(end: Endpoint) -> set[str]:
    if isinstance(end, Node):
        return {end.branch_id} if end.branch_id else set()
    ids = {b.id for b in end.incoming_branches}
    if end.outgoing_branch_id:
        ids.add(end.outgoing_branch_id)
    return ids


def describe(session: Session, transition: Transition) -> dict[str, Any]:
    """Derived facts shown on the canvas: direct connection (D53) and whether the edge joins
    different branches, which is drawn dashed (P9)."""
    source = endpoint(session, transition.source_id)
    target = endpoint(session, transition.target_id)
    a, b = branches_of(source), branches_of(target)
    return {
        "direct": not is_ts(source) and not is_ts(target),
        "cross_branch": bool(a and b and not (a & b)),
    }


def _is_end(node_column, group_column, end: Endpoint):
    return node_column == end.id if isinstance(end, Node) else group_column == end.id


def create(session: Session, fields: dict[str, Any]) -> Transition:
    source_id, target_id = fields.get("source_id"), fields.get("target_id")
    if not isinstance(source_id, str) or not isinstance(target_id, str):
        raise RecordError("A transition needs a source and a target")
    if source_id == target_id:
        raise RecordError("A transition must join two different nodes")
    source, target = endpoint(session, source_id), endpoint(session, target_id)
    for end in (source, target):
        if isinstance(end, Node) and end.kind == NodeKind.SPECIES:
            raise RecordError(
                f"“{end.label or 'Untitled species'}” is a free species; "
                "add it to a transition instead (D69)"
            )
    existing = session.scalar(
        select(Transition).where(
            _is_end(Transition.source_node_id, Transition.source_group_id, source),
            _is_end(Transition.target_node_id, Transition.target_group_id, target),
        )
    )
    if existing is not None:
        raise RecordError("These two nodes are already connected in this direction")
    transition = Transition(
        status=status_value(fields.get("status", "planned")),
        notes=text_value("notes", fields.get("notes", "")),
        source_side=side_value("source_side", fields.get("source_side", "right")),
        target_side=side_value("target_side", fields.get("target_side", "left")),
    )
    if isinstance(source, Node):
        transition.source_node_id = source.id
    else:
        transition.source_group_id = source.id
    if isinstance(target, Node):
        transition.target_node_id = target.id
    else:
        transition.target_group_id = target.id
    session.add(transition)
    session.flush()
    history.record(session, "transition", transition.id, "create", new=snapshot(transition))
    return transition


def update(session: Session, transition_id: str, changes: dict[str, Any]) -> Transition:
    transition = get(session, Transition, transition_id, "Transition")
    for field, value in changes.items():
        if field in SIDE_FIELDS:
            # A29: where the arrow is drawn is layout, like a position, so it has no history.
            setattr(transition, field, side_value(field, value))
            continue
        if field == "status":
            value = status_value(value)
        elif field == "notes":
            value = text_value(field, value)
        else:
            raise RecordError(f"'{field}' cannot be edited")
        old = getattr(transition, field)
        if old != value:
            setattr(transition, field, value)
            history.record(session, "transition", transition.id, "update", field, old, value)
    session.flush()
    return transition


def delete(session: Session, transition_id: str) -> None:
    transition = get(session, Transition, transition_id, "Transition")
    remove(session, [transition])


def remove(session: Session, transitions: list[Transition]) -> None:
    for transition in transitions:
        history.record(session, "transition", transition.id, "delete", old=snapshot(transition))
        session.delete(transition)
    session.flush()
