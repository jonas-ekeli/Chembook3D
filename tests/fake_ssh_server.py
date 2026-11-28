"""A stand-in for Saga (D122): an SSH server on 127.0.0.1 that asks for a password and then a
one-time code by keyboard-interactive authentication, as Sigma2's servers do, and counts every
answer it gets. Its shell is a tiny one written here (prompt, cd, pwd, ls, echo, exit, and the
current directory reported after each command as bash with the keeper's hook does), so it
works on Windows too; `real_shell=True` runs the command the keeper sends in the real bash
instead (Linux only). Files live in `root`, which SFTP shows as `/`.

X11 forwarding (D123a) is allowed unless `x11=False`; the tiny shell's `xclient` connects to the
forwarded display as an X program would, and `FakeXServer` stands in for Xming on this side.

Run as a script for the UI tests: `python -m tests.fake_ssh_server --port 8767 --root <folder>`.
"""

import argparse
import asyncio
import base64
import contextlib
import fnmatch
import json
import os
import posixpath
import re
import socket
import socketserver
import subprocess
import sys
import threading
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path

import asyncssh
from asyncssh.x11 import walk_xauth

from chembook3d.remote import probe, saga_access, saga_prepare
from chembook3d.remote.job_pilot import LOCAL_WORK, SUBSYSTEM, JobPilot, signature
from tests.hpc_pilot_runner import checkpoint_ts_input
from tests.preopt_fixture import write_completed_preopt

USER = "jonas"
PASSWORD = "correct horse"
CODE = "123456"
HOME = "/cluster/home/jonas"
SAGA_FILE_OPERATIONS = {"list_files", "read_file", "create_xyz"}
SAGA_TS_OPERATIONS = {"prepare_ts", "submit_ts", "ts_status"}
SAGA_TS_MARKER = saga_prepare.TS_MARKER


@dataclass
class Record:
    """What the server saw."""

    answers: list[list[str]] = field(default_factory=list)  # every keyboard-interactive answer
    logins: int = 0  # successful logins
    commands: list[str | None] = field(default_factory=list)  # each session's command
    displays: list[str | None] = field(default_factory=list)  # each session's X11 display
    saga_prepare_requests: list[bytes] = field(default_factory=list)
    saga_minimum_requests: list[dict] = field(default_factory=list)
    saga_ts_requests: list[dict] = field(default_factory=list)
    saga_file_requests: list[dict] = field(default_factory=list)
    sftp_paths: list[str] = field(default_factory=list)


class _Server(asyncssh.SSHServer):
    def __init__(self, fake: "FakeSaga"):
        self._fake = fake
        self._step = 0

    def connection_made(self, conn):
        self._fake.connections.append(conn)

    def begin_auth(self, username: str) -> bool:
        return True

    def password_auth_supported(self) -> bool:
        return False

    def public_key_auth_supported(self) -> bool:
        return False

    def kbdint_auth_supported(self) -> bool:
        return True

    def get_kbdint_challenge(self, username, lang, submethods):
        self._step = 0
        return "", "", "", [("Password: ", False)]

    def validate_kbdint_response(self, username, responses):
        self._fake.record.answers.append(list(responses))
        self._step += 1
        if self._step == 1:
            if username == USER and list(responses) == [PASSWORD]:
                return "", "", "", [("Verification code: ", False)]
            # As PAM does: ask for the password again after a wrong one.
            self._step = 0
            return "", "", "", [("Password: ", False)]
        if list(responses) == [self._fake.code]:
            self._fake.record.logins += 1
            return True
        return False


class _SFTPServer(asyncssh.SFTPServer):
    def __init__(self, chan, fake: "FakeSaga"):
        super().__init__(chan, chroot=os.fsencode(fake.root))
        self._record = fake.record

    def map_path(self, path: bytes) -> bytes:
        self._record.sftp_paths.append(os.fsdecode(path))
        return super().map_path(path)

    def lstat(self, path):
        attrs = super().lstat(path)
        if sys.platform == "win32" and posixpath.basename(path) in {
            b".submit-minimum.json",
            SAGA_TS_MARKER.encode(),
        }:
            # A Linux Saga account exposes POSIX mode bits, including on this Windows fixture.
            attrs = asyncssh.SFTPAttrs.from_local(attrs)
            attrs.permissions &= ~0o077
        return attrs


