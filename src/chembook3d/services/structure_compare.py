"""Comparing structures to build a mechanism from a folder (D263, A184).

Three questions Claude asks of the structures a folder of outputs holds, whatever their atom
order:

- `same_structure`: is one structure a duplicate of another (FR-IMP-09, ID-8, D62)? The import's
  duplicate check uses it, so a file numbering its atoms otherwise is still found.
- `compare`: what changes between two structures (bonds formed and broken by A181's rule, the
  atom-matched RMSD, the distances that change most) and, with a transition state, whether the
  changing bonds lie along its imaginary mode; with a third structure, A184's check of a TS
  linking two minima.
- `group`: duplicates, conformers and isomers among many structures.

Structures come as references (`resolve`): a node, a calculation (a frame or an IRC end of its
file), a step of an output file on this computer, or XYZ text. A side may be a list of them: a
sum of species, placed far apart. Atom numbers outside this module are 1-based (D75).
"""

import math
from collections import Counter
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

import numpy as np
from scipy.optimize import linear_sum_assignment
from sqlalchemy.orm import Session

from chembook3d import xyz
from chembook3d.models import Calculation, Node
from chembook3d.parsers.common import ParsedFile, ParsedStep
from chembook3d.pathtools import METALS
from chembook3d.services import atom_matching, geometry, imports, inspection, levels, trajectory
from chembook3d.services.geometry import Rows

APART = 15.0  # Å: each further species of a sum sits this far beyond the ones before
TS_REACH = 1.8  # × the covalent radii: pairs a TS's mode can make or break (D114's reach)
ACTIVE_SHARE = 0.5  # an active pair changes at least this share of the fastest along the mode
MOVED = 0.05  # Å: a distance that changed less is not counted when judging the side of a mode
TOP_CHANGES = 8  # the distance changes listed
LINK_BONDS = (1, 3)  # A184: a TS links two minima when one to three bonds change between them
END_RMSD = 0.5  # A184: an end matches a structure within this atom-matched RMSD (Å)
MAX_GROUP = 300  # structures grouped in one call


class StructureError(ValueError):
    """A reference that cannot be read, or a request that cannot be answered; says why."""


# ---------- structures ----------


@dataclass
class Structure:
    label: str
    rows: Rows
    charge: int | None = None
    multiplicity: int | None = None
    frequencies: list[float] | None = None
    # [mode][atom][xyz] in the rows' order, and the geometry they belong to (the same atoms;
    # a frequency job's own coordinates, which may sit turned against `rows`).
    modes: list[Any] | None = None
    mode_rows: Rows | None = None
    species: list[int] | None = None  # per atom, its species in a sum (None: one species)

    @property
    def parts(self) -> int:
        return 1 + max(self.species) if self.species else 1

    @property
    def imaginary(self) -> list[float]:
        return [f for f in self.frequencies or [] if f < 0]

    @property
    def formula(self) -> str:
        return xyz.formula_of(Counter(row[0] for row in self.rows))

    def describe(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "label": self.label,
            "formula": self.formula,
            "atoms": len(self.rows),
            "charge": self.charge,
            "multiplicity": self.multiplicity,
        }
        if self.parts > 1:
            out["species"] = self.parts
        if self.frequencies:
            out["imaginary"] = [round(f, 1) for f in self.imaginary]
        return {k: v for k, v in out.items() if v is not None}


def _rows(rows: Any) -> Rows:
    return [[r[0], float(r[1]), float(r[2]), float(r[3])] for r in rows]


def _with_modes(
    structure: Structure, frequencies: list[float] | None, modes: list[Any] | None, rows: Rows
) -> Structure:
    """Frequencies kept; the modes too when they fit the atoms."""
    structure.frequencies = list(frequencies or []) or None
    if (
        modes
        and frequencies
        and len(modes) == len(frequencies)
        and all(len(m) == len(rows) for m in modes)
    ):
        structure.modes = modes
        structure.mode_rows = rows
    return structure


def _frequency_job(node: Node, tolerance: float) -> Calculation | None:
    """The node's frequency calculation at its geometry (within `tolerance`), one with
    exactly one imaginary frequency first, then the latest."""
    found = []
    for calc in node.calculations:
        result = calc.result
        if result is None or not result.frequencies or not calc.geometry:
            continue
        if not geometry.matches(calc.geometry, node.geometry, tolerance):
            continue
        found.append(calc)
    if not found:
        return None
    found.sort(key=lambda c: (c.result.imaginary_count != 1, -c.created_at.timestamp()))
    return found[0]


def _frame(rows_of: list[Rows], frame: int | None, what: str) -> Rows:
    number = frame if frame is not None and frame > 0 else len(rows_of) + 1 + (frame or -1)
    if not 1 <= number <= len(rows_of):
        raise StructureError(f"{what} has {len(rows_of)} structures; frame {frame} is not one")
    return rows_of[number - 1]


def _irc_end(step: ParsedStep, direction: str, what: str) -> Rows:
    run = step.irc
    if run is None:
        raise StructureError(f"{what} is no IRC, so it has no {direction} end")
    paths = [p for p, d in run.directions.items() if d == direction]
    points = [p for p in run.points if p.path in paths and p.number > 0]
    if not points:
        have = sorted(set(run.directions.values()))
        raise StructureError(
            f"{what} has no {direction} path" + (f"; it has {' and '.join(have)}" if have else "")
        )
    last = max(points, key=lambda p: p.number)
    return _rows([[a.element, a.x, a.y, a.z] for a in step.geometries[last.structure]])


def _from_step(
    step: ParsedStep, what: str, frame: int | None, irc_end: str | None, label: str
) -> Structure:
    found = Structure(label, [], step.charge, step.multiplicity)
    if irc_end:
        found.rows = _irc_end(step, irc_end, what)
        found.label += f", {irc_end} end"
        return found
    if not step.geometries:
        raise StructureError(f"{what} has no structures")
    final = imports._rows(step.final_geometry) or []
    frames = trajectory.frames_of(step)
    if frame is None or frame == -1 and step.node_geometry is None:
        found.rows = _rows(final)
        return _with_modes(found, step.frequencies, step.normal_modes, found.rows)
    found.rows = _rows(_frame([f.rows for f in frames], frame, what))
    found.label += f", frame {frame}"
    return found


