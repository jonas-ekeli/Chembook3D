"""The minima a transition state links, searched from the TS (D263c, A184, A186, FR-MCP-25).

`structure_compare.compare` with a TS checks two minima Claude names; with a hundred minima
Claude cannot guess the pair. `find` starts from the TS instead: its imaginary mode's active
pairs (`structure_compare._active`) say which bonds form and break on each side, and every
candidate structure (by default each minimum node and free species of the open investigation,
and each sum of one of them with a free species, for a step where a species joins or leaves) is
tested, whatever its atom order:

- the same atoms as the TS (or, for a sum, as the TS's atoms together), and charge and
  multiplicity where both are known;
- its bonds the TS's but for the active pairs (A181's rule, metal–hydrogen contacts weak,
  `structure_compare.split`): ruled out first by the bond counts (`_could_be_same` with the
  active pairs free), then tried in the same numbering, by position and through Match atoms
  (`_mapping`; a candidate numbered as the TS whose bonds differ elsewhere is an isomer, and
  no other numbering is tried for it, which keeps ~150 candidates of 80 atoms near a second);
- the side of the mode it lies on (`structure_compare._side`); one whose active distances
  change both ways is kept on the side most of the motion points to and marked `mixed`.

Each side's candidates are ranked by the atom-matched RMSD to the TS (for a sum, over the
larger species' atoms); energies are never read. The best pairs are then checked by A184's
bond check (`structure_compare.compare` with the TS), and the first that links is given, else
the best pair's verdict. Nothing is written.
"""

import time
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
from sqlalchemy import select
from sqlalchemy.orm import Session

from chembook3d.models import GroupNode, NodeKind, Role
from chembook3d.services import atom_matching, geometry
from chembook3d.services import nodes as node_service
from chembook3d.services import structure_compare as compare
from chembook3d.services.geometry import Rows

TOP = 3  # A186: candidates listed per side
PAIR_TRIES = 3  # A186: the best this many per side are paired for the bond check (at most 3 × 3)
MAX_TOP = 10


@dataclass
class Candidate:
    """A structure the TS may link, with how the answer names it (`label`, `ref` to pass to
    compare_structures, `node_id`, `group` …)."""

    structure: compare.Structure
    entry: dict[str, Any]


def from_investigation(
    session: Session, folder: Path, tolerance: float, exclude: str | None = None
) -> tuple[list[Candidate], list[Candidate]]:
    """Every node with coordinates that is not a TS (minima, and nodes without a role), and
    every free species; and the free species alone, for sums."""
    groups = {g.id: g.label or "Untitled group" for g in session.scalars(select(GroupNode))}
    candidates, species = [], []
    for node in node_service.list_nodes(session, with_calculations=True):
        if not node.geometry or node.id == exclude:
            continue
        is_species = node.kind == NodeKind.SPECIES
        if not is_species and node.role == Role.TRANSITION_STATE:
            continue
        structure = compare.resolve(session, folder, {"node_id": node.id}, tolerance)
        entry: dict[str, Any] = {
            "label": structure.label,
            "node_id": node.id,
            "ref": {"node_id": node.id},
        }
        if is_species:
            entry["kind"] = "species"
        if node.group_id in groups:
            entry["group"] = groups[node.group_id]
        candidate = Candidate(structure, entry)
        candidates.append(candidate)
        if is_species:
            species.append(candidate)
    return candidates, species


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
    return Candidate(structure, entry)


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


def _mapping(ts_rows: Rows, rows: Rows, active: set) -> list[int] | None:
    """The candidate's atom for each of the TS's under which only active pairs change bonds:
    the same numbering, the numbering by position, then Match atoms. A candidate in the TS's
    numbering whose bonds differ off the mode by no more than a step changes (A186) is another
    isomer numbered as the TS, so no other numbering is tried for it."""
    if geometry.elements(ts_rows) == geometry.elements(rows):
        identity = list(range(len(rows)))
        real, off = _off_mode(ts_rows, rows, identity, active)
        if not off:
            return identity
        if real <= atom_matching.MAX_BOND_CHANGES:
            return None
    by_position = compare._by_position(ts_rows, rows)[1]
    if not _off_mode(ts_rows, rows, by_position, active)[1]:
        return by_position
    try:
        found = atom_matching.match(ts_rows, rows)
    except Exception:  # noqa: BLE001 - a match that cannot be made places nothing
        return None
    return None if _off_mode(ts_rows, rows, found.mapping, active)[1] else found.mapping


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
) -> dict[str, Any]:
    """D263c, A184, A186: the structures on each side of the TS's imaginary mode, nearest
    first, and A184's bond check of the best pair. See the module's docstring."""
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
    found: dict[str, list[tuple[tuple, Candidate, dict[str, Any]]]] = {"+": [], "−": []}
    matched = 0
    for candidate in tried:
        structure = candidate.structure
        if not _compatible(structure, ts):
            continue
        if not compare._could_be_same(free, compare._bond_counts(structure.rows), False):
            continue
        matched += 1
        mapping = _mapping(rows, structure.rows, active)
        if mapping is None:
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
        key = (mixed, bool(structure.imaginary), rmsd if rmsd is not None else np.inf)
        found[side].append((key, candidate, entry))
    for side in found:
        found[side].sort(key=lambda f: f[0])

    predicted = _predicted(rows, mode)
    sides = []
    for side in ("−", "+"):
        listed = found[side]
        sides.append(
            {
                "side": side,
                "bonds": predicted[side],
                "found": len(listed),
                "candidates": [entry for _, _, entry in listed[:top]],
            }
        )

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
        },
        "seconds": seconds,
    }
    if len(ts.imaginary) > 1:
        out["notes"] = [
            f"“{ts.label}” has {len(ts.imaginary)} imaginary frequencies; the lowest is used."
        ]
    out["summary"], out["next"] = _words(ts, sides, check)
    return out


def _check(
    ts: compare.Structure,
    found: dict[str, list[tuple[tuple, Candidate, dict[str, Any]]]],
    tolerance: float,
) -> tuple[dict[str, Any] | None, int]:
    """A184's bond check of the best pairs, the nearest first: the first that links, else the
    best pair's verdict."""
    minus, plus = found["−"][:PAIR_TRIES], found["+"][:PAIR_TRIES]
    pairs = sorted(
        ((i, j) for i in range(len(minus)) for j in range(len(plus))),
        key=lambda p: (p[0] + p[1], p[0]),
    )
    first = None
    for count, (i, j) in enumerate(pairs, start=1):
        a, b = minus[i], plus[j]
        result = compare.compare(a[1].structure, b[1].structure, ts, False, tolerance)
        ts_check = result.get("ts_check") or {}
        check = {
            "a": _named(a[2]),
            "b": _named(b[2]),
            "verdict": ts_check.get("verdict", "unclear"),
            "reasons": ts_check.get("reasons", result.get("notes", [])),
            "changed": ts_check.get("changed", []),
        }
        if check["verdict"] == "links":
            return check, count
        if first is None:
            first = check
    return first, len(pairs)


def _named(entry: dict[str, Any]) -> dict[str, Any]:
    keys = ("label", "node_id", "species_node_id", "ref", "rmsd")
    return {k: entry[k] for k in keys if k in entry}


def _words(
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
