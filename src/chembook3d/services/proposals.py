"""Edges Claude proposes, and the user's review of them (D264, FR-PANEL-11).

- `propose` writes many edges at once, each with a confidence (high, medium or low, A184) and
  the evidence that settled it, and marks it open (`Transition.review`). It is refused outside
  a playground (D263a, D235): Claude builds a mechanism only in one. An edge already there in
  the same direction is updated rather than made twice. Nodes may be put in reaction steps
  (named; made when missing) and groups (named; made when missing) through the steps and
  groups services, so their rules hold; no branch is made or changed here (D169).
- Every write is the ordinary per-record history, so Claude's writes are Claude's (the request's
  `history.SOURCE`) and its own until someone else changes them (`autonomy.Autonomy`).
- The review is the user's: `accept` (open → accepted; the confidence and evidence stay on the
  edge and in the history), `accept_all_high`, `reject` (deletes the edge; its delete entry keeps
  the evidence) and `fix` (moves one end to another node; the evidence says so, and the edge
  stays open for the user to accept).
- `counts` and `open_proposals` say what is still open, for the merge review's warning
  (`playground_merge.review`) and Claude's summary.
- A gap Claude fills (D265): an edge names the hole it fills (`hole`, from find_gaps), so its
  confidence is low and its evidence starts “Filled by Claude (hole …)” (A184); `filled` marks
  the nodes built for a hole as planned MACE guesses (`gaps.mark_filled`); and at most 10 holes
  are filled in a run (`gaps.check_bound`, D265d).
"""

from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from chembook3d.models import (
    GroupNode,
    InvestigationInfo,
    Node,
    NodeKind,
    ReactionStep,
    Transition,
)
from chembook3d.services import gaps, history
from chembook3d.services import groups as group_service
from chembook3d.services import nodes as node_service
from chembook3d.services import species as species_service
from chembook3d.services import steps as step_service
from chembook3d.services import transitions as transition_service
from chembook3d.services.records import RecordError, get, status_value, text_value

CONFIDENCES = ("high", "medium", "low")
OPEN = "open"
ACCEPTED = "accepted"
ENDS = ("source", "target")
# What Claude should do after proposing: tidy is the page's own code (D226), not a route.
NEXT = "Call tidy_canvas next so the new edges are laid out, then summarize them for the user."


@dataclass
class Proposed:
    edges: list[Transition] = field(default_factory=list)
    created: int = 0
    updated: int = 0
    steps: list[dict[str, Any]] = field(default_factory=list)
    groups: list[dict[str, Any]] = field(default_factory=list)
    filled: list[dict[str, Any]] = field(default_factory=list)


def in_playground(session: Session) -> bool:
    info = session.get(InvestigationInfo, 1)
    return info is not None and bool(info.playground_of)


def _confidence(value: Any) -> str:
    if value not in CONFIDENCES:
        raise RecordError("confidence must be high, medium or low (A184)")
    return value


def _evidence(value: Any) -> str:
    text = text_value("evidence", value).strip()
    if not text:
        raise RecordError("Each proposed edge needs its evidence, e.g. “names only”")
    return text


def _label(end: transition_service.Endpoint) -> str:
    return end.label or ("Untitled node" if isinstance(end, Node) else "Untitled group")


def _existing(session: Session, source_id: str, target_id: str) -> Transition | None:
    for transition in transition_service.touching(session, [source_id]):
        if transition.source_id == source_id and transition.target_id == target_id:
            return transition
    return None


def _set(session: Session, transition: Transition, name: str, value: Any) -> None:
    old = getattr(transition, name)
    if old != value:
        setattr(transition, name, value)
        history.record(session, "transition", transition.id, "update", name, old, value)


