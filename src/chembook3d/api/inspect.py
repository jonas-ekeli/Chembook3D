"""The Inspect window's API (D123c, FR-3D-11): a file on this computer or on a logged-in SSH
server read without importing it.

A file on a server is copied into a temporary folder, read from there and copied again on
Refresh; the copy is deleted when the window closes. "Import this file" stages the copy (or the
local file) as the import dialog does, so the import goes on with its preview. Only the app's
own page uses this, as for the server connection (D122).
"""

import asyncio
import posixpath
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from chembook3d import settings as app_settings
from chembook3d.api.pathway import CriterionOut, ModesOut, StepFrameOut
from chembook3d.api.remote import _check, _copy_down, _saved
from chembook3d.api.routes import _investigation, _staging
from chembook3d.models import new_id
from chembook3d.remote import servers
from chembook3d.services import imports, inspection

router = APIRouter(prefix="/api/inspections")

MAX_OPEN = 8  # windows open at once; opening another closes the oldest


@dataclass
class _Open:
    id: str
    path: str  # on this computer, or on the server
    server_id: str | None
    copy: Path | None = None  # the temporary folder holding a server's file
    opened: float = field(default_factory=time.monotonic)

    @property
    def local(self) -> Path:
        if self.copy is not None:
            return self.copy / posixpath.basename(self.path)
        return Path(self.path)

    def discard_copy(self) -> None:
        if self.copy is not None:
            shutil.rmtree(self.copy, ignore_errors=True)
            self.copy = None


class Inspections:
    """The open Inspect windows of the running app."""

    def __init__(self) -> None:
        self.open: dict[str, _Open] = {}

    def add(self, item: _Open) -> None:
        while len(self.open) >= MAX_OPEN:
            oldest = min(self.open.values(), key=lambda o: o.opened)
            self.close(oldest.id)
        self.open[item.id] = item

    def close(self, inspection_id: str) -> None:
        item = self.open.pop(inspection_id, None)
        if item is not None:
            item.discard_copy()

    def close_all(self) -> None:
        for key in list(self.open):
            self.close(key)


def inspections(app) -> Inspections:
    found = getattr(app.state, "inspections", None)
    if found is None:
        found = app.state.inspections = Inspections()
    return found


class InspectIn(BaseModel):
    path: str  # on this computer, or a full path on the server
    server: str | None = None  # a logged-in server's id


class ImportIn(BaseModel):
    node_id: str | None = None  # import onto this node


class InspectStepOut(BaseModel):
    index: int
    job_type: str  # as the import names it, or "structures" for an xyz file
    route: str
    method: str | None
    basis: str | None
    charge: int | None
    multiplicity: int | None
    termination: str  # "normal" or "abnormal" (not ended, or ended with an error)
    energy: float | None  # hartree, the step's last SCF energy
    optimization_converged: bool | None
    frequencies: list[float]
    imaginary: int
    scan: str | None
    points: int
    frames: list[StepFrameOut]
    modes: ModesOut | None
    missing: list[str]  # what the file does not say, as the import lists it


class InspectionOut(BaseModel):
    id: str
    name: str
    path: str  # where it is, as the import records it (saga:/cluster/… for a server)
    server: str | None
    server_name: str | None
    size: int  # bytes read
    read_at: float  # seconds since 1970
    program: str
    version: str | None
    warnings: list[str]
    steps: list[InspectStepOut]


def _step_out(step: inspection.Step) -> InspectStepOut:
    return InspectStepOut(
        index=step.index,
        job_type=step.job_type,
        route=step.route,
        method=step.method,
        basis=step.basis,
        charge=step.charge,
        multiplicity=step.multiplicity,
        termination=step.termination,
        energy=step.energy,
        optimization_converged=step.optimization_converged,
        frequencies=step.frequencies,
        imaginary=step.imaginary,
        scan=step.scan,
        points=step.points,
        frames=[
            StepFrameOut(
                geometry=[[e, round(x, 6), round(y, 6), round(z, 6)] for e, x, y, z in f.rows],
                energy=f.energy,
                point=f.point,
                converged=f.converged,
                stage=f.stage,
                criteria=[
                    CriterionOut(
                        name=c.name,
                        value=c.value,
                        threshold=c.threshold,
                        converged=c.converged,
                        unit=c.unit,
                    )
                    for c in f.criteria
                ],
            )
            for f in step.frames
        ],
        modes=ModesOut(
            xyz=step.modes.xyz,
            frequencies=step.modes.frequencies,
            order=step.modes.order,
            modes=step.modes.modes,
        )
        if step.modes
        else None,
        missing=step.missing,
    )


