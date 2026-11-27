"""Claude's read tools for building a mechanism from a folder (D263, FR-MCP-23): a folder of
outputs surveyed without importing it, two structures compared, and many grouped into
duplicates, conformers and isomers, and the minima a TS links searched from the TS (FR-MCP-25).
The rules live in `services/folder_survey.py`, `services/structure_compare.py` and
`services/ts_partners.py`; nothing is written.

Like `api/local_files.py`, these read files on this computer, so only the app's own page or
Claude on this computer may call them."""

import itertools
import json
import time
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from chembook3d.api.jobs import _local_page_only
from chembook3d.api.routes import DbSession, _investigation
from chembook3d.models import Node, Role
from chembook3d.services import (
    batch_import,
    folder_survey,
    irc_ends,
    node_merge,
    thermo_settings,
    ts_partners,
)
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
    ts: StructureRef | None = None
    tss: list[StructureRef] | None = None
    all_ts: Literal["all", "unlinked"] | None = None
    offset: int = Field(0, ge=0)
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


class _Partners:
    """The candidates of one call, made once for every TS at the same geometry level (A187)."""

    def __init__(self, session: Any, folder: Path, tolerance: float, body: PartnersIn):
        self.session, self.folder, self.tolerance, self.body = session, folder, tolerance, body
        self.made: dict[str | None, tuple[list, list, dict[str, int]]] = {}
        self.ends: dict[str, str] | None = None  # A192: IRC end nodes, by node: their TS

    def irc_ends_of_others(self, ts_id: str | None) -> set[str]:
        """A192: the nodes made from another TS's IRC end optimization, never its partners."""
        if self.ends is None:
            self.ends = irc_ends.end_nodes(self.session, self.folder, self.tolerance)
        return {n for n, ts in self.ends.items() if ts != ts_id}

    def find(self, ref: StructureRef) -> dict[str, Any]:
        session, folder, tolerance, body = self.session, self.folder, self.tolerance, self.body
        ts = _side(session, folder, ref, tolerance)
        level = ts_partners.ts_level(session, _ref(ref))
        key = level.id if level is not None else None
        if key not in self.made:
            candidates: list[ts_partners.Candidate] = []
            species: list[ts_partners.Candidate] = []
            skipped: dict[str, int] = {}
            if body.candidates is None or body.species is None:
                candidates, species, skipped = ts_partners.from_investigation(
                    session, folder, tolerance, None, level
                )
            if body.candidates is not None:
                candidates = [_given(session, folder, r, tolerance, level) for r in body.candidates]
                skipped = {}
            if body.species is not None:
                species = [_given(session, folder, r, tolerance, level) for r in body.species]
            self.made[key] = (candidates, species, skipped)
        candidates, species, skipped = self.made[key]
        node = session.get(Node, ref.node_id) if ref.node_id is not None else None
        if body.candidates is None:
            others = self.irc_ends_of_others(node.id if node is not None else None)
            candidates = [c for c in candidates if c.entry.get("node_id") not in others]
        if node is not None:  # never the TS itself, nor counted as left out
            candidates = [c for c in candidates if c.entry.get("node_id") != node.id]
            species = [c for c in species if c.entry.get("node_id") != node.id]
            why = ts_partners.left_out(node, tolerance) if node.geometry else None
            if why is not None and skipped.get(why):
                skipped = {**skipped, why: skipped[why] - 1}
                skipped = {k: v for k, v in skipped.items() if v}
        return ts_partners.find(ts, candidates, species, tolerance, body.top, level, skipped)


@router.post("/structures/ts-partners")
def find_ts_partners(body: PartnersIn, request: Request, session: DbSession) -> dict[str, Any]:
    """D263c, A184, A186: the minima on each side of a TS's imaginary mode, those at the TS's
    geometry level and nearest first, and A184's bond check of the best pair. A187: several
    TSs (`tss`, or `all_ts`) in one call, a few lines each. Changes nothing."""
    _local_page_only(request)
    folder = _investigation(request).folder
    tolerance = thermo_settings.settings(session).duplicate_tolerance
    named = [body.ts is not None, body.tss is not None, body.all_ts is not None]
    if sum(named) != 1:
        raise HTTPException(422, "Name the TS (ts), several (tss), or all_ts: all or unlinked")
    search = _Partners(session, folder, tolerance, body)
    if body.ts is not None:
        try:
            return search.find(body.ts)
        except (compare.StructureError, RecordError) as exc:
            raise _refused(exc) from exc
    if body.tss is not None:
        refs = body.tss
    else:
        chosen = ts_partners.all_ts if body.all_ts == "all" else ts_partners.unlinked
        refs = [StructureRef(node_id=n.id) for n in chosen(session)]
    started = time.perf_counter()
    blocks: list[dict[str, Any]] = []
    size = 0
    for ref in refs[body.offset :]:
        try:
            block = ts_partners.brief(search.find(ref))
        except (compare.StructureError, RecordError) as exc:
            node = session.get(Node, ref.node_id) if ref.node_id else None
            label = ref.label or (node.label if node is not None else None) or "the TS"
            block = {"ts": label, "verdict": "error", "why": str(exc)}
        if ref.node_id is not None:
            block = {"ts": block.pop("ts"), "node_id": ref.node_id, **block}
        size += len(json.dumps(block, ensure_ascii=False)) + 2
        if blocks and size > ts_partners.MAX_ANSWER:
            break
        blocks.append(block)
    return ts_partners.batch(blocks, len(refs), body.offset, time.perf_counter() - started)


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
        found = compare.group(structures, tolerance, body.count_weak)
    except (compare.StructureError, RecordError) as exc:
        raise _refused(exc) from exc
    _name_nodes(session, found, refs)
    return found


def _name_nodes(session: Any, found: dict[str, Any], refs: list[dict[str, Any]]) -> None:
    """A193: each member that is a node carries its id (a TS marked), so the conformer groups
    can be proposed from the answer at once, before any linking."""
    for formula in found["formulas"]:
        for molecule in formula["molecules"]:
            for conformer in molecule["conformers"]:
                nodes = []
                for member in conformer["members"]:
                    node_id = refs[member["index"] - 1].get("node_id")
                    node = session.get(Node, node_id) if node_id else None
                    if node is None:
                        continue
                    member["node_id"] = node.id
                    if node.role == Role.TRANSITION_STATE:
                        member["ts"] = True
                    nodes.append((member["index"], node))
                # A194: members that each hold an optimization at one level are separate runs
                # of one structure (the import keeps them as two nodes, D246): never merged.
                ran: set[int] = set()
                for (i, a), (j, b) in itertools.combinations(nodes, 2):
                    if node_merge.runs(a, b):
                        ran.update((i, j))
                if ran:
                    conformer["runs"] = sorted(ran)
    found["next"] = (
        "Make the conformer groups now, before linking (the linking tools treat a group as "
        "one structure): one propose_mechanism with only groups, each a molecule's minima "
        "(members without ts) as member_ids, when it has more than one. Members of one "
        "conformer are within the duplicate tolerance; merge one into another only when it "
        "holds no optimization at a level the other has (a single point or frequency job on a "
        "node of its own). Members in a conformer's `runs` each hold an optimization at one "
        "level: separate runs of one structure, kept as separate nodes in the conformer group "
        "and never merged (A194)."
    )
