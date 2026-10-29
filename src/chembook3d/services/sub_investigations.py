"""Sub-investigations: a box on the canvas that holds a canvas of its own (D193, FR-SUB).

- A sub-investigation is a side mechanism kept behind one box: its nodes, groups, edges and
  further sub-investigations (at any depth, Q61) live on its own canvas. Nodes and groups say
  which canvas they are on (`canvas_id`, None for the main one); a box is on its `parent_id`.
  Positions are always on the record's own canvas.
- The box is not a node (Q59): it has no energy, no branch and no step, and no edge ends at it.
  Edges end at real nodes; one whose ends are on different canvases crosses, and the canvas
  draws it to the box. So pathways, energies and every analysis are unchanged.
- Its face is one node inside it (at any depth) whose structure the closed box shows, only as a
  picture. A face that leaves the box is cleared.
- A group and its members always share a canvas (Q60): moving a member moves the whole group,
  which the caller confirms with `with_groups`; a node joining a group moves onto its canvas.
- Making one, renaming it, its face, moving things in or out, dissolving and deleting are in
  the history; box positions are layout and are not (as A35).
"""

from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from chembook3d.models import Calculation, GroupNode, Node, NodeKind, Role, SubInvestigation
from chembook3d.services import groups as group_service
from chembook3d.services import history
from chembook3d.services import nodes as node_service
from chembook3d.services import transitions as transition_service
from chembook3d.services.records import RecordError, get, number_value, text_value

GAP = 480.0  # between what a canvas holds and what is moved onto it, in px
Item = Node | GroupNode | SubInvestigation


def list_all(session: Session) -> list[SubInvestigation]:
    return list(session.scalars(select(SubInvestigation).order_by(SubInvestigation.seq)))


def snapshot(sub: SubInvestigation) -> dict[str, Any]:
    return {
        "label": sub.label,
        "notes": sub.notes,
        "parent_id": sub.parent_id,
        "face_id": sub.face_id,
    }


def canvas_of(item: Item) -> str | None:
    return item.parent_id if isinstance(item, SubInvestigation) else item.canvas_id


def chain(session: Session, canvas_id: str | None) -> list[SubInvestigation]:
    """The boxes from the main canvas down to `canvas_id`, outermost first (the breadcrumb)."""
    found: list[SubInvestigation] = []
    seen: set[str] = set()
    while canvas_id is not None and canvas_id not in seen:
        seen.add(canvas_id)
        sub = session.get(SubInvestigation, canvas_id)
        if sub is None:
            break
        found.append(sub)
        canvas_id = sub.parent_id
    return found[::-1]


def within(session: Session, sub_id: str) -> set[str]:
    """`sub_id` and every sub-investigation inside it, at any depth."""
    children: dict[str | None, list[str]] = {}
    for sub in list_all(session):
        children.setdefault(sub.parent_id, []).append(sub.id)
    found = {sub_id}
    todo = [sub_id]
    while todo:
        for child in children.get(todo.pop(), []):
            if child not in found:
                found.add(child)
                todo.append(child)
    return found


@dataclass
class Contents:
    """What a sub-investigation holds, at any depth."""

    subs: list[SubInvestigation] = field(default_factory=list)  # nested ones, not itself
    groups: list[GroupNode] = field(default_factory=list)
    nodes: list[Node] = field(default_factory=list)  # group members included

    @property
    def empty(self) -> bool:
        return not (self.subs or self.groups or self.nodes)


def contents(session: Session, sub_id: str) -> Contents:
    ids = within(session, sub_id)
    return Contents(
        subs=[s for s in list_all(session) if s.id in ids and s.id != sub_id],
        groups=list(
            session.scalars(
                select(GroupNode).where(GroupNode.canvas_id.in_(ids)).order_by(GroupNode.seq)
            )
        ),
        nodes=list(
            session.scalars(
                select(Node)
                .where(Node.canvas_id.in_(ids), Node.kind == NodeKind.NODE)
                .order_by(Node.seq)
            )
        ),
    )


@dataclass
class Summary:
    nodes: int
    transition_states: int
    groups: int
    subs: int