def _parsed(path: Path) -> ParsedFile | inspection.Inspection:
    """The file as the import reads it (cached), else as Inspect reads an xyz file or CIF."""
    try:
        stat = path.stat()
    except OSError as exc:
        raise StructureError(f"Cannot read {path}") from exc
    if stat.st_size > inspection.MAX_SIZE:
        raise StructureError(f"{path.name} is too large to read")
    try:
        parsed = trajectory._read(str(path), stat.st_mtime_ns, stat.st_size)
    except imports.ImportFailed:
        parsed = None
    except OSError as exc:
        raise StructureError(f"Cannot read {path}") from exc
    if parsed is not None:
        return parsed
    try:
        return inspection.read_path(path)
    except (inspection.InspectFailed, imports.ImportFailed) as exc:
        raise StructureError(str(exc)) from exc


def _from_file(ref: dict[str, Any]) -> Structure:
    try:
        path = Path(str(ref["path"])).expanduser().resolve()
    except OSError as exc:
        raise StructureError(f"Cannot read {ref['path']}") from exc
    if not path.is_file():
        raise StructureError(f"{path} is not a file")
    parsed = _parsed(path)
    number = ref.get("step")
    if isinstance(parsed, ParsedFile):
        with_geometry = [s for s in parsed.steps if s.geometries]
        if number is None:
            if not with_geometry:
                raise StructureError(f"{path.name} holds no structures")
            step = with_geometry[-1]
        else:
            step = next((s for s in parsed.steps if s.index == number), None)
            if step is None:
                raise StructureError(f"{path.name} has no step {number}")
        what = f"{path.name} step {step.index}"
        return _from_step(step, what, ref.get("frame"), ref.get("irc_end"), what)
    # An xyz file, a CREST ensemble or a CIF, as Inspect reads it: no modes, no charge.
    steps = [s for s in parsed.steps if s.frames]
    if not steps:
        raise StructureError(f"{path.name} holds no structures")
    chosen = steps[-1] if number is None else next((s for s in steps if s.index == number), None)
    if chosen is None:
        raise StructureError(f"{path.name} has no step {number} with structures")
    if ref.get("irc_end"):
        raise StructureError(f"{path.name} is no IRC output")
    what = f"{path.name} step {chosen.index}"
    rows = _frame([f.rows for f in chosen.frames], ref.get("frame"), what)
    label = what + (f", frame {ref['frame']}" if ref.get("frame") not in (None, -1) else "")
    return Structure(label, _rows(rows), chosen.charge, chosen.multiplicity)


def _from_calculation(session: Session, folder: Path, ref: dict[str, Any]) -> Structure:
    from chembook3d.services import irc  # it imports this module's neighbours

    calc = session.get(Calculation, str(ref["calculation_id"]))
    if calc is None:
        raise StructureError("No calculation has that id")
    name = calc.node.label or "Untitled node"
    level = levels.label(calc.level)
    label = f"{name}: {calc.type.replace('_', ' ')}" + (f" at {level}" if level else "")
    if ref.get("frame") is None and not ref.get("irc_end"):
        if not calc.geometry:
            raise StructureError(f"The {label} calculation has no coordinates")
        found = Structure(label, _rows(calc.geometry), calc.charge, calc.multiplicity)
        result = calc.result
        if result is not None:
            _with_modes(found, result.frequencies, result.normal_modes or None, found.rows)
        return found
    read = irc._read_step(folder, calc)
    if read is None:
        raise StructureError(f"The file of {label} cannot be read")
    step, original = read
    return _from_step(
        step, f"{original} step {step.index}", ref.get("frame"), ref.get("irc_end"), label
    )


def resolve(
    session: Session | None, folder: Path | None, ref: dict[str, Any], tolerance: float
) -> Structure:
    """One reference, named `label` when it gives one; its `charge` and `multiplicity`, when
    given, stand for what the source says (XYZ text says nothing)."""
    found = _resolve(session, folder, ref, tolerance)
    if ref.get("label"):
        found.label = str(ref["label"])
    for name in ("charge", "multiplicity"):
        if ref.get(name) is not None:
            setattr(found, name, int(ref[name]))
    return found


def _resolve(
    session: Session | None, folder: Path | None, ref: dict[str, Any], tolerance: float
) -> Structure:
    """One reference: {node_id}, {calculation_id, frame?, irc_end?}, {path, step?, frame?,
    irc_end?} or {xyz}. `frame` is 1-based (-1 or left out: the last); `irc_end` "forward" or
    "reverse" is the last point of that direction of an IRC. `tolerance`: a node's frequency
    job is at its geometry within it."""
    given = [k for k in ("node_id", "calculation_id", "path", "xyz") if ref.get(k)]
    if len(given) != 1:
        raise StructureError(
            "Each structure names one of node_id, calculation_id, path or xyz"
            + (f" (this one names {', '.join(given)})" if given else "")
        )
    if ref.get("irc_end") not in (None, "", "forward", "reverse"):
        raise StructureError("irc_end is 'forward' or 'reverse'")
    kind = given[0]
    if kind == "xyz":
        try:
            atoms = xyz.parse_xyz(str(ref["xyz"]))
        except xyz.XyzParseError as exc:
            raise StructureError(f"The XYZ text cannot be read: {exc}") from exc
        if not atoms:
            raise StructureError("The XYZ text holds no atoms")
        return Structure("XYZ text", _rows([[a.element, a.x, a.y, a.z] for a in atoms]))
    if kind == "path":
        return _from_file(ref)
    if session is None or folder is None:
        raise StructureError("Nodes and calculations need an open investigation")
    if kind == "calculation_id":
        return _from_calculation(session, folder, ref)
    node = session.get(Node, str(ref["node_id"]))
    if node is None:
        raise StructureError("No node has that id")
    label = node.label or "Untitled node"
    if not node.geometry:
        raise StructureError(f"“{label}” has no coordinates")
    found = Structure(label, _rows(node.geometry), node.charge, node.multiplicity)
    job = _frequency_job(node, tolerance)
    if job is not None and len(job.geometry) == len(found.rows):
        _with_modes(
            found, job.result.frequencies, job.result.normal_modes or None, _rows(job.geometry)
        )
    return found


