"""Calculations handed to Claude Code cloud sessions (D93, FR-CLOUD).

The app still runs no calculation on this computer (X1). When the user asks for one, Claude
(in the Claude panel, through the MCP tools of D91) writes a job folder into the investigation,
`jobs/<date>-<name>/` with `job.md` (what to run and return), `job.json` (the app's record) and
`inputs/`. Starting it commits only that folder (plus the cloud files of `cloud_templates`),
pushes it to the investigation's private GitHub repository (D71) and runs
`claude --cloud "<run the job>"`, which starts a cloud session on that repository. The session
installs xTB and CREST, runs the job and pushes `outputs/` and `result.json` in the job folder
to a branch of its own. It never touches the database, so nothing has to be merged: the app
finds `result.json` on whichever branch it is, copies the outputs into the local job folder,
and Claude imports them through the app like any other output file.
"""

import json
import os
import re
import subprocess
import sys
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any

from chembook3d import claude_panel, cloud_templates, sync

JOBS_DIR = "jobs"
JOB_MD = "job.md"
JOB_JSON = "job.json"
RESULT = "result.json"
INPUTS = "inputs"
OUTPUTS = "outputs"
RESERVED = {JOB_MD, JOB_JSON, RESULT, INPUTS, OUTPUTS}

MAX_INPUTS = 50
MAX_INPUT_BYTES = 5 * 1024 * 1024
MAX_INSTRUCTIONS = 20000
MAX_WAIT = 300.0  # seconds one status call may wait for a result
POLL_EVERY = 20.0  # seconds between fetches while waiting
LAUNCH_WAIT = 90.0  # seconds `start` waits for `claude --cloud` to name the session
LAUNCH_LIMIT = 6 * 3600.0  # a launcher still running after this is stopped

_FILE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")
_JOB_ID = re.compile(r"^\d{4}-\d{2}-\d{2}-[a-z0-9-]{1,60}$")
_ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b\][^\x07]*\x07")
_SESSION = re.compile(r"\b((?:session|cse)_[A-Za-z0-9]{8,})\b")
_URL = re.compile(r"https://claude\.ai/code/[^\s\"'<>)\]]+")


class CloudJobError(Exception):
    pass


class JobNotFound(CloudJobError):
    pass


@dataclass
class InputFile:
    name: str
    text: str
    description: str  # for job.md, e.g. 'node "TS1-2 guess" (charge 0, multiplicity 1)'
    node_id: str | None = None


def slug(text: str, limit: int = 40) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:limit].strip("-") or "job"


def jobs_dir(folder: Path) -> Path:
    return Path(folder) / JOBS_DIR


def job_dir(folder: Path, job_id: str) -> Path:
    if not _JOB_ID.match(job_id):
        raise JobNotFound(f"No job {job_id!r}")
    path = jobs_dir(folder) / job_id
    if not (path / JOB_JSON).is_file():
        raise JobNotFound(f"No job {job_id!r}")
    return path


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _read_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise CloudJobError(f"{path.name} cannot be read: {exc}") from exc
    if not isinstance(data, dict):
        raise CloudJobError(f"{path.name} is not a JSON object")
    return data


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def read_job(folder: Path, job_id: str) -> dict[str, Any]:
    return _read_json(job_dir(folder, job_id) / JOB_JSON)


def _save_job(folder: Path, record: dict[str, Any]) -> None:
    _write(jobs_dir(folder) / record["id"] / JOB_JSON, json.dumps(record, indent=2) + "\n")


_RECORDS = threading.Lock()  # the launcher's threads and requests both update job.json


def _update(folder: Path, job_id: str, **fields: Any) -> dict[str, Any]:
    with _RECORDS:
        record = read_job(folder, job_id)
        record.update(fields)
        _save_job(folder, record)
        return record


# ---------- writing a job ----------


