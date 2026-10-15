"""IRC calculations of a TS node (D131, A76; the connection check D132, A77).

A TS node holds its IRC as calculations marked as IRC parts (`Calculation.irc_part`): the path
in each direction ("path", from a file whose IRC follows one direction or both) and the jobs
run on each end point ("end_opt", "end_freq"). They are never the node's own calculations
(`Node.calculations` leaves them out), so they give the node no energy, geometry or match, and
the card does not change. Everything here is read from the copied files when asked, as the
step movie does (D101).

- Directions are the program's labels: "forward" follows the imaginary mode as the program
  printed it, whose sign is arbitrary, so neither side is the reactant by name.
- A direction holds one run: a later file that starts where the run ended continues it (a
  restarted IRC); one that starts again from the TS replaces it, and the earlier file is listed
  as not used.
- The path is drawn through the TS: the reverse side on the left, the forward side on the
  right, against the distance along the path in mass-weighted coordinates (amu^1/2 bohr),
  which the app computes from the structures for every program (each fitted onto the TS
  first). The energies are each file's own, relative to its own TS point, so a path is always
  compared at one level.
- An end point's optimization and frequency job give E, G and G_qh relative to the TS node at
  the same composite level (EN-3); they never feed profiles, the energy table, energetic span
  or selectivity.
- The connection check compares each end (its optimized structure, else the IRC's last point)
  with the nodes on the TS's edges, on the reacting atoms (D118): an end connects a neighbour
  whose bonds among those atoms are the same. It never changes an edge. The chart is drawn by
  the edges: the side that reaches the node an incoming edge comes from goes on the left (Q39).
- An optimized end point that is the same structure as a node (the import's duplicate check,
  ID-7) says so; otherwise it can be made a node of its own by importing its files again.
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
from sqlalchemy.orm import Session

from chembook3d import pathtools, thermochem
from chembook3d import settings as app_settings
from chembook3d.models import Calculation, CalculationType, Node, Role
from chembook3d.parsers.common import IrcRun, ParsedStep
from chembook3d.services import atom_matching, geometry, imports, levels, trajectory, transitions
from chembook3d.services import energies as energy_service
from chembook3d.services import files as file_service
from chembook3d.services import groups as group_service
from chembook3d.services.records import RecordError, get

DIRECTIONS = ("reverse", "forward")  # the order the path is drawn in, left to right
BOHR = 0.529177210903  # Å
ENERGY_TYPES = ("E", "G", "G_qh")


@dataclass
class Point:
    side: str  # "ts", "forward" or "reverse"
    number: int  # the program's point number; 0 is the TS
    coordinate: float  # amu^1/2 bohr along the path from the TS, negative on the reverse side
    program_coordinate: float | None  # the program's own, as printed (from the TS, unsigned)
    energy: float | None  # hartree
    relative: float | None  # hartree, to the TS point of the same file
    rows: list[list[Any]]  # fitted onto the TS
    calculation_id: str
    file: str


@dataclass
class Part:
    """One imported file's IRC parts, as listed in the window."""

    source_file_id: str | None
    file: str
    part: str  # "path" or "end"
    direction: str  # "forward", "reverse" or "both" (a path file running both ways)
    calculation_ids: list[str]
    types: list[str]
    program: str
    level: str
    termination: str
    imported_at: str
    used: bool = True  # False: a later run of the same direction replaced it
    note: str = ""
    endings: dict[str, str] = field(default_factory=dict)  # direction -> how it ended
    max_points: int | None = None
    points: dict[str, int] = field(default_factory=dict)  # direction -> points after the TS


@dataclass
class EndPoint:
    direction: str
    optimization_id: str | None
    frequency_id: str | None
    level: str
    converged: bool | None
    imaginary: int | None
    start_rmsd: float | None  # Å, the optimization's first structure to the IRC's last point
    values: dict[str, Any]  # energy type -> {value, relative, message}


