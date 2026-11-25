"""Merging a playground into its original (D235h, FR-INV-06).

A merge compares three versions of the same records, which keep their ids in a playground
(D235c): the base (`<playground>/playground/base.sqlite`, the original as it was when the
playground was made, never written), the playground as it is, and the original as it is. Each is
read whole with SQLAlchemy Core into rows keyed by primary key.

What the person reviews is one change per top-level record (a node, a calculation, a note, an
edge, a step, a branch, a group, a sub-investigation, an analysis, a named basis set or
dispersion, the thermochemistry settings), plus one change per canvas for the cards moved on it.
Rows a record owns (a calculation's results, an edge's species, a selectivity's outcomes) are
part of its value, compared without their own ids. Per record (B base, P playground, O original):

- in P only: added (ticked);
- in B and P, P changed: changed (ticked; only the fields P changed are written, so the
  original's own changes to other fields stay), a conflict when O changed one of the same fields
  to something else, or when O removed the record (ticking brings it back as P has it);
- in B, not in P: removed (not ticked, Q65), a conflict when O changed it since.

A conflict is never ticked by default: the person picks a side. A ticked change brings every
change it needs (a calculation its node, an edge both ends); levels of theory, stored files and
note pictures come along with what uses them and are never changes of their own.

The merge is one transaction on the original, with foreign keys checked at its end (PRAGMA
defer_foreign_keys), so the order of the writes does not matter; a preview runs the same and is
rolled back. It writes one "merge" entry first, with what its undo (D235j,
`playground_merge_undo`) needs, and copies the playground's own history entries of what it
merged, keeping their source and pointing to it.
"""

import base64
import json
import os
import shutil
import sqlite3
import tempfile
from collections import defaultdict
from collections.abc import Iterable
from contextlib import closing
from dataclasses import dataclass, field
from datetime import datetime
from functools import cache
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from sqlalchemy import Connection, Table, delete, func, insert, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from chembook3d import autonomy, playgrounds
from chembook3d.investigation import (
    BACKUP_DIR,
    DB_NAME,
    FILES_DIR,
    Investigation,
    InvestigationError,
    _make_engine,
    _migrate,
    acquire_ownership,
)
from chembook3d.models import Base, HistoryEntry, utcnow
from chembook3d.services import energies, history, levels, proposals
from chembook3d.services.notes import images_in

Key = tuple
Rows = dict[str, dict[Key, dict[str, Any]]]  # table → primary key → row

# Tables that are no record of the merge: the history is copied entry by entry instead.
NOT_READ = {"history", "alembic_version"}
# Never compared (D235h): creation order (an added record is numbered after the original's),
# when a record was last written, and layout (card positions are their own change per canvas;
# a node's saved 3D orientation is not merged at all).
IGNORED = {"seq", "updated_at", "pos_x", "pos_y", "view_rotation"}
# Brought along with what uses them, never a change of their own.
HIDDEN = {"levels_of_theory", "source_files", "note_images"}
LEVEL_COLUMNS = ("program", "method", "basis", "dispersion", "solvation_model", "solvent")
THERMO_FIELDS = ("qh_temperature", "qh_cutoff", "standard_state", "geometry_tolerance")  # D252
# A history entry's field where it is not the column's name.
FIELD_ALIASES = {
    ("calculation", "level"): "level_id",
    ("calculation", "geometry_level"): "geometry_level_id",
    ("branch", "parent_ids"): "parents",
    ("group", "incoming_branch_ids"): "incoming",
}


class MergeError(InvestigationError):
    pass


class MergeBlocked(MergeError):
    def __init__(self, result: dict[str, Any]):
        self.result = result
        super().__init__("; ".join(result["blockers"]))


# ---------- what a change is ----------


@dataclass(frozen=True)
class Child:
    """Rows a record owns, compared as one field of it (`field`) without their own ids."""

    table: str
    parent: str  # the column pointing to the owner
    field: str
    nested: "Child | None" = None  # a selectivity's outcomes have members of their own


@dataclass(frozen=True)
class Kind:
    record_type: str
    table: str
    section: str  # "node": the section of the record's node
    noun: str  # the label's prefix, "" for a node
    children: tuple[Child, ...] = ()
    by_name: bool = False  # matched by name, not id (named basis sets and dispersions)


KINDS = (
    Kind("node", "nodes", "node", ""),
    Kind(
        "calculation",
        "calculations",
        "node",
        "Calculation",
        (
            Child("calculation_results", "calculation_id", "results"),
            Child("normal_modes", "calculation_id", "modes"),  # D247
        ),
    ),
    Kind("note", "node_notes", "node", "Note"),
    Kind(
        "transition",
        "transitions",
        "edges",
        "Edge",
        (Child("transition_species", "transition_id", "species"),),
    ),
    Kind("step", "reaction_steps", "steps", "Step"),
    Kind(
        "branch",
        "branches",
        "branches",
        "Branch",
        (Child("branch_parents", "branch_id", "parents"),),
    ),
    Kind(
        "group",
        "group_nodes",
        "groups",
        "Group",
        (Child("group_incoming_branches", "group_id", "incoming"),),
    ),
    Kind("sub_investigation", "sub_investigations", "subs", "Sub-investigation"),
    Kind(
        "selectivity",
        "selectivities",
        "analyses",
        "Selectivity",
        (
            Child(
                "selectivity_outcomes",
                "selectivity_id",
                "outcomes",
                Child("selectivity_members", "outcome_id", "members"),
            ),
        ),
    ),
    Kind("turnover", "turnovers", "analyses", "Turnover"),
    Kind("equilibrium", "conformer_equilibria", "analyses", "Conformer equilibrium"),
    Kind("energy_profile", "energy_profiles", "analyses", "Energy profile"),
    Kind(
        "alignment_set",
        "alignment_sets",
        "analyses",
        "Alignment set",
        (Child("alignment_set_atoms", "set_id", "atoms"),),
    ),
    Kind(
        "steric_profile",
        "steric_profiles",
        "analyses",
        "Steric profile",
        (Child("steric_profile_atoms", "profile_id", "atoms"),),
    ),
    Kind("custom_basis", "custom_bases", "settings", "Basis set", by_name=True),
    Kind("custom_dispersion", "custom_dispersions", "settings", "Dispersion", by_name=True),
    Kind("thermochemistry", "investigation_info", "settings", "Thermochemistry"),
)
BY_TYPE = {kind.record_type: kind for kind in KINDS}
CHILD_TABLES = {
    c.table for kind in KINDS for top in kind.children for c in (top, top.nested) if c is not None
}
SECTIONS = (
    ("edges", "Edges"),
    ("steps", "Reaction steps"),
    ("branches", "Branches"),
    ("groups", "Groups"),
    ("subs", "Sub-investigations"),
    ("analyses", "Analyses"),
    ("settings", "Settings"),
    ("canvas", "Canvas"),
)
# The cards on a canvas, and the column naming their canvas.
CARDS = (("nodes", "canvas_id"), ("group_nodes", "canvas_id"), ("sub_investigations", "parent_id"))


