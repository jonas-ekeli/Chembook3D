"""A scan path between two connected nodes, run as a cloud calculation job (D114, A60).

The user selects two nodes (or groups) joined by an edge and presses "Scan path…". `plan`
checks the ends, matches the end's atoms to the start's numbering (D113) and, for an end that
is a transition state, suggests the coordinates to hold at its values so xTB does not relax
it away: from its imaginary mode when it has a frequency job, otherwise the partial bonds of
the guess and the coordinates that differ most from the other end, for the user to tick.
`create_job` writes the cloud job (D93): the start, the end renumbered in the start's order,
the match, the held coordinates and settings, and instructions that let the cloud session
design the path itself with GFN2-xTB relaxed scans and the helper `pathtools.py`. Its result,
`outputs/path.xyz`, imports as a scan path (D112).
"""

import json
import re
import textwrap
import threading
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from chembook3d import cloud_jobs, pathtools
from chembook3d.models import GroupNode, Node, Role, Status, Transition
from chembook3d.services import atom_matching, imports, scan_species, species
from chembook3d.services.geometry import _coords, _fit
from chembook3d.services.records import RecordError, get

# xTB's ALPB solvents, and the names other programs give some of them (Gaussian, ORCA).
ALPB_SOLVENTS = {
    "acetone", "acetonitrile", "aniline", "benzaldehyde", "benzene", "ch2cl2", "chcl3", "cs2",
    "dioxane", "dmf", "dmso", "ether", "ethylacetate", "furane", "hexandecane", "hexane",
    "methanol", "nitromethane", "octanol", "woctanol", "phenol", "toluene", "thf", "water",
}  # fmt: skip
SOLVENT_NAMES = {
    "dichloromethane": "ch2cl2", "methylenechloride": "ch2cl2", "chloroform": "chcl3",
    "trichloromethane": "chcl3", "tetrahydrofuran": "thf", "diethylether": "ether",
    "n,n-dimethylformamide": "dmf", "dimethylformamide": "dmf", "dimethylsulfoxide": "dmso",
    "1,4-dioxane": "dioxane", "carbondisulfide": "cs2", "h2o": "water",
    "ethylethanoate": "ethylacetate",
    "n-hexane": "hexane", "1-octanol": "octanol", "n-octanol": "octanol", "furan": "furane",
    "hexadecane": "hexandecane", "n-hexadecane": "hexandecane", "mecn": "acetonitrile",
}  # fmt: skip

MODE_SHARE = 0.35  # a distance is suggested when it changes this much of the most-changing one
MAX_SUGGESTED = 6
PARTIAL = (1.2, 1.8)  # × the sum of covalent radii: a bond forming or breaking at a TS
KINDS = {2: "distance", 3: "angle", 4: "dihedral"}
MAX_HELD = 12
MAX_DRIVE = 8  # the user's own coordinates (D119)
DRIVE_ORDERS = ("together", "staged")  # one concerted scan, or one stage per row in order


@dataclass
class End:
    node: Node
    group: GroupNode | None  # the group selected, when its member stands for it
    members: list[Node] = field(default_factory=list)  # the group's members with coordinates

    @property
    def name(self) -> str:
        return self.node.label or "Untitled node"

    def ids(self) -> set[str]:
        """The ids an edge to this end can name: the node, and the group it is in."""
        found = {self.node.id}
        if self.group is not None:
            found.add(self.group.id)
        if self.node.group_id:
            found.add(self.node.group_id)
        return found


def _members(session: Session, group: GroupNode) -> list[Node]:
    query = select(Node).where(Node.group_id == group.id).order_by(Node.group_position, Node.seq)
    return [n for n in session.scalars(query) if n.geometry]


def resolve(session: Session, item_id: str, member_id: str | None = None) -> End:
    """A node, or a group standing for its representative (or `member_id`, one of its
    members)."""
    group = session.get(GroupNode, item_id)
    if group is None:
        return End(get(session, Node, item_id, "Node"), None)
    members = _members(session, group)
    if not members:
        raise RecordError(f"no member of group “{group.label or 'Group'}” has coordinates")
    chosen = member_id or group.representative_id
    node = next((n for n in members if n.id == chosen), None)
    if member_id and node is None:
        raise RecordError("the member chosen is not in that group or has no coordinates")
    return End(node or members[0], group, members)


def edge_between(session: Session, a: End, b: End) -> Transition | None:
    """An edge joining the two ends, in either direction, through their groups too (D108)."""
    ids_a, ids_b = a.ids(), b.ids()
    query = select(Transition).where(
        or_(
            Transition.source_node_id.in_(ids_a | ids_b),
            Transition.source_group_id.in_(ids_a | ids_b),
        )
    )
    for edge in session.scalars(query.order_by(Transition.seq)):
        if (edge.source_id in ids_a and edge.target_id in ids_b) or (
            edge.source_id in ids_b and edge.target_id in ids_a
        ):
            return edge
    return None


def solvent_of(node: Node) -> str | None:
    """xTB's ALPB name for the solvent of the node's latest calculation, if it has one."""
    for calc in sorted(node.calculations, key=lambda c: c.created_at, reverse=True):
        level = calc.level
        if level is None or not level.solvent:
            continue
        name = level.solvent.strip().lower().replace(" ", "")
        name = SOLVENT_NAMES.get(name, name)
        return name if name in ALPB_SOLVENTS else None
    return None


def imaginary_mode(node: Node) -> tuple[float, list[list[float]]] | None:
    """The node's latest imaginary mode (wavenumber, [atom][xyz]) for its own geometry."""
    calcs = sorted(node.calculations, key=lambda c: c.created_at, reverse=True)
    for calc in calcs:
        result = calc.result
        if result is None or not result.frequencies or result.frequencies[0] >= 0:
            continue
        modes = result.normal_modes
        if not modes or len(modes[0]) != len(node.geometry or []):
            continue
        return float(result.frequencies[0]), modes[0]
    return None


def has_frequency_job(node: Node) -> bool:
    return any(c.result is not None and c.result.frequencies for c in node.calculations)


def value(rows: list[list[Any]], atoms: list[int]) -> float:
    """A distance (Å), angle or dihedral (degrees) of 1-based atoms."""
    return pathtools.measure(rows, atoms)


def _radii(rows) -> np.ndarray:
    return np.array([atom_matching.RADII.get(r[0], atom_matching.DEFAULT_RADIUS) for r in rows])


def _distances(rows) -> np.ndarray:
    xyz = np.array([r[1:4] for r in rows], dtype=float)
    return np.linalg.norm(xyz[:, None, :] - xyz[None, :, :], axis=2)


def _close_in_graph(adjacency: np.ndarray) -> np.ndarray:
    """Pairs at most two bonds apart (bonded or sharing a neighbour)."""
    a = adjacency.astype(int)
    return (a + a @ a) > 0


