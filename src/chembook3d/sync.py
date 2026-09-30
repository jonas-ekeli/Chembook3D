"""Sync an investigation folder through a private Git repository (D71, FR-SYNC-*).

A linked investigation is a Git repository whose `origin` is an empty private repository
(GitHub in practice). The app runs the `git` installed on the computer, and git's own
credential helper signs in: Git Credential Manager on Windows, `gh auth login` or Git
Credential Manager on Linux and WSL. The app never asks for, sees or stores a password or
token (NFR-SEC-03). Terminal prompts are turned off, so a missing sign-in fails at once
instead of waiting for input in a terminal nobody is looking at.

The database is one binary file and is never merged. When this computer and the remote both
have changes, nothing is pulled or pushed until the user keeps one copy (FR-SYNC-06); the
other copy is saved.
"""

import os
import re
import shutil
import socket
import sqlite3
import stat
import subprocess
from collections.abc import Iterator
from contextlib import contextmanager, nullcontext
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from chembook3d.investigation import BACKUP_DIR, DB_NAME, LOCK_NAME

# The lock file and journal belong to one running app, and backups/ holds local copies
# (NFR-DATA-03), so none of them is shared. Every file is stored byte for byte: Git for
# Windows would otherwise change the line endings of copied output files (NFR-DATA-02).
IGNORED = [LOCK_NAME, f"{DB_NAME}-journal", f"{BACKUP_DIR}/"]
ATTRIBUTES = ["* -text", f"{DB_NAME} binary"]

NETWORK_TIMEOUT = 300  # seconds; the first sign-in may wait for the browser
LOCAL_TIMEOUT = 120
LARGE_FILE = 100 * 1024 * 1024  # GitHub refuses larger files (FR-SYNC-09)
SET_ASIDE = "set-aside"

NO_GIT = (
    "Git is not installed, or not on the PATH. Install Git (on Windows, Git for Windows) "
    "to sync this investigation."
)
SIGN_IN = (
    "Git could not sign in to the repository. Sign in once outside the app: on Windows, "
    "run `git fetch` in the investigation folder and sign in to GitHub in the browser window "
    "that opens; on Linux or WSL, run `gh auth login`, then `gh auth setup-git`. "
    "Then press Sync again."
)
NOT_REACHED = "Could not reach the repository"


class SyncError(Exception):
    pass


@dataclass
class Commit:
    when: str  # ISO 8601, with the offset of the computer that made it
    summary: str


@dataclass
class SyncStatus:
    """state is one of: not_linked, no_git, up_to_date, not_pushed, behind, diverged,
    unreachable, error."""

    state: str
    message: str
    remote: str | None = None
    local: Commit | None = None
    upstream: Commit | None = None


def git_available() -> bool:
    return shutil.which("git") is not None


def host() -> str:
    return socket.gethostname() or "this computer"


def _env() -> dict[str, str]:
    env = dict(os.environ)
    env["GIT_TERMINAL_PROMPT"] = "0"
    env.setdefault("GIT_SSH_COMMAND", "ssh -o BatchMode=yes")
    return env


def _explain(args: tuple[str, ...], stderr: str) -> str:
    text = stderr.lower()
    if any(
        hint in text
        for hint in (
            "authentication failed",
            "could not read username",
            "could not read password",
            "terminal prompts disabled",
            "permission denied (publickey)",
            "invalid username or password",
        )
    ):
        return SIGN_IN
    if any(
        hint in text
        for hint in (
            "could not resolve host",
            "unable to access",
            "failed to connect",
            "connection timed out",
            "network is unreachable",
            "does not appear to be a git repository",
            "repository not found",
        )
    ):
        lines = [line for line in stderr.strip().splitlines() if line.strip()]
        return f"{NOT_REACHED}: {lines[-1] if lines else stderr.strip()}"
    lines = [line for line in stderr.strip().splitlines() if line.strip()]
    return f"git {args[0]} failed: {' '.join(lines[-3:]) or 'no message'}"


