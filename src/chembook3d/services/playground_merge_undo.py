"""Undoing a playground's merge (D235j, FR-INV-07), as an import's undo (D102).

The merge's history entry (record type "playground", action "merge") keeps what the merge
wrote, row by row (`playground_merge._undo_data`): the rows it inserted, the columns it updated
with their values before and after, and the rows it deleted whole, cascades included. Undoing it
is one new change in one transaction on the open original, previewed the same way in a
transaction that is rolled back:

1. Rows the merge inserted are deleted. Refused when such a row was changed since, or when a
   row the merge did not insert uses it now (a foreign key, or the ids analyses keep in JSON).
   Levels of theory, stored files and note pictures it brought are kept when something else
   uses them now.
2. Columns it updated go back to their value before; one changed again since keeps its later
   value and is reported as kept (D102's "kept"). The rows a record owns (an edge's species, a
   selectivity's outcomes) count as one field of it: put back together, or kept together.
3. Rows it deleted are inserted again with their ids and values; refused when their id or
   name is taken now, or a record they need is gone.
4. Foreign keys are checked at the end of the transaction, and every row the undo did not mean
   to touch must be as it was (no cascade into later work); anything else refuses it.

The merge's entries stay; the undo adds one entry. Merges made before the merge entry kept what
it wrote ("inserted_rows", "updated_to") tell later work from the history instead: an entry after
the merge's, not copied by it, naming the record (and the field, for an updated column).
"""

import base64
import json
import shutil
import sqlite3
from collections import defaultdict
from contextlib import closing
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy import DateTime, LargeBinary, Table, delete, insert, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from chembook3d import autonomy, playgrounds
from chembook3d.investigation import (
    BACKUP_DIR,
    DB_NAME,
    FILES_DIR,
    Investigation,
    InvestigationError,
    InvestigationOwnership,
    acquire_ownership,
)
from chembook3d.models import Base, HistoryEntry
from chembook3d.services import energies, history
from chembook3d.services.notes import images_in
from chembook3d.services.playground_merge import (
    BY_TYPE,
    FIELD_ALIASES,
    HIDDEN,
    IGNORED,
    KINDS,
    Child,
    Key,
    Kind,
    Rows,
    _foreign_keys,
    _json_refs,
    _json_safe,
    _Labels,
    _primary,
    _violations,
    read_rows,
)

RECORD_TYPE = "playground"
MERGE_ACTION = "merge"
UNDO_ACTION = "undo_merge"

THERMO = BY_TYPE["thermochemistry"]
THERMO_KEY: Key = ("investigation",)
# The table of each kind of record, and the tables of the rows records own.
TOP = {kind.table: kind for kind in KINDS}
OWNED: dict[str, tuple[Kind, Child, Child | None]] = {}
for _kind in KINDS:
    for _top in _kind.children:
        OWNED[_top.table] = (_kind, _top, None)
        if _top.nested is not None:
            OWNED[_top.nested.table] = (_kind, _top, _top.nested)
# Tables whose rows name nodes, groups and branches in their JSON (`_json_refs`).
JSON_TARGETS = ("nodes", "group_nodes", "branches")
# Columns never reported as put back or kept: when a row was written, and its creation order.
UNREPORTED = {"updated_at", "seq"}
POSITION = {"pos_x", "pos_y"}


class UndoMergeError(InvestigationError):
    """Not a merge that can still be undone; the message says why."""


class UndoMergeBlocked(UndoMergeError):
    def __init__(self, result: dict[str, Any]):
        self.result = result
        super().__init__("; ".join(result["blockers"]))


# ---------- which merges can be undone ----------


def _is_merge(entry: HistoryEntry) -> bool:
    return (
        entry.record_type == RECORD_TYPE
        and entry.action == MERGE_ACTION
        and entry.merge_entry_id is None
    )


