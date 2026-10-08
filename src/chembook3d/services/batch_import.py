"""Batch import of a results folder (D97, A44, FR-IMP-14).

`scan` reads a folder (and its subfolders) and stages every output file as a single import
does (`imports.Staging`). `run` then proposes a target for each file and imports them in the
caller's transaction, one after another, through `imports.plan` and `imports.commit`. The
preview is the same run as a dry run in a transaction the caller rolls back, so what it shows
is what Import writes: a file is matched against the investigation as it will be after the
files before it, including the nodes they create or finish.

Which node a file goes to, by the first rule that applies (D97):
1. its final geometry matches a node within the attach tolerance (D62), same charge and
   multiplicity;
2. the first geometry in the file matches the guess of a node with no calculations (ID-6);
3. its name matches a node label: exactly (case ignored) first, then one-for-one with `_`
   standing for a character a file name may have lost;
4. otherwise a new node, in a grid to the right of the canvas.
"""

import re
import shutil
import unicodedata
from collections import deque
from dataclasses import asdict, dataclass, field
from pathlib import Path, PurePath
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from chembook3d import settings as app_settings
from chembook3d.api.downloads import file_name
from chembook3d.investigation import FILES_DIR
from chembook3d.models import Calculation, GroupNode, Node, NodeKind, SourceFile, new_id
from chembook3d.parsers import crest, gaussian, orca, xtb
from chembook3d.services import geometry, history, imports
from chembook3d.services import nodes as node_service
from chembook3d.services.layout import COLUMN_WIDTH, ROW_HEIGHT

HEAD = 65536  # bytes read to tell an output from anything else
MAX_FILES = 5000  # files looked at in one scan
GRID_COLUMNS = 4  # new nodes are placed four to a row (A44)


class BatchNotFound(LookupError):
    pass


class BatchFailed(ValueError):
    pass


# ---------- scan ----------


@dataclass
class BatchFile:
    id: str
    relative: str  # path inside the scanned folder, with "/"
    staged: imports.StagedFile


@dataclass
class Batch:
    token: str
    folder: Path
    recursive: bool
    files: list[BatchFile]
    other_count: int  # files that are no output (inputs, .chk, logs, side files)
    unreadable: list[dict[str, str]]  # outputs that could not be read, with the reason


def _looks_like_output(head: str) -> bool:
    return (
        gaussian.looks_like_gaussian(head)
        or orca.looks_like_orca(head)
        or xtb.looks_like_xtb(head)
        or crest.looks_like_ensemble(head)
    )


def _candidates(folder: Path, recursive: bool, skip: Path | None) -> list[Path]:
    found: list[Path] = []
    pending = [folder]
    while pending:
        current = pending.pop()
        try:
            children = sorted(current.iterdir(), key=lambda p: p.name.lower())
        except OSError:
            continue
        for child in children:
            if child.name.startswith("."):
                continue
            try:
                if child.is_dir():
                    if recursive and not child.is_symlink() and child != skip:
                        pending.append(child)
                elif child.is_file():
                    found.append(child)
            except OSError:
                continue
            if len(found) > MAX_FILES:
                raise BatchFailed(
                    f"{folder} holds more than {MAX_FILES} files; choose a smaller folder"
                )
    return found


def scan(
    staging: imports.Staging, folder: Path, recursive: bool, investigation: Path | None = None
) -> Batch:
    """Stage every output in `folder`. Nothing is written to the investigation; the open
    investigation's own folder is never scanned (its copies would all be "imported before")."""
    try:
        folder = folder.expanduser().resolve()
    except OSError as exc:
        raise BatchFailed(f"Cannot read {folder}") from exc
    if not folder.is_dir():
        raise BatchFailed(f"{folder} is not a folder")
    skip = investigation.resolve() if investigation else None
    batch = Batch(
        token=new_id(), folder=folder, recursive=recursive, files=[], other_count=0, unreadable=[]
    )
    try:
        for path in _candidates(folder, recursive, skip):
            relative = path.relative_to(folder).as_posix()
            try:
                with open(path, "rb") as handle:
                    head = handle.read(HEAD)
                if b"\x00" in head or not _looks_like_output(head.decode("utf-8", "replace")):
                    batch.other_count += 1
                    continue
                data = path.read_bytes()
            except OSError:
                batch.unreadable.append({"path": relative, "reason": "cannot be read"})
                continue
            try:
                staged = staging.add(data, path.name, str(path))
            except imports.ImportFailed as exc:
                batch.unreadable.append({"path": relative, "reason": str(exc)})
                continue
            batch.files.append(BatchFile(id=staged.token, relative=relative, staged=staged))
    except BaseException:
        for item in batch.files:
            staging.discard(item.staged.token)
        raise
    staging.batches[batch.token] = batch
    return batch


