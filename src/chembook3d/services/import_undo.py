"""Undo an import from the history (D102, A49).

An import's line in the history ("Imported … from file (step n)") can be undone as one new
change (P15, FR-HIST-03): the whole file goes, not one step. Its calculations and the stored
copy of the file are removed, the nodes it created (a new node, a derived node, a CREST
ensemble's group and members) are deleted, and on a node it attached to or finished every field
it changed goes back to its value before. A field the user changed again later keeps the later
value. Anything that depends on the import blocks the undo, with a message naming it, rather
than being undone as well.

Which records an import wrote is read back from the history: `imports.commit` writes, in one
transaction and in this order, the node it creates or the fields it changes on its target
(geometry, charge, multiplicity, label, role, status and taking off the optimization-incomplete
tag), a derived node, then one entry per calculation, each followed by the
optimization-incomplete tag when that step did not converge, and a CREST ensemble's group last.
`_import_entries` walks out from the calculations' entries by exactly these rules, so it also
finds imports made before this feature. A one-off repair (D100, source "repair") that later
moved the node onto one of the import's optimizations counts as part of the import.

A batch import (D97) is undone file by file, in reverse order, all or nothing.
"""

import shutil
from dataclasses import dataclass, field
from pathlib import Path, PurePath
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from chembook3d import settings as app_settings
from chembook3d.investigation import FILES_DIR
from chembook3d.models import (
    AlignmentSetAtoms,
    Calculation,
    CalculationType,
    GroupNode,
    HistoryEntry,
    Node,
    NodeNote,
    Selectivity,
    SelectivityMember,
    SourceFile,
    StericProfileAtoms,
    TransitionSpecies,
    Turnover,
)
from chembook3d.services import geometry, history, levels
from chembook3d.services import groups as group_service
from chembook3d.services import nodes as node_service
from chembook3d.services import transitions as transition_service
from chembook3d.services.repairs import SOURCE as REPAIR

UNDO_ACTION = "undo_import"

# The node fields an import sets on a node it attaches to or finishes (imports.commit).
RESTORED_FIELDS = ("geometry", "charge", "multiplicity", "label", "role", "status")

FIELD_NAMES = {
    "geometry": "coordinates",
    "charge": "charge",
    "multiplicity": "multiplicity",
    "label": "label",
    "role": "role",
    "status": "status",
}

# How far the history is read either side of an import's calculations: an import writes at
# most a handful of entries before its first calculation and after its last.
_WINDOW = 50


class UndoNotPossible(ValueError):
    """The entry is not an import that can still be undone; the message says why."""


class UndoBlocked(ValueError):
    def __init__(self, blockers: list[str]):
        super().__init__("; ".join(blockers))
        self.blockers = blockers


@dataclass
class ImportRecords:
    """What one imported file wrote, read back from the history."""

    source: SourceFile
    name: str  # the file's name when it was imported
    entries: list[HistoryEntry]
    calculations: list[Calculation]  # those still in the investigation
    created: list[str]  # nodes the import created, in order
    group_id: str | None  # a CREST ensemble's group
    # (node id, field) -> (value before the import, value the import left)
    changes: dict[tuple[str, str], tuple[Any, Any]] = field(default_factory=dict)
    # node id -> (tags the import added, tags it took off)
    tags: dict[str, tuple[set[str], set[str]]] = field(default_factory=dict)


