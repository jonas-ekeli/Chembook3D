"""The ends of a mode-follow job matched against the structures there are (D263c, A184).

A `follow_mode` local job (`local_jobs.create_follow_mode`) displaces a TS 0.1 Å along its
imaginary mode on each side and relaxes each end for at most 40 steps. Its result is
information, never imported: here, on demand, each end is compared with the TS (the bonds that
form and break, `structure_compare.compare`) and matched against the open investigation's nodes
and, when the job named one, a folder's outputs (`folder_survey.final_structures`). An end
matches a structure when no real bond changes between them (A181's rule, weak and metal–hydrogen
contacts left out) and the atom-matched RMSD is under 0.5 Å, whatever the atom order (A184,
`structure_compare.end_match`); formula, charge and multiplicity, the order-independent bound
and the bond counts rule most candidates out before any atoms are matched.

The verdict: both ends each matching one structure links them (high confidence, A184); both
matching the same one is flagged; an end that matches nothing may become a node of its own
(`make_node`), coordinates only: a partial relaxation is a guess, never a result.
"""

from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from chembook3d import local_jobs, xyz
from chembook3d.models import Node, Role, Status
from chembook3d.services import folder_survey
from chembook3d.services import nodes as node_service
from chembook3d.services import structure_compare as compare
from chembook3d.services.geometry import Rows

ENDS = ("+", "-")
SHOWN = {"+": "+", "-": "−"}
NAMES = {"+": "+", "plus": "+", "-": "-", "−": "-", "minus": "-"}
NODE_DX = 260.0  # px: a node made from an end sits this far beside the TS, + right, − left
NODE_DY = 120.0  # px: and this far below it


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
    rows: Rows, charge: int | None, multiplicity: int | None, candidates: list[_Candidate]
) -> list[dict[str, Any]]:
    """The candidates the end matches (A184), nearest first."""
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
        )
        if same is None:
            continue
        entry = {k: v for k, v in candidate.entry.items() if k != "formula"}
        entry["rmsd"] = round(same.rmsd, 3)
        if same.mapping is not None:
            entry["renumbered"] = True
        found.append(entry)
    found.sort(key=lambda m: m["rmsd"])
    return found


def _node_candidates(session: Session, exclude: str | None) -> list[_Candidate]:
    out = []
    for node in node_service.list_nodes(session):
        if not node.geometry or node.id == exclude:
            continue
        rows = [list(r) for r in node.geometry]
        entry = {
            "node_id": node.id,
            "label": node.label or "Untitled node",
            "role": str(node.role) if node.role else None,
            "formula": _formula(rows),
        }
        if node.kind != "node":
            entry["kind"] = str(node.kind)
        out.append(_Candidate(rows, node.charge, node.multiplicity, entry))
    return out


def _file_candidates(folder: Path, investigation: Path) -> list[_Candidate]:
    return [
        _Candidate(
            final.rows,
            final.charge,
            final.multiplicity,
            {"path": final.path, "step": final.step, "formula": _formula(final.rows)},
        )
        for final in folder_survey.final_structures(folder, True, investigation)
    ]


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


def _structures(matches: list[dict[str, Any]], files: list[dict[str, Any]]) -> list[str]:
    """The distinct structures an end matches, as keys: its nodes, else its files."""
    if matches:
        return [f"node:{m['node_id']}" for m in matches]
    return [f"file:{f['path']}" for f in files]


def _name(match: dict[str, Any]) -> str:
    if "node_id" in match:
        return f"“{match['label']}”"
    return f"{match['path']} (step {match['step']})"