def summed(parts: list[Structure]) -> Structure:
    """A sum of species: their rows one after another, each further species moved APART Å
    beyond the ones before so no bond joins them. Charges add; the multiplicity is known when
    at most one species is not a singlet."""
    if len(parts) == 1:
        return parts[0]
    rows: Rows = []
    species: list[int] = []
    edge = None
    for n, part in enumerate(parts):
        coords = np.array([r[1:4] for r in part.rows], dtype=float)
        if edge is not None:
            coords = coords - coords.min(axis=0) + np.array([edge + APART, 0.0, 0.0])
            coords[:, 1:] -= coords[:, 1:].mean(axis=0)
        edge = float(coords[:, 0].max()) if edge is None else max(edge, float(coords[:, 0].max()))
        rows += [[r[0], *map(float, c)] for r, c in zip(part.rows, coords, strict=True)]
        species += [n] * len(part.rows)
    charges = [p.charge for p in parts]
    multiplicities = [p.multiplicity for p in parts]
    multiplicity = None
    if all(m is not None for m in multiplicities):
        open_shell = [m for m in multiplicities if m != 1]
        multiplicity = open_shell[0] if len(open_shell) == 1 else 1 if not open_shell else None
    return Structure(
        " + ".join(p.label for p in parts),
        rows,
        sum(c for c in charges if c is not None) if None not in charges else None,
        multiplicity,
        species=species,
    )


def side(session: Session | None, folder: Path | None, ref: Any, tolerance: float) -> Structure:
    """A reference, or a list of them: a sum of species."""
    if isinstance(ref, list):
        if not ref:
            raise StructureError("A sum of species lists at least one structure")
        return summed([resolve(session, folder, r, tolerance) for r in ref])
    return resolve(session, folder, ref, tolerance)


# ---------- duplicates, order independent ----------


@dataclass
class Print:
    """An order-independent print of a structure's shape whose difference bounds the fitted
    RMSD of any numbering from below (`lower_bound`), so most pairs are ruled out without
    matching atoms."""

    radial: np.ndarray  # per element (sorted), the distances to the centroid, sorted
    pairs: np.ndarray  # per pair of elements (sorted), the distances between them, sorted


def signature(rows: Rows) -> Print:
    """The structure's print, kept for the last structures asked about (the import's duplicate
    check asks for every node's at each file)."""
    return _signature(tuple((r[0], float(r[1]), float(r[2]), float(r[3])) for r in rows))


@lru_cache(maxsize=2048)
def _signature(rows: tuple) -> Print:
    coords = np.array([r[1:4] for r in rows], dtype=float)
    elements = np.array([r[0] for r in rows])
    kinds = sorted(set(elements.tolist()))
    radial = np.linalg.norm(coords - coords.mean(axis=0), axis=1)
    distances = np.linalg.norm(coords[:, None, :] - coords[None, :, :], axis=2)
    upper = np.triu(np.ones(distances.shape, dtype=bool), k=1)
    pairs = []
    for x, e in enumerate(kinds):
        for f in kinds[x:]:
            a, b = elements == e, elements == f
            chosen = (
                (a[:, None] & b[None, :]) | (b[:, None] & a[None, :])
                if e != f
                else (a[:, None] & b[None, :])
            )
            pairs.append(np.sort(distances[chosen & upper]))
    return Print(
        np.concatenate([np.sort(radial[elements == e]) for e in kinds]),
        np.concatenate(pairs) if pairs else np.zeros(0),
    )


def lower_bound(a: Print, b: Print) -> float:
    """A bound below the fitted RMSD r of two structures of the same atoms in any numbering.
    After centring, |R·a_i − b_j| ≥ |‖a_i‖ − ‖b_j‖|; and an interatomic distance changes by at
    most the two atoms' displacements, so the RMS of the distance changes over all pairs is at
    most 2r. Pairing sorted values element by element (or pair of elements) gives the smallest
    sum of squares (rearrangement inequality), so both stay bounds whatever the numbering."""
    radial = float(np.sqrt(((a.radial - b.radial) ** 2).mean()))
    if not a.pairs.size:
        return radial
    return max(radial, float(np.sqrt(((a.pairs - b.pairs) ** 2).mean())) / 2)


def same_atoms(a: Rows, b: Rows) -> bool:
    return len(a) == len(b) and Counter(r[0] for r in a) == Counter(r[0] for r in b)


@dataclass
class Same:
    rmsd: float
    mapping: list[int] | None  # b's atom for each of a's (0-based); None: the same numbering