def suggest(
    ts_rows: list[list[Any]],
    other_rows: list[list[Any]],
    mode: list[list[float]] | None,
    changed: list[list[int]],
) -> list[dict[str, Any]]:
    """Coordinates to hold at a TS end (1-based, in the start's numbering), ticked when they
    come from the imaginary mode; the partial bonds and largest differences of a guess are left
    for the user to tick (D114)."""
    n = len(ts_rows)
    d = _distances(ts_rows)
    radii = _radii(ts_rows)
    reach = radii[:, None] + radii[None, :]
    found: dict[tuple[int, int], dict[str, Any]] = {}

    def add(i: int, j: int, ticked: bool, why: str) -> None:
        key = (min(i, j), max(i, j))
        if key in found or len(found) >= MAX_SUGGESTED:
            return
        atoms = [key[0] + 1, key[1] + 1]
        found[key] = {"kind": "distance", "atoms": atoms, "ticked": ticked, "why": why}

    if mode is not None:
        u = np.array(mode, dtype=float)
        xyz = np.array([r[1:4] for r in ts_rows], dtype=float)
        candidates = []
        for i in range(n):
            for j in range(i + 1, n):
                if d[i, j] < PARTIAL[1] * reach[i, j]:
                    along = np.dot(u[i] - u[j], xyz[i] - xyz[j]) / d[i, j]
                    candidates.append((abs(along), i, j))
        candidates.sort(reverse=True)
        if candidates and candidates[0][0] > 0:
            top = candidates[0][0]
            for share, i, j in candidates:
                if share < MODE_SHARE * top:
                    break
                add(i, j, True, "changes along the imaginary mode")
        for a, b in changed:
            add(a - 1, b - 1, False, "a bond that forms or breaks between the ends")
        return list(found.values())

    for a, b in changed:
        i, j = a - 1, b - 1
        if PARTIAL[0] * reach[i, j] <= d[i, j] < PARTIAL[1] * reach[i, j]:
            add(i, j, False, "a partial bond of the guess that forms or breaks")
    close = _close_in_graph(
        atom_matching.bonds(np.array([r[1:4] for r in ts_rows]), [r[0] for r in ts_rows])
    )
    partial = [
        (d[i, j] / reach[i, j], i, j)
        for i in range(n)
        for j in range(i + 1, n)
        if PARTIAL[0] * reach[i, j] <= d[i, j] < PARTIAL[1] * reach[i, j] and not close[i, j]
    ]
    other = _distances(other_rows)
    partial.sort(key=lambda item: -abs(other[item[1], item[2]] - d[item[1], item[2]]))
    for _, i, j in partial[:3]:
        add(i, j, False, "a partial bond of the guess")
    for a, b in changed:
        add(a - 1, b - 1, False, "a bond that forms or breaks between the ends")
    return list(found.values())


def _charge_multiplicity(start: End, end: End) -> tuple[int, int]:
    values = {}
    for what in ("charge", "multiplicity"):
        a, b = getattr(start.node, what), getattr(end.node, what)
        if a is not None and b is not None and a != b:
            raise RecordError(
                f"“{start.name}” and “{end.name}” have different {what}s ({a} and {b}); a path "
                "keeps the same electrons"
            )
        known = a if a is not None else b
        if known is None:
            raise RecordError(
                f"neither “{start.name}” nor “{end.name}” has a {what}; set it on either node"
            )
        values[what] = known
    return values["charge"], values["multiplicity"]


@dataclass
class Plan:
    start: End
    end: End
    edge: Transition
    match: atom_matching.Match
    renumbered: list[list[Any]]  # the path's end in the start's numbering
    charge: int
    multiplicity: int
    solvent: str | None
    ts_ends: list[dict[str, Any]]
    # A TS end's imaginary mode (wavenumber, vectors) in the start's numbering, turned with
    # the end as it is placed on the start, for `inputs/<end>_mode.json` (D116).
    modes: dict[str, tuple[float, list[list[float]]]] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    # D120: the species joining or leaving, and the path's start in the start's numbering
    # (the separated structure when the species joins; else the start node's geometry).
    species: scan_species.Joining | None = None
    start_rows: list[list[Any]] = field(default_factory=list)
    # The two structures the match was made on, in their own numbering (the start beside the
    # species, or the end beside it), for the review.
    match_rows: tuple[list[list[Any]], list[list[Any]]] | None = None

    def __post_init__(self):
        if not self.start_rows:
            self.start_rows = self.start.node.geometry


NO_BOND_CHANGE = (
    "No bond forms or breaks between these two structures by the app's rule, so the path will "
    "mostly change their conformation. Check that these are the two structures you meant; a "
    "path that ends in a nearby conformer is returned with a note."
)


def _turned(vectors: list[list[float]], stored: list[list[Any]], placed: list[list[Any]]):
    """Mode vectors of `stored` turned the way `stored` was turned onto `placed`."""
    a, b = _coords(stored), _coords(placed)
    rotation, _ = _fit(a - a.mean(axis=0), b - b.mean(axis=0), allow_mirror=False)
    return (np.array(vectors, dtype=float) @ rotation).tolist()


def _formula(counts: Counter) -> str:
    order = sorted(counts, key=lambda e: (e != "C", e != "H", e))
    return "".join(f"{e}{counts[e] if counts[e] > 1 else ''}" for e in order)


def _species_charge(small: End, bound: End, joining: Node) -> tuple[int, int]:
    """The path's charge and multiplicity with a species (D120): the bound end's, which must
    be the small end's charge plus the species'."""
    parts = [small.node.charge, joining.charge]
    if bound.node.charge is not None and all(c is not None for c in parts):
        if sum(parts) != bound.node.charge:
            raise RecordError(
                f"“{small.name}” ({small.node.charge}) with “{species.label(joining)}” "
                f"({joining.charge}) does not have the charge of “{bound.name}” "
                f"({bound.node.charge})"
            )
    charge = bound.node.charge
    if charge is None and all(c is not None for c in parts):
        charge = sum(parts)
    multiplicity = bound.node.multiplicity or small.node.multiplicity
    if charge is None or multiplicity is None:
        what = "charge" if charge is None else "multiplicity"
        raise RecordError(f"“{bound.name}” has no {what}; set it on the node")
    return charge, multiplicity


def _joining(
    session: Session,
    start: End,
    end: End,
    edge: Transition,
    pairs: list[list[int]] | None,
    clearance: float,
) -> tuple[atom_matching.Match, scan_species.Joining, list[list[Any]], list[list[Any]], tuple]:
    """D120: the species on the edge matched across the two ends, and the separated end.
    Returns the match, the species, the path's start and end in the start's numbering and the
    two structures the match was made on."""
    a, b = start.node.geometry, end.node.geometry
    found = scan_species.on_edge(session, edge, start.ids())
    if found is None:
        big, small = (a, b) if len(a) > len(b) else (b, a)
        missing = Counter(r[0] for r in big) - Counter(r[0] for r in small)
        extra = Counter(r[0] for r in small) - Counter(r[0] for r in big)
        what = f": {_formula(missing)} joins or leaves" if missing and not extra else ""
        raise RecordError(
            f"“{start.name}” has {len(a)} atoms and “{end.name}” {len(b)}{what}. Attach the "
            "species that joins or leaves to the edge as a free species, and the scan path "
            "follows it"
        )
    node, direction = found
    if not node.geometry:
        raise RecordError(f"the free species “{species.label(node)}” has no coordinates")
    if not scan_species.CLEARANCE_RANGE[0] <= clearance <= scan_species.CLEARANCE_RANGE[1]:
        low, high = scan_species.CLEARANCE_RANGE
        raise RecordError(f"the separated species' closest contact is {low:g} to {high:g} Å")
    joins = direction == species.JOINS
    small, bound = (start, end) if joins else (end, start)
    if small.node.role == Role.TRANSITION_STATE:
        raise RecordError(
            f"“{small.name}” is a TS without “{species.label(node)}”; with a species that joins "
            "or leaves, a TS end is the one where the species is bound"
        )
    scan_species.check_atoms(
        small.node.geometry,
        node.geometry,
        bound.node.geometry,
        (small.name, species.label(node), bound.name),
    )
    match, start_side, end_side = scan_species.matched(
        a, b, node.geometry, direction, atom_matching.pairs_from(pairs or [])
    )
    n_small = len(small.node.geometry)
    atoms = scan_species.species_atoms(match, direction, n_small, len(start_side))
    bonds = scan_species.crossing(match, atoms)
    if joins:
        bound_rows = [end_side[j] for j in match.mapping]  # the end's own coordinates
        own = start_side
    else:
        bound_rows = [list(r) for r in a]
        own = [end_side[j] for j in match.mapping]
    separated, pull, closest, anchor, complex_rmsd = scan_species.separate(
        bound_rows, own, atoms, bonds, clearance
    )
    match = scan_species.steady_doubts(match, complex_rmsd, bonds, anchor)
    joining = scan_species.Joining(
        node=node,
        direction=direction,
        bound="end" if joins else "start",
        atoms=[i + 1 for i in atoms],
        bonds=[[i + 1, j + 1] for i, j in bonds],
        anchor=[i + 1 for i in anchor],
        separated=separated,
        clearance=clearance,
        pull=pull,
        closest=closest,
        complex_rmsd=complex_rmsd,
    )
    if joins:
        # The review shows the separated start (the start's numbering) beside the end.
        return match, joining, separated, bound_rows, (separated, end_side)
    in_end_order = [None] * len(end_side)
    for i, j in enumerate(match.mapping):
        in_end_order[j] = separated[i]
    return match, joining, bound_rows, separated, (bound_rows, in_end_order)