class FakeSaga:
    def __init__(self, root: Path, real_shell: bool = False, port: int = 0, x11: bool = True):
        self.root = root
        self.real_shell = real_shell
        self.job_pilot = None
        self.drop_submit_reply = False
        self.saga_prepare_reply: dict | None = None
        self.saga_minimum: Callable[[dict], dict] | None = None
        self.saga_ts: Callable[[dict], dict] | None = None
        self.saga_file_error: str | None = None
        self.port = port
        self.x11 = x11
        self.xauthority = root / ".Xauthority"  # the cookies of the forwarded displays
        self.code = CODE
        self.record = Record()
        self.connections: list = []
        self.key = asyncssh.generate_private_key("ssh-ed25519")
        self._server = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        (root / HOME.lstrip("/")).mkdir(parents=True, exist_ok=True)

    @property
    def public_key(self) -> str:
        return self.key.export_public_key("openssh").decode().split()[1]

    async def start_async(self) -> None:
        self._server = await asyncssh.create_server(
            lambda: _Server(self),
            "127.0.0.1",
            self.port,
            server_host_keys=[self.key],
            process_factory=self._session,
            sftp_factory=lambda chan: _SFTPServer(chan, self),
            encoding=None,
            line_editor=False,
            x11_forwarding=self.x11,
            x11_auth_path=str(self.xauthority),
        )
        self.port = self._server.sockets[0].getsockname()[1]

    def start(self) -> "FakeSaga":
        """Run in a thread of its own (for pytest)."""
        ready = threading.Event()

        def run() -> None:
            self._loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self._loop)
            self._loop.run_until_complete(self.start_async())
            ready.set()
            self._loop.run_forever()
            server = self._server
            if server is not None:
                self._loop.run_until_complete(server.wait_closed())
            pending = asyncio.all_tasks(self._loop)
            for task in pending:
                task.cancel()
            self._loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            self._loop.run_until_complete(self._loop.shutdown_asyncgens())
            self._loop.close()

        self._thread = threading.Thread(target=run, daemon=True)
        self._thread.start()
        ready.wait(10)
        return self

    def drop_connections(self) -> None:
        """Close every connection, as a network drop would look to the client."""

        def drop() -> None:
            for conn in self.connections:
                conn.abort()

        assert self._loop is not None
        self._loop.call_soon_threadsafe(drop)

    def stop(self) -> None:
        if self._loop is None:
            return

        def close() -> None:
            for conn in self.connections:
                conn.abort()
            if self._server is not None:
                self._server.close()
            self._loop.stop()

        self._loop.call_soon_threadsafe(close)
        if self._thread is not None:
            self._thread.join(5)

    async def _session(self, process: asyncssh.SSHServerProcess) -> None:
        self.record.commands.append(process.command)
        self.record.displays.append(process.channel.get_x11_display())
        if process.command == saga_access.COMMAND:
            request = await process.stdin.read()
            envelope = None
            with contextlib.suppress(ValueError):
                envelope = json.loads(request)
            operation = envelope.get("operation") if isinstance(envelope, dict) else None
            if isinstance(operation, str) and operation in SAGA_FILE_OPERATIONS:
                self.record.saga_file_requests.append(envelope)
                if await self._saga_files(process, envelope):
                    return
            elif operation == "prepare_minimum":
                self.record.saga_prepare_requests.append(request)
                if self.saga_prepare_reply is not None:
                    process.stdout.write(json.dumps(self.saga_prepare_reply).encode())
                    process.exit(0)
                    return
            if self.saga_ts and isinstance(operation, str) and operation in SAGA_TS_OPERATIONS:
                self.record.saga_ts_requests.append(envelope)
                if self._installed_saga_source() is not None:
                    await self._saga_job(process, self.saga_ts, envelope)
                    return
            if (
                self.saga_minimum
                and isinstance(operation, str)
                and operation
                in {
                    "prepare_minimum",
                    "submit_minimum",
                    "minimum_status",
                }
            ):
                self.record.saga_minimum_requests.append(envelope)
                await self._saga_job(process, self.saga_minimum, envelope)
                return
            await self._pinned(process, saga_access.LAUNCHER, request)
            return
        if process.command == probe.COMMAND:
            await self._pinned(process, probe.PROBE_SOURCE, await process.stdin.read(257))
            return
        if process.subsystem == SUBSYSTEM and self.job_pilot is not None:
            try:
                envelope = json.loads(await process.stdin.read(65536))
                value = await asyncio.to_thread(self.job_pilot.handle, envelope)
                if self.drop_submit_reply and envelope["request"]["operation"] == "submit":
                    self.drop_submit_reply = False
                    process.exit(1)  # the helper recorded the receipt, but the app lost the reply
                    return
                reply = {"ok": True, "result": value}
            except (OSError, ValueError, KeyError) as exc:
                reply = {"ok": False, "error": str(exc)}
            process.stdout.write(json.dumps(reply).encode())
            process.exit(0)
            return
        if process.subsystem:
            process.exit(1)
            return
        if self.real_shell and process.command:
            await _real(process)
        else:
            await _FakeShell(self.root, process, self.xauthority).run()

    @property
    def saga_control(self) -> Path:
        return self.root / HOME.lstrip("/") / ".local/lib/chembook3d"

    async def _saga_job(
        self, process: asyncssh.SSHServerProcess, handler: Callable[[dict], dict], request: dict
    ) -> None:
        try:
            result = await asyncio.to_thread(handler, request)
            if self.drop_submit_reply and request["operation"] in {"submit_minimum", "submit_ts"}:
                self.drop_submit_reply = False
                process.exit(1)  # receipt is durable, but no bytes reached the keeper
                return
            reply, status = {"ok": True, "result": result}, 0
        except (OSError, ValueError) as exc:
            reply, status = {"ok": False, "error": str(exc)}, 1
        process.stdout.write(json.dumps(reply).encode())
        process.exit(status)

    async def _pinned(
        self, process: asyncssh.SSHServerProcess, launcher: str, request: bytes
    ) -> None:
        """Run the keeper's exact pinned launcher in the stand-in account's home."""
        home = str(self.root / HOME.lstrip("/"))
        result = await asyncio.to_thread(
            subprocess.run,
            [sys.executable, "-I", "-S", "-c", launcher],
            env={**os.environ, "HOME": home, "USERPROFILE": home},
            input=request,
            capture_output=True,
            timeout=10,
            check=False,
        )
        process.stdout.write(result.stdout)
        process.exit(result.returncode)

    def _installed_saga_source(self) -> bytes | None:
        installed = self.saga_control / "saga_prepare.py"
        if installed.is_file():
            source = installed.read_bytes().replace(b"\r\n", b"\n")
            if source == Path(saga_prepare.__file__).read_bytes().replace(b"\r\n", b"\n"):
                return source
        return None  # the real launcher reports the missing install or pin mismatch

    async def _saga_files(self, process: asyncssh.SSHServerProcess, request: dict) -> bool:
        """Project helper filesystem operations into the fixture's SFTP namespace."""
        source = self._installed_saga_source()
        if source is None:
            return False
        try:
            if self.saga_file_error is not None:
                raise ValueError(self.saga_file_error)
            policy = json.loads((self.saga_control / saga_prepare.MARKER).read_bytes())
            if sys.platform == "linux":
                result = await asyncio.to_thread(self._linux_saga_files, source, request, policy)
            else:
                result = self._portable_saga_files(request, policy["work_root"])
            reply, status = {"ok": True, "result": result}, 0
        except (OSError, ValueError) as exc:
            reply, status = {"ok": False, "error": str(exc)}, 1
        process.stdout.write(json.dumps(reply).encode())
        process.exit(status)
        return True

    def _linux_saga_files(self, source: bytes, request: dict, policy: dict) -> dict:
        # Give this execution its own globals so other fake servers keep their own root.
        helper = {"__name__": "_fake_saga_prepare"}
        exec(compile(source, str(self.saga_control / "saga_prepare.py"), "exec"), helper)
        helper["PROJECTS"] = self.root / "cluster/projects"
        path = request["path"]
        if path.startswith("/"):
            request = {**request, "path": str(self.root) + path}
        if not Path(policy["work_root"]).is_relative_to(self.root):
            policy = {**policy, "work_root": str(self.root) + policy["work_root"]}
        result = helper["file_operation"](request, policy)
        for key in ("path", "agent_read_root", "free"):
            if isinstance(result.get(key), str):
                result[key] = result[key].removeprefix(str(self.root))
        return result

    def _portable_saga_files(self, request: dict, root: str) -> dict:
        """Stand in for the helper on test hosts without Linux's filesystem interfaces."""
        path = request["path"]
        operation = request["operation"]
        relative = path[len(root) + 1 :] if path.startswith(root + "/") else ""
        parts = relative.split("/") if relative else []
        if (path != root and not parts) or any(
            not p or p in (".", "..") or "\\" in p or ":" in p for p in parts
        ):
            raise ValueError("This path is outside the pilot's approved results directory.")
        target = self.root / root.lstrip("/")
        for part in parts:
            target /= part
            if target.is_symlink():
                raise ValueError("Links are outside the pilot's approved results directory.")
        if operation == "list_files":
            entries = []
            for child in target.iterdir():
                if child.is_symlink():
                    entries.append({"name": child.name, "kind": "outside"})
                elif child.is_dir() or child.is_file():
                    info = child.stat()
                    entries.append(
                        {
                            "name": child.name,
                            "kind": "directory" if child.is_dir() else "file",
                            "size": info.st_size,
                            "modified": int(info.st_mtime),
                        }
                    )
            return {"path": path, "entries": entries, "agent_read_root": root}
        if operation == "read_file":
            with target.open("rb") as stream:
                info = os.fstat(stream.fileno())
                offset = request["offset"]
                size = info.st_size
                start = max(0, size + offset) if offset < 0 else min(size, offset)
                stream.seek(start)
                data = stream.read(request["length"])
            return {
                "path": path,
                "size": size,
                "modified": int(info.st_mtime),
                "version": saga_prepare.file_version(info),
                "offset": start,
                "data": base64.b64encode(data).decode("ascii"),
            }
        if (
            len(parts) != 2
            or parts[0] != "geometries"
            or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,79}\.xyz", parts[1])
        ):
            raise ValueError("Only flat, named XYZ files may be created in geometries.")
        data = request["text"].replace("\r\n", "\n").encode("utf-8")
        try:
            with target.open("xb") as stream:
                stream.write(data)
        except FileExistsError:
            return {"path": path, "written": False, "free": None}
        return {"path": path, "written": True, "size": len(data)}


