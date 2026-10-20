"""Geometry measurements across nodes (D151, A96): distances, angles, dihedrals and the angle
between two vectors, on each node's stored geometry, as a table with a summary per column.
Atoms are numbered as each node has them, as a reference node has them (each node matched to
it, D113), or found in each node by a SMARTS pattern. Nothing is stored."""

import math
from dataclasses import dataclass, field

import numpy as np
from rdkit import Chem
from sqlalchemy.orm import Session

from chembook3d.models import GroupNode, Node
from chembook3d.services import atom_matching, builder, groups, levels, minimize
from chembook3d.services.records import RecordError, get

Point = list[int]  # atoms from 0; more than one means their middle
MAX_NODES = 200
MAX_MEASUREMENTS = 30
DIGITS = {"distance": 3}  # Å; angles in degrees to 2
ANGLE_DIGITS = 2


@dataclass
class Measurement:
    name: str
    kind: str  # "distance", "angle", "dihedral" or "vectors"
    points: list[Point]  # 2 to 4; for "vectors" 4: the first vector's two, then the second's
    smarts: str | None = None  # the points number the pattern's atoms
    lines: bool = False  # vectors: the angle between lines, 0 to 90°


@dataclass
class Condition:
    measurement: int  # its column, from 0
    below: float | None = None
    above: float | None = None


@dataclass
class Source:
    """Where a node's geometry came from (D100)."""

    level_id: str | None
    level: str | None  # the optimization's level label, or None
    converged: bool | None
    program: str | None
    calculation_id: str | None


@dataclass
class Row:
    node: Node
    group: GroupNode | None
    source: Source
    values: list[float | None]
    notes: list[str] = field(default_factory=list)


@dataclass
class Skipped:
    node: Node
    reason: str


@dataclass
class Summary:
    n: int
    mean: float | None
    sd: float | None  # sample standard deviation; None below two values
    min: float | None
    max: float | None
    min_node_id: str | None
    max_node_id: str | None


@dataclass
class Table:
    measurements: list[Measurement]
    rows: list[Row]
    skipped: list[Skipped]
    summary: list[Summary]
    by_level: dict[str, list[Summary]]  # only when the rows' geometries are of several levels
    notes: list[str]


def kind_of(count: int, vectors: bool) -> str:
    if vectors:
        if count != 4:
            raise RecordError("an angle between two vectors needs two points for each vector")
        return "vectors"
    kinds = {2: "distance", 3: "angle", 4: "dihedral"}
    if count not in kinds:
        raise RecordError("a measurement takes 2 (distance), 3 (angle) or 4 (dihedral) points")
    return kinds[count]


def _nodes(
    session: Session, node_ids: list[str], group_ids: list[str]
) -> list[tuple[Node, GroupNode | None]]:
    found: list[tuple[Node, GroupNode | None]] = []
    seen: set[str] = set()
    for group_id in group_ids:
        group = get(session, GroupNode, group_id, "Group")
        for member in groups.members(session, group.id):
            if member.id not in seen:
                seen.add(member.id)
                found.append((member, group))
    for node_id in node_ids:
        node = get(session, Node, node_id, "Node")
        if node.id not in seen:
            seen.add(node.id)
            found.append((node, session.get(GroupNode, node.group_id) if node.group_id else None))
    if not found:
        raise RecordError("give the nodes or groups to measure")
    if len(found) > MAX_NODES:
        raise RecordError(f"measure up to {MAX_NODES} nodes at a time")
    return found


def source_of(node: Node) -> Source:
    calc = levels.node_geometry_optimization(node)
    if calc is None:
        return Source(None, None, None, None, None)
    result = calc.result
    return Source(
        calc.level_id,
        levels.label(calc.level),
        result.optimization_converged if result is not None else None,
        calc.program or None,
        calc.id,
    )


def _level_matches(source: Source, wanted: str) -> bool:
    text = wanted.strip().casefold()
    return source.level_id == wanted or (source.level or "").casefold() == text


