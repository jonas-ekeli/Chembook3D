"""The resume overview (FR-OV-01, WF-10, D45, D53): what an investigation looks like after a
gap. Each branch with its status and its nodes counted by status; the open items (planned,
running and failed nodes and transitions, nodes with warnings, and direct "no TS"
connections per branch); recent changes from the history; and the notes of each step.

Everything is read from the records each time; nothing here changes them.
"""

from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from chembook3d.models import GroupNode, HistoryEntry, Node, NodeKind, Status
from chembook3d.services import branches as branch_service
from chembook3d.services import groups as group_service
from chembook3d.services import steps as step_service
from chembook3d.services import transitions as transition_service
from chembook3d.services import warnings as warning_service

OPEN_STATUSES = (Status.FAILED, Status.RUNNING_EXTERNALLY, Status.PLANNED)
# The order open items are listed in: what needs attention first.
REASONS = ("failed", "running_externally", "planned", "warning", "direct")
RECENT_LIMIT = 50


@dataclass
class BranchSummary:
    id: str | None  # None: nodes that are on no branch and in no group
    name: str
    colour: str | None
    status: str | None
    counts: dict[str, int]  # node status → number of nodes
    node_count: int


@dataclass
class OpenItem:
    kind: str  # node | transition | group
    id: str
    label: str
    reason: str  # one of REASONS
    detail: str
    branch_ids: list[str] = field(default_factory=list)


@dataclass
class StepNotes:
    id: str
    name: str
    position: int
    notes: str


@dataclass
class Overview:
    branches: list[BranchSummary]
    open_items: list[OpenItem]
    recent: list[HistoryEntry]
    steps: list[StepNotes]
    group_count: int

    def as_dict(self) -> dict[str, Any]:
        found = asdict(self)
        found["recent"] = self.recent
        return found


def _node_label(node: Node) -> str:
    return node.label or "Untitled node"


def _counts(nodes: list[Node]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for node in nodes:
        counts[node.status] = counts.get(node.status, 0) + 1
    return counts


def _end_label(end: Node | GroupNode) -> str:
    if isinstance(end, Node):
        return _node_label(end)
    return end.label or "Group"


def overview(session: Session, since: datetime | None = None) -> Overview:
    nodes = list(session.scalars(select(Node).order_by(Node.seq)))
    branch_list = branch_service.list_branches(session)
    branches = []
    for branch in branch_list:
        members = [n for n in nodes if n.branch_id == branch.id]
        branches.append(
            BranchSummary(
                branch.id, branch.name, branch.colour, branch.status, _counts(members), len(members)
            )
        )
    # Free species (D69) are not on the canvas, so they are not counted with loose nodes.
    loose = [
        n for n in nodes if n.branch_id is None and n.group_id is None and n.kind == NodeKind.NODE
    ]
    if loose:
        branches.append(BranchSummary(None, "No branch", None, None, _counts(loose), len(loose)))

    items: list[OpenItem] = []
    for node in nodes:
        branch_ids = [node.branch_id] if node.branch_id else []
        if node.status in OPEN_STATUSES:
            items.append(OpenItem("node", node.id, _node_label(node), node.status, "", branch_ids))
        found = warning_service.node_warnings(node)
        if found:
            codes = sorted({w.code for w in found})
            items.append(
                OpenItem(
                    "node", node.id, _node_label(node), "warning", ", ".join(codes), branch_ids
                )
            )

    for transition in transition_service.list_transitions(session):
        source = transition_service.endpoint(session, transition.source_id)
        target = transition_service.endpoint(session, transition.target_id)
        label = f"{_end_label(source)} → {_end_label(target)}"
        branch_ids = sorted(
            transition_service.branches_of(source) | transition_service.branches_of(target)
        )
        if transition.status in OPEN_STATUSES:
            items.append(
                OpenItem("transition", transition.id, label, transition.status, "", branch_ids)
            )
        if transition_service.describe(session, transition)["direct"]:
            items.append(
                OpenItem("transition", transition.id, label, "direct", "no TS", branch_ids)
            )
    items.sort(key=lambda item: REASONS.index(item.reason))

    query = select(HistoryEntry).order_by(HistoryEntry.id.desc()).limit(RECENT_LIMIT)
    if since is not None:
        query = query.where(HistoryEntry.timestamp >= since)
    recent = list(session.scalars(query))

    steps = [
        StepNotes(step.id, step.name, step.position, step.notes)
        for step in step_service.list_steps(session)
    ]
    return Overview(
        branches=branches,
        open_items=items,
        recent=recent,
        steps=steps,
        group_count=len(group_service.list_groups(session)),
    )
