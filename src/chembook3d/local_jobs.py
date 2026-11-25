"""Calculations run on this computer (D213, FR-LOC-02..04).

A job is a folder `local-jobs/<date>-<name>/` in the investigation: the app writes `job.json`
and a copy of `local_worker.py`, then starts the worker with the tools environment's Python as
a process of its own, detached from the app, so the job goes on when the app stops (a server
started from the shortcut stops minutes after the last tab closes, D107). The worker writes
`progress.json` (with a heartbeat every few seconds), `trajectory.xyz` and finally
`result.json`; see `local_worker.py`. Jobs run one after another: the worker waits for the
tools folder's lock file.

The folder is left out of git sync (`sync.IGNORED`): it changes every few seconds while a job
runs, and a finished job's result is copied into `files/` when it is imported.

Importing a finished job (`import_result`) imports `result.json` onto the node the job was
started from, as any optimization output imported onto that node would be (D23, D100): on a
node without calculations the result becomes the node's geometry; on a node whose own
optimization the job started from it continues it as a pre-optimization chain (D100); on a
node with calculations at its geometry it goes to a derived node.

D240: a single point (`create_single_points`) computes the energy of a node's structure as it
is, with Psi4, and is imported onto that node as a single-point calculation; its geometry level
is the node's optimization (FR-CALC-04).
"""

from __future__ import annotations

import json
import os
import platform
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
from sqlalchemy.orm import Session

from chembook3d import local_tools, xyz
from chembook3d.local_worker import CRITERIA, FOLLOW_ENDS, METHOD_LABELS, SCAN_POINTS, grid_cells
from chembook3d.models import Calculation, Node, Role
from chembook3d.parsers import local_result
from chembook3d.services import history, imports, levels, oniom
from chembook3d.services import nodes as node_service
from chembook3d.services.geometry import matches as same_geometry
from chembook3d.services.records import RecordError

JOBS_DIR = "local-jobs"
JOB_JSON = "job.json"
PROGRESS = "progress.json"
RESULT = "result.json"
CANCEL = "cancel"
TRAJECTORY = "trajectory.xyz"
WORKER = "worker.py"
WORKER_SOURCE = Path(__file__).with_name("local_worker.py")
ALIVE_S = 30.0  # a heartbeat older than this: the process is gone, unless it still runs (D241)
STARTING_S = 60.0  # a job without a heartbeat yet is starting for this long
_JOB_ID = re.compile(r"^\d{4}-\d{2}-\d{2}-[a-z0-9-]{1,60}$")

# The methods offered and what the result is called (the worker's METHOD_LABELS).
METHODS = {
    "gfn2": "GFN2-xTB",
    "b97-3c": "B97-3c",
    "r2scan-3c": "r2SCAN-3c",
    "oniom": "ONIOM(r2SCAN-3c:GFN2-xTB)",
    "gxtb": "g-xTB",  # D216: offered once installed (local_tools.install_gxtb)
    "mace-omol": "MACE-OMOL-0",  # D237: offered once installed (local_tools.install_mace)
}
# D214: the methods with a DFT core, its high and low level.
LAYERED = {"oniom": ("r2scan-3c", "gfn2"), "oniom-stub": ("stub", "stub-low")}

# A143: what a time estimate starts from, per method: seconds per gradient for a 70-atom Ru
# complex on 4 cores (measured 2026-11-02 on the MCB structure: Psi4 1.11 r2SCAN-3c 862 s and
# B97-3c 601 s from a fresh guess, about 60 % of that from the last step's orbitals; GFN2-xTB
# about 0.5 s), how it grows with the number of atoms, and the steps an optimization takes.
# Each finished job on this computer replaces the first number with its own (`calibrate`).
TIMING = {
    "gfn2": {"seconds": 0.5, "power": 2.0},
    "b97-3c": {"seconds": 400.0, "power": 2.7},
    "r2scan-3c": {"seconds": 550.0, "power": 2.7},
    # ONIOM: r2SCAN-3c on the model system (core and link atoms), counted by its size; the
    # GFN2-xTB parts add seconds.
    "oniom": {"seconds": 550.0, "power": 2.7},
    # g-xTB: a guess (not measured here) of a few times GFN2-xTB's cost, until a job has run.
    "gxtb": {"seconds": 2.0, "power": 2.0},
    # D237: MACE-OMOL-0 on the CPU, 3.7 s a gradient measured 2026-11-18 on a 73-atom Ru complex
    # (4 cores); a message-passing model grows about linearly with the atoms.
    "mace-omol": {"seconds": 3.5, "power": 1.2},
    "stub": {"seconds": 1.0, "power": 0.0},
}
TIMING_ATOMS, TIMING_CORES = 70, 4
# CHEMBOOK3D_LOCAL_STUB=1 only (tests)
TEST_METHODS = {
    "stub": "stub",
    "oniom-stub": "ONIOM(stub:stub-low)",
    "stub-saddle": "stub-saddle",
    "mace-stub": "mace-stub",  # D237: run as MACE is, in CHEMBOOK3D_MACE_PYTHON
}
# D217: methods cheap enough for a numerical Hessian at the job's own level, so a TS search with
# one starts from its own Hessian and ends with frequencies at that level (on by default for a
# TS search, offered for a minimum); DFT and ONIOM keep the GFN2-xTB check instead.
FREQUENCY_METHODS = {"gfn2", "gxtb", "stub", "stub-saddle", "mace-omol", "mace-stub"}
OWN_HESSIAN = {"gfn2", "gxtb", "stub-saddle", "mace-omol", "mace-stub"}

_records = threading.Lock()
# The workers this app started: kept so Python never warns that one is still running, and
# polled so a finished one on Linux leaves no zombie behind.
_started: list[subprocess.Popen] = []
_importing = threading.Lock()  # two tabs checking at once import a result once


class LocalJobError(RecordError):
    pass


class JobNotFound(LocalJobError, LookupError):
    pass


def methods() -> dict[str, str]:
    offered = dict(METHODS)
    if local_tools.gxtb_binary() is None:
        offered.pop("gxtb")
    mace = local_tools.mace_python() is not None
    if not mace:
        offered.pop("mace-omol")
    if os.environ.get("CHEMBOOK3D_LOCAL_STUB") == "1":
        tests = {k: v for k, v in TEST_METHODS.items() if mace or k not in local_tools.MACE_METHODS}
        return {**offered, **tests}
    return offered


def steps(atoms: int) -> int:
    """About how many steps an optimization of so many atoms takes."""
    return 15 + atoms // 2


def _scale(method: str, atoms: int, cores: int) -> float:
    timing = TIMING.get(method, TIMING["stub"])
    return (max(atoms, 1) / TIMING_ATOMS) ** timing["power"] * (
        TIMING_CORES / max(cores, 1)
    ) ** 0.85


def estimate(method: str, atoms: int, cores: int, model: int | None = None) -> dict[str, Any]:
    """Seconds for one step and for the optimization, and whether this computer measured it.
    A layered method's step is counted from its `model` size (core and link atoms)."""
    measured = local_tools.calibration().get(method)
    base = measured if measured else TIMING.get(method, TIMING["stub"])["seconds"]
    sized = model if method in LAYERED and model else atoms
    per_step = base * _scale(method, sized, cores)
    return {
        "seconds_per_step": round(per_step, 2),
        "steps": steps(atoms),
        "seconds": round(per_step * steps(atoms)),
        "measured": bool(measured),
    }


def _duration(seconds: float) -> str:
    if seconds < 90:
        return f"{round(seconds)} s"
    if seconds < 5400:
        return f"{round(seconds / 60)} min"
    return f"{seconds / 3600:.1f} h".replace(".0 h", " h")


def calibrate(result: dict[str, Any], job: dict[str, Any]) -> None:
    """Keep what a finished job took per step, scaled back to 70 atoms on 4 cores. The time is
    the worker's own computing (`run_s`), without its wait for other jobs, where it says."""
    if job.get("kind") == SINGLE_POINT:
        _calibrate_single_point(result, job)
        return
    cycles = result.get("cycles")
    elapsed = result.get("run_s") or result.get("elapsed_s")
    method = job.get("method")
    if not cycles or not elapsed or method not in METHODS or cycles < 3:
        return
    if job.get("transition") or job.get("frequencies"):
        return  # its time includes Hessians (D215, D217), not only steps
    atoms = len(job.get("elements") or [])
    if job.get("oniom"):
        atoms = len(job["oniom"]["core"]) + len(job["oniom"]["links"])
    per_step = float(elapsed) / int(cycles)
    local_tools.save_calibration(method, per_step / _scale(method, atoms, int(job.get("cores", 1))))


def slug(text: str, limit: int = 40) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:limit].strip("-") or "job"


def jobs_dir(folder: Path) -> Path:
    return Path(folder) / JOBS_DIR


def job_dir(folder: Path, job_id: str) -> Path:
    if not _JOB_ID.match(job_id):
        raise JobNotFound(f"No local job {job_id!r}")
    # The id's form already keeps the job inside the jobs folder. The path itself is checked as
    # well, in the form code scanning follows, now that Claude's tools name jobs too (D224).
    root = os.path.normpath(jobs_dir(folder))
    inside = os.path.normpath(os.path.join(root, job_id))
    if not inside.startswith(root + os.sep):
        raise JobNotFound(f"No local job {job_id!r}")
    path = Path(inside)
    if not (path / JOB_JSON).is_file():
        raise JobNotFound(f"No local job {job_id!r}")
    return path


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _read(path: Path) -> dict[str, Any] | None:
    for attempt in range(5):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else None
        except FileNotFoundError:
            return None
        except (PermissionError, ValueError):  # being replaced (Windows) or half written
            time.sleep(0.05 * (attempt + 1))
    return None


def _write(path: Path, data: dict[str, Any]) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=1), encoding="utf-8")
    os.replace(tmp, path)


def read_job(folder: Path, job_id: str) -> dict[str, Any]:
    data = _read(job_dir(folder, job_id) / JOB_JSON)
    if data is None:
        raise JobNotFound(f"No local job {job_id!r}")
    return data


def update_job(folder: Path, job_id: str, **fields: Any) -> dict[str, Any]:
    with _records:
        data = read_job(folder, job_id)
        data.update(fields)
        _write(job_dir(folder, job_id) / JOB_JSON, data)
        return data


