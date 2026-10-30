"""The scan editor (D135): several scans put together into one path, as a short film is cut
together from clips.

A scan is one job step of a file that drives a coordinate through points (a relaxed scan:
Gaussian `Opt=ModRedundant` with an S line, ORCA `%geom Scan`, xTB `xtbscan.log`; or a rigid
one), each point the structure its optimization converged on (D125). Its coordinate is the one
the file names (`ParsedStep.scan_coordinates`), else the distance, angle or dihedral that
changes by the most even steps from point to point.

The path is a list of pieces, each a run of one scan's points in either direction. Nothing is
written anywhere: the path is computed again from the pieces whenever they change, with what
the user must know about each join (a structure shared by the two pieces is kept once and its
two energies must agree; a jump in structure; an energy that drops or climbs in one step; a
path that turns back on its coordinate), and saved as an xyz file whose comment lines carry the
level, the energies and where each structure came from. Pieces at different levels of theory
are not joined (Q42). Every structure is fitted onto the one before it, never mirrored, so the
path plays without jumping.
"""

import dataclasses
import json
import math
from dataclasses import dataclass, field
from typing import Any

import numpy as np
from scipy.optimize import linear_sum_assignment

from chembook3d import pathtools, settings, units
from chembook3d.models import LevelOfTheory
from chembook3d.parsers import gaussian
from chembook3d.parsers import joined as joined_file
from chembook3d.parsers.common import ParsedFile, ParsedStep
from chembook3d.services import atom_matching, geometry, imports, irc, levels, trajectory
from chembook3d.services.records import RecordError

Rows = list[list[Any]]

SAME = settings.DUPLICATE_TOLERANCE  # Å (D62): two structures this close are the same one
ENERGY_AGREES = 0.1  # kcal/mol: the same structure's energies in two files
JUMP = pathtools.GOOD_JUMP  # Å (D116): a larger fitted RMSD between neighbours is a jump
STEP_KCAL = 3.0  # kcal/mol: an energy step larger than this ...
STEP_RATIO = 3.0  # ... and this many times the steps beside it is marked
EVEN = 0.05  # a coordinate found from the structures changes by steps this even (relative)
TIE = 1e-4  # spreads this close count as equally even
HEADER = joined_file.HEADER
KCAL = units.HARTREE_IN["kcal/mol"]
UNITS = {2: "Å", 3: "°", 4: "°"}


class ScanEditorError(ValueError):
    """The files or pieces cannot be used; the message says why."""


@dataclass
class Point:
    rows: Rows
    energy: float | None  # hartree
    value: float  # the coordinate on this structure (Å or degrees)
    number: int  # the scan point, from 1
    structure: int  # the structure's number among the step's structures, from 1
    source: str = ""  # for a joined scan read back: where the point came from


@dataclass
class Scan:
    key: str
    file: str  # the file's name
    path: str  # where it is (saga:/… for a server's file)
    step: int  # the job step, from 1
    steps: int  # how many job steps the file has
    program: str
    version: str | None
    level: dict[str, Any]  # what is compared between pieces
    level_label: str
    basis_named: bool  # False for ChkBasis with no basis set in the file (D104)
    charge: int | None
    multiplicity: int | None
    atoms: list[int]  # the coordinate (from 1)
    found_by: str  # "file" or "structures"
    points: list[Point]
    left_out: int = 0  # points whose optimization did not converge
    ended: bool = True  # the step ended normally
    joined: bool = False  # a joined scan saved by the editor, read back
    restarted: bool = False  # the file runs the scan again after it; only the first run is read
    renumbered: str = ""  # whose atom order the points were put in (D113), when they were
    order: list[int] | None = None  # ... the file's atom for each atom, kept for a refresh
    basis_prints: dict[str, str] = field(default_factory=dict)  # a GEN basis set's (D137)

    @property
    def unit(self) -> str:
        return UNITS[len(self.atoms)]

    @property
    def coordinate(self) -> str:
        return coordinate_label(self.atoms, self.points[0].rows)


@dataclass
class Piece:
    scan: str
    first: int  # index into the scan's points; the piece runs from first to last
    last: int

    @property
    def reversed(self) -> bool:
        return self.last < self.first

    def indexes(self) -> list[int]:
        step = -1 if self.reversed else 1
        return list(range(self.first, self.last + step, step))


@dataclass
class Frame:
    rows: Rows  # fitted onto the frame before
    energy: float | None
    x: float
    piece: int
    scan: str
    index: int  # into the scan's points
    number: int
    structure: int
    source: str


@dataclass
class Mark:
    at: int  # the frame the mark is on
    kind: str  # "join", "shared", "energies", "jump", "step", "back", "closed"
    text: str
    warning: bool


@dataclass
class Join:
    after: int  # the piece before the join
    at: int  # the first frame of the piece after it
    shared: bool  # the two pieces meet at the same structure, kept once
    rmsd: float | None  # Å, between the frames on either side (all atoms)
    rmsd_active: float | None  # Å, over the reacting atoms
    energy_difference: float | None  # kcal/mol: the shared structure's two energies
    ok: bool


@dataclass
class Joined:
    frames: list[Frame]
    axis: str  # "coordinate" or "path"
    atoms: list[int]  # the coordinate, when the axis is one
    unit: str
    label: str
    joins: list[Join]
    marks: list[Mark]
    top: int | None  # the frame of the path's highest top (D112), None without one
    barrier: float | None  # kcal/mol, the top above the first frame
    level_label: str
    notes: list[str] = field(default_factory=list)
    blocked: str | None = None  # why these pieces cannot be joined
    mismatch: str | None = None  # the scan whose atoms are in another order, to match (D113)
    text: str = ""  # the saved file
    offers: list["Offer"] = field(default_factory=list)  # finer scans that fit inside the path


# ---------- coordinates ----------


def coordinate_label(atoms: list[int], rows: Rows) -> str:
    names = [f"{rows[a - 1][0]}{a}" if a <= len(rows) else str(a) for a in atoms]
    kind = {2: "", 3: "angle ", 4: "dihedral "}[len(atoms)]
    return kind + "–".join(names)


def same_coordinate(a: list[int], b: list[int]) -> bool:
    return a == b or a == b[::-1]