def _run(
    folder: Path, args: tuple[str, ...], timeout: float, check: bool, binary: bool = False
) -> subprocess.CompletedProcess:
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=folder,
            env=_env(),
            stdin=subprocess.DEVNULL,
            capture_output=True,
            timeout=timeout,
            **({} if binary else {"text": True, "encoding": "utf-8", "errors": "replace"}),
        )
    except FileNotFoundError as exc:
        raise SyncError(NO_GIT) from exc
    except subprocess.TimeoutExpired as exc:
        raise SyncError(f"{NOT_REACHED}: git {args[0]} did not finish in time") from exc
    if check and result.returncode != 0:
        stderr = result.stderr if isinstance(result.stderr, str) else result.stderr.decode()
        raise SyncError(_explain(args, stderr))
    return result


def _git(
    folder: Path, *args: str, network: bool = False, check: bool = True, timeout: float = 0
) -> str:
    timeout = timeout or (NETWORK_TIMEOUT if network else LOCAL_TIMEOUT)
    return _run(folder, args, timeout, check).stdout


def _ok(folder: Path, *args: str) -> bool:
    return _run(folder, args, LOCAL_TIMEOUT, check=False).returncode == 0


def _valid_url(url: str) -> str:
    url = url.strip()
    if not url or url.startswith("-") or any(c.isspace() for c in url):
        raise SyncError(
            "Enter the repository's address, for example "
            "https://github.com/your-name/ru-caac-metathesis.git"
        )
    return url


def _masked(url: str) -> str:
    """An address may carry a user name or token (https://user:token@host/...)."""
    return re.sub(r"//[^/@]+@", "//", url)


def is_linked(folder: Path) -> bool:
    folder = Path(folder)
    if not (folder / ".git").exists():
        return False
    if not git_available():
        return True  # it is linked, but cannot sync here (state no_git)
    return bool(remote_url(folder))


def remote_url(folder: Path) -> str | None:
    result = _run(Path(folder), ("config", "--get", "remote.origin.url"), LOCAL_TIMEOUT, False)
    return (result.stdout.strip() or None) if result.returncode == 0 else None


def write_config_files(folder: Path) -> None:
    """Add the entries of IGNORED and ATTRIBUTES that .gitignore and .gitattributes lack."""
    for name, entries in ((".gitignore", IGNORED), (".gitattributes", ATTRIBUTES)):
        path = Path(folder) / name
        text = path.read_text(encoding="utf-8") if path.exists() else ""
        present = {line.strip() for line in text.splitlines()}
        missing = [entry for entry in entries if entry not in present]
        if missing:
            if text and not text.endswith("\n"):
                text += "\n"
            path.write_text(text + "\n".join(missing) + "\n", encoding="utf-8", newline="\n")


@contextmanager
def _quiesced(db_path: Path) -> Iterator[None]:
    """Hold SQLite's write lock, so no transaction of the running app is half-written in
    the snapshot (the rollback journal keeps the file consistent between transactions, P20)."""
    connection = sqlite3.connect(db_path, timeout=30, isolation_level=None)
    try:
        connection.execute("BEGIN IMMEDIATE")
        yield
    finally:
        try:
            connection.execute("ROLLBACK")
        finally:
            connection.close()


def _identity(folder: Path) -> list[str]:
    """Commits need a name and address; use a stand-in where git has none configured."""
    if _git(folder, "config", "user.email", check=False).strip():
        return []
    return ["-c", f"user.name=Chembook3D on {host()}", "-c", "user.email=chembook3d@localhost"]


def commit_all(folder: Path, message: str, open_db: bool = False) -> bool:
    """Commit everything that changed. With open_db, the running app's database is held
    still while it is staged. Returns whether anything was committed."""
    folder = Path(folder)
    db_path = folder / DB_NAME
    with _quiesced(db_path) if open_db and db_path.exists() else nullcontext():
        _git(folder, "add", "-A")
    if _ok(folder, "diff", "--cached", "--quiet"):
        return False
    _git(folder, *_identity(folder), "commit", "-q", "-m", message)
    return True


def _sync_message() -> str:
    return f"Sync from {host()}, {datetime.now().astimezone().isoformat(timespec='seconds')}"


def _upstream(folder: Path) -> str | None:
    """The remote branch this one follows. A branch with none yet follows origin/<branch>
    once that exists."""
    result = _run(
        folder,
        ("rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"),
        LOCAL_TIMEOUT,
        check=False,
    )
    if result.returncode == 0 and result.stdout.strip():
        return result.stdout.strip()
    branch = _git(folder, "symbolic-ref", "--short", "-q", "HEAD", check=False).strip()
    if branch and _ok(folder, "rev-parse", "--verify", "-q", f"refs/remotes/origin/{branch}"):
        _git(folder, "branch", "-q", f"--set-upstream-to=origin/{branch}")
        return f"origin/{branch}"
    return None


