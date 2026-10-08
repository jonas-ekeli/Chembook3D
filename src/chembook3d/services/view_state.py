"""What an investigation showed when it was last used (D105): the energy view (composite level,
energy type, reference), the canvas filters, the expanded groups and the energy drawer (open,
tab, pathways). Saved in the investigation, so it travels with the folder and its sync, but it
is view only: never in the history, and saving it is not a change other tabs reload for.

What is saved is cleaned twice: on the way in (only known keys and plausible values, so an
investigation cannot fill up with junk) and on the way out (records deleted since are dropped,
so the notebook opens on what still exists). App-wide preferences (energy unit, G_qh
temperature and cutoff, standard state, hydrogens, steric colours) stay in the app's settings."""

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from chembook3d.models import (
    Branch,
    GroupNode,
    InvestigationInfo,
    Node,
    NodeKind,
    ReactionStep,
    Status,
)
from chembook3d.services.energies import ENERGY_TYPES

TABS = ("profile", "table")
MAX_IDS = 2000  # a generous ceiling on any one list
MAX_PATHS = 50
NONE = "none"  # the filters' "no branch" / "no step" (canvasView.ts)


def _ids(value: Any, limit: int = MAX_IDS) -> list[str]:
    if not isinstance(value, list):
        return []
    return [v for v in value if isinstance(v, str) and 0 < len(v) <= 64][:limit]


def _text(value: Any) -> str | None:
    return value if isinstance(value, str) and 0 < len(value) <= 200 else None


def clean(data: Any) -> dict[str, Any]:
    """The parts of `data` that make sense as a view state; anything else is dropped."""
    data = data if isinstance(data, dict) else {}
    filters = data.get("filters") if isinstance(data.get("filters"), dict) else {}
    drawer = data.get("drawer") if isinstance(data.get("drawer"), dict) else {}
    paths = []
    for path in (
        drawer.get("paths", [])[:MAX_PATHS] if isinstance(drawer.get("paths"), list) else []
    ):
        if not isinstance(path, dict) or not _ids(path.get("ids")):
            continue
        choices = path.get("choices") if isinstance(path.get("choices"), list) else []
        paths.append(
            {
                "ids": _ids(path.get("ids")),
                "branch_id": _text(path.get("branch_id")),
                "choices": [
                    {"node_id": c["node_id"]}
                    for c in choices[:100]
                    if isinstance(c, dict) and _text(c.get("node_id"))
                ],
            }
        )
    energy_type = data.get("energy_type")
    return {
        "level": _text(data.get("level")),
        "energy_type": energy_type if energy_type in ENERGY_TYPES else None,
        "reference_id": _text(data.get("reference_id")),
        "edge_energies": data.get("edge_energies") is not False,
        "filters": {key: _ids(filters.get(key)) for key in ("branches", "statuses", "steps")},
        "expanded": _ids(data.get("expanded")),
        "drawer": {
            "open": drawer.get("open") is True,
            "tab": drawer.get("tab") if drawer.get("tab") in TABS else "profile",
            "paths": paths,
        },
    }


def save(session: Session, data: Any) -> None:
    info = session.get(InvestigationInfo, 1)
    if info is not None:
        info.view_state = clean(data)


def load(session: Session) -> dict[str, Any]:
    """The saved view, without what has been deleted since. A reference, a group, a branch or
    a step that is gone is dropped, and so is a pathway through a deleted node; a choice at a
    pathway's fork is given its current label and status."""
    info = session.get(InvestigationInfo, 1)
    state = clean(info.view_state if info else {})
    nodes = {n.id: n for n in session.scalars(select(Node).where(Node.kind == NodeKind.NODE))}
    groups = {g.id: g for g in session.scalars(select(GroupNode))}
    branches = set(session.scalars(select(Branch.id)))
    steps = set(session.scalars(select(ReactionStep.id)))
    on_canvas = nodes.keys() | groups.keys()

    def choice(record_id: str) -> dict[str, str]:
        if record_id in nodes:
            node = nodes[record_id]
            return {
                "node_id": record_id,
                "label": node.label or "Untitled node",
                "status": node.status,
            }
        return {"node_id": record_id, "label": groups[record_id].label or "Group", "status": ""}

    if state["reference_id"] not in on_canvas:
        state["reference_id"] = None
    state["expanded"] = [g for g in state["expanded"] if g in groups]
    filters = state["filters"]
    filters["branches"] = [b for b in filters["branches"] if b in branches or b == NONE]
    filters["steps"] = [s for s in filters["steps"] if s in steps or s == NONE]
    filters["statuses"] = [s for s in filters["statuses"] if s in set(Status)]
    paths = []
    for path in state["drawer"]["paths"]:
        if not all(i in on_canvas for i in path["ids"]):
            continue
        if path["branch_id"] not in branches:
            path["branch_id"] = None
        path["choices"] = [
            choice(c["node_id"]) for c in path["choices"] if c["node_id"] in on_canvas
        ]
        paths.append(path)
    state["drawer"]["paths"] = paths
    return state