def _unwrapped(values: list[float]) -> list[float]:
    """Dihedrals made continuous past ±180°."""
    out: list[float] = []
    for v in values:
        if out:
            while v - out[-1] > 180:
                v -= 360
            while v - out[-1] < -180:
                v += 360
        out.append(v)
    return out


def _evenness(values: list[float], least: float) -> float | None:
    """How unevenly `values` change from point to point (the spread of the steps over their
    mean), or None when they do not move one way by at least `least` each step."""
    steps = np.diff(values)
    if len(steps) == 0:
        return None
    mean = float(steps.mean())
    if abs(mean) < least or np.any(np.sign(steps) != np.sign(mean)):
        return None
    return float(steps.std() / abs(mean))


def find_coordinate(points: list[Rows]) -> list[int] | None:
    """The distance, angle or dihedral that changes by the most even steps across the points,
    as a constrained scan drives it; None when none does so evenly (EVEN)."""
    if len(points) < 3:
        return None
    coords = np.array([[row[1:4] for row in rows] for rows in points], dtype=float)
    n = coords.shape[1]
    best: tuple[float, list[int]] | None = None
    # Every pair of atoms: a distance.
    i, j = np.triu_indices(n, 1)
    distances = np.linalg.norm(coords[:, i, :] - coords[:, j, :], axis=2)
    steps = np.diff(distances, axis=0)
    mean = steps.mean(axis=0)
    moving = (np.abs(mean) >= 0.005) & np.all(np.sign(steps) == np.sign(mean), axis=0)
    if moving.any():
        spread = np.where(moving, steps.std(axis=0) / np.maximum(np.abs(mean), 1e-12), np.inf)
        k = int(np.argmin(spread))
        best = (float(spread[k]), [int(i[k]) + 1, int(j[k]) + 1])
    # Angles and dihedrals along bonds of the first or last structure.
    bonded = pathtools._bonds(points[0]) | pathtools._bonds(points[-1])
    near: dict[int, set[int]] = {k: set() for k in range(n)}
    for a, b in bonded:
        near[a].add(b)
        near[b].add(a)
    candidates: list[list[int]] = []
    for b in range(n):
        for a in near[b]:
            for c in near[b]:
                if a < c:
                    candidates.append([a, b, c])
    for b, c in bonded:
        for a in near[b] - {c}:
            for d in near[c] - {b, a}:
                candidates.append([a, b, c, d])
    for atoms in candidates:
        values = [pathtools._measure(rows, atoms) for rows in points]
        if len(atoms) == 4:
            values = _unwrapped(values)
        spread = _evenness(values, 0.5)
        numbered = [a + 1 for a in atoms]
        # Equally even coordinates (two atoms on one side of a symmetric bond): the lower
        # numbers, whatever rounding the machine does.
        if spread is not None and (
            best is None
            or spread < best[0] - TIE
            or (
                abs(spread - best[0]) <= TIE
                and len(numbered) == len(best[1])
                and numbered < best[1]
            )
        ):
            best = (spread, numbered)
    if best is None or best[0] > EVEN:
        return None
    return best[1]


def values_of(points: list[Rows], atoms: list[int]) -> list[float]:
    values = [pathtools.measure(rows, atoms) for rows in points]
    return _unwrapped(values) if len(atoms) == 4 else values


# ---------- levels ----------


def _basis_prints(step: ParsedStep) -> dict[str, str]:
    """D137: a GEN basis set's fingerprints, saved so an import can name it."""
    if step.resolved_basis_definition:
        return gaussian.basis_fingerprints(step.resolved_basis_definition)
    return dict(step.basis_fingerprints)


def _level(program: str, step: ParsedStep) -> tuple[dict[str, Any], str, bool]:
    """What two pieces must share (Q42): the program, method, basis set (when the file names
    it), dispersion with any IOps that re-parametrize it, implicit solvent, charge and
    multiplicity; a label for it; and whether the file names its basis set."""
    route = step.route
    if route is None:
        fields = {"program": program, "method": "", "basis": "", "dispersion": ""}
        fields |= {"solvation_model": "", "solvent": ""}
        level = {**fields, "iops": {}, "charge": step.charge, "multiplicity": step.multiplicity}
        return level, "level not read", True
    named = not imports.basis_not_named(program, step)
    basis = step.resolved_basis or route.basis or ""
    shown_basis = basis
    if basis in ("GEN", "GENECP") and step.resolved_basis_definition:
        prints = gaussian.basis_fingerprints(step.resolved_basis_definition)
        shown_basis = f"{basis} (defined in the file)"
        basis = f"{basis}:{levels.definition_key(prints)}"
    if not named:
        basis = shown_basis = ""
    method = route.method or ""
    if program == gaussian.PROGRAM:
        method = levels.canonical_method(method, step.multiplicity)
    iops = (
        gaussian.dispersion_iops(route.iops, route.dispersion)
        if program == gaussian.PROGRAM
        else {}
    )
    fields = {
        "program": program,
        "method": method,
        "basis": shown_basis if named else (route.basis or ""),
        "dispersion": (route.dispersion or "") + ("+IOp" if iops else ""),
        "solvation_model": route.solvation_model or "",
        "solvent": route.solvent or "",
    }
    label = levels.label(LevelOfTheory(**fields)) or method
    level = {
        **fields,
        "basis": basis,
        "dispersion": route.dispersion or "",
        "iops": iops,
        "charge": step.charge,
        "multiplicity": step.multiplicity,
    }
    return level, label, named


_LEVEL_NAMES = {
    "program": "program",
    "method": "method",
    "basis": "basis set",
    "dispersion": "dispersion",
    "iops": "dispersion parameters (IOp)",
    "solvation_model": "solvation model",
    "solvent": "solvent",
    "charge": "charge",
    "multiplicity": "multiplicity",
}


def level_difference(a: Scan, b: Scan) -> list[str]:
    """What differs between two scans' levels; a basis set a file does not name is not
    compared."""
    differ = []
    for key, name in _LEVEL_NAMES.items():
        if key == "basis" and not (a.basis_named and b.basis_named):
            continue
        if a.level.get(key) != b.level.get(key):
            differ.append(name)
    return differ