@dataclass
class Change:
    id: str
    record_type: str
    record_id: str
    kind: str  # added | changed | removed | conflict
    op: str  # what ticking it does: insert | update | delete | positions
    section: str
    order: tuple
    label: str
    summary: str
    fields: list[dict[str, Any]] = field(default_factory=list)
    conflict: str | None = None
    ticked: bool = False
    requires: set[str] = field(default_factory=set)
    blocked: str | None = None
    written: list[str] = field(default_factory=list)  # the fields an update writes
    unit: Kind | None = None
    key: Key = ()
    cards: list[tuple[str, Key]] = field(default_factory=list)  # a positions change's cards

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "record_type": self.record_type,
            "record_id": self.record_id,
            "kind": self.kind,
            "label": self.label,
            "summary": self.summary,
            "fields": self.fields,
            "conflict": self.conflict,
            "ticked": self.ticked,
            "requires": sorted(self.requires),
            "blocked": self.blocked,
        }


@dataclass
class Plan:
    base: Rows
    play: Rows
    orig: Rows
    changes: dict[str, Change]
    sections: list[tuple[str, str, list[str]]]
    entries: list[dict[str, Any]]  # the playground's history since it was made


# ---------- reading the three versions ----------


def _tables() -> list[Table]:
    return [t for t in Base.metadata.tables.values() if t.name not in NOT_READ]


def _primary(table: Table) -> list[str]:
    return [c.name for c in table.primary_key.columns]


def read_rows(connection: Connection) -> Rows:
    """Every row of every table but the history, keyed by primary key."""
    found: Rows = {}
    for table in _tables():
        keys = _primary(table)
        found[table.name] = {
            tuple(row[k] for k in keys): dict(row)
            for row in connection.execute(select(table)).mappings()
        }
    return found


def _read_database(path: Path, since: int | None = None) -> tuple[Rows, list[dict[str, Any]]]:
    """A database's rows, migrated to this app's schema first when it is older (D71), and its
    history entries after `since` when given."""
    engine = _make_engine(path)
    try:
        _migrate(engine, path, allow_upgrade=True)
        with engine.connect() as connection:
            rows = read_rows(connection)
            entries = []
            if since is not None:
                table = HistoryEntry.__table__
                query = select(table).where(table.c.id > since).order_by(table.c.id)
                entries = [dict(row) for row in connection.execute(query).mappings()]
        return rows, entries
    finally:
        engine.dispose()  # Windows keeps a file open until its connections are closed


def _read_base(folder: Path) -> Rows:
    """The base, read from a copy in a temporary folder: it is never written, not even to
    migrate it to this app's schema (D235c)."""
    source = folder / playgrounds.BASE_DIR / playgrounds.BASE_NAME
    if not source.is_file():
        raise MergeError("This playground has lost the copy of its original it was made from")
    work = Path(tempfile.mkdtemp(prefix="chembook3d-merge-"))
    try:
        copy = work / DB_NAME
        shutil.copyfile(source, copy)
        return _read_database(copy)[0]
    finally:
        shutil.rmtree(work, ignore_errors=True)


# ---------- values ----------


def _sort_key(value: Any) -> str:
    return json.dumps(value, sort_keys=True, default=str)


class _Index:
    """One version's rows by what owns them, and its records keyed for comparing."""

    def __init__(self, rows: Rows):
        self.rows = rows
        self.owned: dict[tuple[str, str], dict[Any, list[dict]]] = {}
        for kind in KINDS:
            for top in kind.children:
                for child in (top, top.nested):
                    if child is None or (child.table, child.parent) in self.owned:
                        continue
                    found = defaultdict(list)
                    for row in rows[child.table].values():
                        found[row[child.parent]].append(row)
                    self.owned[(child.table, child.parent)] = found

    def records(self, kind: Kind) -> dict[Key, dict[str, Any]]:
        if kind.record_type == "thermochemistry":
            info = self.rows["investigation_info"].get((1,))
            return {} if info is None else {("investigation",): info}
        if kind.by_name:
            return {(row["name"],): row for row in self.rows[kind.table].values()}
        return self.rows[kind.table]

    def children(self, child: Child, owner: Any) -> list[dict[str, Any]]:
        return self.owned[(child.table, child.parent)].get(owner, [])

    def value(
        self, kind: Kind, row: dict[str, Any] | None, gone: frozenset[str] = frozenset()
    ) -> dict[str, Any] | None:
        """A record as it is compared: its fields, and the rows it owns as fields. With `gone`
        (the records the playground removed), as deleting those leaves it: a reference that
        is set null on delete is None, an owned row deleted with them is left out. So what a
        removal does to other records comes with the removal, never as changes of their own."""
        if row is None:
            return None
        if kind.record_type == "thermochemistry":
            return {f: row[f] for f in THERMO_FIELDS}
        skip = IGNORED | ({"id"} if kind.by_name else set())
        value = {k: v for k, v in row.items() if k not in skip}
        for column, _target, ondelete in _foreign_keys(kind.table):
            if ondelete == "SET NULL" and value.get(column) in gone:
                value[column] = None
        for child in kind.children:
            value[child.field] = self._owned(child, row["id"], gone)
        return value

    def _owned(self, child: Child, owner: Any, gone: frozenset[str]) -> list[dict[str, Any]]:
        table = Base.metadata.tables[child.table]
        own = {"id", child.parent, "created_at"} & set(table.c.keys())
        cascades = [c for c, _t, ondelete in _foreign_keys(child.table) if ondelete == "CASCADE"]
        found = []
        for row in self.children(child, owner):
            if any(row.get(c) in gone for c in cascades):
                continue
            item = {k: v for k, v in row.items() if k not in own}
            if child.nested is not None:
                item[child.nested.field] = self._owned(child.nested, row["id"], gone)
            found.append(item)
        return sorted(found, key=_sort_key)


def _shown(value: Any) -> Any:
    """A value as the review shows it: JSON-safe, and a long one summarised."""
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, bytes):
        return f"({len(value)} bytes)"
    if isinstance(value, list | dict) and len(_sort_key(value)) > 300:
        return f"({len(value)} items)" if isinstance(value, list) else "(changed)"
    return value


