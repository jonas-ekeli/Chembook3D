"""The keeper (D122): a small background process on 127.0.0.1 that holds the SSH connections
to servers such as Saga and one shell on each, so they outlive the app. The app starts it at
the first login (`client.py`) and finds it again after a restart through `remote/keeper.json`
in the config folder, which names its port and a random token and is readable by the user only.

The login answers the server's own prompts (keyboard-interactive or password authentication)
with what the user types in the app. The answers are passed on once and kept nowhere: not on
disk, not in the log. A refused login is reported and never tried again (Sigma2 blocks
addresses that keep failing); a prompt the server repeats after it was answered counts as
refused. SSH keys and agents are not tried (A69).

Protocol: one JSON object per line over TCP. A client's first line carries the token and the
operation; the keeper answers with one line and closes, except for `attach`, which streams a
shell's output and takes its input until either side closes.
"""

import argparse
import asyncio
import codecs
import contextlib
import hmac
import json
import logging
import os
import secrets
import sys
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path

import asyncssh

PROTOCOL = 1
KEEPER_FILE = "keeper.json"
KNOWN_HOSTS = "known_hosts"
LOG_FILE = "keeper.log"
LINE_LIMIT = 16 * 1024 * 1024  # longest line of the protocol (a shell's kept output)
TERM = "xterm-256color"

# A69 defaults.
KEEPALIVE_INTERVAL = 60  # seconds between keepalives
KEEPALIVE_MISSES = 3  # unanswered keepalives before the connection counts as lost
IDLE_LIMIT = 12 * 3600  # log out after this long with nothing typed and no file copied
PROMPT_WAIT = 300  # give up a login left at a prompt this long
OUTPUT_KEEP = 1_000_000  # characters of a shell's output kept to show again
CONNECT_TIMEOUT = 30
EVENT_WAIT = 60  # an operation waits this long for the login's next step, then says "waiting"
EMPTY_GRACE = 60  # the keeper ends this long after its last connection and login are gone
WATCH_EVERY = 5

# The shell (D122d): bash run as an SSH login runs it (the system's and the user's login
# files), plus a prompt hook reporting the current directory after each command with an OSC
# 1337 CurrentDir sequence, which terminals that do not know it ignore. One line without "!",
# so a login shell other than bash (tcsh) passes it on unchanged.
RC = (
    "[ -r /etc/profile ] && . /etc/profile; "
    "if [ -r ~/.bash_profile ]; then . ~/.bash_profile; "
    "elif [ -r ~/.bash_login ]; then . ~/.bash_login; "
    "elif [ -r ~/.profile ]; then . ~/.profile; fi; "
    "__chembook3d_cwd() { printf '\\033]1337;CurrentDir=%s\\007' \"$PWD\"; }; "
    'PROMPT_COMMAND="__chembook3d_cwd${PROMPT_COMMAND:+;$PROMPT_COMMAND}"'
)
CWD_MARK = "\x1b]1337;CurrentDir="
FALLBACK_WINDOW = 5.0  # a tracked shell ending this soon with "not found" falls back (A69)

log = logging.getLogger("chembook3d.keeper")


def _quote(text: str) -> str:
    return "'" + text.replace("'", "'\"'\"'") + "'"


SHELL_COMMAND = (
    "exec bash -c "
    + _quote('exec bash --rcfile <(printf "%s\\n" "$1") -i')
    + " chembook3d "
    + _quote(RC)
)


def host_pattern(host: str, port: int) -> str:
    """How known_hosts names a host: the bare name on port 22, else [host]:port."""
    return host if port == 22 else f"[{host}]:{port}"


def clean_server(data: object) -> dict | None:
    """The server a login is for, as the app sends it, or None."""
    if not isinstance(data, dict):
        return None
    server = {
        "id": data.get("id"),
        "name": data.get("name"),
        "host": data.get("host"),
        "port": data.get("port"),
        "username": data.get("username"),
    }
    if not all(
        isinstance(server[key], str) and server[key] for key in ("id", "name", "host", "username")
    ):
        return None
    port = server["port"]
    if not isinstance(port, int) or isinstance(port, bool) or not 0 < port < 65536:
        return None
    return server


# ---------- a shell and what it printed ----------


