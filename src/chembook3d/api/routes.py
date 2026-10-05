"""HTTP API used by the web interface. One investigation is open at a time (single user, D42)."""

import os
import string
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse
from pydantic import AfterValidator, BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from chembook3d import settings as app_settings
from chembook3d import sync, thermochem, units, xyz
from chembook3d.api import downloads
from chembook3d.investigation import (
    DB_NAME,
    Investigation,
    InvestigationError,
    InvestigationLocked,
    NeedsUpgrade,
    check_lock,
    create_investigation,
    open_investigation,
)
from chembook3d.models import (
    Calculation,
    CalculationType,
    CustomBasis,
    CustomDispersion,
    HistoryEntry,
    LevelOfTheory,
    Node,
    SourceFile,
)
from chembook3d.services import basis_sets, energies, history, imports, levels
from chembook3d.services import files as file_service
from chembook3d.services import nodes as node_service
from chembook3d.services import species as species_service
from chembook3d.services import transitions as transition_service
from chembook3d.services import warnings as warning_service

router = APIRouter(prefix="/api")


# ---------- schemas ----------

# Stored times are naive UTC (models.utcnow); send them with an explicit zone.
UtcDatetime = Annotated[
    datetime, AfterValidator(lambda d: d.replace(tzinfo=UTC) if d.tzinfo is None else d)
]


class CommitOut(BaseModel):
    when: str
    summary: str


class SyncOut(BaseModel):
    """Git sync state of an investigation (D71, FR-SYNC-05)."""

    state: str
    message: str
    remote: str | None = None
    local: CommitOut | None = None
    upstream: CommitOut | None = None


class InvestigationOut(BaseModel):
    name: str
    folder: str
    linked: bool = False
    sync: SyncOut | None = None  # the outcome of the pull on open, if linked


class CreateInvestigationIn(BaseModel):
    folder: str
    name: str


class OpenInvestigationIn(BaseModel):
    folder: str
    force: bool = False
    upgrade: bool = False  # the user confirmed upgrading a linked investigation (D71c)


class CloneInvestigationIn(BaseModel):
    url: str
    folder: str


class LinkIn(BaseModel):
    url: str


class ResolveIn(BaseModel):
    folder: str
    keep: str  # "this" or "github"


class WarningOut(BaseModel):
    code: str
    message: str
    calculation_id: str | None = None


class NodeOut(BaseModel):
    id: str
    label: str
    kind: str  # "node" or "species" (D69)
    role: str
    charge: int | None
    multiplicity: int | None
    status: str
    tags: list[str]
    notes: str
    formula: str | None
    atom_count: int
    xyz: str | None
    derived_from_id: str | None
    calculation_count: int
    warnings: list[WarningOut]
    step_id: str | None
    branch_id: str | None
    group_id: str | None
    origin_branch_id: str | None
    pos_x: float
    pos_y: float
    view_rotation: list[float] | None
    created_at: UtcDatetime
    updated_at: UtcDatetime


class NodeIn(BaseModel):
    label: str | None = None
    kind: str | None = None  # only when creating; PUT /nodes/{id}/kind changes it
    role: str | None = None
    charge: int | None = None
    multiplicity: int | None = None
    status: str | None = None
    tags: list[str] | None = None
    notes: str | None = None
    step_id: str | None = None
    branch_id: str | None = None
    pos_x: float | None = None
    pos_y: float | None = None
    view_rotation: list[float] | None = None
    xyz: str | None = None


class GeometryIn(BaseModel):
    xyz: str


class GeometryOut(BaseModel):
    node: NodeOut
    derived: bool


class TransitionBrief(BaseModel):
    id: str
    source_id: str
    target_id: str


class DeletePreview(BaseModel):
    node: str
    calculations: int
    # INV-7: every transition touching the node is listed and deleted with it.
    transitions: list[TransitionBrief]
    # D69: a free species is taken off the transitions it joins or leaves on.
    species_on: list[TransitionBrief] = []


class SourceFileOut(BaseModel):
    id: str
    stored_path: str
    original_name: str
    origin_device: str
    origin_path: str
    checksum: str
    size: int
    imported_at: UtcDatetime
    exists: bool


class SourceFileIn(BaseModel):
    original_name: str | None = None
    origin_device: str | None = None
    origin_path: str | None = None


class LevelOut(BaseModel):
    id: str
    label: str
    program: str
    method: str
    basis: str
    dispersion: str
    solvation_model: str
    solvent: str


class ResultOut(BaseModel):
    energy: float | None
    zpe: float | None
    e_corr: float | None
    h_corr: float | None
    g_corr: float | None
    e_zpe: float | None
    e_thermal: float | None
    h: float | None
    g: float | None
    temperature: float | None
    pressure: float | None
    molecular_mass: float | None
    symmetry_number: int | None
    point_group: str | None
    rotational_temperatures: list[float] | None
    frequencies: list[float]
    imaginary_count: int | None
    optimization_converged: bool | None
    geometry_count: int
    printed: dict[str, str]


