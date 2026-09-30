"""Import of calculation output files (FR-IMP-01…13; FR-FILE-01…04; WF-03, WF-04, WF-05).
Gaussian, ORCA and xTB outputs become calculations on nodes; a CREST conformer ensemble becomes
a group node with one member node per kept conformer (D46).

An import happens in two stages. `stage` parses the file into a temporary folder outside the
investigation, and `plan` works out what would be written; the preview shows that plan and
nothing is stored yet, so cancelling leaves no trace (FR-IMP-05, T-IMP-07). `commit` copies
the file into the investigation and writes every record in the caller's transaction.

Which steps go where (05 §3.1, D25, D52, ID-6, ID-7):
- The node geometry is the last geometry of the chosen step, by default the last step.
- A step is attached to a node when its final geometry matches that node's geometry
  (same atoms in the same order, RMSD after alignment within the geometry tolerance).
- Onto a node that already has calculations, steps are compared with that node's geometry.
  If the chosen step's geometry differs, a derived node is offered for the steps that match
  the new geometry.
- Other steps (e.g. single points on the start geometry before an optimization) are listed
  in the preview only.
- A file with no coordinates at all (xTB single points and frequency jobs) can only be
  imported onto a node; its steps are taken to be at that node's geometry (A15).
"""

import hashlib
import os
import re
import shutil
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path, PurePath
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from chembook3d import settings as app_settings
from chembook3d import xyz
from chembook3d.investigation import FILES_DIR
from chembook3d.models import (
    Calculation,
    CalculationResult,
    CalculationType,
    GroupNode,
    Node,
    NodeKind,
    Role,
    SourceFile,
    Status,
    new_id,
)
from chembook3d.parsers import crest, gaussian, orca, xtb
from chembook3d.parsers.common import ParsedFile, ParsedStep
from chembook3d.services import geometry, history, levels
from chembook3d.services import groups as group_service
from chembook3d.services import nodes as node_service
from chembook3d.services.warnings import OPTIMIZATION_INCOMPLETE

OPTIMIZATION_TYPES = levels.OPTIMIZATION_TYPES


class ImportFailed(ValueError):
    pass


class ImportNotFound(LookupError):
    pass


class ImportBlocked(ValueError):
    def __init__(self, blockers: list[str]):
        self.blockers = blockers
        super().__init__("; ".join(blockers))


# ---------- staging ----------


def read_file(text: str, name: str = "") -> ParsedFile | crest.Ensemble:
    """Recognise the program from the content, not the file name."""
    parsed: ParsedFile | crest.Ensemble
    if gaussian.looks_like_gaussian(text):
        parsed = gaussian.parse(text)
    elif orca.looks_like_orca(text):
        parsed = orca.parse(text)
    elif xtb.looks_like_xtb(text):
        parsed = xtb.parse(text)
    elif crest.looks_like_ensemble(text):
        try:
            parsed = crest.parse(text)
        except crest.NotCrestEnsemble as exc:
            raise ImportFailed(
                f"{name or 'The file'} is not a readable CREST ensemble: {exc}"
            ) from exc
        if not parsed.conformers:
            raise ImportFailed(f"No conformers were found in {name}")
        return parsed
    else:
        raise ImportFailed(
            f"{name or 'The file'} is not a Gaussian, ORCA or xTB output file or a CREST "
            "conformer ensemble."
        )
    if not parsed.steps:
        raise ImportFailed(f"No job steps were found in {name}")
    return parsed


@dataclass
class StagedFile:
    token: str
    folder: Path  # temporary, outside the investigation
    original_name: str
    origin_path: str
    size: int
    checksum: str
    parsed: ParsedFile | crest.Ensemble

    @property
    def path(self) -> Path:
        return self.folder / "upload"


class Staging:
    """Files parsed for a preview and not yet imported, for the running app."""

    def __init__(self) -> None:
        self._files: dict[str, StagedFile] = {}

    def add(self, data: bytes, original_name: str, origin_path: str = "") -> StagedFile:
        text = data.decode("utf-8", errors="replace")
        parsed = read_file(text, original_name)
        folder = Path(tempfile.mkdtemp(prefix="chembook3d-import-"))
        (folder / "upload").write_bytes(data)
        staged = StagedFile(
            token=new_id(),
            folder=folder,
            original_name=PurePath(original_name.replace("\\", "/")).name or "output.log",
            origin_path=origin_path,
            size=len(data),
            checksum=hashlib.sha256(data).hexdigest(),
            parsed=parsed,
        )
        self._files[staged.token] = staged
        return staged

    def get(self, token: str) -> StagedFile:
        try:
            return self._files[token]
        except KeyError as exc:
            raise ImportNotFound(token) from exc

    def discard(self, token: str) -> None:
        staged = self._files.pop(token, None)
        if staged is not None:
            shutil.rmtree(staged.folder, ignore_errors=True)

    def clear(self) -> None:
        for token in list(self._files):
            self.discard(token)


# ---------- plan ----------