class Shell:
    """One interactive shell on a server: its recent output, its current directory and the
    terminals attached to it."""

    def __init__(self, process: asyncssh.SSHClientProcess, tracked: bool):
        self.process = process
        self.tracked = tracked  # the current directory is reported (D122d)
        self.started = time.monotonic()
        self.chunks: deque[str] = deque()
        self.kept = 0
        self.cwd: str | None = None
        self.viewers: set[asyncio.Queue] = set()
        self.ended: str | None = None  # why it ended
        self._carry = ""
        self._decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")

    def feed(self, data: bytes) -> None:
        text = self._decoder.decode(data)
        if not text:
            return
        cwd = self._track(text)
        self.chunks.append(text)
        self.kept += len(text)
        while self.kept > OUTPUT_KEEP and len(self.chunks) > 1:
            self.kept -= len(self.chunks.popleft())
        self._send({"type": "output", "data": text})
        if cwd is not None and cwd != self.cwd:
            self.cwd = cwd
            self._send({"type": "cwd", "path": cwd})

    def _track(self, text: str) -> str | None:
        """The last directory reported in `text`, joined to what an earlier piece cut short."""
        scan = self._carry + text
        found = None
        position = 0
        while True:
            start = scan.find(CWD_MARK, position)
            if start < 0:
                # The start of a report may be split off at the end.
                tail = scan[max(position, len(scan) - len(CWD_MARK) + 1) :]
                escape = tail.find("\x1b")
                self._carry = tail[escape:] if escape >= 0 else ""
                return found
            end = scan.find("\x07", start + len(CWD_MARK))
            if end < 0:
                self._carry = scan[start:] if len(scan) - start <= 8192 else ""
                return found
            found = scan[start + len(CWD_MARK) : end]
            position = end + 1

    def replay(self) -> str:
        return "".join(self.chunks)

    def write(self, text: str) -> None:
        if self.ended is None:
            with contextlib.suppress(OSError, asyncssh.Error, BrokenPipeError):
                self.process.stdin.write(text.encode("utf-8"))

    def resize(self, rows: int, cols: int) -> None:
        if self.ended is None:
            with contextlib.suppress(OSError, asyncssh.Error):
                self.process.change_terminal_size(max(10, min(cols, 1000)), max(2, min(rows, 500)))

    def end(self, message: str) -> None:
        if self.ended is not None:
            return
        self.ended = message
        self._send({"type": "exit", "message": message})

    def _send(self, message: dict) -> None:
        for queue in self.viewers:
            queue.put_nowait(message)


@dataclass
class Session:
    server: dict
    conn: asyncssh.SSHClientConnection
    since: float  # time.time() of the login
    used: float  # time.monotonic() of the last input or file copy (A69 idle time)
    shell: Shell | None = None
    closing: str | None = None  # why the keeper is closing it


# ---------- a login ----------