@dataclass
class UndoPlan:
    records: ImportRecords
    blockers: list[str]
    deleted: list[Node]
    group: GroupNode | None
    restored: list[tuple[Node, str]]  # fields going back to their value before the import
    kept: list[tuple[Node, str]]  # fields changed again after the import, which stay
    tags: list[tuple[Node, set[str], set[str]]]  # tags taken off again, tags put back

    def as_dict(self) -> dict[str, Any]:
        records = self.records
        return {
            "source_file_id": records.source.id,
            "file": records.name,
            "blockers": self.blockers,
            "calculations": [
                {
                    "type": e.new_value.get("type"),
                    "level": e.new_value.get("level"),
                    "step": e.new_value.get("step"),
                    "node_id": e.new_value.get("node_id"),
                }
                for e in records.entries
                if e.record_type == "calculation"
                and e.record_id in {c.id for c in records.calculations}
            ],
            "deleted": [{"id": n.id, "label": n.label} for n in self.deleted],
            "group": {"id": self.group.id, "label": self.group.label} if self.group else None,
            "restored": [{"node_id": n.id, "label": n.label, "field": f} for n, f in self.restored],
            "kept": [
                {"node_id": n.id, "label": n.label, "field": f, "value": getattr(n, f)}
                for n, f in self.kept
            ],
            "tags": [
                {"node_id": n.id, "label": n.label, "removed": sorted(a), "added": sorted(r)}
                for n, a, r in self.tags
            ],
        }


def _label(node: Node | GroupNode | None) -> str:
    if node is None:
        return "a deleted node"
    return f"“{node.label}”" if node.label else "an untitled node"


def _is_create(entry: HistoryEntry, record_type: str) -> bool:
    return (
        entry.record_type == record_type and entry.action == "create" and entry.source == "import"
    )


def _tag_change(entry: HistoryEntry) -> tuple[set[str], set[str]] | None:
    """(added, removed) when the entry is an import changing a node's tags."""
    if entry.record_type != "node" or entry.action != "update" or entry.field != "tags":
        return None
    if entry.source != "import":
        return None
    before, after = set(entry.old_value or []), set(entry.new_value or [])
    return after - before, before - after


def _leading(entry: HistoryEntry) -> bool:
    """An entry `imports.commit` writes before the first calculation."""
    if _is_create(entry, "node"):
        return True
    if entry.record_type != "node" or entry.action != "update" or entry.source != "import":
        return False
    if entry.field in RESTORED_FIELDS:
        return True
    change = _tag_change(entry)
    return change is not None and not change[0] and bool(change[1])


def _trailing(entry: HistoryEntry, owners: set[str], created: set[str]) -> bool:
    """An entry `imports.commit` writes after the last calculation."""
    change = _tag_change(entry)
    if change is not None:
        return entry.record_id in owners and bool(change[0]) and not change[1]
    if _is_create(entry, "group"):
        members = set((entry.new_value or {}).get("member_ids") or [])
        return bool(members) and members <= created
    return False


def _import_entries(session: Session, source: SourceFile) -> ImportRecords:
    calculations = list(
        session.scalars(select(Calculation).where(Calculation.source_file_id == source.id))
    )
    if not calculations:
        raise UndoNotPossible("Nothing of this import is left in the investigation")
    ids = {c.id for c in calculations}
    calculation_entries = list(
        session.scalars(
            select(HistoryEntry)
            .where(
                HistoryEntry.record_type == "calculation",
                HistoryEntry.action == "create",
                HistoryEntry.record_id.in_(ids),
            )
            .order_by(HistoryEntry.id)
        )
    )
    if not calculation_entries:
        raise UndoNotPossible("The history does not say what this import changed")
    first, last = calculation_entries[0].id, calculation_entries[-1].id
    name = str(calculation_entries[0].new_value.get("file") or source.original_name)

    inside = list(
        session.scalars(
            select(HistoryEntry)
            .where(HistoryEntry.id >= first, HistoryEntry.id <= last)
            .order_by(HistoryEntry.id)
        )
    )
    for entry in inside:
        own = _is_create(entry, "calculation") and (
            entry.record_id in ids or entry.new_value.get("file") == name
        )
        if not (own or _is_create(entry, "node") or _tag_change(entry) is not None):
            raise UndoNotPossible("The history does not say what this import changed")

    before = session.scalars(
        select(HistoryEntry)
        .where(HistoryEntry.id < first)
        .order_by(HistoryEntry.id.desc())
        .limit(_WINDOW)
    )
    leading: list[HistoryEntry] = []
    for entry in before:
        if not _leading(entry):
            break
        leading.insert(0, entry)

    entries = leading + inside
    created = {e.record_id for e in entries if _is_create(e, "node")}
    owners = created | {c.node_id for c in calculations} | {e.record_id for e in leading}
    after = session.scalars(
        select(HistoryEntry).where(HistoryEntry.id > last).order_by(HistoryEntry.id).limit(_WINDOW)
    )
    group_id = None
    for entry in after:
        if not _trailing(entry, owners, created):
            break
        entries.append(entry)
        if entry.record_type == "group":
            group_id = entry.record_id
            break  # the group is the ensemble's last entry

    records = ImportRecords(
        source=source,
        name=name,
        entries=entries,
        calculations=calculations,
        created=[e.record_id for e in entries if _is_create(e, "node")],
        group_id=group_id,
    )
    for entry in entries:
        if entry.record_type != "node" or entry.action != "update" or entry.record_id in created:
            continue
        change = _tag_change(entry)
        if change is not None:
            added, removed = records.tags.setdefault(entry.record_id, (set(), set()))
            added |= change[0] - removed
            removed |= change[1] - added
            continue
        key = (entry.record_id, entry.field)
        old = records.changes[key][0] if key in records.changes else entry.old_value
        records.changes[key] = (old, entry.new_value)
    _add_repairs(session, records, last)
    return records


