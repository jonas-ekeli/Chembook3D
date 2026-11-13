"""D226: Claude asks the open page to do what only the page can: Tidy the canvas, open and
close groups, show a record, open a sub-investigation's canvas, change the energy view, and
draw the canvas or the energy profile as an image (saved in the investigation's `exports`).

Each route queues a command, an open tab takes it from its `/api/live` poll, does it with the
same code as its own buttons, and reports back; the route answers with that report. Nothing
here changes the investigation itself: what the page then saves (a Tidy's positions) goes
through its ordinary routes.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from typing import Any, Literal
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field

from chembook3d import exports
from chembook3d.api.live import LOOPBACK, LiveState, PageCommand, _folder, live_state
from chembook3d.api.routes import _investigation

router = APIRouter(prefix="/api/page")

WAIT = 30.0  # seconds a command waits for a tab to do it
SLOW = 60.0  # a Tidy with springs lays the whole canvas out first


async def _ask(
    request: Request, action: str, params: dict[str, Any], slow: bool = False
) -> dict[str, Any]:
    """Queue a command for the open page and wait for its report."""
    folder = _folder(request)
    if folder is None:
        raise HTTPException(409, "No investigation is open")
    state = live_state(request)
    if not state.listening():
        raise HTTPException(
            409,
            "No page of the app is open, so nothing can be done there. Ask the user to open "
            "Chembook3D in the browser.",
        )
    command = PageCommand(
        id=uuid.uuid4().hex,
        action=action,
        params=params,
        folder=folder,
        expires=time.monotonic() + (SLOW if slow else WAIT),
    )
    with state.lock:
        state.commands[command.id] = command
        state.confirm_version += 1  # wakes the tabs' polls
    while command.finished is None:
        state.settle(_folder(request))
        if await request.is_disconnected():
            break
        await asyncio.sleep(0.1)
    if command.status == "done":
        return command.result or {}
    if command.status == "failed":
        raise HTTPException(409, command.error or "The page could not do that")
    raise HTTPException(
        409,
        "The page did not answer in time, so it may not have been done. Ask the user whether "
        "the Chembook3D tab is open on this investigation.",
    )


class TidyIn(BaseModel):
    mode: Literal["columns", "springs", "settle"] = Field(
        "columns",
        description="'columns': one column per reaction step (D199). 'springs': arrows about "
        "one length, laid out afresh (D207). 'settle': springs, starting from where the cards "
        "are (D208).",
    )


@router.post("/tidy")
async def tidy(body: TidyIn, request: Request) -> dict[str, Any]:
    """Tidy the canvas the page shows, as its Tidy menu does. The page's Undo tidy, or
    `/tidy/undo`, puts the cards back."""
    return await _ask(request, "tidy", body.model_dump(), slow=True)


@router.post("/tidy/undo")
async def undo_tidy(request: Request) -> dict[str, Any]:
    """Put the cards back where they were before the last Tidy, while the page still offers
    that (until a card is dragged or another canvas opened)."""
    return await _ask(request, "undo_tidy", {})


class GroupsIn(BaseModel):
    open: bool
    ids: list[str] = Field(
        default_factory=list,
        description="Groups and sub-investigation boxes; empty: every group on the canvas shown.",
    )


@router.post("/groups")
async def open_groups(body: GroupsIn, request: Request) -> dict[str, Any]:
    """Expand or collapse groups (and sub-investigation boxes) on the page (D210)."""
    return await _ask(request, "groups", body.model_dump())


class ShowIn(BaseModel):
    id: str | None = Field(
        None,
        description="A node, group, sub-investigation, edge or branch; none: fit the whole "
        "canvas in the window.",
    )


@router.post("/show")
async def show(body: ShowIn, request: Request) -> dict[str, Any]:
    """Select a record on the page and bring it into view, opening its canvas if needed."""
    return await _ask(request, "show", body.model_dump())


class CanvasIn(BaseModel):
    sub_id: str | None = Field(
        None, description="The sub-investigation whose canvas to open; none: the main canvas."
    )


@router.post("/canvas")
async def open_canvas(body: CanvasIn, request: Request) -> dict[str, Any]:
    """Open a sub-investigation's canvas on the page, or go back to the main one (D193)."""
    return await _ask(request, "canvas", body.model_dump())