def _commit(folder: Path, ref: str) -> Commit | None:
    result = _run(folder, ("log", "-1", "--format=%cI%x00%s", ref), LOCAL_TIMEOUT, check=False)
    if result.returncode != 0 or "\0" not in result.stdout:
        return None
    when, summary = result.stdout.strip().split("\0", 1)
    return Commit(when=when, summary=summary)


MESSAGES = {
    "up_to_date": "Up to date with GitHub",
    "not_pushed": "Changes on this computer are not pushed yet",
    "behind": "GitHub has a newer version",
    "diverged": "This computer and GitHub both have changes",
}


def status(folder: Path, fetch: bool = True, timeout: float = NETWORK_TIMEOUT) -> SyncStatus:
    folder = Path(folder)
    if not is_linked(folder):
        return SyncStatus("not_linked", "Not linked to GitHub")
    if not git_available():
        return SyncStatus("no_git", NO_GIT)
    remote = remote_url(folder)
    shown = _masked(remote) if remote else None
    error = None
    if fetch:
        try:
            _git(folder, "fetch", "-q", "origin", network=True, timeout=timeout)
        except SyncError as exc:
            error = str(exc)
    local = _commit(folder, "HEAD")
    if error:
        state = "unreachable" if error.startswith(NOT_REACHED) else "error"
        return SyncStatus(state, error, shown, local)
    dirty = bool(_git(folder, "status", "--porcelain").strip())
    upstream = _upstream(folder)
    if upstream is None:
        ahead, behind = 1, 0  # nothing pushed yet
    else:
        counts = _git(folder, "rev-list", "--left-right", "--count", f"HEAD...{upstream}").split()
        ahead, behind = int(counts[0]), int(counts[1])
    if behind and (ahead or dirty):
        state = "diverged"
    elif behind:
        state = "behind"
    elif ahead or dirty:
        state = "not_pushed"
    else:
        state = "up_to_date"
    upstream_commit = _commit(folder, upstream) if upstream else None
    return SyncStatus(state, MESSAGES[state], shown, local, upstream_commit)


def _push_now(folder: Path, timeout: float) -> SyncStatus:
    try:
        _git(folder, "push", "-q", "-u", "origin", "HEAD", network=True, timeout=timeout)
    except SyncError as exc:
        after = status(folder, timeout=timeout)
        if after.state in ("diverged", "unreachable"):
            return after  # someone pushed in between, or the connection dropped
        after.state, after.message = "error", str(exc)
        return after
    return status(folder, fetch=False)


def pull(folder: Path, timeout: float = NETWORK_TIMEOUT) -> SyncStatus:
    """Before opening (FR-SYNC-04): commit what an earlier session left, then fast-forward
    to the remote, or push what this computer has. The investigation must be closed."""
    folder = Path(folder)
    if not is_linked(folder) or not git_available():
        return status(folder, fetch=False)
    commit_all(folder, _sync_message())
    current = status(folder, timeout=timeout)
    if current.state == "behind":
        upstream = _upstream(folder)
        assert upstream is not None
        _git(folder, "merge", "--ff-only", "-q", upstream)
        return status(folder, fetch=False)
    if current.state == "not_pushed":
        return _push_now(folder, timeout)
    return current


def push(folder: Path, open_db: bool, timeout: float = NETWORK_TIMEOUT) -> SyncStatus:
    """Commit everything and push (FR-SYNC-05). With the investigation closed, a newer
    remote is fast-forwarded to; while it is open, that waits for the next open."""
    folder = Path(folder)
    if not is_linked(folder) or not git_available():
        return status(folder, fetch=False)
    commit_all(folder, _sync_message(), open_db=open_db)
    current = status(folder, timeout=timeout)
    if current.state == "not_pushed":
        return _push_now(folder, timeout)
    if current.state == "behind":
        if open_db:
            current.message = "GitHub has a newer version: close and open the investigation"
            return current
        upstream = _upstream(folder)
        assert upstream is not None
        _git(folder, "merge", "--ff-only", "-q", upstream)
        return status(folder, fetch=False)
    return current


