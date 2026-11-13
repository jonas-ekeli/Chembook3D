"""The app's live link with Claude (D91, FR-MCP).

- A change counter: every request that changes the investigation counts one change, with the
  client that made it (the `X-Chembook-Client` header). Open tabs wait on `GET /api/live` and
  reload when another client (Claude, or another tab) changed something.
- The selection a tab reports, and the energy level, type and reference it shows, which Claude
  reads to know what "this node" is.
- Confirmations: Claude cannot delete anything itself. It asks with `POST /api/confirmations`;
  the tab shows what would go, and only the user's Confirm there makes the app do it.
"""

import asyncio
import re
import threading
import time
import uuid
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import quote, urlsplit

import anyio.from_thread
import httpx
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from chembook3d.api.identity import check_investigation_identity
from chembook3d.api.routes import DbSession
from chembook3d.autonomy import Autonomy
from chembook3d.models import (
    AlignmentSet,
    Branch,
    Calculation,
    ConformerEquilibrium,
    EnergyProfile,
    GroupNode,
    Node,
    NodeNote,
    ReactionStep,
    Selectivity,
    StericProfile,
    SubInvestigation,
    Transition,
    Turnover,
)
from chembook3d.services import branches as branch_service
from chembook3d.services import groups as group_service
from chembook3d.services import history, import_undo
from chembook3d.services import nodes as node_service
from chembook3d.services import species as species_service
from chembook3d.services import sub_investigations as sub_service
from chembook3d.services import transitions as transition_service
from chembook3d.services.records import RecordError, get

router = APIRouter(prefix="/api")

CLIENT_HEADER = "x-chembook-client"
AGENT = "claude"  # the client name `chembook3d mcp` sends; its changes are "claude" in the history

DEFAULT_TIMEOUT = 120.0  # seconds a confirmation waits for the user's answer (A38)
LONGEST_WAIT = 30.0  # one long poll; the caller asks again

# Requests that change nothing in the investigation, so open tabs need not reload.
QUIET = re.compile(
    r"^/api/("
    r"live|selection|view-state|confirmations(/.*)?"
    r"|page(/.*)?"  # D226: what Claude asks the open page to do; the page's own saves count
    r"|energies/(profile|table|table\.csv)|pathways/extend|overlay|snapshot|snapshot/save|atom-match|measurements"
    r"|scan-paths|scan-paths/plan|calculations/[^/]+/steps/trim-preview"
    r"|steric-profiles/[^/]+/(difference|table\.csv)"
    r"|imports|imports/[^/]+|imports/[^/]+/preview"
    r"|batch-imports|batch-imports/[^/]+|batch-imports/[^/]+/preview"
    r"|note-images|source-files/[^/]+/open|claude(/.*)?|remote(/.*)?|presence(/.*)?|shutdown"
    r"|inspections(/.*)?"  # D123c: reading a file, and staging it for the import preview
    r"|scan-editor(/.*)?"  # D135: scans put together without importing anything
    r"|builder(/.*)?"  # D139: the Edit structure window's tools, nothing stored
    r"|server-files(/.*)?"  # D124, D126: files on a server, not the notebook
    r"|energy-profiles(/.*)?"  # D190: the drawer saves its profile on every step (see PROFILES)
    r"|jobs|jobs/(?![^/]+/import-path$).*"  # importing a scan path changes the notebook
    r"|local/(?!jobs/[^/]+/import$).*"  # D213: only importing a local result changes it
    r")$"
)

# D227: a saved energy profile Claude makes, changes or deletes. Quiet as the drawer's own saves
# are, but an open drawer reads the saved profiles only when it starts, so the tabs are told.
PROFILES = re.compile(r"^/api/energy-profiles(/.*)?$")

# Identity discovery, explicit opening and computer-wide folder browsing must work from a
# stale tab. Close/sync/settings stay guarded: their target depends on the active notebook.
IDENTITY_DISCOVERY = re.compile(
    r"^/api/(live|presence|investigation|health|folders|remote|investigations"
    r"|investigations/open|investigations/clone|sync/resolve)$"
)


@dataclass
class Confirmation:
    id: str
    action: str
    params: dict[str, Any]
    summary: str
    reason: str
    folder: str
    expires: float
    status: str = "pending"  # pending, running, done, refused, expired, failed
    result: Any = None
    error: str | None = None
    finished: float | None = None
    unasked: bool = False  # D223: Claude's own record, deleted without asking while it works alone


