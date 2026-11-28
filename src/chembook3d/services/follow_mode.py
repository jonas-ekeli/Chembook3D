"""The ends of a mode-follow job matched against the structures there are (D263c, A184, A188).

A `follow_mode` local job (`local_jobs.create_follow_mode`) displaces a TS along its imaginary
mode on each side (the atom moving most moves 0.4 Å, A188) and relaxes each end for at most 60
steps. Its result is information, never imported: here, on demand, each end is compared with
the TS (the bonds that form and break, `structure_compare.compare`) and matched against the open
investigation's nodes and, when the job named one, a folder's outputs
(`folder_survey.final_structures`, read again only when the folder's files change). An end
matches a structure when no real bond changes between them (A181's rule, weak and metal–hydrogen
contacts left out) and the atom-matched RMSD is under 0.5 Å, whatever the atom order (A184,
`structure_compare.end_match`); formula, charge and multiplicity, the order-independent bound
and the bond counts rule most candidates out before any atoms are matched. A188: a TS (a node,
or a file of one TS structure) is never a match, nor a node A186 shows is no minimum; an end
whose distances along the mode are still the TS's (`structure_compare._side` "none") returned
to the TS and matches nothing. A191: an end matching several members of one group stands for
the group, one matching several conformers of one molecule otherwise for the nearest (a
medium link); a soft mode (below 100 cm⁻¹) whose end changes no bond and matches nothing
within 0.5 Å takes the nearest minimum with its bonds within 1.0 Å (medium); A193: so does any
other end that left the TS (`_wider`, "conformer": another conformer of a molecule there is,
medium), and `make_node` refuses it. A192: a node or folder file made from another TS's IRC
end optimization is never an end's match. D267: no match turns a stereocentre into its mirror
image (`end_match`); an end that matches nothing but a node reflected (`atom_matching.mirrored`,
within 0.5 Å) says `mirror_of`: that node's mirror image is missing.

The verdict: both ends each matching one structure links them (high confidence, A184); both
matching the same one is flagged; both returned to a TS is said; an end that matches nothing
even within 1.0 Å may become a node of its own (`make_node`), coordinates only: a partial
relaxation is a guess, never a result.
"""

import threading
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from chembook3d import local_jobs, xyz
from chembook3d.models import GroupNode, LevelOfTheory, Node, Role, Status
from chembook3d.services import (
    atom_matching,
    batch_import,
    folder_survey,
    irc_ends,
    levels,
    thermo_settings,
)
from chembook3d.services import nodes as node_service
from chembook3d.services import structure_compare as compare
from chembook3d.services.geometry import Rows

ENDS = ("+", "-")
SHOWN = {"+": "+", "-": "−"}
NAMES = {"+": "+", "plus": "+", "-": "-", "−": "-", "minus": "-"}
NODE_DX = 260.0  # px: a node made from an end sits this far beside the TS, + right, − left
NODE_DY = 120.0  # px: and this far below it
SOFT_MODE = 100.0  # A191: an imaginary mode below this (cm⁻¹) that changes no bond is soft
SOFT_RMSD = 1.0  # A191: a soft mode's end takes the nearest minimum with its bonds within this


@dataclass
class _Candidate:
    """A structure an end may match, with its cheap prints made once per call."""

    rows: Rows
    charge: int | None
    multiplicity: int | None
    entry: dict[str, Any]
    print_: compare.Print | None = None
    counts: Any = None

    def prepared(self) -> "_Candidate":
        if self.print_ is None:
            self.print_ = compare.signature(self.rows)
            self.counts = compare._bond_counts(self.rows)
        return self


def _formula(rows: Rows) -> str:
    return xyz.formula_of(Counter(r[0] for r in rows))


def _compatible(candidate: _Candidate, charge: int | None, multiplicity: int | None) -> bool:
    """Charge and multiplicity agree where both are known."""
    return all(
        a is None or b is None or a == b
        for a, b in ((candidate.charge, charge), (candidate.multiplicity, multiplicity))
    )