def _undone(session: Session) -> set[int]:
    """The merge entries an undo names."""
    values = session.scalars(
        select(HistoryEntry.new_value).where(
            HistoryEntry.record_type == RECORD_TYPE, HistoryEntry.action == UNDO_ACTION
        )
    )
    return {v.get("entry_id") for v in values if isinstance(v, dict)}


def undo_kinds(session: Session, entries: list[HistoryEntry]) -> dict[int, str]:
    """ "merge" for each playground merge's entry not undone yet (whether something blocks its
    undo is only worked out when asked)."""
    merges = [e.id for e in entries if _is_merge(e)]
    if not merges:
        return {}
    undone = _undone(session)
    return {i: "merge" for i in merges if i not in undone}


def _merge_entry(session: Session, entry_id: int) -> HistoryEntry:
    entry = session.get(HistoryEntry, entry_id)
    if entry is None or not _is_merge(entry) or not isinstance(entry.old_value, dict):
        raise UndoMergeError("This history entry is not a playground's merge")
    if entry_id in _undone(session):
        raise UndoMergeError("This merge was undone already")
    return entry


# ---------- values ----------


def _decoded(table: Table, row: dict[str, Any]) -> dict[str, Any]:
    """A row as the merge entry keeps it, back as the database takes it: times from ISO text,
    bytes from base64. Columns this schema no longer has are left out."""
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


def _key(table: Table, row: dict[str, Any]) -> Key:
    return tuple(row.get(c) for c in _primary(table))


def _differs(now: dict[str, Any], wrote: dict[str, Any]) -> bool:
    """Whether a row differs from what the merge wrote, layout and bookkeeping left out (a card
    moved since is no change to undo against)."""
    current = _json_safe(now)
    return any(current.get(c) != v for c, v in wrote.items() if c in now and c not in IGNORED)


def _reported(column: str) -> str | None:
    if column in UNREPORTED:
        return None
    return "position" if column in POSITION else column


# ---------- the history since the merge (older merges) ----------


class _Later:
    """The records the history names after the merge's entry, outside the entries it copied,
    with the fields named (None: the record as a whole)."""

    def __init__(self, session: Session, merge_id: int):
        self.fields: dict[tuple[str, str], set[str | None]] = defaultdict(set)
        query = select(HistoryEntry).where(HistoryEntry.id > merge_id).order_by(HistoryEntry.id)
        for entry in session.scalars(query):
            if entry.merge_entry_id == merge_id:
                continue
            record_id = entry.record_id
            if entry.record_type == "note":  # on the node; the note's id is in the snapshot
                snapshot = entry.new_value if isinstance(entry.new_value, dict) else None
                snapshot = snapshot or (
                    entry.old_value if isinstance(entry.old_value, dict) else {}
                )
                record_id = snapshot.get("id") or record_id
            elif entry.record_type == THERMO.record_type:
                record_id = THERMO_KEY[0]
            name = entry.field
            if name:
                name = FIELD_ALIASES.get((entry.record_type, name), name)
            self.fields[(entry.record_type, str(record_id))].add(name or None)

    def record(self, kind: Kind, key: Key) -> bool:
        return (kind.record_type, str(key[0])) in self.fields

    def field(self, kind: Kind, key: Key, name: str) -> bool:
        found = self.fields.get((kind.record_type, str(key[0])))
        return found is not None and (name in found or None in found)


# ---------- the plan ----------


Record = tuple[Kind, Key]


@dataclass
class _Plan:
    blockers: list[str] = field(default_factory=list)
    deletes: dict[str, set[Key]] = field(default_factory=lambda: defaultdict(set))
    restores: dict[str, dict[Key, dict[str, Any]]] = field(
        default_factory=lambda: defaultdict(dict)
    )
    inserts: dict[str, dict[Key, dict[str, Any]]] = field(default_factory=lambda: defaultdict(dict))
    removed: list[str] = field(default_factory=list)
    restored: list[str] = field(default_factory=list)
    put_back: dict[Record, list[str]] = field(default_factory=dict)
    kept: list[tuple[Record, str]] = field(default_factory=list)

    def block(self, reason: str) -> None:
        if reason not in self.blockers:
            self.blockers.append(reason)

    def restore(self, table: str, key: Key, values: dict[str, Any]) -> None:
        self.restores[table].setdefault(key, {}).update(values)

    def touched(self, table: str, key: Key) -> bool:
        return (
            key in self.deletes[table] or key in self.restores[table] or key in self.inserts[table]
        )


