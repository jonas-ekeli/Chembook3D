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
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from chembook3d import cloud_jobs, pathtools
from chembook3d.models import GroupNode, Node, Role, Status, Transition
from chembook3d.services import atom_matching, imports
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
    renumbered: list[list[Any]]
    charge: int
    multiplicity: int
    solvent: str | None
    ts_ends: list[dict[str, Any]]
    # A TS end's imaginary mode (wavenumber, vectors) in the start's numbering, turned with
    # the end as it is placed on the start, for `inputs/<end>_mode.json` (D116).
    modes: dict[str, tuple[float, list[list[float]]]] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


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


def plan(
    session: Session,
    start_id: str,
    end_id: str,
    pairs: list[list[int]] | None = None,
    start_member_id: str | None = None,
    end_member_id: str | None = None,
) -> Plan:
    """D114: the checks, the atom match and the suggested TS coordinates for a scan path."""
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
    if len(a) != len(b):
        raise RecordError(
            f"“{start.name}” has {len(a)} atoms and “{end.name}” {len(b)}: a species joins or "
            "leaves on this edge, which a scan path cannot follow yet"
        )
    charge, multiplicity = _charge_multiplicity(start, end)
    match = atom_matching.match(a, b, atom_matching.pairs_from(pairs or []))
    renumbered = atom_matching.renumbered(a, b, match.mapping)
    changed = [[i + 1, j + 1] for i, j in match.formed + match.broken]
    ts_ends = []
    modes = {}
    # The end in the start's order as it is stored (not fitted), so its mode vectors fit it.
    reordered = [b[j] for j in match.mapping]
    for which, item, rows, other in (
        ("start", start, a, renumbered),
        ("end", end, reordered, a),
    ):
        if item.node.role != Role.TRANSITION_STATE:
            continue
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
            row["start_value"] = value(a, row["atoms"])
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
        start, end, edge, match, renumbered, charge, multiplicity, solvent, ts_ends, modes, warnings
    )


