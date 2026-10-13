"""Claude reads files on a logged-in SSH server such as Saga (D124, FR-SSH-10..12): which
servers are logged in, the files in the terminal's current directory, a file's text in pages,
its end or the lines matching a search, the Inspect window's reading of an output, and one of
its structures. They are Claude's read-only MCP tools (`list_servers`, `list_server_files`,
`read_server_file`, `summarize_server_file`, `get_server_structure`).

Only the terminal's current directory and the ones below it can be read: the keeper resolves
every path with the server's realpath (symlinks followed) and refuses one outside it, so the
user steers what Claude sees with `cd`. Nothing is written, deleted, run or logged into on the
server, and nothing is added to the investigation; logging in stays the app's page's own
(D122d). A file's end and its first pages are read as byte ranges; a search, a page deeper in
or a summary copies the file once into a temporary folder, kept while the file's size and time
stay the same and dropped at log out and when the app stops.

Writing (D126, FR-SSH-13): once the user turns on "Let Claude write new files here" in the
server's tab (the page's own route in `api/remote.py`; off at every login), `write_server_file`
creates new text files in the terminal's current directory and below, with new folders on the
way. A file that is there already is never replaced: the keeper creates with SFTP's exclusive
create and answers with the next free name for a copy (name_v2.ext). Nothing is ever deleted,
renamed, appended to or run.
"""

import asyncio
import base64
import difflib
import posixpath
import re
import shutil
import tempfile
from collections import OrderedDict
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel

from chembook3d import xyz
from chembook3d.api.remote import _check, _saved
from chembook3d.remote import client, keeper, servers
from chembook3d.services import inspection

router = APIRouter(prefix="/api/server-files")

# A71 defaults.
PAGE_LINES = 400  # lines of a page when Claude does not say
MAX_LINES = 2000  # lines in one answer
MAX_BYTES = 200_000  # bytes of text in one answer
MAX_LINE = 2000  # characters of one line shown; the rest is cut
MAX_MATCHES = 200  # matches of a search in one answer
CONTEXT = 2  # lines shown before and after each match
MAX_CONTEXT = 10
CACHE_LIMIT = 1024**3  # bytes of copied files kept at once
MAX_STRUCTURES = 300  # structures of a step listed in a summary; the first and last are kept
READER_PROTOCOL = 4  # the keeper version that reads for Claude and checks the directory
WRITER_PROTOCOL = 5  # the keeper version that writes new files for Claude (D126)
MAX_DIFF = 300  # lines of an edited copy's difference shown


# ---------- copies of whole files ----------


@dataclass
class _Copy:
    folder: Path
    name: str
    size: int
    modified: int | None
    read: inspection.Inspection | None = None  # the summary's reading, kept with the copy

    @property
    def local(self) -> Path:
        return self.folder / self.name


class Copies:
    """Files copied down for Claude, by server and full path, the most recently used last."""

    def __init__(self) -> None:
        self.kept: OrderedDict[tuple[str, str], _Copy] = OrderedDict()
        self.lock = asyncio.Lock()

    def drop(self, key: tuple[str, str]) -> None:
        found = self.kept.pop(key, None)
        if found is not None:
            shutil.rmtree(found.folder, ignore_errors=True)

    def forget(self, server_id: str) -> None:
        for key in [k for k in self.kept if k[0] == server_id]:
            self.drop(key)

    def close_all(self) -> None:
        for key in list(self.kept):
            self.drop(key)

    async def get(self, server: servers.Server, path: str, size: int, modified) -> _Copy:
        """A copy of the file as it is now (`size` and `modified` as just read)."""
        key = (server.id, path)
        async with self.lock:
            found = self.kept.get(key)
            if found is not None and (found.size, found.modified) == (size, modified):
                self.kept.move_to_end(key)
                return found
            self.drop(key)
            if size > CACHE_LIMIT:
                raise HTTPException(
                    422,
                    f"{path} is {size / 1e6:.0f} MB, more than the {CACHE_LIMIT / 1e6:.0f} MB "
                    "that can be copied to search it; read its end or its first lines instead.",
                )
            while self.kept and sum(c.size for c in self.kept.values()) + size > CACHE_LIMIT:
                self.drop(next(iter(self.kept)))
            folder = Path(tempfile.mkdtemp(prefix="chembook3d-read-"))
            try:
                answer = await _keeper(
                    server,
                    "download",
                    timeout=keeper.COPY_WAIT,
                    paths=[path],
                    folder=str(folder),
                    within=True,
                )
                if answer["failed"]:
                    raise HTTPException(422, answer["failed"][0]["reason"])
                copied = answer["files"][0]
            except BaseException:
                shutil.rmtree(folder, ignore_errors=True)
                raise
            found = _Copy(
                folder, Path(copied["local"]).name, copied["size"], copied.get("modified")
            )
            self.kept[key] = found
            return found


