"""Starting and stopping the app (D107, FR-RUN).

- Every open tab keeps a long poll on `GET /api/presence` and says goodbye when it closes
  (`POST /api/presence/{tab}/closed`, sent with `navigator.sendBeacon`). Started by the
  launcher (`chembook3d --launched`), the server shuts itself down when no tab is left.
- `POST /api/shutdown` is the app's Shut down button, whichever way the app was started.
  Only the app's own page can ask for it, and it is not an MCP tool.
"""

import asyncio
import os
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from starlette.types import ASGIApp, Receive, Scope, Send

from chembook3d import sync
from chembook3d.api.routes import close_and_push

router = APIRouter(prefix="/api")

GRACE = 10.0  # seconds after the last tab said goodbye (A54): long enough for a reload
SILENCE = 300.0  # seconds without a word from any tab (A54): a crash, or a tab put to sleep
LONGEST_WAIT = 30.0  # one long poll; the tab asks again
EXIT_DELAY = 0.5  # seconds, so that the answer and the tabs' "stopping" get out first
SHUTDOWN_PUSH_TIMEOUT = 20  # seconds to push a synced investigation (FR-SYNC-05)

LOOPBACK = {"127.0.0.1", "localhost", "::1"}


def _seconds(name: str, default: float) -> float:
    """The tests shorten the times through these variables; nothing else sets them."""
    try:
        return float(os.environ[name])
    except (KeyError, ValueError):
        return default


@dataclass
class Lifetime:
    """Which tabs are open, and whether the server is stopping. Kept in memory only."""

    launched: bool = False  # started by the launcher: stop when no tab is left
    notices: list[str] = field(default_factory=list)  # from the launcher, shown in each tab
    grace: float = field(default_factory=lambda: _seconds("CHEMBOOK3D_LAUNCH_GRACE", GRACE))
    silence: float = field(default_factory=lambda: _seconds("CHEMBOOK3D_LAUNCH_SILENCE", SILENCE))
    started: float = field(default_factory=time.monotonic)
    tabs: dict[str, float] = field(default_factory=dict)  # tab id: when last heard from
    busy: dict[str, int] = field(default_factory=dict)  # tab id: its requests still running
    goodbye: float | None = None  # when the last tab left said goodbye
    dropped: bool = False  # the last tabs left went silent instead
    stopping: bool = False
    reason: str = ""  # why it is stopping
    sync: str | None = None  # how the push at the Shut down button went
    exit: Callable[[], None] | None = None  # set by `chembook3d`: stops the server
    # Work that keeps a launched server running with no tab left, such as installing the local
    # tools (D213): each answers whether it is still going.
    holds: list[Callable[[], bool]] = field(default_factory=list)
    lock: threading.Lock = field(default_factory=threading.Lock)
    # Held while the Shut down button closes and pushes the investigation: a second press waits
    # for the first instead of stopping the server under it (the first was then cancelled).
    shutting_down: threading.Lock = field(default_factory=threading.Lock)

    def seen(self, tab: str, now: float | None = None) -> None:
        with self.lock:
            self.tabs[tab] = time.monotonic() if now is None else now
            self.goodbye = None
            self.dropped = False

    def asking(self, tab: str, now: float | None = None) -> bool:
        """A request of the tab began. Any request of a known tab is a word from it, and a tab
        with a request still being answered is open: its long poll may be waiting behind that
        request, as a browser opens only six connections to the app. False for a tab not known
        here (Claude's requests, a page still loading)."""
        with self.lock:
            if tab not in self.tabs:
                return False
            self.tabs[tab] = time.monotonic() if now is None else now
            self.busy[tab] = self.busy.get(tab, 0) + 1
            return True

    def answered(self, tab: str, now: float | None = None) -> None:
        """A request `asking` counted has been answered."""
        with self.lock:
            count = self.busy.get(tab, 0) - 1
            if count > 0:
                self.busy[tab] = count
            else:
                self.busy.pop(tab, None)
            if tab in self.tabs:
                self.tabs[tab] = time.monotonic() if now is None else now

    def closed(self, tab: str, now: float | None = None) -> None:
        with self.lock:
            if self.tabs.pop(tab, None) is not None and not self.tabs:
                self.goodbye = time.monotonic() if now is None else now

    def due(self, now: float | None = None) -> bool:
        """Whether a launched server should stop now that no tab is left (FR-RUN-03)."""
        now = time.monotonic() if now is None else now
        with self.lock:
            if not self.launched or self.stopping:
                return False
            if any(hold() for hold in self.holds):
                return False
            for tab, heard in list(self.tabs.items()):
                if now - heard >= self.silence and not self.busy.get(tab):
                    del self.tabs[tab]
                    self.dropped = True
            if self.tabs:
                return False
            if self.dropped:  # they have been silent for `silence` already
                return True
            if self.goodbye is not None:
                return now - self.goodbye >= self.grace
            return now - self.started >= self.silence  # no tab ever came

    def stop(self, reason: str) -> None:
        with self.lock:
            if self.stopping:
                return
            self.stopping = True
            self.reason = reason
        print(f"Chembook3D is shutting down: {reason}", flush=True)
        if self.exit is not None:
            threading.Timer(EXIT_DELAY, self.exit).start()


