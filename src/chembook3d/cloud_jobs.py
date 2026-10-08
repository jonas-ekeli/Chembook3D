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
import shlex
import subprocess
import sys
import threading
import time
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
LAUNCH_QUIET = 5.0  # no session and a screen this long unchanged: it waits for an answer
LAUNCH_GIVE_UP = 1800.0  # no session after this long: stopped
LAUNCH_REPLAY = 256 * 1024  # characters kept, so the Claude panel can show the screen
LAUNCH_LIMIT = 6 * 3600.0  # a launcher still running after this is stopped
LAUNCH_SIZE = (50, 250)  # rows, columns of its terminal: wide, so a link is never wrapped

_FILE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")
_JOB_ID = re.compile(r"^\d{4}-\d{2}-\d{2}-[a-z0-9-]{1,60}$")
_ANSI = re.compile(
    r"\x1b\[[0-9;?<>=!]*[ -/]*[@-~]"  # CSI
    r"|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)"  # OSC, ended by BEL or ST
    r"|\x1b[()][0-9A-Za-z]|\x1b[=>78]"
)
# Moving the cursor to another line (Windows' ConPTY draws lines this way) separates text like
# a line break, moving it along the line (Claude Code places words this way) like a space.
_CURSOR_LINE = re.compile(r"\x1b\[[0-9;?]*[ABEFHdf]")
_CURSOR_ALONG = re.compile(r"\x1b\[[0-9;?]*[CG]")
# What the Claude panel's xterm.js sends by itself, not the user: its attributes (ESC [ ? 1 ; 2 c)
# and cursor position (ESC [ row ; col R) when asked, its colours (OSC 11), and, once Windows'
# ConPTY turns focus reporting on, ESC [ I and ESC [ O as the panel takes and loses focus.
_REPORT = re.compile(
    r"\x1b\[(?:[?>]?[0-9;]*[cRn]|[IO])|\x1b\][0-9]+;rgb:[0-9a-fA-F/]*(?:\x07|\x1b\\)"
)
_SESSION = re.compile(r"\b((?:session|cse)_[A-Za-z0-9]{8,})\b")
_URL = re.compile(r"https://claude\.ai/code/[^\s\"'<>)\]]+")
# What Claude Code shows when it uploads the folder rather than having the session clone it
# from GitHub (the Claude GitHub App is not set up for the repository): such a session starts
# with no `origin` and can push only with the user's GitHub access.
_UPLOADED = re.compile(
    r"Packag(?:ing|ed) this (?:repository|folder)|Upload(?:ing|ed) this repository"
)
MAX_MESSAGE = 4000
MESSAGE_TIMEOUT = 180.0


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
    folder: Path,
    name: str,
    instructions: str,
    inputs: list[InputFile],
    investigation: str,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Write a new job folder on this computer. Nothing leaves it until `start_job`. `extra`
    goes into the record as it is (a scan path's ends, D114)."""
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
        **(extra or {}),
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
        cloud_templates.PATHTOOLS: cloud_templates.PATHTOOLS_TEXT,
        cloud_templates.INSTRUCTIONS: cloud_templates.instructions_text(
            cloud_templates.github_https(sync.remote_url(folder))
        ),
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


def _runnable(argv: list[str]) -> list[str]:
    """Windows runs an npm-installed `claude.cmd` only through cmd.exe. Every argument the app
    passes is its own (a job id, a session id), never text typed by someone."""
    if sys.platform == "win32" and argv[0].lower().endswith((".cmd", ".bat")):
        return [os.environ.get("COMSPEC", "cmd.exe"), "/c", *argv]
    return argv


def claude_command(executable: str, job_id: str) -> list[str]:
    # Auto mode: the cloud session runs the job to the end, its actions checked by Claude
    # Code's classifier rather than waiting for someone to approve them on claude.ai (A40).
    return _runnable([executable, "--permission-mode", "auto", "--cloud", task_text(job_id)])


def launch_command(executable: str | None, folder: Path, job_id: str) -> str:
    """What the user can type in a terminal to start the session themselves."""
    task = task_text(job_id)
    exe = executable or "claude"
    if sys.platform == "win32":
        return f'cd "{folder}"; & "{exe}" --permission-mode auto --cloud "{task}"'
    return (
        f"cd {shlex.quote(str(folder))}; {shlex.quote(exe)} --permission-mode auto --cloud "
        f"{shlex.quote(task)}"
    )


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
    if record.get("commit") and _on_github(folder, record["commit"]):
        return record  # pushed by an earlier start whose launch failed: nothing to commit again
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


def _on_github(folder: Path, commit: str) -> bool:
    """Whether a commit is on GitHub as of the last fetch (`sync.status` fetched just now)."""
    if not sync._ok(folder, "cat-file", "-e", f"{commit}^{{commit}}"):
        return False
    remote = sync._git(folder, "branch", "-r", "--contains", commit, check=False)
    return any(line.strip() and "->" not in line for line in remote.splitlines())


@dataclass
class Launch:
    """One `claude --cloud` run and what it showed. It runs in a pseudo-terminal: without one,
    Claude Code refuses to create a cloud session. When it stops at a question (the folder
    trust question, for one), the Claude panel shows its screen and the user answers there."""

    job_id: str
    name: str = ""
    terminal: claude_panel.Terminal | None = None
    started: float = field(default_factory=time.monotonic)
    screen: str = ""  # the last of what it wrote, terminal codes and all
    last_output: float = field(default_factory=time.monotonic)
    session_id: str | None = None
    session_url: str | None = None
    exit_code: int | None = None
    stopped: str | None = None  # why the app stopped it before it named a session
    ended: bool = False
    named: threading.Event = field(default_factory=threading.Event)
    # Claude panel views of its screen: called with ("output", text), ("named", url) and
    # ("ended", None).
    viewers: list[Callable[[str, str | None], None]] = field(default_factory=list)
    lock: threading.Lock = field(default_factory=threading.Lock)
    asked: bool = False  # a still screen was seen and the user has not typed since

    @property
    def running(self) -> bool:
        return not self.ended

    @property
    def waiting(self) -> bool:
        """Whether it shows a question: running, no session yet, and a still screen. Once
        seen, a question stays until the user types: a terminal redrawing itself (Windows
        does when the Claude panel resizes it to show the question) answers nothing."""
        if not (self.screen and self.running and self.session_id is None):
            return False
        if time.monotonic() - self.last_output >= LAUNCH_QUIET:
            self.asked = True
        return self.asked

    def output(self) -> str:
        return "\n".join(screen_lines(self.screen)[-40:]).strip()

    def watch(self, viewer: Callable[[str, str | None], None]) -> str:
        """Send what it writes from now on to `viewer`, and return what it has written so
        far, so the viewer can draw the screen."""
        with self.lock:
            if self.ended:
                return self.screen
            self.viewers.append(viewer)
            return self.screen

    def unwatch(self, viewer: Callable[[str, str | None], None]) -> None:
        with self.lock:
            if viewer in self.viewers:
                self.viewers.remove(viewer)
            last = not self.viewers
        if last and self.terminal is not None and self.running:
            self.terminal.resize(*LAUNCH_SIZE)

    def answer(self, text: str) -> None:
        """Keys the user typed in the Claude panel. A report the panel's terminal sends by
        itself is passed on but answers nothing: it let a start report "starting" while the
        question was on screen."""
        if _REPORT.sub("", text):
            self.asked = False
            self.last_output = time.monotonic()  # a still screen from now on is a new question
        if self.terminal is not None and self.running:
            self.terminal.write(text)

    def resize(self, rows: int, cols: int) -> None:
        if self.terminal is not None and self.running:
            self.terminal.resize(rows, cols)


def screen_lines(raw: str) -> list[str]:
    """Readable lines from what a program wrote to a terminal: no terminal codes, a line
    redrawn in place only once, and no repeated or empty lines."""
    text = _ANSI.sub("", _CURSOR_ALONG.sub(" ", _CURSOR_LINE.sub("\n", raw)))
    lines: list[str] = []
    for line in re.split(r"\r?\n", text):
        line = line.split("\r")[-1] if "\r" in line.rstrip("\r") else line.rstrip("\r")
        line = re.sub(r"[ \t]+", " ", "".join(c for c in line if c >= " " or c == "\t")).strip()
        if line and (not lines or lines[-1] != line):
            lines.append(line)
    return lines


def find_session(text: str) -> tuple[str | None, str | None]:
    """The session id and link in what `claude --cloud` printed, if any."""
    text = "\n".join(screen_lines(text))
    url_match = _URL.search(text)
    url = url_match.group(0).rstrip(".,;") if url_match else None
    id_match = _SESSION.search(text) or (_SESSION.search(url) if url else None)
    session = id_match.group(1) if id_match else None
    if session and (not url or session not in url):  # no link, or one cut off at the edge
        url = f"https://claude.ai/code/{session}"
    return session, url


# Questions a program may ask its terminal, and the answers a plain terminal gives.
_QUERIES = {
    "\x1b[6n": "\x1b[1;1R",  # where is the cursor
    "\x1b[c": "\x1b[?1;2c",  # what kind of terminal
    "\x1b[0c": "\x1b[?1;2c",
    "\x1b]11;?\x07": "\x1b]11;rgb:0000/0000/0000\x07",  # background colour
    "\x1b]11;?\x1b\\": "\x1b]11;rgb:0000/0000/0000\x1b\\",
}


class Launcher:
    """The `claude --cloud` runs the app started. `claude --cloud` creates the session,
    prints its id and link and may then keep following it; the app reads what it shows and
    leaves it running (it is stopped when the app stops, which does not stop the session). The
    app answers none of its questions itself: one waiting for an answer is shown in the Claude
    panel. One that names no session is stopped after LAUNCH_GIVE_UP."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._runs: dict[tuple[str, str], Launch] = {}

    def get(self, folder: Path, job_id: str) -> Launch | None:
        with self._lock:
            return self._runs.get((str(Path(folder).resolve()), job_id))

    def waiting(self, folder: Path) -> list[Launch]:
        """The runs for this investigation that wait for an answer."""
        key = str(Path(folder).resolve())
        with self._lock:
            runs = [run for (where, _), run in self._runs.items() if where == key]
        return [run for run in runs if run.waiting]

    def launch(
        self,
        folder: Path,
        job_id: str,
        executable: str,
        on_session: Callable[[Launch], None],
        on_end: Callable[[Launch], None],
        name: str = "",
    ) -> Launch:
        key = (str(Path(folder).resolve()), job_id)
        with self._lock:
            old = self._runs.get(key)
            if old is not None and old.running:
                return old
            run = Launch(job_id, name=name)
            seen = [""]
            ready = threading.Event()  # run.terminal is set

            def on_output(text: str | None) -> None:
                if text is None:
                    ready.wait(timeout=10)
                    run.exit_code = run.terminal.exit_code if run.terminal else None
                    with run.lock:
                        run.ended = True
                        viewers, run.viewers = run.viewers, []
                    on_end(run)
                    run.named.set()
                    for viewer in viewers:
                        viewer("ended", None)
                    return
                run.last_output = time.monotonic()
                with run.lock:
                    run.screen = (run.screen + text)[-LAUNCH_REPLAY:]
                    viewers = list(run.viewers)
                for viewer in viewers:
                    viewer("output", text)
                for query, answer in _QUERIES.items():
                    if query in text and run.terminal is not None:
                        run.terminal.write(answer)
                if run.session_id is None:
                    seen[0] = (seen[0] + text)[-8000:]
                    session, url = find_session(seen[0])
                    if session:
                        run.session_id, run.session_url = session, url
                        on_session(run)
                        run.named.set()
                        for viewer in viewers:
                            viewer("named", url)

            rows, cols = LAUNCH_SIZE
            try:
                run.terminal = claude_panel.Terminal(
                    claude_command(executable, job_id), Path(folder), rows, cols, on_output
                )
            except OSError as exc:
                raise CloudJobError(f"Claude Code could not be started: {exc}") from exc
            finally:
                ready.set()
            self._runs[key] = run
        threading.Thread(
            target=self._watch, args=(run,), name="claude-cloud-watch", daemon=True
        ).start()
        return run

    def _watch(self, run: Launch) -> None:
        """Stop a run that waits for an answer, names no session in time, or runs too long."""
        while not run.ended:
            now = time.monotonic()
            if run.session_id is None and now - run.started > LAUNCH_GIVE_UP:
                run.stopped = f"It named no session in {LAUNCH_GIVE_UP / 60:.0f} minutes" + (
                    ", waiting for an answer" if run.waiting else ""
                )
            elif now - run.started > LAUNCH_LIMIT:
                run.stopped = "It ran too long"
            if run.stopped:
                assert run.terminal is not None
                run.terminal.close()
                return
            time.sleep(min(1.0, LAUNCH_QUIET / 4))

    def close_all(self) -> None:
        with self._lock:
            runs = list(self._runs.values())
        for run in runs:
            if run.running and run.terminal is not None:
                run.terminal.close()


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
            uploaded=bool(_UPLOADED.search("\n".join(screen_lines(run.screen)))),
        )

    def on_end(run: Launch) -> None:
        if run.session_id is None and not read_job(folder, job_id).get("session_id"):
            said = run.output() or "nothing"
            why = (
                f"{run.stopped}. It showed"
                if run.stopped
                else f"`claude --cloud` ended (exit code {run.exit_code}) without naming a "
                "session. It said"
            )
            if run.stopped:
                advice = (
                    "Start the job again and answer Claude Code's question in the Claude panel "
                    "when it shows it."
                )
            else:
                advice = (
                    "To start the session yourself, run this in a terminal: "
                    f"{launch_command(executable, folder, job_id)}\nThe app finds the result "
                    "when that session pushes it."
                )
            _update(folder, job_id, launch_error=f"{why}: {said[-1500:]}\n\n{advice}")

    _update(folder, job_id, launch_error=None)
    name = str(read_job(folder, job_id).get("name") or job_id)
    run = launcher.launch(folder, job_id, executable, on_session, on_end, name=name)
    _wait_for_launch(run, wait)
    return job_status(folder, job_id, launcher, fetch=False)


def _wait_for_launch(run: Launch, wait: float, until_answered: bool = False) -> None:
    """Until the run names its session or ends, or the time is up; and, unless until_answered,
    until it waits for an answer."""
    deadline = time.monotonic() + wait
    while not run.named.wait(timeout=min(0.25, max(deadline - time.monotonic(), 0))):
        if (run.waiting and not until_answered) or time.monotonic() >= deadline:
            return


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
    run = launcher.get(folder, job_id) if launcher else None
    if run is not None and run.running and run.session_id is None and wait > 0:
        # Still being started (perhaps waiting for the user's answer in the Claude panel):
        # wait for it rather than for a result.
        _wait_for_launch(run, min(max(wait, 0.0), MAX_WAIT), until_answered=True)
        deadline = time.monotonic()
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
    out["question"] = run.output() if run and run.waiting else None
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
    elif run is not None and run.waiting and not record.get("session_id"):
        status = "waiting_for_answer"
    elif run is not None and run.running and not record.get("session_id"):
        status = "starting"
    elif record.get("launch_error") and not record.get("session_id"):
        status = "launch_failed"
    else:
        status = "running"
    out["status"] = status
    out["warning"] = None
    if record.get("uploaded") and status == "running":
        repository = cloud_templates.github_https(sync.remote_url(folder)) or "the repository"
        out["warning"] = (
            "Claude Code uploaded the investigation folder instead of having the session clone "
            f"it from GitHub, because the Claude GitHub App is not set up for {repository}. "
            "The session can push its results only if it is: install the app "
            "(https://github.com/apps/claude) on that repository, then, if the session says "
            "it could not push, ask it to push again with message_cloud_job."
        )
    return out


def send_message(folder: Path, job_id: str, text: str) -> dict[str, Any]:
    """Send a message to the job's cloud session, as its user would type it on claude.ai.
    The text goes to `claude -p --cloud <session>` on its standard input, never on a command
    line."""
    folder = Path(folder)
    record = read_job(folder, job_id)
    session = record.get("session_id") or ""
    if not _SESSION.fullmatch(session):
        raise CloudJobError("This job has no cloud session yet; start it first")
    text = text.strip()
    if not text:
        raise CloudJobError("The message is empty")
    if len(text) > MAX_MESSAGE:
        raise CloudJobError(f"The message is longer than {MAX_MESSAGE} characters")
    executable = claude_panel.find_claude()
    if executable is None:
        raise CloudJobError("Claude Code (the `claude` command) is not installed on this computer")
    kwargs: dict[str, Any] = {}
    if sys.platform == "win32":
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    try:
        done = subprocess.run(
            _runnable([executable, "-p", "--output-format", "json", "--cloud", session]),
            input=text,
            cwd=folder,
            env=claude_panel.environment(),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=MESSAGE_TIMEOUT,
            **kwargs,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise CloudJobError(f"Claude Code could not send the message: {exc}") from exc
    answer: dict[str, Any] = {}
    for line in reversed(done.stdout.splitlines()):
        try:
            parsed = json.loads(line)
        except ValueError:
            continue
        if isinstance(parsed, dict):
            answer = parsed
            break
    if not answer.get("ok"):
        said = answer.get("error") or "\n".join(screen_lines(done.stdout + done.stderr))[-1500:]
        raise CloudJobError(f"The message could not be sent to the cloud session: {said}")
    return {"sent": True, "session_id": session, "session_url": record.get("session_url")}


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
