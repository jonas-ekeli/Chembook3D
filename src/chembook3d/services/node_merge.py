"""Merge two nodes holding the same molecule (D262, FR-NODE-09, A181).

One node (A, `source`) dissolves into the other (B, `into`): its calculations, edges and every
record pointing at it move onto B, which keeps its id, label, role, status, place and canvas.

- *Refused* (D262b) when one is a free species and the other a canvas node, the atoms, charge
  or multiplicity differ, one is a TS and the other a minimum, Match atoms finds an inverted
  centre, or a local or cloud job naming A is not imported yet. *Warned* when a real bond forms
  or breaks (within 1.2 × the covalent radii in one geometry and beyond 1.5 × in the other,
  A181), when Match atoms has other doubts, and when the two nodes' geometries at one geometry
  level differ by more than the duplicate tolerance (D62).
- *Atom map* (D262c): A's per-atom data in the database are written in B's numbering; each
  moved calculation stores the map to its file's numbering (`Calculation.atom_map`), which is
  never rewritten, and frames read from the file go through it (`atom_maps.mapped_step`).
- *Energies* (D262d): at a composite level both nodes have, the node with the lower energy
  counts (G_qh when both have it, else E); the other node's calculations there are set aside
  (`Calculation.set_aside`), shown and not counted. B shows the geometry at the highest-ranked
  level of either node (D246a), at one level the lower energy's. Every single point or
  frequency job is first linked to the calculation it is at (D255), so no value moves.
- One write in one transaction, with one history entry "merge_nodes" on B holding every row
  changed, before and after, for Undo merge (D262h).

`plan` changes nothing; `apply` writes what it planned.
"""

import base64
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy import Table, delete, or_, select, update
from sqlalchemy.orm import Session

from chembook3d import cloud_jobs, local_jobs
from chembook3d.models import (
    AlignmentSetAtoms,
    Base,
    Branch,
    Calculation,
    CalculationResult,
    ConformerEquilibrium,
    EnergyProfile,
    GroupNode,
    HistoryEntry,
    InvestigationInfo,
    Node,
    NodeKind,
    NodeNote,
    NormalModes,
    Role,
    Selectivity,
    SelectivityMember,
    SelectivityOutcome,
    StericProfileAtoms,
    SubInvestigation,
    Transition,
    TransitionSpecies,
    Turnover,
    pack_modes,
    unpack_modes,
    utcnow,
)
from chembook3d.services import (
    atom_maps,
    atom_matching,
    energies,
    geometry,
    history,
    levels,
    thermo_settings,
)
from chembook3d.services.records import RecordError, get

ACTION = "merge_nodes"
UNDO_KIND = "node_merge"  # HistoryOut.undo; "merge" is a playground's (D235j)
BRANCH_CHOICES = ("none", "keep", "take")


class MergeError(RecordError):
    pass


@dataclass
class Options:
    pairs: list[list[int]] = field(default_factory=list)  # 1-based [B atom, A atom], fixed
    branch: str = "none"  # D262f: on two different branches: none, keep B's, take A's
    anyway: bool = False  # merge despite the warnings


def name(node: Node) -> str:
    return node.label or "Untitled node"


def _quoted(node: Node) -> str:
    return f"“{name(node)}”"


@dataclass
class Level:
    """D262d: one composite level both nodes have values at, and which node counts there."""

    key: energies.LevelKey
    label: str
    energy_type: str
    into_value: float | None
    source_value: float | None
    counts: str  # "into" or "source"

    def as_dict(self) -> dict[str, Any]:
        return {
            "key": self.key.encode(),
            "label": self.label,
            "energy_type": self.energy_type,
            "into_value": self.into_value,
            "from_value": self.source_value,
            "counts": "into" if self.counts == "into" else "from",
        }


@dataclass
class Merge:
    into: Node
    source: Node
    options: Options
    refusals: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    match: atom_matching.Match | None = None
    mapping: list[int] | None = None  # for each of B's atoms, A's (0-based)
    real_formed: list[tuple[int, int]] = field(default_factory=list)
    real_broken: list[tuple[int, int]] = field(default_factory=list)
    geometries: list[dict[str, Any]] = field(default_factory=list)  # same-level comparisons
    levels: list[Level] = field(default_factory=list)
    card: dict[str, Any] | None = None  # the geometry B shows after the merge
    calculations: list[Calculation] = field(default_factory=list)  # A's, IRC parts too
    maps: dict[str, list[int] | None] = field(default_factory=dict)  # per A calculation
    set_aside: dict[str, list[str]] = field(default_factory=dict)  # calculation -> keys added
    links: dict[str, str] = field(default_factory=dict)  # D255 links pinned first
    changes: list[str] = field(default_factory=list)  # what the merge does, for the preview
    edges_moved: list[str] = field(default_factory=list)
    edges_dropped: list[str] = field(default_factory=list)
    ops: list[Callable[[Session], None]] = field(default_factory=list)
    new_geometry: list[Any] | None = None  # None: B keeps its own

    @property
    def blocked(self) -> bool:
        return bool(self.refusals) or (bool(self.warnings) and not self.options.anyway)

    def as_dict(self) -> dict[str, Any]:
        match = self.match
        return {
            "into": _node_dict(self.into),
            "from": _node_dict(self.source),
            "refusals": self.refusals,
            "warnings": self.warnings,
            "match": None
            if match is None
            else {
                **atom_matching.summary(match),
                "real_formed": [[a + 1, b + 1] for a, b in self.real_formed],
                "real_broken": [[a + 1, b + 1] for a, b in self.real_broken],
            },
            "geometries": self.geometries,
            "levels": [level.as_dict() for level in self.levels],
            "card": self.card,
            "calculations": [
                {
                    **_describe(c),
                    "set_aside": [
                        energies.key_label(self._session, energies.LevelKey.decode(k))
                        for k in self.set_aside.get(c.id, [])
                    ],
                }
                for c in self.calculations
            ],
            "set_aside_on_into": [
                {
                    **_describe(c),
                    "set_aside": [
                        energies.key_label(self._session, energies.LevelKey.decode(k))
                        for k in self.set_aside[c.id]
                    ],
                }
                for c in self.into.calculations
                if self.set_aside.get(c.id)
            ],
            "edges_moved": self.edges_moved,
            "edges_dropped": self.edges_dropped,
            "changes": self.changes,
            "branch": self.options.branch,
            "branch_choice": self._branch_choice,
            "can_merge": not self.refusals,
        }

    _session: Session = field(default=None, repr=False)  # type: ignore[assignment]
    _branch_choice: bool = False  # the two are on two different branches (D262f)


