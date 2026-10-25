"""What an investigation showed when it was last used (D105): the energy view (composite level,
energy type, reference), the canvas filters, the expanded groups and the energy drawer (open,
tab, the saved energy profile it shows, D190). Saved in the investigation, so it travels with
the folder and its sync, but it is view only: never in the history, and saving it is not a
change other tabs reload for.

What is saved is cleaned twice: on the way in (only known keys and plausible values, so an
investigation cannot fill up with junk) and on the way out (records deleted since are dropped,
so the notebook opens on what still exists). App-wide preferences (energy unit, hydrogens,
steric colours) stay in the app's settings; the G_qh temperature and cutoff and the standard
state are the investigation's own, in its history (D159, services/thermo_settings.py)."""

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from chembook3d.models import (
    Branch,
    EnergyProfile,
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
            # D190: the saved energy profile the drawer has open (its pathways are its own).
            "profile_id": _text(drawer.get("profile_id")),
        },
    }


def save(session: Session, data: Any) -> None:
    info = session.get(InvestigationInfo, 1)
    if info is not None:
        info.view_state = clean(data)


def load(session: Session) -> dict[str, Any]:
    """The saved view, without what has been deleted since."""
    info = session.get(InvestigationInfo, 1)
    return current(session, clean(info.view_state if info else {}))


def current(session: Session, state: dict[str, Any]) -> dict[str, Any]:
    """A cleaned view without the records that no longer exist. A reference, a group, a branch
    or a step that is gone is dropped, and so is a deleted energy profile (D190)."""
    nodes = {n.id: n for n in session.scalars(select(Node).where(Node.kind == NodeKind.NODE))}
    groups = {g.id: g for g in session.scalars(select(GroupNode))}
    branches = set(session.scalars(select(Branch.id)))
    steps = set(session.scalars(select(ReactionStep.id)))
    profiles = set(session.scalars(select(EnergyProfile.id)))
    on_canvas = nodes.keys() | groups.keys()
    if state["reference_id"] not in on_canvas:
        state["reference_id"] = None
    state["expanded"] = [g for g in state["expanded"] if g in groups]
    filters = state["filters"]
    filters["branches"] = [b for b in filters["branches"] if b in branches or b == NONE]
    filters["steps"] = [s for s in filters["steps"] if s in steps or s == NONE]
    filters["statuses"] = [s for s in filters["statuses"] if s in set(Status)]
    if state["drawer"]["profile_id"] not in profiles:
        state["drawer"]["profile_id"] = None
    return state