@dataclass
class Neighbour:
    """A node on one of the TS's edges, compared with an end of the IRC."""

    node_id: str
    label: str
    edge: str  # "incoming" (its edge comes to the TS) or "outgoing"
    rmsd: float | None  # Å over the reacting atoms after fitting; None: other atoms
    rmsd_all: float | None  # Å over all atoms
    same_bonds: bool  # the same bonds among the reacting atoms
    note: str = ""


@dataclass
class Connection:
    """D132: where an end of the IRC leads. `verdict` is "connects" (a neighbour with the same
    bonds among the reacting atoms, the nearest such), "elsewhere" (no neighbour has them),
    "no_neighbours" (the TS has no edges) or "unknown" (no structure to judge)."""

    verdict: str
    judged_on: str  # "optimized end point" or "last IRC point"
    node_id: str | None = None  # the neighbour it connects, or the nearest one
    label: str = ""
    rmsd: float | None = None
    atoms: int = 0  # the reacting atoms it is judged on; 0: all
    neighbours: list[Neighbour] = field(default_factory=list)


@dataclass
class Side:
    direction: str
    ending: str | None
    max_points: int | None
    points: int  # after the TS
    last_relative: float | None  # hartree, the last point to the TS
    end_point: EndPoint | None
    connection: Connection | None = None
    # Nodes the optimized end point is the same structure as (ID-7's tolerance), nearest first.
    same_as: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class Irc:
    node_id: str
    label: str
    is_ts: bool
    parts: list[Part]
    points: list[Point]
    sides: list[Side]
    ts_energy: float | None
    ts_values: dict[str, Any]  # energy type -> the TS node's value at an end point's level
    warnings: list[str]
    left: str = "reverse"  # the direction drawn on the left (Q39); its distances are negative


def _read_step(folder: Path, calculation: Calculation) -> tuple[ParsedStep, str] | None:
    source = calculation.source_file
    if source is None or calculation.step_index is None:
        return None
    path = file_service.absolute_path(folder, source)
    try:
        stat = path.stat()
        parsed = trajectory._read(str(path), stat.st_mtime_ns, stat.st_size)
    except (OSError, imports.ImportFailed):
        return None
    if parsed is None:
        return None
    step = next((s for s in parsed.steps if s.index == calculation.step_index), None)
    return (step, source.original_name) if step is not None else None


def _masses(rows: list[list[Any]]) -> np.ndarray:
    from morfeus.data import atomic_masses  # most abundant isotopes, as Gaussian's IRC uses

    from chembook3d.xyz import ELEMENTS

    return np.array([atomic_masses[ELEMENTS.index(row[0]) + 1] for row in rows], dtype=float)


def mass_weighted_distance(a: list[list[Any]], b: list[list[Any]], masses: np.ndarray) -> float:
    """sqrt(Σ m_i |Δr_i|²) between two structures in the same frame, in amu^1/2 bohr."""
    p = np.array([row[1:4] for row in a], dtype=float)
    q = np.array([row[1:4] for row in b], dtype=float)
    return float(np.sqrt((masses * ((p - q) ** 2).sum(axis=1)).sum())) / BOHR


@dataclass
class _Run:
    """One file's points in one direction."""

    calculation: Calculation
    file: str
    run: IrcRun
    path: int
    ts: list[list[Any]] | None
    ts_energy: float | None
    points: list[tuple[int, list[list[Any]], float | None, float | None]]  # number, rows, E, coord


def _runs(step: ParsedStep, calculation: Calculation, file: str) -> dict[str, _Run]:
    run = step.irc
    if run is None:
        return {}
    ts = next((p for p in run.points if p.number == 0), None)
    ts_rows = imports._rows(step.geometries[ts.structure]) if ts else None
    found: dict[str, _Run] = {}
    for path, direction in run.directions.items():
        points = sorted(
            (p for p in run.points if p.path == path and p.number > 0), key=lambda p: p.number
        )
        found[direction] = _Run(
            calculation,
            file,
            run,
            path,
            ts_rows,
            run.ts_energy,
            [
                (
                    p.number,
                    imports._rows(step.geometries[p.structure]) or [],
                    p.energy,
                    p.coordinate,
                )
                for p in points
            ],
        )
    return found