def energy_groups(scans: dict[str, Scan]) -> dict[str, str]:
    """D167: the scans whose energies the chart draws against one zero, each named by its
    group's first scan: those with no level difference, so a basis set read from the
    checkpoint goes with the file that names it. Scans that name theirs start the groups."""
    heads: list[Scan] = []
    groups: dict[str, str] = {}
    for scan in sorted(scans.values(), key=lambda s: not s.basis_named):
        head = next((h for h in heads if not level_difference(h, scan)), None)
        if head is None:
            heads.append(scan)
            head = scan
        groups[scan.key] = head.key
    return groups


# ---------- reading ----------


def _points(step: ParsedStep) -> tuple[list[Point], int, bool]:
    """One structure per scan point, the one its optimization converged on (D125), with the
    count of points left out because it never converged and whether the scan starts again
    after its last point (only its first run is read)."""
    frames = trajectory.frames_of(step)
    numbers = [f.point for f in frames if f.point is not None]
    restarted = False
    if not numbers:
        # A scan read from structures alone (xTB): each structure is a point.
        chosen = [(n + 1, n, f) for n, f in enumerate(frames)]
    else:
        last: dict[int, tuple[int, trajectory.Frame]] = {}
        reached = 0
        for n, f in enumerate(frames):
            if f.point is None:
                continue
            if f.point < reached:
                # The scan starts again (a copy that ran it twice, a later job read into the
                # same step): its first run is the scan.
                restarted = True
                break
            reached = f.point
            if f.converged:
                last[f.point] = (n, f)
        chosen = [(p, *last[p]) for p in sorted(last)]
        numbers = [p for p in numbers if p <= reached]
    left_out = len(set(numbers)) - len(chosen) if numbers else 0
    points = [
        Point(rows=f.rows, energy=f.energy, value=0.0, number=p, structure=n + 1)
        for p, n, f in chosen
    ]
    return points, max(left_out, 0), restarted


def scans_of(parsed: ParsedFile, name: str, path: str, file_key: str) -> list[Scan]:
    """The scans in a parsed file: its steps that scan through at least two points."""
    found: list[Scan] = []
    for step in parsed.steps:
        if not step.scan:
            continue
        points, left_out, restarted = _points(step)
        if len(points) < 2:
            continue
        if any(p.energy is None for p in points):
            points = [p for p in points if p.energy is not None]
            if len(points) < 2:
                continue
        atoms = next(
            (
                a
                for a in step.scan_coordinates
                if 2 <= len(a) <= 4 and max(a) <= len(points[0].rows)
            ),
            None,
        )
        found_by = "file"
        if atoms is None:
            atoms = find_coordinate([p.rows for p in points])
            found_by = "structures"
        if atoms is None:
            continue
        for p, v in zip(points, values_of([p.rows for p in points], atoms), strict=True):
            p.value = v
        level, label, named = _level(parsed.program, step)
        found.append(
            Scan(
                key=f"{file_key}:{step.index}",
                file=name,
                path=path,
                step=step.index,
                steps=len(parsed.steps),
                program=parsed.program,
                version=parsed.version,
                level=level,
                level_label=label,
                basis_named=named,
                basis_prints=_basis_prints(step),
                charge=step.charge,
                multiplicity=step.multiplicity,
                atoms=atoms,
                found_by=found_by,
                points=points,
                left_out=left_out,
                ended=step.termination == "normal",
                restarted=restarted,
            )
        )
    return found


def is_joined_scan(text: str) -> bool:
    return joined_file.looks_like_joined(text)


def read_joined(text: str, name: str, path: str, file_key: str) -> list[Scan]:
    """A joined scan saved by the editor, back as one scan whose points keep their sources."""
    frames = joined_file.frames(text)
    if len(frames) < 2:
        raise ScanEditorError(f"{name} holds fewer than two structures.")
    try:
        head = joined_file.head(frames[0][0])
    except joined_file.NotJoinedScan as exc:
        raise ScanEditorError(f"{name}: {exc}.") from exc
    points = []
    for n, (comment, atom_list) in enumerate(frames, start=1):
        fields = joined_file.comment_fields(comment)
        energy = joined_file.number(fields.get("energy"))
        rows = [[a.element, a.x, a.y, a.z] for a in atom_list]
        points.append(Point(rows, energy, 0.0, n, n, fields.get("from", "")))
    atoms = head.atoms or find_coordinate([p.rows for p in points]) or [1, 2]
    for p, v in zip(points, values_of([p.rows for p in points], atoms), strict=True):
        p.value = v
    return [
        Scan(
            key=f"{file_key}:1",
            file=name,
            path=path,
            step=1,
            steps=1,
            program=head.program,
            version=head.version,
            level=head.level,
            level_label=head.label,
            basis_named=head.basis_named,
            charge=head.charge,
            multiplicity=head.multiplicity,
            atoms=atoms,
            found_by="file",
            points=points,
            joined=True,
            basis_prints=head.basis_prints,
        )
    ]


# ---------- placing scans on the path ----------


def _same(a: Rows, b: Rows, *coordinates: list[int]) -> bool:
    """The same structure: close over all atoms (D62) and at the same value of each scanned
    coordinate given, since in a large molecule a whole scan step can hide in the all-atom
    RMSD."""
    if not geometry.matches(a, b, SAME):
        return False
    for atoms in coordinates:
        if not atoms or max(atoms) > len(a):
            continue
        difference = pathtools.measure(a, atoms) - pathtools.measure(b, atoms)
        if len(atoms) == 4:
            difference = (difference + 180) % 360 - 180
        if abs(difference) > (0.002 if len(atoms) == 2 else 0.2):  # Å or degrees
            return False
    return True


def _end_rows(scans: dict[str, Scan], pieces: list[Piece], last: bool) -> Rows:
    piece = pieces[-1] if last else pieces[0]
    scan = scans[piece.scan]
    return scan.points[piece.last if last else piece.first].rows