def check_file_name(name: str) -> str:
    if not _FILE_NAME.match(name) or name in RESERVED or ".." in name:
        raise CloudJobError(
            f"{name!r} is not a usable file name: letters, digits, '.', '_' and '-' only, "
            "starting with a letter or digit"
        )
    return name


def _new_id(folder: Path, name: str) -> str:
    base = f"{datetime.now():%Y-%m-%d}-{slug(name)}"
    candidate, n = base, 2
    while (jobs_dir(folder) / candidate).exists():
        candidate, n = f"{base}-{n}", n + 1
    return candidate


def _job_md(job_id: str, name: str, investigation: str, inputs: list[InputFile], what: str) -> str:
    lines = [
        f"# Job {job_id}: {name}",
        "",
        f"Written by Chembook3D on {datetime.now():%Y-%m-%d} for the investigation "
        f'"{investigation}". '
        f"Run it as `{cloud_templates.INSTRUCTIONS}` describes and return the results in this "
        "job's folder.",
        "",
        "## Inputs",
        "",
    ]
    lines += [f"- `{INPUTS}/{item.name}`: {item.description}" for item in inputs] or ["(none)"]
    lines += [
        "",
        "## What to run",
        "",
        what.strip(),
        "",
        "## What to return",
        "",
        f"Each calculation's full standard output (`.out`) and the files asked for above in "
        f"`{OUTPUTS}/`, and `{RESULT}`, as `{cloud_templates.INSTRUCTIONS}` describes.",
        "",
    ]
    return "\n".join(lines)


def create_job(
    folder: Path, name: str, instructions: str, inputs: list[InputFile], investigation: str
) -> dict[str, Any]:
    """Write a new job folder on this computer. Nothing leaves it until `start_job`."""
    name = " ".join(name.split())
    if not name or len(name) > 80:
        raise CloudJobError("Give the job a short name (up to 80 characters)")
    if not instructions.strip():
        raise CloudJobError("Say what to run: program, method, settings and what to return")
    if len(instructions) > MAX_INSTRUCTIONS:
        raise CloudJobError(f"The instructions are longer than {MAX_INSTRUCTIONS} characters")
    if len(inputs) > MAX_INPUTS:
        raise CloudJobError(f"At most {MAX_INPUTS} input files")
    names = [check_file_name(item.name) for item in inputs]
    if len({n.lower() for n in names}) != len(names):
        raise CloudJobError("Two input files have the same name")
    for item in inputs:
        if len(item.text.encode("utf-8")) > MAX_INPUT_BYTES:
            raise CloudJobError(f"{item.name} is larger than {MAX_INPUT_BYTES // 2**20} MB")
    job_id = _new_id(folder, name)
    path = jobs_dir(folder) / job_id
    for item in inputs:
        _write(
            path / INPUTS / item.name, item.text if item.text.endswith("\n") else item.text + "\n"
        )
    _write(path / JOB_MD, _job_md(job_id, name, investigation, inputs, instructions))
    record = {
        "id": job_id,
        "name": name,
        "created": _now(),
        "inputs": [
            {"file": f"{INPUTS}/{item.name}", "node_id": item.node_id, "about": item.description}
            for item in inputs
        ],
        "state": "draft",
        "started": None,
        "branch": None,
        "commit": None,
        "session_id": None,
        "session_url": None,
        "launch_error": None,
        "fetched": None,
    }
    _save_job(folder, record)
    return record


def list_jobs(folder: Path) -> list[dict[str, Any]]:
    found = []
    root = jobs_dir(folder)
    if root.is_dir():
        for path in sorted(root.iterdir()):
            if _JOB_ID.match(path.name) and (path / JOB_JSON).is_file():
                try:
                    found.append(_read_json(path / JOB_JSON))
                except CloudJobError:
                    continue
    return found


# ---------- the cloud side, in the investigation's repository ----------


