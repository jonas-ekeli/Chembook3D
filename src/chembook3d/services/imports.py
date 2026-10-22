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
from collections.abc import Callable
from dataclasses import asdict, dataclass, field, replace
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
from chembook3d.parsers import crest, gaussian, joined, orca, xtb
from chembook3d.parsers.common import NOT_STATIONARY, ParsedFile, ParsedStep
from chembook3d.services import geometry, history, levels, spin
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


def read_file(text: str, name: str = "", folder: Path | None = None) -> ParsedFile | crest.Ensemble:
    """Recognise the program from the content, not the file name. `folder` is where the file
    was chosen, for an xTB scan's output beside it (D112)."""
    parsed: ParsedFile | crest.Ensemble
    if joined.looks_like_joined(text):
        try:
            parsed = joined.parse(text, name)
        except joined.NotJoinedScan as exc:
            raise ImportFailed(
                f"{name or 'The file'} is not a readable joined scan: {exc}"
            ) from exc
    elif xtb.looks_like_structures(text):
        try:
            parsed = xtb.parse_structures(text, name, scan_outputs(folder) if folder else [])
        except xtb.OptimizationLog as exc:
            raise ImportFailed(str(exc)) from exc
        except xtb.NotXtbOutput as exc:
            raise ImportFailed(f"{name or 'The file'} is not a readable xTB scan: {exc}") from exc
    elif gaussian.looks_like_gaussian(text):
        parsed = gaussian.parse(text)
    elif orca.looks_like_orca(text):
        parsed = orca.parse(text, beside(folder) if folder else None)
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
            f"{name or 'The file'} is not a Gaussian, ORCA or xTB output file, an xTB scan or "
            "a CREST conformer ensemble."
        )
    if not parsed.steps:
        raise ImportFailed(f"No job steps were found in {name}")
    return parsed


def beside(folder: Path) -> Callable[[str], bytes | None]:
    """D133: reads a file in `folder` by the bare name another file gives it (an ORCA IRC's
    trajectories); a name with a folder in it, or a file too large, reads as absent."""

    def read(name: str) -> bytes | None:
        if not name or PurePath(name.replace("\\", "/")).name != name:
            return None
        path = folder / name
        try:
            if not path.is_file() or path.stat().st_size > SCAN_OUTPUT_SIZE:
                return None
            return path.read_bytes()
        except OSError:
            return None

    return read


# Files looked at for the xTB output beside an xtbscan.log (D112), and the largest read.
SCAN_OUTPUT_FILES = 200
SCAN_OUTPUT_SIZE = 50_000_000


def scan_outputs(folder: Path, wrote: Callable[[str], bool] = xtb.ran_relaxed_scan) -> list[str]:
    """The xTB outputs in `folder` (not its subfolders) that ran a relaxed scan, or `wrote`
    something else."""
    found = []
    try:
        children = sorted(folder.iterdir(), key=lambda p: p.name.lower())[:SCAN_OUTPUT_FILES]
    except OSError:
        return []
    for child in children:
        try:
            if not child.is_file() or child.stat().st_size > SCAN_OUTPUT_SIZE:
                continue
            with open(child, "rb") as handle:
                head = handle.read(20000)
            if b"\x00" in head or not xtb.looks_like_xtb(head.decode("utf-8", "replace")):
                continue
            text = child.read_bytes().decode("utf-8", errors="replace")
        except OSError:
            continue
        if wrote(text):
            found.append(text)
    return found


@dataclass
class StagedFile:
    token: str
    folder: Path  # temporary, outside the investigation
    original_name: str
    origin_path: str
    size: int
    checksum: str
    parsed: ParsedFile | crest.Ensemble
    # D122: copied from an SSH server, whose name is the origin device (not remembered as the
    # device of the next import from this computer).
    origin_device: str | None = None

    @property
    def path(self) -> Path:
        return self.folder / "upload"