def _add_repairs(session: Session, records: ImportRecords, last: int) -> None:
    """D100: a node that kept its pre-optimization's geometry was moved onto the optimization
    that continued from it by the one-off repair, with source "repair". When that optimization
    is this import's, the move is undone with it."""
    created = set(records.created)
    ours = {
        c.node_id: [] for c in records.calculations if c.node_id not in created
    }  # node id -> our optimizations' geometries
    for calculation in records.calculations:
        if calculation.node_id in ours and calculation.type in levels.OPTIMIZATION_TYPES:
            ours[calculation.node_id].append(calculation.geometry)
    if not any(ours.values()):
        return
    repairs = session.scalars(
        select(HistoryEntry)
        .where(
            HistoryEntry.id > last,
            HistoryEntry.record_type == "node",
            HistoryEntry.field == "geometry",
            HistoryEntry.source == REPAIR,
            HistoryEntry.record_id.in_(list(ours)),
        )
        .order_by(HistoryEntry.id)
    )
    for entry in repairs:
        if entry.new_value not in ours[entry.record_id]:
            continue
        key = (entry.record_id, "geometry")
        old = records.changes[key][0] if key in records.changes else entry.old_value
        records.changes[key] = (old, entry.new_value)
        records.entries.append(entry)


def records_of(session: Session, source_id: str) -> ImportRecords:
    source = session.get(SourceFile, source_id)
    if source is None:
        raise UndoNotPossible(
            "This import was undone or its file is no longer in the investigation"
        )
    return _import_entries(session, source)


# ---------- what depends on an import ----------


def _later_import(session: Session, after_id: int) -> str | None:
    """The file whose import wrote the history around entry `after_id`."""
    entry = session.scalars(
        select(HistoryEntry)
        .where(
            HistoryEntry.id >= after_id,
            HistoryEntry.record_type == "calculation",
            HistoryEntry.action == "create",
        )
        .order_by(HistoryEntry.id)
        .limit(1)
    ).first()
    return str(entry.new_value.get("file")) if entry is not None else None


def _geometry_changed_by(session: Session, records: ImportRecords, node: Node) -> str:
    last = max(e.id for e in records.entries)
    entry = session.scalars(
        select(HistoryEntry)
        .where(
            HistoryEntry.id > last,
            HistoryEntry.record_type == "node",
            HistoryEntry.record_id == node.id,
            HistoryEntry.field == "geometry",
        )
        .order_by(HistoryEntry.id.desc())
        .limit(1)
    ).first()
    if entry is not None and entry.source == "import":
        file = _later_import(session, entry.id)
        if file:
            return f" (by the import of {file}, which continued its optimization)"
    return ""


