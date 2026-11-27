"""The minima a transition state links, searched from the TS (D263c, A184, A186, FR-MCP-25).

`structure_compare.compare` with a TS checks two minima Claude names; with a hundred minima
Claude cannot guess the pair. `find` starts from the TS instead: its imaginary mode's active
pairs (`structure_compare._active`) say which bonds form and break on each side, and every
candidate structure (by default each true minimum and free species of the open investigation,
`left_out` dropping TSs, scan or constrained geometries and imaginary frequencies, and each sum
of one of them with a free species, for a step where a species joins or leaves) is tested,
whatever its atom order:

- the same atoms as the TS (or, for a sum, as the TS's atoms together), and charge and
  multiplicity where both are known;
- its bonds the TS's but for the active pairs (A181's rule, metal–hydrogen contacts weak,
  `structure_compare.split`): ruled out first by the bond counts (`_could_be_same` with the
  active pairs free), then tried in the same numbering, by position and through Match atoms
  (`_mapping`; a candidate numbered as the TS whose bonds differ elsewhere is an isomer, and
  no other numbering is tried for it, which keeps ~150 candidates of 80 atoms near a second);
- the side of the mode it lies on (`structure_compare._side`); one whose active distances
  change both ways is kept on the side most of the motion points to and marked `mixed`.

Each side's candidates are those holding a geometry at the TS's own geometry level (compared at
that geometry), other levels only when a side has none there (`other_level`), each ranked by the
atom-matched RMSD to the TS (for a sum, over the larger species' atoms); energies are never
read. The best pairs are then checked by A184's bond check (`structure_compare.compare` with the
TS), and the first that links is given, else the best pair's verdict. Nothing is written.

D267: a mirror image is another molecule. A candidate whose numbering turns a stereocentre off
the mode into its mirror image (`atom_matching.stereo_flips`) is never on a side; when its
mirror image fits instead it is listed apart (`mirror_images`), and a side whose best fit it is
says `mirror_of` (the matching minimum is not in the investigation; `find_gaps` lists it).

A187: Match atoms stops at the first starting orientation whose match keeps the bonds off the
mode, the bond check maps each pair through the TS (the mappings that placed each candidate on
it), and several TSs are searched in one call (`all_ts`, `unlinked`, `brief`, `batch`), the
candidates made once per geometry level by the route.
"""

import time
from collections import Counter
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import numpy as np
from sqlalchemy import select
from sqlalchemy.orm import Session

from chembook3d.models import Calculation, CalculationType, GroupNode, Node, NodeKind, Role
from chembook3d.services import atom_matching, geometry, levels
from chembook3d.services import nodes as node_service
from chembook3d.services import structure_compare as compare
from chembook3d.services.geometry import Rows

TOP = 3  # A186: candidates listed per side
PAIR_TRIES = 3  # A186: the best this many per side are paired for the bond check (at most 3 × 3)
MAX_TOP = 10


@dataclass
class Candidate:
    """A structure the TS may link, with how the answer names it (`label`, `ref` to pass to
    compare_structures, `node_id`, `group`, `level` …) and the geometry levels it holds (ids;
    None: not known, as for XYZ text or a node without calculations)."""

    structure: compare.Structure
    entry: dict[str, Any]
    levels: frozenset[str] | None = None
    _counts: Any = None

    def counts(self) -> Any:
        """Its bond counts, made once for every TS searched (A187)."""
        if self._counts is None:
            self._counts = compare._bond_counts(self.structure.rows)
        return self._counts


@dataclass
class Level:
    """The TS's geometry level (A186: candidates at it come first)."""

    id: str
    label: str


