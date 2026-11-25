"""Calculations on this computer (D213, FR-LOC): the tools (`local_tools.py`) and the jobs
(`local_jobs.py`). Installing and changing the tools are left out of Claude's tools (the
settings section does them); reading their state (D222) and starting, listing, cancelling and
importing jobs are offered.

Every write is refused from a web page of another origin, as for cloud jobs (`api/jobs.py`).
"""

import time
from dataclasses import asdict
from typing import Any, Literal

import numpy as np
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from chembook3d import local_jobs, local_tools, xyz
from chembook3d.api import builds
from chembook3d.api.inspect import job_inspection
from chembook3d.api.jobs import _local_page_only
from chembook3d.api.live import AGENT, CLIENT_HEADER
from chembook3d.api.pathway import StepStructureOut, step_structure
from chembook3d.api.routes import DbSession, _investigation
from chembook3d.models import Node
from chembook3d.services import builder, thermo_settings
from chembook3d.services import follow_mode as follow_service
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


def mace_installer(request: Request) -> local_tools.Installer:
    found = getattr(request.app.state, "mace_installer", None)
    if found is None:
        found = request.app.state.mace_installer = local_tools.Installer("mace")
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
    current = mace_installer(request).current
    out["mace"]["install"] = {
        "state": current.state,
        "step": current.step,
        "error": current.error,
        "seconds": round((current.finished or time.time()) - current.started)
        if current.started
        else None,
    }
    out["methods"] = [
        {
            "value": key,
            "label": label,
            "layered": key in local_jobs.LAYERED,
            "estimate": local_jobs.estimate(key, atoms, out["cores"], model) if atoms else None,
        }
        for key, label in local_jobs.methods().items()
    ]
    # D238: the TS guess between two structures, when MACE-OMOL-0 is there
    offered = "mace-omol" in local_jobs.methods()
    out["ts_guess"] = None
    if atoms and offered:
        out["ts_guess"] = local_jobs.ts_guess_estimate("mace-omol", atoms, out["cores"])
    # D261: the levels "Fix imaginary mode" runs at (program and method as a calculation has)
    out["fix_mode_levels"] = [
        {"method": key, "program": program, "label": label}
        for key in sorted(local_jobs.FREQUENCY_METHODS)
        if key in local_jobs.methods()
        for program, label in [local_jobs.METHOD_LABELS.get(key, (key, key))]
    ]
    # D240: the single point levels, each with its estimate for `atoms`
    out["single_point_levels"] = local_jobs.sp_levels_status(atoms, out["cores"])
    return out


@router.get("/tools")
def tools_status(
    request: Request, atoms: int | None = None, model: int | None = None
) -> dict[str, Any]:
    """Whether the tools are installed, their versions, an install under way, the cores and
    memory a job may use, and the methods a refine offers, each with a time estimate for a
    structure of `atoms` atoms (an ONIOM one for a DFT core and link atoms of `model`); D240:
    the single point levels with theirs."""
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


class MaceIn(BaseModel):
    accept_licence: bool = Field(
        False, description="The user accepted MACE-OMOL-0's Academic Software License"
    )


