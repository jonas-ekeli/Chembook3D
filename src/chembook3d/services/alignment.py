"""Overlays on chosen atoms and saved alignment sets (FR-3D-04, FR-3D-07, D80, A31).

Atom numbers here are those the user sees: 1-based, of the full structure (hidden hydrogens
do not shift them, D75). An alignment set keeps one atom list per node. Like positions, sets
are layout-like data: they are saved in the investigation but not in the history (A31), and
deleting a node takes it out of every set (the database cascades).
"""

from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from chembook3d.models import AlignmentSet, AlignmentSetAtoms, Node
from chembook3d.services import geometry
from chembook3d.services.records import RecordError, get, text_value

MAX_STRUCTURES = 12  # A31: more is hard to read
MIN_ATOMS = 3  # fewer atoms do not fix a rotation
ALIGNMENTS = ("all", "atoms", "none")


@dataclass
class OverlayItem:
    node: Node
    reference: bool
    placed: geometry.Placed | None  # None for the reference itself


def _atoms(node: Node, numbers: Any, what: str = "") -> list[int]:
    """Validated 1-based atom numbers of `node`, returned 0-based."""
    name = node.label or "Untitled node"
    if not isinstance(numbers, list) or not all(
        isinstance(n, int) and not isinstance(n, bool) for n in numbers
    ):
        raise RecordError(f"{what}the atoms of “{name}” must be a list of atom numbers")
    count = len(node.geometry or [])
    if len(numbers) < MIN_ATOMS:
        raise RecordError(f"{what}give at least {MIN_ATOMS} atoms for “{name}”")
    beyond = [n for n in numbers if n < 1 or n > count]
    if beyond:
        raise RecordError(
            f"{what}“{name}” has atoms 1 to {count}, so atom {beyond[0]} does not exist"
        )
    if len(set(numbers)) != len(numbers):
        twice = next(n for n in numbers if numbers.count(n) > 1)
        raise RecordError(f"{what}atom {twice} of “{name}” is listed twice")
    return [n - 1 for n in numbers]


def overlay(
    session: Session,
    node_ids: list[str],
    reference_id: str | None = None,
    align: str = "all",
    atoms: dict[str, list[int]] | None = None,
    allow_mirror: bool = False,
) -> list[OverlayItem]:
    """FR-3D-04, D80: every node placed on the reference (the first, or `reference_id`), in the
    order given. With `align == "atoms"`, `atoms` lists each node's alignment atoms, paired
    in order with the reference's. With "none", atoms are optional and only measured."""
    if align not in ALIGNMENTS:
        raise RecordError(f"unknown alignment '{align}'")
    if len(set(node_ids)) != len(node_ids):
        raise RecordError("a structure is listed twice")
    if not 2 <= len(node_ids) <= MAX_STRUCTURES:
        raise RecordError(f"an overlay holds 2 to {MAX_STRUCTURES} structures")
    nodes = [get(session, Node, node_id, "Node") for node_id in node_ids]
    missing = [n for n in nodes if not n.geometry]
    if missing:
        names = ", ".join(f"“{n.label or 'Untitled node'}”" for n in missing)
        raise RecordError(f"{names} has no coordinates to overlay")
    reference_id = reference_id or node_ids[0]
    if reference_id not in node_ids:
        raise RecordError("the reference must be one of the overlaid structures")
    reference = next(n for n in nodes if n.id == reference_id)
    atoms = atoms or {}
    chosen: dict[str, list[int]] = {}
    if align == "atoms" or (align == "none" and atoms):
        for node in nodes:
            if node.id not in atoms:
                raise RecordError(
                    f"choose the alignment atoms of “{node.label or 'Untitled node'}”"
                )
            chosen[node.id] = _atoms(node, atoms[node.id])
    items = []
    for node in nodes:
        if node.id == reference.id:
            items.append(OverlayItem(node, True, None))
            continue
        try:
            placed = geometry.place(
                reference.geometry,  # type: ignore[arg-type]
                node.geometry,  # type: ignore[arg-type]
                align,
                chosen.get(reference.id),
                chosen.get(node.id),
                allow_mirror,
            )
        except geometry.AlignmentError as exc:
            raise RecordError(f"“{node.label or 'Untitled node'}”: {exc}") from exc
        items.append(OverlayItem(node, False, placed))
    return items


# ---------- alignment sets (FR-3D-07) ----------


def list_sets(session: Session) -> list[AlignmentSet]:
    return list(session.scalars(select(AlignmentSet).order_by(AlignmentSet.name, AlignmentSet.id)))


def atoms_of(alignment_set: AlignmentSet) -> dict[str, list[int]]:
    return {entry.node_id: list(entry.atoms) for entry in alignment_set.entries}


def _name(session: Session, value: Any, own_id: str | None = None) -> str:
    name = text_value("name", value).strip()
    if not name:
        raise RecordError("an alignment set needs a name")
    clash = session.scalar(select(AlignmentSet).where(AlignmentSet.name == name))
    if clash is not None and clash.id != own_id:
        raise RecordError(f"there is already an alignment set called “{name}”")
    return name


def _set_atoms(session: Session, alignment_set: AlignmentSet, atoms: dict[str, Any]) -> None:
    """A list sets a node's atoms; None takes the node out of the set."""
    entries = {entry.node_id: entry for entry in alignment_set.entries}
    for node_id, numbers in atoms.items():
        if numbers is None:
            if node_id in entries:
                alignment_set.entries.remove(entries.pop(node_id))
            continue
        node = get(session, Node, node_id, "Node")
        valid = [n + 1 for n in _atoms(node, numbers)]
        if node_id in entries:
            entries[node_id].atoms = valid
        else:
            entry = AlignmentSetAtoms(node_id=node_id, atoms=valid)
            alignment_set.entries.append(entry)
            entries[node_id] = entry


def create_set(session: Session, name: Any, atoms: dict[str, Any]) -> AlignmentSet:
    alignment_set = AlignmentSet(name=_name(session, name))
    session.add(alignment_set)
    _set_atoms(session, alignment_set, atoms)
    session.flush()
    return alignment_set


def update_set(session: Session, set_id: str, changes: dict[str, Any]) -> AlignmentSet:
    alignment_set = get(session, AlignmentSet, set_id, "Alignment set")
    for field in changes:
        if field not in ("name", "atoms"):
            raise RecordError(f"'{field}' cannot be edited")
    if "name" in changes:
        alignment_set.name = _name(session, changes["name"], alignment_set.id)
    if "atoms" in changes:
        _set_atoms(session, alignment_set, changes["atoms"] or {})
    session.flush()
    return alignment_set


def delete_set(session: Session, set_id: str) -> None:
    session.delete(get(session, AlignmentSet, set_id, "Alignment set"))
    session.flush()