def _xyz(result: dict[str, Any]) -> str:
    """The job's final structure as XYZ text (Å)."""
    elements = result.get("elements") or []
    rows = [
        f"{e} {x:.6f} {y:.6f} {z:.6f}"
        for e, (x, y, z) in zip(elements, result.get("coordinates") or [], strict=True)
    ]
    return "\n".join([str(len(rows)), "final structure", *rows]) + "\n"


def _new_id(folder: Path, name: str) -> str:
    base = f"{datetime.now():%Y-%m-%d}-{slug(name)}"
    candidate, n = base, 2
    while (jobs_dir(folder) / candidate).exists():
        candidate, n = f"{base}-{n}", n + 1
    return candidate


def _age(stamp: str | None) -> float | None:
    if not stamp:
        return None
    try:
        when = datetime.strptime(stamp, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    except ValueError:
        return None
    return (datetime.now(UTC) - when).total_seconds()


# ---------- starting ----------


@dataclass
class Start:
    """D258: the structure a refine starts from instead of the node's coordinates: one of the
    node's other optimized geometries (`calculation_id`), or a structure (XYZ text or a
    build)."""

    atoms: list[xyz.Atom]
    calculation_id: str | None = None
    label: str | None = None

    @property
    def described(self) -> dict[str, Any]:
        if self.calculation_id:
            return {
                "from": "geometry",
                "calculation_id": self.calculation_id,
                "label": self.label,
            }
        return {"from": "structure", "label": self.label}


def start_at(node: Node, calculation_id: str) -> Start:
    """D258: a start from one of the node's optimized geometries (levels.node_geometries)."""
    found = next((c for c in levels.node_geometries(node) if c.id == calculation_id), None)
    if found is None:
        raise LocalJobError(
            f'"{node.label}" has no optimized geometry from calculation {calculation_id}; '
            "its geometries are listed by GET /api/nodes/{id}/geometries"
        )
    atoms = [xyz.Atom(e, x, y, z) for e, x, y, z in found.geometry]
    return Start(atoms, found.id, levels.label(found.level))


def check_start(node: Node, atoms: list[xyz.Atom], charge: int, multiplicity: int) -> None:
    """D258: a start structure has the node's atoms in the node's order, and the job the node's
    charge and multiplicity (where the node says), so the result belongs to this node."""
    mine = [a.element for a in node_service.atoms_of(node)]
    theirs = [a.element for a in atoms]
    if len(theirs) != len(mine):
        raise LocalJobError(
            f'The start structure has {len(theirs)} atoms; "{node.label}" has {len(mine)}'
        )
    wrong = [n for n, (a, b) in enumerate(zip(mine, theirs, strict=True)) if a != b]
    if wrong:
        n = wrong[0]
        raise LocalJobError(
            f'Atom {n + 1} of the start structure is {theirs[n]}; in "{node.label}" it is '
            f"{mine[n]}. The start structure needs the node's atoms in the node's order"
        )
    for name, given, own in (
        ("charge", charge, node.charge),
        ("multiplicity", multiplicity, node.multiplicity),
    ):
        if own is not None and given != own:
            raise LocalJobError(
                f'A job from a start structure keeps the {name} of "{node.label}" ({own}); '
                f"{given} was given"
            )


def create_job(
    folder: Path,
    node: Node,
    method: str,
    charge: int,
    multiplicity: int,
    constraints: dict[str, Any] | None = None,
    name: str | None = None,
    core: list[int] | None = None,
    transition: bool = False,
    reacting: list[int] | None = None,
    frequencies: bool | None = None,
    scan: dict[str, Any] | None = None,
    limit: int | None = None,
    start: Start | None = None,
    replace: bool = False,
    fix_mode: dict[str, Any] | None = None,
    follow_mode: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Start a job. `limit` (seconds, D223) refuses one whose estimate is longer: the longest
    job Claude may start while it works on its own. D258: `start` runs it from another
    structure than the node's coordinates (one of its other geometries, or XYZ text or a
    build), with the node's atoms in its order and its charge and multiplicity; the job stays
    the node's, and its result is imported by the usual rules (D246). D263c: `follow_mode`
    makes a job that follows a TS's imaginary mode both ways from `start` (its frequency
    calculation's structure, in that calculation's order), never imported."""
    if method not in methods():
        raise LocalJobError(f"Unknown method {method!r}; offered: {', '.join(methods())}")
    atoms = node_service.atoms_of(node)
    if not atoms:
        raise LocalJobError(f'Node "{node.label}" has no coordinates')
    if start is not None:
        if follow_mode is None:  # a mode-follow's ends are never imported onto the node
            check_start(node, start.atoms, charge, multiplicity)
        atoms = start.atoms
    if multiplicity < 1:
        raise LocalJobError("The multiplicity is at least 1")
    if local_tools.worker_python(method) is None:
        raise LocalJobError(_install_first(method))
    if scan is not None:
        if transition:
            raise LocalJobError("A scan cannot also be a transition state search")
        scan = check_scan(scan, len(atoms), constraints)
        if scan.get("grid") and method not in GRID_METHODS and method not in TEST_METHODS:
            raise LocalJobError("A grid scan runs with GFN2-xTB or g-xTB")
        frequencies = False
    level = methods()[method]
    layers = None
    if method in LAYERED:
        found = check_core(
            atoms, core if core is not None else suggest_core(atoms), charge, multiplicity
        )
        if found.errors:
            raise LocalJobError(found.errors[0])
        high, low = LAYERED[method]
        layers = {"core": found.atoms, "links": found.links, "high": high, "low": low}
        # D214: an ONIOM energy compares only with the same core, so its size is in the level.
        level = f"{level}, {len(found.atoms)}-atom core"
    resources = local_tools.resources()
    if limit is not None:
        model = len(layers["core"]) + len(layers["links"]) if layers else None
        seconds = estimate(method, len(atoms), resources.cores, model)["seconds"]
        seconds *= scan["points"] if scan else 1  # one optimization per point, at most
        seconds *= 2 * len(fix_mode["shifts"]) if fix_mode else 1  # D261: its tries, at most
        if follow_mode:  # A184: two ends of at most so many steps each
            per_step = estimate(method, len(atoms), resources.cores)["seconds_per_step"]
            seconds = per_step * 2 * follow_mode["steps"]
        if seconds > limit:
            raise LocalJobError(
                f"This job is estimated at about {_duration(seconds)}, over the "
                f"{_duration(limit)} the user allows for a job you start on your own. Use a "
                "faster method or a smaller ONIOM core, or leave it and tell the user, who "
                "can start it or raise the limit."
            )
    with _records:
        default = f"{node.label} {METHODS.get(method, method)}" + (
            " TS"
            if transition
            else " scan"
            if scan
            else " fix mode"
            if fix_mode
            else " follow mode"
            if follow_mode
            else ""
        )
        job_id = _new_id(folder, name or default)
        path = jobs_dir(folder) / job_id
        path.mkdir(parents=True)
        job = {
            "id": job_id,
            "kind": "refine",
            "node_id": node.id,
            "node_label": node.label,
            "method": method,
            "level": level,
            "charge": charge,
            "multiplicity": multiplicity,
            "cores": resources.cores,
            "memory_mb": resources.memory_mb,
            "elements": [a.element for a in atoms],
            "coordinates": [[a.x, a.y, a.z] for a in atoms],
            "constraints": constraints or None,
            "created": _now(),
        }
        if start is not None:
            job["start"] = start.described  # D258: where the job started, for the list
        if replace and not scan:
            # D260: a result at a level the node has at another geometry may replace it, once
            # the user confirms; until then it waits instead of going to a derived node.
            job["replace"] = True
        if history.SOURCE.get() != "manual":
            job["started_by"] = history.SOURCE.get()  # D223: "claude"
        if layers:
            job["oniom"] = layers
        if method == "gxtb":
            job["gxtb"] = str(local_tools.gxtb_binary())
            job["gxtb_version"] = local_tools.gxtb_status().get("version")
        if method in local_tools.MACE_METHODS:
            job["mace_model"] = str(local_tools.mace_model())  # D237
        if transition:
            # D215: a TS search, started from a GFN2-xTB Hessian (the tests' stand-in for it);
            # D217: a g-xTB one from a g-xTB Hessian.
            job["transition"] = True
            job["ts_hessian"] = (
                method
                if method in OWN_HESSIAN
                else ("stub-low" if method in TEST_METHODS else "gfn2")
            )
            job["reacting"] = sorted(set(reacting)) if reacting else None
        if frequencies is None:
            frequencies = transition
        if frequencies and method in FREQUENCY_METHODS:
            job["frequencies"] = True  # D217: same-level frequencies at the result
        if scan:
            job["scan"] = scan  # D218: a relaxed scan, imported as a node of its own
        if fix_mode and not scan and not transition:
            job["fix_mode"] = fix_mode  # D261: tries along an imaginary mode
        if follow_mode and not scan and not transition:
            job["kind"] = FOLLOW_MODE  # D263c: both ends of the mode, information only
            job["follow_mode"] = follow_mode
            job.pop("replace", None)
        if method in TEST_METHODS and os.environ.get("CHEMBOOK3D_LOCAL_STUB_DELAY"):
            job["stub_delay_s"] = float(os.environ["CHEMBOOK3D_LOCAL_STUB_DELAY"])
        _write(path / JOB_JSON, job)
        shutil.copyfile(WORKER_SOURCE, path / WORKER)
    _spawn(path)
    return job_status(folder, job_id)


SCAN_KINDS = {"distance": 2, "angle": 3, "dihedral": 4}
SCAN_LETTERS = {"distance": "B", "angle": "A", "dihedral": "D"}
SCAN_OFFSET = 280.0  # px: a scan's node goes this far right of the node it started from


def scan_name(scan: dict[str, Any]) -> str:
    """D218: the scanned coordinates as the parsers name them, e.g. B(1,2) or B(1,2)+B(3,4)."""
    return (" × " if scan.get("grid") else "+").join(
        f"{SCAN_LETTERS[c['kind']]}({','.join(str(int(a) + 1) for a in c['atoms'])})"
        for c in scan["coordinates"]
    )


MAX_SCAN_POINTS = 100
MAX_SCAN_COORDINATES = 4
# D219: a 2D grid runs only with the cheap methods, at most this many points a side and in all
# ---------- D261: fix an imaginary mode of a minimum ----------

FIX_SHIFTS = [0.1, 0.3]  # Å moved by the atom moving most, the second only when the first fails


def fix_method(calculation: Calculation) -> str | None:
    """D261: the local method a calculation was computed with, when it is one that computes
    frequencies here (FREQUENCY_METHODS), else None."""
    level = calculation.level
    if level is None:
        return None
    for method in sorted(FREQUENCY_METHODS):
        program, label = METHOD_LABELS.get(method, (method, method))
        if (level.program or "").lower() == program.lower() and (
            level.method or ""
        ).upper() == label.upper():
            return method
    return None


def create_fix_mode(
    folder: Path, node: Node, calculation_id: str, limit: int | None = None
) -> dict[str, Any]:
    """D261: a job that pushes a minimum off the imaginary mode of one of its frequency jobs at
    a local level: from that job's structure, displaced both ways along the mode (0.1 Å for the
    atom moving most, then 0.3 Å when neither try ends without an imaginary mode), each
    optimized and its frequencies computed at the same level; the lower try without an
    imaginary mode is kept. It is started with Replace offered (D260): the result replaces the
    geometry at that level once the user confirms, or goes to a derived node."""
    calculation = next((c for c in node.calculations if c.id == calculation_id), None)
    if calculation is None:
        raise LocalJobError(f'"{node.label}" has no calculation {calculation_id}')
    if node.role == Role.TRANSITION_STATE:
        raise LocalJobError(
            "Fix imaginary mode is for a minimum; a transition state keeps one imaginary mode"
        )
    result = calculation.result
    wavenumbers = [float(f) for f in (result.frequencies if result is not None else [])]
    if not any(f < 0 for f in wavenumbers):
        raise LocalJobError("This calculation has no imaginary frequency to fix")
    method = fix_method(calculation)
    if method is None or method not in methods():
        offered = ", ".join(METHODS[m] for m in sorted(FREQUENCY_METHODS) if m in METHODS)
        raise LocalJobError(
            f"Fix imaginary mode runs at the level the frequencies were computed at, on this "
            f"computer: {offered}. {levels.label(calculation.level)} is not one; displace the "
            "structure along the mode and run it again where it was computed"
        )
    modes = result.normal_modes
    if len(modes) != len(wavenumbers):
        raise LocalJobError("This calculation has no normal modes stored to follow")
    lowest = min(range(len(wavenumbers)), key=wavenumbers.__getitem__)
    source = calculation.geometry_source
    rows = calculation.geometry or (source.geometry if source is not None else None)
    if not rows:
        raise LocalJobError("This calculation has no structure to start from")
    atoms = [xyz.Atom(e, x, y, z) for e, x, y, z in rows]
    if len(modes[lowest]) != len(atoms):
        raise LocalJobError("The normal mode does not match the structure's atoms")
    start = Start(atoms, (source or calculation).id, levels.label(calculation.level))
    charge = calculation.charge if calculation.charge is not None else node.charge or 0
    multiplicity = (
        calculation.multiplicity if calculation.multiplicity is not None else node.multiplicity or 1
    )
    return create_job(
        folder,
        node,
        method,
        charge,
        multiplicity,
        frequencies=True,
        limit=limit,
        start=start,
        replace=True,
        fix_mode={
            "calculation_id": calculation.id,
            "wavenumber": wavenumbers[lowest],
            "imaginary": [f for f in wavenumbers if f < 0],
            "mode": [[float(v) for v in atom] for atom in modes[lowest]],
            "shifts": list(FIX_SHIFTS),
            "start_energy": result.energy,
        },
    )


# ---------- D263c, A184: follow a TS's imaginary mode both ways ----------

FOLLOW_MODE = "follow_mode"
FOLLOW_SHIFT = 0.1  # A184: Å moved by the atom moving most, each way
FOLLOW_STEPS = 40  # A184: optimizer steps each end relaxes for, at most
FOLLOW_METHODS = {"mace-omol": "MACE-OMOL-0", "gfn2": "GFN2-xTB"}
FOLLOW_TEST_METHODS = {"stub-saddle", "stub", "mace-stub"}  # CHEMBOOK3D_LOCAL_STUB=1 only
FOLLOW_END_FIELDS = ("shift", "converged", "cycles", "energy")  # job_status's, per end


def _follow_calculation(node: Node, calculation_id: str | None, tolerance: float) -> Calculation:
    """The frequency calculation to follow: the one named (on the node), else the node's at its
    geometry with exactly one imaginary frequency, else the one with the lowest; one away from
    the node's geometry only when none is at it."""
    if calculation_id is not None:
        found = next((c for c in node.calculations if c.id == calculation_id), None)
        if found is None:
            raise LocalJobError(f'"{node.label}" has no calculation {calculation_id}')
        return found

    def imaginary(calc: Calculation) -> list[float]:
        result = calc.result
        return [float(f) for f in (result.frequencies if result is not None else []) if f < 0]

    with_mode = [c for c in node.calculations if imaginary(c) and c.geometry]
    if not with_mode:
        raise LocalJobError(
            f'"{node.label}" has no frequency calculation with an imaginary frequency to follow'
        )
    at = [c for c in with_mode if same_geometry(c.geometry, node.geometry, tolerance)]
    return min(
        at or with_mode,
        key=lambda c: (len(imaginary(c)) != 1, min(imaginary(c)), -c.created_at.timestamp()),
    )


