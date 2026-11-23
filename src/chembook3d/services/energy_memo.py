"""D257: a node's computed energies are kept between requests until something they are made
of changes, so an edit recomputes only the nodes it touched.

What a node's energies are made of: the node's own columns other than layout and naming
(`UNUSED_NODE_COLUMNS`), its calculations, their results and normal modes, and, for every node
at once, everything else in the database except the tables `UNUSED_TABLES` names (history,
edges, branches, saved analyses and the like, which `energies.NodeEnergies` never reads). A
table this module does not know touches every node, so a table added later is safe.

How a change is seen: each connection of the investigation's engine has temporary triggers
(nothing is stored in the investigation) that call `chembook_touch(node_id)`, or with NULL for
every node, on each write, from the ORM, Core or plain SQL alike. A commit then gives the
touched nodes a new revision (`_on_commit`, before the commit lands) and counts once more when
the connection comes back to the pool (`_on_checkin`, after it landed).

When a kept value is used: a session notes the count when it begins (`_on_begin`), before it
reads anything; what it computes is kept with that count, and used by a later session only
while the count is past the node's revision. So a session that might have read the node before
a change landed never keeps or uses a value across it. A session with writes of its own (made
or flushed) keeps and uses nothing.
"""

import copy
import threading
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import Engine, event
from sqlalchemy.orm import Session, sessionmaker

from chembook3d.models import Base

FUNCTION = "chembook_touch"
NODE_TABLES = {"nodes": "id", "calculations": "node_id"}
# Rows that belong to one calculation: their node through it.
CALCULATION_TABLES = {"calculation_results": "calculation_id", "normal_modes": "calculation_id"}
UNUSED_TABLES = frozenset(
    {
        "history",
        "reaction_steps",
        "branches",
        "group_nodes",
        "sub_investigations",
        "transitions",
        "transition_species",
        "alignment_sets",
        "alignment_set_atoms",
        "steric_profiles",
        "steric_profile_atoms",
        "selectivities",
        "selectivity_outcomes",
        "selectivity_members",
        "energy_profiles",
        "turnovers",
        "conformer_equilibria",
        "node_notes",
        "note_images",
    }
)
UNUSED_NODE_COLUMNS = frozenset(
    {
        "seq",
        "label",
        "status",
        "tags",
        "notes",
        "derived_from_id",
        "step_id",
        "branch_id",
        "group_id",
        "origin_branch_id",
        "group_position",
        "canvas_id",
        "pos_x",
        "pos_y",
        "view_rotation",
        "path_edge_id",
        "on_edge_only",
        "created_at",
        "updated_at",
    }
)
EVERY_NODE = "*"
KEPT = 50_000  # nodes × settings; a few kilobytes each


@dataclass
class Entry:
    """One node's kept energies for one set of settings (`energies.NodeEnergies` fills it)."""

    count: int  # the count when the session that made it began
    values: dict[tuple[Any, str], Any] = field(default_factory=dict)
    found: dict[str, Any] = field(default_factory=dict)  # available(), and per-level answers


