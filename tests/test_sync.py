"""Git sync of investigations (D71, T-SYNC-*). A local bare repository stands in for GitHub."""

import subprocess
from pathlib import Path

import pytest
from sqlalchemy import select

from chembook3d import investigation as inv
from chembook3d import sync
from chembook3d.models import Node


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    ).stdout


@pytest.fixture(autouse=True)
def git_config(tmp_path, monkeypatch):
    """Keep the user's own git settings out; the system ones (Git for Windows sets
    core.autocrlf=true there) stay, as on a real computer."""
    path = tmp_path / "gitconfig"
    path.write_text("", encoding="utf-8")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(path))
    return path


@pytest.fixture
def remote(tmp_path) -> str:
    """An empty repository, like a new private one on GitHub."""
    path = tmp_path / "remote.git"
    path.mkdir()
    git(path, "init", "-q", "--bare")
    git(path, "symbolic-ref", "HEAD", "refs/heads/main")
    return str(path)


def labels(folder: Path) -> list[str]:
    investigation = inv.open_investigation(folder)
    try:
        with investigation.sessions() as session:
            return sorted(session.scalars(select(Node.label)))
    finally:
        investigation.close()


def add_node(folder: Path, label: str) -> None:
    investigation = inv.open_investigation(folder)
    with investigation.sessions.begin() as session:
        session.add(Node(label=label))
    investigation.close()


def linked(tmp_path: Path, remote: str, name: str = "a") -> Path:
    folder = tmp_path / name
    investigation = inv.create_investigation(folder, "Shared")
    try:
        # Linked while open, as from the app: the lock file exists but is not shared.
        status = sync.link(folder, remote, "Shared")
    finally:
        investigation.close()
    assert status.state == "up_to_date", status.message
    return folder


def remote_files(remote: str, ref: str = "main") -> list[str]:
    return git(Path(remote), "ls-tree", "-r", "--name-only", ref).split()


def test_link_pushes_the_folder_without_lock_or_backups(tmp_path, remote):
    # T-SYNC-02, FR-SYNC-02
    folder = linked(tmp_path, remote)
    files = remote_files(remote)
    assert inv.DB_NAME in files and ".gitignore" in files and ".gitattributes" in files
    assert inv.LOCK_NAME not in files
    (folder / inv.BACKUP_DIR).mkdir()
    (folder / inv.BACKUP_DIR / "old.bak").write_bytes(b"x")
    assert sync.status(folder).state == "up_to_date"
    assert sync.is_linked(folder)


def test_link_refuses_a_repository_that_is_not_empty(tmp_path, remote):
    linked(tmp_path, remote, "a")
    investigation = inv.create_investigation(tmp_path / "b", "Other")
    try:
        with pytest.raises(sync.SyncError, match="not empty"):
            sync.link(tmp_path / "b", remote, "Other")
    finally:
        investigation.close()
    assert not sync.is_linked(tmp_path / "b")


def test_clone_change_there_and_pull_here(tmp_path, remote):
    # T-SYNC-03, FR-SYNC-03, 04, 05
    a = linked(tmp_path, remote)
    b = tmp_path / "b"
    sync.clone(remote, b)
    add_node(b, "made on b")
    assert sync.status(b).state == "not_pushed"
    assert sync.push(b, open_db=False).state == "up_to_date"

    status = sync.pull(a)
    assert status.state == "up_to_date"
    assert labels(a) == ["made on b"]


def test_push_while_open_holds_the_database_still(tmp_path, remote):
    folder = linked(tmp_path, remote)
    investigation = inv.open_investigation(folder)
    try:
        with investigation.sessions.begin() as session:
            session.add(Node(label="open"))
        status = sync.push(folder, open_db=True)
    finally:
        investigation.close()
    assert status.state == "up_to_date"
    assert "Sync from" in status.local.summary


def diverged(tmp_path: Path, remote: str) -> tuple[Path, Path]:
    a = linked(tmp_path, remote)
    b = tmp_path / "b"
    sync.clone(remote, b)
    add_node(b, "from b")
    assert sync.push(b, open_db=False).state == "up_to_date"
    add_node(a, "from a")
    return a, b


