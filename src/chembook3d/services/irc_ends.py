"""Optimizations run from a TS's IRC end, told apart from ordinary minima (D131, A192).

D131 keeps an IRC end point's optimization and frequency job as IRC parts of its TS
(`irc_part` "end_opt"/"end_freq"), never a node of its own; A79 left them out of the batch
import, as an optimization alone cannot tell it was run from an IRC end. A folder can tell: a
file is plainly the optimization of an IRC's end when

- its name shares the IRC file's stem up to "IRC" (`stem`: "TS-3_IRC_reverse_END_opt" and
  "TS-3_IRC_reverse" share "ts-3_irc"), and
- its first or its final structure is that IRC direction's last point within the duplicate
  tolerance (D62), whatever the atom order.

The batch import sends such a file into its TS's IRC as that direction's end point
(`batch_ends`, `node_end`); D268: so does the single import (`routed`, `plan`), the app's
dialog and Claude's import tools alike, unless `irc_end` is false (a node of its own). A node
made from one before (`end_nodes`) is never offered as a partner of another TS
(`find_ts_partners`, a mode-follow's ends), and neither is a folder file of this kind
(`folder_survey.final_structures`).
"""

import re
from dataclasses import dataclass, replace
from pathlib import Path, PurePath
from typing import Any

from sqlalchemy.orm import Session

from chembook3d.models import Node, Role
from chembook3d.parsers.common import ParsedStep
from chembook3d.services import imports, thermo_settings
from chembook3d.services import nodes as node_service
from chembook3d.services import structure_compare as compare
from chembook3d.services.geometry import Rows

_STEM = re.compile(r"^(.*?(?<![a-z])irc)(?![a-z])")
DIRECTIONS = ("forward", "reverse")


def stem(name: str) -> str | None:
    """The file name up to and including the word IRC, in lower case; None without it."""
    found = _STEM.match(PurePath(name).stem.lower())
    return found.group(1) if found else None


def last_points(steps: list[ParsedStep]) -> dict[str, Rows]:
    """Per direction, the last point of an IRC file's paths."""
    out: dict[str, Rows] = {}
    for step in steps:
        if step.irc is None:
            continue
        for direction in DIRECTIONS:
            if direction in out:
                continue
            try:
                out[direction] = compare._irc_end(step, direction, "the IRC")
            except compare.StructureError:
                continue
    return out


def _structures(steps: list[ParsedStep]) -> list[Rows]:
    """A file's first and final structures."""
    first = next((s for s in steps if s.geometries), None)
    last = next((s for s in reversed(steps) if s.final_geometry), None)
    out = []
    if first is not None:
        out.append(imports._rows(first.geometries[0]))
    if last is not None:
        out.append(imports._rows(last.final_geometry))
    return [r for r in out if r]


def is_end_job(steps: list[ParsedStep]) -> bool:
    """An optimization or frequency job, and no IRC itself."""
    return any(s.irc is not None for s in steps) is False and any(
        imports.irc_part_of(s, "end") for s in steps
    )


def matched(steps: list[ParsedStep], points: dict[str, Rows], tolerance: float) -> tuple | None:
    """The direction whose last point the file's first or final structure is (D62), and the
    RMSD; the nearest when both are."""
    best = None
    for rows in _structures(steps):
        for direction, end in points.items():
            same = compare.same_structure(end, rows, tolerance)
            if same is not None and (best is None or same.rmsd < best[1]):
                best = (direction, same.rmsd)
    return best


@dataclass
class BatchEnd:
    irc_file_id: str  # the IRC file of the batch it was run from
    direction: str
    rmsd: float


def batch_ends(files: list[Any], tolerance: float) -> dict[str, BatchEnd]:
    """A192: the batch's files that are plainly an IRC end's optimization of an IRC file in the
    same batch (`batch_import.BatchFile`s), by file id."""
    ircs = []
    for item in files:
        parsed = item.staged.parsed
        steps = getattr(parsed, "steps", None)
        if not steps or not any(s.irc is not None for s in steps):
            continue
        found = stem(item.staged.original_name)
        if found:
            ircs.append((item.id, found, last_points(steps)))
    out: dict[str, BatchEnd] = {}
    if not ircs:
        return out
    for item in files:
        steps = getattr(item.staged.parsed, "steps", None)
        if not steps or not is_end_job(steps):
            continue
        own = stem(item.staged.original_name)
        if own is None:
            continue
        for irc_id, irc_stem, points in ircs:
            if irc_stem != own or not points:
                continue
            found = matched(steps, points, tolerance)
            if found is not None and (item.id not in out or found[1] < out[item.id].rmsd):
                out[item.id] = BatchEnd(irc_id, found[0], found[1])
    return out