class QuasiHarmonicOut(BaseModel):
    """FR-EN-04: the G_qh correction recomputed from this frequency step, shown next to the
    job-printed G correction. `correction` is None with the reason when it cannot be made."""

    temperature: float
    cutoff: float
    correction: float | None
    code: str | None = None
    message: str | None = None
    raised_modes: int | None = None
    imaginary_excluded: int | None = None


class CalculationOut(BaseModel):
    id: str
    node_id: str
    type: str
    program: str
    program_version: str
    level: LevelOut | None
    geometry_level: LevelOut | None
    composite_label: str
    parsed_level: dict[str, Any] | None
    level_edited: bool
    route: str
    title: str
    step_index: int | None
    step_count: int | None
    termination: str
    charge: int | None
    multiplicity: int | None
    parse_warnings: list[str]
    warnings: list[WarningOut]
    source_file: SourceFileOut | None
    result: ResultOut | None
    quasi_harmonic: QuasiHarmonicOut | None
    notes: str
    created_at: UtcDatetime


class CalculationIn(BaseModel):
    method: str | None = None
    basis: str | None = None
    dispersion: str | None = None
    solvation_model: str | None = None
    solvent: str | None = None
    geometry_level_id: str | None = None
    notes: str | None = None


class ImportFromPathIn(BaseModel):
    path: str
    node_id: str | None = None


class ImportOptionsIn(BaseModel):
    target_node_id: str | None = None
    step: int | None = None
    duplicate_action: str | None = None
    duplicate_node_id: str | None = None
    create_derived: bool = True
    basis_names: dict[str, str] = Field(default_factory=dict)
    dispersion_names: dict[str, str] = Field(default_factory=dict)
    label: str | None = None
    kind: str | None = None
    role: str | None = None
    status: str | None = None
    origin_device: str | None = None
    origin_path: str | None = None
    original_name: str | None = None
    pos_x: float | None = None
    pos_y: float | None = None
    conformer_count: int | None = None
    conformers: list[int] | None = None
    method: str | None = None
    charge: int | None = None
    multiplicity: int | None = None


class ImportCommitOut(BaseModel):
    node_id: str | None
    derived_node_id: str | None
    calculation_ids: list[str]
    group_id: str | None = None


class HistoryOut(BaseModel):
    id: int
    timestamp: UtcDatetime
    record_type: str
    record_id: str
    action: str
    field: str | None
    old_value: Any
    new_value: Any
    source: str


class SettingsOut(BaseModel):
    energy_unit: str
    energy_units: list[str]
    # hartree → unit factors (P17), so the interface converts with the backend's constants
    energy_factors: dict[str, float]
    recent: list[str]
    last_device: str
    last_import_folder: str
    geometry_tolerance: float
    duplicate_tolerance: float
    energy_decimals: dict[str, int]
    qh_temperature: float
    qh_cutoff: float
    standard_state: str
    standard_states: list[str]
    crest_count: int
    hydrogens: str
    hydrogen_modes: list[str]
    steric_colours: str


class SettingsIn(BaseModel):
    energy_unit: str | None = None
    last_device: str | None = None
    geometry_tolerance: float | None = None
    duplicate_tolerance: float | None = None
    qh_temperature: float | None = None
    qh_cutoff: float | None = None
    standard_state: str | None = None
    crest_count: int | None = None
    hydrogens: str | None = None
    steric_colours: str | None = None


class FolderEntry(BaseModel):
    name: str
    path: str
    is_investigation: bool
    is_file: bool = False
    size: int | None = None


class FolderListing(BaseModel):
    path: str
    parent: str | None
    is_investigation: bool
    entries: list[FolderEntry]
    roots: list[str]


# ---------- helpers ----------


def _investigation(request: Request) -> Investigation:
    investigation = getattr(request.app.state, "investigation", None)
    if investigation is None:
        raise HTTPException(409, "No investigation is open")
    return investigation


def session_dep(request: Request) -> Iterator[Session]:
    """One transaction per request (NFR-DATA-01): committed on success, rolled back on error."""
    investigation = _investigation(request)
    with investigation.sessions.begin() as session:
        yield session


# scope="function": the transaction commits before the answer is sent, so a request the UI makes
# right after (the canvas refresh after an import) sees the change. The default "request" scope
# commits only after the answer went out.
DbSession = Annotated[Session, Depends(session_dep, scope="function")]


def _node_out(session: Session, node: Node) -> NodeOut:
    atoms = node_service.atoms_of(node)
    return NodeOut(
        id=node.id,
        label=node.label,
        kind=node.kind,
        role=node.role,
        charge=node.charge,
        multiplicity=node.multiplicity,
        status=node.status,
        tags=list(node.tags),
        notes=node.notes,
        formula=xyz.formula(atoms) if atoms else None,
        atom_count=len(atoms),
        xyz=node_service.to_xyz(node) if atoms else None,
        derived_from_id=node.derived_from_id,
        calculation_count=node_service.calculation_count(session, node.id),
        warnings=[WarningOut(**w.as_dict()) for w in warning_service.node_warnings(node)],
        step_id=node.step_id,
        branch_id=node.branch_id,
        group_id=node.group_id,
        origin_branch_id=node.origin_branch_id,
        pos_x=node.pos_x,
        pos_y=node.pos_y,
        view_rotation=node.view_rotation,
        created_at=node.created_at,
        updated_at=node.updated_at,
    )


