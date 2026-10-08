"""The one-click launcher (D107, FR-RUN-01, FR-RUN-02, T-RUN-01 to T-RUN-04). A local bare
repository stands in for GitHub."""

import json
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

import pytest

from chembook3d import launcher, settings

DIRECT = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    ).stdout


@pytest.fixture(autouse=True)
def git_config(tmp_path, monkeypatch):
    path = tmp_path / "gitconfig"
    path.write_text("[user]\n\tname = Test\n\temail = test@example.com\n", encoding="utf-8")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(path))


def commit(repo: Path, name: str, text: str) -> None:
    (repo / name).parent.mkdir(parents=True, exist_ok=True)
    (repo / name).write_text(text, encoding="utf-8")
    git(repo, "add", name)
    git(repo, "commit", "-q", "-m", f"change {name}")


@pytest.fixture
def checkout(tmp_path) -> Path:
    """A clone of Chembook3D on main, with a remote that has moved on."""
    remote = tmp_path / "remote.git"
    remote.mkdir()
    git(remote, "init", "-q", "--bare")
    git(remote, "symbolic-ref", "HEAD", "refs/heads/main")
    first = tmp_path / "first"
    git(tmp_path, "clone", "-q", str(remote), str(first))
    git(first, "checkout", "-q", "-b", "main")
    commit(first, "frontend/src/App.tsx", "one")
    git(first, "push", "-q", "origin", "main")
    repo = tmp_path / "chembook3d"
    git(tmp_path, "clone", "-q", str(remote), str(repo))
    commit(first, "frontend/src/App.tsx", "two")
    git(first, "push", "-q", "origin", "main")
    return repo


def test_the_launcher_finds_the_apps_config_folder(monkeypatch):
    monkeypatch.delenv("CHEMBOOK3D_CONFIG_DIR")
    assert launcher.config_dir() == settings.config_dir()


def test_a_clean_main_is_pulled(checkout, tmp_path):
    log = launcher.Log(tmp_path / "launcher.log")
    done, notice = launcher.update(checkout, launcher.tool_env({}), log)
    assert notice is None and done.startswith("Updated ")
    assert (checkout / "frontend/src/App.tsx").read_text() == "two"
    done, notice = launcher.update(checkout, launcher.tool_env({}), log)
    assert (done, notice) == ("Already up to date", None)
    assert "pull --ff-only" in log.tail()
    log.close()


def test_local_changes_and_other_branches_are_left_alone(checkout, tmp_path):
    env = launcher.tool_env({})
    log = launcher.Log(None)
    (checkout / "frontend/src/App.tsx").write_text("mine")
    assert launcher.update(checkout, env, log)[1] == "Not updated: the checkout has local changes."
    assert (checkout / "frontend/src/App.tsx").read_text() == "mine"  # never stashed or reset

    git(checkout, "checkout", "-q", "--", ".")
    (checkout / "notes.txt").write_text("not tracked")  # untracked files do not stop a pull
    git(checkout, "checkout", "-q", "-b", "feature")
    notice = launcher.update(checkout, env, log)[1]
    assert notice == "Not updated: the checkout is on branch “feature”, not main."
    git(checkout, "checkout", "-q", "--detach")
    assert launcher.update(checkout, env, log)[1] == (
        "Not updated: the checkout is not on a branch, not main."
    )
    git(checkout, "checkout", "-q", "main")
    assert launcher.update(checkout, env, log)[1] is None


def test_a_failed_pull_starts_the_version_there_is(checkout, tmp_path):
    git(checkout, "remote", "set-url", "origin", str(tmp_path / "gone.git"))
    done, notice = launcher.update(checkout, launcher.tool_env({}), launcher.Log(None))
    assert notice is not None and notice.startswith("Not updated: git pull failed (")
    assert (checkout / "frontend/src/App.tsx").read_text() == "one"


def test_outside_a_git_checkout_nothing_is_pulled(tmp_path):
    notice = launcher.update(tmp_path, launcher.tool_env({}), launcher.Log(None))[1]
    assert notice == "Not updated: this copy of Chembook3D is not a git checkout."


def fake_build(repo: Path, text: str = "built") -> None:
    for name in ("dist/index.html", "dist-snapshot/snapshot.html"):
        path = repo / "frontend" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)


def test_the_interface_is_rebuilt_only_when_frontend_changed(checkout):
    assert launcher.needs_build(checkout)  # never built
    fake_build(checkout)
    assert launcher.needs_build(checkout)  # built before builds were stamped
    launcher.write_stamp(checkout)  # what scripts/build_frontend.py does after a build
    assert not launcher.needs_build(checkout)
    git(checkout, "pull", "-q", "--ff-only", "--no-rebase")  # frontend/ changed
    assert launcher.needs_build(checkout)
    launcher.write_stamp(checkout)
    commit(checkout, "README.md", "elsewhere")
    assert not launcher.needs_build(checkout)
    git(checkout, "checkout", "-q", "HEAD~2")  # another version, as after switching branches
    assert launcher.needs_build(checkout)


def test_a_failed_rebuild_keeps_the_previous_interface(checkout, tmp_path):
    fake_build(checkout, "previous")
    log = launcher.Log(tmp_path / "launcher.log")
    # The Python here stands in for uv: `python run ...` fails, as a broken build would.
    problem = launcher.build(
        checkout, sys.executable, launcher.tool_env({}), log, tmp_path / "keep"
    )
    assert problem is not None and problem.startswith("The interface could not be rebuilt")
    assert (checkout / "frontend/dist/index.html").read_text() == "previous"
    assert (checkout / "frontend/dist-snapshot/snapshot.html").read_text() == "previous"
    assert not (tmp_path / "keep").exists()
    log.close()


