"""A folder of outputs surveyed before anything is imported (D263, FR-MCP-23): per file and job
step what it is, and how the files relate to each other and to the investigation's nodes.

Built on the batch import's scan (`batch_import.scan`), with a staging of its own that is
dropped at the end, so the app's staged files are untouched and nothing is written to the
investigation. Structures are compared whatever their atom order
(`structure_compare.same_structure`), pruned first by an order-independent bound, so the
comparisons of a folder of hundreds of outputs take about a second past their parsing.

The answer is read by Claude, so it is kept short: what a file's steps share is said once for
the file, fields that say nothing are left out, and the files' final structures are numbered
(`geometry`): files with the same number end on the same geometry, and `starts_from` names the
geometry a file's first structure is.
"""

from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from chembook3d import xyz
from chembook3d.parsers import crest
from chembook3d.parsers.common import ParsedStep
from chembook3d.services import batch_import, imports, thermo_settings
from chembook3d.services import nodes as node_service
from chembook3d.services import structure_compare as compare
from chembook3d.services.geometry import Rows

LOWEST_REAL = 3  # real frequencies listed when a step has no imaginary one
TITLE = 80  # characters of a title kept
SHARED = (
    "level",
    "formula",
    "atoms",
    "charge",
    "multiplicity",
)  # said once per file when steps agree


@dataclass
class _Shape:
    """One structure to compare: whose it is, its key and its order-independent print."""

    owner: int  # file position, or node position
    rows: Rows
    key: tuple  # formula, charge, multiplicity
    print_: compare.Print


def _shape(owner: int, rows: Rows | None, charge: int | None, multiplicity: int | None):
    if not rows:
        return None
    formula = xyz.formula_of(Counter(r[0] for r in rows))
    return _Shape(owner, rows, (formula, charge, multiplicity), compare.signature(rows))


def _kept(found: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in found.items() if v is not None and v != [] and v != {} and v != ""}


def _hartree(value: float | None) -> float | None:
    return None if value is None else round(float(value), 6)


def _step(session: Session, program: str, step: ParsedStep) -> dict[str, Any]:
    resolved = imports._resolve_level(session, program, step, imports.ImportOptions())
    rows = imports._rows(step.final_geometry) or []
    found: dict[str, Any] = {
        "step": step.index,
        "job": step.job_type,
        "level": imports._level_label(resolved.fields, step),
        "formula": xyz.formula_of(Counter(r[0] for r in rows)) if rows else None,
        "atoms": len(rows) or None,
        "charge": step.charge,
        "multiplicity": step.multiplicity,
        "energy": _hartree(step.scf_energy),
        "converged": step.optimization_converged,
        "termination": None if step.termination == "normal" else step.termination,
        "scan": step.scan,
        "constrained": step.constraints or None,
    }
    if step.frequencies:
        imaginary = [round(f, 1) for f in step.frequencies if f < 0]
        found["g"] = _hartree(step.thermo.get("g"))
        found["imaginary"] = imaginary
        found["imaginary_count"] = len(imaginary)
        if not imaginary:
            found["lowest_real"] = [round(f, 1) for f in sorted(step.frequencies)[:LOWEST_REAL]]
    if step.irc is not None:
        counts = Counter(p.path for p in step.irc.points if p.number > 0)
        found["irc"] = {
            step.irc.directions.get(path, f"path {path}"): _kept(
                {"points": counts.get(path, 0), "ending": step.irc.endings.get(path)}
            )
            for path in sorted(set(step.irc.directions) | set(counts))
        }
    return _kept(found)


def _candidates(shape: _Shape, others: list[_Shape], tolerance: float) -> list[_Shape]:
    """Those of the same formula, charge and multiplicity the bound does not rule out."""
    return [
        o
        for o in others
        if o is not shape
        and o.key == shape.key
        and compare.lower_bound(shape.print_, o.print_) <= tolerance
    ]


