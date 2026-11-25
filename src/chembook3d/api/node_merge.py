"""HTTP API for merging two nodes (D262, FR-NODE-09). The rules live in
`services/node_merge.py`; these handlers only translate. The preview changes nothing; the merge
is one write with one history entry."""

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from chembook3d.api.routes import DbSession, _investigation
from chembook3d.services import node_merge

router = APIRouter(prefix="/api")


class MergeOptionsIn(BaseModel):
    pairs: list[list[int]] = Field(
        default_factory=list,
        description="Atom pairs fixed by hand, 1-based [atom of the node kept, atom of the node "
        "merged], as in Match atoms",
    )
    branch: str = Field(
        "none",
        description="When the two are on different branches: 'none' (shared by both, D67), "
        "'keep' (the kept node's) or 'take' (the merged node's)",
    )
    anyway: bool = Field(False, description="Merge despite the warnings")


class MergePreviewIn(MergeOptionsIn):
    into_id: str = Field(description="The node that stays (B); the other dissolves into it")


def _options(body: MergeOptionsIn) -> node_merge.Options:
    return node_merge.Options(pairs=body.pairs, branch=body.branch, anyway=body.anyway)


@router.post("/nodes/{node_id}/merge-preview")
def preview_merge(
    node_id: str, body: MergePreviewIn, request: Request, session: DbSession
) -> dict[str, Any]:
    """What merging this node into `into_id` would do: why it is refused, what it warns about,
    the atom match, both nodes' values at each shared level and which count, the geometry the
    kept node shows, the calculations, edges and records that move. Changes nothing."""
    folder = _investigation(request).folder
    return node_merge.plan(session, node_id, body.into_id, _options(body), folder).as_dict()


@router.post("/nodes/{node_id}/merge/{into_id}")
def merge(
    node_id: str, into_id: str, body: MergeOptionsIn, request: Request, session: DbSession
) -> dict[str, Any]:
    """Merge this node into `into_id`: it dissolves, its calculations, edges and records move
    onto the other (D262). 409 with the reasons when refused, or warned without `anyway`."""
    folder = _investigation(request).folder
    found = node_merge.plan(session, node_id, into_id, _options(body), folder)
    if found.blocked:
        raise HTTPException(
            409, {"refusals": found.refusals, "warnings": found.warnings, "merge": found.as_dict()}
        )
    summary = found.as_dict()
    entry = node_merge.apply(session, found)
    return {**summary, "entry_id": entry.id}