def plan(
    session: Session,
    start_id: str,
    end_id: str,
    pairs: list[list[int]] | None = None,
    start_member_id: str | None = None,
    end_member_id: str | None = None,
    clearance: float = scan_species.CLEARANCE,
) -> Plan:
    """D114: the checks, the atom match and the suggested TS coordinates for a scan path; with
    a species joining or leaving on the edge (D120), the separated end, its species
    `clearance` Å from the complex."""
    if start_id == end_id:
        raise RecordError("choose two different structures")
    start = resolve(session, start_id, start_member_id)
    end = resolve(session, end_id, end_member_id)
    if start.node.id == end.node.id:
        raise RecordError("both ends are the same structure")
    for item in (start, end):
        if not item.node.geometry:
            raise RecordError(f"“{item.name}” has no coordinates")
    edge = edge_between(session, start, end)
    if edge is None:
        raise RecordError("a scan path runs along an edge; draw one between the two first")
    a, b = start.node.geometry, end.node.geometry
    joining = None
    match_rows = None
    if len(a) != len(b) or edge.species:
        match, joining, start_rows, renumbered, match_rows = _joining(
            session, start, end, edge, pairs, clearance
        )
        small, bound = (start, end) if joining.direction == species.JOINS else (end, start)
        charge, multiplicity = _species_charge(small, bound, joining.node)
    else:
        charge, multiplicity = _charge_multiplicity(start, end)
        match = atom_matching.match(a, b, atom_matching.pairs_from(pairs or []))
        renumbered = atom_matching.renumbered(a, b, match.mapping)
        start_rows = a
    changed = [[i + 1, j + 1] for i, j in match.formed + match.broken]
    ts_ends = []
    modes = {}
    # The end in the start's order as it is stored (not fitted), so its mode vectors fit it.
    for which, item in (("start", start), ("end", end)):
        if item.node.role != Role.TRANSITION_STATE:
            continue
        own = item.node.geometry
        reordered = own if which == "start" else [own[j] for j in match.mapping]
        rows, other = (start_rows, renumbered) if which == "start" else (reordered, start_rows)
        mode = imaginary_mode(item.node)
        renumbered_mode = None
        wavenumber = None
        if mode is not None:
            wavenumber, vectors = mode
            renumbered_mode = vectors if which == "start" else [vectors[j] for j in match.mapping]
            modes[which] = (
                wavenumber,
                renumbered_mode
                if which == "start"
                else _turned(renumbered_mode, reordered, renumbered),
            )
        suggested = suggest(rows, other, renumbered_mode, changed)
        for row in suggested:
            row["start_value"] = value(start_rows, row["atoms"])
            row["end_value"] = value(renumbered, row["atoms"])
        ts_ends.append(
            {
                "end": which,
                "node_id": item.node.id,
                "label": item.name,
                "imaginary": wavenumber,
                "guess": mode is None,
                "frequency_job": has_frequency_job(item.node),
                "suggested": suggested,
            }
        )
    solvent = solvent_of(start.node) or solvent_of(end.node)
    warnings = [] if match.formed or match.broken else [NO_BOND_CHANGE]
    return Plan(
        start,
        end,
        edge,
        match,
        renumbered,
        charge,
        multiplicity,
        solvent,
        ts_ends,
        modes,
        warnings,
        joining,
        start_rows,
        match_rows,
    )