@dataclass
class Login:
    keeper: "Keeper"
    server: dict
    rows: int
    cols: int
    id: str = field(default_factory=lambda: secrets.token_urlsafe(12))
    events: asyncio.Queue = field(default_factory=asyncio.Queue)
    answers: asyncio.Queue = field(default_factory=asyncio.Queue)
    seen: set = field(default_factory=set)
    answered: int = 0
    refused: bool = False
    banner: str = ""
    host_key: asyncssh.SSHKey | None = None
    changed: bool = False
    task: asyncio.Task | None = None

    def start(self) -> None:
        self.host_key = None
        self.changed = False
        self.task = asyncio.create_task(self._run())

    async def next(self) -> dict:
        try:
            return await asyncio.wait_for(self.events.get(), EVENT_WAIT)
        except TimeoutError:
            return {"ok": True, "kind": "waiting", "login": self.id}

    async def ask(
        self, name: str, instructions: str, prompts: list[tuple[str, bool]]
    ) -> list[str] | None:
        """The user's answers to the server's prompts, or None to give up."""
        key = tuple(prompts)
        if key in self.seen:
            # The server asks again what was answered: it refused the answers. The app never
            # answers twice by itself (D122b).
            self.refused = True
            return None
        self.seen.add(key)
        self.events.put_nowait(
            {
                "ok": True,
                "kind": "prompts",
                "login": self.id,
                "name": name,
                "instructions": instructions,
                "banner": self.banner,
                "prompts": [{"text": text, "echo": bool(echo)} for text, echo in prompts],
            }
        )
        self.banner = ""
        try:
            answers = await asyncio.wait_for(self.answers.get(), PROMPT_WAIT)
        except TimeoutError:
            answers = None
        if not isinstance(answers, list) or len(answers) != len(prompts):
            return None
        self.answered += 1
        return answers

    def _event(self, kind: str, **fields) -> None:
        self.events.put_nowait({"ok": True, "kind": kind, "login": self.id, **fields})

    async def _run(self) -> None:
        keeper, server = self.keeper, self.server
        host, port = server["host"], server["port"]
        pending = False
        conn = None
        try:
            conn, client = await asyncssh.create_connection(
                lambda: _Client(keeper, self),
                host,
                port,
                username=server["username"],
                known_hosts=keeper.known_hosts,
                config=None,  # the user's ~/.ssh/config is not read; the app's settings rule
                client_keys=None,  # A69: no keys, so one login is one password-and-code try
                agent_path=None,
                preferred_auth="keyboard-interactive,password",
                keepalive_interval=KEEPALIVE_INTERVAL,
                keepalive_count_max=KEEPALIVE_MISSES,
                connect_timeout=CONNECT_TIMEOUT,
                login_timeout=PROMPT_WAIT * 3,
            )
            session = Session(server=server, conn=conn, since=time.time(), used=time.monotonic())
            client.session = session
            await keeper.open_shell(session, self.rows, self.cols)
            keeper.add(session)
            conn = None
            log.info("logged in to %s as %s", host, server["username"])
            self._event("connected")
        except asyncssh.HostKeyNotVerifiable:
            if self.host_key is not None and not self.changed:
                pending = True
                self._event(
                    "host_key",
                    host=host,
                    port=port,
                    algorithm=self.host_key.get_algorithm(),
                    fingerprint=self.host_key.get_fingerprint("sha256"),
                )
            else:
                self._event(
                    "failed",
                    message=f"The host key of {host} is not the one trusted before. This can mean "
                    "the server was reinstalled, or that someone is in the middle of the "
                    "connection. Check the key with the server's administrators; the app will "
                    "not connect until the old key is removed from known_hosts.",
                )
        except asyncssh.PermissionDenied:
            log.info("login to %s refused", host)
            if self.answered or self.refused:
                message = (
                    f"{host} refused the login. Check the password and the code, then log in "
                    "again; the app does not try again by itself."
                )
            else:
                message = f"The login to {host} stopped before it was finished."
            self._event("failed", message=message)
        except asyncio.CancelledError:
            raise
        except (OSError, asyncssh.Error, TimeoutError, ValueError) as exc:
            log.info("could not connect to %s: %s", host, type(exc).__name__)
            self._event("failed", message=f"Could not connect to {host}: {_reason(exc)}")
        finally:
            if conn is not None:
                conn.close()
            if not pending:
                keeper.logins.pop(self.id, None)

    def trust(self) -> None:
        """Save the unknown host key the user accepted, and connect again."""
        if self.host_key is None or self.changed:
            raise ValueError("No new host key to trust.")
        self.keeper.trust(self.server["host"], self.server["port"], self.host_key)
        self.start()

    def cancel(self) -> None:
        if self.task is not None:
            self.task.cancel()
        self.keeper.logins.pop(self.id, None)


def _reason(exc: BaseException) -> str:
    if isinstance(exc, TimeoutError):
        return "no answer in time."
    if isinstance(exc, asyncssh.DisconnectError):
        return exc.reason or type(exc).__name__
    return str(exc) or type(exc).__name__


class _Client(asyncssh.SSHClient):
    def __init__(self, keeper: "Keeper", login: Login):
        self._keeper = keeper
        self._login = login
        self.session: Session | None = None

    def validate_host_public_key(
        self, host: str, addr: str, port: int, key: asyncssh.SSHKey
    ) -> bool:
        # Called only for a key known_hosts does not list for this host.
        self._login.host_key = key
        self._login.changed = self._keeper.knows(host, addr, port)
        return False

    def auth_banner_received(self, msg: str, lang: str) -> None:
        self._login.banner += msg

    def kbdint_auth_requested(self) -> str | None:
        return ""  # the server picks what to ask

    async def kbdint_challenge_received(self, name, instructions, lang, prompts):
        if not prompts:
            return []
        return await self._login.ask(name, instructions, list(prompts))

    async def password_auth_requested(self) -> str | None:
        if self._login.answered or self._login.refused:
            return None  # one try only: the answers were given once already
        answers = await self._login.ask("", "", [("Password:", False)])
        return answers[0] if answers else None

    def connection_lost(self, exc: Exception | None) -> None:
        if self.session is not None:
            self._keeper.dropped(self.session, exc)


# ---------- the keeper ----------


