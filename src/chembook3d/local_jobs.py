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
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
from sqlalchemy.orm import Session

from chembook3d import local_tools
from chembook3d.local_worker import CRITERIA, METHOD_LABELS, SCAN_POINTS, grid_cells
from chembook3d.models import Node
from chembook3d.parsers import local_result
from chembook3d.services import history, imports, oniom
from chembook3d.services import nodes as node_service
from chembook3d.services.records import RecordError

JOBS_DIR = "local-jobs"
JOB_JSON = "job.json"
PROGRESS = "progress.json"
RESULT = "result.json"
CANCEL = "cancel"
TRAJECTORY = "trajectory.xyz"
WORKER = "worker.py"
WORKER_SOURCE = Path(__file__).with_name("local_worker.py")
ALIVE_S = 30.0  # a heartbeat older than this: the process is gone
STARTING_S = 60.0  # a job without a heartbeat yet is starting for this long
_JOB_ID = re.compile(r"^\d{4}-\d{2}-\d{2}-[a-z0-9-]{1,60}$")

# The methods offered and what the result is called (the worker's METHOD_LABELS).
METHODS = {
    "gfn2": "GFN2-xTB",
    "b97-3c": "B97-3c",
    "r2scan-3c": "r2SCAN-3c",
    "oniom": "ONIOM(r2SCAN-3c:GFN2-xTB)",
    "gxtb": "g-xTB",  # D216: offered once installed (local_tools.install_gxtb)
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
    "stub": {"seconds": 1.0, "power": 0.0},
}
TIMING_ATOMS, TIMING_CORES = 70, 4
# CHEMBOOK3D_LOCAL_STUB=1 only (tests)
TEST_METHODS = {"stub": "stub", "oniom-stub": "ONIOM(stub:stub-low)", "stub-saddle": "stub-saddle"}
# D217: methods cheap enough for a numerical Hessian at the job's own level, so a TS search with
# one starts from its own Hessian and ends with frequencies at that level (on by default for a
# TS search, offered for a minimum); DFT and ONIOM keep the GFN2-xTB check instead.
FREQUENCY_METHODS = {"gfn2", "gxtb", "stub", "stub-saddle"}
OWN_HESSIAN = {"gfn2", "gxtb", "stub-saddle"}

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
    if os.environ.get("CHEMBOOK3D_LOCAL_STUB") == "1":
        return {**offered, **TEST_METHODS}
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
    """Keep what a finished job took per step, scaled back to 70 atoms on 4 cores."""
    cycles, elapsed = result.get("cycles"), result.get("elapsed_s")
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
    path = jobs_dir(folder) / job_id
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
) -> dict[str, Any]:
    """Start a job. `limit` (seconds, D223) refuses one whose estimate is longer: the longest
    job Claude may start while it works on its own."""
    if method not in methods():
        raise LocalJobError(f"Unknown method {method!r}; offered: {', '.join(methods())}")
    atoms = node_service.atoms_of(node)
    if not atoms:
        raise LocalJobError(f'Node "{node.label}" has no coordinates')
    if multiplicity < 1:
        raise LocalJobError("The multiplicity is at least 1")
    if local_tools.worker_python() is None:
        raise LocalJobError("Install the local tools first (Settings, Local calculations)")
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
        if seconds > limit:
            raise LocalJobError(
                f"This job is estimated at about {_duration(seconds)}, over the "
                f"{_duration(limit)} the user allows for a job you start on your own. Use a "
                "faster method or a smaller ONIOM core, or leave it and tell the user, who "
                "can start it or raise the limit."
            )
    with _records:
        default = f"{node.label} {METHODS.get(method, method)}" + (
            " TS" if transition else " scan" if scan else ""
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
        if history.SOURCE.get() != "manual":
            job["started_by"] = history.SOURCE.get()  # D223: "claude"
        if layers:
            job["oniom"] = layers
        if method == "gxtb":
            job["gxtb"] = str(local_tools.gxtb_binary())
            job["gxtb_version"] = local_tools.gxtb_status().get("version")
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


def _spawn(path: Path) -> None:
    """Start the worker as a process of its own: a new session on Linux; on Windows detached
    and outside the app's job object when the job allows it (uv runs the app in one)."""
    python = local_tools.worker_python()
    if python is None:
        raise LocalJobError("Install the local tools first (Settings, Local calculations)")
    argv = [str(python), "-I", str(path / WORKER), str(path)]
    argv += ["--lock", str(local_tools.lock_path())]
    job = _read(path / JOB_JSON) or {}
    env = {k: v for k, v in os.environ.items() if k not in ("PYTHONPATH", "PYTHONHOME")}
    env["PYTHONNOUSERSITE"] = "1"
    if not os.environ.get("CHEMBOOK3D_LOCAL_PYTHON"):
        env["PATH"] = os.pathsep.join(
            [str(p) for p in local_tools.env_path_dirs()] + [env.get("PATH", "")]
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
    if not progress or progress.get("state") == "finished":
        return False
    age = _age(progress.get("heartbeat_at"))
    return age is not None and age < ALIVE_S


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
            )
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
    out["importable"] = status in ("converged", "not_converged") and not job.get("imported")
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
PSI4_METHODS = {"b97-3c", "r2scan-3c", "oniom"}


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
    if not frames:
        data["coordinates"] = job.get("coordinates")  # the start, until the first step
    if job.get("scan"):
        # A scan's points so far, and for a grid every cell, so its map fills in.
        scan = {**job["scan"], "points": _lines(path / SCAN_POINTS)}
        if scan.get("grid"):
            scan["cells"] = [cell for _, _, cell in grid_cells(scan)]
        data["scan"] = scan
        data["frame_points"] = [p or 0 for p in frame_points]
    files = (path / TRAJECTORY, path / CRITERIA, path / SCAN_POINTS)
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


def import_result(session: Session, folder: Path, job_id: str) -> dict[str, Any]:
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
            return _import_result(session, folder, job_id)
    finally:
        history.SOURCE.reset(token)


def _import_result(session: Session, folder: Path, job_id: str) -> dict[str, Any]:
    status = job_status(folder, job_id)
    if status.get("imported"):
        raise LocalJobError("This job's result is imported already")
    if status["status"] not in ("converged", "not_converged"):
        raise LocalJobError(f"This job has no result to import ({status['status']})")
    node = session.get(Node, status["node_id"])
    if node is None:
        message = f'The node "{status["node_label"]}" this job was started from is gone'
        update_job(folder, job_id, import_error=message)
        raise LocalJobError(message)
    path = job_dir(folder, job_id) / RESULT
    job = read_job(folder, job_id)
    if job.get("scan"):
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
        )
    staging = imports.Staging()
    try:
        device = platform.node() or "this computer"
        staged = staging.add(path.read_bytes(), name, str(path), device=device)
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
    update_job(folder, job_id, imported=imported, import_error=None)
    try:
        result = json.loads(path.read_text(encoding="utf-8"))
        calibrate(result, read_job(folder, job_id))
    except (OSError, ValueError):
        pass
    return {**job_status(folder, job_id), "imported": imported}