def link(folder: Path, url: str, name: str) -> SyncStatus:
    """FR-SYNC-02: link an open investigation to an empty repository and push it."""
    folder = Path(folder)
    if not git_available():
        raise SyncError(NO_GIT)
    url = _valid_url(url)
    if is_linked(folder):
        linked_to = _masked(remote_url(folder) or "")
        raise SyncError(f"This investigation is already linked to {linked_to}")
    if _git(folder, "ls-remote", "--heads", "--tags", url, network=True).strip():
        raise SyncError(
            "That repository is not empty. To work on an investigation that is already on "
            "GitHub, close this one and use Open from GitHub."
        )
    if not (folder / ".git").exists():
        _git(folder, "init", "-q")
        _git(folder, "symbolic-ref", "HEAD", "refs/heads/main")
    _git(folder, "config", "core.autocrlf", "false")
    write_config_files(folder)
    if remote_url(folder) is None:
        _git(folder, "remote", "add", "origin", url)
    commit_all(folder, f"Link {name} from {host()}", open_db=True)
    return _push_now(folder, NETWORK_TIMEOUT)


def _remove_tree(folder: Path) -> None:
    """Remove a failed clone. Git marks its object files read-only, which Windows will not
    delete until they are made writable."""
    if not folder.exists():
        return
    for root, _dirs, files in os.walk(folder):
        for name in files:
            os.chmod(os.path.join(root, name), stat.S_IWRITE)
    shutil.rmtree(folder, ignore_errors=True)


def clone(url: str, folder: Path) -> None:
    """FR-SYNC-03: clone a linked investigation into a new or empty folder."""
    folder = Path(folder)
    if not git_available():
        raise SyncError(NO_GIT)
    url = _valid_url(url)
    if folder.exists() and any(folder.iterdir()):
        raise SyncError(f"{folder} is not empty")
    folder.parent.mkdir(parents=True, exist_ok=True)
    try:
        _git(
            folder.parent,
            "-c",
            "core.autocrlf=false",
            "clone",
            "-q",
            "-c",
            "core.autocrlf=false",
            url,
            str(folder),
            network=True,
        )
        if not (folder / DB_NAME).is_file():
            raise SyncError("That repository does not contain a Chembook3D investigation.")
    except SyncError:
        _remove_tree(folder)
        raise
    write_config_files(folder)  # a repository linked by hand may lack some entries


def _safe(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", text).strip("-.") or "computer"


def resolve(folder: Path, keep: str, timeout: float = NETWORK_TIMEOUT) -> SyncStatus:
    """FR-SYNC-06: keep one copy when both sides changed. keep is "this" or "github". The
    investigation must be closed. The other copy is always saved."""
    folder = Path(folder)
    if keep not in ("this", "github"):
        raise SyncError(f"unknown choice {keep!r}")
    commit_all(folder, _sync_message())
    current = status(folder, timeout=timeout)
    if current.state != "diverged":
        return current
    upstream = _upstream(folder)
    assert upstream is not None
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backups = folder / BACKUP_DIR
    backups.mkdir(exist_ok=True)
    if keep == "this":
        theirs = _run(folder, ("show", f"{upstream}:{DB_NAME}"), LOCAL_TIMEOUT, True, binary=True)
        (backups / f"{DB_NAME}.github-{stamp}.bak").write_bytes(theirs.stdout)
        _git(
            folder,
            *_identity(folder),
            "merge",
            "-q",
            "-s",
            "ours",
            "--no-edit",
            "-m",
            f"Kept the copy from {host()}; the GitHub copy is kept in the history",
            upstream,
        )
        return _push_now(folder, timeout)
    branch = f"{SET_ASIDE}/{_safe(host())}-{stamp}"
    _git(folder, "branch", branch, "HEAD")
    shutil.copy2(folder / DB_NAME, backups / f"{DB_NAME}.{_safe(host())}-{stamp}.bak")
    try:
        _git(folder, "push", "-q", "origin", branch, network=True, timeout=timeout)
    except SyncError:
        pass  # the branch and the backup stay on this computer
    _git(folder, "reset", "-q", "--hard", upstream)
    return status(folder, fetch=False)
