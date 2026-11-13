"""HTTP API for saved energy profiles (D190): the rules are in `services/energy_profiles.py`.
Quiet for other tabs (`live.QUIET`), as the drawer saves on every step. D227: Claude makes,
changes and copies them too; what it gives is checked as a pathway, a profile's pathways through
a deleted node stay the user's to remove, and the open drawer starts anew from what is saved
(`live.PROFILES`)."""

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel

from chembook3d.api.routes import DbSession
from chembook3d.autonomy import AGENT
from chembook3d.models import Branch, EnergyProfile
from chembook3d.services import energy_profiles as profile_service
from chembook3d.services import history, pathways
from chembook3d.services.records import RecordError, get

router = APIRouter(prefix="/api")


class PathShiftOut(BaseModel):
    at: int  # the step's index on the pathway
    by: int  # columns it, and every step after it, is moved later (D194)


class ProfilePathOut(BaseModel):
    ids: list[str]
    branch_id: str | None
    shown: bool
    shifts: list[PathShiftOut] = []
    colour: str | None = None  # D195: chosen by the user; None: the branch's colour


class EnergyProfileOut(BaseModel):
    id: str
    name: str
    paths: list[ProfilePathOut]
    # D221: its pathways through a node deleted since, as stored: listed by the drawer, never
    # drawn, valued or exported.
    lost: list[ProfilePathOut] = []
    # D205: the node or group all its pathways are relative to; None: the first node shown.
    reference_id: str | None = None


class EnergyProfileIn(BaseModel):
    name: str | None = None
    # Each {"ids": [...], "branch_id": ... or null, "shown": true or false, "shifts": [{"at": a
    # step's index, "by": columns later}] (D194: optional, none moved), "colour": "#rrggbb" or
    # null (D195: optional, the branch's colour)}.
    paths: list[dict[str, Any]] | None = None
    # D205: a node or group on one of its pathways, or null for the first node shown.
    reference_id: str | None = None


def _out(session: DbSession, profile: EnergyProfile) -> EnergyProfileOut:
    paths, lost = profile_service.split_paths(session, profile.paths or [])
    return EnergyProfileOut(
        id=profile.id,
        name=profile.name,
        paths=[ProfilePathOut(**p) for p in paths],
        lost=[ProfilePathOut(**p) for p in lost],
        reference_id=profile_service.current_reference(profile, paths),
    )


def _checked(session: DbSession, paths: Any, kept: list[dict[str, Any]] | None = None) -> Any:
    """D227: the pathways Claude gives must be pathways, as the drawer's own are when it follows
    them: every id a node or group, each joined to the next by a transition (INV-2), a branch
    that exists. `kept`, the profile's pathways through a node deleted since (D221), stay as
    stored after them. The page's own saves pass as they are."""
    if history.SOURCE.get() != AGENT or not isinstance(paths, list):
        return paths
    known = profile_service.on_canvas(session)
    for path in profile_service.clean_paths(paths):
        unknown = [i for i in path["ids"] if i not in known]
        if unknown:
            raise RecordError(f"No node or group has the id {unknown[0]}")
        pathways.resolve(session, path["ids"])
        if path["branch_id"] is not None and session.get(Branch, path["branch_id"]) is None:
            raise RecordError(f"No branch has the id {path['branch_id']}")
    return [*paths, *(kept or [])]


@router.get("/energy-profiles", response_model=list[EnergyProfileOut])
def list_energy_profiles(session: DbSession):
    return [_out(session, p) for p in profile_service.list_profiles(session)]


@router.post("/energy-profiles", response_model=EnergyProfileOut, status_code=201)
def create_energy_profile(body: EnergyProfileIn, session: DbSession):
    paths = _checked(session, body.paths)
    return _out(session, profile_service.create(session, body.name, paths, body.reference_id))


@router.patch("/energy-profiles/{energy_profile_id}", response_model=EnergyProfileOut)
def update_energy_profile(energy_profile_id: str, body: EnergyProfileIn, session: DbSession):
    changes = body.model_dump(exclude_unset=True)
    if "paths" in changes:
        profile = get(session, EnergyProfile, energy_profile_id, "Energy profile")
        lost = profile_service.split_paths(session, profile.paths or [])[1]
        changes["paths"] = _checked(session, changes["paths"], lost)
    return _out(session, profile_service.update(session, energy_profile_id, changes))


@router.post("/energy-profiles/{energy_profile_id}/duplicate", response_model=EnergyProfileOut)
def duplicate_energy_profile(energy_profile_id: str, session: DbSession):
    return _out(session, profile_service.duplicate(session, energy_profile_id))


@router.delete("/energy-profiles/{energy_profile_id}", status_code=204)
def delete_energy_profile(energy_profile_id: str, session: DbSession):
    profile_service.delete(session, energy_profile_id)