@dataclass
class ImportOptions:
    target_node_id: str | None = None
    step: int | None = None  # chosen step (1-based); default: the last step with a geometry
    duplicate_action: str | None = None  # "attach" or "new" when a possible duplicate exists
    duplicate_node_id: str | None = None
    create_derived: bool = True
    basis_names: dict[str, str] = field(default_factory=dict)  # definition key → name
    dispersion_names: dict[str, str] = field(default_factory=dict)
    label: str | None = None
    kind: str | None = None  # "species" makes a new node a free species (D69)
    role: str | None = None
    status: str | None = None
    origin_device: str | None = None
    origin_path: str | None = None
    original_name: str | None = None
    # Where a new node goes on the canvas, e.g. where the file was dropped (WF-04).
    pos_x: float | None = None
    pos_y: float | None = None
    # CREST ensembles (FR-IMP-10): how many of the lowest conformers to keep, or exactly which
    # (file positions); the method that made the energies; charge and multiplicity (A16).
    conformer_count: int | None = None
    conformers: list[int] | None = None
    method: str | None = None
    charge: int | None = None
    multiplicity: int | None = None


@dataclass
class StepPlan:
    index: int
    type: str
    title: str | None
    route: str | None
    level: dict[str, str] | None
    level_label: str
    geometry_level_label: str | None
    termination: str
    optimization_converged: bool | None
    energy: float | None
    charge: int | None
    multiplicity: int | None
    atom_count: int
    geometry_count: int
    frequency_count: int
    imaginary_count: int | None
    lowest_frequencies: list[float]
    thermo: dict[str, Any]
    rmsd_to_node: float | None  # Å, to the geometry of the node this step is compared with
    assignment: str  # node | derived | preview
    missing: list[str]


@dataclass
class NameRequest:
    kind: str  # basis | dispersion
    key: str
    description: str
    elements: list[str]
    title: str | None
    name: str | None  # the name given in the options or recognised from earlier imports
    recognised: bool


@dataclass
class Plan:
    token: str
    program: str
    program_version: str | None
    original_name: str
    size: int
    checksum: str
    mode: str  # new | planned | onto
    target_node_id: str | None
    target_label: str | None
    chosen_step: int | None
    steps: list[StepPlan]
    xyz: str | None
    derived_offered: bool
    duplicates: list[dict[str, Any]]
    already_imported: list[dict[str, Any]]
    names: list[NameRequest]
    warnings: list[dict[str, Any]]
    suggested: dict[str, Any]
    origin: dict[str, str]
    blockers: list[str]
    kind: str = "steps"  # steps | ensemble
    ensemble: dict[str, Any] | None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _rows(atoms: list[xyz.Atom] | None) -> list[list[Any]] | None:
    return [[a.element, a.x, a.y, a.z] for a in atoms] if atoms else None


@dataclass
class _Resolved:
    """A step's level with custom names looked up (or still missing)."""

    fields: dict[str, str] | None
    names: list[NameRequest]
    # Like `fields`, but an unnamed custom basis or dispersion is stood in for by its
    # definition key, so levels can be compared before the user names them.
    identity: dict[str, str] | None = None


def _resolve_level(
    session: Session, program: str, step: ParsedStep, options: ImportOptions
) -> _Resolved:
    route = step.route
    if route is None:
        return _Resolved(None, [])
    if program != gaussian.PROGRAM:
        # ORCA and xTB: the parser already gives the level as it counts (A12 for ORCA).
        fields = {
            "program": program,
            "method": route.method or "",
            "basis": step.resolved_basis or "",
            "dispersion": route.dispersion or "",
            "solvation_model": route.solvation_model or "",
            "solvent": route.solvent or "",
        }
        return _Resolved(fields, [], dict(fields))
    names: list[NameRequest] = []
    stand_ins: dict[str, str] = {}
    basis = step.resolved_basis or ""
    if basis in ("GEN", "GENECP") and step.resolved_basis_definition:
        definition = step.resolved_basis_definition
        prints = gaussian.basis_fingerprints(definition)
        key = levels.definition_key(prints)
        known = levels.find_basis(session, prints)
        name = known.name if known else (options.basis_names.get(key) or "").strip() or None
        names.append(
            NameRequest(
                kind="basis",
                key=key,
                description=f"{basis} basis set defined in the file",
                elements=sorted(definition),
                title=step.title,
                name=name,
                recognised=known is not None,
            )
        )
        basis = name or ""
        stand_ins["basis"] = name or f"?{key}"
    dispersion = route.dispersion or ""
    iops = gaussian.dispersion_iops(route.iops)
    if iops:
        key = levels.definition_key({"base": dispersion, "iops": iops})
        known = levels.find_dispersion(session, dispersion, iops)
        name = known.name if known else (options.dispersion_names.get(key) or "").strip() or None
        written = ", ".join(f"{k}={v}" for k, v in iops.items())
        names.append(
            NameRequest(
                kind="dispersion",
                key=key,
                description=f"{dispersion or 'dispersion'} modified by IOp({written})",
                elements=[],
                title=step.title,
                name=name,
                recognised=known is not None,
            )
        )
        dispersion = name or ""
        stand_ins["dispersion"] = name or f"?{key}"
    fields = {
        "program": gaussian.PROGRAM,
        "method": levels.canonical_method(route.method or "", step.multiplicity),
        "basis": basis,
        "dispersion": dispersion,
        "solvation_model": route.solvation_model or "",
        "solvent": route.solvent or "",
    }
    return _Resolved(fields, names, {**fields, **stand_ins})