def read(session: Session, folder: Path, node_id: str, structures: bool = True) -> Irc:
    """The node's IRC; without `structures` the points carry no coordinates (Claude's read)."""
    node = get(session, Node, node_id, "Node")
    settings = app_settings.load()
    tolerance = settings.duplicate_tolerance
    warnings: list[str] = []
    parts: dict[str, Part] = {}
    runs: dict[str, list[_Run]] = {d: [] for d in DIRECTIONS}
    ends: dict[str, list[Calculation]] = {d: [] for d in DIRECTIONS}

    def part_of(calculation: Calculation) -> Part:
        return parts[calculation.source_file_id or calculation.id]

    for calculation in node.irc_calculations:
        source = calculation.source_file
        key = calculation.source_file_id or calculation.id
        if key not in parts:
            parts[key] = Part(
                source_file_id=calculation.source_file_id,
                file=source.original_name if source else "",
                part="path" if calculation.irc_part == "path" else "end",
                direction=calculation.irc_direction or "",
                calculation_ids=[],
                types=[],
                program=calculation.program,
                level=levels.label(calculation.level) or "unknown level",
                termination=calculation.termination,
                imported_at=source.imported_at.isoformat() if source else "",
            )
        part = parts[key]
        part.calculation_ids.append(calculation.id)
        part.types.append(calculation.type)
        if calculation.termination != "normal":
            part.termination = calculation.termination
        if calculation.irc_part != "path":
            if calculation.irc_direction in ends:
                ends[calculation.irc_direction].append(calculation)
            continue
        read_back = _read_step(folder, calculation)
        if read_back is None:
            part.used = False
            part.note = "The copy of this file cannot be read"
            continue
        step, file = read_back
        for direction, run in _runs(step, calculation, file).items():
            part.endings[direction] = run.run.endings.get(run.path, "stopped")
            part.max_points = run.run.max_points
            part.points[direction] = len(run.points)
            runs[direction].append(run)

    # Each direction's run: a later file that starts where it ended continues it, any other
    # later file starts it again.
    chains: dict[str, list[_Run]] = {}
    replaced: dict[int, str] = {}  # id of a replaced run's calculation -> the file replacing it
    for direction, found in runs.items():
        chain: list[_Run] = []
        for run in found:
            first = run.points[0][1] if run.points else run.ts
            end = chain[-1].points[-1][1] if chain and chain[-1].points else None
            if not (chain and end is not None and geometry.matches(first, end, tolerance)):
                for earlier in chain:
                    replaced[id(earlier.calculation)] = f"{run.file} ({direction})"
                chain = []
            chain.append(run)
        chains[direction] = chain
    used = {id(r.calculation) for chain in chains.values() for r in chain}
    for found in runs.values():
        for run in found:
            if id(run.calculation) not in used:
                part = part_of(run.calculation)
                part.used = False
                part.note = f"Replaced by a later IRC: {replaced.get(id(run.calculation), '')}"

    first_runs = [chain[0] for chain in chains.values() if chain and chain[0].ts]
    reference = node.geometry or (first_runs[0].ts if first_runs else None)
    masses = _masses(reference) if reference else None
    points: list[Point] = []
    sides: list[Side] = []
    last_rows: dict[str, list[list[Any]]] = {}
    for direction in DIRECTIONS:
        chain = chains[direction]
        if not chain:
            sides.append(Side(direction, None, None, 0, None, None))
            continue
        first = chain[0]
        if first.ts and node.geometry:
            distance = geometry.aligned_rmsd(first.ts, node.geometry)
            if distance is not None and distance > tolerance:
                warnings.append(
                    f"The {direction} IRC ({first.file}) starts {distance:.3f} Å from the TS "
                    "node's structure: it was run from another structure."
                )
        previous = _placed(reference, first.ts)
        travelled = 0.0
        sign = -1 if direction == "reverse" else 1
        side_points: list[Point] = []
        seen: list[list[Any]] | None = None
        for run in chain:
            for number, rows, energy, coordinate in run.points:
                if seen is not None and geometry.matches(rows, seen, 1e-4):
                    continue  # a continuation repeats the point it starts from
                seen = rows
                placed = _placed(reference, rows) or rows
                if previous is not None and masses is not None and len(previous) == len(placed):
                    travelled += mass_weighted_distance(previous, placed, masses)
                previous = placed
                relative = None
                if energy is not None and run.ts_energy is not None:
                    relative = energy - run.ts_energy
                side_points.append(
                    Point(
                        direction,
                        number,
                        sign * travelled,
                        coordinate,
                        energy,
                        relative,
                        placed,
                        run.calculation.id,
                        run.file,
                    )
                )
        last = chain[-1]
        sides.append(
            Side(
                direction,
                last.run.endings.get(last.path, "stopped"),
                last.run.max_points,
                len(side_points),
                side_points[-1].relative if side_points else None,
                None,
            )
        )
        if side_points:
            last_rows[direction] = side_points[-1].rows
        points += side_points if direction == "forward" else list(reversed(side_points))

    ts_energy = None
    if first_runs:
        ts_run = first_runs[0]
        ts_energy = ts_run.ts_energy
        split = sum(1 for p in points if p.side == "reverse")
        points.insert(
            split,
            Point(
                "ts",
                0,
                0.0,
                0.0,
                ts_energy,
                0.0 if ts_energy is not None else None,
                _placed(reference, ts_run.ts) or ts_run.ts,
                ts_run.calculation.id,
                ts_run.file,
            ),
        )
    path_levels = {part_of(r.calculation).level for chain in chains.values() for r in chain}
    if len(path_levels) > 1:
        warnings.append(
            "The forward and reverse IRCs are at different levels ("
            + " and ".join(sorted(path_levels))
            + "); each side's energies are relative to its own TS point."
        )

    ts_values: dict[str, Any] = {}
    node_energies: energy_service.NodeEnergies | None = None
    for side in sides:
        found_ends = ends[side.direction]
        if not found_ends:
            continue
        if node_energies is None:
            node_energies = energy_service.NodeEnergies(
                node, settings.qh_temperature, settings.qh_cutoff, settings.standard_state
            )
        side.end_point = _end_point(
            folder,
            side.direction,
            found_ends,
            node_energies,
            settings,
            last_rows.get(side.direction),
        )
        level_id = next(
            (c.level_id for c in reversed(found_ends) if c.type == CalculationType.OPTIMIZATION),
            None,
        )
        if level_id and level_id not in ts_values:
            key = energy_service.LevelKey(level_id, level_id)
            ts_values[level_id] = {t: _value(node_energies.value(key, t)) for t in ENERGY_TYPES}

    _check_connections(session, node, sides, last_rows, settings.duplicate_tolerance)
    left = _left_side(sides)
    if left == "forward":
        points = list(reversed(points))
        for point in points:
            point.coordinate = -point.coordinate
        sides.reverse()
    if not structures:
        for point in points:
            point.rows = []
    return Irc(
        node_id=node.id,
        label=node.label,
        is_ts=node.role == Role.TRANSITION_STATE,
        parts=list(parts.values()),
        points=points,
        sides=sides,
        ts_energy=ts_energy,
        ts_values=ts_values,
        warnings=warnings,
        left=left,
    )