def same_structure(
    a: Rows,
    b: Rows,
    tolerance: float,
    prints: tuple[Print, Print] | None = None,
    thorough: bool = True,
) -> Same | None:
    """Whether b is the same structure as a within `tolerance` (Å, fitted RMSD), whatever the
    order of its atoms (D263), with no real bond change (A181). The same numbering is tried
    first; another only when the order-independent bound allows one: by position, from the
    same numbering and the principal axes, and through Match atoms when the axes are
    ill-defined (a symmetric top) unless not `thorough` (b already numbered as a, but for
    like atoms swapped)."""
    if not same_atoms(a, b):
        return None
    n = len(a)
    if geometry.elements(a) == geometry.elements(b):
        rmsd = geometry.aligned_rmsd(a, b)
        if rmsd is not None and rmsd <= tolerance:
            return Same(rmsd, None)
        # Another numbering of the same structure moves two or more atoms that lie at least
        # ~0.7 Å apart onto each other, which the same numbering's RMSD would show.
        if rmsd is not None and rmsd + tolerance < 0.7 * math.sqrt(2 / n):
            return None
    pa, pb = prints or (signature(a), signature(b))
    if lower_bound(pa, pb) > tolerance:
        return None
    found = _by_position(a, b)
    if found[0] > tolerance and thorough and _symmetric_top(a):
        # Principal axes do not fix the orientation of a symmetric top: match atoms instead.
        try:
            matched = atom_matching.match(a, b)
        except Exception:  # noqa: BLE001 - a match that cannot be made finds no duplicate
            return None
        found = (matched.rmsd, matched.mapping)
    rmsd, mapping = found
    if rmsd > tolerance:
        return None
    formed, broken = atom_matching.changes(a, b, mapping)
    real_formed, real_broken, _, _ = atom_matching.split_changes(a, b, mapping, formed, broken)
    if real_formed or real_broken:
        return None
    identity = all(i == j for i, j in enumerate(mapping))
    return Same(rmsd, None if identity else mapping)


def _metal_hydrogen(rows: Rows, pair: tuple[int, int]) -> bool:
    kinds = {rows[pair[0]][0], rows[pair[1]][0]}
    return "H" in kinds and bool(kinds & METALS)


def split(
    a: Rows, b: Rows, mapping: list[int] | None, formed: list, broken: list
) -> tuple[list, list, list, list]:
    """A181's real and weak bond changes (`atom_matching.split_changes`), with a contact between
    a metal and a hydrogen always weak (A184: weak metal contacts left out): an agostic C–H that
    leans on the metal in one conformer and not in another changes no bond that counts, and a
    real hydride also breaks the bond to the atom the hydrogen leaves, which still counts."""
    rf, rb, wf, wb = atom_matching.split_changes(a, b, mapping, formed, broken)
    return (
        [p for p in rf if not _metal_hydrogen(a, p)],
        [p for p in rb if not _metal_hydrogen(a, p)],
        wf + [p for p in rf if _metal_hydrogen(a, p)],
        wb + [p for p in rb if _metal_hydrogen(a, p)],
    )


SEEDS = 4  # the proper ways of laying principal axes on each other


def _assigned(
    p: np.ndarray, placed: np.ndarray, elements: list[str], others: list[str]
) -> list[int]:
    """Each atom paired with the nearest free one of its element (Hungarian assignment)."""
    mapping = [0] * len(elements)
    for element in sorted(set(elements)):
        rows = [i for i, e in enumerate(elements) if e == element]
        cols = [j for j, e in enumerate(others) if e == element]
        diff = p[rows][:, None, :] - placed[cols][None, :, :]
        found = linear_sum_assignment((diff * diff).sum(axis=2))
        for r, c in zip(*found, strict=True):
            mapping[rows[r]] = cols[c]
    return mapping


def _by_position(a: Rows, b: Rows) -> tuple[float, list[int]]:
    """The numbering of b that lies best on a by position alone, from the same numbering (when
    the elements line up) and the principal axes, each refitted and paired again until
    stable: for a duplicate this is its numbering, at a fraction of Match atoms' cost."""
    p = np.array([r[1:4] for r in a], dtype=float)
    q = np.array([r[1:4] for r in b], dtype=float)
    elements, others = geometry.elements(a), geometry.elements(b)
    starts = []
    if elements == others:
        starts.append(atom_matching._placed(q, p, {i: i for i in range(len(a))}))
    starts += atom_matching._seed_frames(q, p)[:SEEDS]
    best: tuple[float, list[int]] | None = None
    for placed in starts:
        mapping = _assigned(p, placed, elements, others)
        for _ in range(atom_matching.MAX_ITERATIONS):
            placed = atom_matching._placed(q, p, dict(enumerate(mapping)))
            again = _assigned(p, placed, elements, others)
            if again == mapping:
                break
            mapping = again
        rmsd = float(np.sqrt(((placed[mapping] - p) ** 2).sum(axis=1).mean()))
        if best is None or rmsd < best[0]:
            best = (rmsd, mapping)
    assert best is not None
    return best


def _symmetric_top(rows: Rows) -> bool:
    """Two neighbouring principal moments (unweighted) within 5 % of each other."""
    coords = np.array([r[1:4] for r in rows], dtype=float)
    centred = coords - coords.mean(axis=0)
    values = np.sort(np.linalg.eigvalsh(centred.T @ centred))[::-1]
    if values[0] <= 0:
        return True
    return bool(
        values[0] - values[1] < 0.05 * values[0] or values[1] - values[2] < 0.05 * values[1]
    )


# ---------- comparing two structures ----------


def _bond(rows: Rows, i: int, j: int) -> str:
    return f"{rows[i][0]}{i + 1}–{rows[j][0]}{j + 1}"


def _distances(rows: Rows) -> np.ndarray:
    coords = np.array([r[1:4] for r in rows], dtype=float)
    return np.linalg.norm(coords[:, None, :] - coords[None, :, :], axis=2)


def _reach(rows: Rows) -> np.ndarray:
    radii = np.array([atom_matching.RADII.get(r[0], atom_matching.DEFAULT_RADIUS) for r in rows])
    return TS_REACH * (radii[:, None] + radii[None, :])


def _apart(structure: Structure, mapping: list[int] | None = None) -> np.ndarray | None:
    """Pairs (in the numbering `mapping` takes the structure to) of two species of a sum."""
    if not structure.species:
        return None
    species = np.array(structure.species)
    if mapping is not None:
        species = species[mapping]
    return species[:, None] != species[None, :]


def _round(x: float | None, digits: int = 3) -> float | None:
    return None if x is None else round(float(x), digits)


@dataclass
class _Mode:
    wavenumber: float
    active: list[tuple[int, int, float]]  # (i, j, signed share of the fastest change)