class Staging:
    """Files parsed for a preview and not yet imported, for the running app."""

    def __init__(self) -> None:
        self._files: dict[str, StagedFile] = {}
        # Scanned folders awaiting their review (batch_import.Batch by token, D97); their
        # files are staged here like single files.
        self.batches: dict[str, Any] = {}

    def add(
        self,
        data: bytes,
        original_name: str,
        origin_path: str = "",
        beside: Path | None = None,
        device: str | None = None,
    ) -> StagedFile:
        """`beside` is the local folder of files next to it (the origin path's folder unless
        the file came from elsewhere, such as an SSH server, D122)."""
        text = data.decode("utf-8", errors="replace")
        if beside is None and origin_path:
            beside = Path(origin_path).parent
        parsed = read_file(text, original_name, beside)
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
            origin_device=device,
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
        self.batches.clear()
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
    # D104: the basis set for steps whose file names none (ChkBasis from another job's
    # checkpoint); the file's own basis always wins.
    missing_basis: str | None = None
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
    # A new node's place in the mechanism and its notes (a scan path's node, D114), written
    # with the node so undoing the import (D102) removes them with it.
    step_id: str | None = None
    branch_id: str | None = None
    notes: str | None = None
    # D121: the edge a scan path's node was run on, and whether only its chip there shows it.
    path_edge_id: str | None = None
    on_edge_only: bool = False
    # D131: an IRC part for the TS node `target_node_id`: "path" (the IRC itself; its file
    # says which direction) or "end" (an optimization or frequency job on the end point of the
    # path in `irc_direction`, "forward" or "reverse").
    irc_part: str | None = None
    irc_direction: str | None = None
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
    basis_given: str | None = None  # D104: given at import, the file names none


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
    kind: str = "steps"  # steps | ensemble | irc
    ensemble: dict[str, Any] | None = None
    irc: dict[str, Any] | None = None  # D131: the part and direction an IRC import adds
    # D134: an IRC file planned as an ordinary import: the TS nodes it starts from
    irc_targets: list[dict[str, Any]] | None = None
    # D100: the node's lower-level optimization this file continues from; the node's geometry
    # becomes the file's result and that optimization is kept as a pre-optimization.
    continues_from: dict[str, Any] | None = None

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
    basis_given: str | None = None  # D104


def basis_not_named(program: str, step: ParsedStep) -> bool:
    """D104: the file names no basis set for this step (ChkBasis with no earlier step in the
    file defining one), though its method needs one."""
    route = step.route
    return bool(
        route is not None
        and route.method
        and not step.resolved_basis
        and levels.needs_basis(program, route.method)
    )


def _given_basis(program: str, step: ParsedStep, options: ImportOptions) -> str | None:
    given = (options.missing_basis or "").strip()
    return given if given and basis_not_named(program, step) else None


def _resolve_level(
    session: Session, program: str, step: ParsedStep, options: ImportOptions
) -> _Resolved:
    route = step.route
    if route is None:
        return _Resolved(None, [])
    given = _given_basis(program, step, options)
    if program != gaussian.PROGRAM:
        # ORCA and xTB: the parser already gives the level as it counts (A12 for ORCA).
        fields = {
            "program": program,
            "method": route.method or "",
            "basis": step.resolved_basis or given or "",
            "dispersion": route.dispersion or "",
            "solvation_model": route.solvation_model or "",
            "solvent": route.solvent or "",
        }
        return _Resolved(fields, [], dict(fields), given)
    names: list[NameRequest] = []
    stand_ins: dict[str, str] = {}
    basis = step.resolved_basis or given or ""
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
    elif basis in ("GEN", "GENECP") and step.basis_fingerprints:
        # D137: a joined scan names its basis set by the fingerprints of its definition, which
        # an import of one of its own files registered.
        known = levels.find_basis(session, step.basis_fingerprints)
        basis = known.name if known else ""
        stand_ins["basis"] = (
            known.name if known else f"?{levels.definition_key(step.basis_fingerprints)}"
        )
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
    return _Resolved(fields, names, {**fields, **stand_ins}, given)


def _still_missing(step: ParsedStep, resolved: _Resolved) -> list[str]:
    """What the file leaves out, less a basis set given at import (D104)."""
    if resolved.basis_given:
        return [m for m in step.missing if m != "basis set"]
    return list(step.missing)


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
    """The role the file's last optimization, else its last frequency job, suggests. D157: a
    constrained one (a scan, frozen coordinates or atoms) suggests none: its structure is no
    free minimum or TS."""
    for step in reversed(written):
        if step.job_type in OPTIMIZATION_TYPES and step.constrained:
            return Role.UNSPECIFIED
        if step.job_type == CalculationType.TS_OPTIMIZATION:
            return Role.TRANSITION_STATE
        if step.job_type == CalculationType.OPTIMIZATION:
            return Role.MINIMUM
    for step in reversed(written):
        if step.frequencies:
            if step.constrained:
                return Role.UNSPECIFIED
            return Role.TRANSITION_STATE if step.imaginary_count == 1 else Role.MINIMUM
    return Role.UNSPECIFIED