def _matches(
    rows: Rows,
    charge: int | None,
    multiplicity: int | None,
    candidates: list[_Candidate],
    window: float = compare.END_RMSD,
) -> list[dict[str, Any]]:
    """The candidates the end matches (A184; within `window`, A191), nearest first, those at
    another level than the TS's after the others (A194)."""
    formula = _formula(rows)
    shape = (compare.signature(rows), compare._bond_counts(rows))
    found = []
    for candidate in candidates:
        if candidate.entry.get("formula") != formula:
            continue
        if not _compatible(candidate, charge, multiplicity):
            continue
        candidate.prepared()
        same = compare.end_match(
            rows,
            candidate.rows,
            (shape[0], candidate.print_),
            (shape[1], candidate.counts),
            window,
        )
        if same is None:
            continue
        entry = {k: v for k, v in candidate.entry.items() if k != "formula"}
        entry["rmsd"] = round(same.rmsd, 3)
        if same.mapping is not None:
            entry["renumbered"] = True
        found.append(entry)
    found.sort(key=lambda m: (bool(m.get("other_level")), m["rmsd"]))
    return found


def _is_ts(entry: dict[str, Any]) -> bool:
    return entry.get("role") == Role.TRANSITION_STATE.value and entry.get("kind") is None


HELD = ("scan path", "scan or constrained geometry", "imaginary frequency")


def _node_candidates(
    session: Session,
    exclude: str | None,
    folder: Path | None = None,
    level: tuple[str, int] | None = None,
) -> list[_Candidate]:
    """Every node with coordinates but `exclude` (the TS followed), those A186 shows are no
    minimum (a scan path, a scan or constrained geometry, an imaginary frequency: A188) and,
    with the investigation's `folder`, the nodes made from another TS's IRC end optimization
    (A192); TS nodes among them are told apart by `_is_ts` and never an end's match, nor is a
    node of no role at a TS node's structure (a single point on it, A193 as A188's files).
    A194: a node whose structure is only at levels of a lower kind of method than the TS's
    geometry level `level` (id, `levels.method_rank`), an xTB conformer search's member under
    a DFT TS, say, is marked `other_level`."""
    from chembook3d.services import ts_partners  # it imports gaps, which imports this module

    tolerance = thermo_settings.settings(session).duplicate_tolerance
    ends = irc_ends.end_nodes(session, folder, tolerance) if folder is not None else {}
    groups = {g.id: g.label for g in session.scalars(select(GroupNode))}
    every = node_service.list_nodes(session, with_calculations=True)
    at_ts = [
        [list(r) for r in n.geometry]
        for n in every
        if n.role == Role.TRANSITION_STATE and n.geometry
    ]
    out = []
    for node in every:
        if not node.geometry or node.id == exclude:
            continue
        if ts_partners.left_out(node, tolerance) in HELD:
            continue
        if ends.get(node.id, exclude) != exclude:  # A192: another TS's IRC end optimization
            continue
        rows = [list(r) for r in node.geometry]
        role = str(node.role) if node.role else None
        if role in (None, Role.UNSPECIFIED.value) and any(
            _formula(ts) == _formula(rows) and compare.same_structure(ts, rows, tolerance)
            for ts in at_ts
        ):
            role = Role.TRANSITION_STATE.value
        entry = {
            "node_id": node.id,
            "label": node.label or "Untitled node",
            "role": role,
            "formula": _formula(rows),
        }
        if node.kind != "node":
            entry["kind"] = str(node.kind)
        if node.group_id is not None:
            entry["group_id"] = node.group_id
            entry["group"] = groups.get(node.group_id) or "Untitled group"
        held = ts_partners.held_levels(node) if level is not None else {}
        if (
            level is not None
            and held
            and level[0] not in held
            and max(levels.method_rank(levels.fields_of(lv)) for lv in held.values()) < level[1]
        ):
            entry["other_level"] = True
        out.append(_Candidate(rows, node.charge, node.multiplicity, entry))
    return out


# A188: a folder's final structures, read once while none of its files changes (each read
# parses every output in it: about a minute for a few hundred DFT outputs, once per end asked).
_FILES: dict[tuple[str, str, float], tuple[tuple, list[_Candidate]]] = {}
_FILES_LOCK = threading.Lock()
KEPT_FOLDERS = 4


