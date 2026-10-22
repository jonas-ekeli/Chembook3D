"""The scan editor's API (D135): scans from files on this computer or on a logged-in server,
put together into one path without importing anything.

An editor holds the files added to it (a server's file copied into a temporary folder, as the
Inspect window does, deleted when the editor closes) and the scans read from them. The path is
the page's: it sends its pieces, and gets back the joined path with its checks and the file it
would save. Only the app's own page uses this, as for the server connection (D122).
"""

import asyncio
import posixpath
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from chembook3d.api.inspect import _fetch, _Open
from chembook3d.api.remote import _check, _keeper, _saved
from chembook3d.api.routes import _investigation
from chembook3d.models import Calculation, new_id
from chembook3d.parsers import crest
from chembook3d.remote import servers
from chembook3d.services import files as file_service
from chembook3d.services import imports, inspection
from chembook3d.services import scan_editor as editor

router = APIRouter(prefix="/api/scan-editor")

MAX_OPEN = 4  # editors open at once; opening another closes the oldest
MAX_FILES = 60  # files in one editor


@dataclass
class _Editor:
    id: str
    files: dict[str, _Open] = field(default_factory=dict)
    scans: dict[str, editor.Scan] = field(default_factory=dict)
    stored: set[str] = field(default_factory=set)  # files that are an imported calculation's copy
    opened: float = field(default_factory=time.monotonic)

    def discard(self) -> None:
        for item in self.files.values():
            item.discard_copy()
        self.files.clear()


class ScanEditors:
    """The open scan editors of the running app."""

    def __init__(self) -> None:
        self.open: dict[str, _Editor] = {}

    def add(self, item: _Editor) -> None:
        while len(self.open) >= MAX_OPEN:
            oldest = min(self.open.values(), key=lambda o: o.opened)
            self.close(oldest.id)
        self.open[item.id] = item

    def close(self, editor_id: str) -> None:
        item = self.open.pop(editor_id, None)
        if item is not None:
            item.discard()

    def close_all(self) -> None:
        for key in list(self.open):
            self.close(key)


def scan_editors(app) -> ScanEditors:
    found = getattr(app.state, "scan_editors", None)
    if found is None:
        found = app.state.scan_editors = ScanEditors()
    return found


class TargetIn(BaseModel):
    path: str = ""  # on this computer, or a full path on the server
    server: str | None = None  # a logged-in server's id
    calculation: str | None = None  # an imported scan: its step of the investigation's copy


class FilesIn(BaseModel):
    targets: list[TargetIn]


class PieceIO(BaseModel):
    scan: str
    first: int  # into the scan's points; the piece runs from first to last (reversed if after)
    last: int


class CrossingIO(BaseModel):
    a: str  # the branch lower before the crossing
    b: str  # the branch lower after it
    x: float
    rmsd: float | None = None
    text: str = ""


class PathIn(BaseModel):
    pieces: list[PieceIO]
    add: str | None = None  # a scan to put on the path where it meets it
    chain: list[str] | None = None  # scans to join on by themselves where they meet it
    replace: str | None = None  # a finer scan to put in place of the path's points it covers
    between: list[int] | None = None  # ... only within these two frames of the path
    crossing: CrossingIO | None = None  # two branches to join where they cross


class PointRef(BaseModel):
    scan: str
    index: int


class CompareIn(BaseModel):
    points: list[PointRef]


class MatchIn(BaseModel):
    to: str  # the scan whose atom order to take


class PointOut(BaseModel):
    geometry: list[list[Any]]
    energy: float | None
    value: float
    number: int
    structure: int
    source: str


class ScanOut(BaseModel):
    key: str
    file: str
    path: str
    server_name: str | None
    step: int
    steps: int
    program: str
    version: str | None
    level: str
    basis_named: bool
    energy_group: str  # D167: scans drawn against one energy zero share it
    charge: int | None
    multiplicity: int | None
    atoms: list[int]
    coordinate: str
    unit: str
    found_by: str  # "file" or "structures"
    left_out: int
    ended: bool
    joined: bool
    restarted: bool
    renumbered: str
    points: list[PointOut]