def _level_label(fields: dict[str, str] | None, step: ParsedStep) -> str:
    if fields is None:
        return "unknown level (no input keywords)"
    shown = dict(fields)
    if not shown["basis"] and levels.needs_basis(shown["program"], shown["method"]):
        shown["basis"] = step.resolved_basis or "?"
        if shown["basis"] in ("GEN", "GENECP"):
            shown["basis"] += " (unnamed)"
    if not shown["dispersion"] and step.route and gaussian.dispersion_iops(step.route.iops):
        shown["dispersion"] = f"{step.route.dispersion or ''}+IOp (unnamed)"
    level = levels.LevelOfTheory(**shown)
    return levels.label(level) or ""


def _duplicates(
    session: Session,
    rows: list[list[Any]],
    charge: int | None,
    multiplicity: int | None,
    exclude: set[str],
    tolerance: float,
) -> list[dict[str, Any]]:
    """FR-IMP-09: nodes with the same atoms, charge and multiplicity within the tolerance."""
    found = []
    for node in node_service.list_nodes(session):
        if node.id in exclude or not node.geometry:
            continue
        if node.charge is not None and charge is not None and node.charge != charge:
            continue
        if (
            node.multiplicity is not None
            and multiplicity is not None
            and node.multiplicity != multiplicity
        ):
            continue
        rmsd = geometry.aligned_rmsd(rows, node.geometry)
        if rmsd is not None and rmsd <= tolerance:
            found.append({"node_id": node.id, "label": node.label, "rmsd": rmsd})
    return sorted(found, key=lambda d: d["rmsd"])


def _already_imported(session: Session, checksum: str) -> list[dict[str, Any]]:
    """T-ID-06: the same file content imported before."""
    found = []
    query = select(SourceFile).where(SourceFile.checksum == checksum)
    for source in session.scalars(query):
        calculations = session.scalars(
            select(Calculation).where(Calculation.source_file_id == source.id)
        ).all()
        found.append(
            {
                "original_name": source.original_name,
                "imported_at": source.imported_at.isoformat(),
                "nodes": sorted({c.node.label or "(no label)" for c in calculations}),
            }
        )
    return found


def _suggested_role(written: list[ParsedStep]) -> str:
    for step in reversed(written):
        if step.job_type == CalculationType.TS_OPTIMIZATION:
            return Role.TRANSITION_STATE
        if step.job_type == CalculationType.OPTIMIZATION:
            return Role.MINIMUM
    for step in reversed(written):
        if step.frequencies:
            return Role.TRANSITION_STATE if step.imaginary_count == 1 else Role.MINIMUM
    return Role.UNSPECIFIED


def _finished(step: ParsedStep) -> bool:
    return step.termination == "normal" and step.optimization_converged is not False