def create_follow_mode(
    folder: Path,
    node: Node,
    calculation_id: str | None = None,
    method: str = "mace-omol",
    survey_folder: str | None = None,
    limit: int | None = None,
    tolerance: float = 0.05,
) -> dict[str, Any]:
    """D263c, A184: a job that follows a TS's imaginary mode both ways: the frequency
    calculation's structure (any level, DFT included) displaced 0.1 Å along the normalised
    lowest imaginary mode on each side (the atom moving most moves 0.1 Å) and each relaxed for
    at most 40 steps with MACE-OMOL-0 or GFN2-xTB. Its result is information, never imported:
    `services/follow_mode.py` matches the ends against the nodes and the folder named here."""
    offered = methods()
    allowed = set(FOLLOW_METHODS) | (FOLLOW_TEST_METHODS if method in TEST_METHODS else set())
    if method not in allowed:
        raise LocalJobError(
            f"Follow the mode runs with {' or '.join(FOLLOW_METHODS)} "
            f"({', '.join(FOLLOW_METHODS.values())}); {method!r} is not one"
        )
    if method not in offered:
        if method in local_tools.MACE_METHODS:
            raise LocalJobError(
                "MACE-OMOL-0 is not installed on this computer: install it in Settings, Local "
                "calculations, or follow the mode with gfn2"
            )
        raise LocalJobError(_install_first(method))
    calculation = _follow_calculation(node, calculation_id, tolerance)
    result = calculation.result
    wavenumbers = [float(f) for f in (result.frequencies if result is not None else [])]
    if not any(f < 0 for f in wavenumbers):
        raise LocalJobError("This calculation has no imaginary frequency to follow")
    modes = result.normal_modes
    if len(modes) != len(wavenumbers):
        raise LocalJobError(
            "This calculation has no normal modes stored to follow (its file printed none, or "
            "they were not read)"
        )
    lowest = min(range(len(wavenumbers)), key=wavenumbers.__getitem__)
    source = calculation.geometry_source
    rows = calculation.geometry or (source.geometry if source is not None else None)
    if not rows:
        raise LocalJobError("This calculation has no structure to start from")
    atoms = [xyz.Atom(e, x, y, z) for e, x, y, z in rows]
    if len(modes[lowest]) != len(atoms):
        raise LocalJobError("The normal mode does not match the structure's atoms")
    if not np.linalg.norm(np.asarray(modes[lowest], float), axis=1).max() > 0:
        raise LocalJobError("The imaginary mode moves no atom")
    where = None
    if survey_folder:
        path = Path(survey_folder).expanduser()
        if not path.is_absolute() or not path.is_dir():
            raise LocalJobError(f"{survey_folder} is not a folder on this computer (a full path)")
        where = str(path)
    charge = calculation.charge if calculation.charge is not None else node.charge or 0
    multiplicity = (
        calculation.multiplicity if calculation.multiplicity is not None else node.multiplicity or 1
    )
    return create_job(
        folder,
        node,
        method,
        charge,
        multiplicity,
        frequencies=False,
        limit=limit,
        start=Start(atoms, calculation.id, levels.label(calculation.level)),
        follow_mode={
            "calculation_id": calculation.id,
            "level": levels.label(calculation.level),
            "ts_node_id": node.id,
            "ts_label": node.label,
            "wavenumber": wavenumbers[lowest],
            "imaginary": [f for f in wavenumbers if f < 0],
            "mode": [[float(v) for v in atom] for atom in modes[lowest]],
            "shift": FOLLOW_SHIFT,
            "steps": FOLLOW_STEPS,
            "folder": where,
        },
    )


GRID_METHODS = {"gfn2", "gxtb", "stub"}
MAX_GRID_SIDE = 30
MAX_GRID_POINTS = 400


def check_scan(
    scan: dict[str, Any], count: int, constraints: dict[str, Any] | None
) -> dict[str, Any]:
    """D218: a relaxed scan's coordinates (atoms from 0) with their start and end values (Å or
    degrees), driven together over its number of points, checked. D219: a grid has two
    coordinates, each with its own number of points."""
    if scan.get("grid"):
        return _check_grid(scan, count, constraints)
    points = int(scan.get("points") or 0)
    if not 2 <= points <= MAX_SCAN_POINTS:
        raise LocalJobError(f"A scan has 2 to {MAX_SCAN_POINTS} points")
    coordinates = scan.get("coordinates") or []
    if not 1 <= len(coordinates) <= MAX_SCAN_COORDINATES:
        raise LocalJobError(f"A scan drives 1 to {MAX_SCAN_COORDINATES} coordinates")
    checked = [_check_scanned(c, count, constraints) for c in coordinates]
    keys = [(c["kind"], tuple(sorted(c["atoms"]))) for c in checked]
    if len(set(keys)) != len(keys):
        raise LocalJobError("A coordinate is scanned twice")
    return {"coordinates": checked, "points": points}