class FileProblemOut(BaseModel):
    path: str
    problem: str


class EditorOut(BaseModel):
    id: str
    scans: list[ScanOut]
    added: list[str]  # the scans the files just added hold
    problems: list[FileProblemOut]
    crossings: list[CrossingIO]  # branches of one coordinate whose energies cross
    note: str | None = None  # what matching atoms did
    pieces: list[PieceIO] | None = None  # after a refresh: the path's pieces on the new points


class RefreshIn(BaseModel):
    pieces: list[PieceIO] = []


class SaveIn(BaseModel):
    server: str
    name: str
    text: str


class SavedOut(BaseModel):
    path: str  # where it was written on the server
    renamed: bool  # a file of that name was there, so it took the next free name


class FrameOut(BaseModel):
    geometry: list[list[Any]]
    energy: float | None
    x: float
    piece: int
    scan: str
    index: int
    number: int
    structure: int
    source: str


class MarkOut(BaseModel):
    at: int
    kind: str
    text: str
    warning: bool


class JoinOut(BaseModel):
    after: int
    at: int
    shared: bool
    rmsd: float | None
    rmsd_active: float | None
    energy_difference: float | None
    ok: bool


class OfferOut(BaseModel):
    scan: str
    points: int
    replaced: int
    low: float
    high: float
    text: str


class ComparedOut(BaseModel):
    scan: str
    index: int
    geometry: list[list[Any]]
    energy: float | None
    value: float
    rmsd: float | None  # Å to the first point, fitted, never mirrored


class JoinedOut(BaseModel):
    pieces: list[PieceIO]
    notes: list[str]  # what adding or chaining did
    frames: list[FrameOut]
    axis: str
    atoms: list[int]
    unit: str
    label: str
    joins: list[JoinOut]
    marks: list[MarkOut]
    top: int | None
    barrier: float | None  # kcal/mol
    level: str
    level_notes: list[str]
    blocked: str | None
    mismatch: str | None
    offers: list[OfferOut]
    text: str


def _round(rows: list[list[Any]]) -> list[list[Any]]:
    return [[e, round(x, 6), round(y, 6), round(z, 6)] for e, x, y, z in rows]


def _scan_out(scan: editor.Scan, server_name: str | None, group: str) -> ScanOut:
    return ScanOut(
        key=scan.key,
        file=scan.file,
        path=scan.path,
        server_name=server_name,
        step=scan.step,
        steps=scan.steps,
        program=scan.program,
        version=scan.version,
        level=scan.level_label,
        basis_named=scan.basis_named,
        energy_group=group,
        charge=scan.charge,
        multiplicity=scan.multiplicity,
        atoms=scan.atoms,
        coordinate=scan.coordinate,
        unit=scan.unit,
        found_by=scan.found_by,
        left_out=scan.left_out,
        ended=scan.ended,
        joined=scan.joined,
        restarted=scan.restarted,
        renumbered=scan.renumbered,
        points=[
            PointOut(
                geometry=_round(p.rows),
                energy=p.energy,
                value=p.value,
                number=p.number,
                structure=p.structure,
                source=p.source,
            )
            for p in scan.points
        ],
    )


def _found(request: Request, editor_id: str) -> _Editor:
    item = scan_editors(request.app).open.get(editor_id)
    if item is None:
        raise HTTPException(404, "This scan editor has been closed; open the files again.")
    return item


def _server_name(item: _Open) -> str | None:
    server = servers.find(item.server_id) if item.server_id else None
    return server.name if server else None


