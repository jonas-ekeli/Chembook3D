"""D235, FR-INV-04, FR-INV-05: making a playground of an investigation, the playgrounds the start
page lists under their originals, and discarding one. None is an MCP tool: Claude makes a
playground only through the Work on its own switch, and never merges or discards one (Q69)."""

from pathlib import Path

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
    _set_open,
    _staging,
    close_and_push,
    open_pulled,
)
from chembook3d.investigation import InvestigationError, InvestigationLocked, open_investigation

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
