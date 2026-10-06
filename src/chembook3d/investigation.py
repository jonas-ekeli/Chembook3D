"""An investigation is one self-contained folder (D21, FR-INV-01):

    <folder>/investigation.sqlite   all records
    <folder>/files/                 copied source files (phase 2)
    <folder>/.chembook3d.lock       present while the investigation is open (P22)

Nothing inside refers to the folder's absolute location, so it can be zipped, moved or
synced between Windows and Linux (FR-INV-03, NFR-PORT-01).
"""

import json
import logging
import os
import re
import shutil
import socket
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import Engine, create_engine, event
from sqlalchemy.engine import URL
from sqlalchemy.orm import Session, sessionmaker

from chembook3d.models import InvestigationInfo, utcnow

log = logging.getLogger(__name__)

DB_NAME = "investigation.sqlite"
FILES_DIR = "files"
LOCK_NAME = ".chembook3d.lock"
BACKUP_DIR = "backups"


class InvestigationError(Exception):
    pass


class InvestigationLocked(InvestigationError):
    """The lock file says another running app has this investigation open (P22)."""

    def __init__(self, lock: dict):
        self.lock = lock
        super().__init__(
            f"investigation is open on {lock.get('host', '?')} since {lock.get('opened_at', '?')}"
        )


UPDATE_COMMANDS = "git pull, uv sync, then uv run python scripts/build_frontend.py"


class InvestigationTooNew(InvestigationError):
    """The database has a schema revision this app does not know (D71, FR-SYNC-01)."""

    def __init__(self, revision: str, head: str):
        self.revision = revision
        self.head = head
        super().__init__(
            "This investigation was saved by a newer version of Chembook3D (database version "
            f"{revision}; this app knows up to {head}). Update the app on this computer, then "
            f"open it again. In the Chembook3D folder: {UPDATE_COMMANDS}."
        )


class NeedsUpgrade(InvestigationError):
    """Opening would upgrade the schema, and the caller asked to confirm that first (D71c)."""

    def __init__(self, revision: str, head: str):
        self.revision = revision
        self.head = head
        super().__init__(f"opening upgrades the database from {revision} to {head}")


def _alembic_config(connection) -> Config:
    config = Config()
    config.set_main_option("script_location", "chembook3d:migrations")
    config.attributes["connection"] = connection
    return config