def _read_scans(
    item: _Open, file_key: str, name: str | None = None, shown: str | None = None
) -> list[editor.Scan]:
    """The scans of one file (run in a thread)."""
    local = item.local
    server = servers.find(item.server_id) if item.server_id else None
    name = name or (posixpath.basename(item.path) if item.server_id else local.name)
    shown = shown or (servers.origin_path(server, item.path) if server else str(local))
    try:
        text = inspection.read_text_of(local)
        if editor.is_joined_scan(text):
            return editor.read_joined(text, name, shown, file_key)
        parsed, _ = inspection.parse_text(text, name, local.parent)
    except (inspection.InspectFailed, imports.ImportFailed, editor.ScanEditorError) as exc:
        raise editor.ScanEditorError(str(exc)) from exc
    if isinstance(parsed, crest.Ensemble):
        raise editor.ScanEditorError(f"{name} is a conformer ensemble, not a scan.")
    found = editor.scans_of(parsed, name, shown, file_key)
    if not found:
        raise editor.ScanEditorError(
            f"{name} holds no scan this editor can read: no job step drives a distance, angle "
            "or dihedral through two or more points."
        )
    return found


def _stored(request: Request, calculation_id: str) -> tuple[Path, str, str, int]:
    """An imported calculation's copy in the investigation: where it is, its name, where it
    came from, and its job step."""
    investigation = _investigation(request)
    with investigation.sessions.begin() as session:
        calculation = session.get(Calculation, calculation_id)
        if calculation is None or calculation.source_file is None:
            raise editor.ScanEditorError("That calculation was not imported from a file.")
        source = calculation.source_file
        path = file_service.absolute_path(investigation.folder, source)
        origin = source.origin_path or source.original_name
        return path, source.original_name, origin, calculation.step_index or 1


async def _add_files(
    request: Request, item: _Editor, targets: list[TargetIn]
) -> tuple[list[str], list[FileProblemOut]]:
    added: list[str] = []
    problems: list[FileProblemOut] = []
    if len(item.files) + len(targets) > MAX_FILES:
        raise HTTPException(422, f"The scan editor holds at most {MAX_FILES} files.")
    for target in targets:
        name = shown = None
        step = None
        if target.calculation is not None:
            try:
                local, name, shown, step = _stored(request, target.calculation)
            except editor.ScanEditorError as exc:
                problems.append(FileProblemOut(path=target.path, problem=str(exc)))
                continue
            opened = _Open(new_id(), str(local), None)
        elif target.server is not None:
            _saved(target.server)
            opened = _Open(new_id(), target.path, target.server)
            try:
                await _fetch(opened)
            except HTTPException as exc:
                problems.append(FileProblemOut(path=target.path, problem=str(exc.detail)))
                continue
        else:
            path = Path(target.path).expanduser()
            if not path.is_file():
                problems.append(FileProblemOut(path=target.path, problem=f"{path} is not a file"))
                continue
            opened = _Open(new_id(), str(path.resolve()), None)
        try:
            found = await asyncio.to_thread(_read_scans, opened, opened.id, name, shown)
            if step is not None:
                found = [s for s in found if s.step == step]
                if not found:
                    raise editor.ScanEditorError(f"Step {step} of {name} is not a scan.")
        except editor.ScanEditorError as exc:
            opened.discard_copy()
            problems.append(FileProblemOut(path=target.path or name or "", problem=str(exc)))
            continue
        item.files[opened.id] = opened
        if target.calculation is not None:
            item.stored.add(opened.id)
        for scan in found:
            item.scans[scan.key] = scan
            added.append(scan.key)
    return added, problems


def _editor_out(
    item: _Editor, added: list[str], problems: list[FileProblemOut], note: str | None = None
) -> EditorOut:
    names = {key: _server_name(f) for key, f in item.files.items()}
    groups = editor.energy_groups(item.scans)
    return EditorOut(
        id=item.id,
        scans=[
            _scan_out(s, names.get(s.key.split(":")[0]), groups[s.key]) for s in item.scans.values()
        ],
        added=added,
        problems=problems,
        crossings=[CrossingIO(**c.__dict__) for c in editor.crossings(item.scans)],
        note=note,
    )


