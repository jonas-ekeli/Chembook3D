"""HTTP and WebSocket API of the Claude panel (D92). The terminal itself is in
`claude_panel.py`.

A terminal is new attack surface, so the WebSocket takes a connection only when all of these
hold: the app is bound to the loopback address, the request names a loopback host (no DNS
rebinding), the page comes from a loopback origin (WebSockets are not covered by the browser's
same-origin rule), and it carries a one-time token that only the app's own page can read
(`POST /api/claude/sessions`). The process it starts is always `claude`, never a shell.
"""

import asyncio
import contextlib
import json
import secrets
import time
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException, Request, WebSocket, WebSocketDisconnect
from pydantic import BaseModel
from starlette.datastructures import Headers

from chembook3d import claude_panel
from chembook3d.api.routes import _investigation

router = APIRouter(prefix="/api/claude")

LOOPBACK_ADDRESSES = {"127.0.0.1", "::1"}
LOOPBACK_NAMES = LOOPBACK_ADDRESSES | {"localhost"}
TOKEN_LIFETIME = 60.0  # seconds between asking for a terminal and connecting to it
MAX_TERMINALS = 4
# WebSocket close codes: policy violation, and "try again later" when too many are open.
POLICY = 1008
BUSY = 1013


@dataclass
class _Launch:
    expires: float
    resume: bool
    folder: Path
    name: str
    app_url: str


@dataclass
class Panel:
    """The panel's state on the app: launch tokens not yet used, and running terminals."""

    launches: dict[str, _Launch] = field(default_factory=dict)
    terminals: set[claude_panel.Terminal] = field(default_factory=set)

    def take(self, token: str) -> _Launch | None:
        now = time.monotonic()
        for key in [k for k, v in self.launches.items() if v.expires < now]:
            del self.launches[key]
        return self.launches.pop(token, None)

    def close_all(self) -> None:
        for terminal in list(self.terminals):
            terminal.close()
        self.terminals.clear()


def _panel(app) -> Panel:
    panel = getattr(app.state, "claude_panel", None)
    if panel is None:
        panel = app.state.claude_panel = Panel()
    return panel


def _hostname(host_header: str) -> str:
    return (urlsplit(f"//{host_header}").hostname or "").lower()


def _bound_locally(scope) -> bool:
    server = scope.get("server")
    return bool(server) and server[0] in LOOPBACK_ADDRESSES


def _refusal(scope, headers: Headers) -> str | None:
    """Why a request may not use the panel, or None."""
    if not _bound_locally(scope):
        return "The Claude panel works only while the app listens on 127.0.0.1."
    client = scope.get("client")
    if not client or client[0] not in LOOPBACK_ADDRESSES:
        return "The Claude panel accepts connections from this computer only."
    if _hostname(headers.get("host", "")) not in LOOPBACK_NAMES:
        return "The Claude panel must be opened at 127.0.0.1 or localhost."
    origin = headers.get("origin")
    if origin is not None:
        parts = urlsplit(origin)
        if parts.scheme not in ("http", "https") or (parts.hostname or "") not in LOOPBACK_NAMES:
            return "The Claude panel accepts requests from the app's own page only."
    return None


class InstallHint(BaseModel):
    command: str
    docs: str


class PanelStatus(BaseModel):
    available: bool  # `claude` was found
    enabled: bool  # the panel may be used from this page
    reason: str | None = None  # why not, when it may not
    notebook_tools: bool  # the chembook3d MCP server (D91) is part of this app
    install: InstallHint


@router.get("")
def panel_status(request: Request) -> PanelStatus:
    refusal = _refusal(request.scope, request.headers)
    return PanelStatus(
        available=claude_panel.find_claude() is not None,
        enabled=refusal is None,
        reason=refusal,
        notebook_tools=claude_panel.notebook_server() is not None,
        install=InstallHint(**claude_panel.install_hint()),
    )


class LaunchIn(BaseModel):
    resume: bool = False  # continue the investigation's last conversation (`claude --continue`)


class LaunchOut(BaseModel):
    token: str