class _Merge:
    """The merge's entry read back, beside the investigation as it is now."""

    def __init__(self, session: Session, entry: HistoryEntry):
        self.entry = entry
        tables = Base.metadata.tables
        data: dict[str, Any] = entry.old_value
        self.now = read_rows(session.connection())
        self.inserted: dict[str, list[Key]] = {
            t: [tuple(k) for k in keys]
            for t, keys in (data.get("inserted") or {}).items()
            if t in self.now
        }
        self.updated: dict[str, dict[Key, dict[str, Any]]] = {
            t: {tuple(json.loads(k)): cols for k, cols in rows.items()}
            for t, rows in (data.get("updated") or {}).items()
            if t in self.now
        }
        self.deleted: dict[str, dict[Key, dict[str, Any]]] = {}
        for t, rows in (data.get("deleted") or {}).items():
            if t in self.now:
                decoded = [_decoded(tables[t], row) for row in rows]
                self.deleted[t] = {_key(tables[t], row): row for row in decoded}
        # What the merge wrote; None for a merge made before it was kept (then: the history).
        wrote = data.get("inserted_rows")
        self.wrote: dict[str, dict[Key, dict[str, Any]]] | None = None
        if isinstance(wrote, dict):
            self.wrote = {
                t: {_key(tables[t], row): row for row in rows}
                for t, rows in wrote.items()
                if t in self.now
            }
        updated_to = data.get("updated_to")
        self.updated_to: dict[str, dict[Key, dict[str, Any]]] | None = None
        if isinstance(updated_to, dict):
            self.updated_to = {
                t: {tuple(json.loads(k)): cols for k, cols in rows.items()}
                for t, rows in updated_to.items()
                if t in self.now
            }
        self.later = _Later(session, entry.id)
        self.inserted_keys = {t: set(keys) for t, keys in self.inserted.items()}
        gone: Rows = defaultdict(dict, self.deleted)
        added: Rows = defaultdict(dict, self.wrote or {})
        self.labels = _Labels(self.now, gone, added)
        value = entry.new_value if isinstance(entry.new_value, dict) else {}
        self.change_labels = {
            c["id"]: c["label"]
            for c in value.get("changes") or []
            if isinstance(c, dict) and c.get("id") and c.get("label")
        }

    # Records and their names

    def row(self, table: str, key: Key) -> dict[str, Any] | None:
        return self.labels.row(table, key)

    def owner(self, table: str, row: dict[str, Any]) -> tuple[Kind, Key, str | None] | None:
        """The record a row belongs to, and the field of it the row is (None: the record's own
        row); None for a level, a stored file or a note picture."""
        if table == THERMO.table:
            return THERMO, THERMO_KEY, None
        if table in TOP:
            return TOP[table], _key(Base.metadata.tables[table], row), None
        if table in OWNED:
            kind, top, nested = OWNED[table]
            parent = row.get((nested or top).parent)
            if nested is not None:
                outcome = self.row(top.table, (parent,)) or {}
                parent = outcome.get(top.parent)
            return kind, (parent,), top.field
        return None

    def label(self, kind: Kind, key: Key, review: bool = False) -> str:
        """A record's name as it is now (or was, for one gone); `review`: as the merge's review
        named it, when it did."""
        if kind is THERMO:
            return "Thermochemistry settings"
        row = self.row(kind.table, key)
        if review:
            record_id = (row or {}).get("name") if kind.by_name else key[0]
            found = self.change_labels.get(f"{kind.record_type}:{record_id}")
            if found:
                return found
        return self.labels.of(kind, key, row)

    def added_by_merge(self, kind: Kind, key: Key) -> bool:
        return kind is not THERMO and key in self.inserted_keys.get(kind.table, set())

    def removed_by_merge(self, kind: Kind, key: Key) -> bool:
        return kind is not THERMO and key in self.deleted.get(kind.table, {})


