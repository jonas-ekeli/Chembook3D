"""HTTP API for a TS node's IRC (D131, A76). The rules are in `services/irc.py`; IRC files are
added through the import routes with `irc_part` and `irc_direction` in the options, and taken
off here, as the undo of their import (D102)."""

from dataclasses import asdict
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import event
from sqlalchemy.orm import Session

from chembook3d.api.routes import DbSession, _investigation, _staging
from chembook3d.services import import_undo, imports, irc

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


class NeighbourOut(BaseModel):
    node_id: str
    label: str
    edge: str
    rmsd: float | None
    rmsd_all: float | None
    same_bonds: bool
    note: str


class ConnectionOut(BaseModel):
    verdict: str
    judged_on: str
    node_id: str | None
    label: str
    rmsd: float | None
    atoms: int
    neighbours: list[NeighbourOut]


class SameAsOut(BaseModel):
    node_id: str
    label: str
    rmsd: float


class SideOut(BaseModel):
    direction: str
    ending: str | None
    max_points: int | None
    points: int
    last_relative: float | None
    end_point: EndPointOut | None
    connection: ConnectionOut | None
    same_as: list[SameAsOut]


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
    left: str


class IrcSummaryOut(BaseModel):
    is_ts: bool
    count: int
    paths: dict[str, int]
    optimizations: dict[str, int]
    frequencies: dict[str, int]


@router.get("/nodes/{node_id}/irc", response_model=IrcOut)
def get_irc(node_id: str, request: Request, session: DbSession, structures: bool = False):
    """The node's IRC: its files, the path through the TS with every point's energy (and,
    with `structures`, its coordinates), how each direction ended, where each end leads among
    the TS's neighbours, and the end points' energies relative to the TS."""
    folder = _investigation(request).folder
    return asdict(irc.read(session, folder, node_id, structures=structures))


@router.get("/nodes/{node_id}/irc/summary", response_model=IrcSummaryOut)
def get_irc_summary(node_id: str, session: DbSession):
    """What the node's IRC holds, without reading its files."""
    return irc.summary(session, node_id)


class MadeNodeOut(BaseModel):
    node_id: str


@router.post("/nodes/{node_id}/irc/{direction}/node", response_model=MadeNodeOut)
def make_end_point_node(node_id: str, direction: str, request: Request, session: DbSession):
    """Q38: make the optimized end point a node of its own, imported again from its files."""
    if direction not in irc.DIRECTIONS:
        raise HTTPException(422, "direction is forward or reverse")
    folder = _investigation(request).folder
    try:
        made = irc.make_node(session, folder, _staging(request), node_id, direction)
    except imports.ImportBlocked as exc:
        raise HTTPException(409, {"blockers": exc.blockers, "message": str(exc)}) from exc
    except imports.ImportFailed as exc:
        raise HTTPException(422, str(exc)) from exc
    return MadeNodeOut(node_id=made)


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