def constrained_message(step: ParsedStep) -> str:
    """D157: the import preview's W-CONSTRAINED for one step."""
    what = (
        "a scan or constrained optimization"
        if step.job_type in OPTIMIZATION_TYPES
        else "a scan or constrained job"
    )
    return (
        f"Step {step.index} is {what} ({', '.join(step.constraints)}). Its structure is no "
        "free minimum or transition state, so the import suggests no role, and it never gives "
        "a geometry level to single points or frequency jobs."
    )


def _finished(step: ParsedStep) -> bool:
    return step.termination == "normal" and step.optimization_converged is not False


def ended_on(ended: list[tuple[Any, Any]], rows: list[Any] | None, tolerance: float) -> Any:
    """The value paired with the latest geometry in `ended` that `rows` matches (D100)."""
    for ended_rows, value in reversed(ended):
        if geometry.matches(ended_rows, rows, tolerance):
            return value
    return None


def continued_optimization(
    target: Node,
    steps: list[ParsedStep],
    resolved: dict[int, _Resolved],
    chosen_rows: list[Any],
    tolerance: float,
) -> dict[str, Any] | None:
    """D100: a file whose optimization starts from the geometry the node's own optimization
    ended on, at another level, continues that pre-optimization (an xTB pre-optimization
    followed by a DFT optimization). Its result becomes the node's geometry. A constrained
    optimization or scan continues nothing (D157), but a free optimization continues a
    constrained pre-optimization, such as an xTB optimization with restrained distances
    (D165)."""
    for step in steps:
        if step.job_type not in OPTIMIZATION_TYPES or not step.geometries or step.constrained:
            continue
        if not geometry.matches(_rows(step.geometries[0]), target.geometry, tolerance):
            continue
        if not geometry.matches(_rows(step.final_geometry), chosen_rows, tolerance):
            continue
        previous = levels.pre_optimization_at(target, target.geometry, tolerance)
        if previous is not None and resolved[step.index].identity != levels.fields_of(
            previous.level
        ):
            return {
                "step": step.index,
                "calculation_id": previous.id,
                "level_label": levels.label(previous.level)
                + (" with constraints" if previous.constraints else ""),
            }
    return None


def continues(
    session: Session, staged: StagedFile, target: Node, options: ImportOptions, tolerance: float
) -> bool:
    """Whether this file continues the node's pre-optimization (D100), for batch matching."""
    if isinstance(staged.parsed, crest.Ensemble) or not target.geometry:
        return False
    steps = staged.parsed.steps
    with_geometry = [s for s in steps if s.final_geometry]
    if not with_geometry:
        return False
    resolved = {s.index: _resolve_level(session, staged.parsed.program, s, options) for s in steps}
    chosen_rows = _rows(with_geometry[-1].final_geometry)
    return continued_optimization(target, steps, resolved, chosen_rows, tolerance) is not None


def _names(
    session: Session,
    written: list[ParsedStep],
    resolved: dict[int, _Resolved],
    store_names: bool,
    blockers: list[str],
) -> list[NameRequest]:
    """Names for custom basis sets and dispersions, asked once per definition (FR-CALC-03,
    D59); a missing or conflicting one is added to `blockers`."""
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
    return list(names.values())


def plan(
    session: Session, staged: StagedFile, options: ImportOptions, store_names: bool = False
) -> Plan:
    settings = app_settings.load()
    if options.irc_part is not None:
        return _plan_irc(session, staged, options, settings, store_names)
    if isinstance(staged.parsed, crest.Ensemble):
        return _plan_ensemble(session, staged, staged.parsed, options, settings)
    found = _plan_steps(session, staged, options, settings, store_names)
    if any(s.irc is not None for s in staged.parsed.steps):
        # D134: an IRC goes into the IRC of the TS it starts from, never onto the canvas.
        found.irc_targets = irc_targets(session, staged.parsed, settings.duplicate_tolerance)
        found.blockers.append(
            "This file is an IRC: it goes into the IRC of the transition state it starts from"
            + (
                ", not onto the canvas"
                if found.irc_targets
                else f". No transition state here starts where it does (within "
                f"{settings.duplicate_tolerance} Å); open IRC… on its node to add it there"
            )
        )
    return found


