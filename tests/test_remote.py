"""SSH servers such as Saga (D122): saved servers, logging in with password and code through
the keeper, the terminal, and coming back after a restart. `tests/fake_ssh_server.py` stands
in for Saga; the keeper runs as the separate process it is in the app."""

import asyncio
import hashlib
import json
import os
import re
import sys
import tempfile
import threading
import time
from pathlib import Path

import anyio
import asyncssh
import httpx
import pytest
from fastapi.testclient import TestClient

from chembook3d.app import create_app
from chembook3d.mcp_server import Bridge
from chembook3d.mcp_tools import BY_NAME, PANEL_ALLOWED, READ_ONLY_TOOLS, TOOLS, input_schema
from chembook3d.parsers import orca
from chembook3d.remote import client, job_pilot, keeper, saga_access, saga_prepare, servers
from tests.fake_ssh_server import (
    CODE,
    HOME,
    PASSWORD,
    USER,
    FakeSaga,
    FakeXServer,
    enable_job_pilot,
    enable_saga_minimum,
    free_display,
)
from tests.hpc_pilot_runner import linked_minimum_input

APP = "http://127.0.0.1:8765"
ORIGIN = {"origin": APP}
WS = "ws://127.0.0.1:8765"
# OSC sequences end with BEL or ST (ESC \): Ubuntu 26.04's login files print systemd's
# ST-ended OSC 3008 shell context around each command, and matching on to the next BEL ate the
# output between them.
ANSI = re.compile(
    r"\x1b\[[0-9;?]*[A-Za-z]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b[()][A-Za-z0-9]|\r"
)


@pytest.fixture
def home(tmp_path, monkeypatch) -> Path:
    """The user's home folder, so the real ~/.ssh/known_hosts is not read."""
    path = tmp_path / "home"
    path.mkdir()
    monkeypatch.setenv("HOME", str(path))
    monkeypatch.setenv("USERPROFILE", str(path))
    return path


@pytest.fixture(autouse=True)
def own_temp(tmp_path, monkeypatch):
    """Each test gets its own temp folder: the tests count the app's chembook3d-remote-* and
    chembook3d-read-* copies there, and tests running in parallel would see each other's."""
    path = tmp_path / "temp"
    path.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(path))
    return path


@pytest.fixture
def saga(tmp_path):
    fake = FakeSaga(tmp_path / "saga").start()
    (fake.root / "cluster" / "work" / "ru-caac").mkdir(parents=True)
    yield fake
    fake.stop()


def _app(**kwargs) -> TestClient:
    return TestClient(create_app(), base_url=APP, client=("127.0.0.1", 50000), **kwargs)


@pytest.fixture
def local(home, config_dir):
    with _app() as test_client:
        yield test_client
    client.stop_quietly()  # the keeper outlives the app on purpose; not the test


def _point_at(local, saga, username=USER, **x11) -> str:
    response = local.put(
        "/api/remote/servers",
        json={
            "servers": [
                {
                    "id": "saga",
                    "name": "Saga",
                    "host": "127.0.0.1",
                    "port": saga.port,
                    "username": username,
                    **x11,
                }
            ]
        },
        headers=ORIGIN,
    )
    assert response.status_code == 200, response.text
    return "saga"


def _step(response) -> dict:
    assert response.status_code == 200, response.text
    step = response.json()
    assert step["kind"] != "waiting", step  # the fake server answers at once
    return step


def _log_in(local, saga, code=CODE) -> dict:
    """Log in through every step, trusting the fake server's key; the last step."""
    step = _step(local.post("/api/remote/servers/saga/login", json={}, headers=ORIGIN))
    if step["kind"] == "host_key":
        step = _step(local.post(f"/api/remote/logins/{step['login']}/trust", headers=ORIGIN))
    assert step["kind"] == "prompts", step
    assert step["prompts"] == [{"text": "Password: ", "echo": False}]
    step = _step(
        local.post(
            f"/api/remote/logins/{step['login']}/answer",
            json={"answers": [PASSWORD]},
            headers=ORIGIN,
        )
    )
    assert step["prompts"] == [{"text": "Verification code: ", "echo": False}]
    return _step(
        local.post(
            f"/api/remote/logins/{step['login']}/answer", json={"answers": [code]}, headers=ORIGIN
        )
    )


def _status(local) -> dict:
    return next(s for s in local.get("/api/remote").json()["servers"] if s["id"] == "saga")


def _terminal(local):
    token = local.post("/api/remote/terminals", json={"server": "saga"}, headers=ORIGIN).json()[
        "token"
    ]
    return local.websocket_connect(f"{WS}/api/remote/terminal?token={token}&rows=30&cols=100")


def _receive_json(ws, seconds: float) -> dict:
    """The next message, or a TimeoutError after `seconds`: the test client's own receive waits
    for ever, which hung a CI job until its 30-minute limit with nothing said."""

    async def receive() -> dict:
        with anyio.fail_after(seconds):
            return await ws._send_rx.receive()

    message = ws.portal.call(receive)
    ws._raise_on_close(message)
    return json.loads(message["text"])


def _read_until(ws, check) -> tuple[str, list[dict]]:
    """Output (without escape sequences) and other messages until `check(text, messages)`;
    after 20 s without it, fails showing what came."""
    seen, messages = "", []
    deadline = time.monotonic() + 20
    while not check(seen, messages):
        left = deadline - time.monotonic()
        try:
            message = _receive_json(ws, max(left, 0.01))
        except TimeoutError:
            pytest.fail(f"not seen within 20 s; output {seen!r}, messages {messages!r}")
        if message["type"] == "output":
            seen += ANSI.sub("", message["data"])
        else:
            messages.append(message)
    return seen, messages


def _files_containing(folder: Path, secret: str) -> list[Path]:
    return [
        path
        for path in folder.rglob("*")
        if path.is_file() and secret.encode() in path.read_bytes()
    ]


# ---------- T-SSH-01 saved servers ----------


def test_saga_is_the_first_server_and_nothing_secret_is_saved(local, config_dir):
    status = local.get("/api/remote").json()
    assert status["enabled"] is True
    assert status["servers"] == [
        {
            **{"id": "saga", "name": "Saga", "host": "saga.sigma2.no", "port": 22},
            **{"username": "", "connected": False, "since": None, "cwd": None, "tracked": False},
            **{"shell": None, "lost": None, "inspect": False},
            **{"x11": False, "x11_display": "localhost:0.0", "shell_x11": None, "x11_ready": None},
            **{"claude_writes": False, "claude_written": []},
        }
    ]
    servers = [
        {
            "id": "saga",
            "name": "Saga",
            "host": "login-1.saga.sigma2.no",
            "port": 22,
            "username": "jonas",
        },
        {"name": "Betzy", "host": "betzy.sigma2.no", "username": "jonas"},
    ]
    saved = local.put("/api/remote/servers", json={"servers": servers}, headers=ORIGIN).json()
    assert [(s["name"], s["host"], s["port"]) for s in saved] == [
        ("Saga", "login-1.saga.sigma2.no", 22),
        ("Betzy", "betzy.sigma2.no", 22),
    ]
    assert saved[0]["id"] == "saga" and saved[1]["id"]
    on_disk = json.loads((config_dir / "remote" / "servers.json").read_text(encoding="utf-8"))
    assert set(on_disk["servers"][0]) == {
        *{"id", "name", "host", "port", "username", "x11", "x11_display"}
    }

    for bad, why in (
        ({"name": "Saga", "host": "saga sigma2", "username": "x"}, "host name"),
        ({"name": "Saga", "host": "saga.sigma2.no", "port": 0}, "port"),
        ({"name": "Saga", "host": "saga.sigma2.no", "username": "jo nas"}, "user name"),
        ({"name": "", "host": "saga.sigma2.no"}, "name"),
        ({"name": "Saga", "host": "saga.sigma2.no", "x11_display": "Xming"}, "X11 display"),
    ):
        response = local.put("/api/remote/servers", json={"servers": [bad]}, headers=ORIGIN)
        assert response.status_code == 422 and why in response.json()["detail"], response.text
    twice = [{"name": "Saga", "host": "a.no"}, {"name": "saga", "host": "b.no"}]
    assert (
        local.put("/api/remote/servers", json={"servers": twice}, headers=ORIGIN).status_code == 422
    )


# ---------- T-SSH-05 only the app's own page ----------


def test_only_the_apps_own_page_can_log_in(local, home):
    for headers in ({}, {"origin": "https://evil.example"}):
        assert (
            local.post("/api/remote/servers/saga/login", json={}, headers=headers).status_code
            == 403
        )
        assert (
            local.put("/api/remote/servers", json={"servers": []}, headers=headers).status_code
            == 403
        )
        assert (
            local.post(
                "/api/remote/terminals", json={"server": "saga"}, headers=headers
            ).status_code
            == 403
        )
        assert local.post("/api/remote/servers/saga/logout", headers=headers).status_code == 403
    with _app() as other:
        other.base_url = "http://evil.example"
        assert other.get("/api/remote").json()["enabled"] is False
    with TestClient(create_app(), base_url=APP, client=("10.0.0.2", 50000)) as elsewhere:
        response = elsewhere.post("/api/remote/servers/saga/login", json={}, headers=ORIGIN)
        assert response.status_code == 403
    # A terminal needs a token, spent by any attempt.
    from starlette.websockets import WebSocketDisconnect

    with pytest.raises(WebSocketDisconnect):
        with local.websocket_connect(f"{WS}/api/remote/terminal?token=made-up") as ws:
            ws.receive_json()


def test_a_user_name_is_needed(local, saga):
    _point_at(local, saga, username="")
    response = local.post("/api/remote/servers/saga/login", json={}, headers=ORIGIN)
    assert response.status_code == 422 and "user name" in response.json()["detail"]


# ---------- T-SSH-02, T-SSH-03, T-SSH-04 log in, terminal, restart ----------


def test_log_in_use_the_terminal_and_find_it_again_after_a_restart(local, saga, config_dir, home):
    _point_at(local, saga, username="")
    step = _step(
        local.post("/api/remote/servers/saga/login", json={"username": USER}, headers=ORIGIN)
    )
    assert _status(local)["username"] == USER  # saved with the server
    assert step["kind"] == "host_key"
    assert step["fingerprint"] == saga.key.get_fingerprint("sha256")
    assert step["algorithm"] == "ssh-ed25519"
    step = _step(local.post(f"/api/remote/logins/{step['login']}/trust", headers=ORIGIN))
    trusted = (config_dir / "remote" / "known_hosts").read_text(encoding="utf-8")
    assert trusted == f"[127.0.0.1]:{saga.port} ssh-ed25519 {saga.public_key}\n"
    assert step["kind"] == "prompts" and step["prompts"][0]["echo"] is False
    step = _step(
        local.post(
            f"/api/remote/logins/{step['login']}/answer",
            json={"answers": [PASSWORD]},
            headers=ORIGIN,
        )
    )
    step = _step(
        local.post(
            f"/api/remote/logins/{step['login']}/answer", json={"answers": [CODE]}, headers=ORIGIN
        )
    )
    assert step["kind"] == "connected"
    assert saga.record.answers == [[PASSWORD], [CODE]] and saga.record.logins == 1
    # The keeper sends its shell command; the fake shell ignores it but reports a directory.
    # The fake server records it when its session task runs, which can come just after the
    # keeper has its channel and reports the login (seen under coverage, which is slower).
    deadline = time.monotonic() + 10
    while not saga.record.commands and time.monotonic() < deadline:
        time.sleep(0.05)
    assert saga.record.commands == [keeper.SHELL_COMMAND]

    status = _status(local)
    assert status["connected"] and status["shell"] == "running" and status["since"]
    with _terminal(local) as ws:
        seen, messages = _read_until(ws, lambda text, m: "$ " in text)
        assert "Welcome to the fake Saga." in seen
        ws.send_json({"type": "input", "data": "cd /cluster/work/ru-caac\r"})
        _read_until(ws, lambda text, m: {"type": "cwd", "path": "/cluster/work/ru-caac"} in m)
    assert _status(local)["cwd"] == "/cluster/work/ru-caac"

    # The app restarts: the keeper still holds the shell, its output and its directory.
    local.__exit__(None, None, None)
    with _app() as again:
        status = next(s for s in again.get("/api/remote").json()["servers"] if s["id"] == "saga")
        assert status["connected"] and status["cwd"] == "/cluster/work/ru-caac"
        with _terminal(again) as ws:
            hello = ws.receive_json()
            assert hello == {"type": "hello", "cwd": "/cluster/work/ru-caac", "tracked": True}
            seen, _ = _read_until(ws, lambda text, m: "ru-caac$ " in text)
            assert "Welcome to the fake Saga." in seen  # shown again
            ws.send_json({"type": "input", "data": "pwd\r"})
            _read_until(ws, lambda text, m: "\n/cluster/work/ru-caac\n" in text)
        assert saga.record.logins == 1  # no new login

        # The password and the code were never written anywhere.
        for secret in (PASSWORD, CODE):
            assert _files_containing(config_dir, secret) == []
            assert _files_containing(home, secret) == []

        assert again.post("/api/remote/servers/saga/logout", headers=ORIGIN).status_code == 200
        status = next(s for s in again.get("/api/remote").json()["servers"] if s["id"] == "saga")
        assert not status["connected"] and status["lost"] is None