def copies(app) -> Copies:
    found = getattr(app.state, "server_copies", None)
    if found is None:
        found = app.state.server_copies = Copies()
    return found


# ---------- the keeper ----------


async def _keeper(server: servers.Server, op: str, **fields) -> dict:
    try:
        return await client.call(op, server=server.id, **fields)
    except client.KeeperError as exc:
        message = str(exc)
        if message == "Not logged in.":
            message = (
                f"Not logged in to {server.name}. The user logs in in the app's {server.name} "
                "tab; Claude cannot log in."
            )
        raise HTTPException(409, message) from exc


async def _reader(request: Request, server_id: str, write: bool = False) -> servers.Server:
    """The server, logged in through a keeper that checks the directory (D124) and, to
    `write`, writes new files (D126)."""
    _check(request, page_only=False)
    server = _saved(server_id)
    ping = await _keeper(server, "ping")
    if ping.get("protocol", 1) < (WRITER_PROTOCOL if write else READER_PROTOCOL):
        what = "write" if write else "read"
        raise HTTPException(
            409,
            f"The connection to {server.name} was opened by an older version of Chembook3D, "
            f"which cannot let Claude {what} files. The user logs out and logs in again.",
        )
    return server


@dataclass
class _Meta:
    path: str  # full path on the server, symlinks followed
    size: int
    modified: int | None
    head: bytes  # the first bytes asked for


async def _meta(server: servers.Server, path: str, head: int = 0) -> _Meta:
    answer = await _keeper(server, "read", path=path, offset=0, length=head)
    return _Meta(
        answer["path"],
        answer["size"],
        answer.get("modified"),
        base64.b64decode(answer["data"]),
    )


def _when(modified: int | None) -> str:
    if modified is None:
        return "time unknown"
    return datetime.fromtimestamp(modified, UTC).strftime("modified %Y-%m-%d %H:%M UTC")


def _size(size: int) -> str:
    if size < 1000:
        return f"{size} bytes"
    if size < 1_000_000:
        return f"{size / 1e3:.1f} kB"
    return f"{size / 1e6:.1f} MB"


def _heading(server: servers.Server, meta: _Meta) -> str:
    return f"{servers.origin_path(server, meta.path)} ({_size(meta.size)}, {_when(meta.modified)})"


def _binary(data: bytes) -> bool:
    return b"\x00" in data[:8192]


def _decoded(data: bytes) -> list[str]:
    return data.decode("utf-8", errors="replace").splitlines()


def _file_lines(path: Path) -> Iterator[str]:
    with path.open("r", encoding="utf-8", errors="replace", newline=None) as handle:
        for line in handle:
            yield line.rstrip("\r\n")


def _cut(line: str) -> str:
    if len(line) <= MAX_LINE:
        return line
    return line[:MAX_LINE] + f" [… {len(line) - MAX_LINE} more characters]"


def _numbered(number: int | None, line: str) -> str:
    return f"{number:>7}\t{_cut(line)}" if number is not None else _cut(line)


class _Out:
    """Lines of an answer, up to MAX_BYTES."""

    def __init__(self) -> None:
        self.lines: list[str] = []
        self.size = 0
        self.full = False

    def add(self, text: str) -> bool:
        size = len(text.encode("utf-8")) + 1
        if self.size + size > MAX_BYTES:
            self.full = True
            return False
        self.lines.append(text)
        self.size += size
        return True