def write_cloud_files(folder: Path) -> list[str]:
    """Write or refresh the files a cloud session needs; returns the paths (with '/')."""
    folder = Path(folder)
    wanted = {
        cloud_templates.SETUP_SCRIPT: cloud_templates.SETUP_TEXT,
        cloud_templates.INSTRUCTIONS: cloud_templates.INSTRUCTIONS_TEXT,
    }
    settings = folder / cloud_templates.SETTINGS
    current = settings.read_text(encoding="utf-8") if settings.is_file() else None
    wanted[cloud_templates.SETTINGS] = cloud_templates.settings_text(current)
    for relative, text in wanted.items():
        path = folder / relative
        if not path.is_file() or path.read_text(encoding="utf-8") != text:
            _write(path, text)
    return list(wanted)


# ---------- starting ----------


def task_text(job_id: str) -> str:
    """The cloud session's task. Only the job id goes in, which is letters, digits and '-', so
    the command line is safe on every system (Windows runs `claude.cmd` through cmd.exe)."""
    if not _JOB_ID.match(job_id):
        raise CloudJobError(f"No job {job_id!r}")
    return (
        f"Run the Chembook3D calculation job in {JOBS_DIR}/{job_id}/ as {JOBS_DIR}/{job_id}/"
        f"{JOB_MD} and {cloud_templates.INSTRUCTIONS} say, then push its outputs and "
        f"{RESULT} to your branch."
    )


def claude_command(executable: str, job_id: str) -> list[str]:
    argv = [executable, "--cloud", task_text(job_id)]
    if sys.platform == "win32" and executable.lower().endswith((".cmd", ".bat")):
        argv = [os.environ.get("COMSPEC", "cmd.exe"), "/c", *argv]
    return argv


def _commit_paths(folder: Path, paths: list[str], message: str) -> None:
    """Commit only these paths, whatever else is changed or staged (the database stays as it
    is until the next sync)."""
    sync._git(folder, "add", "--", *paths)
    if sync._ok(folder, "diff", "--cached", "--quiet", "--", *paths):
        return  # committed already, by an earlier attempt
    sync._git(folder, *sync._identity(folder), "commit", "-q", "-m", message, "--", *paths)


def prepare_start(folder: Path, job_id: str) -> dict[str, Any]:
    """Commit the job and the cloud files, and push them, so a cloud session can clone them."""
    folder = Path(folder)
    record = read_job(folder, job_id)
    if record.get("session_id"):
        raise CloudJobError(
            f"This job is already running in a cloud session: {record.get('session_url')}. "
            "To run it again, create a new job."
        )
    if not sync.is_linked(folder):
        raise CloudJobError(
            "This investigation is not linked to a GitHub repository. Link it first (Sync, "
            "then Link to GitHub, D71): the cloud session clones the job from there."
        )
    if not sync.git_available():
        raise CloudJobError(sync.NO_GIT)
    current = sync.status(folder)
    if current.state in ("behind", "diverged"):
        raise CloudJobError(
            "GitHub has a newer version of this investigation (saved on another computer). "
            "Close and open the investigation to bring it in, then start the job."
        )
    if current.state in ("unreachable", "error"):
        raise CloudJobError(current.message)
    branch = sync._git(folder, "symbolic-ref", "--short", "-q", "HEAD", check=False).strip()
    if not branch:
        raise CloudJobError("The investigation folder is not on a branch; sync it first")
    paths = [f"{JOBS_DIR}/{job_id}", *write_cloud_files(folder)]
    first = record["state"] == "draft"
    if first:
        record = _update(folder, job_id, state="started", started=_now(), branch=branch)
    try:
        _commit_paths(folder, paths, f"Calculation job {job_id} for a Claude Code cloud session")
        sync._git(folder, "push", "-q", "-u", "origin", "HEAD", network=True)
    except sync.SyncError as exc:
        if first:
            _update(folder, job_id, state="draft", started=None, branch=None)
        raise CloudJobError(f"The job could not be pushed to GitHub: {exc}") from exc
    return _update(folder, job_id, commit=sync._git(folder, "rev-parse", "HEAD").strip())