def survey(
    session: Session, folder: Path, recursive: bool = True, investigation: Path | None = None
) -> dict[str, Any]:
    """D263: what each output in `folder` is (per step: job type, level, charge, multiplicity,
    formula, atoms, energy, G with frequencies, imaginary frequencies, convergence, IRC
    directions) and how the files relate: the same content or the same final geometry as
    another file (a single point or a restart on an optimization), starting where another
    ended, imported before, or a node's structure. Writes nothing."""
    settings = thermo_settings.settings(session)
    staging = imports.Staging()
    try:
        batch = batch_import.scan(staging, folder, recursive, investigation)
        return _survey(
            session,
            batch,
            settings.geometry_tolerance,  # D252
            settings.duplicate_tolerance,  # D62
        )
    finally:
        staging.clear()


@dataclass
class Final:
    """A file's final structure, as the survey's `geometry` numbers compare them."""

    path: str  # inside the folder, with "/"
    step: int
    rows: Rows
    charge: int | None
    multiplicity: int | None


def final_structures(
    folder: Path, recursive: bool = True, investigation: Path | None = None
) -> list[Final]:
    """D263c: the final structure of each output in `folder` (the last step with one), read
    as the survey reads them; nothing is staged for the app or written."""
    staging = imports.Staging()
    try:
        batch = batch_import.scan(staging, folder, recursive, investigation)
        found = []
        for item in batch.files:
            if isinstance(item.staged.parsed, crest.Ensemble):
                continue
            last = batch_import._final(item.staged)
            rows = imports._rows(last.final_geometry) if last is not None else None
            if last is not None and rows:
                found.append(Final(item.relative, last.index, rows, last.charge, last.multiplicity))
        return found
    finally:
        staging.clear()


def _file(session: Session, staged: imports.StagedFile) -> dict[str, Any]:
    parsed = staged.parsed
    if isinstance(parsed, crest.Ensemble):
        return {
            "program": parsed.program,
            "conformers": len(parsed.conformers),
            "atoms": parsed.atom_count,
        }
    steps = [_step(session, parsed.program, s) for s in parsed.steps]
    title = next((s.title for s in parsed.steps if s.title), None)
    found: dict[str, Any] = {
        "program": parsed.program,
        "version": parsed.version,
        "title": title[:TITLE] if title else None,
        "role": str(imports._suggested_role(parsed.steps)),
    }
    for name in SHARED:  # said once when every step that gives it agrees
        values = {s[name] for s in steps if name in s}
        if len(values) == 1:
            found[name] = values.pop()
            for s in steps:
                s.pop(name, None)
    found["steps"] = steps
    return found