def discard(staging: imports.Staging, token: str) -> None:
    batch = staging.batches.pop(token, None)
    if batch is not None:
        for item in batch.files:
            staging.discard(item.staged.token)


def get(staging: imports.Staging, token: str) -> Batch:
    try:
        return staging.batches[token]
    except KeyError as exc:
        raise BatchNotFound(token) from exc


# ---------- name matching (D97 rule 3) ----------

# Characters every file name the app ever wrote kept; any other one may have become "_".
_KEPT = re.compile(r"[A-Za-z0-9.\-]")


def saved_name(label: str) -> str:
    """A label as the app writes it into a file name (the download rule)."""
    return file_name(label, "")


def _key(text: str) -> str:
    return unicodedata.normalize("NFC", text).casefold()


def _suffix_pattern(suffix: str) -> re.Pattern[str]:
    pattern = "".join(
        ".*" if c == "*" else "." if c == "?" else re.escape(c) for c in suffix.strip()
    )
    return re.compile(pattern, re.IGNORECASE | re.DOTALL)


def name_bases(stem: str, suffixes: list[str]) -> list[str]:
    """The file name without its extension, then without each suffix it ends in (longest base
    first)."""
    bases = [stem]
    patterns = [_suffix_pattern(s) for s in suffixes if s.strip()]
    for i in range(len(stem) - 1, 0, -1):
        if any(p.fullmatch(stem[i:]) for p in patterns) and stem[:i] not in bases:
            bases.append(stem[:i])
    return bases


def close_match(name: str, label: str) -> bool:
    """One for one: each `_` in the name may stand for one character of the label that is no
    letter, digit, "-" or "." (a prime, a space, a Greek letter); everything else must agree,
    case ignored. So "TS1-2__" fits TS1-2'' and never TS1-2'."""
    name = unicodedata.normalize("NFC", name)
    label = unicodedata.normalize("NFC", label)
    if len(name) != len(label):
        return False
    return all(
        a.casefold() == b.casefold() or (a == "_" and not _KEPT.fullmatch(b))
        for a, b in zip(name, label, strict=True)
    )


def match_name(stem: str, suffixes: list[str], nodes: list[Node]) -> tuple[str | None, list[Node]]:
    """("exact" | "close" | None, the nodes the name fits). Exact matches win over close ones
    for every base; among exact ones a match with the same case wins."""
    labelled = [n for n in nodes if n.label.strip()]
    bases = name_bases(stem, suffixes)
    for base in bases:
        found = [n for n in labelled if _key(saved_name(n.label)) == _key(base)]
        if len(found) > 1:
            same_case = [n for n in found if saved_name(n.label) == base]
            found = same_case if len(same_case) == 1 else found
        if found:
            return "exact", found
    for base in bases:
        found = [n for n in labelled if close_match(base, n.label)]
        if found:
            return "close", found
    return None, []


# ---------- options and plan ----------

DEFAULT_SUFFIXES = app_settings.BATCH_SUFFIXES


@dataclass
class RowChoice:
    included: bool | None = None
    # "auto", "new", "node:<node id>" or "file:<file id>" (the node another file creates)
    target: str | None = None
    duplicate_action: str | None = None  # "attach" or "new" for a possible duplicate
    label: str | None = None  # a new node's label
    basis: str | None = None  # D104: this file's basis set, if it names none; overrides the batch's