def ts_level(session: Session | None, ref: dict[str, Any]) -> Level | None:
    """The geometry level of a TS given as a node (its geometry's) or a calculation (its own
    when it is a free optimization or frequency job, else its node's)."""
    if session is None:
        return None
    found = None
    if ref.get("node_id"):
        node = session.get(Node, str(ref["node_id"]))
        found = levels.node_geometry_level(node) if node is not None else None
    elif ref.get("calculation_id"):
        calc = session.get(Calculation, str(ref["calculation_id"]))
        if calc is not None:
            own = calc.level is not None and not calc.constraints
            free = own and (
                levels.is_geometry_source(calc) or calc.type == CalculationType.FREQUENCY
            )
            found = calc.level if free else levels.node_geometry_level(calc.node)
    if found is None:
        return None
    return Level(found.id, levels.label(found) or "its level")


def _latest_frequency(node: Node, tolerance: float) -> Calculation | None:
    jobs = [
        c
        for c in node.calculations
        if c.type == CalculationType.FREQUENCY
        and c.result is not None
        and c.result.frequencies
        and geometry.matches(c.geometry, node.geometry, tolerance)
    ]
    return max(jobs, key=lambda c: c.created_at) if jobs else None


def left_out(node: Node, tolerance: float) -> str | None:
    """Why a node is no minimum to search by default (A186), or None: a TS; a scan path; a
    geometry from a scan or constrained optimization (W-CONSTRAINED) that no free one ended on;
    an imaginary frequency in its latest frequency job at its geometry; without a role, neither
    a converged free optimization nor a frequency job at its geometry."""
    if node.kind != NodeKind.SPECIES and node.role == Role.TRANSITION_STATE:
        return "transition state"
    if node.path_edge_id is not None:
        return "scan path"
    free = levels.geometry_sources(node)
    held = [c for c in node.calculations if c.constraints and c.geometry]
    if held and not any(geometry.matches(c.geometry, node.geometry, tolerance) for c in free):
        return "scan or constrained geometry"
    frequency = _latest_frequency(node, tolerance)
    if frequency is not None and (frequency.result.imaginary_count or 0) > 0:
        return "imaginary frequency"
    if node.kind == NodeKind.SPECIES or node.role == Role.MINIMUM:
        return None
    converged = any(
        c.result is not None and c.result.optimization_converged is True
        for c in levels.optimizations(node)
    )
    if not converged and frequency is None:
        return "not shown to be a minimum"
    return None


def node_candidate(
    session: Session,
    folder: Path,
    node: Node,
    tolerance: float,
    level: Level | None,
    groups: dict[str, str] | None = None,
) -> Candidate:
    """A node as a candidate: at the TS's geometry level when it holds a geometry there
    (`levels.geometry_at_level`), else its own geometry."""
    structure = compare.resolve(session, folder, {"node_id": node.id}, tolerance)
    held = {c.level_id: c.level for c in levels.geometry_sources(node) if c.level is not None}
    every = held or held_levels(node)
    entry: dict[str, Any] = {
        "label": structure.label,
        "node_id": node.id,
        "ref": {"node_id": node.id},
    }
    shown = levels.node_geometry_level(node, tolerance)
    if level is not None and level.id in held:
        rows = levels.geometry_at_level(node, level.id, tolerance)
        if rows and rows is not node.geometry and len(rows) == len(structure.rows):
            structure.rows = [[r[0], float(r[1]), float(r[2]), float(r[3])] for r in rows]
            structure.modes = structure.mode_rows = None
        shown = held[level.id]
    if shown is not None:
        entry["level"] = levels.label(shown)
    if node.kind == NodeKind.SPECIES:
        entry["kind"] = "species"
    if groups and node.group_id in groups:
        entry["group"] = groups[node.group_id]
    return Candidate(structure, entry, frozenset(every) or None)


def held_levels(node: Node) -> dict[str, Any]:
    """The levels a node's structure is at: its geometry sources' (D100), else (A194) those of
    any calculation holding a geometry, as a conformer search's member is at the search's
    level, so at another level than a DFT TS's. Empty when none is known."""
    held = {c.level_id: c.level for c in levels.geometry_sources(node) if c.level is not None}
    return held or {
        c.level_id: c.level for c in node.calculations if c.level is not None and c.geometry
    }