def _check_grid(
    scan: dict[str, Any], count: int, constraints: dict[str, Any] | None
) -> dict[str, Any]:
    coordinates = scan.get("coordinates") or []
    if len(coordinates) != 2:
        raise LocalJobError("A grid scan has two coordinates")
    checked = []
    for c in coordinates:
        side = int(c.get("points") or 0)
        if not 2 <= side <= MAX_GRID_SIDE:
            raise LocalJobError(f"Each coordinate of a grid has 2 to {MAX_GRID_SIDE} points")
        checked.append({**_check_scanned(c, count, constraints), "points": side})
    if checked[0]["points"] * checked[1]["points"] > MAX_GRID_POINTS:
        raise LocalJobError(f"A grid has at most {MAX_GRID_POINTS} points in all")
    if (checked[0]["kind"], sorted(checked[0]["atoms"])) == (
        checked[1]["kind"],
        sorted(checked[1]["atoms"]),
    ):
        raise LocalJobError("A coordinate is scanned twice")
    return {
        "coordinates": checked,
        "grid": True,
        "points": checked[0]["points"] * checked[1]["points"],
    }


def _check_scanned(
    scan: dict[str, Any], count: int, constraints: dict[str, Any] | None
) -> dict[str, Any]:
    kind = scan.get("kind")
    if kind not in SCAN_KINDS:
        raise LocalJobError("A scan drives a distance, an angle or a dihedral")
    atoms = [int(a) for a in scan.get("atoms") or []]
    if len(atoms) != SCAN_KINDS[kind] or len(set(atoms)) != len(atoms):
        raise LocalJobError(f"A scanned {kind} needs {SCAN_KINDS[kind]} different atoms")
    if not all(0 <= a < count for a in atoms):
        raise LocalJobError(f"Scan atoms are numbered 1 to {count}")
    if scan.get("start") is None or scan.get("end") is None:
        raise LocalJobError(f"A scanned {kind} needs the values it goes from and to")
    start, end = float(scan["start"]), float(scan["end"])
    if start == end:
        raise LocalJobError("A scan's start and end differ")
    if kind == "distance" and min(start, end) <= 0.3:
        raise LocalJobError("A scanned distance stays above 0.3 Å")
    if kind == "angle" and not (0 < min(start, end) and max(start, end) <= 180):
        raise LocalJobError("A scanned angle stays between 0° and 180°")
    for item in (constraints or {}).get(kind + "s", []):
        if sorted(item.get("atoms", [])) == sorted(atoms):
            raise LocalJobError(f"The scanned {kind} is also held by a constraint")
    if set(atoms) & set((constraints or {}).get("frozen") or []) == set(atoms):
        raise LocalJobError(f"The scanned {kind}'s atoms are all frozen")
    return {"kind": kind, "atoms": atoms, "start": start, "end": end}


def suggest_core(atoms: list[Any], seeds: list[int] | None = None) -> list[int]:
    """D214: the suggested DFT core of a structure (atoms from 0)."""
    return oniom.suggest(
        [a.element for a in atoms], np.array([[a.x, a.y, a.z] for a in atoms]), seeds
    )


def mode_atoms(node: Node) -> list[int]:
    """D215: the atoms (from 0) the node's latest imaginary mode moves most, as the reacting
    atoms of a TS search or the seeds of a core; empty without one."""
    from chembook3d.local_worker import moving_atoms
    from chembook3d.services.scan_path import imaginary_mode

    found = imaginary_mode(node)
    return moving_atoms(found[1]) if found else []


def check_core(atoms: list[Any], core: list[int], charge: int, multiplicity: int) -> oniom.Core:
    """D214: the core with its link atoms and what is wrong with it."""
    return oniom.build(
        [a.element for a in atoms],
        np.array([[a.x, a.y, a.z] for a in atoms]),
        core,
        charge,
        multiplicity,
    )


def _install_first(method: str) -> str:
    if method in local_tools.MACE_METHODS:
        return "Install MACE-OMOL-0 first (Settings, Local calculations)"
    return "Install the local tools first (Settings, Local calculations)"


def _spawn(path: Path) -> None:
    """Start the worker as a process of its own: a new session on Linux; on Windows detached
    and outside the app's job object when the job allows it (uv runs the app in one). A MACE
    job (D237) runs with the MACE environment's Python."""
    job = _read(path / JOB_JSON) or {}
    method = job.get("method")
    if job.get("kind") == TS_GUESS:
        method = "mace-omol"  # D238: ASE and Sella are in the MACE environment only
    python = local_tools.worker_python(method)
    if python is None:
        raise LocalJobError(_install_first(str(method)))
    argv = [str(python), "-I", str(path / WORKER), str(path)]
    argv += ["--lock", str(local_tools.lock_path())]
    env = {k: v for k, v in os.environ.items() if k not in ("PYTHONPATH", "PYTHONHOME")}
    env["PYTHONNOUSERSITE"] = "1"
    mace = method in local_tools.MACE_METHODS
    override = "CHEMBOOK3D_MACE_PYTHON" if mace else "CHEMBOOK3D_LOCAL_PYTHON"
    if not os.environ.get(override):
        tools = local_tools.mace_dir() / "env" if mace else None
        env["PATH"] = os.pathsep.join(
            [str(p) for p in local_tools.env_path_dirs(tools)] + [env.get("PATH", "")]
        )
    threads = str(job.get("cores", 1))
    for name in ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS"):
        env[name] = threads
    env["PSI_SCRATCH"] = str(path / "scratch")
    local_tools.lock_path().parent.mkdir(parents=True, exist_ok=True)
    with open(path / "worker.err", "ab") as output:
        options: dict[str, Any] = {
            "stdin": subprocess.DEVNULL,
            "stdout": output,
            "stderr": output,
            "cwd": path,
            "env": env,
            "close_fds": True,
        }
        _started[:] = [p for p in _started if p.poll() is None]
        if sys.platform != "win32":
            _started.append(subprocess.Popen(argv, start_new_session=True, **options))
            return
        flags = (
            subprocess.DETACHED_PROCESS  # type: ignore[attr-defined]
            | subprocess.CREATE_NEW_PROCESS_GROUP  # type: ignore[attr-defined]
            | subprocess.CREATE_NO_WINDOW  # type: ignore[attr-defined]
        )
        try:
            _started.append(
                subprocess.Popen(
                    argv,
                    creationflags=flags | subprocess.CREATE_BREAKAWAY_FROM_JOB,  # type: ignore[attr-defined]
                    **options,
                )
            )
        except OSError:  # the job object does not allow leaving it
            _started.append(subprocess.Popen(argv, creationflags=flags, **options))


# ---------- where a job stands ----------


def _alive(progress: dict[str, Any] | None) -> bool:
    """The worker still runs: its heartbeat is fresh, or it is busy and its process still lives.

    The heartbeat is a Python thread, and Psi4's long steps (one SCF cycle on disk-stored
    integrals, 90 s for 1,536 basis functions) hold Python's lock, so it can lag for minutes
    while the job is fine (D241)."""
    if not progress or progress.get("state") == "finished":
        return False
    age = _age(progress.get("heartbeat_at"))
    if age is None:
        return False
    return age < ALIVE_S or _worker_process(progress)


def _worker_process(progress: dict[str, Any]) -> bool:
    """The process `pid` names is still the worker that wrote `progress`: it runs, and it started
    before the worker first wrote (`queued_at`). A process given the same id after the worker
    ended (after a restart of the computer, say) started later."""
    pid, first = progress.get("pid"), _epoch(progress.get("queued_at"))
    if not isinstance(pid, int) or pid <= 0 or first is None:
        return False
    started = _process_started(pid)
    # queued_at is cut to whole seconds, so up to a second before the process it names
    return started is not None and started <= first + 2.0


def _epoch(stamp: str | None) -> float | None:
    if not stamp:
        return None
    try:
        return datetime.strptime(stamp, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC).timestamp()
    except ValueError:
        return None


def _process_started(pid: int) -> float | None:
    """When the running process `pid` started (seconds since 1970), or None: no such process,
    one that has ended, or a system this cannot ask (then only the heartbeat counts)."""
    if sys.platform == "win32":
        return _process_started_windows(pid)
    try:
        stat = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8", errors="replace")
        boot = next(
            float(line.split()[1])
            for line in Path("/proc/stat").read_text(encoding="utf-8").splitlines()
            if line.startswith("btime ")
        )
        fields = stat[stat.rindex(")") + 2 :].split()
        if fields[0] in ("Z", "X"):  # a zombie: ended, not yet reaped
            return None
        return boot + int(fields[19]) / os.sysconf("SC_CLK_TCK")
    except (OSError, ValueError, IndexError, StopIteration):
        return None


def _process_started_windows(pid: int) -> float | None:
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel32.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return None
    try:
        code = wintypes.DWORD()
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)) or code.value != 259:
            return None  # 259: STILL_ACTIVE
        times = [wintypes.FILETIME() for _ in range(4)]
        if not kernel32.GetProcessTimes(handle, *(ctypes.byref(t) for t in times)):
            return None
        created = (times[0].dwHighDateTime << 32) | times[0].dwLowDateTime  # 100 ns since 1601
        return created / 1e7 - 11644473600.0
    finally:
        kernel32.CloseHandle(handle)


