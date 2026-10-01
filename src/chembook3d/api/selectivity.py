"""HTTP API for selectivities (D83). All rules live in `services/selectivity.py`; these
handlers only translate."""

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel

from chembook3d.api.routes import DbSession
from chembook3d.models import Selectivity
from chembook3d.services import selectivity as selectivity_service
from chembook3d.services.records import get

router = APIRouter(prefix="/api")


class OutcomeOut(BaseModel):
    id: str
    name: str
    members: list[str]  # node and group ids, in order
    experimental: float | None


class SelectivityOut(BaseModel):
    id: str
    name: str
    level: str | None
    energy_type: str
    temperature: float | None  # None: the G_qh temperature setting
    conformers: str
    excess: str
    notes: str
    outcomes: list[OutcomeOut]


class OutcomeIn(BaseModel):
    name: str
    members: list[str] = []
    experimental: float | None = None


class SelectivityIn(BaseModel):
    name: str | None = None
    level: str | None = None
    energy_type: str | None = None
    temperature: float | None = None
    conformers: str | None = None
    excess: str | None = None
    notes: str | None = None
    outcomes: list[OutcomeIn] | None = None


def _out(selectivity: Selectivity) -> SelectivityOut:
    return SelectivityOut(
        id=selectivity.id,
        name=selectivity.name,
        level=selectivity.level,
        energy_type=selectivity.energy_type,
        temperature=selectivity.temperature,
        conformers=selectivity.conformers,
        excess=selectivity.excess,
        notes=selectivity.notes,
        outcomes=[OutcomeOut(**o) for o in selectivity_service.outcomes_value(selectivity)],
    )


def _changes(body: SelectivityIn) -> dict[str, Any]:
    return body.model_dump(exclude_unset=True)


@router.get("/selectivities", response_model=list[SelectivityOut])
def list_selectivities(session: DbSession):
    return [_out(s) for s in selectivity_service.list_all(session)]


@router.post("/selectivities", response_model=SelectivityOut, status_code=201)
def create_selectivity(body: SelectivityIn, session: DbSession):
    return _out(selectivity_service.create(session, _changes(body)))


@router.patch("/selectivities/{selectivity_id}", response_model=SelectivityOut)
def update_selectivity(selectivity_id: str, body: SelectivityIn, session: DbSession):
    return _out(selectivity_service.update(session, selectivity_id, _changes(body)))


@router.delete("/selectivities/{selectivity_id}", status_code=204)
def delete_selectivity(selectivity_id: str, session: DbSession):
    selectivity_service.delete(session, selectivity_id)


@router.get("/selectivities/{selectivity_id}/result")
def selectivity_result(selectivity_id: str, session: DbSession) -> dict[str, Any]:
    """S2–S6: ΔΔG‡ (hartree), the predicted ratio both ways, the ee or de, the experiment."""
    found = get(session, Selectivity, selectivity_id, "Selectivity")
    return selectivity_service.result(session, found)