def _short(value: Any) -> str:
    if value is None or value == "":
        return "none"
    if isinstance(value, bool):
        return "yes" if value else "no"
    text = str(value)
    return text if len(text) <= 40 else text[:39] + "…"


def _json_safe(row: dict[str, Any]) -> dict[str, Any]:
    """A row as the merge entry keeps it for the undo: times as ISO text, bytes as base64."""
    out = {}
    for k, v in row.items():
        if isinstance(v, datetime):
            v = v.isoformat()
        elif isinstance(v, bytes):
            v = base64.b64encode(v).decode("ascii")
        out[k] = v
    return out


# ---------- labels ----------


class _Labels:
    """Names of records for the review, from the playground first, then the original, then
    the base."""

    def __init__(self, *versions: Rows):
        self.versions = versions

    def row(self, table: str, key: Key) -> dict[str, Any] | None:
        for rows in self.versions:
            found = rows[table].get(key)
            if found is not None:
                return found
        return None

    def end(self, node_id: str | None, group_id: str | None) -> str:
        if node_id:
            return self.node(node_id)
        row = self.row("group_nodes", (group_id,))
        return (row or {}).get("label") or "a group"

    def node(self, node_id: str) -> str:
        row = self.row("nodes", (node_id,))
        return (row or {}).get("label") or "unnamed node"

    def level(self, level_id: str | None) -> str | None:
        row = self.row("levels_of_theory", (level_id,)) if level_id else None
        return levels.label(SimpleNamespace(**row)) if row else None  # type: ignore[arg-type]

    def of(self, kind: Kind, key: Key, row: dict[str, Any] | None) -> str:
        row = row or {}
        if kind.record_type == "node":
            return row.get("label") or "unnamed node"
        if kind.record_type == "calculation":
            what = (row.get("type") or "calculation").replace("_", " ")
            level = self.level(row.get("level_id"))
            return f"Calculation: {what}" + (f" {level}" if level else "")
        if kind.record_type == "note":
            title = row.get("title")
            return f"Note “{title}”" if title else f"Note on {self.node(row.get('node_id'))}"
        if kind.record_type == "transition":
            source = self.end(row.get("source_node_id"), row.get("source_group_id"))
            target = self.end(row.get("target_node_id"), row.get("target_group_id"))
            return f"Edge {source} → {target}"
        if kind.record_type == "thermochemistry":
            return "Thermochemistry settings"
        name = row.get("name") if "name" in row else row.get("label")
        return f"{kind.noun} “{name}”" if name else kind.noun

    def any(self, record_id: str) -> str:
        """Whatever record an id names, for a reason given in the review."""
        for kind in KINDS:
            if kind.by_name or kind.record_type == "thermochemistry":
                continue
            row = self.row(kind.table, (record_id,))
            if row is not None:
                return self.of(kind, (record_id,), row)
        return "a record"


# ---------- references ----------


@cache
def _foreign_keys(table: str) -> list[tuple[str, str, str | None]]:
    """(column, the table it points to, on delete) for each foreign key of a table."""
    return [
        (fk.parent.name, fk.column.table.name, fk.ondelete)
        for fk in Base.metadata.tables[table].foreign_keys
    ]


def _json_refs(table: str, row: dict[str, Any]) -> dict[str, list[str]]:
    """Ids of nodes, groups and branches a record names inside its JSON, by field."""
    found: dict[str, list[str]] = {}
    if table == "energy_profiles":
        paths = row.get("paths") or []
        found["paths"] = [
            i
            for p in paths
            if isinstance(p, dict)
            for i in [*(p.get("ids") or []), p.get("branch_id")]
            if isinstance(i, str)
        ]
        found["reference_id"] = [row["reference_id"]] if row.get("reference_id") else []
    elif table == "turnovers":
        found["path"] = [i for i in row.get("path") or [] if isinstance(i, str)]
    elif table == "conformer_equilibria":
        members = row.get("members") or []
        found["members"] = [m["id"] for m in members if isinstance(m, dict) and m.get("id")]
        found["start_id"] = [row["start_id"]] if row.get("start_id") else []
        found["next_ids"] = [i for i in row.get("next_ids") or [] if isinstance(i, str)]
    elif table == "selectivities":
        found["reference_id"] = [row["reference_id"]] if row.get("reference_id") else []
    return found


def _refs(
    kind: Kind, index: _Index, row: dict[str, Any], fields: Iterable[str] | None
) -> list[tuple[str, bool]]:
    """(id, through a foreign key) of every record the playground's version of a record points
    to, in the fields given (None: all of them); levels, files and pictures left out."""
    wanted = None if fields is None else set(fields)
    found: list[tuple[str, bool]] = []

    def from_row(table: str, item: dict[str, Any], owner_field: str | None) -> None:
        for column, target, _ondelete in _foreign_keys(table):
            if target in HIDDEN or target in CHILD_TABLES or item.get(column) is None:
                continue
            if wanted is None or (owner_field or column) in wanted:
                found.append((item[column], True))

    if kind.record_type == "thermochemistry":
        return found
    from_row(kind.table, row, None)
    for name, ids in _json_refs(kind.table, row).items():
        if wanted is None or name in wanted:
            found.extend((i, False) for i in ids)
    for top in kind.children:
        for child_row in index.children(top, row["id"]):
            from_row(top.table, child_row, top.field)
            if top.nested is not None:
                for nested in index.children(top.nested, child_row["id"]):
                    from_row(top.nested.table, nested, top.field)
    return found


def _cascade_refs(kind: Kind, index: _Index, row: dict[str, Any]) -> set[str]:
    """The records whose deletion would cascade to (a row of) this record."""
    found: set[str] = set()
    if kind.record_type == "thermochemistry":
        return found

    def from_row(table: str, item: dict[str, Any]) -> None:
        for column, _target, ondelete in _foreign_keys(table):
            if ondelete == "CASCADE" and item.get(column) is not None:
                found.add(item[column])

    from_row(kind.table, row)
    for top in kind.children:
        for child_row in index.children(top, row["id"]):
            from_row(top.table, child_row)
            if top.nested is not None:
                for nested in index.children(top.nested, child_row["id"]):
                    from_row(top.nested.table, nested)
    found.discard(row.get("id"))
    return {f for f in found if isinstance(f, str)}


# ---------- the plan ----------


def _record_id(kind: Kind, key: Key) -> str:
    return str(key[0])


