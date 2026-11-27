"""Holes in a mechanism Claude builds from a folder, and the bound on filling them (D265,
FR-PANEL-10, FR-MCP-24).

`find` lists the holes of D265a in the open investigation (a playground in practice), each with
its kind, the nodes involved, the bond changes (A181's rule with metal–hydrogen contacts weak,
A184, through `structure_compare`) and a suggested fill with the tools there are (D265b). It
changes nothing. The kinds, most useful first:

- `ts_end`: a transition state with no edge on a side. What lies there comes from the cheapest
  evidence at hand: a finished mode-follow job of the TS (`follow_mode.ends`), else its IRC's
  ends matched against the minima (A184's `end_match`), else only the bonds its imaginary mode
  moves; the hole says which.
- `missing_mirror_image` (D267): a TS side's minimum is the mirror image of a node there is: a
  mode-follow end or the IRC's end matches that node only reflected, else `find_ts_partners`
  finds it on the side only reflected. A mirror image is another molecule, so the minimum is
  missing; it is made exactly by the builder's `mirror` step on that node, then refined.
- `missing_ts`: two minima joined by an edge, or next to each other in the steps' order with
  nothing joining them, one elementary step apart (one to three bonds formed or broken, A184),
  with the same atoms or balanced by the edge's free species, and no TS between them.
- `unbalanced`: an edge whose two sides, with its free species, differ in atoms (D69).
- `missing_intermediate`: two minima linked in the same way but more than one step apart.
- `cycle`: the chain of steps starts from a metal complex and its last minimum is not the same
  molecule as its first, so the catalyst is not given back.

Only what edges and the steps' order already link is looked at: no branch is invented (D265d).
Each hole has a stable id (its kind and the records in it), which `propose_mechanism` takes
when Claude fills it (`hole` on an edge, `filled` for its nodes): `mark_filled` marks a filled
node planned, "MACE guess" (label and tag), and refuses one holding results above the MACE
level (D265c, D246's method ranks); `check_bound` refuses an 11th hole filled since Work on its
own was turned on (or in the playground when it is off), D265d.
"""

import hashlib
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from chembook3d import local_jobs, xyz
from chembook3d.models import (
    GroupNode,
    Node,
    NodeKind,
    ReactionStep,
    Role,
    Status,
    Transition,
)
from chembook3d.pathtools import METALS
from chembook3d.services import atom_matching, follow_mode, levels, thermo_settings, ts_partners
from chembook3d.services import nodes as node_service
from chembook3d.services import structure_compare as compare
from chembook3d.services import transitions as transition_service
from chembook3d.services.records import RecordError

MAX_FILLED = 10  # D265d: holes filled in one run; the rest are flagged
MAX_LISTED = 25  # holes listed in one answer, most useful first
MAX_PAIRS = 40  # pairs of minima compared in one call
MACE_GUESS = "MACE guess"  # D265c: the filled node's label suffix and tag (A185)
FILLED = "Filled by Claude"  # D265c, A184: the evidence of an edge that fills a hole
KINDS = (
    "ts_end",
    "missing_mirror_image",
    "missing_ts",
    "unbalanced",
    "missing_intermediate",
    "cycle",
)
_HOLE_ID = re.compile(r"\b((?:" + "|".join(KINDS) + r")-[0-9a-f]{8})\b")
_FILLED_EDGE = re.compile(r"^\s*filled by claude", re.IGNORECASE)


def hole_id(kind: str, *parts: str) -> str:
    digest = hashlib.sha1("|".join((kind, *parts)).encode()).hexdigest()[:8]
    return f"{kind}-{digest}"


def check_hole_id(value: Any) -> str:
    text = str(value or "").strip()
    if not _HOLE_ID.fullmatch(text):
        raise RecordError(f"“{text}” is not a hole id from find_gaps (e.g. “missing_ts-1a2b3c4d”)")
    return text


# ---------- the records, as the mechanism's units ----------


@dataclass
class _Unit:
    """A node, or a group standing for its members (EN-7), as an end of edges."""

    id: str
    label: str
    node: Node | None  # the node whose structure stands for it
    ts: bool
    step_id: str | None
    branch_id: str | None
    seq: int

    def ref(self) -> dict[str, Any]:
        return {"id": self.id, "label": self.label, "role": "TS" if self.ts else "minimum"}