class _FakeShell:
    def __init__(self, root: Path, process: asyncssh.SSHServerProcess, xauthority: Path):
        self.root = root
        self.process = process
        self.xauthority = xauthority
        self.cwd = HOME

    def _write(self, text: str) -> None:
        self.process.stdout.write(text.replace("\n", "\r\n").encode("utf-8"))

    def _prompt(self) -> None:
        self.process.stdout.write(f"\x1b]1337;CurrentDir={self.cwd}\x07".encode())
        self._write(f"{USER}@login-1 {posixpath.basename(self.cwd) or '/'}$ ")

    def _local(self, path: str) -> Path:
        return self.root / path.lstrip("/")

    async def _xclient(self) -> None:
        """Open the forwarded display as an X program does: the connection setup with the
        display's cookie, then wait for the X server's answer and print it."""
        display = self.process.channel.get_x11_display()
        if not display:
            self._write("xclient: cannot open display\n")
            return
        host, number = display.rsplit(":", 1)
        number = number.split(".")[0]
        entries = walk_xauth(str(self.xauthority))
        entry = next(e for e in entries if e.dpynum == number.encode())
        prefix = b"l\x00" + b"".join(
            n.to_bytes(2, "little") for n in (11, 0, len(entry.proto), len(entry.data), 0)
        )
        try:
            reader, writer = await asyncio.open_connection(host, 6000 + int(number))
        except OSError as exc:
            self._write(f"xclient: {exc}\n")
            return
        try:
            writer.write(prefix + _pad(entry.proto) + _pad(entry.data))
            await writer.drain()
            reply = await asyncio.wait_for(reader.read(100), 10)
        except (OSError, TimeoutError):
            reply = b""
        finally:
            writer.close()
            with contextlib.suppress(OSError, ConnectionError):
                await writer.wait_closed()
        self._write(f"xclient: {reply.decode('utf-8', 'replace') or 'no answer'}\n")

    def _glob(self, words: list[str]) -> list[str]:
        """Each word with a wildcard as bash expands it in the current directory (sorted; kept
        as typed when nothing matches)."""
        found: list[str] = []
        for word in words:
            if not any(c in word for c in "*?["):
                found.append(word)
                continue
            names = sorted(
                p.name
                for p in self._local(self.cwd).iterdir()
                if fnmatch.fnmatchcase(p.name, word) and not p.name.startswith(".")
            )
            found.extend(names or [word])
        return found

    async def _run(self, line: str) -> bool:
        words = line.split()
        if not words:
            return True
        command, args = words[0], words[1:]
        if command == "exit":
            return False
        if command == "xclient":
            await self._xclient()
        elif command == "cd":
            target = posixpath.normpath(posixpath.join(self.cwd, args[0] if args else HOME))
            if self._local(target).is_dir():
                self.cwd = target
            else:
                self._write(f"cd: {args[0]}: No such file or directory\n")
        elif command == "pwd":
            self._write(self.cwd + "\n")
        elif command == "ls":
            names = sorted(p.name for p in self._local(self.cwd).iterdir())
            self._write("  ".join(names) + ("\n" if names else ""))
        elif command == "echo":
            self._write(" ".join(args) + "\n")
        elif command in ("inspect", "scans"):  # as the keeper's RC has them (D123c, D138)
            report = "Inspect" if command == "inspect" else "Scans"
            for name in self._glob(args):
                target = posixpath.normpath(posixpath.join(self.cwd, name))
                if self._local(target).is_file():
                    self.process.stdout.write(f"\x1b]1337;{report}={target}\x07".encode())
                elif self._local(target).is_dir():
                    self._write(f"{command}: {name}: is a folder\n")
                else:
                    self._write(f"{command}: {name}: no such file\n")
        else:
            self._write(f"{command}: command not found\n")
        return True

    async def run(self) -> None:
        self._write("Welcome to the fake Saga.\n")
        self._prompt()
        line = ""
        while True:
            try:
                data = await self.process.stdin.read(1024)
            except asyncssh.TerminalSizeChanged:
                continue
            except (asyncssh.Error, OSError):
                break
            if not data:
                break
            for char in data.decode("utf-8", errors="replace"):
                if char in "\r\n":
                    self._write("\n")
                    if not await self._run(line):
                        self._write("logout\n")
                        self.process.exit(0)
                        return
                    line = ""
                    self._prompt()
                elif char in "\x7f\b":
                    if line:
                        line = line[:-1]
                        self._write("\b \b")
                elif char == "\x03":
                    line = ""
                    self._write("^C\n")
                    self._prompt()
                else:
                    line += char
                    self._write(char)
        self.process.exit(0)