def _node_dict(node: Node) -> dict[str, Any]:
    return {
        "id": node.id,
        "label": node.label,
        "kind": node.kind,
        "role": node.role,
    }


def _describe(calculation: Calculation) -> dict[str, Any]:
    source = calculation.source_file
    return {
        "id": calculation.id,
        "type": calculation.type,
        "level": levels.composite_label(
            calculation.level, calculation.geometry_level if calculation.geometry_level_id else None
        ),
        "file": source.original_name if source else None,
        "step": calculation.step_index,
        "irc_part": calculation.irc_part,
    }


# ---------- checks ----------


def _refusals(session: Session, into: Node, source: Node, folder: Path | None) -> list[str]:
    found: list[str] = []
    a, b = _quoted(source), _quoted(into)
    if (into.kind == NodeKind.SPECIES) != (source.kind == NodeKind.SPECIES):
        found.append("One is a free species and the other a node on the canvas")
    for what, x, y in (
        ("charge", into.charge, source.charge),
        ("multiplicity", into.multiplicity, source.multiplicity),
    ):
        if x is not None and y is not None and x != y:
            found.append(f"{b} has {what} {x} and {a} {what} {y}")
    roles = {into.role, source.role} - {Role.UNSPECIFIED}
    if len(roles) > 1:
        found.append(f"One is a transition state and the other a minimum ({b}, {a})")
    for node in (into, source):
        if not node.geometry:
            found.append(f"{_quoted(node)} has no coordinates")
    if into.geometry and source.geometry:
        differs = atom_matching.atom_difference(into.geometry, source.geometry)
        if differs:
            found.append(differs[:1].upper() + differs[1:])
    if folder is not None:
        found.extend(_waiting_jobs(folder, source))
    return found


def _waiting_jobs(folder: Path, node: Node) -> list[str]:
    """D262b: a local or cloud job naming A that is not imported yet (its coordinates are in
    A's numbering, and it would import onto a node that is gone)."""
    found = []
    for job in local_jobs.list_jobs(folder):
        names = {job.get("node_id"), *((job.get("ts_guess") or {}).get(k) for k in ("start_id",))}
        names.add((job.get("ts_guess") or {}).get("end_id"))
        if node.id in names and (job.get("active") or job.get("importable")):
            found.append(
                f"The local job “{job.get('name') or job['id']}” on {_quoted(node)} is not "
                "imported yet: import it, or cancel it, first"
            )
    for job in cloud_jobs.list_jobs(folder):
        names = {item.get("node_id") for item in job.get("inputs") or []}
        names |= {(job.get("scan_path") or {}).get(k) for k in ("start_id", "end_id")}
        if node.id in names and not job.get("imported"):
            found.append(
                f"The cloud job “{job.get('name') or job.get('id')}” on {_quoted(node)} is not "
                "imported yet: import or delete it first"
            )
    return found


# A181's rule lives with the atom match, which the folder tools share (D263).
real_changes = atom_matching.real_changes


def _bond_text(into: Node, rows: list[Any], pair: tuple[int, int]) -> str:
    i, j = pair
    return f"{rows[i][0]}{i + 1}–{rows[j][0]}{j + 1}"


# ---------- the plan ----------


