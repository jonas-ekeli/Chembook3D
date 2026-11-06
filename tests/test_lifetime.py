"""Starting and stopping the app (D107, FR-RUN-03, FR-RUN-04, T-RUN-04, T-RUN-05)."""

import os
import socket
import subprocess
import sys
import threading
import time
import urllib.request

import pytest
from fastapi.testclient import TestClient

from chembook3d.api import lifetime as module
from chembook3d.api.lifetime import Lifetime
from chembook3d.app import create_app
from chembook3d.sync import SyncStatus

DIRECT = urllib.request.build_opener(urllib.request.ProxyHandler({}))
APP = "http://127.0.0.1:5173"  # any page on this computer


def test_a_launched_server_stops_once_no_tab_is_left():
    life = Lifetime(launched=True, grace=10, silence=300, started=0)
    assert not life.due(now=200)  # waiting for the first tab
    life.seen("a", now=1)
    life.seen("b", now=1)
    life.closed("a", now=2)
    assert not life.due(now=100)  # b is still open
    life.closed("b", now=100)
    assert not life.due(now=105)
    life.seen("c", now=106)  # a reload, or a new tab, within the grace time
    assert not life.due(now=120)
    life.closed("c", now=120)
    assert life.due(now=130)


def test_a_launched_server_stops_when_every_tab_has_gone_silent():
    life = Lifetime(launched=True, grace=10, silence=300, started=0)
    assert life.due(now=300)  # no tab ever came
    life = Lifetime(launched=True, grace=10, silence=300, started=0)
    life.seen("a", now=1)
    assert not life.due(now=299)
    assert life.due(now=301)  # a crashed browser, or a tab put to sleep


def test_a_tab_with_a_request_still_running_is_open():
    """A tab whose long poll waits behind slow requests (a browser opens six connections to
    the app) is still open: the server stopped under such a tab while it was being used."""
    life = Lifetime(launched=True, grace=10, silence=300, started=0)
    life.seen("a", now=1)
    assert life.asking("a", now=10)
    assert life.asking("a", now=20)
    assert not life.due(now=1000)  # its presence has not come through, but it is busy
    life.answered("a", now=1000)
    assert not life.due(now=1200)
    life.answered("a", now=1200)
    assert life.busy == {} and life.tabs == {"a": 1200}
    assert not life.due(now=1499)  # heard from when its last request was answered
    assert life.due(now=1500)
    # Requests from a client that is not a tab here change nothing
    assert not life.asking("claude", now=5)
    assert life.busy == {}


def test_every_request_of_a_tab_is_a_word_from_it(client):
    client.get("/api/presence", params={"tab": "t1"})
    life = client.app.state.lifetime
    life.tabs["t1"] = 0.0
    client.get("/api/health", headers={"X-Chembook-Client": "t1"})
    assert life.tabs["t1"] > 0 and life.busy == {}
    client.get("/api/health", headers={"X-Chembook-Client": "someone"})
    assert "someone" not in life.tabs


def test_a_goodbye_from_an_unknown_tab_changes_nothing():
    life = Lifetime(launched=True, grace=10, silence=300, started=0)
    life.seen("a", now=1)
    life.closed("someone-else", now=2)
    assert life.tabs == {"a": 1} and life.goodbye is None
    assert not life.due(now=50)


def test_a_server_started_from_a_terminal_never_stops_by_itself():
    life = Lifetime(launched=False, grace=10, silence=300, started=0)
    life.seen("a", now=1)
    life.closed("a", now=2)
    assert not life.due(now=10_000)


def test_presence_answers_and_says_when_the_server_stops(client):
    found = client.get("/api/presence", params={"tab": "t1"}).json()
    assert found == {"launched": False, "stopping": False, "notices": [], "sync": None}
    assert "t1" in client.app.state.lifetime.tabs
    assert client.post("/api/presence/t1/closed").status_code == 204
    assert client.app.state.lifetime.tabs == {}
    client.app.state.lifetime.stop("test")
    started = time.monotonic()
    found = client.get("/api/presence", params={"tab": "t2", "wait": 20}).json()
    assert found["stopping"] and time.monotonic() - started < 5


def test_the_launcher_notices_reach_every_tab():
    with TestClient(create_app(launched=True, notices=["Not updated: local changes."])) as client:
        found = client.get("/api/presence", params={"tab": "t1"}).json()
    assert found["launched"] and found["notices"] == ["Not updated: local changes."]