def job_status(folder: Path, job_id: str) -> dict[str, Any]:
    """The job with `status`: starting, queued (waiting for another local job), running,
    converged, not_converged, failed, cancelled or stopped (the process ended without a result:
    the computer restarted, or it was killed), and the progress the worker last wrote."""
    path = job_dir(folder, job_id)
    job = read_job(folder, job_id)
    progress = _read(path / PROGRESS)
    result = _read(path / RESULT)
    cancelled = (path / CANCEL).exists()
    if result is not None:
        status = result.get("state", "failed")
    elif _alive(progress):
        status = "cancelling" if cancelled else (progress or {}).get("state", "running")
    elif progress is None and (_age(job.get("created")) or 0) < STARTING_S and not cancelled:
        status = "starting"
    else:
        status = "cancelled" if cancelled else "stopped"
    out = dict(job)
    out.pop("coordinates", None)
    out.pop("elements", None)
    if job.get("ts_guess"):  # D238: its end's coordinates stay in job.json
        out["ts_guess"] = {k: v for k, v in job["ts_guess"].items() if k != "end"}
    if job.get("fix_mode"):  # D261: the mode it follows stays in job.json
        out["fix_mode"] = {k: v for k, v in job["fix_mode"].items() if k != "mode"}
    if job.get("follow_mode"):  # D263c: likewise
        out["follow_mode"] = {k: v for k, v in job["follow_mode"].items() if k != "mode"}
    out["atoms"] = len(job.get("elements") or [])
    out["status"] = status
    out["active"] = status in ("starting", "queued", "running", "cancelling")
    progress = progress or {}
    out["progress"] = {
        "cycle": progress.get("cycle", 0),
        "energy": progress.get("energy"),
        "max_gradient": progress.get("max_gradient"),
        "energies": progress.get("energies") or [],
        "started_at": progress.get("started_at"),
        "heartbeat_at": progress.get("heartbeat_at"),
        "phase": progress.get("phase"),  # D215: a Hessian being computed
        "scf": None,
    }
    if status == "running" and job.get("method") in PSI4_METHODS and not progress.get("phase"):
        out["progress"]["scf"] = scf_progress(path / "psi4.out")  # D220
    if result is not None:
        out["result"] = {
            key: result.get(key)
            for key in (
                "state",
                "energy",
                "cycles",
                "elapsed_s",
                "error",
                "program",
                "level",
                "ts_check",
                "scan",
                "fix_mode",  # D261: its tries and the one kept
            )
        }
        found = result.get("follow_mode")
        if found:  # D263c: each end's state, without its frames
            out["result"]["follow_mode"] = {
                **{k: v for k, v in found.items() if k != "ends"},
                "ends": {
                    name: {k: v for k, v in end.items() if k in FOLLOW_END_FIELDS}
                    for name, end in (found.get("ends") or {}).items()
                },
            }
        found = result.get("ts_guess")
        if found:  # D238: what the guess's notes say, without the ends' structures
            out["result"]["ts_guess"] = {
                k: found.get(k) for k in ("band", "bonds", "warnings", "converged", "saddle_kcal")
            }
            correction = found.get("correction")
            if correction:  # D239: without the picked frame's coordinates
                out["result"]["ts_guess"]["correction"] = {
                    k: v for k, v in correction.items() if k != "coordinates"
                }
        found = result.get("frequencies")
        if found:  # D217: what the frequency step will show, without its modes
            wavenumbers = found.get("wavenumbers") or []
            out["result"]["frequencies"] = {
                "level": found.get("level"),
                "imaginary": [f for f in wavenumbers if f < 0],
                "count": len(wavenumbers),
            }
        if (result.get("ts_check") or {}).get("end", {}).get("mode"):
            out["result"]["final_xyz"] = _xyz(result)  # D217: to animate the checked mode
    # D263c: a mode-follow's result is information: nothing to import (`follow_mode.ends`)
    out["nothing_to_import"] = job.get("kind") == FOLLOW_MODE
    out["importable"] = (
        status in ("converged", "not_converged")
        and not job.get("imported")
        and not out["nothing_to_import"]
    )
    # D260: waiting for the user's choice between Replace and a derived node
    out["awaiting_choice"] = bool(out["importable"] and job.get("replace_offer"))
    return out


def _word_after(words: list[str], key: str) -> str | None:
    if key in words and words.index(key) + 1 < len(words):
        return words[words.index(key) + 1]
    return None


def _trajectory(
    path: Path, count: int
) -> tuple[list[list[list[float]]], list[float | None], list[int | None]]:
    """trajectory.xyz's whole frames (a frame being written is left out), the energy each
    comment line carries and a scan's point (`cycle N energy E point P`)."""
    frames: list[list[list[float]]] = []
    energies: list[float | None] = []
    points: list[int | None] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (FileNotFoundError, PermissionError):
        return frames, energies, points
    i = 0
    while i + count + 2 <= len(lines):
        if lines[i].strip() != str(count):
            break
        try:
            frame = [[float(v) for v in line.split()[1:4]] for line in lines[i + 2 : i + 2 + count]]
        except ValueError:
            break
        if any(len(row) != 3 for row in frame):
            break
        words = lines[i + 1].split()
        try:
            energy = float(_word_after(words, "energy") or "")
        except ValueError:
            energy = None
        point = _word_after(words, "point")
        frames.append(frame)
        energies.append(energy)
        points.append(int(point) if point and point.isdigit() else None)
        i += count + 2
    return frames, energies, points


def _lines(path: Path) -> list[dict[str, Any]]:
    """A jsonl file's whole lines; a line being written is left out."""
    out = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (FileNotFoundError, PermissionError):
        return out
    for line in lines:
        try:
            found = json.loads(line)
        except ValueError:
            continue
        if isinstance(found, dict):
            out.append(found)
    return out


# D220: Psi4's SCF iterations (`@DF-RKS iter  12:  -1234.5678  -1.2e-05  3.4e-06 DIIS`), read
# from the end of psi4.out to tell how far the step in progress has got.
_SCF_ITERATION = re.compile(r"@\S+ iter\s+(\d+):\s+(-?\d+\.\d+)\s+(-?\d+\.\d+e[+-]\d+)")
SCF_TAIL = 262_144  # bytes: more than one SCF of a large system prints
PSI4_METHODS = {"b97-3c", "r2scan-3c", "oniom", "psi4-sp"}


def scf_progress(path: Path) -> dict[str, Any] | None:
    """The SCF of the step Psi4 is computing: its last iteration and energy change, or that it
    has converged and the gradient is being computed; None when no step is in progress."""
    try:
        with path.open("rb") as fh:
            fh.seek(max(0, path.stat().st_size - SCF_TAIL))
            text = fh.read().decode("utf-8", errors="replace")
    except OSError:
        return None
    start = text.rfind("==> Iterations <==")
    if start < 0:
        return None
    block = text[start:]
    if "-Total Gradient:" in block:
        return None  # the step's gradient is done; the optimizer takes its next step
    found = _SCF_ITERATION.findall(block)
    return {
        "iteration": int(found[-1][0]) if found else 0,
        "delta_e": float(found[-1][2]) if found else None,
        "converged": "Energy and wave function converged" in block,
    }


def _criteria(path: Path, count: int) -> list[list[Any]]:
    """criteria.jsonl as one list per frame (D220); a line being written is left out."""
    out: list[list[Any]] = [[] for _ in range(count)]
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (FileNotFoundError, PermissionError):
        return out
    for line in lines:
        try:
            found = json.loads(line)
            frame = int(found["frame"])
        except (ValueError, KeyError, TypeError):
            continue
        if 0 <= frame < count:
            out[frame] = found.get("criteria") or []
    return out


def inspect_data(folder: Path, job_id: str) -> tuple[dict[str, Any], dict[str, Any], int]:
    """D220: what the Inspect window reads for a local job: its result once it has one, else
    what the worker has written so far, in the result's form with the state "running" (every
    whole frame of trajectory.xyz with its energy, and each one's criteria); with the job's
    status and the bytes read."""
    path = job_dir(folder, job_id)
    status = job_status(folder, job_id)
    result = _read(path / RESULT)
    if result is not None:
        return result, status, (path / RESULT).stat().st_size
    job = read_job(folder, job_id)
    elements = list(job.get("elements") or [])
    frames, energies, frame_points = _trajectory(path / TRAJECTORY, len(elements))
    program, label = METHOD_LABELS.get(job.get("method", ""), (job.get("method"), None))
    data = {
        "format": local_result.FORMAT,
        "method": job.get("method"),
        "program": program,
        "level": job.get("level") or label,
        "charge": job.get("charge"),
        "multiplicity": job.get("multiplicity"),
        "constraints": job.get("constraints"),
        "transition": bool(job.get("transition")),
        "state": local_result.RUNNING,
        "elements": elements,
        "frames": frames,
        "energies": energies,
        "criteria": _criteria(path / CRITERIA, len(frames)),
        "energy": next((e for e in reversed(energies) if e is not None), None),
    }
    if job.get("single_point"):
        data["single_point"] = job["single_point"]  # D240
    if not frames:
        data["coordinates"] = job.get("coordinates")  # the start, until the first step
    if job.get("scan"):
        # A scan's points so far, and for a grid every cell, so its map fills in.
        scan = {**job["scan"], "points": _lines(path / SCAN_POINTS)}
        if scan.get("grid"):
            scan["cells"] = [cell for _, _, cell in grid_cells(scan)]
        data["scan"] = scan
        data["frame_points"] = [p or 0 for p in frame_points]
    if job.get("follow_mode"):
        # D263c: the ends relaxed so far, then the one relaxing now (trajectory.xyz)
        follow = {k: v for k, v in job["follow_mode"].items() if k != "mode"}
        follow["ends"] = {str(e.pop("end")): e for e in _lines(path / FOLLOW_ENDS) if "end" in e}
        data["follow_mode"] = follow
    files = (path / TRAJECTORY, path / CRITERIA, path / SCAN_POINTS, path / FOLLOW_ENDS)
    size = sum(f.stat().st_size for f in files if f.is_file())
    return data, status, size


def list_jobs(folder: Path) -> list[dict[str, Any]]:
    base = jobs_dir(folder)
    if not base.is_dir():
        return []
    out = []
    for child in base.iterdir():
        if child.is_dir() and _JOB_ID.match(child.name) and (child / JOB_JSON).is_file():
            try:
                out.append(job_status(folder, child.name))
            except LocalJobError:
                continue
    out.sort(key=lambda job: (str(job.get("created", "")), job["id"]), reverse=True)
    return out


def busy(folder: Path | None = None) -> bool:
    """A job of this investigation is starting, waiting or running."""
    if folder is None:
        return False
    return any(job["active"] for job in list_jobs(folder))


def cancel(folder: Path, job_id: str) -> dict[str, Any]:
    """Ask the job to stop and end its process; a job that already ended is left as it is."""
    path = job_dir(folder, job_id)
    current = job_status(folder, job_id)
    if not current["active"]:
        return current
    (path / CANCEL).write_text(_now(), encoding="utf-8")
    progress = _read(path / PROGRESS)
    pid = (progress or {}).get("pid")
    # A fresh heartbeat proves the process with this id is still the worker.
    if isinstance(pid, int) and _alive(progress):
        _kill(pid)
        deadline = time.time() + 5
        while time.time() < deadline and _running(pid):
            time.sleep(0.1)
        if not _running(pid) and not (path / RESULT).exists():
            # The worker had no time to say so itself.
            _write(path / RESULT, {"format": "chembook3d-local-result", "state": "cancelled"})
    return job_status(folder, job_id)