def _survey(
    session: Session, batch: batch_import.Batch, same_geometry: float, duplicate: float
) -> dict[str, Any]:
    files: list[dict[str, Any]] = []
    finals: list[_Shape | None] = []
    firsts: list[_Shape | None] = []
    seen: dict[str, str] = {}
    imported: dict[str, list[dict[str, Any]]] = {}
    for n, item in enumerate(batch.files):
        staged = item.staged
        found: dict[str, Any] = {"path": item.relative, "size": staged.size}
        if staged.checksum in seen:
            found["same_content_as"] = seen[staged.checksum]
        else:
            seen[staged.checksum] = item.relative
        if staged.checksum not in imported:
            imported[staged.checksum] = imports._already_imported(session, staged.checksum)
        found["already_imported"] = [
            {"name": i["original_name"], "nodes": i["nodes"]} for i in imported[staged.checksum]
        ]
        found.update(_file(session, staged))
        files.append(_kept(found))
        final = first = None
        if not isinstance(staged.parsed, crest.Ensemble):
            last = batch_import._final(staged)
            if last is not None:
                rows = imports._rows(last.final_geometry)
                final = _shape(n, rows, last.charge, last.multiplicity)
            start = next((s for s in staged.parsed.steps if s.geometries), None)
            if start is not None:
                rows = imports._rows(start.geometries[0])
                first = _shape(n, rows, start.charge, start.multiplicity)
        finals.append(final)
        firsts.append(first)

    # Final structures numbered: the same number for the same geometry (a single point or a
    # restart on an optimization), whatever the atom order.
    shapes = [s for s in finals if s is not None]
    number: dict[int, int] = {}
    renumbered: set[int] = set()
    count = 0
    for shape in shapes:
        if shape.owner in number:
            continue
        count += 1
        number[shape.owner] = count
        for other in _candidates(shape, shapes, same_geometry):
            if other.owner in number:
                continue
            same = compare.same_structure(
                shape.rows, other.rows, same_geometry, (shape.print_, other.print_)
            )
            if same is not None:
                number[other.owner] = count
                if same.mapping is not None:
                    renumbered.add(other.owner)
    for owner, k in number.items():
        files[owner]["geometry"] = k
        if owner in renumbered:
            files[owner]["renumbered"] = True
    sizes = Counter(number.values())

    # A start where another file ended (a restart or a continuation).
    for first in firsts:
        if first is None:
            continue
        starts = set()
        for other in _candidates(first, shapes, same_geometry):
            if other.owner == first.owner or number[other.owner] in starts:
                continue
            if compare.same_structure(
                other.rows, first.rows, same_geometry, (other.print_, first.print_)
            ):
                starts.add(number[other.owner])
        if starts:
            files[first.owner]["starts_from"] = sorted(starts)

    # A node with the same structure within the duplicate tolerance (FR-IMP-09, D62).
    nodes = [n for n in node_service.list_nodes(session) if n.geometry]
    node_shapes = [
        s
        for k, n in enumerate(nodes)
        if (s := _shape(k, [list(r) for r in n.geometry], n.charge, n.multiplicity)) is not None
    ]
    for shape in shapes:
        matches = []
        for other in node_shapes:
            if other.key[0] != shape.key[0] or not _compatible(other.key, shape.key):
                continue
            if compare.lower_bound(other.print_, shape.print_) > duplicate:
                continue
            same = compare.same_structure(
                other.rows, shape.rows, duplicate, (other.print_, shape.print_)
            )
            if same is None:
                continue
            node = nodes[other.owner]
            entry: dict[str, Any] = {"node_id": node.id, "label": node.label}
            entry["rmsd"] = round(same.rmsd, 4)
            if same.mapping is not None:
                entry["renumbered"] = True
            matches.append(entry)
        if matches:
            files[shape.owner]["matches_node"] = sorted(matches, key=lambda m: m["rmsd"])

    # Levels of theory numbered, as their labels are long and repeat.
    levels: dict[str, int] = {}
    for found in files:
        for holder in [found, *found.get("steps", [])]:
            if "level" in holder:
                holder["level"] = levels.setdefault(holder["level"], len(levels) + 1)

    roles = Counter(f["role"] for f in files if "role" in f)
    return _kept(
        {
            "folder": batch.place,
            "outputs": len(files),
            "other_files": batch.other_count,
            "unreadable": batch.unreadable,
            "roles": dict(roles),
            "geometries": count,
            "shared_geometries": sum(1 for v in sizes.values() if v > 1),
            "tolerances": {"same_geometry": same_geometry, "duplicate": duplicate},
            "levels": list(levels),
            "key": (
                "Energies and G in hartree, frequencies in cm-1. level: the number of its "
                "label in levels, from 1. A file's level, formula, atoms, charge and "
                "multiplicity are its steps' when they agree. geometry: files with "
                "the same number end on the same structure (within the same_geometry tolerance: "
                "a single point or restart on an optimization); renumbered: its atoms are "
                "numbered otherwise than the first file with that number; starts_from: the "
                "geometry numbers its first structure is (a restart or continuation); "
                "matches_node: a node's structure within the duplicate tolerance; role: what "
                "the import would suggest. Nothing was imported."
            ),
            "files": files,
        }
    )


def _compatible(a: tuple, b: tuple) -> bool:
    """Charge and multiplicity agree where both are known (as the import's duplicate check)."""
    return all(x is None or y is None or x == y for x, y in zip(a[1:], b[1:], strict=True))