def _signature(folder: Path, investigation: Path) -> tuple:
    """What a folder's files are: each path, size and time of change."""
    out = []
    skip = investigation.resolve() if investigation else None
    for path in batch_import._candidates(folder.expanduser().resolve(), True, skip):
        try:
            stat = path.stat()
        except OSError:
            continue
        out.append((str(path), stat.st_size, stat.st_mtime_ns))
    return tuple(out)


def _file_candidates(folder: Path, investigation: Path, tolerance: float) -> list[_Candidate]:
    """The folder's final structures, read again only when its files changed. Files ending on
    one structure (an optimization and the single point on it, a restart) share a `structure`
    number, so an end matching them all matches one structure."""
    key = (str(folder), str(investigation), tolerance)
    signature = _signature(folder, investigation)
    with _FILES_LOCK:
        kept = _FILES.get(key)
        if kept is not None and kept[0] == signature:
            return kept[1]
    out = _read_files(folder, investigation, tolerance)
    with _FILES_LOCK:
        _FILES[key] = (signature, out)
        while len(_FILES) > KEPT_FOLDERS:
            _FILES.pop(next(iter(_FILES)))
    return out


def _read_files(folder: Path, investigation: Path, tolerance: float) -> list[_Candidate]:
    out: list[_Candidate] = []
    kept: list[_Candidate] = []  # one per structure
    for final in folder_survey.final_structures(folder, True, investigation, tolerance):
        entry = {"path": final.path, "step": final.step, "formula": _formula(final.rows)}
        if final.role == Role.TRANSITION_STATE.value:
            entry["role"] = final.role
        if final.irc_end:
            entry["irc_end"] = final.irc_end
        candidate = _Candidate(final.rows, final.charge, final.multiplicity, entry).prepared()
        same = next(
            (
                k
                for k in kept
                if k.entry["formula"] == entry["formula"]
                and _compatible(k, final.charge, final.multiplicity)
                and compare.same_structure(
                    k.rows, final.rows, tolerance, (k.print_, candidate.print_)
                )
                is not None
            ),
            None,
        )
        if same is None:
            entry["structure"] = len(kept) + 1
            kept.append(candidate)
        else:
            entry["structure"] = same.entry["structure"]
        out.append(candidate)
    # A188: a single point on a TS's structure is that TS too, never an end's match.
    at_ts = {c.entry["structure"] for c in out if c.entry.get("role")}
    for candidate in out:
        if candidate.entry["structure"] in at_ts:
            candidate.entry["role"] = Role.TRANSITION_STATE.value
    return out


@dataclass
class _Job:
    job: dict[str, Any]
    status: dict[str, Any]
    follow: dict[str, Any]
    ends: dict[str, dict[str, Any]]
    ts_rows: Rows
    ts_label: str
    ts_node: Node | None


def _read(session: Session, folder: Path, job_id: str) -> _Job:
    status = local_jobs.job_status(folder, job_id)
    job = local_jobs.read_job(folder, job_id)
    follow = job.get("follow_mode")
    if job.get("kind") != local_jobs.FOLLOW_MODE or not follow:
        raise local_jobs.LocalJobError(f"Job {job_id} does not follow a mode (D263c)")
    result = local_jobs._read(local_jobs.job_dir(folder, job_id) / local_jobs.RESULT)
    ends = ((result or {}).get("follow_mode") or {}).get("ends") or {}
    if not all(name in ends for name in ENDS):
        raise local_jobs.LocalJobError(
            f"The job has no ends to match yet ({status['status']}); "
            + ("wait until it has finished" if status["active"] else "start it again")
        )
    elements = list(job["elements"])
    ts_rows = [[e, *map(float, c)] for e, c in zip(elements, job["coordinates"], strict=True)]
    node = session.get(Node, follow.get("ts_node_id") or job["node_id"])
    label = (node.label if node is not None else None) or follow.get("ts_label") or "the TS"
    return _Job(job, status, follow, ends, ts_rows, label, node)