def place(scans: dict[str, Scan], pieces: list[Piece], key: str) -> tuple[list[Piece], str]:
    """`pieces` with the whole of scan `key` put where it meets the path, the right way round:
    starting where the path ends (appended), ending where it ends (appended, reversed),
    starting where it starts (another direction from the same structure: reversed, in front)
    or ending where it starts (in front). One that meets neither end is appended as it is."""
    scan = scans[key]
    whole = Piece(key, 0, len(scan.points) - 1)
    turned = Piece(key, len(scan.points) - 1, 0)
    if not pieces:
        return [whole], f"{_name(scan)} starts the path."
    first, last = scan.points[0].rows, scan.points[-1].rows
    end, start = _end_rows(scans, pieces, True), _end_rows(scans, pieces, False)
    at_end = (scan.atoms, scans[pieces[-1].scan].atoms)
    at_start = (scan.atoms, scans[pieces[0].scan].atoms)
    if _same(first, end, *at_end):
        return [*pieces, whole], f"{_name(scan)} goes on from where the path ends."
    if _same(last, end, *at_end):
        return [
            *pieces,
            turned,
        ], f"{_name(scan)} is turned round to go on from where the path ends."
    if _same(first, start, *at_start):
        return [turned, *pieces], (
            f"{_name(scan)} starts from the same structure as the path, so it is turned round "
            "and put in front."
        )
    if _same(last, start, *at_start):
        return [whole, *pieces], f"{_name(scan)} ends where the path starts, so it is put in front."
    return [*pieces, whole], (
        f"{_name(scan)} does not start or end at either end of the path; it is added at the end."
    )


def chain(
    scans: dict[str, Scan], keys: list[str], pieces: list[Piece]
) -> tuple[list[Piece], list[str]]:
    """Scans added together joined by themselves where they meet: continuations one after the
    other, and two directions from the same structure through it. Scans that meet none, or only
    run back over the path (a finer scan, a reverse branch), stay off it, to be cut by hand.
    On an empty path the start is the scan from which the most scans join on, then the longest
    path (scans that continue no other tried first), so a chain is not entered in its middle."""
    left = list(keys)
    if pieces or not left:
        grown, notes, left = _grow(scans, pieces, left)
    else:

        def continues(key: str) -> bool:
            first = scans[key].points[0].rows
            return any(
                _same(first, scans[k].points[-1].rows, scans[key].atoms, scans[k].atoms)
                for k in left
                if k != key
            )

        best: tuple[list[Piece], list[str], list[str]] | None = None
        for start in sorted(left, key=continues):
            placed, note = place(scans, [], start)
            tried = _grow(scans, placed, [k for k in left if k != start])
            if best is None or (len(tried[2]), -_length(tried[0])) < (
                len(best[2]),
                -_length(best[0]),
            ):
                best = (tried[0], [note, *tried[1]], tried[2])
            if not best[2]:
                break
        grown, notes, left = best
    for key in left:
        notes.append(
            f"{_name(scans[key])} does not go on from either end of the path; it is in the list."
        )
    return grown, notes


def _length(pieces: list[Piece]) -> int:
    return sum(abs(p.last - p.first) + 1 for p in pieces)


def _grow(
    scans: dict[str, Scan], pieces: list[Piece], left: list[str]
) -> tuple[list[Piece], list[str], list[str]]:
    notes: list[str] = []
    left = list(left)
    progress = True
    while left and progress:
        progress = False
        for key in list(left):
            scan = scans[key]
            ends = (
                (_end_rows(scans, pieces, False), scans[pieces[0].scan].atoms),
                (_end_rows(scans, pieces, True), scans[pieces[-1].scan].atoms),
            )
            if not any(
                _same(scan.points[i].rows, e, scan.atoms, atoms)
                for i in (0, -1)
                for e, atoms in ends
            ):
                continue
            placed, note = place(scans, pieces, key)
            if _turns_back(scans, placed):
                continue
            pieces = placed
            notes.append(note)
            left.remove(key)
            progress = True
    return pieces, notes, left


def _turns_back(scans: dict[str, Scan], pieces: list[Piece]) -> bool:
    """The pieces, all on one coordinate, do not run one way along it."""
    atoms = scans[pieces[0].scan].atoms
    if not all(same_coordinate(scans[p.scan].atoms, atoms) for p in pieces):
        return False
    values = [scans[p.scan].points[k].value for p in pieces for k in p.indexes()]
    if len(atoms) == 4:
        values = _unwrapped(values)
    tolerance = 1e-3 if len(atoms) == 2 else 0.05
    steps = [b - a for a, b in zip(values, values[1:], strict=False) if abs(b - a) > tolerance]
    return bool(steps) and any((st > 0) != (steps[0] > 0) for st in steps)


def _name(scan: Scan) -> str:
    return scan.file if scan.steps == 1 else f"{scan.file} (step {scan.step})"


# ---------- the joined path ----------


def _subset(rows: Rows, atoms: list[int]) -> Rows:
    return [rows[a - 1] for a in atoms]


def _kcal(a: float | None, b: float | None) -> float | None:
    return None if a is None or b is None else (b - a) * KCAL


def check_pieces(scans: dict[str, Scan], pieces: list[Piece]) -> None:
    for piece in pieces:
        scan = scans.get(piece.scan)
        if scan is None:
            raise ScanEditorError("A piece names a scan that is no longer in the editor.")
        size = len(scan.points)
        if not (0 <= piece.first < size and 0 <= piece.last < size):
            raise ScanEditorError(f"A piece of {_name(scan)} runs past its {size} points.")