def _history_out(entry: HistoryEntry) -> HistoryOut:
    return HistoryOut.model_validate(entry, from_attributes=True)


def _xyz_error(exc: xyz.XyzParseError) -> HTTPException:
    return HTTPException(
        422, {"xyz_errors": [{"line": e.line, "message": e.message} for e in exc.errors]}
    )


def _staging(request: Request) -> imports.Staging:
    return request.app.state.staging


def _sync_out(status: sync.SyncStatus | None) -> SyncOut | None:
    return None if status is None else SyncOut.model_validate(status, from_attributes=True)


def _investigation_out(investigation: Investigation, status=None) -> InvestigationOut:
    return InvestigationOut(
        name=investigation.name,
        folder=str(investigation.folder),
        linked=sync.is_linked(investigation.folder),
        sync=_sync_out(status),
    )


def close_and_push(
    investigation: Investigation, timeout: float = sync.NETWORK_TIMEOUT
) -> sync.SyncStatus | None:
    """Close an investigation, then commit and push it if it is linked (FR-SYNC-05)."""
    investigation.close()
    if not sync.is_linked(investigation.folder):
        return None
    try:
        return sync.push(investigation.folder, open_db=False, timeout=timeout)
    except sync.SyncError as exc:
        return sync.SyncStatus("error", str(exc))


def _set_open(request: Request, investigation: Investigation, status=None) -> InvestigationOut:
    current = getattr(request.app.state, "investigation", None)
    if current is not None:
        close_and_push(current)
    _staging(request).clear()
    request.app.state.investigation = investigation
    app_settings.remember_recent(investigation.folder)
    return _investigation_out(investigation, status)


# ---------- investigations ----------


@router.get("/investigation", response_model=InvestigationOut | None)
def current_investigation(request: Request):
    investigation = getattr(request.app.state, "investigation", None)
    if investigation is None:
        return None
    return _investigation_out(investigation)


@router.post("/investigations", response_model=InvestigationOut)
def create(body: CreateInvestigationIn, request: Request):
    try:
        investigation = create_investigation(Path(body.folder), body.name)
    except InvestigationError as exc:
        raise HTTPException(422, str(exc)) from exc
    return _set_open(request, investigation)


def _conflict(folder: Path, status: sync.SyncStatus) -> HTTPException:
    out = _sync_out(status)
    assert out is not None
    return HTTPException(409, {"sync_conflict": {**out.model_dump(), "folder": str(folder)}})


def _open_folder(request: Request, folder: Path, force: bool, upgrade: bool) -> InvestigationOut:
    current = getattr(request.app.state, "investigation", None)
    if current is not None and folder.resolve() == current.folder.resolve():
        return _investigation_out(current)
    try:
        check_lock(folder, force)
    except InvestigationLocked as exc:
        raise HTTPException(409, {"locked": exc.lock, "message": str(exc)}) from exc
    except InvestigationError as exc:
        raise HTTPException(422, str(exc)) from exc
    status = None
    if sync.is_linked(folder):
        # FR-SYNC-04: pull first. Without a connection the investigation opens anyway.
        try:
            status = sync.pull(folder)
        except sync.SyncError as exc:
            status = sync.SyncStatus("error", str(exc))
        if status.state == "diverged":
            raise _conflict(folder, status)
    try:
        investigation = open_investigation(
            folder, force=force, allow_upgrade=upgrade or status is None
        )
    except InvestigationLocked as exc:
        raise HTTPException(409, {"locked": exc.lock, "message": str(exc)}) from exc
    except NeedsUpgrade as exc:
        raise HTTPException(
            409,
            {"needs_upgrade": {"from": exc.revision, "to": exc.head}, "message": str(exc)},
        ) from exc
    except InvestigationError as exc:
        raise HTTPException(422, str(exc)) from exc
    return _set_open(request, investigation, status)


@router.post("/investigations/open", response_model=InvestigationOut)
def open_(body: OpenInvestigationIn, request: Request):
    return _open_folder(request, Path(body.folder), body.force, body.upgrade)


@router.post("/investigations/clone", response_model=InvestigationOut)
def clone(body: CloneInvestigationIn, request: Request):
    """FR-SYNC-03: Open from GitHub."""
    folder = Path(body.folder).expanduser()
    try:
        sync.clone(body.url, folder)
    except sync.SyncError as exc:
        raise HTTPException(422, str(exc)) from exc
    return _open_folder(request, folder, force=False, upgrade=False)


