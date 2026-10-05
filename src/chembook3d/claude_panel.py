"""The Claude panel (D92): the official `claude` CLI (Claude Code) in a pseudo-terminal, shown
in the app's Claude panel through a WebSocket (`api/claude.py`).

Only `claude` is ever started, never a shell. It runs in an app-managed working folder per
investigation, with settings written there on every start: the chembook3d MCP server (D91) as
its only MCP server, the notebook's read tools pre-approved, its write tools asking first, and
shell, file editing and web tools denied. It uses the person's own Claude Code sign-in; the app
stores no credentials.
"""

import codecs
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import threading
from collections.abc import Callable
from pathlib import Path

from chembook3d.settings import config_dir

SERVER_NAME = "chembook3d"
# Tools Claude Code may never use from the panel: no shell, no file editing, no web.
DENIED_TOOLS = (
    "Bash",
    "PowerShell",
    "Edit",
    "Write",
    "NotebookEdit",
    "WebFetch",
    "WebSearch",
)
SETTINGS_NAME = "chembook3d-settings.json"
MCP_CONFIG_NAME = "chembook3d-mcp.json"
INSTALL_DOCS = "https://code.claude.com/docs/en/setup"
INSTALL_COMMANDS = {
    "windows": "irm https://claude.ai/install.ps1 | iex",
    "unix": "curl -fsSL https://claude.ai/install.sh | bash",
}

NOTEBOOK_GUIDE = """\
# Chembook3D notebook assistant

This folder was written by Chembook3D and is rewritten each time the Claude panel starts.
You are running inside the Claude panel of Chembook3D, a local notebook for computational
chemistry mechanism investigations. The investigation open in the app is "{name}".

Work on the notebook only through the `chembook3d` MCP tools. They act on the investigation
open in the app, with the same rules and history as a click in its window. If you have no
`chembook3d` tools, say that the notebook tools are not installed in this version of
Chembook3D, and answer from what the person tells you.

Rules of the notebook:
- A reaction step is a stage of the mechanism (a column that lines up branches); a transition
  is a concrete edge between two nodes that realises a step. Keep them apart.
- Compare energies only at the same level of theory, including the geometry level
  (e.g. QZ//DZ); never mix levels. Take energies from the app; do not recompute them.
- Never choose or rebuild branch lineage from energies (for example from per-step minima).
- Deleting anything is confirmed by the person in the app window; you cannot confirm it.

Calculations (D93): nothing runs on this computer. An xTB or CREST calculation goes to a
Claude Code cloud session on the person's account, through the investigation's private GitHub
repository:
1. `create_cloud_job`: the nodes as inputs (their charge and multiplicity go with them) and
   exactly what to run and return (program, method such as GFN2-xTB, job type, solvent,
   constraints, which files). Ask about anything the person did not say that changes the
   result, then show them the job.
2. `start_cloud_job` only once they agree. Give them the session link; they can follow and
   steer it on claude.ai or their phone. If it is waiting_for_answer, Claude Code is asking
   something before it starts (whether it may trust the investigation folder, for one): the
   app shows that in a box at the top of this panel, and the person answers it there (never
   in another terminal). Tell them what it asks, then `get_cloud_job` with `wait` until the
   session link is there. If it ends in launch_failed, pass on what its launch_error says and
   start it again when they are ready; that pushes nothing new.
3. `get_cloud_job` with `wait` to see when it has finished (call again to keep waiting; they
   can also ask you later to look at a named job). If it has a `warning`, or the session says
   it could not push its results, the Claude GitHub App is not set up for the investigation's
   repository: ask the person to install it there (https://github.com/apps/claude), then ask
   the session to push again with `message_cloud_job`. Its results wait in the session.
4. `fetch_cloud_job`, then import the outputs it lists with `import_file` and
   `commit_import`, or set the coordinates of the node the person names. Say what changed,
   and tell them if the cloud session changed anything outside its job folder.
The investigation must be linked to GitHub (Sync, then Link to GitHub) for this to work.

Shell, file editing and web tools are switched off in this panel; the job tools do the git
and cloud work themselves.
"""


def _home_bin() -> list[Path]:
    """Where the official installer puts `claude` when it is not on the PATH."""
    home = Path.home()
    if sys.platform == "win32":
        appdata = os.environ.get("APPDATA")
        found = [home / ".local" / "bin" / "claude.exe"]
        if appdata:
            found.append(Path(appdata) / "npm" / "claude.cmd")
        return found
    return [home / ".local" / "bin" / "claude", home / ".claude" / "local" / "claude"]