@router.post("/tools/mace")
def install_mace(body: MaceIn, request: Request) -> dict[str, Any]:
    """D237: install MACE-OMOL-0 (its environment and the model), optional; the user accepts
    the model's licence first."""
    _local_page_only(request)
    try:
        mace_installer(request).start(
            busy=local_jobs.busy(_open_folder(request)), accept=body.accept_licence
        )
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
    start: float | None = Field(
        None,
        description="Å for a distance, degrees for an angle or dihedral; left out: the value in "
        "the node's structure now (D222)",
    )
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
        description="gfn2: GFN2-xTB (tblite), seconds to minutes; gxtb: g-xTB (D216, only when "
        "the user installed it), closer to DFT than GFN2-xTB at a few times its cost; b97-3c: "
        "B97-3c (Psi4), the whole molecule at DFT quality, hours for 70 atoms; r2scan-3c: "
        "r2SCAN-3c (Psi4), a little better than B97-3c at about 1.4 times its cost; oniom: "
        "r2SCAN-3c on a core, GFN2-xTB on the rest (D214); mace-omol: MACE-OMOL-0 (D237, only "
        "when the user installed it), a machine-learned potential that keeps pi-complexes and "
        "cycloaddition barriers xTB loses, seconds a step, for guess structures. "
        "get_local_tools lists the methods this computer offers",
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
        "frequency calculation (gfn2, gxtb and mace-omol only); default on for a TS search",
    )
    start_geometry_of: str | None = Field(
        None,
        description="D258: start from another of the node's optimized geometries: the id of "
        "its optimization, from GET /api/nodes/{id}/geometries; default the node's own "
        "coordinates",
    )
    xyz: str | None = Field(
        None,
        description="D258: start from this structure (XYZ text) instead: the node's atoms in "
        "the node's order, with its charge and multiplicity. The job stays the node's: a free "
        "species takes a level it lacks beside its geometry, otherwise the result goes to a "
        "derived node",
    )
    build_id: str | None = Field(
        None,
        description="D258: start from a build_structure answer instead (as xyz)",
    )
    replace: bool = Field(
        False,
        description="D260: when the result ends elsewhere at a level the node already has, "
        "offer to replace the node's geometry at that level (its optimizations there and what "
        "sits on them are removed) instead of making a derived node. The result then waits: "
        "the user picks in Local jobs, or you ask with replace_with_local_result, which the "
        "user confirms in the app",
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


def _scan(scan: ScanIn | None, atoms: list[Any]) -> dict[str, Any] | None:
    """The scan as `local_jobs.check_scan` takes it. D222: a coordinate without a start begins
    at its value in the node's structure, as the dialog's empty "from" field does."""
    if scan is None:
        return None
    out = scan.model_dump()
    coords = np.array([[a.x, a.y, a.z] for a in atoms])
    for coordinate in out["coordinates"]:
        picks = coordinate["atoms"]
        measurable = 2 <= len(picks) <= 4 and all(0 <= a < len(atoms) for a in picks)
        if coordinate["start"] is None and measurable:
            coordinate["start"] = builder.measure(coords, picks)
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


def _claude_limit(request: Request, folder: Any) -> int | None:
    """D223: the longest job Claude may start while it works on its own; None for the user's
    own jobs, and for Claude's while it asks first."""
    autonomy = request.app.state.live.autonomy
    if request.headers.get(CLIENT_HEADER) == AGENT and autonomy.on_for(folder):
        return autonomy.job_limit
    return None


@router.post("/jobs")
def start_refine(body: RefineIn, request: Request, session: DbSession) -> dict[str, Any]:
    """Start a geometry refinement of a node on this computer; the result is imported onto the
    node when it finishes."""
    _local_page_only(request)
    folder = _investigation(request).folder
    node = get(session, Node, body.node_id, "Node")
    try:
        start = _start(request, node, body)
        atoms = start.atoms if start else node_service.atoms_of(node)
        count = len(atoms)
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
            _scan(body.scan, atoms),
            _claude_limit(request, folder),
            start,
            body.replace,
        )
    except local_jobs.LocalJobError as exc:
        raise _refused(exc) from exc


def _start(request: Request, node: Node, body: RefineIn) -> local_jobs.Start | None:
    """D258: the structure the refine starts from, when it is not the node's coordinates."""
    given = [v for v in (body.start_geometry_of, body.xyz, body.build_id) if v is not None]
    if len(given) > 1:
        raise HTTPException(422, "Give one of start_geometry_of, xyz or build_id")
    if body.start_geometry_of is not None:
        return local_jobs.start_at(node, body.start_geometry_of)
    if body.build_id is not None:
        build = builds.get(request, body.build_id)
        for name in ("charge", "multiplicity"):
            built, own = getattr(build, name), getattr(node, name)
            if built is not None and own is not None and built != own:
                raise local_jobs.LocalJobError(
                    f'The build\'s {name} is {built}; "{node.label}" has {own}'
                )
        atoms = [xyz.Atom(e, x, y, z) for e, x, y, z in build.structure.rows]
        return local_jobs.Start(atoms, label="a build")
    if body.xyz is not None:
        try:
            atoms = xyz.parse_xyz(body.xyz)
        except xyz.XyzParseError as exc:
            raise HTTPException(422, f"The start structure is not valid XYZ: {exc}") from exc
        return local_jobs.Start(atoms, label="XYZ text")
    return None


