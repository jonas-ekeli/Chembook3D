"""The Claude panel (D92): who may start a terminal, what it starts, and the terminal itself.
`tests/fake_claude.py` stands in for the `claude` CLI."""

import json
import re
import sys
import threading
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from chembook3d import claude_panel
from chembook3d.app import create_app

APP = "http://127.0.0.1:8765"
ORIGIN = {"origin": APP}
WS = "ws://127.0.0.1:8765"  # the test client sends WebSockets to "testserver" otherwise
ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]|\x1b\][^\x07]*\x07|\x1b[()][A-Za-z0-9]|\r")


@pytest.fixture
def fake_claude(tmp_path, monkeypatch) -> Path:
    """A `claude` command that runs tests/fake_claude.py, written as Windows or a shell would
    find it (on Windows a .cmd file, as npm installs it)."""
    script = Path(__file__).with_name("fake_claude.py")
    folder = tmp_path / "bin"
    folder.mkdir()
    if sys.platform == "win32":
        path = folder / "claude.cmd"
        path.write_text(f'@"{sys.executable}" "{script}" %*\r\n', encoding="utf-8")
    else:
        path = folder / "claude"
        path.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{script}" "$@"\n', encoding="utf-8")
        path.chmod(0o755)
    monkeypatch.setenv("CHEMBOOK3D_CLAUDE", str(path))
    return path


@pytest.fixture
def local(tmp_path):
    """A client that reaches the app as the browser on this computer does."""
    app = create_app()
    with TestClient(app, base_url=APP, client=("127.0.0.1", 50000)) as test_client:
        response = test_client.post(
            "/api/investigations", json={"folder": str(tmp_path / "inv"), "name": "Metathesis"}
        )
        assert response.status_code == 200, response.text
        yield test_client


def _token(client, resume=False, **kwargs) -> str:
    response = client.post("/api/claude/sessions", json={"resume": resume}, **kwargs)
    assert response.status_code == 200, response.text
    return response.json()["token"]


def _read_until(ws, text: str) -> str:
    """Terminal output up to `text`, without escape sequences; fails on an early exit."""
    seen = ""
    while text not in seen:
        message = ws.receive_json()
        assert message["type"] == "output", f"ended before {text!r}: {seen!r}"
        seen += ANSI.sub("", message["data"])
    return seen


def _exit_code(ws) -> int | None:
    while True:
        message = ws.receive_json()
        if message["type"] == "exit":
            return message["code"]


def test_status_without_claude(local, monkeypatch, tmp_path):
    monkeypatch.setenv("CHEMBOOK3D_CLAUDE", str(tmp_path / "missing" / "claude"))
    status = local.get("/api/claude").json()
    assert status["available"] is False
    assert status["enabled"] is True
    assert status["install"]["docs"].startswith("https://")
    assert "claude.ai/install" in status["install"]["command"]
    response = local.post("/api/claude/sessions", json={}, headers=ORIGIN)
    assert response.status_code == 409
    assert "not installed" in response.json()["detail"]


def test_status_with_claude(local, fake_claude):
    status = local.get("/api/claude").json()
    assert status["available"] is True and status["enabled"] is True


def test_not_offered_away_from_loopback(client, fake_claude):
    """A request that did not come to 127.0.0.1 from this computer (the default test client
    is "testclient" talking to "testserver") cannot use the panel."""
    status = client.get("/api/claude").json()
    assert status["enabled"] is False
    assert "127.0.0.1" in status["reason"]
    assert client.post("/api/claude/sessions", json={}).status_code == 403


@pytest.mark.parametrize(
    "headers",
    [
        {"host": "evil.example:8765"},  # DNS rebinding: a foreign name that resolves to us
        {"origin": "https://evil.example"},  # another web site's page
        {"origin": "null"},
    ],
)
def test_foreign_pages_get_no_token(local, fake_claude, headers):
    response = local.post("/api/claude/sessions", json={}, headers=headers)
    assert response.status_code == 403


def test_needs_an_open_investigation(fake_claude):
    with TestClient(create_app(), base_url=APP, client=("127.0.0.1", 50000)) as client:
        assert client.post("/api/claude/sessions", json={}, headers=ORIGIN).status_code == 409


def _refused(client, url: str, headers: dict) -> int:
    with pytest.raises(WebSocketDisconnect) as refused:
        with client.websocket_connect(url, headers=headers):
            pass
    return refused.value.code


def test_terminal_refuses_without_valid_token(local, fake_claude):
    assert _refused(local, WS + "/api/claude/terminal", ORIGIN) == 1008
    assert _refused(local, WS + "/api/claude/terminal?token=made-up", ORIGIN) == 1008
    token = _token(local, headers=ORIGIN)
    # A page from another site cannot use a token, and the token is spent by trying.
    assert _refused(
        local, f"{WS}/api/claude/terminal?token={token}", {"origin": "http://x.example"}
    )
    assert _refused(local, f"{WS}/api/claude/terminal?token={token}", ORIGIN) == 1008