def from_investigation(
    session: Session,
    folder: Path,
    tolerance: float,
    exclude: str | None = None,
    level: Level | None = None,
) -> tuple[list[Candidate], list[Candidate], dict[str, int]]:
    """The true minima (A186: every node with coordinates `left_out` keeps) and the free
    species; the free species alone, for sums; and how many nodes were left out, by why."""
    groups = {g.id: g.label or "Untitled group" for g in session.scalars(select(GroupNode))}
    candidates, species = [], []
    skipped: Counter = Counter()
    for node in node_service.list_nodes(session, with_calculations=True):
        if not node.geometry or node.id == exclude:
            continue
        why = left_out(node, tolerance)
        if why is not None:
            skipped[why] += 1
            continue
        candidate = node_candidate(session, folder, node, tolerance, level, groups)
        candidates.append(candidate)
        if node.kind == NodeKind.SPECIES:
            species.append(candidate)
    return candidates, species, dict(skipped)


def _sum(main: Candidate, extra: Candidate) -> Candidate:
    structure = compare.summed([main.structure, extra.structure])
    entry: dict[str, Any] = {
        "label": structure.label,
        "with_species": extra.entry["label"],
    }
    if "ref" in main.entry and "ref" in extra.entry:
        entry["ref"] = [main.entry["ref"], extra.entry["ref"]]
    for key in ("node_id", "group"):
        if key in main.entry:
            entry[key] = main.entry[key]
    if "node_id" in extra.entry:
        entry["species_node_id"] = extra.entry["node_id"]
    if "level" in main.entry:
        entry["level"] = main.entry["level"]
    return Candidate(structure, entry, main.levels)


def _tried(
    atoms: Counter, candidates: list[Candidate], species: list[Candidate]
) -> list[Candidate]:
    """The candidates with the TS's atoms, and the sums of a candidate or free species and a
    free species that have them."""
    out = []
    seen: set[frozenset[int]] = set()
    known = {id(c) for c in candidates}
    for candidate in candidates + [s for s in species if id(s) not in known]:
        have = Counter(r[0] for r in candidate.structure.rows)
        if have == atoms:
            out.append(candidate)
            continue
        if not have or have - atoms:  # more of some element than the TS: no sum helps
            continue
        for extra in species:
            pair = frozenset((id(candidate), id(extra)))
            if pair in seen:  # a species with itself is two molecules of it
                continue
            if have + Counter(r[0] for r in extra.structure.rows) != atoms:
                continue
            seen.add(pair)
            # The larger part first: its RMSD to the TS is the one that says something.
            if len(extra.structure.rows) > len(candidate.structure.rows):
                out.append(_sum(extra, candidate))
            else:
                out.append(_sum(candidate, extra))
    return out


def _compatible(structure: compare.Structure, ts: compare.Structure) -> bool:
    return all(
        a is None or b is None or a == b
        for a, b in ((structure.charge, ts.charge), (structure.multiplicity, ts.multiplicity))
    )


def _off_mode(ts_rows: Rows, rows: Rows, mapping: list[int], active: set) -> tuple[int, int]:
    """Real bond changes (A181, metal–hydrogen contacts weak) under the mapping: in all, and
    those not on the mode's active pairs."""
    formed, broken = atom_matching.changes(ts_rows, rows, mapping)
    real_formed, real_broken, _, _ = compare.split(ts_rows, rows, mapping, formed, broken)
    real = real_formed + real_broken
    return len(real), sum(p not in active for p in real)


@dataclass
class Found:
    """A candidate on one side of the mode: its sort key, the answer's entry and its atom for
    each of the TS's."""

    key: tuple
    candidate: Candidate
    entry: dict[str, Any]
    mapping: list[int]