def join(scans: dict[str, Scan], pieces: list[Piece]) -> Joined:
    check_pieces(scans, pieces)
    if not pieces:
        return Joined([], "coordinate", [], "", "", [], [], None, None, "", [])
    first = scans[pieces[0].scan]
    used = [scans[p.scan] for p in pieces]
    blocked = None
    mismatch = None
    for scan in used:
        elements = [r[0] for r in scan.points[0].rows]
        if elements != [r[0] for r in first.points[0].rows]:
            if sorted(elements) == sorted(r[0] for r in first.points[0].rows):
                mismatch = scan.key
                blocked = (
                    f"{_name(scan)} numbers its atoms in another order than {_name(first)}; "
                    "match its atoms to join them."
                )
            else:
                blocked = (
                    f"{_name(scan)} has other atoms than {_name(first)}; such scans cannot be "
                    "joined."
                )
            break
        differ = level_difference(first, scan)
        if differ:
            blocked = (
                f"{_name(scan)} ({scan.level_label}) is at another level of theory than "
                f"{_name(first)} ({first.level_label}): the {', '.join(differ)} differ. "
                "Pieces at different levels cannot be joined, since their energies cannot be "
                "compared (EN-3)."
            )
            break
    shared_axis = all(same_coordinate(s.atoms, first.atoms) for s in used)
    atoms = first.atoms if shared_axis else []
    unit = first.unit if shared_axis else "amu½·bohr"
    label = first.coordinate if shared_axis else "distance along the path"
    notes = []
    if not all(s.basis_named for s in used):
        notes.append(
            "The basis set is read from the checkpoint (ChkBasis) and not named in the files, "
            "so it was not compared."
        )
    if blocked:
        return Joined(
            [],
            "coordinate" if shared_axis else "path",
            atoms,
            unit,
            label,
            [],
            [],
            None,
            None,
            first.level_label,
            notes,
            blocked,
            mismatch,
        )

    frames: list[Frame] = []
    joins: list[Join] = []
    marks: list[Mark] = []
    active = pathtools.active_atoms(first.points[0].rows, first.points[0].rows, held=[first.atoms])
    for n, piece in enumerate(pieces):
        scan = scans[piece.scan]
        indexes = piece.indexes()
        if frames:
            previous = frames[-1]
            head = scan.points[indexes[0]]
            rmsd_all = geometry.aligned_rmsd(previous.rows, head.rows)
            shared = _same(previous.rows, head.rows, scan.atoms, scans[pieces[n - 1].scan].atoms)
            difference = _kcal(previous.energy, head.energy) if shared else None
            if shared:
                indexes = indexes[1:]
            join_at = len(frames) if indexes else len(frames) - 1
            rmsd_active = None
            if not shared:
                rmsd_active = (
                    geometry.aligned_rmsd(
                        _subset(previous.rows, active), _subset(head.rows, active)
                    )
                    if active
                    else rmsd_all
                )
            ok = True
            if shared and difference is not None and abs(difference) > ENERGY_AGREES:
                ok = False
                marks.append(
                    Mark(
                        join_at,
                        "energies",
                        f"The two files give the shared structure energies "
                        f"{abs(difference):.2f} kcal/mol apart; were they run with the same "
                        "settings (grid, SCF)?",
                        True,
                    )
                )
            if not shared and rmsd_all is not None and max(rmsd_all, rmsd_active or 0) > JUMP:
                ok = False
            joins.append(
                Join(
                    n - 1,
                    join_at,
                    shared,
                    None if shared else rmsd_all,
                    rmsd_active,
                    difference,
                    ok,
                )
            )
        for k in indexes:
            point = scan.points[k]
            rows = point.rows
            if frames:
                rows = geometry.place(frames[-1].rows, rows).rows
            source = point.source or (
                f"{scan.file}, step {scan.step}, point {point.number}"
                + (" (reversed)" if piece.reversed else "")
            )
            frames.append(
                Frame(
                    rows,
                    point.energy,
                    point.value,
                    n,
                    scan.key,
                    k,
                    point.number,
                    point.structure,
                    source,
                )
            )

    # The x axis: the coordinate, else the distance along the path.
    if shared_axis:
        xs = _unwrapped([f.x for f in frames]) if len(atoms) == 4 else [f.x for f in frames]
    else:
        masses = irc._masses(frames[0].rows)
        xs = [0.0]
        for a, b in zip(frames, frames[1:], strict=False):
            xs.append(xs[-1] + irc.mass_weighted_distance(a.rows, b.rows, masses))
    for f, x in zip(frames, xs, strict=True):
        f.x = x

    # Jumps and energy steps along the whole path.
    for k in range(1, len(frames)):
        a, b = frames[k - 1], frames[k]
        jump = geometry.aligned_rmsd(a.rows, b.rows) or 0.0
        jump_active = (
            geometry.aligned_rmsd(_subset(a.rows, active), _subset(b.rows, active))
            if active
            else jump
        ) or 0.0
        if max(jump, jump_active) > JUMP:
            marks.append(
                Mark(
                    k,
                    "jump",
                    f"The structure jumps {jump:.2f} Å here "
                    f"({jump_active:.2f} Å over the reacting atoms).",
                    True,
                )
            )
    energies = [f.energy for f in frames]
    steps = [_kcal(energies[k - 1], energies[k]) for k in range(1, len(frames))]
    for k, step in enumerate(steps):
        if step is None or abs(step) <= STEP_KCAL:
            continue
        beside = [
            abs(s)
            for s in (steps[k - 1] if k > 0 else None, steps[k + 1] if k + 1 < len(steps) else None)
            if s is not None
        ]
        if beside and abs(step) > STEP_RATIO * max(beside):
            word = "drops" if step < 0 else "climbs"
            marks.append(
                Mark(
                    k + 1,
                    "step",
                    f"The energy {word} {abs(step):.1f} kcal/mol in one step here.",
                    True,
                )
            )
    if shared_axis and len(xs) > 2:
        overall = math.copysign(1.0, xs[-1] - xs[0]) if xs[-1] != xs[0] else 0.0
        tolerance = 1e-3 if len(atoms) == 2 else 0.05
        turning = False
        for k in range(1, len(xs)):
            back = overall and (xs[k] - xs[k - 1]) * overall < -tolerance
            if back and not turning:
                marks.append(Mark(k, "back", f"The path turns back on {label} here.", True))
            turning = bool(back)
        if (
            len(atoms) == 4
            and abs(xs[-1] - xs[0]) >= 360 - 0.5
            and _same(frames[0].rows, frames[-1].rows, atoms)
        ):
            marks.append(
                Mark(
                    len(frames) - 1,
                    "closed",
                    f"The path turns {label} a full 360° back to its first structure.",
                    False,
                )
            )
    for j in joins:
        kind = "shared" if j.shared else "join"
        if j.shared and j.ok:
            text = "The pieces meet at the same structure, kept once."
        elif j.shared:
            text = "The pieces meet at the same structure, but its energies differ."
        else:
            text = (
                f"The pieces meet {j.rmsd:.2f} Å apart ({j.rmsd_active:.2f} Å over the reacting "
                "atoms)."
                if j.rmsd is not None and j.rmsd_active is not None
                else "The pieces meet here."
            )
        marks.append(Mark(j.at, kind, text, not j.ok))
    marks.sort(key=lambda m: m.at)

    top = None
    barrier = None
    if len(frames) > 2 and all(e is not None for e in energies):
        found = xtb_representative(energies)
        # D112's rule falls back on the middle point; only a real top is named one.
        if 0 < found < len(energies) - 1 and energies[found] > max(
            energies[found - 1], energies[found + 1]
        ):
            top = found
            barrier = _kcal(energies[0], energies[top])
    joined = Joined(
        frames,
        "coordinate" if shared_axis else "path",
        atoms,
        unit,
        label,
        joins,
        marks,
        top,
        barrier,
        first.level_label,
        notes,
    )
    joined.text = file_text(joined, first)
    joined.offers = finer_offers(scans, pieces, joined)
    return joined


