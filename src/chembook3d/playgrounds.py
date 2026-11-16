"""Playgrounds (D235, FR-INV-04): a full working copy of an investigation to try things in, kept
inside the original so it moves, zips and copies with it:

    <original>/playgrounds/<name>/investigation.sqlite    the copy, worked in like any other
    <original>/playgrounds/<name>/files/                  the original's files, hard-linked
    <original>/playgrounds/<name>/playground/base.sqlite  the original as it was, never written

Records keep their ids, so a merge (a later PR) can compare base, playground and the original as
it is now record by record. The copy is the investigation's own: Git, Claude's folder, jobs,
exports and backups stay with the original (A165).
"""

import os
import re
import shutil
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from send2trash import send2trash

from chembook3d.investigation import (
    BACKUP_DIR,
    DB_NAME,
    FILES_DIR,
    LOCK_NAME,
    OWNER_NAME,
    Investigation,
    InvestigationError,
    acquire_ownership,
)
from chembook3d.models import new_id, utcnow

PLAYGROUNDS_DIR = "playgrounds"
BASE_DIR = "playground"
BASE_NAME = "base.sqlite"
BACK = "../.."  # from <original>/playgrounds/<name>/ to the original
MAKING = ".making-"  # a playground being made; renamed to its name once whole
# The UI tests' stand-in for the recycle bin: a folder discarded playgrounds are moved into, so a
# test run never fills the recycle bin of the computer it runs on.
TRASH_ENV = "CHEMBOOK3D_TRASH_DIR"
NAME_LIMIT = 100

# What a playground does not copy (D235, A165): Git and its files would push to the original's
# repository, Claude's folder and the jobs belong to the original (a running job keeps writing
# there), and exports and backups are the original's. The database is copied on its own.
LEFT_OUT = {
    DB_NAME,
    f"{DB_NAME}-journal",
    LOCK_NAME,
    OWNER_NAME,
    ".git",
    ".gitignore",
    ".gitattributes",
    ".claude",
    "local-jobs",
    "jobs",
    "exports",
    BACKUP_DIR,
    PLAYGROUNDS_DIR,
    BASE_DIR,
}

# Characters Windows refuses in a folder name, and its reserved names (NFR-PORT-01).
_BAD = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_RESERVED = re.compile(r"^(con|prn|aux|nul|com[1-9]|lpt[1-9])(\..*)?$", re.IGNORECASE)


class PlaygroundError(InvestigationError):
    pass


@dataclass
class Made:
    folder: Path
    copied: int  # files copied because the disk could not hard-link them (A166)
    linked: int


def next_name(original: Path) -> str:
    """ "Playground 1", or the first number whose folder is not taken (A165)."""
    n = 1
    while (Path(original) / PLAYGROUNDS_DIR / folder_name(f"Playground {n}")).exists():
        n += 1
    return f"Playground {n}"


def clean_name(name: str) -> str:
    name = " ".join(name.split())
    if not name:
        raise PlaygroundError("Give the playground a name")
    if len(name) > NAME_LIMIT:
        raise PlaygroundError(f"A playground's name can have at most {NAME_LIMIT} characters")
    return name


def folder_name(name: str) -> str:
    """The folder a playground of this name lives in: the name with what Windows refuses in a
    folder name replaced by "-", so the folder opens on Windows and Linux alike."""
    folder = _BAD.sub("-", clean_name(name)).rstrip(" .")
    if not folder or folder.startswith("."):
        folder = "playground" + folder
    if _RESERVED.match(folder):
        folder += "-"
    return folder


def _read(db: Path, sql: str, params: tuple = ()) -> list[tuple] | None:
    """Rows from a database read without opening the investigation (another app may have it
    open; reading is safe with its rollback journal, P20). None when it cannot be read."""
    if not db.is_file():
        return None
    try:
        uri = db.resolve().as_uri() + "?mode=ro"
        with closing(sqlite3.connect(uri, uri=True, timeout=2)) as connection:
            return connection.execute(sql, params).fetchall()
    except (sqlite3.Error, OSError, ValueError):
        return None