@dataclass
class Launch:
    """One `claude --cloud` process and what it said."""

    job_id: str
    process: subprocess.Popen
    started: float = field(default_factory=time.monotonic)
    lines: deque[str] = field(default_factory=lambda: deque(maxlen=40))
    session_id: str | None = None
    session_url: str | None = None
    exit_code: int | None = None
    named: threading.Event = field(default_factory=threading.Event)

    @property
    def running(self) -> bool:
        return self.exit_code is None

    def output(self) -> str:
        return "\n".join(self.lines).strip()


def find_session(text: str) -> tuple[str | None, str | None]:
    """The session id and link in what `claude --cloud` printed, if any."""
    text = _ANSI.sub("", text)
    url_match = _URL.search(text)
    url = url_match.group(0).rstrip(".,;") if url_match else None
    id_match = _SESSION.search(text) or (_SESSION.search(url) if url else None)
    session = id_match.group(1) if id_match else None
    if session and not url:
        url = f"https://claude.ai/code/{session}"
    return session, url


class Launcher:
    """The `claude --cloud` processes the app started. `claude --cloud` creates the session,
    prints its id and link and may then keep following it; the app reads what it prints and
    leaves it running (it is stopped when the app stops, which does not stop the session)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._runs: dict[tuple[str, str], Launch] = {}

    def get(self, folder: Path, job_id: str) -> Launch | None:
        with self._lock:
            return self._runs.get((str(Path(folder).resolve()), job_id))

    def launch(
        self,
        folder: Path,
        job_id: str,
        executable: str,
        on_session: Callable[[Launch], None],
        on_end: Callable[[Launch], None],
    ) -> Launch:
        key = (str(Path(folder).resolve()), job_id)
        with self._lock:
            old = self._runs.get(key)
            if old is not None and old.running:
                return old
            kwargs: dict[str, Any] = {}
            if sys.platform == "win32":
                kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
            try:
                process = subprocess.Popen(
                    claude_command(executable, job_id),
                    cwd=folder,
                    env=claude_panel.environment(),
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    **kwargs,
                )
            except OSError as exc:
                raise CloudJobError(f"Claude Code could not be started: {exc}") from exc
            run = Launch(job_id, process)
            self._runs[key] = run
        threading.Thread(
            target=self._read, args=(run, on_session, on_end), name="claude-cloud", daemon=True
        ).start()
        threading.Thread(
            target=self._limit, args=(run,), name="claude-cloud-limit", daemon=True
        ).start()
        return run

    def _read(self, run: Launch, on_session: Callable, on_end: Callable) -> None:
        assert run.process.stdout is not None
        seen = ""
        try:
            for line in run.process.stdout:
                clean = _ANSI.sub("", line).rstrip()
                if clean:
                    run.lines.append(clean)
                if run.session_id is None:
                    seen = (seen + line)[-4000:]
                    session, url = find_session(seen)
                    if session:
                        run.session_id, run.session_url = session, url
                        on_session(run)
                        run.named.set()
        finally:
            run.exit_code = run.process.wait()
            run.process.stdout.close()
            on_end(run)
            run.named.set()

    def _limit(self, run: Launch) -> None:
        try:
            run.process.wait(timeout=LAUNCH_LIMIT)
        except subprocess.TimeoutExpired:
            run.process.kill()

    def close_all(self) -> None:
        with self._lock:
            runs = list(self._runs.values())
        for run in runs:
            if run.running:
                run.process.terminate()
                try:
                    run.process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    run.process.kill()


def start_job(folder: Path, job_id: str, launcher: Launcher, wait: float = LAUNCH_WAIT) -> dict:
    """Push the job and start a cloud session on it with the user's own Claude Code."""
    folder = Path(folder)
    executable = claude_panel.find_claude()
    if executable is None:
        raise CloudJobError(
            "Claude Code (the `claude` command) is not installed on this computer, so no "
            "cloud session can be started. Install it and sign in, then try again."
        )
    running = launcher.get(folder, job_id)
    if running is not None and running.running and running.session_id is None:
        raise CloudJobError("This job's cloud session is still being started")
    prepare_start(folder, job_id)

    def on_session(run: Launch) -> None:
        _update(
            folder,
            job_id,
            session_id=run.session_id,
            session_url=run.session_url,
            launch_error=None,
        )

    def on_end(run: Launch) -> None:
        if run.session_id is None and not read_job(folder, job_id).get("session_id"):
            said = run.output() or "nothing"
            _update(
                folder,
                job_id,
                launch_error=f"`claude --cloud` ended (exit code {run.exit_code}) without "
                f"naming a session. It said: {said[-1500:]}",
            )

    _update(folder, job_id, launch_error=None)
    run = launcher.launch(folder, job_id, executable, on_session, on_end)
    run.named.wait(timeout=wait)
    return job_status(folder, job_id, launcher, fetch=False)


