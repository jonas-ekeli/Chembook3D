"""One-off corrections of records an earlier version of the app got wrong. Each repair runs
once per investigation, when it is opened by a version that has it; its name is then kept in
`InvestigationInfo.repairs`. The database was backed up by the migration that came with it."""

import logging
import re
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from chembook3d import settings as app_settings
from chembook3d.models import (
    Calculation,
    CalculationType,
    ConformerEquilibrium,
    CustomDispersion,
    HistoryEntry,
    InvestigationInfo,
    LevelOfTheory,
    Node,
    Selectivity,
    Turnover,
)
from chembook3d.parsers.common import (
    BSSE,
    FREQUENCY_SCALE,
    NOT_STATIONARY,
    SPIN_KEYS,
    THERMO_MODEL_KEYS,
)
from chembook3d.services import (
    energy_profiles,
    geometry,
    history,
    imports,
    levels,
    thermo_settings,
)

log = logging.getLogger(__name__)
SOURCE = "repair"


def _start_geometries(folder: Path, calculations: list[Calculation]) -> dict[str, list[Any]]:
    """The geometry each optimization started from, read again from its copied file."""
    parsed: dict[str, Any] = {}
    found: dict[str, list[Any]] = {}
    for calculation in calculations:
        source = calculation.source_file
        if source is None or calculation.step_index is None:
            continue
        if source.id not in parsed:
            try:
                data = (folder / source.stored_path).read_bytes()
                parsed[source.id] = imports.read_file(
                    data.decode("utf-8", errors="replace"), source.original_name
                )
            except (OSError, imports.ImportFailed):
                parsed[source.id] = None
        steps = getattr(parsed[source.id], "steps", None) or []
        step = next((s for s in steps if s.index == calculation.step_index), None)
        if step is not None and step.geometries:
            found[calculation.id] = imports._rows(step.geometries[0])
    return found


def _continue_pre_optimizations(
    session: Session, folder: Path, node: Node, tolerance: float
) -> bool:
    """D100: a node kept on its pre-optimization's geometry when a later optimization at
    another level started from it takes that optimization's result, as an import now does."""
    optimizations = [c for c in levels.optimizations(node) if c.geometry]
    if len({c.level_id for c in optimizations}) < 2:
        return False
    starts = _start_geometries(folder, optimizations)
    changed = False
    for _ in optimizations:  # a chain xTB → DFT → DFT moves at most once per optimization
        current = levels.optimization_at(node, node.geometry, tolerance)
        if current is None:
            break
        following = next(
            (
                c
                for c in optimizations
                if c.level_id != current.level_id
                and geometry.matches(starts.get(c.id), current.geometry, tolerance)
                and not geometry.matches(c.geometry, node.geometry, tolerance)
            ),
            None,
        )
        if following is None:
            break
        history.record(
            session,
            "node",
            node.id,
            "update",
            "geometry",
            node.geometry,
            following.geometry,
            source=SOURCE,
        )
        node.geometry = following.geometry
        changed = True
    return changed


def _set_by_hand(session: Session) -> set[str]:
    """Calculations whose geometry level the user (or Claude) set."""
    return set(
        session.scalars(
            select(HistoryEntry.record_id).where(
                HistoryEntry.record_type == "calculation",
                HistoryEntry.field == "geometry_level",
                HistoryEntry.source != SOURCE,
            )
        )
    )


def geometry_levels(session: Session, folder: Path) -> dict[str, int]:
    """D100: before it, a node kept a lower-level pre-optimization's geometry when a higher
    level optimization continued from it, and a single point took the level of the node's
    latest optimization rather than the one that ended on its geometry (DFT//xTB instead of
    SP//DFT). Fix both; a geometry level set by hand is kept, and a single point that no
    optimization of its node ended on is left for the user to set."""
    tolerance = app_settings.load().geometry_tolerance
    by_hand = _set_by_hand(session)
    nodes = session.scalars(
        select(Node).options(
            selectinload(Node.calculations).selectinload(Calculation.source_file),
            selectinload(Node.calculations).selectinload(Calculation.level),
        )
    ).all()
    counts = {"nodes": 0, "single_points": 0}
    for node in nodes:
        if _continue_pre_optimizations(session, folder, node, tolerance):
            counts["nodes"] += 1
        counts["single_points"] += _single_point_levels(session, node, tolerance, by_hand)
    return counts