def plan(
    session: Session,
    source_id: str,
    into_id: str,
    options: Options | None = None,
    folder: Path | None = None,
) -> Merge:
    """What merging node `source_id` (A) into `into_id` (B) would do. Changes nothing."""
    options = options or Options()
    if options.branch not in BRANCH_CHOICES:
        raise MergeError(f"branch is one of {', '.join(BRANCH_CHOICES)}")
    if source_id == into_id:
        raise MergeError("Choose two different nodes to merge")
    into = get(session, Node, into_id, "Node")
    source = get(session, Node, source_id, "Node")
    merge = Merge(into, source, options, _session=session)
    merge.refusals = _refusals(session, into, source, folder)
    if merge.refusals:
        return merge
    # A pair fixed by hand that does not fit raises, answered as any RecordError is.
    result = atom_matching.match(
        into.geometry, source.geometry, atom_matching.pairs_from(options.pairs)
    )
    merge.match = result
    merge.mapping = result.mapping
    if result.inverted:
        centres = ", ".join(f"{into.geometry[i][0]}{i + 1}" for i in result.inverted)
        merge.refusals.append(
            f"Match atoms finds {centres} of {_quoted(into)} the other way round in "
            f"{_quoted(source)}: a mirror image is another molecule"
        )
        return merge
    merge.real_formed, merge.real_broken = real_changes(into.geometry, source.geometry, result)
    for pair in merge.real_formed:
        merge.warnings.append(
            f"{_bond_text(into, into.geometry, pair)} is bonded in {_quoted(source)} but not "
            f"in {_quoted(into)}"
        )
    for pair in merge.real_broken:
        merge.warnings.append(
            f"{_bond_text(into, into.geometry, pair)} is bonded in {_quoted(into)} but not "
            f"in {_quoted(source)}"
        )
    for doubt in result.doubts:
        if "mirror" not in doubt and "bonds form or break" not in doubt:
            merge.warnings.append(f"Match atoms: {doubt}")

    settings = thermo_settings.settings(session)
    tolerance = settings.geometry_tolerance
    _plan_calculations(session, merge, tolerance)
    if merge.refusals:
        return merge
    _plan_energies(merge, settings)
    _plan_geometry(merge, settings, tolerance)
    _plan_fields(session, merge)
    _plan_edges(session, merge)
    _plan_records(session, merge)
    return merge


def _plan_calculations(session: Session, merge: Merge, tolerance: float) -> None:
    into, source, mapping = merge.into, merge.source, merge.mapping
    assert mapping is not None
    merge.calculations = list(
        session.scalars(
            select(Calculation)
            .where(Calculation.node_id == source.id)
            .order_by(Calculation.created_at, Calculation.step_index, Calculation.id)
        )
    )
    order = geometry.elements(source.geometry)
    for calculation in merge.calculations:
        rows = calculation.geometry or calculation.start_geometry
        if not rows or geometry.elements(rows) == order:
            merge.maps[calculation.id] = mapping
            continue
        # D246c: a free species' geometry added beside it from elsewhere may number its
        # atoms otherwise; it is matched on its own.
        try:
            own = atom_matching.match(into.geometry, rows)
        except RecordError:
            merge.refusals.append(
                f"A calculation of {_quoted(source)} ({_describe(calculation)['file']}) holds "
                "other atoms than the node"
            )
            continue
        if own.inverted:
            merge.refusals.append(
                f"A calculation of {_quoted(source)} ({_describe(calculation)['file']}) is the "
                f"mirror image of {_quoted(into)}"
            )
            continue
        merge.maps[calculation.id] = own.mapping
    # D255: every single point and frequency job of both is linked to the calculation it is
    # at before anything moves, so neither node's values move with the other's geometries.
    for node in (into, source):
        for calculation in node.calculations:
            if calculation.type in levels.LINKED_TYPES and calculation.geometry_source_id is None:
                found = levels.rule_source(calculation, tolerance)
                if found is not None:
                    merge.links[calculation.id] = found.id
    merge.changes.append(
        f"{len(merge.calculations)} calculation(s) move from {_quoted(source)} to "
        f"{_quoted(into)}" + ("" if atom_maps.is_identity(mapping) else ", renumbered as it is")
    )


def _plan_energies(merge: Merge, settings: Any) -> None:
    """D262d: at each composite level both have values at, the lower energy's node counts."""
    session = merge._session
    made = {
        role: energies.NodeEnergies(
            node,
            settings.qh_temperature,
            settings.qh_cutoff,
            settings.standard_state,
            tolerance=settings.geometry_tolerance,
        )
        for role, node in (("into", merge.into), ("source", merge.source))
    }
    available = {role: found.available() for role, found in made.items()}
    shared = [k for k in available["into"] if k in available["source"]]
    shared.sort(key=lambda k: energies.key_label(session, k))
    for key in shared:
        types = available["into"][key] & available["source"][key]
        energy_type = "G_qh" if "G_qh" in types else "E"
        into_value = made["into"].value(key, energy_type).value
        source_value = made["source"].value(key, energy_type).value
        counts = (
            "source"
            if source_value is not None and (into_value is None or source_value < into_value)
            else "into"
        )
        merge.levels.append(
            Level(
                key,
                energies.key_label(session, key),
                energy_type,
                into_value,
                source_value,
                counts,
            )
        )
        loser = made["into" if counts == "source" else "source"]
        aside = list(loser.by_key.get(key, []))
        if key.geometry_level_id is not None:
            at_geometry = energies.LevelKey(key.geometry_level_id, key.geometry_level_id)
            aside += [
                c
                for c in loser.by_key.get(at_geometry, [])
                if c.type == "frequency" and c not in aside
            ]
        for calculation in aside:
            merge.set_aside.setdefault(calculation.id, []).append(key.encode())
    for level in merge.levels:
        winner = merge.source if level.counts == "source" else merge.into
        merge.changes.append(
            f"At {level.label}, {_quoted(winner)}'s calculations count ({level.energy_type} "
            "lower); the other's are set aside"
        )


