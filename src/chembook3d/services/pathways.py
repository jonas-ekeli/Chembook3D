"""Pathways, energy profiles and the energy table (FR-EN-05, 06, 09; D4, D39, D44, INV-2).

- A pathway is a sequence of nodes or group nodes where each neighbour pair is joined by an
  existing transition, followed in either direction. Nothing here builds a pathway by picking
  nodes per step, and energies are never used to choose a node or an edge (INV-2, EN-10).
- Extending a pathway follows an outgoing transition only while there is exactly one to
  follow. At a fork (two TS candidates, a split) it stops and lists the choices for the user.
- A pathway visits each node once, except that it may end at a node it has already visited,
  closing a catalytic cycle (A13). The cycle is closed once; a further turnover would only
  repeat the same calculations.
- A profile plots X(n) − X(ref) for a reference node the user picks on one of the pathways
  (EN-8). A direct connection is marked "no TS" and never drawn as a barrier (D53, INV-8).
- Free species that join or leave on the transitions between the reference and a point are
  added to or subtracted from its value, so every point has the reference's atoms (D69). A
  pathway that does not pass through the reference is balanced from the first point it shares
  with one that does, or else from its first point joined to the reference by transitions,
  with that point's balance on the canvas (D72).
"""

from dataclasses import dataclass
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from chembook3d import units
from chembook3d.models import Branch, GroupNode, Node, ReactionStep, Role, Transition
from chembook3d.services import branches as branch_service
from chembook3d.services import species as species_service
from chembook3d.services import transitions as transition_service
from chembook3d.services.energies import ENERGY_TYPES, Energies, LevelKey, key_label
from chembook3d.services.records import RecordError, get

Endpoint = Node | GroupNode


@dataclass
class Segment:
    transition: Transition
    forward: bool  # the pathway follows the transition from its source to its target


def _label(end: Endpoint) -> str:
    if isinstance(end, Node):
        return end.label or "Untitled node"
    return end.label or "Group"


def _outgoing(session: Session, record_id: str) -> list[Transition]:
    query = select(Transition).where(
        or_(Transition.source_node_id == record_id, Transition.source_group_id == record_id)
    )
    return list(session.scalars(query.order_by(Transition.seq)))


def _incoming(session: Session, record_id: str) -> list[Transition]:
    query = select(Transition).where(
        or_(Transition.target_node_id == record_id, Transition.target_group_id == record_id)
    )
    return list(session.scalars(query.order_by(Transition.seq)))


def resolve(session: Session, ids: list[str]) -> list[Segment]:
    """The transitions joining each neighbour pair. Refuses a pathway with a gap (INV-2)."""
    if not ids:
        raise RecordError("A pathway needs at least one node")
    if len(set(ids[:-1])) != len(ids) - 1:
        raise RecordError(
            "A pathway visits each node only once; only its last node may return to an "
            "earlier one, closing a cycle (A13)"
        )
    ends = [transition_service.endpoint(session, record_id) for record_id in ids]
    segments = []
    for a, b in zip(ends, ends[1:], strict=False):
        forward = [t for t in _outgoing(session, a.id) if t.target_id == b.id]
        backward = [t for t in _outgoing(session, b.id) if t.target_id == a.id]
        if forward:
            segments.append(Segment(forward[0], True))
        elif backward:
            segments.append(Segment(backward[0], False))
        else:
            raise RecordError(
                f"No transition joins “{_label(a)}” and “{_label(b)}”: a pathway follows "
                "existing transitions only (INV-2)"
            )
    return segments


def _descendants(session: Session, branch: Branch) -> set[str]:
    seen: set[str] = set()
    stack = [branch.id]
    while stack:
        for child in branch_service.children(session, stack.pop()):
            if child.id not in seen:
                seen.add(child.id)
                stack.append(child.id)
    return seen


def _in(end: Endpoint, branch_ids: set[str]) -> bool:
    return bool(transition_service.branches_of(end) & branch_ids)


def _shared(end: Endpoint) -> bool:
    """A node on no branch (e.g. the intermediate a split starts from) belongs to every branch
    that reaches it along a transition, so a branch's pathway may pass through it (D67)."""
    return not transition_service.branches_of(end)


def _choices(session: Session, transitions: list[Transition]) -> list[dict[str, Any]]:
    found = []
    for t in transitions:
        target = transition_service.endpoint(session, t.target_id)
        found.append(
            {
                "transition_id": t.id,
                "node_id": target.id,
                "label": _label(target),
                "status": target.status if isinstance(target, Node) else t.status,
            }
        )
    return found


def closed(path: list[str]) -> bool:
    """Whether the last node returns to an earlier one, closing a cycle (A13)."""
    return path[-1] in path[:-1]


@dataclass
class Extended:
    path: list[str]
    choices: list[dict[str, Any]]
    closed: bool


