"""Calculations on this computer (D213, FR-LOC): the tools (`local_tools.py`) and the jobs
(`local_jobs.py`). Installing and changing the tools are left out of Claude's tools (the
settings section does them); starting, listing, cancelling and importing jobs are offered.

Every write is refused from a web page of another origin, as for cloud jobs (`api/jobs.py`).
"""

from typing import Any, Literal

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


def gxtb_installer(request: Request) -> local_tools.Installer:
    found = getattr(request.app.state, "gxtb_installer", None)
    if found is None:
        found = request.app.state.gxtb_installer = local_tools.Installer("gxtb")
    return found


def _open_folder(request: Request):
    investigation = getattr(request.app.state, "investigation", None)
    return investigation.folder if investigation is not None else None


def _refused(exc: local_jobs.LocalJobError) -> HTTPException:
    if isinstance(exc, local_jobs.JobNotFound):
        return HTTPException(404, str(exc))
    return HTTPException(422, str(exc))


def _status(request: Request, atoms: int | None = None, model: int | None = None) -> dict[str, Any]:
    out = local_tools.status(installer(request))
    current = gxtb_installer(request).current
    out["gxtb"]["install"] = {"state": current.state, "step": current.step, "error": current.error}
    out["methods"] = [
        {
            "value": key,
            "label": label,
            "layered": key in local_jobs.LAYERED,
            "estimate": local_jobs.estimate(key, atoms, out["cores"], model) if atoms else None,
        }
        for key, label in local_jobs.methods().items()
    ]
    return out


@router.get("/tools")
def tools_status(
    request: Request, atoms: int | None = None, model: int | None = None
) -> dict[str, Any]:
    """Whether the tools are installed, their versions, an install under way, the cores and
    memory a job may use, and the methods a refine offers, each with a time estimate for a
    structure of `atoms` atoms (an ONIOM one for a DFT core and link atoms of `model`)."""
    return _status(request, atoms, model)


@router.post("/tools/install")
def install_tools(request: Request) -> dict[str, Any]:
    """Install the tools, or update them to the package list of this version of the app."""
    _local_page_only(request)
    try:
        installer(request).start(busy=local_jobs.busy(_open_folder(request)))
    except local_tools.ToolsError as exc:
        raise HTTPException(422, str(exc)) from exc
    return _status(request)


@router.post("/tools/gxtb")
def install_gxtb(request: Request) -> dict[str, Any]:
    """D216: install g-xTB (the Grimme group's pinned xtb build), optional."""
    _local_page_only(request)
    if not local_tools.status()["installed"]:
        raise HTTPException(422, "Install the local tools first")
    try:
        gxtb_installer(request).start(busy=local_jobs.busy(_open_folder(request)))
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


class ScanCoordinateIn(BaseModel):
    kind: Literal["distance", "angle", "dihedral"]
    atoms: list[int] = Field(description="2, 3 or 4 atoms, counted from 0")
    start: float = Field(description="Å for a distance, degrees for an angle or dihedral")
    end: float
    points: int | None = Field(None, description="grid only: this coordinate's points")


class ScanIn(BaseModel):
    coordinates: list[ScanCoordinateIn] = Field(
        description="1 to 4 coordinates, driven together (concerted): at point k each is at "
        "its k-th value from start to end; a grid has exactly two"
    )
    # The range is checked by local_jobs.check_scan: 2 to 100 for a scan, while a grid's points
    # are its coordinates' product (up to 400, D219), which the dialog sends here too.
    points: int | None = Field(
        None,
        description=f"2 to {local_jobs.MAX_SCAN_POINTS}; for a grid the coordinates' own count",
    )
    grid: bool = Field(
        False,
        description="D219: every pair of the two coordinates' values (gfn2 and gxtb only), "
        "each with its own points",
    )


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
    core: list[int] | None = Field(
        None,
        description="oniom only: the atoms (from 0) treated with r2SCAN-3c; default the "
        "suggested core (check_local_core)",
    )
    transition: bool = Field(
        False,
        description="D215: search for a transition state (geomeTRIC's TS mode, started from a "
        "GFN2-xTB Hessian) instead of a minimum",
    )
    reacting: list[int] | None = Field(
        None,
        description="TS search: the atoms (from 0) that react; the result says whether its "
        "imaginary mode stays on them",
    )
    scan: ScanIn | None = Field(
        None,
        description="D218: a relaxed scan instead of one optimization: the coordinates are held "
        "at each of `points` values from start to end, each point optimized from the one before; "
        "imported as a node of its own",
    )
    frequencies: bool | None = Field(
        None,
        description="D217: frequencies at the result at the job's own level, imported as a "
        "frequency calculation (gfn2 and gxtb only); default on for a TS search",
    )
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


def _check_core(core: list[int] | None, count: int, what: str = "Core") -> list[int] | None:
    if core is not None and any(a < 0 or a >= count for a in core):
        raise HTTPException(422, f"{what} atoms are counted from 0 to {count - 1}")
    return core


class CoreIn(BaseModel):
    node_id: str
    core: list[int] | None = Field(None, description="Atoms from 0; None: suggest one")
    seeds: list[int] | None = Field(
        None,
        description="Atoms from 0 to grow a suggestion from (e.g. a reaction's atoms); "
        "None: every metal atom",
    )
    charge: int | None = Field(None, description="Default: the node's charge, else 0")
    multiplicity: int | None = Field(None, ge=1, description="Default: the node's, else 1")


@router.post("/core")
def check_core(body: CoreIn, request: Request, session: DbSession) -> dict[str, Any]:
    """D214: the DFT core of an ONIOM refine, suggested (from the metals, or from seed atoms)
    or as given, with the H link atoms on the bonds it cuts, its size, what is wrong with it,
    and the time a refine with it would take. Changes nothing."""
    _investigation(request)
    node = get(session, Node, body.node_id, "Node")
    atoms = node_service.atoms_of(node)
    count = len(atoms)
    for name, given in (("core", body.core), ("seeds", body.seeds)):
        if given is not None and any(a < 0 or a >= count for a in given):
            raise HTTPException(422, f"The {name} atoms are counted from 0 to {count - 1}")
    suggested = body.core is None
    core = local_jobs.suggest_core(atoms, body.seeds) if suggested else body.core
    found = local_jobs.check_core(
        atoms,
        core or [],
        body.charge if body.charge is not None else (node.charge or 0),
        body.multiplicity if body.multiplicity is not None else (node.multiplicity or 1),
    )
    cores = local_tools.resources().cores
    return {
        "core": found.atoms,
        "links": [{"inner": a, "outer": b, "g": g} for a, b, g in found.links],
        "suggested": suggested,
        "atoms": count,
        "model_size": found.model_size,
        "warnings": found.warnings,
        "errors": found.errors,
        "estimate": local_jobs.estimate("oniom", count, cores, found.model_size),
        "mode_atoms": local_jobs.mode_atoms(node),
    }


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
            _check_core(body.core, count),
            body.transition,
            _check_core(body.reacting, count, "Reacting"),
            body.frequencies,
            body.scan.model_dump() if body.scan else None,
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
