"""HTTP API for the pathway structure and the canvas (phase 3): reaction steps, branches,
transitions, group nodes, layout, and the data the 3D viewer needs for modes and overlays.
All rules live in the services; these handlers only translate."""

from collections import Counter
from dataclasses import asdict
from datetime import date, datetime, time
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from chembook3d import xyz
from chembook3d.api.notes import NoteOut, note_out
from chembook3d.api.routes import (
    DbSession,
    GeometryOut,
    HistoryOut,
    NodeOut,
    WarningOut,
    _get_node,
    _history_list,
    _investigation,
    _node_out,
)
from chembook3d.models import (
    AlignmentSet,
    Branch,
    Calculation,
    GroupNode,
    Node,
    ReactionStep,
    Transition,
)
from chembook3d.services import alignment as alignment_service
from chembook3d.services import atom_matching, layout, trajectory
from chembook3d.services import branches as branch_service
from chembook3d.services import groups as group_service
from chembook3d.services import nodes as node_service
from chembook3d.services import notes as note_service
from chembook3d.services import overview as overview_service
from chembook3d.services import species as species_service
from chembook3d.services import steps as step_service
from chembook3d.services import transitions as transition_service
from chembook3d.services.records import get

router = APIRouter(prefix="/api")


# ---------- schemas ----------


class StepOut(BaseModel):
    id: str
    name: str
    notes: str
    position: int
    node_count: int


class StepIn(BaseModel):
    name: str | None = None
    notes: str | None = None


class StepOrderIn(BaseModel):
    ids: list[str]


class BranchOut(BaseModel):
    id: str
    name: str
    colour: str
    status: str
    notes: str
    parent_ids: list[str]
    child_ids: list[str]
    lineage_paths: list[list[str]]
    split_node_id: str | None
    node_count: int


class BranchIn(BaseModel):
    name: str | None = None
    colour: str | None = None
    status: str | None = None
    notes: str | None = None
    parent_ids: list[str] | None = None


class NewBranchIn(BaseModel):
    name: str = ""
    colour: str | None = None


class SplitIn(BaseModel):
    branches: list[NewBranchIn]


class TransitionSpeciesOut(BaseModel):
    species_id: str
    label: str
    formula: str | None
    direction: str  # "joins" or "leaves" (D69)
    count: int


class TransitionOut(BaseModel):
    id: str
    source_id: str
    target_id: str
    source_kind: str  # "node" or "group"
    target_kind: str
    status: str
    notes: str
    direct: bool  # D53: no TS at either end
    cross_branch: bool  # P9: drawn dashed
    source_side: str  # D76: the side of each box the arrow leaves from and arrives at
    target_side: str
    species: list[TransitionSpeciesOut]  # D69: free species that join or leave
    warnings: list[WarningOut]  # W-BALANCE


class TransitionSpeciesIn(BaseModel):
    species_id: str
    direction: str
    count: int = 1


class TransitionIn(BaseModel):
    source_id: str | None = None
    target_id: str | None = None
    status: str | None = None
    notes: str | None = None
    source_side: str | None = None
    target_side: str | None = None


class GroupOut(BaseModel):
    id: str
    label: str
    notes: str
    step_id: str | None
    member_ids: list[str]
    representative_id: str | None
    incoming_branch_ids: list[str]
    outgoing_branch_id: str | None
    pos_x: float
    pos_y: float
    layout: str


class GroupIn(BaseModel):
    label: str | None = None
    notes: str | None = None
    step_id: str | None = None
    representative_id: str | None = None
    outgoing_branch_id: str | None = None
    pos_x: float | None = None
    pos_y: float | None = None
    layout: str | None = None


class AddMembersIn(BaseModel):
    node_ids: list[str]


class ReconnectIn(BaseModel):
    member_ids: list[str]
    label: str = ""
    outgoing: NewBranchIn | None = None


class GroupMemberBrief(BaseModel):
    id: str
    label: str
    calculation_count: int


class GroupDeletePreview(BaseModel):
    members: list[GroupMemberBrief]
    calculations: int
    group_transitions: int  # removed by dissolving
    all_transitions: int  # removed by deleting with contents