def _mode(found: _Job) -> Any:
    """The followed mode's active pairs (`structure_compare._active`), in the TS's numbering."""
    mode = found.follow.get("mode")
    if not mode:
        return None
    ts = compare.Structure(
        found.ts_label,
        found.ts_rows,
        frequencies=[float(found.follow.get("wavenumber") or -1.0)],
        modes=[mode],
        mode_rows=found.ts_rows,
    )
    return compare._active(ts)


def _end_rows(found: _Job, name: str) -> Rows:
    coords = found.ends[name]["coordinates"]
    return [[e, *map(float, c)] for e, c in zip(found.job["elements"], coords, strict=True)]


def _bonds(found: _Job, name: str) -> dict[str, Any]:
    """The end against the TS: the bonds formed and broken by the 3D view's rule (a TS's
    forming and breaking bonds are partial, so A181's weak changes count here, as compare's
    `count_weak`) and how far it moved."""
    ts = compare.Structure(found.ts_label, found.ts_rows)
    end = compare.Structure(f"{SHOWN[name]} end", _end_rows(found, name))
    compared = compare.compare(ts, end, count_weak=True)
    return {
        "formed": [{"bond": b["bond"], "ts": b["a"], "end": b["b"]} for b in compared["formed"]],
        "broken": [{"bond": b["bond"], "ts": b["a"], "end": b["b"]} for b in compared["broken"]],
        "rmsd_from_ts": compared.get("rmsd"),
    }


def _key(settled: dict[str, Any] | None) -> str | None:
    """The structure an end settled on, as a key: its group, node or folder structure."""
    if settled is None:
        return None
    if settled.get("as") == "group":
        return f"group:{settled['group_id']}"
    if "node_id" in settled:
        return f"node:{settled['node_id']}"
    return f"file:{settled['structure']}"


def _settle(entry: dict[str, Any]) -> None:
    """A191: the one structure an end stands for. One match is it; several, all in one group,
    are the group; several of one molecule otherwise (every match has the end's bonds, so they
    are conformers of one molecule) are the nearest, "and N other conformers"; nodes before
    the folder's files."""
    matches = entry["matches"]
    if matches:
        groups = {m.get("group_id") for m in matches}
        if len(matches) == 1:
            entry["settled"] = {**matches[0], "as": "one"}
        elif len(groups) == 1 and None not in groups:
            first = matches[0]
            entry["settled"] = {
                "group_id": first["group_id"],
                "label": first["group"],
                "rmsd": first["rmsd"],
                "as": "group",
                "members": len(matches),
            }
            if all(m.get("other_level") for m in matches):
                entry["settled"]["other_level"] = True
        else:
            entry["settled"] = {**matches[0], "as": "nearest", "others": len(matches) - 1}
        return
    files = entry.get("files") or []
    structures = list(dict.fromkeys(f["structure"] for f in files))
    if len(structures) == 1:
        entry["settled"] = {**files[0], "as": "one"}
    elif structures:
        entry["settled"] = {**files[0], "as": "nearest", "others": len(structures) - 1}


def _name(match: dict[str, Any]) -> str:
    if match.get("as") == "group":
        return f"the group “{match['label']}”"
    name = (
        f"“{match['label']}”" if "node_id" in match else f"{match['path']} (step {match['step']})"
    )
    if match.get("as") == "nearest":
        others = match["others"]
        name += f" and {others} other conformer{'s' if others > 1 else ''} of the same molecule"
    return name