def _active(structure: Structure, order: list[int] | None = None) -> _Mode | None:
    """D263: the atom pairs whose distance changes fastest along the TS's imaginary mode
    (within TS_REACH in the TS, at least ACTIVE_SHARE of the fastest), in the numbering
    `order` takes the TS to (order[i]: the TS's atom for atom i)."""
    if not structure.imaginary or structure.modes is None or structure.mode_rows is None:
        return None
    frequencies = structure.frequencies or []
    index = min(range(len(frequencies)), key=lambda k: frequencies[k])
    rows = structure.mode_rows
    u = np.array(structure.modes[index], dtype=float)
    x = np.array([r[1:4] for r in rows], dtype=float)
    if order is not None:
        u, x, rows = u[order], x[order], [rows[k] for k in order]
    norm = float(np.linalg.norm(u))
    if norm == 0:
        return None
    u = u / norm
    diff = x[:, None, :] - x[None, :, :]
    d = np.linalg.norm(diff, axis=2)
    np.fill_diagonal(d, np.inf)
    rate = ((diff * (u[:, None, :] - u[None, :, :])).sum(axis=2)) / d
    near = np.triu(d < _reach(rows), k=1)
    rates = np.where(near, np.abs(rate), 0.0)
    fastest = float(rates.max()) if rates.size else 0.0
    if fastest <= 0:
        return None
    pairs = np.argwhere(rates >= ACTIVE_SHARE * fastest)
    active = sorted(
        ((int(i), int(j), float(rate[i, j] / fastest)) for i, j in pairs),
        key=lambda p: -abs(p[2]),
    )
    return _Mode(float(frequencies[index]), active)


def _side(
    mode: _Mode, ts_rows: Rows, other_rows: Rows, apart: np.ndarray | None
) -> tuple[str, list[dict[str, Any]]]:
    """Which way along the mode the other structure lies from the TS: "+" when every active
    pair that changed (by MOVED or more) changed as the mode's + direction moves it, "−" when
    every one changed the other way, "mixed" or "none" (nothing moved)."""
    d_ts, d_other = _distances(ts_rows), _distances(other_rows)
    pairs, signs = [], set()
    for i, j, share in mode.active:
        separate = apart is not None and bool(apart[i, j])
        change = None if separate else float(d_other[i, j] - d_ts[i, j])
        row: dict[str, Any] = {
            "bond": _bond(ts_rows, i, j),
            "ts": _round(d_ts[i, j]),
            "other": None if separate else _round(d_other[i, j]),
            "mode": "lengthens" if share > 0 else "shortens",
        }
        if separate:  # two species of a sum: far apart, so the pair lengthened
            change = math.inf
            row["other"] = "separate species"
        if abs(change) >= MOVED:
            sign = "+" if (change > 0) == (share > 0) else "−"
            signs.add(sign)
            row["side"] = sign
        pairs.append(row)
    if not signs:
        return "none", pairs
    return (signs.pop() if len(signs) == 1 else "mixed"), pairs


def _pairs_text(rows: Rows, pairs: list[tuple[int, int]]) -> str:
    return ", ".join(_bond(rows, i, j) for i, j in pairs)


def _formula_difference(a: Structure, b: Structure) -> str:
    ca = Counter(r[0] for r in a.rows)
    cb = Counter(r[0] for r in b.rows)
    more_b, more_a = cb - ca, ca - cb
    parts = []
    if more_b:
        parts.append(f"B has {xyz.formula_of(more_b)} more")
    if more_a:
        parts.append(f"A has {xyz.formula_of(more_a)} more")
    return "; ".join(parts) or "the same atoms"