def _make_engine(db_path: Path) -> Engine:
    engine = create_engine(URL.create("sqlite", database=str(db_path)))

    @event.listens_for(engine, "connect")
    def _sqlite_pragmas(dbapi_connection, _record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        # P20: rollback journal instead of WAL, so the folder is one consistent unit.
        cursor.execute("PRAGMA journal_mode=DELETE")
        cursor.close()

    return engine


def _pid_alive_windows(pid: int) -> bool:
    # os.kill(pid, 0) would terminate the process on Windows, so ask the kernel instead.
    import ctypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    process_query_limited_information, still_active, error_access_denied = 0x1000, 259, 5
    handle = kernel32.OpenProcess(process_query_limited_information, False, pid)
    if not handle:
        return ctypes.get_last_error() == error_access_denied  # exists, not ours to query
    try:
        exit_code = ctypes.c_ulong()
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
            return True
        return exit_code.value == still_active
    finally:
        kernel32.CloseHandle(handle)


def _pid_alive(pid: int) -> bool:
    if pid == os.getpid():
        return True
    if pid <= 0:
        return False
    if os.name == "nt":
        return _pid_alive_windows(pid)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except OSError:
        return True  # exists but belongs to someone else
    return True


def _read_lock(folder: Path) -> dict | None:
    try:
        return json.loads((folder / LOCK_NAME).read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def _lock_is_stale(lock: dict) -> bool:
    """A lock left by a crashed app on this same machine can be taken over silently."""
    return lock.get("host") == socket.gethostname() and not _pid_alive(int(lock.get("pid", -1)))


@dataclass
class Investigation:
    folder: Path
    engine: Engine
    sessions: sessionmaker[Session]

    @property
    def name(self) -> str:
        with self.sessions() as session:
            info = session.get(InvestigationInfo, 1)
            return info.name if info else self.folder.name

    def close(self) -> None:
        self.engine.dispose()
        lock = _read_lock(self.folder)
        if lock and lock.get("pid") == os.getpid() and lock.get("host") == socket.gethostname():
            (self.folder / LOCK_NAME).unlink(missing_ok=True)


def _write_lock(folder: Path) -> None:
    lock = {"host": socket.gethostname(), "pid": os.getpid(), "opened_at": utcnow().isoformat()}
    (folder / LOCK_NAME).write_text(json.dumps(lock), encoding="utf-8")


def _migrate(engine: Engine, db_path: Path, allow_upgrade: bool = True) -> str | None:
    """Bring the schema to the latest revision. Before changing an existing database, copy
    it to backups/ (NFR-DATA-03). Returns the backup path, or None when nothing changed.
    A revision this app does not know was written by a newer app: nothing is changed (D71)."""
    with engine.connect() as connection:
        current = MigrationContext.configure(connection).get_current_revision()
        scripts = ScriptDirectory.from_config(_alembic_config(connection))
        head = scripts.get_current_head()
        known = {script.revision for script in scripts.walk_revisions()}
    if current == head:
        return None
    if current is not None and current not in known:
        raise InvestigationTooNew(current, head)
    if current is not None and not allow_upgrade:
        raise NeedsUpgrade(current, head)

    backup = None
    if current is not None:
        backup_dir = db_path.parent / BACKUP_DIR
        backup_dir.mkdir(exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        backup = backup_dir / f"{DB_NAME}.{current}.{stamp}.bak"
        engine.dispose()
        shutil.copy2(db_path, backup)

    with engine.connect() as connection:
        # Some migrations rebuild a table (SQLite cannot alter columns in place). With foreign
        # keys on, dropping the old copy of `nodes` would cascade into its calculations, so
        # they are off while migrating and checked afterwards (sqlite.org/lang_altertable).
        connection.exec_driver_sql("PRAGMA foreign_keys=OFF")
        connection.commit()
        try:
            with connection.begin():
                command.upgrade(_alembic_config(connection), "head")
                broken = connection.exec_driver_sql("PRAGMA foreign_key_check").fetchall()
                if broken:
                    raise InvestigationError(
                        f"migration left {len(broken)} broken references; the database was "
                        f"backed up to {backup}"
                    )
        finally:
            connection.exec_driver_sql("PRAGMA foreign_keys=ON")
            connection.commit()
    return str(backup) if backup else None


def _remove_orphaned_copies(folder: Path, engine: Engine) -> None:
    """P26, NFR-DATA-04: an import interrupted after copying its file but before its records
    were committed leaves files/<source-file-id>/ with no record. Only such folders, named
    like record ids, are removed; nothing else in files/ is touched."""
    with engine.connect() as connection:
        known = {row[0] for row in connection.exec_driver_sql("SELECT id FROM source_files")}
    for child in (folder / FILES_DIR).iterdir():
        if child.is_dir() and re.fullmatch(r"[0-9a-f]{32}", child.name) and child.name not in known:
            shutil.rmtree(child, ignore_errors=True)


def create_investigation(folder: Path, name: str) -> Investigation:
    folder = Path(folder)
    if (folder / DB_NAME).exists():
        raise InvestigationError(f"{folder} already contains an investigation")
    if folder.exists() and any(folder.iterdir()):
        raise InvestigationError(f"{folder} is not empty")
    folder.mkdir(parents=True, exist_ok=True)
    (folder / FILES_DIR).mkdir()

    engine = _make_engine(folder / DB_NAME)
    _migrate(engine, folder / DB_NAME)
    sessions = sessionmaker(engine, expire_on_commit=False)
    from chembook3d.services import repairs  # the services import this module

    with sessions.begin() as session:
        session.add(InvestigationInfo(id=1, name=name, repairs=list(repairs.REPAIRS)))
    _write_lock(folder)
    return Investigation(folder, engine, sessions)


def check_lock(folder: Path, force: bool = False) -> None:
    """Raise InvestigationLocked if another running app holds the lock, unless force=True
    (the user chose to open it anyway)."""
    folder = Path(folder)
    if not (folder / DB_NAME).is_file():
        raise InvestigationError(f"{folder} does not contain an investigation")
    lock = _read_lock(folder)
    if lock and not force and not _lock_is_stale(lock):
        mine = lock.get("host") == socket.gethostname() and lock.get("pid") == os.getpid()
        if not mine:
            raise InvestigationLocked(lock)


def open_investigation(
    folder: Path, force: bool = False, allow_upgrade: bool = True
) -> Investigation:
    """Open an existing investigation. Raises InvestigationLocked if another running app
    holds the lock (see check_lock), InvestigationTooNew if a newer app saved it, and
    NeedsUpgrade if it needs a migration and allow_upgrade is False."""
    folder = Path(folder)
    db_path = folder / DB_NAME
    check_lock(folder, force)

    engine = _make_engine(db_path)
    try:
        _migrate(engine, db_path, allow_upgrade)
    except BaseException:
        engine.dispose()  # Windows keeps a file open until its connections are closed
        raise
    (folder / FILES_DIR).mkdir(exist_ok=True)
    _remove_orphaned_copies(folder, engine)
    sessions = sessionmaker(engine, expire_on_commit=False)
    _run_repairs(folder, sessions)
    _write_lock(folder)
    return Investigation(folder, engine, sessions)


def _run_repairs(folder: Path, sessions: sessionmaker[Session]) -> None:
    """Correct records an earlier version got wrong (D100). A repair that fails is logged and
    tried again at the next open; it never keeps the investigation from opening."""
    from chembook3d.services import repairs  # the services import this module

    try:
        with sessions.begin() as session:
            repairs.run(session, folder)
    except Exception:
        log.exception("a repair of %s failed; it is tried again when it is next opened", folder)