@dataclass
class _Mechanism:
    units: dict[str, _Unit]
    of_node: dict[str, str]  # node id → its unit's id
    edges: list[Transition]
    incoming: dict[str, list[Transition]] = field(default_factory=lambda: defaultdict(list))
    outgoing: dict[str, list[Transition]] = field(default_factory=lambda: defaultdict(list))

    def unit(self, record_id: str) -> _Unit | None:
        return self.units.get(self.of_node.get(record_id, record_id))

    def neighbours(self, unit_id: str) -> set[str]:
        out = {self.of_node.get(t.source_id, t.source_id) for t in self.incoming[unit_id]}
        return out | {self.of_node.get(t.target_id, t.target_id) for t in self.outgoing[unit_id]}

    def component(self, unit_id: str) -> set[str]:
        seen, todo = {unit_id}, [unit_id]
        while todo:
            for other in self.neighbours(todo.pop()) - seen:
                seen.add(other)
                todo.append(other)
        return seen


def _group_node(session: Session, group: GroupNode, members: list[Node]) -> Node | None:
    if group.representative_id:
        found = session.get(Node, group.representative_id)
        if found is not None and found.geometry:
            return found
    return next((m for m in members if m.geometry), None)


def _mechanism(session: Session) -> _Mechanism:
    units: dict[str, _Unit] = {}
    of_node: dict[str, str] = {}
    members: dict[str, list[Node]] = defaultdict(list)
    for node in node_service.list_nodes(session, with_calculations=True):
        if node.kind == NodeKind.SPECIES:
            continue
        if node.group_id:
            members[node.group_id].append(node)
            of_node[node.id] = node.group_id
            continue
        units[node.id] = _Unit(
            node.id,
            node.label or "Untitled node",
            node,
            node.role == Role.TRANSITION_STATE,
            node.step_id,
            node.branch_id,
            node.seq,
        )
    for group in session.scalars(select(GroupNode).order_by(GroupNode.seq)):
        found = _group_node(session, group, members[group.id])
        units[group.id] = _Unit(
            group.id,
            group.label or "Untitled group",
            found,
            transition_service.is_ts(group),
            group.step_id,
            found.branch_id if found is not None else None,
            group.seq,
        )
    mechanism = _Mechanism(units, of_node, transition_service.list_transitions(session))
    for edge in mechanism.edges:
        source = of_node.get(edge.source_id, edge.source_id)
        target = of_node.get(edge.target_id, edge.target_id)
        mechanism.outgoing[source].append(edge)
        mechanism.incoming[target].append(edge)
    return mechanism


def _rows(unit: _Unit) -> compare.Rows | None:
    if unit.node is None or not unit.node.geometry:
        return None
    return [[r[0], float(r[1]), float(r[2]), float(r[3])] for r in unit.node.geometry]


def _structure(session: Session, folder: Path, unit: _Unit, tolerance: float):
    if unit.node is None or not unit.node.geometry:
        return None
    found = compare.resolve(session, folder, {"node_id": unit.node.id}, tolerance)
    found.label = unit.label
    return found


def _species_rows(node: Node | None) -> compare.Rows | None:
    if node is None or not node.geometry:
        return None
    return [[r[0], float(r[1]), float(r[2]), float(r[3])] for r in node.geometry]


def _bond_names(pairs: list[dict[str, Any]]) -> list[str]:
    return [p["bond"] for p in pairs]


# ---------- holes ----------


@dataclass
class Hole:
    kind: str
    id: str
    nodes: list[dict[str, Any]]
    what: str  # the hole, in a sentence
    fill: str  # the suggested fill (D265b)
    bonds: dict[str, Any] | None = None
    difference: str | None = None
    evidence: str | None = None  # what the hole rests on
    weight: float = 0.0  # within its kind: smaller first

    def out(self) -> dict[str, Any]:
        found: dict[str, Any] = {
            "id": self.id,
            "kind": self.kind,
            "nodes": self.nodes,
            "what": self.what,
            "fill": self.fill,
        }
        for name in ("bonds", "difference", "evidence"):
            if getattr(self, name):
                found[name] = getattr(self, name)
        return found


TS_GUESS_FILL = (
    "start_local_ts_guess between them (mace-omol; correct=true only when the person asked for "
    "the PBE correction, D239), then propose_mechanism with the two edges through the new TS, "
    "low, hole set, and filled naming the TS node"
)
REFINE = "start_local_refine with method mace-omol"
MIRROR_FILL = (
    "build_structure from node “{label}” (node_id {node_id}) with the one step mirror, "
    "create_node with its build_id (planned), then " + REFINE + " on it, then "
    "propose_mechanism with the edge to the TS, low, hole set, and filled naming the new node."
)