class DissolveIn(BaseModel):
    restore_branches: bool = False


class CanvasOut(BaseModel):
    nodes: list[NodeOut]  # pathway nodes only
    species: list[NodeOut]  # free species (D69), not drawn on the canvas
    steps: list[StepOut]
    branches: list[BranchOut]
    transitions: list[TransitionOut]
    groups: list[GroupOut]
    notes: list[NoteOut]  # pinned to node cards (D85)


class PositionsIn(BaseModel):
    positions: dict[str, dict[str, float]] = Field(default_factory=dict)


class ModesOut(BaseModel):
    xyz: str
    frequencies: list[float]
    order: list[int]  # mode indices, imaginary first (FR-3D-03)
    modes: list[list[list[float]]]  # [mode][atom][dx, dy, dz]


class StepFrameOut(BaseModel):
    geometry: list[list[Any]]  # [[element, x, y, z], ...], placed on the final geometry
    energy: float | None  # hartree
    point: int | None  # scan point, 1-based
    converged: bool
    stage: int | None = None  # D112: a scan path's stage, when the file names it
    removed: bool = False  # D117: left out of the scan path by hand


class CutOut(BaseModel):
    before: int  # frame indices (0-based) of the kept frames on either side
    after: int
    removed: int
    jump: float | None  # Å, fitted RMSD across the cut
    large: bool  # over 0.5 Å (D116's limit): the trimmed path is not continuous there


class TrimOut(BaseModel):
    """D117: a scan path over its kept points."""

    removed: list[int]  # scan points (1-based) left out
    kept: int
    top: int  # frame (0-based) the node takes over the kept points
    shown: int | None  # frame the calculation stands for
    automatic: bool  # it shows the automatic top, so trimming moves it with the top
    barrier: float | None  # hartree, the top above the first kept point
    cuts: list[CutOut]
    spikes: list[int]  # frames (0-based) suggested for removal
    first_removed: bool
    last_removed: bool


class StepsOut(BaseModel):
    """D101: the structures of an optimization or scan, for the movie in the 3D view."""

    scan: str | None  # "relaxed", "rigid" or None
    points: int
    frames: list[StepFrameOut]
    # D112: "Use this structure" changes the node in place (a scan path node), rather than
    # making a derived node.
    in_place: bool = False
    trim: TrimOut | None = None  # D117: a scan path's kept points; None for other calculations


class TrimIn(BaseModel):
    """D117: scan points (1-based) to take out of the path and to put back."""

    remove: list[int] = Field(default_factory=list)
    restore: list[int] = Field(default_factory=list)


class TrimResultOut(BaseModel):
    steps: StepsOut
    node: NodeOut
    moved: int | None  # the frame (0-based) the node moved to with the top, if it did


class OverlayIn(BaseModel):
    node_ids: list[str]
    reference_id: str | None = None
    align: str = "all"  # all | atoms | none (D80)
    atoms: dict[str, list[int]] = Field(default_factory=dict)  # 1-based, per node
    allow_mirror: bool = False


class OverlayStructureOut(BaseModel):
    node_id: str
    label: str
    xyz: str  # as placed on the reference
    reference: bool
    rotated: bool
    rmsd_atoms: float | None
    rmsd_all: float | None
    mirrored: bool


class OverlayOut(BaseModel):
    align: str
    structures: list[OverlayStructureOut]


class AtomMatchIn(BaseModel):
    start_id: str
    end_id: str
    pairs: list[list[int]] = Field(default_factory=list)  # (start atom, end atom), 1-based


class AtomMatchOut(BaseModel):
    start_id: str
    end_id: str
    mapping: list[int]  # for each start atom in order, the end's atom matched to it (1-based)
    rmsd: float  # Å over all atoms, the end fitted on the start in this numbering
    formed: list[list[int]]  # start's numbering: bonded at the end only
    broken: list[list[int]]  # bonded at the start only
    inverted: list[int]  # start atoms whose neighbours sit the other way round at the end
    fixed: list[list[int]]  # the hand-fixed pairs kept
    same_numbering: bool
    confident: bool
    doubts: list[str]
    start_xyz: str
    end_xyz: str  # as stored
    renumbered_xyz: str  # the end in the start's order, placed on the start