def xtb_representative(energies: list[float | None]) -> int:
    from chembook3d.parsers import xtb

    return xtb.representative(energies)


# ---------- refining, crossing, comparing, matching ----------


@dataclass
class Offer:
    scan: str
    points: int  # the finer scan's points inside the path
    replaced: int  # the path's points they take the place of
    low: float
    high: float
    text: str


@dataclass
class Crossing:
    a: str  # the branch lower before the crossing (smaller x)
    b: str  # the branch lower after it
    x: float
    rmsd: float | None  # Å, between the two branches' points nearest the crossing
    text: str


NOISE = 0.05  # kcal/mol: energy differences below this do not make a crossing


def _tolerance(atoms: list[int]) -> float:
    return 0.005 if len(atoms) == 2 else 0.5  # Å or degrees


def _aligned(values: list[float], near: list[float], atoms: list[int]) -> list[float]:
    """A dihedral scan's values moved by whole turns to lie over `near` (another scan or the
    path); other coordinates as they are."""
    if len(atoms) != 4 or not values or not near:
        return list(values)
    turns = round((float(np.median(near)) - float(np.median(values))) / 360)
    return [v + 360 * turns for v in values]


def _values(scan: Scan, near: list[float]) -> list[float]:
    return _aligned([p.value for p in scan.points], near, scan.atoms)


def finer_offers(scans: dict[str, Scan], pieces: list[Piece], joined: Joined) -> list[Offer]:
    """Scans off the path that drive its coordinate in smaller steps within its range: one
    click puts them in place of the coarser points there."""
    if joined.axis != "coordinate" or len(joined.frames) < 2 or joined.blocked:
        return []
    used = {p.scan for p in pieces}
    first = scans[pieces[0].scan]
    xs = [f.x for f in joined.frames]
    tol = _tolerance(joined.atoms)
    offers = []
    for key, scan in scans.items():
        if key in used or not same_coordinate(scan.atoms, joined.atoms):
            continue
        if level_difference(first, scan) or len(scan.points[0].rows) != len(first.points[0].rows):
            continue
        values = _values(scan, xs)
        low, high = min(values), max(values)
        if low < min(xs) - tol or high > max(xs) + tol or high - low <= tol:
            continue
        inside = [x for x in xs if low - tol <= x <= high + tol]
        if len(scan.points) <= len(inside):
            continue
        unit = UNITS[len(joined.atoms)]
        offers.append(
            Offer(
                key,
                len(scan.points),
                len(inside),
                low,
                high,
                f"Use the {len(scan.points)} finer points of {_name(scan)} between "
                f"{_shown(low, unit)} and {_shown(high, unit)} {unit}",
            )
        )
    return offers


def _shown(value: float, unit: str) -> str:
    return f"{value:.3f}" if unit == "Å" else f"{value:.1f}"


def _upto(pieces: list[Piece], frames: list[Frame], at: int) -> list[Piece]:
    """The pieces that make the path's frames up to `at` (none when `at` < 0)."""
    if at < 0:
        return []
    frame = frames[at]
    piece = pieces[frame.piece]
    return [*pieces[: frame.piece], Piece(piece.scan, piece.first, frame.index)]


def _onward(pieces: list[Piece], frames: list[Frame], at: int) -> list[Piece]:
    """The pieces that make the path's frames from `at` on (none past the end)."""
    if at >= len(frames):
        return []
    frame = frames[at]
    piece = pieces[frame.piece]
    return [Piece(piece.scan, frame.index, piece.last), *pieces[frame.piece + 1 :]]


def replace(
    scans: dict[str, Scan], pieces: list[Piece], key: str, between: tuple[int, int] | None = None
) -> tuple[list[Piece], str]:
    """The path with the points of scan `key` (within the frames `between`, when given) put in
    place of the path's points over the same stretch of its coordinate. A structure the two
    share at either end is kept (once, by the join); nothing is interpolated."""
    joined = join(scans, pieces)
    if joined.blocked:
        raise ScanEditorError(joined.blocked)
    finer = scans[key]
    if joined.axis != "coordinate" or not same_coordinate(finer.atoms, joined.atoms):
        raise ScanEditorError(
            f"{_name(finer)} drives {finer.coordinate}, not the path's {joined.label}; only a "
            "scan of the same coordinate can replace part of it."
        )
    frames = joined.frames
    xs = [f.x for f in frames]
    tol = _tolerance(joined.atoms)
    values = _values(finer, xs)
    start, end = sorted(between) if between else (0, len(frames) - 1)
    if not (0 <= start <= end < len(frames)):
        raise ScanEditorError("That stretch is not on the path.")
    low, high = sorted((xs[start], xs[end])) if between else (min(values), max(values))
    keep = [k for k, v in enumerate(values) if low - tol <= v <= high + tol]
    if len(keep) < 2:
        raise ScanEditorError(f"{_name(finer)} has fewer than two points in that stretch.")
    low, high = min(values[k] for k in keep), max(values[k] for k in keep)
    region = [k for k in range(start, end + 1) if low - tol <= xs[k] <= high + tol]
    if region:
        # The first run of frames over that stretch, should the path cross it twice.
        run = [region[0]]
        for k in region[1:]:
            if k != run[-1] + 1:
                break
            run.append(k)
        r0, r1 = run[0], run[-1]
    else:
        # The finer points fall between two frames.
        r1 = next((k for k in range(start, end) if (xs[k] - low) * (xs[k + 1] - low) <= 0), None)
        if r1 is None:
            raise ScanEditorError(f"{_name(finer)} does not lie over that stretch of the path.")
        r0, r1 = r1 + 1, r1
    before = xs[r0 - 1] if r0 > 0 else xs[r0]
    after = xs[r1 + 1] if r1 + 1 < len(xs) else xs[r1]
    rising = after >= before if before != after else xs[-1] >= xs[0]
    ordered = sorted(keep, key=lambda k: values[k], reverse=not rising)
    piece = Piece(key, ordered[0], ordered[-1])
    if sorted(piece.indexes()) != sorted(keep):
        raise ScanEditorError(f"{_name(finer)} does not run one way over that stretch.")
    a = (
        r0
        if r0 < len(frames) and _same(frames[r0].rows, finer.points[piece.first].rows, finer.atoms)
        else r0 - 1
    )
    b = (
        r1
        if r1 >= 0 and _same(frames[r1].rows, finer.points[piece.last].rows, finer.atoms)
        else r1 + 1
    )
    taken = max(b - a - 1, 0)
    unit = UNITS[len(joined.atoms)]
    note = (
        f"{len(keep)} points of {_name(finer)} replace {taken} of the path's between "
        f"{_shown(low, unit)} and {_shown(high, unit)} {unit}."
    )
    return [*_upto(pieces, frames, a), piece, *_onward(pieces, frames, b)], note


