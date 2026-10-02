"""Node rules (docs/spec/02 §3): identity is the geometry, coordinates may be edited in place
only while a node has no calculations (ID-4), otherwise an edit creates a derived node
(ID-5, D23). Every change is written to the history in the same transaction."""

import math
from dataclasses import dataclass
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from chembook3d import xyz
from chembook3d.models import (
    Branch,
    Calculation,
    GroupNode,
    Node,
    NodeKind,
    ReactionStep,
    Role,
    Status,
    TransitionSpecies,
)
from chembook3d.services import history, transitions

EDITABLE_FIELDS = (
    "label",
    "role",
    "charge",
    "multiplicity",
    "status",
    "tags",
    "notes",
    "step_id",
    "branch_id",
)
# Canvas positions and the saved 3D orientation: how a node is shown, not recorded in history.
LAYOUT_FIELDS = ("pos_x", "pos_y", "view_rotation")
DERIVED_NODE_OFFSET = 60.0


class NodeError(ValueError):
    pass


class NodeNotFound(LookupError):
    pass


def _geometry_from_atoms(atoms: list[xyz.Atom]) -> list[list[Any]]:
    return [[a.element, a.x, a.y, a.z] for a in atoms]


def atoms_of(node: Node) -> list[xyz.Atom]:
    return [xyz.Atom(e, x, y, z) for e, x, y, z in (node.geometry or [])]


def calculation_count(session: Session, node_id: str) -> int:
    query = select(func.count()).select_from(Calculation).where(Calculation.node_id == node_id)
    return session.scalar(query) or 0


def snapshot(node: Node) -> dict[str, Any]:
    return {
        "label": node.label,
        "kind": node.kind,
        "role": node.role,
        "charge": node.charge,
        "multiplicity": node.multiplicity,
        "status": node.status,
        "tags": list(node.tags),
        "notes": node.notes,
        "geometry": node.geometry,
        "derived_from_id": node.derived_from_id,
        "step_id": node.step_id,
        "branch_id": node.branch_id,
        "group_id": node.group_id,
    }


def _validate_link(session: Session, node: Node | None, field: str, value: Any) -> Any:
    """FR-STEP-02: at most one step. P5, FR-BR-03: at most one branch, set only by the user.
    Group members keep and change their branch like any node (D66)."""
    if value is None:
        return None
    if node is not None and node.kind == NodeKind.SPECIES:
        raise NodeError("A free species has no reaction step or branch (D69)")
    if not isinstance(value, str):
        raise NodeError(f"{field} must be an id")
    model = ReactionStep if field == "step_id" else Branch
    if session.get(model, value) is None:
        raise NodeError("unknown reaction step" if field == "step_id" else "unknown branch")
    return value


def _validate(field: str, value: Any) -> Any:
    if field == "label" or field == "notes":
        if not isinstance(value, str):
            raise NodeError(f"{field} must be text")
        return value
    if field == "role":
        if value not in {r.value for r in Role}:
            raise NodeError(f"unknown role '{value}'")
        return value
    if field == "status":
        if value not in {s.value for s in Status}:
            raise NodeError(f"unknown status '{value}'")
        return value
    if field == "charge":
        if value is not None and (not isinstance(value, int) or isinstance(value, bool)):
            raise NodeError("charge must be a whole number")
        return value
    if field == "multiplicity":
        if value is not None and (
            not isinstance(value, int) or isinstance(value, bool) or value < 1
        ):
            raise NodeError("multiplicity must be a whole number of at least 1")
        return value
    if field == "tags":
        if not isinstance(value, list) or not all(isinstance(t, str) and t for t in value):
            raise NodeError("tags must be a list of non-empty text labels")
        return list(dict.fromkeys(t.strip() for t in value))  # unique, in order
    if field == "view_rotation":
        return _rotation(value)
    if field in LAYOUT_FIELDS:
        if not isinstance(value, int | float) or isinstance(value, bool):
            raise NodeError(f"{field} must be a number")
        return float(value)
    raise NodeError(f"'{field}' cannot be edited")


def _rotation(value: Any) -> list[float] | None:
    """A unit quaternion [x, y, z, w], normalised here, or None for the default orientation."""
    if value is None:
        return None
    if (
        not isinstance(value, list)
        or len(value) != 4
        or not all(isinstance(v, int | float) and not isinstance(v, bool) for v in value)
        or not all(math.isfinite(v) for v in value)
    ):
        raise NodeError("view_rotation must be a quaternion [x, y, z, w]")
    length = math.sqrt(sum(v * v for v in value))
    if length < 1e-9:
        raise NodeError("view_rotation must not be zero")
    return [float(v) / length for v in value]


def get(session: Session, node_id: str) -> Node:
    node = session.get(Node, node_id)
    if node is None:
        raise NodeNotFound(node_id)
    return node


def list_nodes(session: Session) -> list[Node]:
    return list(session.scalars(select(Node).order_by(Node.seq)))


