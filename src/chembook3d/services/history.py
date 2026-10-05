"""Append-only change history (D29, FR-HIST-01…03). Entries are only ever added."""

from contextvars import ContextVar
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from chembook3d.models import Calculation, HistoryEntry

# Who makes the current request's changes: "manual" for the user, "claude" when Claude makes
# them through `chembook3d mcp` (D91). Set per request by api.live.ChangeTracker.
SOURCE: ContextVar[str] = ContextVar("history_source", default="manual")


def record(
    session: Session,
    record_type: str,
    record_id: str,
    action: str,
    field: str | None = None,
    old: Any = None,
    new: Any = None,
    source: str = "manual",
) -> None:
    if source == "manual":
        source = SOURCE.get()
    session.add(
        HistoryEntry(
            record_type=record_type,
            record_id=record_id,
            action=action,
            field=field,
            old_value=old,
            new_value=new,
            source=source,
        )
    )


def entries(session: Session, record_id: str | None = None, limit: int = 200) -> list[HistoryEntry]:
    """Newest first, for one record or for the whole investigation (FR-HIST-02). A node's
    history includes the calculations on it."""
    query = select(HistoryEntry).order_by(HistoryEntry.id.desc()).limit(limit)
    if record_id is not None:
        calculations = select(Calculation.id).where(Calculation.node_id == record_id)
        query = query.where(
            (HistoryEntry.record_id == record_id) | HistoryEntry.record_id.in_(calculations)
        )
    return list(session.scalars(query))