def _answer(*parts: str) -> PlainTextResponse:
    return PlainTextResponse("\n".join(p for p in parts if p is not None) + "\n")


# ---------- routes ----------


class ServerReadable(BaseModel):
    id: str
    name: str
    host: str
    username: str
    logged_in: bool
    cwd: str | None  # the terminal's current directory: what Claude may read, with below it
    readable: bool
    reason: str | None = None  # why not, when not
    writes: bool = False  # D126: the user lets Claude write new files there in this login


class ServersReadable(BaseModel):
    servers: list[ServerReadable]


@router.get("")
async def list_servers(request: Request) -> ServersReadable:
    """The saved servers, whether each is logged in, and the terminal's current directory."""
    _check(request, page_only=False)
    held = await client.status()
    sessions = {s["server"]["id"]: s for s in held.get("sessions", [])}
    protocol = held.get("protocol", 1)
    result = []
    for server in servers.load():
        session = sessions.get(server.id)
        cwd = session.get("cwd") if session else None
        reason = None
        if session is None:
            reason = f"Not logged in; the user logs in in the app's {server.name} tab."
        elif protocol < READER_PROTOCOL:
            reason = "Logged in by an older version of the app; the user logs out and in again."
        elif not cwd:
            reason = "The terminal has not reported its current directory."
        result.append(
            ServerReadable(
                id=server.id,
                name=server.name,
                host=server.host,
                username=server.username,
                logged_in=session is not None,
                cwd=cwd,
                readable=reason is None,
                reason=reason,
                writes=bool(session and session.get("writes")),
            )
        )
    return ServersReadable(servers=result)


class FileEntry(BaseModel):
    name: str
    kind: str  # file, directory, or outside (a link out of the current directory)
    size: int | None = None
    modified: str | None = None  # UTC


class FileListing(BaseModel):
    server: str
    path: str  # full path on the server
    cwd_note: str
    entries: list[FileEntry]


@router.get("/{server_id}/files")
async def list_server_files(server_id: str, request: Request, path: str = "") -> FileListing:
    """The files in the terminal's current directory, or in `path` below it."""
    server = await _reader(request, server_id)
    answer = await _keeper(server, "list", path=path or None, within=True)
    entries = sorted(answer["entries"], key=lambda e: (e["kind"] != "directory", e["name"].lower()))
    return FileListing(
        server=server.id,
        path=answer["path"],
        cwd_note="Paths are relative to the terminal's current directory; only it and the "
        "directories below it can be read.",
        entries=[
            FileEntry(
                name=e["name"],
                kind=e["kind"],
                size=e.get("size"),
                modified=datetime.fromtimestamp(e["modified"], UTC).strftime("%Y-%m-%d %H:%M")
                if e.get("modified") is not None
                else None,
            )
            for e in entries
        ],
    )


def _pattern(search: str, regex: bool, ignore_case: bool) -> re.Pattern:
    flags = re.IGNORECASE if ignore_case else 0
    try:
        return re.compile(search if regex else re.escape(search), flags)
    except re.error as exc:
        raise HTTPException(422, f"Not a regular expression: {exc}") from exc


async def _all_lines(request: Request, server: servers.Server, meta: _Meta) -> Iterator[str]:
    """Every line of the file: from the bytes already read when they hold it all, else from
    a copy."""
    if len(meta.head) >= meta.size:
        return iter(_decoded(meta.head))
    copy = await copies(request.app).get(server, meta.path, meta.size, meta.modified)
    return _file_lines(copy.local)


def _page(lines: Iterator[str], start: int, count: int) -> tuple[list[str], int, bool, int | None]:
    """Lines `start`.. (1-based), numbered: the text, the last line shown, whether the answer
    filled up, and the file's line count when it was read to the end."""
    out = _Out()
    last = start - 1
    total = 0
    for number, line in enumerate(lines, 1):
        total = number
        if start <= number < start + count and not out.full and out.add(_numbered(number, line)):
            last = number
    return out.lines, last, out.full, total