def ts_ircs(session: Session, folder: Path) -> list[tuple[Node, str, dict[str, Rows]]]:
    """Each TS node's IRC paths with a stem: the node, the stem and the paths' last points."""
    from chembook3d.services import irc  # it imports much of the app

    out = []
    for node in node_service.list_nodes(session, with_calculations=True):
        if node.role != Role.TRANSITION_STATE:
            continue
        for calc in node.irc_calculations:
            if calc.irc_part != "path" or calc.source_file is None:
                continue
            found = stem(calc.source_file.original_name or "")
            if not found:
                continue
            read = irc._read_step(folder, calc)
            if read is None:
                continue
            points = last_points([read[0]])
            if points:
                out.append((node, found, points))
    return out


def node_end(
    name: str,
    steps: list[ParsedStep],
    ircs: list[tuple[Node, str, dict[str, Rows]]],
    tolerance: float,
) -> tuple[Node, str] | None:
    """A192: the TS node (and direction) whose IRC, imported before (`ircs`, from `ts_ircs`),
    the file is plainly the end point's job of."""
    own = stem(name)
    if own is None or not is_end_job(steps):
        return None
    best = None
    for node, found, points in ircs:
        if found != own:
            continue
        hit = matched(steps, points, tolerance)
        if hit is not None and (best is None or hit[1] < best[2]):
            best = (node, hit[0], hit[1])
    return (best[0], best[1]) if best else None


def _steps(folder: Path, source: Any) -> list[ParsedStep]:
    """A stored file's steps, read as the IRC window reads them (cached by time and size)."""
    from chembook3d.services import files as file_service
    from chembook3d.services import trajectory

    path = file_service.absolute_path(folder, source)
    try:
        stat = path.stat()
        parsed = trajectory._read(str(path), stat.st_mtime_ns, stat.st_size)
    except (OSError, imports.ImportFailed):
        return []
    return list(getattr(parsed, "steps", None) or [])


def end_nodes(session: Session, folder: Path, tolerance: float) -> dict[str, str]:
    """A192: nodes made from a TS's IRC end optimization (a file of the node whose stem is the
    TS's IRC file's and whose first or final structure is that IRC's last point), by node id:
    the TS node's id."""
    ircs = ts_ircs(session, folder)
    if not ircs:
        return {}
    stems = {found for _, found, _ in ircs}
    out: dict[str, str] = {}
    for node in node_service.list_nodes(session, with_calculations=True):
        if not node.geometry or node.role == Role.TRANSITION_STATE:
            continue
        sources = {
            c.source_file.id: c.source_file
            for c in node.calculations
            if c.source_file is not None and stem(c.source_file.original_name or "") in stems
        }
        for source in sources.values():
            own = stem(source.original_name or "")
            steps = _steps(folder, source)
            hit = next(
                (
                    ts
                    for ts, found, points in ircs
                    if found == own and ts.id != node.id and matched(steps, points, tolerance)
                ),
                None,
            )
            if hit is not None:
                out[node.id] = hit.id
                break
    return out


REASON = "optimized from its IRC end"  # A192: the batch row's words for `irc_end`


def routed(
    session: Session, folder: Path, staged: Any, options: imports.ImportOptions
) -> tuple[imports.ImportOptions, dict[str, Any] | None]:
    """D268, A192: a single file that is plainly the end point's job of an IRC a TS node holds
    goes into that TS's IRC (D131's end_opt and end_freq): the options for it, and what the
    plan says of it (`irc_end`: the TS, the direction and why). Unchanged when the options name
    an IRC part already, another node, a free species, or `irc_end` false (a node of its own)."""
    if options.irc_part is not None or options.irc_end is False or options.kind == "species":
        return options, None
    steps = getattr(staged.parsed, "steps", None)
    name = staged.original_name
    if not steps or stem(name) is None or not is_end_job(steps):
        return options, None
    tolerance = thermo_settings.settings(session).duplicate_tolerance
    found = node_end(name, steps, ts_ircs(session, folder), tolerance)
    if found is None:
        return options, None
    node, direction = found
    if options.target_node_id not in (None, node.id):
        return options, None
    chosen = replace(options, irc_part="end", irc_direction=direction, target_node_id=node.id)
    return chosen, {
        "node_id": node.id,
        "label": node.label,
        "direction": direction,
        "reason": REASON,
    }


def plan(session: Session, folder: Path, staged: Any, options: imports.ImportOptions) -> dict:
    """D268: a single import's plan, an IRC end's file planned into its TS's IRC (`routed`)."""
    chosen, irc_end = routed(session, folder, staged, options)
    found = imports.plan(session, staged, chosen).as_dict()
    found["irc_end"] = irc_end
    return found
