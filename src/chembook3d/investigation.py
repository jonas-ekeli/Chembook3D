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
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from threading import Condition, RLock
from typing import BinaryIO
from uuid import uuid4
from weakref import WeakValueDictionary, finalize

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import Engine, create_engine, event, func, select
from sqlalchemy.engine import URL
from sqlalchemy.orm import Session, sessionmaker

from chembook3d.models import Branch, InvestigationInfo, Node, NodeKind, utcnow

log = logging.getLogger(__name__)

DB_NAME = "investigation.sqlite"
FILES_DIR = "files"
LOCK_NAME = ".chembook3d.lock"
OWNER_NAME = ".chembook3d.owner"
BACKUP_DIR = "backups"


class InvestigationError(Exception):
    pass


class InvestigationLocked(InvestigationError):
    """The lock file says another running app has this investigation open (P22)."""

    def __init__(self, lock: dict, *, can_force: bool = True):
        self.lock = {**lock, "can_force": can_force}
        message = (
            f"investigation is open on {lock.get('host', '?')} since {lock.get('opened_at', '?')}"
        )
        if not can_force:
            message += ". Close the other app instance before opening it here."
        super().__init__(message)


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
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    process_query_limited_information, still_active, error_access_denied = 0x1000, 259, 5
    handle = kernel32.OpenProcess(process_query_limited_information, False, pid)
    if not handle:
        return ctypes.get_last_error() == error_access_denied  # exists, not ours to query
    try:
        exit_code = wintypes.DWORD()
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
        value = json.loads((folder / LOCK_NAME).read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else None
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def _lock_is_stale(lock: dict) -> bool:
    """A lock left by a crashed app on this same machine can be taken over silently."""
    try:
        pid = int(lock.get("pid", -1))
    except (TypeError, ValueError):
        return False
    return lock.get("host") == socket.gethostname() and not _pid_alive(pid)


_owners: WeakValueDictionary[Path, "InvestigationOwnership"] = WeakValueDictionary()


def _release_ownership_resources(folder: Path, handle: BinaryIO, token: str) -> None:
    """Finalizer arguments own resources only; retaining the owner would prevent GC."""
    if handle.closed:
        return
    try:
        lock = _read_lock(folder)
        if lock and lock.get("owner") == token:
            (folder / LOCK_NAME).unlink(missing_ok=True)
    finally:
        handle.close()  # also releases the OS lock


@dataclass
class InvestigationOwnership:
    """P22: an OS-held lease, retained even while closing SQLite to replace its file.

    The sidecar is never removed: unlinking an advisory lock permits a second process
    to lock a different inode. The JSON marker remains the portable, human-readable
    warning for another computer. Force overrides that warning, never a held OS lock.
    """

    folder: Path
    handle: BinaryIO
    token: str
    database_open: bool = False
    _mutex: RLock = field(default_factory=RLock, repr=False)
    _session_condition: Condition = field(default_factory=Condition, repr=False)
    _sessions: int = field(default=0, repr=False)
    _accept_sessions: bool = field(default=True, repr=False)
    _finalizer: finalize = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._finalizer = finalize(
            self,
            _release_ownership_resources,
            self.folder,
            self.handle,
            self.token,
        )

    def require(self, folder: Path, *, closed: bool = False) -> None:
        if self.handle.closed or Path(folder).resolve() != self.folder:
            raise InvestigationError("investigation ownership is no longer held")
        if closed and self.database_open:
            raise InvestigationError("close the investigation before replacing its database")

    def release(self) -> None:
        with self._mutex:
            try:
                self._finalizer()  # exactly once, including when explicitly released
            finally:
                if _owners.get(self.folder) is self:
                    del _owners[self.folder]

    @contextmanager
    def operation(self, folder: Path, *, closed: bool = False) -> Iterator[None]:
        """Serialize same-process sync/close borrowers before checking the held lease."""
        with self._mutex:
            self.require(folder, closed=closed)
            yield

    def admit_session(self) -> None:
        with self._session_condition:
            if not self._accept_sessions or self.handle.closed:
                raise InvestigationError("the investigation is closing or closed; open it again")
            self._sessions += 1

    def finish_session(self) -> None:
        with self._session_condition:
            self._sessions -= 1
            self._session_condition.notify_all()

    def drain_sessions(self) -> None:
        # Session context setup and teardown may run on different FastAPI worker
        # threads. Count admissions rather than holding a thread-owned lock at yield.
        with self._session_condition:
            self._accept_sessions = False
            self._session_condition.wait_for(lambda: self._sessions == 0)

    def __enter__(self) -> "InvestigationOwnership":
        self.require(self.folder)
        return self

    def __exit__(self, *_args) -> None:
        self.release()


def active_ownership(folder: Path) -> InvestigationOwnership | None:
    """An open database's lease, for service callers syncing their own live database."""
    owner = _owners.get(Path(folder).resolve())
    return owner if owner is not None and owner.database_open else None


def acquire_ownership(
    folder: Path, force: bool = False, *, existing: bool = True
) -> InvestigationOwnership:
    folder = Path(folder).resolve()
    if existing and not (folder / DB_NAME).is_file():
        raise InvestigationError(f"{folder} does not contain an investigation")
    handle = (folder / OWNER_NAME).open("a+b")
    try:
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise InvestigationLocked(
                _read_lock(folder) or {"host": "another process"},
                can_force=False,
            ) from exc
        lock = _read_lock(folder)
        if lock and not force and not _lock_is_stale(lock):
            raise InvestigationLocked(lock)
        owner = InvestigationOwnership(folder, handle, uuid4().hex)
        _write_lock(folder, owner.token)
        _owners[folder] = owner
        return owner
    except BaseException:
        handle.close()
        raise


@dataclass
class Investigation:
    folder: Path
    engine: Engine
    sessions: sessionmaker[Session]
    ownership: InvestigationOwnership

    @property
    def name(self) -> str:
        with self.sessions() as session:
            info = session.get(InvestigationInfo, 1)
            return info.name if info else self.folder.name

    def counts(self) -> tuple[int, int]:
        """D182: the nodes on the canvas (free species left out) and the branches, which the
        start page shows beside the investigation's name."""
        with self.sessions() as session:
            nodes = session.scalar(
                select(func.count()).select_from(Node).where(Node.kind == NodeKind.NODE)
            )
            branches = session.scalar(select(func.count()).select_from(Branch))
            return nodes or 0, branches or 0

    def close(self, *, release_ownership: bool = True) -> None:
        with self.ownership._mutex:
            self.ownership.drain_sessions()
            self.engine.dispose()
            self.ownership.database_open = False
            if release_ownership:
                self.ownership.release()


class _OwnedSession(Session):
    """A checked-out session keeps its investigation admitted until it really closes."""

    def __init__(self, *args, ownership: InvestigationOwnership, **kwargs):
        self._ownership = ownership
        self._admitted = False
        ownership.admit_session()
        try:
            super().__init__(*args, **kwargs)
            self._admitted = True
        except BaseException:
            ownership.finish_session()
            raise

    def close(self) -> None:
        try:
            super().close()
        finally:
            if self._admitted:
                self._admitted = False
                self._ownership.finish_session()

    def __del__(self) -> None:
        # A discarded Session can be part of a transaction cycle. Close its actual
        # SQLAlchemy resources before releasing admission, even when GC breaks it.
        if getattr(self, "_admitted", False):
            try:
                self.close()
            except Exception:
                log.exception("could not close an abandoned investigation session")


def _session_factory(engine: Engine, ownership: InvestigationOwnership) -> sessionmaker[Session]:
    # SQLAlchemy normally permits reuse after close; that would bypass admission.
    return sessionmaker(
        engine,
        class_=_OwnedSession,
        ownership=ownership,
        expire_on_commit=False,
        close_resets_only=False,
    )


def _write_lock(folder: Path, token: str) -> None:
    lock = {
        "host": socket.gethostname(),
        "pid": os.getpid(),
        "opened_at": utcnow().isoformat(),
        "owner": token,
    }
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
    ownership = acquire_ownership(folder, existing=False)
    engine = None
    try:
        # Another creator may have finished after our initial empty-folder check.
        if any(child.name not in (OWNER_NAME, LOCK_NAME) for child in folder.iterdir()):
            raise InvestigationError(f"{folder} is not empty")
        (folder / FILES_DIR).mkdir()
        engine = _make_engine(folder / DB_NAME)
        _migrate(engine, folder / DB_NAME)
        sessions = _session_factory(engine, ownership)
        from chembook3d.services import repairs, thermo_settings  # services import this module

        # D159: a new investigation starts with the app's thermochemistry settings.
        defaults = thermo_settings.values(None)
        with sessions.begin() as session:
            session.add(
                InvestigationInfo(id=1, name=name, repairs=list(repairs.REPAIRS), **defaults)
            )
        ownership.database_open = True
        return Investigation(folder, engine, sessions, ownership)
    except BaseException:
        if engine is not None:
            engine.dispose()
        ownership.release()
        raise


def check_lock(folder: Path, force: bool = False) -> None:
    """Advisory availability check only; mutation requires retaining acquire_ownership."""
    if active_ownership(folder) is not None:
        return
    with acquire_ownership(folder, force):
        pass


def open_investigation(
    folder: Path,
    force: bool = False,
    allow_upgrade: bool = True,
    *,
    ownership: InvestigationOwnership | None = None,
) -> Investigation:
    """Open an existing investigation. Raises InvestigationLocked if another running app
    holds the lock (see check_lock), InvestigationTooNew if a newer app saved it, and
    NeedsUpgrade if it needs a migration and allow_upgrade is False."""
    folder = Path(folder)
    db_path = folder / DB_NAME
    ownership = ownership or acquire_ownership(folder, force)
    ownership.require(folder, closed=True)
    engine = None
    try:
        if not ownership._accept_sessions:
            # Reopening this token would also revive stale factories from its old
            # Investigation. Close→sync retains it; a later open acquires a new one.
            raise InvestigationError("a closed investigation must acquire new ownership to reopen")
        engine = _make_engine(db_path)
        _migrate(engine, db_path, allow_upgrade)
        (folder / FILES_DIR).mkdir(exist_ok=True)
        _remove_orphaned_copies(folder, engine)
        sessions = _session_factory(engine, ownership)
        _run_repairs(folder, sessions)
        ownership.database_open = True
        return Investigation(folder, engine, sessions, ownership)
    except BaseException:
        if engine is not None:
            engine.dispose()  # Windows keeps a file open until its connections are closed
        ownership.release()
        raise


def _run_repairs(folder: Path, sessions: sessionmaker[Session]) -> None:
    """Correct records an earlier version got wrong (D100). A repair that fails is logged and
    tried again at the next open; it never keeps the investigation from opening."""
    from chembook3d.services import repairs  # the services import this module

    try:
        with sessions.begin() as session:
            repairs.run(session, folder)
    except Exception:
        log.exception("a repair of %s failed; it is tried again when it is next opened", folder)
