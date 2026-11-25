"""Undo merge (D262h): bring a merged node back and put back every row its merge changed.

The merge's history entry ("merge_nodes" on the kept node B) holds each row it changed, before
and after (`node_merge.apply`). The undo works from that list alone:

- a row the merge removed (node A, a duplicate edge dropped) is written back as it was;
- a row the merge added is removed;
- a row the merge changed gets back the columns the merge changed, and only those, so later
  changes to other columns (B's label, a card moved) stay.

It is refused, naming what, when something the merge wrote has changed since: a column it
changed holds another value now, a row it added or changed is gone or was changed, or a later
calculation was run at the geometry of one going back to A. Normal modes are reordered back
through the inverse of the map the entry keeps. One transaction; one history entry
"undo_merge_nodes" on B naming the merge's entry.
"""

import base64
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, LargeBinary, Table, and_, delete, insert, select, update
from sqlalchemy.orm import Session

from chembook3d.models import (
    Base,
    Calculation,
    HistoryEntry,
    Node,
    NormalModes,
    Transition,
    pack_modes,
    unpack_modes,
)
from chembook3d.services import atom_maps, history, node_merge
from chembook3d.services.records import RecordError

UNDO_ACTION = "undo_merge_nodes"
# Columns a later change to is no reason to refuse: bookkeeping and where a card sits.
IGNORED = {"updated_at", "pos_x", "pos_y"}
# Columns put back without asking whether they changed since: what the notebook showed last
# (D105) is written by every tab all the time, and a stale tab may write the merged node back.
NEVER_BLOCKS = {"view_state"}
WHAT = {
    "nodes": "node",
    "calculations": "calculation",
    "calculation_results": "result of a calculation",
    "transitions": "edge",
    "transition_species": "free species on an edge",
    "branches": "branch",
    "group_nodes": "group",
    "sub_investigations": "sub-investigation",
    "node_notes": "note",
    "alignment_set_atoms": "alignment set",
    "steric_profile_atoms": "steric profile",
    "selectivity_members": "selectivity",
    "selectivities": "selectivity",
    "energy_profiles": "energy profile",
    "turnovers": "turnover",
    "conformer_equilibria": "conformer equilibrium",
    "investigation_info": "investigation's settings",
}


class UndoMergeError(RecordError):
    """Not a merge that can be undone."""


class UndoMergeBlocked(UndoMergeError):
    def __init__(self, blockers: list[str]):
        self.blockers = blockers
        super().__init__("; ".join(blockers))


# ---------- which merges can be undone ----------


def _is_merge(entry: HistoryEntry) -> bool:
    return (
        entry.record_type == "node"
        and entry.action == node_merge.ACTION
        and isinstance(entry.old_value, dict)
        and isinstance(entry.new_value, dict)
    )


def undone(session: Session) -> set[int]:
    """The merge entries an undo names."""
    values = session.scalars(
        select(HistoryEntry.new_value).where(
            HistoryEntry.record_type == "node", HistoryEntry.action == UNDO_ACTION
        )
    )
    return {v.get("entry_id") for v in values if isinstance(v, dict)}


def undo_kinds(session: Session, entries: list[HistoryEntry]) -> dict[int, str]:
    """ "node_merge" for each merge listed not undone yet (whether something blocks its undo is
    only worked out when asked)."""
    merges = [e.id for e in entries if _is_merge(e)]
    if not merges:
        return {}
    done = undone(session)
    return {i: node_merge.UNDO_KIND for i in merges if i not in done}


def standing_merge(session: Session, calculation: Calculation) -> HistoryEntry | None:
    """The merge that moved a calculation onto its node, while it is not undone (D262h: Undo
    import of its file asks to undo the merge first)."""
    origin = calculation.merged_from
    if not isinstance(origin, dict) or origin.get("entry_id") is None:
        return None
    entry = session.get(HistoryEntry, origin["entry_id"])
    if entry is None or not _is_merge(entry) or entry.id in undone(session):
        return None
    return entry


def _merge_entry(session: Session, entry_id: int) -> HistoryEntry:
    entry = session.get(HistoryEntry, entry_id)
    if entry is None or not _is_merge(entry):
        raise UndoMergeError("This history entry is not a merge of two nodes")
    if entry_id in undone(session):
        raise UndoMergeError("This merge was undone already")
    return entry


# ---------- values ----------