@dataclass
class PageCommand:
    """D226: something Claude asks the open page to do, as the user would there."""

    id: str
    action: str
    params: dict[str, Any]
    folder: str
    expires: float
    status: str = "pending"  # pending, running (a tab took it), done, failed, expired
    result: dict[str, Any] | None = None
    error: str | None = None
    finished: float | None = None


LISTENING = 45.0  # seconds after its last poll a tab still counts as open (one poll: 30 s)


@dataclass
class LiveState:
    """Kept in memory by the running app; nothing here is saved in the investigation."""

    lock: threading.Lock = field(default_factory=threading.Lock)
    version: int = 0
    changes: deque[tuple[int, str]] = field(default_factory=lambda: deque(maxlen=500))
    confirm_version: int = 0
    confirmations: dict[str, Confirmation] = field(default_factory=dict)
    selection: dict[str, Any] | None = None
    autonomy: Autonomy = field(default_factory=Autonomy)  # D223: Claude working on its own
    commands: dict[str, PageCommand] = field(default_factory=dict)  # D226
    listeners: dict[str, float] = field(default_factory=dict)  # tab: when it last polled
    profiles_version: int = 0  # D227: counts Claude's changes to the saved energy profiles
    claude_requests: int = 0  # D231: requests Claude's notebook tools made, for its stop check
    stop_checks: dict[str, int] = field(default_factory=dict)  # session: claude_requests then

    def profiles_changed(self) -> None:
        """Claude changed a saved energy profile: the tabs' polls answer, as for a confirmation."""
        with self.lock:
            self.profiles_version += 1
            self.confirm_version += 1

    def changed(self, client: str) -> None:
        with self.lock:
            self.version += 1
            self.changes.append((self.version, client))

    def changed_by_others(self, since: int, client: str) -> bool:
        """Whether anyone but `client` changed something after `since`."""
        with self.lock:
            if since >= self.version:
                return False
            if not self.changes or since < self.changes[0][0] - 1:
                return True  # older than what is kept: assume so
            return any(v > since and c != client for v, c in self.changes)

    def settle(self, folder: str | None) -> None:
        """Expire requests nobody answered in time, or made for an investigation that is no
        longer open, and forget old answers."""
        now = time.monotonic()
        with self.lock:
            for c in self.confirmations.values():
                if c.status == "pending" and (now >= c.expires or c.folder != folder):
                    self._finish(c, "expired")
            for key in [
                k
                for k, c in self.confirmations.items()
                if c.finished is not None and now - c.finished > 600
            ]:
                del self.confirmations[key]
            for command in self.commands.values():
                if command.finished is None and (
                    now >= command.expires or command.folder != folder
                ):
                    command.status = "expired"
                    command.finished = now
                    self.confirm_version += 1
            for key in [
                k
                for k, c in self.commands.items()
                if c.finished is not None and now - c.finished > 600
            ]:
                del self.commands[key]

    def heard(self, client: str) -> None:
        """A tab polled: it is open, and can be asked to do something (D226)."""
        if client and client != AGENT:
            with self.lock:
                self.listeners[client] = time.monotonic()

    def gone(self, client: str) -> None:
        with self.lock:
            self.listeners.pop(client, None)

    def listening(self) -> bool:
        now = time.monotonic()
        with self.lock:
            for key in [k for k, at in self.listeners.items() if now - at > LISTENING]:
                del self.listeners[key]
            return bool(self.listeners)

    def asked(self) -> list[PageCommand]:
        with self.lock:
            return [c for c in self.commands.values() if c.status == "pending"]

    def pending(self) -> list[Confirmation]:
        with self.lock:
            return [c for c in self.confirmations.values() if c.status == "pending"]

    def _finish(self, confirmation: Confirmation, status: str) -> None:
        confirmation.status = status
        confirmation.finished = time.monotonic()
        self.confirm_version += 1


def live_state(request: Request) -> LiveState:
    return request.app.state.live


def _folder(request: Request) -> str | None:
    investigation = getattr(request.app.state, "investigation", None)
    return None if investigation is None else str(investigation.folder)