def compare(
    a: Structure,
    b: Structure,
    ts: Structure | None = None,
    count_weak: bool = False,
    tolerance: float = 0.05,
) -> dict[str, Any]:
    """D263: B matched onto A's numbering and what changes (see the module's docstring).
    `tolerance` is the investigation's duplicate tolerance (D62)."""
    out: dict[str, Any] = {"a": a.describe(), "b": b.describe()}
    notes: list[str] = []
    out["notes"] = notes
    if not same_atoms(a.rows, b.rows):
        out["same_atoms"] = False
        out["difference"] = _formula_difference(a, b)
        notes.append(
            f"A is {a.formula} and B is {b.formula} ({out['difference']}): they are not the "
            "same atoms, so no atoms are matched. Add the missing species to a side as a list."
        )
        return out
    out["same_atoms"] = True
    if None not in (a.charge, b.charge) and a.charge != b.charge:
        notes.append(f"A has charge {a.charge} and B {b.charge}.")
    if None not in (a.multiplicity, b.multiplicity) and a.multiplicity != b.multiplicity:
        notes.append(f"A has multiplicity {a.multiplicity} and B {b.multiplicity}.")
    match = atom_matching.match(a.rows, b.rows)
    mapping = match.mapping
    formed, broken, weak_formed, weak_broken = split(
        a.rows, b.rows, mapping, match.formed, match.broken
    )
    if count_weak:
        formed, broken = formed + weak_formed, broken + weak_broken
        weak_formed, weak_broken = [], []
    b_in_a = [b.rows[j] for j in mapping]
    d_a, d_b = _distances(a.rows), _distances(b_in_a)
    apart_a, apart_b = _apart(a), _apart(b, mapping)
    summed_side = a.parts > 1 or b.parts > 1
    rmsd = None if summed_side else match.rmsd

    def distance(d: np.ndarray, apart: np.ndarray | None, i: int, j: int) -> float | str | None:
        if apart is not None and apart[i, j]:
            return "separate species"
        return _round(d[i, j])

    def bond_rows(pairs: list[tuple[int, int]]) -> list[dict[str, Any]]:
        return [
            {
                "atoms": [i + 1, j + 1],
                "bond": _bond(a.rows, i, j),
                "a": distance(d_a, apart_a, i, j),
                "b": distance(d_b, apart_b, i, j),
            }
            for i, j in pairs
        ]

    renumbered = not all(i == j for i, j in enumerate(mapping))
    out.update(
        {
            "mapping": [j + 1 for j in mapping],
            "renumbered": renumbered,
            "rmsd": _round(rmsd),
            "formed": bond_rows(formed),
            "broken": bond_rows(broken),
        }
    )
    if weak_formed or weak_broken:
        out["weak"] = [{**r, "change": "formed"} for r in bond_rows(weak_formed)] + [
            {**r, "change": "broken"} for r in bond_rows(weak_broken)
        ]

    # The distances that change most, among pairs within reach in either structure.
    reach = _reach(a.rows)
    within = (d_a < reach) | (d_b < reach)
    for apart in (apart_a, apart_b):
        if apart is not None:
            within &= ~apart
    delta = np.where(np.triu(within, k=1), np.abs(d_b - d_a), 0.0)
    order = np.dstack(np.unravel_index(np.argsort(-delta, axis=None), delta.shape))[0]
    largest = []
    for i, j in order[:TOP_CHANGES]:
        if delta[i, j] < 0.05:
            break
        largest.append(
            {
                "bond": _bond(a.rows, int(i), int(j)),
                "a": _round(d_a[i, j]),
                "b": _round(d_b[i, j]),
                "change": _round(d_b[i, j] - d_a[i, j]),
            }
        )
    out["largest_changes"] = largest

    same_molecule = not formed and not broken
    out["same_molecule"] = same_molecule
    out["same_conformer"] = bool(same_molecule and rmsd is not None and rmsd <= tolerance)
    if rmsd is not None:
        out["end_match"] = bool(same_molecule and rmsd < END_RMSD)  # A184
    if match.doubts:
        out["doubts"] = match.doubts

    # Words.
    if renumbered:
        notes.append("B numbers its atoms otherwise; `mapping` gives B's atom for each of A's.")
    if same_molecule:
        if out["same_conformer"]:
            notes.append(
                f"Same bonds and {rmsd:.3f} Å apart after matching atoms: a duplicate (within "
                f"the duplicate tolerance of {tolerance} Å)."
            )
        elif rmsd is not None:
            notes.append(
                f"Same bonds, {rmsd:.3f} Å apart after matching atoms: "
                + (
                    "the same molecule in another conformation."
                    if rmsd >= END_RMSD
                    else "the same molecule, close in shape (an IRC end would match it, A184)."
                )
            )
        else:
            notes.append("Same bonds.")
    else:
        changed = []
        if formed:
            changed.append(f"{_pairs_text(a.rows, formed)} formed")
        if broken:
            changed.append(f"{_pairs_text(a.rows, broken)} broken")
        notes.append(
            f"From A to B: {'; '.join(changed)} (A181: within 1.2 × the covalent radii on "
            "one side, beyond 1.5 × on the other)."
        )
    if out.get("weak"):
        notes.append(
            f"{len(out['weak'])} weak contact(s) change between 1.2 × and 1.5 × the covalent "
            "radii (such as a metal–olefin contact); they are not counted as bond changes"
            + (" here" if not count_weak else "")
            + "."
        )
    if summed_side:
        notes.append("No RMSD: a sum of species has no shape of its own.")

    # The TS's mode, when A or B is one.
    for name, structure, other_rows, order, apart in (
        ("A", a, b_in_a, None, apart_b),
        ("B", b, a.rows, mapping, apart_a),
    ):
        mode = _active(structure, order)
        if mode is None:
            continue
        ts_rows = a.rows if name == "A" else b_in_a
        side_name, pairs = _side(mode, ts_rows, other_rows, apart)
        active = {(i, j) for i, j, _ in mode.active}
        # A TS's forming and breaking bonds are partial, so weak changes count here too.
        changed = formed + broken + weak_formed + weak_broken
        along = all(p in active for p in changed) if changed else None
        other = "B" if name == "A" else "A"
        out["mode"] = {
            "ts": name,
            "wavenumber": _round(mode.wavenumber, 1),
            "active": [
                {"bond": _bond(a.rows, i, j), "share": round(s, 2)} for i, j, s in mode.active
            ],
            "side": side_name,
            "pairs": pairs,
            "changes_along_mode": along,
        }
        if len(structure.imaginary) > 1:
            notes.append(
                f"{name} has {len(structure.imaginary)} imaginary frequencies; the lowest is used."
            )
        notes.append(
            f"{name}'s imaginary mode ({mode.wavenumber:.0f} cm⁻¹) moves mostly "
            f"{', '.join(_bond(a.rows, i, j) for i, j, _ in mode.active[:4])}; {other} lies "
            + {
                "+": "on its + side.",
                "−": "on its − side.",
                "mixed": "neither way clearly (its active distances change both ways).",
                "none": "where the active distances are as in the TS.",
            }[side_name]
        )
        if along is True:
            notes.append(
                "Every bond that changes between A and B (weak ones included) lies along the mode."
            )
        elif along is False:
            off = [p for p in changed if p not in active]
            notes.append(f"{_pairs_text(a.rows, off)} change(s) but not along the mode.")
        break

    if ts is not None:
        out["ts_check"] = _links(a, b, ts, mapping, b_in_a, formed + broken, count_weak)
        notes.append(out["ts_check"]["summary"])
    return out


