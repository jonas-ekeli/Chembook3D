"""SSH servers such as Saga (D122): saved servers, logging in with password and code through
the keeper, the terminal, and coming back after a restart. `tests/fake_ssh_server.py` stands
in for Saga; the keeper runs as the separate process it is in the app."""

import asyncio
import json
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
from chembook3d.mcp_tools import BY_NAME, PANEL_ALLOWED, READ_ONLY_TOOLS, TOOLS
from chembook3d.remote import client, keeper
from tests.fake_ssh_server import CODE, PASSWORD, USER, FakeSaga, FakeXServer, free_display

APP = "http://127.0.0.1:8765"
ORIGIN = {"origin": APP}
WS = "ws://127.0.0.1:8765"
ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]|\x1b\][^\x07]*\x07|\x1b[()][A-Za-z0-9]|\r")


@pytest.fixture
def home(tmp_path, monkeypatch) -> Path:
    """The user's home folder, so the real ~/.ssh/known_hosts is not read."""
    path = tmp_path / "home"
    path.mkdir()
    monkeypatch.setenv("HOME", str(path))
    monkeypatch.setenv("USERPROFILE", str(path))
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


def _read_until(ws, check) -> tuple[str, list[dict]]:
    """Output (without escape sequences) and other messages until `check(text, messages)`."""
    seen, messages = "", []
    deadline = time.monotonic() + 20
    while not check(seen, messages):
        assert time.monotonic() < deadline, seen
        message = ws.receive_json()
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
            _read_until(ws, lambda text, m: any(x["type"] == "cwd" for x in m))
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
    _point_at(local, saga, x11=True, x11_display=free_display())
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
    time.sleep(2.0)
    assert asyncio.run(client.status())["sessions"] == []  # logged out: nothing typed for 1 s
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


def test_claude_sees_the_servers_and_the_files_in_the_terminals_directory(at_work):
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
            assert not servers.isError and f'"cwd": "{WORK}"' in servers.content[0].text
            tail = await bridge.call(
                "read_server_file", {"server_id": "saga", "path": "job.gjf", "tail": 1}
            )
            assert tail.content[0].text.splitlines()[-1] == "      5\t0 1"
            xyz = await bridge.call("get_server_structure", {"server_id": "saga", "path": TS})
            assert xyz.content[0].text.splitlines()[1].startswith(f"saga:{WORK}/{TS} step 2")
            away = await bridge.call("list_server_files", {"server_id": "saga", "path": ".."})
            assert away.isError and "outside the terminal's current directory" in (
                away.content[0].text
            )

    anyio.run(run)


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
    # Nothing Claude has deletes, renames or runs anything on a server.
    assert {t.name for t in TOOLS if "/server-files" in t.path and t.method != "GET"} == {
        "write_server_file"
    }

    async def run():
        http = httpx.AsyncClient(transport=httpx.ASGITransport(app=at_work.app), base_url=APP)
        async with http:
            bridge = Bridge(APP, http=http)
            off = await bridge.call(
                "write_server_file", {"server_id": "saga", "path": "a.gjf", "text": "x\n"}
            )
            assert off.isError and "Let Claude write new files here" in off.content[0].text
            assert _allow(at_work).status_code == 200
            done = await bridge.call(
                "write_server_file", {"server_id": "saga", "path": "a.gjf", "text": "x\n"}
            )
            assert not done.isError and f"saga:{WORK}/a.gjf" in done.content[0].text

    anyio.run(run)
    assert (saga.root / WORK.lstrip("/") / "a.gjf").read_text(encoding="utf-8") == "x\n"
