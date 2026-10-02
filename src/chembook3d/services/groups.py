"""Group nodes and reconnections (FR-GRP-01, 02, 04, 05; D13, D14, D18, D54, P4).

- "Reconnect as group" puts the selected nodes in a new group, records the branches they came
  from as the group's incoming branches, and can create an outgoing branch whose parents are
  those branches (D13). Members keep their branch, so one group can hold members from several
  branches, e.g. one conformer per branch of the same species (D66).
- The representative is chosen by the user and never by the app (FR-GRP-02, EN-10).
- Transitions into a group are optional (D14): nothing here requires one.
- Nodes can be added to an existing group later; a node in another group moves (D65, A20).
- A member can be taken out of its group and kept as a node, with its calculations and edges
  (D88).
- The members are kept in an order the user sets, which every member layout follows (D89).
- Removing a group either dissolves it (members stay, keeping their branch or with it cleared,
  P4, A26) or deletes it with its members, their calculations and edges (D54).
"""

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from chembook3d.models import Branch, Calculation, GroupNode, Node, NodeKind, ReactionStep
from chembook3d.services import branches as branch_service
from chembook3d.services import history
from chembook3d.services import nodes as node_service
from chembook3d.services import transitions as transition_service
from chembook3d.services.records import RecordError, get, number_value, text_value

GROUP_OFFSET_X = 260.0
LAYOUTS = ("grid", "vertical", "horizontal")  # A21


def members(session: Session, group_id: str) -> list[Node]:
    """In the group's order (D89); a member without a place yet comes last."""
    query = (
        select(Node)
        .where(Node.group_id == group_id)
        .order_by(Node.group_position.is_(None), Node.group_position, Node.seq)
    )
    return list(session.scalars(query))


def snapshot(session: Session, group: GroupNode) -> dict[str, Any]:
    return {
        "label": group.label,
        "notes": group.notes,
        "step_id": group.step_id,
        "member_ids": [m.id for m in members(session, group.id)],
        "representative_id": group.representative_id,
        "incoming_branch_ids": [b.id for b in group.incoming_branches],
        "outgoing_branch_id": group.outgoing_branch_id,
    }


def _join(session: Session, node: Node, group: GroupNode) -> None:
    """Make `node` a member of `group`. It keeps its branch (D66); a loose node's branch is
    also noted as the branch it came from, and a node from another group keeps its note."""
    history.record(session, "node", node.id, "update", "group_id", node.group_id, group.id)
    if node.group_id is None:
        node.origin_branch_id = node.branch_id
    # D89: a new member comes last in the group's order.
    current = members(session, group.id)
    for position, member in enumerate(current, start=1):
        member.group_position = position
    node.group_position = len(current) + 1
    node.group_id = group.id


def _refuse_species(nodes: list[Node]) -> None:
    for node in nodes:
        if node.kind == NodeKind.SPECIES:
            raise RecordError(
                f"“{node.label or 'Untitled species'}” is a free species and cannot join a group"
            )


def list_groups(session: Session) -> list[GroupNode]:
    return list(session.scalars(select(GroupNode).order_by(GroupNode.seq)))


def reconnect(session: Session, fields: dict[str, Any]) -> GroupNode:
    """FR-GRP-01, WF-07. `fields`: member_ids, label, and optionally `outgoing` (name and
    colour of a new outgoing branch) and step_id."""
    member_ids = fields.get("member_ids")
    if not isinstance(member_ids, list) or len(set(member_ids)) < 2:
        raise RecordError("Select at least two nodes to reconnect as a group")
    nodes = [get(session, Node, node_id, "Node") for node_id in dict.fromkeys(member_ids)]
    _refuse_species(nodes)
    for node in nodes:
        if node.group_id is not None:
            raise RecordError(f"“{node.label or 'Untitled node'}” is already in a group")

    incoming_ids = list(dict.fromkeys(n.branch_id for n in nodes if n.branch_id))
    incoming = [session.get(Branch, bid) for bid in incoming_ids]
    step_ids = {n.step_id for n in nodes}
    step_id = fields.get("step_id", next(iter(step_ids)) if len(step_ids) == 1 else None)
    if step_id is not None:
        get(session, ReactionStep, step_id, "Reaction step")

    group = GroupNode(
        label=text_value("label", fields.get("label", "")).strip(),
        notes=text_value("notes", fields.get("notes", "")),
        step_id=step_id,
        pos_x=max(n.pos_x for n in nodes) + GROUP_OFFSET_X,
        pos_y=sum(n.pos_y for n in nodes) / len(nodes),
    )
    group.incoming_branches = [b for b in incoming if b is not None]
    session.add(group)
    session.flush()

    outgoing = fields.get("outgoing")
    if outgoing is not None:
        if not isinstance(outgoing, dict):
            raise RecordError("outgoing must describe the new branch")
        branch = branch_service.create(session, {**outgoing, "parent_ids": incoming_ids})
        group.outgoing_branch_id = branch.id

    for node in nodes:
        _join(session, node, group)
    session.flush()
    # INV-3: one explicit entry for the reconnection itself.
    history.record(session, "group", group.id, "reconnect", new=snapshot(session, group))
    return group


