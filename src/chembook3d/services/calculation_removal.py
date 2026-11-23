"""Remove one calculation from a node (D259, FR-CALC-10).

A calculation goes with every calculation stored as sitting on it (D255's
`geometry_source_id`, followed to the end): removing an optimization also removes the
frequency jobs and single points run at its geometry, so nothing is left pointing at a
geometry the node no longer holds. The node keeps its coordinates. Like deleting a node it is
permanent: one history entry on the node lists what went (P15). A copied file stays while
another calculation still comes from it; the copy of a file none does any more is removed
once the transaction has committed, as an undone import's is (D102).

The user confirms in the app first (D91): the API's preview lists what goes, and Claude's
`remove_calculation` is a confirm tool.
"""

from dataclasses import dataclass, field
from pathlib import PurePath
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from chembook3d.models import Calculation, Node, SourceFile
from chembook3d.services import history, levels
from chembook3d.services.records import RecordError, get

ACTION = "remove_calculations"


class RemovalError(RecordError):
    pass


@dataclass
class Removal:
    calculation: Calculation
    linked: list[Calculation] = field(default_factory=list)  # sitting on it, in order
    files: list[SourceFile] = field(default_factory=list)  # no calculation comes from them after

    @property
    def all(self) -> list[Calculation]:
        return [self.calculation, *self.linked]

    def as_dict(self) -> dict[str, Any]:
        node = self.calculation.node
        return {
            "node_id": node.id,
            "node_label": node.label,
            "calculation": describe(self.calculation),
            "linked": [describe(c) for c in self.linked],
            "files": [f.original_name for f in self.files],
        }


def describe(calculation: Calculation) -> dict[str, Any]:
    source = calculation.source_file
    return {
        "id": calculation.id,
        "type": calculation.type,
        "level": levels.composite_label(
            calculation.level, calculation.geometry_level if calculation.geometry_level_id else None
        ),
        "file": source.original_name if source else None,
        "step": calculation.step_index,
    }


def plan(session: Session, calculation_id: str) -> Removal:
    """What removing the calculation takes with it. Changes nothing."""
    calculation = get(session, Calculation, calculation_id, "Calculation")
    if calculation.irc_part is not None:
        raise RemovalError(
            "This is one direction of an IRC; undo the import of its file instead (D102)"
        )
    linked: list[Calculation] = []
    seen = {calculation.id}
    frontier = [calculation.id]
    while frontier:
        found = list(
            session.scalars(
                select(Calculation)
                .where(Calculation.geometry_source_id.in_(frontier))
                .order_by(Calculation.created_at, Calculation.id)
            )
        )
        found = [c for c in found if c.id not in seen]
        seen.update(c.id for c in found)
        linked.extend(found)
        frontier = [c.id for c in found]
    removal = Removal(calculation, linked)
    gone = {c.id for c in removal.all}
    for source_id in dict.fromkeys(c.source_file_id for c in removal.all if c.source_file_id):
        others = session.scalars(
            select(Calculation.id).where(
                Calculation.source_file_id == source_id, Calculation.id.not_in(gone)
            )
        ).first()
        source = session.get(SourceFile, source_id)
        if others is None and source is not None:
            removal.files.append(source)
    return removal


def apply(session: Session, removal: Removal) -> list[str]:
    """Remove them. Returns the stored copies' folders, relative to the investigation, for the
    caller to remove once the transaction is committed."""
    summary = removal.as_dict()
    node: Node = removal.calculation.node
    for calculation in reversed(removal.all):
        session.delete(calculation)
    session.flush()
    session.expire(node, ["calculations"])
    stored = [PurePath(f.stored_path).parent.as_posix() for f in removal.files]
    for source in removal.files:
        session.delete(source)
    session.flush()
    history.record(
        session,
        "node",
        node.id,
        ACTION,
        old={k: summary[k] for k in ("calculation", "linked", "files")},
    )
    session.flush()
    return stored