def _summary(base: dict | None, play: dict | None, fields: Iterable[str]) -> str:
    parts = []
    for f in fields:
        old, new = (base or {}).get(f), (play or {}).get(f)
        if isinstance(old, list | dict) or isinstance(new, list | dict):
            parts.append(f"{f} changed")
        else:
            parts.append(f"{f}: {_short(old)} → {_short(new)}")
    return ", ".join(parts)


def _field_rows(
    names: Iterable[str], base: dict | None, play: dict | None, orig: dict | None, both: set[str]
) -> list[dict[str, Any]]:
    return [
        {
            "field": f,
            "base": _shown((base or {}).get(f)),
            "playground": _shown((play or {}).get(f)),
            "original": _shown((orig or {}).get(f)),
            "conflict": f in both,
        }
        for f in names
    ]


@dataclass
class _Versions:
    base: _Index
    play: _Index
    orig: _Index
    gone_play: frozenset[str]  # the records the playground removed
    gone_orig: frozenset[str]  # and those the original removed


def _gone(base: Rows, other: Rows) -> frozenset[str]:
    return frozenset(
        key[0]
        for table, rows in base.items()
        for key in rows
        if len(key) == 1 and isinstance(key[0], str) and key not in other[table]
    )


def _classify(kind: Kind, key: Key, v: _Versions, labels: _Labels) -> Change | None:
    """One record's change, or None when the playground did nothing to it (D235h). The base
    is compared with each side as that side's own removals left it (`_Index.value`)."""
    b_row, p_row, o_row = (index.records(kind).get(key) for index in (v.base, v.play, v.orig))
    b, p, o = v.base.value(kind, b_row), v.play.value(kind, p_row), v.orig.value(kind, o_row)
    b_play = v.base.value(kind, b_row, v.gone_play)  # what the playground started from
    b_orig = v.base.value(kind, b_row, v.gone_orig)  # and the original
    change = Change(
        id=f"{kind.record_type}:{_record_id(kind, key)}",
        record_type=kind.record_type,
        record_id=_record_id(kind, key),
        kind="",
        op="",
        section=kind.section,
        order=(),
        label=labels.of(kind, key, p_row or o_row or b_row),
        summary="",
        unit=kind,
        key=key,
    )
    if b is None and p is not None:
        if o is None:
            change.kind, change.op, change.summary, change.ticked = "added", "insert", "added", True
            return change
        differ = [f for f in p if p[f] != o.get(f)]
        if not differ:
            return None  # the original has it as it is
        # A record of the same name the original made on its own (a named basis set).
        change.kind, change.op = "conflict", "update"
        change.conflict = "The original has its own of this name"
        change.written = differ
        change.fields = _field_rows(differ, None, p, o, set(differ))
        change.summary = _summary(o, p, differ)
        return change
    if b is not None and b_play is not None and b_orig is not None and p is not None:
        changed = [f for f in p if p[f] != b_play.get(f)]
        if not changed:
            return None
        if o is None:
            change.kind, change.op = "conflict", "insert"
            change.conflict = "Removed in the original"
            change.written = changed
            change.fields = _field_rows(changed, b, p, None, set())
            change.summary = "removed in the original; " + _summary(b, p, changed)
            return change
        written = [f for f in changed if p[f] != o.get(f)]
        if not written:
            return None  # the original made the same change
        both = {f for f in written if o.get(f) != b_orig.get(f)}
        change.op, change.written = "update", written
        change.fields = _field_rows(written, b, p, o, both)
        change.summary = _summary(b, p, written)
        if both:
            change.kind, change.conflict = "conflict", "Changed in the original too"
        else:
            change.kind, change.ticked = "changed", True
        return change
    if b is not None and b_orig is not None and p is None and o is not None:
        change.op = "delete"
        if o == b_orig:
            change.kind, change.summary = "removed", "removed"  # unticked (Q65)
            return change
        changed = [f for f in o if o[f] != b_orig.get(f)]
        change.kind, change.conflict = "conflict", "Changed in the original since"
        change.fields = _field_rows(changed, b, None, o, set(changed))
        change.summary = "removed here; changed in the original: " + _summary(b, o, changed)
        return change
    return None


def _positions(base_ix: _Index, play_ix: _Index, orig_ix: _Index, labels: _Labels) -> list[Change]:
    """One change per canvas: the cards the playground moved on it."""
    canvases: dict[str | None, list[tuple[str, Key, bool, dict]]] = defaultdict(list)
    for table, canvas_column in CARDS:
        for key, p_row in play_ix.rows[table].items():
            b_row, o_row = base_ix.rows[table].get(key), orig_ix.rows[table].get(key)
            if b_row is None or o_row is None:
                continue
            if table == "nodes" and p_row["kind"] != "node":
                continue
            moved = (p_row["pos_x"], p_row["pos_y"]) != (b_row["pos_x"], b_row["pos_y"])
            here = (p_row["pos_x"], p_row["pos_y"]) != (o_row["pos_x"], o_row["pos_y"])
            if not moved or not here:
                continue
            theirs = (o_row["pos_x"], o_row["pos_y"]) != (b_row["pos_x"], b_row["pos_y"])
            canvases[p_row[canvas_column]].append((table, key, theirs, p_row))
    found = []
    for canvas, cards in canvases.items():
        record_id = canvas or "main"
        both = sum(1 for _t, _k, theirs, _r in cards if theirs)
        where = (
            ""
            if canvas is None
            else " in "
            + labels.of(
                BY_TYPE["sub_investigation"], (canvas,), labels.row("sub_investigations", (canvas,))
            )
        )
        count = f"{len(cards)} card{'s' if len(cards) != 1 else ''}"
        fields = []
        for table, key, theirs, p_row in cards:
            b_row, o_row = base_ix.rows[table][key], orig_ix.rows[table][key]
            name = p_row.get("label") or "unnamed"
            fields.append(
                {
                    "field": name,
                    "base": [b_row["pos_x"], b_row["pos_y"]],
                    "playground": [p_row["pos_x"], p_row["pos_y"]],
                    "original": [o_row["pos_x"], o_row["pos_y"]],
                    "conflict": theirs,
                }
            )
        found.append(
            Change(
                id=f"positions:{record_id}",
                record_type="positions",
                record_id=record_id,
                kind="conflict" if both else "changed",
                op="positions",
                section="canvas",
                order=(0 if canvas is None else 1, record_id),
                label=f"Positions of {count}{where}",
                summary=f"{count} moved" + (f"; {both} also moved in the original" if both else ""),
                fields=fields,
                conflict=f"{both} of them also moved in the original" if both else None,
                ticked=not both,
                cards=[(t, k) for t, k, _theirs, _r in cards],
            )
        )
    return found