def _depends_on_created(
    session: Session, records: ImportRecords, nodes: list[Node], group: GroupNode | None
) -> list[str]:
    blockers: list[str] = []
    ids = [n.id for n in nodes]
    created = set(ids)
    by_id = {n.id: n for n in nodes}
    ours = {c.id for c in records.calculations}
    others = session.scalars(
        select(Calculation).where(Calculation.node_id.in_(ids), Calculation.id.not_in(ours))
    )
    files: dict[str, set[str]] = {}
    for calculation in others:
        name = calculation.source_file.original_name if calculation.source_file else "a file"
        files.setdefault(calculation.node_id, set()).add(name)
    for node_id, names in files.items():
        blockers.append(
            f"{_label(by_id[node_id])} has calculations imported later from "
            f"{', '.join(sorted(names))}"
        )
    for derived in session.scalars(
        select(Node).where(Node.derived_from_id.in_(ids), Node.id.not_in(ids))
    ):
        blockers.append(
            f"{_label(derived)} was derived from {_label(by_id[derived.derived_from_id])}"
        )
    ends = ids + ([group.id] if group else [])
    edges = transition_service.touching(session, ends)
    if edges:
        blockers.append(
            f"{len(edges)} edge{'s' if len(edges) != 1 else ''} connect"
            f"{'s' if len(edges) == 1 else ''} what this import made to the pathway"
        )
    for entry in session.scalars(
        select(TransitionSpecies).where(TransitionSpecies.species_id.in_(ids))
    ):
        blockers.append(f"{_label(by_id[entry.species_id])} joins or leaves on an edge")
    for note in session.scalars(select(NodeNote).where(NodeNote.node_id.in_(ids))):
        blockers.append(f"{_label(by_id[note.node_id])} has a pinned note")
    for node in nodes:
        if group is None and node.group_id is not None:
            blockers.append(f"{_label(node)} was put in a group")
        elif group is not None and node.group_id != group.id:
            blockers.append(f"{_label(node)} was taken out of the group “{group.label}”")
    if group is not None:
        extra = [m for m in group_service.members(session, group.id) if m.id not in created]
        if extra:
            blockers.append(f"Nodes were added to the group “{group.label}” later")
    members = session.scalars(
        select(SelectivityMember).where(
            or_(SelectivityMember.node_id.in_(ids), SelectivityMember.group_id.in_(ends))
        )
    ).all()
    references = session.scalars(
        select(Selectivity).where(Selectivity.reference_id.in_(ends))
    ).all()
    if members or references:
        blockers.append("A selectivity counts what this import made")
    if any(set(t.path or []) & set(ends) for t in session.scalars(select(Turnover))):
        blockers.append("A turnover's pathway goes through what this import made")
    if session.scalars(
        select(StericProfileAtoms).where(StericProfileAtoms.node_id.in_(ids))
    ).first():
        blockers.append("A steric profile has atoms chosen on what this import made")
    if session.scalars(select(AlignmentSetAtoms).where(AlignmentSetAtoms.node_id.in_(ids))).first():
        blockers.append("An alignment set has atoms chosen on what this import made")
    return blockers