def _rank(calculation: Calculation) -> int:
    return levels.method_rank(levels.fields_of(calculation.level))


def _plan_geometry(merge: Merge, settings: Any, tolerance: float) -> None:
    """D262d, D246a: B shows the geometry at the highest-ranked level of either node; at one
    level, the one with the lower energy. Same-level geometries further apart than the
    duplicate tolerance are warned about (Q84)."""
    into, source = merge.into, merge.source
    own = levels.node_geometries(into, tolerance)
    other = levels.node_geometries(source, tolerance)

    def rows_of(calculation: Calculation) -> list[Any]:
        if calculation.node_id == into.id:
            return calculation.geometry
        return atom_maps.reordered(calculation.geometry, merge.maps.get(calculation.id))

    for calculation in other:
        same = [c for c in own if c.level_id == calculation.level_id]
        if not same or calculation.level_id is None:
            continue
        rmsd = geometry.aligned_rmsd(same[-1].geometry, rows_of(calculation))
        label = levels.label(calculation.level)
        warned = rmsd is not None and rmsd > settings.duplicate_tolerance
        merge.geometries.append({"level": label, "rmsd": rmsd, "warned": warned})
        if warned:
            merge.warnings.append(
                f"At {label} the two geometries differ by {rmsd:.3f} Å after fitting, more than "
                f"the duplicate tolerance ({settings.duplicate_tolerance:g} Å)"
            )
    if merge.match is not None:
        merge.geometries.insert(0, {"level": None, "rmsd": merge.match.rmsd, "warned": False})

    candidates = own + other
    if not candidates:
        return
    top = max(_rank(c) for c in candidates)
    card_into = levels.node_geometry_optimization(into, tolerance)
    card_source = levels.node_geometry_optimization(source, tolerance)
    if card_into is not None and card_into in own and _rank(card_into) == top:
        level_id = card_into.level_id
    elif card_source is not None and card_source in other and _rank(card_source) == top:
        level_id = card_source.level_id
    else:
        level_id = next(c.level_id for c in candidates if _rank(c) == top)
    at_into = [c for c in own if c.level_id == level_id]
    at_source = [c for c in other if c.level_id == level_id]
    winner = "into" if at_into else "source"
    if at_into and at_source:
        found = next(
            (lv for lv in merge.levels if lv.key == energies.LevelKey(level_id, level_id)), None
        )
        winner = found.counts if found is not None else "into"
    if winner == "into":
        chosen = (
            card_into if card_into is not None and card_into.level_id == level_id else at_into[-1]
        )
        if card_into is not None and chosen is card_into:
            merge.card = {"from": "into", "level": levels.label(chosen.level)}
            return
    else:
        chosen = (
            card_source
            if card_source is not None and card_source.level_id == level_id
            else at_source[-1]
        )
    merge.new_geometry = [list(row) for row in rows_of(chosen)]
    merge.card = {
        "from": "into" if winner == "into" else "from",
        "level": levels.label(chosen.level),
    }
    owner = into if winner == "into" else source
    merge.changes.append(
        f"{_quoted(into)} shows {_quoted(owner)}'s geometry at {levels.label(chosen.level)}"
    )


def _plan_fields(session: Session, merge: Merge) -> None:
    """D262a, f: what B keeps and takes of A's own fields."""
    into, source = merge.into, merge.source
    values: dict[str, Any] = {}
    tags = list(into.tags or [])
    added = [t for t in source.tags or [] if t not in tags]
    if added:
        values["tags"] = tags + added
        merge.changes.append(f"Tags added: {', '.join(map(str, added))}")
    if (source.notes or "").strip():
        values["notes"] = (
            (into.notes.rstrip() + "\n\n" if (into.notes or "").strip() else "")
            + f"From {_quoted(source)}:\n"
            + source.notes.strip()
        )
        merge.changes.append(f"{_quoted(source)}'s notes are added to {_quoted(into)}'s")
    if into.step_id is None and source.step_id is not None:
        values["step_id"] = source.step_id
        merge.changes.append(f"{_quoted(into)} takes {_quoted(source)}'s step")
    group_id = into.group_id
    if group_id is None and source.group_id is not None:
        if source.canvas_id == into.canvas_id:
            group_id = source.group_id
            values["group_id"] = group_id
            values["group_position"] = source.group_position
            values["branch_id"] = None
            merge.changes.append(f"{_quoted(into)} joins {_quoted(source)}'s group")
        else:
            merge.changes.append(
                f"{_quoted(source)}'s group is on another canvas; {_quoted(into)} does not join it"
            )
    if group_id is None:
        a, b = source.branch_id, into.branch_id
        if b is None and a is not None:
            values["branch_id"] = a
            merge.changes.append(f"{_quoted(into)} takes {_quoted(source)}'s branch")
        elif a is not None and b is not None and a != b:
            merge._branch_choice = True
            choice = merge.options.branch
            if choice == "none":
                values["branch_id"] = None
                merge.changes.append(
                    "The two are on different branches: the merged node is on no branch of its "
                    "own, so it is shared by both (D67)"
                )
            elif choice == "take":
                values["branch_id"] = a
                merge.changes.append(f"{_quoted(into)} takes {_quoted(source)}'s branch")
            else:
                merge.changes.append(f"{_quoted(into)} keeps its branch")
    if into.origin_branch_id is None and source.origin_branch_id is not None:
        values["origin_branch_id"] = source.origin_branch_id
    if into.path_edge_id is None and source.path_edge_id is not None:
        values["path_edge_id"] = source.path_edge_id
        values["on_edge_only"] = source.on_edge_only
        merge.changes.append(f"{_quoted(into)} takes {_quoted(source)}'s place on its edge")
    derived = into.derived_from_id
    if derived == source.id:
        derived = None
    if derived is None and source.derived_from_id not in (None, into.id):
        derived = source.derived_from_id
    if derived != into.derived_from_id:
        values["derived_from_id"] = derived
    if merge.new_geometry is not None:
        values["geometry"] = merge.new_geometry
    into_id = into.id

    def write(session: Session) -> None:
        if values:
            session.execute(update(Node).where(Node.id == into_id).values(**values))

    merge.ops.append(write)