def _value(coords: np.ndarray, m: Measurement, atoms: list[Point]) -> float:
    points = np.array([coords[p].mean(axis=0) for p in atoms])
    if m.kind == "vectors":
        u, v = points[1] - points[0], points[3] - points[2]
        if np.linalg.norm(u) < 1e-9 or np.linalg.norm(v) < 1e-9:
            raise RecordError("a vector's two points are on top of each other")
        cos = float(u @ v / (np.linalg.norm(u) * np.linalg.norm(v)))
        if m.lines:
            cos = abs(cos)
        return float(np.degrees(np.arccos(np.clip(cos, -1.0, 1.0))))
    value = builder.measure(points, list(range(len(points))))
    assert value is not None
    return value


def _round(m: Measurement, value: float) -> float:
    return round(value, DIGITS.get(m.kind, ANGLE_DIGITS))


class Finder:
    """Each SMARTS pattern compiled once; its unique match in a node's structure."""

    def __init__(self, measurements: list[Measurement]):
        self.patterns: dict[str, Chem.Mol] = {}
        for m in measurements:
            if m.smarts is None or m.smarts in self.patterns:
                continue
            pattern = Chem.MolFromSmarts(m.smarts)
            if pattern is None:
                raise RecordError(f"“{m.smarts}” is not a SMARTS pattern RDKit can read")
            count = pattern.GetNumAtoms()
            for point in m.points:
                for atom in point:
                    if not 0 <= atom < count:
                        raise RecordError(
                            f"“{m.smarts}” has {count} atoms; there is no atom {atom + 1} in it"
                        )
            self.patterns[m.smarts] = pattern
        self.molecules: dict[str, Chem.Mol] = {}

    def match(self, node: Node, smarts: str) -> tuple[int, ...]:
        mol = self.molecules.get(node.id)
        if mol is None:
            mol = minimize._molecule(builder.Structure([list(r) for r in node.geometry]))[0]
            self.molecules[node.id] = mol
        found = mol.GetSubstructMatches(self.patterns[smarts], uniquify=True, maxMatches=10)
        if not found:
            raise RecordError(f"“{smarts}” matches nothing")
        if len(found) > 1:
            places = "; ".join(", ".join(str(a + 1) for a in f) for f in found[:3])
            raise RecordError(
                f"“{smarts}” matches {len(found)} places ({places}{'; …' if len(found) > 3 else ''}"
                "): make the pattern pick one"
            )
        return found[0]


