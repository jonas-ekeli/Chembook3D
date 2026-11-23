"""HTTP API for removing one calculation from a node (D259, FR-CALC-10). The rules live in
`services/calculation_removal.py`; these handlers only translate. The copies of files no
calculation comes from any more are removed only once the transaction has committed."""

from typing import Any

from fastapi import APIRouter, Request
from sqlalchemy import event
from sqlalchemy.orm import Session

from chembook3d.api.routes import DbSession, _investigation
from chembook3d.services import calculation_removal, import_undo

router = APIRouter(prefix="/api")


@router.get("/calculations/{calculation_id}/removal")
def preview_removal(calculation_id: str, session: DbSession) -> dict[str, Any]:
    """What removing the calculation takes with it: the calculations stored as sitting on it
    (its frequency jobs and single points, D255) and the copied files nothing else comes from.
    Changes nothing."""
    return calculation_removal.plan(session, calculation_id).as_dict()


@router.delete("/calculations/{calculation_id}")
def remove_calculation(calculation_id: str, request: Request, session: DbSession) -> dict[str, Any]:
    """Remove the calculation with those sitting on it; the node keeps its coordinates."""
    removal = calculation_removal.plan(session, calculation_id)
    summary = removal.as_dict()
    stored = calculation_removal.apply(session, removal)
    folder = _investigation(request).folder

    def remove_copies(_session: Session) -> None:
        for path in stored:
            import_undo.remove_copy(folder, path)

    event.listen(session, "after_commit", remove_copies, once=True)
    return summary