def _edge(session: Session, item: Any) -> tuple[Transition, bool]:
    """One proposed edge: made, or the one already there updated. True when made."""
    if not isinstance(item, dict):
        raise RecordError("Each proposed edge must be an object")
    source_id, target_id = item.get("source_id"), item.get("target_id")
    if not isinstance(source_id, str) or not isinstance(target_id, str):
        raise RecordError("Each proposed edge needs a source_id and a target_id")
    source = transition_service.endpoint(session, source_id)
    target = transition_service.endpoint(session, target_id)
    if transition_service.is_ts(source) and transition_service.is_ts(target):
        raise RecordError(
            f"“{_label(source)}” and “{_label(target)}” are both transition states; "
            "an edge joins a TS to a minimum, or two minima for a step without a TS"
        )
    proposal = {
        "confidence": _confidence(item.get("confidence")),
        "evidence": _evidence(item.get("evidence")),
        "review": OPEN,
    }
    hole = item.get("hole")
    if not hole and gaps.is_filled_evidence(proposal["evidence"]):
        raise RecordError(
            "An edge “filled by Claude” names the hole it fills (hole, from find_gaps, D265)"
        )
    if hole:
        hole = gaps.check_hole_id(hole)
        if proposal["confidence"] != "low":
            raise RecordError(
                f"An edge that fills a gap ({hole}) is low confidence (A184, D265c), "
                f"not {proposal['confidence']}"
            )
        proposal["evidence"] = gaps.filled_evidence(hole, proposal["evidence"])
    status = item.get("status")
    if status is not None:
        status = status_value(status)
    transition = _existing(session, source.id, target.id)
    made = transition is None
    if transition is None:
        fields = {"source_id": source.id, "target_id": target.id}
        if status is not None:
            fields["status"] = status
        transition = transition_service.create(session, fields, proposal)
    else:
        for name, value in proposal.items():
            _set(session, transition, name, value)
        if status is not None:
            transition_service.update(session, transition.id, {"status": status})
    for entry in item.get("species") or []:
        if not isinstance(entry, dict):
            raise RecordError("Each free species of an edge must be an object")
        species_service.attach(
            session,
            transition.id,
            entry.get("species_id"),
            entry.get("direction"),
            entry.get("count", 1),
        )
    session.flush()
    return transition, made


def _place_in_step(session: Session, item: Any) -> dict[str, Any]:
    if not isinstance(item, dict):
        raise RecordError("Each step must be an object with a name and ids")
    name = text_value("name", item.get("name", "")).strip()
    if not name:
        raise RecordError("Each step needs a name")
    ids = item.get("ids") or []
    if not isinstance(ids, list) or not all(isinstance(i, str) for i in ids):
        raise RecordError("A step's ids must be a list of node or group ids")
    step = session.scalar(select(ReactionStep).where(ReactionStep.name == name).limit(1))
    made = step is None
    if step is None:
        step = step_service.create(session, name)
    for record_id in ids:
        if session.get(GroupNode, record_id) is not None:
            group_service.update(session, record_id, {"step_id": step.id})
            continue
        get(session, Node, record_id, "Node")
        try:
            node_service.update(session, record_id, {"step_id": step.id})
        except node_service.NodeError as exc:
            raise RecordError(str(exc)) from exc
    return {"id": step.id, "name": step.name, "created": made}


def _place_in_group(session: Session, item: Any) -> dict[str, Any]:
    if not isinstance(item, dict):
        raise RecordError("Each group must be an object with a label and member_ids")
    label = text_value("label", item.get("label", "")).strip()
    if not label:
        raise RecordError("Each group needs a label")
    member_ids = item.get("member_ids") or []
    if not isinstance(member_ids, list) or not all(isinstance(i, str) for i in member_ids):
        raise RecordError("A group's member_ids must be a list of node ids")
    group = session.scalar(select(GroupNode).where(GroupNode.label == label).limit(1))
    if group is None:
        group = group_service.reconnect(session, {"member_ids": member_ids, "label": label})
        return {"id": group.id, "label": group.label, "created": True}
    ids = dict.fromkeys(member_ids)
    new = [i for i in ids if get(session, Node, i, "Node").group_id != group.id]
    if new:
        group_service.add_members(session, group.id, new)
    return {"id": group.id, "label": group.label, "created": False}


def _holes(edges: list[Any], filled: list[Any]) -> set[str]:
    found = {gaps.check_hole_id(e["hole"]) for e in edges if isinstance(e, dict) and e.get("hole")}
    for item in filled:
        if not isinstance(item, dict):
            raise RecordError("Each filled gap must be an object with a hole and node_ids")
        found.add(gaps.check_hole_id(item.get("hole")))
    return found


def _mark(session: Session, item: dict[str, Any]) -> dict[str, Any]:
    hole = gaps.check_hole_id(item.get("hole"))
    node_ids = item.get("node_ids") or []
    if (
        not node_ids
        or not isinstance(node_ids, list)
        or not all(isinstance(i, str) for i in node_ids)
    ):
        raise RecordError("A filled gap's node_ids lists the nodes built for it")
    what = text_value("what", item.get("what") or "")
    nodes = [gaps.mark_filled(session, hole, node_id, what) for node_id in node_ids]
    return {"hole": hole, "nodes": [{"id": n.id, "label": n.label} for n in nodes]}