def _record_label(merge: _Merge, record: Record) -> str:
    return merge.label(*record)


def _updated_columns(merge: _Merge, plan: _Plan, table: str, key: Key, before: dict) -> None:
    """Rule 2 for a record's own row: each column back to its value before, unless changed
    since (kept)."""
    row = merge.now[table].get(key)
    if row is None:
        return  # gone since: nothing to put back, nothing to report
    target = Base.metadata.tables[table]
    owner = merge.owner(table, row)
    record = (owner[0], owner[1]) if owner else None
    current = _json_safe(row)
    wrote = (
        (merge.updated_to or {}).get(table, {}).get(key) if merge.updated_to is not None else None
    )
    values: dict[str, Any] = {}
    put: list[str] = []
    kept: list[str] = []
    for column, was in before.items():
        if column not in target.c or current.get(column) == was:
            continue
        if wrote is not None:
            same = current.get(column) == wrote.get(column)
        elif merge.updated_to is not None:
            same = False  # the merge entry does not say what it wrote here
        else:
            same = record is None or not merge.later.field(record[0], record[1], column)
        name = _reported(column)
        if same:
            values[column] = was
            if name and name not in put:
                put.append(name)
        elif name and name not in kept:
            kept.append(name)
    if kept and "updated_at" in values:
        del values["updated_at"]  # the row keeps a later change: it was written since
    if values:
        plan.restore(table, key, _decoded(target, values))
    if record is None or owner is None or owner[2] is not None:
        return
    if put:
        fields = plan.put_back.setdefault(record, [])
        fields.extend(f for f in put if f not in fields)
    for name in kept:
        plan.kept.append((record, name))


def _owned_rows(merge: _Merge, plan: _Plan) -> None:
    """Rule 2 for the rows records the merge kept own (an edge's species, a selectivity's
    outcomes): one field of the record, put back whole unless changed since."""
    groups: dict[tuple[Kind, Key, str], dict[str, list]] = defaultdict(
        lambda: {"insert": [], "update": [], "delete": []}
    )

    def group(table: str, row: dict[str, Any]) -> dict[str, list] | None:
        owner = merge.owner(table, row)
        if owner is None or owner[2] is None:
            return None
        kind, key, name = owner
        if merge.added_by_merge(kind, key) or merge.removed_by_merge(kind, key):
            return None  # goes, or comes back, with its record (rules 1 and 3)
        return groups[(kind, key, name)]

    for table, keys in merge.inserted.items():
        if table not in OWNED:
            continue
        for key in keys:
            # One gone since is found by what the merge wrote (then the field was changed).
            row = merge.now[table].get(key) or (merge.wrote or {}).get(table, {}).get(key)
            found = group(table, row) if row is not None else None
            if found is not None:
                found["insert"].append((table, key))
    for table, rows in merge.updated.items():
        if table not in OWNED:
            continue
        for key, before in rows.items():
            row = merge.now[table].get(key)
            found = group(table, row) if row is not None else None
            if found is not None:
                found["update"].append((table, key, before))
    for table, rows in merge.deleted.items():
        if table not in OWNED:
            continue
        for key, row in rows.items():
            found = group(table, row)
            if found is not None:
                found["delete"].append((table, key, row))

    for (kind, key, name), parts in groups.items():
        changed = merge.later.field(kind, key, name)
        for table, row_key in parts["insert"]:
            now = merge.now[table].get(row_key)
            wrote = (merge.wrote or {}).get(table, {}).get(row_key)
            if now is None or (wrote is not None and _differs(now, wrote)):
                changed = True
        for table, row_key, _before in parts["update"]:
            now = merge.now[table].get(row_key)
            wrote = (merge.updated_to or {}).get(table, {}).get(row_key)
            if now is None or (wrote is not None and _differs(now, wrote)):
                changed = True
        for table, row_key, _row in parts["delete"]:
            if row_key in merge.now[table]:
                changed = True
        record = (kind, key)
        if changed:
            plan.kept.append((record, name))
            continue
        for table, row_key in parts["insert"]:
            plan.deletes[table].add(row_key)
        for table, row_key, before in parts["update"]:
            plan.restore(table, row_key, _decoded(Base.metadata.tables[table], before))
        for table, row_key, row in parts["delete"]:
            plan.inserts[table][row_key] = row
        fields = plan.put_back.setdefault(record, [])
        if name not in fields:
            fields.append(name)