def test_token_expires(local, fake_claude, monkeypatch):
    token = _token(local, headers=ORIGIN)
    later = time.monotonic() + 61
    monkeypatch.setattr("chembook3d.api.claude.time.monotonic", lambda: later)
    assert _refused(local, f"{WS}/api/claude/terminal?token={token}", ORIGIN) == 1008


def test_runs_claude_in_the_investigation_folder(local, fake_claude, config_dir, tmp_path):
    token = _token(local, headers=ORIGIN)
    url = f"{WS}/api/claude/terminal?token={token}&rows=30&cols=101"
    with local.websocket_connect(url, headers=ORIGIN) as ws:
        _read_until(ws, "Fake Claude ready")
        ws.send_text(json.dumps({"type": "input", "data": "hello notebook\r"}))
        _read_until(ws, "Fake Claude got: hello notebook")
        ws.send_text("not json")  # ignored
        ws.send_text(json.dumps({"type": "input", "data": "exit\r"}))
        assert _exit_code(ws) == 3

    work = claude_panel.workspace(tmp_path / "inv")
    assert work.parent == config_dir / "claude"
    assert work.name.startswith("inv-")
    started = json.loads((work / "fake-claude.json").read_text("utf-8"))
    assert Path(started["cwd"]).resolve() == work.resolve()
    assert (started["rows"], started["cols"]) == (30, 101)
    assert started["args"] == [
        "--mcp-config",
        "chembook3d-mcp.json",
        "--strict-mcp-config",
        "--settings",
        "chembook3d-settings.json",
    ]
    settings = json.loads((work / "chembook3d-settings.json").read_text("utf-8"))
    for tool in ("Bash", "PowerShell", "Edit", "Write", "WebFetch", "WebSearch"):
        assert tool in settings["permissions"]["deny"]
    assert "Metathesis" in (work / "CLAUDE.md").read_text("utf-8")
    assert not local.app.state.claude_panel.terminals


def test_continue_last_conversation(local, fake_claude, tmp_path):
    token = _token(local, resume=True, headers=ORIGIN)
    with local.websocket_connect(f"{WS}/api/claude/terminal?token={token}", headers=ORIGIN) as ws:
        _read_until(ws, "Fake Claude ready")
        ws.send_text(json.dumps({"type": "input", "data": "exit\r"}))
        assert _exit_code(ws) == 3
    work = claude_panel.workspace(tmp_path / "inv")
    started = json.loads((work / "fake-claude.json").read_text("utf-8"))
    assert started["args"][-1] == "--continue"


def test_closing_the_panel_stops_claude(local, fake_claude):
    token = _token(local, headers=ORIGIN)
    with local.websocket_connect(f"{WS}/api/claude/terminal?token={token}", headers=ORIGIN) as ws:
        _read_until(ws, "Fake Claude ready")
        (terminal,) = local.app.state.claude_panel.terminals
    deadline = time.monotonic() + 20
    while terminal.exit_code is None and time.monotonic() < deadline:
        time.sleep(0.05)
    assert terminal.exit_code is not None  # stopped, not left running
    assert not local.app.state.claude_panel.terminals


def test_terminal_stops_on_close(fake_claude, tmp_path):
    ended = threading.Event()
    output: list[str] = []

    def on_output(text):
        if text is None:
            ended.set()
        else:
            output.append(text)

    terminal = claude_panel.Terminal(
        claude_panel.command(str(fake_claude), resume=False), tmp_path, 24, 80, on_output
    )
    deadline = time.monotonic() + 20
    while "ready" not in "".join(output) and time.monotonic() < deadline:
        time.sleep(0.05)
    terminal.resize(40, 120)
    terminal.close()
    assert ended.wait(20)
    terminal.write("ignored after closing\r")


def test_notebook_tools_when_the_server_exists(monkeypatch, tmp_path):
    """With the chembook3d MCP server (D91) in the app, it is the only server, started with
    the app's own Python against this app, and its read tools are pre-approved."""
    server = {"type": "stdio", "command": sys.executable, "args": ["-m", "chembook3d.cli", "mcp"]}
    monkeypatch.setattr(claude_panel, "notebook_server", lambda: (dict(server), ["get_node"]))
    work = claude_panel.prepare(tmp_path / "inv", "Test", "http://127.0.0.1:8765")
    config = json.loads((work / "chembook3d-mcp.json").read_text("utf-8"))
    assert config["mcpServers"]["chembook3d"]["args"][-2:] == ["--url", "http://127.0.0.1:8765"]
    settings = json.loads((work / "chembook3d-settings.json").read_text("utf-8"))
    assert settings["permissions"]["allow"] == ["mcp__chembook3d__get_node"]


def test_without_notebook_tools(monkeypatch, tmp_path):
    monkeypatch.setattr(claude_panel, "notebook_server", lambda: None)
    work = claude_panel.prepare(tmp_path / "inv", "Test", "http://127.0.0.1:8765")
    assert json.loads((work / "chembook3d-mcp.json").read_text("utf-8")) == {"mcpServers": {}}


def test_workspace_differs_per_folder(tmp_path):
    first = claude_panel.workspace(tmp_path / "a" / "inv")
    second = claude_panel.workspace(tmp_path / "b" / "inv")
    assert first != second and first.name.startswith("inv-")