def test_a_refused_login_is_not_tried_again(local, saga):
    _point_at(local, saga)
    step = _log_in(local, saga, code="000000")
    assert step["kind"] == "failed" and "refused the login" in step["message"]
    assert saga.record.answers == [[PASSWORD], ["000000"]]
    assert saga.record.logins == 0 and len(saga.connections) == 2  # the host key, then the login

    # A wrong password: the server asks for it again, which ends the login as refused.
    step = _step(local.post("/api/remote/servers/saga/login", json={}, headers=ORIGIN))
    step = _step(
        local.post(
            f"/api/remote/logins/{step['login']}/answer",
            json={"answers": ["wrong"]},
            headers=ORIGIN,
        )
    )
    assert step["kind"] == "failed" and "refused the login" in step["message"]
    assert saga.record.answers[-1] == ["wrong"] and len(saga.record.answers) == 3
    # What is tested is that nothing happens: no new attempt to connect within a second. There
    # is no event to wait for instead, so this sleep is the time watched.
    time.sleep(1)
    assert len(saga.connections) == 3 and saga.record.logins == 0
    assert not _status(local)["connected"]


def test_a_changed_host_key_is_refused(local, saga, home):
    _point_at(local, saga)
    other = asyncssh.generate_private_key("ssh-ed25519").export_public_key("openssh").decode()
    (home / ".ssh").mkdir()
    (home / ".ssh" / "known_hosts").write_text(
        f"[127.0.0.1]:{saga.port} {' '.join(other.split()[:2])}\n", encoding="utf-8"
    )
    step = _step(local.post("/api/remote/servers/saga/login", json={}, headers=ORIGIN))
    assert step["kind"] == "failed" and "not the one trusted before" in step["message"]
    assert saga.record.answers == []


def test_a_key_the_user_already_trusts_needs_no_question(local, saga, home):
    _point_at(local, saga)
    (home / ".ssh").mkdir()
    (home / ".ssh" / "known_hosts").write_text(
        f"[127.0.0.1]:{saga.port} ssh-ed25519 {saga.public_key}\n", encoding="utf-8"
    )
    step = _step(local.post("/api/remote/servers/saga/login", json={}, headers=ORIGIN))
    assert step["kind"] == "prompts"
    assert local.delete(f"/api/remote/logins/{step['login']}", headers=ORIGIN).status_code == 200
    assert saga.record.answers == []


def test_asking_for_the_next_step_of_a_login_that_ended(local, saga, home):
    """The page asks for a login's next step (POST /api/remote/logins/{id}/next) after a
    "waiting" answer; a login cancelled meanwhile, or never started, says to log in again."""
    _point_at(local, saga)
    (home / ".ssh").mkdir()
    (home / ".ssh" / "known_hosts").write_text(
        f"[127.0.0.1]:{saga.port} ssh-ed25519 {saga.public_key}\n", encoding="utf-8"
    )
    step = _step(local.post("/api/remote/servers/saga/login", json={}, headers=ORIGIN))
    url = f"/api/remote/logins/{step['login']}/next"
    assert local.post(url).status_code == 403  # only the app's own page
    assert local.delete(f"/api/remote/logins/{step['login']}", headers=ORIGIN).status_code == 200
    for path in (url, "/api/remote/logins/made-up/next"):
        response = local.post(path, headers=ORIGIN)
        assert response.status_code == 409 and "Log in again" in response.json()["detail"]
    assert saga.record.answers == []


def test_a_lost_connection_is_reported_and_not_logged_in_again(local, saga):
    _point_at(local, saga)
    assert _log_in(local, saga)["kind"] == "connected"
    with _terminal(local) as ws:
        _read_until(ws, lambda text, m: "$ " in text)
        saga.drop_connections()
        _, messages = _read_until(ws, lambda text, m: any(x["type"] == "exit" for x in m))
        assert "connection to Saga was lost" in messages[-1]["message"]
    status = _status(local)
    assert not status["connected"] and "Log in again" in status["lost"]
    # What is tested is that nothing happens: no new login within a second. There is no event
    # to wait for instead, so this sleep is the time watched.
    time.sleep(1)
    assert saga.record.logins == 1


def test_a_shell_that_ends_can_be_started_again(local, saga):
    _point_at(local, saga)
    assert _log_in(local, saga)["kind"] == "connected"
    with _terminal(local) as ws:
        _read_until(ws, lambda text, m: "$ " in text)
        ws.send_json({"type": "input", "data": "exit\r"})
        _, messages = _read_until(ws, lambda text, m: any(x["type"] == "exit" for x in m))
        assert "has ended" in messages[-1]["message"]
    assert _status(local)["shell"] == "ended" and _status(local)["connected"]
    assert local.post("/api/remote/servers/saga/shell", json={}, headers=ORIGIN).status_code == 200
    with _terminal(local) as ws:
        _read_until(ws, lambda text, m: "$ " in text)
    assert saga.record.logins == 1 and len(saga.record.commands) == 2


@pytest.mark.skipif(sys.platform == "win32", reason="the real bash runs on Linux")
def test_bash_reports_its_directory_after_each_command(local, tmp_path):
    fake = FakeSaga(tmp_path / "real", real_shell=True).start()
    try:
        _point_at(local, fake)
        assert _log_in(local, fake)["kind"] == "connected"
        work = tmp_path / "work dir"
        work.mkdir()
        with _terminal(local) as ws:
            # The shell is tracked once it has said where it is: in a "cwd" message, or in the
            # "hello" when its first prompt came before the terminal attached (no cwd follows).
            _read_until(
                ws,
                lambda text, m: any(
                    x["type"] == "cwd" or (x["type"] == "hello" and x.get("cwd")) for x in m
                ),
            )
            ws.send_json({"type": "input", "data": f"cd '{work}'\r"})
            _read_until(ws, lambda text, m: {"type": "cwd", "path": str(work)} in m)
            ws.send_json({"type": "input", "data": "echo $0 $-\r"})
            seen, _ = _read_until(
                ws, lambda text, m: "bash " in text and "i" in text.split("bash ")[-1]
            )
            # D123c: `inspect FILE` asks the app to open the file, by its full path.
            (work / "TS 1.out").write_text("output\n")
            ws.send_json({"type": "input", "data": "inspect 'TS 1.out' missing.log\r"})
            text, _ = _read_until(
                ws,
                lambda text, m: (
                    {"type": "inspect", "paths": [f"{work}/TS 1.out"]} in m
                    and "inspect: missing.log: no such file" in text
                ),
            )
            # D129: a wildcard's files go to the app as one list, in bash's order.
            (work / "b.log").write_text("output\n")
            (work / "a.log").write_text("output\n")
            (work / "c.log").mkdir()
            ws.send_json({"type": "input", "data": "inspect *.log\r"})
            text, _ = _read_until(
                ws,
                lambda text, m: (
                    {"type": "inspect", "paths": [f"{work}/a.log", f"{work}/b.log"]} in m
                    and "inspect: c.log: is a folder" in text
                ),
            )
            ws.send_json({"type": "input", "data": "inspect\r"})
            _read_until(ws, lambda text, m: "usage: inspect FILE" in text)
        status = _status(local)
        assert status["cwd"] == str(work) and status["tracked"] is True and status["inspect"]
    finally:
        fake.stop()


# ---------- T-SSH-09 X11 forwarding ----------


def _xclient(local) -> str:
    """Run an X program in the terminal; what it printed."""
    with _terminal(local) as ws:
        _read_until(ws, lambda text, m: "$ " in text)
        ws.send_json({"type": "input", "data": "xclient\r"})
        text, _ = _read_until(ws, lambda text, m: re.search(r"xclient: .*\n", text))
    return re.search(r"xclient: (.*)\n", text).group(1)


def test_x11_windows_reach_the_x_server_on_this_computer(local, saga):
    xming = FakeXServer()
    try:
        _point_at(local, saga, x11=True, x11_display=xming.display)
        assert _log_in(local, saga)["kind"] == "connected"
        status = _status(local)
        assert status["shell_x11"] == xming.display and status["x11_ready"] is True
        assert saga.record.displays[0]  # the server was asked to forward
        assert _xclient(local) == "fake X server"
        # The X server sees the keeper's own cookie, not the one the server handed out.
        assert len(xming.cookies) == 1 and len(xming.cookies[0]) == 16

        # Turned off: the running shell keeps it until a new shell takes its place.
        _point_at(local, saga, x11=False, x11_display=xming.display)
        assert _status(local)["shell_x11"] == xming.display
        response = local.post(
            "/api/remote/servers/saga/shell", json={"restart": True}, headers=ORIGIN
        )
        assert response.status_code == 200, response.text
        status = _status(local)
        assert status["shell"] == "running" and status["shell_x11"] is None
        assert status["x11_ready"] is None and saga.record.displays[-1] is None
        assert _xclient(local) == "cannot open display"

        # And on again, in a new shell, without a new login.
        _point_at(local, saga, x11=True, x11_display=xming.display)
        local.post("/api/remote/servers/saga/shell", json={"restart": True}, headers=ORIGIN)
        assert _xclient(local) == "fake X server"
        assert saga.record.logins == 1 and len(saga.record.commands) == 3
    finally:
        xming.stop()


def test_no_x_server_is_reported(local, saga):
    with free_display() as display:
        _point_at(local, saga, x11=True, x11_display=display)
        assert _log_in(local, saga)["kind"] == "connected"
        status = _status(local)
        assert status["shell"] == "running" and status["x11_ready"] is False


def test_a_server_refusing_x11_still_gives_a_shell(local, tmp_path):
    fake = FakeSaga(tmp_path / "no-x11", x11=False).start()
    try:
        _point_at(local, fake, x11=True)
        assert _log_in(local, fake)["kind"] == "connected"
        assert _status(local)["shell"] == "running"
        assert fake.record.displays == [None]
        assert _xclient(local) == "cannot open display"
    finally:
        fake.stop()


# ---------- the keeper on its own ----------


def test_the_directory_report_split_over_pieces():
    shell = keeper.Shell(process=None, tracked=True)  # type: ignore[arg-type]
    for piece in (b"out\x1b]13", b"37;CurrentDir=/clu", b"ster/work\x07prompt$ "):
        shell.feed(piece)
    assert shell.cwd == "/cluster/work"
    shell.feed("\x1b]1337;CurrentDir=/a\x07x\x1b]1337;CurrentDir=/b ø\x07".encode())
    assert shell.cwd == "/b ø"
    assert shell.replay().startswith("out\x1b]1337")
    # D123c, D129: the files one `inspect` names go on together when the prompt is back, before
    # the directory.
    viewer: asyncio.Queue = asyncio.Queue()
    shell.viewers.add(viewer)
    for piece in (
        b"\x1b]1337;Inspect=/w/a.out\x07\x1b]1337;Insp",
        b"ect=/w/b.out\x07\x1b]1337;CurrentDir=/w\x07$ ",
    ):
        shell.feed(piece)
    messages = [viewer.get_nowait() for _ in range(viewer.qsize())]
    assert [m for m in messages if m["type"] != "output"] == [
        {"type": "inspect", "paths": ["/w/a.out", "/w/b.out"]},
        {"type": "cwd", "path": "/w"},
    ]


def test_files_to_inspect_without_a_prompt_after_them_go_on_after_a_pause(monkeypatch):
    # D129: a command that prints no prompt after it (a script) still opens its files.
    monkeypatch.setattr(keeper, "INSPECT_WAIT", 0.05)

    async def run() -> list[dict]:
        shell = keeper.Shell(process=None, tracked=True)  # type: ignore[arg-type]
        viewer: asyncio.Queue = asyncio.Queue()
        shell.viewers.add(viewer)
        shell.feed(b"\x1b]1337;Inspect=/w/a.out\x07")
        await asyncio.sleep(0.01)
        shell.feed(b"\x1b]1337;Inspect=/w/b.out\x07")
        assert viewer.qsize() == 2  # only the output so far
        await asyncio.sleep(0.2)
        shell.feed(b"\x1b]1337;Inspect=/w/c.out\x07")
        shell.end("closed")
        return [m for m in (viewer.get_nowait() for _ in range(viewer.qsize()))]

    messages = [m for m in asyncio.run(run()) if m["type"] != "output"]
    assert messages == [
        {"type": "inspect", "paths": ["/w/a.out", "/w/b.out"]},
        {"type": "inspect", "paths": ["/w/c.out"]},
        {"type": "exit", "message": "closed"},
    ]