def plan(base: Rows, play: Rows, orig: Rows, entries: list[dict[str, Any]]) -> Plan:
    """Every change the playground made, classified, with what each needs (D235h)."""
    base_ix, play_ix, orig_ix = _Index(base), _Index(play), _Index(orig)
    versions = _Versions(base_ix, play_ix, orig_ix, _gone(base, play), _gone(base, orig))
    labels = _Labels(play, orig, base)
    changes: list[Change] = []
    for kind in KINDS:
        keys = set(base_ix.records(kind)) | set(play_ix.records(kind))
        for key in keys:
            change = _classify(kind, key, versions, labels)
            if change is not None:
                changes.append(change)
    changes.extend(_positions(base_ix, play_ix, orig_ix, labels))
    by_id = {c.id: c for c in changes}
    _requirements(changes, base_ix, play_ix, orig_ix, labels)
    _blocked_names(changes, play_ix, orig_ix)
    sections = _sections(changes, base_ix, play_ix, orig_ix, labels)
    ordered = {cid: by_id[cid] for _k, _t, ids in sections for cid in ids}
    return Plan(base, play, orig, ordered, sections, entries)


def _requirements(
    changes: list[Change], base_ix: _Index, play_ix: _Index, orig_ix: _Index, labels: _Labels
) -> None:
    """What each change needs (`requires`), and the removals blocked because the original
    added something to the record since (deleting it would cascade that away)."""
    in_original = {
        key[0]
        for rows in orig_ix.rows.values()
        for key in rows
        if len(key) == 1 and isinstance(key[0], str)
    }
    brings = {c.key[0]: c.id for c in changes if c.op == "insert" and c.unit and not c.unit.by_name}
    removing = {c.key[0]: c for c in changes if c.op == "delete" and c.unit and not c.unit.by_name}
    for change in changes:
        if change.unit is None or change.op not in ("insert", "update"):
            continue
        row = play_ix.records(change.unit).get(change.key)
        if row is None:
            continue
        fields = None if change.op == "insert" else change.written
        for record_id, hard in _refs(change.unit, play_ix, row, fields):
            if record_id in in_original or record_id == change.key[0]:
                continue
            if record_id in brings:
                change.requires.add(brings[record_id])
            elif hard and change.blocked is None:
                change.blocked = f"Needs {labels.any(record_id)}, which the original no longer has"
    # A removal brings the removals of what its deletion takes with it, and is blocked by what
    # the original has added to the record since.
    added_by_original: dict[str, list[str]] = defaultdict(list)
    for kind in KINDS:
        for key, o_row in orig_ix.records(kind).items():
            b_row = base_ix.records(kind).get(key)
            new = _cascade_refs(kind, orig_ix, o_row)
            if b_row is not None:
                new -= _cascade_refs(kind, base_ix, b_row)
            for record_id in new:
                added_by_original[record_id].append(labels.of(kind, key, o_row))
    for change in changes:
        if change.op != "delete" or change.unit is None:
            continue
        o_row = orig_ix.records(change.unit).get(change.key)
        if o_row is None:
            continue
        for record_id in _cascade_refs(change.unit, orig_ix, o_row):
            if record_id in removing:
                removing[record_id].requires.add(change.id)
        added = added_by_original.get(change.key[0])
        if added:
            change.blocked = (
                "The original has added " + ", ".join(sorted(set(added))) + " to it since"
            )


def _blocked_names(changes: list[Change], play_ix: _Index, orig_ix: _Index) -> None:
    """A record whose name the original gave another record of the same kind since cannot be
    written (the names are unique)."""
    for change in changes:
        kind = change.unit
        if kind is None or kind.by_name or change.op not in ("insert", "update"):
            continue
        table = Base.metadata.tables[kind.table]
        if "name" not in table.c or not table.c.name.unique:
            continue
        if change.op == "update" and "name" not in change.written:
            continue
        name = (play_ix.records(kind).get(change.key) or {}).get("name")
        taken = any(
            row["name"] == name and key != change.key for key, row in orig_ix.records(kind).items()
        )
        if taken and change.blocked is None:
            change.blocked = f"The original has another {kind.noun.lower()} named “{name}”"


def _node_of(change: Change, versions: tuple[_Index, ...]) -> str | None:
    if change.record_type == "node":
        return change.key[0]
    if change.record_type in ("calculation", "note"):
        for index in versions:
            row = index.records(change.unit).get(change.key)  # type: ignore[arg-type]
            if row is not None:
                return row["node_id"]
    return None


def _seq(table: str, key: Key, versions: tuple[_Index, ...]) -> tuple:
    for index in versions:
        row = index.rows[table].get(key)
        if row is not None:
            return (row.get("seq") or 0, str(row.get("created_at") or ""), str(key))
    return (0, "", str(key))


def _sections(
    changes: list[Change], base_ix: _Index, play_ix: _Index, orig_ix: _Index, labels: _Labels
) -> list[tuple[str, str, list[str]]]:
    """One section per node, in the nodes' order, then Edges, Reaction steps, Branches, Groups,
    Sub-investigations, Analyses, Settings and Canvas; empty ones left out."""
    versions = (play_ix, orig_ix, base_ix)
    by_node: dict[str, list[Change]] = defaultdict(list)
    others: dict[str, list[Change]] = defaultdict(list)
    for change in changes:
        if change.section == "node":
            node = _node_of(change, versions)
            rank = ("node", "calculation", "note").index(change.record_type)
            unit = change.unit
            change.order = (rank, _seq(unit.table, change.key, versions) if unit else ())
            by_node[node or ""].append(change)
        else:
            kind_rank = next(
                (i for i, k in enumerate(KINDS) if k.record_type == change.record_type), 99
            )
            if change.unit is not None and not change.order:
                change.order = (kind_rank, _seq(change.unit.table, change.key, versions))
            elif not change.order:
                change.order = (kind_rank,)
            others[change.section].append(change)
    sections = []
    for node in sorted(by_node, key=lambda n: _seq("nodes", (n,), versions)):
        found = sorted(by_node[node], key=lambda c: c.order)
        sections.append((f"node:{node}", f"Node {labels.node(node)}", [c.id for c in found]))
    for key, title in SECTIONS:
        if others.get(key):
            found = sorted(others[key], key=lambda c: c.order)
            sections.append((key, title, [c.id for c in found]))
    return sections


# ---------- review ----------


@dataclass
class Playground:
    """The playground's own record, read without opening it."""

    folder: Path
    uuid: str
    of: str
    name: str
    created_at: datetime | None
    fork: int
    original: Path