@router.post("/investigations/close", response_model=SyncOut | None)
def close(request: Request):
    investigation = getattr(request.app.state, "investigation", None)
    status = None
    if investigation is not None:
        request.app.state.investigation = None
        status = close_and_push(investigation)
    _staging(request).clear()
    return _sync_out(status)


# ---------- sync (D71) ----------


@router.get("/sync", response_model=SyncOut)
def sync_status(request: Request, fetch: bool = True):
    try:
        return _sync_out(sync.status(_investigation(request).folder, fetch=fetch))
    except sync.SyncError as exc:
        return SyncOut(state="error", message=str(exc))


@router.post("/sync", response_model=SyncOut)
def sync_now(request: Request):
    """The Sync button: commit and push while the investigation stays open."""
    try:
        return _sync_out(sync.push(_investigation(request).folder, open_db=True))
    except sync.SyncError as exc:
        return SyncOut(state="error", message=str(exc))


@router.post("/sync/link", response_model=SyncOut)
def sync_link(body: LinkIn, request: Request):
    investigation = _investigation(request)
    try:
        return _sync_out(sync.link(investigation.folder, body.url, investigation.name))
    except sync.SyncError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.post("/sync/resolve", response_model=SyncOut)
def sync_resolve(body: ResolveIn, request: Request):
    """FR-SYNC-06: keep one copy. The investigation is closed first if it is open; the
    interface opens it again afterwards."""
    folder = Path(body.folder)
    current = getattr(request.app.state, "investigation", None)
    if current is not None and folder.resolve() == current.folder.resolve():
        request.app.state.investigation = None
        current.close()
        _staging(request).clear()
    try:
        return _sync_out(sync.resolve(folder, body.keep))
    except sync.SyncError as exc:
        raise HTTPException(422, str(exc)) from exc


def _is_investigation(folder: Path) -> bool:
    return (folder / DB_NAME).is_file()


def _roots() -> list[str]:
    if os.name == "nt":
        return [f"{d}:\\" for d in string.ascii_uppercase if os.path.isdir(f"{d}:\\")]
    return ["/"]


@router.get("/folders", response_model=FolderListing)
def list_folders(path: str | None = None, files: bool = False):
    """Browse local folders for the open and create dialogs, since a browser page cannot
    read local paths itself. The server listens on 127.0.0.1 only (NFR-SEC-01)."""
    folder = Path(path).expanduser() if path else Path.home()
    try:
        folder = folder.resolve()
    except OSError as exc:
        raise HTTPException(422, f"Cannot read {path}") from exc
    if not folder.is_dir():
        raise HTTPException(422, f"{folder} is not a folder")
    entries = []
    try:
        children = sorted(folder.iterdir(), key=lambda p: p.name.lower())
    except OSError:
        children = []
    for child in children:
        if child.name.startswith("."):
            continue
        try:
            if files and child.is_file():
                entries.append(
                    FolderEntry(
                        name=child.name,
                        path=str(child),
                        is_investigation=False,
                        is_file=True,
                        size=child.stat().st_size,
                    )
                )
                continue
            if not child.is_dir():
                continue
            entries.append(
                FolderEntry(
                    name=child.name, path=str(child), is_investigation=_is_investigation(child)
                )
            )
        except OSError:
            continue
    return FolderListing(
        path=str(folder),
        parent=str(folder.parent) if folder.parent != folder else None,
        is_investigation=_is_investigation(folder),
        entries=entries,
        roots=[str(Path.home()), *_roots()],
    )


# ---------- nodes ----------


@router.get("/nodes", response_model=list[NodeOut])
def list_nodes(session: DbSession):
    return [_node_out(session, n) for n in node_service.list_nodes(session)]


@router.post("/nodes", response_model=NodeOut, status_code=201)
def create_node(body: NodeIn, session: DbSession):
    fields = body.model_dump(exclude_unset=True, exclude={"xyz"})
    try:
        node = node_service.create(session, fields, xyz_text=body.xyz)
    except xyz.XyzParseError as exc:
        raise _xyz_error(exc) from exc
    except node_service.NodeError as exc:
        raise HTTPException(422, str(exc)) from exc
    return _node_out(session, node)


def _get_node(session: Session, node_id: str) -> Node:
    try:
        return node_service.get(session, node_id)
    except node_service.NodeNotFound as exc:
        raise HTTPException(404, "Node not found") from exc


@router.get("/nodes/{node_id}", response_model=NodeOut)
def get_node(node_id: str, session: DbSession):
    return _node_out(session, _get_node(session, node_id))


@router.patch("/nodes/{node_id}", response_model=NodeOut)
def update_node(node_id: str, body: NodeIn, session: DbSession):
    _get_node(session, node_id)
    changes = body.model_dump(exclude_unset=True)
    if "xyz" in changes:
        raise HTTPException(422, "Use PUT /api/nodes/{id}/geometry to change coordinates")
    try:
        node = node_service.update(session, node_id, changes)
    except node_service.NodeError as exc:
        raise HTTPException(422, str(exc)) from exc
    return _node_out(session, node)