def _pad(data: bytes) -> bytes:
    return data + b"\x00" * (-len(data) % 4)


class FakeXServer:
    """A stand-in for Xming: listens on 127.0.0.1 port 6000 + display, records the cookie each
    X program connects with and answers "fake X server"."""

    def __init__(self):
        self.cookies: list[bytes] = []
        fake = self

        class Handler(socketserver.BaseRequestHandler):
            def handle(self) -> None:
                data = b""
                with contextlib.suppress(OSError):
                    while len(data) < 12:
                        data += self.request.recv(4096) or b"!" * 12
                    name, cookie = (int.from_bytes(data[i : i + 2], "little") for i in (6, 8))
                    need = 12 + len(_pad(b"x" * name)) + len(_pad(b"x" * cookie))
                    while len(data) < need:
                        more = self.request.recv(4096)
                        if not more:
                            return
                        data += more
                    start = 12 + len(_pad(b"x" * name))
                    fake.cookies.append(data[start : start + cookie])
                    self.request.sendall(b"fake X server")

        for display in range(40, 100):
            try:
                self._server = socketserver.ThreadingTCPServer(
                    ("127.0.0.1", 6000 + display), Handler
                )
            except OSError:
                continue
            self.display = f"127.0.0.1:{display}.0"
            break
        else:
            raise OSError("no free X11 display port")
        self._server.daemon_threads = True
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._server.shutdown()
        self._server.server_close()