def plan(
    session: Session, staged: StagedFile, options: ImportOptions, store_names: bool = False
) -> Plan:
    settings = app_settings.load()
    if isinstance(staged.parsed, crest.Ensemble):
        return _plan_ensemble(session, staged, staged.parsed, options, settings)
    steps = staged.parsed.steps
    blockers: list[str] = []
    warnings: list[dict[str, Any]] = []

    target: Node | None = None
    if options.target_node_id:
        target = session.get(Node, options.target_node_id)
        if target is None:
            raise ImportFailed("The node to import onto no longer exists")

    with_geometry = [s for s in steps if s.final_geometry]
    # A15: a file that prints no coordinates is taken to be at the geometry of the node it is
    # imported onto.
    no_coordinates = not with_geometry and target is not None and bool(target.geometry)
    chosen: ParsedStep | None = None
    if options.step is not None:
        chosen = next((s for s in with_geometry if s.index == options.step), None)
        if chosen is None:
            blockers.append(f"Step {options.step} has no geometry to use")
    elif with_geometry:
        chosen = with_geometry[-1]
    if not with_geometry and not no_coordinates:
        blockers.append(
            "No geometry was found in the file. If the program does not print one (xTB single "
            "points and frequency jobs), import the file onto the node it was run on"
        )
    if no_coordinates:
        warnings.append(
            {
                "code": "NO-COORD",
                "message": (
                    "The file prints no coordinates. Its results are attached to "
                    f"'{target.label}' and taken to be at that node's geometry."
                ),
            }
        )
    chosen_rows = _rows(chosen.final_geometry) if chosen else None

    duplicates: list[dict[str, Any]] = []
    if target is None and chosen_rows:
        duplicates = _duplicates(
            session,
            chosen_rows,
            chosen.charge,
            chosen.multiplicity,
            set(),
            settings.duplicate_tolerance,
        )
        if duplicates:
            if options.duplicate_action == "attach":
                node_id = options.duplicate_node_id or duplicates[0]["node_id"]
                target = session.get(Node, node_id)
            elif options.duplicate_action != "new":
                blockers.append(
                    "This geometry is a possible duplicate: attach it to the existing node "
                    "or create a new node"
                )

    if target is None:
        mode, node_rows = "new", chosen_rows
    elif no_coordinates:
        mode, node_rows = "onto", target.geometry
    elif not target.calculations:
        mode, node_rows = "planned", chosen_rows  # ID-6: the guess is replaced
    else:
        mode, node_rows = "onto", target.geometry

    tolerance = settings.geometry_tolerance
    derived_offered = bool(
        mode == "onto" and chosen_rows and not geometry.matches(chosen_rows, node_rows, tolerance)
    )
    if derived_offered:
        warnings.append(
            {
                "code": "W-GEOM",
                "message": (
                    f"The geometry of step {chosen.index} differs from the node "
                    f"'{target.label}'. Its results cannot be attached to that node"
                    + (
                        "; they go to a new derived node."
                        if options.create_derived
                        else "; no derived node will be created."
                    )
                ),
            }
        )

    step_plans: list[StepPlan] = []
    resolved: dict[int, _Resolved] = {}
    for step in steps:
        rows = _rows(step.final_geometry)
        if rows and geometry.matches(rows, node_rows, tolerance):
            assignment = "node"
        elif no_coordinates and not rows:
            assignment = "node"
        elif (
            derived_offered
            and options.create_derived
            and rows
            and geometry.matches(rows, chosen_rows, tolerance)
        ):
            assignment = "derived"
        else:
            assignment = "preview"
        resolved[step.index] = _resolve_level(session, staged.parsed.program, step, options)
        step_plans.append(
            StepPlan(
                index=step.index,
                type=step.job_type,
                title=step.title,
                route=step.route.text if step.route else None,
                level=resolved[step.index].fields,
                level_label=_level_label(resolved[step.index].fields, step),
                geometry_level_label=None,
                termination=step.termination,
                optimization_converged=step.optimization_converged,
                energy=step.scf_energy,
                charge=step.charge,
                multiplicity=step.multiplicity,
                atom_count=len(step.final_geometry or []),
                geometry_count=len(step.geometries),
                frequency_count=len(step.frequencies),
                imaginary_count=step.imaginary_count if step.frequencies else None,
                lowest_frequencies=step.frequencies[:6],
                thermo={k: v for k, v in step.thermo.items() if not isinstance(v, list)},
                rmsd_to_node=geometry.aligned_rmsd(rows, node_rows) if rows and node_rows else None,
                assignment=assignment,
                missing=step.missing,
            )
        )

    written = [s for s, p in zip(steps, step_plans, strict=True) if p.assignment != "preview"]
    if not written and not blockers:
        blockers.append("Nothing would be imported: no step matches the node's geometry")

    # Names for custom basis sets and dispersions, asked once per definition (FR-CALC-03, D59).
    names: dict[str, NameRequest] = {}
    for step in written:
        for request in resolved[step.index].names:
            names.setdefault(request.key, request)
    for request in names.values():
        if not request.name:
            what = "basis set" if request.kind == "basis" else "dispersion"
            blockers.append(f"Give the custom {what} a name")
        elif not request.recognised:
            try:
                if request.kind == "basis":
                    step = next(
                        s
                        for s in written
                        if any(r.key == request.key for r in resolved[s.index].names)
                    )
                    definition = step.resolved_basis_definition or {}
                    prints = gaussian.basis_fingerprints(definition)
                    if store_names:
                        levels.register_basis(session, request.name, definition, prints)
                    else:
                        levels.check_basis_name(session, request.name, prints)
                else:
                    route = next(
                        s.route
                        for s in written
                        if any(r.key == request.key for r in resolved[s.index].names)
                    )
                    iops = gaussian.dispersion_iops(route.iops)
                    if store_names:
                        levels.register_dispersion(
                            session, request.name, route.dispersion or "", iops
                        )
                    else:
                        levels.check_dispersion_name(
                            session, request.name, route.dispersion or "", iops
                        )
            except levels.NameConflict as exc:
                blockers.append(str(exc))

    # Geometry level of each destination, for composite levels and the freq check.
    existing_geometry_level = levels.node_geometry_level(target) if mode == "onto" else None
    for destination in ("node", "derived"):
        geometry_level = existing_geometry_level if destination == "node" else None
        geometry_label = levels.label(geometry_level)
        geometry_identity = levels.fields_of(geometry_level)
        for step, step_plan in zip(steps, step_plans, strict=True):
            if step_plan.assignment != destination:
                continue
            identity = resolved[step.index].identity
            if step.job_type in OPTIMIZATION_TYPES:
                geometry_identity, geometry_label = identity, step_plan.level_label
            elif geometry_identity is None or geometry_identity == identity:
                continue
            elif step.job_type == CalculationType.SINGLE_POINT:
                step_plan.geometry_level_label = geometry_label
            elif step.job_type == CalculationType.FREQUENCY:
                warnings.append(
                    {
                        "code": "FREQ-LEVEL",
                        "message": (
                            f"Step {step.index} is a frequency job at {step_plan.level_label}, "
                            f"but the geometry was optimized at {geometry_label}. G at the "
                            "geometry level uses a frequency job at that level (EN-4)."
                        ),
                    }
                )

    for step, step_plan in zip(steps, step_plans, strict=True):
        if step_plan.assignment == "preview":
            continue
        if step.termination != "normal":
            warnings.append(
                {"code": "W-TERM", "message": f"Step {step.index} did not terminate normally."}
            )
        if step.optimization_converged is False:
            warnings.append(
                {
                    "code": "W-OPT-INC",
                    "message": (
                        f"The optimization in step {step.index} did not finish. Its last "
                        f"geometry is used and the node is tagged {OPTIMIZATION_INCOMPLETE}."
                    ),
                }
            )
        if step.missing:
            warnings.append(
                {
                    "code": "W-PARSE",
                    "message": f"Step {step.index}: not read from the file: "
                    + ", ".join(step.missing),
                }
            )
        if (
            mode == "onto"
            and step_plan.assignment == "node"
            and target.charge is not None
            and step.charge is not None
            and (target.charge, target.multiplicity) != (step.charge, step.multiplicity)
        ):
            warnings.append(
                {
                    "code": "W-CHG",
                    "message": (
                        f"Step {step.index} has charge {step.charge} and multiplicity "
                        f"{step.multiplicity}; the node has {target.charge} and "
                        f"{target.multiplicity}."
                    ),
                }
            )

    # A node created or re-shaped by this import may duplicate another one (W-DUP).
    if chosen_rows and mode in ("planned", "onto") and (mode == "planned" or derived_offered):
        exclude = {target.id} if target else set()
        for duplicate in _duplicates(
            session,
            chosen_rows,
            chosen.charge,
            chosen.multiplicity,
            exclude,
            settings.duplicate_tolerance,
        ):
            warnings.append(
                {
                    "code": "W-DUP",
                    "message": (
                        f"Possible duplicate of '{duplicate['label']}' "
                        f"(RMSD {duplicate['rmsd']:.3f} Å)."
                    ),
                }
            )
    if mode == "new" and duplicates:
        for duplicate in duplicates:
            warnings.append(
                {
                    "code": "W-DUP",
                    "message": (
                        f"Possible duplicate of '{duplicate['label']}' "
                        f"(RMSD {duplicate['rmsd']:.3f} Å)."
                    ),
                }
            )

    stem = PurePath(staged.original_name).stem
    role = _suggested_role(written)
    if mode == "planned" and target.role != Role.UNSPECIFIED:
        role = target.role
    status = Status.DONE if written and all(_finished(s) for s in written) else Status.FAILED
    suggested = {
        "label": target.label if mode == "planned" and target.label else stem,
        "role": role,
        "status": status,
    }
    origin = {
        "device": options.origin_device
        if options.origin_device is not None
        else settings.last_device,
        "path": options.origin_path if options.origin_path is not None else staged.origin_path,
        "name": options.original_name or staged.original_name,
    }

    return Plan(
        token=staged.token,
        program=staged.parsed.program,
        program_version=staged.parsed.version,
        original_name=staged.original_name,
        size=staged.size,
        checksum=staged.checksum,
        mode=mode,
        target_node_id=target.id if target else None,
        target_label=target.label if target else None,
        chosen_step=chosen.index if chosen else None,
        steps=step_plans,
        xyz=xyz.format_xyz(chosen.final_geometry, comment=stem) if chosen else None,
        derived_offered=derived_offered,
        duplicates=duplicates,
        already_imported=_already_imported(session, staged.checksum),
        names=list(names.values()),
        warnings=warnings,
        suggested=suggested,
        origin=origin,
        blockers=blockers,
    )