def extend(session: Session, ids: list[str], branch_id: str | None = None) -> Extended:
    """Follow outgoing transitions from the last node while exactly one leads on. With a
    branch, only edges into that branch's lineage (its ancestors and descendants) or into a
    node on no branch (D67) count, so an interconversion into a sibling branch is not
    followed. Stops at an end, at a fork (listing the choices, which may include nodes already
    visited) and when the pathway returns to a node it visited, closing a cycle (A13). A
    closed pathway is complete."""
    path = list(ids)
    resolve(session, path)
    if closed(path):
        return Extended(path, [], True)
    related = None
    if branch_id is not None:
        branch = get(session, Branch, branch_id, "Branch")
        related = {branch.id} | branch_service.ancestors(branch) | _descendants(session, branch)

    def _leads_into(end: Endpoint) -> bool:
        return _in(end, related) or _shared(end)

    while True:
        options = [
            t
            for t in _outgoing(session, path[-1])
            if related is None or _leads_into(transition_service.endpoint(session, t.target_id))
        ]
        if len(options) != 1:
            return Extended(path, _choices(session, options), False)
        path.append(options[0].target_id)
        if closed(path):
            return Extended(path, [], True)


def branch_pathway(session: Session, branch_id: str) -> Extended:
    """The pathway of one branch: from its first node, traced back through its ancestors and
    through nodes on no branch (D67) while one transition leads in, then forward through its
    lineage while one leads on. Tracing back never enters the branch itself or goes from a
    later reaction step to an earlier one, so the edge that closes a catalytic cycle is not
    mistaken for the way in (A13)."""
    branch = get(session, Branch, branch_id, "Branch")
    steps = {s.id: s.position for s in session.scalars(select(ReactionStep))}
    members = list(session.scalars(select(Node).where(Node.branch_id == branch.id)))
    member_ids = {m.id for m in members}
    if not members:
        raise RecordError("The branch has no nodes")
    starts = [
        m for m in members if not any(t.source_id in member_ids for t in _incoming(session, m.id))
    ] or members
    start = min(starts, key=lambda n: (steps.get(n.step_id or "", 10**6), n.seq))

    lineage = branch_service.ancestors(branch)

    def position(end: Endpoint) -> int | None:
        return steps.get(end.step_id or "")

    def leads_in(t: Transition) -> bool:
        source = transition_service.endpoint(session, t.source_id)
        before, after = position(source), position(transition_service.endpoint(session, path[0]))
        later = before is not None and after is not None and before > after
        shared = _in(source, lineage) or _shared(source)
        return t.source_id not in path and shared and not later

    path = [start.id]
    while True:
        options = [t for t in _incoming(session, path[0]) if leads_in(t)]
        if len(options) != 1:
            break
        path.insert(0, options[0].source_id)
    return extend(session, path, branch.id)


# ---------- profiles and the table ----------


def _balances(
    session: Session, paths: list[list[str]], reference_id: str
) -> list[list[species_service.Balance]]:
    """D69: the free-species balance of every point of every pathway."""
    known: dict[str, species_service.Balance] = {reference_id: {}}
    found: list[list[species_service.Balance]] = [[] for _ in paths]
    graph: dict[str, species_service.Balance] | None = None
    # Pathways through the reference first, so the others can start from a shared point.
    for i in sorted(range(len(paths)), key=lambda i: reference_id not in paths[i]):
        path, segments = paths[i], resolve(session, paths[i])
        start = next((j for j, record_id in enumerate(path) if record_id in known), None)
        anchor = known
        if start is None:
            # D72: no point shared with a pathway through the reference, so start from the
            # first point the reference reaches along transitions (the same balance as its
            # node card).
            graph = (
                graph if graph is not None else species_service.balances_from(session, reference_id)
            )
            start = next((j for j, record_id in enumerate(path) if record_id in graph), 0)
            anchor = graph
        balance: list[species_service.Balance] = [{} for _ in path]
        balance[start] = dict(anchor.get(path[start], {}))
        for j in range(start, len(path) - 1):
            step = species_service.change(segments[j].transition, segments[j].forward)
            balance[j + 1] = species_service.combine(balance[j], step)
        for j in range(start - 1, -1, -1):
            step = species_service.change(segments[j].transition, segments[j].forward)
            balance[j] = species_service.combine(balance[j + 1], step, -1)
        for record_id, value in zip(path, balance, strict=True):
            known.setdefault(record_id, value)
        found[i] = balance
    return found


def _point(
    session: Session,
    energies: Energies,
    end: Endpoint,
    key: LevelKey,
    energy_type: str,
    steps: dict[str, ReactionStep],
) -> dict[str, Any]:
    value = energies.value(end.id, key, energy_type)
    is_node = isinstance(end, Node)
    step = steps.get(end.step_id or "")
    branch_id = end.branch_id if is_node else end.outgoing_branch_id
    return {
        "id": end.id,
        "kind": "node" if is_node else "group",
        "label": _label(end),
        "is_ts": is_node and end.role == Role.TRANSITION_STATE,
        "step_id": step.id if step else None,
        "step_name": step.name if step else None,
        "step_position": step.position if step else None,
        "branch_id": branch_id,
        **value.as_dict(),
    }