class TabRequests:
    """Tells the lifetime about every request a tab sends (its `X-Chembook-Client` header)."""

    def __init__(self, app: ASGIApp, lifetime: Lifetime) -> None:
        self.app = app
        self.lifetime = lifetime

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        tab = ""
        if scope["type"] == "http":
            for name, value in scope.get("headers", []):
                if name == b"x-chembook-client":
                    tab = value.decode("latin-1")[:100]
        if not tab:
            await self.app(scope, receive, send)
            return
        if not self.lifetime.asking(tab):
            await self.app(scope, receive, send)
            return
        try:
            await self.app(scope, receive, send)
        finally:
            self.lifetime.answered(tab)


def lifetime(request: Request) -> Lifetime:
    return request.app.state.lifetime


async def watch(found: Lifetime) -> None:
    """Runs beside a launched server and stops it when no tab is left."""
    while True:
        await asyncio.sleep(min(1.0, found.grace / 4))
        if found.due():
            found.stop("the last browser tab was closed")
            return


# ---------- presence ----------


class PresenceOut(BaseModel):
    launched: bool
    stopping: bool
    notices: list[str]
    sync: str | None = None  # after Shut down: how the push went, if the investigation is synced


def _presence_out(found: Lifetime) -> PresenceOut:
    return PresenceOut(
        launched=found.launched, stopping=found.stopping, notices=found.notices, sync=found.sync
    )


def _tab(tab: str) -> str:
    if not tab or len(tab) > 100:
        raise HTTPException(422, "tab must be a short name")
    return tab


@router.get("/presence", response_model=PresenceOut)
async def presence(request: Request, tab: str, wait: float = 0):
    """A tab's long poll: answers when the server starts stopping, or after `wait` seconds."""
    found = lifetime(request)
    tab = _tab(tab)
    deadline = time.monotonic() + max(0.0, min(wait, LONGEST_WAIT))
    while not found.stopping:
        found.seen(tab)
        if time.monotonic() >= deadline or await request.is_disconnected():
            break
        await asyncio.sleep(0.2)
    return _presence_out(found)


@router.post("/presence/{tab}/closed", status_code=204)
def tab_closed(request: Request, tab: str):
    """The tab is being closed (or reloaded). A tab not known here changes nothing."""
    lifetime(request).closed(_tab(tab))
    live = getattr(request.app.state, "live", None)
    if live is not None:
        live.gone(tab)  # D226: it can no longer be asked to do anything


# ---------- shut down ----------


class ShutdownInfo(BaseModel):
    launched: bool
    investigation: bool  # one is open, and is closed first
    linked: bool  # the open investigation is synced, so it is pushed first
    claude_panel: bool  # a Claude panel conversation is running and will stop


@router.get("/shutdown", response_model=ShutdownInfo)
def shutdown_info(request: Request):
    """What the Shut down question should mention."""
    investigation = getattr(request.app.state, "investigation", None)
    panel = getattr(request.app.state, "claude_panel", None)
    return ShutdownInfo(
        launched=lifetime(request).launched,
        investigation=investigation is not None,
        linked=investigation is not None and sync.is_linked(investigation.folder),
        claude_panel=bool(panel is not None and panel.terminals),
    )


class ShutdownOut(BaseModel):
    sync: str | None = None  # how the push went, if the investigation is synced


@router.post("/shutdown", response_model=ShutdownOut)
def shut_down(request: Request):
    """The Shut down button (FR-RUN-04): close the investigation (pushing a synced one), then
    stop the server. Only a page on this computer can ask; a browser always sends its page's
    origin with this request."""
    origin = request.headers.get("origin")
    if not origin or urlsplit(origin).hostname not in LOOPBACK:
        raise HTTPException(403, "Only the app's own page can shut it down")
    found = lifetime(request)
    with found.shutting_down:
        if found.stopping:  # pressed again (another tab) while the first press was pushing
            return ShutdownOut(sync=found.sync)
        investigation = getattr(request.app.state, "investigation", None)
        message = None
        if investigation is not None:
            request.app.state.investigation = None
            status = close_and_push(investigation, timeout=SHUTDOWN_PUSH_TIMEOUT)
            if status is not None:
                message = status.message
                print(f"Chembook3D sync: {message}", flush=True)
        found.sync = message
        found.stop("Shut down in the app")
        return ShutdownOut(sync=message)