def test_the_keeper_logs_out_after_the_idle_time_and_ends_when_empty(
    saga, config_dir, home, monkeypatch
):
    monkeypatch.setattr(keeper, "WATCH_EVERY", 0.1)
    folder = config_dir / "remote"
    loop = asyncio.new_event_loop()
    thread = threading.Thread(
        target=lambda: loop.run_until_complete(keeper.serve(folder, grace=1.0, idle=1.0)),
        daemon=True,
    )
    thread.start()
    deadline = time.monotonic() + 10
    while not (folder / keeper.KEEPER_FILE).exists():
        assert time.monotonic() < deadline
        time.sleep(0.05)
    if sys.platform != "win32":
        assert (folder / keeper.KEEPER_FILE).stat().st_mode & 0o777 == 0o600

    async def log_in() -> dict:
        server = {
            "id": "saga",
            "name": "Saga",
            "host": "127.0.0.1",
            "port": saga.port,
            "username": USER,
        }
        step = await client.call("login", server=server)
        step = await client.call("trust", login=step["login"])
        step = await client.call("answer", login=step["login"], answers=[PASSWORD])
        return await client.call("answer", login=step["login"], answers=[CODE])

    assert asyncio.run(log_in())["kind"] == "connected"
    logged_in = time.monotonic()
    deadline = logged_in + 10
    while asyncio.run(client.status())["sessions"]:  # logged out: nothing typed for 1 s
        assert time.monotonic() < deadline
        time.sleep(0.05)
    assert time.monotonic() - logged_in >= 1.0  # not before the idle time
    thread.join(10)  # and then ended, with nothing left to hold
    assert not thread.is_alive()
    assert not (folder / keeper.KEEPER_FILE).exists()
    loop.close()


def test_a_wrong_token_gets_nothing(saga, config_dir, home):
    folder = config_dir / "remote"
    loop = asyncio.new_event_loop()
    thread = threading.Thread(
        target=lambda: loop.run_until_complete(keeper.serve(folder)), daemon=True
    )
    thread.start()
    deadline = time.monotonic() + 10
    while not (folder / keeper.KEEPER_FILE).exists():
        assert time.monotonic() < deadline
        time.sleep(0.05)
    info = json.loads((folder / keeper.KEEPER_FILE).read_text(encoding="utf-8"))

    async def ask(token: str) -> bytes:
        reader, writer = await asyncio.open_connection("127.0.0.1", info["port"])
        writer.write(json.dumps({"token": token, "op": "status"}).encode() + b"\n")
        await writer.drain()
        line = await reader.readline()
        writer.close()
        await writer.wait_closed()
        return line

    assert asyncio.run(ask("wrong")) == b""
    assert json.loads(asyncio.run(ask(info["token"])))["ok"] is True
    asyncio.run(client.call("quit"))
    thread.join(10)
    loop.close()


# ---------- T-SSH-07 import from the terminal's directory ----------

FIXTURES = Path(__file__).parent / "fixtures" / "gaussian"
TS = "aminationTS-full-unfrz-c1.log"
TS_SP = "aminationTS-full-unfrz-c1_sp_tzpop.log"
WORK = "/cluster/work/ru-caac"


@pytest.fixture
def at_work(local, saga, tmp_path):
    """Logged in to the fake Saga with an investigation open and the shell in WORK, which
    holds two outputs, an input and a subdirectory."""
    work = saga.root / WORK.lstrip("/")
    for name in (TS, TS_SP):
        (work / name).write_bytes((FIXTURES / name).read_bytes())
    (work / "job.gjf").write_text("#P B3LYP/6-31G(d) Opt\n\njob\n\n0 1\n", encoding="utf-8")
    (work / "old").mkdir()
    response = local.post(
        "/api/investigations", json={"folder": str(tmp_path / "inv"), "name": "Ru"}
    )
    assert response.status_code == 200, response.text
    _point_at(local, saga)
    assert _log_in(local, saga)["kind"] == "connected"
    with _terminal(local) as ws:
        _read_until(ws, lambda text, m: "$ " in text)
        ws.send_json({"type": "input", "data": f"cd {WORK}\r"})
        _read_until(ws, lambda text, m: {"type": "cwd", "path": WORK} in m)
    return local


def _copies() -> set[str]:
    return {p.name for p in Path(tempfile.gettempdir()).glob("chembook3d-remote-*")}


def test_the_directory_is_listed_with_its_outputs(at_work):
    listing = at_work.get("/api/remote/servers/saga/files").json()
    assert listing["path"] == WORK
    # Directories first, then by name.
    assert [(e["name"], e["kind"], e["output"]) for e in listing["entries"]] == [
        ("old", "directory", False),
        (TS, "file", True),
        (TS_SP, "file", True),
        ("job.gjf", "file", False),
    ]
    ts = next(e for e in listing["entries"] if e["name"] == TS)
    assert ts["size"] == (FIXTURES / TS).stat().st_size and ts["modified"]
    other = at_work.get("/api/remote/servers/saga/files", params={"path": "/cluster/work"}).json()
    assert [e["name"] for e in other["entries"]] == ["ru-caac"]


def test_one_output_is_imported_with_the_server_as_its_origin(at_work, config_dir):
    before = _copies()
    plan = at_work.post(
        "/api/remote/servers/saga/import", json={"paths": [f"{WORK}/{TS}"]}, headers=ORIGIN
    ).json()
    assert plan["origin"] == {"device": "Saga", "path": f"saga:{WORK}/{TS}", "name": TS}
    assert _copies() == before  # the copy is removed once staged
    response = at_work.post(f"/api/imports/{plan['token']}/commit", json={})
    assert response.status_code == 200, response.text
    node_id = response.json()["node_id"]
    source = at_work.get(f"/api/nodes/{node_id}/calculations").json()[0]["source_file"]
    assert (source["origin_device"], source["origin_path"]) == ("Saga", f"saga:{WORK}/{TS}")
    download = at_work.get(f"/api/source-files/{source['id']}/download")
    assert download.content == (FIXTURES / TS).read_bytes()
    # The next import from this computer still offers this computer's name.
    settings = json.loads((config_dir / "settings.json").read_text(encoding="utf-8"))
    assert settings.get("last_device", "") != "Saga"


def test_several_outputs_go_through_the_batch_import(at_work):
    paths = [f"{WORK}/{name}" for name in (TS, TS_SP, "job.gjf")]
    response = at_work.post(
        "/api/remote/servers/saga/batch-import", json={"paths": paths}, headers=ORIGIN
    )
    assert response.status_code == 200, response.text
    plan = response.json()
    assert plan["folder"] == f"saga:{WORK}" and plan["origin_device"] == "Saga"
    assert sorted(r["path"] for r in plan["rows"]) == sorted([TS, TS_SP])
    assert plan["other_count"] == 1  # the input
    result = at_work.post(f"/api/batch-imports/{plan['token']}/commit", json={})
    assert result.status_code == 200, result.text
    assert result.json()["imported"] == 2
    nodes = at_work.get("/api/canvas").json()["nodes"]
    origins = {
        c["source_file"]["origin_path"]
        for n in nodes
        for c in at_work.get(f"/api/nodes/{n['id']}/calculations").json()
    }
    assert origins == {f"saga:{WORK}/{TS}", f"saga:{WORK}/{TS_SP}"}


def test_import_refusals(at_work, local):
    one = {"paths": [f"{WORK}/missing.log"]}
    response = at_work.post("/api/remote/servers/saga/import", json=one, headers=ORIGIN)
    assert response.status_code == 422 and "Could not copy from Saga" in response.json()["detail"]
    two = {"paths": [f"{WORK}/{TS}", f"{WORK}/{TS_SP}"]}
    response = at_work.post("/api/remote/servers/saga/import", json=two, headers=ORIGIN)
    assert response.status_code == 422 and "batch import" in response.json()["detail"]
    assert at_work.post("/api/remote/servers/saga/import", json=one).status_code == 403
    at_work.post("/api/remote/servers/saga/logout", headers=ORIGIN)
    assert at_work.get("/api/remote/servers/saga/files").status_code == 409


# ---------- T-SSH-21 an IRC from the server (D134) ----------

ORCA_IRC = Path(__file__).parent / "fixtures" / "orca" / "irc"
IRC_NAMES = ("output.out", "orca_IRC_F_trj.xyz", "orca_IRC_B_trj.xyz")


def test_an_orca_irc_comes_down_with_its_trajectories(at_work, saga):
    work = saga.root / WORK.lstrip("/") / "irc"
    work.mkdir()
    for name in IRC_NAMES:
        (work / name).write_bytes((ORCA_IRC / name).read_bytes())
    text = (ORCA_IRC / "output.out").read_text(encoding="utf-8")
    start = orca.parse(text).steps[0].geometries[0]
    xyz = f"{len(start)}\n\n" + "\n".join(f"{a.element} {a.x} {a.y} {a.z}" for a in start)
    body = {"label": "TS", "xyz": xyz, "role": "transition_state", "charge": 1, "multiplicity": 1}
    ts = at_work.post("/api/nodes", json=body).json()["id"]

    # Only the output is chosen; its trajectories come down with it.
    one = {"paths": [f"{WORK}/irc/output.out"], "node_id": ts}
    plan = at_work.post("/api/remote/servers/saga/import", json=one, headers=ORIGIN).json()
    assert [t["node_id"] for t in plan["irc_targets"]] == [ts]
    options = {"target_node_id": ts, "irc_part": "path"}
    response = at_work.post(f"/api/imports/{plan['token']}/preview", json=options)
    assert response.status_code == 200 and response.json()["blockers"] == []
    response = at_work.post(f"/api/imports/{plan['token']}/commit", json=options)
    assert response.status_code == 200, response.text
    found = at_work.get(f"/api/nodes/{ts}/irc").json()
    assert {s["direction"]: s["points"] for s in found["sides"]} == {"forward": 49, "reverse": 73}
    assert found["parts"][0]["file"] == "output.out"


# ---------- T-SSH-10 inspect a file on the server (D123c) ----------