def _ends(edge: Transition) -> tuple[str, str]:
    return edge.source_id, edge.target_id


def _edge_label(session: Session, edge: Transition) -> str:
    def end(node_id: str | None, group_id: str | None) -> str:
        if node_id is not None:
            node = session.get(Node, node_id)
            return _quoted(node) if node else "?"
        group = session.get(GroupNode, group_id)
        return f"“{group.label or 'Untitled group'}”" if group else "?"

    return (
        f"{end(edge.source_node_id, edge.source_group_id)} → "
        f"{end(edge.target_node_id, edge.target_group_id)}"
    )


def _plan_edges(session: Session, merge: Merge) -> None:
    """D262e: A's edges move to B; an A–B edge is dropped; one parallel to B's edge (same
    neighbour, same direction) is dropped, its free species and scan path chip going to B's
    edge where it has none. As a free species, A's places on transitions become B's."""
    a, b = merge.source.id, merge.into.id
    touching = list(
        session.scalars(
            select(Transition).where(
                or_(
                    Transition.source_node_id.in_([a, b]),
                    Transition.target_node_id.in_([a, b]),
                )
            )
        )
    )
    others = {_ends(e): e for e in touching if a not in (e.source_node_id, e.target_node_id)}
    chips = {
        n.path_edge_id: n.id
        for n in session.scalars(
            select(Node).where(Node.path_edge_id.in_([e.id for e in touching]))
        )
    }
    moves: list[tuple[str, str]] = []  # (edge id, "source"/"target")
    drops: list[str] = []
    species_moves: list[tuple[str, str]] = []  # (row id, onto edge id)
    chip_moves: list[tuple[str, str]] = []  # (path node id, onto edge id)
    for edge in touching:
        if a not in (edge.source_node_id, edge.target_node_id):
            continue
        label = _edge_label(session, edge)
        source_end = b if edge.source_node_id == a else edge.source_id
        target_end = b if edge.target_node_id == a else edge.target_id
        if source_end == target_end:
            drops.append(edge.id)
            merge.edges_dropped.append(f"{label} (between the two)")
            continue
        twin = others.get((source_end, target_end))
        if twin is not None:
            drops.append(edge.id)
            merge.edges_dropped.append(f"{label} ({_quoted(merge.into)} has the same edge)")
            have = {s.species_id for s in twin.species}
            for item in edge.species:
                if item.species_id not in have:
                    species_moves.append((item.id, twin.id))
            if edge.id in chips and twin.id not in chips:
                chip_moves.append((chips[edge.id], twin.id))
            continue
        if edge.source_node_id == a:
            moves.append((edge.id, "source"))
        if edge.target_node_id == a:
            moves.append((edge.id, "target"))
        merge.edges_moved.append(label)
        others[(source_end, target_end)] = edge

    # A free species on transitions (D69).
    species_rows = list(
        session.scalars(select(TransitionSpecies).where(TransitionSpecies.species_id == a))
    )
    on_b = {
        s.transition_id
        for s in session.scalars(select(TransitionSpecies).where(TransitionSpecies.species_id == b))
    }
    species_renamed = [s.id for s in species_rows if s.transition_id not in on_b]
    species_dropped = [s.id for s in species_rows if s.transition_id in on_b]
    if species_rows:
        merge.changes.append(
            f"{_quoted(merge.into)} takes {_quoted(merge.source)}'s place on "
            f"{len(species_rows)} transition(s)"
            + (f", keeping its own count on {len(species_dropped)}" if species_dropped else "")
        )

    def write(session: Session) -> None:
        for row_id, edge_id in species_moves:
            session.execute(
                update(TransitionSpecies)
                .where(TransitionSpecies.id == row_id)
                .values(transition_id=edge_id)
            )
        for node_id, edge_id in chip_moves:
            session.execute(update(Node).where(Node.id == node_id).values(path_edge_id=edge_id))
        if drops:
            session.execute(delete(Transition).where(Transition.id.in_(drops)))
        for edge_id, end in moves:
            column = Transition.source_node_id if end == "source" else Transition.target_node_id
            session.execute(update(Transition).where(Transition.id == edge_id).values({column: b}))
        if species_dropped:
            session.execute(
                delete(TransitionSpecies).where(TransitionSpecies.id.in_(species_dropped))
            )
        if species_renamed:
            session.execute(
                update(TransitionSpecies)
                .where(TransitionSpecies.id.in_(species_renamed))
                .values(species_id=b)
            )

    merge.ops.append(write)