class ChangeTracker:
    """Counts each successful request that changes the investigation, and marks what Claude
    does as "claude" in the history (D91)."""

    def __init__(self, app: ASGIApp, live: LiveState) -> None:
        self.app = app
        self.live = live

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        client = ""
        for name, value in scope.get("headers", []):
            if name == CLIENT_HEADER.encode():
                client = value.decode("latin-1")[:100]
        path: str = scope["path"]
        if path.startswith("/api/") and not IDENTITY_DISCOVERY.fullmatch(path):
            try:
                check_investigation_identity(Request(scope))
            except HTTPException as exc:
                await JSONResponse({"detail": exc.detail}, status_code=exc.status_code)(
                    scope, receive, send
                )
                return
        counts = (
            scope["method"] not in ("GET", "HEAD", "OPTIONS")
            and path.startswith("/api/")
            and not QUIET.match(path)
        )
        profiles = (
            client == AGENT
            and scope["method"] not in ("GET", "HEAD", "OPTIONS")
            and PROFILES.match(path) is not None
        )
        if client == AGENT and path.startswith("/api/"):
            with self.live.lock:
                self.live.claude_requests += 1  # D231: Claude did something since it stopped
        status: dict[str, int] = {}

        async def watched(message: Message) -> None:
            if message["type"] == "http.response.start":
                status["code"] = message["status"]
            await send(message)

        token = history.SOURCE.set(AGENT if client == AGENT else "manual")
        made = history.MADE.set(self.live.autonomy)  # D223: what Claude made, for its deletes
        try:
            await self.app(scope, receive, watched if counts or profiles else send)
        finally:
            history.MADE.reset(made)
            history.SOURCE.reset(token)
        if counts and status.get("code", 500) < 400:
            self.live.changed(client)
        if profiles and status.get("code", 500) < 400:
            self.live.profiles_changed()


# ---------- changes ----------


class ConfirmationOut(BaseModel):
    id: str
    action: str
    summary: str
    reason: str
    status: str
    seconds_left: float
    result: Any = None
    error: str | None = None
    unasked: bool = False


class PageCommandOut(BaseModel):
    id: str
    action: str
    params: dict[str, Any]


class LiveOut(BaseModel):
    version: int
    changed: bool  # another client changed something after `since`
    confirm_version: int
    confirmations: list[ConfirmationOut]  # waiting for the user's answer, oldest first
    commands: list[PageCommandOut] = Field(default_factory=list)  # D226: for a tab to take
    profiles_version: int = 0  # D227: differs once Claude changed a saved energy profile
    investigation: str | None


def _confirmation_out(c: Confirmation) -> ConfirmationOut:
    return ConfirmationOut(
        id=c.id,
        action=c.action,
        summary=c.summary,
        reason=c.reason,
        status=c.status,
        seconds_left=max(0.0, round(c.expires - time.monotonic(), 1)),
        result=c.result,
        error=c.error,
        unasked=c.unasked,
    )


@router.get("/live", response_model=LiveOut)
async def live(
    request: Request,
    since: int | None = None,
    confirmations: int | None = None,
    client: str = "",
    wait: float = 0,
    expected_investigation: str | None = None,
):
    """A long poll: answers as soon as another client changed something after `since`, or the
    list of confirmations differs from `confirmations`, or after `wait` seconds."""
    state = live_state(request)
    initial_folder = _folder(request)
    deadline = time.monotonic() + max(0.0, min(wait, LONGEST_WAIT))
    while True:
        state.heard(client)
        state.settle(_folder(request))
        changed = since is not None and state.changed_by_others(since, client)
        if (
            changed
            # D91: identity must update even for this client's own close, before a slow
            # push finishes. Explicit identity also catches a switch before this poll began.
            or _folder(request) != initial_folder
            or (
                expected_investigation is not None
                and (_folder(request) or "") != expected_investigation
            )
            or (confirmations is not None and confirmations != state.confirm_version)
            or time.monotonic() >= deadline
            or await request.is_disconnected()
        ):
            break
        await asyncio.sleep(0.2)
    return LiveOut(
        version=state.version,
        changed=changed,
        confirm_version=state.confirm_version,
        confirmations=[_confirmation_out(c) for c in state.pending()],
        commands=[PageCommandOut(id=c.id, action=c.action, params=c.params) for c in state.asked()],
        profiles_version=state.profiles_version,
        investigation=_folder(request),
    )


# ---------- selection ----------