def create(session: Session, fields: dict[str, Any], xyz_text: str | None = None) -> Node:
    """FR-NODE-01: no field is required. New nodes start as planned."""
    node = Node()
    kind = fields.pop("kind", NodeKind.NODE)
    if kind not in {k.value for k in NodeKind}:
        raise NodeError(f"unknown kind '{kind}'")
    node.kind = kind
    for field, value in fields.items():
        if field in ("step_id", "branch_id"):
            setattr(node, field, _validate_link(session, node, field, value))
        else:
            setattr(node, field, _validate(field, value))
    if xyz_text is not None and xyz_text.strip():
        node.geometry = _geometry_from_atoms(xyz.parse_xyz(xyz_text))
    session.add(node)
    session.flush()
    history.record(session, "node", node.id, "create", new=snapshot(node))
    return node


def update(session: Session, node_id: str, changes: dict[str, Any]) -> Node:
    node = get(session, node_id)
    for field, value in changes.items():
        if field not in EDITABLE_FIELDS and field not in LAYOUT_FIELDS:
            raise NodeError(f"'{field}' cannot be edited")
        if field in ("step_id", "branch_id"):
            value = _validate_link(session, node, field, value)
        else:
            value = _validate(field, value)
        old = getattr(node, field)
        if old == value:
            continue
        setattr(node, field, value)
        if field in EDITABLE_FIELDS:
            history.record(
                session,
                "node",
                node.id,
                "update",
                field,
                list(old) if field == "tags" else old,
                value,
            )
    session.flush()
    return node


@dataclass
class GeometryResult:
    node: Node
    derived: bool  # True when a new node was created (ID-5)


def set_geometry(session: Session, node_id: str, xyz_text: str) -> GeometryResult:
    """Save edited coordinates. Invalid text raises xyz.XyzParseError and changes nothing.
    Empty text removes the coordinates of a node with no calculations (D90)."""
    node = get(session, node_id)
    if not xyz_text.strip():
        return GeometryResult(clear_geometry(session, node), derived=False)
    geometry = _geometry_from_atoms(xyz.parse_xyz(xyz_text))

    if calculation_count(session, node.id) == 0:  # ID-4: edit in place
        if node.geometry != geometry:
            history.record(session, "node", node.id, "update", "geometry", node.geometry, geometry)
            node.geometry = geometry
            session.flush()
        return GeometryResult(node, derived=False)

    # ID-5: the original keeps its geometry and calculations; the edit becomes a new node.
    derived = Node(
        label=f"{node.label} (derived)" if node.label else "derived",
        kind=node.kind,
        role=node.role,
        charge=node.charge,
        multiplicity=node.multiplicity,
        status=Status.PLANNED,
        tags=[t for t in node.tags if t != "optimization-incomplete"],
        geometry=geometry,
        derived_from_id=node.id,
        # The derived node starts at the same place in the mechanism; the user can move it.
        step_id=node.step_id,
        branch_id=node.branch_id,
        pos_x=node.pos_x + DERIVED_NODE_OFFSET,
        pos_y=node.pos_y + DERIVED_NODE_OFFSET,
        view_rotation=node.view_rotation,
    )
    session.add(derived)
    session.flush()
    history.record(session, "node", derived.id, "create", new=snapshot(derived))
    return GeometryResult(derived, derived=True)


def clear_geometry(session: Session, node: Node) -> Node:
    """D90: a node with no calculations can go back to having no coordinates, recorded in the
    history like any edit. A node with calculations keeps its coordinates: they are the
    geometry its calculations belong to (ID-4, ID-5)."""
    if calculation_count(session, node.id) > 0:
        raise NodeError(
            "This node has calculations, so its coordinates cannot be removed: they are the "
            "geometry its calculations were run on"
        )
    if node.geometry is not None:
        history.record(session, "node", node.id, "update", "geometry", node.geometry, None)
        node.geometry = None
        session.flush()
    return node


def delete(session: Session, node_id: str) -> dict[str, Any]:
    """Delete a node, its calculations and the transitions touching it. The API lists all of
    them for confirmation first (INV-7, P3, NFR-UX-01); no other edge is rewired."""
    node = get(session, node_id)
    edges = transitions.touching(session, [node.id])
    attached = session.scalars(
        select(TransitionSpecies).where(TransitionSpecies.species_id == node.id)
    ).all()
    removed = {
        "node": node.id,
        "calculations": calculation_count(session, node.id),
        "transitions": [t.id for t in edges],
        # D69: a free species is taken off the transitions it joins or leaves on.
        "species_on": [e.transition_id for e in attached],
    }
    for entry in attached:
        transition = entry.transition
        before = transitions.snapshot(transition)["species"]
        transition.species.remove(entry)
        session.flush()
        after = transitions.snapshot(transition)["species"]
        history.record(session, "transition", transition.id, "update", "species", before, after)
    transitions.remove(session, edges)
    if node.group_id is not None:
        group = session.get(GroupNode, node.group_id)
        if group is not None and group.representative_id == node.id:
            history.record(session, "group", group.id, "update", "representative_id", node.id, None)
            group.representative_id = None
    history.record(session, "node", node.id, "delete", old=snapshot(node))
    session.delete(node)
    session.flush()
    return removed


def to_xyz(node: Node) -> str:
    if not node.geometry:
        raise NodeError("node has no coordinates")
    return xyz.format_xyz(atoms_of(node), comment=node.label)