def _single_points_on(session: Session, records: ImportRecords, node: Node) -> list[str]:
    """D100: a single point imported later whose geometry level is one of this import's
    optimizations, with no other optimization at that level ending on its geometry."""
    ours = {c.id for c in records.calculations}
    removed = [
        c
        for c in records.calculations
        if c.node_id == node.id and c.type in levels.OPTIMIZATION_TYPES
    ]
    if not removed:
        return []
    remaining = [c for c in levels.optimizations(node) if c.id not in ours]
    tolerance = app_settings.load().geometry_tolerance

    def ends_on(optimizations: list[Calculation], level_id: str, rows: Any) -> bool:
        return any(
            o.level_id == level_id
            and (rows is None or geometry.matches(o.geometry, rows, tolerance))
            for o in optimizations
        )

    blockers = []
    for calculation in node.calculations:
        if (
            calculation.id in ours
            or calculation.type != CalculationType.SINGLE_POINT
            or calculation.geometry_level_id is None
        ):
            continue
        level, rows = calculation.geometry_level_id, calculation.geometry
        if ends_on(removed, level, rows) and not ends_on(remaining, level, rows):
            name = calculation.source_file.original_name if calculation.source_file else ""
            blockers.append(
                f"The single point{f' from {name}' if name else ''} on {_label(node)} takes "
                f"its geometry level from this import's optimization"
            )
    return blockers


def plan(session: Session, records: ImportRecords) -> UndoPlan:
    """What undoing the import would do and what blocks it. Changes nothing."""
    deleted = [n for n in (session.get(Node, i) for i in records.created) if n is not None]
    group = session.get(GroupNode, records.group_id) if records.group_id else None
    blockers = _depends_on_created(session, records, deleted, group) if deleted else []

    restored: list[tuple[Node, str]] = []
    kept: list[tuple[Node, str]] = []
    for (node_id, field_name), (_old, new) in records.changes.items():
        node = session.get(Node, node_id)
        if node is None:
            continue
        if getattr(node, field_name) == new:
            restored.append((node, field_name))
        elif field_name == "geometry":
            blockers.append(
                f"The coordinates of {_label(node)} were changed after this import"
                + _geometry_changed_by(session, records, node)
            )
        else:
            kept.append((node, field_name))
    tags = []
    for node_id, (added, removed) in records.tags.items():
        node = session.get(Node, node_id)
        if node is None:
            continue
        take_off = added & set(node.tags)
        put_back = removed - set(node.tags)
        if take_off or put_back:
            tags.append((node, take_off, put_back))
    for node, field_name in restored:
        if field_name == "geometry":
            blockers.extend(_on_its_coordinates(records, node))
    for node_id in {c.node_id for c in records.calculations} - set(records.created):
        node = session.get(Node, node_id)
        if node is not None:
            blockers.extend(_single_points_on(session, records, node))
    blockers = list(dict.fromkeys(blockers))
    return UndoPlan(records, blockers, deleted, group, restored, kept, tags)


def _on_its_coordinates(records: ImportRecords, node: Node) -> list[str]:
    """Calculations imported later onto the coordinates this import gave the node, which would
    no longer be the node's after the undo (W-GEOM). One that prints no coordinates was run at
    the node's geometry of its time (A15)."""
    old, new = records.changes[(node.id, "geometry")]
    ours = {c.id for c in records.calculations}
    since = max((c.created_at for c in records.calculations), default=None)
    tolerance = app_settings.load().geometry_tolerance
    files = set()
    for calculation in node.calculations:
        if calculation.id in ours:
            continue
        if calculation.geometry:
            later = geometry.matches(calculation.geometry, new, tolerance) and not geometry.matches(
                calculation.geometry, old, tolerance
            )
        else:
            later = since is not None and calculation.created_at > since
        if later:
            source = calculation.source_file
            files.add(source.original_name if source else "a file")
    if not files:
        return []
    return [
        f"{_label(node)} has calculations imported later on the coordinates this import gave it "
        f"({', '.join(sorted(files))})"
    ]