def _mapping(ts_rows: Rows, rows: Rows, active: set) -> list[int] | None:
    """The candidate's atom for each of the TS's under which only active pairs change bonds:
    the same numbering, the numbering by position, then Match atoms (ended at the first match
    that keeps the bonds off the mode, A187). A candidate in the TS's numbering whose bonds
    differ off the mode by no more than a step changes (A186) is another isomer numbered as
    the TS, so no other numbering is tried for it."""
    if geometry.elements(ts_rows) == geometry.elements(rows):
        identity = list(range(len(rows)))
        real, off = _off_mode(ts_rows, rows, identity, active)
        if not off:
            return identity
        if real <= atom_matching.MAX_BOND_CHANGES:
            return None
    return _matched(ts_rows, rows, active)


def _matched(ts_rows: Rows, rows: Rows, active: set) -> list[int] | None:
    by_position = compare._by_position(ts_rows, rows)[1]
    if not _off_mode(ts_rows, rows, by_position, active)[1]:
        return by_position

    def enough(mapping: list[int]) -> bool:
        return not _off_mode(ts_rows, rows, mapping, active)[1]

    try:
        found = atom_matching.match(ts_rows, rows, enough=enough)
    except Exception:  # noqa: BLE001 - a match that cannot be made places nothing
        return None
    return found.mapping if enough(found.mapping) else None


def _placing(
    ts_rows: Rows, structure: compare.Structure, active: set, skip: set[int]
) -> tuple[compare.Structure, list[int] | None, bool] | None:
    """D267: the candidate as it lies on the TS, its atom for each of the TS's, and whether
    only its mirror image fits: its own structure when the numbering that keeps the bonds off
    the mode turns no stereocentre off the mode into its mirror image (`stereo_flips`); else
    its mirror image (`atom_matching.mirrored`) when that fits so; else no atoms (another
    stereoisomer). None when no numbering keeps the bonds off the mode. A numbering that keeps
    the bonds cannot put a stereocentre right (its neighbours all differ), so no other is
    tried."""
    mapping = _mapping(ts_rows, structure.rows, active)
    if mapping is None:
        return None
    if not atom_matching.stereo_flips(ts_rows, structure.rows, mapping, skip):
        return structure, mapping, False
    image = replace(
        structure, rows=atom_matching.mirrored(structure.rows), modes=None, mode_rows=None
    )
    reflected = _mapping(ts_rows, image.rows, active)
    if reflected is None or atom_matching.stereo_flips(ts_rows, image.rows, reflected, skip):
        return structure, None, False
    return image, reflected, True


def _side_of(mode: Any, ts_rows: Rows, placed: Rows, apart: np.ndarray | None) -> tuple[str, bool]:
    """The side of the mode the candidate lies on, and whether its active distances change
    both ways (then the side most of the motion, weighted by the mode's shares, points to)."""
    side, _ = compare._side(mode, ts_rows, placed, apart)
    if side in ("+", "−", "none"):
        return side, False
    d_ts, d_other = compare._distances(ts_rows), compare._distances(placed)
    vote = 0.0
    for i, j, share in mode.active:
        separate = apart is not None and bool(apart[i, j])
        change = 1.0 if separate else float(d_other[i, j] - d_ts[i, j])
        if abs(change) >= compare.MOVED:
            vote += np.sign(change) * np.sign(share) * abs(share) * min(abs(change), 1.0)
    return ("+" if vote > 0 else "−"), True


def _predicted(rows: Rows, mode: Any) -> dict[str, dict[str, list[str]]]:
    """Per side, the active pairs that shorten (bonds forming, or tightening when bonded in
    the TS by the 3D view's rule) and lengthen (breaking, or moving apart when beyond A181's
    outer reach already)."""
    strict, loose = compare._adjacency(rows)
    out: dict[str, dict[str, list[str]]] = {}
    for side, sign in (("+", 1), ("−", -1)):
        bonds: dict[str, list[str]] = {"forms": [], "tightens": [], "breaks": [], "parts": []}
        for i, j, share in mode.active:
            name = compare._bond(rows, i, j)
            if share * sign > 0:
                bonds["breaks" if loose[i, j] else "parts"].append(name)
            else:
                bonds["tightens" if strict[i, j] else "forms"].append(name)
        out[side] = {k: v for k, v in bonds.items() if v}
    return out


