"""Edges Claude proposes and the user's review of them (D264, FR-PANEL-11). The rules live in
`services/proposals.py`.

`POST /api/mechanism/propose` is Claude's write tool `propose_mechanism` (a playground only);
`GET /api/mechanism/gaps` its read tool `find_gaps`, the holes of D265 (`services/gaps.py`);
`GET /api/mechanism/direct-links` its read tool `find_direct_links`, steps between minima with
no TS (FR-MCP-27, `services/direct_links.py`).
Accept, Accept all high, Reject and Fix are the user's buttons: only the app's own page may
press them, never Claude, which does not accept its own proposals."""

from datetime import UTC, datetime
from typing import Any, Literal

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel, Field

from chembook3d.api.pathway import TransitionOut, _transition_out
from chembook3d.api.playgrounds import _from_the_page
from chembook3d.api.routes import DbSession, _investigation
from chembook3d.services import direct_links, gaps, proposals

router = APIRouter(prefix="/api")


class ProposedSpeciesIn(BaseModel):
    species_id: str = Field(description="Id of a free species (a node with kind 'species').")
    direction: Literal["joins", "leaves"] = Field(
        description="'joins' (association) or 'leaves' (dissociation) on this edge (D69)."
    )
    count: int = Field(1, description="How many of the species join or leave.")


class ProposedEdgeIn(BaseModel):
    source_id: str = Field(
        description="The edge's start: a minimum before a TS, the TS before the minimum it "
        "leads to, or a minimum before another for a step without a TS on purpose."
    )
    target_id: str = Field(description="The edge's end, as for source_id.")
    confidence: Literal["high", "medium", "low"] = Field(
        description="A184: high from an IRC or a mode-follow whose two ends each match one "
        "structure; medium from the bond check along the mode alone; low from names only or "
        "a gap filled with a MACE guess (D265)."
    )
    evidence: str = Field(
        description="What settled it, in a sentence the user reads on the edge, e.g. “IRC "
        "forward end matches INT2 (RMSD 0.04 Å)”, “C1–H9 breaks and C5–H9 forms along the "
        "mode”, “names only” or “filled by Claude”."
    )
    status: str | None = Field(
        None,
        description="The edge's status (planned, running_externally, done, failed, rejected "
        "or superseded); left out, done when both ends are done, else planned (D192).",
    )
    species: list[ProposedSpeciesIn] = Field(
        default_factory=list,
        description="Free species that join or leave on this edge, so both sides have the "
        "same atoms (D69).",
    )
    hole: str | None = Field(
        None,
        description="D265: the id of the find_gaps hole this edge fills. Its confidence must "
        "be low, and its evidence is stored as “Filled by Claude (hole …): ” and yours.",
    )


class FilledIn(BaseModel):
    hole: str = Field(description="The id of the find_gaps hole these nodes fill (D265).")
    node_ids: list[str] = Field(
        description="The nodes and free species you built for it (made, refined with MACE): "
        "each becomes planned, “(MACE guess)” after its label and the tag MACE guess, with "
        "the hole in its notes; refused when one holds a result above the MACE level."
    )
    what: str = Field("", description="What was built, in a sentence, for the nodes' notes.")


class ProposedStepIn(BaseModel):
    name: str = Field(description="The reaction step's name; made, last, when no step has it.")
    ids: list[str] = Field(description="Ids of the nodes and groups to put in this step.")


class ProposedGroupIn(BaseModel):
    label: str = Field(
        description="The group's name (e.g. the intermediate whose conformers it holds); "
        "made from member_ids when no group has it, else the members are added."
    )
    member_ids: list[str] = Field(description="Ids of the nodes in it (two or more for a new one).")


class ProposeIn(BaseModel):
    edges: list[ProposedEdgeIn] = Field(
        description="The edges, each with its confidence and evidence. An edge already there "
        "in the same direction is updated and opened for review again, not made twice."
    )
    steps: list[ProposedStepIn] = Field(
        default_factory=list, description="Optional: nodes placed in reaction steps by name."
    )
    groups: list[ProposedGroupIn] = Field(
        default_factory=list,
        description="Optional: conformers of one intermediate placed in a group by label.",
    )
    filled: list[FilledIn] = Field(
        default_factory=list,
        description="D265: per hole you filled, the nodes you built for it. At most 10 holes "
        "are filled in a run (D265d): the 11th is refused.",
    )


class FilledNodeOut(BaseModel):
    id: str
    label: str