def _verdict(ends: dict[str, dict[str, Any]], ts_label: str) -> dict[str, Any]:
    """A184: both ends each matching one structure link those two (high confidence); A191: an
    end standing for a group of its matches is one structure too, while the nearest of several
    conformers or a soft mode's wider match makes the link medium; both on the same structure,
    one end or none are said and flagged."""
    for n in ENDS:
        if "settled" not in ends[n] and not ends[n].get("returned_to_ts"):
            _settle(ends[n])
    settled = {n: ends[n].get("settled") for n in ENDS}
    keys = {n: _key(settled[n]) for n in ENDS}
    flags: list[str] = []
    back = [n for n in ENDS if ends[n].get("returned_to_ts") or ends[n].get("ts_matches")]
    for n in back:
        if ends[n].get("returned_to_ts"):
            flags.append(
                f"The {SHOWN[n]} end returned to the TS: its distances along the mode are the "
                "TS's, so it matches nothing (A188)."
            )
        else:
            flags.append(
                f"The {SHOWN[n]} end matches another TS, {_name(ends[n]['ts_matches'][0])}: "
                "a TS is never an end's match (A188)."
            )
    if len(back) == 2 and not any(keys.values()):
        return {
            "outcome": "returned",
            "verdict": f"Followed the mode of “{ts_label}”: both ends returned to a TS",
            "confidence": None,
            "flags": flags,
        }
    for n in ENDS:
        if not ends[n]["converged"]:
            flags.append(
                f"The {SHOWN[n]} end stopped after {ends[n]['cycles']} steps without converging "
                "(a partial relaxation, as expected); its match is by A184's 0.5 Å."
            )
        found = settled[n]
        if found is None:
            continue
        if found["as"] == "nearest":
            flags.insert(
                0,
                f"The {SHOWN[n]} end matches {found['others'] + 1} conformers of one molecule "
                f"within 0.5 Å; the nearest, {_name(found)}, is taken (A191): group them, or "
                "check which.",
            )
        elif found["as"] == "soft":
            flags.insert(
                0,
                f"The {SHOWN[n]} end of this soft mode (below {SOFT_MODE:.0f} cm⁻¹, no bond "
                f"change) matches nothing within 0.5 Å; the nearest minimum with its bonds, "
                f"{_name(found)} ({found['rmsd']:.2f} Å, within {SOFT_RMSD} Å), is taken (A191).",
            )
        elif found["as"] == "conformer":
            flags.insert(
                0,
                f"The {SHOWN[n]} end matches nothing within 0.5 Å but has the bonds of "
                f"{_name(found)} ({found['rmsd']:.2f} Å, within {SOFT_RMSD} Å): another "
                "conformer of that molecule, which is taken (A193); link it or its group, and "
                "make no node from this end.",
            )
    head = f"Followed the mode of “{ts_label}”"
    if all(keys[n] for n in ENDS):
        a, b = settled["+"], settled["-"]
        if keys["+"] == keys["-"]:
            flags.insert(0, f"Both ends match the same structure, {_name(a)}.")
            return {
                "outcome": "same_structure",
                "verdict": f"{head}: both ends match the same structure, {_name(a)}",
                "confidence": None,
                "flags": flags,
            }
        exact = all(settled[n]["as"] in ("one", "group") for n in ENDS)
        elsewhere = [n for n in ENDS if settled[n].get("other_level")]
        for n in elsewhere:
            flags.insert(
                0,
                f"The {SHOWN[n]} end matches {_name(settled[n])}, a structure only at a lower "
                "kind of method than the TS's geometry level (an xTB conformer search's under a "
                "DFT TS, say): a weak end, so the link is low (A194).",
            )
        return {
            "outcome": "links",
            "verdict": f"{head}: links {_name(a)} and {_name(b)}",
            # A184: a mode-follow whose two ends each match one structure; A191, A193: medium
            # when an end took the nearest of several conformers or a wider match; A194: low
            # when an end is at another level than the TS's only.
            "confidence": "low" if elsewhere else "high" if exact else "medium",
            "flags": flags,
            "links": [a, b],
        }
    parts = []
    for n in ENDS:
        image = ends[n].get("mirror_of")
        if keys[n] is None and image is not None:
            parts.append(
                f"{SHOWN[n]} end is the mirror image of “{image['label']}” ({image['rmsd']:.2f} "
                "Å reflected): the matching minimum is not in the investigation"
            )
            flags.insert(
                0,
                f"The {SHOWN[n]} end matches “{image['label']}” only reflected: a mirror image "
                "is another molecule (D267), so its minimum is missing. Make it from that node "
                "(find_gaps: missing_mirror_image) rather than from this end.",
            )
        elif keys[n] is None:
            parts.append(f"{SHOWN[n]} end matches nothing")
        else:
            parts.append(f"{SHOWN[n]} end matches {_name(settled[n])} ({settled[n]['rmsd']:.2f} Å)")
    outcome = "one_end" if any(keys.values()) else "none"
    if outcome == "none":
        flags.insert(0, "No end matches a structure: the TS is unsettled.")
    return {
        "outcome": outcome,
        "verdict": f"{head}: {', '.join(parts)}",
        "confidence": None,
        "flags": flags,
    }