def _kill(pid: int) -> None:
    try:
        if sys.platform == "win32":
            os.kill(pid, signal.SIGTERM)  # TerminateProcess
        else:
            os.killpg(pid, signal.SIGTERM)  # the worker leads its own session
    except (OSError, ProcessLookupError):
        pass


def _running(pid: int) -> bool:
    if sys.platform == "win32":
        import ctypes

        handle = ctypes.windll.kernel32.OpenProcess(0x1000 | 0x100000, False, pid)  # type: ignore[attr-defined]
        if not handle:
            return False
        try:
            code = ctypes.c_ulong()
            ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(code))  # type: ignore[attr-defined]
            if code.value != 259:  # STILL_ACTIVE
                return False
            # SYNCHRONIZE right: wait 0 ms tells a live process from a finished one
            return ctypes.windll.kernel32.WaitForSingleObject(handle, 0) != 0  # type: ignore[attr-defined]
        finally:
            ctypes.windll.kernel32.CloseHandle(handle)  # type: ignore[attr-defined]
    for process in _started:
        if process.pid == pid:
            return process.poll() is None
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


# ---------- importing the result ----------


def import_result(
    session: Session, folder: Path, job_id: str, replace: bool | None = None
) -> dict[str, Any]:
    """Import a finished job's result. D223: whoever asks for the import (the page does, by
    itself), what a job Claude started brings in is Claude's in the history, as the job was."""
    by = history.SOURCE.get()
    try:
        by = read_job(folder, job_id).get("started_by") or by
    except LocalJobError:
        pass
    token = history.SOURCE.set(by)
    try:
        with _importing:
            return _import_result(session, folder, job_id, replace)
    finally:
        history.SOURCE.reset(token)


def _import_result(
    session: Session, folder: Path, job_id: str, replace: bool | None = None
) -> dict[str, Any]:
    """D260: `replace` True replaces the node's geometry at the job's level (the caller has
    the user's confirm), False makes a derived node as usual; None does as the job asked: a
    job started with replace waits for the user's choice when its result would replace."""
    status = job_status(folder, job_id)
    if status.get("nothing_to_import"):
        raise LocalJobError(
            "A mode-follow job's result is information and is never imported: its ends are "
            "matched by GET /api/local/jobs/{id}/ends (get_follow_mode_ends), and an end that "
            "matches nothing becomes a node with make_node_from_end"
        )
    if status.get("imported"):
        raise LocalJobError("This job's result is imported already")
    if status["status"] not in ("converged", "not_converged"):
        raise LocalJobError(f"This job has no result to import ({status['status']})")
    node = session.get(Node, status["node_id"])
    if node is None and status.get("kind") != TS_GUESS:
        message = f'The node "{status["node_label"]}" this job was started from is gone'
        update_job(folder, job_id, import_error=message)
        raise LocalJobError(message)
    path = job_dir(folder, job_id) / RESULT
    job = read_job(folder, job_id)
    if job.get("kind") == TS_GUESS:
        return _import_ts_guess(session, folder, job_id, job, path)
    if job.get("kind") == SINGLE_POINT:
        # D240: onto the node, at its structure: a single-point calculation, its geometry level
        # the node's optimization that ended on that structure (FR-CALC-04)
        level = (job.get("single_point") or {}).get("level", "")
        name = f"{slug(status['node_label'], 60)}-{slug(level)}-single-point.json"
        options = imports.ImportOptions(
            target_node_id=node.id,
            origin_path=str(path),
            original_name=name,
            at_calculation_id=job.get("at_calculation_id"),
        )
    elif job.get("scan"):
        # D218: a scan is a node of its own beside the one it started from, in its step and
        # branch, as an imported Gaussian scan is (D200); the start node keeps its structure.
        name = f"{slug(status['node_label'], 60)}-{status['method']}-scan.json"
        options = imports.ImportOptions(
            origin_path=str(path),
            original_name=name,
            label=f"{node.label} {'grid' if job['scan'].get('grid') else 'scan'} "
            f"{scan_name(job['scan'])}",
            duplicate_action="new",
            pos_x=node.pos_x + SCAN_OFFSET,
            pos_y=node.pos_y,
            step_id=node.step_id,
            branch_id=node.branch_id,
            canvas_id=node.canvas_id,
        )
    else:
        name = f"{slug(status['node_label'], 60)}-{status['method']}-refine.json"
        options = imports.ImportOptions(
            target_node_id=node.id,
            origin_path=str(path),
            original_name=name,
            replace=bool(replace),
        )
    staging = imports.Staging()
    try:
        device = platform.node() or "this computer"
        staged = staging.add(path.read_bytes(), name, str(path), device=device)
        if replace is None and job.get("replace"):
            offer = imports.plan(session, staged, options).replace
            if offer is not None:
                # D260: the user picks Replace (confirmed) or a derived node in Local jobs.
                update_job(folder, job_id, replace_offer=offer, import_error=None)
                return job_status(folder, job_id)
        try:
            committed = imports.commit(session, folder, staged, options)
        except (imports.ImportBlocked, imports.ImportFailed) as exc:
            update_job(folder, job_id, import_error=str(exc))
            raise LocalJobError(str(exc)) from exc
    finally:
        staging.clear()
    imported = {
        "node_id": committed.derived_node_id or committed.node_id,
        "derived": bool(committed.derived_node_id),
        "calculation_ids": list(committed.calculation_ids),
        "at": _now(),
    }
    if committed.removed_copies:
        imported["replaced"] = True
        _remove_after_commit(session, folder, committed.removed_copies)
    update_job(folder, job_id, imported=imported, import_error=None, replace_offer=None)
    try:
        result = json.loads(path.read_text(encoding="utf-8"))
        calibrate(result, read_job(folder, job_id))
    except (OSError, ValueError):
        pass
    return {**job_status(folder, job_id), "imported": imported}


def _remove_after_commit(session: Session, folder: Path, stored: list[str]) -> None:
    """D260: the copies of files a Replace removed go once its transaction has committed."""
    from sqlalchemy import event

    from chembook3d.services import import_undo

    def remove(_session: Session) -> None:
        for directory in stored:
            import_undo.remove_copy(folder, directory)

    event.listen(session, "after_commit", remove, once=True)


# ---------- D238: a TS guess between two minima ----------

TS_GUESS = "ts_guess"
CORRECTED = "corrected.json"  # D239: the corrected guess, imported as a node of its own
TS_GUESS_METHODS = {"mace-omol"}
TS_GUESS_TEST_METHODS = {"stub-saddle"}
# A143-style estimate: two minima, the band, the saddle search and the mode check on about this
# many region atoms, each counted in gradients of the method.
TS_GUESS_STEPS = {"ends": 2 * 60, "band": 10, "saddle": 60}
TS_GUESS_REGION = 30  # D244: the region reaches two bonds out (about 16 atoms at one)
TS_GUESS_OFFSET = 0.5  # the guess's node sits halfway between its ends (as D115's path)
# D239: the correction's frames (a held-bond minimum and a single point each), and a single
# point's seconds for 70 atoms on 4 cores (PBE-D3M(BJ)/def2-SVP on a 73-atom Ru complex: 1 to 4
# minutes, the first longest, the rest starting from its orbitals), growing with the cube of the
# atoms
CORRECT_FRAMES = 6
CORRECT_GRADIENTS = 40
CORRECT_SECONDS = 150.0
CORRECTED_DY = 140.0  # the corrected guess's node sits below the raw one


def ts_guess_estimate(method: str, atoms: int, cores: int) -> dict[str, Any]:
    """D238: a TS guess's seconds; D239: those its correction adds."""
    per = estimate(method, atoms, cores)["seconds_per_step"]
    gradients = sum(TS_GUESS_STEPS.values()) + 6 * TS_GUESS_REGION
    single_point = CORRECT_SECONDS * (atoms / 70) ** 3 * 4 / max(1, cores)
    correction = CORRECT_FRAMES * (CORRECT_GRADIENTS * per + single_point)
    return {
        "seconds_per_step": per,
        "gradients": gradients,
        "seconds": round(per * gradients),
        "correction_seconds": round(correction),
    }


def create_ts_guess(
    session: Session,
    folder: Path,
    start_id: str,
    end_id: str,
    method: str = "mace-omol",
    pairs: list[list[int]] | None = None,
    start_member_id: str | None = None,
    end_member_id: str | None = None,
    limit: int | None = None,
    correct: bool = False,
) -> dict[str, Any]:
    """D238: start a TS guess between two structures joined by an edge, in the start's atom
    order (D113's match): both ends optimized with the method, an IDPP band, Sella's saddle
    search from its top and a check of the saddle's mode on the reacting atoms; D239: with
    `correct`, the saddle is then corrected along its forming bond with DFT single points."""
    from chembook3d.services import scan_path

    offered = TS_GUESS_METHODS | (
        TS_GUESS_TEST_METHODS if os.environ.get("CHEMBOOK3D_LOCAL_STUB") == "1" else set()
    )
    if method not in offered or method not in methods():
        if method in TS_GUESS_METHODS:
            raise LocalJobError(_install_first(method))
        raise LocalJobError(f"A TS guess runs with {', '.join(sorted(TS_GUESS_METHODS))}")
    if local_tools.worker_python("mace-omol") is None:
        raise LocalJobError(_install_first("mace-omol"))
    try:
        plan = scan_path.plan(session, start_id, end_id, pairs, start_member_id, end_member_id)
    except RecordError as exc:
        message = str(exc).replace("a scan path runs", "a TS guess runs")
        raise LocalJobError(message[:1].upper() + message[1:]) from exc
    if plan.species is not None:
        raise LocalJobError(
            "A TS guess needs the same atoms at both ends; this edge has a species joining or "
            "leaving. Make a node of the complex with the species close by first."
        )
    start, end = plan.start.node, plan.end.node
    rows, renumbered = plan.start_rows, plan.renumbered
    elements = [r[0] for r in rows]
    formed = [[int(i), int(j)] for i, j in plan.match.formed]
    broken = [[int(i), int(j)] for i, j in plan.match.broken]
    reacting = sorted({a for pair in formed + broken for a in pair})
    if not reacting:
        # no bond changes: the atoms that move most between the ends stand for them
        from chembook3d.local_worker import moving_atoms

        moved = np.array([r[1:4] for r in renumbered], float) - np.array(
            [r[1:4] for r in rows], float
        )
        reacting = sorted(moving_atoms(moved))
    resources = local_tools.resources()
    if limit is not None:
        found = ts_guess_estimate(method, len(rows), resources.cores)
        seconds = found["seconds"] + (found["correction_seconds"] if correct else 0)
        if seconds > limit:
            raise LocalJobError(
                f"This TS guess is estimated at about {_duration(seconds)}, over the "
                f"{_duration(limit)} the user allows for a job you start on your own."
            )
    label = methods()[method]
    with _records:
        job_id = _new_id(folder, f"TS guess {start.label} {end.label}")
        path = jobs_dir(folder) / job_id
        path.mkdir(parents=True)
        job = {
            "id": job_id,
            "kind": TS_GUESS,
            "node_id": start.id,
            "node_label": start.label,
            "method": method,
            "level": label,
            "charge": plan.charge,
            "multiplicity": plan.multiplicity,
            "cores": resources.cores,
            "memory_mb": resources.memory_mb,
            "elements": elements,
            "coordinates": [[float(v) for v in r[1:4]] for r in rows],
            "constraints": None,
            "transition": True,
            "reacting": reacting,
            "created": _now(),
            "ts_guess": {
                "start_id": start.id,
                "end_id": end.id,
                "start_label": start.label,
                "end_label": end.label,
                "edge_id": plan.edge.id,
                "end": [[float(v) for v in r[1:4]] for r in renumbered],
                "formed": formed,
                "broken": broken,
                "reacting": reacting,
                "method_label": label,
                "match_rmsd": round(float(plan.match.rmsd), 3),
            },
        }
        if correct:  # D239: the tests' engine has stand-in single points
            job["ts_guess"]["correct"] = "stub" if method in TS_GUESS_TEST_METHODS else "pbe"
        if history.SOURCE.get() != "manual":
            job["started_by"] = history.SOURCE.get()
        job["mace_model"] = str(local_tools.mace_model())
        if method in TEST_METHODS and os.environ.get("CHEMBOOK3D_LOCAL_STUB_DELAY"):
            job["stub_delay_s"] = float(os.environ["CHEMBOOK3D_LOCAL_STUB_DELAY"])
        _write(path / JOB_JSON, job)
        shutil.copyfile(WORKER_SOURCE, path / WORKER)
    _spawn(path)
    return job_status(folder, job_id)


