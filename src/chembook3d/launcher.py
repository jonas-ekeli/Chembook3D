"""The one-click launcher (D107, FR-RUN): what the Chembook3D shortcut runs.

The shortcut (`chembook3d shortcut`) runs this file from the checkout by its path, with the
standard library only, so the `git pull` below may replace it and the packages it pulls in:

1. When Chembook3D already answers on its port, open a tab on it and stop.
2. Open a tab on a small page that shows each step below while it works.
3. Update the checkout with `git pull --ff-only`, only on `main` with no local changes.
4. Rebuild the interface when `frontend/` differs from the tree it was last built from.
5. Start the server through `uv run` (which installs what uv.lock asks for) with no window;
   the page turns into the app once the server answers.

It then waits for the server, which stops by itself when the last tab has closed. What it does
goes to launcher.log in the app's config folder.
"""

import argparse
import http.client
import json
import os
import shutil
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser
from collections.abc import Callable
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import IO, Any

HOST = "127.0.0.1"  # loopback only (NFR-SEC-01)
PORT = 8765  # the app's, as in cli.py
PAGE_PORT = 8764  # the launcher's page; a second double-click while it works finds it here
REPO = Path(__file__).resolve().parents[2]
STAMP = ".chembook3d-build"  # in frontend/dist: the git tree of frontend/ it was built from
SETTINGS = "launcher.json"  # in the config folder, written by `chembook3d shortcut`
LOG = "launcher.log"
WINDOWS = sys.platform == "win32"
# A console program started from the windowless launcher would open a console window of its own.
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

GIT_TIMEOUT = 30  # seconds, for the quick git questions
PULL_TIMEOUT = 120
BUILD_TIMEOUT = 1800  # npm ci on a slow connection
START_TIMEOUT = 600  # uv may first install packages the pull asked for
PAGE_QUIET = 60  # after a failure the page is kept until nobody has looked at it this long


def config_dir() -> Path:
    """The app's config folder, found as `settings.config_dir` finds it with platformdirs."""
    override = os.environ.get("CHEMBOOK3D_CONFIG_DIR")
    if override:
        return Path(override)
    if WINDOWS:
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    elif sys.platform == "darwin":
        base = str(Path.home() / "Library" / "Application Support")
    else:
        base = os.environ.get("XDG_CONFIG_HOME", "").strip() or str(Path.home() / ".config")
    return Path(base) / "chembook3d"


