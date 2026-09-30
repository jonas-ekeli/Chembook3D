"""Append-only change history (D29, FR-HIST-01…03). Entries are only ever added."""

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from chembook3d.models import Calculation, HistoryEntry


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