@router.put("/nodes/{node_id}/geometry", response_model=GeometryOut)
def set_geometry(node_id: str, body: GeometryIn, session: DbSession):
    _get_node(session, node_id)
    try:
        result = node_service.set_geometry(session, node_id, body.xyz)
    except xyz.XyzParseError as exc:
        raise _xyz_error(exc) from exc
    except node_service.NodeError as exc:
        raise HTTPException(422, str(exc)) from exc
    return GeometryOut(node=_node_out(session, result.node), derived=result.derived)


@router.get("/nodes/{node_id}/xyz")
def download_xyz(node_id: str, session: DbSession):
    node = _get_node(session, node_id)
    try:
        text = node_service.to_xyz(node)
    except node_service.NodeError as exc:
        raise HTTPException(404, str(exc)) from exc
    name = downloads.file_name(node.label or "", node.id)
    return Response(
        text,
        media_type="chemical/x-xyz",
        headers={"Content-Disposition": downloads.attachment(f"{name}.xyz")},
    )


@router.get("/nodes/{node_id}/delete-preview", response_model=DeletePreview)
def delete_preview(node_id: str, session: DbSession):
    node = _get_node(session, node_id)
    edges = transition_service.touching(session, [node.id])
    carrying = [e.transition for e in species_service.usage(session, node.id)]
    return DeletePreview(
        node=node.id,
        calculations=node_service.calculation_count(session, node.id),
        transitions=[
            TransitionBrief(id=t.id, source_id=t.source_id, target_id=t.target_id) for t in edges
        ],
        species_on=[
            TransitionBrief(id=t.id, source_id=t.source_id, target_id=t.target_id) for t in carrying
        ],
    )


class KindIn(BaseModel):
    kind: str


@router.put("/nodes/{node_id}/kind", response_model=NodeOut)
def set_kind(node_id: str, body: KindIn, session: DbSession):
    """D69: turn a node into a free species, or back."""
    _get_node(session, node_id)
    return _node_out(session, species_service.set_kind(session, node_id, body.kind))


@router.delete("/nodes/{node_id}", response_model=DeletePreview)
def delete_node(node_id: str, session: DbSession):
    preview = delete_preview(node_id, session)
    node_service.delete(session, node_id)
    return preview


# ---------- history ----------


@router.get("/history", response_model=list[HistoryOut])
def get_history(session: DbSession, record_id: str | None = None, limit: int = 200):
    return [_history_out(e) for e in history.entries(session, record_id, min(limit, 1000))]


# ---------- settings ----------


@router.get("/settings", response_model=SettingsOut)
def get_settings():
    s = app_settings.load()
    return SettingsOut(
        energy_unit=s.energy_unit,
        energy_units=list(app_settings.ENERGY_UNITS),
        energy_factors=units.HARTREE_IN,
        recent=s.recent,
        last_device=s.last_device,
        last_import_folder=app_settings.import_folder(s),
        geometry_tolerance=s.geometry_tolerance,
        duplicate_tolerance=s.duplicate_tolerance,
        energy_decimals=units.DECIMALS,
        qh_temperature=s.qh_temperature,
        qh_cutoff=s.qh_cutoff,
        standard_state=s.standard_state,
        standard_states=list(app_settings.STANDARD_STATES),
        crest_count=s.crest_count,
        hydrogens=s.hydrogens,
        hydrogen_modes=list(app_settings.HYDROGEN_MODES),
        steric_colours=s.steric_colours,
    )


@router.put("/settings", response_model=SettingsOut)
def put_settings(body: SettingsIn):
    s = app_settings.load()
    if body.energy_unit is not None:
        if body.energy_unit not in app_settings.ENERGY_UNITS:
            raise HTTPException(422, f"Unknown energy unit '{body.energy_unit}'")
        s.energy_unit = body.energy_unit
    if body.last_device is not None:
        s.last_device = body.last_device.strip()
    for key in ("geometry_tolerance", "duplicate_tolerance"):
        value = getattr(body, key)
        if value is not None:
            if value <= 0:
                raise HTTPException(422, f"{key.replace('_', ' ')} must be above zero")
            setattr(s, key, value)
    if body.qh_temperature is not None:
        if body.qh_temperature <= 0:
            raise HTTPException(422, "The G_qh temperature must be above zero")
        s.qh_temperature = body.qh_temperature
    if body.qh_cutoff is not None:
        if body.qh_cutoff < 0:
            raise HTTPException(422, "The G_qh cutoff must not be negative")
        s.qh_cutoff = body.qh_cutoff
    if body.standard_state is not None:
        if body.standard_state not in app_settings.STANDARD_STATES:
            raise HTTPException(422, f"Unknown standard state '{body.standard_state}'")
        s.standard_state = body.standard_state
    if body.crest_count is not None:
        if body.crest_count < 1:
            raise HTTPException(422, "The number of CREST conformers must be at least 1")
        s.crest_count = body.crest_count
    if body.hydrogens is not None:
        if body.hydrogens not in app_settings.HYDROGEN_MODES:
            raise HTTPException(422, f"Unknown hydrogen display '{body.hydrogens}'")
        s.hydrogens = body.hydrogens
    if body.steric_colours is not None:
        if body.steric_colours not in app_settings.STERIC_COLOURS:
            raise HTTPException(422, f"Unknown steric map colours '{body.steric_colours}'")
        s.steric_colours = body.steric_colours
    app_settings.save(s)
    return get_settings()


