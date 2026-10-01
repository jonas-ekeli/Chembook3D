"""HTTP API for notes pinned to node cards and the pictures in them (D85). All rules live in
`services/notes.py`; these handlers only translate."""

from typing import Any

from fastapi import APIRouter, Request, Response
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel

from chembook3d.api.routes import DbSession, UtcDatetime, _investigation
from chembook3d.models import NodeNote, NoteImage
from chembook3d.services import notes as note_service
from chembook3d.services.records import get

router = APIRouter(prefix="/api")

# A picture opened on its own (e.g. from the browser's "open image in new tab") must not run
# anything, even if an SVG somehow kept a script: no scripts, no outside requests.
IMAGE_POLICY = "default-src 'none'; style-src 'unsafe-inline'; img-src data:; sandbox"


class NoteOut(BaseModel):
    id: str
    node_id: str
    corner: str
    colour: str
    title: str
    body: str  # cleaned HTML; pictures are <img data-note-image="…">
    collapsed: bool
    width: int
    created_at: UtcDatetime
    updated_at: UtcDatetime


class NoteIn(BaseModel):
    corner: str | None = None
    colour: str | None = None
    title: str | None = None
    body: str | None = None
    collapsed: bool | None = None
    width: int | None = None


class NoteImageOut(BaseModel):
    id: str
    media_type: str
    size: int


def note_out(note: NodeNote) -> NoteOut:
    return NoteOut(
        id=note.id,
        node_id=note.node_id,
        corner=note.corner,
        colour=note.colour,
        title=note.title,
        body=note.body,
        collapsed=note.collapsed,
        width=note.width,
        created_at=note.created_at,
        updated_at=note.updated_at,
    )


def _changes(body: NoteIn) -> dict[str, Any]:
    return body.model_dump(exclude_unset=True)


@router.get("/notes", response_model=list[NoteOut])
def list_notes(session: DbSession):
    return [note_out(n) for n in note_service.list_all(session)]


@router.post("/nodes/{node_id}/notes", response_model=NoteOut, status_code=201)
def create_note(node_id: str, body: NoteIn, session: DbSession):
    return note_out(note_service.create(session, node_id, _changes(body)))


@router.patch("/notes/{note_id}", response_model=NoteOut)
def update_note(note_id: str, body: NoteIn, session: DbSession):
    return note_out(note_service.update(session, note_id, _changes(body)))


@router.delete("/notes/{note_id}", status_code=204)
def delete_note(note_id: str, session: DbSession):
    note_service.delete_note(session, note_id)


@router.post("/note-images", response_model=NoteImageOut, status_code=201)
async def upload_note_image(request: Request):
    """A pasted, dropped or chosen picture; its type is read from the bytes, not the request."""
    investigation = _investigation(request)
    data = await request.body()

    def store() -> NoteImageOut:
        with investigation.sessions.begin() as session:
            image = note_service.add_image(session, data)
            return NoteImageOut(id=image.id, media_type=image.media_type, size=len(image.data))

    return await run_in_threadpool(store)


@router.get("/note-images/{image_id}")
def note_image(image_id: str, session: DbSession) -> Response:
    image = get(session, NoteImage, image_id, "Picture")
    return Response(
        content=image.data,
        media_type=image.media_type,
        headers={
            "Content-Security-Policy": IMAGE_POLICY,
            "X-Content-Type-Options": "nosniff",
            # Named by its content, so it never changes.
            "Cache-Control": "private, max-age=31536000, immutable",
        },
    )