def playground(folder: Path) -> Playground:
    """A playground that can still be merged, with its original's folder; refused otherwise."""
    folder = Path(folder).resolve()
    playgrounds.check(folder)  # a playground in its original's playgrounds/ folder
    got = playgrounds._read(
        folder / DB_NAME,
        "SELECT uuid, playground_of, playground_name, playground_created_at, "
        "playground_history_id, playground_merged_at, playground_original "
        "FROM investigation_info WHERE id = 1",
    )
    if not got:
        raise MergeError(f"{folder} is not a playground")
    uuid, of, name, created, fork, merged, back = got[0]
    if merged:
        raise MergeError("This playground was merged already; a playground merges only once")
    original = playgrounds.original_folder(folder, back)
    found = playgrounds._read(
        original / DB_NAME, "SELECT uuid FROM investigation_info WHERE id = 1"
    )
    if not found:
        raise MergeError("The playground's original is not in the folder it was made in")
    if found[0][0] != of:
        raise MergeError("That folder holds another investigation, not this playground's original")
    return Playground(
        folder, uuid or "", of, name or folder.name, playgrounds._time(created), fork or 0, original
    )


def check_original(investigation: Investigation, found: Playground) -> None:
    info = investigation.info()
    if investigation.folder.resolve() != found.original or info is None or info.uuid != found.of:
        raise MergeError("The open investigation is not this playground's original")


def _plan(session: Session, found: Playground) -> Plan:
    """Read the three versions, holding the playground's lease while reading it (refused while
    another app has it open), and plan the merge."""
    with acquire_ownership(found.folder):
        play, entries = _read_database(found.folder / DB_NAME, since=found.fork)
    base = _read_base(found.folder)
    orig = read_rows(session.connection())
    return plan(base, play, orig, entries)


def review(investigation: Investigation, folder: Path) -> dict[str, Any]:
    """What the merge review shows (D235h): every change, grouped, with its default tick."""
    found = playground(folder)
    check_original(investigation, found)
    with investigation.sessions() as session:
        try:
            merged = _plan(session, found)
            since = session.scalar(
                select(func.count()).select_from(HistoryEntry).where(HistoryEntry.id > found.fork)
            )
        finally:
            session.rollback()
    return _review(merged, found, investigation, since or 0)


def proposal_warnings(merged: Plan) -> list[str]:
    """What the review warns about without blocking the merge: edges Claude proposed in the
    playground that the user has not accepted yet (D264b)."""
    rows = merged.play.get("transitions", {}).values()
    found = proposals.warning(proposals.counts([r for r in rows if r.get("review") == "open"]))
    return [found] if found else []


def _review(merged: Plan, found: Playground, investigation: Investigation, since: int) -> dict:
    info = investigation.info()
    return {
        "warnings": proposal_warnings(merged),
        "playground": {
            "name": found.name,
            "folder": str(found.folder),
            "created_at": found.created_at,
        },
        "original_name": info.name if info else investigation.folder.name,
        "changed_since": since,
        "sections": [
            {"key": key, "title": title, "changes": [merged.changes[c].as_dict() for c in ids]}
            for key, title, ids in merged.sections
        ],
    }


# ---------- apply ----------


def _chosen(merged: Plan, ticked: Iterable[str]) -> tuple[list[Change], list[str], list[str]]:
    """The ticked changes and all they need, in the review's order; the ids brought; and what
    blocks the merge."""
    blockers: list[str] = []
    wanted: set[str] = set()
    for change_id in dict.fromkeys(ticked):
        if change_id not in merged.changes:
            blockers.append(f"{change_id} is no longer among the changes; review the merge again")
            continue
        wanted.add(change_id)
    ticked_set = set(wanted)
    queue = list(wanted)
    while queue:
        for needed in merged.changes[queue.pop()].requires:
            if needed not in wanted:
                wanted.add(needed)
                queue.append(needed)
    chosen = [c for cid, c in merged.changes.items() if cid in wanted]
    for change in chosen:
        if change.blocked:
            blockers.append(f"{change.label}: {change.blocked}")
    brought = [c.id for c in chosen if c.id not in ticked_set]
    return chosen, brought, blockers


def _counts(chosen: list[Change]) -> dict[str, int]:
    return {
        "added": sum(c.op == "insert" for c in chosen),
        "changed": sum(c.op in ("update", "positions") for c in chosen),
        "removed": sum(c.op == "delete" for c in chosen),
    }