# ---------- CREST ensembles (FR-IMP-10, WF-05, D34, D46) ----------

DEFAULT_ENSEMBLE_METHOD = "GFN2-xTB"  # CREST's default level (A16)


def _selected_conformers(
    ensemble: crest.Ensemble, options: ImportOptions, count: int
) -> tuple[list[crest.Conformer], set[int]]:
    ordered = sorted(ensemble.conformers, key=lambda c: (c.energy, c.index))
    known = {c.index for c in ordered}
    if options.conformers is not None:
        selected = {i for i in options.conformers if i in known}
    else:
        selected = {c.index for c in ordered[:count]}
    return ordered, selected


def _plan_ensemble(
    session: Session,
    staged: StagedFile,
    ensemble: crest.Ensemble,
    options: ImportOptions,
    settings: app_settings.Settings,
) -> Plan:
    count = options.conformer_count if options.conformer_count is not None else settings.crest_count
    count = max(0, count)
    ordered, selected = _selected_conformers(ensemble, options, count)
    lowest = ordered[0].energy
    blockers = [] if selected else ["Tick at least one conformer to import"]
    if options.multiplicity is not None and options.multiplicity < 1:
        blockers.append("The multiplicity must be 1 or more")
    first = next((c for c in ordered if c.index in selected), ordered[0])
    stem = PurePath(staged.original_name).stem
    method = (options.method or "").strip() or DEFAULT_ENSEMBLE_METHOD
    level = {**{key: "" for key in levels.LEVEL_FIELDS}, "program": crest.PROGRAM, "method": method}
    level_label = levels.label(levels.LevelOfTheory(**level)) or ""
    warnings: list[dict[str, Any]] = []
    if options.charge is None or options.multiplicity is None:
        warnings.append(
            {
                "code": "W-CHG",
                "message": (
                    "The ensemble does not give the charge and multiplicity; the members get "
                    "the values entered here, or none."
                ),
            }
        )
    return Plan(
        token=staged.token,
        program=crest.PROGRAM,
        program_version=None,
        original_name=staged.original_name,
        size=staged.size,
        checksum=staged.checksum,
        mode="new",
        target_node_id=None,
        target_label=None,
        chosen_step=None,
        steps=[],
        xyz=xyz.format_xyz(first.atoms, comment=stem),
        derived_offered=False,
        duplicates=[],
        already_imported=_already_imported(session, staged.checksum),
        names=[],
        warnings=warnings,
        suggested={"label": stem, "role": Role.MINIMUM, "status": Status.DONE},
        origin={
            "device": options.origin_device
            if options.origin_device is not None
            else settings.last_device,
            "path": options.origin_path if options.origin_path is not None else staged.origin_path,
            "name": options.original_name or staged.original_name,
        },
        blockers=blockers,
        kind="ensemble",
        ensemble={
            "count": count,
            "method": method,
            "level_label": level_label,
            "charge": options.charge,
            "multiplicity": options.multiplicity,
            "atom_count": ensemble.atom_count,
            "selected_count": len(selected),
            "conformers": [
                {
                    "index": c.index,
                    "energy": c.energy,
                    "relative": c.energy - lowest,  # hartree
                    "selected": c.index in selected,
                }
                for c in ordered
            ],
        },
    )