class ViewIn(BaseModel):
    """Only what is given changes."""

    mode: Literal["compact", "energy", "structure"] | None = Field(
        None, description="What the cards show."
    )
    level: str | None = Field(None, description="The composite level key, from get_energy_options.")
    energy_type: str | None = Field(None, description="For example 'G', 'H' or 'E'.")
    reference_id: str | None = Field(None, description="The node or group energies count from.")
    edge_energies: bool | None = Field(None, description="Show energy differences on arrows.")
    drawer: bool | None = Field(None, description="Open or close 'Profile and table'.")
    drawer_tab: Literal["profile", "table"] | None = None
    profile_id: str | None = Field(None, description="The saved energy profile to open there.")


@router.post("/view")
async def set_view(body: ViewIn, request: Request) -> dict[str, Any]:
    """Change what the page shows: the cards, the energy view, the profile drawer. Nothing in
    the investigation changes; the page remembers its view as it does the user's own."""
    asked = body.model_dump(exclude_none=True)
    if not asked:
        raise HTTPException(422, "Give at least one thing to change")
    return await _ask(request, "view", asked)


class CanvasImageIn(BaseModel):
    what: Literal["full", "viewport"] = Field(
        "full", description="The whole open canvas, or only what the window shows."
    )
    format: Literal["png", "svg"] = "png"


@router.post("/canvas-image")
async def canvas_image(body: CanvasImageIn, request: Request) -> dict[str, Any]:
    """The page draws its canvas as its Export menu does, in the view it shows, and the image
    is saved as a new file in the investigation's `exports` folder."""
    return await _ask(request, "canvas_image", body.model_dump(), slow=True)


class ProfileImageIn(BaseModel):
    format: Literal["svg", "png"] = "svg"
    profile_id: str | None = Field(
        None, description="The saved energy profile to draw; none: the one open in the drawer."
    )


@router.post("/profile-image")
async def profile_image(body: ProfileImageIn, request: Request) -> dict[str, Any]:
    """The page draws an energy profile as its drawer's Export does (the ticked pathways, in
    the profile style, at the level and energy type the page shows), saved as a new file in
    the investigation's `exports` folder."""
    return await _ask(request, "profile_image", body.model_dump(exclude_none=True), slow=True)


IMAGES = {"canvas_image", "profile_image"}  # the commands whose answer is a file


# ---------- the page's side ----------


def _page_only(request: Request) -> None:
    """A browser always sends the page's origin with a POST; the MCP server sends none."""
    origin = request.headers.get("origin")
    if not origin or urlsplit(origin).hostname not in LOOPBACK:
        raise HTTPException(403, "Only the app's own page can do that")


def _command(state: LiveState, command_id: str) -> PageCommand:
    found = state.commands.get(command_id)
    if found is None:
        raise HTTPException(404, "No such request")
    return found


@router.post("/commands/{command_id}/claim", status_code=204)
def claim(command_id: str, request: Request) -> None:
    """A tab takes the command; only the first one does it."""
    _page_only(request)
    state = live_state(request)
    with state.lock:
        found = _command(state, command_id)
        if found.status != "pending":
            raise HTTPException(409, f"This request is already {found.status}")
        found.status = "running"
        state.confirm_version += 1


@router.post("/commands/{command_id}/file")
async def save_file(command_id: str, name: str, request: Request) -> dict[str, str]:
    """The tab hands over the image it drew for a command it took; saved in `exports`, never
    over a file there."""
    _page_only(request)
    investigation = _investigation(request)
    state = live_state(request)
    with state.lock:
        found = _command(state, command_id)
        if found.status != "running" or found.action not in IMAGES:
            raise HTTPException(409, "This request takes no file")
    data = await request.body()
    try:
        path = await run_in_threadpool(exports.save, investigation.folder, name, data)
    except exports.ExportError as exc:
        raise HTTPException(422, str(exc)) from exc
    return {"file": path}


class ReportIn(BaseModel):
    result: dict[str, Any] | None = None
    error: str | None = None


@router.post("/commands/{command_id}/report", status_code=204)
def report(command_id: str, body: ReportIn, request: Request) -> None:
    """The tab that took the command says how it went."""
    _page_only(request)
    state = live_state(request)
    with state.lock:
        found = _command(state, command_id)
        if found.status != "running":
            raise HTTPException(409, f"This request is already {found.status}")
        found.result = body.result
        found.error = None if body.error is None else body.error[:1000]
        found.status = "done" if body.error is None else "failed"
        found.finished = time.monotonic()
        state.confirm_version += 1