def add_members(session: Session, group_id: str, node_ids: Any) -> GroupNode:
    """FR-GRP-06, A20: add nodes to an existing group. Each node's branch (or, for a node from
    another group, the branch it came from) joins the group's incoming branches and becomes a
    parent of its outgoing branch, unless it is the outgoing branch or one of its descendants."""
    group = get(session, GroupNode, group_id, "Group")
    if not isinstance(node_ids, list) or not all(isinstance(i, str) for i in node_ids):
        raise RecordError("node_ids must be a list of node ids")
    nodes = [get(session, Node, node_id, "Node") for node_id in dict.fromkeys(node_ids)]
    _refuse_species(nodes)
    nodes = [n for n in nodes if n.group_id != group.id]
    if not nodes:
        raise RecordError("Select at least one node that is not in this group yet")
    before = snapshot(session, group)

    outgoing = session.get(Branch, group.outgoing_branch_id) if group.outgoing_branch_id else None
    incoming = list(group.incoming_branches)
    for node in nodes:
        came_from = node.branch_id or node.origin_branch_id
        branch = session.get(Branch, came_from) if came_from else None
        if branch is None or branch in incoming:
            continue
        if outgoing is not None and (
            branch.id == outgoing.id or outgoing.id in branch_service.ancestors(branch)
        ):
            continue  # the group already leads to this branch; it cannot also lead into it
        incoming.append(branch)
    new_incoming = [b for b in incoming if b not in group.incoming_branches]

    for node in nodes:
        old_group = session.get(GroupNode, node.group_id) if node.group_id else None
        if old_group is not None and old_group.representative_id == node.id:
            history.record(
                session, "group", old_group.id, "update", "representative_id", node.id, None
            )
            old_group.representative_id = None
        _join(session, node, group)
    group.incoming_branches = incoming
    if outgoing is not None and new_incoming:
        parents = [p.id for p in outgoing.parents]
        extra = [b.id for b in new_incoming if b.id not in parents]
        if extra:
            branch_service.update(session, outgoing.id, {"parent_ids": parents + extra})
    session.flush()
    # INV-3: one explicit entry for the addition, like the reconnection itself.
    history.record(
        session, "group", group.id, "add_members", old=before, new=snapshot(session, group)
    )
    return group


def remove_member(session: Session, group_id: str, node_id: str) -> GroupNode:
    """D88: take one member out of the group and keep it as a node beside the group, with its
    calculations and edges. It keeps its branch, or goes back to the one it came from. If it
    was the representative, the group has none until the user picks one (FR-GRP-02). A branch
    no remaining member came from stops being one of the group's incoming branches; the
    outgoing branch's parents are left as they are."""
    group = get(session, GroupNode, group_id, "Group")
    node = get(session, Node, node_id, "Node")
    if node.group_id != group.id:
        raise RecordError(f"“{node.label or 'Untitled node'}” is not in this group")
    rest = [m for m in members(session, group.id) if m.id != node.id]
    if not rest:
        raise RecordError("This is the group's last member: dissolve the group instead")
    before = snapshot(session, group)

    if group.representative_id == node.id:
        group.representative_id = None
    history.record(session, "node", node.id, "update", "group_id", group.id, None)
    node.group_id = None
    node.group_position = None
    branch_id = node.branch_id or node.origin_branch_id
    if branch_id is not None and session.get(Branch, branch_id) is None:
        branch_id = None
    if branch_id != node.branch_id:
        history.record(session, "node", node.id, "update", "branch_id", node.branch_id, branch_id)
        node.branch_id = branch_id
    node.origin_branch_id = None
    # Beside the group, where reconnected members came from, not hidden under it (layout only).
    node.pos_x, node.pos_y = group.pos_x - GROUP_OFFSET_X, group.pos_y

    still = {b for m in rest for b in (m.branch_id, m.origin_branch_id) if b}
    group.incoming_branches = [b for b in group.incoming_branches if b.id in still]
    session.flush()
    # INV-3: one explicit entry for the removal, like the addition.
    history.record(
        session, "group", group.id, "remove_member", old=before, new=snapshot(session, group)
    )
    return group