def test_both_changed_nothing_is_merged(tmp_path, remote):
    # T-SYNC-04, FR-SYNC-06
    a, _ = diverged(tmp_path, remote)
    before = git(Path(remote), "rev-parse", "main")
    assert sync.pull(a).state == "diverged"
    assert sync.push(a, open_db=False).state == "diverged"
    assert git(Path(remote), "rev-parse", "main") == before
    assert labels(a) == ["from a"]


def test_keep_the_github_copy_sets_this_one_aside(tmp_path, remote):
    a, _ = diverged(tmp_path, remote)
    status = sync.resolve(a, "github")
    assert status.state == "up_to_date"
    assert labels(a) == ["from b"]
    saved = list((a / inv.BACKUP_DIR).glob(f"{inv.DB_NAME}.*.bak"))
    assert len(saved) == 1 and b"from a" in saved[0].read_bytes()
    branches = git(Path(remote), "branch", "--list", f"{sync.SET_ASIDE}/*").split()
    assert len(branches) == 1
    assert git(Path(remote), "rev-parse", branches[0]) == git(a, "rev-parse", branches[0])


def test_keep_this_copy_keeps_the_github_one_in_the_history(tmp_path, remote):
    a, b = diverged(tmp_path, remote)
    status = sync.resolve(a, "this")
    assert status.state == "up_to_date"
    assert labels(a) == ["from a"]
    saved = list((a / inv.BACKUP_DIR).glob(f"{inv.DB_NAME}.github-*.bak"))
    assert len(saved) == 1 and b"from b" in saved[0].read_bytes()
    # The other computer simply catches up: its commit is part of the history.
    assert sync.pull(b).state == "up_to_date"
    assert labels(b) == ["from a"]


def test_copied_outputs_keep_their_bytes_on_a_windows_style_checkout(tmp_path, remote, git_config):
    # T-SYNC-05, NFR-DATA-02: Git for Windows converts line endings unless told not to.
    a = linked(tmp_path, remote)
    output = a / inv.FILES_DIR / ("0" * 32) / "job.log"
    output.parent.mkdir(parents=True)
    content = b"Entering Gaussian System\n SCF Done:  E(RB3LYP) =  -76.4\r\nmixed\n"
    output.write_bytes(content)
    assert sync.push(a, open_db=False).state == "up_to_date"

    git_config.write_text("[core]\n\tautocrlf = true\n", encoding="utf-8")
    b = tmp_path / "b"
    sync.clone(remote, b)
    assert (b / output.relative_to(a)).read_bytes() == content
    assert sync.status(b).state == "up_to_date"


def test_unreachable_remote_opens_anyway_and_pushes_later(tmp_path, remote):
    # T-SYNC-06
    a = linked(tmp_path, remote)
    moved = Path(remote).with_name("moved.git")
    Path(remote).rename(moved)
    add_node(a, "offline")
    status = sync.pull(a)
    assert status.state == "unreachable"
    assert status.message.startswith(sync.NOT_REACHED)
    assert labels(a) == ["offline"]

    moved.rename(remote)
    assert sync.pull(a).state == "up_to_date"
    assert remote_files(remote)  # the offline change reached the remote
    assert git(Path(remote), "rev-parse", "main") == git(a, "rev-parse", "HEAD")


def test_clone_of_something_else_is_refused_and_removed(tmp_path, remote):
    other = tmp_path / "other"
    other.mkdir()
    git(other, "init", "-q")
    (other / "README").write_text("hello", encoding="utf-8")
    git(other, "add", "README")
    git(other, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "x")
    with pytest.raises(sync.SyncError, match="does not contain"):
        sync.clone(str(other), tmp_path / "copy")
    assert not (tmp_path / "copy").exists()


def test_git_never_waits_for_a_password():
    # FR-SYNC-08: no prompt nobody can see; the message says how to sign in.
    assert sync._env()["GIT_TERMINAL_PROMPT"] == "0"
    message = sync._explain(("push",), "fatal: could not read Username: terminal prompts disabled")
    assert message == sync.SIGN_IN


def test_addresses_are_shown_without_credentials():
    assert (
        sync._masked("https://jonas:ghp_secret@github.com/j/r.git") == "https://github.com/j/r.git"
    )
    with pytest.raises(sync.SyncError):
        sync._valid_url("--upload-pack=touch x")


# ---------- versions (FR-SYNC-01, FR-SYNC-07) ----------


