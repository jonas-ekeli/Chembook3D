"""Scan paths shown on the edge they were run on (D121, A68).

A scan path node (D112, D115) stays a node: it grows into the TS. It remembers its edge
(`Node.path_edge_id`, in the history), and the edge shows a chip for it: "xTB path · top
+12.4 kcal/mol", the path's top above its first kept point, both from the path itself (one
xTB level, so EN-3 holds), following its trimming (D117). The chip never feeds ΔX, profiles,
the energy table, energetic span or selectivity (D27, EN-3, INV-8); nothing is rewired.

A linked path node can be shown "on the edge only" (`Node.on_edge_only`, layout, no history,
like A29): its box leaves the canvas and the chip stands for it. The box comes back by
itself when the node gets a calculation that is not a scan path (it is becoming the TS), when
its edge is deleted, when it is unlinked, and while it has edges or a group of its own (so
nothing drawn goes missing).

D200: a scan imported from a program's own output (a Gaussian or ORCA scan,
`trajectory.is_held_scan`) is linked the same way. Its chip's top is measured over its
converged points; it is not trimmed (D117 stays with xTB paths and joined scans) and its
"Use this structure" still makes a derived node, since its energies are the node's (D157).
"""

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from chembook3d import cloud_jobs
from chembook3d.models import Calculation, CalculationType, GroupNode, Node, NodeKind, Transition
from chembook3d.parsers import joined, xtb
from chembook3d.services import history, trajectory
from chembook3d.services.records import RecordError

log = logging.getLogger(__name__)

FIELD = "path_edge_id"


def is_path(calculation: Calculation) -> bool:
    """What can be shown on an edge: a scan path (D112, D137) or a program's scan (D200)."""
    return trajectory.is_scan_path(calculation) or trajectory.is_held_scan(calculation)


def scan_paths(node: Node) -> list[Any]:
    """The node's scan path calculations (D112) and scans (D200), oldest first."""
    return [c for c in node.calculations if is_path(c)]


def paths_only(node: Node) -> bool:
    """D121, D200: every calculation on the node is a path that can be shown on an edge."""
    return bool(node.calculations) and all(is_path(c) for c in node.calculations)


def has_scan_path(session: Session, node_id: str) -> bool:
    """`scan_paths` without loading the node's calculations (asked for every node shown);
    only their constraints are read for D200's scans."""
    query = (
        select(Calculation.id)
        .where(
            Calculation.node_id == node_id,
            Calculation.type == CalculationType.OTHER,
            or_(
                (Calculation.program == xtb.PROGRAM)
                & Calculation.route.startswith(xtb.SCAN_ROUTE, autoescape=True),
                Calculation.route.startswith(joined.ROUTE, autoescape=True),
            ),
        )
        .limit(1)
    )
    if session.scalar(query) is not None:
        return True
    held = session.scalars(select(Calculation.constraints).where(Calculation.node_id == node_id))
    return any(trajectory.names_scan(constraints or []) for constraints in held)


def linked(session: Session, transition_id: str) -> list[Node]:
    """The path nodes linked to an edge, in creation order."""
    return list(
        session.scalars(select(Node).where(Node.path_edge_id == transition_id).order_by(Node.seq))
    )


def _has_edges(session: Session, node: Node) -> bool:
    return (
        session.scalar(
            select(Transition.id)
            .where((Transition.source_node_id == node.id) | (Transition.target_node_id == node.id))
            .limit(1)
        )
        is not None
    )


def on_edge(session: Session, node: Node) -> bool:
    """D121: the node's box is off the canvas and its edge's chip stands for it: it is set so,
    linked, still only paths (`paths_only`), and has no edges or group of its own."""
    return (
        node.on_edge_only
        and node.path_edge_id is not None
        and node.group_id is None
        and paths_only(node)
        and not _has_edges(session, node)
    )


