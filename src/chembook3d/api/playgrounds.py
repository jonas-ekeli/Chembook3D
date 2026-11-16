"""D235, FR-INV-04…06: making a playground of an investigation, the playgrounds the start page
lists under their originals, discarding one and merging one into its original. None is an MCP
tool: Claude makes a playground only through the Work on its own switch, and never merges or
discards one (Q69)."""

from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from chembook3d import autonomy, local_jobs, playgrounds, sync
from chembook3d import settings as app_settings
from chembook3d.api.claude import _panel, _refusal
from chembook3d.api.routes import (
    _SWITCHING,
    InvestigationOut,
    UtcDatetime,
    _begin_switch,
    _investigation,
    _investigation_out,
    _set_open,
    _staging,
    close_and_push,
    open_pulled,
)
from chembook3d.investigation import (
    Investigation,
    InvestigationError,
    InvestigationLocked,
    acquire_ownership,
    open_investigation,
)
from chembook3d.services import playground_merge

router = APIRouter(prefix="/api")


class AloneIn(BaseModel):
    """D235i: "Make a playground first" on the Work on its own warning: the switch's choices."""

    job_limit: int = Field(autonomy.DEFAULT_JOB_LIMIT, ge=60, le=14 * 24 * 3600)
    cloud: bool = False


class PlaygroundIn(BaseModel):
    folder: str  # the original's folder
    name: str | None = None  # None: the first "Playground N" not taken (A165)
    force: bool = False
    upgrade: bool = False
    # D235i: turn Work on its own on in the new playground, as the switch does
    alone: AloneIn | None = None


class DiscardIn(BaseModel):
    folder: str


class DiscardPreviewOut(BaseModel):
    folder: str
    name: str
    original_name: str
    files: int
    size: int  # bytes in the folder
    frees: int  # bytes discarding frees: files not hard-linked with the original's


class PlaygroundMadeOut(InvestigationOut):
    # what the app says once the playground is open (files copied, jobs left in the original)
    notice: str | None = None


class PlaygroundRowOut(BaseModel):
    folder: str
    name: str
    created_at: UtcDatetime | None = None
    merged_at: UtcDatetime | None = None
    changed: int | None = None
    foreign: bool = False


@router.get("/playgrounds", response_model=dict[str, list[PlaygroundRowOut]])
def list_playgrounds():
    """The playgrounds of each folder on the recent list, by that folder as the list holds it.
    Found by looking in its playgrounds/ folder; nothing is opened (D235)."""
    found = {}
    for folder in app_settings.load().recent:
        listed = playgrounds.rows(Path(folder))
        if listed:
            found[folder] = listed
    return found


def _notice(made: playgrounds.Made, busy: bool) -> str | None:
    said = []
    if made.copied:
        said.append(
            f"{made.copied} file{'s' if made.copied != 1 else ''} could not be hard-linked on "
            "this disk and were copied."
        )
    if busy:
        said.append("Local jobs are running in the original; their results will arrive there only.")
    return " ".join(said) or None


def _from_the_page(request: Request, what: str) -> None:
    """As the Work on its own switch (api/claude.py set_autonomy): the app's own page only, never
    Claude, which reaches the app without an origin (Q69)."""
    if _refusal(request.scope, request.headers) is not None or not request.headers.get("origin"):
        raise HTTPException(403, f"Only the app's own page can {what}")