def _commit_ensemble(
    session: Session, folder: Path, staged: StagedFile, plan_: Plan, options: ImportOptions
) -> "CommitResult":
    """One group node, and one member node per kept conformer with its CREST energy as a
    conformer-search calculation. No representative is chosen (D18, FR-GRP-02)."""
    ensemble = staged.parsed
    assert isinstance(ensemble, crest.Ensemble) and plan_.ensemble is not None
    label = (options.label if options.label is not None else plan_.suggested["label"]).strip()
    status = options.status or Status.DONE
    role = options.role or Role.MINIMUM
    if status not in {s.value for s in Status}:
        raise ImportFailed(f"unknown status '{status}'")
    if role not in {r.value for r in Role}:
        raise ImportFailed(f"unknown role '{role}'")
    by_index = {c.index: c for c in ensemble.conformers}
    kept = [by_index[c["index"]] for c in plan_.ensemble["conformers"] if c["selected"]]
    level = levels.get_or_create(
        session, {"program": crest.PROGRAM, "method": plan_.ensemble["method"]}
    )

    origin = plan_.origin
    source_id = new_id()
    stored = _copy_into(folder, staged, source_id, _stored_name(origin["name"]))
    try:
        source = SourceFile(
            id=source_id,
            stored_path=stored,
            original_name=origin["name"],
            origin_device=origin["device"].strip(),
            origin_path=origin["path"].strip(),
            checksum=staged.checksum,
            size=staged.size,
        )
        session.add(source)
        group = GroupNode(label=label, pos_x=options.pos_x or 0.0, pos_y=options.pos_y or 0.0)
        session.add(group)
        session.flush()
        calculation_ids = []
        for conformer in kept:
            rows = _rows(conformer.atoms)
            node = Node(
                label=f"{label}-{conformer.index}" if label else f"conformer {conformer.index}",
                role=role,
                status=status,
                charge=options.charge,
                multiplicity=options.multiplicity,
                geometry=rows,
                tags=[],
                group_id=group.id,
            )
            session.add(node)
            session.flush()
            history.record(
                session, "node", node.id, "create", new=node_service.snapshot(node), source="import"
            )
            calculation = Calculation(
                node_id=node.id,
                type=CalculationType.CONFORMER_SEARCH,
                program=crest.PROGRAM,
                program_version="",
                level_id=level.id,
                route="",
                title="",
                step_index=conformer.index,
                step_count=len(ensemble.conformers),
                termination="normal",
                charge=options.charge,
                multiplicity=options.multiplicity,
                geometry=rows,
                parse_warnings=[],
                source_file_id=source.id,
            )
            session.add(calculation)
            session.flush()
            calculation.result = CalculationResult(energy=conformer.energy, geometry_count=1)
            history.record(
                session,
                "calculation",
                calculation.id,
                "create",
                new={
                    "node_id": node.id,
                    "type": calculation.type,
                    "level": levels.label(level),
                    "file": source.original_name,
                    "step": conformer.index,
                },
                source="import",
            )
            calculation_ids.append(calculation.id)
        session.flush()
        history.record(
            session,
            "group",
            group.id,
            "create",
            new=group_service.snapshot(session, group),
            source="import",
        )
        session.flush()
    except BaseException:
        shutil.rmtree(folder / FILES_DIR / source_id, ignore_errors=True)
        raise
    _remember_device(origin["device"])
    return CommitResult(
        node_id=None,
        derived_node_id=None,
        calculation_ids=calculation_ids,
        source_file_id=source_id,
        group_id=group.id,
    )