def table(
    session: Session,
    measurements: list[Measurement],
    node_ids: list[str] = (),  # type: ignore[assignment]
    group_ids: list[str] = (),  # type: ignore[assignment]
    reference_id: str | None = None,
    level: str | None = None,
    converged_only: bool = False,
    keep: list[Condition] = (),  # type: ignore[assignment]
) -> Table:
    """D151: each node's measurements, with where its geometry came from, and per column n,
    mean, standard deviation, min and max over the rows kept."""
    if not measurements:
        raise RecordError("give at least one measurement")
    if len(measurements) > MAX_MEASUREMENTS:
        raise RecordError(f"give up to {MAX_MEASUREMENTS} measurements at a time")
    for c in keep:
        if not 0 <= c.measurement < len(measurements):
            raise RecordError(f"there is no measurement {c.measurement + 1} to keep rows by")
        if c.below is None and c.above is None:
            raise RecordError("a condition needs `below` or `above`")
    reference = None
    if reference_id is not None:
        reference = get(session, Node, reference_id, "Node")
        if not reference.geometry:
            raise RecordError(f"the reference “{reference.label or 'Untitled node'}” has no "
                              "coordinates")  # fmt: skip
    finder = Finder(measurements)
    notes: list[str] = []
    rows: list[Row] = []
    skipped: list[Skipped] = []
    for node, group in _nodes(session, list(node_ids), list(group_ids)):
        source = source_of(node)
        if not node.geometry:
            skipped.append(Skipped(node, "no coordinates"))
            continue
        if level is not None and not _level_matches(source, level):
            skipped.append(
                Skipped(node, f"geometry from {source.level or 'no optimization'}, not {level}")
            )
            continue
        if converged_only and source.converged is not True:
            reason = (
                "no optimization"
                if source.calculation_id is None
                else "its optimization did not converge"
                if source.converged is False
                else "its optimization's convergence is not known"
            )
            skipped.append(Skipped(node, reason))
            continue
        row = Row(node, group, source, [])
        mapping: list[int] | None = None
        if reference is not None and node.id != reference.id:
            try:
                matched = atom_matching.match(reference.geometry, node.geometry)
            except RecordError as exc:
                skipped.append(Skipped(node, f"cannot be matched to the reference: {exc}"))
                continue
            mapping = matched.mapping
            if matched.doubts:
                row.notes.append(
                    "atom match to the reference doubtful: " + "; ".join(matched.doubts)
                )
        coords = np.array([[r[1], r[2], r[3]] for r in node.geometry], dtype=float)
        for m in measurements:
            try:
                if m.smarts is not None:
                    found = finder.match(node, m.smarts)
                    atoms = [[found[a] for a in p] for p in m.points]
                elif mapping is not None:
                    atoms = [[mapping[a] for a in p] for p in m.points]
                else:
                    atoms = m.points
                    for p in atoms:
                        for a in p:
                            if not 0 <= a < len(coords):
                                raise RecordError(f"there is no atom {a + 1}")
                row.values.append(_round(m, _value(coords, m, atoms)))
            except RecordError as exc:
                row.values.append(None)
                row.notes.append(f"{m.name}: {exc}")
        if not _kept(row, keep):
            skipped.append(
                Skipped(node, "left out by the conditions: " + _said(measurements, keep))
            )
            continue
        rows.append(row)
    summary = _summaries(measurements, rows)
    by_level: dict[str, list[Summary]] = {}
    named = {r.source.level or "no optimization" for r in rows}
    if len(named) > 1:
        for name in sorted(named):
            by_level[name] = _summaries(
                measurements, [r for r in rows if (r.source.level or "no optimization") == name]
            )
        notes.append(
            f"the geometries come from {len(named)} levels; `by_level` summarises each apart"
        )
    for m, s in zip(measurements, summary, strict=True):
        if m.kind == "dihedral" and s.n and (s.min < -180 or s.max > 180):  # type: ignore[operator]
            notes.append(
                f"{m.name} crosses ±180°: its summary counts each angle round the mean, so its "
                "min or max lies past ±180°"
            )
    return Table(measurements, rows, skipped, summary, by_level, notes)


def _kept(row: Row, keep: list[Condition]) -> bool:
    for c in keep:
        value = row.values[c.measurement]
        if value is None:
            return False
        if c.below is not None and not value < c.below:
            return False
        if c.above is not None and not value > c.above:
            return False
    return True


def _said(measurements: list[Measurement], keep: list[Condition]) -> str:
    parts = []
    for c in keep:
        name = measurements[c.measurement].name
        if c.below is not None:
            parts.append(f"{name} below {c.below:g}")
        if c.above is not None:
            parts.append(f"{name} above {c.above:g}")
    return ", ".join(parts)


def _summaries(measurements: list[Measurement], rows: list[Row]) -> list[Summary]:
    out = []
    for n, m in enumerate(measurements):
        pairs = [(r.values[n], r.node.id) for r in rows if r.values[n] is not None]
        if not pairs:
            out.append(Summary(0, None, None, None, None, None, None))
            continue
        if m.kind == "dihedral":  # counted round their circular mean, so ±180° is no edge
            pairs = _round_mean(pairs)
        values = np.array([v for v, _ in pairs], dtype=float)
        low = min(pairs, key=lambda p: p[0])
        high = max(pairs, key=lambda p: p[0])
        sd = float(values.std(ddof=1)) if len(values) > 1 else None
        out.append(
            Summary(
                len(values),
                _round(m, float(values.mean())),
                None if sd is None or not math.isfinite(sd) else _round(m, sd),
                low[0],
                high[0],
                low[1],
                high[1],
            )
        )
    return out


def _round_mean(pairs: list[tuple[float, str]]) -> list[tuple[float, str]]:
    """Dihedrals moved by whole turns to within 180° of their circular mean (D151)."""
    radians = np.radians([v for v, _ in pairs])
    mean = math.degrees(math.atan2(float(np.sin(radians).mean()), float(np.cos(radians).mean())))
    return [(round(mean + (v - mean + 180.0) % 360.0 - 180.0, ANGLE_DIGITS), i) for v, i in pairs]
