"""Claude reads an output on this computer without importing it (D225, FR-MCP-16): what the
Inspect window reads from a file the user named, and one of its structures, as the tools
`summarize_file` and `get_file_structure`. The same reading as for a file on a server
(`api/server_files.py`, D124); nothing is staged, copied or written.

Only files the parsers read are answered (Gaussian, ORCA, xTB and CREST outputs, xyz files);
any other file is refused, so this is no way to read arbitrary files."""

import asyncio
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel

from chembook3d.api.jobs import _local_page_only
from chembook3d.api.server_files import _size, _step_summary, _when, structure_text
from chembook3d.services import inspection

router = APIRouter(prefix="/api/local-files")


def _read(path: str) -> tuple[Path, inspection.Inspection]:
    try:
        local = Path(path).expanduser().resolve()
    except OSError as exc:
        raise HTTPException(422, f"Cannot read {path}") from exc
    if not local.is_file():
        raise HTTPException(422, f"{local} is not a file")
    size = local.stat().st_size
    if size > inspection.MAX_SIZE:
        raise HTTPException(422, f"{local} is {_size(size)}, more than the Inspect window reads.")
    try:
        return local, inspection.read_path(local, local.name)
    except inspection.InspectFailed as exc:
        raise HTTPException(422, str(exc)) from exc


class FileSummary(BaseModel):
    path: str
    size: int
    modified: str
    program: str
    version: str | None
    warnings: list[str]
    units: str
    steps: list[dict]


@router.get("/summary")
async def summarize_file(request: Request, path: str) -> FileSummary:
    """An output on this computer read as the Inspect window reads it (D123c): per job step
    the route, charge and multiplicity, termination, last energy, convergence, frequencies,
    and per structure its energy and convergence criteria. Nothing is imported."""
    _local_page_only(request)
    local, read = await asyncio.to_thread(_read, path)
    stat = local.stat()
    return FileSummary(
        path=str(local),
        size=stat.st_size,
        modified=_when(int(stat.st_mtime)),
        program=read.program,
        version=read.version,
        warnings=read.warnings,
        units="energies in hartree; frequencies in cm-1; criteria as [value, threshold, met]",
        steps=[_step_summary(step, "get_file_structure") for step in read.steps],
    )


@router.get("/structure", response_class=PlainTextResponse)
async def get_file_structure(request: Request, path: str, step: int | None = None, frame: int = -1):
    """One structure of an output or xyz file on this computer as XYZ text: by default the
    last of the last step that has structures."""
    _local_page_only(request)
    local, read = await asyncio.to_thread(_read, path)
    return PlainTextResponse(structure_text(read, str(local), step, frame))