def find(
    ts: compare.Structure,
    candidates: list[Candidate],
    species: list[Candidate],
    tolerance: float,
    top: int = TOP,
    level: Level | None = None,
    skipped: dict[str, int] | None = None,
) -> dict[str, Any]:
    """D263c, A184, A186: the structures on each side of the TS's imaginary mode, those at the
    TS's geometry `level` first (others only when a side has none there, marked `other_level`),
    nearest first, and A184's bond check of the best pair. `skipped`: the nodes left out of the
    default candidates, by why. See the module's docstring."""
    started = time.perf_counter()
    mode = compare._active(ts)
    if mode is None:
        raise compare.StructureError(
            f"“{ts.label}” has no imaginary mode to search along"
            + ("" if ts.frequencies else " (no frequency job at its geometry)")
        )
    top = max(1, min(int(top), MAX_TOP))
    rows = ts.rows
    elements = geometry.elements(rows)
    active = {(i, j) for i, j, _ in mode.active}
    strict, loose = compare._adjacency(rows)
    for i, j in active:
        strict[i, j] = strict[j, i] = False
        loose[i, j] = loose[j, i] = True
    free = compare._counts(elements, strict, loose)  # the TS's bonds, the active pairs free

    tried = _tried(Counter(elements), candidates, species)
    found: dict[str, list[Found]] = {"+": [], "−": []}
    mirrors: dict[str, list[Found]] = {"+": [], "−": []}  # D267: fit only as mirror images
    skip = {k for pair in active for k in pair}
    matched = stereoisomers = 0
    for candidate in tried:
        structure = candidate.structure
        if not _compatible(structure, ts):
            continue
        if not compare._could_be_same(free, candidate.counts(), False):
            continue
        matched += 1
        placing = _placing(rows, structure, active, skip)
        if placing is None:
            continue
        structure, mapping, mirror = placing
        if mapping is None:
            stereoisomers += 1  # D267: another stereoisomer, never a partner
            continue
        placed = [structure.rows[j] for j in mapping]
        apart = compare._apart(structure, mapping)
        side, mixed = _side_of(mode, rows, placed, apart)
        if side == "none":
            continue  # where the TS is: no minimum on either side
        main = [
            i
            for i in range(len(rows))
            if structure.species is None or structure.species[mapping[i]] == 0
        ]
        rmsd = geometry.aligned_rmsd([rows[i] for i in main], [placed[i] for i in main])
        entry = dict(candidate.entry)
        entry["rmsd"] = None if rmsd is None else round(rmsd, 3)
        if any(i != j for i, j in enumerate(mapping)):
            entry["renumbered"] = True
        if structure.imaginary:
            entry["imaginary"] = [round(f, 1) for f in structure.imaginary]
        if mixed:
            entry["mixed"] = True
        other = (
            level is not None and candidate.levels is not None and level.id not in candidate.levels
        )
        if other:
            entry["other_level"] = True
        key = (other, mixed, bool(structure.imaginary), rmsd if rmsd is not None else np.inf)
        if mirror:
            entry["mirror_image"] = True
            mirrors[side].append(Found(key, candidate, entry, mapping))
            continue
        found[side].append(Found(key, candidate, entry, mapping))
    predicted = _predicted(rows, mode)
    sides = []
    for side in ("−", "+"):
        found[side].sort(key=lambda f: f.key)
        same = [f for f in found[side] if not f.key[0]]
        listed = same or found[side]  # other levels only when none is at the TS's (A186)
        shown: dict[str, Any] = {
            "side": side,
            "bonds": predicted[side],
            "found": len(listed),
            "candidates": [f.entry for f in listed[:top]],
        }
        if same and len(same) < len(found[side]):
            shown["at_other_levels"] = len(found[side]) - len(same)
        if listed and not same:
            shown["other_level"] = True
        if mirrors[side]:
            mirrors[side].sort(key=lambda f: f.key)
            shown["mirror_images"] = [f.entry for f in mirrors[side][:top]]
            best = mirrors[side][0].entry
            nearest = listed[0].entry.get("rmsd") if listed else None
            if nearest is None or (best["rmsd"] is not None and best["rmsd"] < nearest):
                # D267: a minimum fits this side well only as its mirror image
                shown["mirror_of"] = {
                    k: best[k] for k in ("label", "node_id", "rmsd", "group") if k in best
                }
        found[side] = listed
        sides.append(shown)

    check, tries = _check(ts, found, tolerance)
    seconds = round(time.perf_counter() - started, 2)
    out: dict[str, Any] = {
        "ts": ts.describe(),
        "mode": {
            "wavenumber": round(mode.wavenumber, 1),
            "active": [
                {"bond": compare._bond(rows, i, j), "share": round(s, 2)} for i, j, s in mode.active
            ],
        },
        "sides": sides,
        "check": check,
        "pairs_checked": tries,
        "searched": {
            "structures": len(candidates),
            "with_the_atoms": len(tried),
            "bond_counts_fit": matched,
            **({"other_stereoisomers": stereoisomers} if stereoisomers else {}),
        },
        "seconds": seconds,
    }
    if level is not None:
        out["level"] = level.label
    if skipped:
        out["searched"]["left_out"] = skipped
    if len(ts.imaginary) > 1:
        out["notes"] = [
            f"“{ts.label}” has {len(ts.imaginary)} imaginary frequencies; the lowest is used."
        ]
    out["summary"], out["next"] = _words(ts, sides, check, level)
    return out