def summaries(session: Session) -> dict[str, Summary]:
    """Per box, what it holds at any depth, for the closed box's line under its face."""
    subs = list_all(session)
    parent = {s.id: s.parent_id for s in subs}

    def boxes(canvas_id: str | None) -> list[str]:
        found: list[str] = []
        while canvas_id is not None and canvas_id not in found:
            found.append(canvas_id)
            canvas_id = parent.get(canvas_id)
        return found

    counts = {s.id: Summary(0, 0, 0, 0) for s in subs}
    for sub in subs:
        for box in boxes(sub.parent_id):
            counts[box].subs += 1
    for group in session.scalars(select(GroupNode).where(GroupNode.canvas_id.is_not(None))):
        for box in boxes(group.canvas_id):
            counts[box].groups += 1
    query = select(Node).where(Node.canvas_id.is_not(None), Node.kind == NodeKind.NODE)
    for node in session.scalars(query):
        for box in boxes(node.canvas_id):
            counts[box].nodes += 1
            if node.role == Role.TRANSITION_STATE:
                counts[box].transition_states += 1
    return counts


# ---------- moving ----------


def _items(session: Session, ids: Any) -> list[Item]:
    if not isinstance(ids, list) or not all(isinstance(i, str) for i in ids):
        raise RecordError("ids must be a list of node, group and sub-investigation ids")
    items: list[Item] = []
    for record_id in dict.fromkeys(ids):
        item = (
            session.get(Node, record_id)
            or session.get(GroupNode, record_id)
            or session.get(SubInvestigation, record_id)
        )
        if item is None:
            raise RecordError("unknown node, group or sub-investigation")
        if isinstance(item, Node) and item.kind == NodeKind.SPECIES:
            raise RecordError(
                f"“{item.label or 'Untitled species'}” is a free species, which is on no canvas"
            )
        items.append(item)
    return items


def _whole_groups(session: Session, items: list[Item], with_groups: bool) -> list[Item]:
    """Q60: a member stands for its whole group, once the caller has asked (`with_groups`)."""
    listed = {i.id for i in items}
    kept: list[Item] = []
    for item in items:
        if isinstance(item, Node) and item.group_id is not None:
            if item.group_id in listed:
                continue  # moves with its group
            group = get(session, GroupNode, item.group_id, "Group")
            if not with_groups:
                name, group_name = item.label or "Untitled node", group.label or "Group"
                raise RecordError(
                    f"“{name}” is in the group “{group_name}”, which moves as a whole"
                )
            listed.add(group.id)
            kept.append(group)
            continue
        kept.append(item)
    return kept


def _nodes_of(session: Session, items: list[Item]) -> list[Node]:
    """Every node the items stand for: themselves, a group's members, a box's contents."""
    found: list[Node] = []
    for item in items:
        if isinstance(item, Node):
            found.append(item)
        elif isinstance(item, GroupNode):
            found += group_service.members(session, item.id)
        else:
            found += contents(session, item.id).nodes
    return found


def _set_canvas(session: Session, item: Item, canvas_id: str | None) -> None:
    if isinstance(item, SubInvestigation):
        if item.parent_id != canvas_id:
            history.record(
                session,
                "sub_investigation",
                item.id,
                "update",
                "parent_id",
                item.parent_id,
                canvas_id,
            )
            item.parent_id = canvas_id
        return
    if isinstance(item, GroupNode):
        for member in group_service.members(session, item.id):
            _set_canvas(session, member, canvas_id)
        record_type = "group"
    else:
        record_type = "node"
    if item.canvas_id != canvas_id:
        history.record(
            session, record_type, item.id, "update", "canvas_id", item.canvas_id, canvas_id
        )
        item.canvas_id = canvas_id


def _on_canvas(session: Session, canvas_id: str | None) -> list[Item]:
    """What a canvas draws as its own boxes: loose nodes, groups and sub-investigations."""
    nodes = session.scalars(
        select(Node).where(
            Node.canvas_id.is_(None) if canvas_id is None else Node.canvas_id == canvas_id,
            Node.kind == NodeKind.NODE,
            Node.group_id.is_(None),
        )
    )
    groups = session.scalars(
        select(GroupNode).where(
            GroupNode.canvas_id.is_(None) if canvas_id is None else GroupNode.canvas_id == canvas_id
        )
    )
    subs = session.scalars(
        select(SubInvestigation).where(
            SubInvestigation.parent_id.is_(None)
            if canvas_id is None
            else SubInvestigation.parent_id == canvas_id
        )
    )
    return [*nodes, *groups, *subs]