def test_a_file_on_the_server_is_inspected_from_the_terminal(at_work, saga):
    with _terminal(at_work) as ws:
        _read_until(ws, lambda text, m: "$ " in text)
        ws.send_json({"type": "input", "data": f"inspect {TS}\r"})
        _, messages = _read_until(ws, lambda text, m: any(x["type"] == "inspect" for x in m))
    path = next(m["paths"] for m in messages if m["type"] == "inspect")[0]
    assert path == f"{WORK}/{TS}"

    before = _copies()
    remote = saga.root / path.lstrip("/")
    full = remote.read_text(encoding="utf-8")
    lines = full.splitlines(keepends=True)
    remote.write_text("".join(lines[: len(lines) // 3]), encoding="utf-8")  # still running
    body = {"server": "saga", "path": path}
    response = at_work.post("/api/inspections", json=body, headers=ORIGIN)
    assert response.status_code == 200, response.text
    shown = response.json()
    assert (shown["name"], shown["path"]) == (TS, f"saga:{path}")
    assert (shown["server"], shown["server_name"]) == ("saga", "Saga")
    assert len(shown["steps"]) == 1 and shown["steps"][0]["termination"] == "abnormal"
    assert len(_copies() - before) == 1  # kept while the window is open

    remote.write_text(full, encoding="utf-8")
    again = at_work.post(f"/api/inspections/{shown['id']}/refresh", headers=ORIGIN).json()
    assert len(again["steps"]) == 2 and again["steps"][1]["imaginary"] == 1
    assert len(_copies() - before) == 1  # the new copy in place of the old

    plan = at_work.post(f"/api/inspections/{shown['id']}/import", json={}, headers=ORIGIN).json()
    assert plan["origin"] == {"device": "Saga", "path": f"saga:{path}", "name": TS}
    assert at_work.get("/api/canvas").json()["nodes"] == []
    at_work.delete(f"/api/inspections/{shown['id']}", headers=ORIGIN)
    assert _copies() == before
    # Nothing was changed on the server.
    assert remote.read_text(encoding="utf-8") == full


def test_inspecting_a_missing_server_file(at_work):
    body = {"server": "saga", "path": f"{WORK}/missing.log"}
    response = at_work.post("/api/inspections", json=body, headers=ORIGIN)
    assert response.status_code == 422 and "Could not copy from Saga" in response.json()["detail"]


# ---------- T-SSH-08 send a node's structure to the terminal's directory ----------

WATER = "3\nwater\nO 0.000 0.000 0.117\nH 0.000 0.757 -0.467\nH 0.000 -0.757 -0.467\n"


def _node(local, label: str, coordinates: str | None = WATER) -> str:
    node_id = local.post("/api/nodes", json={"label": label}).json()["id"]
    if coordinates:
        response = local.put(f"/api/nodes/{node_id}/geometry", json={"xyz": coordinates})
        assert response.status_code == 200, response.text
    return node_id


def test_a_structure_is_sent_and_a_file_there_is_replaced_only_when_asked(at_work, saga):
    node_id = _node(at_work, "Ru–CAAC TS1: syn")  # an en dash, a colon Windows refuses
    send = {"node_id": node_id}
    answer = at_work.post("/api/remote/servers/saga/send", json=send, headers=ORIGIN).json()
    path = f"{WORK}/Ru–CAAC TS1_ syn.xyz"
    assert answer == {"path": path, "sent": True, "exists": False}
    written = saga.root / path.lstrip("/")
    expected = at_work.get(f"/api/nodes/{node_id}/xyz").text
    assert written.read_text(encoding="utf-8") == expected

    # The same name again: nothing is written until the person says yes.
    written.write_text("their own file\n", encoding="utf-8")
    answer = at_work.post("/api/remote/servers/saga/send", json=send, headers=ORIGIN).json()
    assert answer == {"path": path, "sent": False, "exists": True}
    assert written.read_text(encoding="utf-8") == "their own file\n"
    replace = {**send, "replace": True}
    answer = at_work.post("/api/remote/servers/saga/send", json=replace, headers=ORIGIN).json()
    assert answer == {"path": path, "sent": True, "exists": True}
    assert written.read_text(encoding="utf-8") == expected
    # Everything else in the directory is as it was.
    assert sorted(p.name for p in written.parent.iterdir()) == sorted(
        [TS, TS_SP, "job.gjf", "old", written.name]
    )


def test_send_refusals(at_work, saga):
    bare = _node(at_work, "no coordinates", None)
    response = at_work.post("/api/remote/servers/saga/send", json={"node_id": bare}, headers=ORIGIN)
    assert response.status_code == 422 and "no coordinates" in response.json()["detail"]
    (saga.root / WORK.lstrip("/") / "old.xyz").mkdir()
    folder = _node(at_work, "old")
    response = at_work.post(
        "/api/remote/servers/saga/send", json={"node_id": folder, "replace": True}, headers=ORIGIN
    )
    assert response.status_code == 409 and "is a directory" in response.json()["detail"]
    send = {"node_id": _node(at_work, "water")}
    assert at_work.post("/api/remote/servers/saga/send", json=send).status_code == 403
    at_work.post("/api/remote/servers/saga/logout", headers=ORIGIN)
    response = at_work.post("/api/remote/servers/saga/send", json=send, headers=ORIGIN)
    assert response.status_code == 409 and "Not logged in" in response.json()["detail"]
    assert not (saga.root / WORK.lstrip("/") / "water.xyz").exists()


# ---------- T-SSH-11..14 Claude reads files in the terminal's directory (D124) ----------


def _reads() -> set[str]:
    return {p.name for p in Path(tempfile.gettempdir()).glob("chembook3d-read-*")}


def _text(response) -> str:
    assert response.status_code == 200, response.text
    return response.text


def test_claude_sees_the_servers_and_the_files_in_the_terminals_directory(at_work, saga):
    # No Origin, as Claude's MCP server sends.
    listed = at_work.get("/api/server-files").json()["servers"]
    assert listed == [
        {
            **{"id": "saga", "name": "Saga", "host": "127.0.0.1", "username": USER},
            **{"logged_in": True, "cwd": WORK, "readable": True, "reason": None},
            "writes": False,
        }
    ]
    files = at_work.get("/api/server-files/saga/files").json()
    assert files["path"] == WORK
    assert [(e["name"], e["kind"]) for e in files["entries"]] == [
        ("old", "directory"),
        (TS, "file"),
        (TS_SP, "file"),
        ("job.gjf", "file"),
    ]
    below = at_work.get("/api/server-files/saga/files", params={"path": "old"}).json()
    assert (below["path"], below["entries"]) == (f"{WORK}/old", [])
    for outside in ("..", "/cluster/work", "old/../..", "/"):
        response = at_work.get("/api/server-files/saga/files", params={"path": outside})
        assert response.status_code == 409, outside
        assert "outside the terminal's current directory" in response.json()["detail"]
    foreign = {"origin": "https://evil.example"}
    assert at_work.get("/api/server-files/saga/files", headers=foreign).status_code == 403
    text = at_work.get("/api/server-files/saga/read", params={"path": "job.gjf"})
    assert text.is_success and "#P B3LYP/6-31G(d) Opt" in text.text
    assert f"{WORK}/old" in saga.record.sftp_paths
    assert f"{WORK}/job.gjf" in saga.record.sftp_paths
    assert saga.record.saga_file_requests == []


def test_claude_reads_pages_the_end_and_searches_without_changing_anything(at_work, saga):
    work = saga.root / WORK.lstrip("/")
    lines = [f"line {n:06d} of a long output" for n in range(1, 100_001)]
    for n in (500, 501, 70_000, 99_990):
        lines[n - 1] = f" Error termination via Lnk1e at line {n}"
    big = work / "big.log"
    big.write_bytes(("\n".join(lines) + "\n").encode())  # \n on Windows too, as on Saga
    assert big.stat().st_size > keeper.MAX_READ
    before = _reads()

    first = _text(at_work.get("/api/server-files/saga/read", params={"path": "big.log"}))
    shown = first.splitlines()
    assert re.match(rf"saga:{WORK}/big\.log \(2\.\d MB, modified 20\d\d-", shown[0])
    assert shown[1] == "Lines 1-400; the file goes on: start=401."
    assert shown[2] == "      1\tline 000001 of a long output" and len(shown) == 402
    assert _reads() == before  # read from the start of the file, not copied

    deep = at_work.get(
        "/api/server-files/saga/read", params={"path": "big.log", "start": 99_995, "lines": 50}
    )
    shown = _text(deep).splitlines()
    assert shown[1] == "Lines 99995-100000 of 100000."
    assert shown[-1] == " 100000\tline 100000 of a long output"
    assert len(_reads() - before) == 1  # one copy, kept

    end = _text(at_work.get("/api/server-files/saga/read", params={"path": "big.log", "tail": 3}))
    assert end.splitlines()[1:] == [
        "Last 3 lines (line numbers unknown without reading the whole file):",
        "line 099998 of a long output",
        "line 099999 of a long output",
        "line 100000 of a long output",
    ]

    params = {"path": "big.log", "search": "error TERMINATION", "context": 1}
    found = _text(at_work.get("/api/server-files/saga/read", params=params)).splitlines()
    assert found[1] == 'Search for "error TERMINATION": 4 matching lines in lines 1-100000.'
    assert found[2:7] == [
        "    499\tline 000499 of a long output",
        "    500\t Error termination via Lnk1e at line 500",
        "    501\t Error termination via Lnk1e at line 501",
        "    502\tline 000502 of a long output",
        "--",
    ]
    assert len(_reads() - before) == 1  # the same copy again
    params = {"path": "big.log", "search": r"line (7|9)\d+$", "regex": True, "context": 0}
    params |= {"last": True, "ignore_case": False}
    found = _text(at_work.get("/api/server-files/saga/read", params=params)).splitlines()
    assert found[1].endswith("2 matching lines in lines 1-100000.")
    assert found[2:] == [
        "  70000\t Error termination via Lnk1e at line 70000",
        "--",
        "  99990\t Error termination via Lnk1e at line 99990",
    ]

    small = at_work.get("/api/server-files/saga/read", params={"path": "job.gjf", "tail": 2})
    assert _text(small).splitlines()[1:] == ["Last 2 lines of 5:", "      4\t", "      5\t0 1"]

    # A grown file is copied again; the copies go at log out.
    big.write_bytes(("\n".join(lines[:10]) + "\n").encode())
    again = at_work.get("/api/server-files/saga/read", params={"path": "big.log", "search": "x"})
    assert "no match in lines 1-10" in _text(again)
    assert len(_reads() - before) == 1  # small enough to read without a copy; the old one stays
    at_work.post("/api/remote/servers/saga/logout", headers=ORIGIN)
    assert _reads() == before
    assert sorted(p.name for p in work.iterdir()) == sorted(
        [TS, TS_SP, "job.gjf", "old", "big.log"]
    )


@pytest.mark.skipif(sys.platform == "win32", reason="symlinks need extra rights on Windows")
def test_a_link_out_of_the_directory_is_not_followed_and_cd_moves_what_is_readable(at_work, saga):
    home = saga.root / "cluster" / "home" / USER
    home.mkdir(parents=True, exist_ok=True)
    (home / "secret.txt").write_text("not for Claude\n", encoding="utf-8")
    work = saga.root / WORK.lstrip("/")
    (work / "secret.txt").symlink_to(home / "secret.txt")
    (work / "ts.log").symlink_to(work / TS)
    (work / "away").symlink_to(home, target_is_directory=True)

    listed = at_work.get("/api/server-files/saga/files").json()["entries"]
    kinds = {e["name"]: e["kind"] for e in listed}
    assert (kinds["secret.txt"], kinds["away"], kinds["ts.log"]) == ("outside", "outside", "file")
    for path in ("secret.txt", "away/secret.txt", f"{WORK}/away/secret.txt"):
        response = at_work.get("/api/server-files/saga/read", params={"path": path})
        assert response.status_code == 409, path
        assert f"/cluster/home/{USER}" in response.json()["detail"]
    assert "not for Claude" not in at_work.get("/api/server-files/saga/files?path=away").text
    inside = at_work.get("/api/server-files/saga/read", params={"path": "ts.log", "lines": 1})
    assert _text(inside).startswith(f"saga:{WORK}/{TS} (")  # by the path it resolves to

    with _terminal(at_work) as ws:
        _read_until(ws, lambda text, m: "$ " in text)
        ws.send_json({"type": "input", "data": f"cd /cluster/home/{USER}\r"})
        _read_until(ws, lambda text, m: {"type": "cwd", "path": f"/cluster/home/{USER}"} in m)
    secret = at_work.get("/api/server-files/saga/read", params={"path": "secret.txt"})
    assert "not for Claude" in _text(secret)
    response = at_work.get("/api/server-files/saga/read", params={"path": f"{WORK}/{TS}"})
    assert response.status_code == 409


def test_an_output_is_summarised_and_its_structures_read(at_work, saga):
    summary = at_work.get("/api/server-files/saga/summary", params={"path": TS})
    assert summary.status_code == 200, summary.text
    found = summary.json()
    assert (found["path"], found["program"]) == (f"saga:{WORK}/{TS}", "Gaussian")
    steps = found["steps"]
    assert [s["job_type"] for s in steps] == ["ts_optimization", "frequency"]
    assert steps[0]["optimization_converged"] is True
    assert steps[1]["imaginary"] == 1 and steps[1]["imaginary_frequencies"][0] < 0
    rows = steps[0]["structure_list"]
    assert len(rows) == steps[0]["structures"] > 1 and rows[-1]["converged"] is True
    assert rows[-1]["criteria"]["Maximum force"][1:] == [1.5e-05, True] and rows[-1]["energy"] < 0

    last = _text(at_work.get("/api/server-files/saga/structure", params={"path": TS}))
    head = last.splitlines()
    assert int(head[0]) == steps[-1]["atoms"]
    assert head[1].startswith(f"saga:{WORK}/{TS} step 2 structure 1 of 1 E=-")
    first = at_work.get("/api/server-files/saga/structure", params={"path": TS, "step": 1})
    assert _text(first).splitlines()[1].endswith(" converged")
    params = {"path": TS, "step": 1, "frame": 1}
    one = _text(at_work.get("/api/server-files/saga/structure", params=params)).splitlines()
    assert one[1].startswith(f"saga:{WORK}/{TS} step 1 structure 1 of {steps[0]['structures']}")
    for bad in ({"step": 7}, {"step": 1, "frame": 999}):
        response = at_work.get("/api/server-files/saga/structure", params={"path": TS, **bad})
        assert response.status_code == 422, bad
    copied = _reads()
    at_work.post("/api/remote/servers/saga/logout", headers=ORIGIN)
    assert not copied & _reads()


def test_what_claude_cannot_read(at_work, saga, monkeypatch):
    work = saga.root / WORK.lstrip("/")
    (work / "data.chk").write_bytes(b"\x00\x01binary" * 100)
    read = "/api/server-files/saga/read"
    for params, status, why in (
        ({"path": "data.chk"}, 422, "not a text file"),
        ({"path": "missing.log"}, 409, "Saga"),
        ({"path": "old"}, 409, "is a directory"),
        ({"path": "job.gjf", "start": 9}, 422, "has 5 lines"),
        ({"path": "job.gjf", "search": "(", "regex": True}, 422, "regular expression"),
    ):
        response = at_work.get(read, params=params)
        assert response.status_code == status and why in response.json()["detail"], params
    response = at_work.get("/api/server-files/saga/summary", params={"path": "job.gjf"})
    assert response.status_code == 422
    assert at_work.get("/api/server-files/betzy/files").status_code == 404

    with monkeypatch.context() as patched:  # a keeper from before D124
        patched.setattr("chembook3d.api.server_files.READER_PROTOCOL", keeper.PROTOCOL + 1)
        response = at_work.get("/api/server-files/saga/files")
    assert response.status_code == 409 and "logs out and logs in again" in response.json()["detail"]

    at_work.post("/api/remote/servers/saga/logout", headers=ORIGIN)
    status = at_work.get("/api/server-files").json()["servers"][0]
    assert (status["logged_in"], status["readable"]) == (False, False)
    response = at_work.get(read, params={"path": "job.gjf"})
    assert response.status_code == 409
    assert "Claude cannot log in" in response.json()["detail"]


def test_claudes_tools_read_the_server_and_are_allowed_without_asking(at_work):
    tools = {
        "list_servers",
        "check_server_job",
        "list_server_files",
        "read_server_file",
        "summarize_server_file",
        "get_server_structure",
    }
    assert tools <= READ_ONLY_TOOLS and all(BY_NAME[t].method == "GET" for t in tools)

    async def run():
        http = httpx.AsyncClient(transport=httpx.ASGITransport(app=at_work.app), base_url=APP)
        async with http:
            bridge = Bridge(APP, http=http)
            servers = await bridge.call("list_servers", {})
            assert not servers.is_error and f'"cwd": "{WORK}"' in servers.content[0].text
            tail = await bridge.call(
                "read_server_file", {"server_id": "saga", "path": "job.gjf", "tail": 1}
            )
            assert tail.content[0].text.splitlines()[-1] == "      5\t0 1"
            xyz = await bridge.call("get_server_structure", {"server_id": "saga", "path": TS})
            assert xyz.content[0].text.splitlines()[1].startswith(f"saga:{WORK}/{TS} step 2")
            away = await bridge.call("list_server_files", {"server_id": "saga", "path": ".."})
            assert away.is_error and "outside the terminal's current directory" in (
                away.content[0].text
            )

    anyio.run(run)


def test_check_server_job_through_mcp(at_work, saga):
    # D266: synthetic wrapper records for two separate attempts with the same name.
    work = saga.root / WORK.lstrip("/")
    url = "/api/server-files/saga/jobs/"
    copied = work / "trial.out"
    copied.write_bytes((FIXTURES / TS).read_bytes())
    log = work / "trial.log"

    def start(job_id):
        return (
            f"Starting job {job_id} on test-node on Saga at Mon Jan 01 12:00:00 CET 2024\n"
            f"JobName: trial\nWork space: {WORK}/{job_id}-trial\n"
            "Looking for formatted checkpoint files (*.fchk) ...\n"
            "Running command: g16 < trial.inp > trial.out\n"
        )

    assert at_work.get(url + "301").json()["state"] == "unknown"
    log.write_text(
        start("301")
        + "Checking normal termination:\n -> trial.out \n    normally terminated\n"
        + "Copying './trial.out' to submission folder...\nJobState=RUNNING\n"
        + "Job exited normally.\nJob 301 completed at noon\n",
        encoding="utf-8",
    )

    async def check():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=at_work.app), base_url=APP
        ) as http:
            result = await Bridge(APP, http=http).call(
                "check_server_job", {"server_id": "saga", "job_id": "301"}
            )
            assert not result.is_error, result.content
            return json.loads(result.content[0].text)

    completed = anyio.run(check)
    assert completed["state"] == "completed" and completed["wrapper_termination"] == "normal"
    assert completed["output_path"] == f"{WORK}/trial.out"
    assert completed["steps"][0]["optimization_converged"]
    assert completed["steps"][1]["imaginary_frequencies_cm1"][0] < 0
    os.utime(copied, (1, 1))
    stale = at_work.get(url + "301").json()
    assert stale["steps"] is None and "modification time" in stale["output_note"]
    assert at_work.get(url + "302").json()["state"] == "unknown"
    log.write_text(start("302"), encoding="utf-8")
    assert at_work.get(url + "302").json()["steps"] is None
    log.write_text(
        start("302")
        + "[12:01] error: *** JOB 302 ON test-node CANCELLED AT noon DUE to SIGNAL Terminated ***\n"
        + "Job was cancelled.\n302  40  72s  0\n302.batch Signal 15\nJob 302 completed at noon\n",
        encoding="utf-8",
    )
    cancelled = at_work.get(url + "302").json()
    assert cancelled["state"] == "cancelled" and cancelled["steps"] is None
    assert not cancelled["copy_announced"] and cancelled["output_path"] is None
    assert cancelled["run_directory"] == f"{WORK}/302-trial"
    run = work / "302-trial"
    run.mkdir()
    (run / "trial.out").write_bytes(copied.read_bytes())
    assert at_work.get(url + "302").json()["output_path"] == f"{WORK}/302-trial/trial.out"
    assert at_work.get(url + "301", params={"path": "trial.log"}).json()["state"] == "unknown"
    assert at_work.get(url + "302", params={"path": ".."}).status_code == 409
    assert at_work.get(url + "302", headers={"origin": "https://other.test"}).status_code == 403

    assert at_work.post("/api/remote/servers/saga/logout", headers=ORIGIN).is_success
    assert at_work.get(url + "302").status_code == 409
    assert _log_in(at_work, saga)["kind"] == "connected"
    with _terminal(at_work) as ws:
        _read_until(ws, lambda text, m: "$ " in text)
        ws.send_json({"type": "input", "data": f"cd {WORK}\r"})
        _read_until(ws, lambda text, m: {"type": "cwd", "path": WORK} in m)
    assert at_work.get(url + "302").json()["state"] == "cancelled"


def test_user_can_export_and_import_outside_the_pilot_area(at_work, saga, config_dir):
    enable_job_pilot(saga, config_dir, "saga")
    assert _allow(at_work).is_success
    assert _write(at_work, "manual.xyz").status_code == 409
    node = _node(at_work, "manual")
    sent = at_work.post("/api/remote/servers/saga/send", json={"node_id": node}, headers=ORIGIN)
    assert sent.json() == {"path": f"{WORK}/manual.xyz", "sent": True, "exists": False}
    plan = at_work.post(
        "/api/remote/servers/saga/import", json={"paths": [f"{WORK}/{TS}"]}, headers=ORIGIN
    )
    assert plan.is_success
    imported = at_work.post(f"/api/imports/{plan.json()['token']}/commit", json={})
    assert imported.is_success and imported.json()["node_id"]


def test_missing_job_subsystem_never_falls_back_to_shell(at_work, saga, config_dir):
    enable_job_pilot(saga, config_dir, "saga")
    pilot = saga.job_pilot
    saga.job_pilot = None  # client configured, helper unavailable on the server
    before = len(saga.record.commands)
    response = at_work.post(
        "/api/hpc-jobs/saga/prepare",
        json={"geometry": "water.xyz", "job_file": "minimum.jd", "cores": 1, "hours": 1},
    )
    assert response.status_code == 409
    assert saga.record.commands[before:] == [None]  # one subsystem request, no exec fallback
    assert saga.record.logins == 1 and not (pilot.state / "attempts.sqlite").exists()


def test_saga_probe_reuses_the_keepers_login(at_work, saga, capsys):
    from chembook3d.cli import main
    from chembook3d.remote import probe

    before = len(saga.record.commands)
    with pytest.raises(client.KeeperError, match="only a server ID"):
        asyncio.run(client.call("saga_probe", server="saga", path="custom.py"))
    assert len(saga.record.commands) == before
    dest = saga.root / HOME.lstrip("/") / ".local/lib/chembook3d/hpc_capability_probe.py"
    for install in (False, True):
        if install:
            dest.parent.mkdir(parents=True)
            dest.write_text("raise RuntimeError('unreviewed file')", encoding="ascii")
        with pytest.raises(SystemExit) as failure:
            main(["saga-probe"])
        assert failure.value.code == 1 and "Install the branch's probe" in capsys.readouterr().err
    source = Path(__file__).resolve().parents[1] / "scripts/hpc_capability_probe.py"
    dest.write_bytes(source.read_bytes().replace(b"\r\n", b"\n").replace(b"\n", b"\r\n"))
    main(["saga-probe"])
    assert json.loads(capsys.readouterr().out) == probe.REPLY
    assert saga.record.logins == 1 and saga.record.commands[before:] == [probe.COMMAND] * 3


SAGA_WORK = f"/cluster/projects/nn0000k/{USER}/chembook3d-pilot"
SAGA_CONTROL = ".local/lib/chembook3d"
SAGA_MARKER = f"{SAGA_CONTROL}/{saga_prepare.MARKER}"


def _enroll_saga(saga, *, install_helper=False) -> Path:
    # The fixture's SFTP home is its root, and the launcher uses the SSH account's HOME.
    for login_home in (saga.root, saga.root / HOME.lstrip("/")):
        manifest = login_home / SAGA_MARKER
        manifest.parent.mkdir(parents=True, exist_ok=True)
        with os.fdopen(
            os.open(manifest, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600),
            "w",
            encoding="utf-8",
        ) as stream:
            json.dump({"work_root": SAGA_WORK}, stream)
    work = saga.root / SAGA_WORK.lstrip("/")
    (work / "geometries").mkdir(parents=True)
    if install_helper:
        (saga.saga_control / "saga_prepare.py").write_bytes(
            Path(saga_prepare.__file__).read_bytes()
        )
    return work


def test_saga_preparation_uses_only_the_fixed_command_on_the_existing_login(at_work, saga):
    tool = BY_NAME["prepare_saga_minimum"]
    schema = input_schema(tool)
    assert set(schema["properties"]) == {"server_id"}
    assert schema["required"] == ["server_id"]
    assert tool.kind == "write"
    before = len(saga.record.commands)

    async def exercise():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=at_work.app), base_url=APP
        ) as http:
            bridge = Bridge(APP, http=http)
            args = {"server_id": "saga"}
            assert (await bridge.call(tool.name, args)).is_error  # no operator installation
            assert saga.record.commands[before:] == []
            work = _enroll_saga(saga)
            assert (await bridge.call(tool.name, {**args, "path": "water.xyz"})).is_error
            with pytest.raises(client.KeeperError):
                await client.call("saga_prepare_minimum", server="saga", path="water.xyz")
            assert saga.record.commands[before:] == []

            # The launcher is available, but the installed helper is not.
            assert (await bridge.call(tool.name, args)).is_error
            assert saga.record.commands[before:] == [saga_access.COMMAND]

            input_text = (
                "#p HF/STO-3G Opt\n\nWater\n\n0 1\nO 0 0 0\nH 0.76 0.59 0\nH -0.76 0.59 0\n\n"
            )
            digest = hashlib.sha256(input_text.encode()).hexdigest()
            directory = "prepare-0123456789abcdef0123456789abcdef"
            prepared = {
                "state": "prepared",
                "directory": f"{SAGA_WORK}/{directory}",
                "input_name": "water.inp",
                "input": input_text,
                "sha256": digest,
                "reference_sha256": digest,
            }
            (work / directory).mkdir()
            (work / directory / "water.inp").write_bytes(input_text.encode())
            saga.saga_prepare_reply = {
                "ok": True,
                "result": {**prepared, "reference_sha256": "0" * 64},
            }
            assert (await bridge.call(tool.name, args)).is_error
            saga.saga_prepare_reply = {"ok": True, "result": prepared}
            answer = await bridge.call(tool.name, args)
            assert not answer.is_error, answer.content
            assert json.loads(answer.content[0].text) == prepared

    anyio.run(exercise)
    assert saga.record.commands[before:] == [saga_access.COMMAND] * 3
    assert saga.record.saga_prepare_requests == [b'{"operation":"prepare_minimum"}\n'] * 3
    assert saga.record.answers == [[PASSWORD], [CODE]]
    assert saga.record.logins == 1