class _Writer:
    """Writes the chosen changes into the original, in its open transaction."""

    def __init__(self, session: Session, merged: Plan):
        self.session = session
        self.merged = merged
        self.play_ix = _Index(merged.play)
        self.orig_ix = merged.orig
        self.levels: dict[str, str] = {}
        self.by_level = {
            tuple(row[c] for c in LEVEL_COLUMNS): key[0]
            for key, row in self.orig_ix["levels_of_theory"].items()
        }
        self.sources: list[str] = []  # source files inserted, whose folders are linked
        self.done: set[tuple[str, Any]] = set()  # hidden rows inserted

    def table(self, name: str) -> Table:
        return Base.metadata.tables[name]

    def execute(self, statement) -> None:
        self.session.execute(statement)

    # Hidden dependencies (D235h)

    def level(self, level_id: str | None) -> str | None:
        """The original's id for a level: the same one, an equal one under another id (levels
        are unique by their six parts, and each side may have made the same one), or the
        playground's inserted."""
        if not level_id or (level_id,) in self.orig_ix["levels_of_theory"]:
            return level_id
        if level_id in self.levels:
            return self.levels[level_id]
        row = self.merged.play["levels_of_theory"].get((level_id,)) or self.merged.base[
            "levels_of_theory"
        ].get((level_id,))
        if row is None:
            return level_id  # dangling: the foreign key check names it
        parts = tuple(row[c] for c in LEVEL_COLUMNS)
        if parts in self.by_level:
            self.levels[level_id] = self.by_level[parts]
        else:
            self.execute(insert(self.table("levels_of_theory")).values(**row))
            self.by_level[parts] = level_id
            self.levels[level_id] = level_id
        return self.levels[level_id]

    def composite(self, value: str | None) -> str | None:
        """A composite level key `levelId~geometryLevelId` with each part remapped."""
        if not value:
            return value
        return "~".join(self.level(part) or "" if part else "" for part in value.split("~"))

    def source(self, source_id: str | None) -> None:
        if (
            not source_id
            or (source_id,) in self.orig_ix["source_files"]
            or source_id in self.sources
        ):
            return
        row = self.merged.play["source_files"].get((source_id,))
        if row is None:
            return  # dangling: the foreign key check names it
        self.execute(insert(self.table("source_files")).values(**row))
        self.sources.append(source_id)

    def pictures(self, body: str | None) -> None:
        for image_id in images_in(body or ""):
            if (image_id,) in self.orig_ix["note_images"] or ("img", image_id) in self.done:
                continue
            row = self.merged.play["note_images"].get((image_id,))
            if row is not None:
                self.execute(insert(self.table("note_images")).values(**row))
                self.done.add(("img", image_id))

    def prepared(self, table: str, values: dict[str, Any]) -> dict[str, Any]:
        """A record's values with their levels remapped and their files and pictures brought."""
        values = dict(values)
        if table == "calculations":
            for column in ("level_id", "geometry_level_id"):
                if column in values:
                    values[column] = self.level(values[column])
            if "source_file_id" in values:
                self.source(values["source_file_id"])
        if table in energies.LEVEL_KEY_TABLES and "level" in values:  # D251
            values["level"] = self.composite(values["level"])
        if table == "node_notes" and "body" in values:
            self.pictures(values["body"])
        return values

    # The changes

    def where(self, kind: Kind, key: Key):
        table = self.table(kind.table)
        if kind.record_type == "thermochemistry":
            return table.c.id == 1
        if kind.by_name:
            return table.c.name == key[0]
        return table.c[_primary(table)[0]] == key[0]

    def children(self, kind: Kind, owner: str, only: Iterable[str] | None = None) -> None:
        """Replace the rows a record owns with the playground's."""
        for child in kind.children:
            if only is not None and child.field not in only:
                continue
            table = self.table(child.table)
            self.execute(delete(table).where(table.c[child.parent] == owner))
            for row in self.play_ix.children(child, owner):
                self.execute(insert(table).values(**row))
                if child.nested is not None:
                    nested = self.table(child.nested.table)
                    for item in self.play_ix.children(child.nested, row["id"]):
                        self.execute(insert(nested).values(**item))

    def apply(self, change: Change) -> None:
        kind = change.unit
        if change.op == "positions":
            for table, key in change.cards:
                if key not in self.orig_ix[table]:
                    continue
                row = self.merged.play[table][key]
                target = self.table(table)
                self.execute(
                    update(target)
                    .where(target.c.id == key[0])
                    .values(pos_x=row["pos_x"], pos_y=row["pos_y"])
                )
            return
        assert kind is not None
        table = self.table(kind.table)
        row = self.play_ix.records(kind).get(change.key)
        if change.op == "delete":
            self.execute(delete(table).where(self.where(kind, change.key)))
            return
        assert row is not None
        if change.op == "insert":
            values = {k: v for k, v in row.items() if k != "seq"}  # numbered after the original's
            self.execute(insert(table).values(**self.prepared(kind.table, values)))
            self.children(kind, row["id"])
            return
        columns = [f for f in change.written if f in table.c]
        if kind.by_name:
            columns = [c for c in columns if c != "id"]
        if columns:
            values = self.prepared(kind.table, {c: row[c] for c in columns})
            self.execute(update(table).where(self.where(kind, change.key)).values(**values))
        owned = [f for f in change.written if f not in table.c]
        if owned:
            self.children(kind, row["id"], owned)


def _order(change: Change) -> tuple:
    """Inserts in creation order per table, so each gets the next seq after the original's."""
    kind_rank = next((i for i, k in enumerate(KINDS) if k is change.unit), 99)
    return (kind_rank, change.order)


def _entry_change(entry: dict[str, Any], merged: Plan) -> str | None:
    """The change a history entry of the playground belongs to, by the record it is about."""
    record_type, record_id = entry["record_type"], entry["record_id"]
    if record_type == "note":
        snapshot = entry["new_value"] if isinstance(entry["new_value"], dict) else None
        snapshot = snapshot or (entry["old_value"] if isinstance(entry["old_value"], dict) else {})
        note = snapshot.get("id") if snapshot else None
        return f"note:{note}" if note else None
    if record_type == "thermochemistry":
        return "thermochemistry:investigation"
    kind = BY_TYPE.get(record_type)
    if kind is None:
        return None
    if kind.by_name:
        for rows in (merged.play, merged.base):
            row = rows[kind.table].get((record_id,))
            if row is not None:
                return f"{record_type}:{row['name']}"
        return None
    return f"{record_type}:{record_id}"


def _copied(entry: dict[str, Any], change: Change) -> bool:
    """Whether a playground entry is copied with its change: every entry of a record added,
    brought back or removed, and of a changed one those of the fields written."""
    if change.op in ("insert", "delete"):
        return True
    if change.op != "update" or entry["action"] in ("create", "delete") or not entry["field"]:
        return False
    name = FIELD_ALIASES.get((entry["record_type"], entry["field"]), entry["field"])
    return name in change.written


def _copy_history(session: Session, merged: Plan, chosen: list[Change], merge_id: int) -> int:
    by_id = {c.id: c for c in chosen}
    table = HistoryEntry.__table__
    copied = 0
    for entry in merged.entries:
        change = by_id.get(_entry_change(entry, merged) or "")
        if change is None or not _copied(entry, change):
            continue
        values = {k: v for k, v in entry.items() if k != "id"}
        values["merge_entry_id"] = merge_id
        session.execute(insert(table).values(**values))
        copied += 1
    return copied


def _undo_data(before: Rows, after: Rows) -> dict[str, Any]:
    """What the merge wrote, row by row, for its undo (D235j): inserted rows by key, updated
    columns with their values before, deleted rows whole (cascades included). And what it wrote,
    so the undo tells later work apart: inserted rows whole ("inserted_rows"; levels, files
    and pictures left out, which the undo never compares) and the updated columns' new values
    ("updated_to"). Merges made before these two keys fall back to the history."""
    inserted: dict[str, list] = {}
    inserted_rows: dict[str, list] = {}
    updated: dict[str, dict] = {}
    updated_to: dict[str, dict] = {}
    deleted: dict[str, list] = {}
    for table, rows in after.items():
        old = before.get(table, {})
        new_keys = [list(k) for k in rows if k not in old]
        gone = [_json_safe(row) for k, row in old.items() if k not in rows]
        changed, changed_to = {}, {}
        for key, row in rows.items():
            was = old.get(key)
            if was is None:
                continue
            columns = {c: v for c, v in was.items() if row.get(c) != v}
            if columns:
                name = json.dumps(list(key), default=str)
                changed[name] = _json_safe(columns)
                changed_to[name] = _json_safe({c: row.get(c) for c in columns})
        if new_keys:
            inserted[table] = new_keys
            if table not in HIDDEN:
                inserted_rows[table] = [_json_safe(row) for k, row in rows.items() if k not in old]
        if gone:
            deleted[table] = gone
        if changed:
            updated[table] = changed
            updated_to[table] = changed_to
    return {
        "inserted": inserted,
        "updated": updated,
        "deleted": deleted,
        "inserted_rows": inserted_rows,
        "updated_to": updated_to,
    }


