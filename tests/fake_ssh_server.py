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
import contextlib
import os
import posixpath
import socket
import socketserver
import sys
import threading
from dataclasses import dataclass, field
from pathlib import Path

import asyncssh
from asyncssh.x11 import walk_xauth

USER = "jonas"
PASSWORD = "correct horse"
CODE = "123456"
HOME = "/cluster/home/jonas"


@dataclass
class Record:
    """What the server saw."""

    answers: list[list[str]] = field(default_factory=list)  # every keyboard-interactive answer
    logins: int = 0  # successful logins
    commands: list[str | None] = field(default_factory=list)  # each session's command
    displays: list[str | None] = field(default_factory=list)  # each session's X11 display


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


class FakeSaga:
    def __init__(self, root: Path, real_shell: bool = False, port: int = 0, x11: bool = True):
        self.root = root
        self.real_shell = real_shell
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
        root = str(self.root)
        self._server = await asyncssh.create_server(
            lambda: _Server(self),
            "127.0.0.1",
            self.port,
            server_host_keys=[self.key],
            process_factory=self._session,
            sftp_factory=lambda chan: asyncssh.SFTPServer(chan, chroot=root.encode()),
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
        if self.real_shell and process.command:
            await _real(process)
        else:
            await _FakeShell(self.root, process, self.xauthority).run()


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


def free_display() -> str:
    """A display nothing answers on."""
    for display in range(40, 100):
        with socket.socket() as probe:
            if probe.connect_ex(("127.0.0.1", 6000 + display)) != 0:
                return f"127.0.0.1:{display}.0"
    raise OSError("every X11 display port answers")


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


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--root", required=True)
    args = parser.parse_args()
    root = Path(args.root)
    root.mkdir(parents=True, exist_ok=True)
    fake = FakeSaga(root, port=args.port)

    async def serve() -> None:
        await fake.start_async()
        print(f"fake Saga on 127.0.0.1:{fake.port}", flush=True)
        await asyncio.Event().wait()

    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(serve())
    sys.exit(0)


if __name__ == "__main__":
    main()