def _single_point_levels(session: Session, node: Node, tolerance: float, by_hand: set[str]) -> int:
    """D100: set each single point's geometry level to the optimization ending on its geometry
    (none when none does), except one set by hand; the number changed."""
    changed = 0
    for calculation in node.calculations:
        if (
            calculation.type != CalculationType.SINGLE_POINT
            or not calculation.geometry
            or calculation.id in by_hand
        ):
            continue
        found = levels.optimization_at(node, calculation.geometry, tolerance)
        new = found.level if found else None
        if calculation.geometry_level_id == (new.id if new else None):
            continue
        history.record(
            session,
            "calculation",
            calculation.id,
            "update",
            "geometry_level",
            levels.label(calculation.geometry_level),
            levels.label(new),
            source=SOURCE,
        )
        calculation.geometry_level_id = new.id if new else None
        changed += 1
    return changed


# D157: text that may mark a scan or a constraint in a Gaussian, ORCA or xTB output; only a
# file that has one is parsed again.
_MAY_BE_HELD = re.compile(
    rb"ModRedundant input section|\bScan\b|Frozen|NQMF=\s*[1-9]|NMMIF=\s*[1-9]|SURFACE SCAN|"
    rb"Parameter Scan|constrain|RELAXED SCAN|frozen atoms in %\s*:\s*\S+\s+[1-9]",
    re.IGNORECASE,
)


def constrained_steps(session: Session, folder: Path) -> dict[str, int]:
    """D157: before it, an optimization held by a scan, frozen coordinates or frozen atoms was
    imported like a free one and could give single points their geometry level. Read each
    calculation's copied file again for what held it, and set again the geometry level of the
    single points on the nodes that had one (not those set by hand). The node's role is left
    as it is; W-CONSTRAINED now shows on the calculation."""
    tolerance = app_settings.load().geometry_tolerance
    calculations = session.scalars(
        select(Calculation).options(selectinload(Calculation.source_file))
    ).all()
    parsed: dict[str, Any] = {}
    counts = {"calculations": 0, "single_points": 0}
    affected: dict[str, Node] = {}
    for calculation in calculations:
        source = calculation.source_file
        if source is None or calculation.step_index is None or calculation.constraints:
            continue
        if source.id not in parsed:
            parsed[source.id] = None
            try:
                data = (folder / source.stored_path).read_bytes()
                if _MAY_BE_HELD.search(data):
                    parsed[source.id] = imports.read_file(
                        data.decode("utf-8", errors="replace"), source.original_name
                    )
            except (OSError, imports.ImportFailed):
                pass
        steps = getattr(parsed[source.id], "steps", None) or []
        step = next((s for s in steps if s.index == calculation.step_index), None)
        if step is None or not step.constraints:
            continue
        calculation.constraints = list(step.constraints)
        counts["calculations"] += 1
        if calculation.type in levels.OPTIMIZATION_TYPES and calculation.irc_part is None:
            affected[calculation.node_id] = calculation.node
    session.flush()
    by_hand = _set_by_hand(session)
    for node in affected.values():
        counts["single_points"] += _single_point_levels(session, node, tolerance, by_hand)
    return counts


def scan_path_edges(session: Session, folder: Path) -> dict[str, int]:
    """D121: scan path nodes imported from cloud jobs before they remembered their edge."""
    from chembook3d.services import scan_edges  # it imports the parsers through trajectory

    return scan_edges.link_imported(session, folder)


def _steps_again(
    session: Session, folder: Path, query, needle: bytes | None = None
) -> Iterator[tuple[Calculation, Any]]:
    """Each calculation the query finds with the step it came from, read again from its copied
    file (each file read once, and only when it holds `needle`); calculations whose file or
    step is gone are left out."""
    parsed: dict[str, Any] = {}
    for calculation in session.scalars(
        query.options(selectinload(Calculation.result), selectinload(Calculation.source_file))
    ):
        source, result = calculation.source_file, calculation.result
        if source is None or result is None or calculation.step_index is None:
            continue
        if source.id not in parsed:
            try:
                data = (folder / source.stored_path).read_bytes()
                parsed[source.id] = None
                if needle is None or needle in data:
                    parsed[source.id] = imports.read_file(
                        data.decode("utf-8", errors="replace"), source.original_name
                    )
            except (OSError, imports.ImportFailed):
                parsed[source.id] = None
        steps = getattr(parsed[source.id], "steps", None) or []
        step = next((s for s in steps if s.index == calculation.step_index), None)
        if step is not None:
            yield calculation, step