@router.post("/playgrounds", response_model=PlaygroundMadeOut)
def make_playground(body: PlaygroundIn, request: Request):
    """New playground…: open the original as Open does (a linked one is pulled first), copy it
    into playgrounds/<name>/, close it again (pushing a linked one) and open the playground."""
    if body.alone is not None:
        _from_the_page(request, "change how Claude works")  # as the switch itself
    switch = _begin_switch(request)
    folder = Path(body.folder)
    name = body.name if body.name is not None else playgrounds.next_name(folder)
    current = getattr(request.app.state, "investigation", None)
    try:
        if current is not None and folder.resolve() == current.folder.resolve():
            made = playgrounds.make(current, name)
            busy = local_jobs.busy(current.folder)
        else:
            original, _status = open_pulled(folder, body.force, body.upgrade)
            try:
                made = playgrounds.make(original, name)
                busy = local_jobs.busy(original.folder)
                if sync.is_linked(original.folder):
                    sync.write_config_files(original.folder)  # playgrounds/ in .gitignore
            finally:
                close_and_push(original)
        investigation = open_investigation(made.folder)
    except InvestigationError as exc:
        raise HTTPException(422, str(exc)) from exc
    except OSError as exc:  # a full disk, a folder the user may not write to
        raise HTTPException(422, f"The playground could not be made: {exc}") from exc
    out = _set_open(request, investigation, switch)
    if body.alone is not None:
        # D235i: on in the playground, with no backup (its base and its original are the copy);
        # every panel terminal restarts, as when the switch changes.
        state: autonomy.Autonomy = request.app.state.live.autonomy
        state.turn_on(investigation.folder, body.alone.job_limit, body.alone.cloud, backup=False)
        _panel(request.app).close_all()
    return PlaygroundMadeOut(**out.model_dump(), notice=_notice(made, busy))


@router.get("/playgrounds/discard-preview", response_model=DiscardPreviewOut)
def discard_preview(folder: str):
    """What Discard's confirmation names: the playground, its original and its size."""
    try:
        return playgrounds.discard_preview(Path(folder))
    except InvestigationError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.post("/playgrounds/discard", status_code=204)
def discard_playground(body: DiscardIn, request: Request):
    """Discard…: the playground's folder goes to the recycle bin (D235g, Q68). Closed first when
    it is the open investigation; refused while another app has it open."""
    _from_the_page(request, "discard a playground")
    folder = Path(body.folder)
    try:
        playgrounds.check(folder)  # a playground, before anything is closed
    except InvestigationError as exc:
        raise HTTPException(422, str(exc)) from exc
    with _SWITCHING:
        current = getattr(request.app.state, "investigation", None)
        closing = current is not None and folder.resolve() == current.folder.resolve()
        if closing:
            _begin_switch(request)
            request.app.state.investigation = None
    if closing:
        close_and_push(current)
        _staging(request).clear()
        state: autonomy.Autonomy = request.app.state.live.autonomy
        if state.on_for(current.folder):
            state.turn_off()
    try:
        playgrounds.discard(folder)
    except InvestigationLocked as exc:
        raise HTTPException(409, {"locked": exc.lock, "message": str(exc)}) from exc
    except InvestigationError as exc:
        raise HTTPException(422, str(exc)) from exc


# ---------- merge (D235h, FR-INV-06) ----------


class MergeStartIn(BaseModel):
    folder: str  # the playground's folder
    force: bool = False  # as Open: open the original although its lock file says it is open
    upgrade: bool = False


class MergeIn(BaseModel):
    folder: str  # the playground's folder
    ticked: list[str] = Field(default_factory=list)  # change ids; what they need comes along


class MergeFieldOut(BaseModel):
    field: str
    base: Any = None
    playground: Any = None
    original: Any = None
    conflict: bool = False  # changed to different values on both sides


class MergeChangeOut(BaseModel):
    id: str  # "<record_type>:<record_id>"
    record_type: str
    record_id: str
    kind: str  # added | changed | removed | conflict
    label: str
    summary: str
    fields: list[MergeFieldOut]
    conflict: str | None = None  # why it is a conflict; ticking takes the playground's side
    ticked: bool  # the default
    requires: list[str]  # change ids it brings
    blocked: str | None = None  # why it cannot be merged


class MergeSectionOut(BaseModel):
    key: str
    title: str
    changes: list[MergeChangeOut]


class MergePlaygroundOut(BaseModel):
    name: str
    folder: str
    created_at: UtcDatetime | None = None