def _mirror_hole(unit: "_Unit", image: dict[str, Any], where: str, evidence: str) -> Hole:
    """D267: the TS `unit` needs the mirror image of the node `image` (node_id, label, rmsd
    reflected) on the side `where` says."""
    rmsd = image.get("rmsd")
    return Hole(
        "missing_mirror_image",
        hole_id("missing_mirror_image", unit.id, image["node_id"]),
        [unit.ref(), {"id": image["node_id"], "label": image["label"]}],
        f"Missing mirror image of “{image['label']}”, needed by “{unit.label}”: {where} "
        f"matches “{image['label']}” only reflected"
        + (f" ({rmsd:.2f} Å)" if rmsd is not None else "")
        + ", and a mirror image is another molecule (D267).",
        MIRROR_FILL.format(label=image["label"], node_id=image["node_id"]),
        evidence=evidence,
        weight=0,
    )


def _follow_job(folder: Path, node_id: str) -> str | None:
    for job in local_jobs.list_jobs(folder):  # newest first
        follow = job.get("follow_mode") or {}
        if job.get("kind") != local_jobs.FOLLOW_MODE or follow.get("ts_node_id") != node_id:
            continue
        if job.get("status") in ("converged", "not_converged"):
            return job["id"]
    return None


def _irc_ends(session: Session, folder: Path, node: Node, tolerance: float) -> dict[str, Any]:
    out = {}
    for calc in node.irc_calculations:
        if calc.irc_part != "path":
            continue
        for direction in ("forward", "reverse"):
            if direction in out:
                continue
            ref = {"calculation_id": calc.id, "irc_end": direction}
            try:
                out[direction] = compare.resolve(session, folder, ref, tolerance)
            except compare.StructureError:
                continue
    return out


def _matching_units(rows: compare.Rows, minima: list[_Unit]) -> list[tuple[_Unit, float]]:
    found = []
    for unit in minima:
        other = _rows(unit)
        if other is None:
            continue
        same = compare.end_match(rows, other)
        if same is not None:
            found.append((unit, same.rmsd))
    return sorted(found, key=lambda m: m[1])


def _partner_mirrors(
    session: Session,
    folder: Path,
    mechanism: _Mechanism,
    unit: _Unit,
    ts: compare.Structure,
    tolerance: float,
    made: dict[Any, Any],
) -> list[Hole]:
    """D267: the sides of a TS with neither a mode-follow nor an IRC that `find_ts_partners`
    finds a minimum on only reflected (`mirror_of`), unless an edge already joins the TS to a
    candidate of that side. `made` keeps the candidates per geometry level, and the IRC end
    nodes (A192), for every TS of the call."""
    from chembook3d.services import irc_ends  # it imports the import code

    node = unit.node
    assert node is not None
    if "ends" not in made:
        made["ends"] = irc_ends.end_nodes(session, folder, tolerance)
    level = ts_partners.ts_level(session, {"node_id": node.id})
    key = ("level", level.id if level is not None else None)
    if key not in made:
        made[key] = ts_partners.from_investigation(session, folder, tolerance, None, level)
    candidates, species, _ = made[key]
    others = {n for n, ts_id in made["ends"].items() if ts_id != node.id} | {node.id}
    candidates = [c for c in candidates if c.entry.get("node_id") not in others]
    species = [c for c in species if c.entry.get("node_id") != node.id]
    found = ts_partners.find(ts, candidates, species, tolerance, level=level)
    if (found.get("check") or {}).get("verdict") == "links":
        return []
    near = mechanism.neighbours(unit.id)
    holes = []
    for side in found["sides"]:
        image = side.get("mirror_of")
        if not image or not image.get("node_id"):
            continue
        linked = {
            mechanism.of_node.get(c.get("node_id"), c.get("node_id")) for c in side["candidates"]
        }
        if linked & near:
            continue
        holes.append(
            _mirror_hole(
                unit,
                image,
                f"its mode's {side['side']} side (find_ts_partners)",
                found["summary"],
            )
        )
    return holes