@contextlib.contextmanager
def free_display() -> Iterator[str]:
    """A display nothing answers on, kept that way while in use: its port is bound but not
    listening, so connecting is refused and no FakeXServer (in another pytest-xdist worker)
    can start listening on it in the meantime."""
    for display in range(40, 100):
        hold = socket.socket()
        try:
            hold.bind(("127.0.0.1", 6000 + display))
        except OSError:
            hold.close()
            continue
        try:
            yield f"127.0.0.1:{display}.0"
        finally:
            hold.close()
        return
    raise OSError("every X11 display port is taken")


async def _real(process: asyncssh.SSHServerProcess) -> None:
    """Run the command the client sent in a real pseudo-terminal (Linux)."""
    import pty

    master, slave = pty.openpty()
    child = await asyncio.create_subprocess_exec(
        "sh", "-c", process.command, stdin=slave, stdout=slave, stderr=slave,
        start_new_session=True, env={**os.environ, "TERM": "xterm-256color"},
    )  # fmt: skip
    os.close(slave)
    os.set_blocking(master, False)
    loop = asyncio.get_running_loop()
    done = asyncio.Event()

    def readable() -> None:
        try:
            data = os.read(master, 65536)
        except BlockingIOError:
            return
        except OSError:
            data = b""
        if data:
            process.stdout.write(data)
        else:
            loop.remove_reader(master)
            done.set()

    loop.add_reader(master, readable)

    async def typed() -> None:
        while True:
            try:
                data = await process.stdin.read(1024)
            except asyncssh.TerminalSizeChanged:
                continue
            except (asyncssh.Error, OSError):
                return
            if not data:
                return
            with contextlib.suppress(OSError):
                os.write(master, data)

    reader = asyncio.ensure_future(typed())
    try:
        await done.wait()
    finally:
        reader.cancel()
        loop.remove_reader(master)
        if child.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                child.kill()
        await child.wait()
        os.close(master)
    process.exit(child.returncode or 0)