def check_held(plan_: Plan, held: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The coordinates to hold, checked: which TS end, 2 to 4 atoms of the structure; with
    their values at that end."""
    ts = {t["end"]: t for t in plan_.ts_ends}
    if len(held) > MAX_HELD:
        raise RecordError(f"hold at most {MAX_HELD} coordinates")
    count = len(plan_.start.node.geometry)
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
        rows, other = plan_.start.node.geometry, plan_.renumbered
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
This is a **scan path job** (Chembook3D D114, D116). Find the best path of relaxed GFN2-xTB
structures from `inputs/start.xyz` to `inputs/end.xyz` and return it as `outputs/path.xyz`.
Chembook3D imports it as a new node whose scan plays as a movie (D112). Paths here are
relative to this job's folder, where the commands below are run from; the helper is
`../../.claude/chembook3d/pathtools.py` from there (Python, standard library only; run it as a
script, `python3 ../../.claude/chembook3d/pathtools.py <command>`, here written `pathtools.py`).
Atoms are numbered from 1 everywhere, as xTB numbers them.

## The two ends

- `inputs/start.xyz`: “{start}”{start_ts}.
- `inputs/end.xyz`: “{end}”{end_ts}, renumbered by the app in the start's atom order and fitted
  on the start. The atoms correspond one to one; never renumber them.
- `inputs/mapping.json`: the match. `formed` and `broken` are the bonds (start numbering) that
  form and break between the ends by the app's rule; `mapping` gives each start atom's number
  in the end's own file.
- `inputs/path.json`: the settings below, machine-readable.{mode_files}

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

**After every stage**, run `pathtools.py trace stage-1/xtbscan.log --atoms 3 7 --to 1.54`:
xTB ends normally even when a scan moved nothing (a wrong `$scan` number, for example), and
the trace says whether each driven coordinate moved and reached its target (exit status 1 when
one barely moved). `pathtools.py frames stage-1/xtbscan.log --last -o stage-2/start.xyz` takes
out the structure the next stage starts from.

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
{ts_first}
- One concerted scan of the bonds that form and break (and the held coordinates).
- Stages: the large dihedral changes first, then the bond changes, or the other way round.
- The same scanned from the end back to the start (`join --reverse` turns it round).
- xTB's own path finder, `xtb inputs/start.xyz --path inputs/end.xyz --input path.inp`, which
  needs no coordinates.

**Budget**: stop after 6 strategies or about 60 minutes of xTB time, whichever comes first,
then return the best path you have.

When the two ends differ mostly in atoms far from anything that forms, breaks or is held (a
different conformer of a side group), do not force that conformer change into the path: a
path that ends in a nearby conformer is fine. Say so in `path.conformer_note`, with the end
RMSD over the atoms that do take part when you can work it out.

## Which path is best

Check each candidate with

    pathtools.py check path.xyz inputs/start.xyz inputs/end.xyz --mapping inputs/mapping.json

A path **passes** when it reaches the end (`end_rmsd` at most 0.5 Å after fitting), forms or
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
    "end_rmsd": 0.21,
    "top": 14,
    "barrier_kcal": 18.2,
    "spikes": [],
    "design": "What the best path drives, stage by stage, and why.",
    "stages": [{{"coordinates": ["distance 3-7 2.10 -> 1.54"], "points": 20,
                 "force_constant": 1.0, "direction": "forward"}}],
    "tried": ["What else was tried and how it failed."],
    "conformer_note": null,
    "ts_check": null
  }}
  ```

  `gate` is `"passed"` or `"missed"`; `missed` lists the reasons in a few words each (for
  example "end RMSD 1.27 Å"). `summary` says in two or three sentences what the best path does
  and how close it came.
"""

MODE_FILE = "{end}_mode.json"  # a TS end's imaginary mode, in inputs/ (D116)

TS_FIRST = """
- **Downhill from the TS first.** Push the TS a little along its imaginary mode toward the
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


def create_job(
    folder: Path,
    investigation: str,
    plan_: Plan,
    held: list[dict[str, Any]],
    solvent: str | None,
) -> dict[str, Any]:
    """D114: the scan path job folder, ready to start (D93). The database is not changed."""
    if solvent:
        solvent = SOLVENT_NAMES.get(solvent.strip().lower(), solvent.strip().lower())
        if solvent not in ALPB_SOLVENTS:
            raise RecordError(f"xTB's ALPB has no solvent “{solvent}”")
    held = check_held(plan_, held)
    start, end, match = plan_.start, plan_.end, plan_.match
    summary = atom_matching.summary(match)
    mapping = {
        "start": {"node_id": start.node.id, "label": start.name},
        "end": {"node_id": end.node.id, "label": end.name},
        **{k: summary[k] for k in ("mapping", "formed", "broken", "inverted", "fixed", "rmsd")},
    }
    settings = {
        "charge": plan_.charge,
        "multiplicity": plan_.multiplicity,
        "uhf": plan_.multiplicity - 1,
        "solvent": solvent,
        "held": held,
    }
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
    )
    inputs = [
        cloud_jobs.InputFile(
            name="start.xyz",
            text=_xyz(start.node.geometry, start.name),
            description=f"the start, node “{start.name}”, {len(start.node.geometry)} atoms",
            node_id=start.node.id,
        ),
        cloud_jobs.InputFile(
            name="end.xyz",
            text=_xyz(plan_.renumbered, f"{end.name} in the atom order of {start.name}"),
            description=f"the end, node “{end.name}”, renumbered in the start's order",
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
            description="charge, multiplicity, solvent and the coordinates held at a TS end",
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


def _notes(job: dict[str, Any], result: dict[str, Any], start: str, end: str) -> str:
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
        rmsd = path.get("end_rmsd")
        rmsd_text = f" (RMSD {float(rmsd):.2f} Å)" if isinstance(rmsd, int | float) else ""
        lines.append(f"End {reached}{rmsd_text}.")
    if isinstance(path.get("barrier_kcal"), int | float):
        lines.append(
            f"Highest point {float(path['barrier_kcal']):.1f} kcal/mol above the start (GFN2-xTB)."
        )
    design = str(path.get("design") or "").strip()
    if design:
        lines.append(f"Design: {design}")
    conformer = str(path.get("conformer_note") or "").strip()
    if conformer:
        lines.append(f"Conformers: {conformer}")
    if path.get("ts_check"):
        lines.append(f"TS check: {_ts_check_text(path['ts_check'])}")
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
        notes=_notes(job, result if isinstance(result, dict) else {}, start_name, end_name),
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