def ts_guess_notes(job: dict[str, Any], result: dict[str, Any]) -> str:
    """D238: the notes of a TS guess's node: its ends, the bonds that change, the band, the
    mode check and any warning."""
    spec = job.get("ts_guess") or {}
    found = result.get("ts_guess") or {}
    lines = [
        f"TS guess between “{spec.get('start_label', '')}” and “{spec.get('end_label', '')}”, "
        f"{result.get('level') or job.get('level')} on this computer (D238): both ends "
        "optimized, an IDPP band between them, Sella's saddle search from its highest image.",
        "A starting structure for a TS optimization at your own level; its energy is not for "
        "comparison with DFT.",
    ]
    for bond in found.get("bonds") or []:
        a, b = (int(i) + 1 for i in bond["atoms"])
        lines.append(
            f"Bond {a}–{b}: {bond['start']} Å at the start, {bond['saddle']} Å here, "
            f"{bond['end']} Å at the end."
        )
    band = (found.get("band") or {}).get("kcal")
    if band:
        lines.append("Band (kcal/mol from the lower end): " + ", ".join(f"{e:.1f}" for e in band))
    check = found.get("mode_check") or {}
    if check:
        imaginary = check.get("imaginary") or []
        text = ", ".join(f"{abs(f):.0f}i" for f in imaginary) or "none"
        share = check.get("share_on_reacting")
        lines.append(
            f"Mode check on {len(check.get('region') or [])} atoms around the reacting ones: "
            f"imaginary {text}"
            + (f"; {round(100 * share)} % of the first on the reacting atoms" if share else "")
            + ". It is kept on this node's calculation (its mode under Vibrations in 3D); a "
            "Hessian of part of the molecule gives no ZPE or G (D242)."
        )
    lines += [f"Warning: {w}" for w in found.get("warnings") or []]
    if result.get("state") == "not_converged":
        lines.append("The saddle search did not converge; this is its last structure.")
    if (found.get("correction") or {}).get("frames"):
        lines.append(
            f"A {found['correction']['level']} correction is a node of its own beside this one "
            "(D239)."
        )
    return "\n".join(lines)


def correction_notes(job: dict[str, Any], result: dict[str, Any]) -> str:
    """D239: the notes of a corrected TS guess: the bond held, each frame's single point and
    the warnings."""
    spec = job.get("ts_guess") or {}
    found = (result.get("ts_guess") or {}).get("correction") or {}
    a, b = (int(i) + 1 for i in found["bond"])
    picked = found["frames"][found["picked"]]
    lines = [
        f"TS guess between “{spec.get('start_label', '')}” and “{spec.get('end_label', '')}”, "
        f"corrected with {found['level']} single points (D239): from the "
        f"{result.get('level') or job.get('level')} saddle, bond {a}–{b} held at "
        f"{len(found['frames'])} lengths 0.1 Å apart, each frame minimized with the bond "
        "held, and the frame with the highest single point kept.",
        f"This is the frame at {picked['value']:.2f} Å; its energy is the "
        f"{result.get('level') or job.get('level')} one, not for comparison with DFT.",
        "Single points (kcal/mol from the lowest): "
        + ", ".join(f"{f['value']:.2f} Å {f['kcal']:.1f}" for f in found["frames"]),
        "The mode check is of the uncorrected saddle and stays on its node; this frame has none "
        "(D242).",
    ]
    lines += [f"Warning: {w}" for w in found.get("warnings") or []]
    return "\n".join(lines)


def _import_ts_guess(
    session: Session, folder: Path, job_id: str, job: dict[str, Any], path: Path
) -> dict[str, Any]:
    """D238: the guess as a new node halfway between its ends, role TS, status planned, no
    edges (as D115's path), in the start's step and branch, with the notes of ts_guess_notes."""
    from chembook3d.models import GroupNode, Role, Status
    from chembook3d.services.scan_path import _place

    spec = job["ts_guess"]
    result = _read(path) or {}
    start = session.get(Node, spec["start_id"])
    end = session.get(Node, spec["end_id"])
    start_name = start.label if start is not None else spec["start_label"]
    end_name = end.label if end is not None else spec["end_label"]
    options = imports.ImportOptions(
        origin_path=str(path),
        original_name=f"{slug(f'ts guess {start_name} {end_name}', 60)}.json",
        label=f"TS guess {start_name} to {end_name}"[:200],
        role=Role.TRANSITION_STATE,
        status=Status.PLANNED,
        duplicate_action="new",
        notes=ts_guess_notes(job, result),
    )
    if start is not None:
        group = session.get(GroupNode, start.group_id) if start.group_id else None
        options.step_id = start.step_id or (group.step_id if group else None)
        options.branch_id = start.branch_id or (group.outgoing_branch_id if group else None)
        options.canvas_id = start.canvas_id
        a = _place(session, start)
        b = _place(session, end) if end is not None else (a[0] + 400.0, a[1])
        options.pos_x = a[0] + TS_GUESS_OFFSET * (b[0] - a[0])
        options.pos_y = a[1] + TS_GUESS_OFFSET * (b[1] - a[1])
    correction = (result.get("ts_guess") or {}).get("correction") or {}
    if not correction.get("coordinates"):
        return _commit(session, folder, job_id, path, options)
    # D239: the raw guess first, recorded as the job's import; then the corrected one beside it
    imported = _commit_file(session, folder, job_id, path, options)
    update_job(folder, job_id, imported=imported, import_error=None)
    a, b = correction["bond"]
    value = correction["frames"][correction["picked"]]["value"]
    held = {
        **result,
        "state": "converged",
        "frames": [correction["coordinates"]],
        "energies": [correction["energy"]],
        "coordinates": correction["coordinates"],
        "energy": correction["energy"],
        "constraints": {"distances": [{"atoms": [a, b], "value": value}]},
        "ts_check": None,
        "frequencies": None,
        # D242: the check was of the raw saddle, not of this frame (one bond held)
        "ts_guess": {k: v for k, v in result["ts_guess"].items() if k != "mode_check"},
    }
    corrected = path.with_name(CORRECTED)
    _write(corrected, held)
    level = correction.get("level", "DFT").split("/")[0].split("-")[0]
    second = imports.ImportOptions(
        origin_path=str(corrected),
        original_name=f"{slug(f'ts guess {start_name} {end_name} corrected', 60)}.json",
        label=f"TS guess {start_name} to {end_name}, {level}-corrected"[:200],
        role=Role.TRANSITION_STATE,
        status=Status.PLANNED,
        duplicate_action="new",
        notes=correction_notes(job, result),
        step_id=options.step_id,
        branch_id=options.branch_id,
        canvas_id=options.canvas_id,
        pos_x=options.pos_x,
        pos_y=None if options.pos_y is None else options.pos_y + CORRECTED_DY,
    )
    found = _commit_file(session, folder, job_id, corrected, second)
    imported = {**imported, "corrected_node_id": found["node_id"]}
    update_job(folder, job_id, imported=imported, import_error=None)
    return {**job_status(folder, job_id), "imported": imported}


def _commit_file(
    session: Session, folder: Path, job_id: str, path: Path, options: imports.ImportOptions
) -> dict[str, Any]:
    staging = imports.Staging()
    try:
        device = platform.node() or "this computer"
        staged = staging.add(path.read_bytes(), options.original_name, str(path), device=device)
        try:
            committed = imports.commit(session, folder, staged, options)
        except (imports.ImportBlocked, imports.ImportFailed) as exc:
            update_job(folder, job_id, import_error=str(exc))
            raise LocalJobError(str(exc)) from exc
    finally:
        staging.clear()
    return {
        "node_id": committed.derived_node_id or committed.node_id,
        "derived": bool(committed.derived_node_id),
        "calculation_ids": list(committed.calculation_ids),
        "at": _now(),
    }


def _commit(
    session: Session, folder: Path, job_id: str, path: Path, options: imports.ImportOptions
) -> dict[str, Any]:
    imported = _commit_file(session, folder, job_id, path, options)
    update_job(folder, job_id, imported=imported, import_error=None)
    return {**job_status(folder, job_id), "imported": imported}


# ---------- D240: single points on this computer ----------

