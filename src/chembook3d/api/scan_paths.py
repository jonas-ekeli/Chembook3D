"""Scan path between two connected nodes (D114, A60): the plan the dialog shows, and the cloud
job it sends (D93). The rules are in `services/scan_path.py`."""

from typing import Any, Literal

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from chembook3d import cloud_jobs, xyz
from chembook3d.api.jobs import _local_page_only, launcher
from chembook3d.api.pathway import AtomMatchOut, atom_match_out
from chembook3d.api.routes import DbSession, _investigation
from chembook3d.services import atom_matching, scan_path, scan_species

router = APIRouter(prefix="/api/scan-paths")


class PlanIn(BaseModel):
    start_id: str = Field(description="A node, or a group standing for its representative.")
    end_id: str
    pairs: list[list[int]] = Field(
        default_factory=list, description="Atom pairs fixed by hand, [start, end], 1-based."
    )
    start_member_id: str | None = Field(None, description="A group end's member to use.")
    end_member_id: str | None = None
    clearance: float = Field(
        scan_species.CLEARANCE,
        description="D120: with a species joining or leaving, its closest contact (Å) with the "
        "complex in the separated structure.",
    )


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


class SpeciesOut(BaseModel):
    """D120: the free species joining or leaving along the path, in the start's numbering."""

    node_id: str
    label: str
    direction: str  # "joins" or "leaves", from the path's start to its end
    bound: str  # the end where it is bound: "end" when it joins, "start" when it leaves
    atoms: list[int]  # its atoms, 1-based
    bonds: list[list[int]]  # the bonds it makes with the complex where it is bound
    anchor: list[int]  # the complex atoms the line out starts from
    clearance: float  # Å asked for
    pull: float  # Å it was moved out along the line from its bound place
    closest: float  # Å, its closest contact with the complex once separated
    separated_xyz: str  # the separated end
    bound_xyz: str  # the bound end, in the same frame


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
    warnings: list[str]  # D116: e.g. no bond forms or breaks between the ends
    species: SpeciesOut | None  # D120


class HeldIn(BaseModel):
    end: str = Field(description='"start" or "end": the TS end where the coordinate is held.')
    atoms: list[int] = Field(description="2 to 4 atoms, 1-based, in the start's numbering.")


class DriveIn(BaseModel):
    atoms: list[int] = Field(description="2 to 4 atoms, 1-based, in the start's numbering.")
    from_value: float | None = Field(
        None, alias="from", description="Where the scan starts; the start's value if left out."
    )
    to_value: float | None = Field(
        None,
        alias="to",
        description="Where it ends; the end's value if left out. A dihedral may lie past ±180°.",
    )


class JobIn(PlanIn):
    held: list[HeldIn] = Field(default_factory=list)
    drive: list[DriveIn] = Field(
        default_factory=list,
        description="D119: the user's own coordinates, run first as given; empty: the agent's.",
    )
    drive_order: Literal["together", "staged"] = Field(
        "together", description="One concerted scan, or one stage per coordinate in order."
    )
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
        session,
        body.start_id,
        body.end_id,
        body.pairs,
        body.start_member_id,
        body.end_member_id,
        body.clearance,
    )


def _xyz(rows, comment: str) -> str:
    return xyz.format_xyz([xyz.Atom(e, x, y, z) for e, x, y, z in rows], comment=comment)


def _match_out(p: scan_path.Plan) -> AtomMatchOut:
    """The match as the review shows it; with a species (D120), on the structures it was made
    on: the separated start (or end) and the bound end (or start)."""
    if p.match_rows is None:
        return atom_match_out(p.start.node, p.end.node, p.match)
    start_rows, end_rows = p.match_rows
    return AtomMatchOut(
        start_id=p.start.node.id,
        end_id=p.end.node.id,
        start_xyz=_xyz(start_rows, p.start.name),
        end_xyz=_xyz(end_rows, p.end.name),
        renumbered_xyz=_xyz(p.renumbered, f"{p.end.name} in the order of {p.start.name}"),
        **atom_matching.summary(p.match),
    )


def _species_out(p: scan_path.Plan) -> SpeciesOut | None:
    j = p.species
    if j is None:
        return None
    bound = p.renumbered if j.bound == "end" else p.start_rows
    return SpeciesOut(
        node_id=j.node.id,
        label=j.name,
        direction=j.direction,
        bound=j.bound,
        atoms=j.atoms,
        bonds=j.bonds,
        anchor=j.anchor,
        clearance=j.clearance,
        pull=j.pull,
        closest=j.closest,
        separated_xyz=_xyz(j.separated, f"{j.name} separated"),
        bound_xyz=_xyz(bound, "bound"),
    )


@router.post("/plan", response_model=PlanOut)
def plan(body: PlanIn, session: DbSession):
    """D114: the checks, the atom match and the suggested TS coordinates. Changes nothing."""
    p = _plan(body, session)
    return PlanOut(
        start=_end_out(p.start),
        end=_end_out(p.end),
        edge_id=p.edge.id,
        match=_match_out(p),
        charge=p.charge,
        multiplicity=p.multiplicity,
        solvent=p.solvent,
        solvents=sorted(scan_path.ALPB_SOLVENTS),
        ts_ends=[TsEndOut(**t) for t in p.ts_ends],
        warnings=p.warnings,
        species=_species_out(p),
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
        [{"atoms": d.atoms, "from": d.from_value, "to": d.to_value} for d in body.drive],
        body.drive_order,
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