def crossings(scans: dict[str, Scan]) -> list[Crossing]:
    """Where two scans of the same coordinate and level that overlap (a forward and a reverse
    branch) cross in energy: the lower branch changes there."""
    found: list[Crossing] = []
    keys = list(scans)
    for i, ka in enumerate(keys):
        for kb in keys[i + 1 :]:
            a, b = scans[ka], scans[kb]
            if not same_coordinate(a.atoms, b.atoms) or level_difference(a, b):
                continue
            if [r[0] for r in a.points[0].rows] != [r[0] for r in b.points[0].rows]:
                continue
            if any(p.energy is None for p in a.points + b.points):
                continue
            va = [p.value for p in a.points]
            vb = _values(b, va)
            tol = _tolerance(a.atoms)
            low, high = max(min(va), min(vb)), min(max(va), max(vb))
            if high - low <= tol:
                continue
            order_a, order_b = np.argsort(va), np.argsort(vb)
            xa, ea = np.array(va)[order_a], np.array([p.energy for p in a.points])[order_a] * KCAL
            xb, eb = np.array(vb)[order_b], np.array([p.energy for p in b.points])[order_b] * KCAL
            grid = sorted({float(x) for x in [*xa, *xb] if low - 1e-9 <= x <= high + 1e-9})
            diff = [float(np.interp(x, xa, ea) - np.interp(x, xb, eb)) for x in grid]
            signed = [(x, d) for x, d in zip(grid, diff, strict=True) if abs(d) > NOISE]
            for (x0, d0), (x1, d1) in zip(signed, signed[1:], strict=False):
                if (d0 > 0) == (d1 > 0):
                    continue
                x = x0 + (x1 - x0) * d0 / (d0 - d1)
                near_a = a.points[int(np.argmin([abs(v - x) for v in va]))].rows
                near_b = b.points[int(np.argmin([abs(v - x) for v in vb]))].rows
                rmsd = geometry.aligned_rmsd(near_a, near_b)
                lower_first, lower_after = (ka, kb) if d0 < 0 else (kb, ka)
                unit = UNITS[len(a.atoms)]
                text = (
                    f"{_name(scans[lower_first])} and {_name(scans[lower_after])} cross at "
                    f"{_shown(x, unit)} {unit}"
                    + (f", {rmsd:.2f} Å apart there" if rmsd is not None else "")
                )
                found.append(Crossing(lower_first, lower_after, x, rmsd, text))
                break  # the first crossing of the pair
    return found


def cut_at_crossing(
    scans: dict[str, Scan], pieces: list[Piece], crossing: Crossing
) -> tuple[list[Piece], str]:
    """The lower branch on each side of the crossing joined there: the branch lower before it
    up to the crossing, then the other from there on, both turned the path's way (the way the
    path already runs through them, else rising). Nothing is interpolated between them."""
    low_first, low_after = scans[crossing.a], scans[crossing.b]
    va = [p.value for p in low_first.points]
    vb = _values(low_after, va)
    before = [k for k, v in enumerate(va) if v <= crossing.x]
    after = [k for k, v in enumerate(vb) if v > crossing.x]
    if not before or not after:
        raise ScanEditorError("The branches do not both reach the crossing.")
    rising = True
    for piece in pieces:
        if piece.scan in (crossing.a, crossing.b) and piece.first != piece.last:
            values = va if piece.scan == crossing.a else vb
            rising = values[piece.last] > values[piece.first]
            break
    one = sorted(before, key=lambda k: va[k])
    two = sorted(after, key=lambda k: vb[k])
    joined = [Piece(crossing.a, one[0], one[-1]), Piece(crossing.b, two[0], two[-1])]
    if not rising:
        joined = [Piece(p.scan, p.last, p.first) for p in reversed(joined)]
    at = next((n for n, p in enumerate(pieces) if p.scan in (crossing.a, crossing.b)), None)
    rest = [p for p in pieces if p.scan not in (crossing.a, crossing.b)]
    if at is None:
        new = [*pieces, *joined]
    else:
        kept_before = len([p for p in pieces[:at] if p.scan not in (crossing.a, crossing.b)])
        new = [*rest[:kept_before], *joined, *rest[kept_before:]]
    return new, f"Joined at the crossing: {crossing.text}."


def compare(scans: dict[str, Scan], points: list[tuple[str, int]]) -> list[dict[str, Any]]:
    """The structures of these scan points fitted onto the first, never mirrored, with each
    one's RMSD to it."""
    if not points:
        return []
    for key, index in points:
        if key not in scans or not 0 <= index < len(scans[key].points):
            raise ScanEditorError("A point to compare is no longer in the editor.")
    reference = scans[points[0][0]].points[points[0][1]].rows
    out = []
    for key, index in points:
        point = scans[key].points[index]
        same = [r[0] for r in point.rows] == [r[0] for r in reference]
        rows = geometry.place(reference, point.rows).rows if same else point.rows
        out.append(
            {
                "scan": key,
                "index": index,
                "geometry": rows,
                "energy": point.energy,
                "value": point.value,
                "rmsd": geometry.aligned_rmsd(reference, point.rows) if same else None,
            }
        )
    return out