def _middle(session: Session, transition: Transition) -> tuple[float, float] | None:
    """Halfway along the edge, between where its ends are drawn."""
    places = []
    for node_id, group_id in (
        (transition.source_node_id, transition.source_group_id),
        (transition.target_node_id, transition.target_group_id),
    ):
        end = session.get(Node, node_id) if node_id else session.get(GroupNode, group_id)
        if end is None:
            return None
        group = (
            session.get(GroupNode, end.group_id) if isinstance(end, Node) and end.group_id else None
        )
        places.append((group.pos_x, group.pos_y) if group else (end.pos_x, end.pos_y))
    (ax, ay), (bx, by) = places
    return (ax + bx) / 2, (ay + by) / 2


def _show(session: Session, node: Node, transition: Transition | None) -> None:
    """Puts the box back on the canvas, halfway along its edge when it was off it."""
    if not node.on_edge_only:
        return
    if (
        transition is not None
        and on_edge(session, node)
        and (place := _middle(session, transition))
    ):
        node.pos_x, node.pos_y = place
    node.on_edge_only = False


def link(
    session: Session, node_id: str, edge_id: str | None, on_edge_only: bool | None = None
) -> Node:
    """D121: shows a path node on an edge (or on none), in the history; `on_edge_only` takes
    its box off the canvas or puts it back (layout, no history)."""
    node = session.get(Node, node_id)
    if node is None:
        raise RecordError("Node not found")
    if edge_id is not None and edge_id != node.path_edge_id:
        if node.kind == NodeKind.SPECIES:
            raise RecordError("A free species has no scan path (D69)")
        if not scan_paths(node):
            raise RecordError(
                "Only a node with a scan (an xTB path.xyz or xtbscan.log, a joined scan, or a "
                "Gaussian or ORCA scan) can be shown on an edge"
            )
        if session.get(Transition, edge_id) is None:
            raise RecordError("Transition not found")
    if edge_id != node.path_edge_id:
        old = node.path_edge_id
        if edge_id is None:
            _show(session, node, session.get(Transition, old) if old else None)
        history.record(session, "node", node.id, "update", FIELD, old, edge_id)
        node.path_edge_id = edge_id
    if on_edge_only is not None and on_edge_only != node.on_edge_only:
        if on_edge_only and node.path_edge_id is None:
            raise RecordError("Show the path on an edge first")
        if on_edge_only:
            node.on_edge_only = True
        else:
            _show(session, node, session.get(Transition, node.path_edge_id))
    session.flush()
    return node


def release(session: Session, transitions: list[Transition]) -> None:
    """INV-7, D121: an edge being deleted lets go of its paths: each path node is kept,
    unlinked (in the history), with its box back on the canvas."""
    for transition in transitions:
        for node in linked(session, transition.id):
            _show(session, node, transition)
            history.record(session, "node", node.id, "update", FIELD, transition.id, None)
            node.path_edge_id = None


def after_import(session: Session, node: Node) -> None:
    """D121: a path node shown on its edge only that gets a calculation which is not a scan
    path (it is becoming the TS) comes back on the canvas, halfway along its edge; the chip
    stays."""
    if not node.on_edge_only:
        return
    session.expire(node, ["calculations"])  # the import added them by id
    if not paths_only(node):
        transition = session.get(Transition, node.path_edge_id) if node.path_edge_id else None
        place = _middle(session, transition) if transition is not None else None
        if place:
            node.pos_x, node.pos_y = place
        node.on_edge_only = False


# ---------- what the chip and the edge panel show ----------


@dataclass
class PathOnEdge:
    node_id: str
    label: str
    calculation_id: str | None  # the scan path whose movie the edge panel plays
    top: float | None  # hartree: the path's top above its first kept point (D117's barrier);
    # for a program's scan (D200), above its first converged point
    gate: str | None  # "passed" or "missed" (D116, D118); None when no job says
    reached_end: bool | None
    active_rmsd: float | None  # Å, the end's RMSD over the reacting atoms (D118)
    end_rmsd: float | None  # Å, over all atoms
    on_edge: bool  # its box is off the canvas
    program: str = xtb.PROGRAM  # D137: a joined scan's is its pieces' (Gaussian, ORCA)


