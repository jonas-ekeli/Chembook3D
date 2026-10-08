"""HTTP API for what the notebook showed when last used (D105). The rules live in
`services/view_state.py`. Saving is quiet for the change counter (`live.QUIET`): it changes
nothing other tabs or Claude need to reload for."""

from typing import Annotated, Any

from fastapi import APIRouter, Body

from chembook3d.api.routes import DbSession
from chembook3d.services import view_state

router = APIRouter(prefix="/api")


@router.get("/view-state")
def read_view_state(session: DbSession) -> dict[str, Any]:
    return view_state.load(session)


@router.put("/view-state", status_code=204)
def save_view_state(session: DbSession, body: Annotated[dict[str, Any], Body()]) -> None:
    view_state.save(session, body)