def check_held(plan_: Plan, held: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The coordinates to hold, checked: which TS end, 2 to 4 atoms of the structure; with
    their values at that end."""
    ts = {t["end"]: t for t in plan_.ts_ends}
    if len(held) > MAX_HELD:
        raise RecordError(f"hold at most {MAX_HELD} coordinates")
    count = len(plan_.start_rows)
    out = []
    for row in held:
        which, atoms = row.get("end"), row.get("atoms")
        if which not in ts:
            raise RecordError("coordinates are held only at an end that is a transition state")
        if (
            not isinstance(atoms, list)
            or len(atoms) not in KINDS
            or not all(isinstance(x, int) and 1 <= x <= count for x in atoms)
            or len(set(atoms)) != len(atoms)
        ):
            raise RecordError(f"a held coordinate names 2 to 4 different atoms from 1 to {count}")
        rows, other = plan_.start_rows, plan_.renumbered
        if which == "end":
            rows, other = other, rows
        out.append(
            {
                "end": which,
                "kind": KINDS[len(atoms)],
                "atoms": atoms,
                "value": value(rows, atoms),
                "other_value": value(other, atoms),
            }
        )
    missing = [
        t["label"]
        for t in plan_.ts_ends
        if t["guess"] and not any(r["end"] == t["end"] for r in out)
    ]
    if missing:
        raise RecordError(
            f"“{missing[0]}” is a TS guess with no imaginary mode to read: tick the coordinates "
            "that make it a TS"
        )
    return out


def _same(a: list[int], b: list[int]) -> bool:
    return a == b or a == b[::-1]


def check_drive(
    plan_: Plan, drive: list[dict[str, Any]], order: str, held: list[dict[str, Any]]
) -> dict[str, Any] | None:
    """The user's own coordinates to drive (D119), checked: 2 to 4 atoms of the structure,
    each from its `from` value (the start's, unless given) to its `to` value (the end's, unless
    given; a dihedral's may lie past ±180°, scanned as written). None when there are none."""
    if not drive:
        return None
    if order not in DRIVE_ORDERS:
        raise RecordError("drive the coordinates together or staged, in the order given")
    if len(drive) > MAX_DRIVE:
        raise RecordError(f"drive at most {MAX_DRIVE} coordinates")
    count = len(plan_.start_rows)
    out: list[dict[str, Any]] = []
    for row in drive:
        atoms = row.get("atoms")
        if (
            not isinstance(atoms, list)
            or len(atoms) not in KINDS
            or not all(isinstance(x, int) and 1 <= x <= count for x in atoms)
            or len(set(atoms)) != len(atoms)
        ):
            raise RecordError(
                f"a coordinate to drive names 2 to 4 different atoms from 1 to {count}"
            )
        kind = KINDS[len(atoms)]
        name = f"{kind} {'–'.join(map(str, atoms))}"
        if any(_same(atoms, h["atoms"]) for h in held):
            raise RecordError(
                f"{name} is held at the TS, so it is driven to the other end's value already"
            )
        if any(_same(atoms, o["atoms"]) for o in out):
            raise RecordError(f"{name} is listed twice")
        start_value = value(plan_.start_rows, atoms)
        end_value = value(plan_.renumbered, atoms)
        ends = []
        for key, default in (("from", start_value), ("to", end_value)):
            given = row.get(key)
            number = default if given is None else float(given)
            if not np.isfinite(number):
                raise RecordError(f"{name}: give a number")
            if kind == "distance" and not 0.5 <= number <= 15:
                raise RecordError(f"{name}: a distance from 0.5 to 15 Å")
            if kind == "angle" and not 0 < number < 180:
                raise RecordError(f"{name}: an angle between 0° and 180°")
            if kind == "dihedral" and not -540 <= number <= 540:
                raise RecordError(f"{name}: a dihedral from −540° to 540°")
            ends.append(number)
        out.append(
            {
                "kind": kind,
                "atoms": atoms,
                "from": round(ends[0], 4),
                "to": round(ends[1], 4),
                "start_value": round(start_value, 4),
                "end_value": round(end_value, 4),
            }
        )
    return {"order": order, "coordinates": out}


def _drive_text(drive: dict[str, Any]) -> str:
    lines = []
    for n, row in enumerate(drive["coordinates"], start=1):
        unit = "Å" if row["kind"] == "distance" else "°"
        atoms = ", ".join(str(a) for a in row["atoms"])
        lead = f"{n}. " if drive["order"] == "staged" else "- "
        lines.append(
            f"{lead}{row['kind']} {atoms}: from {row['from']:.3f} to {row['to']:.3f} {unit} "
            f"(start {row['start_value']:.3f}, end {row['end_value']:.3f} {unit})"
        )
    return "\n".join(lines)


def _xyz(rows, comment: str) -> str:
    return pathtools.format_frame(rows, comment)


def _held_text(held: list[dict[str, Any]], plan_: Plan) -> str:
    if not held:
        return "Neither end is a transition state: no coordinate is held."
    lines = []
    for row in held:
        name = plan_.start.name if row["end"] == "start" else plan_.end.name
        other = "end" if row["end"] == "start" else "start"
        unit = "Å" if row["kind"] == "distance" else "°"
        atoms = "–".join(str(a) for a in row["atoms"])
        lines.append(
            f"- At the {row['end']} (“{name}”, a TS): {row['kind']} {atoms} = "
            f"{row['value']:.3f} {unit}; {row['other_value']:.3f} {unit} at the {other}"
        )
    return "\n".join(lines)


INSTRUCTIONS = """\
This is a **scan path job** (Chembook3D D114, D116, D118). Find the best path of relaxed GFN2-xTB
structures from `inputs/start.xyz` to `inputs/end.xyz` and return it as `outputs/path.xyz`.
Chembook3D imports it as a new node whose scan plays as a movie (D112). Paths here are
relative to this job's folder, where the commands below are run from; the helper is
`../../.claude/chembook3d/pathtools.py` from there (Python, standard library only; run it as a
script, `python3 ../../.claude/chembook3d/pathtools.py <command>`, here written `pathtools.py`).
Atoms are numbered from 1 everywhere, as xTB numbers them.

## The two ends

- `inputs/start.xyz`: “{start}”{start_ts}{start_apart}.
- `inputs/end.xyz`: “{end}”{end_ts}{end_apart}, renumbered by the app in the start's atom order
  and fitted on the start. The atoms correspond one to one; never renumber them.
- `inputs/mapping.json`: the match. `formed` and `broken` are the bonds (start numbering) that
  form and break between the ends by the app's rule; `mapping` gives each start atom's number
  in the end's own file.
- `inputs/path.json`: the settings below, machine-readable, and `active`: the {active_count}
  atoms the end is judged on (below).{species_file}{drive_file}{mode_files}

Charge {charge}, multiplicity {multiplicity}: run every xtb call with
`--gfn 2 --chrg {charge} --uhf {uhf}{solvent_flag}`{solvent_text}.

`pathtools.py diff inputs/start.xyz inputs/end.xyz` lists the bonds that form and break and the
distances and dihedrals that change most; start there.{no_bond_change}

## Coordinates the user holds at a TS end

{held}

A TS end is a TS only along these coordinates, and xTB relaxes it away from them if they are
left free. Holding them means the path passes through the TS's values, not that they stay
fixed: at the TS end of the path they are at the TS's values (within 0.05 Å or 2°), and from
there they are driven to the other end's values, usually as the path's reaction coordinate.
Never optimise the TS end without them in `$constrain`.
{species}{drive}
## Running xTB scans

A stage is `xtb <structure>.xyz --opt --input scan.inp <the flags above> > scan.out 2>&1`;
xTB writes the optimised structure of each point to `xtbscan.log`. Everything you hold or
drive goes in `$constrain` (atoms from 1); `$scan` drives constraints by their **position in
the `$constrain` block** (1 = the first line after `force constant`), so list the scanned
ones first. One scanned distance and one held distance:

    $constrain
      force constant=1.0
      distance: 3, 7, 2.10
      distance: 2, 5, 1.95
    $scan
      1: 2.10, 1.54, 20
    $end

Add `mode=concerted` under `$scan` to drive several lines together (`1: ...` and `2: ...`, the
same number of points). Force constants of 0.5 to 2 Eh/bohr² work; xTB's restraints lag their
targets a little.

xTB drives a scanned value from the first number to the second as written, past ±180° too:
`60.0, 300.0` turns a dihedral through 180°, `60.0, -60.0` through 0°. `pathtools.py diff`
gives each turning bond both ways round (`scan` and `other_way`).

**After every stage**, run `pathtools.py trace stage-1/xtbscan.log --input stage-1/scan.inp`.
xTB ends normally even when a scan moved nothing (a wrong `$scan` number, for example) or
when its first structure relaxed away from the start value before the scan began; the trace
reads the stage's input and warns about both (exit status 1), and says whether each scanned
coordinate reached its target and each held one stayed. Fix a slipped first point (start from
a structure at that value, or a stiffer force constant) before going on.
`pathtools.py frames stage-1/xtbscan.log --last -o stage-2/start.xyz` takes out the structure
the next stage starts from.

**A later stage keeps what earlier stages did**: its `$constrain` holds every coordinate an
earlier stage drove, at the value it actually reached (from the trace, not the value asked
for), until it is meant to move; otherwise the first optimisation of the stage relaxes back.
A second stage after the one above:

    $constrain
      force constant=1.0
      dihedral: 4, 3, 7, 12, 60.0
      distance: 3, 7, 1.56
    $scan
      1: 60.0, 175.0, 24
    $end

## Strategies, in this order

A **strategy** is one design: which coordinates are driven, together or in which stages and
in which order. Running the same design scanned the other way, with another force constant or
another number of points is a variant of it, not a new strategy.
{user_first}{ts_first}{species_first}
- One concerted scan of the bonds that form and break (and the held coordinates).
- Stages: the large dihedral changes first, then the bond changes, or the other way round.
  When several bonds turn, drive them together: `pathtools.py diff` lists the turning bonds
  (`rotations`, one dihedral each; rings and methyl groups are left out, but a ligand bound
  side-on to a metal, η², turning about it is one of them) and writes a concerted
  `$scan` block for all of them (`scan_block`); one at a time, each relaxes the others back.
- The same scanned from the end back to the start (`join --reverse` turns it round).
- xTB's own path finder, which needs no coordinates. These settings worked on a 91-atom
  organometallic step (about 30 s; the defaults gave a higher, worse path):

      $path
         nrun=1
         npoint=25
         anopt=10
         kpush=0.003
         kpull=-0.015
         ppull=0.05
         alp=1.2
      $end

  Run `xtb inputs/start.xyz --path inputs/end.xyz --input path.inp <the flags above> >
  path.out 2>&1`. Its `xtbpath.xyz` gives energies in kcal/mol from the first structure, not
  in Eh: convert it with `pathtools.py pathfinder xtbpath.xyz path.out -o stage-1.xyz` before
  joining (`join` refuses it as it is). Its points are only partly relaxed, so its barrier is
  an estimate; a relaxed scan along the same change is better when there is time.

**Budget**: about 60 minutes of xTB time and at most 6 strategies. Stop early only when a path
passes; otherwise use the budget (try the reverse direction and the concerted rotations before
giving up), then return the best path you have.

**Where the path ends.** The end is judged on the atoms that take part in the reaction, listed
in `path.json` as `active`: the atoms of the bonds that form or break and of the held
coordinates, and every atom up to two bonds from them ({active_count} of {atom_count} here).
Other atoms (side groups far from the reaction) may end in a different conformer from the end
node's; do not force that change into the path. A path downhill from a TS that settles in
another conformer is the answer the user wants, as an IRC would give it. Say in
`path.conformer_note` which atoms differ from the end node and by how much (`end_rmsd` over all
atoms against `active_end_rmsd`).

## Which path is best

Check each candidate with

    pathtools.py check path.xyz inputs/start.xyz inputs/end.xyz --mapping inputs/mapping.json \\
        --settings inputs/path.json

A path **passes** when it reaches the end over the reacting atoms (`active_end_rmsd` at most
0.5 Å after fitting on them; `end_rmsd` over all atoms is information only), forms or
breaks no bond other than those in `mapping.json`, has no jump between neighbouring structures
over 0.5 Å (`largest_jump`), and starts or ends at the held coordinates' TS values. Among those,
keep the lowest highest point. A `spikes` entry is one structure far above both neighbours with
a jump into it, as a conformer flipping in one step gives: try to avoid it (more points or a
restraint on that dihedral), and give `top_without_spikes` as well.

If no path passes, return the one that came closest, with `"gate": "missed"` and the reasons:
the app imports it all the same, saying in its notes that it did not pass.
{guess_check}
## What to return

- `outputs/path.xyz`: the best path, every structure of every stage in order, made with
  `pathtools.py join outputs/path.xyz stage-1/xtbscan.log stage-2/xtbscan.log --call "<the
  stage's xtb command line>" ...` (one `--call` per stage, `--reverse <n>` for a stage scanned
  backwards), so each comment line reads `energy: <Eh> stage: <n> call: <xtb command line>`.
- `outputs/stage-<n>/`: each stage of the best path, its `scan.out`, `xtbscan.log` and
  `scan.inp`.
- `outputs/alternatives/<k>/path.xyz`: the other candidates that got furthest, with a line on
  each in the summary.
- `result.json` as `.claude/CLAUDE.md` says, with `outputs` listing `outputs/path.xyz` first,
  and also:

  ```json
  "path": {{
    "gate": "passed",
    "missed": [],
    "reached_end": true,
    "active_end_rmsd": 0.21,
    "end_rmsd": 0.94,
    "top": 14,
    "barrier_kcal": 18.2,
    "spikes": [],
    "design": "What the best path drives, stage by stage, and why.",
    "stages": [{{"coordinates": ["distance 3-7 2.10 -> 1.54"], "points": 20,
                 "force_constant": 1.0, "direction": "forward"}}],
    "tried": ["What else was tried and how it failed."],
    "conformer_note": null,
    "ts_check": null,
    "user_strategy": null
  }}
  ```

  `gate` is `"passed"` or `"missed"`; `missed` lists the reasons in a few words each (for
  example "end RMSD over the reacting atoms 0.81 Å"). `summary` says in two or three sentences
  what the best path does and how close it came.{user_result}{species_result}
- `outputs/feedback.md` (optional): anything in these instructions or the helper that got in
  your way, and what would have helped, for the app's developer. The app keeps it with the job.
"""

MODE_FILE = "{end}_mode.json"  # a TS end's imaginary mode, in inputs/ (D116)

DRIVE = """
## Coordinates the user asked to drive

The user designed this path: {how}

{rows}

These are in `path.json` as `drive` (`from` and `to` for each, in the start's numbering).
A dihedral's `to` may lie past ±180°: xTB drives it as written, the way round the user chose.
"""

DRIVE_HOW = {
    "together": "drive these coordinates together, in one concerted scan, each from its first "
    "value to its second.",
    "staged": "drive these coordinates one stage per line, in this order, each from its first "
    "value to its second.",
}

USER_FIRST = """
- **The user's design first, run as given.** {stages} Add the held coordinates (driven from the
  TS's values to the other end's, as always), choose the number of points and the force
  constant yourself, and add restraints that keep the scan from slipping if it needs them, but
  never drop one of the user's lines or change its values.{reverse} `pathtools.py check ...
  --settings inputs/path.json` says under `drive` whether the path followed each line. If this
  path passes, it is the answer: return it. If it misses, say why in `tried`, keep it as a
  candidate (it may still come closest) and spend the rest of the budget on the strategies
  below.
"""

USER_STAGES = {
    "together": "One concerted `$scan` (`mode=concerted`) of every line above, the same number "
    "of points for each.",
    "staged": "One stage per line, in the order given; a later stage keeps what earlier stages "
    "drove, at the values they reached.",
}

USER_RESULT = """
  `user_strategy` reports the user's design either way: `{"gate": "passed" or "missed",
  "why": "a few words", "chosen": true when outputs/path.xyz is that design}`."""

SPECIES = """
## A species {verb}: “{species}”

“{species}” {verb} along this path: it is {bound_text}.
The app matched it across the ends and built the separated end, `inputs/{apart}.xyz`, itself:
the bound structure with
“{small}”'s own geometry fitted on the complex and “{species}”'s own geometry fitted on where
it sits bound, then pulled straight out along the line from atom {anchor} through its centre
until its closest contact with the complex was {closest:.1f} Å. So it faces the complex the
way it binds, turned the way it binds.

- `path.json` `species`: its atoms ({atoms}), the bonds it {makes} with their bound and
  separated lengths, which end is the separated one (`separated`: "{apart}"), the charge and
  `--uhf` of the complex alone (`complex`) and of the species alone (`alone`), and a two-stage
  approach (`approach`, below).
- `inputs/complex.xyz` and `inputs/species.xyz`: “{small}” and “{species}” alone, as their
  nodes have them, each in its own atom order.{leaves_mapping}

**The separated end** is judged by itself: `pathtools.py check ... --settings inputs/path.json`
reports it under `separated`. It passes when the complex fits `inputs/{apart}.xyz`'s within
0.5 Å after fitting on the complex alone (over its reacting atoms, as `active` lists them;
`complex_rmsd`) and the species' closest contact with the complex is at least 3.5 Å
(`closest_contact`), wherever it lies and however it is turned past that.{replaces} The bound
end, `inputs/{bound_end}.xyz`, is judged as any end, and no bond other than the ones the
species {makes} may change along the path.

**The leftover interaction.** At the separated end the species is still a few Å from the
complex, so the path's energies there are not those of the two apart. Optimise each alone, in
its own folder: `xtb inputs/complex.xyz --opt --gfn 2 --chrg {complex_charge} --uhf
{complex_uhf}{solvent_flag}` and `xtb inputs/species.xyz --opt --gfn 2 --chrg {alone_charge}
--uhf {alone_uhf}{solvent_flag}`, then add `--apart complex/xtbopt.xyz species/xtbopt.xyz` to
`check`: `separated.leftover_kcal` is the separated end's energy minus the two apart.
"""

SPECIES_FIRST = """
- **Pull it out from the bound end{backwards}.** Drive the bonds it {makes} together
  (`mode=concerted`), from their bound lengths to their separated ones, starting from
  `inputs/{bound_end}.xyz` with the complex otherwise free.{join_note} Starting from the bound
  structure is far more robust than steering a loose molecule in from a few Å away, where it
  wanders: it leaves the way it sits.
- **Bring it in.** One concerted scan of the same bonds from their separated lengths to their
  bound ones, starting from `inputs/{apart}.xyz`.{bring_note}
- **In two stages.** {two_stages}
- The straight line out is the app's guess. If it clashes in xTB, or the bound pose is only
  reached by a turn on the way in, change the line (another angle, a turn first) and say what
  you did in `design`.
"""

SPECIES_RESULT = """
  `species` reports the separated end from `check`: `{"complex_rmsd": 0.12, "closest_contact":
  4.1, "leftover_kcal": -1.4}`."""

TS_FIRST = """
- **{lead}** Push the TS a little along its imaginary mode toward the
  other end, `pathtools.py displace inputs/{ts}.xyz {mode} --toward inputs/{other}.xyz
  -o downhill/start.xyz` (try `--step 0.1` to `0.3`), then run one relaxed scan of the held
  coordinates from the TS's values to the {other}'s values, everything else free, starting
  from the pushed structure, so its first point is the TS held at its values.{reverse} If the
  scan stops short of the {other}, a last optimisation without restraints often finishes it.
"""

GUESS_CHECK = """
## A TS end that is only a guess

{names} has no frequency job, so the user ticked the coordinates that make it a TS. Run an xTB
frequency job on it (`xtb inputs/<ts>.xyz --hess <the flags above>`, which writes `g98.out`),
then

    pathtools.py mode inputs/<ts>.xyz g98.out --atoms <a> <b> [--atoms ...] --json

with every held coordinate. It reports the imaginary mode, the five distances that change most
along it and each held coordinate's change; the mode runs along the held coordinates when each
held distance changes by at least 35 % of the largest change (`runs_along`), the rule the app
uses to suggest coordinates from a mode. Put that report in `path.ts_check`. This is a warning
only: never change the held coordinates. The same `g98.out` serves `pathtools.py displace`.
"""


def _apart(joining: dict[str, Any] | None, which: str) -> str:
    """“ with <species> apart” for the separated end (D120)."""
    if joining is None or joining["separated"] != which:
        return ""
    return f" with “{joining['label']}” apart"


def _species_settings(plan_: Plan) -> dict[str, Any]:
    """path.json's `species` (D120): the species in the start's numbering, the bonds it makes
    with their bound and separated lengths, which end is separated, the complex's and the
    species' own charge and --uhf, and a two-stage approach."""
    joining = plan_.species
    joins = joining.direction == species.JOINS
    small = plan_.start if joins else plan_.end
    bound_rows = plan_.renumbered if joins else plan_.start_rows
    alone = joining.node.charge
    if alone is None:
        alone = plan_.charge - (small.node.charge or 0)
    return {
        "label": joining.name,
        "node_id": joining.node.id,
        "direction": joining.direction,
        "separated": "start" if joins else "end",
        "atoms": joining.atoms,
        "bonds": [
            {
                "atoms": pair,
                "bound": round(value(bound_rows, pair), 3),
                "separated": round(value(joining.separated, pair), 3),
            }
            for pair in joining.bonds
        ],
        "anchor": joining.anchor,
        "clearance": joining.clearance,
        "closest": round(joining.closest, 3),
        "complex": {
            "label": small.name,
            "charge": plan_.charge - alone,
            "uhf": (small.node.multiplicity or plan_.multiplicity) - 1,
        },
        "alone": {"charge": alone, "uhf": (joining.node.multiplicity or 1) - 1},
        "approach": scan_species.approach(
            bound_rows,
            joining.separated,
            [a - 1 for a in joining.atoms],
            [a - 1 for a in joining.anchor],
        ),
    }


def _wrapped(text: str, width: int = 95) -> str:
    """Paragraphs and list items of a brief part filled to `width` again once the names are
    in."""
    out = []
    for block in text.split("\n\n"):
        items = re.split(r"\n(?=- )", block.strip("\n"))
        lines = []
        for item in items:
            words = " ".join(item.split())
            lead = "  " if words.startswith("- ") else ""
            lines.append(
                textwrap.fill(
                    words,
                    width,
                    subsequent_indent=lead,
                    break_long_words=False,
                    break_on_hyphens=False,
                )
            )
        out.append("\n".join(lines))
    return "\n" + "\n\n".join(out) + "\n"


def _species_text(
    plan_: Plan, joining: dict[str, Any] | None, solvent: str | None
) -> dict[str, str]:
    """The brief's species parts (D120), empty without a species."""
    keys = ("start_apart", "end_apart", "species_file", "species", "species_first")
    if joining is None:
        return dict.fromkeys((*keys, "species_result"), "")
    joins = joining["direction"] == species.JOINS
    apart, bound_end = ("start", "end") if joins else ("end", "start")
    makes = "forms" if joins else "breaks"
    name = joining["label"]
    pairs = ", ".join("–".join(map(str, b["atoms"])) for b in joining["bonds"]) or "none"
    approach = joining["approach"]
    if approach is None:
        two_stages = (
            "First its centre in to about 3.5 Å from the complex, its turn held by one angle "
            f"and one dihedral to the complex, then the bonds it {makes}."
        )
    else:
        d, a, t = approach["distance"], approach["angle"], approach["dihedral"]
        two_stages = (
            f"First its centre in: distance {'–'.join(map(str, d['atoms']))} from "
            f"{d['from']:.2f} to {d['to']:.2f} Å, its turn held by angle "
            f"{'–'.join(map(str, a['atoms']))} at {a['value']:.1f}° and dihedral "
            f"{'–'.join(map(str, t['atoms']))} at {t['value']:.1f}° (`species.approach`); then "
            f"the bonds it {makes} to their bound lengths, the angle and dihedral let go."
        )
    if not joins:
        two_stages += " Run both from the separated end and join them with `--reverse`."
    complex_ = joining["complex"]
    alone = joining["alone"]
    return {
        "start_apart": f", with “{name}” apart (the separated end, below)" if joins else "",
        "end_apart": "" if joins else f", with “{name}” apart (the separated end, below)",
        "species_file": f"\n  With “{name}” joining or leaving, `species` too (below).",
        "species": _wrapped(
            SPECIES.format(
                species=name,
                verb=joining["direction"],
                bound_text=(
                    f"apart at the start and bound at the end, by {pairs}"
                    if joins
                    else f"bound at the start, by {pairs}, and apart at the end"
                ),
                apart=apart,
                bound_end=bound_end,
                small=complex_["label"],
                anchor=", ".join(map(str, joining["anchor"])),
                closest=joining["closest"],
                atoms=", ".join(map(str, joining["atoms"])),
                makes=makes,
                leaves_mapping=(
                    ""
                    if joins
                    else "\n- `mapping.json`'s `mapping` numbers the end's atoms followed by the "
                    f"species node's: past {len(plan_.start_rows) - len(joining['atoms'])} it is "
                    f"“{name}”'s own order."
                ),
                replaces=(
                    " For this end it stands in for `active_end_rmsd`."
                    if not joins
                    else " The path must start there."
                ),
                complex_charge=complex_["charge"],
                complex_uhf=complex_["uhf"],
                alone_charge=alone["charge"],
                alone_uhf=alone["uhf"],
                solvent_flag=f" --alpb {solvent}" if solvent else "",
            )
        ),
        "species_first": _wrapped(
            SPECIES_FIRST.format(
                backwards=" and turn it round" if joins else "",
                makes=makes,
                bound_end=bound_end,
                apart=apart,
                join_note=(
                    " It runs from the end back, so join it with `--reverse`." if joins else ""
                ),
                bring_note=(
                    "" if joins else " It runs from the end back: join it with `--reverse`."
                ),
                two_stages=two_stages,
            )
        ).rstrip("\n"),
        "species_result": SPECIES_RESULT,
    }


def create_job(
    folder: Path,
    investigation: str,
    plan_: Plan,
    held: list[dict[str, Any]],
    solvent: str | None,
    drive: list[dict[str, Any]] | None = None,
    drive_order: str = "together",
) -> dict[str, Any]:
    """D114: the scan path job folder, ready to start (D93). The database is not changed.
    `drive` lists the user's own coordinates (D119), run first as given."""
    if solvent:
        solvent = SOLVENT_NAMES.get(solvent.strip().lower(), solvent.strip().lower())
        if solvent not in ALPB_SOLVENTS:
            raise RecordError(f"xTB's ALPB has no solvent “{solvent}”")
    held = check_held(plan_, held)
    driven = check_drive(plan_, drive or [], drive_order, held)
    start, end, match = plan_.start, plan_.end, plan_.match
    summary = atom_matching.summary(match)
    mapping = {
        "start": {"node_id": start.node.id, "label": start.name},
        "end": {"node_id": end.node.id, "label": end.name},
        **{k: summary[k] for k in ("mapping", "formed", "broken", "inverted", "fixed", "rmsd")},
    }
    held_atoms = [row["atoms"] for row in held]
    active = pathtools.active_atoms(
        plan_.start_rows, plan_.renumbered, summary["formed"] + summary["broken"], held_atoms
    )
    settings = {
        "charge": plan_.charge,
        "multiplicity": plan_.multiplicity,
        "uhf": plan_.multiplicity - 1,
        "solvent": solvent,
        "held": held,
        "active": active,
    }
    if driven is not None:
        settings["drive"] = driven
    joining = _species_settings(plan_) if plan_.species is not None else None
    if joining is not None:
        settings["species"] = joining
    ts_of = {t["end"]: t for t in plan_.ts_ends}

    def ts_note(which: str) -> str:
        t = ts_of.get(which)
        if t is None:
            return ""
        if t["guess"]:
            return ", a transition state guess (no frequency job)"
        return f", a transition state (imaginary mode {abs(t['imaginary']):.0f}i cm⁻¹)"

    guesses = [f"“{t['label']}”" for t in plan_.ts_ends if t["guess"]]
    mode_lines = []
    for which in plan_.modes:
        mode_lines.append(
            f"\n- `inputs/{MODE_FILE.format(end=which)}`: the {which}'s imaginary mode from its "
            "frequency job, `wavenumber` in cm⁻¹ and one displacement `vectors` row per atom, in "
            "the start's numbering and as `inputs/" + which + ".xyz` is turned."
        )
    ts_first = []
    for t in plan_.ts_ends:
        which = t["end"]
        other = "end" if which == "start" else "start"
        ts_first.append(
            TS_FIRST.format(
                lead="Then downhill from the TS." if driven else "Downhill from the TS first.",
                ts=which,
                other=other,
                mode=f"inputs/{MODE_FILE.format(end=which)}" if which in plan_.modes else "g98.out",
                reverse=(
                    " The TS is the end here, so this stage runs from the end back to the start:"
                    " join it with `--reverse`."
                    if which == "end"
                    else ""
                ),
            ).rstrip("\n")
        )
    text = INSTRUCTIONS.format(
        start=start.name,
        end=end.name,
        start_ts=ts_note("start"),
        end_ts=ts_note("end"),
        charge=plan_.charge,
        multiplicity=plan_.multiplicity,
        uhf=plan_.multiplicity - 1,
        solvent_flag=f" --alpb {solvent}" if solvent else "",
        solvent_text=f" (ALPB {solvent})" if solvent else " (gas phase)",
        active_count=len(active) or len(plan_.start_rows),
        atom_count=len(plan_.start_rows),
        held=_held_text(held, plan_),
        guess_check=GUESS_CHECK.format(names=" and ".join(guesses)) if guesses else "",
        mode_files="".join(mode_lines),
        no_bond_change=(
            "\n\nNo bond forms or breaks between the ends by the app's rule, so this path is "
            "mostly a change of conformation; see the note on conformers below."
            if plan_.warnings
            else ""
        ),
        ts_first="".join(ts_first),
        drive_file=("\n  With the user's own coordinates, `drive` too (below)." if driven else ""),
        drive=(
            DRIVE.format(how=DRIVE_HOW[driven["order"]], rows=_drive_text(driven)) if driven else ""
        ),
        user_first=(
            USER_FIRST.format(
                stages=USER_STAGES[driven["order"]],
                reverse=(
                    " The TS is the end here: you may run it from the end back (each line from"
                    " its second value to its first) and join it with `--reverse`."
                    if "end" in ts_of and "start" not in ts_of
                    else ""
                ),
            ).rstrip("\n")
            if driven
            else ""
        ),
        user_result=USER_RESULT if driven else "",
        **_species_text(plan_, joining, solvent),
    )
    inputs = [
        cloud_jobs.InputFile(
            name="start.xyz",
            text=_xyz(plan_.start_rows, start.name + _apart(joining, "start")),
            description=f"the start, node “{start.name}”{_apart(joining, 'start')}, "
            f"{len(plan_.start_rows)} atoms",
            node_id=start.node.id,
        ),
        cloud_jobs.InputFile(
            name="end.xyz",
            text=_xyz(
                plan_.renumbered,
                f"{end.name}{_apart(joining, 'end')} in the atom order of {start.name}",
            ),
            description=f"the end, node “{end.name}”{_apart(joining, 'end')}, renumbered in "
            "the start's order",
            node_id=end.node.id,
        ),
        cloud_jobs.InputFile(
            name="mapping.json",
            text=json.dumps(mapping, indent=2),
            description="the atom match (D113)",
        ),
        cloud_jobs.InputFile(
            name="path.json",
            text=json.dumps(settings, indent=2),
            description="charge, multiplicity, solvent, the coordinates held at a TS end"
            + (" and the user's own coordinates" if driven else "")
            + (" and the species that joins or leaves" if joining else ""),
        ),
    ]
    if joining is not None:
        small = start if joining["separated"] == "start" else end
        inputs += [
            cloud_jobs.InputFile(
                name="complex.xyz",
                text=_xyz(small.node.geometry, small.name),
                description=f"the complex alone, node “{small.name}”",
                node_id=small.node.id,
            ),
            cloud_jobs.InputFile(
                name="species.xyz",
                text=_xyz(plan_.species.node.geometry, joining["label"]),
                description=f"the species alone, node “{joining['label']}”",
                node_id=plan_.species.node.id,
            ),
        ]
    for which, (wavenumber, vectors) in plan_.modes.items():
        node = start.node if which == "start" else end.node
        inputs.append(
            cloud_jobs.InputFile(
                name=MODE_FILE.format(end=which),
                text=json.dumps(
                    {
                        "wavenumber": wavenumber,
                        "vectors": [[round(float(x), 6) for x in v] for v in vectors],
                    }
                ),
                description=f"the imaginary mode of “{node.label or 'Untitled node'}”",
                node_id=node.id,
            )
        )
    extra = {
        "kind": "scan_path",
        "scan_path": {
            "start_id": start.node.id,
            "end_id": end.node.id,
            "start_label": start.name,
            "end_label": end.name,
            "edge_id": plan_.edge.id,
            **settings,
        },
    }
    name = f"Path {start.name} to {end.name}"
    if len(name) > 80:
        name = name[:79] + "…"
    try:
        return cloud_jobs.create_job(folder, name, text, inputs, investigation, extra)
    except cloud_jobs.CloudJobError as exc:
        raise RecordError(str(exc)) from exc


# ---------- the result (PR 4) ----------

PATH_FILE = "outputs/path.xyz"
FEEDBACK_FILE = "outputs/feedback.md"  # the session's notes on the brief and helper (D118)
NODE_GAP = 0.5  # the new node sits halfway between the ends


def _place(session: Session, node: Node) -> tuple[float, float]:
    """Where a node is drawn: a group member inside its group's box is drawn at the group."""
    if node.group_id:
        group = session.get(GroupNode, node.group_id)
        if group is not None:
            return group.pos_x, group.pos_y
    return node.pos_x, node.pos_y


def _ts_check_text(check: Any) -> str:
    """The session's TS check (`pathtools.py mode --json`, D116), or its own words."""
    if not isinstance(check, dict):
        return str(check)
    parts = []
    wavenumber = check.get("wavenumber")
    if isinstance(wavenumber, int | float):
        parts.append(
            f"imaginary mode {abs(wavenumber):.0f}i cm⁻¹"
            if wavenumber < 0
            else f"no imaginary mode (lowest {wavenumber:.0f} cm⁻¹)"
        )
    runs = check.get("runs_along")
    if runs is not None:
        parts.append("runs along the held coordinates" if runs else "does not run along them")
    held = check.get("coordinates")
    if isinstance(held, list):
        shares = [
            f"{c.get('atoms')} {c['share']:.0%}"
            for c in held
            if isinstance(c, dict) and isinstance(c.get("share"), int | float)
        ]
        if shares:
            parts.append("held distances " + ", ".join(shares) + " of the largest change")
    return "; ".join(parts) + "." if parts else json.dumps(check)


def _species_note(joining: dict[str, Any], report: Any) -> str:
    """D120: the species that joins or leaves, and what the energies at its far end mean."""
    name = joining.get("label") or "the species"
    far = joining.get("separated") or "far"
    text = (
        f"“{name}” {joining.get('direction', 'joins')} along this path (D120). The energies are "
        f"GFN2-xTB with “{name}” a few Å from the complex at the {far} end, not apart"
    )
    report = report if isinstance(report, dict) else {}
    closest, leftover = report.get("closest_contact"), report.get("leftover_kcal")
    if isinstance(closest, int | float):
        text += f" (closest contact {float(closest):.1f} Å)"
    text += "."
    if isinstance(leftover, int | float):
        side = "below" if leftover < 0 else "above"
        text += (
            f" There the path lies {abs(float(leftover)):.1f} kcal/mol {side} the complex and "
            f"“{name}” each optimised alone."
        )
    return text


def _notes(
    job: dict[str, Any], result: dict[str, Any], start: str, end: str, feedback: bool = False
) -> str:
    path = result.get("path") if isinstance(result.get("path"), dict) else {}
    lines = [f"Scan path from “{start}” to “{end}”, cloud job {job['id']} (D114)."]
    summary = str(result.get("summary") or "").strip()
    if summary:
        lines.append(summary)
    if path.get("gate") == "missed":
        missed = path.get("missed")
        reasons = (
            "; ".join(str(x) for x in missed if str(x).strip())
            if isinstance(missed, list)
            else str(missed or "").strip()
        )
        lines.append(
            "Did not pass the quality check (D116)" + (f": {reasons}." if reasons else ".")
        )
    if path.get("reached_end") is not None:
        reached = "reached" if path["reached_end"] else "not reached"
        active, whole = path.get("active_end_rmsd"), path.get("end_rmsd")
        if isinstance(active, int | float):
            # D118: judged on the reacting atoms; the whole structure may differ in conformer.
            whole_text = (
                f", {float(whole):.2f} Å over all atoms" if isinstance(whole, int | float) else ""
            )
            lines.append(
                f"End {reached} over the reacting atoms (RMSD {float(active):.2f} Å{whole_text})."
            )
        else:
            rmsd_text = f" (RMSD {float(whole):.2f} Å)" if isinstance(whole, int | float) else ""
            lines.append(f"End {reached}{rmsd_text}.")
    joining = (job.get("scan_path") or {}).get("species")
    if isinstance(joining, dict):
        lines.append(_species_note(joining, path.get("species")))
    if isinstance(path.get("barrier_kcal"), int | float):
        lines.append(
            f"Highest point {float(path['barrier_kcal']):.1f} kcal/mol above the start (GFN2-xTB)."
        )
    user = path.get("user_strategy")
    if isinstance(user, dict):
        # D119: whether the path is the user's own design, and if not, why theirs missed.
        why = str(user.get("why") or "").strip()
        if user.get("chosen"):
            lines.append("Driven by your coordinates (D119)." + (f" {why}" if why else ""))
        else:
            verdict = "passed too" if user.get("gate") == "passed" else "missed"
            lines.append(
                f"The agent's own design (yours {verdict}" + (f": {why})." if why else ").")
            )
    design = str(path.get("design") or "").strip()
    if design:
        lines.append(f"Design: {design}")
    conformer = str(path.get("conformer_note") or "").strip()
    if conformer:
        lines.append(f"Conformers: {conformer}")
    if path.get("ts_check"):
        lines.append(f"TS check: {_ts_check_text(path['ts_check'])}")
    if feedback:
        lines.append(f"The session left feedback in jobs/{job['id']}/{FEEDBACK_FILE}.")
    return "\n\n".join(lines)


def import_result(
    session: Session, folder: Path, job_id: str, again: bool = False
) -> dict[str, Any]:
    """D115: the path a scan path job returned, imported as a new node halfway between its
    ends, with no edges, in the start's step and branch, role unspecified, status planned, and
    notes naming the ends and the session's summary. Its geometry is the path's top, else its
    middle point (D112). The import can be undone like any other (D102); `again` imports a path
    whose node is gone (undone or deleted) once more, which the app never does by itself."""
    with _importing:
        return _import_result(session, folder, job_id, again)


_importing = threading.Lock()  # two tabs checking at once import a path once


def _import_result(session: Session, folder: Path, job_id: str, again: bool) -> dict[str, Any]:
    job = cloud_jobs.read_job(folder, job_id)
    if job.get("kind") != "scan_path":
        raise RecordError("this job is not a scan path")
    imported = job.get("imported") or {}
    if imported and (not again or session.get(Node, imported.get("node_id")) is not None):
        raise RecordError("this path is imported already")
    if not job.get("fetched"):
        try:
            cloud_jobs.fetch_results(folder, job_id)
        except cloud_jobs.CloudJobError as exc:
            raise RecordError(str(exc)) from exc
    base = cloud_jobs.jobs_dir(folder) / job_id
    path_file = base / PATH_FILE
    result = {}
    if (base / cloud_jobs.RESULT).is_file():
        try:
            result = json.loads((base / cloud_jobs.RESULT).read_text(encoding="utf-8"))
        except ValueError:
            result = {}
    if not path_file.is_file():
        why = str(result.get("summary") or "").strip()
        message = f"The session returned no {PATH_FILE}" + (f": {why}" if why else "")
        cloud_jobs.update_job(folder, job_id, import_error=message)
        raise RecordError(message)
    ends = job["scan_path"]
    start = session.get(Node, ends["start_id"])
    end = session.get(Node, ends["end_id"])
    start_name = start.label if start is not None else ends["start_label"]
    end_name = end.label if end is not None else ends["end_label"]
    options = imports.ImportOptions(
        duplicate_action="new",
        label=f"Path {start_name} to {end_name}"[:200],
        role=Role.UNSPECIFIED,
        status=Status.PLANNED,
        origin_path=str(path_file),
        original_name="path.xyz",
        notes=_notes(
            job,
            result if isinstance(result, dict) else {},
            start_name,
            end_name,
            (base / FEEDBACK_FILE).is_file(),
        ),
    )
    if start is not None:
        group = session.get(GroupNode, start.group_id) if start.group_id else None
        options.step_id = start.step_id or (group.step_id if group else None)
        options.branch_id = start.branch_id or (group.outgoing_branch_id if group else None)
        a = _place(session, start)
        b = _place(session, end) if end is not None else (a[0] + 400.0, a[1])
        options.pos_x = a[0] + NODE_GAP * (b[0] - a[0])
        options.pos_y = a[1] + NODE_GAP * (b[1] - a[1])
    staging = imports.Staging()
    try:
        staged = staging.add(path_file.read_bytes(), "path.xyz", str(path_file))
        try:
            committed = imports.commit(session, folder, staged, options)
        except imports.ImportBlocked as exc:
            message = "; ".join(exc.blockers)
            cloud_jobs.update_job(folder, job_id, import_error=message)
            raise RecordError(message) from exc
        except imports.ImportFailed as exc:
            cloud_jobs.update_job(folder, job_id, import_error=str(exc))
            raise RecordError(str(exc)) from exc
    finally:
        staging.clear()
    node = session.get(Node, committed.node_id)
    cloud_jobs.update_job(
        folder,
        job_id,
        imported={"node_id": committed.node_id, "at": cloud_jobs.now()},
        import_error=None,
    )
    return {"node_id": committed.node_id, "label": node.label if node else "", "job": job_id}
