"""HTTP API for energies (phase 4): the level and energy-type choices, values for the canvas,
pathways, profiles and the energy table with its CSV export. The rules are in
`services/energies.py` and `services/pathways.py`; these handlers only translate."""

import csv
import io
from typing import Any

from fastapi import APIRouter, Response
from pydantic import BaseModel

from chembook3d import settings as app_settings
from chembook3d.api.routes import DbSession
from chembook3d.services import pathways
from chembook3d.services import species as species_service
from chembook3d.services import transitions as transition_service
from chembook3d.services.energies import Energies, LevelKey
from chembook3d.services.records import RecordError

router = APIRouter(prefix="/api")


class EnergyOptionOut(BaseModel):
    key: str
    label: str
    types: list[str]


class EnergyOptionsOut(BaseModel):
    levels: list[EnergyOptionOut]
    temperature: float
    cutoff: float


class ValueOut(BaseModel):
    value: float | None
    code: str | None
    message: str | None
    energy_calculation_id: str | None
    thermo_calculation_id: str | None
    details: dict[str, Any]


class EdgeValueOut(BaseModel):
    delta: float | None
    direct: bool
    message: str | None = None  # why delta is missing, when a free species has no value


class SpeciesCountOut(BaseModel):
    species_id: str
    label: str
    count: int  # added this many times; negative: subtracted


class RelativeOut(BaseModel):
    """ΔX from the reference for a node card, with the free species that keep its atoms
    equal to the reference's (D69, D72)."""

    value: float | None
    species: list[SpeciesCountOut]
    message: str | None  # why value is missing
    joined: bool  # joined to the reference by transitions; if not, no species are counted


class EnergyViewOut(BaseModel):
    level: str
    type: str
    values: dict[str, ValueOut]  # nodes and group nodes by id
    edges: dict[str, EdgeValueOut]
    reference_id: str | None = None
    relative: dict[str, RelativeOut] = {}  # by id, when a reference is given


class PathIn(BaseModel):
    path: list[str]
    branch_id: str | None = None


class ChoiceOut(BaseModel):
    transition_id: str
    node_id: str
    label: str
    status: str


class PathOut(BaseModel):
    path: list[str]
    choices: list[ChoiceOut]
    closed: bool  # the last node closed a cycle (A13)


def _path_out(found: pathways.Extended) -> PathOut:
    return PathOut(
        path=found.path, choices=[ChoiceOut(**c) for c in found.choices], closed=found.closed
    )


class ProfileIn(BaseModel):
    paths: list[list[str]]
    reference_id: str | None = None
    level: str
    type: str
    unit: str | None = None


class TableOut(BaseModel):
    columns: list[str]
    rows: list[list[str]]


def _key(text: str) -> LevelKey:
    return LevelKey.decode(text)


@router.get("/energies/options", response_model=EnergyOptionsOut)
def energy_options(session: DbSession):
    """FR-EN-01: the composite levels and, for each, the types with at least one value."""
    energies = Energies(session)
    return EnergyOptionsOut(
        levels=[EnergyOptionOut(**o) for o in energies.options()],
        temperature=energies.temperature,
        cutoff=energies.cutoff,
    )


@router.get("/energies/view", response_model=EnergyViewOut)
def energy_view(level: str, type: str, session: DbSession, reference: str | None = None):
    """Every node's and group's value at one selection, and ΔX on every edge (FR-EN-02).
    With a reference, also every node's and group's ΔX from it, balanced by the free species
    along the route from the reference (D72)."""
    key = _key(level)
    energies = Energies(session)
    values = {
        record_id: ValueOut(**energies.value(record_id, key, type).as_dict())
        for record_id in [*energies.nodes, *energies.groups]
    }
    edges = {}
    for transition in transition_service.list_transitions(session):
        source = values[transition.source_id].value
        target = values[transition.target_id].value
        # D69: the free species that join or leave on the edge keep the atoms equal.
        added, message = species_service.energy(
            session, energies, species_service.change(transition, forward=True), key, type
        )
        known = source is not None and target is not None and added is not None
        edges[transition.id] = EdgeValueOut(
            delta=target - source + added if known else None,
            direct=transition_service.describe(session, transition)["direct"],
            message=message,
        )
    relative = {}
    if reference is not None:
        # A reference deleted meanwhile leaves every card without a value, not an error.
        base = values[reference].value if reference in values else None
        balances = species_service.balances_from(session, reference) if reference in values else {}
        for record_id, own in values.items():
            balance = balances.get(record_id, {})
            added, message = species_service.energy(session, energies, balance, key, type)
            if own.value is None:
                message = own.message or "no value at this level"
            elif reference not in values:
                message = "the reference is no longer on the canvas"
            elif base is None:
                message = "the reference has no value at this level"
            relative[record_id] = RelativeOut(
                value=own.value + added - base if message is None else None,
                species=[SpeciesCountOut(**s) for s in species_service.describe(session, balance)],
                message=message,
                joined=record_id in balances,
            )
    return EnergyViewOut(
        level=level,
        type=type,
        values=values,
        edges=edges,
        reference_id=reference,
        relative=relative,
    )


@router.post("/pathways/extend", response_model=PathOut)
def extend_pathway(body: PathIn, session: DbSession):
    return _path_out(pathways.extend(session, body.path, body.branch_id))


@router.get("/branches/{branch_id}/pathway", response_model=PathOut)
def branch_pathway(branch_id: str, session: DbSession):
    return _path_out(pathways.branch_pathway(session, branch_id))


@router.post("/energies/profile")
def energy_profile(body: ProfileIn, session: DbSession) -> dict[str, Any]:
    return pathways.profiles(session, body.paths, body.reference_id, _key(body.level), body.type)


def _table(body: ProfileIn, session) -> dict[str, Any]:
    unit = body.unit or app_settings.load().energy_unit
    if unit not in app_settings.ENERGY_UNITS:
        raise RecordError(f"unknown energy unit '{unit}'")
    return pathways.table(session, body.paths, body.reference_id, _key(body.level), body.type, unit)


@router.post("/energies/table", response_model=TableOut)
def energy_table(body: ProfileIn, session: DbSession):
    return TableOut(**_table(body, session))


@router.post("/energies/table.csv")
def energy_table_csv(body: ProfileIn, session: DbSession) -> Response:
    """FR-EN-06: the same text as the table on screen (T-EN-08). UTF-8 with a byte-order
    mark, so spreadsheet programs read Δ and the unit names correctly."""
    data = _table(body, session)
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\r\n")
    writer.writerow(data["columns"])
    writer.writerows(data["rows"])
    return Response(
        content=("﻿" + buffer.getvalue()).encode("utf-8"),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="energy-table.csv"'},
    )