def _neighbours(session: Session, node: Node) -> list[tuple[Node, str]]:
    """The nodes on the TS's edges (and on its group's), each with whether its edge comes in;
    a group end stands for each of its members."""
    ends = [node.id] + ([node.group_id] if node.group_id else [])
    found: dict[str, tuple[Node, str]] = {}
    for edge in transitions.touching(session, ends):
        incoming = edge.target_id in ends
        other = edge.source_id if incoming else edge.target_id
        if other in ends:
            continue
        other_node = session.get(Node, other)
        members = [other_node] if other_node else group_service.members(session, other)
        for member in members:
            if member.id != node.id and member.geometry:
                found.setdefault(member.id, (member, "incoming" if incoming else "outgoing"))
    return list(found.values())


def _in_order(rows: list[list[Any]], other: list[list[Any]]) -> list[list[Any]] | None:
    """`other` in `rows`'s atom order: as it is when the elements line up, else matched by
    their bonds (D113); None when the atoms differ."""
    if geometry.elements(rows) == geometry.elements(other):
        return other
    if sorted(geometry.elements(rows)) != sorted(geometry.elements(other)):
        return None
    try:
        found = atom_matching.match(rows, other)
    except RecordError:
        return None
    return atom_matching.renumbered(rows, other, found.mapping)