class Memo:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._count = 1
        self._revisions: dict[str, int] = {}
        self._everything = 0
        self._entries: OrderedDict[tuple[str, tuple], Entry] = OrderedDict()

    # ---------- what changed ----------

    def install(self, engine: Engine, sessions: sessionmaker[Session]) -> None:
        """Watches every connection of `engine` from now on (those made so far are closed, so
        each new one has the triggers) and every session `sessions` makes."""
        event.listen(engine, "connect", self._on_connect)
        event.listen(engine, "commit", self._on_commit)
        event.listen(engine, "rollback", self._on_rollback)
        event.listen(engine, "checkin", self._on_checkin)
        event.listen(sessions, "after_begin", self._on_begin)
        event.listen(sessions, "after_flush", self._on_flush)
        engine.dispose()

    def _on_connect(self, dbapi_connection: Any, record: Any) -> None:
        touched: set[str] = record.info.setdefault("energy_touched", set())

        def touch(node_id: str | None) -> None:
            touched.add(node_id if node_id is not None else EVERY_NODE)

        dbapi_connection.create_function(FUNCTION, 1, touch)
        cursor = dbapi_connection.cursor()
        try:
            present = {
                row[0]
                for row in cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
            }
            for statement in _triggers(present):
                cursor.execute(statement)
        finally:
            cursor.close()

    def _on_commit(self, connection: Any) -> None:
        touched: set[str] = connection.info.get("energy_touched") or set()
        if not touched:
            return
        with self._lock:
            self._count += 1
            if EVERY_NODE in touched:
                self._everything = self._count
            for node_id in touched - {EVERY_NODE}:
                self._revisions[node_id] = self._count
            touched.clear()
        connection.info["energy_committed"] = True

    def _on_rollback(self, connection: Any) -> None:
        # Only a whole transaction's rollback: a savepoint's leaves the writes before it.
        touched = connection.info.get("energy_touched")
        if touched:
            touched.clear()

    def _on_checkin(self, _dbapi_connection: Any, record: Any) -> None:
        if record.info.pop("energy_committed", False):
            with self._lock:
                self._count += 1  # the commit has landed: sessions from now on read it

    def _on_begin(self, session: Session, _transaction: Any, _connection: Any) -> None:
        with self._lock:
            session.info.setdefault("energy_count", self._count)

    def _on_flush(self, session: Session, _context: Any) -> None:
        session.info["energy_wrote"] = True

    # ---------- kept values ----------

    def clear(self) -> None:
        """Forget every kept value (for a test that changes the code's own tables)."""
        with self._lock:
            self._entries.clear()

    def entry(self, session: Session | None, node_id: str, settings: tuple) -> Entry | None:
        """The node's kept energies for these settings, made empty when there are none or
        they are out of date; None when this session may neither keep nor use any."""
        if session is None or session.info.get("energy_wrote"):
            return None
        if session.new or session.dirty or session.deleted:
            return None
        count = session.info.get("energy_count")
        if count is None:
            return None
        key = (node_id, settings)
        with self._lock:
            since = max(self._revisions.get(node_id, 0), self._everything)
            if count <= since:
                return Entry(count)  # this session may have read it before the change landed
            found = self._entries.get(key)
            if found is not None and found.count > since:
                self._entries.move_to_end(key)
                return found
            made = Entry(count)
            self._entries[key] = made
            while len(self._entries) > KEPT:
                self._entries.popitem(last=False)
            return made


def kept(value: Any) -> Any:
    """A copy for the memo or out of it: callers change the values they are given."""
    return copy.deepcopy(value)


def memo_of(session: Session | None) -> Memo | None:
    return session.info.get("energy_memo") if session is not None else None


def _triggers(present: set[str]) -> list[str]:
    statements = []
    for table in Base.metadata.sorted_tables:
        name = table.name
        if name not in present or name in UNUSED_TABLES:
            continue
        if name in NODE_TABLES:
            column = NODE_TABLES[name]
            node = {"NEW": f"NEW.{column}", "OLD": f"OLD.{column}"}
        elif name in CALCULATION_TABLES:
            column = CALCULATION_TABLES[name]
            node = {
                row: f"(SELECT node_id FROM calculations WHERE id = {row}.{column})"
                for row in ("NEW", "OLD")
            }
        else:
            node = {"NEW": "NULL", "OLD": "NULL"}
        when = ""
        if name == "nodes":
            used = [c.name for c in table.columns if c.name not in UNUSED_NODE_COLUMNS]
            when = " WHEN " + " OR ".join(f'NEW."{c}" IS NOT OLD."{c}"' for c in used)
        for action, rows in (("INSERT", ["NEW"]), ("UPDATE", ["NEW", "OLD"]), ("DELETE", ["OLD"])):
            calls = " ".join(f"SELECT {FUNCTION}({node[row]});" for row in rows)
            statements.append(
                f'CREATE TEMP TRIGGER IF NOT EXISTS "energy_{name}_{action.lower()}" '
                f'AFTER {action} ON main."{name}" FOR EACH ROW'
                f"{when if action == 'UPDATE' else ''} BEGIN {calls} END"
            )
    return statements
