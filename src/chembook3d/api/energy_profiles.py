"""HTTP API for saved energy profiles (D190): the rules are in `services/energy_profiles.py`.
Quiet for other tabs (`live.QUIET`), as the drawer saves on every step; Claude reads them
through `list_energy_profiles` and changes them only in the page."""

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel

from chembook3d.api.routes import DbSession
from chembook3d.models import EnergyProfile
from chembook3d.services import energy_profiles as profile_service

router = APIRouter(prefix="/api")


class ProfilePathOut(BaseModel):
    ids: list[str]
    branch_id: str | None
    shown: bool


class EnergyProfileOut(BaseModel):
    id: str
    name: str
    paths: list[ProfilePathOut]


class EnergyProfileIn(BaseModel):
    name: str | None = None
    # Each {"ids": [...], "branch_id": ... or null, "shown": true or false}.
    paths: list[dict[str, Any]] | None = None


def _out(session: DbSession, profile: EnergyProfile) -> EnergyProfileOut:
    paths = profile_service.current_paths(session, profile.paths or [])
    return EnergyProfileOut(
        id=profile.id, name=profile.name, paths=[ProfilePathOut(**p) for p in paths]
    )


@router.get("/energy-profiles", response_model=list[EnergyProfileOut])
def list_energy_profiles(session: DbSession):
    return [_out(session, p) for p in profile_service.list_profiles(session)]


@router.post("/energy-profiles", response_model=EnergyProfileOut, status_code=201)
def create_energy_profile(body: EnergyProfileIn, session: DbSession):
    return _out(session, profile_service.create(session, body.name, body.paths))


@router.patch("/energy-profiles/{profile_id}", response_model=EnergyProfileOut)
def update_energy_profile(profile_id: str, body: EnergyProfileIn, session: DbSession):
    changes = body.model_dump(exclude_unset=True)
    return _out(session, profile_service.update(session, profile_id, changes))


@router.post("/energy-profiles/{profile_id}/duplicate", response_model=EnergyProfileOut)
def duplicate_energy_profile(profile_id: str, session: DbSession):
    return _out(session, profile_service.duplicate(session, profile_id))


@router.delete("/energy-profiles/{profile_id}", status_code=204)
def delete_energy_profile(profile_id: str, session: DbSession):
    profile_service.delete(session, profile_id)