def _subset(rows: list[list[Any]], atoms: list[int]) -> list[list[Any]]:
    return [rows[a - 1] for a in atoms] if atoms else rows


def _bonds_among(rows: list[list[Any]], atoms: list[int]) -> set[tuple[int, int]]:
    keep = set(atoms) if atoms else set(range(1, len(rows) + 1))
    return {pair for pair in pathtools.bonds(rows) if set(pair) <= keep}


def _check_connections(
    session: Session,
    node: Node,
    sides: list[Side],
    last_rows: dict[str, list[list[Any]]],
    tolerance: float,
) -> None:
    """Each end against the TS's neighbours, on the atoms that react between the two ends (or
    between the TS and the one end there is), D118's reacting atoms."""
    ends: dict[str, tuple[list[list[Any]], str]] = {}
    for side in sides:
        optimization = (
            session.get(Calculation, side.end_point.optimization_id)
            if side.end_point and side.end_point.optimization_id
            else None
        )
        if optimization is not None and optimization.geometry:
            ends[side.direction] = (optimization.geometry, "optimized end point")
            side.same_as = imports._duplicates(
                session,
                optimization.geometry,
                node.charge,
                node.multiplicity,
                {node.id},
                tolerance,
            )
        elif side.direction in last_rows:
            ends[side.direction] = (last_rows[side.direction], "last IRC point")
    reference = node.geometry
    found = [rows for rows, _ in ends.values()]
    atoms: list[int] = []
    if len(found) == 2 and len(found[0]) == len(found[1]):
        atoms = pathtools.active_atoms(found[0], found[1])
    elif found and reference and len(found[0]) == len(reference):
        atoms = pathtools.active_atoms(reference, found[0])
    neighbours = _neighbours(session, node)
    for side in sides:
        if side.direction not in ends:
            continue
        rows, judged_on = ends[side.direction]
        compared = []
        for other, edge in neighbours:
            ordered = _in_order(rows, other.geometry or [])
            if ordered is None:
                compared.append(
                    Neighbour(other.id, other.label, edge, None, None, False, "other atoms")
                )
                continue
            compared.append(
                Neighbour(
                    other.id,
                    other.label,
                    edge,
                    geometry.aligned_rmsd(_subset(rows, atoms), _subset(ordered, atoms)),
                    geometry.aligned_rmsd(rows, ordered),
                    _bonds_among(rows, atoms) == _bonds_among(ordered, atoms),
                )
            )
        compared.sort(key=lambda n: (n.rmsd is None, n.rmsd or 0.0))
        same = [n for n in compared if n.same_bonds]
        best = (same or [n for n in compared if n.rmsd is not None] or [None])[0]
        verdict = "connects" if same else "elsewhere" if compared else "no_neighbours"
        side.connection = Connection(
            verdict=verdict,
            judged_on=judged_on,
            node_id=best.node_id if best else None,
            label=best.label if best else "",
            rmsd=best.rmsd if best else None,
            atoms=len(atoms),
            neighbours=compared,
        )