def profiles(
    session: Session,
    paths: list[list[str]],
    reference_id: str | None,
    key: LevelKey,
    energy_type: str,
) -> dict[str, Any]:
    if energy_type not in ENERGY_TYPES:
        raise RecordError(f"unknown energy type '{energy_type}'")
    if not paths:
        raise RecordError("Choose at least one pathway")
    on_paths = {record_id for path in paths for record_id in path}
    if reference_id is None:
        reference_id = paths[0][0]
    if reference_id not in on_paths:
        raise RecordError("The reference must be a node on one of the pathways (EN-8)")
    energies = Energies(session)
    steps = {s.id: s for s in session.scalars(select(ReactionStep))}
    reference = energies.value(reference_id, key, energy_type).value
    balances = _balances(session, paths, reference_id)
    shown = []
    for path, balance in zip(paths, balances, strict=True):
        segments = resolve(session, path)
        points = []
        for record_id, species in zip(path, balance, strict=True):
            point = _point(
                session,
                energies,
                transition_service.endpoint(session, record_id),
                key,
                energy_type,
                steps,
            )
            value = point["value"]
            added, missing = species_service.energy(session, energies, species, key, energy_type)
            point["species"] = species_service.describe(session, species)
            point["species_message"] = missing
            point["relative"] = (
                value + added - reference
                if value is not None and reference is not None and added is not None
                else None
            )
            points.append(point)
        shown.append(
            {
                "points": points,
                "segments": [
                    {
                        "transition_id": s.transition.id,
                        "forward": s.forward,
                        "status": s.transition.status,
                        # D53, INV-8: drawn as a dotted "no TS" connector, never as a barrier.
                        "direct": transition_service.describe(session, s.transition)["direct"],
                    }
                    for s in segments
                ],
            }
        )
    return {
        "level": key.encode(),
        "level_label": key_label(session, key),
        "type": energy_type,
        "reference_id": reference_id,
        "reference_value": reference,
        "temperature": energies.temperature,
        "cutoff": energies.cutoff,
        "profiles": shown,
    }


def _absolute(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.8f}"


def _relative(value: float | None, unit: str) -> str:
    if value is None:
        return "n/a"
    text = f"{value * units.HARTREE_IN[unit]:.{units.DECIMALS[unit]}f}"
    return text[1:] if text.startswith("-") and not text.strip("-0.") else text  # no "-0.00"


def table(
    session: Session,
    paths: list[list[str]],
    reference_id: str | None,
    key: LevelKey,
    energy_type: str,
    unit: str,
) -> dict[str, Any]:
    """FR-EN-06, D44: one row per node on the pathways, in pathway order. Values are already
    formatted, so the screen and the CSV file show the same text (T-EN-08)."""
    if unit not in units.HARTREE_IN:
        raise RecordError(f"unknown energy unit '{unit}'")
    data = profiles(session, paths, reference_id, key, energy_type)
    energies = Energies(session)
    branch_names = {b.id: b.name or "Unnamed branch" for b in session.scalars(select(Branch))}
    label = data["level_label"]
    qh = f"G_qh {data['temperature']:g} K {data['cutoff']:g} cm-1"
    columns = [
        "Label",
        "Step",
        "Branch",
        "Level",
        "E (hartree)",
        "H (hartree)",
        "G (hartree)",
        f"{qh} (hartree)",
        "Free species",
        f"Δ{energy_type} ({unit})",
    ]
    rows: list[list[str]] = []
    seen: set[tuple] = set()
    for profile in data["profiles"]:
        for point in profile["points"]:
            # A point balanced with other free species (a closed cycle) gets its own row.
            balance = tuple((s["species_id"], s["count"]) for s in point["species"])
            if (point["id"], balance) in seen:
                continue
            seen.add((point["id"], balance))
            name = point["label"]
            representative = point["details"].get("representative_id")
            if point["kind"] == "group":
                rep = session.get(Node, representative) if representative else None
                name += f" (representative: {rep.label or 'Untitled node'})" if rep else ""
            values = {t: energies.value(point["id"], key, t).value for t in ("E", "H", "G", "G_qh")}
            rows.append(
                [
                    name,
                    point["step_name"] or "",
                    branch_names.get(point["branch_id"] or "", ""),
                    label,
                    _absolute(values["E"]),
                    _absolute(values["H"]),
                    _absolute(values["G"]),
                    _absolute(values["G_qh"]),
                    species_service.text(point["species"]),
                    _relative(point["relative"], unit),
                ]
            )
    return {"columns": columns, "rows": rows}
