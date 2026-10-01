"""HTTP API for buried volume and steric maps (D81, A32). All rules live in
`services/sterics.py`; these handlers only translate."""

from datetime import datetime
from typing import Any

from fastapi import APIRouter
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy.orm import Session

from chembook3d.api.routes import DbSession
from chembook3d.models import Node, StericProfile
from chembook3d.services import sterics as steric_service

router = APIRouter(prefix="/api")


class StericAtoms(BaseModel):
    centre: list[int]
    z_axis: list[int]
    xz_plane: list[int]
    excluded: list[int]


class StericResultOut(BaseModel):
    values: dict[str, Any]
    computed_at: datetime | None
    stale: str | None  # why the result is out of date, or None while it is current


class StericProfileOut(BaseModel):
    id: str
    name: str
    radius: float
    radii: str
    radii_scale: float
    include_hydrogens: bool
    mesh: float
    map_limit: float | None
    atoms: dict[str, StericAtoms]  # node id → its lists, 1-based
    results: dict[str, StericResultOut]  # node id → its last result


class StericProfileIn(BaseModel):
    name: str | None = None
    radius: float | None = None
    radii: str | None = None
    radii_scale: float | None = None
    include_hydrogens: bool | None = None
    mesh: float | None = None
    map_limit: float | None = None
    # Per node: the four lists, {"same_as": node id}, or null to take the node out.
    atoms: dict[str, dict[str, Any] | None] | None = None


class StericComputeIn(BaseModel):
    node_ids: list[str]
    maps: bool = False


class StericMapOut(BaseModel):
    x: list[float]  # grid positions along x and y, Å
    z: list[list[float | None]]  # [row along y][column along x], Å; None where no surface
    limit: float  # the colour scale runs from -limit to +limit Å


class StericComputedOut(BaseModel):
    node_id: str
    label: str
    values: dict[str, Any] | None
    map: StericMapOut | None
    error: str | None
    computed_at: datetime | None


class StericDifferenceIn(BaseModel):
    first_id: str
    second_id: str


class StericTableIn(BaseModel):
    node_ids: list[str]


def _profile_out(session: Session, profile: StericProfile) -> StericProfileOut:
    results = {}
    for entry in profile.entries:
        if entry.result is None:
            continue
        node = session.get(Node, entry.node_id)
        results[entry.node_id] = StericResultOut(
            values=entry.result,
            computed_at=entry.computed_at,
            stale=steric_service.stale_reason(profile, entry, node) if node else None,
        )
    return StericProfileOut(
        id=profile.id,
        name=profile.name,
        radius=profile.radius,
        radii=profile.radii,
        radii_scale=profile.radii_scale,
        include_hydrogens=profile.include_hydrogens,
        mesh=profile.mesh,
        map_limit=profile.map_limit,
        atoms={k: StericAtoms(**v) for k, v in steric_service.atoms_of(profile).items()},
        results=results,
    )


@router.get("/steric-profiles", response_model=list[StericProfileOut])
def list_steric_profiles(session: DbSession):
    return [_profile_out(session, p) for p in steric_service.list_profiles(session)]


@router.post("/steric-profiles", response_model=StericProfileOut, status_code=201)
def create_steric_profile(body: StericProfileIn, session: DbSession):
    created = steric_service.create_profile(session, body.model_dump(exclude_unset=True))
    return _profile_out(session, created)


@router.patch("/steric-profiles/{profile_id}", response_model=StericProfileOut)
def update_steric_profile(profile_id: str, body: StericProfileIn, session: DbSession):
    changes = body.model_dump(exclude_unset=True)
    return _profile_out(session, steric_service.update_profile(session, profile_id, changes))


@router.delete("/steric-profiles/{profile_id}", status_code=204)
def delete_steric_profile(profile_id: str, session: DbSession):
    steric_service.delete_profile(session, profile_id)


@router.post("/steric-profiles/{profile_id}/compute", response_model=list[StericComputedOut])
def compute_sterics(profile_id: str, body: StericComputeIn, session: DbSession):
    """B4: computes, stores and returns each node's result, with its map if asked."""
    return steric_service.compute(session, profile_id, body.node_ids, body.maps)


@router.post("/steric-profiles/{profile_id}/difference", response_model=StericMapOut)
def steric_difference(profile_id: str, body: StericDifferenceIn, session: DbSession):
    """B5: the first node's map minus the second's."""
    return steric_service.difference(session, profile_id, body.first_id, body.second_id)


@router.post("/steric-profiles/{profile_id}/table.csv")
def steric_table(profile_id: str, body: StericTableIn, session: DbSession) -> Response:
    """B5: %V_bur by quadrant and octant for the nodes, with the parameters on every row."""
    text = steric_service.table_csv(session, profile_id, body.node_ids)
    return Response(
        content=("\ufeff" + text).encode("utf-8"),  # the BOM makes spreadsheets read Å and ×
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="buried-volume.csv"'},
    )