def test_the_tools_saved_with_the_shortcut_come_first(tmp_path):
    base = {"PATH": "/usr/bin", "VIRTUAL_ENV": "/elsewhere/.venv"}
    env = launcher.tool_env({"path": ["/home/me/.local/bin", "/usr/bin"]}, base)
    assert env["PATH"].split(launcher.os.pathsep) == ["/home/me/.local/bin", "/usr/bin"]
    assert "VIRTUAL_ENV" not in env and env["GIT_TERMINAL_PROMPT"] == "0"
    (tmp_path / launcher.SETTINGS).write_text(json.dumps({"uv": "/bin/uv"}))
    assert launcher.load_settings(tmp_path) == {"uv": "/bin/uv"}
    assert launcher.load_settings(tmp_path / "none") == {}


# ---------- the page and the whole launch ----------


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def fetch(url: str, method: str = "GET") -> str:
    request = urllib.request.Request(url, method=method)
    with DIRECT.open(request, timeout=30) as response:
        return response.read().decode("utf-8")


def test_the_page_shows_each_step_and_the_log(tmp_path):
    log = launcher.Log(tmp_path / "launcher.log")
    log.write("pulling")
    progress = launcher.Progress("http://127.0.0.1:1/")
    progress.set("update", "done", "Already up to date")
    page = launcher.serve_page(progress, log, free_port())
    assert page is not None
    try:
        base = f"http://127.0.0.1:{page.server_port}"
        assert "Starting Chembook3D" in fetch(base + "/")
        status = json.loads(fetch(base + "/status"))
        assert status["chembook3d"] == "launcher" and not status["ready"]
        assert status["steps"][0] == {
            "id": "update",
            "name": "Check for updates",
            "state": "done",
            "detail": "Already up to date",
        }
        assert "pulling" in fetch(base + "/log")
    finally:
        page.shutdown()
        page.server_close()
        log.close()


def test_a_second_double_click_finds_the_launcher_at_work(tmp_path, monkeypatch):
    opened: list[str] = []
    monkeypatch.setattr(launcher, "open_browser", lambda url, _settings: opened.append(url))
    progress = launcher.Progress("http://127.0.0.1:1/")
    page = launcher.serve_page(progress, launcher.Log(None), free_port())
    assert page is not None
    try:
        code = launcher.launch(tmp_path, free_port(), page.server_port, {}, tmp_path / "config")
    finally:
        page.shutdown()
        page.server_close()
    assert code == 0 and opened == [f"http://127.0.0.1:{page.server_port}/"]
    assert not (tmp_path / "config" / launcher.LOG).exists()  # its log is left alone


def test_without_uv_the_page_says_so(tmp_path, monkeypatch):
    monkeypatch.setattr(launcher, "update", lambda *_args: ("Skipped", "Not updated: test."))
    monkeypatch.setenv("PATH", "")
    folder = tmp_path / "config"
    code = launcher.launch(
        tmp_path, free_port(), free_port(), {"uv": str(tmp_path / "no-uv")}, folder, show=False
    )
    assert code == 1
    assert "uv was not found" in (folder / launcher.LOG).read_text(encoding="utf-8")


@pytest.mark.skipif(shutil.which("uv") is None, reason="needs uv, as the shortcut does")
def test_a_launch_starts_the_app_which_stops_when_the_last_tab_closes(tmp_path, monkeypatch):
    # T-RUN-03, T-RUN-04 with the real uv and server; the pull and the build are tested above.
    monkeypatch.setattr(launcher, "update", lambda *_args: ("Skipped", "Not updated: test."))
    monkeypatch.setattr(launcher, "needs_build", lambda *_args: False)
    monkeypatch.setenv("CHEMBOOK3D_LAUNCH_GRACE", "0.5")
    opened: list[str] = []
    monkeypatch.setattr(launcher, "open_browser", lambda url, _settings: opened.append(url))
    folder = tmp_path / "config"
    port = free_port()
    settings_ = {"uv": shutil.which("uv")}
    result: list[int] = []
    run = threading.Thread(
        target=lambda: result.append(
            launcher.launch(launcher.REPO, port, free_port(), settings_, folder, show=False)
        )
    )
    run.start()
    try:
        deadline = time.monotonic() + 300
        while not launcher.app_answers(port):
            assert run.is_alive() and time.monotonic() < deadline, "the app did not start"
            time.sleep(0.3)
        # Already running: a second double-click only opens a tab.
        assert launcher.launch(launcher.REPO, port, free_port(), settings_, folder) == 0
        assert opened == [f"http://127.0.0.1:{port}/"]

        presence = json.loads(fetch(f"http://127.0.0.1:{port}/api/presence?tab=t1"))
        assert presence["launched"] and presence["notices"] == ["Not updated: test."]
        fetch(f"http://127.0.0.1:{port}/api/presence/t1/closed", "POST")
        run.join(timeout=60)
        assert not run.is_alive() and result == [0]
    finally:
        if launcher.app_answers(port):  # a failed test leaves no server behind
            fetch(f"http://127.0.0.1:{port}/api/presence?tab=x")
            fetch(f"http://127.0.0.1:{port}/api/presence/x/closed", "POST")
            run.join(timeout=60)
    log = (folder / launcher.LOG).read_text(encoding="utf-8")
    assert "the last browser tab was closed" in log
    assert "Chembook3D has stopped (exit code 0)" in log
