"""Saved energy profiles (D190, FR-EN-19, A127): named figures of pathways that the energy
drawer opens, one at a time. Each pathway keeps its node and group ids, the branch it was added
for, whether it is drawn ("Show on the figure"; any number may be drawn together) and the steps
moved along the axis (D194: each a step's index and the columns it, with every later step, is
moved; only later than where the pathway puts it, so moving back stops there) and the colour
the user chose for it (D195: "#rrggbb", or None for its branch's colour). A profile also keeps
its own reference (D199), one node or group on its pathways that all of them are relative to,
whatever the node cards' reference is; it stays when pathways are added or hidden. The
"Continue with" choices are never stored: the drawer asks `pathways.extend(..., follow=False)`
for them (D189).

Like alignment sets and steric profiles, profiles are layout-like data: saved in the
investigation but not in the history, and saving one is not a change other tabs reload for
(the drawer saves on every step). A pathway through a node deleted since is dropped when the
profiles are read, as the remembered view drops it (D105)."""

import re
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from chembook3d.models import Branch, EnergyProfile, GroupNode, InvestigationInfo, Node, NodeKind
from chembook3d.services.records import RecordError, get, text_value

MAX_PATHS = 50  # as many as the drawer remembered before profiles (D105)
MAX_IDS = 500  # nodes along one pathway
MAX_SHIFT = 20  # columns one step may be moved (D194)
COLOUR = re.compile(r"#[0-9a-fA-F]{6}")  # as the browser's colour field gives it (D195)
FIRST_NAME = "Profile"


def _ids(value: Any) -> list[str]:
    if not isinstance(value, list):
        raise RecordError("a pathway's ids must be a list")
    if not 1 <= len(value) <= MAX_IDS:
        raise RecordError(f"a pathway has 1 to {MAX_IDS} nodes")
    if not all(isinstance(v, str) and 0 < len(v) <= 64 for v in value):
        raise RecordError("a pathway's ids must be record ids")
    return list(value)


