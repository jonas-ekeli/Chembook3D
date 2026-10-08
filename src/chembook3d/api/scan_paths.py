"""Scan path between two connected nodes (D114, A60): the plan the dialog shows, and the cloud
job it sends (D93). The rules are in `services/scan_path.py`."""

from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from chembook3d import cloud_jobs
from chembook3d.api.jobs import _local_page_only, launcher
from chembook3d.api.pathway import AtomMatchOut, atom_match_out
from chembook3d.api.routes import DbSession, _investigation
from chembook3d.services import scan_path

router = APIRouter(prefix="/api/scan-paths")


class PlanIn(BaseModel):
    start_id: str = Field(description="A node, or a group standing for its representative.")
    end_id: str
    pairs: list[list[int]] = Field(
        default_factory=list, description="Atom pairs fixed by hand, [start, end], 1-based."
    )
    start_member_id: str | None = Field(None, description="A group end's member to use.")
    end_member_id: str | None = None


class MemberOut(BaseModel):
    id: str
    label: str


class EndOut(BaseModel):
    node_id: str
    label: str
    role: str
    group_id: str | None
    group_label: str | None
    members: list[MemberOut]  # a group end's members with coordinates


class SuggestedOut(BaseModel):
    kind: str
    atoms: list[int]  # 1-based, in the start's numbering
    ticked: bool
    why: str
    start_value: float
    end_value: float


class TsEndOut(BaseModel):
    end: str  # "start" or "end"
    node_id: str
    label: str
    imaginary: float | None  # cm⁻¹, negative
    guess: bool  # no imaginary mode to read: the user ticks the coordinates
    frequency_job: bool
    suggested: list[SuggestedOut]


class PlanOut(BaseModel):
    start: EndOut
    end: EndOut
    edge_id: str
    match: AtomMatchOut
    charge: int
    multiplicity: int
    solvent: str | None  # xTB's ALPB name, from the nodes' levels
    solvents: list[str]  # every ALPB solvent
    ts_ends: list[TsEndOut]


class HeldIn(BaseModel):
    end: str = Field(description='"start" or "end": the TS end where the coordinate is held.')
    atoms: list[int] = Field(description="2 to 4 atoms, 1-based, in the start's numbering.")


class JobIn(PlanIn):
    held: list[HeldIn] = Field(default_factory=list)
    solvent: str | None = None
    start: bool = Field(True, description="Start the cloud session at once.")


def _end_out(end: scan_path.End) -> EndOut:
    return EndOut(
        node_id=end.node.id,
        label=end.name,
        role=end.node.role,
        group_id=end.group.id if end.group else None,
        group_label=(end.group.label or "Group") if end.group else None,
        members=[MemberOut(id=n.id, label=n.label or "Untitled node") for n in end.members],
    )


def _plan(body: PlanIn, session) -> scan_path.Plan:
    return scan_path.plan(
        session, body.start_id, body.end_id, body.pairs, body.start_member_id, body.end_member_id
    )


@router.post("/plan", response_model=PlanOut)
def plan(body: PlanIn, session: DbSession):
    """D114: the checks, the atom match and the suggested TS coordinates. Changes nothing."""
    p = _plan(body, session)
    return PlanOut(
        start=_end_out(p.start),
        end=_end_out(p.end),
        edge_id=p.edge.id,
        match=atom_match_out(p.start.node, p.end.node, p.match),
        charge=p.charge,
        multiplicity=p.multiplicity,
        solvent=p.solvent,
        solvents=sorted(scan_path.ALPB_SOLVENTS),
        ts_ends=[TsEndOut(**t) for t in p.ts_ends],
    )


@router.post("")
def send(body: JobIn, request: Request, session: DbSession) -> dict[str, Any]:
    """D114: write the scan path job and, unless `start` is false, start its cloud session.
    A job that cannot start stays a draft and says why (`start_error`)."""
    _local_page_only(request)
    investigation = _investigation(request)
    p = _plan(body, session)
    record = scan_path.create_job(
        investigation.folder,
        investigation.name,
        p,
        [h.model_dump() for h in body.held],
        body.solvent,
    )
    folder = investigation.folder
    start_error = None
    if body.start:
        try:
            cloud_jobs.start_job(folder, record["id"], launcher(request))
        except cloud_jobs.CloudJobError as exc:
            start_error = str(exc)
    job = cloud_jobs.job_status(folder, record["id"], launcher(request), fetch=False)
    return {"job": job, "start_error": start_error}