class SelectionIn(BaseModel):
    nodes: list[str] = Field(default_factory=list)  # nodes and free species
    groups: list[str] = Field(default_factory=list)
    transitions: list[str] = Field(default_factory=list)
    branch_id: str | None = None
    view: str = "canvas"  # canvas, history or analyses
    level: str | None = None  # the composite level key the tab shows energies at
    energy_type: str | None = None
    reference_id: str | None = None


@router.put("/selection", status_code=204)
def report_selection(body: SelectionIn, request: Request):
    """The tab reports what is selected; the tab used last is the one Claude reads (A38)."""
    investigation = getattr(request.app.state, "investigation", None)
    check_investigation_identity(request, investigation)
    folder = None if investigation is None else str(investigation.folder)
    with live_state(request).lock:
        live_state(request).selection = {"folder": folder, **body.model_dump()}


def _label(record: Any) -> str:
    if isinstance(record, Node):
        if species_service.is_species(record):
            return species_service.label(record)
        return record.label or "Untitled node"
    if isinstance(record, GroupNode):
        return record.label or "Group"
    if isinstance(record, SubInvestigation):
        return record.label or "Sub-investigation"
    return getattr(record, "name", "") or "unnamed"


def _end(session: Session, record_id: str) -> Node | GroupNode | None:
    return session.get(Node, record_id) or session.get(GroupNode, record_id)


@router.get("/selection")
def read_selection(request: Request, session: DbSession) -> dict[str, Any]:
    """What the user has selected in the app, by id and name, with the energy view shown.
    Records deleted since are left out."""
    with live_state(request).lock:
        reported = live_state(request).selection
    if reported is None or reported["folder"] != _folder(request):
        return {"reported": False, "nodes": [], "groups": [], "transitions": [], "branch": None}
    nodes = [n for i in reported["nodes"] if (n := session.get(Node, i)) is not None]
    groups = [g for i in reported["groups"] if (g := session.get(GroupNode, i)) is not None]
    edges = [t for i in reported["transitions"] if (t := session.get(Transition, i)) is not None]
    branch = session.get(Branch, reported["branch_id"]) if reported["branch_id"] else None
    reference = _end(session, reported["reference_id"]) if reported["reference_id"] else None
    return {
        "reported": True,
        "view": reported["view"],
        "nodes": [{"id": n.id, "label": _label(n), "kind": n.kind, "role": n.role} for n in nodes],
        "groups": [
            {
                "id": g.id,
                "label": _label(g),
                "member_ids": [m.id for m in group_service.members(session, g.id)],
            }
            for g in groups
        ],
        "transitions": [
            {
                "id": t.id,
                "source_id": t.source_id,
                "target_id": t.target_id,
                "label": " → ".join(
                    _label(end) if (end := _end(session, i)) else "?"
                    for i in (t.source_id, t.target_id)
                ),
            }
            for t in edges
        ],
        "branch": {"id": branch.id, "name": branch.name} if branch else None,
        "energy_view": {
            "level": reported["level"],
            "type": reported["energy_type"],
            "reference": {"id": reference.id, "label": _label(reference)} if reference else None,
        },
    }


# ---------- confirmations ----------


@dataclass(frozen=True)
class Action:
    """One kind of change that needs the user's Confirm (D91): the request the app makes once
    confirmed, and what the dialog says it does."""

    method: str
    path: str  # with {param} placeholders, each a record id
    describe: Callable[[Session, dict[str, Any]], str]
    body: Callable[[dict[str, Any]], dict[str, Any] | None] = lambda _params: None
    # D223: whether everything the change removes is Claude's own (made by it, and changed by
    # nobody else since); None: it always asks
    own: Callable[[Session, dict[str, Any], Autonomy], bool] | None = None

    @property
    def params(self) -> list[str]:
        return re.findall(r"{(\w+)}", self.path)


def _count(n: int, one: str, many: str | None = None) -> str:
    return f"{n} {one if n == 1 else (many or one + 's')}"


def _listing(parts: list[str]) -> str:
    parts = [p for p in parts if p]
    if len(parts) <= 1:
        return "".join(parts)
    return ", ".join(parts[:-1]) + " and " + parts[-1]


