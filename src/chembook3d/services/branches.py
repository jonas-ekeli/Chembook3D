"""Branches (FR-BR-01…04, D12, P5): named, coloured lineages with parent branches. A node
belongs to at most one branch, and membership changes only by explicit user action (INV-3).
Lineage is read from branch parents only, never from energies (INV-1)."""

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from chembook3d.models import Branch, GroupNode, Node, branch_parents, group_incoming_branches
from chembook3d.services import history
from chembook3d.services.records import (
    RecordError,
    colour_value,
    get,
    status_value,
    text_value,
)

# Distinct colours handed out in turn to new branches; the user can change any of them.
PALETTE = (
    "#2459c6",
    "#d9480f",
    "#2b8a3e",
    "#c2255c",
    "#7048e8",
    "#0b7285",
    "#e67700",
    "#5c940d",
    "#862e9c",
    "#495057",
)
MAX_LINEAGE_PATHS = 50


def snapshot(branch: Branch) -> dict[str, Any]:
    return {
        "name": branch.name,
        "colour": branch.colour,
        "status": branch.status,
        "notes": branch.notes,
        "parent_ids": [p.id for p in branch.parents],
        "split_node_id": branch.split_node_id,
    }


def list_branches(session: Session) -> list[Branch]:
    return list(session.scalars(select(Branch).order_by(Branch.seq)))


def next_colour(session: Session) -> str:
    count = session.scalar(select(func.count()).select_from(Branch)) or 0
    return PALETTE[count % len(PALETTE)]


def ancestors(branch: Branch) -> set[str]:
    seen: set[str] = set()
    stack = list(branch.parents)
    while stack:
        parent = stack.pop()
        if parent.id not in seen:
            seen.add(parent.id)
            stack.extend(parent.parents)
    return seen


def lineage_paths(branch: Branch) -> list[list[str]]:
    """FR-BR-04, INV-1: every chain of parents from this branch to a root, e.g. [A1, A, T].
    A reconnection's outgoing branch has one chain per incoming branch."""
    paths: list[list[str]] = []

    def walk(current: Branch, path: list[str]) -> None:
        if len(paths) >= MAX_LINEAGE_PATHS:
            return
        path = [*path, current.id]
        parents = [p for p in current.parents if p.id not in path]
        if not parents:
            paths.append(path)
            return
        for parent in parents:
            walk(parent, path)

    walk(branch, [])
    return paths


def children(session: Session, branch_id: str) -> list[Branch]:
    query = (
        select(Branch)
        .join(branch_parents, branch_parents.c.branch_id == Branch.id)
        .where(branch_parents.c.parent_id == branch_id)
        .order_by(Branch.seq)
    )
    return list(session.scalars(query))


def _parents(session: Session, parent_ids: Any, branch: Branch | None) -> list[Branch]:
    if not isinstance(parent_ids, list) or not all(isinstance(p, str) for p in parent_ids):
        raise RecordError("parents must be a list of branch ids")
    parents = [get(session, Branch, pid, "Parent branch") for pid in dict.fromkeys(parent_ids)]
    if branch is not None:
        for parent in parents:
            # A branch cannot descend from itself, or lineage would have no root (INV-1).
            if parent.id == branch.id or branch.id in ancestors(parent) | {parent.id}:
                raise RecordError(
                    f"“{parent.name or 'unnamed'}” descends from this branch and cannot be "
                    "its parent"
                )
    return parents


def create(
    session: Session,
    fields: dict[str, Any],
    source: str = "manual",
    record: bool = True,
) -> Branch:
    """FR-BR-01: name, colour, parent branches and status; all optional."""
    branch = Branch(
        name=text_value("name", fields.get("name", "")).strip(),
        colour=colour_value(fields["colour"]) if fields.get("colour") else next_colour(session),
        status=status_value(fields.get("status", "planned")),
        notes=text_value("notes", fields.get("notes", "")),
        split_node_id=fields.get("split_node_id"),
    )
    branch.parents = _parents(session, fields.get("parent_ids", []), None)
    session.add(branch)
    session.flush()
    if record:
        history.record(session, "branch", branch.id, "create", new=snapshot(branch), source=source)
    return branch


def update(session: Session, branch_id: str, changes: dict[str, Any]) -> Branch:
    branch = get(session, Branch, branch_id, "Branch")
    for field, value in changes.items():
        if field == "parent_ids":
            parents = _parents(session, value, branch)
            old = [p.id for p in branch.parents]
            new = [p.id for p in parents]
            if old != new:
                branch.parents = parents
                history.record(session, "branch", branch.id, "update", field, old, new)
            continue
        if field in ("name", "notes"):
            value = text_value(field, value)
            value = value.strip() if field == "name" else value
        elif field == "colour":
            value = colour_value(value)
        elif field == "status":
            value = status_value(value)
        else:
            raise RecordError(f"'{field}' cannot be edited")
        old = getattr(branch, field)
        if old != value:
            setattr(branch, field, value)
            history.record(session, "branch", branch.id, "update", field, old, value)
    session.flush()
    return branch


def members(session: Session, branch_id: str) -> list[Node]:
    return list(session.scalars(select(Node).where(Node.branch_id == branch_id).order_by(Node.seq)))


def delete(session: Session, branch_id: str) -> dict[str, int]:
    """Delete a branch; its nodes are kept with no branch. A branch with child branches cannot
    be deleted, since their lineage would lose its root (INV-1)."""
    branch = get(session, Branch, branch_id, "Branch")
    kids = children(session, branch.id)
    if kids:
        names = ", ".join(f"“{k.name or 'unnamed'}”" for k in kids)
        raise RecordError(
            f"This branch is the parent of {names}. Change their parents or delete them first."
        )
    nodes = members(session, branch.id)
    for node in nodes:
        history.record(session, "node", node.id, "update", "branch_id", branch.id, None)
        node.branch_id = None
    started = select(GroupNode).where(GroupNode.outgoing_branch_id == branch.id)
    for group in session.scalars(started):
        history.record(session, "group", group.id, "update", "outgoing_branch_id", branch.id, None)
        group.outgoing_branch_id = None
    incoming = select(group_incoming_branches.c.group_id).where(
        group_incoming_branches.c.branch_id == branch.id
    )
    for group in session.scalars(select(GroupNode).where(GroupNode.id.in_(incoming))):
        old = [b.id for b in group.incoming_branches]
        group.incoming_branches = [b for b in group.incoming_branches if b.id != branch.id]
        new = [b.id for b in group.incoming_branches]
        history.record(session, "group", group.id, "update", "incoming_branch_ids", old, new)
    history.record(session, "branch", branch.id, "delete", old=snapshot(branch))
    session.delete(branch)
    session.flush()
    return {"unassigned_nodes": len(nodes)}


def split(session: Session, node_id: str, branches: list[dict[str, Any]]) -> list[Branch]:
    """FR-BR-02: create child branches whose parent is the node's branch, and record the split
    node. The node itself stays in its branch; the user assigns nodes to the new branches."""
    node = get(session, Node, node_id, "Node")
    if node.branch_id is None:
        raise RecordError(
            "Assign this node to a branch first: the new branches take it as their parent."
        )
    if not branches:
        raise RecordError("Name at least one new branch")
    created = [
        create(
            session,
            {**fields, "parent_ids": [node.branch_id], "split_node_id": node.id},
            record=True,
        )
        for fields in branches
    ]
    # INV-3: the split itself is one explicit history entry on the split node.
    history.record(
        session,
        "node",
        node.id,
        "split",
        "branch_id",
        node.branch_id,
        [b.id for b in created],
    )
    return created