def _ts_end_holes(
    session: Session, folder: Path, mechanism: _Mechanism, tolerance: float
) -> list[Hole]:
    holes = []
    made: dict[Any, Any] = {}  # D267: the candidates `_partner_mirrors` makes, once per call
    minima = [u for u in mechanism.units.values() if not u.ts]
    for unit in mechanism.units.values():
        if not unit.ts:
            continue
        before, after = mechanism.incoming[unit.id], mechanism.outgoing[unit.id]
        if before and after:
            continue
        sides = [s for s, edges in (("reactant", before), ("product", after)) if not edges]
        side_text = " and ".join(sides)
        node = unit.node
        involved = [unit.ref()]
        guess = (node is not None and (node.label or "").startswith("TS guess")) or (
            node is not None and MACE_GUESS in (node.tags or [])
        )
        if guess:
            holes.append(
                Hole(
                    "ts_end",
                    hole_id("ts_end", unit.id, side_text),
                    involved,
                    f"“{unit.label}” is a TS guess with no edge on its {side_text} side.",
                    "propose_mechanism with its edges to the two minima it was made between "
                    "(low, with the hole id of the gap it fills).",
                    weight=0,
                )
            )
            continue
        job_id = _follow_job(folder, node.id) if node is not None else None
        if job_id is not None:
            try:
                ends = follow_mode.ends(session, folder, job_id)
            except (RecordError, compare.StructureError):
                ends = None
            if ends is not None:
                for name, end in ends["ends"].items():
                    shown = follow_mode.SHOWN[name]
                    bonds = {
                        "formed": [b["bond"] for b in end["formed"]],
                        "broken": [b["bond"] for b in end["broken"]],
                    }
                    if end["matches"]:
                        linked = {m["node_id"] for m in end["matches"]}
                        linked = {mechanism.of_node.get(i, i) for i in linked}
                        if linked & mechanism.neighbours(unit.id):
                            continue
                        first = end["matches"][0]
                        holes.append(
                            Hole(
                                "ts_end",
                                hole_id("ts_end", unit.id, name),
                                [*involved, {"id": first["node_id"], "label": first["label"]}],
                                f"The {shown} end of “{unit.label}”'s mode-follow (job "
                                f"{job_id}) matches “{first['label']}” ({first['rmsd']:.2f} Å), "
                                "but no edge joins them.",
                                "No structure is needed: propose the edge with the confidence "
                                "the mode-follow gives (not a filled gap).",
                                bonds=bonds,
                                evidence=ends["verdict"],
                                weight=0,
                            )
                        )
                        continue
                    if end.get("mirror_of"):
                        holes.append(
                            _mirror_hole(
                                unit,
                                end["mirror_of"],
                                f"the {shown} end of its mode-follow (job {job_id})",
                                ends["verdict"],
                            )
                        )
                        continue
                    holes.append(
                        Hole(
                            "ts_end",
                            hole_id("ts_end", unit.id, name),
                            involved,
                            f"The {shown} end of “{unit.label}”'s mode-follow (job {job_id}) "
                            "matches no structure.",
                            f"make_node_from_end (job {job_id}, end {name}), then {REFINE} "
                            "on it, then propose_mechanism with the edge, low, hole set, and "
                            "filled naming the new node.",
                            bonds=bonds,
                            evidence=ends["verdict"],
                            weight=len(bonds["formed"]) + len(bonds["broken"]),
                        )
                    )
                continue
        ircs = _irc_ends(session, folder, node, tolerance) if node is not None else {}
        if ircs:
            ts_structure = _structure(session, folder, unit, tolerance)
            for direction, end in ircs.items():
                matches = _matching_units(end.rows, minima)
                if matches and {m[0].id for m in matches} & mechanism.neighbours(unit.id):
                    continue
                bonds = None
                if ts_structure is not None and compare.same_atoms(ts_structure.rows, end.rows):
                    found = compare.compare(ts_structure, end, count_weak=True)
                    bonds = {
                        "formed": _bond_names(found["formed"]),
                        "broken": _bond_names(found["broken"]),
                    }
                if matches:
                    first, rmsd = matches[0]
                    holes.append(
                        Hole(
                            "ts_end",
                            hole_id("ts_end", unit.id, direction),
                            [*involved, first.ref()],
                            f"The IRC's {direction} end of “{unit.label}” matches "
                            f"“{first.label}” ({rmsd:.2f} Å), but no edge joins them.",
                            "No structure is needed: propose the edge, high from the IRC "
                            "(not a filled gap).",
                            bonds=bonds,
                            weight=0,
                        )
                    )
                    continue
                image = _matching_units(atom_matching.mirrored(end.rows), minima)
                if image:
                    first, rmsd = image[0]
                    holes.append(
                        _mirror_hole(
                            unit,
                            {"node_id": first.id, "label": first.label, "rmsd": rmsd},
                            f"the IRC's {direction} end",
                            "IRC end compared with every minimum and their mirror images (A184, "
                            "D267)",
                        )
                    )
                    continue
                holes.append(
                    Hole(
                        "ts_end",
                        hole_id("ts_end", unit.id, direction),
                        involved,
                        f"The IRC's {direction} end of “{unit.label}” matches no minimum.",
                        "make_irc_end_node for that end (or create_node with its structure, "
                        f"compare_structures gives it), then {REFINE}, then propose_mechanism "
                        "with the edge, low, hole set, and filled naming the new node.",
                        bonds=bonds,
                        evidence="IRC end compared with every minimum (A184: same bonds, "
                        "RMSD under 0.5 Å)",
                        weight=len((bonds or {}).get("formed", [])),
                    )
                )
            continue
        mode = None
        structure = _structure(session, folder, unit, tolerance)
        if structure is not None:
            mode = compare._active(structure)
        if mode is not None and node is not None and structure is not None:
            mirrors = _partner_mirrors(session, folder, mechanism, unit, structure, tolerance, made)
            holes += mirrors
            if len(mirrors) >= len(sides):
                continue
        bonds = None
        if mode is not None and structure is not None:
            bonds = {"along_mode": [compare._bond(structure.rows, i, j) for i, j, _ in mode.active]}
        holes.append(
            Hole(
                "ts_end",
                hole_id("ts_end", unit.id, side_text),
                involved,
                f"No edge joins “{unit.label}” on its {side_text} side. Only that is known: "
                "no IRC or finished mode-follow says what lies there"
                + ("" if mode is not None else ", and it has no imaginary mode to follow")
                + ".",
                "start_local_follow_mode on it first (cheapest evidence), then find_gaps again: "
                "an end that matches nothing becomes the filled minimum (make_node_from_end, "
                f"then {REFINE})."
                if mode is not None
                else "Flag it: without an imaginary mode there is nothing to follow.",
                bonds=bonds,
                weight=1 if mode is not None else 5,
            )
        )
    return holes