class TsGuessIn(BaseModel):
    start_id: str = Field(description="The start: a node, or a group for its representative")
    end_id: str = Field(description="The end, joined to the start by an edge")
    start_member_id: str | None = Field(None, description="A group's member to use instead")
    end_member_id: str | None = None
    pairs: list[list[int]] | None = Field(
        None, description="[start atom, end atom] pairs (from 1) fixed in the atom match (D113)"
    )
    method: str = Field("mace-omol", description="mace-omol (MACE-OMOL-0, D237)")
    correct: bool = Field(
        False,
        description="D239: also correct the saddle along its forming bond with PBE-D3M(BJ)/"
        "def2-SVP single points (about half an hour for 70 atoms); kept as a second TS node",
    )


@router.post("/ts-guess")
def start_ts_guess(body: TsGuessIn, request: Request, session: DbSession) -> dict[str, Any]:
    """D238: a TS guess between two structures joined by an edge, on this computer: both ends
    optimized with MACE-OMOL-0, an IDPP band, Sella's saddle search from its top and a mode
    check; imported as a new TS node halfway between them when it finishes."""
    _local_page_only(request)
    folder = _investigation(request).folder
    try:
        return local_jobs.create_ts_guess(
            session,
            folder,
            body.start_id,
            body.end_id,
            body.method,
            body.pairs,
            body.start_member_id,
            body.end_member_id,
            _claude_limit(request, folder),
            body.correct,
        )
    except local_jobs.LocalJobError as exc:
        raise _refused(exc) from exc


class FixModeIn(BaseModel):
    node_id: str
    calculation_id: str = Field(
        description="The node's frequency calculation with the imaginary mode, at a local level "
        "with frequencies (GFN2-xTB, g-xTB or MACE-OMOL-0)"
    )


@router.post("/fix-mode")
def start_fix_mode(body: FixModeIn, request: Request, session: DbSession) -> dict[str, Any]:
    """D261: push a minimum off an imaginary mode: the frequency job's structure displaced both
    ways along its mode, each optimized with frequencies at the same level, the lower try
    without an imaginary mode kept. Started with Replace offered (D260)."""
    _local_page_only(request)
    folder = _investigation(request).folder
    node = get(session, Node, body.node_id, "Node")
    try:
        return local_jobs.create_fix_mode(
            folder, node, body.calculation_id, _claude_limit(request, folder)
        )
    except local_jobs.LocalJobError as exc:
        raise _refused(exc) from exc


class FollowModeIn(BaseModel):
    node_id: str = Field(description="The transition state node")
    calculation_id: str | None = Field(
        None,
        description="Its frequency calculation with the imaginary mode and stored normal modes, "
        "at any level (DFT included); default the node's at its geometry with exactly one "
        "imaginary frequency, else the one with the lowest",
    )
    method: str = Field(
        "mace-omol",
        description="mace-omol (MACE-OMOL-0, the default) or gfn2 (GFN2-xTB): what the ends "
        "relax with",
    )
    folder: str | None = Field(
        None,
        description="A folder on this computer (full path) whose outputs' final structures the "
        "ends are also matched against, as survey_folder reads them",
    )