def _wider(
    rows: Rows, charge: int | None, multiplicity: int | None, nodes: list[_Candidate]
) -> list[dict[str, Any]]:
    """A191, A193: the minima with the end's bonds within SOFT_RMSD, nearest first: an end
    that matches nothing within 0.5 Å but has the bonds of a molecule the folder holds is
    another conformer of it (a MACE end beside a DFT minimum often is), never a new node."""
    return [m for m in _matches(rows, charge, multiplicity, nodes, SOFT_RMSD) if not _is_ts(m)]


def _own_ts_files(files: list[_Candidate], ts_rows: Rows, session: Session) -> set[int]:
    """A195: the `structure` numbers of the folder's TS files that are the followed TS itself
    (the same structure within the duplicate tolerance)."""
    tolerance = thermo_settings.settings(session).duplicate_tolerance
    formula = _formula(ts_rows)
    return {
        c.entry["structure"]
        for c in files
        if c.entry.get("role")
        and c.entry.get("formula") == formula
        and compare.same_structure(c.rows, ts_rows, tolerance) is not None
    }


def _no_bond_change(ts_rows: Rows, rows: Rows) -> bool:
    """No real bond (A181, metal–hydrogen contacts weak) differs between the TS and an end
    in its numbering."""
    formed, broken = atom_matching.changes(ts_rows, rows)
    real_formed, real_broken, _, _ = compare.split(ts_rows, rows, None, formed, broken)
    return not real_formed and not real_broken


def _own_irc_stems(node: Node | None) -> set[str | None]:
    """The stems of the TS node's own IRC files (A192); None stands for "not an IRC end"."""
    out: set[str | None] = {None}
    for calc in node.irc_calculations if node is not None else []:
        if calc.irc_part == "path" and calc.source_file is not None:
            out.add(irc_ends.stem(calc.source_file.original_name or ""))
    return out