def _composition(rows: compare.Rows | None) -> Counter[str] | None:
    return None if rows is None else Counter(r[0] for r in rows)


def _side_atoms(
    session: Session, mechanism: _Mechanism, edge: Transition
) -> tuple[Counter[str], Counter[str], list, list] | None:
    """The atoms before (source + species joining) and after (target + species leaving) an
    edge, with the species' rows; None when something has no coordinates."""
    source, target = mechanism.unit(edge.source_id), mechanism.unit(edge.target_id)
    if source is None or target is None:
        return None
    before, after = _composition(_rows(source)), _composition(_rows(target))
    if before is None or after is None:
        return None
    joins, leaves = [], []
    for entry in edge.species:
        rows = _species_rows(session.get(Node, entry.species_id))
        if rows is None:
            return None
        for _ in range(entry.count):
            (joins if entry.direction == "joins" else leaves).append(rows)
            (before if entry.direction == "joins" else after).update(r[0] for r in rows)
    return before, after, joins, leaves


def _difference(before: Counter[str], after: Counter[str]) -> str:
    parts = []
    if before - after:
        parts.append(f"{xyz.formula_of(before - after)} more before than after")
    if after - before:
        parts.append(f"{xyz.formula_of(after - before)} more after than before")
    return "; ".join(parts)


def _unbalanced_holes(session: Session, mechanism: _Mechanism) -> list[Hole]:
    holes = []
    for edge in mechanism.edges:
        found = _side_atoms(session, mechanism, edge)
        if found is None:
            continue
        before, after, _, _ = found
        if before == after:
            continue
        source, target = mechanism.unit(edge.source_id), mechanism.unit(edge.target_id)
        assert source is not None and target is not None
        missing = before - after or after - before
        direction = "leaves" if before - after else "joins"
        holes.append(
            Hole(
                "unbalanced",
                hole_id("unbalanced", edge.id),
                [source.ref(), target.ref()],
                f"The edge “{source.label}” → “{target.label}” does not balance: "
                f"{_difference(before, after)}.",
                f"A free species of {xyz.formula_of(missing)} that {direction} on the edge: "
                "the one there is if it fits, else built from SMILES (build_structure, then "
                f"create_node with kind species and {REFINE}); attach it with propose_mechanism "
                "(the edge's species), hole set, and filled naming a new species.",
                difference=_difference(before, after),
                weight=sum(missing.values()),
            )
        )
    return holes


@dataclass
class _Pair:
    a: _Unit
    b: _Unit
    edge: Transition | None  # the edge joining them, or None when the steps' order links them
    why: str


def _step_pairs(session: Session, mechanism: _Mechanism) -> list[_Pair]:
    """Per chain (a branch, with what has none), each two steps next to each other whose
    records nothing joins: the last minimum of the first (one with no edge out, else the
    latest) and the first of the second (one with no edge in, else the earliest)."""
    pairs = []
    for chain in _chains(session, mechanism):
        for (first_step, a_units), (second_step, b_units) in zip(chain, chain[1:], strict=False):
            reach = set().union(*(mechanism.component(u.id) for u in a_units))
            if reach & {u.id for u in b_units}:
                continue
            a = _end_of(mechanism, a_units, last=True)
            b = _end_of(mechanism, b_units, last=False)
            if a is None or b is None:
                continue
            pairs.append(
                _Pair(
                    a,
                    b,
                    None,
                    f"steps “{first_step.name}” and “{second_step.name}” follow "
                    "each other with nothing joining them",
                )
            )
    return pairs