def _remember_device(device: str) -> None:
    if device.strip():
        settings = app_settings.load()
        settings.last_device = device.strip()
        app_settings.save(settings)


# ---------- commit ----------


@dataclass
class CommitResult:
    node_id: str | None  # the node the import was attached to or created
    derived_node_id: str | None
    calculation_ids: list[str]
    source_file_id: str
    group_id: str | None = None  # the group a CREST ensemble was imported as


_UNSAFE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def _stored_name(name: str) -> str:
    cleaned = _UNSAFE.sub("_", name).strip(" .")
    return cleaned or "output.log"


def _copy_into(folder: Path, staged: StagedFile, source_id: str, name: str) -> str:
    """Copy the file to files/<source-file-id>/<name> (D16). Returns the relative path."""
    directory = folder / FILES_DIR / source_id
    directory.mkdir(parents=True)
    target = directory / name
    partial = directory / (name + ".part")
    shutil.copyfile(staged.path, partial)
    # Windows only flushes a handle opened for writing (fsync on "rb" raises EBADF).
    with open(partial, "r+b") as handle:
        os.fsync(handle.fileno())
    os.replace(partial, target)
    return f"{FILES_DIR}/{source_id}/{name}"


def _new_node(
    session: Session,
    fields: dict[str, Any],
    rows: list[list[Any]],
    step: ParsedStep,
    derived_from: Node | None,
) -> Node:
    node = Node(
        label=fields["label"],
        # A node derived from a free species is a free species too (D69).
        kind=derived_from.kind if derived_from is not None else fields.get("kind", NodeKind.NODE),
        role=fields["role"],
        status=fields["status"],
        charge=step.charge,
        multiplicity=step.multiplicity,
        geometry=rows,
        tags=[],
        derived_from_id=derived_from.id if derived_from else None,
    )
    if derived_from is not None:
        node.pos_x = derived_from.pos_x + node_service.DERIVED_NODE_OFFSET
        node.pos_y = derived_from.pos_y + node_service.DERIVED_NODE_OFFSET
        # Like a coordinate edit (ID-5), the derived node keeps its place in the mechanism.
        node.step_id = derived_from.step_id
        node.branch_id = derived_from.branch_id
    elif fields.get("pos_x") is not None and fields.get("pos_y") is not None:
        node.pos_x, node.pos_y = fields["pos_x"], fields["pos_y"]
    session.add(node)
    session.flush()
    history.record(
        session, "node", node.id, "create", new=node_service.snapshot(node), source="import"
    )
    return node


def _set(session: Session, node: Node, field_name: str, value: Any) -> None:
    old = getattr(node, field_name)
    if old == value:
        return
    setattr(node, field_name, value)
    history.record(session, "node", node.id, "update", field_name, old, value, source="import")


def _add_tag(session: Session, node: Node, tag: str) -> None:
    if tag not in node.tags:
        _set(session, node, "tags", [*node.tags, tag])


def _node_fields(plan_: Plan, options: ImportOptions) -> dict[str, Any]:
    fields = dict(plan_.suggested)
    if options.label is not None:
        fields["label"] = options.label
    if options.role is not None:
        if options.role not in {r.value for r in Role}:
            raise ImportFailed(f"unknown role '{options.role}'")
        fields["role"] = options.role
    if options.status is not None:
        if options.status not in {s.value for s in Status}:
            raise ImportFailed(f"unknown status '{options.status}'")
        fields["status"] = options.status
    if options.kind is not None:
        if options.kind not in {k.value for k in NodeKind}:
            raise ImportFailed(f"unknown kind '{options.kind}'")
        fields["kind"] = options.kind
    fields["pos_x"], fields["pos_y"] = options.pos_x, options.pos_y
    return fields