def _box(items: list[Item]) -> tuple[float, float, float, float] | None:
    if not items:
        return None
    xs = [i.pos_x for i in items]
    ys = [i.pos_y for i in items]
    return min(xs), min(ys), max(xs), max(ys)


def _shift(items: list[Item], dx: float, dy: float) -> None:
    for item in items:
        item.pos_x += dx
        item.pos_y += dy


def _refuse_loops(session: Session, items: list[Item], target: str | None) -> None:
    """Q61: a box cannot go into itself or into a box inside it."""
    if target is None:
        return
    for item in items:
        if isinstance(item, SubInvestigation) and target in within(session, item.id):
            raise RecordError(
                f"“{item.label or 'Sub-investigation'}” cannot go inside itself or a box it holds"
            )


def _check_faces(session: Session) -> None:
    """A face that is no longer inside its box is cleared."""
    for sub in list_all(session):
        if sub.face_id is None:
            continue
        face = session.get(Node, sub.face_id)
        if face is None or face.canvas_id not in within(session, sub.id):
            history.record(
                session, "sub_investigation", sub.id, "update", "face_id", sub.face_id, None
            )
            sub.face_id = None


def _target(session: Session, target_id: Any) -> str | None:
    if target_id is None:
        return None
    return get(session, SubInvestigation, target_id, "Sub-investigation").id


def move(session: Session, ids: Any, target_id: Any, with_groups: bool = False) -> int:
    """ "Move to…": nodes, groups and boxes go onto the canvas of `target_id` (None: the main
    canvas). On a canvas that already holds something they keep their arrangement, placed
    right of what is there; on an empty one they keep their positions. Returns how many boxes
    moved."""
    target = _target(session, target_id)
    items = _whole_groups(session, _items(session, ids), with_groups)
    _refuse_loops(session, items, target)
    items = [i for i in items if canvas_of(i) != target]
    if not items:
        return 0
    there = _box(_on_canvas(session, target))
    moved = _box(items)
    if there is not None and moved is not None:
        _shift(items, there[2] + GAP - moved[0], there[1] - moved[1])
    for item in items:
        _set_canvas(session, item, target)
    session.flush()
    _check_faces(session)
    session.flush()
    return len(items)


# ---------- the box itself ----------


def _face(session: Session, sub: SubInvestigation, value: Any) -> str | None:
    if value is None:
        return None
    node = get(session, Node, value, "Node")
    if node.canvas_id not in within(session, sub.id):
        raise RecordError("The face must be a node inside the sub-investigation")
    return node.id


def _first_face(session: Session, items: list[Item]) -> str | None:
    """The first node selected; for a group its representative or first member, for a box its
    face."""
    for item in items:
        if isinstance(item, Node):
            return item.id
        if isinstance(item, GroupNode):
            members = group_service.members(session, item.id)
            if item.representative_id or members:
                return item.representative_id or members[0].id
        elif item.face_id is not None:
            return item.face_id
    return None


def create(session: Session, fields: dict[str, Any]) -> SubInvestigation:
    """ "Move into new sub-investigation…": the box appears where the selection was, on its
    canvas, and the selection keeps its arrangement inside. With nothing selected, an empty box
    goes on `canvas_id` at `pos_x`, `pos_y`. The face defaults to the first node selected."""
    items = _whole_groups(
        session, _items(session, fields.get("ids") or []), bool(fields.get("with_groups"))
    )
    canvases = {canvas_of(i) for i in items}
    if len(canvases) > 1:
        raise RecordError("Select things on one canvas to make a sub-investigation of them")
    if items:
        parent_id = canvases.pop()
        x0, y0, x1, y1 = _box(items)  # type: ignore[misc]
        pos = ((x0 + x1) / 2, (y0 + y1) / 2)
    else:
        parent_id = _target(session, fields.get("canvas_id"))
        pos = (
            number_value("pos_x", fields.get("pos_x") or 0.0),
            number_value("pos_y", fields.get("pos_y") or 0.0),
        )
    sub = SubInvestigation(
        label=text_value("label", fields.get("label", "")).strip(),
        notes=text_value("notes", fields.get("notes", "")),
        parent_id=parent_id,
        pos_x=pos[0],
        pos_y=pos[1],
    )
    session.add(sub)
    session.flush()
    face_id = fields.get("face_id")
    if face_id is None:
        face_id = _first_face(session, items)
    elif face_id not in {n.id for n in _nodes_of(session, items)}:
        raise RecordError("The face must be one of the nodes moved into the sub-investigation")
    sub.face_id = face_id
    history.record(session, "sub_investigation", sub.id, "create", new=snapshot(sub))
    for item in items:
        _set_canvas(session, item, sub.id)
    session.flush()
    _check_faces(session)
    session.flush()
    return sub