def _violations(session: Session) -> set[tuple]:
    rows = session.connection().exec_driver_sql("PRAGMA foreign_key_check").fetchall()
    return {tuple(row) for row in rows}


def _broken(session: Session, labels: _Labels, already: set[tuple]) -> list[str]:
    """The foreign key check at the end of the merge's transaction, as blockers."""
    found = []
    connection = session.connection()
    for table, rowid, parent, _fk in _violations(session) - already:
        name = "a record"
        if table in Base.metadata.tables and "id" in Base.metadata.tables[table].c:
            row = connection.exec_driver_sql(
                f'SELECT id FROM "{table}" WHERE rowid = ?', (rowid,)
            ).first()
            if row is not None:
                name = labels.any(row[0])
        found.append(f"{name} ({table}) would point to a {parent} record the original lacks")
    return sorted(set(found))


def _relative(found: Playground) -> str:
    return Path(os.path.relpath(found.folder, found.original)).as_posix()


def _apply(
    session: Session, merged: Plan, found: Playground, chosen: list[Change]
) -> tuple[list[str], list[str], int]:
    """Write the chosen changes in the session's transaction: the merge's entry first, then the
    records, then the copied entries. The blockers the foreign key check finds, the source files
    whose folders must be linked, and the merge entry's id."""
    counts = _counts(chosen)
    entry = history.record(
        session,
        "playground",
        found.uuid,
        "merge",
        new={"name": found.name},
        source="manual",
    )
    session.flush()  # the first write: SQLite's transaction starts here
    # Foreign keys are checked at the commit from here on, so the order of the writes does not
    # matter in the cycles of nodes, branches, groups, sub-investigations and edges (D235h).
    session.connection().exec_driver_sql("PRAGMA defer_foreign_keys = ON")
    writer = _Writer(session, merged)
    labels = _Labels(merged.play, merged.orig, merged.base)
    already = _violations(session)  # broken before the merge: not the merge's to answer for
    for change in sorted(chosen, key=_order):
        try:
            writer.apply(change)
        except IntegrityError as exc:  # a unique name or a CHECK; the caller rolls back
            return [f"{change.label} could not be written: {exc.orig}"], [], entry.id
    _copy_history(session, merged, chosen, entry.id)
    blockers = _broken(session, labels, already)
    after = read_rows(session.connection())
    entry.new_value = {
        "name": found.name,
        "folder": _relative(found),
        "changes": [{"id": c.id, "label": c.label, "kind": c.kind} for c in chosen],
        "counts": counts,
    }
    entry.old_value = {**_undo_data(merged.orig, after), "linked_files": list(writer.sources)}
    session.flush()
    return blockers, writer.sources, entry.id


def _link_sources(found: Playground, source_ids: list[str]) -> list[Path]:
    """Hard-link each merged file's folder `files/<id>/` from the playground into the original
    (copied where the disk cannot link, D235b). The folders made, to remove if the merge fails."""
    made: list[Path] = []
    try:
        for source_id in source_ids:
            source = found.folder / FILES_DIR / source_id
            target = found.original / FILES_DIR / source_id
            if not source.is_dir():
                raise MergeError(f"The playground's stored copy files/{source_id}/ is missing")
            if target.exists():
                continue
            target.mkdir(parents=True)
            made.append(target)
            for root, _dirs, names in os.walk(source):
                here = target / Path(root).relative_to(source)
                here.mkdir(parents=True, exist_ok=True)
                for name in names:
                    playgrounds._link(Path(root) / name, here / name)
    except BaseException:
        _unlink(made)
        raise
    return made


def _unlink(made: list[Path]) -> None:
    for folder in made:
        shutil.rmtree(folder, ignore_errors=True)


def _mark_merged(found: Playground) -> None:
    """D235h: the playground is merged; it opens read-only from now on (Q64)."""
    try:
        with closing(sqlite3.connect(found.folder / DB_NAME)) as db:
            db.execute(
                "UPDATE investigation_info SET playground_merged_at = ? WHERE id = 1",
                (playgrounds._stored(utcnow()),),
            )
            db.commit()
    except sqlite3.Error as exc:
        raise MergeError(
            f"The playground was merged, but could not be marked as merged ({exc}); "
            "do not merge it again"
        ) from exc


def merge(
    investigation: Investigation, folder: Path, ticked: list[str], dry_run: bool = False
) -> dict[str, Any]:
    """Merge the ticked changes and all they need into the open original (D235h). A dry run
    (the preview) writes everything in a transaction that is rolled back and links no file.
    Otherwise the original is backed up first and the playground marked merged after the
    commit; refused with MergeBlocked, changing nothing, when anything blocks it. The
    playground's lease is held throughout, so no app opens it meanwhile."""
    found = playground(folder)
    check_original(investigation, found)
    with acquire_ownership(found.folder):
        play, entries = _read_database(found.folder / DB_NAME, since=found.fork)
        base = _read_base(found.folder)
        session = investigation.sessions()
        backup = None
        made: list[Path] = []
        try:
            merged = plan(base, play, read_rows(session.connection()), entries)
            chosen, brought, blockers = _chosen(merged, ticked)
            result: dict[str, Any] = {
                "counts": _counts(chosen),
                "warnings": proposal_warnings(merged),
                "brought": brought,
                "blockers": blockers,
                "backup": None,
                "history_id": None,
            }
            if blockers or not chosen:
                if not chosen and not blockers:
                    result["blockers"] = ["Nothing is ticked"]
                if dry_run:
                    return result
                raise MergeBlocked(result)
            if not dry_run:
                # D235h: the original is backed up before anything is written to it.
                backup = autonomy.backup_database(investigation.folder, label="merge")
            blockers, sources, entry_id = _apply(session, merged, found, chosen)
            result["blockers"] = blockers
            if dry_run:
                return result
            if blockers:
                raise MergeBlocked(result)
            made = _link_sources(found, sources)
            session.commit()
            made = []  # committed: the folders are the original's now
        except BaseException:
            session.rollback()
            _unlink(made)
            if backup is not None:  # nothing was merged: the copy is not needed
                (investigation.folder / BACKUP_DIR / backup).unlink(missing_ok=True)
            raise
        finally:
            if dry_run:
                session.rollback()
            session.close()
        _mark_merged(found)
    result["backup"] = backup
    result["history_id"] = entry_id
    return result