def frequency_stationary(session: Session, folder: Path) -> dict[str, int]:
    """D158: Gaussian frequency jobs imported before the app kept the stationary-point check
    printed after them (W-FREQ-NONSTAT), read again from their copied files."""
    query = select(Calculation).where(
        Calculation.type == CalculationType.FREQUENCY, Calculation.program == "Gaussian"
    )
    counts = {"checked": 0, "not_stationary": 0}
    for calculation, step in _steps_again(session, folder, query):
        counts["checked"] += 1
        if NOT_STATIONARY in step.printed:
            unmet = step.printed[NOT_STATIONARY]
            result = calculation.result
            result.printed = {**(result.printed or {}), NOT_STATIONARY: unmet}
            counts["not_stationary"] += 1
    return counts


def spin_values(session: Session, folder: Path) -> dict[str, int]:
    """D162: Gaussian and ORCA calculations imported before the app kept the ⟨S²⟩ they printed
    (W-SPIN), read again from their copied files."""
    query = select(Calculation).where(Calculation.program.in_(("Gaussian", "ORCA")))
    counts = {"checked": 0, "with_s2": 0}
    for calculation, step in _steps_again(session, folder, query, b"S**2"):
        counts["checked"] += 1
        found = {key: step.printed[key] for key in SPIN_KEYS if key in step.printed}
        if found:
            result = calculation.result
            kept = {k: v for k, v in (result.printed or {}).items() if k not in SPIN_KEYS}
            result.printed = {**kept, **found}
            counts["with_s2"] += 1
    return counts


def thermochemistry(session: Session, folder: Path) -> bool:
    """D159: an investigation saved before it kept its own G_qh temperature, cutoff and
    standard state takes the app's settings once, recorded in its history, so nothing shifts."""
    return thermo_settings.seed(session, source=SOURCE)


def counterpoise_energies(session: Session, folder: Path) -> dict[str, int]:
    """D161: Gaussian counterpoise jobs imported before the app read the corrected total kept
    the last fragment's SCF energy; their energy, BSSE and W-PARSE are read again from their
    copied files."""
    query = select(Calculation).where(Calculation.program == "Gaussian")
    counts = {"checked": 0, "corrected": 0, "missing": 0}
    for calculation, step in _steps_again(session, folder, query, b"Counterpoise"):
        if step.route is None or not step.route.counterpoise:
            continue
        counts["checked"] += 1
        result = calculation.result
        kept = {k: v for k, v in (result.printed or {}).items() if k not in (BSSE, "energy")}
        found = {k: step.printed[k] for k in (BSSE, "energy") if k in step.printed}
        result.printed = {**kept, **found, "energy_label": step.energy_label}
        result.energy = step.scf_energy
        warnings = [w for w in calculation.parse_warnings if w != "SCF energy"]
        if step.energy_missing:
            warnings.append(step.energy_missing)
            counts["missing"] += 1
        else:
            counts["corrected"] += 1
        calculation.parse_warnings = list(dict.fromkeys(warnings))
    return counts


def thermo_models(session: Session, folder: Path) -> dict[str, int]:
    """D163: frequency jobs imported before the app kept the model of their printed G and the
    frequency scale factor (W-SCALE, W-G-MODEL), read again from their copied files. One whose
    file is gone keeps its program's model and a scale of 1."""
    query = select(Calculation).where(Calculation.type == CalculationType.FREQUENCY)
    counts = {"checked": 0, "scaled": 0}
    for calculation, step in _steps_again(session, folder, query):
        found = {key: step.printed[key] for key in THERMO_MODEL_KEYS if key in step.printed}
        if not found:
            continue
        counts["checked"] += 1
        result = calculation.result
        result.printed = {**(result.printed or {}), **found}
        if float(found.get(FREQUENCY_SCALE, "1")) != 1.0:
            counts["scaled"] += 1
    return counts


def start_geometries(session: Session, folder: Path) -> dict[str, int]:
    """D168: optimizations imported before they kept their start geometry. It matters only on
    a node with an optimization that did not end on the node's geometry (a pre-optimization a
    later optimization may have continued), so only those nodes' files are read again."""
    tolerance = app_settings.load().geometry_tolerance
    nodes = session.scalars(
        select(Node).options(selectinload(Node.calculations).selectinload(Calculation.source_file))
    ).all()
    counts = {"nodes": 0, "optimizations": 0}
    for node in nodes:
        optimizations = [
            c
            for c in node.calculations
            if c.type in levels.OPTIMIZATION_TYPES and c.start_geometry is None
        ]
        if len(optimizations) < 2 or all(
            geometry.matches(c.geometry, node.geometry, tolerance) for c in optimizations
        ):
            continue
        starts = _start_geometries(folder, optimizations)
        for calculation in optimizations:
            if calculation.id in starts:
                calculation.start_geometry = starts[calculation.id]
                counts["optimizations"] += 1
        counts["nodes"] += 1
    return counts