@dataclass
class BatchOptions:
    rows: dict[str, RowChoice] = field(default_factory=dict)
    basis_names: dict[str, str] = field(default_factory=dict)
    dispersion_names: dict[str, str] = field(default_factory=dict)
    # D104: the basis set for every file that names none (ChkBasis from another job)
    missing_basis: str | None = None
    origin_device: str | None = None
    suffixes: list[str] | None = None


@dataclass
class RowPlan:
    id: str
    path: str
    name: str
    program: str
    version: str | None
    size: int
    kind: str  # steps | ensemble
    included: bool
    skipped: str | None  # imported_before | same_file
    skipped_detail: str | None
    order: int | None  # position in the import order
    match: str  # geometry | first_geometry | name | name_close | new | chosen | none
    target: dict[str, Any] | None  # {kind: node | file | new | group, node_id, file_id, label}
    mode: str | None  # new | planned | onto (imports.Plan.mode)
    derived: bool
    candidates: list[dict[str, Any]]  # nodes a name fits equally
    duplicates: list[dict[str, Any]]
    duplicate_action: str | None
    label: str | None  # a new node's label
    jobs: str  # e.g. "Optimization + Frequency"
    level_label: str | None
    termination: str  # normal | abnormal
    atom_count: int
    steps: list[dict[str, Any]]
    warnings: list[dict[str, Any]]
    blockers: list[str]
    basis_missing: bool = False  # D104: a step's method needs a basis set the file never names
    basis_given: str | None = None  # the basis set it gets at import instead


@dataclass
class BatchPlan:
    token: str
    folder: str
    recursive: bool
    suffixes: list[str]
    origin_device: str
    rows: list[RowPlan]
    other_count: int
    unreadable: list[dict[str, str]]
    names: list[dict[str, Any]]
    blockers: list[str]
    counts: dict[str, int]
    missing_basis: str = ""  # D104

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class BatchResult:
    imported: int
    files: list[dict[str, Any]]
    history_id: str


def _jobs(staged: imports.StagedFile) -> str:
    if isinstance(staged.parsed, crest.Ensemble):
        return f"{len(staged.parsed.conformers)} conformers"
    return " + ".join(s.job_type.replace("_", " ").capitalize() for s in staged.parsed.steps)


def _termination(staged: imports.StagedFile) -> str:
    if isinstance(staged.parsed, crest.Ensemble):
        return "normal"
    normal = all(s.termination == "normal" for s in staged.parsed.steps)
    return "normal" if normal else "abnormal"


def _atom_count(staged: imports.StagedFile) -> int:
    if isinstance(staged.parsed, crest.Ensemble):
        return staged.parsed.atom_count
    final = _final(staged)
    return len(final.final_geometry) if final else 0


def _first_geometry(staged: imports.StagedFile) -> list[list[Any]] | None:
    for step in staged.parsed.steps:
        if step.geometries:
            return imports._rows(step.geometries[0])
    return None


def _final(staged: imports.StagedFile):
    with_geometry = [s for s in staged.parsed.steps if s.final_geometry]
    return with_geometry[-1] if with_geometry else None


def _basis_missing(staged: imports.StagedFile) -> bool:
    """D104: the file names no basis set for a step whose method needs one."""
    if isinstance(staged.parsed, crest.Ensemble):
        return False
    return any(imports.basis_not_named(staged.parsed.program, s) for s in staged.parsed.steps)


def _has_optimization(staged: imports.StagedFile) -> bool:
    if isinstance(staged.parsed, crest.Ensemble):
        return False
    return any(s.job_type in imports.OPTIMIZATION_TYPES for s in staged.parsed.steps)


def _compatible(node: Node, charge: int | None, multiplicity: int | None) -> bool:
    if node.charge is not None and charge is not None and node.charge != charge:
        return False
    return not (
        node.multiplicity is not None
        and multiplicity is not None
        and node.multiplicity != multiplicity
    )


def _closest(
    nodes: list[Node], rows: list[list[Any]] | None, step, tolerance: float
) -> Node | None:
    best: tuple[float, Node] | None = None
    if not rows:
        return None
    for node in nodes:
        if not node.geometry or not _compatible(node, step.charge, step.multiplicity):
            continue
        rmsd = geometry.aligned_rmsd(rows, node.geometry)
        if rmsd is not None and rmsd <= tolerance and (best is None or rmsd < best[0]):
            best = (rmsd, node)
    return best[1] if best else None


