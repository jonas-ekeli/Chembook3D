"""D235, FR-INV-04: making a playground of an investigation, and the playgrounds the start page
lists under their originals. Neither is an MCP tool: Claude makes a playground only through the
Work on its own switch, and never merges or discards one (Q69)."""

from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from chembook3d import local_jobs, playgrounds, sync
from chembook3d import settings as app_settings
from chembook3d.api.routes import (
    InvestigationOut,
    UtcDatetime,
    _begin_switch,
    _set_open,
    close_and_push,
    open_pulled,
)
from chembook3d.investigation import InvestigationError, open_investigation

router = APIRouter(prefix="/api")


class PlaygroundIn(BaseModel):
    folder: str  # the original's folder
    name: str
    force: bool = False
    upgrade: bool = False


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


@router.post("/playgrounds", response_model=PlaygroundMadeOut)
def make_playground(body: PlaygroundIn, request: Request):
    """New playground…: open the original as Open does (a linked one is pulled first), copy it
    into playgrounds/<name>/, close it again (pushing a linked one) and open the playground."""
    switch = _begin_switch(request)
    folder = Path(body.folder)
    current = getattr(request.app.state, "investigation", None)
    try:
        if current is not None and folder.resolve() == current.folder.resolve():
            made = playgrounds.make(current, body.name)
            busy = local_jobs.busy(current.folder)
        else:
            original, _status = open_pulled(folder, body.force, body.upgrade)
            try:
                made = playgrounds.make(original, body.name)
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
    return PlaygroundMadeOut(**out.model_dump(), notice=_notice(made, busy))