def commit(
    session: Session, folder: Path, staged: StagedFile, options: ImportOptions
) -> CommitResult:
    plan_ = plan(session, staged, options, store_names=True)
    if plan_.blockers:
        raise ImportBlocked(plan_.blockers)
    if plan_.kind == "ensemble":
        return _commit_ensemble(session, folder, staged, plan_, options)
    fields = _node_fields(plan_, options)
    steps = {s.index: s for s in staged.parsed.steps}
    chosen = steps[plan_.chosen_step] if plan_.chosen_step is not None else None
    chosen_rows = _rows(chosen.final_geometry) if chosen else None

    origin = plan_.origin
    source_id = new_id()
    stored = _copy_into(folder, staged, source_id, _stored_name(origin["name"]))
    try:
        source = SourceFile(
            id=source_id,
            stored_path=stored,
            original_name=origin["name"],
            origin_device=origin["device"].strip(),
            origin_path=origin["path"].strip(),
            checksum=staged.checksum,
            size=staged.size,
        )
        session.add(source)

        target = session.get(Node, plan_.target_node_id) if plan_.target_node_id else None
        if plan_.mode == "new":
            node = _new_node(session, fields, chosen_rows, chosen, None)
        elif plan_.mode == "planned":
            node = target
            _set(session, node, "geometry", chosen_rows)  # ID-6: history keeps the guess
            for name in ("charge", "multiplicity"):
                _set(session, node, name, getattr(chosen, name))
            for name in ("label", "role", "status"):
                _set(session, node, name, fields[name])
        else:
            node = target
            if options.status is not None:
                _set(session, node, "status", options.status)

        derived = None
        if any(p.assignment == "derived" for p in plan_.steps):
            derived = _new_node(session, fields, chosen_rows, chosen, target)

        calculation_ids = []
        for destination, owner in (("node", node), ("derived", derived)):
            if owner is None:
                continue
            geometry_level = levels.node_geometry_level(owner) if owner is target else None
            for step_plan in plan_.steps:
                if step_plan.assignment != destination:
                    continue
                step = steps[step_plan.index]
                calculation = _create_calculation(
                    session, owner, step, step_plan, staged, source, geometry_level
                )
                calculation_ids.append(calculation.id)
                if calculation.type in OPTIMIZATION_TYPES:
                    geometry_level = calculation.level
                if step.optimization_converged is False:
                    _add_tag(session, owner, OPTIMIZATION_INCOMPLETE)
        session.flush()
    except BaseException:
        shutil.rmtree(folder / FILES_DIR / source_id, ignore_errors=True)
        raise

    _remember_device(origin["device"])
    return CommitResult(
        node_id=node.id,
        derived_node_id=derived.id if derived else None,
        calculation_ids=calculation_ids,
        source_file_id=source_id,
    )


def _create_calculation(
    session: Session,
    node: Node,
    step: ParsedStep,
    step_plan: StepPlan,
    staged: StagedFile,
    source: SourceFile,
    geometry_level: levels.LevelOfTheory | None,
) -> Calculation:
    level = levels.get_or_create(session, step_plan.level) if step_plan.level else None
    route = step.route
    parsed_level = None
    if route is not None:
        parsed_level = {
            **(step_plan.level or {}),
            "basis_written": route.basis,
            "basis_resolved": step.resolved_basis,
            "dispersion_written": route.dispersion,
            "dispersion_iops": gaussian.dispersion_iops(route.iops),
            "other_iops": {
                k: v for k, v in route.iops.items() if k not in gaussian.DISPERSION_IOPS
            },
        }
    is_single_point = step.job_type == CalculationType.SINGLE_POINT
    calculation = Calculation(
        node_id=node.id,
        type=step.job_type,
        program=staged.parsed.program,
        program_version=staged.parsed.version or "",
        level_id=level.id if level else None,
        geometry_level_id=geometry_level.id if is_single_point and geometry_level else None,
        parsed_level=parsed_level,
        route=route.text if route else "",
        title=step.title or "",
        step_index=step.index,
        step_count=len(staged.parsed.steps),
        termination=step.termination,
        charge=step.charge,
        multiplicity=step.multiplicity,
        geometry=_rows(step.final_geometry),
        parse_warnings=list(step.missing),
        source_file_id=source.id,
    )
    session.add(calculation)
    session.flush()
    thermo = step.thermo
    calculation.result = CalculationResult(
        energy=step.scf_energy,
        zpe=thermo.get("zpe"),
        e_corr=thermo.get("e_corr"),
        h_corr=thermo.get("h_corr"),
        g_corr=thermo.get("g_corr"),
        e_zpe=thermo.get("e_zpe"),
        e_thermal=thermo.get("e_thermal"),
        h=thermo.get("h"),
        g=thermo.get("g"),
        temperature=thermo.get("temperature"),
        pressure=thermo.get("pressure"),
        molecular_mass=thermo.get("molecular_mass"),
        symmetry_number=thermo.get("symmetry_number"),
        point_group=thermo.get("point_group"),
        rotational_constants=thermo.get("rotational_constants"),
        rotational_temperatures=thermo.get("rotational_temperatures"),
        frequencies=step.frequencies,
        reduced_masses=step.reduced_masses,
        normal_modes=step.normal_modes,
        imaginary_count=step.imaginary_count if step.frequencies else None,
        optimization_converged=step.optimization_converged,
        geometry_count=len(step.geometries),
        printed=step.printed,
    )
    history.record(
        session,
        "calculation",
        calculation.id,
        "create",
        new={
            "node_id": node.id,
            "type": calculation.type,
            "level": levels.composite_label(
                level, geometry_level if calculation.geometry_level_id else None
            ),
            "file": source.original_name,
            "step": step.index,
        },
        source="import",
    )
    return calculation