def _left_side(sides: list[Side]) -> str:
    """Q39: the side reaching the node an incoming edge comes from is drawn on the left; one
    reaching an outgoing edge's node on the right; reverse on the left otherwise."""
    for side in sides:
        connection = side.connection
        if connection is None or connection.verdict != "connects":
            continue
        edge = next(n.edge for n in connection.neighbours if n.node_id == connection.node_id)
        if (edge == "incoming") == (side.direction == "forward"):
            return "forward"
        return "reverse"
    return "reverse"


def make_node(
    session: Session, folder: Path, staging: imports.Staging, node_id: str, direction: str
) -> str:
    """Q38: an optimized end point becomes a node of its own: its optimization's file, and the
    frequency job's when it is another file, imported again as a new node beside the TS. The
    IRC keeps its parts; the new node is an ordinary import, undone like one (D102)."""
    node = get(session, Node, node_id, "Node")
    found = [c for c in node.irc_calculations if c.irc_direction == direction]
    optimizations = [c for c in found if c.type == CalculationType.OPTIMIZATION]
    if not optimizations:
        raise RecordError(f"The {direction} end point has no optimization to make a node from")
    optimization = optimizations[-1]
    frequencies = [
        c
        for c in found
        if c.type == CalculationType.FREQUENCY
        and geometry.matches(c.geometry, optimization.geometry, 0.001)
    ]
    sources = [optimization.source_file]
    if frequencies and frequencies[-1].source_file_id != optimization.source_file_id:
        sources.append(frequencies[-1].source_file)
    sign = 1 if direction == "forward" else -1
    basis = (optimization.parsed_level or {}).get("basis_given")
    new_id: str | None = None
    for source in sources:
        if source is None:
            raise RecordError("The end point's file was not imported, so it cannot be read again")
        data = file_service.absolute_path(folder, source).read_bytes()
        staged = staging.add(
            data, source.original_name, source.origin_path, device=source.origin_device or None
        )
        options = imports.ImportOptions(
            target_node_id=new_id,
            duplicate_action="new",
            label=f"{node.label or 'TS'} {direction} end" if new_id is None else None,
            pos_x=node.pos_x + sign * END_NODE_OFFSET[0] if new_id is None else None,
            pos_y=node.pos_y + END_NODE_OFFSET[1] if new_id is None else None,
            branch_id=node.branch_id if new_id is None else None,
            missing_basis=basis,
        )
        try:
            result = imports.commit(session, folder, staged, options)
        finally:
            staging.discard(staged.token)
        new_id = new_id or result.derived_node_id or result.node_id
    assert new_id is not None
    return new_id


END_NODE_OFFSET = (320.0, 180.0)  # where a node made from an end point goes, from the TS


def _placed(
    reference: list[list[Any]] | None, rows: list[list[Any]] | None
) -> list[list[Any]] | None:
    """`rows` fitted onto `reference` when they have the same atoms."""
    if not rows:
        return None
    if not reference or geometry.elements(reference) != geometry.elements(rows):
        return rows
    return geometry.place(reference, rows).rows


def _value(found: energy_service.Value) -> dict[str, Any]:
    return {"value": found.value, "message": found.message}


def _end_point(
    folder: Path,
    direction: str,
    found: list[Calculation],
    node_energies: energy_service.NodeEnergies,
    settings: app_settings.Settings,
    last_rows: list[list[Any]] | None,
) -> EndPoint:
    """The latest optimization on this end and the latest frequency job on its structure (or
    the latest one, when none matches); their energies relative to the TS at the same level."""
    tolerance = settings.geometry_tolerance
    optimizations = [c for c in found if c.type == CalculationType.OPTIMIZATION]
    optimization = optimizations[-1] if optimizations else None
    frequencies = [c for c in found if c.type == CalculationType.FREQUENCY]
    on_it = [
        c
        for c in frequencies
        if optimization is not None
        and geometry.matches(c.geometry, optimization.geometry, tolerance)
    ]
    frequency = (on_it or frequencies or [None])[-1]
    start_rmsd = None
    if optimization is not None and last_rows:
        read_back = _read_step(folder, optimization)
        if read_back is not None and read_back[0].geometries:
            first = imports._rows(read_back[0].geometries[0])
            start_rmsd = geometry.aligned_rmsd(first, last_rows)
    owner = optimization or frequency
    level = owner.level if owner is not None else None
    result = frequency.result if frequency is not None else None
    optimized = optimization.result if optimization is not None else None
    return EndPoint(
        direction=direction,
        optimization_id=optimization.id if optimization else None,
        frequency_id=frequency.id if frequency else None,
        level=levels.label(level) or "unknown level",
        converged=optimized.optimization_converged if optimized is not None else None,
        imaginary=result.imaginary_count if result is not None else None,
        start_rmsd=start_rmsd,
        values={
            t: _end_value(t, optimization, frequency, node_energies, settings) for t in ENERGY_TYPES
        },
    )