def _end_of(mechanism: _Mechanism, units: list[_Unit], last: bool) -> _Unit | None:
    minima = [u for u in units if not u.ts and u.node is not None and u.node.geometry]
    if not minima:
        return None
    free = [u for u in minima if not (mechanism.outgoing if last else mechanism.incoming)[u.id]]
    chosen = free or minima
    return max(chosen, key=lambda u: u.seq) if last else min(chosen, key=lambda u: u.seq)


def _chains(session: Session, mechanism: _Mechanism) -> list[list[tuple[ReactionStep, list]]]:
    """The steps in order with their units, per branch (units with no branch are in each)."""
    steps = list(session.scalars(select(ReactionStep).order_by(ReactionStep.position)))
    if not steps:
        return []
    branches = sorted({u.branch_id for u in mechanism.units.values() if u.branch_id})
    chains = []
    for branch in branches or [None]:
        chain = []
        for step in steps:
            units = [
                u
                for u in mechanism.units.values()
                if u.step_id == step.id and (branch is None or u.branch_id in (None, branch))
            ]
            if any(not u.ts for u in units):
                chain.append((step, sorted(units, key=lambda u: u.seq)))
        if len(chain) >= 2:
            chains.append(chain)
    return chains


def _edge_pairs(mechanism: _Mechanism) -> list[_Pair]:
    pairs = []
    for edge in mechanism.edges:
        a, b = mechanism.unit(edge.source_id), mechanism.unit(edge.target_id)
        if a is None or b is None or a.ts or b.ts:
            continue
        between = mechanism.neighbours(a.id) & mechanism.neighbours(b.id)
        if any(mechanism.units[u].ts for u in between if u in mechanism.units):
            continue  # a TS joins them too (a TS guess made for this edge, D238)
        pairs.append(_Pair(a, b, edge, "an edge joins them with no TS"))
    return pairs


def _pair_holes(
    session: Session, folder: Path, mechanism: _Mechanism, tolerance: float, notes: list[str]
) -> list[Hole]:
    holes = []
    pairs = _edge_pairs(mechanism) + _step_pairs(session, mechanism)
    if len(pairs) > MAX_PAIRS:
        notes.append(f"{len(pairs)} pairs of linked minima; the first {MAX_PAIRS} were compared.")
        pairs = pairs[:MAX_PAIRS]
    for pair in pairs:
        a = _structure(session, folder, pair.a, tolerance)
        b = _structure(session, folder, pair.b, tolerance)
        if a is None or b is None:
            continue
        joins = leaves = []
        if pair.edge is not None:
            found = _side_atoms(session, mechanism, pair.edge)
            if found is None:
                continue
            before, after, joins, leaves = found
            if before != after:
                continue  # `unbalanced` says so
        involved = [pair.a.ref(), pair.b.ref()]
        ids = sorted([pair.a.id, pair.b.id])
        if joins or leaves:
            a = compare.summed([a, *(compare.Structure("species", r) for r in joins)])
            b = compare.summed([b, *(compare.Structure("species", r) for r in leaves)])
        if not compare.same_atoms(a.rows, b.rows):
            difference = _difference(Counter(r[0] for r in a.rows), Counter(r[0] for r in b.rows))
            holes.append(
                Hole(
                    "missing_intermediate",
                    hole_id("missing_intermediate", *ids),
                    involved,
                    f"“{pair.a.label}” and “{pair.b.label}”: {pair.why}, and they differ in "
                    f"atoms ({difference}).",
                    "The steps between them, with the free species that joins or leaves: look "
                    "for it in the folder first; build it from SMILES only when the reaction "
                    "class says which it is.",
                    difference=difference,
                    weight=99,
                )
            )
            continue
        found = compare.compare(a, b, tolerance=tolerance)
        formed = [r["bond"] for r in found.get("formed", [])]
        broken = [r["bond"] for r in found.get("broken", [])]
        count = len(formed) + len(broken)
        if count == 0:
            continue  # a conformer change or an association by weak contacts
        bonds = {"formed": formed, "broken": broken}
        species = joins or leaves
        if compare.LINK_BONDS[0] <= count <= compare.LINK_BONDS[1]:
            if species:
                fill = (
                    "The TS guess needs the same atoms at both ends: build the complex of the "
                    "free species with the side that lacks it (build_structure), "
                    f"{REFINE}, then " + TS_GUESS_FILL + ". A plain association may have no "
                    "barrier: fill it only when the reaction class expects one."
                )
            elif pair.edge is None:
                fill = (
                    "propose_mechanism with a planned edge between them first (low, hole set: "
                    "the steps' order links them), then " + TS_GUESS_FILL + "."
                )
            else:
                fill = TS_GUESS_FILL + "."
            holes.append(
                Hole(
                    "missing_ts",
                    hole_id("missing_ts", *ids),
                    involved,
                    f"“{pair.a.label}” and “{pair.b.label}”: {pair.why}; {count} bond(s) "
                    "change, one elementary step, with no TS between them.",
                    fill,
                    bonds=bonds,
                    weight=count + (2 if species else 0) + (1 if pair.edge is None else 0),
                )
            )
            continue
        holes.append(
            Hole(
                "missing_intermediate",
                hole_id("missing_intermediate", *ids),
                involved,
                f"“{pair.a.label}” and “{pair.b.label}”: {pair.why}; {count} bonds change, "
                "more than one elementary step (A184: one to three).",
                "An intermediate between them built with the structure tools (build_structure "
                f"and its edits, create_node), {REFINE}, then a TS guess on each side as for "
                "missing_ts; the literature first when the reaction class leaves the "
                "intermediate unclear (D263h).",
                bonds=bonds,
                weight=count,
            )
        )
    return holes


