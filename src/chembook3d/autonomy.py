"""Claude working on its own (D223, FR-PANEL-05…07): the switch "Work on its own" of the
Claude panel, kept in memory by the running app for the investigation it was turned on for.

While it is on, the panel starts Claude Code with every changing tool allowed (so it asks
nothing in its terminal) and with a guide that tells it to decide and carry on
(`claude_panel.prepare`); the app deletes without asking what Claude made itself and nobody
else changed since (`api/live.py`), and refuses a local job Claude starts whose estimate is
over the limit the user set (`api/local.py`). It is off again when the app restarts or another
investigation is opened. Nothing about a server changes (D126).

The ledger of what Claude made is kept from the start of the app, on or off, so that turning
the switch on later covers what Claude made before in the same run of the app. It is filled by
`services.history.record` through `history.MADE`, which `api.live.ChangeTracker` sets for each
request.
"""

from __future__ import annotations

import sqlite3
import threading
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from chembook3d.investigation import BACKUP_DIR, DB_NAME

AGENT = "claude"
DEFAULT_JOB_LIMIT = 3600  # seconds: the longest local job Claude may start on its own (A153)
JOB_LIMITS = (600, 3600, 4 * 3600, 12 * 3600, 48 * 3600)  # what the dialog offers
# The history's record types whose entries are kept under the record's own id (a note's are
# read from its snapshot), and the actions that make one ("reconnect" makes a group, D152).
OWN_TYPES = frozenset(
    {
        "node",
        "calculation",
        "group",
        "step",
        "branch",
        "transition",
        "sub_investigation",
        "note",
        "selectivity",
        "turnover",
        "equilibrium",
    }
)
CREATES = frozenset({"create", "reconnect"})


@dataclass
class Autonomy:
    lock: threading.Lock = field(default_factory=threading.Lock)
    folder: str = ""  # the investigation it is on for; "" while off
    job_limit: int = DEFAULT_JOB_LIMIT
    cloud: bool = False  # Claude may also start cloud sessions without asking
    since: str | None = None
    backup: str | None = None  # the copy of the database made when it was turned on
    made: set[str] = field(default_factory=set)

    def on_for(self, folder: str | Path | None) -> bool:
        return bool(self.folder) and folder is not None and str(folder) == self.folder

    def turn_on(self, folder: Path, job_limit: int, cloud: bool) -> None:
        backup = backup_database(folder)
        with self.lock:
            self.folder = str(folder)
            self.job_limit = int(job_limit)
            self.cloud = bool(cloud)
            self.since = datetime.now(UTC).isoformat(timespec="seconds")
            self.backup = backup

    def turn_off(self) -> None:
        with self.lock:
            self.folder = ""
            self.since = None

    # ---------- the ledger of what Claude made ----------

    def note(
        self, who: str, record_type: str, record_id: str, action: str, old: Any, new: Any
    ) -> None:
        """One history entry: a record Claude creates is its own until anyone else changes it."""
        if record_type == "note":
            # a note's entries are kept under its node; its own id is in the snapshot
            ids = [v["id"] for v in (new, old) if isinstance(v, dict) and v.get("id")]
            if not ids:
                return
            record_id = ids[0]
        with self.lock:
            if who != AGENT:
                self.made.discard(record_id)
            elif record_type in OWN_TYPES and action in CREATES:
                self.made.add(record_id)

    def owns(self, *record_ids: str) -> bool:
        with self.lock:
            return all(r in self.made for r in record_ids)


def backup_database(folder: Path) -> str | None:
    """A copy of the investigation's database in backups/, taken with SQLite's own backup so a
    change under way cannot leave it half written. The file's name, or None when there is no
    database to copy."""
    source = folder / DB_NAME
    if not source.is_file():
        return None
    backups = folder / BACKUP_DIR
    backups.mkdir(exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    target = backups / f"{DB_NAME}.claude-{stamp}.bak"
    with sqlite3.connect(source) as live, sqlite3.connect(target) as copy:
        live.backup(copy)
    live.close()
    copy.close()
    return target.name


def limit_text(seconds: int) -> str:
    if seconds < 3600:
        return f"{round(seconds / 60)} minutes"
    hours = seconds / 3600
    if hours == 1:
        return "1 hour"
    return f"{hours / 24:g} days" if hours >= 48 and hours % 24 == 0 else f"{hours:g} hours"
