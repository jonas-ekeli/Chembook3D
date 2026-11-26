"""Claude's read tools for building a mechanism from a folder (D263, FR-MCP-23): a folder of
outputs surveyed without importing it, two structures compared, and many grouped into
duplicates, conformers and isomers, and the minima a TS links searched from the TS (FR-MCP-25).
The rules live in `services/folder_survey.py`, `services/structure_compare.py` and
`services/ts_partners.py`; nothing is written.

Like `api/local_files.py`, these read files on this computer, so only the app's own page or
Claude on this computer may call them."""

from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from chembook3d.api.jobs import _local_page_only
from chembook3d.api.routes import DbSession, _investigation
from chembook3d.models import Node
from chembook3d.services import batch_import, folder_survey, thermo_settings, ts_partners
from chembook3d.services import nodes as node_service
from chembook3d.services import structure_compare as compare
from chembook3d.services.records import RecordError

router = APIRouter(prefix="/api")


class StructureRef(BaseModel):
    """One structure: name exactly one of node_id, calculation_id, path or xyz."""

    node_id: str | None = Field(None, description="A node's or free species' structure.")
    calculation_id: str | None = Field(
        None,
        description="A calculation's structure (its final geometry, or with frame or "
        "irc_end one read from its file).",
    )
    path: str | None = Field(
        None, description="Full path of an output (or xyz) file on this computer."
    )
    step: int | None = Field(
        None,
        description="With path: the job step, as summarize_file numbers them (default: "
        "the last with structures).",
    )
    frame: int | None = Field(
        None,
        description="With path or calculation_id: the structure in the step, from 1; -1 "
        "or left out is the last.",
    )
    irc_end: Literal["forward", "reverse"] | None = Field(
        None, description="With path or calculation_id of an IRC: the last point of that direction."
    )
    xyz: str | None = Field(None, description="XYZ text.")
    label: str | None = Field(None, description="A name for it in the answer.")
    charge: int | None = Field(None, description="Its charge, when the source does not say.")
    multiplicity: int | None = Field(
        None, description="Its multiplicity, when the source does not say."
    )


Side = StructureRef | list[StructureRef]


class CompareIn(BaseModel):
    a: Side
    b: Side
    ts: StructureRef | None = None
    count_weak: bool = False


class PartnersIn(BaseModel):
    ts: StructureRef
    candidates: list[Side] | None = None
    species: list[StructureRef] | None = None
    top: int = Field(ts_partners.TOP, ge=1, le=ts_partners.MAX_TOP)


class GroupIn(BaseModel):
    structures: list[StructureRef] = []
    all_nodes: bool = False
    count_weak: bool = False


def _ref(ref: StructureRef) -> dict[str, Any]:
    return ref.model_dump(exclude_none=True)


def _side(session: Any, folder: Path, ref: Side, tolerance: float) -> compare.Structure:
    given = [_ref(r) for r in ref] if isinstance(ref, list) else _ref(ref)
    return compare.side(session, folder, given, tolerance)


def _refused(exc: Exception) -> HTTPException:
    return HTTPException(422, str(exc))


@router.get("/folder-survey")
def survey_folder(
    request: Request, session: DbSession, path: str, recursive: bool = True
) -> dict[str, Any]:
    """D263: every output in a folder on this computer, per file and job step, and how the
    files relate to each other and to the nodes. Nothing is imported or staged."""
    _local_page_only(request)
    investigation = _investigation(request)
    try:
        return folder_survey.survey(session, Path(path), recursive, investigation.folder)
    except batch_import.BatchFailed as exc:
        raise _refused(exc) from exc