@pytest.mark.parametrize("drop_reply", [False, True])
def test_enrolled_saga_minimum_requires_review_and_recovers_one_receipt(
    at_work, saga, monkeypatch, drop_reply
):
    preview = linked_minimum_input("sample", ["H 0 0 0", "H 0 0 0.74"])
    revision = hashlib.sha256(preview.encode()).hexdigest()
    enable_saga_minimum(saga, SAGA_WORK, preview, monkeypatch)
    marker = json.loads((saga.saga_control / ".submit-minimum.json").read_bytes())
    attempt = marker["attempt"]
    stage_path = f"{SAGA_WORK}/prepare-{attempt}/submission"
    stage = saga.root / stage_path.lstrip("/")
    saga.drop_submit_reply = drop_reply
    before = len(saga.record.commands)
    ids = {"server_id": "saga", "attempt": attempt}
    resources = {"nodes": 1, "cores": 40, "hours": 24}

    async def exercise():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=at_work.app), base_url=APP
        ) as http:
            bridge = Bridge(APP, http=http)
            for _ in range(2):
                prepared = await bridge.call("prepare_saga_minimum", {"server_id": "saga"})
                assert not prepared.is_error, prepared.content
                job = json.loads(prepared.content[0].text)
                assert job["submission_enabled"] and job["attempt"] == attempt
                assert job["input"] == preview
                assert job["revision"] == job["reference_sha256"] == revision
                assert {field: job[field] for field in resources} == resources
                assert marker["key"] not in prepared.content[0].text
            url = f"/api/hpc-jobs/saga/attempts/{attempt}/submit"
            assert (await http.post(url, headers=ORIGIN)).status_code == 403
            extra = {"action": "submit_prepared_job", "params": {**ids, "cores": 4}}
            assert (await http.post("/api/confirmations", json=extra)).status_code == 422
            if drop_reply:
                # The general "Work on its own" setting still requires this specific review.
                autonomy = await http.put("/api/claude/autonomy", json={"on": True}, headers=ORIGIN)
                assert autonomy.is_success and autonomy.json()["on"]

            async def decide(choice):
                task = asyncio.create_task(bridge.call("submit_prepared_job", ids))
                try:
                    with anyio.fail_after(10):
                        while True:
                            pending = (await http.get("/api/live")).json()["confirmations"]
                            if pending:
                                break
                            if task.done():
                                pytest.fail(str((await task).content))
                            await asyncio.sleep(0.02)
                    [dialog] = pending
                    assert dialog["preview"] == preview
                    for item in ("sample.inp", stage_path, revision, "1 node, 40 cores, 24 h"):
                        assert item in dialog["summary"], dialog["summary"]
                    answer = f"/api/confirmations/{dialog['id']}/answer"
                    body = {"confirm": choice != "refuse"}
                    for headers in ({}, {**ORIGIN, "X-Chembook-Client": "claude"}):
                        assert (
                            await http.post(answer, json=body, headers=headers)
                        ).status_code == 403
                    if choice == "change target":
                        saved = servers.load()
                        servers.save([servers.Server("saga", "Saga", "127.0.0.1", 1, USER)])
                    try:
                        action = await http.post(answer, json=body, headers=ORIGIN)
                    finally:
                        if choice == "change target":
                            servers.save(saved)
                    assert action.is_success, action.text
                    return action.json()["status"], await task
                finally:
                    task.cancel()

            for choice, expected in (("refuse", "refused"), ("change target", "failed")):
                status, _ = await decide(choice)
                assert status == expected
                assert not any(
                    r["operation"] == "submit_minimum" for r in saga.record.saga_minimum_requests
                )
                assert not stage.exists()
            initial = await bridge.call("get_prepared_job", ids)
            assert (
                not initial.is_error and json.loads(initial.content[0].text)["state"] == "prepared"
            )
            status, submitted = await decide("confirm")
            assert status == ("failed" if drop_reply else "done"), submitted.content
            assert submitted.is_error == drop_reply
            follow_up = {"server_id": "saga", "job_id": "72410", "path": stage_path}
            for repeat in (False, True):
                if repeat:
                    status, submitted = await decide("confirm")
                    assert status == "done" and not submitted.is_error
                found = await bridge.call("get_prepared_job", ids)
                assert not found.is_error, found.content
                result = json.loads(found.content[0].text)
                assert result["state"] == "submitted" and result["revision"] == revision
                assert result["follow_up"] == follow_up
            checked = await bridge.call("check_server_job", follow_up)
            assert (
                not checked.is_error and json.loads(checked.content[0].text)["state"] == "started"
            )

    anyio.run(exercise)
    payload = {"operation": "submit_minimum", "attempt": attempt, "revision": revision, **resources}
    signed = {**payload, "signature": job_pilot.signature(bytes.fromhex(marker["key"]), payload)}
    submissions = [
        r for r in saga.record.saga_minimum_requests if r["operation"] == "submit_minimum"
    ]
    assert submissions == [signed, signed]
    reads = [r for r in saga.record.saga_minimum_requests if r["operation"] == "minimum_status"]
    assert reads == [{"operation": "minimum_status", "attempt": attempt, "revision": revision}] * 3
    assert (stage / "scheduler-calls.txt").read_text().splitlines() == [
        "sample.inp -n 1 -c 40 -t 24"
    ]
    assert (stage / "sample.inp").read_bytes() == preview.encode()
    assert saga.record.logins == 1 and set(saga.record.commands[before:]) == {saga_access.COMMAND}