def find_claude() -> str | None:
    """The `claude` executable: CHEMBOOK3D_CLAUDE if set, then the PATH, then the installer's
    usual folders."""
    override = os.environ.get("CHEMBOOK3D_CLAUDE")
    if override:
        return override if Path(override).is_file() else None
    found = shutil.which("claude")
    if found:
        return found
    for candidate in _home_bin():
        if candidate.is_file():
            return str(candidate)
    return None


def install_hint() -> dict[str, str]:
    key = "windows" if sys.platform == "win32" else "unix"
    return {"command": INSTALL_COMMANDS[key], "docs": INSTALL_DOCS}


def notebook_server() -> tuple[dict, list[str]] | None:
    """The chembook3d MCP server (D91) and its read-only tools, or None in a version of the app
    that does not have it yet (Claude then has no notebook tools)."""
    try:
        from chembook3d import mcp_tools  # type: ignore[attr-defined]
    except ImportError:
        return None
    read_only = sorted(tool.name for tool in mcp_tools.TOOLS if tool.kind == "read")
    # The app's own Python runs `chembook3d mcp`, so it works without uv on the PATH.
    server = {"type": "stdio", "command": sys.executable, "args": ["-m", "chembook3d.cli", "mcp"]}
    return server, read_only


def workspace(folder: Path) -> Path:
    """The working folder of the panel for one investigation, in the app's config folder, so
    `claude --continue` picks up that investigation's last conversation. Named after the
    folder plus a hash of its full path."""
    resolved = str(folder.resolve())
    digest = hashlib.sha256(resolved.encode("utf-8")).hexdigest()[:10]
    slug = re.sub(r"[^A-Za-z0-9_-]+", "-", folder.name).strip("-")[:40] or "investigation"
    return config_dir() / "claude" / f"{slug}-{digest}"


def prepare(folder: Path, name: str, app_url: str) -> Path:
    """Write the panel's working folder for an investigation and return it."""
    work = workspace(folder)
    work.mkdir(parents=True, exist_ok=True)
    servers: dict[str, dict] = {}
    allow: list[str] = []
    server = notebook_server()
    if server is not None:
        config, read_only = server
        config["args"] = [*config["args"], "--url", app_url]
        servers[SERVER_NAME] = config
        allow = [f"mcp__{SERVER_NAME}__{tool}" for tool in read_only]
    settings = {"permissions": {"allow": allow, "deny": list(DENIED_TOOLS)}}
    _write(work / MCP_CONFIG_NAME, json.dumps({"mcpServers": servers}, indent=2) + "\n")
    _write(work / SETTINGS_NAME, json.dumps(settings, indent=2) + "\n")
    _write(work / "CLAUDE.md", NOTEBOOK_GUIDE.format(name=name.replace("\n", " ")))
    return work