class Keeper:
    def __init__(self, folder: Path, grace: float = EMPTY_GRACE, idle: float = IDLE_LIMIT):
        self.folder = folder
        self.grace = grace
        self.idle = idle
        self.token = secrets.token_urlsafe(32)
        self.sessions: dict[str, Session] = {}
        self.logins: dict[str, Login] = {}
        self.lost: dict[str, str] = {}  # server id → why its connection ended
        self.stopping = asyncio.Event()
        self.handlers: set[asyncio.Task] = set()  # open connections from the app

    # known hosts (D122b)

    def _host_lists(self) -> list[asyncssh.SSHKnownHosts]:
        lists = []
        for path in (Path.home() / ".ssh" / "known_hosts", self.folder / KNOWN_HOSTS):
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            known = asyncssh.SSHKnownHosts()
            for line in text.splitlines():
                with contextlib.suppress(ValueError, asyncssh.KeyImportError):
                    known.load(line + "\n")
            lists.append(known)
        return lists

    def known_hosts(self, host: str, addr: str, port: int | None):
        keys: list = []
        cas: list = []
        revoked: list = []
        for known in self._host_lists():
            found = known.match(host, addr, port)
            keys.extend(found[0])
            cas.extend(found[1])
            revoked.extend(found[2])
        return keys, cas, revoked

    def knows(self, host: str, addr: str, port: int | None) -> bool:
        keys, cas, _ = self.known_hosts(host, addr, port)
        return bool(keys or cas)

    def trust(self, host: str, port: int, key: asyncssh.SSHKey) -> None:
        line = (
            host_pattern(host, port)
            + " "
            + " ".join(key.export_public_key("openssh").decode().split()[:2])
        )
        path = self.folder / KNOWN_HOSTS
        with path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(line + "\n")
        log.info("trusted the %s host key of %s", key.get_algorithm(), host_pattern(host, port))

    # sessions

    def add(self, session: Session) -> None:
        old = self.sessions.get(session.server["id"])
        if old is not None:
            self.close(old, "Logged in again.")
        self.sessions[session.server["id"]] = session
        self.lost.pop(session.server["id"], None)

    def close(self, session: Session, message: str) -> None:
        session.closing = message
        if self.sessions.get(session.server["id"]) is session:
            del self.sessions[session.server["id"]]
        if session.shell is not None:
            session.shell.end(message)
        session.conn.close()

    def dropped(self, session: Session, exc: Exception | None) -> None:
        if session.closing is not None:
            return
        name = session.server["name"]
        message = (
            f"The connection to {name} was lost"
            + (f" ({_reason(exc)})" if exc else "")
            + ". Log in again."
        )
        log.info("connection to %s lost", session.server["host"])
        session.closing = message
        if self.sessions.get(session.server["id"]) is session:
            del self.sessions[session.server["id"]]
        self.lost[session.server["id"]] = message
        if session.shell is not None:
            session.shell.end(message)

    async def open_shell(
        self, session: Session, rows: int, cols: int, tracked: bool = True
    ) -> None:
        process = await session.conn.create_process(
            SHELL_COMMAND if tracked else None,
            term_type=TERM,
            term_size=(max(10, cols), max(2, rows)),
            encoding=None,
            stderr=asyncssh.STDOUT,
        )
        shell = Shell(process, tracked)
        previous = session.shell
        session.shell = shell
        if previous is not None and previous.ended is None:
            previous.end("A new shell was started.")
        asyncio.create_task(self._pump(session, shell, rows, cols))

    async def _pump(self, session: Session, shell: Shell, rows: int, cols: int) -> None:
        with contextlib.suppress(OSError, asyncssh.Error, asyncio.IncompleteReadError):
            while True:
                data = await shell.process.stdout.read(65536)
                if not data:
                    break
                shell.feed(data)
        if session.closing is not None:
            return
        with contextlib.suppress(OSError, asyncssh.Error, TimeoutError):
            await asyncio.wait_for(shell.process.wait_closed(), 5)
        status = shell.process.exit_status
        if (
            shell.tracked
            and shell.cwd is None
            and status in (126, 127)
            and time.monotonic() - shell.started < FALLBACK_WINDOW
        ):
            # No bash there: the login shell, without the current directory (A69).
            output = shell.replay()
            try:
                await self.open_shell(session, rows, cols, tracked=False)
            except (OSError, asyncssh.Error):
                shell.end("The shell on the server could not be started.")
                return
            if session.shell is not None and output:
                session.shell.feed(output.encode("utf-8"))
            return
        shell.end("The shell on the server has ended. Start a new one, or log out.")

    # what the app asks

    async def dispatch(self, op: str, msg: dict) -> dict:
        if op == "ping":
            return {"ok": True, "protocol": PROTOCOL, "pid": os.getpid()}
        if op == "status":
            return {
                "ok": True,
                "protocol": PROTOCOL,
                "sessions": self._status(),
                "lost": dict(self.lost),
            }
        if op == "login":
            server = clean_server(msg.get("server"))
            if server is None:
                return {"ok": False, "error": "The server is not described completely."}
            session = self.sessions.get(server["id"])
            if session is not None:
                return {"ok": True, "kind": "connected", "login": None}
            for login in [x for x in self.logins.values() if x.server["id"] == server["id"]]:
                login.cancel()
            login = Login(self, server, _int(msg.get("rows"), 24), _int(msg.get("cols"), 80))
            self.logins[login.id] = login
            self.lost.pop(server["id"], None)
            login.start()
            return await login.next()
        if op in ("answer", "next", "trust", "cancel"):
            login = self.logins.get(str(msg.get("login")))
            if login is None:
                return {"ok": False, "error": "That login is no longer waiting. Log in again."}
            if op == "cancel":
                login.cancel()
                return {"ok": True}
            if op == "answer":
                answers = msg.get("answers")
                if not isinstance(answers, list) or not all(isinstance(a, str) for a in answers):
                    return {"ok": False, "error": "The answers must be text."}
                login.answers.put_nowait(answers)
            elif op == "trust":
                try:
                    login.trust()
                except (ValueError, OSError) as exc:
                    return {"ok": False, "error": str(exc)}
            return await login.next()
        if op == "logout":
            session = self.sessions.get(str(msg.get("server")))
            if session is not None:
                log.info("logged out of %s", session.server["host"])
                self.close(session, "Logged out.")
            self.lost.pop(str(msg.get("server")), None)
            return {"ok": True}
        if op == "shell":
            session = self.sessions.get(str(msg.get("server")))
            if session is None:
                return {"ok": False, "error": "Not logged in."}
            if session.shell is None or session.shell.ended is not None:
                try:
                    await self.open_shell(
                        session, _int(msg.get("rows"), 24), _int(msg.get("cols"), 80)
                    )
                except (OSError, asyncssh.Error) as exc:
                    return {"ok": False, "error": f"Could not start a shell: {_reason(exc)}"}
            return {"ok": True}
        if op == "quit":
            self.stopping.set()
            return {"ok": True}
        return {"ok": False, "error": f"Unknown operation {op!r}."}

    def _status(self) -> list[dict]:
        return [
            {
                "server": session.server,
                "since": session.since,
                "cwd": session.shell.cwd if session.shell else None,
                "tracked": bool(session.shell and session.shell.tracked),
                "shell": "ended" if session.shell is None or session.shell.ended else "running",
            }
            for session in self.sessions.values()
        ]

    async def attach(
        self, msg: dict, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        """Stream a shell to the app: first what it printed recently, then everything new."""
        session = self.sessions.get(str(msg.get("server")))
        shell = session.shell if session is not None else None
        if shell is None:
            message = self.lost.get(str(msg.get("server")), "Not logged in.")
            await _write(writer, {"type": "exit", "message": message})
            return
        queue: asyncio.Queue = asyncio.Queue()
        await _write(writer, {"type": "hello", "cwd": shell.cwd, "tracked": shell.tracked})
        replay = shell.replay()
        if replay:
            await _write(writer, {"type": "output", "data": replay})
        if shell.ended is not None:
            await _write(writer, {"type": "exit", "message": shell.ended})
            return
        shell.viewers.add(queue)
        shell.resize(_int(msg.get("rows"), 24), _int(msg.get("cols"), 80))

        async def send() -> None:
            while True:
                message = await queue.get()
                await _write(writer, message)
                if message["type"] == "exit":
                    return

        async def receive() -> None:
            while True:
                line = await reader.readline()
                if not line:
                    return
                try:
                    message = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(message, dict):
                    continue
                if message.get("type") == "input" and isinstance(message.get("data"), str):
                    session.used = time.monotonic()
                    shell.write(message["data"])
                elif message.get("type") == "resize":
                    rows, cols = message.get("rows"), message.get("cols")
                    if isinstance(rows, int) and isinstance(cols, int):
                        shell.resize(rows, cols)

        tasks = {asyncio.create_task(send()), asyncio.create_task(receive())}
        try:
            await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        finally:
            shell.viewers.discard(queue)
            for task in tasks:
                task.cancel()
            for task in tasks:
                with contextlib.suppress(asyncio.CancelledError, OSError, ConnectionError):
                    await task

    async def handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        task = asyncio.current_task()
        if task is not None:
            self.handlers.add(task)
            task.add_done_callback(self.handlers.discard)
        try:
            line = await asyncio.wait_for(reader.readline(), 10)
            msg = json.loads(line)
            if not isinstance(msg, dict):
                return
            token = str(msg.get("token", "")).encode("utf-8")
            if not hmac.compare_digest(token, self.token.encode("utf-8")):
                return
            op = str(msg.get("op", ""))
            if op == "attach":
                await self.attach(msg, reader, writer)
            else:
                await _write(writer, await self.dispatch(op, msg))
        except (ValueError, TimeoutError, OSError, ConnectionError, asyncio.IncompleteReadError):
            pass
        finally:
            writer.close()
            with contextlib.suppress(OSError, ConnectionError):
                await writer.wait_closed()

    async def watch(self) -> None:
        """Log out what has not been used for the idle time (A69), and end the keeper once
        nothing is left."""
        empty_since = time.monotonic()
        while not self.stopping.is_set():
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self.stopping.wait(), WATCH_EVERY)
            now = time.monotonic()
            for session in list(self.sessions.values()):
                if now - session.used > self.idle:
                    log.info("logging out of %s after the idle time", session.server["host"])
                    self.close(session, f"Logged out after {self.idle / 3600:g} hours without use.")
            if self.sessions or self.logins:
                empty_since = now
            elif now - empty_since > self.grace:
                self.stopping.set()

    def stop_all(self) -> None:
        for login in list(self.logins.values()):
            login.cancel()
        for session in list(self.sessions.values()):
            self.close(session, "The keeper has stopped.")