def _through_ts(a: Found, b: Found) -> tuple[list[int], list[int]]:
    """B's and the TS's atom for each of A's, from the mappings each was placed on the TS by."""
    ts_of_a = [0] * len(a.mapping)
    for i, k in enumerate(a.mapping):
        ts_of_a[k] = i
    return [b.mapping[i] for i in ts_of_a], ts_of_a


def _check(
    ts: compare.Structure,
    found: dict[str, list[Found]],
    tolerance: float,
) -> tuple[dict[str, Any] | None, int]:
    """A184's bond check of the best pairs, the nearest first: the first that links, else the
    best pair's verdict. Each pair is matched through the TS (A187), not by Match atoms again."""
    minus, plus = found["−"][:PAIR_TRIES], found["+"][:PAIR_TRIES]
    pairs = sorted(  # at the TS's level first, then nearest
        ((i, j) for i in range(len(minus)) for j in range(len(plus))),
        key=lambda p: (minus[p[0]].key[0] + plus[p[1]].key[0], p[0] + p[1], p[0]),
    )
    first = None
    for count, (i, j) in enumerate(pairs, start=1):
        a, b = minus[i], plus[j]
        result = compare.compare(
            a.candidate.structure,
            b.candidate.structure,
            ts,
            False,
            tolerance,
            mappings=_through_ts(a, b),
        )
        ts_check = result.get("ts_check") or {}
        check = {
            "a": _named(a.entry),
            "b": _named(b.entry),
            "verdict": ts_check.get("verdict", "unclear"),
            "reasons": ts_check.get("reasons", result.get("notes", [])),
            "changed": ts_check.get("changed", []),
        }
        if check["verdict"] == "links":
            if not (a.entry.get("other_level") or b.entry.get("other_level")):
                return check, count
            # A link only through a structure at another level is not a bond check at the
            # TS's level: it stays unclear (validation on a real folder: such links were the
            # wrong ones, e.g. an xTB conformer search's structures).
            check["verdict"] = "unclear"
            check["reasons"] = [
                "It would link, but through a structure at another geometry level than the "
                "TS's, so the bond check does not settle it.",
                *check["reasons"],
            ]
            if first is None:
                first = check
            continue
        if first is None:
            first = check
    return first, len(pairs)


def _named(entry: dict[str, Any]) -> dict[str, Any]:
    keys = ("label", "node_id", "species_node_id", "ref", "rmsd", "level", "other_level")
    return {k: entry[k] for k in keys if k in entry}