# ---------- results ----------


def _result_refs(folder: Path, job_id: str) -> list[tuple[str, str]]:
    """(ref, commit time) of every branch that carries the job's result, newest first."""
    path = f"{JOBS_DIR}/{job_id}/{RESULT}"
    refs = sync._git(
        folder,
        "for-each-ref",
        "--format=%(refname)%00%(committerdate:iso-strict)",
        "refs/remotes/origin",
        "refs/heads",
    ).splitlines()
    found = []
    for line in refs:
        ref, _, when = line.partition("\0")
        if ref.endswith("/HEAD"):
            continue
        if sync._ok(folder, "cat-file", "-e", f"{ref}:{path}"):
            found.append((ref, when))
    found.sort(key=lambda item: item[1], reverse=True)
    return found


def _read_result(folder: Path, ref: str, job_id: str) -> dict[str, Any]:
    raw = sync._run(
        folder,
        ("cat-file", "blob", f"{ref}:{JOBS_DIR}/{job_id}/{RESULT}"),
        sync.LOCAL_TIMEOUT,
        True,
        binary=True,
    ).stdout
    try:
        data = json.loads(raw.decode("utf-8"))
    except ValueError:
        return {"status": "unreadable", "summary": f"{RESULT} is not valid JSON", "outputs": []}
    return data if isinstance(data, dict) else {"status": "unreadable", "outputs": []}


def _short(ref: str) -> str:
    return ref.removeprefix("refs/remotes/").removeprefix("refs/heads/")


def job_status(
    folder: Path,
    job_id: str,
    launcher: Launcher | None = None,
    fetch: bool = True,
    wait: float = 0.0,
) -> dict[str, Any]:
    """The job as recorded, where its cloud session stands and whether its result is on GitHub.
    With wait, fetch every POLL_EVERY seconds until a result appears or the time is up."""
    folder = Path(folder)
    record = read_job(folder, job_id)
    deadline = time.monotonic() + min(max(wait, 0.0), MAX_WAIT)
    fetch_error = None
    refs: list[tuple[str, str]] = []
    while True:
        if fetch and record["state"] != "draft" and sync.is_linked(folder):
            try:
                sync._git(folder, "fetch", "-q", "--prune", "origin", network=True, timeout=120)
                fetch_error = None
            except sync.SyncError as exc:
                fetch_error = str(exc)
        if record["state"] != "draft" and sync.git_available() and (folder / ".git").exists():
            refs = _result_refs(folder, job_id)
        if refs or time.monotonic() + POLL_EVERY > deadline:
            break
        time.sleep(POLL_EVERY)
        record = read_job(folder, job_id)
    record = read_job(folder, job_id)
    run = launcher.get(folder, job_id) if launcher else None
    out: dict[str, Any] = dict(record)
    out["folder"] = str(jobs_dir(folder) / job_id)
    out["launcher_running"] = bool(run and run.running)
    out["launcher_output"] = run.output() if run and not record.get("session_id") else None
    out["fetch_error"] = fetch_error
    out["result"] = None
    out["result_branch"] = None
    if refs:
        ref = refs[0][0]
        out["result_branch"] = _short(ref)
        out["result"] = _read_result(folder, ref, job_id)
        out["other_result_branches"] = [_short(r) for r, _ in refs[1:]]
    if record.get("fetched"):
        status = "fetched"
    elif refs:
        status = "finished"
    elif record["state"] == "draft":
        status = "draft"
    elif run is not None and run.running and not record.get("session_id"):
        status = "starting"
    elif record.get("launch_error") and not record.get("session_id"):
        status = "launch_failed"
    else:
        status = "running"
    out["status"] = status
    return out