async def _fetch(item: _Open) -> None:
    """A server's file copied (again) into a new temporary folder."""
    server = _saved(item.server_id or "")
    folder, _ = await _copy_down(server, [item.path])
    item.discard_copy()
    item.copy = folder


async def _read(item: _Open) -> InspectionOut:
    if item.server_id is not None:
        await _fetch(item)
    local = item.local
    try:
        found = await asyncio.to_thread(inspection.read_path, local, posixpath.basename(item.path))
    except inspection.InspectFailed as exc:
        raise HTTPException(422, str(exc)) from exc
    server = servers.find(item.server_id) if item.server_id else None
    return InspectionOut(
        id=item.id,
        name=local.name,
        path=servers.origin_path(server, item.path) if server else str(local),
        server=item.server_id,
        server_name=server.name if server else None,
        size=local.stat().st_size,
        read_at=time.time(),
        program=found.program,
        version=found.version,
        warnings=found.warnings,
        steps=[_step_out(s) for s in found.steps],
    )


def _found(request: Request, inspection_id: str) -> _Open:
    item = inspections(request.app).open.get(inspection_id)
    if item is None:
        raise HTTPException(404, "This Inspect window has been closed; open the file again.")
    return item


@router.post("")
async def open_inspection(body: InspectIn, request: Request) -> InspectionOut:
    """Read a file for the Inspect window; nothing is written to the investigation."""
    _check(request)
    if body.server is not None:
        _saved(body.server)
        item = _Open(new_id(), body.path, body.server)
    else:
        path = Path(body.path).expanduser()
        if not path.is_file():
            raise HTTPException(422, f"{path} is not a file")
        item = _Open(new_id(), str(path.resolve()), None)
    try:
        out = await _read(item)
    except BaseException:
        item.discard_copy()
        raise
    inspections(request.app).add(item)
    return out


@router.post("/{inspection_id}/refresh")
async def refresh_inspection(inspection_id: str, request: Request) -> InspectionOut:
    """Read the file again (copied again from the server), for a job still running."""
    _check(request)
    return await _read(_found(request, inspection_id))


@router.delete("/{inspection_id}", status_code=204)
def close_inspection(inspection_id: str, request: Request) -> None:
    """The window closed: its copy of a server's file is deleted."""
    _check(request)
    inspections(request.app).close(inspection_id)


@router.post("/{inspection_id}/import")
async def import_inspected(inspection_id: str, body: ImportIn, request: Request):
    """Stage the file read last, as the import dialog does, and return its preview. A server's
    file keeps the server as origin device and `<server>:/path` as origin path (D122e)."""
    _check(request)
    investigation = _investigation(request)
    item = _found(request, inspection_id)
    server = _saved(item.server_id) if item.server_id else None

    def stage_and_plan():
        local = item.local
        try:
            data = local.read_bytes()
        except OSError as exc:
            raise HTTPException(422, f"Cannot read {local}") from exc
        try:
            if server is not None:
                staged = _staging(request).add(
                    data,
                    local.name,
                    servers.origin_path(server, item.path),
                    beside=item.copy,
                    device=server.name,
                )
            else:
                staged = _staging(request).add(data, local.name, str(local))
                app_settings.remember_import_folder(local.parent)  # D78
            options = imports.ImportOptions(target_node_id=body.node_id)
            with investigation.sessions.begin() as session:
                return imports.plan(session, staged, options).as_dict()
        except imports.ImportFailed as exc:
            raise HTTPException(422, str(exc)) from exc

    return await asyncio.to_thread(stage_and_plan)