@router.post("/sessions")
def new_session(body: LaunchIn, request: Request) -> LaunchOut:
    refusal = _refusal(request.scope, request.headers)
    if refusal is not None:
        raise HTTPException(403, refusal)
    investigation = _investigation(request)
    if claude_panel.find_claude() is None:
        raise HTTPException(409, "Claude Code is not installed on this computer.")
    token = secrets.token_urlsafe(32)
    port = request.scope["server"][1]
    _panel(request.app).launches[token] = _Launch(
        expires=time.monotonic() + TOKEN_LIFETIME,
        resume=body.resume,
        folder=investigation.folder,
        name=investigation.name,
        app_url=f"http://127.0.0.1:{port}",
    )
    return LaunchOut(token=token)


def _size(value: str | None, default: int) -> int:
    try:
        return int(value) if value is not None else default
    except ValueError:
        return default


@router.websocket("/terminal")
async def terminal(websocket: WebSocket) -> None:
    """Messages from the page: {"type": "input", "data": text} and
    {"type": "resize", "rows": n, "cols": n}. To the page: {"type": "output", "data": text},
    then {"type": "exit", "code": n} when Claude Code has ended."""
    panel = _panel(websocket.app)
    launch = panel.take(websocket.query_params.get("token", ""))  # spent by any attempt
    if launch is None or _refusal(websocket.scope, websocket.headers) is not None:
        await websocket.close(code=POLICY)
        return
    if len(panel.terminals) >= MAX_TERMINALS:
        await websocket.close(code=BUSY)
        return
    executable = claude_panel.find_claude()
    if executable is None:
        await websocket.close(code=POLICY)
        return
    await websocket.accept()

    loop = asyncio.get_running_loop()
    queue: asyncio.Queue[str | None] = asyncio.Queue()

    def on_output(text: str | None) -> None:
        loop.call_soon_threadsafe(queue.put_nowait, text)

    try:
        work = claude_panel.prepare(launch.folder, launch.name, launch.app_url)
        process = claude_panel.Terminal(
            claude_panel.command(executable, launch.resume),
            work,
            rows=_size(websocket.query_params.get("rows"), 24),
            cols=_size(websocket.query_params.get("cols"), 80),
            on_output=on_output,
        )
    except OSError as exc:
        await websocket.send_json({"type": "output", "data": f"Could not start Claude Code: {exc}"})
        await websocket.send_json({"type": "exit", "code": None})
        await websocket.close()
        return
    panel.terminals.add(process)

    async def send_output() -> None:
        while True:
            text = await queue.get()
            # Join what has piled up, so a burst of output is one message.
            chunks: list[str] = []
            while text is not None:
                chunks.append(text)
                if queue.empty():
                    break
                text = queue.get_nowait()
            if chunks:
                await websocket.send_json({"type": "output", "data": "".join(chunks)})
            if text is None:
                await websocket.send_json({"type": "exit", "code": process.exit_code})
                return

    async def receive_input() -> None:
        while True:
            try:
                message = json.loads(await websocket.receive_text())
            except ValueError:
                continue
            if not isinstance(message, dict):
                continue
            if message.get("type") == "input" and isinstance(message.get("data"), str):
                process.write(message["data"])
            elif message.get("type") == "resize":
                rows, cols = message.get("rows"), message.get("cols")
                if isinstance(rows, int) and isinstance(cols, int):
                    process.resize(rows, cols)

    sender = asyncio.create_task(send_output())
    receiver = asyncio.create_task(receive_input())
    try:
        # Claude Code ended (the sender told the page), or the page went away.
        await asyncio.wait({sender, receiver}, return_when=asyncio.FIRST_COMPLETED)
    finally:
        for task in (sender, receiver):
            task.cancel()
        panel.terminals.discard(process)
        # Stopping can wait a moment for the program to exit, so not on the event loop; and it
        # goes on even if this handler is cancelled (the server shutting down).
        await asyncio.shield(asyncio.to_thread(process.close))
        for task in (sender, receiver):
            with contextlib.suppress(asyncio.CancelledError, WebSocketDisconnect, RuntimeError):
                await task
        with contextlib.suppress(RuntimeError, WebSocketDisconnect):  # the page may be gone
            await websocket.close()