def drawer_profiles(session: Session, folder: Path) -> int:
    """D190: the pathways an investigation's drawer held before saved profiles become
    "Profile 1"; the count of pathways moved."""
    return energy_profiles.from_drawer(session)


def _rekeyed(key: str | None, moved: dict[str, str]) -> str | None:
    """A composite level key `levelId~geometryLevelId` with moved levels replaced."""
    if not key:
        return key
    return "~".join(moved.get(part, part) for part in key.split("~"))


def dispersion_without_model(session: Session, folder: Path) -> dict[str, int]:
    """D203: a Gaussian route with dispersion parameters set by IOp(3/174–179) but no
    dispersion model (no EmpiricalDispersion and no IOp(3/124)) adds no dispersion, yet the
    app named those IOps as a dispersion (D59). Each such level becomes the same level
    without dispersion, for every calculation and every saved level key (selectivities,
    turnovers, conformer equilibria and the remembered view); the name and the old levels go."""
    names = [
        d
        for d in session.scalars(select(CustomDispersion))
        if not d.base and "3/124" not in (d.iops or {})
    ]
    counts = {"dispersions": 0, "levels": 0, "calculations": 0}
    moved: dict[str, str] = {}
    for dispersion in names:
        for level in session.scalars(
            select(LevelOfTheory).where(LevelOfTheory.dispersion == dispersion.name)
        ).all():
            plain = levels.get_or_create(session, {**levels.fields_of(level), "dispersion": ""})
            moved[level.id] = plain.id
            counts["levels"] += 1
        counts["dispersions"] += 1
    if not moved:
        return counts
    for calculation in session.scalars(
        select(Calculation).where(
            Calculation.level_id.in_(moved) | Calculation.geometry_level_id.in_(moved)
        )
    ).all():
        for field in ("level", "geometry_level"):
            old = getattr(calculation, field)
            if old is None or old.id not in moved:
                continue
            new = session.get(LevelOfTheory, moved[old.id])
            history.record(
                session,
                "calculation",
                calculation.id,
                "update",
                field,
                levels.label(old),
                levels.label(new),
                source=SOURCE,
            )
            setattr(calculation, f"{field}_id", new.id)
        counts["calculations"] += 1
    for model in (Selectivity, Turnover, ConformerEquilibrium):
        for record in session.scalars(select(model)):
            record.level = _rekeyed(record.level, moved)
    info = session.get(InvestigationInfo, 1)
    if info is not None and isinstance(info.view_state, dict) and info.view_state.get("level"):
        info.view_state = {**info.view_state, "level": _rekeyed(info.view_state["level"], moved)}
    session.flush()
    for level_id in moved:
        level = session.get(LevelOfTheory, level_id)
        if level is not None:
            session.delete(level)
    for dispersion in names:
        session.delete(dispersion)
    return counts


REPAIRS: dict[str, Callable[[Session, Path], Any]] = {
    "geometry_levels": geometry_levels,
    "scan_path_edges": scan_path_edges,
    "constrained_steps": constrained_steps,
    "frequency_stationary": frequency_stationary,
    "spin_values": spin_values,
    "thermochemistry": thermochemistry,
    "counterpoise_energies": counterpoise_energies,
    "thermo_models": thermo_models,
    "start_geometries": start_geometries,
    "energy_profiles": drawer_profiles,
    "dispersion_without_model": dispersion_without_model,
}


def run(session: Session, folder: Path) -> dict[str, Any]:
    """Run the repairs this investigation has not had yet, in the caller's transaction."""
    info = session.get(InvestigationInfo, 1)
    if info is None:
        return {}
    done = list(info.repairs or [])
    results: dict[str, Any] = {}
    for name, repair in REPAIRS.items():
        if name in done:
            continue
        results[name] = repair(session, folder)
        done.append(name)
        log.info("repair %s: %s", name, results[name])
    # In REPAIRS' order, whichever ran first; names this version does not know are kept.
    info.repairs = [n for n in REPAIRS if n in done] + [n for n in done if n not in REPAIRS]
    return results