def _describe_node(session: Session, params: dict[str, Any]) -> str:
    node = get(session, Node, params["node_id"], "Node")
    if species_service.is_species(node):
        uses = len(species_service.usage(session, node.id))
        on = f", which joins or leaves on {_count(uses, 'edge')}" if uses else ""
        return f"Delete the free species “{_label(node)}”{on}"
    calculations = node_service.calculation_count(session, node.id)
    edges = len(transition_service.touching(session, [node.id]))
    notes = session.scalar(
        select(func.count()).select_from(NodeNote).where(NodeNote.node_id == node.id)
    )
    parts = [
        _count(calculations, "calculation") if calculations else "",
        _count(edges, "edge") if edges else "",
        _count(notes, "note") if notes else "",
    ]
    text = f"Delete node “{_label(node)}”"
    if any(parts):
        text += " with " + _listing(parts)
    if node.group_id:
        group = session.get(GroupNode, node.group_id)
        text += f" (a member of group “{_label(group)}”)"
    return text


def _describe_group(session: Session, params: dict[str, Any]) -> str:
    preview = group_service.delete_preview(session, params["group_id"])
    parts = [
        _count(len(preview["members"]), "member"),
        _count(preview["calculations"], "calculation"),
        _count(len(preview["all_transitions"]), "edge"),
    ]
    return f"Delete group “{_label(preview['group'])}” with its contents: " + _listing(parts)


def _describe_dissolve(session: Session, params: dict[str, Any]) -> str:
    preview = group_service.delete_preview(session, params["group_id"])
    members = _count(len(preview["members"]), "member")
    text = f"Dissolve group “{_label(preview['group'])}”: its {members} stay as nodes"
    edges = len(preview["group_transitions"])
    if edges:
        text += f", and {_count(edges, 'edge')} to or from the group go with it"
    if params.get("restore_branches"):
        text += "; members go back to the branches they came from"
    return text


def _describe_sub(session: Session, params: dict[str, Any]) -> str:
    """D193: everything inside goes with the box."""
    found = sub_service.delete_preview(session, params["sub_investigation_id"])
    inside = found.contents
    parts = [
        _count(len(inside.nodes), "node") if inside.nodes else "",
        _count(len(inside.groups), "group") if inside.groups else "",
        _count(len(inside.subs), "sub-investigation") if inside.subs else "",
        _count(found.calculations, "calculation") if found.calculations else "",
        _count(len(found.transitions), "edge") if found.transitions else "",
    ]
    text = f"Delete sub-investigation “{_label(found.sub)}”"
    return text + (" with its contents: " + _listing(parts) if any(parts) else " (it is empty)")


def _describe_sub_dissolve(session: Session, params: dict[str, Any]) -> str:
    found = sub_service.delete_preview(session, params["sub_investigation_id"])
    where = (
        f"“{_label(session.get(SubInvestigation, found.sub.parent_id))}”"
        if found.sub.parent_id
        else "the main canvas"
    )
    return (
        f"Dissolve sub-investigation “{_label(found.sub)}”: what it holds goes back onto {where}, "
        "where the box was; nothing is deleted"
    )


def _describe_step(session: Session, params: dict[str, Any]) -> str:
    step = get(session, ReactionStep, params["step_id"], "Reaction step")
    count = session.scalar(select(func.count()).select_from(Node).where(Node.step_id == step.id))
    text = f"Delete reaction step “{_label(step)}”"
    if count:
        text += f"; {_count(count, 'node')} will have no step"
    return text


def _describe_branch(session: Session, params: dict[str, Any]) -> str:
    branch = get(session, Branch, params["branch_id"], "Branch")
    kids = branch_service.children(session, branch.id)
    if kids:
        names = ", ".join(f"“{_label(k)}”" for k in kids)
        raise RecordError(f"Branch “{_label(branch)}” has child branches ({names}) (INV-1)")
    count = len(branch_service.members(session, branch.id))
    text = f"Delete branch “{_label(branch)}”"
    if count:
        text += f"; its {_count(count, 'node')} stay with no branch"
    return text


def _describe_transition(session: Session, params: dict[str, Any]) -> str:
    edge = get(session, Transition, params["transition_id"], "Transition")
    ends = [_end(session, i) for i in (edge.source_id, edge.target_id)]
    text = "Delete the edge " + " → ".join(f"“{_label(e)}”" if e else "?" for e in ends)
    if edge.species:
        text += f" with {_count(len(edge.species), 'free species', 'free species')} on it"
    return text