def _verdict(ends: dict[str, dict[str, Any]], ts_label: str) -> dict[str, Any]:
    """A184: both ends each matching exactly one structure link those two (high confidence);
    both matching the same one, more than one, one end or none are said and flagged."""
    keys = {n: _structures(ends[n]["matches"], ends[n].get("files", [])) for n in ENDS}
    first = {n: (ends[n]["matches"] or ends[n].get("files") or [None])[0] for n in ENDS}
    flags: list[str] = []
    for n in ENDS:
        if not ends[n]["converged"]:
            flags.append(
                f"The {SHOWN[n]} end stopped after {ends[n]['cycles']} steps without converging "
                "(a partial relaxation, as expected); its match is by A184's 0.5 Å."
            )
    head = f"Followed the mode of “{ts_label}”"
    if all(len(keys[n]) == 1 for n in ENDS):
        a, b = first["+"], first["-"]
        if keys["+"] == keys["-"]:
            flags.insert(0, f"Both ends match the same structure, {_name(a)}.")
            return {
                "outcome": "same_structure",
                "verdict": f"{head}: both ends match the same structure, {_name(a)}",
                "confidence": None,
                "flags": flags,
            }
        return {
            "outcome": "links",
            "verdict": f"{head}: links {_name(a)} and {_name(b)}",
            "confidence": "high",  # A184: a mode-follow whose two ends each match one
            "flags": flags,
            "links": [a, b],
        }
    parts = []
    for n in ENDS:
        if not keys[n]:
            parts.append(f"{SHOWN[n]} end matches nothing")
        elif len(keys[n]) == 1:
            parts.append(f"{SHOWN[n]} end matches {_name(first[n])} ({first[n]['rmsd']:.2f} Å)")
        else:
            names = ", ".join(_name(m) for m in (ends[n]["matches"] or ends[n]["files"])[:4])
            parts.append(f"{SHOWN[n]} end matches {len(keys[n])} structures ({names})")
            flags.insert(
                0,
                f"The {SHOWN[n]} end matches {len(keys[n])} structures: duplicates or conformers "
                "closer than 0.5 Å; check them.",
            )
    matched = [n for n in ENDS if keys[n]]
    if not matched:
        outcome = "none"
    elif any(len(keys[n]) > 1 for n in ENDS):
        outcome = "ambiguous"
    else:
        outcome = "one_end"
    if outcome == "none":
        flags.insert(0, "No end matches a structure: the TS is unsettled.")
    return {
        "outcome": outcome,
        "verdict": f"{head}: {', '.join(parts)}",
        "confidence": None,
        "flags": flags,
    }


def ends(session: Session, folder: Path, job_id: str) -> dict[str, Any]:
    """D263c: per end how it relaxed, its bonds against the TS, the nodes it matches (A184,
    nearest first) and the files of the job's folder it matches; with the verdict."""
    found = _read(session, folder, job_id)
    charge, multiplicity = found.job["charge"], found.job["multiplicity"]
    nodes = _node_candidates(session, found.ts_node.id if found.ts_node else None)
    notes: list[str] = []
    files: list[_Candidate] | None = None
    where = found.follow.get("folder")
    if where:
        try:
            files = _file_candidates(Path(where), folder)
        except Exception as exc:  # noqa: BLE001 - a folder gone or unreadable is said
            notes.append(f"The folder {where} could not be read: {exc}")
    made = found.job.get("made") or {}
    out_ends: dict[str, dict[str, Any]] = {}
    for name in ENDS:
        end = found.ends[name]
        rows = _end_rows(found, name)
        entry: dict[str, Any] = {
            "shift": end.get("shift"),
            "converged": bool(end.get("converged")),
            "cycles": end.get("cycles"),
            "energy": end.get("energy"),
            **_bonds(found, name),
            "matches": _matches(rows, charge, multiplicity, nodes),
        }
        if files is not None:
            entry["files"] = _matches(rows, charge, multiplicity, files)
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
            "atom-matched RMSD, nearest first (A184); files: "
            "the folder's outputs whose final structure matches. Energies are the job's own "
            "method's, never for comparison. An end that matches nothing can become a node "
            "with make_node_from_end."
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
    matches = _matches(rows, charge, multiplicity, _node_candidates(session, ts.id if ts else None))
    if matches and not anyway:
        names = ", ".join(f"{_name(m)} ({m['rmsd']:.2f} Å)" for m in matches[:4])
        raise local_jobs.LocalJobError(
            f"The {SHOWN[name]} end matches {names}: use that node, or pass anyway to make a "
            "node all the same"
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