@router.post("/follow-mode")
def start_follow_mode(body: FollowModeIn, request: Request, session: DbSession) -> dict[str, Any]:
    """D263c, A184: follow a TS's imaginary mode both ways: the frequency calculation's
    structure displaced 0.1 Å along the mode on each side, each end relaxed for at most 40
    steps. The result is information, never imported: GET /jobs/{id}/ends matches the ends."""
    _local_page_only(request)
    folder = _investigation(request).folder
    node = get(session, Node, body.node_id, "Node")
    try:
        return local_jobs.create_follow_mode(
            folder,
            node,
            body.calculation_id,
            body.method,
            body.folder,
            _claude_limit(request, folder),
            thermo_settings.settings(session).geometry_tolerance,
        )
    except local_jobs.LocalJobError as exc:
        raise _refused(exc) from exc


class SinglePointsIn(BaseModel):
    node_ids: list[str] = Field(
        description="The nodes whose structures get a single point, one job each (a group's "
        f"members listed one by one); 1 to {local_jobs.SP_MAX_NODES}"
    )
    level: str = Field(
        local_jobs.SP_DEFAULT,
        description="pbe-tzvp: PBE-D3M(BJ)/def2-TZVP (the default, about 10 min for 70 atoms "
        "on 4 cores); pbe-svp: PBE-D3M(BJ)/def2-SVP (about 4 min); r2scan-3c (lost the barrier "
        "of a Ru [2+2] step, so not for barriers there); b97-3c (about 5 min); pbe0-tzvp: "
        "PBE0-D3(BJ)/def2-TZVP, a hybrid (about 20 min). "
        "get_local_tools?atoms=N lists them with estimates as single_point_levels",
    )
    charge: int | None = Field(None, description="Default: each node's charge, else 0")
    multiplicity: int | None = Field(None, ge=1, description="Default: each node's, else 1")
    geometry_of: dict[str, str] | None = Field(
        None,
        description="D246: node id -> the id of the node's optimization whose geometry its "
        "single point is run at, from GET /api/nodes/{id}/geometries (a node can hold "
        "geometries optimized at several levels); default the node's own geometry",
    )