def update(session: Session, sub_id: str, changes: dict[str, Any]) -> SubInvestigation:
    sub = get(session, SubInvestigation, sub_id, "Sub-investigation")
    for name, value in changes.items():
        if name in ("pos_x", "pos_y"):  # layout only; not in the history
            setattr(sub, name, number_value(name, value))
            continue
        if name in ("label", "notes"):
            value = text_value(name, value)
            value = value.strip() if name == "label" else value
        elif name == "face_id":
            value = _face(session, sub, value)
        else:
            raise RecordError(f"'{name}' cannot be edited")
        old = getattr(sub, name)
        if old != value:
            setattr(sub, name, value)
            history.record(session, "sub_investigation", sub.id, "update", name, old, value)
    session.flush()
    return sub


def dissolve(session: Session, sub_id: str) -> int:
    """Everything on the box's canvas goes back onto the canvas the box is on, centred where
    the box was; the box goes. Returns how many boxes came out."""
    sub = get(session, SubInvestigation, sub_id, "Sub-investigation")
    items = _on_canvas(session, sub.id)
    found = _box(items)
    if found is not None:
        x0, y0, x1, y1 = found
        _shift(items, sub.pos_x - (x0 + x1) / 2, sub.pos_y - (y0 + y1) / 2)
    history.record(session, "sub_investigation", sub.id, "dissolve", old=snapshot(sub))
    for item in items:
        _set_canvas(session, item, sub.parent_id)
    sub.face_id = None
    session.flush()
    session.delete(sub)
    session.flush()
    return len(items)


@dataclass
class DeletePreview:
    sub: SubInvestigation
    contents: Contents
    calculations: int
    transitions: list[Any]


def delete_preview(session: Session, sub_id: str) -> DeletePreview:
    """What "Delete with contents" removes, listed before the user confirms (as D54)."""
    sub = get(session, SubInvestigation, sub_id, "Sub-investigation")
    found = contents(session, sub.id)
    ids = [n.id for n in found.nodes]
    calculations = (
        len(list(session.scalars(select(Calculation.id).where(Calculation.node_id.in_(ids)))))
        if ids
        else 0
    )
    edges = transition_service.touching(session, [*ids, *(g.id for g in found.groups)])
    return DeletePreview(sub, found, calculations, edges)


def delete(session: Session, sub_id: str) -> dict[str, int]:
    """The box with everything inside it: its nodes with their calculations, its groups, the
    boxes inside it and every edge touching any of them, each in the history."""
    preview = delete_preview(session, sub_id)
    found = preview.contents
    for group in found.groups:
        group_service.delete_with_contents(session, group.id)
    for node in found.nodes:
        if session.get(Node, node.id) is not None:
            node_service.delete(session, node.id)
    # Innermost first, so no box is left pointing at a deleted parent.
    depth = {s.id: len(chain(session, s.id)) for s in [*found.subs, preview.sub]}
    for sub in sorted([*found.subs, preview.sub], key=lambda s: -depth[s.id]):
        history.record(session, "sub_investigation", sub.id, "delete", old=snapshot(sub))
        sub.face_id = None
        session.flush()
        session.delete(sub)
        session.flush()
    return {
        "nodes": len(found.nodes),
        "groups": len(found.groups),
        "sub_investigations": len(found.subs),
        "calculations": preview.calculations,
        "transitions": len(preview.transitions),
    }