def _assert_only_enrollment_sftp(saga, start):
    assert {p.lstrip("/") for p in saga.record.sftp_paths[start:]} <= {
        ".",
        SAGA_MARKER,
        SAGA_WORK.lstrip("/"),
    }


def test_saga_enrollment_routes_agent_files_through_the_pinned_helper(at_work, saga):
    work = _enroll_saga(saga, install_helper=True)
    (work / "results").mkdir()
    (work / "results" / "sample.txt").write_text("fixture result", encoding="utf-8")
    before_exec, before_sftp = len(saga.record.commands), len(saga.record.sftp_paths)
    files = "/api/server-files/saga"
    results = f"{SAGA_WORK}/results"
    geometry = f"{SAGA_WORK}/geometries/other.xyz"

    response = at_work.get(f"{files}/files", params={"path": results})
    assert response.is_success, response.text
    listing = response.json()
    assert listing["path"] == results and SAGA_WORK in listing["cwd_note"]
    assert [(e["name"], e["kind"], e["size"]) for e in listing["entries"]] == [
        ("sample.txt", "file", len("fixture result")),
    ]
    read = at_work.get(f"{files}/read", params={"path": f"{results}/sample.txt"})
    assert read.is_success and "fixture result" in read.text

    assert _write(at_work, geometry, WATER).status_code == 409
    assert [r["operation"] for r in saga.record.saga_file_requests] == ["list_files", "read_file"]
    assert _allow(at_work).is_success
    text = WATER.replace("\n", "\r\n").replace("water\r\n", "water\r\r\n")
    expected = WATER.replace("water\n", "water\r\n").encode()
    written = _write(at_work, geometry, text)
    assert written.is_success, written.text
    assert written.json()["path"] == f"saga:{geometry}"
    assert written.json()["folders"] == [] and written.json()["size"] == len(expected)
    assert saga.record.saga_file_requests[-1] == {
        "operation": "create_xyz",
        "path": geometry,
        "text": text,
    }
    assert _write(at_work, geometry, WATER.replace("water", "duplicate")).status_code == 409
    assert (work / "geometries" / "other.xyz").read_bytes() == expected
    assert [entry["path"] for entry in _status(at_work)["claude_written"]] == [geometry]

    for path in (
        "manual.xyz",
        f"{results}/other.xyz",
        f"{SAGA_WORK}/geometries/deep/other.xyz",
        f"{SAGA_WORK}/geometries/other.inp",
    ):
        assert _write(at_work, path, WATER).status_code == 409, path
    assert at_work.get(f"{files}/files", params={"path": WORK}).status_code == 409
    assert at_work.get(f"{files}/read", params={"path": f"{WORK}/job.gjf"}).status_code == 409
    assert at_work.post(f"{files}/import", json={"paths": [f"{WORK}/{TS}"]}).status_code == 409
    assert not (saga.root / WORK.lstrip("/") / "manual.xyz").exists()
    assert not (work / "geometries" / "deep").exists()
    assert sorted(p.name for p in (work / "results").iterdir()) == ["sample.txt"]
    assert saga.record.commands[before_exec:] == [saga_access.COMMAND] * len(
        saga.record.saga_file_requests
    )
    _assert_only_enrollment_sftp(saga, before_sftp)
    assert saga.record.logins == 1


def test_saga_agent_import_copies_an_output_in_bounded_helper_reads(at_work, saga):
    work = _enroll_saga(saga, install_helper=True)
    output = (FIXTURES / TS).read_bytes()
    (work / "results").mkdir()
    (work / "results" / TS).write_bytes(output)
    path = f"{SAGA_WORK}/results/{TS}"
    before_exec, before_sftp = len(saga.record.commands), len(saga.record.sftp_paths)
    plan = at_work.post("/api/server-files/saga/import", json={"paths": [path]})
    assert plan.is_success, plan.text
    assert plan.json()["origin"] == {"device": "Saga", "path": f"saga:{path}", "name": TS}
    imported = at_work.post(f"/api/imports/{plan.json()['token']}/commit", json={})
    assert imported.is_success, imported.text
    node_id = imported.json()["node_id"]
    source = at_work.get(f"/api/nodes/{node_id}/calculations").json()[0]["source_file"]
    assert at_work.get(f"/api/source-files/{source['id']}/download").content == output

    assert all(
        r["operation"] == "read_file" and r["path"] == path for r in saga.record.saga_file_requests
    )
    chunks = [(r["offset"], r["length"]) for r in saga.record.saga_file_requests if r["length"]]
    assert chunks == [
        (start, min(keeper.MAX_READ, len(output) - start))
        for start in range(0, len(output), keeper.MAX_READ)
    ]
    assert saga.record.commands[before_exec:] == [saga_access.COMMAND] * len(
        saga.record.saga_file_requests
    )
    _assert_only_enrollment_sftp(saga, before_sftp)


def test_saga_download_discards_chunks_when_output_is_replaced(
    at_work, saga, tmp_path, monkeypatch
):
    work = _enroll_saga(saga, install_helper=True)
    output = work / "growing.out"
    output.write_bytes(b"a" * (keeper.MAX_READ + 10))
    replacement = work / "replacement"
    replacement.write_bytes(b"b" * output.stat().st_size)
    stamp = output.stat().st_mtime_ns
    os.utime(replacement, ns=(stamp, stamp))
    original = saga._saga_files

    async def file_request(process, request):
        if request["operation"] == "read_file" and request["offset"] == keeper.MAX_READ:
            replacement.replace(output)
        return await original(process, request)

    monkeypatch.setattr(saga, "_saga_files", file_request)
    destination = tmp_path / "copies"
    destination.mkdir()
    existing = destination / output.name
    existing.write_bytes(b"previous copy")
    reply = asyncio.run(
        client.call(
            "download",
            server="saga",
            paths=[f"{SAGA_WORK}/{output.name}"],
            folder=str(destination),
            within=True,
        )
    )
    assert not reply["files"] and "changed" in reply["failed"][0]["reason"]
    assert list(destination.iterdir()) == [existing]
    assert existing.read_bytes() == b"previous copy"


@pytest.mark.parametrize("failure", ["missing", "wrong pin", "refusal"])
def test_saga_agent_file_helper_failures_never_fall_back_to_sftp(at_work, saga, tmp_path, failure):
    work = _enroll_saga(saga, install_helper=failure == "refusal")
    if failure == "wrong pin":
        (saga.saga_control / "saga_prepare.py").write_text(
            "raise AssertionError()", encoding="utf-8"
        )
    if failure == "refusal":
        saga.saga_file_error = "Fixture helper refused the request."
    expected = {
        "missing": "Saga could not read ~/.local/lib/chembook3d/saga_prepare.py",
        "wrong pin": f"The running keeper expects Saga helper SHA-256 {saga_access._sha}",
        "refusal": saga.saga_file_error,
    }[failure]
    (work / "results").mkdir()
    (work / "results" / TS).write_bytes((FIXTURES / TS).read_bytes())
    results = f"{SAGA_WORK}/results"
    path = f"{results}/{TS}"
    geometry = f"{SAGA_WORK}/geometries/failed.xyz"
    assert _allow(at_work).is_success
    before_exec, before_sftp = len(saga.record.commands), len(saga.record.sftp_paths)
    files = "/api/server-files/saga"
    for response in (
        at_work.get(f"{files}/files", params={"path": results}),
        at_work.get(f"{files}/read", params={"path": path}),
        _write(at_work, geometry, WATER),
    ):
        assert response.status_code == 409, response.text
        assert expected in response.json()["detail"]
    folder = tmp_path / "copy"
    folder.mkdir()
    copied = asyncio.run(
        client.call("download", server="saga", paths=[path], folder=str(folder), within=True)
    )
    assert copied["files"] == [] and [item["path"] for item in copied["failed"]] == [path]
    assert not list(folder.iterdir()) and not list((work / "geometries").iterdir())
    assert [r["operation"] for r in saga.record.saga_file_requests] == [
        "list_files",
        "read_file",
        "create_xyz",
        "read_file",
    ]
    assert saga.record.commands[before_exec:] == [saga_access.COMMAND] * 4
    _assert_only_enrollment_sftp(saga, before_sftp)
    assert saga.record.logins == 1