def _renamed(ids: list[Any], a: str, b: str, adjacent: bool = True) -> list[Any]:
    """A list of ids with A replaced by B; a B next to a B (adjacent) or anywhere after the
    first (not adjacent) is kept once."""
    out: list[Any] = []
    for item in ids:
        item = b if item == a else item
        if item == b and ((adjacent and out and out[-1] == b) or (not adjacent and b in out)):
            continue
        out.append(item)
    return out


def _renumbered(atoms: list[Any], back: list[int]) -> list[Any]:
    return [back[n - 1] + 1 if isinstance(n, int) and 1 <= n <= len(back) else n for n in atoms]


def _plan_records(session: Session, merge: Merge) -> None:
    """D262f, g: everything else that names A is repointed to B."""
    a, b = merge.source.id, merge.into.id
    into, source = merge.into, merge.source
    assert merge.mapping is not None
    back = atom_maps.inverse(merge.mapping)

    def say(text: str) -> None:
        merge.changes.append(text)

    # Nodes derived from A (ID-5), branches split at A, groups it stands for, boxes it shows.
    derived = list(session.scalars(select(Node.id).where(Node.derived_from_id == a, Node.id != b)))
    if derived:
        say(
            f"{len(derived)} node(s) derived from {_quoted(source)} now derive from {_quoted(into)}"
        )
    split = list(session.scalars(select(Branch).where(Branch.split_node_id == a)))
    for branch in split:
        say(f"Branch “{branch.name}” is split at {_quoted(into)}")
    groups = list(session.scalars(select(GroupNode).where(GroupNode.representative_id == a)))
    for group in groups:
        joins = into.group_id == group.id or (
            into.group_id is None
            and source.group_id == group.id
            and source.canvas_id == into.canvas_id
        )
        say(
            f"{_quoted(into)} represents group “{group.label}”"
            if joins
            else f"Group “{group.label}” loses its representative"
        )
    faces = list(session.scalars(select(SubInvestigation).where(SubInvestigation.face_id == a)))
    for box in faces:
        say(f"Sub-investigation “{box.label}” shows {_quoted(into)}")
    notes = list(session.scalars(select(NodeNote.id).where(NodeNote.node_id == a)))
    if notes:
        say(f"{len(notes)} pinned note(s) move to {_quoted(into)}")

    # Atom lists (D80, D81), renumbered through the map; both in one set keep B's.
    lists: list[tuple[type, str, Any, dict[str, Any] | None]] = []
    for model, key in ((AlignmentSetAtoms, "set_id"), (StericProfileAtoms, "profile_id")):
        mine = {getattr(r, key) for r in session.scalars(select(model).where(model.node_id == b))}
        for row in session.scalars(select(model).where(model.node_id == a)):
            if getattr(row, key) in mine:
                lists.append((model, key, getattr(row, key), None))
                continue
            if model is AlignmentSetAtoms:
                values = {"atoms": _renumbered(row.atoms, back)}
            else:
                values = {
                    k: _renumbered(getattr(row, k), back)
                    for k in ("centre", "z_axis", "xz_plane", "excluded")
                }
            lists.append((model, key, getattr(row, key), values))
    if lists:
        say(
            f"{len(lists)} alignment or steric atom list(s) of {_quoted(source)} "
            + ("move to it, renumbered" if any(v for *_x, v in lists) else "are dropped")
            + (
                "; where both are in one set, its own is kept"
                if any(v is None for *_x, v in lists)
                else ""
            )
        )

    # Selectivities (D83, D103).
    members = list(session.scalars(select(SelectivityMember).where(SelectivityMember.node_id == a)))
    outcomes_with_b = set(
        session.scalars(select(SelectivityMember.outcome_id).where(SelectivityMember.node_id == b))
    )
    member_drops = [m.id for m in members if m.outcome_id in outcomes_with_b]
    member_moves = [m.id for m in members if m.outcome_id not in outcomes_with_b]
    selectivity_refs = list(
        session.scalars(select(Selectivity).where(Selectivity.reference_id == a))
    )
    for item in {session.get(SelectivityOutcome, m.outcome_id).selectivity_id for m in members} | {
        s.id for s in selectivity_refs
    }:
        say(f"Selectivity “{session.get(Selectivity, item).name}” names {_quoted(into)}")

    # Saved profiles, turnovers, equilibria (node ids in JSON) and the energy reference.
    profile_values: dict[str, dict[str, Any]] = {}
    for profile in session.scalars(select(EnergyProfile)):
        paths = []
        hit = profile.reference_id == a
        for path in profile.paths or []:
            if isinstance(path, dict) and a in (path.get("ids") or []):
                hit = True
                path = {**path, "ids": _renamed(path["ids"], a, b)}
            paths.append(path)
        if hit:
            profile_values[profile.id] = {
                "paths": paths,
                "reference_id": b if profile.reference_id == a else profile.reference_id,
            }
            say(f"Saved profile “{profile.name}” names {_quoted(into)}")
    turnover_values: dict[str, dict[str, Any]] = {}
    for turnover in session.scalars(select(Turnover)):
        if a in (turnover.path or []):
            turnover_values[turnover.id] = {"path": _renamed(turnover.path, a, b)}
            say(f"Turnover “{turnover.name}” names {_quoted(into)}")
    equilibrium_values: dict[str, dict[str, Any]] = {}
    for item in session.scalars(select(ConformerEquilibrium)):
        listed = [m for m in item.members or [] if isinstance(m, dict)]
        if a not in {m.get("id") for m in listed} | {item.start_id} | set(item.next_ids or []):
            continue
        seen: set[str] = set()
        new_members = []
        for member in item.members or []:
            if isinstance(member, dict) and member.get("id") in (a, b):
                if b in seen:
                    continue
                seen.add(b)
                member = {**member, "id": b}
            new_members.append(member)
        equilibrium_values[item.id] = {
            "members": new_members,
            "start_id": b if item.start_id == a else item.start_id,
            "next_ids": _renamed(item.next_ids or [], a, b, adjacent=False),
        }
        say(f"Conformer equilibrium “{item.name}” names {_quoted(into)}")
    info = session.scalars(select(InvestigationInfo)).first()
    view_state = None
    if info is not None and (info.view_state or {}).get("reference_id") == a:
        view_state = {**info.view_state, "reference_id": b}
        say(f"The energy reference is {_quoted(into)}")

    group_ids = [g.id for g in groups]
    into_joins = {
        g.id: (
            into.group_id == g.id
            or (
                into.group_id is None
                and source.group_id == g.id
                and source.canvas_id == into.canvas_id
            )
        )
        for g in groups
    }

    def write(session: Session) -> None:
        if derived:
            session.execute(update(Node).where(Node.id.in_(derived)).values(derived_from_id=b))
        if split:
            session.execute(update(Branch).where(Branch.split_node_id == a).values(split_node_id=b))
        for group_id in group_ids:
            session.execute(
                update(GroupNode)
                .where(GroupNode.id == group_id)
                .values(representative_id=b if into_joins[group_id] else None)
            )
        if faces:
            session.execute(
                update(SubInvestigation).where(SubInvestigation.face_id == a).values(face_id=b)
            )
        if notes:
            session.execute(update(NodeNote).where(NodeNote.node_id == a).values(node_id=b))
        for model, key, owner, values in lists:
            where = (model.node_id == a, getattr(model, key) == owner)
            if values is None:
                session.execute(delete(model).where(*where))
            else:
                session.execute(update(model).where(*where).values(node_id=b, **values))
        if member_drops:
            session.execute(delete(SelectivityMember).where(SelectivityMember.id.in_(member_drops)))
        if member_moves:
            session.execute(
                update(SelectivityMember)
                .where(SelectivityMember.id.in_(member_moves))
                .values(node_id=b)
            )
        if selectivity_refs:
            session.execute(
                update(Selectivity).where(Selectivity.reference_id == a).values(reference_id=b)
            )
        for item_id, values in profile_values.items():
            session.execute(
                update(EnergyProfile).where(EnergyProfile.id == item_id).values(**values)
            )
        for item_id, values in turnover_values.items():
            session.execute(update(Turnover).where(Turnover.id == item_id).values(**values))
        for item_id, values in equilibrium_values.items():
            session.execute(
                update(ConformerEquilibrium)
                .where(ConformerEquilibrium.id == item_id)
                .values(**values)
            )
        if view_state is not None and info is not None:
            session.execute(
                update(InvestigationInfo)
                .where(InvestigationInfo.id == info.id)
                .values(view_state=view_state)
            )

    merge.ops.append(write)