def propose(
    session: Session,
    edges: list[Any],
    steps: list[Any] | None = None,
    groups: list[Any] | None = None,
    filled: list[Any] | None = None,
    since: datetime | None = None,
) -> Proposed:
    """D264a: write Claude's proposed edges, then place nodes in steps and groups; all or
    nothing (the request's one transaction). D265: `filled` marks the nodes built for each hole;
    the holes named here and filled since `since` are at most 10."""
    if not in_playground(session):
        raise RecordError(
            "Edges are proposed only in a playground (D263a): build the mechanism in a "
            "playground of this investigation"
        )
    if not isinstance(edges, list):
        raise RecordError("edges must be a list")
    filled = filled or []
    holes = _holes(edges, filled)
    if holes:
        gaps.check_bound(session, holes, since)
    found = Proposed()
    seen: set[str] = set()
    for item in edges:
        transition, made = _edge(session, item)
        if transition.id in seen:
            raise RecordError("The same edge is proposed twice")
        seen.add(transition.id)
        found.edges.append(transition)
        found.created += made
        found.updated += not made
    for item in steps or []:
        found.steps.append(_place_in_step(session, item))
    for item in groups or []:
        found.groups.append(_place_in_group(session, item))
    for item in filled:
        found.filled.append(_mark(session, item))
    session.flush()
    return found


def _proposal(session: Session, transition_id: str) -> Transition:
    transition = get(session, Transition, transition_id, "Transition")
    if transition.review is None:
        raise RecordError("This edge was not proposed by Claude, so there is nothing to review")
    return transition


def accept(session: Session, transition_id: str) -> Transition:
    """D264b: the badge goes; the confidence and evidence stay on the edge and in its history."""
    transition = _proposal(session, transition_id)
    _set(session, transition, "review", ACCEPTED)
    session.flush()
    return transition


def accept_all_high(session: Session) -> list[Transition]:
    found = [t for t in open_proposals(session) if t.confidence == "high"]
    for transition in found:
        _set(session, transition, "review", ACCEPTED)
    session.flush()
    return found


def reject(session: Session, transition_id: str) -> None:
    """The edge goes; its delete entry keeps the confidence and evidence."""
    transition = _proposal(session, transition_id)
    if transition.review != OPEN:
        raise RecordError("This edge was accepted already; delete it as any edge")
    transition_service.remove(session, [transition])


def fix(session: Session, transition_id: str, end: str, node_id: str) -> Transition:
    """Move one end of an open proposal to another node or group (D264b). The evidence says
    what the user changed; the edge stays open until they accept it."""
    transition = _proposal(session, transition_id)
    if transition.review != OPEN:
        raise RecordError("This edge was accepted already")
    if end not in ENDS:
        raise RecordError("end must be 'source' or 'target'")
    new = transition_service.endpoint(session, node_id)
    if isinstance(new, Node) and new.kind == NodeKind.SPECIES:
        raise RecordError(f"“{_label(new)}” is a free species; it cannot be an end of an edge")
    old_id = transition.source_id if end == "source" else transition.target_id
    if new.id == old_id:
        return transition
    other_id = transition.target_id if end == "source" else transition.source_id
    if new.id == other_id:
        raise RecordError("A transition must join two different nodes")
    source_id, target_id = (new.id, other_id) if end == "source" else (other_id, new.id)
    if _existing(session, source_id, target_id) is not None:
        raise RecordError("These two nodes are already connected in this direction")
    old = transition_service.endpoint(session, old_id)
    is_node = isinstance(new, Node)
    setattr(transition, f"{end}_node_id", new.id if is_node else None)
    setattr(transition, f"{end}_group_id", None if is_node else new.id)
    history.record(session, "transition", transition.id, "update", f"{end}_id", old_id, new.id)
    note = f"Fixed by the user: {end} “{_label(old)}” → “{_label(new)}”."
    evidence = f"{transition.evidence}\n{note}" if transition.evidence else note
    _set(session, transition, "evidence", evidence)
    session.flush()
    return transition


def open_proposals(session: Session) -> list[Transition]:
    query = select(Transition).where(Transition.review == OPEN).order_by(Transition.seq)
    return list(session.scalars(query))


def counts(rows: list[Any]) -> dict[str, int]:
    """Open proposals per confidence, from records or rows with a `confidence`."""
    found = Counter(
        (r["confidence"] if isinstance(r, dict) else r.confidence) or "low" for r in rows
    )
    return {c: found.get(c, 0) for c in CONFIDENCES}


def warning(by_confidence: dict[str, int]) -> str | None:
    """The merge review's warning (D264b), or None when nothing is open."""
    total = sum(by_confidence.values())
    if not total:
        return None
    each = ", ".join(f"{c} {by_confidence[c]}" for c in CONFIDENCES)
    what = "proposed edge is" if total == 1 else "proposed edges are"
    return f"{total} {what} still open: {each}"