def _describe_coordinates(session: Session, params: dict[str, Any]) -> str:
    node = get(session, Node, params["node_id"], "Node")
    if not node.geometry:
        raise RecordError(f"Node “{_label(node)}” has no coordinates")
    if node_service.calculation_count(session, node.id):
        # D90: they are the geometry its calculations were run on (ID-4, ID-5).
        raise RecordError(
            f"Node “{_label(node)}” has calculations, so it keeps its coordinates (D90)"
        )
    return (
        f"Remove the coordinates of node “{_label(node)}” ({_count(len(node.geometry), 'atom')});"
        " Restore in its history brings them back"
    )


def _describe_note(session: Session, params: dict[str, Any]) -> str:
    note = get(session, NodeNote, params["note_id"], "Note")
    node = session.get(Node, note.node_id)
    title = f"“{note.title}” " if note.title else ""
    return f"Delete the note {title}on node “{_label(node)}”"


def _describe_undo(session: Session, params: dict[str, Any]) -> str:
    """D102: only one file's import; a batch is undone from the app's history."""
    try:
        entry_id = int(params["entry_id"])
    except ValueError as exc:
        raise RecordError("entry_id must be the id of a history entry") from exc
    try:
        found = import_undo.preview_import(session, entry_id)
    except import_undo.UndoNotPossible as exc:
        raise RecordError(str(exc)) from exc
    if found.blockers:
        raise RecordError("This import cannot be undone: " + "; ".join(found.blockers))
    summary = found.as_dict()
    parts = [_count(len(summary["calculations"]), "calculation")]
    if summary["group"]:
        parts.append(f"the group “{summary['group']['label']}” with its members")
    elif summary["deleted"]:
        parts.append(_count(len(summary["deleted"]), "node") + " it made")
    text = f"Undo the import of {summary['file']}: remove {_listing(parts)}"
    if summary["restored"]:
        text += " and put back what it changed on the node"
    return text


def _describe_named(model: type, what: str, param: str) -> Callable[[Session, dict], str]:
    def describe(session: Session, params: dict[str, Any]) -> str:
        record = get(session, model, params[param], what)
        return f"Delete the {what.lower()} “{_label(record)}”"

    return describe


# ---------- D223: what Claude made itself ----------


def _own_nodes(session: Session, made: Autonomy, node_ids: list[str], *more: str) -> bool:
    """The nodes with all that goes when they are deleted: their calculations, notes, the edges
    touching them and the edges a free species is on."""
    ids = [*node_ids, *more]
    if node_ids:
        ids += session.scalars(select(Calculation.id).where(Calculation.node_id.in_(node_ids)))
        ids += session.scalars(select(NodeNote.id).where(NodeNote.node_id.in_(node_ids)))
        ids += [t.id for t in transition_service.touching(session, node_ids)]
        for node_id in node_ids:
            ids += [u.transition_id for u in species_service.usage(session, node_id)]
    return made.owns(*ids)


def _own_node(session: Session, params: dict[str, Any], made: Autonomy) -> bool:
    return _own_nodes(session, made, [params["node_id"]])


def _own_group(session: Session, params: dict[str, Any], made: Autonomy) -> bool:
    preview = group_service.delete_preview(session, params["group_id"])
    edges = [t.id for t in preview["all_transitions"]]
    return _own_nodes(
        session, made, [n.id for n in preview["members"]], preview["group"].id, *edges
    )


def _own_dissolve(session: Session, params: dict[str, Any], made: Autonomy) -> bool:
    preview = group_service.delete_preview(session, params["group_id"])
    return made.owns(preview["group"].id, *(t.id for t in preview["group_transitions"]))


def _own_sub(session: Session, params: dict[str, Any], made: Autonomy) -> bool:
    found = sub_service.delete_preview(session, params["sub_investigation_id"])
    inside = found.contents
    others = [found.sub.id, *(g.id for g in inside.groups), *(s.id for s in inside.subs)]
    others += [t.id for t in found.transitions]
    return _own_nodes(session, made, [n.id for n in inside.nodes], *others)


def _own_undo(session: Session, params: dict[str, Any], made: Autonomy) -> bool:
    plan = import_undo.preview_import(session, int(params["entry_id"]))
    ids = [c.id for c in plan.records.calculations] + [n.id for n in plan.deleted]
    ids += [n.id for n, _field in [*plan.restored, *plan.kept]] + [n.id for n, _a, _r in plan.tags]
    if plan.group is not None:
        ids.append(plan.group.id)
    return made.owns(*ids)


