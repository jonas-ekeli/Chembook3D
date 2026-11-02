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

from sqlalchemy.orm import Session

from chembook3d import local_tools
from chembook3d.models import Node
from chembook3d.services import imports
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
METHODS = {"gfn2": "GFN2-xTB"}
TEST_METHODS = {"stub": "stub"}  # CHEMBOOK3D_LOCAL_STUB=1 only (tests)

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
    if os.environ.get("CHEMBOOK3D_LOCAL_STUB") == "1":
        return {**METHODS, **TEST_METHODS}
    return dict(METHODS)


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
    resources = local_tools.resources()
    with _records:
        job_id = _new_id(folder, name or f"{node.label} {METHODS.get(method, method)}")
        path = jobs_dir(folder) / job_id
        path.mkdir(parents=True)
        job = {
            "id": job_id,
            "kind": "refine",
            "node_id": node.id,
            "node_label": node.label,
            "method": method,
            "level": methods()[method],
            "charge": charge,
            "multiplicity": multiplicity,
            "cores": resources.cores,
            "memory_mb": resources.memory_mb,
            "elements": [a.element for a in atoms],
            "coordinates": [[a.x, a.y, a.z] for a in atoms],
            "constraints": constraints or None,
            "created": _now(),
        }
        if method == "stub" and os.environ.get("CHEMBOOK3D_LOCAL_STUB_DELAY"):
            job["stub_delay_s"] = float(os.environ["CHEMBOOK3D_LOCAL_STUB_DELAY"])
        _write(path / JOB_JSON, job)
        shutil.copyfile(WORKER_SOURCE, path / WORKER)
    _spawn(path)
    return job_status(folder, job_id)


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
    }
    if result is not None:
        out["result"] = {
            key: result.get(key)
            for key in ("state", "energy", "cycles", "elapsed_s", "error", "program", "level")
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
    return {**job_status(folder, job_id), "imported": imported}
