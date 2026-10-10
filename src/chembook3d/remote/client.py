"""The app's side of the keeper (D122c): find it through `remote/keeper.json`, start it when
there is none, and pass requests and the terminal to it."""

import asyncio
import contextlib
import json
import subprocess
import sys
import time
from pathlib import Path

from chembook3d.remote import keeper
from chembook3d.remote.servers import folder
from chembook3d.settings import config_dir

START_WAIT = 20.0  # seconds a new keeper may take to start listening
CALL_WAIT = keeper.EVENT_WAIT + 15  # a login step waits up to EVENT_WAIT in the keeper


# Keepers this app started: kept so Python does not warn that they still run (they should).
_started: list[subprocess.Popen] = []


class KeeperError(RuntimeError):
    """The keeper cannot be reached or refused the request."""


def _info() -> dict | None:
    try:
        info = json.loads((folder() / keeper.KEEPER_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if (
        not isinstance(info, dict)
        or not isinstance(info.get("port"), int)
        or not isinstance(info.get("token"), str)
    ):
        return None
    return info


async def _open(info: dict) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    return await asyncio.wait_for(
        asyncio.open_connection("127.0.0.1", info["port"], limit=keeper.LINE_LIMIT), 5
    )


async def call(op: str, timeout: float = CALL_WAIT, **fields) -> dict:
    """One request to the running keeper; KeeperError when there is none."""
    info = _info()
    if info is None:
        raise KeeperError("Not logged in.")
    try:
        reader, writer = await _open(info)
    except (OSError, TimeoutError) as exc:
        raise KeeperError("Not logged in.") from exc
    try:
        writer.write(
            json.dumps({"token": info["token"], "op": op, **fields}).encode("utf-8") + b"\n"
        )
        await writer.drain()
        line = await asyncio.wait_for(reader.readline(), timeout)
    except (OSError, TimeoutError, ValueError) as exc:
        raise KeeperError("The keeper did not answer.") from exc
    finally:
        writer.close()
        with contextlib.suppress(OSError, ConnectionError):
            await writer.wait_closed()
    if not line:
        raise KeeperError("The keeper refused the request.")
    answer = json.loads(line)
    if not answer.get("ok"):
        raise KeeperError(answer.get("error") or "The keeper refused the request.")
    return answer


async def running() -> bool:
    """A keeper of this version answers."""
    try:
        answer = await call("ping", timeout=5)
    except KeeperError:
        return False
    return answer.get("protocol") == keeper.PROTOCOL


async def status() -> dict:
    """What the keeper holds; nothing when none is running."""
    try:
        return await call("status", timeout=10)
    except KeeperError:
        return {"sessions": [], "lost": {}}


def _python() -> str:
    """This app's Python, the windowless one on Windows."""
    executable = Path(sys.executable)
    if sys.platform == "win32":
        windowless = executable.with_name("pythonw.exe")
        if windowless.is_file():
            return str(windowless)
    return str(executable)


def spawn() -> None:
    """Start a keeper that outlives this app: a session of its own on Linux; on Windows
    detached, outside the app's job object when the job allows it (uv runs the app in one)."""
    work = folder()
    work.mkdir(parents=True, exist_ok=True)
    argv = [_python(), "-m", "chembook3d.remote.keeper", "--config", str(config_dir())]
    with open(work / keeper.LOG_FILE, "ab") as output:
        options: dict = {
            "stdin": subprocess.DEVNULL,
            "stdout": output,
            "stderr": output,
            "cwd": work,
            "close_fds": True,
        }
        if sys.platform != "win32":
            _started.append(subprocess.Popen(argv, start_new_session=True, **options))
            return
        flags = (
            subprocess.DETACHED_PROCESS
            | subprocess.CREATE_NEW_PROCESS_GROUP
            | subprocess.CREATE_NO_WINDOW
        )
        try:
            _started.append(
                subprocess.Popen(
                    argv, creationflags=flags | subprocess.CREATE_BREAKAWAY_FROM_JOB, **options
                )
            )
        except OSError:  # the job does not allow leaving it
            _started.append(subprocess.Popen(argv, creationflags=flags, **options))


async def ensure() -> None:
    """A keeper is running, started now if none was."""
    if await running():
        return
    info = _info()
    if info is not None:  # left by a keeper that is gone, or by another version
        with contextlib.suppress(KeeperError):
            await call("quit", timeout=5)
        with contextlib.suppress(OSError):
            (folder() / keeper.KEEPER_FILE).unlink()
    await asyncio.to_thread(spawn)
    deadline = time.monotonic() + START_WAIT
    while time.monotonic() < deadline:
        await asyncio.sleep(0.2)
        if await running():
            return
    raise KeeperError(f"The background keeper did not start; see {folder() / keeper.LOG_FILE}.")


class Attached:
    """A shell streamed from the keeper (`attach`)."""

    def __init__(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        self._reader = reader
        self._writer = writer

    async def receive(self) -> dict | None:
        line = await self._reader.readline()
        if not line:
            return None
        return json.loads(line)

    async def send(self, message: dict) -> None:
        self._writer.write(json.dumps(message).encode("utf-8") + b"\n")
        await self._writer.drain()

    async def close(self) -> None:
        self._writer.close()
        with contextlib.suppress(OSError, ConnectionError):
            await self._writer.wait_closed()


async def attach(server_id: str, rows: int, cols: int) -> Attached:
    info = _info()
    if info is None:
        raise KeeperError("Not logged in.")
    try:
        reader, writer = await _open(info)
    except (OSError, TimeoutError) as exc:
        raise KeeperError("Not logged in.") from exc
    attached = Attached(reader, writer)
    await attached.send(
        {"token": info["token"], "op": "attach", "server": server_id, "rows": rows, "cols": cols}
    )
    return attached


def pid() -> int | None:
    info = _info()
    return info.get("pid") if info else None


def stop_quietly() -> None:
    """For tests: ask a running keeper to end."""
    with contextlib.suppress(Exception):
        asyncio.run(call("quit", timeout=5))
    for process in _started:
        with contextlib.suppress(subprocess.TimeoutExpired):
            process.wait(10)