@router.get("/{server_id}/read", response_class=PlainTextResponse)
async def read_server_file(
    server_id: str,
    request: Request,
    path: str,
    start: int = 1,
    lines: int = PAGE_LINES,
    tail: int | None = None,
    search: str | None = None,
    regex: bool = False,
    ignore_case: bool = True,
    context: int = CONTEXT,
    last: bool = False,
):
    """A text file's lines, numbered: a page from `start`, the last `tail` lines, or the lines
    matching `search` with `context` lines around them."""
    server = await _reader(request, server_id)
    count = max(1, min(lines, MAX_LINES))
    meta = await _meta(server, path, head=keeper.MAX_READ)
    heading = _heading(server, meta)
    if _binary(meta.head):
        raise HTTPException(422, f"{meta.path} is not a text file.")

    if search:
        pattern = _pattern(search, regex, ignore_case)
        around = max(0, min(context, MAX_CONTEXT))
        source = await _all_lines(request, server, meta)
        text, summary = await asyncio.to_thread(
            _search, source, pattern, max(1, start), around, last
        )
        kind = " (regular expression)" if regex else ""
        return _answer(heading, f'Search for "{search}"{kind}: {summary}', *text)

    if tail is not None:
        want = max(1, min(tail, MAX_LINES))
        if meta.size <= len(meta.head):
            every = _decoded(meta.head)
            first = max(len(every) - want, 0)
            numbered = [_numbered(n, line) for n, line in enumerate(every[first:], first + 1)]
            known = f"of {len(every)}"
        else:
            answer = await _keeper(server, "read", path=meta.path, offset=-(MAX_BYTES + 1))
            every = _decoded(base64.b64decode(answer["data"]))[1:]  # the first may be cut
            numbered = [_numbered(None, line) for line in every[-want:]]
            known = "(line numbers unknown without reading the whole file)"
        out = _Out()
        for line in reversed(numbered):
            if not out.add(line):
                break
        shown = list(reversed(out.lines))
        return _answer(heading, f"Last {len(shown)} lines {known}:", *shown)

    begin = max(1, start)
    whole = len(meta.head) >= meta.size
    head = _decoded(meta.head)
    if not whole and meta.head and not meta.head.endswith(b"\n"):
        head = head[:-1]  # the last line read may be cut
    if not whole and len(head) >= begin + count:
        # The first bytes hold the page and the line after it: no copy needed.
        shown, end, _, _ = _page(iter(head), begin, count)
        return _answer(heading, f"Lines {begin}-{end}; the file goes on: start={end + 1}.", *shown)
    source = iter(head) if whole else await _all_lines(request, server, meta)
    shown, end, _, total = await asyncio.to_thread(_page, source, begin, count)
    if not shown:
        raise HTTPException(422, f"{meta.path} has {total} lines; start is past its end.")
    more = f"Lines {begin}-{end} of {total}"
    if end < total:
        more += f"; next page: start={end + 1}"
    return _answer(heading, more + ".", *shown)


def _search(
    lines: Iterator[str], pattern: re.Pattern, start: int, around: int, last: bool
) -> tuple[list[str], str]:
    """Matching lines with `around` lines each side, as numbered blocks."""
    before: list[tuple[int, str]] = []
    blocks: list[list[tuple[int, str]]] = []
    matches = 0
    after = 0
    total = 0
    for number, line in enumerate(lines, 1):
        total = number
        if number < start:
            continue
        hit = pattern.search(line) is not None
        if hit:
            matches += 1
            if not last and matches > MAX_MATCHES:
                continue
            if blocks and after > 0:
                blocks[-1].append((number, line))
            else:
                blocks.append([*before, (number, line)])
            after = around
            before = []
            if last and len(blocks) > MAX_MATCHES:
                blocks.pop(0)
        elif after > 0 and blocks:
            blocks[-1].append((number, line))
            after -= 1
        else:
            before = [*before, (number, line)][-around:] if around else []
    out = _Out()
    kept: list[list[str]] = []
    for block in reversed(blocks) if last else blocks:
        text = [_numbered(n, line) for n, line in block]
        if not all(out.add(t) for t in [*text, "--"]):
            break
        kept.append(text)
    if last:
        kept.reverse()
    lines_out = [line for i, text in enumerate(kept) for line in (["--"] if i else []) + text]
    summary = f"{matches} matching lines in lines {start}-{total}"
    if matches == 0:
        summary = f"no match in lines {start}-{total}"
    elif len(kept) < len(blocks) or matches > MAX_MATCHES:
        summary += (
            f"; only the {'last' if last else 'first'} ones are shown "
            "(narrow the search, or give start= to search further on)"
        )
    return lines_out, summary + "."