def ends(session: Session, folder: Path, job_id: str) -> dict[str, Any]:
    """D263c: per end how it relaxed, its bonds against the TS, the nodes it matches (A184,
    nearest first) and the files of the job's folder it matches; with the verdict."""
    found = _read(session, folder, job_id)
    charge, multiplicity = found.job["charge"], found.job["multiplicity"]
    from chembook3d.services import ts_partners  # it imports gaps, which imports this module

    level = ts_partners.ts_level(
        session,
        {"node_id": found.ts_node.id}
        if found.ts_node is not None
        else {"calculation_id": found.follow.get("calculation_id")},
    )
    held = session.get(LevelOfTheory, level.id) if level is not None else None
    nodes = _node_candidates(
        session,
        found.ts_node.id if found.ts_node else None,
        folder,
        (held.id, levels.method_rank(levels.fields_of(held))) if held is not None else None,
    )
    notes: list[str] = []
    files: list[_Candidate] | None = None
    where = found.follow.get("folder")
    if where:
        try:
            files = _file_candidates(
                Path(where), folder, thermo_settings.settings(session).duplicate_tolerance
            )
        except Exception as exc:  # noqa: BLE001 - a folder gone or unreadable is said
            notes.append(f"The folder {where} could not be read: {exc}")
    own_files = _own_ts_files(files or [], found.ts_rows, session)
    made = found.job.get("made") or {}
    out_ends: dict[str, dict[str, Any]] = {}
    mode = _mode(found)
    # A191: a soft mode (a rotation's, say) whose ends change no bond may match wider.
    soft = abs(float(found.follow.get("wavenumber") or -1e9)) < SOFT_MODE
    # A192: the folder's IRC end optimizations are this TS's ends only, never another's.
    own = _own_irc_stems(found.ts_node)
    for name in ENDS:
        end = found.ends[name]
        rows = _end_rows(found, name)
        matched = _matches(rows, charge, multiplicity, nodes)
        entry: dict[str, Any] = {
            "shift": end.get("shift"),
            "converged": bool(end.get("converged")),
            "cycles": end.get("cycles"),
            "energy": end.get("energy"),
            **_bonds(found, name),
            "matches": [m for m in matched if not _is_ts(m)],
        }
        # A188: the side of the mode the end lies on, by the mode's active pairs; one whose
        # active distances are still the TS's ("none") did not leave the TS: it matches
        # nothing, whatever lies within 0.5 Å (a TS and its minima often do, with the TS's
        # partial bonds weak). Another TS is never an end's match either.
        side = compare._side(mode, found.ts_rows, rows, None)[0] if mode is not None else None
        entry["side"] = SHOWN.get(side, side) if side is not None else None
        entry["returned_to_ts"] = side == "none"
        at_ts = [m for m in matched if _is_ts(m)]
        if files is not None:
            in_files = _matches(rows, charge, multiplicity, files)
            entry["files"] = [
                f
                for f in in_files
                if f.get("role") is None and f.get("irc_end") in own  # A192
            ]
            # A195: the followed TS's own files (its optimization, a single point on it) are
            # not another TS, as its node is not: an end near them has still left it (A188's
            # side says whether it did).
            at_ts += [
                f
                for f in in_files
                if f.get("role") is not None and f.get("structure") not in own_files
            ]
        if at_ts:
            entry["ts_matches"] = at_ts
        if entry["returned_to_ts"]:  # whatever lies near the TS is no end of it either
            entry["near_ts"] = entry["matches"] + entry.get("files", [])
            entry["matches"] = []
            if "files" in entry:
                entry["files"] = []
        else:
            _settle(entry)
            if "settled" not in entry:
                near = _wider(rows, charge, multiplicity, nodes)
                if near and soft and _no_bond_change(found.ts_rows, rows):
                    # A191: the single nearest, said to be a soft mode's wider match
                    entry["matches"] = [{**near[0], "soft": True}]
                    entry["settled"] = {**near[0], "as": "soft"}
                elif near:  # A193: another conformer of a molecule the folder has
                    entry["matches"] = [{**near[0], "conformer": True}]
                    entry["settled"] = {**near[0], "as": "conformer"}
                else:  # D267: a minimum it matches only reflected is another molecule
                    image = [
                        m
                        for m in _matches(atom_matching.mirrored(rows), charge, multiplicity, nodes)
                        if not _is_ts(m)
                    ]
                    if image:
                        entry["mirror_of"] = image[0]
        if name in made:
            entry["node_made"] = made[name]
        out_ends[name] = entry
    verdict = _verdict(out_ends, found.ts_label)
    return {
        "job_id": job_id,
        "ts": {
            "node_id": found.ts_node.id if found.ts_node else found.follow.get("ts_node_id"),
            "label": found.ts_label,
            "calculation_id": found.follow.get("calculation_id"),
            "level": found.follow.get("level"),
            "wavenumber": found.follow.get("wavenumber"),
        },
        "method": found.job.get("level"),
        "shift": found.follow.get("shift"),
        "steps": found.follow.get("steps"),
        "folder": where,
        "ends": out_ends,
        **verdict,
        "notes": notes,
        "key": (
            "Each end was displaced `shift` Å (the atom moving most) along the TS's lowest "
            "imaginary mode and relaxed for at most `steps` steps. formed/broken: bonds against "
            "the TS by the 3D view's rule (its partial bonds count), in the TS's numbering; "
            "matches: nodes with the same bonds (weak contacts left out) within 0.5 Å "
            "atom-matched RMSD, nearest first (A184), never a TS (`ts_matches`); files: "
            "the folder's outputs whose final structure matches; returned_to_ts: the end "
            "still matches the TS it started from, so it matches nothing (A188); settled: "
            "the one structure the end stands for (A191: `as` one, group when its matches are "
            "one group's members, nearest of several conformers of one molecule, soft: a "
            "soft mode's nearest minimum with its bonds within 1.0 Å, or conformer: any other "
            "end's, another conformer of that molecule; nearest, soft and conformer make a "
            "link medium). mirror_of: the node an end that matches nothing fits only "
            "reflected within 0.5 Å (D267: a mirror image is another molecule, so that minimum "
            "is missing). Energies are the job's own method's, never for comparison. An end "
            "that matches nothing, not even within 1.0 Å, can become a node with "
            "make_node_from_end."
        ),
    }


