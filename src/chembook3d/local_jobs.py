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
from chembook3d.models import Node
from chembook3d.services import imports, oniom
from chembook3d.services import nodes as node_service
from chembook3d.services.records import RecordError

JOBS_DIR = "local-jobs"
JOB_JSON = "job.json"
PROGRESS = "progress.json"
RESULT = "result.json"
CANCEL = "cancel"
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


def calibrate(result: dict[str, Any], job: dict[str, Any]) -> None:
    """Keep what a finished job took per step, scaled back to 70 atoms on 4 cores."""
    cycles, elapsed = result.get("cycles"), result.get("elapsed_s")
    method = job.get("method")
    if not cycles or not elapsed or method not in METHODS or cycles < 3:
        return
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
) -> dict[str, Any]:
    if method not in methods():
        raise LocalJobError(f"Unknown method {method!r}; offered: {', '.join(methods())}")
    atoms = node_service.atoms_of(node)
    if not atoms:
        raise LocalJobError(f'Node "{node.label}" has no coordinates')
    if multiplicity < 1:
        raise LocalJobError("The multiplicity is at least 1")
    if local_tools.worker_python() is None:
        raise LocalJobError("Install the local tools first (Settings, Local calculations)")
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
    with _records:
        default = f"{node.label} {METHODS.get(method, method)}" + (" TS" if transition else "")
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
        if layers:
            job["oniom"] = layers
        if method == "gxtb":
            job["gxtb"] = str(local_tools.gxtb_binary())
            job["gxtb_version"] = local_tools.gxtb_status().get("version")
        if transition:
            # D215: a TS search, started from a GFN2-xTB Hessian (the tests' stand-in for it).
            job["transition"] = True
            job["ts_hessian"] = {"stub-saddle": "stub-saddle"}.get(
                method, "stub-low" if method in TEST_METHODS else "gfn2"
            )
            job["reacting"] = sorted(set(reacting)) if reacting else None
        if method in TEST_METHODS and os.environ.get("CHEMBOOK3D_LOCAL_STUB_DELAY"):
            job["stub_delay_s"] = float(os.environ["CHEMBOOK3D_LOCAL_STUB_DELAY"])
        _write(path / JOB_JSON, job)
        shutil.copyfile(WORKER_SOURCE, path / WORKER)
    _spawn(path)
    return job_status(folder, job_id)


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
    }
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
            )
        }
    out["importable"] = status in ("converged", "not_converged") and not job.get("imported")
    return out


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
    with _importing:
        return _import_result(session, folder, job_id)


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
