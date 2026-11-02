"""Calculations on this computer (D213, FR-LOC): the tools (`local_tools.py`) and the jobs
(`local_jobs.py`). Installing and changing the tools are left out of Claude's tools (the
settings section does them); starting, listing, cancelling and importing jobs are offered.

Every write is refused from a web page of another origin, as for cloud jobs (`api/jobs.py`).
"""

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from chembook3d import local_jobs, local_tools
from chembook3d.api.jobs import _local_page_only
from chembook3d.api.routes import DbSession, _investigation
from chembook3d.models import Node
from chembook3d.services import nodes as node_service
from chembook3d.services.records import get

router = APIRouter(prefix="/api/local")


def installer(request: Request) -> local_tools.Installer:
    found = getattr(request.app.state, "local_tools", None)
    if found is None:
        found = request.app.state.local_tools = local_tools.Installer()
    return found


def _open_folder(request: Request):
    investigation = getattr(request.app.state, "investigation", None)
    return investigation.folder if investigation is not None else None


def _refused(exc: local_jobs.LocalJobError) -> HTTPException:
    if isinstance(exc, local_jobs.JobNotFound):
        return HTTPException(404, str(exc))
    return HTTPException(422, str(exc))


def _status(request: Request, atoms: int | None = None) -> dict[str, Any]:
    out = local_tools.status(installer(request))
    out["methods"] = [
        {
            "value": key,
            "label": label,
            "estimate": local_jobs.estimate(key, atoms, out["cores"]) if atoms else None,
        }
        for key, label in local_jobs.methods().items()
    ]
    return out


@router.get("/tools")
def tools_status(request: Request, atoms: int | None = None) -> dict[str, Any]:
    """Whether the tools are installed, their versions, an install under way, the cores and
    memory a job may use, and the methods a refine offers, each with a time estimate for a
    structure of `atoms` atoms."""
    return _status(request, atoms)


@router.post("/tools/install")
def install_tools(request: Request) -> dict[str, Any]:
    """Install the tools, or update them to the package list of this version of the app."""
    _local_page_only(request)
    try:
        installer(request).start(busy=local_jobs.busy(_open_folder(request)))
    except local_tools.ToolsError as exc:
        raise HTTPException(422, str(exc)) from exc
    return _status(request)


class ResourcesIn(BaseModel):
    cores: int | None = Field(None, ge=1, description="None: physical cores minus one")
    memory_mb: int | None = Field(None, ge=500, description="None: half the memory, ≤16 GB")


@router.put("/tools")
def save_resources(body: ResourcesIn, request: Request) -> dict[str, Any]:
    _local_page_only(request)
    local_tools.save_resources(body.cores, body.memory_mb)
    return _status(request)


class Held(BaseModel):
    atoms: list[int] = Field(description="Atoms counted from 0")
    value: float | None = Field(None, description="Target (Å or degrees); None: current value")


class ConstraintsIn(BaseModel):
    frozen: list[int] = Field(default_factory=list, description="Frozen atoms, from 0")
    distances: list[Held] = Field(default_factory=list)
    angles: list[Held] = Field(default_factory=list)
    dihedrals: list[Held] = Field(default_factory=list)


class RefineIn(BaseModel):
    node_id: str
    method: str = Field(
        "gfn2",
        description="gfn2: GFN2-xTB (tblite), seconds to minutes; b97-3c: B97-3c (Psi4), the "
        "whole molecule at DFT quality, hours for 70 atoms; r2scan-3c: r2SCAN-3c (Psi4), "
        "a little better than B97-3c at about 1.4 times its cost",
    )
    charge: int | None = Field(None, description="Default: the node's charge, else 0")
    multiplicity: int | None = Field(None, ge=1, description="Default: the node's, else 1")
    constraints: ConstraintsIn | None = None
    name: str | None = None


def _check_atoms(constraints: ConstraintsIn | None, count: int) -> dict[str, Any] | None:
    if constraints is None:
        return None
    sizes = {"distances": 2, "angles": 3, "dihedrals": 4}
    out: dict[str, Any] = {"frozen": sorted(set(constraints.frozen))}
    for key, size in sizes.items():
        out[key] = []
        for item in getattr(constraints, key):
            if len(item.atoms) != size or len(set(item.atoms)) != size:
                raise HTTPException(422, f"Each of the {key} needs {size} different atoms")
            out[key].append({"atoms": item.atoms, "value": item.value})
    atoms = out["frozen"] + [a for key in sizes for item in out[key] for a in item["atoms"]]
    if any(a < 0 or a >= count for a in atoms):
        raise HTTPException(422, f"Atoms are counted from 0 to {count - 1}")
    if not atoms:
        return None
    return out


@router.post("/jobs")
def start_refine(body: RefineIn, request: Request, session: DbSession) -> dict[str, Any]:
    """Start a geometry refinement of a node on this computer; the result is imported onto the
    node when it finishes."""
    _local_page_only(request)
    folder = _investigation(request).folder
    node = get(session, Node, body.node_id, "Node")
    count = len(node_service.atoms_of(node))
    try:
        return local_jobs.create_job(
            folder,
            node,
            body.method,
            body.charge if body.charge is not None else (node.charge or 0),
            body.multiplicity if body.multiplicity is not None else (node.multiplicity or 1),
            _check_atoms(body.constraints, count),
            body.name,
        )
    except local_jobs.LocalJobError as exc:
        raise _refused(exc) from exc


@router.get("/jobs")
def list_jobs(request: Request, session: DbSession) -> list[dict[str, Any]]:
    """The investigation's local jobs, newest first, with where each stands."""
    folder = _investigation(request).folder
    jobs = local_jobs.list_jobs(folder)
    for job in jobs:
        if job.get("imported"):
            job["imported"]["present"] = session.get(Node, job["imported"]["node_id"]) is not None
    return jobs


@router.get("/jobs/{job_id}")
def job_status(job_id: str, request: Request) -> dict[str, Any]:
    try:
        return local_jobs.job_status(_investigation(request).folder, job_id)
    except local_jobs.LocalJobError as exc:
        raise _refused(exc) from exc


@router.post("/jobs/{job_id}/cancel")
def cancel_job(job_id: str, request: Request) -> dict[str, Any]:
    _local_page_only(request)
    try:
        return local_jobs.cancel(_investigation(request).folder, job_id)
    except local_jobs.LocalJobError as exc:
        raise _refused(exc) from exc


@router.post("/jobs/{job_id}/import")
def import_result(job_id: str, request: Request, session: DbSession) -> dict[str, Any]:
    """Import a finished job's result onto its node (the open app does this by itself)."""
    _local_page_only(request)
    try:
        return local_jobs.import_result(session, _investigation(request).folder, job_id)
    except local_jobs.LocalJobError as exc:
        raise _refused(exc) from exc