def _grid_start(session: Session) -> tuple[float, float]:
    """Right of everything on the canvas, level with its top (A44)."""
    points = [
        (n.pos_x, n.pos_y)
        for n in session.scalars(
            select(Node).where(Node.kind == NodeKind.NODE, Node.group_id.is_(None))
        )
    ]
    points += [(g.pos_x, g.pos_y) for g in session.scalars(select(GroupNode))]
    if not points:
        return 0.0, 0.0
    return max(x for x, _ in points) + 2 * COLUMN_WIDTH, min(y for _, y in points)


def _skipped(session: Session, batch: Batch) -> dict[str, tuple[str, str]]:
    """Files imported before (T-ID-06) or a second copy of a file in this folder."""
    found: dict[str, tuple[str, str]] = {}
    seen: dict[str, str] = {}
    for item in batch.files:
        checksum = item.staged.checksum
        if checksum in seen:
            found[item.id] = ("same_file", f"Same content as {seen[checksum]}")
            continue
        seen[checksum] = item.relative
        source = session.scalars(select(SourceFile).where(SourceFile.checksum == checksum)).first()
        if source is not None:
            found[item.id] = (
                "imported_before",
                f"Imported before as {source.original_name} on {source.imported_at:%Y-%m-%d %H:%M}",
            )
    return found


def _order(batch: Batch) -> list[BatchFile]:
    """Files with an optimization first, so the files that create or finish nodes come before
    the files that attach to them (D97); then by path."""
    return sorted(
        batch.files,
        key=lambda f: (
            0 if _has_optimization(f.staged) else 1,
            isinstance(f.staged.parsed, crest.Ensemble),
            f.relative.lower(),
        ),
    )


NAME_BLOCKERS = ("Give the custom basis set a name", "Give the custom dispersion a name")


