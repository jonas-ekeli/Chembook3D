"""HTTP API for conformer equilibria (D171). All rules live in `services/equilibrium.py`;
these handlers only translate."""

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel

from chembook3d.api.routes import DbSession
from chembook3d.models import ConformerEquilibrium
from chembook3d.services import equilibrium as equilibrium_service
from chembook3d.services.records import get

router = APIRouter(prefix="/api")


class MemberOut(BaseModel):
    id: str
    count: int


class EquilibriumOut(BaseModel):
    id: str
    name: str
    members: list[MemberOut]  # nodes and groups; TS nodes are the barriers (D171a)
    level: str | None
    energy_type: str
    temperature: float | None  # None: the G_qh temperature setting
    start_id: str | None  # None: the lowest conformer
    notes: str


class MemberIn(BaseModel):
    id: str
    count: int = 1


class EquilibriumIn(BaseModel):
    name: str | None = None
    members: list[MemberIn | str] | None = None
    level: str | None = None
    energy_type: str | None = None
    temperature: float | None = None
    start_id: str | None = None
    notes: str | None = None


def _out(equilibrium: ConformerEquilibrium) -> EquilibriumOut:
    return EquilibriumOut(
        **{field: getattr(equilibrium, field) for field in EquilibriumOut.model_fields}
    )


def _changes(body: EquilibriumIn) -> dict[str, Any]:
    changes = body.model_dump(exclude_unset=True)
    if body.members is not None:
        changes["members"] = [m if isinstance(m, str) else m.model_dump() for m in body.members]
    return changes


@router.get("/equilibria", response_model=list[EquilibriumOut])
def list_equilibria(session: DbSession):
    return [_out(e) for e in equilibrium_service.list_all(session)]


@router.post("/equilibria", response_model=EquilibriumOut, status_code=201)
def create_equilibrium(body: EquilibriumIn, session: DbSession):
    return _out(equilibrium_service.create(session, _changes(body)))


@router.patch("/equilibria/{equilibrium_id}", response_model=EquilibriumOut)
def update_equilibrium(equilibrium_id: str, body: EquilibriumIn, session: DbSession):
    return _out(equilibrium_service.update(session, equilibrium_id, _changes(body)))


@router.delete("/equilibria/{equilibrium_id}", status_code=204)
def delete_equilibrium(equilibrium_id: str, session: DbSession):
    equilibrium_service.delete(session, equilibrium_id)


@router.get("/equilibria/{equilibrium_id}/result")
def equilibrium_result(equilibrium_id: str, session: DbSession) -> dict[str, Any]:
    """D171: each conformer's ΔG, population and effective barrier, each TS's barriers, rates
    and half-lives, and each set's relaxation times, time to equilibrium and curve. Energies in
    hartree, rates in 1/s, times in s."""
    found = get(session, ConformerEquilibrium, equilibrium_id, "Conformer equilibrium")
    return equilibrium_service.result(session, found)
