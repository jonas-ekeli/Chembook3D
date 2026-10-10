"""HTTP and WebSocket API of the SSH servers (D122): the saved servers, logging in through the
keeper (`remote/keeper.py`), and the terminal on a server's shell.

Everything here has the Claude panel's checks (D92: the app on 127.0.0.1, a loopback client,
Host and Origin), and whatever changes something or carries a password needs the page's
Origin, so only the app's own page can log in: Claude's MCP server sends none. The terminal's
WebSocket takes a one-time token as the Claude panel's does.
"""

import asyncio
import contextlib
import json
import secrets
import time
from dataclasses import dataclass, field

from fastapi import APIRouter, HTTPException, Request, WebSocket, WebSocketDisconnect
from pydantic import BaseModel

from chembook3d.api.claude import BUSY, POLICY, TOKEN_LIFETIME, _refusal, _size
from chembook3d.remote import client, servers

router = APIRouter(prefix="/api/remote")

MAX_TERMINALS = 8


@dataclass
class _Terminal:
    expires: float
    server_id: str


@dataclass
class Remote:
    """One-time tokens for terminals not yet connected, and how many are open."""

    tokens: dict[str, _Terminal] = field(default_factory=dict)
    open: int = 0

    def take(self, token: str) -> _Terminal | None:
        now = time.monotonic()
        for key in [k for k, v in self.tokens.items() if v.expires < now]:
            del self.tokens[key]
        return self.tokens.pop(token, None)


def _remote(app) -> Remote:
    remote = getattr(app.state, "remote", None)
    if remote is None:
        remote = app.state.remote = Remote()
    return remote


def _check(request: Request, page_only: bool = True) -> None:
    refusal = _refusal(request.scope, request.headers)
    if refusal is None and page_only and request.headers.get("origin") is None:
        refusal = "Only the app's own page can use the server connection."
    if refusal is not None:
        raise HTTPException(403, refusal.replace("The Claude panel", "The server connection"))


class ServerIn(BaseModel):
    id: str | None = None
    name: str
    host: str
    port: int = 22
    username: str = ""


class ServerOut(BaseModel):
    id: str
    name: str
    host: str
    port: int
    username: str
    connected: bool = False
    since: float | None = None  # when the login happened (seconds since 1970)
    cwd: str | None = None  # the shell's current directory, when it reports it
    tracked: bool = False  # the shell reports its current directory (D122d)
    shell: str | None = None  # "running" or "ended" while connected
    lost: str | None = None  # why the last connection ended, when it was not a log out


class RemoteStatus(BaseModel):
    enabled: bool
    reason: str | None = None
    servers: list[ServerOut]


def _out(saved: list[servers.Server], held: dict) -> list[ServerOut]:
    sessions = {s["server"]["id"]: s for s in held.get("sessions", [])}
    lost = held.get("lost", {})
    result = []
    for server in saved:
        session = sessions.get(server.id)
        out = ServerOut(
            id=server.id,
            name=server.name,
            host=server.host,
            port=server.port,
            username=server.username,
        )
        if session is not None:
            out.connected = True
            out.since = session.get("since")
            out.cwd = session.get("cwd")
            out.tracked = bool(session.get("tracked"))
            out.shell = session.get("shell")
            # What the connection was opened with, even if the saved server changed since.
            out.host = session["server"]["host"]
            out.port = session["server"]["port"]
            out.username = session["server"]["username"]
        else:
            out.lost = lost.get(server.id)
        result.append(out)
    return result


@router.get("")
async def remote_status(request: Request) -> RemoteStatus:
    refusal = _refusal(request.scope, request.headers)
    if refusal is not None:
        return RemoteStatus(
            enabled=False,
            reason=refusal.replace("The Claude panel", "The server connection"),
            servers=[],
        )
    return RemoteStatus(enabled=True, servers=_out(servers.load(), await client.status()))


class ServersIn(BaseModel):
    servers: list[ServerIn]


@router.put("/servers")
async def save_servers(body: ServersIn, request: Request) -> list[ServerOut]:
    _check(request)
    try:
        saved = servers.replace([s.model_dump() for s in body.servers])
    except servers.ServerError as exc:
        raise HTTPException(422, str(exc)) from exc
    return _out(saved, await client.status())


class LoginIn(BaseModel):
    username: str | None = None  # saved with the server when given
    rows: int = 24
    cols: int = 80


class Prompt(BaseModel):
    text: str
    echo: bool  # False: hide what is typed (a password or code)


class LoginStep(BaseModel):
    """The next step of a login: the server's prompts, an unknown host key to trust, done,
    refused, or still waiting for the server."""

    kind: str  # prompts, host_key, connected, failed, waiting
    login: str | None = None
    name: str = ""
    instructions: str = ""
    banner: str = ""
    prompts: list[Prompt] = []
    host: str | None = None
    port: int | None = None
    algorithm: str | None = None
    fingerprint: str | None = None
    message: str | None = None


def _step(answer: dict) -> LoginStep:
    return LoginStep(
        **{key: value for key, value in answer.items() if key in LoginStep.model_fields}
    )


async def _keeper(op: str, **fields) -> dict:
    try:
        return await client.call(op, **fields)
    except client.KeeperError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.post("/servers/{server_id}/login")