@router.post("/structures/compare")
def compare_structures(body: CompareIn, request: Request, session: DbSession) -> dict[str, Any]:
    """D263: B matched onto A's numbering and what changes between them; with a TS, whether
    the changes lie along its imaginary mode (A184)."""
    _local_page_only(request)
    folder = _investigation(request).folder
    tolerance = thermo_settings.settings(session).duplicate_tolerance
    try:
        a = _side(session, folder, body.a, tolerance)
        b = _side(session, folder, body.b, tolerance)
        ts = _side(session, folder, body.ts, tolerance) if body.ts is not None else None
        return compare.compare(a, b, ts, body.count_weak, tolerance)
    except (compare.StructureError, RecordError) as exc:
        raise _refused(exc) from exc


def _given(
    session: Any, folder: Path, ref: Side, tolerance: float, level: ts_partners.Level | None
) -> ts_partners.Candidate:
    """A structure the caller named, with the reference to pass on (not XYZ text); a node
    with the geometry levels it holds (A186)."""
    if isinstance(ref, StructureRef) and set(_ref(ref)) == {"node_id"}:
        node = session.get(Node, ref.node_id)
        if node is not None and node.geometry:
            return ts_partners.node_candidate(session, folder, node, tolerance, level)
    structure = _side(session, folder, ref, tolerance)
    refs = [_ref(r) for r in ref] if isinstance(ref, list) else [_ref(ref)]
    entry: dict[str, Any] = {"label": structure.label}
    if not any("xyz" in r for r in refs):
        entry["ref"] = refs if isinstance(ref, list) else refs[0]
    if refs[0].get("node_id"):
        entry["node_id"] = refs[0]["node_id"]
    return ts_partners.Candidate(structure, entry)


@router.post("/structures/ts-partners")
def find_ts_partners(body: PartnersIn, request: Request, session: DbSession) -> dict[str, Any]:
    """D263c, A184, A186: the minima on each side of a TS's imaginary mode, those at the TS's
    geometry level and nearest first, and A184's bond check of the best pair. Changes
    nothing."""
    _local_page_only(request)
    folder = _investigation(request).folder
    tolerance = thermo_settings.settings(session).duplicate_tolerance
    try:
        ts = _side(session, folder, body.ts, tolerance)
        level = ts_partners.ts_level(session, _ref(body.ts))
        candidates: list[ts_partners.Candidate] = []
        species: list[ts_partners.Candidate] = []
        skipped: dict[str, int] = {}
        if body.candidates is None or body.species is None:
            candidates, species, skipped = ts_partners.from_investigation(
                session, folder, tolerance, body.ts.node_id, level
            )
        if body.candidates is not None:
            candidates = [_given(session, folder, r, tolerance, level) for r in body.candidates]
            skipped = {}
        if body.species is not None:
            species = [_given(session, folder, r, tolerance, level) for r in body.species]
        return ts_partners.find(ts, candidates, species, tolerance, body.top, level, skipped)
    except (compare.StructureError, RecordError) as exc:
        raise _refused(exc) from exc


@router.post("/structures/group")
def group_structures(body: GroupIn, request: Request, session: DbSession) -> dict[str, Any]:
    """D263: structures grouped into isomers, conformers and duplicates, whatever their atom
    order."""
    _local_page_only(request)
    folder = _investigation(request).folder
    tolerance = thermo_settings.settings(session).duplicate_tolerance
    refs = [_ref(r) for r in body.structures]
    if body.all_nodes:
        named = {r.get("node_id") for r in refs}
        refs += [
            {"node_id": n.id}
            for n in node_service.list_nodes(session)
            if n.geometry and n.id not in named
        ]
    if not refs:
        raise HTTPException(422, "Name the structures to group, or ask for all nodes")
    if len(refs) > compare.MAX_GROUP:
        raise HTTPException(
            422,
            f"{len(refs)} structures are more than the {compare.MAX_GROUP} grouped at once; "
            "group fewer",
        )
    try:
        structures = [compare.resolve(session, folder, ref, tolerance) for ref in refs]
        return compare.group(structures, tolerance, body.count_weak)
    except (compare.StructureError, RecordError) as exc:
        raise _refused(exc) from exc