def apply(session: Session, undo: UndoPlan) -> str:
    """Undo the import. Returns the stored copy's folder, relative to the investigation, for
    the caller to remove once the transaction is committed."""
    if undo.blockers:
        raise UndoBlocked(undo.blockers)
    records = undo.records
    summary = undo.as_dict()
    if undo.group is not None:
        group_service.delete_with_contents(session, undo.group.id)
    for node in undo.deleted:
        if undo.group is None or node.group_id != undo.group.id:
            node_service.delete(session, node.id)
    gone = {n.id for n in undo.deleted}  # their calculations went with them
    for calculation in records.calculations:
        if calculation.node_id not in gone:
            session.delete(calculation)
    session.flush()
    for node, field_name in undo.restored:
        old = records.changes[(node.id, field_name)][0]
        current = getattr(node, field_name)
        history.record(session, "node", node.id, "update", field_name, current, old)
        setattr(node, field_name, old)
    for node, take_off, put_back in undo.tags:
        tags = [t for t in node.tags if t not in take_off] + sorted(put_back)
        history.record(session, "node", node.id, "update", "tags", list(node.tags), tags)
        node.tags = tags
    stored = PurePath(records.source.stored_path).parent.as_posix()
    session.delete(records.source)
    session.flush()

    # One entry for the whole file, on the node it was imported onto (or made), so that node's
    # history says what happened; the import's own entries stay (P15).
    owner = ("group", undo.group.id) if undo.group is not None else ("node", _owner(records))
    history.record(
        session,
        owner[0],
        owner[1],
        UNDO_ACTION,
        old={
            **{k: summary[k] for k in ("source_file_id", "file", "calculations", "deleted")},
            "group": summary["group"],
            "restored": [{"node_id": n.id, "field": f} for n, f in undo.restored],
            "kept": [{"node_id": n.id, "field": f} for n, f in undo.kept],
            "entries": [e.id for e in records.entries],
        },
    )
    session.flush()
    return stored


def _owner(records: ImportRecords) -> str:
    created = set(records.created)
    for calculation in records.calculations:
        if calculation.node_id not in created:
            return calculation.node_id
    return records.created[0] if records.created else records.source.id


def remove_copy(folder: Path, stored: str) -> None:
    """Remove files/<source-file-id>/ after the transaction that undid its import committed.
    A copy left behind by a crash is removed when the investigation is next opened."""
    directory = folder / stored
    if directory.parent == folder / FILES_DIR:
        shutil.rmtree(directory, ignore_errors=True)


# ---------- from a history entry ----------


def _calculation_source(session: Session, entry: HistoryEntry) -> str | None:
    if entry.record_type != "calculation" or entry.action != "create":
        return None
    calculation = session.get(Calculation, entry.record_id)
    return calculation.source_file_id if calculation is not None else None


def batch_sources(session: Session, entry: HistoryEntry) -> list[str] | None:
    """The source files of a batch import (D97) still in the investigation, in import order,
    or None when they cannot be told. Batches written since D102 name them; for earlier ones the
    files imported just before the batch's entry must match its list by name."""
    files = (entry.new_value or {}).get("files") or []
    if not files:
        return None
    if all("source_file_id" in f for f in files):
        ids = [f["source_file_id"] for f in files]
        return [i for i in ids if session.get(SourceFile, i) is not None]
    names = [PurePath(str(f.get("file", ""))).name for f in files]
    found: list[tuple[str, str]] = []  # (source file id, its name when imported)
    for earlier in session.scalars(
        select(HistoryEntry)
        .where(HistoryEntry.id < entry.id)
        .order_by(HistoryEntry.id.desc())
        .limit(10_000)
    ):
        if earlier.record_type in ("custom_basis", "custom_dispersion"):
            continue  # named while the batch was written
        if earlier.source != "import" or earlier.record_type == "batch_import":
            break
        if not _is_create(earlier, "calculation"):
            continue
        source_id = _calculation_source(session, earlier)
        if source_id is None:
            return None  # a calculation of the batch was deleted; its file cannot be told
        if source_id in {f[0] for f in found}:
            continue
        found.insert(0, (source_id, str(earlier.new_value.get("file"))))
        if len(found) == len(names):
            break
    if [name for _, name in found] != names:
        return None
    return [source_id for source_id, _ in found]


def _undone(session: Session, entry: HistoryEntry) -> bool:
    return (
        session.scalars(
            select(HistoryEntry.id).where(
                HistoryEntry.record_type == "batch_import",
                HistoryEntry.record_id == entry.record_id,
                HistoryEntry.action == "undo",
            )
        ).first()
        is not None
    )


