"""HTTP API for undoing an import from the history (D102, A49). The rules live in
`services/import_undo.py`; these handlers only translate.

An entry is undone by its history id: a calculation's "Imported …" entry undoes that file, a
batch's entry every file of the batch. The preview is the same undo in a transaction that is
always rolled back, so it writes nothing; the stored copies are removed only once the undo's
transaction has committed."""

from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import event
from sqlalchemy.orm import Session

from chembook3d.api.routes import DbSession, _investigation
from chembook3d.services import import_undo

router = APIRouter(prefix="/api")


@router.get("/history/{entry_id}/undo")
def preview_undo(entry_id: int, request: Request):
    """What undoing the import would remove and restore, and what blocks it. Nothing is
    written."""
    investigation = _investigation(request)
    session = investigation.sessions()
    try:
        result, _ = import_undo.undo_entry(session, entry_id, dry_run=True)
        return result.as_dict()
    except import_undo.UndoNotPossible as exc:
        raise HTTPException(422, str(exc)) from exc
    finally:
        session.rollback()
        session.close()


@router.post("/history/{entry_id}/undo")
def undo(entry_id: int, request: Request, session: DbSession):
    """Undo the import; refused with the blockers when something depends on it."""
    try:
        result, stored = import_undo.undo_entry(session, entry_id)
    except import_undo.UndoNotPossible as exc:
        raise HTTPException(422, str(exc)) from exc
    except import_undo.UndoBlocked as exc:
        raise HTTPException(409, {"blockers": exc.blockers, "message": str(exc)}) from exc
    folder = _investigation(request).folder

    def remove_copies(_session: Session) -> None:
        for path in stored:
            import_undo.remove_copy(folder, path)

    event.listen(session, "after_commit", remove_copies, once=True)
    return result.as_dict()