@router.post("/single-points")
def start_single_points(
    body: SinglePointsIn, request: Request, session: DbSession
) -> list[dict[str, Any]]:
    """D240: a single point of each node's structure as it is, on this computer with Psi4,
    one job per node, run one after another; each is imported onto its node as a single-point
    calculation whose geometry level is the node's optimization."""
    _local_page_only(request)
    folder = _investigation(request).folder
    nodes = [get(session, Node, node_id, "Node") for node_id in body.node_ids]
    try:
        return local_jobs.create_single_points(
            folder,
            nodes,
            body.level,
            body.charge,
            body.multiplicity,
            _claude_limit(request, folder),
            body.geometry_of,
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


def _job_step(request: Request, job_id: str, step: int | None):
    """The local job's steps as the Inspect window reads them (D220), and the one asked for:
    `step` as numbered there, else the first with structures."""
    found, status, _size = job_inspection(_investigation(request).folder, job_id)
    if step is None:
        chosen = next((s for s in found.steps if s.frames), None)
    else:
        chosen = next((s for s in found.steps if s.index == step), None)
        if chosen is None:
            raise HTTPException(422, f"The job has no step {step}")
    return found, status, chosen


@router.get("/jobs/{job_id}/steps")
def job_steps(job_id: str, request: Request) -> dict[str, Any]:
    """D224: how a local job is going, running or finished, as the Inspect window shows it
    (D220) but without coordinates: per step of the job its type, how it ended, the last
    energy, the frequencies once there are any, and every structure so far with its energy,
    scan point, convergence and the optimizer's criteria."""
    found, status, _chosen = _job_step(request, job_id, None)
    return {
        "job": status,
        "program": found.program,
        "warnings": found.warnings,
        "steps": [
            {
                "index": s.index,
                "job_type": s.job_type,
                "method": s.method,
                "termination": s.termination,
                "energy": s.energy,
                "optimization_converged": s.optimization_converged,
                "frequencies": s.frequencies,
                "imaginary": s.imaginary,
                "scan": s.scan,
                "points": s.points,
                "grid": s.grid,
                "frames": [
                    {
                        "frame": i,
                        "energy": f.energy,
                        "point": f.point,
                        "converged": f.converged,
                        "criteria": [asdict(c) for c in f.criteria],
                    }
                    for i, f in enumerate(s.frames)
                ],
            }
            for s in found.steps
        ],
    }


@router.get("/jobs/{job_id}/steps/{frame}", response_model=StepStructureOut)
def job_step(job_id: str, frame: int, request: Request, step: int | None = None):
    """D224: one structure of a local job so far as XYZ text, with its energy. `frame` is
    0-based as `/steps` lists them and -1 is the last; `step` picks one of the job's steps
    (left out: the first that has structures)."""
    _found, status, chosen = _job_step(request, job_id, step)
    if chosen is None or not chosen.frames:
        raise HTTPException(422, "The job has written no structure yet")
    return step_structure(chosen.frames, frame, str(status.get("node_label") or job_id))


@router.post("/jobs/{job_id}/cancel")
def cancel_job(job_id: str, request: Request) -> dict[str, Any]:
    _local_page_only(request)
    try:
        return local_jobs.cancel(_investigation(request).folder, job_id)
    except local_jobs.LocalJobError as exc:
        raise _refused(exc) from exc


class ImportJobIn(BaseModel):
    derived: bool = Field(
        False,
        description="D260: for a job waiting with a replace_offer, import onto a derived node "
        "instead (replacing is replace_with_local_result, which the user confirms)",
    )


@router.post("/jobs/{job_id}/import")
def import_result(
    job_id: str, request: Request, session: DbSession, body: ImportJobIn | None = None
) -> dict[str, Any]:
    """Import a finished job's result onto its node (the open app does this by itself)."""
    _local_page_only(request)
    try:
        return local_jobs.import_result(
            session,
            _investigation(request).folder,
            job_id,
            False if body and body.derived else None,
        )
    except local_jobs.LocalJobError as exc:
        raise _refused(exc) from exc


@router.post("/jobs/{job_id}/replace")
def replace_with_result(job_id: str, request: Request, session: DbSession) -> dict[str, Any]:
    """D260: import the result replacing the node's geometry at the job's level, after the
    user's confirm (the page's dialog, or Claude's confirm tool)."""
    _local_page_only(request)
    try:
        return local_jobs.import_result(session, _investigation(request).folder, job_id, True)
    except local_jobs.LocalJobError as exc:
        raise _refused(exc) from exc


@router.get("/jobs/{job_id}/ends")
def follow_mode_ends(job_id: str, request: Request, session: DbSession) -> dict[str, Any]:
    """D263c, A184: a mode-follow job's ends: how each relaxed, its bonds against the TS, the
    nodes (and the job's folder's outputs) it matches, and the verdict."""
    _local_page_only(request)
    try:
        return follow_service.ends(session, _investigation(request).folder, job_id)
    except local_jobs.LocalJobError as exc:
        raise _refused(exc) from exc


class EndNodeIn(BaseModel):
    label: str | None = Field(None, description="Default: '<TS label> end +' or 'end −'")
    anyway: bool = Field(
        False, description="Make it even though the end matches a node (say why to the user)"
    )


@router.post("/jobs/{job_id}/ends/{end}/node", status_code=201)
def make_node_from_end(
    job_id: str,
    end: str,
    request: Request,
    session: DbSession,
    body: EndNodeIn | None = None,
) -> dict[str, Any]:
    """D263c: a new node from an end of a mode-follow that matches nothing: coordinates only,
    a planned minimum beside the TS with its charge and multiplicity."""
    _local_page_only(request)
    body = body or EndNodeIn()
    try:
        return follow_service.make_node(
            session, _investigation(request).folder, job_id, end, body.label, body.anyway
        )
    except local_jobs.LocalJobError as exc:
        raise _refused(exc) from exc