def _inserted_rows(merge: _Merge, plan: _Plan) -> set[tuple[str, Key]]:
    """Rule 1: the rows the merge inserted, with their records, are deleted unless changed since.
    Returns the levels, files and pictures it inserted that are still there (deleted only when
    nothing uses them)."""
    hidden: set[tuple[str, Key]] = set()
    for table, keys in merge.inserted.items():
        for key in keys:
            row = merge.now[table].get(key)
            if row is None:
                continue  # gone already
            if table in HIDDEN:
                hidden.add((table, key))
                continue
            owner = merge.owner(table, row)
            if owner is None:
                continue
            kind, record_key, name = owner
            if name is not None and not merge.added_by_merge(kind, record_key):
                continue  # a field of a record the merge kept (_owned_rows)
            wrote = (merge.wrote or {}).get(table, {}).get(key)
            if merge.wrote is not None and wrote is not None:
                changed = _differs(row, wrote)
            else:
                changed = merge.later.record(kind, record_key)
            if changed:
                plan.block(f"{merge.label(kind, record_key)} was changed after the merge")
            plan.deletes[table].add(key)
    return hidden


def _deleted_rows(merge: _Merge, plan: _Plan) -> None:
    """Rule 3: the rows the merge deleted come back with their ids and values."""
    for table, rows in merge.deleted.items():
        for key, row in rows.items():
            owner = merge.owner(table, row)
            if owner is not None and owner[2] is not None:
                if not merge.removed_by_merge(owner[0], owner[1]):
                    continue  # a field of a record the merge kept (_owned_rows)
            if key in merge.now[table]:
                what = merge.label(owner[0], owner[1]) if owner else "A record"
                plan.block(f"{what} cannot be brought back: its id is taken now")
                continue
            plan.inserts[table][key] = row


def _projected(merge: _Merge, plan: _Plan) -> Rows:
    """The rows as the undo would leave them (before the levels, files and pictures it may
    delete)."""
    found: Rows = {}
    for table, rows in merge.now.items():
        doomed = plan.deletes.get(table, set())
        restores = plan.restores.get(table, {})
        here = {k: {**row, **restores.get(k, {})} for k, row in rows.items() if k not in doomed}
        here.update(plan.inserts.get(table, {}))
        found[table] = here
    return found