@router.post("")
async def open_editor(body: FilesIn, request: Request) -> EditorOut:
    """A new scan editor holding the scans of these files (none is fine)."""
    _check(request)
    item = _Editor(new_id())
    try:
        added, problems = await _add_files(request, item, body.targets)
    except BaseException:
        item.discard()
        raise
    scan_editors(request.app).add(item)
    return _editor_out(item, added, problems)


@router.post("/{editor_id}/files")
async def add_files(editor_id: str, body: FilesIn, request: Request) -> EditorOut:
    """More files read into the editor; their scans are listed, not put on the path."""
    _check(request)
    item = _found(request, editor_id)
    added, problems = await _add_files(request, item, body.targets)
    return _editor_out(item, added, problems)


@router.delete("/{editor_id}/scans/{scan_key}", status_code=204)
def remove_scan(editor_id: str, scan_key: str, request: Request) -> None:
    """A scan taken out of the editor; its file goes when none of its scans is left."""
    _check(request)
    item = _found(request, editor_id)
    item.scans.pop(scan_key, None)
    file_key = scan_key.split(":")[0]
    if not any(k.split(":")[0] == file_key for k in item.scans):
        found = item.files.pop(file_key, None)
        if found is not None:
            found.discard_copy()


@router.post("/{editor_id}/path")
def joined_path(editor_id: str, body: PathIn, request: Request) -> JoinedOut:
    """The path these pieces make, after putting `add` on it or chaining `chain` onto it, with
    the checks at each join and the file it saves as."""
    _check(request)
    item = _found(request, editor_id)
    pieces = [editor.Piece(p.scan, p.first, p.last) for p in body.pieces]
    notes: list[str] = []
    try:
        editor.check_pieces(item.scans, pieces)
        if body.add is not None:
            if body.add not in item.scans:
                raise editor.ScanEditorError("That scan is no longer in the editor.")
            pieces, note = editor.place(item.scans, pieces, body.add)
            notes.append(note)
        if body.chain:
            keys = [k for k in body.chain if k in item.scans]
            pieces, chained = editor.chain(item.scans, keys, pieces)
            notes += chained
        if body.replace is not None:
            if body.replace not in item.scans:
                raise editor.ScanEditorError("That scan is no longer in the editor.")
            between = tuple(body.between) if body.between and len(body.between) == 2 else None
            pieces, note = editor.replace(item.scans, pieces, body.replace, between)
            notes.append(note)
        if body.crossing is not None:
            if body.crossing.a not in item.scans or body.crossing.b not in item.scans:
                raise editor.ScanEditorError(
                    "A branch of that crossing is no longer in the editor."
                )
            crossing = editor.Crossing(**body.crossing.model_dump())
            pieces, note = editor.cut_at_crossing(item.scans, pieces, crossing)
            notes.append(note)
        joined = editor.join(item.scans, pieces)
    except editor.ScanEditorError as exc:
        raise HTTPException(422, str(exc)) from exc
    return JoinedOut(
        pieces=[PieceIO(scan=p.scan, first=p.first, last=p.last) for p in pieces],
        notes=notes,
        frames=[
            FrameOut(
                geometry=_round(f.rows),
                energy=f.energy,
                x=f.x,
                piece=f.piece,
                scan=f.scan,
                index=f.index,
                number=f.number,
                structure=f.structure,
                source=f.source,
            )
            for f in joined.frames
        ],
        axis=joined.axis,
        atoms=joined.atoms,
        unit=joined.unit,
        label=joined.label,
        joins=[JoinOut(**j.__dict__) for j in joined.joins],
        marks=[MarkOut(**m.__dict__) for m in joined.marks],
        top=joined.top,
        barrier=joined.barrier,
        level=joined.level_label,
        level_notes=joined.notes,
        blocked=joined.blocked,
        mismatch=joined.mismatch,
        offers=[OfferOut(**o.__dict__) for o in joined.offers],
        text=joined.text,
    )


