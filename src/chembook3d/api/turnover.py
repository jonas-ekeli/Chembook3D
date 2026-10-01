"""HTTP API for turnovers (D86). All rules live in `services/turnover.py`; these handlers
only translate."""

import csv
import io
from typing import Any

from fastapi import APIRouter, Response
from pydantic import BaseModel

from chembook3d.api.routes import DbSession
from chembook3d.models import Turnover
from chembook3d.services import turnover as turnover_service
from chembook3d.services.records import RecordError, get

router = APIRouter(prefix="/api")


class TurnoverOut(BaseModel):
    id: str
    name: str
    path: list[str]  # node and group ids; the last returns to an earlier one (A13)
    level: str | None
    energy_type: str
    temperature: float | None  # None: the G_qh temperature setting
    compare_id: str | None
    notes: str


class TurnoverIn(BaseModel):
    name: str | None = None
    path: list[str] | None = None
    level: str | None = None
    energy_type: str | None = None
    temperature: float | None = None
    compare_id: str | None = None
    notes: str | None = None


def _out(turnover: Turnover) -> TurnoverOut:
    return TurnoverOut(**{field: getattr(turnover, field) for field in TurnoverOut.model_fields})


def _changes(body: TurnoverIn) -> dict[str, Any]:
    return body.model_dump(exclude_unset=True)


@router.get("/turnovers", response_model=list[TurnoverOut])
def list_turnovers(session: DbSession):
    return [_out(t) for t in turnover_service.list_all(session)]


@router.post("/turnovers", response_model=TurnoverOut, status_code=201)
def create_turnover(body: TurnoverIn, session: DbSession):
    return _out(turnover_service.create(session, _changes(body)))


@router.patch("/turnovers/{turnover_id}", response_model=TurnoverOut)
def update_turnover(turnover_id: str, body: TurnoverIn, session: DbSession):
    return _out(turnover_service.update(session, turnover_id, _changes(body)))


@router.delete("/turnovers/{turnover_id}", status_code=204)
def delete_turnover(turnover_id: str, session: DbSession):
    turnover_service.delete(session, turnover_id)


@router.get("/turnovers/{turnover_id}/result")
def turnover_result(turnover_id: str, session: DbSession) -> dict[str, Any]:
    """T1–T5: TOF (1/s), δE, ΔG_r (hartree), the degree of TOF control of every point, the
    profile of the cycle, the table and the comparison."""
    found = get(session, Turnover, turnover_id, "Turnover")
    return turnover_service.result(session, found)


@router.get("/turnovers/{turnover_id}/table.csv")
def turnover_table_csv(turnover_id: str, session: DbSession) -> Response:
    """T4: the same text as the table on screen, UTF-8 with a byte-order mark (as the energy
    table's CSV)."""
    found = turnover_service.result(session, get(session, Turnover, turnover_id, "Turnover"))
    if found["table"] is None:
        raise RecordError(found["message"] or "The turnover has no result")
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\r\n")
    writer.writerow(found["table"]["columns"])
    writer.writerows(found["table"]["rows"])
    return Response(
        content=("\ufeff" + buffer.getvalue()).encode("utf-8"),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="turnover.csv"'},
    )
