"""HTTP API for a TS node's IRC (D131, A76). The rules are in `services/irc.py`; IRC files are
added through the import routes with `irc_part` and `irc_direction` in the options, and taken
off here, as the undo of their import (D102)."""

from dataclasses import asdict
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import event
from sqlalchemy.orm import Session

from chembook3d.api.routes import DbSession, _investigation
from chembook3d.services import import_undo, irc

router = APIRouter(prefix="/api")


class PointOut(BaseModel):
    side: str
    number: int
    coordinate: float
    program_coordinate: float | None
    energy: float | None
    relative: float | None
    rows: list[list[Any]]
    calculation_id: str
    file: str


class PartOut(BaseModel):
    source_file_id: str | None
    file: str
    part: str
    direction: str
    calculation_ids: list[str]
    types: list[str]
    program: str
    level: str
    termination: str
    imported_at: str
    used: bool
    note: str
    endings: dict[str, str]
    max_points: int | None
    points: dict[str, int]


class EndPointOut(BaseModel):
    direction: str
    optimization_id: str | None
    frequency_id: str | None
    level: str
    converged: bool | None
    imaginary: int | None
    start_rmsd: float | None
    values: dict[str, Any]


class SideOut(BaseModel):
    direction: str
    ending: str | None
    max_points: int | None
    points: int
    last_relative: float | None
    end_point: EndPointOut | None


class IrcOut(BaseModel):
    node_id: str
    label: str
    is_ts: bool
    parts: list[PartOut]
    points: list[PointOut]
    sides: list[SideOut]
    ts_energy: float | None
    ts_values: dict[str, Any]
    warnings: list[str]


class IrcSummaryOut(BaseModel):
    is_ts: bool
    count: int
    paths: dict[str, int]
    optimizations: dict[str, int]
    frequencies: dict[str, int]


@router.get("/nodes/{node_id}/irc", response_model=IrcOut)
def get_irc(node_id: str, request: Request, session: DbSession):
    """The node's IRC: its files, the path through the TS with every point's structure and
    energy, how each direction ended and its end point's energies relative to the TS."""
    return asdict(irc.read(session, _investigation(request).folder, node_id))


@router.get("/nodes/{node_id}/irc/summary", response_model=IrcSummaryOut)
def get_irc_summary(node_id: str, session: DbSession):
    """What the node's IRC holds, without reading its files."""
    return irc.summary(session, node_id)


@router.delete("/nodes/{node_id}/irc/files/{source_file_id}", status_code=204)
def remove_irc_file(node_id: str, source_file_id: str, request: Request, session: DbSession):
    """Take a file's IRC parts off the node by undoing its import (D102)."""
    try:
        stored = irc.remove(session, node_id, source_file_id)
    except import_undo.UndoBlocked as exc:
        raise HTTPException(409, {"blockers": exc.blockers, "message": str(exc)}) from exc
    folder = _investigation(request).folder

    def remove_copy(_session: Session) -> None:
        import_undo.remove_copy(folder, stored)

    event.listen(session, "after_commit", remove_copy, once=True)
