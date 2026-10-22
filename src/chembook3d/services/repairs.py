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
    HistoryEntry,
    InvestigationInfo,
    Node,
)
from chembook3d.parsers.common import BSSE, NOT_STATIONARY, SPIN_KEYS
from chembook3d.services import geometry, history, imports, levels, thermo_settings

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


REPAIRS: dict[str, Callable[[Session, Path], Any]] = {
    "geometry_levels": geometry_levels,
    "scan_path_edges": scan_path_edges,
    "constrained_steps": constrained_steps,
    "frequency_stationary": frequency_stationary,
    "spin_values": spin_values,
    "thermochemistry": thermochemistry,
    "counterpoise_energies": counterpoise_energies,
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