def _uses(merge: _Merge, plan: _Plan, projected: Rows) -> set[tuple[str, Key]]:
    """Rule 1's refusal when a row the undo leaves uses a row it deletes; returns the levels,
    files and pictures those rows use."""
    used: set[tuple[str, Key]] = set()
    for table, rows in projected.items():
        for row in rows.values():
            targets: list[tuple[str, Key]] = []
            for column, target, _ondelete in _foreign_keys(table):
                if row.get(column) is not None:
                    targets.append((target, (row[column],)))
            for ids in _json_refs(table, row).values():
                targets.extend((t, (i,)) for i in ids for t in JSON_TARGETS)
            if table in energies.LEVEL_KEY_TABLES and row.get("level"):  # D251
                ids = energies.key_level_ids(row["level"])
                targets.extend(("levels_of_theory", (p,)) for p in ids)
            if table == "node_notes":
                targets.extend(("note_images", (i,)) for i in images_in(row.get("body") or ""))
            for target, target_key in targets:
                if target in HIDDEN:
                    used.add((target, target_key))
                    continue
                if target_key not in plan.deletes.get(target, set()):
                    continue
                gone = merge.owner(target, merge.now[target].get(target_key) or {})
                user = merge.owner(table, row)
                if gone is None or user is None:
                    continue
                gone_label = merge.label(gone[0], gone[1])
                if (user[0], user[1]) == (gone[0], gone[1]):
                    plan.block(f"{gone_label} was changed after the merge")
                else:
                    plan.block(
                        f"{merge.label(user[0], user[1])} uses {gone_label}, added by the merge"
                    )
    return used


def _unique(table: Table) -> list[list[str]]:
    found = [[c.name] for c in table.c if c.unique]
    for constraint in table.constraints:
        names = [c.name for c in getattr(constraint, "columns", [])]
        if type(constraint).__name__ == "UniqueConstraint" and names and names not in found:
            found.append(names)
    return found


def _fits(merge: _Merge, plan: _Plan, projected: Rows) -> None:
    """Rule 3's refusals: a row brought back whose name is taken now, or that needs a record
    gone since; and a column put back that points to a record gone since."""
    tables = Base.metadata.tables
    for table, rows in plan.inserts.items():
        target = tables[table]
        for key, row in rows.items():
            owner = merge.owner(table, row)
            what = merge.label(owner[0], owner[1]) if owner else "A record"
            for columns in _unique(target):
                values = tuple(row.get(c) for c in columns)
                if any(v is None for v in values):
                    continue
                for other_key, other in projected[table].items():
                    if other_key != key and tuple(other.get(c) for c in columns) == values:
                        taken = " and ".join(str(v) for v in values)
                        plan.block(f"{what} cannot be brought back: “{taken}” is taken now")
                        break
    for table, changes in [*plan.inserts.items(), *plan.restores.items()]:
        for key, values in changes.items():
            row = projected[table].get(key)
            if row is None:
                continue
            owner = merge.owner(table, row)
            what = merge.label(owner[0], owner[1]) if owner else "A record"
            for column, parent, _ondelete in _foreign_keys(table):
                value = values.get(column)
                if value is None or (value,) in projected[parent]:
                    continue
                needed = merge.labels.any(value) if parent not in HIDDEN else "a record"
                plan.block(f"{what} cannot be put back: it needs {needed}, which is gone now")


def _listed(merge: _Merge, plan: _Plan) -> None:
    """What the undo removes and brings back, by record, as the merge's review named them."""
    for table, keys in plan.deletes.items():
        kind = TOP.get(table)
        if kind is None or kind is THERMO:
            continue
        for key in sorted(keys, key=str):
            plan.removed.append(merge.label(kind, key, review=True))
    for table, rows in plan.inserts.items():
        kind = TOP.get(table)
        if kind is None or kind is THERMO:
            continue
        for key in sorted(rows, key=str):
            plan.restored.append(merge.label(kind, key, review=True))


def _ordered(merge: _Merge, labels: list[str], order: list[str]) -> list[str]:
    """Labels in the merge review's order where it named them."""
    rank = {label: i for i, label in enumerate(order)}
    return sorted(labels, key=lambda label: (rank.get(label, len(rank)), label))


