"""Calculation jobs handed to Claude Code cloud sessions (D93, FR-CLOUD); the rules are in
`cloud_jobs.py`. Nothing here changes the investigation's database.

Creating, starting and fetching are refused from a web page of another origin: a browser
sends its page's origin with every such request, Claude's MCP server sends none, and the app's
own page is on a loopback address.
"""

from typing import Annotated, Any
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from chembook3d import cloud_jobs
from chembook3d.api.routes import DbSession, _investigation
from chembook3d.models import Node
from chembook3d.services import nodes as node_service
from chembook3d.services import scan_path
from chembook3d.services.records import get

router = APIRouter(prefix="/api/jobs")

LOOPBACK = {"127.0.0.1", "localhost", "::1"}


def launcher(request: Request) -> cloud_jobs.Launcher:
    return request.app.state.cloud_jobs


def _local_page_only(request: Request) -> None:
    origin = request.headers.get("origin")
    if origin is not None and urlsplit(origin).hostname not in LOOPBACK:
        raise HTTPException(403, "Only the app itself or Claude on this computer can do that")


def _refused(exc: cloud_jobs.CloudJobError) -> HTTPException:
    if isinstance(exc, cloud_jobs.JobNotFound):
        return HTTPException(404, str(exc))
    return HTTPException(422, str(exc))


class NodeInput(BaseModel):
    node_id: str
    file: str | None = Field(
        None, description="File name in inputs/ (default: the node's label, .xyz)"
    )


class FileInput(BaseModel):
    name: str
    content: str


class JobIn(BaseModel):
    name: str = Field(description="A short name; it goes into the job's folder name.")
    instructions: str = Field(
        description="What to run and return: program (xtb or crest), method (e.g. GFN2-xTB), "
        "job type, solvent, constraints, settings, and which files to bring back."
    )
    nodes: list[NodeInput] = Field(
        default_factory=list, description="Nodes whose coordinates go in as XYZ inputs."
    )
    files: list[FileInput] = Field(
        default_factory=list,
        description="Other input text files (e.g. an xcontrol file with constraints).",
    )


def _node_input(session, item: NodeInput) -> cloud_jobs.InputFile:
    node = get(session, Node, item.node_id, "Node")
    if not node_service.atoms_of(node):
        raise HTTPException(422, f'Node "{node.label}" has no coordinates')
    atoms = len(node_service.atoms_of(node))
    name = item.file or f"{cloud_jobs.slug(node.label, 60)}.xyz"
    return cloud_jobs.InputFile(
        name=name,
        text=node_service.to_xyz(node),
        description=f'node "{node.label}", {atoms} atoms, '
        + ", ".join(
            f"{what} {value}" if value is not None else f"{what} not recorded (see below)"
            for what, value in (("charge", node.charge), ("multiplicity", node.multiplicity))
        ),
        node_id=node.id,
    )


@router.get("")
def list_jobs(
    request: Request, session: DbSession, detail: bool = False, refresh: bool = False
) -> list[dict[str, Any]]:
    """The jobs as recorded; with `detail`, each with where it stands, after one fetch from
    GitHub with `refresh`, and for an imported scan path whether its node is still there
    (D115)."""
    folder = _investigation(request).folder
    jobs = cloud_jobs.list_jobs(folder)
    if not detail:
        return jobs
    error = None
    if refresh and any(j.get("state") != "draft" for j in jobs):
        error = cloud_jobs.fetch_origin(folder)
    out = [cloud_jobs.job_status(folder, j["id"], launcher(request), fetch=False) for j in jobs]
    for job in out:
        job["fetch_error"] = error
        if job.get("imported"):
            job["imported"]["present"] = session.get(Node, job["imported"]["node_id"]) is not None
    return out


@router.post("")
def create_job(body: JobIn, request: Request, session: DbSession) -> dict[str, Any]:
    _local_page_only(request)
    investigation = _investigation(request)
    inputs = [_node_input(session, item) for item in body.nodes]
    inputs += [
        cloud_jobs.InputFile(name=f.name, text=f.content, description="text file")
        for f in body.files
    ]
    try:
        record = cloud_jobs.create_job(
            investigation.folder, body.name, body.instructions, inputs, investigation.name
        )
    except cloud_jobs.CloudJobError as exc:
        raise _refused(exc) from exc
    return cloud_jobs.job_status(investigation.folder, record["id"], launcher(request), fetch=False)


@router.get("/{job_id}")
def job_status(
    job_id: str,
    request: Request,
    refresh: bool = True,
    wait: Annotated[float, Query(ge=0, le=cloud_jobs.MAX_WAIT)] = 0,
) -> dict[str, Any]:
    folder = _investigation(request).folder
    try:
        return cloud_jobs.job_status(folder, job_id, launcher(request), fetch=refresh, wait=wait)
    except cloud_jobs.CloudJobError as exc:
        raise _refused(exc) from exc


@router.post("/{job_id}/start")
def start_job(job_id: str, request: Request) -> dict[str, Any]:
    _local_page_only(request)
    folder = _investigation(request).folder
    try:
        return cloud_jobs.start_job(folder, job_id, launcher(request))
    except cloud_jobs.CloudJobError as exc:
        raise _refused(exc) from exc


@router.post("/{job_id}/fetch")
def fetch_results(job_id: str, request: Request) -> dict[str, Any]:
    _local_page_only(request)
    folder = _investigation(request).folder
    try:
        return cloud_jobs.fetch_results(folder, job_id)
    except cloud_jobs.CloudJobError as exc:
        raise _refused(exc) from exc


class MessageIn(BaseModel):
    text: str = Field(description="What to tell the job's cloud session, as its user would.")


@router.post("/{job_id}/message")
def message_session(job_id: str, body: MessageIn, request: Request) -> dict[str, Any]:
    _local_page_only(request)
    folder = _investigation(request).folder
    try:
        return cloud_jobs.send_message(folder, job_id, body.text)
    except cloud_jobs.CloudJobError as exc:
        raise _refused(exc) from exc


@router.post("/{job_id}/import-path")
def import_path(
    job_id: str, request: Request, session: DbSession, again: bool = False
) -> dict[str, Any]:
    """D115: a scan path job's result imported as a new node between its ends; `again` once
    more after its node was removed."""
    _local_page_only(request)
    folder = _investigation(request).folder
    try:
        return scan_path.import_result(session, folder, job_id, again=again)
    except cloud_jobs.CloudJobError as exc:
        raise _refused(exc) from exc