def _own_step(session: Session, params: dict[str, Any], made: Autonomy) -> bool:
    """The step, and the nodes that would be left with no step."""
    nodes = session.scalars(select(Node.id).where(Node.step_id == params["step_id"]))
    return made.owns(params["step_id"], *nodes)


def _own_branch(session: Session, params: dict[str, Any], made: Autonomy) -> bool:
    """The branch, and the nodes that would be left with no branch."""
    members = branch_service.members(session, params["branch_id"])
    return made.owns(params["branch_id"], *(n.id for n in members))


def _own(param: str) -> Callable[[Session, dict[str, Any], Autonomy], bool]:
    """A record that takes nothing else with it."""
    return lambda _session, params, made: made.owns(params[param])


ACTIONS: dict[str, Action] = {
    "delete_node": Action("DELETE", "/api/nodes/{node_id}", _describe_node, own=_own_node),
    "delete_group": Action("DELETE", "/api/groups/{group_id}", _describe_group, own=_own_group),
    "dissolve_group": Action(
        "POST",
        "/api/groups/{group_id}/dissolve",
        _describe_dissolve,
        lambda p: {"restore_branches": bool(p.get("restore_branches", False))},
        own=_own_dissolve,
    ),
    "delete_sub_investigation": Action(
        "DELETE", "/api/sub-investigations/{sub_investigation_id}", _describe_sub, own=_own_sub
    ),
    "dissolve_sub_investigation": Action(
        "POST",
        "/api/sub-investigations/{sub_investigation_id}/dissolve",
        _describe_sub_dissolve,
        own=_own("sub_investigation_id"),
    ),
    "delete_step": Action("DELETE", "/api/steps/{step_id}", _describe_step, own=_own_step),
    "delete_branch": Action(
        "DELETE", "/api/branches/{branch_id}", _describe_branch, own=_own_branch
    ),
    "delete_transition": Action(
        "DELETE",
        "/api/transitions/{transition_id}",
        _describe_transition,
        own=_own("transition_id"),
    ),
    "remove_coordinates": Action(
        "PUT",
        "/api/nodes/{node_id}/geometry",
        _describe_coordinates,
        lambda _p: {"xyz": ""},
        own=_own("node_id"),
    ),
    "delete_note": Action("DELETE", "/api/notes/{note_id}", _describe_note, own=_own("note_id")),
    "delete_selectivity": Action(
        "DELETE",
        "/api/selectivities/{selectivity_id}",
        _describe_named(Selectivity, "Selectivity", "selectivity_id"),
        own=_own("selectivity_id"),
    ),
    "delete_turnover": Action(
        "DELETE",
        "/api/turnovers/{turnover_id}",
        _describe_named(Turnover, "Turnover", "turnover_id"),
        own=_own("turnover_id"),
    ),
    "delete_conformer_equilibrium": Action(
        "DELETE",
        "/api/equilibria/{equilibrium_id}",
        _describe_named(ConformerEquilibrium, "Conformer equilibrium", "equilibrium_id"),
        own=_own("equilibrium_id"),
    ),
    "delete_steric_profile": Action(
        "DELETE",
        "/api/steric-profiles/{profile_id}",
        _describe_named(StericProfile, "Steric profile", "profile_id"),
        own=_own("profile_id"),
    ),
    # D227: saved energy profiles are not in the history, so none is known to be Claude's own:
    # deleting one always asks.
    "delete_energy_profile": Action(
        "DELETE",
        "/api/energy-profiles/{energy_profile_id}",
        _describe_named(EnergyProfile, "Energy profile", "energy_profile_id"),
    ),
    "undo_import": Action("POST", "/api/history/{entry_id}/undo", _describe_undo, own=_own_undo),
    "delete_alignment_set": Action(
        "DELETE",
        "/api/alignment-sets/{set_id}",
        _describe_named(AlignmentSet, "Alignment set", "set_id"),
    ),
}


class ConfirmationIn(BaseModel):
    action: str
    params: dict[str, Any] = Field(default_factory=dict)
    reason: str = ""  # why Claude asks, shown in the dialog
    timeout: float = DEFAULT_TIMEOUT