def plan(session: Session, entry: HistoryEntry) -> tuple[_Merge, _Plan]:
    """What undoing the merge would do and what blocks it. Changes nothing."""
    merge = _Merge(session, entry)
    found = _Plan()
    hidden = _inserted_rows(merge, found)
    _owned_rows(merge, found)
    for table, rows in merge.updated.items():
        if table in OWNED:
            continue
        for key, before in rows.items():
            _updated_columns(merge, found, table, key, before)
    _deleted_rows(merge, found)
    projected = _projected(merge, found)
    used = _uses(merge, found, projected)
    for table, key in hidden:
        if (table, key) not in used:
            found.deletes[table].add(key)
    _fits(merge, found, projected)
    _listed(merge, found)
    order = list(merge.change_labels.values())
    found.removed = _ordered(merge, found.removed, order)
    found.restored = _ordered(merge, found.restored, order)
    return merge, found


# ---------- writing ----------


def _where(table: Table, key: Key):
    return [table.c[c] == v for c, v in zip(_primary(table), key, strict=True)]


def _write(session: Session, found: _Plan) -> str | None:
    """The plan's writes, in the session's transaction; why one failed, if one did."""
    tables = Base.metadata.tables
    try:
        for table, keys in found.deletes.items():
            for key in keys:
                session.execute(delete(tables[table]).where(*_where(tables[table], key)))
        for table, rows in found.restores.items():
            for key, values in rows.items():
                if values:
                    target = tables[table]
                    session.execute(update(target).where(*_where(target, key)).values(**values))
        for table, rows in found.inserts.items():
            for row in rows.values():
                session.execute(insert(tables[table]).values(**row))
    except IntegrityError as exc:  # a unique name or a CHECK; the caller rolls back
        return f"The undo could not be written: {exc.orig}"
    return None


def _broken(session: Session, merge: _Merge, already: set[tuple]) -> list[str]:
    """The foreign key check at the end of the undo's transaction, as blockers."""
    found = []
    connection = session.connection()
    for table, rowid, parent, _fk in _violations(session) - already:
        name = "A record"
        if table in Base.metadata.tables and "id" in Base.metadata.tables[table].c:
            row = connection.exec_driver_sql(
                f'SELECT id FROM "{table}" WHERE rowid = ?', (rowid,)
            ).first()
            if row is not None:
                name = merge.labels.any(row[0])
        found.append(f"{name} ({table}) would point to a {parent} record that is gone now")
    return sorted(set(found))


def _untouched(merge: _Merge, found: _Plan, after: Rows) -> list[str]:
    """Every row the plan does not name must be as it was: a cascade (ON DELETE CASCADE or SET
    NULL) into later work refuses the undo."""
    blockers = []
    for table, before in merge.now.items():
        now = after.get(table, {})
        for key in set(before) | set(now):
            if found.touched(table, key) or before.get(key) == now.get(key):
                continue
            row = before.get(key) or now.get(key) or {}
            owner = merge.owner(table, row)
            what = merge.label(owner[0], owner[1]) if owner else f"a record ({table})"
            change = "remove" if key not in now else "change"
            blockers.append(f"Undoing the merge would also {change} {what}")
    return list(dict.fromkeys(blockers))


# ---------- the playground ----------


def _playground(investigation: Investigation, entry: HistoryEntry) -> Path | None:
    """The merged playground's folder when it is still where it was, holding that playground."""
    value = entry.new_value if isinstance(entry.new_value, dict) else {}
    relative = value.get("folder")
    if not isinstance(relative, str) or not relative:
        return None
    original = investigation.folder.resolve()
    folder = (original / relative).resolve()
    if folder.parent != original / playgrounds.PLAYGROUNDS_DIR:
        return None
    found = playgrounds._read(folder / DB_NAME, "SELECT uuid FROM investigation_info WHERE id = 1")
    if not found or found[0][0] != entry.record_id:
        return None
    return folder


def _clear_merged(folder: Path) -> bool:
    """The playground is not merged any more: it opens writable and can be merged again."""
    try:
        with closing(sqlite3.connect(folder / DB_NAME)) as db:
            db.execute("UPDATE investigation_info SET playground_merged_at = NULL WHERE id = 1")
            db.commit()
        return True
    except sqlite3.Error:
        return False