# ---------- import (FR-IMP, FR-FILE) ----------


def _options(body: ImportOptionsIn | None) -> imports.ImportOptions:
    return imports.ImportOptions(**body.model_dump()) if body else imports.ImportOptions()


def _plan(session: Session, staged: imports.StagedFile, options: imports.ImportOptions):
    try:
        return imports.plan(session, staged, options).as_dict()
    except imports.ImportFailed as exc:
        raise HTTPException(422, str(exc)) from exc


def _stage(request: Request, data: bytes, name: str, origin_path: str):
    try:
        return _staging(request).add(data, name, origin_path)
    except imports.ImportFailed as exc:
        raise HTTPException(422, str(exc)) from exc


@router.post("/imports")
async def upload_import(request: Request, filename: str, node_id: str | None = None):
    """Stage an uploaded file (the browser cannot tell where it came from, so the origin path
    starts empty) and return the preview. Nothing is written to the investigation."""
    investigation = _investigation(request)
    data = await request.body()

    def stage_and_plan():
        staged = _stage(request, data, filename, "")
        with investigation.sessions.begin() as session:
            return _plan(session, staged, imports.ImportOptions(target_node_id=node_id))

    return await run_in_threadpool(stage_and_plan)


@router.post("/imports/from-path")
def import_from_path(body: ImportFromPathIn, request: Request, session: DbSession):
    """Stage a file chosen in the app's own file browser; its path becomes the origin path."""
    path = Path(body.path).expanduser()
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise HTTPException(422, f"Cannot read {path}") from exc
    staged = _stage(request, data, path.name, str(path.resolve()))
    app_settings.remember_import_folder(path.resolve().parent)  # D78
    return _plan(session, staged, imports.ImportOptions(target_node_id=body.node_id))


def _staged(request: Request, token: str) -> imports.StagedFile:
    try:
        return _staging(request).get(token)
    except imports.ImportNotFound as exc:
        raise HTTPException(404, "This import preview has expired; choose the file again") from exc


@router.post("/imports/{token}/preview")
def preview_import(token: str, body: ImportOptionsIn, request: Request, session: DbSession):
    return _plan(session, _staged(request, token), _options(body))


@router.post("/imports/{token}/commit", response_model=ImportCommitOut)
def commit_import(token: str, body: ImportOptionsIn, request: Request, session: DbSession):
    staged = _staged(request, token)
    try:
        result = imports.commit(session, _investigation(request).folder, staged, _options(body))
    except imports.ImportBlocked as exc:
        raise HTTPException(422, {"blockers": exc.blockers, "message": str(exc)}) from exc
    except imports.ImportFailed as exc:
        raise HTTPException(422, str(exc)) from exc
    session.flush()
    _staging(request).discard(token)
    return ImportCommitOut(
        node_id=result.node_id,
        derived_node_id=result.derived_node_id,
        calculation_ids=result.calculation_ids,
        group_id=result.group_id,
    )


@router.delete("/imports/{token}", status_code=204)
def cancel_import(token: str, request: Request):
    """FR-IMP-05, T-IMP-07: cancelling writes nothing."""
    _staging(request).discard(token)


# ---------- calculations (FR-CALC) ----------


def _level_out(level: LevelOfTheory | None) -> LevelOut | None:
    if level is None:
        return None
    return LevelOut(id=level.id, label=levels.label(level) or "", **levels.fields_of(level))


def _source_out(request: Request, source: SourceFile | None) -> SourceFileOut | None:
    if source is None:
        return None
    path = file_service.absolute_path(_investigation(request).folder, source)
    return SourceFileOut(
        id=source.id,
        stored_path=source.stored_path,
        original_name=source.original_name,
        origin_device=source.origin_device,
        origin_path=source.origin_path,
        checksum=source.checksum,
        size=source.size,
        imported_at=source.imported_at,
        exists=path.is_file(),
    )


def _quasi_harmonic_out(calculation: Calculation) -> QuasiHarmonicOut | None:
    if calculation.type != CalculationType.FREQUENCY or calculation.result is None:
        return None
    s = app_settings.load()
    out = QuasiHarmonicOut(temperature=s.qh_temperature, cutoff=s.qh_cutoff, correction=None)
    try:
        qh = energies.quasi_harmonic(calculation, s.qh_temperature, s.qh_cutoff)
    except thermochem.NotComputable as exc:
        out.code, out.message = exc.code, exc.message
        return out
    out.correction = qh.g_corr
    out.raised_modes = qh.raised_modes
    out.imaginary_excluded = qh.imaginary_excluded
    return out