# ---------- the write ----------


def _json_safe(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, bytes):
        return base64.b64encode(value).decode("ascii")
    return value


def _row(table: Table, row: Any) -> dict[str, Any]:
    return {c: _json_safe(v) for c, v in row._mapping.items()}


# The rows a merge may change, by table: a condition on the two nodes (and the edges touching
# them) read before and after. Normal modes are left out: they are only reordered, and the
# entry keeps the map instead (`modes`).
def _watched(a: str, b: str, edges: list[str], calcs: list[str]) -> dict[str, Any]:
    t = Base.metadata.tables
    return {
        "nodes": or_(
            t["nodes"].c.id.in_([a, b]),
            t["nodes"].c.derived_from_id.in_([a, b]),
            t["nodes"].c.path_edge_id.in_(edges),
        ),
        "calculations": t["calculations"].c.node_id.in_([a, b]),
        "calculation_results": t["calculation_results"].c.calculation_id.in_(calcs),
        "transitions": t["transitions"].c.id.in_(edges),
        "transition_species": or_(
            t["transition_species"].c.transition_id.in_(edges),
            t["transition_species"].c.species_id.in_([a, b]),
        ),
        "branches": t["branches"].c.split_node_id.in_([a, b]),
        "group_nodes": t["group_nodes"].c.representative_id.in_([a, b]),
        "sub_investigations": t["sub_investigations"].c.face_id.in_([a, b]),
        "node_notes": t["node_notes"].c.node_id.in_([a, b]),
        "alignment_set_atoms": t["alignment_set_atoms"].c.node_id.in_([a, b]),
        "steric_profile_atoms": t["steric_profile_atoms"].c.node_id.in_([a, b]),
        "selectivity_members": t["selectivity_members"].c.node_id.in_([a, b]),
        "selectivities": None,
        "energy_profiles": None,
        "turnovers": None,
        "conformer_equilibria": None,
        "investigation_info": None,
    }


