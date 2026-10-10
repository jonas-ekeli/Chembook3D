"""The structures of an optimization or a scan, played as a movie in the 3D view (D101,
FR-3D-08).

Nothing is stored for this: the structures and their energies are read again from the
calculation's copied output file when asked, so calculations imported before D101 have them
too. Each structure is turned onto the calculation's final geometry, so the movie does not
jump about and ends on the structure the node shows.

The structure shown can be taken over as a node's geometry (D112, `use_frame`): in place on a
scan path node, whose calculations are all xTB relaxed scans read from `xtbscan.log` or
`path.xyz`; otherwise on a derived node (ID-4, ID-5).

A scan path's points can be removed from the path by hand (D117, `trim`): the file is never
touched, the calculation lists the points it leaves out (`removed_points`), and the path's top
is chosen again over the points kept, with each cut's jump measured as `pathtools.py` does.
"""

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from chembook3d import pathtools
from chembook3d.models import Calculation, CalculationType, Node
from chembook3d.parsers import xtb
from chembook3d.parsers.common import ParsedFile
from chembook3d.services import files as file_service
from chembook3d.services import geometry, history, imports
from chembook3d.services import nodes as node_service

# Two structures printed one after the other this close (Å, every coordinate) are the same
# structure printed again: Gaussian repeats the final one after an optimization, ORCA
# evaluates it once more at the stationary point.
SAME_STRUCTURE = 1e-5
# D117: the structure a scan path calculation shows is the frame within this fitted RMSD (Å).
SHOWN_RMSD = 1e-3
# D117 (D116's limits): a cut whose structures differ by more than this (Å) is a jump, and a
# point this far (kcal/mol) above both kept neighbours with such a jump is a spike.
LARGE_JUMP = pathtools.GOOD_JUMP


class StepsUnavailable(ValueError):
    """The calculation's structures cannot be read; the message says why."""


@dataclass
class Frame:
    rows: list[list[Any]]  # [[element, x, y, z], ...], placed on the final geometry
    energy: float | None  # hartree, the SCF energy computed on this structure
    point: int | None  # the scan point it belongs to (1-based), None outside a scan
    converged: bool  # an optimization (or a scan point's) converged on this structure
    stage: int | None = None  # D112: the stage of a scan path that names its stages
    removed: bool = False  # D117: left out of the path by hand


@dataclass
class Cut:
    """D117: where removed points leave a gap between two kept frames (0-based)."""

    before: int  # the last kept frame before the gap
    after: int  # the first kept frame after it
    removed: int  # how many frames the gap holds
    jump: float | None  # Å, the fitted RMSD between `before` and `after`
    large: bool  # the jump is over LARGE_JUMP: the trimmed path is not continuous there


@dataclass
class Trim:
    """D117: a scan path with its removed points left out."""

    removed: list[int]  # the points removed (1-based scan points), in order
    kept: int  # how many frames are kept
    top: int  # the frame (0-based) the node takes over the kept frames (`xtb.representative`)
    shown: int | None  # the frame the calculation stands for, when one matches its geometry
    automatic: bool  # the calculation shows `top`, so a trim moves it with the top
    barrier: float | None  # hartree, the top above the first kept frame
    cuts: list[Cut]
    spikes: list[int]  # kept frames (0-based) far above both kept neighbours with a jump
    first_removed: bool  # the path no longer starts where it was run from
    last_removed: bool  # nor ends there


@dataclass
class Steps:
    scan: str | None  # "relaxed", "rigid" or None
    points: int  # how many scan points the file reached; 0 outside a scan
    frames: list[Frame]
    trim: Trim | None = None  # D117: a scan path's kept points; None for any other calculation


@lru_cache(maxsize=32)  # the canvas reads every scan path shown on an edge (D121)
def _read(path: str, modified: int, size: int) -> ParsedFile | None:
    """The parsed file; the cache key changes when the copy does (it never should)."""
    text = Path(path).read_bytes().decode("utf-8", errors="replace")
    parsed = imports.read_file(text, Path(path).name)
    return parsed if isinstance(parsed, ParsedFile) else None


def _same(a: list[list[Any]], b: list[list[Any]]) -> bool:
    return len(a) == len(b) and all(
        p[0] == q[0] and all(abs(p[k] - q[k]) <= SAME_STRUCTURE for k in (1, 2, 3))
        for p, q in zip(a, b, strict=True)
    )