@pytest.mark.parametrize("capability", [None, False])
def test_older_keeper_cannot_offer_agent_file_operations(at_work, saga, monkeypatch, capability):
    _enroll_saga(saga, install_helper=True)
    assert _allow(at_work).is_success
    call = client.call
    operations = []

    async def old_keeper(operation, **fields):
        operations.append(operation)
        result = await call(operation, **fields)
        if operation == "ping":
            if capability is None:
                result.pop("saga_file_operations", None)
            else:
                result["saga_file_operations"] = capability
        return result

    monkeypatch.setattr(client, "call", old_keeper)
    files = "/api/server-files/saga"
    path = f"{SAGA_WORK}/geometries/old.xyz"
    for response in (
        at_work.get(f"{files}/files", params={"path": SAGA_WORK}),
        at_work.get(f"{files}/read", params={"path": path}),
        _write(at_work, path, WATER),
        at_work.post(f"{files}/import", json={"paths": [path]}),
    ):
        assert response.status_code == 409
        assert "older version" in response.json()["detail"]
    assert operations == ["ping"] * 4
    assert not saga.record.saga_file_requests and saga.record.logins == 1


def test_saga_enrollment_preserves_manual_transfers_outside_the_pilot(at_work, saga):
    _enroll_saga(saga)  # manual transfers still work with the helper missing
    node = _node(at_work, "manual")
    sent = at_work.post("/api/remote/servers/saga/send", json={"node_id": node}, headers=ORIGIN)
    assert sent.json() == {"path": f"{WORK}/manual.xyz", "sent": True, "exists": False}
    url = "/api/remote/servers/saga/files"
    listing = at_work.get(url)
    assert listing.is_success, listing.text
    assert "manual.xyz" in {entry["name"] for entry in listing.json()["entries"]}
    before_sftp = len(saga.record.sftp_paths)
    agent = at_work.get(url, headers={"X-Chembook-Client": "claude"})
    assert agent.status_code == 403 and len(saga.record.sftp_paths) == before_sftp
    plan = at_work.post(
        "/api/remote/servers/saga/import", json={"paths": [f"{WORK}/{TS}"]}, headers=ORIGIN
    )
    assert plan.is_success, plan.text
    imported = at_work.post(f"/api/imports/{plan.json()['token']}/commit", json={})
    assert imported.is_success and imported.json()["node_id"]
    assert f"{WORK}/manual.xyz" in saga.record.sftp_paths
    assert f"{WORK}/{TS}" in saga.record.sftp_paths
    assert saga.record.saga_file_requests == []
    assert saga.record.logins == 1


@pytest.mark.parametrize("drop_reply", [False, True])
def test_local_preparation_submission_and_follow_up_through_mcp(
    at_work, saga, config_dir, drop_reply
):
    # The app, bridge, real keeper and loopback SSH helper exercise the production routes.
    args = {
        "server_id": "saga",
        "job_file": "linked-minimum.jd" if drop_reply else "minimum.jd",
        "geometry": "water.xyz",
        "cores": 40,
        "hours": 24,
    }
    assert (
        at_work.post(
            "/api/hpc-jobs/saga/prepare", json={k: v for k, v in args.items() if k != "server_id"}
        ).status_code
        == 409
    )
    enable_job_pilot(saga, config_dir, "saga")
    saga.drop_submit_reply = drop_reply
    remote = saga.job_pilot.remote_work
    # The user's shell stays in WORK, outside the pilot. Its location grants no agent access.
    assert _allow(at_work).is_success
    assert _write(at_work, "other.xyz").status_code == 409
    assert _write(at_work, f"{remote}/other.xyz").status_code == 409
    assert _write(at_work, f"{remote}/geometries/other.xyz", "1\n\nHe 0 0 0\n").is_success

    async def exercise():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=at_work.app), base_url=APP
        ) as http:
            bridge = Bridge(APP, http=http)
            assert (
                await bridge.call("list_server_files", {"server_id": "saga", "path": WORK})
            ).is_error
            prepared = await bridge.call("prepare_gaussian_job", args)
            assert not prepared.is_error, prepared.content
            record = json.loads(prepared.content[0].text)
            ids = {"attempt": record["attempt"], "server_id": "saga"}
            assert record["state"] == "prepared"
            assert record["input"].count("--Link1--") == (4 if drop_reply else 0)
            url = f"/api/hpc-jobs/saga/attempts/{record['attempt']}/submit"
            assert (await http.post(url)).status_code == 403
            for approved in (False, True):
                task = asyncio.create_task(bridge.call("submit_prepared_job", ids))
                try:
                    with anyio.fail_after(10):
                        while True:
                            pending = (await http.get("/api/live")).json()["confirmations"]
                            if pending:
                                break
                            await asyncio.sleep(0.02)
                    dialog = pending[0]
                    assert record["revision"] in dialog["summary"]
                    assert dialog["preview"] == record["input"]
                    assert "40 cores, 24 h" in dialog["summary"]
                    answer = f"/api/confirmations/{dialog['id']}/answer"
                    assert (await http.post(answer, json={"confirm": True})).status_code == 403
                    response = await http.post(answer, json={"confirm": approved}, headers=ORIGIN)
                    expected = ("failed" if drop_reply else "done") if approved else "refused"
                    assert response.json()["status"] == expected, response.text
                    await task
                finally:
                    task.cancel()
                status = await bridge.call("get_prepared_job", ids)
                observed = json.loads(status.content[0].text)
                assert observed["state"] == ("submitted" if approved else "prepared")
            checked = await bridge.call("check_server_job", observed["follow_up"])
            assert json.loads(checked.content[0].text)["state"] == "started"
            assert saga.record.logins == 1
            calls = saga.job_pilot.work / record["attempt"] / "submission" / "scheduler-calls.txt"
            assert calls.read_text().splitlines() == ["water.inp 1 40 24"]
            assert calls.with_name("water.inp").read_bytes() == record["input"].encode()

    anyio.run(exercise)


# ---------- T-SSH-23 Claude imports outputs from the terminal's directory (D225) ----------


def test_claude_imports_outputs_from_the_terminals_directory(at_work, saga):
    claude = {"X-Chembook-Client": "claude"}
    url = "/api/server-files/saga/import"
    work = saga.root / WORK.lstrip("/")
    before = {p.name: p.read_bytes() for p in work.iterdir() if p.is_file()}

    # Several of one directory: the folder import's plan, with the server as the origin.
    several = at_work.post(url, json={"paths": [TS, TS_SP, "job.gjf"]}, headers=claude)
    assert several.status_code == 200, several.text
    plan = several.json()
    assert plan["folder"] == f"saga:{WORK}" and plan["origin_device"] == "Saga"
    assert sorted(r["path"] for r in plan["rows"]) == sorted([TS, TS_SP])
    assert at_work.get("/api/canvas").json()["nodes"] == []  # nothing imported yet
    assert at_work.delete(f"/api/batch-imports/{plan['token']}").status_code == 204

    # One file: the import's plan, committed as any import.
    one = at_work.post(url, json={"paths": [f"{WORK}/{TS}"]}, headers=claude)
    assert one.status_code == 200, one.text
    plan = one.json()
    assert plan["origin"] == {"device": "Saga", "path": f"saga:{WORK}/{TS}", "name": TS}
    done = at_work.post(f"/api/imports/{plan['token']}/commit", json={}, headers=claude)
    assert done.status_code == 200, done.text
    calc = at_work.get(f"/api/nodes/{done.json()['node_id']}/calculations").json()[0]
    source = calc["source_file"]
    assert (source["origin_device"], source["origin_path"]) == ("Saga", f"saga:{WORK}/{TS}")

    # Only the terminal's directory and below, files of one directory, and from this computer.
    (work / "old" / "x.log").write_bytes((FIXTURES / TS).read_bytes())
    for paths, status, why in (
        (["/etc/hostname"], 409, "outside the terminal's current directory"),
        (["missing.log"], 409, "Saga"),
        ([TS, "old/x.log"], 422, "one directory"),
        ([], 422, "at least one file"),
        (["job.gjf"], 422, ""),
    ):
        refused = at_work.post(url, json={"paths": paths}, headers=claude)
        assert refused.status_code == status and why in str(refused.json()["detail"]), paths
    foreign = at_work.post(url, json={"paths": [TS]}, headers={"origin": "https://example.com"})
    assert foreign.status_code == 403
    # Nothing on the server changed.
    (work / "old" / "x.log").unlink()
    assert {p.name: p.read_bytes() for p in work.iterdir() if p.is_file()} == before

    tool = BY_NAME["import_server_files"]
    assert tool.kind == "write" and tool.name not in READ_ONLY_TOOLS  # it asks in the terminal


def test_claudes_import_brings_an_orca_ircs_trajectories(at_work, saga):
    work = saga.root / WORK.lstrip("/") / "irc"
    work.mkdir()
    for name in IRC_NAMES:
        (work / name).write_bytes((ORCA_IRC / name).read_bytes())
    text = (ORCA_IRC / "output.out").read_text(encoding="utf-8")
    start = orca.parse(text).steps[0].geometries[0]
    xyz = f"{len(start)}\n\n" + "\n".join(f"{a.element} {a.x} {a.y} {a.z}" for a in start)
    body = {"label": "TS", "xyz": xyz, "role": "transition_state", "charge": 1, "multiplicity": 1}
    ts = at_work.post("/api/nodes", json=body).json()["id"]
    one = {"paths": ["irc/output.out"], "node_id": ts}
    plan = at_work.post("/api/server-files/saga/import", json=one).json()
    assert [t["node_id"] for t in plan["irc_targets"]] == [ts]
    options = {"target_node_id": ts, "irc_part": "path"}
    response = at_work.post(f"/api/imports/{plan['token']}/commit", json=options)
    assert response.status_code == 200, response.text
    found = at_work.get(f"/api/nodes/{ts}/irc").json()
    assert {s["direction"]: s["points"] for s in found["sides"]} == {"forward": 49, "reverse": 73}


# ---------- T-SSH-15..19 Claude writes new files once the user allows it (D126) ----------


def _allow(local, allow=True, headers=ORIGIN):
    return local.put(
        "/api/remote/servers/saga/claude-writes", json={"allow": allow}, headers=headers
    )


def _write(local, path, text="#P B3LYP/def2SVP Opt\n", **more):
    return local.post("/api/server-files/saga/write", json={"path": path, "text": text, **more})


def test_writing_is_off_until_the_apps_page_turns_it_on_and_at_every_login(at_work, saga):
    work = saga.root / WORK.lstrip("/")
    response = _write(at_work, "new.gjf")
    assert response.status_code == 409
    assert "Let Claude write new files here" in response.json()["detail"]
    # Claude's requests carry no Origin: it cannot give itself the go-ahead.
    assert _allow(at_work, headers={}).status_code == 403
    assert _allow(at_work, headers={"origin": "https://evil.example"}).status_code == 403
    assert _write(at_work, "new.gjf").status_code == 409
    assert not (work / "new.gjf").exists()

    assert _allow(at_work).status_code == 200
    assert _status(at_work)["claude_writes"] is True
    assert at_work.get("/api/server-files").json()["servers"][0]["writes"] is True
    assert _write(at_work, "new.gjf").status_code == 200
    assert _allow(at_work, False).status_code == 200  # Stop
    assert _write(at_work, "other.gjf").status_code == 409

    _allow(at_work)
    at_work.post("/api/remote/servers/saga/logout", headers=ORIGIN)
    assert _log_in(at_work, saga)["kind"] == "connected"
    status = _status(at_work)
    assert (status["claude_writes"], status["claude_written"]) == (False, [])