def reorder_members(session: Session, group_id: str, ordered_ids: Any) -> GroupNode:
    """D89: set the order of all members at once, as the reaction steps are ordered
    (FR-STEP-01). The grid, column and row layouts all follow it; one history entry."""
    group = get(session, GroupNode, group_id, "Group")
    current = members(session, group.id)
    if not isinstance(ordered_ids, list) or sorted(ordered_ids) != sorted(m.id for m in current):
        raise RecordError("the new order must list every member of the group once")
    old = [m.id for m in current]
    by_id = {m.id: m for m in current}
    for position, node_id in enumerate(ordered_ids, start=1):
        by_id[node_id].group_position = position
    if old != ordered_ids:
        history.record(session, "group", group.id, "update", "member_ids", old, ordered_ids)
    session.flush()
    return group


def update(session: Session, group_id: str, changes: dict[str, Any]) -> GroupNode:
    group = get(session, GroupNode, group_id, "Group")
    for field, value in changes.items():
        if field in ("pos_x", "pos_y"):  # layout only; not recorded in history
            setattr(group, field, number_value(field, value))
            continue
        if field == "layout":  # A21: layout too, so not recorded in history
            if value not in LAYOUTS:
                raise RecordError(f"layout must be one of {', '.join(LAYOUTS)}")
            group.layout = value
            continue
        if field in ("label", "notes"):
            value = text_value(field, value)
            value = value.strip() if field == "label" else value
        elif field == "step_id":
            if value is not None:
                get(session, ReactionStep, value, "Reaction step")
        elif field == "representative_id":
            # FR-GRP-02: at most one representative, chosen only here, by the user.
            if value is not None and get(session, Node, value, "Node").group_id != group.id:
                raise RecordError("The representative must be a member of the group")
        elif field == "outgoing_branch_id":
            if value is not None:
                get(session, Branch, value, "Branch")
        else:
            raise RecordError(f"'{field}' cannot be edited")
        old = getattr(group, field)
        if old != value:
            setattr(group, field, value)
            history.record(session, "group", group.id, "update", field, old, value)
    session.flush()
    return group


def delete_preview(session: Session, group_id: str) -> dict[str, Any]:
    """What each way of removing the group affects (D54), listed before confirming."""
    group = get(session, GroupNode, group_id, "Group")
    nodes = members(session, group.id)
    ids = [n.id for n in nodes]
    calculations = (
        list(session.scalars(select(Calculation).where(Calculation.node_id.in_(ids))))
        if ids
        else []
    )
    return {
        "group": group,
        "members": nodes,
        "calculations": len(calculations),
        "group_transitions": transition_service.touching(session, [group.id]),
        "all_transitions": transition_service.touching(session, [group.id, *ids]),
    }


def dissolve(session: Session, group_id: str, restore_branches: bool) -> list[Node]:
    """P4, A26: members stay as nodes, keeping their branch (or, with none, going back to the
    one they came from) or with it cleared, by the user's choice. Edges to the group itself go
    with it; edges between members stay."""
    group = get(session, GroupNode, group_id, "Group")
    nodes = members(session, group.id)
    transition_service.remove(session, transition_service.touching(session, [group.id]))
    history.record(session, "group", group.id, "dissolve", old=snapshot(session, group))
    group.representative_id = None
    for node in nodes:
        history.record(session, "node", node.id, "update", "group_id", group.id, None)
        node.group_id = None
        node.group_position = None
        branch_id = (node.branch_id or node.origin_branch_id) if restore_branches else None
        if branch_id is not None and session.get(Branch, branch_id) is None:
            branch_id = None
        if branch_id != node.branch_id:
            history.record(
                session, "node", node.id, "update", "branch_id", node.branch_id, branch_id
            )
            node.branch_id = branch_id
        node.origin_branch_id = None
    session.flush()
    session.delete(group)
    session.flush()
    return nodes


def delete_with_contents(session: Session, group_id: str) -> dict[str, int]:
    """D54: the group, its members with their calculations, and every edge touching any of
    them are deleted, each recorded in the history."""
    preview = delete_preview(session, group_id)
    group: GroupNode = preview["group"]
    transition_service.remove(session, preview["all_transitions"])
    history.record(session, "group", group.id, "delete", old=snapshot(session, group))
    group.representative_id = None
    session.flush()
    for node in preview["members"]:
        node_service.delete(session, node.id)
    session.delete(group)
    session.flush()
    return {
        "members": len(preview["members"]),
        "calculations": preview["calculations"],
        "transitions": len(preview["all_transitions"]),
    }