def _end_value(
    energy_type: str,
    optimization: Calculation | None,
    frequency: Calculation | None,
    node_energies: energy_service.NodeEnergies,
    settings: app_settings.Settings,
) -> dict[str, Any]:
    if optimization is None or optimization.result is None or optimization.result.energy is None:
        return {"value": None, "relative": None, "message": "no optimization of this end point"}
    energy = optimization.result.energy
    value: float | None = energy
    message = None
    if energy_type != "E":
        if frequency is None or frequency.result is None:
            value, message = None, "no frequency job on this end point"
        elif frequency.level_id != optimization.level_id:
            value, message = None, "the frequency job is at another level than the optimization"
        elif energy_type == "G":
            correction = frequency.result.g_corr
            value = None if correction is None else energy + correction
            message = (
                "the frequency job has no printed G correction" if correction is None else None
            )
        else:
            try:
                qh = energy_service.quasi_harmonic(
                    frequency, settings.qh_temperature, settings.qh_cutoff
                )
                value = energy + qh.g_corr
            except thermochem.NotComputable as exc:
                value, message = None, exc.message
        if value is not None and settings.standard_state == "1 M":
            value += thermochem.standard_state_correction(settings.qh_temperature)
    relative = None
    if value is not None and optimization.level_id:
        key = energy_service.LevelKey(optimization.level_id, optimization.level_id)
        ts = node_energies.value(key, energy_type)
        if ts.value is not None:
            relative = value - ts.value
        else:
            message = f"the TS has no {energy_type} at this level"
    return {"value": value, "relative": relative, "message": message}


def summary(session: Session, node_id: str) -> dict[str, Any]:
    """What the node's IRC holds, without reading its files: for the node panel's line."""
    node = get(session, Node, node_id, "Node")
    found: dict[str, Any] = {
        "is_ts": node.role == Role.TRANSITION_STATE,
        "paths": {d: 0 for d in DIRECTIONS},
        "optimizations": {d: 0 for d in DIRECTIONS},
        "frequencies": {d: 0 for d in DIRECTIONS},
        "count": len(node.irc_calculations),
    }
    for calculation in node.irc_calculations:
        direction = calculation.irc_direction or ""
        names = DIRECTIONS if direction == "both" else (direction,)
        group = {"path": "paths", "end_opt": "optimizations", "end_freq": "frequencies"}.get(
            calculation.irc_part or ""
        )
        for name in names:
            if group and name in DIRECTIONS:
                found[group][name] += 1
    return found


def remove(session: Session, node_id: str, source_file_id: str) -> str:
    """Take one imported file's IRC parts off the node: the undo of its import (D102), so the
    history keeps both. Returns the stored copy's folder for the caller to remove once the
    transaction has committed."""
    from chembook3d.services import import_undo  # it reads the history of imports

    node = get(session, Node, node_id, "Node")
    if not any(c.source_file_id == source_file_id for c in node.irc_calculations):
        raise RecordError("This file is not part of the node's IRC")
    undo = import_undo.plan(session, import_undo.records_of(session, source_file_id))
    return import_undo.apply(session, undo)