def _calculation_out(request: Request, calculation: Calculation) -> CalculationOut:
    result = calculation.result
    parsed = calculation.parsed_level
    current = levels.fields_of(calculation.level)
    edited = bool(parsed and current and any(parsed.get(k, "") != v for k, v in current.items()))
    return CalculationOut(
        id=calculation.id,
        node_id=calculation.node_id,
        type=calculation.type,
        program=calculation.program,
        program_version=calculation.program_version,
        level=_level_out(calculation.level),
        geometry_level=_level_out(calculation.geometry_level),
        composite_label=levels.composite_label(calculation.level, calculation.geometry_level),
        parsed_level=parsed,
        level_edited=edited,
        route=calculation.route,
        title=calculation.title,
        step_index=calculation.step_index,
        step_count=calculation.step_count,
        termination=calculation.termination,
        charge=calculation.charge,
        multiplicity=calculation.multiplicity,
        parse_warnings=list(calculation.parse_warnings),
        warnings=[
            WarningOut(**w.as_dict())
            for w in warning_service.calculation_warnings(calculation, calculation.node)
        ],
        source_file=_source_out(request, calculation.source_file),
        result=ResultOut.model_validate(result, from_attributes=True) if result else None,
        quasi_harmonic=_quasi_harmonic_out(calculation),
        notes=calculation.notes,
        created_at=calculation.created_at,
    )


@router.get("/nodes/{node_id}/calculations", response_model=list[CalculationOut])
def node_calculations(node_id: str, request: Request, session: DbSession):
    node = _get_node(session, node_id)
    ordered = sorted(node.calculations, key=lambda c: (c.created_at, c.step_index or 0))
    return [_calculation_out(request, c) for c in ordered]


def _get_calculation(session: Session, calculation_id: str) -> Calculation:
    calculation = session.get(Calculation, calculation_id)
    if calculation is None:
        raise HTTPException(404, "Calculation not found")
    return calculation


@router.get("/calculations/{calculation_id}", response_model=CalculationOut)
def get_calculation(calculation_id: str, request: Request, session: DbSession):
    return _calculation_out(request, _get_calculation(session, calculation_id))


@router.patch("/calculations/{calculation_id}", response_model=CalculationOut)
def update_calculation(
    calculation_id: str, body: CalculationIn, request: Request, session: DbSession
):
    """FR-CALC-05: level fields and the geometry level can be edited; the parsed values stay
    in parsed_level and every change is in the history (P13)."""
    calculation = _get_calculation(session, calculation_id)
    changes = body.model_dump(exclude_unset=True)
    level_changes = {k: v for k, v in changes.items() if k in levels.LEVEL_FIELDS}
    if level_changes:
        fields = levels.fields_of(calculation.level) or {
            "program": calculation.program,
            **{k: "" for k in levels.LEVEL_FIELDS if k != "program"},
        }
        fields.update({k: (v or "").strip() for k, v in level_changes.items()})
        level = levels.get_or_create(session, fields)
        if level.id != calculation.level_id:
            history.record(
                session,
                "calculation",
                calculation.id,
                "update",
                "level",
                levels.label(calculation.level),
                levels.label(level),
            )
            calculation.level_id = level.id
    if "geometry_level_id" in changes and changes["geometry_level_id"] != (
        calculation.geometry_level_id
    ):
        new_level = None
        if changes["geometry_level_id"] is not None:
            new_level = session.get(LevelOfTheory, changes["geometry_level_id"])
            if new_level is None:
                raise HTTPException(422, "Unknown level of theory")
        history.record(
            session,
            "calculation",
            calculation.id,
            "update",
            "geometry_level",
            levels.label(calculation.geometry_level),
            levels.label(new_level),
        )
        calculation.geometry_level_id = new_level.id if new_level else None
    if (
        "notes" in changes
        and changes["notes"] is not None
        and changes["notes"] != (calculation.notes)
    ):
        history.record(
            session,
            "calculation",
            calculation.id,
            "update",
            "notes",
            calculation.notes,
            changes["notes"],
        )
        calculation.notes = changes["notes"]
    session.flush()
    session.expire(calculation)
    return _calculation_out(request, _get_calculation(session, calculation_id))


@router.get("/levels", response_model=list[LevelOut])
def list_levels(session: DbSession):
    found = sorted(levels.all_levels(session), key=lambda lv: levels.label(lv) or "")
    return [_level_out(level) for level in found]


# ---------- source files (FR-FILE) ----------


def _get_source(session: Session, source_id: str) -> SourceFile:
    source = session.get(SourceFile, source_id)
    if source is None:
        raise HTTPException(404, "File not found")
    return source


@router.patch("/source-files/{source_id}", response_model=SourceFileOut)
def update_source_file(source_id: str, body: SourceFileIn, request: Request, session: DbSession):
    """FR-FILE-02: the origin name and path are user-editable. The copy itself never changes."""
    source = _get_source(session, source_id)
    for field, value in body.model_dump(exclude_unset=True).items():
        if value is None or getattr(source, field) == value.strip():
            continue
        history.record(
            session,
            "source_file",
            source.id,
            "update",
            field,
            getattr(source, field),
            value.strip(),
        )
        setattr(source, field, value.strip())
    session.flush()
    return _source_out(request, source)