def _safe_output(job_id: str, path: str) -> PurePosixPath | None:
    """The path below the job folder, if it is one the app copies (outputs/… or result.json)."""
    prefix = f"{JOBS_DIR}/{job_id}/"
    if not path.startswith(prefix):
        return None
    inner = PurePosixPath(path[len(prefix) :])
    parts = inner.parts
    if inner.as_posix() == RESULT:
        return inner
    if len(parts) < 2 or parts[0] != OUTPUTS:
        return None
    for part in parts:
        if part in ("", ".", "..") or re.search(r'[<>:"|?*\\\x00-\x1f]', part):
            return None
        if part.rstrip(" .") != part:  # Windows drops trailing dots and spaces
            return None
    return inner


def fetch_results(folder: Path, job_id: str) -> dict[str, Any]:
    """Copy the job's outputs and result.json from the branch the cloud session pushed into the
    local job folder; answers with the local paths to import."""
    folder = Path(folder)
    record = read_job(folder, job_id)
    if record["state"] == "draft":
        raise CloudJobError("This job has not been started")
    try:
        sync._git(folder, "fetch", "-q", "origin", network=True, timeout=120)
    except sync.SyncError as exc:
        fetch_error = str(exc)
    else:
        fetch_error = None
    refs = _result_refs(folder, job_id)
    if not refs:
        raise CloudJobError(
            "No result for this job on GitHub yet"
            + (f" ({fetch_error})" if fetch_error else "")
            + ". The cloud session pushes outputs and result.json when it has finished."
        )
    ref = refs[0][0]
    listed = sync._git(
        folder, "ls-tree", "-r", "-z", "--name-only", ref, "--", f"{JOBS_DIR}/{job_id}"
    ).split("\0")
    base = jobs_dir(folder) / job_id
    copied: list[str] = []
    skipped: list[str] = []
    for path in filter(None, listed):
        inner = _safe_output(job_id, path)
        if inner is None:
            if not path.startswith(f"{JOBS_DIR}/{job_id}/{INPUTS}/") and path not in (
                f"{JOBS_DIR}/{job_id}/{JOB_MD}",
                f"{JOBS_DIR}/{job_id}/{JOB_JSON}",
            ):
                skipped.append(path)
            continue
        data = sync._run(
            folder, ("cat-file", "blob", f"{ref}:{path}"), sync.LOCAL_TIMEOUT, True, binary=True
        ).stdout
        target = base.joinpath(*inner.parts)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        if inner.as_posix() != RESULT:
            copied.append(str(target))
    outside = []
    if record.get("commit"):
        changed = sync._git(
            folder, "diff", "--name-only", "-z", f"{record['commit']}...{ref}", check=False
        ).split("\0")
        outside = [p for p in changed if p and not p.startswith(f"{JOBS_DIR}/{job_id}/")]
    _update(folder, job_id, fetched={"at": _now(), "branch": _short(ref), "files": len(copied)})
    result = _read_result(folder, ref, job_id)
    return {
        "job": job_id,
        "branch": _short(ref),
        "result": result,
        "files": sorted(copied),
        "skipped": skipped,
        "changed_outside_job": outside,
    }