def _by_distances(reference: Rows, rows: Rows) -> list[int]:
    """`rows`' atom for each of `reference`'s: paired element by element on each atom's sorted
    distances to all the others, which turning the molecule leaves as they are."""

    def profiles(atoms: Rows) -> np.ndarray:
        xyz = np.array([r[1:4] for r in atoms], dtype=float)
        return np.sort(np.linalg.norm(xyz[:, None, :] - xyz[None, :, :], axis=2), axis=1)

    a, b = profiles(reference), profiles(rows)
    mapping = [0] * len(reference)
    for element in sorted({r[0] for r in reference}):
        mine = [i for i, r in enumerate(reference) if r[0] == element]
        theirs = [j for j, r in enumerate(rows) if r[0] == element]
        cost = np.abs(a[mine][:, None, :] - b[theirs][None, :, :]).sum(axis=2)
        for r, c in zip(*linear_sum_assignment(cost), strict=True):
            mapping[mine[r]] = theirs[c]
    return mapping


def match_atoms(scans: dict[str, Scan], key: str, to: str) -> str:
    """Scan `key`'s points renumbered to scan `to`'s atom order (D113), its coordinate with
    them: the atoms of whichever of its ends lies closest to an end of `to` matched to that
    end's, so the two structures compared are alike. Returns what was done."""
    scan, reference = scans[key], scans[to]
    found = None
    for mine in (scan.points[0], scan.points[-1]):
        for theirs in (reference.points[0], reference.points[-1]):
            try:
                tried = atom_matching.match(theirs.rows, mine.rows)
            except RecordError as exc:
                raise ScanEditorError(
                    f"{_name(scan)} cannot be matched to {_name(reference)}: {exc}"
                ) from exc
            if found is None or tried.rmsd < found.rmsd:
                found = tried
            if found.rmsd > SAME:
                # The same structure numbered another way, as where two scans meet, is found
                # outright by each atom's distances to all the others.
                mapping = _by_distances(theirs.rows, mine.rows)
                rmsd = geometry.aligned_rmsd(theirs.rows, [mine.rows[j] for j in mapping])
                if rmsd is not None and rmsd < found.rmsd:
                    found = dataclasses.replace(
                        found, mapping=mapping, rmsd=rmsd, formed=[], broken=[], doubts=[]
                    )
    renumber(scan, found.mapping)  # mapping[i]: the scan's atom for the reference's atom i
    scan.renumbered = _name(reference)
    note = (
        f"{_name(scan)} renumbered to the atom order of {_name(reference)} "
        f"({found.rmsd:.2f} Å apart where they meet, once matched)."
    )
    if found.doubts:
        note += " Check it: " + " ".join(found.doubts)
    return note


def renumber(scan: Scan, mapping: list[int]) -> None:
    """The scan's atoms put in another order: atom i becomes its atom mapping[i] (from 0)."""
    for point in scan.points:
        point.rows = [point.rows[j] for j in mapping]
    new_number = {j + 1: i + 1 for i, j in enumerate(mapping)}
    scan.atoms = [new_number[a] for a in scan.atoms]
    scan.order = [scan.order[j] for j in mapping] if scan.order else list(mapping)


# ---------- a file read again (D138) ----------


def refreshed(old: Scan, fresh: Scan) -> Scan:
    """`fresh`, the scan read again from its file, put in the atom order `old` was given."""
    if old.order and len(old.order) == len(fresh.points[0].rows):
        renumber(fresh, old.order)
        fresh.renumbered = old.renumbered
    return fresh


def carry(old: dict[str, Scan], new: dict[str, Scan], pieces: list[Piece]) -> list[Piece]:
    """The path's pieces moved onto the scans read again (D138): each index follows its scan
    point (the nearest point when that one is gone), and an end of the path at the last point
    of a scan still running follows it to its new last point."""

    def moved(key: str, index: int) -> int:
        before, after = old[key].points, new[key].points
        number = before[min(index, len(before) - 1)].number
        return min(range(len(after)), key=lambda i: (abs(after[i].number - number), i))

    out: list[Piece] = []
    for n, piece in enumerate(pieces):
        if piece.scan not in new or piece.scan not in old:
            out.append(piece)
            continue
        end = len(old[piece.scan].points) - 1
        last_new = len(new[piece.scan].points) - 1
        first, last = moved(piece.scan, piece.first), moved(piece.scan, piece.last)
        if n == 0 and piece.first == end and piece.reversed:
            first = last_new
        if n == len(pieces) - 1 and piece.last == end and not piece.reversed:
            last = last_new
        out.append(Piece(piece.scan, first, last))
    return out


# ---------- the saved file ----------


def file_text(joined: Joined, first: Scan) -> str:
    """The joined path as a multi-structure xyz file. The first comment line names the level
    (as a label and as the fields compared, D135), the coordinate and how the editor wrote it;
    every comment line gives the structure's energy in Eh as its file printed it, its value on
    the x axis and where it came from."""
    level = {k: v for k, v in first.level.items()}
    head = [
        HEADER,
        f"program: {first.program}",
        f"version: {first.version or ''}",
        f"level: {joined.level_label}",
        f"level fields: {json.dumps(level, sort_keys=True)}",
        f"basis named: {'yes' if first.basis_named else 'no'}",
        *(
            [f"basis fingerprints: {json.dumps(first.basis_prints, sort_keys=True)}"]
            if first.basis_prints
            else []
        ),
        f"charge: {first.charge if first.charge is not None else ''}",
        f"multiplicity: {first.multiplicity if first.multiplicity is not None else ''}",
        f"coordinate: {' '.join(str(a) for a in joined.atoms)}",
        f"x: {joined.label} ({joined.unit})",
    ]
    out = []
    for n, frame in enumerate(joined.frames):
        energy = "" if frame.energy is None else f"{frame.energy:.10f}"
        parts = [f"energy: {energy}", f"x: {frame.x:.5f}", f"from: {frame.source}"]
        comment = " | ".join((head if n == 0 else []) + parts)
        rows = [[r[0], r[1], r[2], r[3]] for r in frame.rows]
        out.append(pathtools.format_frame(rows, comment))
    return "".join(out)