@router.post("/{editor_id}/compare")
def compare_points(editor_id: str, body: CompareIn, request: Request) -> list[ComparedOut]:
    """Points of several scans fitted onto the first, to see how alike they are."""
    _check(request)
    item = _found(request, editor_id)
    try:
        found = editor.compare(item.scans, [(p.scan, p.index) for p in body.points])
    except editor.ScanEditorError as exc:
        raise HTTPException(422, str(exc)) from exc
    return [ComparedOut(**{**c, "geometry": _round(c["geometry"])}) for c in found]


@router.post("/{editor_id}/scans/{scan_key}/match")
def match_scan(editor_id: str, scan_key: str, body: MatchIn, request: Request) -> EditorOut:
    """A scan whose atoms are numbered in another order renumbered to another scan's (D113)."""
    _check(request)
    item = _found(request, editor_id)
    if scan_key not in item.scans or body.to not in item.scans:
        raise HTTPException(404, "That scan is no longer in the editor.")
    try:
        note = editor.match_atoms(item.scans, scan_key, body.to)
    except editor.ScanEditorError as exc:
        raise HTTPException(422, str(exc)) from exc
    return _editor_out(item, [], [], note)


@router.post("/{editor_id}/refresh")
async def refresh_scans(editor_id: str, body: RefreshIn, request: Request) -> EditorOut:
    """Every file read again (a server's copied again), for scans still running (D138). The
    scans keep their keys and atom order, and the path's pieces come back moved onto the new
    points."""
    _check(request)
    item = _found(request, editor_id)
    old = dict(item.scans)
    problems: list[FileProblemOut] = []
    for file_key, opened in list(item.files.items()):
        mine = [s for s in old.values() if s.key.split(":")[0] == file_key]
        if file_key in item.stored or not mine:
            continue
        name, shown = mine[0].file, mine[0].path
        try:
            if opened.server_id is not None:
                await _fetch(opened)
            found = await asyncio.to_thread(_read_scans, opened, file_key, name, shown)
        except HTTPException as exc:
            problems.append(FileProblemOut(path=shown, problem=str(exc.detail)))
            continue
        except editor.ScanEditorError as exc:
            problems.append(FileProblemOut(path=shown, problem=str(exc)))
            continue
        fresh = {s.key: s for s in found}
        for scan in mine:
            if scan.key in fresh:
                item.scans[scan.key] = editor.refreshed(scan, fresh[scan.key])
            else:
                problems.append(
                    FileProblemOut(
                        path=shown,
                        problem=f"Step {scan.step} of {name} is no longer read as a scan; "
                        "it is kept as it was.",
                    )
                )
    pieces = [editor.Piece(p.scan, p.first, p.last) for p in body.pieces]
    moved = editor.carry(old, item.scans, pieces)
    out = _editor_out(item, [], problems)
    out.pieces = [PieceIO(scan=p.scan, first=p.first, last=p.last) for p in moved]
    return out


@router.post("/{editor_id}/save-to-server")
async def save_to_server(editor_id: str, body: SaveIn, request: Request) -> SavedOut:
    """The joined scan written into the server terminal's current directory, never over a
    file there: a name taken gives the next free one, name_v2.xyz (D138, as D126)."""
    _check(request)
    _found(request, editor_id)
    _saved(body.server)
    if not editor.is_joined_scan(body.text):
        raise HTTPException(422, "Only a joined scan from this editor is saved to a server.")
    answer = await _keeper(
        "upload", server=body.server, name=body.name, text=body.text, replace=False, free=True
    )
    if not answer.get("sent"):
        # A keeper from before D138 does not take the next free name.
        raise HTTPException(
            409,
            f"A file named {body.name} is there already. Log out of the server and in again "
            "so the connection can save under the next free name.",
        )
    return SavedOut(path=answer["path"], renamed=bool(answer.get("exists")))


@router.delete("/{editor_id}", status_code=204)
def close_editor(editor_id: str, request: Request) -> None:
    """The window closed: its copies of servers' files are deleted."""
    _check(request)
    scan_editors(request.app).close(editor_id)