def undo_kind(session: Session, entry: HistoryEntry) -> str | None:
    """ "import" or "batch" when the entry's import can still be undone (whether something
    blocks it is only worked out when asked)."""
    if _calculation_source(session, entry) is not None:
        return "import"
    if _is_create(entry, "batch_import") and not _undone(session, entry):
        sources = batch_sources(session, entry)
        if sources:
            return "batch"
    return None


def undo_kinds(session: Session, entries: list[HistoryEntry]) -> dict[int, str]:
    found = {}
    calculation_ids = [
        e.record_id for e in entries if e.record_type == "calculation" and e.action == "create"
    ]
    imported = (
        set(
            session.scalars(
                select(Calculation.id).where(
                    Calculation.id.in_(calculation_ids), Calculation.source_file_id.is_not(None)
                )
            )
        )
        if calculation_ids
        else set()
    )
    for entry in entries:
        if entry.record_type == "calculation" and entry.action == "create":
            if entry.record_id in imported:
                found[entry.id] = "import"
        elif _is_create(entry, "batch_import"):
            kind = undo_kind(session, entry)
            if kind:
                found[entry.id] = kind
    return found


@dataclass
class EntryUndo:
    kind: str  # import | batch
    entry: HistoryEntry
    plans: list[UndoPlan]

    @property
    def blockers(self) -> list[str]:
        if self.kind == "import":
            return self.plans[0].blockers
        return [f"{p.records.name}: {b}" for p in self.plans for b in p.blockers]

    def as_dict(self) -> dict[str, Any]:
        value = self.entry.new_value or {}
        return {
            "kind": self.kind,
            "folder": value.get("folder") if self.kind == "batch" else None,
            "blockers": self.blockers,
            "files": [p.as_dict() for p in self.plans],
        }


def preview_import(session: Session, entry_id: int) -> UndoPlan:
    """The plan for one file's import, from its calculation's entry, changing nothing."""
    entry = _entry(session, entry_id)
    source_id = _calculation_source(session, entry)
    if source_id is None:
        raise UndoNotPossible("This history entry is not an import of one file that can be undone")
    return plan(session, records_of(session, source_id))


def _entry(session: Session, entry_id: int) -> HistoryEntry:
    entry = session.get(HistoryEntry, entry_id)
    if entry is None:
        raise UndoNotPossible("No such history entry")
    return entry


def undo_entry(
    session: Session, entry_id: int, dry_run: bool = False
) -> tuple[EntryUndo, list[str]]:
    """Plan, and unless it is a dry run apply, the undo of the import a history entry records:
    one file for a calculation's entry, every file of a batch for a batch's entry (last file
    first, so files of the batch that build on each other do not block it). A dry run applies
    every unblocked file too, so later files see the state the earlier undos leave: the caller
    rolls its transaction back. Returns the stored copies to remove after the commit."""
    entry = _entry(session, entry_id)
    kind = undo_kind(session, entry)
    if kind is None:
        raise UndoNotPossible("This history entry is not an import that can still be undone")
    if kind == "import":
        source_ids = [_calculation_source(session, entry)]
    else:
        source_ids = list(reversed(batch_sources(session, entry) or []))
    plans: list[UndoPlan] = []
    stored: list[str] = []
    for source_id in source_ids:
        undo = plan(session, records_of(session, source_id))
        plans.append(undo)
        if not undo.blockers:
            stored.append(apply(session, undo))
    result = EntryUndo(kind, entry, plans)
    if result.blockers and not dry_run:
        raise UndoBlocked(result.blockers)  # the caller rolls back the files already undone
    if kind == "batch":
        history.record(
            session,
            "batch_import",
            entry.record_id,
            "undo",
            old={
                "folder": (entry.new_value or {}).get("folder"),
                "files": [p.records.name for p in plans],
            },
        )
        session.flush()
    return result, stored