# ---------- an output read as the Inspect window reads it (D123c) ----------


async def _inspected(request: Request, server: servers.Server, path: str):
    meta = await _meta(server, path)
    if meta.size > inspection.MAX_SIZE:
        raise HTTPException(
            422,
            f"{meta.path} is {meta.size / 1e6:.0f} MB, more than the Inspect window reads; "
            "use read_server_file with search or tail.",
        )
    copy = await copies(request.app).get(server, meta.path, meta.size, meta.modified)
    if copy.read is None:
        try:
            copy.read = await asyncio.to_thread(inspection.read_path, copy.local, copy.name)
        except inspection.InspectFailed as exc:
            raise HTTPException(422, str(exc)) from exc
    return meta, copy.read


def _criteria(frame) -> dict[str, list] | None:
    if not frame.criteria:
        return None
    return {c.name: [c.value, c.threshold, c.converged] for c in frame.criteria}


def _structure_row(number: int, frame) -> dict:
    row: dict = {"n": number, "energy": frame.energy, "converged": frame.converged}
    if frame.point is not None:
        row["point"] = frame.point
    if frame.stage is not None:
        row["stage"] = frame.stage
    criteria = _criteria(frame)
    if criteria:
        row["criteria"] = criteria
    return row


def _step_summary(step: inspection.Step) -> dict:
    frames = step.frames
    rows = [_structure_row(n, f) for n, f in enumerate(frames, 1)]
    left_out = 0
    if len(rows) > MAX_STRUCTURES:
        left_out = len(rows) - MAX_STRUCTURES
        rows = rows[:50] + rows[-(MAX_STRUCTURES - 50) :]
    frequencies = step.frequencies
    summary: dict = {
        "step": step.index,
        "job_type": step.job_type,
        "route": step.route,
        "method": step.method,
        "basis": step.basis,
        "charge": step.charge,
        "multiplicity": step.multiplicity,
        "termination": step.termination,
        "energy": step.energy,
        "optimization_converged": step.optimization_converged,
        "frequency_count": len(frequencies),
        "imaginary": step.imaginary,
        "imaginary_frequencies": [f for f in frequencies if f < 0],
        "lowest_frequencies": sorted(frequencies)[:10],
        "scan": step.scan,
        "scan_points": step.points or None,
        "structures": len(frames),
        "atoms": len(frames[-1].rows) if frames else None,
        "missing": step.missing,
        "structure_list": rows,
    }
    if left_out:
        summary["structures_left_out"] = (
            f"structures 51-{50 + left_out} are left out of this list; "
            "get_server_structure reads any of them"
        )
    return summary


class ServerFileSummary(BaseModel):
    server: str
    path: str
    size: int
    modified: str
    program: str
    version: str | None
    warnings: list[str]
    units: str
    steps: list[dict]


@router.get("/{server_id}/summary")
async def summarize_server_file(server_id: str, request: Request, path: str) -> ServerFileSummary:
    """An output read as the Inspect window reads it (D123c): per job step the route, charge
    and multiplicity, termination, last energy, convergence, frequencies, and per structure
    its energy and convergence criteria."""
    server = await _reader(request, server_id)
    meta, read = await _inspected(request, server, path)
    return ServerFileSummary(
        server=server.id,
        path=servers.origin_path(server, meta.path),
        size=meta.size,
        modified=_when(meta.modified),
        program=read.program,
        version=read.version,
        warnings=read.warnings,
        units="energies in hartree; frequencies in cm-1; criteria as [value, threshold, met]",
        steps=[_step_summary(step) for step in read.steps],
    )