def read_steps(folder: Path, calculation: Calculation) -> Steps:
    source = calculation.source_file
    if source is None or calculation.step_index is None:
        raise StepsUnavailable("This calculation was not imported from an output file")
    path = file_service.absolute_path(folder, source)
    try:
        stat = path.stat()
        parsed = _read(str(path), stat.st_mtime_ns, stat.st_size)
    except OSError as exc:
        raise StepsUnavailable(f"The copy of {source.original_name} cannot be read") from exc
    except imports.ImportFailed as exc:
        raise StepsUnavailable(str(exc)) from exc
    step = (
        next((s for s in parsed.steps if s.index == calculation.step_index), None)
        if parsed
        else None
    )
    if step is None or not step.geometries:
        raise StepsUnavailable(f"No structures were found for this step in {source.original_name}")

    frames: list[Frame] = []
    converged = set(step.converged_geometries)
    for n, atoms in enumerate(step.geometries):
        rows = imports._rows(atoms) or []
        energy = step.geometry_energies[n] if n < len(step.geometry_energies) else None
        point = step.geometry_points[n] if n < len(step.geometry_points) else None
        stage = step.geometry_stages[n] if n < len(step.geometry_stages) else None
        frame = Frame(rows, energy, point, n in converged, stage)
        if frames and _same(frames[-1].rows, rows):
            # Shown once: the later energy (the final evaluation's) if it has one.
            last = frames[-1]
            last.energy = energy if energy is not None else last.energy
            last.point = point if point is not None else last.point
            last.converged = last.converged or frame.converged
            continue
        frames.append(frame)

    reference = calculation.geometry or frames[-1].rows
    for frame in frames:
        if geometry.elements(frame.rows) == geometry.elements(reference):
            frame.rows = geometry.place(reference, frame.rows).rows
    points = {f.point for f in frames if f.point is not None}
    steps = Steps(scan=step.scan, points=len(points), frames=frames)
    if is_scan_path(calculation):
        steps.trim = trim_of(frames, calculation.removed_points or [], calculation.geometry)
    return steps


def _jump(a: list[list[Any]], b: list[list[Any]]) -> float | None:
    try:
        return pathtools.rmsd(a, b)
    except ValueError:
        return None


def trim_of(frames: list[Frame], removed: list[int], shown_rows: list[Any] | None) -> Trim:
    """D117: marks the removed frames and describes the path over the kept ones: its top
    (neighbours across a cut are the nearest kept frames), each cut and its jump, and the
    spikes `pathtools.py check` would report on the kept frames."""
    gone = set(removed)
    for frame in frames:
        frame.removed = frame.point in gone
    kept = [n for n, f in enumerate(frames) if not f.removed]
    energies = [frames[n].energy for n in kept]
    top = kept[xtb.representative(energies)] if kept else 0
    jumps = [_jump(frames[a].rows, frames[b].rows) for a, b in zip(kept, kept[1:], strict=False)]
    cuts = [
        Cut(a, b, b - a - 1, jump, jump is not None and jump > LARGE_JUMP)
        for (a, b), jump in zip(zip(kept, kept[1:], strict=False), jumps, strict=True)
        if b - a > 1
    ]
    spikes: list[int] = []
    if len(kept) > 2 and None not in energies and None not in jumps:
        spikes = [kept[k] for k in pathtools._spikes(energies, jumps)]
    shown = None
    if shown_rows:
        fits = [
            (rmsd, n)
            for n, f in enumerate(frames)
            if (rmsd := geometry.aligned_rmsd(shown_rows, f.rows)) is not None
            and rmsd <= SHOWN_RMSD
        ]
        shown = min(fits)[1] if fits else None
    low = frames[kept[0]].energy if kept else None
    high = frames[top].energy
    return Trim(
        removed=sorted(gone),
        kept=len(kept),
        top=top,
        shown=shown,
        automatic=shown == top,
        barrier=high - low if high is not None and low is not None else None,
        cuts=cuts,
        spikes=spikes,
        first_removed=bool(frames) and frames[0].removed,
        last_removed=bool(frames) and frames[-1].removed,
    )