def test_shut_down_only_from_a_page_on_this_computer(open_client):
    calls = []
    open_client.app.state.lifetime.exit = lambda: calls.append("exit")
    info = open_client.get("/api/shutdown").json()
    assert info == {
        "launched": False,
        "investigation": True,
        "linked": False,
        "claude_panel": False,
    }
    assert open_client.post("/api/shutdown").status_code == 403  # no page (Claude's MCP server)
    refused = open_client.post("/api/shutdown", headers={"Origin": "https://example.org"})
    assert refused.status_code == 403
    assert open_client.app.state.investigation is not None

    answer = open_client.post("/api/shutdown", headers={"Origin": APP})
    assert answer.status_code == 200 and answer.json() == {"sync": None}
    assert open_client.app.state.investigation is None  # closed, so its lock is released
    assert open_client.app.state.lifetime.stopping
    time.sleep(1)
    assert calls == ["exit"]
    assert open_client.get("/api/presence", params={"tab": "t"}).json()["stopping"]


def test_a_second_shut_down_waits_for_the_first(open_client, monkeypatch):
    """Shut down closes the investigation before its slow push; a press in the meantime (from the
    start page another tab now shows) must not stop the server under the first, whose answer
    was then cancelled at the graceful-shutdown timeout."""
    found = open_client.app.state.lifetime
    calls = []
    found.exit = lambda: calls.append("exit")
    pushing, release = threading.Event(), threading.Event()

    def slow_push(investigation, timeout):
        investigation.close()
        pushing.set()
        release.wait(5)
        return SyncStatus("ok", "Pushed to GitHub.")

    monkeypatch.setattr(module, "close_and_push", slow_push)
    answers = {}

    def press(name):
        answers[name] = open_client.post("/api/shutdown", headers={"Origin": APP})

    first = threading.Thread(target=press, args=("first",))
    first.start()
    assert pushing.wait(5)
    second = threading.Thread(target=press, args=("second",))
    second.start()
    second.join(0.5)
    assert second.is_alive() and not found.stopping  # waits for the push, stops nothing yet
    release.set()
    first.join(5)
    second.join(5)
    for name in ("first", "second"):
        assert answers[name].status_code == 200
        assert answers[name].json() == {"sync": "Pushed to GitHub."}
    time.sleep(1)
    assert calls == ["exit"]


# ---------- real servers ----------


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def call(port: int, path: str, method: str = "GET", origin: str | None = None) -> int:
    request = urllib.request.Request(f"http://127.0.0.1:{port}{path}", method=method)
    if origin:
        request.add_header("Origin", origin)
    with DIRECT.open(request, timeout=30) as response:
        return response.status


def answers(port: int) -> bool:
    try:
        return call(port, "/api/health") == 200
    except OSError:
        return False


@pytest.fixture
def server(tmp_path):
    """Start `chembook3d` on a free port with the given options; stopped at the end."""
    started: list[subprocess.Popen] = []

    def start(*options: str, grace: float = 0.5, silence: float = 60) -> tuple:
        port = free_port()
        env = dict(os.environ)
        env["CHEMBOOK3D_LAUNCH_GRACE"] = str(grace)
        env["CHEMBOOK3D_LAUNCH_SILENCE"] = str(silence)
        process = subprocess.Popen(
            [sys.executable, "-m", "chembook3d.cli", "--port", str(port), "--no-browser", *options],
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        started.append(process)
        deadline = time.monotonic() + 60
        while not answers(port):
            assert process.poll() is None and time.monotonic() < deadline, "did not start"
            time.sleep(0.2)
        return process, port

    yield start
    for process in started:
        if process.poll() is None:
            process.kill()
            process.wait()


def stopped(process: subprocess.Popen, within: float) -> bool:
    try:
        process.wait(timeout=within)
    except subprocess.TimeoutExpired:
        return False
    return True


def test_a_launched_server_stops_after_the_last_tab_closes(server):
    process, port = server("--launched", grace=0.5)
    call(port, "/api/presence?tab=a")
    call(port, "/api/presence/a/closed", "POST")
    call(port, "/api/presence?tab=b")  # a reload within the grace time
    assert not stopped(process, within=2)
    call(port, "/api/presence/b/closed", "POST")
    assert stopped(process, within=20)
    assert process.returncode == 0


def test_a_launched_server_stops_when_no_tab_is_heard_from(server):
    process, port = server("--launched", silence=1)
    call(port, "/api/presence?tab=a")
    assert stopped(process, within=20)


def test_a_server_started_from_a_terminal_keeps_running_until_shut_down(server):
    process, port = server(grace=0.2, silence=0.5)
    call(port, "/api/presence?tab=a")
    call(port, "/api/presence/a/closed", "POST")
    assert not stopped(process, within=2.5)
    assert call(port, "/api/shutdown", "POST", origin=APP) == 200
    assert stopped(process, within=20)
    assert process.returncode == 0