def _links(
    a: Structure,
    b: Structure,
    ts: Structure,
    mapping: list[int],
    b_in_a: Rows,
    changed: list[tuple[int, int]],
    count_weak: bool,
) -> dict[str, Any]:
    """A184: whether the TS links minima A and B: one to three bonds change between them,
    each along the TS's imaginary mode, and A and B lie on opposite sides of it."""
    reasons: list[str] = []
    found: dict[str, Any] = {"ts": ts.describe()}
    if not same_atoms(a.rows, ts.rows):
        found.update(verdict="unclear", reasons=["The TS does not hold the same atoms as A."])
        found["summary"] = "TS check: " + found["reasons"][0]
        return found
    if len(ts.imaginary) != 1:
        reasons.append(
            f"The TS has {len(ts.imaginary)} imaginary frequencies"
            + ("" if ts.frequencies else " (no frequency job found)")
            + ", not one."
        )
    for name, s in (("A", a), ("B", b)):
        if s.imaginary:
            reasons.append(f"{name} has an imaginary frequency, so it is not a minimum.")
    ts_match = atom_matching.match(a.rows, ts.rows)
    ts_in_a = [ts.rows[j] for j in ts_match.mapping]
    mode = _active(ts, ts_match.mapping)
    if ts_match.doubts:
        reasons.append("The TS's atom match is doubtful: " + "; ".join(ts_match.doubts) + ".")
    verdict = "links"
    count = len(changed)
    if not LINK_BONDS[0] <= count <= LINK_BONDS[1]:
        verdict = "does not link"
        reasons.append(
            f"{count} bonds change between A and B; a single step changes one to three (A184)."
        )
    if mode is None:
        found["verdict"] = "unclear" if verdict == "links" else verdict
        reasons.append("The TS has no imaginary mode to check the bonds against.")
        found["reasons"] = reasons
        found["summary"] = f"TS check: {found['verdict']}. " + " ".join(reasons)
        return found
    active = {(i, j) for i, j, _ in mode.active}
    off = [p for p in changed if p not in active]
    if off:
        verdict = "does not link"
        reasons.append(
            f"{_pairs_text(a.rows, off)} change(s) between A and B but not along the TS's mode."
        )
    side_a, pairs_a = _side(mode, ts_in_a, a.rows, _apart(a))
    side_b, pairs_b = _side(mode, ts_in_a, b_in_a, _apart(b, mapping))
    found.update(
        {
            "wavenumber": _round(mode.wavenumber, 1),
            "active": [
                {"bond": _bond(a.rows, i, j), "share": round(s, 2)} for i, j, s in mode.active
            ],
            "changed": [_bond(a.rows, i, j) for i, j in changed],
            "side_a": side_a,
            "side_b": side_b,
            "pairs_a": pairs_a,
            "pairs_b": pairs_b,
        }
    )
    if {side_a, side_b} == {"+", "−"}:
        pass
    elif side_a == side_b and side_a in ("+", "−"):
        verdict = "does not link"
        reasons.append(f"A and B both lie on the TS mode's {side_a} side.")
    elif verdict == "links":
        verdict = "unclear"
        reasons.append(f"A lies on the {side_a} side of the mode and B on the {side_b} side.")
    if verdict == "links" and reasons:
        verdict = "unclear"
    if verdict == "links":
        reasons.append(
            f"{count} bond(s) change between A and B ({_pairs_text(a.rows, changed)}), each "
            f"along the TS's imaginary mode, and A and B lie on its opposite sides (A184)."
        )
    found["verdict"] = verdict
    found["reasons"] = reasons
    found["summary"] = f"TS check: {verdict}. " + " ".join(reasons)
    return found


# ---------- grouping many structures ----------


def fingerprint(rows: Rows, table: dict) -> tuple[int, ...]:
    """The connectivity without metal bonds, whose contacts vary, as an order-independent print
    (Weisfeiler–Lehman labels after the last round, sorted); `table` is shared by the call."""
    elements = [r[0] for r in rows]
    coords = np.array([r[1:4] for r in rows], dtype=float)
    adjacency = atom_matching.bonds(coords, elements)
    metal = np.array([e in METALS for e in elements])
    adjacency &= ~(metal[:, None] | metal[None, :])
    labels = atom_matching._labels(elements, adjacency, table)
    return tuple(sorted(labels[-1]))


@dataclass
class _Bonds:
    """Per element and neighbouring element, each atom's number of such neighbours, sorted:
    bonded by the 3D view's rule (`strict`) and within A181's outer reach (`loose`)."""

    strict: dict[tuple[str, str], np.ndarray]
    loose: dict[tuple[str, str], np.ndarray]


def _bond_counts(rows: Rows) -> _Bonds:
    elements = [r[0] for r in rows]
    coords = np.array([r[1:4] for r in rows], dtype=float)
    strict = atom_matching.bonds(coords, elements)
    radii = np.array([atom_matching.RADII.get(e, atom_matching.DEFAULT_RADIUS) for e in elements])
    reach = atom_matching.OUTER_SCALE * (radii[:, None] + radii[None, :])
    d = np.linalg.norm(coords[:, None, :] - coords[None, :, :], axis=2)
    loose = (d <= reach) & (d > atom_matching.MIN_DISTANCE)
    np.fill_diagonal(loose, False)
    kinds = np.array(elements)
    found: tuple[dict, dict] = ({}, {})
    for e in sorted(set(elements)):
        rows_e = kinds == e
        for f in sorted(set(elements)):
            cols = kinds == f
            for table, adjacency in zip(found, (strict, loose), strict=True):
                table[(e, f)] = np.sort(adjacency[np.ix_(rows_e, cols)].sum(axis=1))
    return _Bonds(*found)


def _could_be_same(a: _Bonds, b: _Bonds, count_weak: bool) -> bool:
    """A test, whatever the numbering, that no numbering keeps every bond (A181): one that
    does pairs each atom with one bonded within the outer reach to at least as many neighbours
    of each element, and sorted counts then compare one by one."""
    if count_weak:
        return all(np.array_equal(a.strict[k], b.strict[k]) for k in a.strict)
    return all(
        (a.strict[k] <= b.loose[k]).all() and (b.strict[k] <= a.loose[k]).all()
        for k in a.strict
        if not ("H" in k and set(k) & METALS)  # metal–hydrogen contacts are weak (`split`)
    )