def _whole(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def clean_shifts(value: Any, length: int) -> list[dict[str, int]]:
    """D194: the steps moved along the axis, by index, each once, in order along the pathway."""
    if not isinstance(value, list):
        raise RecordError("a pathway's shifts must be a list")
    found: dict[int, int] = {}
    for shift in value:
        if not (isinstance(shift, dict) and _whole(shift.get("at")) and _whole(shift.get("by"))):
            raise RecordError("each shift must be {at: a step's index, by: whole columns}")
        at, by = shift["at"], shift["by"]
        if not 0 <= at < length:
            raise RecordError("a shift's step must be on the pathway")
        if not 0 <= by <= MAX_SHIFT:
            raise RecordError(f"a step is moved 0 to {MAX_SHIFT} columns later")
        if at in found:
            raise RecordError("a step is moved once")
        if by:
            found[at] = by
    return [{"at": at, "by": found[at]} for at in sorted(found)]


def clean_colour(value: Any) -> str | None:
    """D195: the colour the user chose for a pathway, in lower case; None: its branch's."""
    if value is None:
        return None
    if not (isinstance(value, str) and COLOUR.fullmatch(value)):
        raise RecordError("a pathway's colour must be #rrggbb or null")
    return value.lower()


def clean_paths(value: Any) -> list[dict[str, Any]]:
    """The pathways as stored: ids, branch_id, shown, shifts and colour, nothing else."""
    if not isinstance(value, list):
        raise RecordError("paths must be a list")
    if len(value) > MAX_PATHS:
        raise RecordError(f"a profile holds at most {MAX_PATHS} pathways")
    paths = []
    for path in value:
        if not isinstance(path, dict):
            raise RecordError("each pathway must be an object")
        branch_id = path.get("branch_id")
        if branch_id is not None and not (isinstance(branch_id, str) and len(branch_id) <= 64):
            raise RecordError("a pathway's branch_id must be a record id")
        shown = path.get("shown", True)
        if not isinstance(shown, bool):
            raise RecordError("a pathway's shown must be true or false")
        ids = _ids(path.get("ids"))
        shifts = clean_shifts(path.get("shifts", []), len(ids))
        colour = clean_colour(path.get("colour"))
        paths.append(
            {"ids": ids, "branch_id": branch_id, "shown": shown, "shifts": shifts, "colour": colour}
        )
    return paths


def current_paths(session: Session, paths: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """`paths` without those through a deleted node; a deleted branch is forgotten."""
    nodes = set(session.scalars(select(Node.id).where(Node.kind == NodeKind.NODE)))
    on_canvas = nodes | set(session.scalars(select(GroupNode.id)))
    branches = set(session.scalars(select(Branch.id)))
    kept = []
    for path in paths:
        if not all(i in on_canvas for i in path["ids"]):
            continue
        branch_id = path["branch_id"] if path["branch_id"] in branches else None
        # Stored before D194: no step moved; before D195: the branch's colour.
        kept.append({"shifts": [], "colour": None, **path, "branch_id": branch_id})
    return kept


def _on(paths: list[dict[str, Any]]) -> set[str]:
    return {i for path in paths for i in path["ids"]}


def clean_reference(value: Any, paths: list[dict[str, Any]]) -> str | None:
    """D199: the profile's reference, a node or group on one of its pathways, or None."""
    if value is None:
        return None
    if not (isinstance(value, str) and 0 < len(value) <= 64):
        raise RecordError("a profile's reference_id must be a record id or null")
    if value not in _on(paths):
        raise RecordError("a profile's reference must be on one of its pathways")
    return value


def current_reference(profile: EnergyProfile, paths: list[dict[str, Any]]) -> str | None:
    """The profile's reference as read: None once it is on none of its `current_paths`."""
    return profile.reference_id if profile.reference_id in _on(paths) else None


def list_profiles(session: Session) -> list[EnergyProfile]:
    return list(session.scalars(select(EnergyProfile).order_by(EnergyProfile.seq)))


def _taken(session: Session, own_id: str | None = None) -> set[str]:
    query = select(func.lower(EnergyProfile.name))
    if own_id is not None:
        query = query.where(EnergyProfile.id != own_id)
    return set(session.scalars(query))


def _name(session: Session, value: Any, own_id: str | None = None) -> str:
    name = text_value("name", value).strip()
    if not name:
        raise RecordError("give the profile a name")
    if len(name) > 200:
        raise RecordError("a profile's name has at most 200 characters")
    if name.lower() in _taken(session, own_id):
        raise RecordError(f"there is already a profile named “{name}”")
    return name


def _free_name(session: Session) -> str:
    """The first "Profile N" not taken."""
    taken = _taken(session)
    n = 1
    while f"{FIRST_NAME} {n}".lower() in taken:
        n += 1
    return f"{FIRST_NAME} {n}"


def _added(session: Session, profile: EnergyProfile) -> EnergyProfile:
    """`profile` stored; a name another request took meanwhile is refused, not a crash."""
    session.add(profile)
    try:
        session.flush()
    except IntegrityError as exc:
        raise RecordError(f"there is already a profile named “{profile.name}”") from exc
    session.refresh(profile)
    return profile


def create(
    session: Session, name: Any = None, paths: Any = None, reference_id: Any = None
) -> EnergyProfile:
    """A new profile; without a name the first free "Profile N"."""
    cleaned = clean_paths(paths if paths is not None else [])
    profile = EnergyProfile(
        name=_free_name(session) if name is None else _name(session, name),
        paths=cleaned,
        reference_id=clean_reference(reference_id, cleaned),
    )
    return _added(session, profile)


def update(session: Session, profile_id: str, changes: dict[str, Any]) -> EnergyProfile:
    profile = get(session, EnergyProfile, profile_id, "Energy profile")
    if "name" in changes:
        profile.name = _name(session, changes["name"], profile.id)
    if "paths" in changes:
        profile.paths = clean_paths(changes["paths"])
    if "reference_id" in changes:
        profile.reference_id = clean_reference(changes["reference_id"], profile.paths)
    elif profile.reference_id not in _on(profile.paths):
        profile.reference_id = None  # its last pathway through the reference was taken out
    session.flush()
    return profile


def duplicate(session: Session, profile_id: str) -> EnergyProfile:
    """A copy named "<name> (copy)", or "(copy 2)" and so on when that is taken."""
    original = get(session, EnergyProfile, profile_id, "Energy profile")
    taken = _taken(session)
    name = f"{original.name} (copy)"
    n = 2
    while name.lower() in taken:
        name = f"{original.name} (copy {n})"
        n += 1
    copy = EnergyProfile(
        name=name[:200],
        paths=[dict(p) for p in original.paths],
        reference_id=original.reference_id,
    )
    return _added(session, copy)


def delete(session: Session, profile_id: str) -> None:
    session.delete(get(session, EnergyProfile, profile_id, "Energy profile"))
    session.flush()


def from_drawer(session: Session) -> int:
    """The repair `energy_profiles`: the pathways the drawer held before profiles existed
    become "Profile 1", opened in the drawer; nothing is made when it held none."""
    info = session.get(InvestigationInfo, 1)
    state = info.view_state if info and isinstance(info.view_state, dict) else {}
    drawer = state.get("drawer") if isinstance(state.get("drawer"), dict) else {}
    old = drawer.get("paths") if isinstance(drawer.get("paths"), list) else []
    paths = []
    for path in old[:MAX_PATHS]:
        try:
            paths.extend(
                clean_paths([{"ids": path.get("ids"), "branch_id": path.get("branch_id")}])
            )
        except (RecordError, AttributeError):
            continue
    if not paths or list_profiles(session):
        return 0
    # D199: it keeps the reference the energy view had, which its pathways used until now.
    reference = state.get("reference_id")
    on = reference if isinstance(reference, str) and reference in _on(paths) else None
    profile = create(session, paths=paths, reference_id=on)
    if info is not None:
        rest = {k: v for k, v in drawer.items() if k != "paths"}
        info.view_state = {**state, "drawer": {**rest, "profile_id": profile.id}}
    return len(paths)