class FilledOut(BaseModel):
    hole: str
    nodes: list[FilledNodeOut]


class PlacedOut(BaseModel):
    id: str
    name: str
    created: bool


class ProposedOut(BaseModel):
    edges: list[TransitionOut]
    created: int  # edges made
    updated: int  # edges already there, opened for review again
    steps: list[PlacedOut]
    groups: list[PlacedOut]
    filled: list[FilledOut]  # D265: the nodes marked as MACE guesses, per hole
    open: dict[str, int]  # every open proposal, per confidence
    next: str  # what Claude does next: tidy the canvas (a page command, D226)


class ProposalsOut(BaseModel):
    edges: list[TransitionOut]  # the open proposals
    open: dict[str, int]  # per confidence


class FixIn(BaseModel):
    end: Literal["source", "target"]
    node_id: str  # the node or group the end moves to


def _since(request: Request) -> datetime | None:
    """D265d: a run is since Work on its own was turned on for this investigation (naive UTC,
    as the records' times); None while it is off (the whole playground)."""
    state = request.app.state.live.autonomy
    folder = _investigation(request).folder
    if not state.on_for(folder) or not state.since:
        return None
    return datetime.fromisoformat(state.since).astimezone(UTC).replace(tzinfo=None)


@router.post("/mechanism/propose", response_model=ProposedOut)
def propose(body: ProposeIn, request: Request, session: DbSession):
    found = proposals.propose(
        session,
        [e.model_dump() for e in body.edges],
        [s.model_dump() for s in body.steps],
        [g.model_dump() for g in body.groups],
        [f.model_dump() for f in body.filled],
        _since(request),
    )
    return ProposedOut(
        edges=[_transition_out(session, t) for t in found.edges],
        created=found.created,
        updated=found.updated,
        steps=[PlacedOut(**s) for s in found.steps],
        groups=[PlacedOut(id=g["id"], name=g["label"], created=g["created"]) for g in found.groups],
        filled=[FilledOut(**f) for f in found.filled],
        open=proposals.counts(proposals.open_proposals(session)),
        next=proposals.NEXT,
    )


@router.get("/mechanism/gaps")
def find_gaps(request: Request, session: DbSession) -> dict[str, Any]:
    """D265a: the holes in the mechanism, most useful first, with a suggested fill each;
    nothing is written."""
    return gaps.find(session, _investigation(request).folder, _since(request))


@router.get("/mechanism/direct-links")
def find_direct_links(
    session: DbSession,
    kind: Literal["all", "association", "same_atoms"] = Query(
        "all",
        description="Which pairs: all, association (a species joins or leaves) or "
        "same_atoms (coordination mode or conformation).",
    ),
    offset: int = Query(
        0, ge=0, description="Skip this many pairs (the `more` of the last answer)."
    ),
) -> dict[str, Any]:
    """FR-MCP-27, A190: pairs of minima a step with no TS may join (a free species joining or
    leaving, or a change of coordination mode or conformation), nearest first; nothing is
    written."""
    kinds = direct_links.KINDS if kind == "all" else (kind,)
    return direct_links.find(session, kinds, offset)


@router.get("/proposals", response_model=ProposalsOut)
def list_proposals(session: DbSession):
    found = proposals.open_proposals(session)
    return ProposalsOut(
        edges=[_transition_out(session, t) for t in found], open=proposals.counts(found)
    )


@router.post("/transitions/accept-high", response_model=list[TransitionOut])
def accept_all_high(request: Request, session: DbSession):
    _from_the_page(request, "accept proposed edges")
    return [_transition_out(session, t) for t in proposals.accept_all_high(session)]


@router.post("/transitions/{transition_id}/accept", response_model=TransitionOut)
def accept(transition_id: str, request: Request, session: DbSession):
    _from_the_page(request, "accept a proposed edge")
    return _transition_out(session, proposals.accept(session, transition_id))


@router.post("/transitions/{transition_id}/reject", status_code=204)
def reject(transition_id: str, request: Request, session: DbSession):
    _from_the_page(request, "reject a proposed edge")
    proposals.reject(session, transition_id)


@router.post("/transitions/{transition_id}/fix", response_model=TransitionOut)
def fix(transition_id: str, body: FixIn, request: Request, session: DbSession):
    _from_the_page(request, "fix a proposed edge")
    return _transition_out(session, proposals.fix(session, transition_id, body.end, body.node_id))