class AlignmentSetOut(BaseModel):
    id: str
    name: str
    atoms: dict[str, list[int]]  # node id → 1-based atom numbers, paired in order


class AlignmentSetIn(BaseModel):
    name: str | None = None
    # A list sets a node's atoms; null takes the node out of the set.
    atoms: dict[str, list[int] | None] | None = None


# ---------- helpers ----------


def _step_out(session: Session, step: ReactionStep, counts: dict[str, int]) -> StepOut:
    return StepOut(
        id=step.id,
        name=step.name,
        notes=step.notes,
        position=step.position,
        node_count=counts.get(step.id, 0),
    )


def _branch_out(session: Session, branch: Branch, nodes: list[Node] | None = None) -> BranchOut:
    if nodes is None:
        nodes = branch_service.members(session, branch.id)
        count = len(nodes)
    else:
        count = sum(1 for n in nodes if n.branch_id == branch.id)
    return BranchOut(
        id=branch.id,
        name=branch.name,
        colour=branch.colour,
        status=branch.status,
        notes=branch.notes,
        parent_ids=[p.id for p in branch.parents],
        child_ids=[c.id for c in branch_service.children(session, branch.id)],
        lineage_paths=branch_service.lineage_paths(branch),
        split_node_id=branch.split_node_id,
        node_count=count,
    )


def _transition_out(session: Session, transition: Transition) -> TransitionOut:
    facts = transition_service.describe(session, transition)
    return TransitionOut(
        id=transition.id,
        source_id=transition.source_id,
        target_id=transition.target_id,
        source_kind="node" if transition.source_node_id else "group",
        target_kind="node" if transition.target_node_id else "group",
        status=transition.status,
        notes=transition.notes,
        source_side=transition.source_side,
        target_side=transition.target_side,
        species=[
            TransitionSpeciesOut(
                species_id=e.species_id,
                label=species_service.label(node),
                formula=xyz.formula_of(Counter(row[0] for row in node.geometry))
                if node.geometry
                else None,
                direction=e.direction,
                count=e.count,
            )
            for e in transition.species
            if (node := session.get(Node, e.species_id)) is not None
        ],
        warnings=[
            WarningOut(**w.as_dict()) for w in species_service.balance_warnings(session, transition)
        ],
        **facts,
    )


def _group_out(session: Session, group: GroupNode) -> GroupOut:
    return GroupOut(
        id=group.id,
        label=group.label,
        notes=group.notes,
        step_id=group.step_id,
        member_ids=[m.id for m in group_service.members(session, group.id)],
        representative_id=group.representative_id,
        incoming_branch_ids=[b.id for b in group.incoming_branches],
        outgoing_branch_id=group.outgoing_branch_id,
        pos_x=group.pos_x,
        pos_y=group.pos_y,
        layout=group.layout,
    )