def _decoded(table: Table, row: dict[str, Any]) -> dict[str, Any]:
    """A row as the entry keeps it, back as the database takes it: times from ISO text, bytes
    from base64. Columns this schema no longer has are left out."""
    out = {}
    for name, value in row.items():
        if name not in table.c:
            continue
        kind = table.c[name].type
        if isinstance(value, str) and isinstance(kind, DateTime):
            value = datetime.fromisoformat(value)
        elif isinstance(value, str) and isinstance(kind, LargeBinary):
            value = base64.b64decode(value)
        out[name] = value
    return out


def _where(table: Table, key: list[Any]):
    return and_(*(c == v for c, v in zip(table.primary_key.columns, key, strict=True)))


def _now(session: Session, table: Table, key: list[Any]) -> dict[str, Any] | None:
    found = session.execute(select(table).where(_where(table, key))).first()
    return None if found is None else node_merge._row(table, found)


def _changed(before: dict[str, Any], after: dict[str, Any]) -> list[str]:
    return [c for c in after if c not in IGNORED and before.get(c) != after.get(c)]


def _parts(before: Any, after: Any) -> list[str] | None:
    """The keys of a JSON object column the merge changed (the view state's reference, say), or
    None when the column is not an object on both sides and counts as a whole."""
    if not isinstance(before, dict) or not isinstance(after, dict):
        return None
    return [k for k in dict.fromkeys([*before, *after]) if before.get(k) != after.get(k)]


def _later(before: dict[str, Any] | None, after: dict[str, Any], now: dict[str, Any]) -> list[str]:
    """The columns (or keys of an object column) the merge wrote that hold another value now."""
    if before is None:
        columns = [c for c in after if c not in IGNORED]
    else:
        columns = _changed(before, after)
    found = []
    for c in columns:
        if c not in now or c in NEVER_BLOCKS:
            continue
        parts = _parts(before.get(c), after.get(c)) if before is not None else None
        if parts is None:
            if now[c] != after.get(c):
                found.append(c)
        elif not isinstance(now[c], dict) or any(now[c].get(k) != after[c].get(k) for k in parts):
            found.append(c)
    return found


def _put_back(before: dict[str, Any], after: dict[str, Any], now: dict[str, Any]) -> dict:
    """The columns the merge changed with their values from before it; of an object column only
    the keys it changed, so what changed in it since (the canvas's pan, say) stays."""
    values = {}
    for c in _changed(before, after):
        parts = _parts(before.get(c), after.get(c))
        if parts is None or not isinstance(now.get(c), dict):
            values[c] = before.get(c)
            continue
        merged = dict(now[c])
        for k in parts:
            if k in before[c]:
                merged[k] = before[c][k]
            else:
                merged.pop(k, None)
        values[c] = merged
    return values


# ---------- the plan ----------


@dataclass
class Change:
    table: str
    key: list[Any]
    before: dict[str, Any] | None
    after: dict[str, Any] | None


@dataclass
class Plan:
    entry: HistoryEntry
    into_id: str
    from_id: str
    from_label: str
    into_label: str
    changes: list[Change]
    blockers: list[str] = field(default_factory=list)
    calculations: list[str] = field(default_factory=list)  # going back to A
    edges: int = 0  # edges going back to A or brought back
    records: int = 0  # other rows put back

    def as_dict(self) -> dict[str, Any]:
        return {
            "entry_id": self.entry.id,
            "into_id": self.into_id,
            "from_id": self.from_id,
            "from_label": self.from_label,
            "into_label": self.into_label,
            "blockers": self.blockers,
            "calculations": len(self.calculations),
            "edges": self.edges,
            "records": self.records,
        }


def _name(session: Session, table: str, row: dict[str, Any]) -> str:
    """What a row is, for a blocker."""
    what = WHAT.get(table, table.replace("_", " "))
    if table == "nodes":
        return f"node “{row.get('label') or 'Untitled'}”"
    if table == "calculations":
        node = session.get(Node, row.get("node_id"))
        on = f" on “{node_merge.name(node)}”" if node else ""
        level = row.get("type") or "calculation"
        return f"the {level} calculation{on}"
    if table in ("transitions", "transition_species"):
        edge_id = row.get("id") if table == "transitions" else row.get("transition_id")
        edge = session.get(Transition, edge_id)
        return f"the {what} {node_merge._edge_label(session, edge)}" if edge else f"an {what}"
    label = row.get("label") or row.get("name") or row.get("title")
    return f"the {what} “{label}”" if label else f"the {what}"