def test_a_newer_database_is_refused_and_left_untouched(tmp_path):
    # T-SYNC-01
    folder = tmp_path / "newer"
    inv.create_investigation(folder, "Newer").close()
    engine = inv._make_engine(folder / inv.DB_NAME)
    with engine.begin() as connection:
        connection.exec_driver_sql("UPDATE alembic_version SET version_num = '9999_future'")
    engine.dispose()
    before = (folder / inv.DB_NAME).read_bytes()

    with pytest.raises(inv.InvestigationTooNew, match="newer version of Chembook3D"):
        inv.open_investigation(folder)
    assert not (folder / inv.BACKUP_DIR).exists()
    assert not (folder / inv.LOCK_NAME).exists()
    assert (folder / inv.DB_NAME).read_bytes() == before


def test_open_api_explains_a_newer_database(tmp_path, client):
    folder = tmp_path / "newer"
    inv.create_investigation(folder, "Newer").close()
    engine = inv._make_engine(folder / inv.DB_NAME)
    with engine.begin() as connection:
        connection.exec_driver_sql("UPDATE alembic_version SET version_num = '9999_future'")
    engine.dispose()
    response = client.post("/api/investigations/open", json={"folder": str(folder)})
    assert response.status_code == 422
    assert "Update the app" in response.json()["detail"]


def older_linked(tmp_path: Path, remote: str) -> Path:
    """A linked investigation still at revision 0005."""
    folder = tmp_path / "older"
    folder.mkdir()
    (folder / inv.FILES_DIR).mkdir()
    engine = inv._make_engine(folder / inv.DB_NAME)
    with engine.begin() as connection:
        inv.command.upgrade(inv._alembic_config(connection), "0005")
        connection.exec_driver_sql("INSERT INTO investigation_info VALUES (1, 'Old', '2026-09-29')")
    engine.dispose()
    assert sync.link(folder, remote, "Old").state == "up_to_date"
    return folder


def test_a_linked_investigation_asks_before_upgrading(tmp_path, remote, client):
    # T-SYNC-07
    folder = older_linked(tmp_path, remote)
    response = client.post("/api/investigations/open", json={"folder": str(folder)})
    assert response.status_code == 409
    assert response.json()["detail"]["needs_upgrade"]["from"] == "0005"
    assert not (folder / inv.BACKUP_DIR).exists()

    response = client.post(
        "/api/investigations/open", json={"folder": str(folder), "upgrade": True}
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["linked"] is True and body["sync"]["state"] == "up_to_date"
    assert len(list((folder / inv.BACKUP_DIR).iterdir())) == 1

    # Closing pushes the upgraded database.
    closed = client.post("/api/investigations/close").json()
    assert closed["state"] == "up_to_date"


def test_api_link_sync_and_conflict(tmp_path, remote, client):
    a = tmp_path / "a"
    assert client.post("/api/investigations", json={"folder": str(a), "name": "A"}).is_success
    assert client.get("/api/investigation").json()["linked"] is False
    linked_status = client.post("/api/sync/link", json={"url": remote}).json()
    assert linked_status["state"] == "up_to_date"
    assert client.get("/api/investigation").json()["linked"] is True
    client.post("/api/nodes", json={"label": "from a"})
    assert client.get("/api/sync").json()["state"] == "not_pushed"
    assert client.post("/api/sync").json()["state"] == "up_to_date"

    b = tmp_path / "b"
    opened = client.post("/api/investigations/clone", json={"url": remote, "folder": str(b)})
    assert opened.status_code == 200, opened.text
    assert opened.json()["folder"] == str(b)
    client.post("/api/nodes", json={"label": "from b"})
    client.post("/api/investigations/close")
    add_node(a, "also from a")

    response = client.post("/api/investigations/open", json={"folder": str(a)})
    assert response.status_code == 409
    conflict = response.json()["detail"]["sync_conflict"]
    assert conflict["state"] == "diverged" and conflict["folder"] == str(a)
    assert "Sync from" in conflict["upstream"]["summary"]

    resolved = client.post("/api/sync/resolve", json={"folder": str(a), "keep": "github"})
    assert resolved.json()["state"] == "up_to_date"
    assert client.post("/api/investigations/open", json={"folder": str(a)}).is_success
    nodes = client.get("/api/nodes").json()
    assert sorted(n["label"] for n in nodes) == ["from a", "from b"]