def _same_molecule(
    a: Rows,
    b: Rows,
    count_weak: bool,
    counts: tuple[_Bonds, _Bonds] | None = None,
    match: bool = True,
) -> tuple[bool, list[int] | None, int]:
    """Whether b has a's bonds (A181; weak changes too when `count_weak`): the same numbering
    first, else through Match atoms when the bond counts allow it. Gives the map (None: the same
    numbering) and how many weak changes there are."""
    for attempt in ("same", "match"):
        if attempt == "same":
            if geometry.elements(a) != geometry.elements(b):
                continue
            mapping = None
            formed, broken = atom_matching.changes(a, b)
        else:
            if not match or (counts is not None and not _could_be_same(*counts, count_weak)):
                break
            found = atom_matching.match(a, b)
            mapping, formed, broken = found.mapping, found.formed, found.broken
        rf, rb, wf, wb = split(a, b, mapping, formed, broken)
        weak = len(wf) + len(wb)
        if not rf and not rb and not (count_weak and weak):
            identity = mapping is None or all(i == j for i, j in enumerate(mapping))
            return True, None if identity else mapping, weak
    return False, None, 0


@dataclass
class _Conformer:
    rows: Rows  # in the molecule's numbering
    shape: Print
    members: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class _Molecule:
    index: int
    rows: Rows
    counts: _Bonds
    conformers: list[_Conformer] = field(default_factory=list)
    # Per order of elements met, a member's own rows and its map to the molecule's numbering
    # (None: the same): a structure in that order is checked against it first, which saves
    # matching atoms again for every file of one program.
    orders: dict[tuple[str, ...], tuple[Rows, list[int] | None]] = field(default_factory=dict)

    def same(
        self, rows: Rows, counts: _Bonds, count_weak: bool
    ) -> tuple[bool, list[int] | None, int]:
        order = tuple(geometry.elements(rows))
        known = self.orders.get(order)
        if known is not None:
            same, _, weak = _same_molecule(known[0], rows, count_weak, match=False)
            if same:
                return True, known[1], weak
        found = _same_molecule(self.rows, rows, count_weak, (self.counts, counts))
        if found[0]:
            self.orders.setdefault(order, (rows, found[1]))
        return found


def group(
    structures: list[Structure], tolerance: float, count_weak: bool = False
) -> dict[str, Any]:
    """D263: per formula (with charge and multiplicity) its molecules, isomers of each other;
    per molecule its conformers; per conformer its members, duplicates of each other within
    `tolerance` (D62). By connectivity first (an order-independent print without metal bonds,
    then Match atoms and A181's rule), by atom-matched RMSD second. `near_cut`: pairs that
    could have gone either way."""
    if len(structures) > MAX_GROUP:
        raise StructureError(
            f"{len(structures)} structures are more than the {MAX_GROUP} grouped at once; "
            "group fewer (one formula, or one folder's files)"
        )
    table: dict = {}
    buckets: dict[tuple, dict[tuple, list[_Molecule]]] = {}
    near: list[dict[str, Any]] = []

    def member(n: int, rmsd: float | None, renumbered: bool) -> dict[str, Any]:
        out: dict[str, Any] = {"index": n + 1, "label": structures[n].label}
        if rmsd is not None:
            out["rmsd"] = round(rmsd, 3)
        if renumbered:
            out["renumbered"] = True
        return out

    for n, s in enumerate(structures):
        key = (s.formula, s.charge, s.multiplicity)
        molecules = buckets.setdefault(key, {}).setdefault(fingerprint(s.rows, table), [])
        counts = _bond_counts(s.rows)
        shape = signature(s.rows)
        placed = False
        for molecule in molecules:
            same, mapping, weak = molecule.same(s.rows, counts, count_weak)
            if not same:
                continue
            first = molecule.conformers[0].members[0]["index"]
            if weak:
                near.append(
                    {
                        "a": first,
                        "b": n + 1,
                        "why": f"the same bonds but for {weak} weak contact(s) (A181)",
                    }
                )
            rows = s.rows if mapping is None else [s.rows[j] for j in mapping]
            best: tuple[float, _Conformer, bool] | None = None
            for conformer in molecule.conformers:
                found = same_structure(
                    conformer.rows, rows, 2 * tolerance, (conformer.shape, shape), thorough=False
                )
                if found is None:
                    continue
                if found.rmsd <= tolerance:
                    if best is None or found.rmsd < best[0]:
                        best = (
                            found.rmsd,
                            conformer,
                            mapping is not None or found.mapping is not None,
                        )
                else:
                    near.append(
                        {
                            "a": conformer.members[0]["index"],
                            "b": n + 1,
                            "rmsd": round(found.rmsd, 3),
                            "why": f"between the duplicate tolerance ({tolerance} Å) and twice it",
                        }
                    )
            if best is not None:
                best[1].members.append(member(n, best[0], best[2]))
            else:
                molecule.conformers.append(
                    _Conformer(rows, shape, [member(n, None, mapping is not None)])
                )
            placed = True
            break
        if not placed:
            molecule = _Molecule(
                n, s.rows, counts, [_Conformer(s.rows, shape, [member(n, None, False)])]
            )
            molecule.orders[tuple(geometry.elements(s.rows))] = (s.rows, None)
            molecules.append(molecule)

    formulas = []
    for (formula, charge, multiplicity), prints in buckets.items():
        molecules = [m for found in prints.values() for m in found]
        molecules.sort(key=lambda m: m.index)
        formulas.append(
            {
                "formula": formula,
                "charge": charge,
                "multiplicity": multiplicity,
                "molecules": [
                    {"conformers": [{"members": c.members} for c in m.conformers]}
                    for m in molecules
                ],
            }
        )
    molecule_count = sum(len(f["molecules"]) for f in formulas)
    conformer_count = sum(len(m["conformers"]) for f in formulas for m in f["molecules"])
    return {
        "count": len(structures),
        "tolerance": tolerance,
        "formulas": formulas,
        "near_cut": near,
        "summary": (
            f"{len(structures)} structures: {len(formulas)} formula(s), {molecule_count} "
            f"molecule(s) by their bonds, {conformer_count} conformer(s) (duplicates within "
            f"{tolerance} Å)."
        ),
    }