def plan(session: Session, entry_id: int) -> Plan:
    """What undoing the merge would do and what blocks it. Changes nothing."""
    entry = _merge_entry(session, entry_id)
    value = entry.new_value or {}
    changes = [
        Change(r["table"], list(r["key"]), r.get("before"), r.get("after"))
        for r in (entry.old_value or {}).get("rows", [])
    ]
    found = Plan(
        entry,
        entry.record_id,
        value.get("from_id", ""),
        value.get("from_label") or "Untitled",
        value.get("into_label") or "Untitled",
        changes,
    )
    tables = Base.metadata.tables
    back: set[str] = set()  # calculations going back to A
    for change in changes:
        if change.table not in tables:
            continue
        table = tables[change.table]
        now = _now(session, table, change.key)
        shown = change.after or change.before or {}
        if change.after is None:
            if now is not None:
                found.blockers.append(f"{_name(session, change.table, now)} was made again")
        elif now is None:
            found.blockers.append(
                f"{_name(session, change.table, shown).capitalize()} was removed after the merge"
            )
        else:
            later = _later(change.before, change.after, now)
            if later:
                found.blockers.append(
                    f"{_name(session, change.table, now).capitalize()} was changed after the merge"
                    f" ({', '.join(c.replace('_', ' ') for c in later)})"
                )
        if change.table == "calculations":
            if change.before is not None and change.before.get("node_id") == found.from_id:
                back.add(str(change.key[0]))
                found.calculations.append(str(change.key[0]))
        elif change.table == "transitions":
            found.edges += 1
        elif change.table != "nodes":
            found.records += 1
    if back:
        later = session.scalars(
            select(Calculation).where(
                Calculation.geometry_source_id.in_(back), Calculation.id.not_in(back)
            )
        )
        listed = {c.key[0] for c in changes if c.table == "calculations"}
        for calculation in later:
            if calculation.id not in listed:
                found.blockers.append(
                    f"The {calculation.type} calculation on “{node_merge.name(calculation.node)}” "
                    f"was run after the merge at the geometry of a calculation from "
                    f"“{found.from_label}”"
                )
    found.blockers = list(dict.fromkeys(found.blockers))
    return found


# ---------- the write ----------


def apply(session: Session, found: Plan) -> HistoryEntry:
    """Undo the merge planned; refused while something blocks it."""
    if found.blockers:
        raise UndoMergeBlocked(found.blockers)
    tables = Base.metadata.tables
    order = {t.name: i for i, t in enumerate(Base.metadata.sorted_tables)}
    changes = sorted((c for c in found.changes if c.table in tables), key=lambda c: order[c.table])
    # Rows the merge added go first, children before parents; then, parents before children,
    # the rows it removed come back and the rows it changed get their columns back.
    for change in reversed(changes):
        if change.before is None:
            table = tables[change.table]
            session.execute(delete(table).where(_where(table, change.key)))
    for change in changes:
        table = tables[change.table]
        if change.before is not None and change.after is None:
            session.execute(insert(table).values(_decoded(table, change.before)))
        elif change.before is not None and change.after is not None:
            now = _now(session, table, change.key) or {}
            values = _decoded(table, _put_back(change.before, change.after, now))
            if values:
                session.execute(update(table).where(_where(table, change.key)).values(values))
    for calculation_id, mapping in ((found.entry.old_value or {}).get("modes") or {}).items():
        stored = session.get(NormalModes, calculation_id)
        if stored is not None:
            data = atom_maps.modes(unpack_modes(stored.data), atom_maps.inverse(mapping))
            session.execute(
                update(NormalModes)
                .where(NormalModes.calculation_id == calculation_id)
                .values(data=pack_modes(data))
            )
    entry = history.record(
        session,
        "node",
        found.into_id,
        UNDO_ACTION,
        new={
            "entry_id": found.entry.id,
            "from_id": found.from_id,
            "from_label": found.from_label,
            "into_label": found.into_label,
            "calculations": len(found.calculations),
        },
    )
    session.flush()
    session.expire_all()
    return entry


def undo(session: Session, entry_id: int) -> tuple[Plan, HistoryEntry]:
    found = plan(session, entry_id)
    return found, apply(session, found)
