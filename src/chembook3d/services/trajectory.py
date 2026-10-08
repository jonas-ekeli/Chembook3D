"""The structures of an optimization or a scan, played as a movie in the 3D view (D101,
FR-3D-08).

Nothing is stored for this: the structures and their energies are read again from the
calculation's copied output file when asked, so calculations imported before D101 have them
too. Each structure is turned onto the calculation's final geometry, so the movie does not
jump about and ends on the structure the node shows.

The structure shown can be taken over as a node's geometry (D112, `use_frame`): in place on a
scan path node, whose calculations are all xTB relaxed scans read from `xtbscan.log` or
`path.xyz`; otherwise on a derived node (ID-4, ID-5).
"""

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

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


class StepsUnavailable(ValueError):
    """The calculation's structures cannot be read; the message says why."""


@dataclass
class Frame:
    rows: list[list[Any]]  # [[element, x, y, z], ...], placed on the final geometry
    energy: float | None  # hartree, the SCF energy computed on this structure
    point: int | None  # the scan point it belongs to (1-based), None outside a scan
    converged: bool  # an optimization (or a scan point's) converged on this structure
    stage: int | None = None  # D112: the stage of a scan path that names its stages


@dataclass
class Steps:
    scan: str | None  # "relaxed", "rigid" or None
    points: int  # how many scan points the file reached; 0 outside a scan
    frames: list[Frame]


@lru_cache(maxsize=4)
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
    return Steps(scan=step.scan, points=len(points), frames=frames)


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