async def login(server_id: str, body: LoginIn, request: Request) -> LoginStep:
    _check(request)
    saved = servers.load()
    server = next((s for s in saved if s.id == server_id), None)
    if server is None:
        raise HTTPException(404, "That server is not in the list.")
    if body.username is not None and body.username.strip() != server.username:
        server.username = body.username.strip()
        try:
            servers.clean({**server.__dict__})
        except servers.ServerError as exc:
            raise HTTPException(422, str(exc)) from exc
        servers.save(saved)
    if not server.username:
        raise HTTPException(422, f"Give your user name on {server.name}.")
    try:
        await client.ensure()
    except client.KeeperError as exc:
        raise HTTPException(500, str(exc)) from exc
    return _step(await _keeper("login", server=server.__dict__, rows=body.rows, cols=body.cols))


class AnswersIn(BaseModel):
    answers: list[str]


@router.post("/logins/{login_id}/answer")
async def answer(login_id: str, body: AnswersIn, request: Request) -> LoginStep:
    _check(request)
    return _step(await _keeper("answer", login=login_id, answers=body.answers))


@router.post("/logins/{login_id}/next")
async def next_step(login_id: str, request: Request) -> LoginStep:
    _check(request)
    return _step(await _keeper("next", login=login_id))


@router.post("/logins/{login_id}/trust")
async def trust(login_id: str, request: Request) -> LoginStep:
    _check(request)
    return _step(await _keeper("trust", login=login_id))


@router.delete("/logins/{login_id}")
async def cancel(login_id: str, request: Request) -> None:
    _check(request)
    with contextlib.suppress(client.KeeperError):
        await client.call("cancel", login=login_id)


@router.post("/servers/{server_id}/logout")
async def logout(server_id: str, request: Request) -> None:
    _check(request)
    with contextlib.suppress(client.KeeperError):
        await client.call("logout", server=server_id)


class ShellIn(BaseModel):
    rows: int = 24
    cols: int = 80


@router.post("/servers/{server_id}/shell")
async def new_shell(server_id: str, body: ShellIn, request: Request) -> None:
    """Start a new shell on a server still logged in, after the last one ended."""
    _check(request)
    await _keeper("shell", server=server_id, rows=body.rows, cols=body.cols)


class TerminalIn(BaseModel):
    server: str


class TerminalOut(BaseModel):
    token: str


@router.post("/terminals")
def new_terminal(body: TerminalIn, request: Request) -> TerminalOut:
    _check(request)
    token = secrets.token_urlsafe(32)
    _remote(request.app).tokens[token] = _Terminal(
        expires=time.monotonic() + TOKEN_LIFETIME, server_id=body.server
    )
    return TerminalOut(token=token)


@router.websocket("/terminal")
async def terminal(websocket: WebSocket) -> None:
    """The shell of a logged-in server. Messages as for the Claude panel's terminal, plus
    {"type": "cwd", "path": p} when the shell's current directory changes, and {"type":
    "exit", "message": why} when the shell or the connection has ended."""
    remote = _remote(websocket.app)
    ticket = remote.take(websocket.query_params.get("token", ""))  # spent by any attempt
    if ticket is None or _refusal(websocket.scope, websocket.headers) is not None:
        await websocket.close(code=POLICY)
        return
    if remote.open >= MAX_TERMINALS:
        await websocket.close(code=BUSY)
        return
    rows = _size(websocket.query_params.get("rows"), 24)
    cols = _size(websocket.query_params.get("cols"), 80)
    await websocket.accept()
    try:
        stream = await client.attach(ticket.server_id, rows, cols)
    except (client.KeeperError, OSError) as exc:
        await websocket.send_json({"type": "exit", "message": str(exc)})
        await websocket.close()
        return
    remote.open += 1

    async def from_keeper() -> None:
        while True:
            try:
                message = await stream.receive()
            except (ValueError, OSError, ConnectionError):
                message = None
            if message is None:
                await websocket.send_json(
                    {"type": "exit", "message": "The connection to the keeper was lost."}
                )
                return
            await websocket.send_json(message)
            if message.get("type") == "exit":
                return

    async def from_page() -> None:
        while True:
            try:
                message = json.loads(await websocket.receive_text())
            except ValueError:
                continue
            if not isinstance(message, dict):
                continue
            if message.get("type") == "input" and isinstance(message.get("data"), str):
                await stream.send({"type": "input", "data": message["data"]})
            elif message.get("type") == "resize":
                rows, cols = message.get("rows"), message.get("cols")
                if isinstance(rows, int) and isinstance(cols, int):
                    await stream.send({"type": "resize", "rows": rows, "cols": cols})

    tasks = {asyncio.create_task(from_keeper()), asyncio.create_task(from_page())}
    try:
        await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    finally:
        remote.open -= 1
        for task in tasks:
            task.cancel()
        for task in tasks:
            with contextlib.suppress(
                asyncio.CancelledError, WebSocketDisconnect, RuntimeError, OSError, ConnectionError
            ):
                await task
        await stream.close()  # the shell goes on in the keeper
        with contextlib.suppress(RuntimeError, WebSocketDisconnect):
            await websocket.close()