@router.get("/source-files/{source_id}/download")
def download_source_file(source_id: str, request: Request, session: DbSession):
    source = _get_source(session, source_id)
    path = file_service.absolute_path(_investigation(request).folder, source)
    if not path.is_file():
        raise HTTPException(404, "The copied file is missing from the investigation folder")
    return FileResponse(path, filename=source.original_name or path.name)


@router.post("/source-files/{source_id}/open", status_code=204)
def open_source_file(source_id: str, request: Request, session: DbSession, reveal: bool = False):
    """FR-FILE-03: open the copy with the system's default program, or show it in the file
    manager. The server only runs on 127.0.0.1, so this acts on the user's own computer."""
    source = _get_source(session, source_id)
    path = file_service.absolute_path(_investigation(request).folder, source)
    if not path.is_file():
        raise HTTPException(404, "The copied file is missing from the investigation folder")
    try:
        file_service.launch(path, reveal=reveal)
    except OSError as exc:
        raise HTTPException(500, f"Could not open the file: {exc}") from exc


@router.get("/custom-names")
def custom_names(session: DbSession):
    bases = session.scalars(select(CustomBasis).order_by(CustomBasis.name))
    dispersions = session.scalars(select(CustomDispersion).order_by(CustomDispersion.name))
    return {
        "bases": [{"name": b.name, "elements": sorted(b.definition)} for b in bases],
        "dispersions": [{"name": d.name, "base": d.base, "iops": d.iops} for d in dispersions],
    }


# ---------- saved custom basis sets: inspect and download (D94) ----------


def _get_basis(session: Session, basis_id: str) -> CustomBasis:
    basis = session.get(CustomBasis, basis_id)
    if basis is None:
        raise HTTPException(404, "Basis set not found")
    return basis


def _basis_calculations(session: Session, name: str) -> list[dict[str, Any]]:
    """Gaussian calculations whose level, or geometry level, uses this basis set's name."""
    level_ids = set(
        session.scalars(
            select(LevelOfTheory.id).where(
                LevelOfTheory.basis == name, LevelOfTheory.program == "Gaussian"
            )
        )
    )
    if not level_ids:
        return []
    rows = session.execute(
        select(Calculation, Node)
        .join(Node, Node.id == Calculation.node_id)
        .where(Calculation.level_id.in_(level_ids) | Calculation.geometry_level_id.in_(level_ids))
        .order_by(Node.seq, Calculation.created_at)
    )
    return [
        {
            "id": calculation.id,
            "type": calculation.type,
            "node_id": node.id,
            "node_label": node.label,
            "geometry_level_only": calculation.level_id not in level_ids,
        }
        for calculation, node in rows
    ]


@router.get("/custom-bases")
def list_custom_bases(session: DbSession):
    """The saved custom basis sets with their elements and how many calculations use them."""
    return [
        {
            "id": b.id,
            "name": b.name,
            "description": b.description,
            "elements": basis_sets.sort_elements(b.definition),
            "created_at": b.created_at.replace(tzinfo=UTC),
            "calculation_count": len(_basis_calculations(session, b.name)),
        }
        for b in session.scalars(select(CustomBasis).order_by(CustomBasis.name))
    ]


@router.get("/custom-bases/{basis_id}")
def get_custom_basis(basis_id: str, session: DbSession):
    """One saved basis set in full: per element the contraction scheme, the shells with
    exponents and coefficients, the ECP and its Gaussian input text, and the calculations
    that use it."""
    basis = _get_basis(session, basis_id)
    return {
        "id": basis.id,
        "name": basis.name,
        "description": basis.description,
        "created_at": basis.created_at.replace(tzinfo=UTC),
        "elements": basis_sets.describe(basis.definition),
        "calculations": _basis_calculations(session, basis.name),
    }


@router.get("/custom-bases/{basis_id}/file")
def custom_basis_file(basis_id: str, session: DbSession, elements: str | None = None):
    """The basis set as a Gaussian Gen/GenECP basis file (.gbs); `elements` (comma-separated
    symbols) limits it to those elements."""
    basis = _get_basis(session, basis_id)
    chosen = [e.strip() for e in elements.split(",") if e.strip()] if elements else None
    if chosen is not None:
        unknown = [e for e in chosen if e not in basis.definition]
        if unknown or not chosen:
            raise HTTPException(
                422, f"{basis.name} does not define {', '.join(unknown) or 'these elements'}"
            )
    text = basis_sets.gaussian_file(basis.name, basis.definition, chosen)
    name = downloads.file_name(basis.name, "basis")
    return Response(
        content=text.encode("utf-8"),
        media_type="text/plain; charset=utf-8",
        headers={"Content-Disposition": downloads.attachment(f"{name}.gbs")},
    )
