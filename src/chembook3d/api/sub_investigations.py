"""HTTP API for sub-investigations (D193): boxes holding a canvas of their own. The rules are
in `services/sub_investigations.py`; `GET /api/canvas` carries the boxes too."""

from fastapi import APIRouter
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from chembook3d.api.routes import DbSession
from chembook3d.models import Role, SubInvestigation
from chembook3d.services import sub_investigations as sub_service

router = APIRouter(prefix="/api")


class SubInvestigationOut(BaseModel):
    id: str
    label: str
    notes: str
    parent_id: str | None  # the canvas the box is on; none: the main canvas
    pos_x: float
    pos_y: float
    face_id: str | None  # the node inside whose structure the closed box shows
    # The boxes from the main canvas down to this one, outermost first (the breadcrumb).
    path: list[str]
    # What it holds at any depth: nodes (TSs among them), groups, sub-investigations.
    node_count: int
    ts_count: int
    group_count: int
    sub_count: int


class SubInvestigationIn(BaseModel):
    label: str | None = None
    notes: str | None = None
    face_id: str | None = None
    pos_x: float | None = None
    pos_y: float | None = None


class NewSubInvestigationIn(BaseModel):
    label: str = ""
    notes: str = ""
    # Nodes, groups and sub-investigations on one canvas that move into the new box.
    ids: list[str] = Field(default_factory=list)
    face_id: str | None = None  # default: the first node given
    # Q60: a group member listed alone stands for its whole group (the app asks first).
    with_groups: bool = False
    # An empty box only: the canvas it goes on (none: the main one) and where.
    canvas_id: str | None = None
    pos_x: float | None = None
    pos_y: float | None = None


class MoveIn(BaseModel):
    ids: list[str]
    target_id: str | None = None  # a sub-investigation; none: the main canvas
    with_groups: bool = False


class MovedOut(BaseModel):
    moved: int


class SubDeletePreview(BaseModel):
    nodes: int
    transition_states: int
    groups: int
    sub_investigations: int
    calculations: int
    transitions: int


def sub_out(
    session: Session,
    sub: SubInvestigation,
    summaries: dict[str, sub_service.Summary] | None = None,
) -> SubInvestigationOut:
    found = (summaries or sub_service.summaries(session))[sub.id]
    return SubInvestigationOut(
        id=sub.id,
        label=sub.label,
        notes=sub.notes,
        parent_id=sub.parent_id,
        pos_x=sub.pos_x,
        pos_y=sub.pos_y,
        face_id=sub.face_id,
        path=[s.id for s in sub_service.chain(session, sub.id)],
        node_count=found.nodes,
        ts_count=found.transition_states,
        group_count=found.groups,
        sub_count=found.subs,
    )


def subs_out(session: Session) -> list[SubInvestigationOut]:
    summaries = sub_service.summaries(session)
    return [sub_out(session, s, summaries) for s in sub_service.list_all(session)]


@router.get("/sub-investigations", response_model=list[SubInvestigationOut])
def list_sub_investigations(session: DbSession):
    return subs_out(session)


@router.post("/sub-investigations", response_model=SubInvestigationOut, status_code=201)
def create_sub_investigation(body: NewSubInvestigationIn, session: DbSession):
    """ "Move into new sub-investigation…" (D193): the box goes where the selection was."""
    return sub_out(session, sub_service.create(session, body.model_dump()))


@router.post("/sub-investigations/move", response_model=MovedOut)
def move_to_canvas(body: MoveIn, session: DbSession):
    """ "Move to…": onto a sub-investigation's canvas or the main one."""
    return MovedOut(moved=sub_service.move(session, body.ids, body.target_id, body.with_groups))


@router.patch("/sub-investigations/{sub_investigation_id}", response_model=SubInvestigationOut)
def update_sub_investigation(
    sub_investigation_id: str, body: SubInvestigationIn, session: DbSession
):
    sub = sub_service.update(session, sub_investigation_id, body.model_dump(exclude_unset=True))
    return sub_out(session, sub)


@router.post("/sub-investigations/{sub_investigation_id}/dissolve", response_model=MovedOut)
def dissolve_sub_investigation(sub_investigation_id: str, session: DbSession):
    """Everything inside goes back onto the box's canvas, centred where the box was."""
    return MovedOut(moved=sub_service.dissolve(session, sub_investigation_id))


@router.get(
    "/sub-investigations/{sub_investigation_id}/delete-preview", response_model=SubDeletePreview
)
def sub_delete_preview(sub_investigation_id: str, session: DbSession):
    found = sub_service.delete_preview(session, sub_investigation_id)
    return SubDeletePreview(
        nodes=len(found.contents.nodes),
        transition_states=sum(1 for n in found.contents.nodes if n.role == Role.TRANSITION_STATE),
        groups=len(found.contents.groups),
        sub_investigations=len(found.contents.subs),
        calculations=found.calculations,
        transitions=len(found.transitions),
    )


@router.delete("/sub-investigations/{sub_investigation_id}")
def delete_sub_investigation(sub_investigation_id: str, session: DbSession) -> dict[str, int]:
    """The box with everything inside it (the app asks first when it is not empty)."""
    return sub_service.delete(session, sub_investigation_id)