def is_scan_path(calculation: Calculation) -> bool:
    """D112: a relaxed scan read from an xTB `xtbscan.log` or `path.xyz`."""
    return (
        calculation.program == xtb.PROGRAM
        and calculation.type == CalculationType.OTHER
        and calculation.route.startswith(xtb.SCAN_ROUTE)
    )


def changes_in_place(node: Node) -> bool:
    """D112: a scan path node, whose calculations are all such scans, takes a structure of
    its movie in place; no other result belongs to its coordinates."""
    return bool(node.calculations) and all(is_scan_path(c) for c in node.calculations)


def use_frame(
    session: Session, folder: Path, calculation: Calculation, frame: int
) -> node_service.GeometryResult:
    """D112: the structure `frame` (0-based, as `read_steps` lists them) as the geometry of
    the calculation's node: in place on a scan path node, where the scan then stands for
    that point (its geometry and energy), else on a new derived node."""
    steps = read_steps(folder, calculation)
    if not 0 <= frame < len(steps.frames):
        raise StepsUnavailable(f"There is no structure {frame + 1} in this calculation")
    chosen = steps.frames[frame]
    rows = [[e, float(x), float(y), float(z)] for e, x, y, z in chosen.rows]
    node = calculation.node
    if not changes_in_place(node):
        return node_service.GeometryResult(node_service.derive(session, node, rows), derived=True)
    if node.geometry != rows:
        history.record(session, "node", node.id, "update", "geometry", node.geometry, rows)
        node.geometry = rows
    if calculation.geometry != rows:
        history.record(
            session, "calculation", calculation.id, "update", "geometry", calculation.geometry, rows
        )
        calculation.geometry = rows
    result = calculation.result
    if result is not None and result.energy != chosen.energy:
        history.record(
            session, "calculation", calculation.id, "update", "energy", result.energy, chosen.energy
        )
        result.energy = chosen.energy
    session.flush()
    return node_service.GeometryResult(node, derived=False)


@dataclass
class TrimResult:
    steps: Steps
    moved: int | None  # the frame (0-based) the node moved to with the top, if it did


def removed_after(
    steps: Steps, remove: list[int] | None = None, restore: list[int] | None = None
) -> list[int]:
    """D117: the points removed once `remove` are taken out of the path and `restore` put
    back, checked against the path: points it has, and at least two points kept."""
    if steps.trim is None:
        raise StepsUnavailable("Only a scan path's points can be removed")
    points = {f.point for f in steps.frames if f.point is not None}
    asked = set(remove or []) | set(restore or [])
    if unknown := sorted(asked - points):
        raise StepsUnavailable(f"The path has no point {', '.join(map(str, unknown))}")
    removed = (set(steps.trim.removed) | set(remove or [])) - set(restore or [])
    if len(points - removed) < 2:
        raise StepsUnavailable("At least two points of the path must stay")
    return sorted(removed)


def trim(
    session: Session,
    folder: Path,
    calculation: Calculation,
    remove: list[int] | None = None,
    restore: list[int] | None = None,
) -> TrimResult:
    """D117: takes points out of a scan path or puts them back, in the history. When the
    calculation showed the automatic top on a scan path node, the node and the scan move to the
    new top (`use_frame`); a structure picked with "Use this structure" stays."""
    before = read_steps(folder, calculation)
    removed = removed_after(before, remove, restore)
    old = list(calculation.removed_points or [])
    if removed == old:
        return TrimResult(before, None)
    history.record(session, "calculation", calculation.id, "update", "removed_points", old, removed)
    calculation.removed_points = removed
    assert before.trim is not None
    after = trim_of(before.frames, removed, calculation.geometry)
    moved = None
    # Only in place: on a node with other results the top never makes a derived node by itself.
    follows = before.trim.automatic and changes_in_place(calculation.node)
    if follows and after.top != before.trim.top:
        use_frame(session, folder, calculation, after.top)
        moved = after.top
    session.flush()
    return TrimResult(read_steps(folder, calculation), moved)


def preview_trim(
    folder: Path,
    calculation: Calculation,
    remove: list[int] | None = None,
    restore: list[int] | None = None,
) -> Trim:
    """D117: the path as it would be after `trim`, changing nothing (the cut a selection
    would leave and the new top, before the user confirms)."""
    steps = read_steps(folder, calculation)
    removed = removed_after(steps, remove, restore)
    return trim_of(steps.frames, removed, calculation.geometry)