def _remove_linked(folder: Path, source_ids: list[str], after: Rows) -> None:
    """The `files/<id>/` folders the merge linked in, once their stored file is gone (as an
    import's undo removes its copy)."""
    files = folder / FILES_DIR
    for source_id in source_ids:
        if (source_id,) in after.get("source_files", {}):
            continue
        directory = files / source_id
        if directory.parent == files and source_id not in ("", ".", ".."):
            shutil.rmtree(directory, ignore_errors=True)


# ---------- undo ----------


def undo(investigation: Investigation, entry_id: int, dry_run: bool = False) -> dict[str, Any]:
    """Undo a playground's merge in the open original (D235j). A dry run (the preview) writes
    everything in a transaction that is rolled back. Otherwise the original is backed up first,
    the playground's lease is held from before the commit until it is marked not merged, and
    the folders of the files the merge linked in are removed after the commit. Refused with
    UndoMergeBlocked, changing nothing, when anything blocks it."""
    session = investigation.sessions()
    backup: str | None = None
    owner: InvestigationOwnership | None = None
    committed = False
    after: Rows = {}
    try:
        entry = _merge_entry(session, entry_id)
        value = entry.new_value if isinstance(entry.new_value, dict) else {}
        name = str(value.get("name") or "the playground")
        merge, found = plan(session, entry)
        folder = _playground(investigation, entry)
        blockers = list(found.blockers)
        if folder is not None:
            try:
                owner = acquire_ownership(folder)
            except InvestigationError:
                blockers.append(f"Close {name} in the other app first")
            if dry_run and owner is not None:
                owner.release()
                owner = None
        counts = {
            "removed": len(found.removed),
            "restored": len(found.restored),
            "put_back": len(found.put_back),
            "kept": len(found.kept),
        }
        result: dict[str, Any] = {
            "name": name,
            "blockers": blockers,
            "removed": found.removed,
            "restored": found.restored,
            "put_back": [
                {"label": _record_label(merge, record), "fields": fields}
                for record, fields in found.put_back.items()
            ],
            "kept": [
                {"label": _record_label(merge, record), "field": name_}
                for record, name_ in found.kept
            ],
            "counts": counts,
            "backup": None,
            "playground_back": folder is not None,
            "history_id": None,
        }
        if blockers:
            if dry_run:
                return result
            raise UndoMergeBlocked(result)
        if not dry_run:
            # D235j: the original is backed up before anything is written to it, as for a merge.
            backup = autonomy.backup_database(investigation.folder, label="undo-merge")
        undone = history.record(
            session,
            RECORD_TYPE,
            entry.record_id,
            UNDO_ACTION,
            new={"name": name, "entry_id": entry.id, "counts": counts},
            source="manual",
        )
        session.flush()  # the first write: SQLite's transaction starts here
        session.connection().exec_driver_sql("PRAGMA defer_foreign_keys = ON")
        already = _violations(session)
        failed = _write(session, found)
        if failed:
            blockers = [failed]
        else:
            after = read_rows(session.connection())
            blockers = _broken(session, merge, already) + _untouched(merge, found, after)
        result["blockers"] = blockers
        if dry_run:
            return result
        if blockers:
            raise UndoMergeBlocked(result)
        result["history_id"] = undone.id
        linked = [i for i in entry.old_value.get("linked_files") or [] if isinstance(i, str)]
        session.commit()
        committed = True
    except BaseException:
        session.rollback()
        if backup is not None:  # nothing was undone: the copy is not needed
            (investigation.folder / BACKUP_DIR / backup).unlink(missing_ok=True)
        if owner is not None:
            owner.release()
        raise
    finally:
        if not committed:
            session.rollback()
        session.close()
    try:
        _remove_linked(investigation.folder, linked, after)
        result["playground_back"] = folder is not None and _clear_merged(folder)
    finally:
        if owner is not None:
            owner.release()
    result["backup"] = backup
    return result