def _cycle_holes(
    session: Session, folder: Path, mechanism: _Mechanism, tolerance: float
) -> list[Hole]:
    holes = []
    for chain in _chains(session, mechanism):
        first = _end_of(mechanism, chain[0][1], last=False)
        last = _end_of(mechanism, chain[-1][1], last=True)
        if first is None or last is None or first.id == last.id:
            continue
        if last.id in mechanism.neighbours(first.id):
            continue  # an edge closes it; `unbalanced` checks its atoms
        rows_first, rows_last = _rows(first), _rows(last)
        if rows_first is None or rows_last is None:
            continue
        if not {r[0] for r in rows_first} & METALS:
            continue  # no catalyst to give back
        involved = [first.ref(), last.ref()]
        ids = sorted([first.id, last.id])
        if compare.same_atoms(rows_first, rows_last):
            same, _, _ = compare._same_molecule(rows_first, rows_last, False)
            if same:
                continue
            a = _structure(session, folder, first, tolerance)
            b = _structure(session, folder, last, tolerance)
            found = compare.compare(a, b, tolerance=tolerance)
            bonds = {
                "formed": [r["bond"] for r in found["formed"]],
                "broken": [r["bond"] for r in found["broken"]],
            }
            holes.append(
                Hole(
                    "cycle",
                    hole_id("cycle", *ids),
                    involved,
                    f"The chain of steps starts at “{first.label}” and ends at “{last.label}”, "
                    "an isomer of it: the catalyst is not given back.",
                    "The steps from the last minimum back to the first, as missing_ts or "
                    "missing_intermediate; flag it when the reaction class does not say them.",
                    bonds=bonds,
                    weight=len(bonds["formed"]) + len(bonds["broken"]),
                )
            )
            continue
        a, b = Counter(r[0] for r in rows_first), Counter(r[0] for r in rows_last)
        difference = _difference(a, b)
        extra = b - a
        fill = (
            f"A release of {xyz.formula_of(extra)} from the last minimum back to the first: "
            "the free species (from SMILES if there is none), an edge last → first with it "
            "leaving, and a TS guess when a bond breaks."
            if extra and not (a - b)
            else "Flag it: the last minimum is not the first one with a species added."
        )
        holes.append(
            Hole(
                "cycle",
                hole_id("cycle", *ids),
                involved,
                f"The chain of steps starts at “{first.label}” and ends at “{last.label}”, "
                f"which differ in atoms ({difference}): the catalyst is not given back.",
                fill,
                difference=difference,
                weight=sum((a - b).values()) + sum(extra.values()),
            )
        )
    return holes


def find(session: Session, folder: Path, since: datetime | None = None) -> dict[str, Any]:
    """D265a: the holes, most useful first, at most MAX_LISTED; nothing is written."""
    tolerance = thermo_settings.settings(session).duplicate_tolerance
    mechanism = _mechanism(session)
    notes: list[str] = []
    holes = (
        _ts_end_holes(session, folder, mechanism, tolerance)
        + _pair_holes(session, folder, mechanism, tolerance, notes)
        + _unbalanced_holes(session, mechanism)
        + _cycle_holes(session, folder, mechanism, tolerance)
    )
    unique = {h.id: h for h in holes}
    ordered = sorted(unique.values(), key=lambda h: (KINDS.index(h.kind), h.weight))
    filled = filled_holes(session, since)
    count = Counter(h.kind for h in ordered)
    return {
        "count": len(ordered),
        "by_kind": {k: count.get(k, 0) for k in KINDS},
        "holes": [h.out() for h in ordered[:MAX_LISTED]],
        "more": max(0, len(ordered) - MAX_LISTED),
        "filled": {"count": len(filled), "limit": MAX_FILLED, "ids": sorted(filled)},
        "notes": notes,
        "summary": (
            f"{len(ordered)} hole(s)"
            + (f", the {MAX_LISTED} most useful listed" if len(ordered) > MAX_LISTED else "")
            + f"; {len(filled)} of at most {MAX_FILLED} filled in this run (D265d)."
            if ordered
            else f"No holes found; {len(filled)} of at most {MAX_FILLED} filled in this run."
        ),
        "key": (
            "Holes of D265a, most useful first: ts_end, missing_mirror_image (D267: a TS's "
            "minimum is the mirror image of a node, made exactly by build_structure's mirror "
            "step), missing_ts, unbalanced, missing_intermediate, cycle. bonds: formed and "
            "broken by A181's rule (weak and metal–hydrogen contacts left out; a TS against "
            "an end counts its partial bonds), "
            "or along_mode: the pairs the TS's imaginary mode moves. Only what edges and the "
            "steps' order link is looked at; no branch is invented. Fill at most 10 a run "
            "(cheapest and most certain first), each with MACE guesses only, through "
            "propose_mechanism with the hole id (hole on its edges, filled for its nodes); "
            "flag the rest."
        ),
    }