def _job_results(folder: Path) -> dict[str, dict[str, Any]]:
    """The `path` section of each imported scan path job's result.json, by node id."""
    found: dict[str, dict[str, Any]] = {}
    for job in cloud_jobs.list_jobs(folder):
        node_id = (job.get("imported") or {}).get("node_id")
        if job.get("kind") != "scan_path" or not node_id:
            continue
        try:
            text = (cloud_jobs.jobs_dir(folder) / job["id"] / cloud_jobs.RESULT).read_text(
                encoding="utf-8"
            )
            result = json.loads(text)
        except (OSError, ValueError):
            continue
        path = result.get("path") if isinstance(result, dict) else None
        if isinstance(path, dict):
            found[node_id] = path
    return found


def _number(value: Any) -> float | None:
    return float(value) if isinstance(value, int | float) and not isinstance(value, bool) else None


def _top(steps: trajectory.Steps) -> float | None:
    """The chip's top: a scan path's D117 barrier; for a program's scan (D200) the highest
    top over its converged points (`xtb.representative`, as a path's) above the first."""
    if steps.trim is not None:
        return steps.trim.barrier
    energies = [f.energy for f in steps.frames if f.converged]
    if len(energies) < 2 or None in energies:
        return None
    high = energies[xtb.representative(energies)]
    low = energies[0]
    assert high is not None and low is not None
    return high - low


def describe(session: Session, folder: Path | None, node: Node, path: dict[str, Any]) -> PathOnEdge:
    calculations = scan_paths(node)
    calculation = calculations[0] if calculations else None
    top = None
    if calculation is not None and folder is not None:
        try:
            top = _top(trajectory.read_steps(folder, calculation))
        except trajectory.StepsUnavailable:
            top = None
    gate = path.get("gate") if path.get("gate") in ("passed", "missed") else None
    reached = path.get("reached_end")
    return PathOnEdge(
        node_id=node.id,
        label=node.label,
        calculation_id=calculation.id if calculation else None,
        top=top,
        gate=gate,
        reached_end=reached if isinstance(reached, bool) else None,
        active_rmsd=_number(path.get("active_end_rmsd")),
        end_rmsd=_number(path.get("end_rmsd")),
        on_edge=on_edge(session, node),
        program=calculation.program if calculation is not None else xtb.PROGRAM,
    )


def on_edges(session: Session, folder: Path | None) -> dict[str, list[PathOnEdge]]:
    """Every edge's linked paths, by edge id."""
    nodes = list(
        session.scalars(select(Node).where(Node.path_edge_id.is_not(None)).order_by(Node.seq))
    )
    if not nodes:
        return {}
    results = _job_results(folder) if folder is not None else {}
    found: dict[str, list[PathOnEdge]] = {}
    for node in nodes:
        found.setdefault(node.path_edge_id, []).append(
            describe(session, folder, node, results.get(node.id, {}))
        )
    return found


# ---------- linking the paths imported before D121 ----------


def link_imported(session: Session, folder: Path) -> dict[str, int]:
    """D121: path nodes imported from cloud jobs before the link existed are linked once to
    the edge their job was run on (`imported.node_id` → `scan_path.edge_id`), when both are
    still there; they stay on the canvas."""
    count = 0
    for job in cloud_jobs.list_jobs(folder):
        if job.get("kind") != "scan_path":
            continue
        node_id = (job.get("imported") or {}).get("node_id")
        edge_id = (job.get("scan_path") or {}).get("edge_id")
        node = session.get(Node, node_id) if isinstance(node_id, str) else None
        if node is None or node.path_edge_id is not None or not isinstance(edge_id, str):
            continue
        if session.get(Transition, edge_id) is None or not scan_paths(node):
            continue
        history.record(session, "node", node.id, "update", FIELD, None, edge_id, source="repair")
        node.path_edge_id = edge_id
        count += 1
    if count:
        log.info("linked %d scan paths to their edges", count)
    return {"linked": count}