@router.post("/confirmations", response_model=ConfirmationOut, status_code=201)
def ask(body: ConfirmationIn, request: Request, session: DbSession):
    """Claude asks to delete something (D91). Nothing changes until the user confirms in the
    app; the answer says what the dialog shows. D223: while Claude works on its own, what it
    made itself (and nobody else changed since) goes at once, without the dialog."""
    action = ACTIONS.get(body.action)
    if action is None:
        raise HTTPException(422, f"'{body.action}' is not a change that can be asked for")
    for name in action.params:
        value = body.params.get(name)
        if not isinstance(value, str) or not value or "/" in value:
            raise HTTPException(422, f"'{name}' must be the id of a record")
    summary = action.describe(session, body.params)
    timeout = max(5.0, min(body.timeout, 600.0))
    confirmation = Confirmation(
        id=uuid.uuid4().hex,
        action=body.action,
        params=dict(body.params),
        summary=summary,
        reason=body.reason.strip()[:1000],
        folder=_folder(request) or "",
        expires=time.monotonic() + timeout,
    )
    state = live_state(request)
    made = state.autonomy
    unasked = (
        request.headers.get(CLIENT_HEADER) == AGENT
        and made.on_for(_folder(request))
        and action.own is not None
        and action.own(session, body.params, made)
    )
    with state.lock:
        state.confirmations[confirmation.id] = confirmation
        if unasked:
            confirmation.unasked = True
            confirmation.status = "running"  # never shown in the page's dialog
        else:
            state.confirm_version += 1
    if unasked:
        session.close()  # the delete below is a request of its own; hold no read open over it
        anyio.from_thread.run(_run, request.app, state, confirmation)
    return _confirmation_out(confirmation)


async def _run(app: ASGIApp, state: LiveState, found: Confirmation) -> None:
    """Make the app's own request for a confirmed (or, D223, unasked) change, as Claude."""
    action = ACTIONS[found.action]
    path = action.path.format(**{k: quote(found.params[k], safe="") for k in action.params})
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1") as client:
        response = await client.request(
            action.method, path, json=action.body(found.params), headers={CLIENT_HEADER: AGENT}
        )
    with state.lock:
        if response.is_success:
            found.result = response.json() if response.content else None
            state._finish(found, "done")
        else:
            try:
                found.error = str(response.json().get("detail"))
            except ValueError:
                found.error = response.text or f"HTTP {response.status_code}"
            state._finish(found, "failed")


def _confirmation(request: Request, confirmation_id: str) -> Confirmation:
    found = live_state(request).confirmations.get(confirmation_id)
    if found is None:
        raise HTTPException(404, "No such request")
    return found


@router.get("/confirmations/{confirmation_id}", response_model=ConfirmationOut)
async def answer_of(request: Request, confirmation_id: str, wait: float = 0):
    """The request's state; with `wait`, answers as soon as the user has decided."""
    state = live_state(request)
    deadline = time.monotonic() + max(0.0, min(wait, LONGEST_WAIT))
    while True:
        state.settle(_folder(request))
        found = _confirmation(request, confirmation_id)
        if found.status not in ("pending", "running") or time.monotonic() >= deadline:
            return _confirmation_out(found)
        if await request.is_disconnected():
            return _confirmation_out(found)
        await asyncio.sleep(0.2)


class AnswerIn(BaseModel):
    confirm: bool


LOOPBACK = {"127.0.0.1", "localhost", "::1"}


@router.post("/confirmations/{confirmation_id}/answer", response_model=ConfirmationOut)
async def answer(request: Request, confirmation_id: str, body: AnswerIn):
    """The user's Confirm or Refuse, from the app's page. A browser always sends the page's
    origin with this request; the MCP server sends none and offers no way to answer."""
    origin = request.headers.get("origin")
    if not origin or urlsplit(origin).hostname not in LOOPBACK:
        raise HTTPException(403, "Only the app's own page can answer a request")
    state = live_state(request)
    state.settle(_folder(request))
    with state.lock:
        found = _confirmation(request, confirmation_id)
        if found.status != "pending":
            raise HTTPException(409, f"This request is already {found.status}")
        if not body.confirm:
            state._finish(found, "refused")
            return _confirmation_out(found)
        found.status = "running"
    await _run(request.app, state, found)
    return _confirmation_out(found)