def _words(
    ts: compare.Structure,
    sides: list[dict[str, Any]],
    check: dict[str, Any] | None,
    level: Level | None = None,
) -> tuple[str, str]:
    summary, next_ = _said(ts, sides, check)
    mirrored = [s for s in sides if s.get("mirror_of")]
    if mirrored and not (check and check["verdict"] == "links"):
        summary += " " + " ".join(
            f"On the {s['side']} side: {mirror_text(s['mirror_of'])}." for s in mirrored
        )
        next_ = (
            "The missing minimum is the mirror image of an existing node: a gap (find_gaps "
            "lists it as missing_mirror_image, with how to make it)."
        )
    fallen = [s["side"] for s in sides if s.get("other_level")]
    if fallen and level is not None:
        summary += (
            f" The {' and '.join(fallen)} side has no candidate at the TS's geometry level "
            f"({level.label}): those listed are at other levels (`other_level`), so a link "
            "through them is weaker."
        )
    return summary, next_


def mirror_text(found: dict[str, Any]) -> str:
    """D267's words for a side whose minimum fits only reflected."""
    rmsd = f" (RMSD {found['rmsd']} Å reflected)" if found.get("rmsd") is not None else ""
    return (
        f"mirror image of “{found['label']}”{rmsd}: the matching minimum is not in the "
        "investigation (a mirror image is another molecule, D267)"
    )


def _said(
    ts: compare.Structure, sides: list[dict[str, Any]], check: dict[str, Any] | None
) -> tuple[str, str]:
    follow = "follow the mode (start_local_follow_mode, then get_follow_mode_ends)"
    empty = [s["side"] for s in sides if not s["found"]]
    if len(empty) == 2:
        return (
            f"No structure lies on either side of “{ts.label}”'s mode with the bonds it "
            "predicts (`bonds`).",
            f"Nothing to link yet: {follow}; an end that matches nothing is a gap (find_gaps).",
        )
    if empty:
        (side,) = empty
        other = next(s for s in sides if s["found"])
        best = other["candidates"][0]
        return (
            f"Nothing lies on the {side} side of “{ts.label}”'s mode; on the {other['side']} "
            f"side the nearest is “{best['label']}” (RMSD {best['rmsd']} Å).",
            f"{follow.capitalize()} for the {side} side; if its end matches nothing, the "
            "structure is missing (find_gaps).",
        )
    assert check is not None
    pair = (
        f"“{check['a']['label']}” (− side, RMSD {check['a'].get('rmsd')} Å) and "
        f"“{check['b']['label']}” (+ side, RMSD {check['b'].get('rmsd')} Å)"
    )
    several = [s["side"] for s in sides if s["found"] > 1]
    if check["verdict"] == "links":
        more = (
            f" The {' and '.join(several)} side(s) have other candidates (listed); the "
            "nearest that links is given."
            if several
            else ""
        )
        return (
            f"“{ts.label}” links {pair}: A184's bond check says links.{more}",
            "Propose the edges at medium confidence (A184 bond check), naming both RMSDs; a "
            "mode-follow whose ends each match these structures would make them high.",
        )
    return (
        f"The nearest pair, {pair}, {check['verdict']}: " + " ".join(check["reasons"]),
        f"Not settled: {follow}.",
    )


# ---------- several TSs in one call (A187) ----------

MAX_ANSWER = 20_000  # A187: characters of a batch answer; the TSs beyond wait for `offset`
WHY_LENGTH = 160  # characters of the reason kept per TS in a batch


def unlinked(session: Session) -> list[Node]:
    """The TS nodes with coordinates missing an edge on either side (an edge to their group
    counts), in canvas order (A187)."""
    from chembook3d.services import gaps  # gaps imports follow_mode, which is heavier

    mechanism = gaps._mechanism(session)
    out = []
    for node in all_ts(session):
        unit = mechanism.of_node.get(node.id, node.id)
        if not (mechanism.incoming[unit] and mechanism.outgoing[unit]):
            out.append(node)
    return out