def enable_job_pilot(fake: FakeSaga, config: Path, server_id: str) -> None:
    """Create a local operator profile and toy project, outside the real user's settings."""
    import secrets

    remote = LOCAL_WORK
    work = fake.root / remote.lstrip("/")
    work.mkdir(parents=True, exist_ok=True)
    geometries = work / "geometries"
    geometries.mkdir(exist_ok=True)
    (geometries / "water.xyz").write_text(
        "3\nWater\nO 0 0 0\nH 0.76 0.59 0\nH -0.76 0.59 0\n", encoding="ascii"
    )
    private = config / "pilot-helper"
    jobs = private / "job-files"
    jobs.mkdir(parents=True, exist_ok=True)
    (jobs / "minimum.jd").write_text("#p HF/STO-3G Opt", encoding="ascii")
    (jobs / "linked-minimum.jd").write_text("linked-minimum", encoding="ascii")
    (jobs / "ts.jd").write_text("checkpoint-ts", encoding="ascii")
    tool = (sys.executable, str(Path(__file__).with_name("hpc_pilot_runner.py").resolve()))
    profile = config / "remote" / "local-job-pilot.json"
    profile.parent.mkdir(parents=True, exist_ok=True)
    target = ["127.0.0.1", fake.port, USER]
    if profile.exists():
        saved = json.loads(profile.read_text("utf-8"))
        if saved["target"] != target or saved["server_id"] != server_id:
            raise ValueError("This pilot profile belongs to a different server.")
        key = bytes.fromhex(saved["key"])
    else:
        key = secrets.token_bytes(32)
        with os.fdopen(
            os.open(profile, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w", encoding="utf-8"
        ) as stream:
            json.dump({"target": target, "key": key.hex(), "server_id": server_id}, stream)
    fake.job_pilot = JobPilot(
        private,
        work,
        remote,
        geometries,
        jobs,
        (*tool, "prepare", "ok"),
        (*tool, "submit", "ok"),
        key,
        40,
        24,
    )


def enable_saga_minimum(fake: FakeSaga, remote_root: str, contents: str, monkeypatch) -> None:
    """Enroll a synthetic manual minimum and a scheduler which only writes local test files."""
    work = fake.root / remote_root.lstrip("/")
    home = fake.root / HOME.lstrip("/")
    control = fake.saga_control
    control.mkdir(mode=0o700, parents=True)
    (control / "saga_prepare.py").write_bytes(Path(saga_prepare.__file__).read_bytes())
    wrapper = home / "tools/uibkvant-tools/runnable/submit_job_g16-C.01"
    wrapper.parent.mkdir(parents=True)
    wrapper.write_bytes(
        b"#!/usr/bin/env python3\n"
        b"from pathlib import Path\n"
        b"import os, sys\n"
        b"stage = Path('.') if sys.platform == 'linux' else Path(os.environ['SAGA_FIXTURE_CWD'])\n"
        b"assert sys.argv[1:] == ['sample.inp', '-n', '1', '-c', '40', '-t', '24']\n"
        b"assert (stage / 'sample.inp').is_file()\n"
        b"with (stage / 'scheduler-calls.txt').open('a') as calls:\n"
        b"    print(' '.join(sys.argv[1:]), file=calls)\n"
        b"(stage / 'sample.log').write_text('Starting job 72410 on test-node on Saga "
        b"at Tue Nov 24 09:00:00 CET 2026\\nJobName: sample\\n')\n"
        b"print('Submitted batch job 72410')\n"
    )
    wrapper.chmod(0o700)
    marker = ".submit-minimum.json"
    if sys.platform == "linux":
        # This pytest process owns the synthetic remote. The keeper is a separate process.
        monkeypatch.setenv("HOME", str(home))
        monkeypatch.setattr(saga_prepare, "PROJECTS", work.parents[2])
        reference = work / "min-test-reference/sample.inp"
        reference.parent.mkdir(parents=True)
        reference.write_bytes(contents.encode())
        reference.with_suffix(".xyz").write_bytes(b"2\nSynthetic H2\nH 0 0 0\nH 0 0 0.74\n")
        job = work.parent / "jobdescriptions" / saga_prepare.JOB
        job.parent.mkdir()
        job.write_bytes(b"synthetic job description\n")
        generator = home / "tools/scripts/submit_all_DFT.sh"
        generator.parent.mkdir(parents=True)
        generator.write_bytes(
            b'#!/bin/sh\n[ "$3" = nosub ] && cp ../min-test-reference/sample.inp sample.inp\n'
        )
        generator.chmod(0o700)
        saga_prepare.install(reference)
        policy = json.loads((control / saga_prepare.MARKER).read_bytes())
        saga_prepare.prepare({"operation": "prepare_minimum"}, policy)
        saga_prepare.enable_submit()

        def operation(request):
            if request["operation"] == "prepare_minimum":
                result = saga_prepare.prepare(request, policy)
            else:
                result = saga_prepare.minimum_operation(request, policy)
            return {**result, "directory": result["directory"].removeprefix(str(fake.root))}

    else:
        # Saga's fd-based helper requires Linux. Supply its wire replies on other hosts.
        attempt, revision = "19" * 16, saga_prepare.digest(contents.encode())
        directory = f"{remote_root}/prepare-{attempt}"
        prepared = {
            "state": "prepared",
            "directory": directory,
            "input_name": "sample.inp",
            "input": contents,
            "sha256": revision,
            "reference_sha256": revision,
        }
        policy = {"work_root": remote_root, "attempt": attempt, "reference_sha256": revision}
        resources = {"nodes": 1, "cores": 40, "hours": 24}
        authorization = {
            "attempt": attempt,
            "revision": revision,
            "key": "71" * 32,
            **resources,
            "wrapper": HOME + "/tools/uibkvant-tools/runnable/submit_job_g16-C.01",
            "wrapper_sha256": saga_prepare.digest(wrapper.read_bytes()),
        }
        saga_prepare.atomic_write(control / marker, json.dumps(authorization).encode())
        saga_prepare.atomic_write(control / saga_prepare.MARKER, json.dumps(policy).encode())
        stage = work / ("prepare-" + attempt) / "submission"
        stage.parent.mkdir(parents=True)
        (stage.parent / "sample.inp").write_bytes(contents.encode())
        receipt = {
            "attempt": attempt,
            "revision": revision,
            "state": "prepared",
            "directory": directory + "/submission",
            "job_id": None,
            "detail": None,
            **resources,
        }

        def operation(request):
            if request["operation"] == "prepare_minimum":
                return prepared
            if request["operation"] == "submit_minimum" and receipt["state"] == "prepared":
                stage.mkdir()
                (stage / "sample.inp").write_bytes((stage.parent / "sample.inp").read_bytes())
                subprocess.run(
                    [sys.executable, str(wrapper), "sample.inp", "-n", "1", "-c", "40", "-t", "24"],
                    # CreateProcess rejects long current directories on Windows.
                    env={**os.environ, "SAGA_FIXTURE_CWD": str(stage)},
                    check=True,
                    capture_output=True,
                    timeout=10,
                )
                receipt.update(state="submitted", job_id="72410")
            return receipt.copy()

    # The fake SFTP server starts at its chroot. Expose the remote names in that namespace.
    sftp_control = fake.root / ".local/lib/chembook3d"
    sftp_control.mkdir(parents=True, mode=0o700)
    saga_prepare.atomic_write(sftp_control / marker, (control / marker).read_bytes())
    saga_prepare.atomic_write(
        sftp_control / saga_prepare.MARKER,
        json.dumps({**policy, "work_root": remote_root}).encode(),
    )
    fake.saga_minimum = operation


def enable_saga_ts(fake: FakeSaga, remote_root: str, monkeypatch) -> tuple[dict, dict]:
    """Enroll a reviewed synthetic checkpoint handoff and a scheduler with no cluster access."""
    work = fake.root / remote_root.lstrip("/")
    home = fake.root / HOME.lstrip("/")
    control = fake.saga_control
    parent = work / "synthetic-parent"
    parent.mkdir()
    write_completed_preopt(parent, "water", "72410", [1, 2])
    bundle = work / "ts-test-water"
    bundle.mkdir()
    contents = checkpoint_ts_input("water-ts")
    checkpoint = (parent / "water.fchk.gz").read_bytes()
    review = {
        "format": "chembook3d-saga-ts-v1",
        "parent_job_id": "72410",
        "parent_stem": "water",
        "input_name": "water-ts.inp",
        "checkpoint_name": "water-ts.fchk.gz",
        "input_sha256": saga_prepare.digest(contents.encode()),
        "checkpoint_sha256": saga_prepare.digest(checkpoint),
        "output_sha256": saga_prepare.digest((parent / "water.out").read_bytes()),
        "freeze_distance": [1, 2],
        "mode_index": 1,
        "frequency_cm1": -425.0,
        "bond_projection": 0.96,
        "alignment": 1.0,
    }
    review_bytes = (json.dumps(review, sort_keys=True, separators=(",", ":")) + "\n").encode()
    (bundle / "review.json").write_bytes(review_bytes)
    (bundle / "child.inp").write_bytes(contents.encode())
    (bundle / "parent.fchk.gz").write_bytes(checkpoint)
    wrapper = home / saga_prepare.WRAPPER
    wrapper.parent.mkdir(parents=True)
    wrapper.write_bytes(
        b"#!/usr/bin/env python3\n"
        b"from pathlib import Path\n"
        b"import os, sys\n"
        b"stage = Path(os.environ.get('SAGA_FIXTURE_CWD', '.'))\n"
        b"assert sys.argv[1:] == ['water-ts.inp', '-n', '1', '-c', '40', '-t', '24']\n"
        b"assert {p.name for p in stage.iterdir()} == {'water-ts.inp', 'water-ts.fchk.gz'}\n"
        b"with (stage / 'scheduler-calls.txt').open('a') as calls:\n"
        b"    print(' '.join(sys.argv[1:]), file=calls)\n"
        b"(stage / 'water-ts.log').write_text('Starting job 72411 on test-node on Saga "
        b"at Tue Nov 24 09:00:00 CET 2026\\nJobName: water-ts\\n')\n"
        b"print('Submitted batch job 72411')\n"
    )
    wrapper.chmod(0o700)
    marker_path = control / SAGA_TS_MARKER
    if sys.platform == "linux":
        monkeypatch.setenv("HOME", str(home))
        monkeypatch.setattr(saga_prepare, "PROJECTS", work.parents[2])
        policy = {
            "work_root": str(work),
            "stem": "water",
            "attempt": "18" * 16,
            "reference_sha256": "60" * 32,
        }
        saga_prepare.atomic_write(control / saga_prepare.MARKER, json.dumps(policy).encode())
        saga_prepare.enroll_ts(bundle)
        marker = json.loads(marker_path.read_bytes())

        def operation(request):
            result = saga_prepare.ts_operation(request, policy)
            return {**result, "directory": result["directory"].removeprefix(str(fake.root))}

    else:
        # Non-Linux hosts exercise the real keeper and confirmation against portable wire replies.
        attempt = "29" * 16
        identity = {
            "attempt": attempt,
            "revision": review["input_sha256"],
            "checkpoint_sha256": review["checkpoint_sha256"],
            "review_sha256": saga_prepare.digest(review_bytes),
        }
        resources = {"nodes": 1, "cores": 40, "hours": 24}
        marker = {**identity, **resources, "key": "72" * 32}
        saga_prepare.atomic_write(marker_path, json.dumps(marker).encode())
        directory = f"{remote_root}/ts-{attempt}"
        stage = work / f"ts-{attempt}" / "submission"
        receipt_path = control / f"{attempt}.fixture-ts-receipt"

        def operation(request):
            name = request["operation"]
            expected = {"operation": name, **identity}
            if name == "submit_ts":
                expected.update(resources)
                expected["signature"] = signature(bytes.fromhex(marker["key"]), expected)
            if request != expected:
                raise ValueError("The request does not match the enrolled TS.")
            if name == "prepare_ts":
                if receipt_path.exists():
                    raise ValueError("Check the prepared TS attempt for its submission status.")
                stage.parent.mkdir(exist_ok=True)
                preview = stage.parent / review["input_name"]
                if not preview.exists():
                    preview.write_bytes(contents.encode())
                return {
                    **identity,
                    "state": "prepared",
                    "directory": directory,
                    "input_name": review["input_name"],
                    "input": contents,
                    "review": review,
                }
            receipt = {
                **identity,
                **resources,
                "state": "prepared",
                "directory": directory + "/submission",
                "job_id": None,
                "detail": None,
            }
            if receipt_path.exists():
                return json.loads(receipt_path.read_bytes())
            if name == "submit_ts":
                stage.mkdir()
                (stage / review["input_name"]).write_bytes(contents.encode())
                (stage / review["checkpoint_name"]).write_bytes(checkpoint)
                subprocess.run(
                    [
                        sys.executable,
                        str(wrapper),
                        review["input_name"],
                        "-n",
                        "1",
                        "-c",
                        "40",
                        "-t",
                        "24",
                    ],
                    env={**os.environ, "SAGA_FIXTURE_CWD": str(stage)},
                    check=True,
                    capture_output=True,
                    timeout=10,
                )
                receipt.update(state="submitted", job_id="72411")
                saga_prepare.atomic_write(receipt_path, json.dumps(receipt).encode())
            return receipt

    saga_prepare.atomic_write(
        fake.root / ".local/lib/chembook3d" / SAGA_TS_MARKER, marker_path.read_bytes()
    )
    fake.saga_ts = operation
    return marker, review


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--root", required=True)
    parser.add_argument(
        "--job-pilot-config",
        type=Path,
        help="Enable the local Gaussian pilot using an isolated app config folder.",
    )
    args = parser.parse_args()
    root = Path(args.root)
    root.mkdir(parents=True, exist_ok=True)
    fake = FakeSaga(root, port=args.port)

    async def serve() -> None:
        if args.job_pilot_config:
            # A repeat run on the same isolated profile must keep the host key trusted
            # by the app so the user can reconnect and recover an earlier receipt.
            key = args.job_pilot_config / "pilot-helper" / "ssh-host-key"
            key.parent.mkdir(parents=True, exist_ok=True)
            try:
                descriptor = os.open(key, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            except FileExistsError:
                fake.key = asyncssh.read_private_key(key)
            else:
                with os.fdopen(descriptor, "wb") as stream:
                    stream.write(fake.key.export_private_key())
        await fake.start_async()
        if args.job_pilot_config:
            enable_job_pilot(fake, args.job_pilot_config, "saga")
        print(f"fake Saga on 127.0.0.1:{fake.port}", flush=True)
        await asyncio.Event().wait()

    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(serve())
    sys.exit(0)


if __name__ == "__main__":
    main()