def irc_targets(session: Session, parsed: ParsedFile, tolerance: float) -> list[dict[str, Any]]:
    """D134: the transition states an IRC file starts from (its TS point within the duplicate
    tolerance of their structure after fitting), nearest first."""
    starts = []
    for step in parsed.steps:
        start = next((p for p in step.irc.points if p.number == 0), None) if step.irc else None
        if start is not None:
            starts.append(_rows(step.geometries[start.structure]))
    found = []
    for node in node_service.list_nodes(session):
        if node.role != Role.TRANSITION_STATE or not node.geometry:
            continue
        distances = [
            d for s in starts if (d := geometry.aligned_rmsd(s, node.geometry)) is not None
        ]
        if distances and min(distances) <= tolerance:
            found.append({"node_id": node.id, "label": node.label, "rmsd": min(distances)})
    return sorted(found, key=lambda t: t["rmsd"])


def _plan_steps(
    session: Session,
    staged: StagedFile,
    options: ImportOptions,
    settings: Any,
    store_names: bool,
) -> Plan:
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
    resolved = {s.index: _resolve_level(session, staged.parsed.program, s, options) for s in steps}
    continues_from = None
    if mode == "onto" and chosen_rows and not geometry.matches(chosen_rows, node_rows, tolerance):
        continues_from = continued_optimization(target, steps, resolved, chosen_rows, tolerance)
    if continues_from is not None:
        node_rows = chosen_rows
        warnings.append(
            {
                "code": "PRE-OPT",
                "message": (
                    f"Step {continues_from['step']} starts from the geometry of "
                    f"'{target.label}', optimized at {continues_from['level_label']}. That "
                    "optimization is kept as a pre-optimization, and the node's geometry "
                    f"becomes the result of step {continues_from['step']}."
                ),
            }
        )
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
                missing=_still_missing(step, resolved[step.index]),
                basis_given=resolved[step.index].basis_given,
            )
        )

    written = [s for s, p in zip(steps, step_plans, strict=True) if p.assignment != "preview"]
    if not written and not blockers:
        blockers.append("Nothing would be imported: no step matches the node's geometry")

    names = _names(session, written, resolved, store_names, blockers)
    for step in written:
        fields = resolved[step.index].fields
        if step.basis_fingerprints and fields is not None and not fields["basis"]:
            # D137: the definition is not in the file, so it cannot be named here.
            blockers.append(
                "The custom basis set of this joined scan is not known in this investigation: "
                "import one of the scan's own output files first, which names it"
            )

    # D100: a single point or frequency job takes the level of the optimization that ended on
    # its geometry, among the node's own and those in this file before it; never a constrained
    # one (D157).
    for destination in ("node", "derived"):
        ended: list[tuple[list[Any] | None, tuple[Any, str]]] = []
        if destination == "node" and mode == "onto":
            ended = [
                (c.geometry, (levels.fields_of(c.level), levels.label(c.level)))
                for c in levels.optimizations(target)
            ]
        for step, step_plan in zip(steps, step_plans, strict=True):
            if step_plan.assignment != destination:
                continue
            identity = resolved[step.index].identity
            rows = _rows(step.final_geometry)
            if step.job_type in OPTIMIZATION_TYPES:
                if not step.constrained:
                    ended.append((rows, (identity, step_plan.level_label)))
                continue
            if rows:
                source = ended_on(ended, rows, tolerance)
            else:  # no coordinates printed (A15): at the node's geometry
                source = ended_on(ended, node_rows, tolerance) or (ended[-1][1] if ended else None)
            if step.job_type == CalculationType.SINGLE_POINT:
                if source is None and ended:
                    warnings.append(
                        {
                            "code": "SP-GEOM",
                            "message": (
                                f"Step {step.index} is a single point on a geometry that none "
                                "of the node's optimizations ended on. Set its geometry level "
                                "by hand in the calculation's details."
                            ),
                        }
                    )
                elif source is not None and source[0] != identity:
                    step_plan.geometry_level_label = source[1]
            elif step.job_type == CalculationType.FREQUENCY:
                source = source or (ended[-1][1] if ended else None)  # A11
                if source is not None and source[0] != identity:
                    warnings.append(
                        {
                            "code": "FREQ-LEVEL",
                            "message": (
                                f"Step {step.index} is a frequency job at "
                                f"{step_plan.level_label}, but the geometry was optimized at "
                                f"{source[1]}. G at the geometry level uses a frequency job at "
                                "that level (EN-4)."
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
        if NOT_STATIONARY in step.printed:
            warnings.append(
                {
                    "code": "W-FREQ-NONSTAT",
                    "message": (
                        f"The frequency job in step {step.index} is not at a stationary point: "
                        f"{step.printed[NOT_STATIONARY]} (D158)."
                    ),
                }
            )
        contaminated = spin.read(step.printed, step.multiplicity)
        if contaminated is not None and contaminated.contaminated:
            warnings.append(
                {
                    "code": "W-SPIN",
                    "message": f"Step {step.index}: {spin.message(contaminated)} (D162).",
                }
            )
        if step_plan.missing:
            warnings.append(
                {
                    "code": "W-PARSE",
                    "message": f"Step {step.index}: not read from the file: "
                    + ", ".join(step_plan.missing),
                }
            )
        if step.constrained:
            warnings.append({"code": "W-CONSTRAINED", "message": constrained_message(step)})
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
                        f"{target.multiplicity}. Another state gives none of this node's "
                        "energies (D155): import it as a new node to use them."
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
    origin = _origin(staged, options, settings)

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
        names=names,
        warnings=warnings,
        suggested=suggested,
        origin=origin,
        blockers=blockers,
        continues_from=continues_from,
    )


# ---------- IRC parts of a TS node (D131) ----------

IRC_DIRECTIONS = ("forward", "reverse")
_ENDINGS = {
    "max_points": "stopped at its maximum number of points{limit}, before reaching a minimum",
    "stopped": "ends before the path does: the job did not finish",
    "complete": "ended without reporting a minimum",
}


def _origin(staged: StagedFile, options: ImportOptions, settings: Any) -> dict[str, str]:
    return {
        "device": options.origin_device
        if options.origin_device is not None
        else staged.origin_device or settings.last_device,
        "path": options.origin_path if options.origin_path is not None else staged.origin_path,
        "name": options.original_name or staged.original_name,
    }


def irc_part_of(step: ParsedStep, part: str) -> str | None:
    """The IRC part a file's step adds as `part` ("path" or "end"), or None if it adds none:
    the IRC step itself, or an end point's optimization or frequency job."""
    if part == "path":
        return "path" if step.irc is not None else None
    if step.job_type == CalculationType.OPTIMIZATION and step.final_geometry:
        return "end_opt"
    if step.job_type == CalculationType.FREQUENCY:
        return "end_freq"
    return None


def _irc_basis(
    target: Node, program: str, steps: list[ParsedStep], options: ImportOptions
) -> tuple[ImportOptions, str | None]:
    """D131, A76: an IRC or end-point job usually reads its basis set from the TS job's
    checkpoint (ChkBasis), so a file naming none takes the basis set of the TS's own
    optimization unless one was given (D104)."""
    if (options.missing_basis or "").strip() or not any(basis_not_named(program, s) for s in steps):
        return options, None
    optimization = levels.node_geometry_optimization(target)
    basis = optimization.level.basis if optimization and optimization.level else ""
    if not basis:
        return options, None
    return replace(options, missing_basis=basis), (
        f"The file reads its basis set from a checkpoint; it is taken as {basis}, the basis "
        f"set of the optimization of '{target.label}'."
    )


def _plan_irc(
    session: Session,
    staged: StagedFile,
    options: ImportOptions,
    settings: Any,
    store_names: bool,
) -> Plan:
    """D131: the preview of an IRC part added to a TS node. Nothing about the node changes: the
    file's steps become IRC parts, which never give the node an energy or a geometry."""
    part = options.irc_part
    if part not in ("path", "end"):
        raise ImportFailed(f"unknown IRC part '{part}'")
    target = session.get(Node, options.target_node_id) if options.target_node_id else None
    if target is None:
        raise ImportFailed("An IRC is imported onto the TS node it was run from")
    if isinstance(staged.parsed, crest.Ensemble):
        raise ImportFailed("A conformer ensemble is not an IRC calculation")
    if part == "end" and options.irc_direction not in IRC_DIRECTIONS:
        raise ImportFailed("Say which end of the IRC this job was run on: forward or reverse")
    steps = staged.parsed.steps
    blockers: list[str] = []
    warnings: list[dict[str, Any]] = []
    options, basis_note = _irc_basis(target, staged.parsed.program, steps, options)
    if basis_note:
        warnings.append({"code": "IRC-BASIS", "message": basis_note})
    if target.role != Role.TRANSITION_STATE:
        blockers.append("IRC calculations go on a node whose role is transition state")

    chosen = [s for s in steps if irc_part_of(s, part)]
    if not chosen and part == "path":
        blockers.append("No IRC was found in this file")
    elif not chosen:
        blockers.append("No optimization or frequency job was found in this file")
    direction = options.irc_direction if part == "end" else None
    if part == "path" and chosen:
        directions = {s.irc.requested for s in chosen}
        direction = directions.pop() if len(directions) == 1 else "both"

    tolerance = settings.duplicate_tolerance
    for step in chosen:
        run = step.irc
        if run is not None and run.missing:
            blockers.append(
                f"{staged.parsed.program} wrote this IRC's points to "
                + " and ".join(run.missing)
                + ", which were not beside the file. Pick the output in the folder where "
                f"{staged.parsed.program} wrote them, so they are read and copied with it."
            )
        if run is not None:
            start = next((p for p in run.points if p.number == 0), None)
            rows = _rows(step.geometries[start.structure if start else 0])
            distance = geometry.aligned_rmsd(rows, target.geometry) if target.geometry else None
            if distance is not None and distance > tolerance:
                warnings.append(
                    {
                        "code": "IRC-START",
                        "message": (
                            f"The IRC starts {distance:.3f} Å from the structure of "
                            f"'{target.label}' (over {tolerance} Å): it was run from another "
                            "structure, such as an earlier TS or one at another level."
                        ),
                    }
                )
            for path, ending in sorted(run.endings.items()):
                if ending in _ENDINGS:
                    limit = f" ({run.max_points})" if run.max_points else ""
                    side = run.directions.get(path, run.requested)
                    warnings.append(
                        {
                            "code": "IRC-END",
                            "message": f"The {side} path "
                            + _ENDINGS[ending].format(limit=limit)
                            + ".",
                        }
                    )
        if step.termination != "normal":
            warnings.append(
                {"code": "W-TERM", "message": f"Step {step.index} did not terminate normally."}
            )
        if step.optimization_converged is False:
            warnings.append(
                {
                    "code": "W-OPT-INC",
                    "message": f"The optimization in step {step.index} did not finish.",
                }
            )
        if part == "end" and step.frequencies and step.imaginary_count:
            warnings.append(
                {
                    "code": "IRC-IMAG",
                    "message": (
                        f"Step {step.index} has {step.imaginary_count} imaginary "
                        "frequenc"
                        + ("y" if step.imaginary_count == 1 else "ies")
                        + ": the end point is not a minimum."
                    ),
                }
            )
        if (
            target.charge is not None
            and step.charge is not None
            and (target.charge, target.multiplicity) != (step.charge, step.multiplicity)
        ):
            warnings.append(
                {
                    "code": "W-CHG",
                    "message": (
                        f"Step {step.index} has charge {step.charge} and multiplicity "
                        f"{step.multiplicity}; the TS has {target.charge} and "
                        f"{target.multiplicity}."
                    ),
                }
            )

    resolved = {s.index: _resolve_level(session, staged.parsed.program, s, options) for s in steps}
    names = _names(session, chosen, resolved, store_names, blockers)
    step_plans = []
    for step in steps:
        rows = _rows(step.final_geometry)
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
                rmsd_to_node=geometry.aligned_rmsd(rows, target.geometry)
                if rows and target.geometry
                else None,
                assignment="node" if step in chosen else "preview",
                missing=_still_missing(step, resolved[step.index]),
                basis_given=resolved[step.index].basis_given,
            )
        )
        if step in chosen and step_plans[-1].missing:
            warnings.append(
                {
                    "code": "W-PARSE",
                    "message": f"Step {step.index}: not read from the file: "
                    + ", ".join(step_plans[-1].missing),
                }
            )
    last = chosen[-1] if chosen else None
    return Plan(
        token=staged.token,
        program=staged.parsed.program,
        program_version=staged.parsed.version,
        original_name=staged.original_name,
        size=staged.size,
        checksum=staged.checksum,
        mode="onto",
        target_node_id=target.id,
        target_label=target.label,
        chosen_step=last.index if last else None,
        steps=step_plans,
        xyz=xyz.format_xyz(last.final_geometry, comment=PurePath(staged.original_name).stem)
        if last and last.final_geometry
        else None,
        derived_offered=False,
        duplicates=[],
        already_imported=_already_imported(session, staged.checksum),
        names=names,
        warnings=warnings,
        suggested={"label": target.label, "role": target.role, "status": target.status},
        origin=_origin(staged, options, settings),
        blockers=blockers,
        kind="irc",
        irc={"part": part, "direction": direction},
    )