def _time(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(value) if isinstance(value, str) else None
    except ValueError:
        return None


def _stored(when: datetime) -> str:
    """A time as SQLAlchemy stores one in SQLite, so the models read it back."""
    return when.strftime("%Y-%m-%d %H:%M:%S.%f")


def original_folder(folder: Path, back: str | None) -> Path:
    return (Path(folder) / (back or BACK)).resolve()


def _link(source: Path, target: Path) -> bool:
    """A hard link where the disk allows one, else a copy (D235b). Stored files are never
    changed, so a second name for the same bytes is as safe as a copy, and deleting either
    leaves the other. True when linked."""
    try:
        os.link(source, target)
        return True
    except OSError:
        shutil.copy2(source, target)
        return False


def _link_tree(source: Path, target: Path) -> tuple[int, int]:
    linked = copied = 0
    for entry in sorted(source.iterdir()):
        if entry.name in LEFT_OUT or entry.is_symlink():
            continue
        if entry.is_file():
            done = _link(entry, target / entry.name)
            linked, copied = linked + done, copied + (not done)
            continue
        if not entry.is_dir():
            continue
        for root, dirs, files in os.walk(entry):
            dirs[:] = sorted(d for d in dirs if not (Path(root) / d).is_symlink())
            here = target / Path(root).relative_to(source)
            here.mkdir(parents=True, exist_ok=True)
            for name in sorted(files):
                path = Path(root) / name
                if path.is_symlink():
                    continue
                done = _link(path, here / name)
                linked, copied = linked + done, copied + (not done)
    return linked, copied


def make(original: Investigation, name: str) -> Made:
    """Make a playground of an open investigation: its database copied twice with SQLite's
    online backup (the playground's own and the frozen base), its other files hard-linked.
    Made under a hidden name and renamed once whole, so a half-made one is never listed."""
    info = original.info()
    if info is None:
        raise PlaygroundError("This investigation has no record of itself")
    if info.playground_of:
        raise PlaygroundError(
            "This is a playground. Make a new playground from its original instead (D235)."
        )
    name = clean_name(name)
    parent = original.folder / PLAYGROUNDS_DIR
    target = parent / folder_name(name)
    if target.exists():
        raise PlaygroundError(f"A playground named “{target.name}” already exists here")
    uuid = info.uuid
    if not uuid:
        from chembook3d.models import InvestigationInfo

        uuid = new_id()
        with original.sessions.begin() as session:
            session.get(InvestigationInfo, 1).uuid = uuid
    parent.mkdir(exist_ok=True)
    work = parent / f"{MAKING}{new_id()}"
    work.mkdir()
    try:
        (work / BASE_DIR).mkdir()
        base = work / BASE_DIR / BASE_NAME
        with (
            closing(sqlite3.connect(original.folder / DB_NAME)) as live,
            closing(sqlite3.connect(base)) as copy,
        ):
            live.backup(copy)
        shutil.copyfile(base, work / DB_NAME)
        linked, copied = _link_tree(original.folder, work)
        (work / FILES_DIR).mkdir(exist_ok=True)
        with closing(sqlite3.connect(work / DB_NAME)) as db:
            fork = db.execute("SELECT MAX(id) FROM history").fetchone()[0]
            db.execute(
                "UPDATE investigation_info SET uuid = ?, playground_of = ?, "
                "playground_original = ?, playground_name = ?, playground_created_at = ?, "
                "playground_history_id = ?, playground_merged_at = NULL WHERE id = 1",
                (new_id(), uuid, BACK, name, _stored(utcnow()), fork),
            )
            db.commit()
        if target.exists():
            raise PlaygroundError(f"A playground named “{target.name}” already exists here")
        os.replace(work, target)
    except BaseException:
        shutil.rmtree(work, ignore_errors=True)
        raise
    return Made(target, copied, linked)


def describe(investigation: Investigation) -> dict[str, Any] | None:
    """What the playground band shows (D235), or None for an investigation that is not one."""
    info = investigation.info()
    if info is None or not info.playground_of:
        return None
    original = original_folder(investigation.folder, info.playground_original)
    found = _read(original / DB_NAME, "SELECT name, uuid FROM investigation_info WHERE id = 1")
    return {
        "name": info.playground_name or investigation.folder.name,
        "original_folder": str(original),
        "original_name": found[0][0] if found else original.name,
        "original_found": bool(found) and found[0][1] == info.playground_of,
        "created_at": info.playground_created_at,
        "merged_at": info.playground_merged_at,
    }


def rows(original: Path) -> list[dict[str, Any]]:
    """The playgrounds in a folder's playgrounds/, oldest first, for the start page (D235), read
    without opening any database. `changed` counts the original's history entries since the
    playground was made (None when its database cannot be read)."""
    parent = Path(original) / PLAYGROUNDS_DIR
    if not parent.is_dir():
        return []
    own = _read(Path(original) / DB_NAME, "SELECT uuid FROM investigation_info WHERE id = 1")
    found = []
    for child in parent.iterdir():
        if not child.is_dir() or child.name.startswith("."):
            continue
        got = _read(
            child / DB_NAME,
            "SELECT playground_of, playground_name, playground_created_at, "
            "playground_history_id, playground_merged_at FROM investigation_info WHERE id = 1",
        )
        if not got or not got[0][0]:
            continue
        of, name, created, fork, merged = got[0]
        counted = _read(
            Path(original) / DB_NAME, "SELECT COUNT(*) FROM history WHERE id > ?", (fork or 0,)
        )
        found.append(
            {
                "folder": str(child),
                "name": name or child.name,
                "created_at": _time(created),
                "merged_at": _time(merged),
                "changed": counted[0][0] if counted else None,
                # made from another investigation (a folder copied in by hand)
                "foreign": bool(own) and own[0][0] != of,
            }
        )
    found.sort(key=lambda row: (row["created_at"] or datetime.min, row["name"]))
    return found


def _playground_info(folder: Path) -> tuple[str, str] | None:
    """(name, original's name) of the playground in `folder`, read without opening it; None when
    the folder is not a playground in its original's playgrounds/ folder."""
    folder = Path(folder)
    got = _read(
        folder / DB_NAME,
        "SELECT playground_of, playground_name, playground_original FROM investigation_info "
        "WHERE id = 1",
    )
    if not got or not got[0][0] or folder.parent.name != PLAYGROUNDS_DIR:
        return None
    original = original_folder(folder, got[0][2])
    if original != folder.resolve().parent.parent:
        return None
    named = _read(original / DB_NAME, "SELECT name FROM investigation_info WHERE id = 1")
    return got[0][1] or folder.name, named[0][0] if named else original.name


def check(folder: Path) -> tuple[str, str]:
    """(name, original's name) of a playground; refused for any other folder (D235g)."""
    found = _playground_info(folder)
    if found is None:
        raise PlaygroundError(f"{folder} is not a playground")
    return found


def discard_preview(folder: Path) -> dict[str, Any]:
    """What the Discard confirmation names (D235g): the playground, its original, its folder's
    size, and how much discarding it frees (files hard-linked with the original's free nothing
    while the original keeps them)."""
    found = check(folder)
    size = frees = files = 0
    for root, _dirs, names in os.walk(folder):
        for name in names:
            try:
                stat = (Path(root) / name).lstat()
            except OSError:
                continue
            files += 1
            size += stat.st_size
            if stat.st_nlink <= 1:
                frees += stat.st_size
    return {
        "folder": str(Path(folder).resolve()),
        "name": found[0],
        "original_name": found[1],
        "files": files,
        "size": size,
        "frees": frees,
    }


def discard(folder: Path) -> None:
    """Send a playground's folder to the recycle bin (D235g, Q68). Only a playground in its
    original's playgrounds/ folder, and only while no app has it open; the original is not
    touched. Where the recycle bin cannot take it, nothing is deleted (A168)."""
    folder = Path(folder)
    check(folder)
    acquire_ownership(folder).release()  # refused while another app has it open
    try:
        stand_in = os.environ.get(TRASH_ENV)
        if stand_in:
            Path(stand_in).mkdir(parents=True, exist_ok=True)
            os.replace(folder, Path(stand_in) / f"{folder.name}-{new_id()}")
            return
        send2trash(str(folder.resolve()))
    except OSError as exc:
        raise PlaygroundError(
            f"The playground could not be moved to the recycle bin ({exc}); nothing was deleted"
        ) from exc