def _write(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8", newline="\n")


def command(executable: str, resume: bool) -> list[str]:
    """The command line: only `claude`, reading the files `prepare` wrote (relative paths, as
    it runs in that folder), with no other MCP servers."""
    argv = [
        executable,
        "--mcp-config",
        MCP_CONFIG_NAME,
        "--strict-mcp-config",
        "--settings",
        SETTINGS_NAME,
    ]
    if resume:
        argv.append("--continue")
    if sys.platform == "win32" and executable.lower().endswith((".cmd", ".bat")):
        # npm installs `claude.cmd`, which Windows runs only through cmd.exe. The arguments are
        # the fixed ones above, so nothing typed in the panel reaches cmd.exe.
        argv = [os.environ.get("COMSPEC", "cmd.exe"), "/c", *argv]
    return argv


def environment() -> dict[str, str]:
    env = dict(os.environ)
    env["TERM"] = "xterm-256color"
    env["COLORTERM"] = "truecolor"
    # Started from inside another Claude Code session, the CLI would think it is nested.
    env.pop("CLAUDECODE", None)
    return env


class Terminal:
    """A program in a pseudo-terminal. `on_output` receives text from a reader thread and is
    called once with None when the program has ended."""

    def __init__(
        self,
        argv: list[str],
        cwd: Path,
        rows: int,
        cols: int,
        on_output: Callable[[str | None], None],
    ):
        self._on_output = on_output
        self._closed = False
        self.exit_code: int | None = None
        if sys.platform == "win32":
            self._backend: _Backend = _WindowsBackend(argv, cwd, rows, cols)
        else:
            self._backend = _PosixBackend(argv, cwd, rows, cols)
        self._thread = threading.Thread(target=self._pump, name="claude-panel", daemon=True)
        self._thread.start()

    def _pump(self) -> None:
        try:
            while True:
                text = self._backend.read()
                if text is None:
                    break
                if text:
                    self._on_output(text)
        finally:
            self.exit_code = self._backend.wait()
            self._on_output(None)

    def write(self, text: str) -> None:
        if not self._closed:
            self._backend.write(text)

    def resize(self, rows: int, cols: int) -> None:
        if not self._closed:
            self._backend.resize(max(2, min(rows, 500)), max(10, min(cols, 1000)))

    def close(self) -> None:
        """Stop the program (and anything it started) if it is still running."""
        if self._closed:
            return
        self._closed = True
        self._backend.terminate()


class _Backend:
    def read(self) -> str | None: ...
    def write(self, text: str) -> None: ...
    def resize(self, rows: int, cols: int) -> None: ...
    def terminate(self) -> None: ...
    def wait(self) -> int | None: ...


class _PosixBackend(_Backend):
    def __init__(self, argv: list[str], cwd: Path, rows: int, cols: int):
        import pty

        master, slave = pty.openpty()
        _set_size(slave, rows, cols)
        try:
            # A session of its own with the terminal as controlling terminal, so Ctrl+C and
            # resizing reach the program as they would in a real terminal.
            self._process = subprocess.Popen(
                argv,
                cwd=cwd,
                env=environment(),
                stdin=slave,
                stdout=slave,
                stderr=slave,
                start_new_session=True,
                preexec_fn=_take_terminal,
                close_fds=True,
            )
        except BaseException:
            os.close(master)
            raise
        finally:
            os.close(slave)
        self._fd = master
        # Writes come from the event loop while the reader thread may close the terminal; the
        # lock keeps a write from reaching a file number the system has already reused.
        self._lock = threading.Lock()
        self._decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")

    def read(self) -> str | None:
        try:
            data = os.read(self._fd, 65536)
        except OSError:  # EIO once the program has ended and the terminal is closed
            data = b""
        if not data:
            return None
        return self._decoder.decode(data)

    def write(self, text: str) -> None:
        data = text.encode("utf-8")
        with self._lock:
            try:
                while data and self._fd >= 0:
                    data = data[os.write(self._fd, data) :]
            except OSError:
                pass

    def resize(self, rows: int, cols: int) -> None:
        with self._lock:
            if self._fd >= 0:
                try:
                    _set_size(self._fd, rows, cols)
                except OSError:
                    pass

    def terminate(self) -> None:
        import signal

        if self._process.poll() is None:
            try:
                os.killpg(self._process.pid, signal.SIGHUP)
            except OSError:
                pass
            try:
                self._process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(self._process.pid, signal.SIGKILL)
                except OSError:
                    pass

    def wait(self) -> int | None:
        code = self._process.wait()
        with self._lock:
            fd, self._fd = self._fd, -1
        os.close(fd)
        return code


def _take_terminal() -> None:  # runs in the child, after setsid
    import fcntl
    import termios

    fcntl.ioctl(0, termios.TIOCSCTTY, 0)


def _set_size(fd: int, rows: int, cols: int) -> None:
    import fcntl
    import struct
    import termios

    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))


class _WindowsBackend(_Backend):
    """ConPTY through pywinpty."""

    def __init__(self, argv: list[str], cwd: Path, rows: int, cols: int):
        from winpty import PtyProcess

        self._process = PtyProcess.spawn(
            argv, cwd=str(cwd), env=environment(), dimensions=(rows, cols)
        )

    def read(self) -> str | None:
        try:
            text = self._process.read(65536)
        except (EOFError, OSError):
            return None
        # pywinpty's reader thread sends this marker when a read found nothing.
        return text.replace("0011Ignore", "")

    def write(self, text: str) -> None:
        try:
            self._process.write(text)
        except (EOFError, OSError):
            pass

    def resize(self, rows: int, cols: int) -> None:
        try:
            self._process.setwinsize(rows, cols)
        except (EOFError, OSError):
            pass

    def terminate(self) -> None:
        if self._process.isalive():
            try:
                self._process.terminate(force=True)
            except (EOFError, OSError):
                pass

    def wait(self) -> int | None:
        try:
            self._process.wait()
        except (EOFError, OSError):
            pass
        code = self._process.exitstatus
        # pywinpty reads through a local socket pair that its close() leaves open once the
        # program has ended (it then counts as closed), so close those here.
        for sock in (self._process.fileobj, getattr(self._process, "_server", None)):
            if sock is not None:
                sock.close()
        return code