# ---------- marking a filled hole and the bound (D265c, d) ----------


def _after(stamp: datetime | None, since: datetime | None) -> bool:
    return since is None or stamp is None or stamp >= since


def filled_holes(session: Session, since: datetime | None) -> set[str]:
    """The holes filled (since `since`, a naive UTC time; None: ever): ids named in the
    evidence of edges that fill one and in the notes of nodes marked MACE guess."""
    found: set[str] = set()
    for edge in session.scalars(select(Transition).where(Transition.evidence != "")):
        if _FILLED_EDGE.match(edge.evidence or "") and _after(edge.created_at, since):
            found.update(_HOLE_ID.findall(edge.evidence.split("\n", 1)[0]))
    for node in session.scalars(select(Node)):
        if MACE_GUESS in (node.tags or []) and _after(node.created_at, since):
            for line in (node.notes or "").splitlines():
                if line.startswith(FILLED):
                    found.update(_HOLE_ID.findall(line))
    return found


def check_bound(session: Session, new: set[str], since: datetime | None) -> None:
    """D265d: at most MAX_FILLED holes filled in a run."""
    done = filled_holes(session, since)
    total = done | new
    if len(total) > MAX_FILLED:
        raise RecordError(
            f"At most {MAX_FILLED} gaps are filled in one run (D265d): {len(done)} are filled "
            "already"
            + (" since Work on its own was turned on" if since else "")
            + f" and this call adds {len(total - done)}. Flag the rest in the summary instead."
        )


def is_filled_evidence(evidence: str) -> bool:
    return bool(_FILLED_EDGE.match(evidence or ""))


def filled_evidence(hole: str, evidence: str) -> str:
    """A filling edge's evidence: “Filled by Claude (hole …): …” (A184)."""
    rest = _FILLED_EDGE.sub("", evidence, count=1).strip()
    rest = re.sub(r"^\(hole [^)]*\)", "", rest).lstrip(" :;,.—-").strip()
    return f"{FILLED} (hole {hole})" + (f": {rest}" if rest else "")


def _higher_than_mace(node: Node) -> str | None:
    for calc in [*node.calculations, *node.irc_calculations]:
        for level in (calc.level, calc.geometry_level):
            if level is None:
                continue
            if levels.method_rank(levels.fields_of(level)) > levels.MACHINE_LEARNED:
                return levels.label(level) or level.method
    return None


def mark_filled(session: Session, hole: str, node_id: str, what: str = "") -> Node:
    """D265c: a node built to fill a hole: planned, “(MACE guess)” after its label and the tag
    MACE guess, the hole in its notes; refused when it holds a result above the MACE level, so
    its energy never shares a profile with DFT numbers (EN-3 keeps levels apart)."""
    node = session.get(Node, node_id)
    if node is None:
        raise RecordError(f"No node has the id {node_id}")
    name = node.label or "Untitled node"
    higher = _higher_than_mace(node)
    if higher is not None:
        raise RecordError(
            f"“{name}” holds a result at {higher}: a filled gap holds MACE guesses only "
            "(D265c). Fill it with a node of its own."
        )
    changes: dict[str, Any] = {"status": Status.PLANNED.value}
    if MACE_GUESS.lower() not in name.lower():
        changes["label"] = f"{name} ({MACE_GUESS})"[:200]
    if MACE_GUESS not in (node.tags or []):
        changes["tags"] = [*(node.tags or []), MACE_GUESS]
    line = f"{FILLED} (hole {hole})" + (f": {what.strip()}" if what.strip() else ".")
    if line not in (node.notes or ""):
        changes["notes"] = f"{node.notes}\n{line}".strip() if node.notes else line
    try:
        return node_service.update(session, node.id, changes)
    except node_service.NodeError as exc:
        raise RecordError(str(exc)) from exc