SINGLE_POINT = "single_point"
SP_METHOD = "psi4-sp"  # the worker's method for every level: Psi4, energy only
SP_TEST_METHOD = "stub-sp"
SP_DEFAULT = "pbe-tzvp"
SP_MAX_NODES = 100
SP_POWER = 2.7  # how a single point's time grows with the atoms (as the DFT refines', A143)
# A smaller structure's time is mostly start-up, so it would scale up to far too little (water's
# 3 s at PBE/def2-TZVP to 2 hours for 70 atoms): it is not kept.
SP_CALIBRATION_ATOMS = 20
# D240: the levels offered, the default first. `psi4` and `basis` are what Psi4 is asked for;
# `method`, `dispersion` and `basis_name` the level the calculation is imported at. `seconds`:
# one single point of a 70-atom Ru complex on 4 cores from a fresh guess. Measured 2026-11-18 on
# a 73-atom Ru [2+2] system (Psi4 1.11, density fitting): PBE/def2-TZVP 10.4-12.4 min,
# PBE/def2-SVP 3.8-4.2 min, r2SCAN-3c 10.6-13.0 min, B97-3c 5.4-5.5 min, PBE0/def2-TZVP 21-22 min.
# ωB97M-V/def2-TZVP is not offered: with density fitting Psi4 writes its range-separated exchange
# integrals to disk (tens of GB for that system: it ran out of disk), and integral-direct it had
# not finished its first SCF cycle after 30 minutes (over 8 hours a single point). A level may
# still set `scf_type` for the worker.
SP_LEVELS: dict[str, dict[str, Any]] = {
    "pbe-tzvp": {
        "label": "PBE-D3M(BJ)/def2-TZVP",
        "psi4": "pbe-d3mbj",
        "basis": "def2-tzvp",
        "method": "PBE",
        "dispersion": "D3M(BJ)",
        "basis_name": "def2-TZVP",
        "seconds": 600.0,
        "note": "Your Saga functional with a smaller basis; kept a Ru [2+2] barrier within "
        "0.3 kcal/mol of PBE/def2-SVP.",
    },
    "pbe-svp": {
        "label": "PBE-D3M(BJ)/def2-SVP",
        "psi4": "pbe-d3mbj",
        "basis": "def2-svp",
        "method": "PBE",
        "dispersion": "D3M(BJ)",
        "basis_name": "def2-SVP",
        "seconds": 215.0,
        "note": "The geometry level of your Gaussian runs; the fastest.",
    },
    "r2scan-3c": {
        "label": "r2SCAN-3c",
        "psi4": "r2scan-3c",
        "basis": None,
        "method": "r2SCAN-3c",
        "dispersion": "",
        "basis_name": "",
        "seconds": 630.0,
        "note": "Lost the barrier on a Ru [2+2] test (−0.6 kcal/mol against PBE's 4.6).",
    },
    "b97-3c": {
        "label": "B97-3c",
        "psi4": "b97-3c",
        "basis": None,
        "method": "B97-3c",
        "dispersion": "",
        "basis_name": "",
        "seconds": 295.0,
        "note": "A cheap composite method; kept a Ru [2+2] barrier (5.2 kcal/mol).",
    },
    "pbe0-tzvp": {
        "label": "PBE0-D3(BJ)/def2-TZVP",
        "psi4": "pbe0-d3bj",
        "basis": "def2-tzvp",
        "method": "PBE0",
        "dispersion": "D3(BJ)",
        "basis_name": "def2-TZVP",
        "seconds": 1175.0,
        "note": "A hybrid, about twice PBE's time; 1.7 kcal/mol for the Ru [2+2] barrier "
        "(PBE/def2-TZVP 4.6).",
    },
}
# CHEMBOOK3D_LOCAL_STUB=1 only (tests): the stub engine's energy, at once
SP_TEST_LEVELS: dict[str, dict[str, Any]] = {
    "stub": {
        "label": "stub-sp/def2-SVP",
        "psi4": "stub",
        "basis": None,
        "method": "stub-sp",
        "dispersion": "",
        "basis_name": "def2-SVP",
        "seconds": 1.0,
        "note": "Tests only.",
    },
}


def sp_levels() -> dict[str, dict[str, Any]]:
    """D240: the single point levels offered (the tests' stand-in too, with
    CHEMBOOK3D_LOCAL_STUB)."""
    if os.environ.get("CHEMBOOK3D_LOCAL_STUB") == "1":
        return {**SP_LEVELS, **SP_TEST_LEVELS}
    return dict(SP_LEVELS)


def _sp_scale(atoms: int, cores: int) -> float:
    return (max(atoms, 1) / TIMING_ATOMS) ** SP_POWER * (TIMING_CORES / max(cores, 1)) ** 0.85


def sp_estimate(level: str, atoms: int, cores: int) -> dict[str, Any]:
    """D240: one single point's seconds for a structure, and whether this computer measured
    it (`calibrate` keeps each level's own time as `sp:<level>`)."""
    measured = local_tools.calibration().get(f"sp:{level}")
    spec = sp_levels().get(level) or SP_LEVELS[SP_DEFAULT]
    base = measured if measured else float(spec["seconds"])
    return {"seconds": round(base * _sp_scale(atoms, cores)), "measured": bool(measured)}


def _calibrate_single_point(result: dict[str, Any], job: dict[str, Any]) -> None:
    level = (job.get("single_point") or {}).get("level")
    elapsed = result.get("run_s") or result.get("elapsed_s")
    atoms = len(job.get("elements") or [])
    if level not in SP_LEVELS or not elapsed or atoms < SP_CALIBRATION_ATOMS:
        return
    scale = _sp_scale(atoms, int(job.get("cores", 1)))
    local_tools.save_calibration(f"sp:{level}", float(elapsed) / scale)


def sp_levels_status(atoms: int | None, cores: int) -> list[dict[str, Any]]:
    """D240: the levels as the tools' status lists them, each with its estimate for `atoms`."""
    return [
        {
            "value": key,
            "label": spec["label"],
            "note": spec["note"],
            "default": key == SP_DEFAULT,
            "estimate": sp_estimate(key, atoms, cores) if atoms else None,
        }
        for key, spec in sp_levels().items()
    ]


def create_single_points(
    folder: Path,
    nodes: list[Node],
    level: str = SP_DEFAULT,
    charge: int | None = None,
    multiplicity: int | None = None,
    limit: int | None = None,
    geometry_of: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    """D240: a single point of each node's structure as it is, one job per node; the jobs wait
    for each other through the tools folder's lock, so they run one after another. Charge and
    multiplicity are each node's own unless given. `limit` (seconds, D223) refuses the lot when
    their estimates add up to more. D246: `geometry_of` maps a node to the optimization whose
    geometry its single point is run at (one of `levels.node_geometries`), else the node's."""
    offered = sp_levels()
    if level not in offered:
        raise LocalJobError(f"Unknown level {level!r}; offered: {', '.join(offered)}")
    if not nodes:
        raise LocalJobError("Choose at least one node with a structure")
    if len(nodes) > SP_MAX_NODES:
        raise LocalJobError(f"At most {SP_MAX_NODES} single points at a time")
    if len({n.id for n in nodes}) != len(nodes):
        raise LocalJobError("A node is listed twice")
    method = SP_TEST_METHOD if level in SP_TEST_LEVELS else SP_METHOD
    if local_tools.worker_python(method) is None:
        raise LocalJobError(_install_first(method))
    if multiplicity is not None and multiplicity < 1:
        raise LocalJobError("The multiplicity is at least 1")
    structures = []
    for node in nodes:
        atoms = node_service.atoms_of(node)
        chosen = (geometry_of or {}).get(node.id)
        if chosen:
            found = next((c for c in levels.node_geometries(node) if c.id == chosen), None)
            if found is None:
                raise LocalJobError(
                    f'"{node.label}" has no optimized geometry from calculation {chosen}; '
                    "its geometries are listed by GET /api/nodes/{id}/geometries"
                )
            atoms = [xyz.Atom(e, x, y, z) for e, x, y, z in found.geometry]
        if not atoms:
            raise LocalJobError(f'Node "{node.label}" has no coordinates')
        structures.append((node, atoms, chosen or None))
    resources = local_tools.resources()
    if limit is not None:
        seconds = sum(
            sp_estimate(level, len(a), resources.cores)["seconds"] for _, a, _ in structures
        )
        if seconds > limit:
            raise LocalJobError(
                f"{'These single points are' if len(nodes) > 1 else 'This single point is'} "
                f"estimated at about {_duration(seconds)}, over the {_duration(limit)} the user "
                "allows for a job you start on your own. Use a faster level or fewer nodes, or "
                "leave it and tell the user, who can start it or raise the limit."
            )
    spec = offered[level]
    started = []
    for node, atoms, at_calculation in structures:
        with _records:
            job_id = _new_id(folder, f"{node.label} {spec['label']} SP")
            path = jobs_dir(folder) / job_id
            path.mkdir(parents=True)
            job = {
                "id": job_id,
                "kind": SINGLE_POINT,
                "node_id": node.id,
                "node_label": node.label,
                "method": method,
                "level": spec["label"],
                "charge": charge if charge is not None else (node.charge or 0),
                "multiplicity": multiplicity
                if multiplicity is not None
                else (node.multiplicity or 1),
                "cores": resources.cores,
                "memory_mb": resources.memory_mb,
                "elements": [a.element for a in atoms],
                "coordinates": [[a.x, a.y, a.z] for a in atoms],
                "constraints": None,
                "created": _now(),
                # D255: the calculation chosen in "At the geometry of" (D246e), kept for the import
                "at_calculation_id": at_calculation,
                "single_point": {
                    "level": level,
                    **{k: spec[k] for k in ("label", "psi4", "basis", "method", "dispersion")},
                    "basis_name": spec["basis_name"],
                    "scf_type": spec.get("scf_type", "df"),
                },
            }
            if history.SOURCE.get() != "manual":
                job["started_by"] = history.SOURCE.get()  # D223
            if method == SP_TEST_METHOD and os.environ.get("CHEMBOOK3D_LOCAL_STUB_DELAY"):
                job["stub_delay_s"] = float(os.environ["CHEMBOOK3D_LOCAL_STUB_DELAY"])
            _write(path / JOB_JSON, job)
            shutil.copyfile(WORKER_SOURCE, path / WORKER)
        _spawn(path)
        started.append(job_status(folder, job_id))
    return started
