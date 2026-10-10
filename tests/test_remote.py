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

import asyncssh
import pytest
from fastapi.testclient import TestClient

from chembook3d.app import create_app
from chembook3d.remote import client, keeper
from tests.fake_ssh_server import CODE, PASSWORD, USER, FakeSaga

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


def _point_at(local, saga, username=USER) -> str:
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
            **{"shell": None, "lost": None},
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
    assert set(on_disk["servers"][0]) == {"id", "name", "host", "port", "username"}

    for bad, why in (
        ({"name": "Saga", "host": "saga sigma2", "username": "x"}, "host name"),
        ({"name": "Saga", "host": "saga.sigma2.no", "port": 0}, "port"),
        ({"name": "Saga", "host": "saga.sigma2.no", "username": "jo nas"}, "user name"),
        ({"name": "", "host": "saga.sigma2.no"}, "name"),
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
        assert _status(local)["cwd"] == str(work) and _status(local)["tracked"] is True
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