def _int(value: object, default: int) -> int:
    return (
        value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else default
    )


async def _write(writer: asyncio.StreamWriter, message: dict) -> None:
    writer.write(json.dumps(message).encode("utf-8") + b"\n")
    await writer.drain()


def write_private(path: Path, text: str) -> None:
    """Write `text` so only this user can read it (POSIX mode 600; on Windows the config folder
    is the user's own)."""
    temporary = path.with_name(path.name + f".{os.getpid()}.tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        handle.write(text)
    os.replace(temporary, path)


async def serve(folder: Path, grace: float = EMPTY_GRACE, idle: float = IDLE_LIMIT) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    keeper = Keeper(folder, grace=grace, idle=idle)
    server = await asyncio.start_server(keeper.handle, "127.0.0.1", 0, limit=LINE_LIMIT)
    port = server.sockets[0].getsockname()[1]
    info = {"protocol": PROTOCOL, "port": port, "token": keeper.token, "pid": os.getpid()}
    path = folder / KEEPER_FILE
    write_private(path, json.dumps(info) + "\n")
    log.info("keeper %d listening on 127.0.0.1:%d", os.getpid(), port)
    watcher = asyncio.create_task(keeper.watch())
    try:
        await keeper.stopping.wait()
    finally:
        keeper.stop_all()
        server.close()
        watcher.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await watcher
        # Let the connection that asked to quit get its answer, then end the rest, so none is
        # left pending when the loop closes.
        if keeper.handlers:
            await asyncio.wait(set(keeper.handlers), timeout=2)
        for task in list(keeper.handlers):
            task.cancel()
        await asyncio.gather(*keeper.handlers, return_exceptions=True)
        with contextlib.suppress(OSError, ValueError):
            if json.loads(path.read_text(encoding="utf-8")).get("token") == keeper.token:
                path.unlink()
        log.info("keeper %d stopped", os.getpid())


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="python -m chembook3d.remote.keeper")
    parser.add_argument("--config", required=True, help="the app's config folder")
    parser.add_argument("--grace", type=float, default=EMPTY_GRACE)
    parser.add_argument("--idle", type=float, default=IDLE_LIMIT)
    args = parser.parse_args(argv)
    folder = Path(args.config) / "remote"
    folder.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        filename=folder / LOG_FILE,
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    logging.getLogger("asyncssh").setLevel(logging.WARNING)
    try:
        asyncio.run(serve(folder, grace=args.grace, idle=args.idle))
    except KeyboardInterrupt:
        pass
    sys.exit(0)


if __name__ == "__main__":
    main()