@router.get("/{server_id}/structure", response_class=PlainTextResponse)
async def get_server_structure(
    server_id: str, request: Request, path: str, step: int | None = None, frame: int = -1
):
    """One structure of an output or xyz file as XYZ text: by default the last of the last
    step that has structures."""
    server = await _reader(request, server_id)
    meta, read = await _inspected(request, server, path)
    with_frames = [s for s in read.steps if s.frames]
    if not with_frames:
        raise HTTPException(422, f"{meta.path} holds no structures.")
    if step is None:
        chosen = with_frames[-1]
    else:
        chosen = next((s for s in read.steps if s.index == step), None)
        if chosen is None or not chosen.frames:
            have = ", ".join(str(s.index) for s in with_frames)
            raise HTTPException(422, f"Step {step} has no structures; steps with some: {have}.")
    frames = chosen.frames
    number = frame if frame > 0 else len(frames) + 1 + frame
    if not 1 <= number <= len(frames):
        raise HTTPException(422, f"Step {chosen.index} has {len(frames)} structures.")
    found = frames[number - 1]
    atoms = [xyz.Atom(e, x, y, z) for e, x, y, z in found.rows]
    comment = f"{servers.origin_path(server, meta.path)} step {chosen.index} structure {number}"
    comment += f" of {len(frames)}"
    if found.energy is not None:
        comment += f" E={found.energy:.8f}"
    if found.converged:
        comment += " converged"
    return PlainTextResponse(xyz.format_xyz(atoms, comment=comment))


# ---------- writing new files (D126) ----------


class WriteIn(BaseModel):
    path: str
    text: str
    based_on: str | None = None


class WriteOut(BaseModel):
    server: str
    path: str  # where it was written, as server:/full/path
    size: int  # bytes
    folders: list[str]  # folders made for it
    based_on: str | None = None  # the original of an edited copy, unchanged
    diff: str | None = None  # the copy against its original, unified, for the user
    note: str


def _difference(original: bytes, name: str, text: str, new_name: str) -> str:
    before = original.decode("utf-8", errors="replace").splitlines()
    after = text.replace("\r\n", "\n").splitlines()
    lines = list(difflib.unified_diff(before, after, name, new_name, n=2, lineterm=""))
    if not lines:
        return "(no difference)"
    if len(lines) > MAX_DIFF:
        lines = lines[:MAX_DIFF] + [f"[… {len(lines) - MAX_DIFF} more lines of difference]"]
    return "\n".join(lines)


@router.post("/{server_id}/write")
async def write_server_file(server_id: str, body: WriteIn, request: Request) -> WriteOut:
    """D126: a new text file in the terminal's current directory or below, only while the user
    lets Claude write. An existing file is never replaced, edited or deleted: to change one,
    write a copy under a new name with `based_on` naming the original, and the answer shows the
    difference."""
    server = await _reader(request, server_id, write=True)
    original: _Meta | None = None
    if body.based_on:
        original = await _meta(server, body.based_on, head=keeper.MAX_READ)
        if _binary(original.head):
            raise HTTPException(422, f"{original.path} is not a text file.")
    answer = await _keeper(
        server,
        "write",
        path=body.path,
        text=body.text,
        based_on=original.path if original else None,
    )
    if not answer.get("written"):
        free = answer.get("free")
        instead = f" Write it as {posixpath.basename(free)} instead." if free else ""
        raise HTTPException(
            409,
            f"{answer['path']} is there already, and Claude never replaces a file.{instead} "
            "The user deletes the original themselves if they want.",
        )
    diff = None
    note = f"Written as a new file on {server.name}; nothing else there changed."
    if original is not None:
        if original.size > len(original.head):
            diff = "(the original is too large to compare)"
        else:
            diff = _difference(
                original.head,
                posixpath.basename(original.path),
                body.text,
                posixpath.basename(answer["path"]),
            )
        note += (
            f" {original.path} is unchanged; show the user the difference. If they want the "
            "original gone they delete it themselves (you may give them the rm command to copy, "
            "never run it)."
        )
    return WriteOut(
        server=server.id,
        path=servers.origin_path(server, answer["path"]),
        size=answer["size"],
        folders=answer.get("folders") or [],
        based_on=servers.origin_path(server, original.path) if original else None,
        diff=diff,
        note=note,
    )
