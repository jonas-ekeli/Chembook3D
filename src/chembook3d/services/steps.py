"""Reaction steps (FR-STEP-01…03, D3, P2): conceptual positions in the mechanism, shared by
all branches, in a user-defined order. Nodes and group nodes are assigned to at most one."""

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from chembook3d.models import GroupNode, Node, ReactionStep
from chembook3d.services import history
from chembook3d.services.records import RecordError, get, text_value


def snapshot(step: ReactionStep) -> dict[str, Any]:
    return {"name": step.name, "notes": step.notes, "position": step.position}


def list_steps(session: Session) -> list[ReactionStep]:
    return list(
        session.scalars(select(ReactionStep).order_by(ReactionStep.position, ReactionStep.id))
    )


def create(session: Session, name: str = "", notes: str = "") -> ReactionStep:
    last = session.scalar(select(func.max(ReactionStep.position)))
    step = ReactionStep(
        name=text_value("name", name).strip(),
        notes=text_value("notes", notes),
        position=(last or 0) + 1,
    )
    session.add(step)
    session.flush()
    history.record(session, "step", step.id, "create", new=snapshot(step))
    return step


def update(session: Session, step_id: str, changes: dict[str, Any]) -> ReactionStep:
    step = get(session, ReactionStep, step_id, "Reaction step")
    for field, value in changes.items():
        if field not in ("name", "notes"):
            raise RecordError(f"'{field}' cannot be edited")
        value = text_value(field, value)
        if field == "name":
            value = value.strip()
        old = getattr(step, field)
        if old != value:
            setattr(step, field, value)
            history.record(session, "step", step.id, "update", field, old, value)
    session.flush()
    return step


def reorder(session: Session, ordered_ids: list[str]) -> list[ReactionStep]:
    """FR-STEP-01: set the order of all steps at once."""
    steps = {s.id: s for s in list_steps(session)}
    if sorted(ordered_ids) != sorted(steps):
        raise RecordError("the new order must list every reaction step once")
    for position, step_id in enumerate(ordered_ids, start=1):
        step = steps[step_id]
        if step.position != position:
            history.record(session, "step", step.id, "update", "position", step.position, position)
            step.position = position
    session.flush()
    return list_steps(session)


def delete(session: Session, step_id: str) -> dict[str, int]:
    """FR-STEP-01: deleting a step unassigns its nodes (each change is in their history)."""
    step = get(session, ReactionStep, step_id, "Reaction step")
    nodes = list(session.scalars(select(Node).where(Node.step_id == step.id)))
    for node in nodes:
        history.record(session, "node", node.id, "update", "step_id", step.id, None)
        node.step_id = None
    groups = list(session.scalars(select(GroupNode).where(GroupNode.step_id == step.id)))
    for group in groups:
        history.record(session, "group", group.id, "update", "step_id", step.id, None)
        group.step_id = None
    history.record(session, "step", step.id, "delete", old=snapshot(step))
    session.delete(step)
    session.flush()
    remaining = list_steps(session)
    for position, other in enumerate(remaining, start=1):
        other.position = position
    session.flush()
    return {"unassigned_nodes": len(nodes), "unassigned_groups": len(groups)}