def load_settings(folder: Path) -> dict[str, Any]:
    """What `chembook3d shortcut` saved: uv's path, the folders of the tools, the browser."""
    try:
        found = json.loads((folder / SETTINGS).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return found if isinstance(found, dict) else {}


def tool_env(settings: dict[str, Any], base: dict[str, str] | None = None) -> dict[str, str]:
    """The environment for git, uv, npm and the server. A shortcut starts with the desktop's
    PATH, which often lacks what the terminal had (uv in ~/.local/bin, Node.js from nvm), so the
    folders saved when the shortcut was made come first."""
    env = dict(os.environ if base is None else base)
    for name in ("VIRTUAL_ENV", "PYTHONHOME", "PYTHONPATH"):
        env.pop(name, None)
    current = [p for p in env.get("PATH", "").split(os.pathsep) if p]
    saved = [p for p in settings.get("path", []) if isinstance(p, str) and p]
    env["PATH"] = os.pathsep.join(dict.fromkeys([*saved, *current]))
    env["GIT_TERMINAL_PROMPT"] = "0"  # never wait for a password nobody can type
    env["GCM_INTERACTIVE"] = "never"
    return env


class Log:
    """launcher.log; the server started below writes its output into the same file."""

    def __init__(self, path: Path | None) -> None:
        self.path = path
        self.file: IO[str] | None = None
        if path is not None:
            path.parent.mkdir(parents=True, exist_ok=True)
            try:  # keep the launch before this one
                path.replace(path.with_name("launcher.previous.log"))
            except OSError:
                pass
            self.file = open(path, "w", encoding="utf-8", errors="replace")  # noqa: SIM115

    def write(self, text: str) -> None:
        if self.file is None:
            return
        stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        for line in text.rstrip().splitlines() or [""]:
            self.file.write(f"{stamp} {line}\n")
        self.file.flush()

    def close(self) -> None:
        if self.file is not None:
            self.file.close()
            self.file = None

    def tail(self, size: int = 200_000) -> str:
        if self.path is None:
            return ""
        try:
            data = self.path.read_bytes()
        except OSError:
            return ""
        return data[-size:].decode("utf-8", errors="replace")


def run(
    command: list[str], cwd: Path, env: dict[str, str], log: Log, timeout: float = GIT_TIMEOUT
) -> subprocess.CompletedProcess[str]:
    """Run a command with no window, its output into the log. Raises on a timeout."""
    log.write("> " + " ".join(command))
    result = subprocess.run(
        command,
        cwd=cwd,
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        creationflags=NO_WINDOW,
    )
    if result.stdout.strip():
        log.write(result.stdout)
    return result


# ---------- update ----------


def update(repo: Path, env: dict[str, str], log: Log) -> tuple[str, str | None]:
    """Pull, only on a clean `main` and only fast-forward (A54). Returns what was done and, when
    the checkout was not updated, why (shown in the app). Never merges, stashes or resets."""
    git = shutil.which("git", path=env.get("PATH"))
    if git is None:
        return "Skipped", "Not updated: git was not found."
    if not (repo / ".git").exists():
        return "Skipped", "Not updated: this copy of Chembook3D is not a git checkout."

    def git_run(*args: str, timeout: float = GIT_TIMEOUT) -> subprocess.CompletedProcess[str]:
        return run([git, "-C", str(repo), *args], repo, env, log, timeout)

    try:
        branch = git_run("rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
        if branch != "main":
            where = "is not on a branch" if branch == "HEAD" else f"is on branch “{branch}”"
            return "Skipped", f"Not updated: the checkout {where}, not main."
        if git_run("status", "--porcelain", "--untracked-files=no").stdout.strip():
            return "Skipped", "Not updated: the checkout has local changes."
        before = git_run("rev-parse", "HEAD").stdout.strip()
        pulled = git_run("pull", "--ff-only", "--no-rebase", timeout=PULL_TIMEOUT)
    except subprocess.TimeoutExpired:
        return "Skipped", "Not updated: git did not answer in time (no network?)."
    except OSError as exc:
        return "Skipped", f"Not updated: git could not be run ({exc})."
    if pulled.returncode != 0:
        lines = [line.strip() for line in pulled.stdout.splitlines() if line.strip()]
        why = lines[-1] if lines else f"exit code {pulled.returncode}"
        return "Skipped", f"Not updated: git pull failed ({why})."
    after = git_run("rev-parse", "HEAD").stdout.strip()
    if after == before:
        return "Already up to date", None
    return f"Updated {before[:7]} → {after[:7]}", None


# ---------- interface ----------


def frontend_tree(repo: Path, env: dict[str, str] | None = None) -> str | None:
    """The git tree id of the committed `frontend/`, or None outside a git checkout."""
    path = None if env is None else env.get("PATH")
    git = shutil.which("git", path=path)
    if git is None or not (repo / ".git").exists():
        return None
    try:
        result = subprocess.run(
            [git, "-C", str(repo), "rev-parse", "HEAD:frontend"],
            env=env,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=GIT_TIMEOUT,
            creationflags=NO_WINDOW,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return result.stdout.strip() if result.returncode == 0 else None


def write_stamp(repo: Path) -> None:
    """Record which `frontend/` the build in frontend/dist came from (scripts/build_frontend.py
    calls this after a build)."""
    tree = frontend_tree(repo)
    stamp = repo / "frontend" / "dist" / STAMP
    if tree is None:
        stamp.unlink(missing_ok=True)
    else:
        stamp.write_text(tree + "\n", encoding="utf-8")


def built(repo: Path) -> bool:
    frontend = repo / "frontend"
    return (frontend / "dist" / "index.html").is_file() and (
        frontend / "dist-snapshot" / "snapshot.html"
    ).is_file()


def needs_build(repo: Path, env: dict[str, str] | None = None) -> bool:
    """No build yet, or one made from another `frontend/` than the one checked out. Outside a
    git checkout an existing build is kept."""
    if not built(repo):
        return True
    tree = frontend_tree(repo, env)
    if tree is None:
        return False
    try:
        stamp = (repo / "frontend" / "dist" / STAMP).read_text(encoding="utf-8").strip()
    except OSError:
        return True
    return stamp != tree


def build(repo: Path, uv: str, env: dict[str, str], log: Log, keep: Path) -> str | None:
    """Rebuild the interface with scripts/build_frontend.py. A failed rebuild puts the previous
    build back. Returns why the old interface is shown, or None."""
    frontend = repo / "frontend"
    outputs = ["dist", "dist-snapshot"]
    had = built(repo)
    shutil.rmtree(keep, ignore_errors=True)
    if had:
        for name in outputs:
            shutil.copytree(frontend / name, keep / name)
    command = [uv, "run", "--project", str(repo), "python", "scripts/build_frontend.py"]
    log.write("> " + " ".join(command))
    try:
        result = subprocess.run(
            command,
            cwd=repo,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=log.file or subprocess.DEVNULL,
            stderr=subprocess.STDOUT,
            timeout=BUILD_TIMEOUT,
            creationflags=NO_WINDOW,
        )
        ok = result.returncode == 0
    except (OSError, subprocess.TimeoutExpired) as exc:
        log.write(f"The build could not run: {exc}")
        ok = False
    if not ok and had:
        for name in outputs:
            shutil.rmtree(frontend / name, ignore_errors=True)
            shutil.copytree(keep / name, frontend / name)
    shutil.rmtree(keep, ignore_errors=True)
    if ok:
        return None
    return "The interface could not be rebuilt, so the previous version is shown. See launcher.log."


# ---------- the server ----------

_DIRECT = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # never through a proxy


def get_json(url: str, timeout: float = 1.0) -> Any:
    try:
        with _DIRECT.open(url, timeout=timeout) as response:
            return json.loads(response.read())
    except (OSError, ValueError, http.client.HTTPException):
        return None


def app_answers(port: int) -> bool:
    found = get_json(f"http://{HOST}:{port}/api/health")
    return isinstance(found, dict) and found.get("status") == "ok"


def start(
    repo: Path, uv: str, port: int, notices: list[str], env: dict[str, str], log: Log
) -> subprocess.Popen[bytes]:
    command = [uv, "run", "--project", str(repo), "chembook3d", "--launched", "--port", str(port)]
    for notice in notices:
        command += ["--notice", notice]
    log.write("> " + " ".join(command))
    return subprocess.Popen(
        command,
        cwd=repo,
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=log.file if log.file is not None else subprocess.DEVNULL,
        stderr=subprocess.STDOUT,
        creationflags=NO_WINDOW,
    )


# ---------- the page shown while it works ----------


class Progress:
    """What the page shows: each step, then the app's address, or what went wrong."""

    def __init__(self, app_url: str) -> None:
        self.lock = threading.Lock()
        self.app = app_url
        self.steps = [
            {"id": "update", "name": "Check for updates", "state": "waiting", "detail": ""},
            {"id": "build", "name": "Build the interface", "state": "waiting", "detail": ""},
            {"id": "start", "name": "Start Chembook3D", "state": "waiting", "detail": ""},
        ]
        self.notices: list[str] = []
        self.error: str | None = None
        self.ready = False
        self.delivered = threading.Event()  # the page has seen that the app is ready
        self.looked = time.monotonic()  # when the page last asked

    def set(self, step: str, state: str, detail: str = "") -> None:
        with self.lock:
            for s in self.steps:
                if s["id"] == step:
                    s["state"], s["detail"] = state, detail

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            self.looked = time.monotonic()
            return {
                "chembook3d": "launcher",
                "steps": [dict(s) for s in self.steps],
                "notices": list(self.notices),
                "error": self.error,
                "ready": self.ready,
                "app": self.app,
            }


PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Starting Chembook3D</title>
<style>
:root { --text: #1c2430; --muted: #667085; --accent: #2459c6; --border: #d9dde3; }
body { font-family: system-ui, -apple-system, 'Segoe UI', Roboto, sans-serif; color: var(--text);
  background: #f6f7f9; margin: 0; }
main { max-width: 34em; margin: 14vh auto; padding: 1.5rem 2rem; background: #fff;
  border: 1px solid var(--border); border-radius: 8px; }
h1 { font-size: 1.4rem; margin: 0 0 1rem; }
ol { list-style: none; padding: 0; margin: 0; display: grid; gap: .5rem; }
li { display: grid; grid-template-columns: 1.5em 1fr; }
.mark { color: var(--muted); } .running .mark { color: var(--accent); }
.done .mark { color: #067647; } .failed .mark { color: #b42318; }
.detail { grid-column: 2; color: var(--muted); font-size: .9em; }
.notice { margin-top: 1rem; color: var(--muted); }
.error { margin-top: 1rem; color: #b42318; }
a { color: var(--accent); }
</style></head>
<body><main>
<h1>Starting Chembook3D…</h1>
<ol id="steps"></ol>
<div id="notices"></div>
<p id="error" class="error" hidden></p>
<p id="log" hidden><a href="/log" target="_blank">Show the launcher's log</a></p>
</main>
<script>
const marks = { waiting: '○', running: '◌', done: '✓', skipped: '–', failed: '✗' }
let gone = 0
function text(tag, cls, value) {
  const el = document.createElement(tag)
  if (cls) el.className = cls
  el.textContent = value
  return el
}
async function poll() {
  let status
  try {
    status = await (await fetch('/status', { cache: 'no-store' })).json()
    gone = 0
  } catch {
    if (++gone > 10) {
      document.getElementById('error').hidden = false
      document.getElementById('error').textContent =
        'The launcher has stopped. Its log is launcher.log in the Chembook3D config folder.'
      return
    }
    setTimeout(poll, 500)
    return
  }
  if (status.ready) {
    location.replace(status.app)
    return
  }
  const list = document.getElementById('steps')
  list.replaceChildren(...status.steps.map((s) => {
    const li = text('li', s.state, '')
    li.append(text('span', 'mark', marks[s.state] || '○'), text('span', '', s.name))
    if (s.detail) li.append(text('span', 'detail', s.detail))
    return li
  }))
  document.getElementById('notices').replaceChildren(
    ...status.notices.map((n) => text('p', 'notice', n)))
  if (status.error) {
    document.querySelector('h1').textContent = 'Chembook3D could not start'
    document.getElementById('error').hidden = false
    document.getElementById('error').textContent = status.error
    document.getElementById('log').hidden = false
  }
  setTimeout(poll, status.error ? 2000 : 400)
}
poll()
</script>
</body></html>
"""


class PageServer(ThreadingHTTPServer):
    daemon_threads = True
    # On Windows this option would let a second launcher bind the same port.
    allow_reuse_address = not WINDOWS


def serve_page(progress: Progress, log: Log, port: int) -> PageServer | None:
    """The page on PAGE_PORT, or on any free port when that is taken."""

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 (the name http.server calls)
            path = self.path.split("?", 1)[0]
            if path == "/":
                self._send(PAGE, "text/html")
            elif path == "/status":
                found = progress.snapshot()
                self._send(json.dumps(found), "application/json")
                if found["ready"]:
                    progress.delivered.set()
            elif path == "/log":
                self._send(log.tail(), "text/plain")
            else:
                self.send_error(404)

        def _send(self, body: str, kind: str) -> None:
            data = body.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", f"{kind}; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
            pass  # pythonw has nowhere to print to

    for candidate in (port, 0):
        try:
            server = PageServer((HOST, candidate), Handler)
        except OSError:
            continue
        threading.Thread(target=server.serve_forever, daemon=True).start()
        return server
    return None


def running_in_wsl() -> bool:
    if sys.platform != "linux":
        return False
    try:
        return "microsoft" in Path("/proc/version").read_text().lower()
    except OSError:
        return False


def open_browser(url: str, settings: dict[str, Any]) -> None:
    """A tab in the default browser; under WSL the Windows one, as cli.open_browser does."""
    explorer = settings.get("browser") or (
        shutil.which("explorer.exe") if running_in_wsl() else None
    )
    if explorer and Path(explorer).exists():
        # explorer.exe returns 1 even when it opened the page, so its result is not checked.
        subprocess.run([explorer, url], cwd="/", capture_output=True)
        return
    webbrowser.open(url)


# ---------- the whole launch ----------


def launch(
    repo: Path,
    port: int,
    page_port: int,
    settings: dict[str, Any],
    folder: Path,
    show: bool = True,
) -> int:
    """Everything a double-click does; returns when the server has stopped."""
    app_url = f"http://{HOST}:{port}/"

    def browse(url: str) -> None:
        if show:
            open_browser(url, settings)

    if app_answers(port):  # FR-RUN-01: already running, so only a tab
        browse(app_url)
        return 0
    other = get_json(f"http://{HOST}:{page_port}/status")
    if isinstance(other, dict) and other.get("chembook3d") == "launcher":
        browse(f"http://{HOST}:{page_port}/")  # a launcher is at work already
        return 0

    log = Log(folder / LOG)
    try:
        return _launch(repo, port, page_port, settings, folder, browse, show, log)
    finally:
        log.close()


def _launch(
    repo: Path,
    port: int,
    page_port: int,
    settings: dict[str, Any],
    folder: Path,
    browse: Callable[[str], None],
    show: bool,
    log: Log,
) -> int:
    app_url = f"http://{HOST}:{port}/"
    log.write(f"Chembook3D launcher, checkout {repo}")
    env = tool_env(settings)
    progress = Progress(app_url)
    page = serve_page(progress, log, page_port)
    if page is not None:
        browse(f"http://{HOST}:{page.server_port}/")

    def close_page() -> None:
        if page is not None:
            page.shutdown()
            page.server_close()

    def fail(step: str, message: str) -> int:
        progress.set(step, "failed")
        with progress.lock:
            progress.error = message
        log.write(message)
        if page is not None and show:  # keep the page while someone looks at it
            while time.monotonic() - progress.looked < PAGE_QUIET:
                time.sleep(1)
        close_page()
        return 1

    progress.set("update", "running")
    done, notice = update(repo, env, log)
    progress.set("update", "skipped" if notice else "done", notice or done)
    notices = [notice] if notice else []

    uv = settings.get("uv") or shutil.which("uv", path=env.get("PATH"))
    if not uv or not Path(uv).exists():
        return fail("build", "uv was not found. Run `uv run chembook3d shortcut` again.")

    if needs_build(repo, env):
        progress.set("build", "running", "This can take a minute")
        had = built(repo)
        problem = build(repo, uv, env, log, folder / "previous-build")
        if problem and not had:
            return fail("build", "The interface could not be built. See the log.")
        if problem:
            notices.append(problem)
        progress.set("build", "skipped" if problem else "done", problem or "Rebuilt")
    else:
        progress.set("build", "done", "Up to date")
    with progress.lock:
        progress.notices = list(notices)

    progress.set("start", "running", "Installing Python packages if needed")
    try:
        server = start(repo, uv, port, notices, env, log)
    except OSError as exc:
        return fail("start", f"Chembook3D could not be started: {exc}")
    deadline = time.monotonic() + START_TIMEOUT
    while not app_answers(port):
        code = server.poll()
        if code is not None:
            return fail("start", f"Chembook3D stopped while starting (exit code {code}).")
        if time.monotonic() > deadline:
            server.terminate()
            return fail("start", "Chembook3D did not start in time.")
        time.sleep(0.3)
    progress.set("start", "done")
    with progress.lock:
        progress.ready = True
    log.write(f"Chembook3D answers at {app_url}")
    if page is None:
        browse(app_url)
    else:
        progress.delivered.wait(20 if show else 0)  # until the page has turned into the app
        close_page()
    code = server.wait()
    log.write(f"Chembook3D has stopped (exit code {code})")
    return code


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Start Chembook3D without a terminal (D107).")
    parser.add_argument("--port", type=int, default=PORT)
    parser.add_argument("--page-port", type=int, default=PAGE_PORT)
    parser.add_argument("--no-browser", action="store_true", help="open no tab (tests)")
    args = parser.parse_args(argv)
    folder = config_dir()
    return launch(
        REPO, args.port, args.page_port, load_settings(folder), folder, not args.no_browser
    )


if __name__ == "__main__":
    sys.exit(main())
