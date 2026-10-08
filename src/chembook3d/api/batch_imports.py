"""HTTP API for importing a folder of output files at once (D97, FR-IMP-14). The rules live in
`services/batch_import.py`; these handlers only translate.

A preview is a dry run of the whole batch in a transaction that is always rolled back, so it
writes nothing; Import runs the same batch in the request's transaction."""

from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from chembook3d import settings as app_settings
from chembook3d.api.routes import DbSession, _investigation, _staging
from chembook3d.services import batch_import, imports

router = APIRouter(prefix="/api")


class BatchScanIn(BaseModel):
    folder: str
    recursive: bool = True


class RowChoiceIn(BaseModel):
    included: bool | None = None
    target: str | None = None
    duplicate_action: str | None = None
    label: str | None = None
    basis: str | None = None  # D104


class BatchOptionsIn(BaseModel):
    rows: dict[str, RowChoiceIn] = {}
    basis_names: dict[str, str] = {}
    dispersion_names: dict[str, str] = {}
    missing_basis: str | None = None  # D104: for every file that names no basis set
    origin_device: str | None = None
    suffixes: list[str] | None = None


class BatchImportedOut(BaseModel):
    imported: int
    files: list[dict]


def _options(body: BatchOptionsIn | None) -> batch_import.BatchOptions:
    if body is None:
        return batch_import.BatchOptions()
    return batch_import.BatchOptions(
        rows={k: batch_import.RowChoice(**v.model_dump()) for k, v in body.rows.items()},
        basis_names=body.basis_names,
        dispersion_names=body.dispersion_names,
        missing_basis=body.missing_basis,
        origin_device=body.origin_device,
        suffixes=body.suffixes,
    )


def _batch(request: Request, token: str) -> batch_import.Batch:
    try:
        return batch_import.get(_staging(request), token)
    except batch_import.BatchNotFound as exc:
        raise HTTPException(
            404, "This folder preview has expired; choose the folder again"
        ) from exc


def _preview(request: Request, batch: batch_import.Batch, options: batch_import.BatchOptions):
    investigation = _investigation(request)
    session = investigation.sessions()
    try:
        plan, _ = batch_import.run(session, investigation.folder, batch, options, dry_run=True)
        return plan.as_dict()
    finally:
        session.rollback()
        session.close()


@router.post("/batch-imports")
def scan_folder(body: BatchScanIn, request: Request):
    """Read every output in a folder and return the proposed import. Nothing is written."""
    investigation = _investigation(request)
    try:
        batch = batch_import.scan(
            _staging(request), Path(body.folder), body.recursive, investigation.folder
        )
    except batch_import.BatchFailed as exc:
        raise HTTPException(422, str(exc)) from exc
    app_settings.remember_import_folder(batch.folder)  # D78
    return _preview(request, batch, batch_import.BatchOptions())


@router.post("/batch-imports/{token}/preview")
def preview_batch(token: str, body: BatchOptionsIn, request: Request):
    return _preview(request, _batch(request, token), _options(body))


@router.post("/batch-imports/{token}/commit", response_model=BatchImportedOut)
def commit_batch(token: str, body: BatchOptionsIn, request: Request, session: DbSession):
    batch = _batch(request, token)
    options = _options(body)
    try:
        _, result = batch_import.run(
            session, _investigation(request).folder, batch, options, dry_run=False
        )
    except imports.ImportBlocked as exc:
        raise HTTPException(422, {"blockers": exc.blockers, "message": str(exc)}) from exc
    assert result is not None
    session.flush()
    if options.suffixes is not None:
        settings = app_settings.load()
        settings.batch_suffixes = [s.strip() for s in options.suffixes if s.strip()]
        app_settings.save(settings)
    batch_import.discard(_staging(request), token)
    return BatchImportedOut(imported=result.imported, files=result.files)


@router.delete("/batch-imports/{token}", status_code=204)
def cancel_batch(token: str, request: Request):
    """Cancelling writes nothing and drops the staged files (FR-IMP-05)."""
    batch_import.discard(_staging(request), token)
