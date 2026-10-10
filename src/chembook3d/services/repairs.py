"""One-off corrections of records an earlier version of the app got wrong. Each repair runs
once per investigation, when it is opened by a version that has it; its name is then kept in
`InvestigationInfo.repairs`. The database was backed up by the migration that came with it."""

import logging
from collections.abc import Callable
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
from chembook3d.services import geometry, history, imports, levels

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
            counts["single_points"] += 1
    return counts


def scan_path_edges(session: Session, folder: Path) -> dict[str, int]:
    """D121: scan path nodes imported from cloud jobs before they remembered their edge."""
    from chembook3d.services import scan_edges  # it imports the parsers through trajectory

    return scan_edges.link_imported(session, folder)


REPAIRS: dict[str, Callable[[Session, Path], Any]] = {
    "geometry_levels": geometry_levels,
    "scan_path_edges": scan_path_edges,
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
    info.repairs = done
    return results