def make_node(
    session: Session,
    folder: Path,
    job_id: str,
    end: str,
    label: str | None = None,
    anyway: bool = False,
) -> dict[str, Any]:
    """D263c: a new node from an end that matches nothing: its coordinates only (a partial
    relaxation is a guess: no calculation), a planned minimum beside the TS on its canvas, with
    the TS's charge and multiplicity. Refused when the end matches a node, unless `anyway`."""
    name = NAMES.get(end.strip().lower())
    if name is None:
        raise local_jobs.LocalJobError(f"The end is + or -, not {end!r}")
    found = _read(session, folder, job_id)
    rows = _end_rows(found, name)
    charge, multiplicity = found.job["charge"], found.job["multiplicity"]
    ts = found.ts_node
    candidates = _node_candidates(session, ts.id if ts else None)
    matches = _matches(rows, charge, multiplicity, candidates)
    if matches and not anyway:
        names = ", ".join(f"{_name(m)} ({m['rmsd']:.2f} Å)" for m in matches[:4])
        raise local_jobs.LocalJobError(
            f"The {SHOWN[name]} end matches {names}: use that node, or pass anyway to make a "
            "node all the same"
        )
    near = [] if anyway else _wider(rows, charge, multiplicity, candidates)
    if near:  # A193: another conformer of a molecule the investigation has
        names = ", ".join(f"{_name(m)} ({m['rmsd']:.2f} Å)" for m in near[:4])
        raise local_jobs.LocalJobError(
            f"The {SHOWN[name]} end has the bonds of {names}, within {SOFT_RMSD} Å: another "
            "conformer of that molecule (A193). Link that node or its group, or pass anyway "
            "to make a node all the same"
        )
    state = found.ends[name]
    fields: dict[str, Any] = {
        "label": label or f"{found.ts_label} end {SHOWN[name]}",
        "role": Role.MINIMUM.value,
        "status": Status.PLANNED.value,
        "charge": charge,
        "multiplicity": multiplicity,
        "notes": (
            f"Made from the {SHOWN[name]} end of following the "
            f"{abs(float(found.follow.get('wavenumber') or 0)):.0f}i cm⁻¹ mode of "
            f"“{found.ts_label}” (local job {job_id}): displaced {state.get('shift')} Å and "
            f"relaxed {state.get('cycles')} steps with {found.job.get('level')}, "
            f"{'converged' if state.get('converged') else 'not converged'}. Coordinates only: "
            "a guess, not a result (D263c)."
        ),
    }
    if ts is not None:
        fields["pos_x"] = ts.pos_x + (NODE_DX if name == "+" else -NODE_DX)
        fields["pos_y"] = ts.pos_y + NODE_DY
        if ts.canvas_id is not None:
            fields["canvas_id"] = ts.canvas_id
    atoms = [xyz.Atom(r[0], r[1], r[2], r[3]) for r in rows]
    node = node_service.create(session, fields, xyz.format_xyz(atoms))
    made = dict(found.job.get("made") or {})
    made[name] = node.id
    local_jobs.update_job(folder, job_id, made=made)
    return {
        "node_id": node.id,
        "label": node.label,
        "end": SHOWN[name],
        "matched": matches,
        "summary": f"Made “{node.label}” from the {SHOWN[name]} end, a planned minimum beside "
        f"“{found.ts_label}” with coordinates only.",
    }