def test_a_new_file_and_its_folders_are_written_and_listed(at_work, saga):
    work = saga.root / WORK.lstrip("/")
    _allow(at_work)
    with _terminal(at_work) as ws:
        _read_until(ws, lambda text, m: "$ " in text)
        response = _write(at_work, "TS1_opt.gjf", "%chk=TS1_opt.chk\r\n#P Opt\r\n")
        assert response.status_code == 200, response.text
        _, messages = _read_until(ws, lambda text, m: any(x["type"] == "written" for x in m))
    answer = response.json()
    assert answer["path"] == f"saga:{WORK}/TS1_opt.gjf" and answer["size"] == 24
    assert (answer["folders"], answer["diff"], answer["based_on"]) == ([], None, None)
    written = work / "TS1_opt.gjf"
    assert written.read_bytes() == b"%chk=TS1_opt.chk\n#P Opt\n"  # LF, as on Saga
    if sys.platform != "win32":
        assert written.stat().st_mode & 0o111 == 0  # never executable
    assert next(m for m in messages if m["type"] == "written")["path"] == f"{WORK}/TS1_opt.gjf"

    deep = _write(at_work, f"{WORK}/conformers/c01/c01.xyz", WATER)
    assert deep.status_code == 200, deep.text
    assert deep.json()["folders"] == [f"{WORK}/conformers", f"{WORK}/conformers/c01"]
    assert (work / "conformers" / "c01" / "c01.xyz").read_text(encoding="utf-8") == WATER
    assert _write(at_work, "conformers/c02.xyz", WATER).json()["folders"] == []
    assert f"{WORK}/conformers/c01/c01.xyz" in saga.record.sftp_paths
    assert saga.record.saga_file_requests == []

    listed = _status(at_work)["claude_written"]
    assert [w["path"] for w in listed] == [
        f"{WORK}/TS1_opt.gjf",
        f"{WORK}/conformers/c01/c01.xyz",
        f"{WORK}/conformers/c02.xyz",
    ]
    assert all(w["time"] > 0 and w["size"] > 0 for w in listed)


def test_a_file_there_is_never_replaced_and_an_edit_is_a_copy(at_work, saga):
    work = saga.root / WORK.lstrip("/")
    before = (work / "job.gjf").read_bytes()
    _allow(at_work)
    response = _write(at_work, "job.gjf", "overwritten?\n")
    assert response.status_code == 409
    detail = response.json()["detail"]
    assert "never replaces a file" in detail and "job_v2.gjf" in detail
    for directory in ("old", f"{WORK}/old"):
        assert _write(at_work, directory).status_code == 409
    assert (work / "job.gjf").read_bytes() == before

    edited = before.decode().replace("Opt", "Opt Freq")
    copy = _write(at_work, "job_v2.gjf", edited, based_on="job.gjf")
    assert copy.status_code == 200, copy.text
    answer = copy.json()
    assert answer["based_on"] == f"saga:{WORK}/job.gjf"
    assert "-#P B3LYP/6-31G(d) Opt\n+#P B3LYP/6-31G(d) Opt Freq" in answer["diff"]
    assert answer["diff"].startswith("--- job.gjf\n+++ job_v2.gjf")
    assert f"{WORK}/job.gjf is unchanged" in answer["note"]
    assert (work / "job.gjf").read_bytes() == before
    # The next copy of either name is _v3.
    for name in ("job.gjf", "job_v2.gjf"):
        assert "job_v3.gjf" in _write(at_work, name, "x\n").json()["detail"]
    assert sorted(p.name for p in work.iterdir()) == sorted(
        [TS, TS_SP, "job.gjf", "job_v2.gjf", "old"]
    )


def test_what_claude_cannot_write(at_work, saga, monkeypatch):
    work = saga.root / WORK.lstrip("/")
    home = saga.root / "cluster" / "home" / USER
    home.mkdir(parents=True, exist_ok=True)
    _allow(at_work)
    for path, why in (
        (".bashrc", "starting with a dot"),
        (".ssh/authorized_keys", "starting with a dot"),
        ("jobs/.hidden.sh", "starting with a dot"),
        ("../escape.gjf", "current directory"),
        ("old/../../escape.gjf", "current directory"),
        ("/cluster/work/escape.gjf", "outside the terminal's current directory"),
        (f"/cluster/home/{USER}/x.gjf", "outside the terminal's current directory"),
        ("", "Name the file"),
        (f"{TS}/x.gjf", "is a file, not a directory"),
    ):
        response = _write(at_work, path)
        assert response.status_code == 409, (path, response.text)
        assert why in response.json()["detail"], (path, response.json())
    too_big = _write(at_work, "big.txt", "x" * (keeper.MAX_WRITE + 1))
    assert too_big.status_code == 409 and "at most 1 MB" in too_big.json()["detail"]
    assert "Only text" in _write(at_work, "nul.txt", "a\x00b").json()["detail"]
    missing = _write(at_work, "copy.gjf", "x\n", based_on="missing.gjf")
    assert missing.status_code == 409 and not (work / "copy.gjf").exists()
    if sys.platform != "win32":  # symlinks need extra rights on Windows
        (work / "away").symlink_to(home, target_is_directory=True)
        (work / "dangling.gjf").symlink_to(home / "nothing.gjf")
        assert "leads outside" in _write(at_work, "away/x.gjf").json()["detail"]
        assert "never replaces" in _write(at_work, "dangling.gjf").json()["detail"]
        assert not (home / "x.gjf").exists() and not (home / "nothing.gjf").exists()
    assert not list(home.iterdir())
    assert sorted(p.name for p in work.iterdir() if not p.is_symlink()) == sorted(
        [TS, TS_SP, "job.gjf", "old"]
    )

    with monkeypatch.context() as patched:  # a keeper from before D126
        patched.setattr("chembook3d.api.server_files.WRITER_PROTOCOL", keeper.PROTOCOL + 1)
        response = _write(at_work, "new.gjf")
    assert response.status_code == 409 and "cannot let Claude write" in response.json()["detail"]
    at_work.post("/api/remote/servers/saga/logout", headers=ORIGIN)
    assert "Claude cannot log in" in _write(at_work, "new.gjf").json()["detail"]


def test_claudes_write_tool_needs_only_the_apps_go_ahead_and_nothing_deletes(at_work, saga):
    spec = BY_NAME["write_server_file"]
    assert (spec.kind, spec.method, spec.allowed_in_app) == ("write", "POST", True)
    assert "write_server_file" in PANEL_ALLOWED and "write_server_file" not in READ_ONLY_TOOLS
    # Nothing Claude has deletes, renames or runs anything on a server: the one other tool
    # that is not a GET copies files down to stage their import (D225) and writes nothing there.
    assert {t.name for t in TOOLS if "/server-files" in t.path and t.method != "GET"} == {
        "write_server_file",
        "import_server_files",
    }

    async def run():
        http = httpx.AsyncClient(transport=httpx.ASGITransport(app=at_work.app), base_url=APP)
        async with http:
            bridge = Bridge(APP, http=http)
            off = await bridge.call(
                "write_server_file", {"server_id": "saga", "path": "a.gjf", "text": "x\n"}
            )
            assert off.is_error and "Let Claude write new files here" in off.content[0].text
            assert _allow(at_work).status_code == 200
            done = await bridge.call(
                "write_server_file", {"server_id": "saga", "path": "a.gjf", "text": "x\n"}
            )
            assert not done.is_error and f"saga:{WORK}/a.gjf" in done.content[0].text

    anyio.run(run)
    assert (saga.root / WORK.lstrip("/") / "a.gjf").read_text(encoding="utf-8") == "x\n"


# ---------- T-SSH-22 the scan editor on the server (D138) ----------

SCAN = "dvb_scan_relaxed.log"


def test_scans_reports_go_to_the_app_apart_from_inspect():
    shell = keeper.Shell(process=None, tracked=True)  # type: ignore[arg-type]
    viewer: asyncio.Queue = asyncio.Queue()
    shell.viewers.add(viewer)
    shell.feed(
        b"\x1b]1337;Scans=/w/a.log\x07\x1b]1337;Inspect=/w/x.out\x07\x1b]1337;Sca"
        b"ns=/w/b.log\x07\x1b]1337;CurrentDir=/w\x07$ "
    )
    messages = [viewer.get_nowait() for _ in range(viewer.qsize())]
    assert [m for m in messages if m["type"] != "output"] == [
        {"type": "scans", "paths": ["/w/a.log", "/w/b.log"]},
        {"type": "inspect", "paths": ["/w/x.out"]},
        {"type": "cwd", "path": "/w"},
    ]
    assert "scans() {" in keeper.RC and keeper.PROTOCOL >= 7


def _editor_on(at_work, path: str) -> dict:
    body = {"targets": [{"server": "saga", "path": path}]}
    response = at_work.post("/api/scan-editor", json=body, headers=ORIGIN)
    assert response.status_code == 200, response.text
    return response.json()


def test_a_running_scan_on_the_server_is_refreshed_with_its_path(at_work, saga):
    with _terminal(at_work) as ws:
        _read_until(ws, lambda text, m: "$ " in text)
        ws.send_json({"type": "input", "data": "scans *.log missing.log\r"})
        _, messages = _read_until(ws, lambda text, m: any(x["type"] == "scans" for x in m))
    assert next(m["paths"] for m in messages if m["type"] == "scans") == [
        f"{WORK}/{TS}",
        f"{WORK}/{TS_SP}",
    ]

    remote = saga.root / WORK.lstrip("/") / SCAN
    full = (FIXTURES / SCAN).read_text(encoding="utf-8")
    lines = full.splitlines(keepends=True)
    remote.write_text("".join(lines[: len(lines) // 2]), encoding="utf-8")  # still running
    before = _copies()
    opened = _editor_on(at_work, f"{WORK}/{SCAN}")
    [scan] = opened["scans"]
    assert scan["server_name"] == "Saga" and scan["path"] == f"saga:{WORK}/{SCAN}"
    assert len(scan["points"]) == 6 and not scan["ended"]
    assert len(_copies() - before) == 1

    remote.write_text(full, encoding="utf-8")
    # The path from the scan's start to its last point, and a piece of it reversed in front.
    pieces = [{"scan": scan["key"], "first": 5, "last": 0}]
    url = f"/api/scan-editor/{opened['id']}/refresh"
    again = at_work.post(url, json={"pieces": pieces}, headers=ORIGIN).json()
    [fresh] = again["scans"]
    assert fresh["key"] == scan["key"] and len(fresh["points"]) == 13 and fresh["ended"]
    # The path's end at the scan's last point follows it to the new last point.
    assert again["pieces"] == [{"scan": scan["key"], "first": 12, "last": 0}]
    middle = [{"scan": scan["key"], "first": 1, "last": 3}]
    kept = at_work.post(url, json={"pieces": middle}, headers=ORIGIN).json()
    assert kept["pieces"] == middle  # pieces inside the scan keep their points
    assert len(_copies() - before) == 1  # the new copy in place of the old
    at_work.delete(f"/api/scan-editor/{opened['id']}", headers=ORIGIN)
    assert _copies() == before
    assert remote.read_text(encoding="utf-8") == full  # nothing changed on the server


def test_a_joined_scan_is_saved_to_the_server_never_over_a_file(at_work, saga):
    (saga.root / WORK.lstrip("/") / SCAN).write_bytes((FIXTURES / SCAN).read_bytes())
    opened = _editor_on(at_work, f"{WORK}/{SCAN}")
    [scan] = opened["scans"]
    pieces = {"pieces": [{"scan": scan["key"], "first": 0, "last": 6}]}
    path = at_work.post(f"/api/scan-editor/{opened['id']}/path", json=pieces, headers=ORIGIN)
    text = path.json()["text"]
    url = f"/api/scan-editor/{opened['id']}/save-to-server"
    body = {"server": "saga", "name": "dvb_joined.xyz", "text": text}
    first = at_work.post(url, json=body, headers=ORIGIN).json()
    assert first == {"path": f"{WORK}/dvb_joined.xyz", "renamed": False}
    second = at_work.post(url, json={**body, "text": text + "\n"}, headers=ORIGIN).json()
    assert second == {"path": f"{WORK}/dvb_joined_v2.xyz", "renamed": True}
    work = saga.root / WORK.lstrip("/")
    assert (work / "dvb_joined.xyz").read_text(encoding="utf-8") == text
    assert (work / "dvb_joined_v2.xyz").read_text(encoding="utf-8") == text + "\n"

    response = at_work.post(url, json={**body, "text": "3\nwater\n"}, headers=ORIGIN)
    assert response.status_code == 422
    assert at_work.post(url, json=body).status_code == 403  # the app's own page only