class MergeReviewOut(BaseModel):
    playground: MergePlaygroundOut
    original_name: str
    changed_since: int  # the original's history entries since the playground was made
    sections: list[MergeSectionOut]


class MergeStartOut(InvestigationOut):
    review: MergeReviewOut


class MergeResultOut(BaseModel):
    counts: dict[str, int]  # added, changed, removed
    brought: list[str]  # changes not ticked but needed by a ticked one
    blockers: list[str]
    backup: str | None = None  # the original's copy in backups/, once merged
    history_id: int | None = None  # the merge's entry in the original's history


def _merge_errors(exc: InvestigationError) -> HTTPException:
    if isinstance(exc, InvestigationLocked):
        return HTTPException(409, {"locked": exc.lock, "message": str(exc)})
    return HTTPException(422, str(exc))


def _review(investigation: Investigation, folder: Path) -> dict[str, Any]:
    try:
        return playground_merge.review(investigation, folder)
    except InvestigationError as exc:
        raise _merge_errors(exc) from exc


@router.post("/playgrounds/merge/start", response_model=MergeStartOut)
def start_merge(body: MergeStartIn, request: Request):
    """Merge…: close what is open, open the playground's original as Open does (a linked one is
    pulled first; lock and upgrade answers as Open's) and review the playground's changes."""
    _from_the_page(request, "merge a playground")
    try:
        found = playground_merge.playground(Path(body.folder))
    except InvestigationError as exc:
        raise _merge_errors(exc) from exc
    current = getattr(request.app.state, "investigation", None)
    open_here = current is not None and current.folder.resolve() == found.folder
    if not open_here:
        try:  # refused before anything is closed while another app has the playground open
            acquire_ownership(found.folder).release()
        except InvestigationError as exc:
            raise _merge_errors(exc) from exc
    switch = _begin_switch(request)
    if current is not None and current.folder.resolve() == found.original:
        original = current
        out = _investigation_out(current)
    else:
        original, status = open_pulled(found.original, body.force, body.upgrade)
        out = _set_open(request, original, switch, status)  # closes the playground if open
    if open_here:
        state: autonomy.Autonomy = request.app.state.live.autonomy
        if state.on_for(current.folder):
            state.turn_off()
    # The original just opened, not the tab's notebook: the tab still names the playground
    # until the answer arrives, so the identity check of _investigation does not apply here.
    return MergeStartOut(**out.model_dump(), review=_review(original, found.folder))


@router.get("/playgrounds/merge/review", response_model=MergeReviewOut)
def merge_review(folder: str, request: Request):
    """The review again; the open investigation must be the playground's original."""
    return _review(_investigation(request), Path(folder))


@router.post("/playgrounds/merge/preview", response_model=MergeResultOut)
def merge_preview(body: MergeIn, request: Request):
    """The merge of the ticked changes, run in a transaction that is rolled back: its counts,
    what it brings and what blocks it. Nothing is written."""
    _from_the_page(request, "merge a playground")
    try:
        return playground_merge.merge(
            _investigation(request), Path(body.folder), body.ticked, dry_run=True
        )
    except InvestigationError as exc:
        raise _merge_errors(exc) from exc


@router.post("/playgrounds/merge", response_model=MergeResultOut)
def merge_playground(body: MergeIn, request: Request):
    """Merge the ticked changes into the open original: backed up first, one transaction, every
    merged change in its history; the playground is read-only afterwards (D235h, Q64). Refused
    with the blockers, changing nothing, when anything blocks it."""
    _from_the_page(request, "merge a playground")
    try:
        return playground_merge.merge(_investigation(request), Path(body.folder), body.ticked)
    except playground_merge.MergeBlocked as exc:
        raise HTTPException(409, {"blockers": exc.result["blockers"], "message": str(exc)}) from exc
    except InvestigationError as exc:
        raise _merge_errors(exc) from exc