def run(
    session: Session,
    folder: Path,
    batch: Batch,
    options: BatchOptions,
    dry_run: bool,
) -> tuple[BatchPlan, BatchResult | None]:
    """Plan and import the batch in the caller's transaction. A dry run (the preview) writes
    the same records but copies no file; the caller rolls it back. A real run raises
    imports.ImportBlocked, having removed every copied file, when anything is still needed."""
    settings = app_settings.load()
    suffixes = options.suffixes if options.suffixes is not None else settings.batch_suffixes
    device = (
        options.origin_device if options.origin_device is not None else settings.last_device
    ).strip()
    skipped = _skipped(session, batch)
    ordered = _order(batch)
    by_id = {f.id: f for f in batch.files}

    def choice(item: BatchFile) -> RowChoice:
        return options.rows.get(item.id) or RowChoice()

    def basis_for(item: BatchFile) -> str | None:
        """D104: the file's own basis set if given, else the batch's."""
        own = (choice(item).basis or "").strip()
        return own or (options.missing_basis or "").strip() or None

    def included(item: BatchFile) -> bool:
        chosen = choice(item).included
        return chosen if chosen is not None else item.id not in skipped

    rows: dict[str, RowPlan] = {}
    created: dict[str, str] = {}  # file id → the node it created or finished
    made_by: dict[str, str] = {}  # node id → the file whose import created it
    names: dict[str, dict[str, Any]] = {}
    basis_names = dict(options.basis_names)
    dispersion_names = dict(options.dispersion_names)
    tolerance = settings.geometry_tolerance
    x0, y0 = _grid_start(session)
    slot = 0
    copied: list[str] = []
    written: list[dict[str, Any]] = []
    queue = deque(item for item in ordered if included(item))
    deferred: set[str] = set()
    position = 0

    def describe(node_id: str | None, label: str | None) -> dict[str, Any] | None:
        if node_id is None:
            return None
        if node_id in made_by:
            source = by_id[made_by[node_id]]
            return {
                "kind": "file",
                "file_id": source.id,
                "node_id": None,
                "label": label or "",
                "file": source.relative,
            }
        return {"kind": "node", "node_id": node_id, "file_id": None, "label": label or ""}

    def unnamed(plan_: imports.Plan) -> bool:
        """Note the custom names still missing; a dry run stands in a placeholder so the
        files after it are planned as they will be once named."""
        added = False
        for request in plan_.names:
            if request.recognised:
                continue
            names.setdefault(request.key, asdict(request))
            target = basis_names if request.kind == "basis" else dispersion_names
            if not (target.get(request.key) or "").strip() and dry_run:
                what = "basis set" if request.kind == "basis" else "dispersion"
                target[request.key] = f"(unnamed {what} {len(names)})"
                added = True
        return added

    try:
        while queue:
            item = queue.popleft()
            chosen = choice(item)
            target_text = chosen.target or "auto"
            # A file sent to the node another file makes waits until that file is imported.
            if target_text.startswith("file:"):
                other = target_text[5:]
                waiting = [i for i, q in enumerate(queue) if q.id == other]
                if other not in created and waiting and item.id not in deferred:
                    deferred.add(item.id)
                    queue.insert(waiting[0] + 1, item)
                    continue
            position += 1
            staged = item.staged
            is_ensemble = isinstance(staged.parsed, crest.Ensemble)
            final = None if is_ensemble else _final(staged)
            final_rows = imports._rows(final.final_geometry) if final else None
            blockers: list[str] = []
            candidates: list[Node] = []
            target: Node | None = None
            match = "new"
            nodes = node_service.list_nodes(session)

            if is_ensemble:
                match = "new"
            elif target_text == "new":
                match = "chosen"
            elif target_text.startswith("node:"):
                target = session.get(Node, target_text[5:])
                match = "chosen"
                if target is None:
                    blockers.append("The node chosen for it no longer exists")
            elif target_text.startswith("file:"):
                other = target_text[5:]
                match = "chosen"
                if other in created:
                    target = session.get(Node, created[other])
                elif other in by_id:
                    blockers.append(
                        f"It goes to the node {by_id[other].relative} makes, which is not imported"
                    )
                else:
                    blockers.append("The file chosen for it is not in this batch")
            else:
                target = _closest(nodes, final_rows, final, tolerance)
                if target is not None:
                    match = "geometry"
                if target is None and final is not None:
                    calculated = set(session.scalars(select(Calculation.node_id).distinct()))
                    planned = [n for n in nodes if n.id not in calculated]
                    target = _closest(planned, _first_geometry(staged), final, tolerance)
                    if target is None:
                        # D100: an optimization continuing a node's pre-optimization.
                        names_so_far = imports.ImportOptions(
                            basis_names=basis_names,
                            dispersion_names=dispersion_names,
                            missing_basis=basis_for(item),
                        )
                        target = next(
                            (
                                n
                                for n in nodes
                                if n.id in calculated
                                and _compatible(n, final.charge, final.multiplicity)
                                and imports.continues(session, staged, n, names_so_far, tolerance)
                            ),
                            None,
                        )
                    if target is not None:
                        match = "first_geometry"
                if target is None:
                    how, found = match_name(PurePath(staged.original_name).stem, suffixes, nodes)
                    if len(found) == 1:
                        target, match = found[0], "name" if how == "exact" else "name_close"
                    elif found:
                        match, candidates = "none", found
                        blockers.append(
                            "Its name fits "
                            + ", ".join(f"“{n.label}”" for n in found)
                            + ": choose one"
                        )
                if target is None and not candidates and final is None:
                    match = "none"
                    blockers.append(
                        "It prints no coordinates: choose the node it was run on, or untick it"
                    )

            pos = (
                x0 + (slot % GRID_COLUMNS) * COLUMN_WIDTH,
                y0 + (slot // GRID_COLUMNS) * ROW_HEIGHT,
            )
            single = imports.ImportOptions(
                target_node_id=target.id if target else None,
                # Choosing "new node" answers the duplicate question too.
                duplicate_action=chosen.duplicate_action
                or ("new" if target_text == "new" else None),
                basis_names=basis_names,
                dispersion_names=dispersion_names,
                missing_basis=basis_for(item),
                label=chosen.label if chosen.label is not None and chosen.label.strip() else None,
                origin_device=device,
                pos_x=pos[0],
                pos_y=pos[1],
            )
            plan_: imports.Plan | None = None
            if not blockers:
                try:
                    plan_ = imports.plan(session, staged, single)
                    if unnamed(plan_):
                        plan_ = imports.plan(session, staged, single)
                except imports.ImportFailed as exc:
                    blockers.append(str(exc))
            if plan_ is not None:
                for blocker in plan_.blockers:
                    if blocker.startswith(NAME_BLOCKERS):
                        continue  # asked once for the whole batch
                    if plan_.duplicates and blocker.startswith("This geometry is a possible"):
                        closest = plan_.duplicates[0]
                        blocker = (
                            f"Possible duplicate of “{closest['label']}” (RMSD "
                            f"{closest['rmsd']:.3f} Å): attach it there or make a new node"
                        )
                    blockers.append(blocker)
                if target is None and plan_.mode != "new":
                    match = "duplicate"  # attached to a possible duplicate, as chosen

            result: imports.CommitResult | None = None
            commit_ok = (
                plan_ is not None
                and not blockers
                and not any(b.startswith(NAME_BLOCKERS) for b in plan_.blockers)
            )
            if commit_ok:
                try:
                    result = imports.commit(session, folder, staged, single, dry_run=dry_run)
                except imports.ImportBlocked as exc:
                    blockers.extend(b for b in exc.blockers if not b.startswith(NAME_BLOCKERS))
                except imports.ImportFailed as exc:
                    blockers.append(str(exc))
            if result is not None:
                # Calculations are added by node id, so a node loaded before still lists none;
                # the files after this one must see them (planned or not, ID-6).
                session.expire_all()
                copied.append(result.source_file_id)
                if plan_.mode == "new" and plan_.kind != "ensemble":
                    slot += 1
                    made_by[result.node_id] = item.id
                if plan_.kind == "ensemble":
                    slot += 1
                if result.derived_node_id:
                    made_by[result.derived_node_id] = item.id
                if result.node_id:
                    created[item.id] = result.derived_node_id or result.node_id
                node = session.get(Node, created[item.id]) if item.id in created else None
                group = session.get(GroupNode, result.group_id) if result.group_id else None
                written.append(
                    {
                        "file": item.relative,
                        "how": "group"
                        if group
                        else "derived"
                        if result.derived_node_id
                        else plan_.mode,
                        "node_id": node.id if node else None,
                        "group_id": group.id if group else None,
                        "label": (group.label if group else node.label if node else "") or "",
                        "calculations": len(result.calculation_ids),
                        # D102: so the batch can be undone file by file
                        "source_file_id": result.source_file_id,
                    }
                )

            described: dict[str, Any] | None = None
            if plan_ is not None and plan_.mode == "new":
                described = {
                    "kind": "group" if is_ensemble else "new",
                    "node_id": None,
                    "file_id": None,
                    "label": single.label or plan_.suggested["label"],
                }
            elif plan_ is not None and plan_.target_node_id:
                described = describe(plan_.target_node_id, plan_.target_label)
            elif target is not None:
                described = describe(target.id, target.label)
            steps = [] if plan_ is None else plan_.steps
            level_label = None
            if plan_ is not None and plan_.ensemble:
                level_label = plan_.ensemble["level_label"]
            elif steps:
                shown = next((s for s in steps if s.index == plan_.chosen_step), steps[-1])
                level_label = shown.level_label
            rows[item.id] = RowPlan(
                id=item.id,
                path=item.relative,
                name=staged.original_name,
                program=staged.parsed.program,
                version=getattr(staged.parsed, "version", None),
                size=staged.size,
                kind="ensemble" if is_ensemble else "steps",
                included=True,
                skipped=skipped.get(item.id, (None, None))[0],
                skipped_detail=skipped.get(item.id, (None, None))[1],
                order=position,
                match=match,
                target=described,
                mode=plan_.mode if plan_ else None,
                derived=bool(plan_ and plan_.derived_offered),
                candidates=[{"node_id": n.id, "label": n.label} for n in candidates],
                duplicates=[
                    {**(describe(d["node_id"], d["label"]) or {}), "rmsd": d["rmsd"]}
                    for d in (plan_.duplicates if plan_ else [])
                ],
                duplicate_action=chosen.duplicate_action,
                label=described["label"]
                if described and described["kind"] in ("new", "group")
                else None,
                jobs=_jobs(staged),
                level_label=level_label,
                termination=_termination(staged),
                atom_count=_atom_count(staged),
                steps=[
                    {
                        "index": s.index,
                        "type": s.type,
                        "level_label": s.level_label,
                        "geometry_level_label": s.geometry_level_label,
                        "termination": s.termination,
                        "optimization_converged": s.optimization_converged,
                        "energy": s.energy,
                        "assignment": s.assignment,
                    }
                    for s in steps
                ],
                warnings=[] if plan_ is None else plan_.warnings,
                blockers=blockers,
                basis_missing=_basis_missing(staged),
                basis_given=basis_for(item) if _basis_missing(staged) else None,
            )

        for item in ordered:
            if item.id in rows:
                continue
            staged = item.staged
            rows[item.id] = RowPlan(
                id=item.id,
                path=item.relative,
                name=staged.original_name,
                program=staged.parsed.program,
                version=getattr(staged.parsed, "version", None),
                size=staged.size,
                kind="ensemble" if isinstance(staged.parsed, crest.Ensemble) else "steps",
                included=False,
                skipped=skipped.get(item.id, (None, None))[0],
                skipped_detail=skipped.get(item.id, (None, None))[1],
                order=None,
                match="none",
                target=None,
                mode=None,
                derived=False,
                candidates=[],
                duplicates=[],
                duplicate_action=None,
                label=None,
                jobs=_jobs(staged),
                level_label=None,
                termination=_termination(staged),
                atom_count=_atom_count(staged),
                steps=[],
                warnings=[],
                blockers=[],
                basis_missing=_basis_missing(staged),
            )

        batch_blockers: list[str] = []
        for key, request in names.items():
            given = (
                options.basis_names if request["kind"] == "basis" else options.dispersion_names
            ).get(key) or ""
            request["name"] = given.strip() or None
            if not request["name"]:
                what = "basis set" if request["kind"] == "basis" else "dispersion"
                batch_blockers.append(f"Give the custom {what} a name")
        for row in rows.values():
            for blocker in row.blockers:
                batch_blockers.append(f"{row.path}: {blocker}")
        if not any(r.included for r in rows.values()):
            batch_blockers.append("Tick at least one file to import")

        result_out: BatchResult | None = None
        if not dry_run:
            if batch_blockers:
                raise imports.ImportBlocked(batch_blockers)
            entry_id = new_id()
            history.record(
                session,
                "batch_import",
                entry_id,
                "create",
                new={"folder": str(batch.folder), "count": len(written), "files": written},
                source="import",
            )
            session.flush()
            result_out = BatchResult(imported=len(written), files=written, history_id=entry_id)
    except BaseException:
        if not dry_run:
            for source_id in copied:
                shutil.rmtree(folder / FILES_DIR / source_id, ignore_errors=True)
        raise

    in_order = sorted(rows.values(), key=lambda r: (r.order is None, r.order or 0, r.path))
    counts = {
        "files": len(rows),
        "included": sum(r.included for r in in_order),
        "new": sum(r.included and r.mode == "new" for r in in_order),
        "attached": sum(r.included and r.mode == "onto" for r in in_order),
        "finished": sum(r.included and r.mode == "planned" for r in in_order),
        "skipped": sum(r.skipped is not None for r in in_order),
        "basis_missing": sum(r.included and r.basis_missing for r in in_order),
    }
    return (
        BatchPlan(
            token=batch.token,
            folder=str(batch.folder),
            recursive=batch.recursive,
            suffixes=list(suffixes),
            origin_device=device,
            rows=in_order,
            other_count=batch.other_count,
            unreadable=batch.unreadable,
            names=list(names.values()),
            blockers=batch_blockers,
            counts=counts,
            missing_basis=(options.missing_basis or "").strip(),
        ),
        result_out,
    )