def all_ts(session: Session) -> list[Node]:
    """Every TS node with coordinates, in canvas order."""
    return [
        n
        for n in node_service.list_nodes(session)
        if n.kind == NodeKind.NODE and n.role == Role.TRANSITION_STATE and n.geometry
    ]


def _best(side: dict[str, Any], checked: dict[str, Any] | None) -> dict[str, Any] | None:
    """The side's candidate in the bond check (the pair that links, else the nearest pair),
    else its nearest."""
    if not side["candidates"]:
        return None
    best = checked or side["candidates"][0]
    keys = ("label", "node_id", "species_node_id", "rmsd", "other_level")
    out = {k: best[k] for k in keys if k in best}
    out["found"] = side["found"]
    return out


def _clip(text: str) -> str:
    return text if len(text) <= WHY_LENGTH else text[: WHY_LENGTH - 1].rstrip() + "…"


def brief(found: dict[str, Any]) -> dict[str, Any]:
    """One TS's answer in a few lines (A187): the mode's active bonds, per side the candidate
    of the bond check (else the nearest) and how many were found, and the verdict: links,
    unclear or does not link (A184's bond check of the pairs tried), mirror image (D267: a
    side's best fit is a node only reflected, and nothing links), one side (a side has no
    candidate) or no side."""
    sides = {s["side"]: s for s in found["sides"]}
    block: dict[str, Any] = {
        "ts": found["ts"]["label"],
        "mode": ", ".join(a["bond"] for a in found["mode"]["active"]),
    }
    empty = [s for s in ("−", "+") if not sides[s]["found"]]
    check = found.get("check")
    mirrored = [s for s in ("−", "+") if sides[s].get("mirror_of")]
    if mirrored and not (check and check["verdict"] == "links"):
        block["verdict"] = "mirror image"  # D267
        block["why"] = _clip(
            "; ".join(f"{s} side: {mirror_text(sides[s]['mirror_of'])}" for s in mirrored)
        )
    elif len(empty) == 2:
        block["verdict"] = "no side"
    elif empty:
        block["verdict"] = "one side"
        block["why"] = f"nothing on the {empty[0]} side"
    else:
        assert check is not None
        block["verdict"] = check["verdict"]
        if check["verdict"] != "links" and check.get("reasons"):
            block["why"] = _clip(str(check["reasons"][0]))
    for side, checked in (("−", "a"), ("+", "b")):
        best = _best(sides[side], check[checked] if check is not None else None)
        if best is not None:
            block[side] = best
        if side in mirrored and block["verdict"] == "mirror image":
            block[side] = {**(best or {}), "mirror_of": sides[side]["mirror_of"]}
    return block


def batch(blocks: list[dict[str, Any]], total: int, offset: int, seconds: float) -> dict[str, Any]:
    """The answer for several TSs: their blocks, counts by verdict, and `more` when some were
    left for the next call (A187)."""
    counts = Counter(b["verdict"] for b in blocks)
    out: dict[str, Any] = {
        "tss": blocks,
        "counts": dict(counts),
        "shown": {"from": offset + 1 if blocks else offset, "to": offset + len(blocks)},
        "of": total,
        "seconds": round(seconds, 1),
    }
    left = total - offset - len(blocks)
    if left > 0:
        out["more"] = {
            "left": left,
            "offset": offset + len(blocks),
            "hint": f"{left} more TSs: call again with offset {offset + len(blocks)}.",
        }
    out["summary"] = (
        f"{len(blocks)} of {total} TSs searched: "
        + ", ".join(f"{n} {v}" for v, n in counts.most_common())
        + "."
    )
    out["next"] = (
        "Propose each `links` pair at medium (A184 bond check, with both RMSDs); follow the "
        "mode (start_local_follow_mode) only for the TSs still unsettled; a `mirror image` "
        "TS needs a minimum the investigation lacks, the mirror image of `mirror_of` (D267; "
        "find_gaps lists it); call with `ts` for one TS's full answer (bonds per side, more "
        "candidates, reasons)."
    )
    return out