def _commit_irc(
    session: Session,
    folder: Path,
    staged: StagedFile,
    plan_: Plan,
    options: ImportOptions,
    dry_run: bool,
) -> "CommitResult":
    """D131: the file's IRC steps, or its end point's jobs, become IRC parts of the TS node."""
    node = session.get(Node, plan_.target_node_id)
    origin = plan_.origin
    source_id = new_id()
    stored = _copy_into(folder, staged, source_id, _stored_name(origin["name"]), dry_run)
    try:
        steps = {s.index: s for s in staged.parsed.steps}
        if not dry_run:
            # D133: the files the IRC's points were read from go beside the copy, where reading
            # the copy again finds them.
            for step in steps.values():
                for name, data in (step.irc.files if step.irc else {}).items():
                    target = folder / FILES_DIR / source_id / name
                    if not target.exists():
                        target.write_bytes(data)
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
        calculation_ids = []
        for step_plan in plan_.steps:
            if step_plan.assignment != "node":
                continue
            step = steps[step_plan.index]
            part = irc_part_of(step, plan_.irc["part"])
            direction = step.irc.requested if step.irc else options.irc_direction
            calculation = _create_calculation(
                session, node, step, step_plan, staged, source, None, (part, direction)
            )
            calculation_ids.append(calculation.id)
        session.flush()
    except BaseException:
        shutil.rmtree(folder / FILES_DIR / source_id, ignore_errors=True)
        raise
    if not dry_run and staged.origin_device is None:
        _remember_device(origin["device"])
    return CommitResult(
        node_id=node.id,
        derived_node_id=None,
        calculation_ids=calculation_ids,
        source_file_id=source_id,
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
            else staged.origin_device or settings.last_device,
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
    session: Session,
    folder: Path,
    staged: StagedFile,
    plan_: Plan,
    options: ImportOptions,
    dry_run: bool = False,
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
    stored = _copy_into(folder, staged, source_id, _stored_name(origin["name"]), dry_run)
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
        for position, conformer in enumerate(kept, start=1):
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
                group_position=position,  # D89
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
    if not dry_run and staged.origin_device is None:
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


def _copy_into(
    folder: Path, staged: StagedFile, source_id: str, name: str, dry_run: bool = False
) -> str:
    """Copy the file to files/<source-file-id>/<name> (D16). Returns the relative path; a dry
    run (a batch preview, D97) only returns it."""
    if dry_run:
        return f"{FILES_DIR}/{source_id}/{name}"
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
        node.view_rotation = derived_from.view_rotation
    else:
        if fields.get("pos_x") is not None and fields.get("pos_y") is not None:
            node.pos_x, node.pos_y = fields["pos_x"], fields["pos_y"]
        node.step_id = fields.get("step_id")
        node.branch_id = fields.get("branch_id")
        node.notes = fields.get("notes") or ""
        node.path_edge_id = fields.get("path_edge_id")
        node.on_edge_only = bool(fields.get("on_edge_only")) and node.path_edge_id is not None
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


def _remove_tag(session: Session, node: Node, tag: str) -> None:
    if tag in node.tags:
        _set(session, node, "tags", [t for t in node.tags if t != tag])


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
    fields["step_id"], fields["branch_id"] = options.step_id, options.branch_id
    fields["notes"] = options.notes
    fields["path_edge_id"], fields["on_edge_only"] = options.path_edge_id, options.on_edge_only
    return fields


def commit(
    session: Session,
    folder: Path,
    staged: StagedFile,
    options: ImportOptions,
    dry_run: bool = False,
) -> CommitResult:
    """Write the import in the caller's transaction. A dry run writes the same records but
    copies no file and remembers no setting, for a caller that rolls the transaction back
    (the batch preview, D97)."""
    plan_ = plan(session, staged, options, store_names=True)
    if plan_.blockers:
        raise ImportBlocked(plan_.blockers)
    if plan_.kind == "ensemble":
        return _commit_ensemble(session, folder, staged, plan_, options, dry_run)
    if plan_.kind == "irc":
        return _commit_irc(session, folder, staged, plan_, options, dry_run)
    fields = _node_fields(plan_, options)
    steps = {s.index: s for s in staged.parsed.steps}
    chosen = steps[plan_.chosen_step] if plan_.chosen_step is not None else None
    chosen_rows = _rows(chosen.final_geometry) if chosen else None

    origin = plan_.origin
    source_id = new_id()
    stored = _copy_into(folder, staged, source_id, _stored_name(origin["name"]), dry_run)
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
            if plan_.continues_from is not None:
                _set(session, node, "geometry", chosen_rows)  # D100; the history keeps the old
                if all(
                    steps[s.index].optimization_converged is not False
                    for s in plan_.steps
                    if s.assignment == "node"
                ):
                    _remove_tag(session, node, OPTIMIZATION_INCOMPLETE)

        derived = None
        if any(p.assignment == "derived" for p in plan_.steps):
            derived = _new_node(session, fields, chosen_rows, chosen, target)

        calculation_ids = []
        tolerance = app_settings.load().geometry_tolerance
        for destination, owner in (("node", node), ("derived", derived)):
            if owner is None:
                continue
            # D100: a single point's geometry level is the optimization that ended on its
            # geometry; one that prints no coordinates is at the node's geometry (A15).
            ended: list[tuple[Any, levels.LevelOfTheory | None]] = []
            if owner is target:
                ended = [(c.geometry, c.level) for c in levels.optimizations(owner)]
            for step_plan in plan_.steps:
                if step_plan.assignment != destination:
                    continue
                step = steps[step_plan.index]
                rows = _rows(step.final_geometry) or owner.geometry
                geometry_level = ended_on(ended, rows, tolerance) or (
                    None if step.final_geometry or not ended else ended[-1][1]
                )
                calculation = _create_calculation(
                    session, owner, step, step_plan, staged, source, geometry_level
                )
                calculation_ids.append(calculation.id)
                if calculation.type in OPTIMIZATION_TYPES and not calculation.constraints:
                    ended.append((calculation.geometry, calculation.level))  # D157
                if step.optimization_converged is False:
                    _add_tag(session, owner, OPTIMIZATION_INCOMPLETE)
        session.flush()
        if node.on_edge_only:
            from chembook3d.services import scan_edges  # it reads paths with this module

            scan_edges.after_import(session, node)  # D121: a TS calculation brings the box back
    except BaseException:
        shutil.rmtree(folder / FILES_DIR / source_id, ignore_errors=True)
        raise

    if not dry_run and staged.origin_device is None:
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
    irc: tuple[str | None, str | None] = (None, None),
) -> Calculation:
    """`irc` is the IRC part and direction the calculation is on a TS node (D131)."""
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
        if step_plan.basis_given:
            # D104: the file names no basis; the one given at import is the level's, and the
            # parsed level keeps what the file said.
            parsed_level["basis"] = ""
            parsed_level["basis_given"] = step_plan.basis_given
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
        parse_warnings=list(step_plan.missing),
        constraints=list(step.constraints),  # D157
        source_file_id=source.id,
        irc_part=irc[0],
        irc_direction=irc[1],
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
        # D153: which printed total the energy is, for a correlated method.
        printed={**step.printed, "energy_label": step.energy_label}
        if step.energy_label
        else step.printed,
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
            **({"irc_part": irc[0], "irc_direction": irc[1]} if irc[0] else {}),
        },
        source="import",
    )
    return calculation