def _step_counts(nodes: list[Node]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for node in nodes:
        if node.step_id:
            counts[node.step_id] = counts.get(node.step_id, 0) + 1
    return counts


class BranchSummaryOut(BaseModel):
    id: str | None
    name: str
    colour: str | None
    status: str | None
    counts: dict[str, int]
    node_count: int


class OpenItemOut(BaseModel):
    kind: str
    id: str
    label: str
    reason: str
    detail: str
    branch_ids: list[str]


class StepNotesOut(BaseModel):
    id: str
    name: str
    position: int
    notes: str


class OverviewOut(BaseModel):
    branches: list[BranchSummaryOut]
    open_items: list[OpenItemOut]
    recent: list[HistoryOut]
    steps: list[StepNotesOut]
    group_count: int


# ---------- canvas ----------


@router.get("/canvas", response_model=CanvasOut)
def canvas(session: DbSession):
    """Everything the canvas draws, in one request."""
    everything = node_service.list_nodes(session)
    nodes = [n for n in everything if not species_service.is_species(n)]
    counts = _step_counts(nodes)
    return CanvasOut(
        nodes=[_node_out(session, n) for n in nodes],
        species=[_node_out(session, n) for n in everything if species_service.is_species(n)],
        steps=[_step_out(session, s, counts) for s in step_service.list_steps(session)],
        branches=[_branch_out(session, b, nodes) for b in branch_service.list_branches(session)],
        transitions=[
            _transition_out(session, t) for t in transition_service.list_transitions(session)
        ],
        groups=[_group_out(session, g) for g in group_service.list_groups(session)],
        notes=[note_out(n) for n in note_service.list_all(session)],
    )


@router.get("/overview", response_model=OverviewOut)
def overview(session: DbSession, since: date | None = None):
    """FR-OV-01: the resume overview. `since` limits recent changes to that day and later."""
    start = datetime.combine(since, time.min) if since else None
    found = overview_service.overview(session, start)
    return OverviewOut(
        branches=[BranchSummaryOut(**asdict(b)) for b in found.branches],
        open_items=[OpenItemOut(**asdict(i)) for i in found.open_items],
        recent=_history_list(session, found.recent),
        steps=[StepNotesOut(**asdict(s)) for s in found.steps],
        group_count=found.group_count,
    )


@router.put("/positions", status_code=204)
def set_positions(body: PositionsIn, session: DbSession):
    layout.set_positions(session, body.positions)


# ---------- reaction steps ----------


@router.get("/steps", response_model=list[StepOut])
def list_steps(session: DbSession):
    counts = _step_counts(node_service.list_nodes(session))
    return [_step_out(session, s, counts) for s in step_service.list_steps(session)]


@router.post("/steps", response_model=StepOut, status_code=201)
def create_step(body: StepIn, session: DbSession):
    step = step_service.create(session, body.name or "", body.notes or "")
    return _step_out(session, step, {})


@router.patch("/steps/{step_id}", response_model=StepOut)
def update_step(step_id: str, body: StepIn, session: DbSession):
    step = step_service.update(session, step_id, body.model_dump(exclude_unset=True))
    return _step_out(session, step, _step_counts(node_service.list_nodes(session)))


@router.put("/steps/order", response_model=list[StepOut])
def reorder_steps(body: StepOrderIn, session: DbSession):
    counts = _step_counts(node_service.list_nodes(session))
    return [_step_out(session, s, counts) for s in step_service.reorder(session, body.ids)]


@router.delete("/steps/{step_id}")
def delete_step(step_id: str, session: DbSession) -> dict[str, int]:
    return step_service.delete(session, step_id)


# ---------- branches ----------


@router.get("/branches", response_model=list[BranchOut])
def list_branches(session: DbSession):
    nodes = node_service.list_nodes(session)
    return [_branch_out(session, b, nodes) for b in branch_service.list_branches(session)]


@router.post("/branches", response_model=BranchOut, status_code=201)
def create_branch(body: BranchIn, session: DbSession):
    branch = branch_service.create(session, body.model_dump(exclude_none=True))
    return _branch_out(session, branch)


@router.get("/branches/{branch_id}", response_model=BranchOut)
def get_branch(branch_id: str, session: DbSession):
    return _branch_out(session, get(session, Branch, branch_id, "Branch"))


@router.patch("/branches/{branch_id}", response_model=BranchOut)
def update_branch(branch_id: str, body: BranchIn, session: DbSession):
    branch = branch_service.update(session, branch_id, body.model_dump(exclude_unset=True))
    return _branch_out(session, branch)


@router.delete("/branches/{branch_id}")
def delete_branch(branch_id: str, session: DbSession) -> dict[str, int]:
    return branch_service.delete(session, branch_id)


@router.post("/branches/{branch_id}/arrange")
def arrange_branch(branch_id: str, session: DbSession) -> dict[str, int]:
    """FR-CAN-06: only this branch's nodes (and a group that starts it) move."""
    return {"moved": len(layout.arrange_branch(session, branch_id))}


@router.post("/nodes/{node_id}/split", response_model=list[BranchOut], status_code=201)
def split_node(node_id: str, body: SplitIn, session: DbSession):
    _get_node(session, node_id)
    created = branch_service.split(
        session, node_id, [b.model_dump(exclude_none=True) for b in body.branches]
    )
    return [_branch_out(session, b) for b in created]


# ---------- transitions ----------


@router.get("/transitions", response_model=list[TransitionOut])
def list_transitions(session: DbSession):
    return [_transition_out(session, t) for t in transition_service.list_transitions(session)]


@router.post("/transitions", response_model=TransitionOut, status_code=201)
def create_transition(body: TransitionIn, session: DbSession):
    transition = transition_service.create(session, body.model_dump(exclude_none=True))
    return _transition_out(session, transition)


@router.patch("/transitions/{transition_id}", response_model=TransitionOut)
def update_transition(transition_id: str, body: TransitionIn, session: DbSession):
    changes = body.model_dump(exclude_unset=True, exclude={"source_id", "target_id"})
    transition = transition_service.update(session, transition_id, changes)
    return _transition_out(session, transition)


@router.put("/transitions/{transition_id}/species", response_model=TransitionOut)
def attach_species(transition_id: str, body: TransitionSpeciesIn, session: DbSession):
    """D69: a free species joins or leaves on this transition (or changes direction or count)."""
    transition = species_service.attach(
        session, transition_id, body.species_id, body.direction, body.count
    )
    return _transition_out(session, transition)


@router.delete("/transitions/{transition_id}/species/{species_id}", response_model=TransitionOut)
def detach_species(transition_id: str, species_id: str, session: DbSession):
    return _transition_out(session, species_service.detach(session, transition_id, species_id))


@router.delete("/transitions/{transition_id}", status_code=204)
def delete_transition(transition_id: str, session: DbSession):
    transition_service.delete(session, transition_id)


# ---------- group nodes ----------


@router.get("/groups", response_model=list[GroupOut])
def list_groups(session: DbSession):
    return [_group_out(session, g) for g in group_service.list_groups(session)]


@router.post("/groups/reconnect", response_model=GroupOut, status_code=201)
def reconnect(body: ReconnectIn, session: DbSession):
    fields: dict[str, Any] = {"member_ids": body.member_ids, "label": body.label}
    if body.outgoing is not None:
        fields["outgoing"] = body.outgoing.model_dump(exclude_none=True)
    return _group_out(session, group_service.reconnect(session, fields))


@router.post("/groups/{group_id}/members", response_model=GroupOut)
def add_group_members(group_id: str, body: AddMembersIn, session: DbSession):
    return _group_out(session, group_service.add_members(session, group_id, body.node_ids))


@router.delete("/groups/{group_id}/members/{node_id}", response_model=GroupOut)
def remove_group_member(group_id: str, node_id: str, session: DbSession):
    """D88: the node leaves the group and stays, with its calculations and edges."""
    return _group_out(session, group_service.remove_member(session, group_id, node_id))


@router.put("/groups/{group_id}/order", response_model=GroupOut)
def reorder_group_members(group_id: str, body: StepOrderIn, session: DbSession):
    """D89: the members' order, which every member layout follows."""
    return _group_out(session, group_service.reorder_members(session, group_id, body.ids))


@router.patch("/groups/{group_id}", response_model=GroupOut)
def update_group(group_id: str, body: GroupIn, session: DbSession):
    group = group_service.update(session, group_id, body.model_dump(exclude_unset=True))
    return _group_out(session, group)


@router.get("/groups/{group_id}/delete-preview", response_model=GroupDeletePreview)
def group_delete_preview(group_id: str, session: DbSession):
    preview = group_service.delete_preview(session, group_id)
    return GroupDeletePreview(
        members=[
            GroupMemberBrief(
                id=m.id,
                label=m.label,
                calculation_count=node_service.calculation_count(session, m.id),
            )
            for m in preview["members"]
        ],
        calculations=preview["calculations"],
        group_transitions=len(preview["group_transitions"]),
        all_transitions=len(preview["all_transitions"]),
    )


@router.post("/groups/{group_id}/dissolve", status_code=204)
def dissolve_group(group_id: str, body: DissolveIn, session: DbSession):
    group_service.dissolve(session, group_id, body.restore_branches)


@router.delete("/groups/{group_id}")
def delete_group(group_id: str, session: DbSession) -> dict[str, int]:
    return group_service.delete_with_contents(session, group_id)


# ---------- 3D viewer data (FR-3D-03, FR-3D-04) ----------


@router.get("/calculations/{calculation_id}/modes", response_model=ModesOut)
def calculation_modes(calculation_id: str, session: DbSession):
    """Normal modes for animation, on the geometry the frequencies were computed for."""
    calculation = session.get(Calculation, calculation_id)
    if calculation is None:
        raise HTTPException(404, "Calculation not found")
    result = calculation.result
    if result is None or not result.frequencies or not result.normal_modes:
        raise HTTPException(404, "This calculation has no normal modes")
    rows = calculation.geometry or calculation.node.geometry
    if not rows or len(result.normal_modes[0]) != len(rows):
        raise HTTPException(422, "The normal modes do not match the geometry")
    atoms = [xyz.Atom(e, x, y, z) for e, x, y, z in rows]
    frequencies = [float(f) for f in result.frequencies]
    order = sorted(range(len(frequencies)), key=lambda i: (frequencies[i] >= 0, i))
    return ModesOut(
        xyz=xyz.format_xyz(atoms, comment=calculation.node.label),
        frequencies=frequencies,
        order=order,
        modes=result.normal_modes,
    )


@router.get("/calculations/{calculation_id}/steps", response_model=StepsOut)
def calculation_steps(calculation_id: str, request: Request, session: DbSession):
    """D101: every structure of the calculation's step with its energy, read again from the
    copied output file."""
    calculation = session.get(Calculation, calculation_id)
    if calculation is None:
        raise HTTPException(404, "Calculation not found")
    try:
        steps = trajectory.read_steps(_investigation(request).folder, calculation)
    except trajectory.StepsUnavailable as exc:
        raise HTTPException(422, str(exc)) from exc
    return _steps_out(calculation, steps)


def _trim_out(trim: trajectory.Trim) -> TrimOut:
    return TrimOut(**asdict(trim))


def _steps_out(calculation: Calculation, steps: trajectory.Steps) -> StepsOut:
    return StepsOut(
        scan=steps.scan,
        points=steps.points,
        frames=[
            StepFrameOut(
                geometry=[[e, round(x, 6), round(y, 6), round(z, 6)] for e, x, y, z in f.rows],
                energy=f.energy,
                point=f.point,
                converged=f.converged,
                stage=f.stage,
                removed=f.removed,
            )
            for f in steps.frames
        ],
        in_place=trajectory.changes_in_place(calculation.node),
        trim=_trim_out(steps.trim) if steps.trim else None,
    )


@router.post("/calculations/{calculation_id}/steps/trim", response_model=TrimResultOut)
def trim_steps(calculation_id: str, body: TrimIn, request: Request, session: DbSession):
    """D117: takes points out of a scan path or puts them back; the node follows the top
    when it showed the automatic one."""
    calculation = session.get(Calculation, calculation_id)
    if calculation is None:
        raise HTTPException(404, "Calculation not found")
    folder = _investigation(request).folder
    try:
        result = trajectory.trim(session, folder, calculation, body.remove, body.restore)
    except trajectory.StepsUnavailable as exc:
        raise HTTPException(422, str(exc)) from exc
    return TrimResultOut(
        steps=_steps_out(calculation, result.steps),
        node=_node_out(session, calculation.node),
        moved=result.moved,
    )


@router.post("/calculations/{calculation_id}/steps/trim-preview", response_model=TrimOut)
def preview_trim_steps(calculation_id: str, body: TrimIn, request: Request, session: DbSession):
    """D117: the path as a trim would leave it (cuts, jumps, top), changing nothing."""
    calculation = session.get(Calculation, calculation_id)
    if calculation is None:
        raise HTTPException(404, "Calculation not found")
    folder = _investigation(request).folder
    try:
        return _trim_out(trajectory.preview_trim(folder, calculation, body.remove, body.restore))
    except trajectory.StepsUnavailable as exc:
        raise HTTPException(422, str(exc)) from exc


@router.post("/calculations/{calculation_id}/steps/{frame}/use", response_model=GeometryOut)
def use_step(calculation_id: str, frame: int, request: Request, session: DbSession):
    """D112: the structure `frame` (0-based, as listed by the steps) as the node's geometry:
    in place on a scan path node, else on a new derived node (ID-4, ID-5)."""
    calculation = session.get(Calculation, calculation_id)
    if calculation is None:
        raise HTTPException(404, "Calculation not found")
    try:
        result = trajectory.use_frame(session, _investigation(request).folder, calculation, frame)
    except trajectory.StepsUnavailable as exc:
        raise HTTPException(422, str(exc)) from exc
    return GeometryOut(node=_node_out(session, result.node), derived=result.derived)


def atom_match_out(start: Node, end: Node, result: atom_matching.Match) -> AtomMatchOut:
    rows = atom_matching.renumbered(start.geometry, end.geometry, result.mapping)
    atoms = [xyz.Atom(e, x, y, z) for e, x, y, z in rows]
    return AtomMatchOut(
        start_id=start.id,
        end_id=end.id,
        start_xyz=node_service.to_xyz(start),
        end_xyz=node_service.to_xyz(end),
        renumbered_xyz=xyz.format_xyz(atoms, comment=f"{end.label} in the order of {start.label}"),
        **atom_matching.summary(result),
    )


@router.post("/atom-match", response_model=AtomMatchOut)
def atom_match(body: AtomMatchIn, session: DbSession):
    """D113, A59: the end's atoms matched to the start's numbering; nothing is stored."""
    start, end, result = atom_matching.match_nodes(session, body.start_id, body.end_id, body.pairs)
    return atom_match_out(start, end, result)


@router.post("/overlay", response_model=OverlayOut)
def overlay(body: OverlayIn, session: DbSession):
    """FR-3D-04, D80: the nodes' geometries placed on the reference's."""
    items = alignment_service.overlay(
        session, body.node_ids, body.reference_id, body.align, body.atoms, body.allow_mirror
    )
    structures = []
    for item in items:
        label = item.node.label or "Untitled node"
        if item.placed is None:
            structures.append(
                OverlayStructureOut(
                    node_id=item.node.id,
                    label=label,
                    xyz=node_service.to_xyz(item.node),
                    reference=True,
                    rotated=False,
                    rmsd_atoms=None,
                    rmsd_all=None,
                    mirrored=False,
                )
            )
            continue
        atoms = [xyz.Atom(e, x, y, z) for e, x, y, z in item.placed.rows]
        structures.append(
            OverlayStructureOut(
                node_id=item.node.id,
                label=label,
                xyz=xyz.format_xyz(atoms, comment=item.node.label),
                reference=False,
                rotated=item.placed.rotated,
                rmsd_atoms=item.placed.rmsd_atoms,
                rmsd_all=item.placed.rmsd_all,
                mirrored=item.placed.mirrored,
            )
        )
    return OverlayOut(align=body.align, structures=structures)


# ---------- alignment sets (FR-3D-07, D80) ----------


def _alignment_set_out(alignment_set: AlignmentSet) -> AlignmentSetOut:
    return AlignmentSetOut(
        id=alignment_set.id,
        name=alignment_set.name,
        atoms=alignment_service.atoms_of(alignment_set),
    )


@router.get("/alignment-sets", response_model=list[AlignmentSetOut])
def list_alignment_sets(session: DbSession):
    return [_alignment_set_out(s) for s in alignment_service.list_sets(session)]


@router.post("/alignment-sets", response_model=AlignmentSetOut, status_code=201)
def create_alignment_set(body: AlignmentSetIn, session: DbSession):
    created = alignment_service.create_set(session, body.name or "", body.atoms or {})
    return _alignment_set_out(created)


@router.patch("/alignment-sets/{set_id}", response_model=AlignmentSetOut)
def update_alignment_set(set_id: str, body: AlignmentSetIn, session: DbSession):
    changes = body.model_dump(exclude_unset=True)
    return _alignment_set_out(alignment_service.update_set(session, set_id, changes))


@router.delete("/alignment-sets/{set_id}", status_code=204)
def delete_alignment_set(set_id: str, session: DbSession):
    alignment_service.delete_set(session, set_id)