def _key(table: Table, row: dict[str, Any]) -> tuple:
    return tuple(row[c.name] for c in table.primary_key.columns)


def _read(session: Session, watched: dict[str, Any]) -> dict[str, dict[tuple, dict[str, Any]]]:
    found: dict[str, dict[tuple, dict[str, Any]]] = {}
    for name, where in watched.items():
        table = Base.metadata.tables[name]
        query = select(table) if where is None else select(table).where(where)
        rows = [_row(table, r) for r in session.execute(query)]
        found[name] = {_key(table, r): r for r in rows}
    return found


def apply(session: Session, merge: Merge) -> HistoryEntry:
    """Write the merge planned. Refused while it is refused, or warned and not `anyway`."""
    if merge.refusals:
        raise MergeError(merge.refusals[0])
    if merge.warnings and not merge.options.anyway:
        raise MergeError("Check the warnings first: " + "; ".join(merge.warnings))
    into, source = merge.into, merge.source
    a, b = source.id, into.id
    session.flush()
    edges = list(
        session.scalars(
            select(Transition.id).where(
                or_(
                    Transition.source_node_id.in_([a, b]),
                    Transition.target_node_id.in_([a, b]),
                )
            )
        )
    )
    calc_ids = [c.id for c in merge.calculations] + [c.id for c in into.calculations]
    watched = _watched(a, b, edges, calc_ids)
    before = _read(session, watched)

    summary = merge.as_dict()
    at = utcnow()
    entry = history.record(
        session,
        "node",
        b,
        ACTION,
        old=None,
        new={
            "from_id": a,
            "from_label": source.label,
            "into_label": into.label,
            "summary": {
                k: summary[k]
                for k in ("levels", "card", "edges_moved", "edges_dropped", "changes", "warnings")
            },
        },
    )
    session.flush()
    origin = {"node_id": a, "label": source.label, "at": at.isoformat(), "entry_id": entry.id}

    # D255 links first, then the set-aside marks, while every calculation is where it was.
    for calculation_id, found in merge.links.items():
        session.execute(
            update(Calculation)
            .where(Calculation.id == calculation_id)
            .values(geometry_source_id=found)
        )
    for calculation in into.calculations:
        added = merge.set_aside.get(calculation.id)
        if added:
            session.execute(
                update(Calculation)
                .where(Calculation.id == calculation.id)
                .values(set_aside=list(dict.fromkeys([*(calculation.set_aside or []), *added])))
            )
    modes: dict[str, list[int]] = {}
    for calculation in merge.calculations:
        mapping = merge.maps.get(calculation.id)
        values: dict[str, Any] = {
            "node_id": b,
            "merged_from": origin,
            "set_aside": list(
                dict.fromkeys(
                    [*(calculation.set_aside or []), *merge.set_aside.get(calculation.id, [])]
                )
            ),
        }
        if not atom_maps.is_identity(mapping):
            assert mapping is not None
            values["geometry"] = atom_maps.reordered(calculation.geometry, mapping)
            values["start_geometry"] = atom_maps.reordered(calculation.start_geometry, mapping)
            values["atom_map"] = atom_maps.compose(calculation.atom_map, mapping)
            values["constraints"] = [
                atom_maps.constraint_text(c, mapping) for c in calculation.constraints or []
            ]
            result = calculation.result
            if result is not None and result.mode_check:
                session.execute(
                    update(CalculationResult)
                    .where(CalculationResult.calculation_id == calculation.id)
                    .values(mode_check=atom_maps.mode_check(result.mode_check, mapping))
                )
            stored = session.get(NormalModes, calculation.id)
            if stored is not None:
                data = atom_maps.modes(unpack_modes(stored.data), mapping)
                session.execute(
                    update(NormalModes)
                    .where(NormalModes.calculation_id == calculation.id)
                    .values(data=pack_modes(data))
                )
                modes[calculation.id] = mapping
        session.execute(update(Calculation).where(Calculation.id == calculation.id).values(values))
    for op in merge.ops:
        op(session)
    session.execute(delete(Node).where(Node.id == a))
    session.flush()
    session.expire_all()

    after = _read(session, watched)
    rows = []
    for name in watched:
        for key in dict.fromkeys([*before[name], *after[name]]):
            old, new = before[name].get(key), after[name].get(key)
            if old != new:
                rows.append({"table": name, "key": list(key), "before": old, "after": new})
    entry = session.get(HistoryEntry, entry.id)
    assert entry is not None
    entry.old_value = {"rows": rows, "modes": modes}
    session.flush()
    return entry


def merge_nodes(
    session: Session,
    source_id: str,
    into_id: str,
    options: Options | None = None,
    folder: Path | None = None,
) -> tuple[Merge, HistoryEntry]:
    found = plan(session, source_id, into_id, options, folder)
    return found, apply(session, found)
