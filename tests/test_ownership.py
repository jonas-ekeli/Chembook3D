"""P22: ownership protects filesystem lifecycle work, as well as SQLite writes."""

import gc
import json
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event
from types import SimpleNamespace
from weakref import ref

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.exc import InvalidRequestError
from starlette.requests import Request

from chembook3d import investigation as inv
from chembook3d import sync
from chembook3d.api import routes


def wait_until(predicate, processes=()):
    deadline = time.monotonic() + 20
    while not predicate():
        for process in processes:
            if process.poll() is not None:
                out, err = process.communicate()
                pytest.fail(f"worker exited {process.returncode}: {out}\n{err}")
        if time.monotonic() > deadline:
            pytest.fail("ownership worker timed out")
        time.sleep(0.02)


def worker(root, identity, mode):
    return subprocess.Popen(
        [
            sys.executable,
            str(Path(__file__).with_name("ownership_worker.py")),
            str(root),
            identity,
            mode,
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def stop(process):
    if process.poll() is None:
        process.terminate()
    out, err = process.communicate(timeout=10)
    return out, err


def test_two_processes_cannot_both_reach_migration(tmp_path):
    inv.create_investigation(tmp_path / "inv", "Race").close()
    processes = [worker(tmp_path, str(index), "race") for index in range(2)]
    try:
        wait_until(lambda: len(list(tmp_path.glob("ready-*"))) == 2, processes)
        (tmp_path / "start").touch()
        wait_until(lambda: bool(list(tmp_path.glob("migrating-*"))))
        wait_until(lambda: bool(list(tmp_path.glob("result-*"))))
        assert len(list(tmp_path.glob("migrating-*"))) == 1
        assert [path.read_text() for path in tmp_path.glob("result-*")] == ["locked"]
        (tmp_path / "release").touch()
        for process in processes:
            out, err = process.communicate(timeout=20)
            assert process.returncode == 0, (out, err)
        assert sorted(path.read_text() for path in tmp_path.glob("result-*")) == [
            "locked",
            "opened",
        ]
    finally:
        for process in processes:
            stop(process)
    inv.open_investigation(tmp_path / "inv").close()


def test_live_owner_refuses_force_then_crash_is_recovered(tmp_path):
    folder = tmp_path / "inv"
    inv.create_investigation(folder, "Crash").close()
    process = worker(tmp_path, "owner", "hold")
    try:
        wait_until(lambda: (tmp_path / "ready-owner").exists(), [process])
        marker = (folder / inv.LOCK_NAME).read_bytes()
        for force in (False, True):
            with pytest.raises(inv.InvestigationLocked) as error:
                inv.open_investigation(folder, force=force)
            assert error.value.lock["can_force"] is False
            assert (folder / inv.LOCK_NAME).read_bytes() == marker
    finally:
        stop(process)
    inv.open_investigation(folder).close()


def test_force_only_overrides_unheld_foreign_metadata(tmp_path):
    folder = tmp_path / "inv"
    inv.create_investigation(folder, "Foreign").close()
    (folder / inv.LOCK_NAME).write_text(json.dumps({"host": "other-computer", "pid": 42}))
    with pytest.raises(inv.InvestigationLocked) as error:
        inv.open_investigation(folder)
    assert error.value.lock["can_force"] is True
    investigation = inv.open_investigation(folder, force=True)
    try:
        with pytest.raises(inv.InvestigationLocked) as error:
            inv.open_investigation(folder, force=True)
        assert error.value.lock["can_force"] is False
    finally:
        investigation.close()


def test_old_handle_close_cannot_release_new_owner(tmp_path):
    first = inv.create_investigation(tmp_path / "inv", "Owner")
    first.close()
    second = inv.open_investigation(first.folder)
    try:
        marker = (first.folder / inv.LOCK_NAME).read_bytes()
        first.close()
        assert (first.folder / inv.LOCK_NAME).read_bytes() == marker
        with pytest.raises(inv.InvestigationLocked):
            inv.open_investigation(first.folder, force=True)
    finally:
        second.close()


def test_abandoned_investigation_releases_handle_and_own_marker(tmp_path):
    investigation = inv.create_investigation(tmp_path / "inv", "Abandoned")
    folder = investigation.folder
    abandoned = ref(investigation.ownership)
    handle = investigation.ownership.handle
    del investigation
    gc.collect()
    assert abandoned() is None
    assert handle.closed
    assert not (folder / inv.LOCK_NAME).exists()
    inv.open_investigation(folder).close()


def test_abandoned_owner_finalizer_cannot_remove_a_different_token(tmp_path):
    investigation = inv.create_investigation(tmp_path / "inv", "Abandoned marker")
    folder = investigation.folder
    replacement = json.dumps({"owner": "different-owner", "host": "other-computer", "pid": 42})
    (folder / inv.LOCK_NAME).write_text(replacement)
    handle = investigation.ownership.handle
    del investigation
    gc.collect()
    assert handle.closed
    assert (folder / inv.LOCK_NAME).read_text() == replacement
    inv.open_investigation(folder, force=True).close()


def test_discarded_active_session_closes_before_releasing_admission(tmp_path):
    investigation = inv.create_investigation(tmp_path / "inv", "Abandoned session")
    session = investigation.sessions()
    session.get(inv.InvestigationInfo, 1).name = "Uncommitted change"
    # Hold the checked-out Connection to observe that Session GC closes it first.
    connection = session.connection()
    abandoned = ref(session)
    del session
    gc.collect()
    try:
        assert abandoned() is None
        assert connection.closed
        assert investigation.ownership._sessions == 0
        assert investigation.name == "Abandoned session"
    finally:
        investigation.close()
    inv.open_investigation(investigation.folder).close()


@pytest.mark.parametrize("stage", ["_migrate", "_remove_orphaned_copies", "_run_repairs"])
def test_failed_open_releases_ownership(tmp_path, monkeypatch, stage):
    folder = tmp_path / "inv"
    inv.create_investigation(folder, "Failure").close()

    def fail(*args, **kwargs):
        raise RuntimeError("failed lifecycle step")

    with monkeypatch.context() as patched:
        patched.setattr(inv, stage, fail)
        with pytest.raises(RuntimeError, match="failed lifecycle step"):
            inv.open_investigation(folder)
    assert not (folder / inv.LOCK_NAME).exists()
    inv.open_investigation(folder).close()


def request_for(investigation=None):
    app = SimpleNamespace(
        state=SimpleNamespace(
            investigation=investigation,
            staging=SimpleNamespace(clear=lambda: None),
        )
    )
    return Request({"type": "http", "app": app, "headers": [], "query_string": b""})


@pytest.mark.parametrize("operation", ["resolve", "pull", "push"])
def test_foreign_marker_refuses_sync_before_any_git_or_database_write(
    tmp_path, monkeypatch, operation
):
    folder = tmp_path / "inv"
    inv.create_investigation(folder, "Foreign").close()
    marker = json.dumps({"host": "another-computer", "pid": 42})
    (folder / inv.LOCK_NAME).write_text(marker)
    before = (folder / inv.DB_NAME).read_bytes()
    monkeypatch.setattr(sync, "commit_all", lambda *a, **k: pytest.fail("must not stage"))
    with pytest.raises(inv.InvestigationLocked):
        if operation == "resolve":
            sync.resolve(folder, "github")
        elif operation == "pull":
            sync.pull(folder)
        else:
            sync.push(folder, open_db=False)
    assert (folder / inv.DB_NAME).read_bytes() == before
    assert (folder / inv.LOCK_NAME).read_text() == marker
    with pytest.raises(HTTPException) as error:
        routes.sync_resolve(routes.ResolveIn(folder=str(folder), keep="github"), request_for())
    assert error.value.status_code == 409
    assert (folder / inv.DB_NAME).read_bytes() == before


def test_resolve_keeps_lease_from_close_through_reset_then_reopen(tmp_path, monkeypatch):
    investigation = inv.create_investigation(tmp_path / "inv", "Resolve")
    request = request_for(investigation)
    called = []

    def resolve(folder, keep, timeout):
        # The real public service wrapper has checked the retained, closed database lease.
        assert not investigation.ownership.database_open
        assert request.app.state.investigation is None
        with pytest.raises(inv.InvestigationLocked):
            inv.open_investigation(folder, force=True)
        called.append(keep)
        return sync.SyncStatus("up_to_date", "resolved")

    monkeypatch.setattr(sync, "_resolve", resolve)
    response = routes.sync_resolve(
        routes.ResolveIn(folder=str(investigation.folder), keep="github"), request
    )
    assert response.state == "up_to_date" and called == ["github"]
    assert not (investigation.folder / inv.LOCK_NAME).exists()
    inv.open_investigation(investigation.folder).close()


def test_close_and_push_retains_ownership(tmp_path, monkeypatch):
    investigation = inv.create_investigation(tmp_path / "inv", "Close")
    monkeypatch.setattr(routes, "_remember", lambda *a, **k: None)
    monkeypatch.setattr(sync, "is_linked", lambda folder: True)

    def push(folder, open_db, timeout):
        assert not open_db
        with pytest.raises(inv.InvestigationLocked):
            inv.open_investigation(folder, force=True)
        return sync.SyncStatus("up_to_date", "pushed")

    monkeypatch.setattr(sync, "_push", push)
    assert routes.close_and_push(investigation).state == "up_to_date"
    inv.open_investigation(investigation.folder).close()


def test_close_waits_for_borrowed_sync_before_releasing_owner(tmp_path, monkeypatch):
    investigation = inv.create_investigation(tmp_path / "inv", "Concurrent sync")
    open_sync, release_open = Event(), Event()
    close_started, closed_sync, release_close = Event(), Event(), Event()
    monkeypatch.setattr(routes, "_remember", lambda *a, **k: None)
    monkeypatch.setattr(sync, "is_linked", lambda folder: True)

    def push(folder, open_db, timeout):
        if open_db:
            open_sync.set()
            assert release_open.wait(10)
        else:
            closed_sync.set()
            assert release_close.wait(10)
        return sync.SyncStatus("up_to_date", "pushed")

    def close():
        close_started.set()
        return routes.close_and_push(investigation)

    def contender(identity):
        process = worker(tmp_path, identity, "attempt")
        out, err = process.communicate(timeout=20)
        assert process.returncode == 0, (out, err)
        assert (tmp_path / f"result-{identity}").read_text() == "locked"

    monkeypatch.setattr(sync, "_push", push)
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(sync.push, investigation.folder, True)
            assert open_sync.wait(5)
            second = pool.submit(close)
            try:
                assert close_started.wait(5)
                assert not closed_sync.wait(0.1)
                assert investigation.ownership.database_open
                contender("during-open-sync")
                release_open.set()
                assert first.result(timeout=5).state == "up_to_date"
                assert closed_sync.wait(5)
                assert not investigation.ownership.database_open
                contender("during-close-sync")
            finally:
                release_open.set()
                release_close.set()
            assert second.result(timeout=5).state == "up_to_date"
    finally:
        investigation.close()
    inv.open_investigation(investigation.folder).close()


def test_close_drains_active_sessions_and_blocks_stale_factories(tmp_path, monkeypatch):
    investigation = inv.create_investigation(tmp_path / "inv", "Active request")
    session = investigation.sessions()
    assert session.get(inv.InvestigationInfo, 1).name == "Active request"
    monkeypatch.setattr(routes, "_remember", lambda *a, **k: None)
    monkeypatch.setattr(sync, "is_linked", lambda folder: False)
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            closing = pool.submit(routes.close_and_push, investigation)
            try:
                wait_until(lambda: not investigation.ownership._accept_sessions)
                assert not closing.done()
                with pytest.raises(inv.InvestigationError, match="closing or closed"):
                    investigation.sessions()
                process = worker(tmp_path, "during-session-drain", "attempt")
                out, err = process.communicate(timeout=20)
                assert process.returncode == 0, (out, err)
                assert (tmp_path / "result-during-session-drain").read_text() == "locked"
                # An admitted request finishes on the old database before its owner closes.
                session.get(inv.InvestigationInfo, 1).name = "Finished admitted request"
                session.commit()
            finally:
                session.close()
            assert closing.result(timeout=5) is None
        with pytest.raises(InvalidRequestError, match="permanently closed"):
            session.execute(select(inv.InvestigationInfo))
        with pytest.raises(inv.InvestigationError, match="closing or closed"):
            investigation.sessions()
    finally:
        session.close()
        investigation.close()
    reopened = inv.open_investigation(investigation.folder)
    try:
        assert reopened.name == "Finished admitted request"
    finally:
        reopened.close()


def test_request_session_can_finish_on_a_different_worker_thread(tmp_path):
    investigation = inv.create_investigation(tmp_path / "inv", "Worker handoff")
    dependency = routes.session_dep(request_for(investigation))
    session = next(dependency)
    session.get(inv.InvestigationInfo, 1)
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            pool.submit(dependency.close).result(timeout=5)
        assert investigation.ownership._sessions == 0
    finally:
        dependency.close()
        investigation.close()


def test_reopen_uses_new_owner_so_old_session_factories_stay_closed(tmp_path):
    first = inv.create_investigation(tmp_path / "inv", "Reopen")
    first.close(release_ownership=False)
    with pytest.raises(inv.InvestigationError, match="new ownership"):
        inv.open_investigation(first.folder, ownership=first.ownership)
    second = inv.open_investigation(first.folder)
    try:
        assert second.name == "Reopen"
        with pytest.raises(inv.InvestigationError, match="closing or closed"):
            first.sessions()
    finally:
        first.close()
        second.close()


def test_destructive_sync_cannot_borrow_a_live_database_lease(tmp_path):
    investigation = inv.create_investigation(tmp_path / "inv", "Live")
    try:
        with pytest.raises(inv.InvestigationError, match="close the investigation"):
            sync.resolve(investigation.folder, "github", ownership=investigation.ownership)
    finally:
        investigation.close()


def divergent_repository(tmp_path, monkeypatch):
    """Real local Git history; only remote fetch/push need a stand-in."""
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(tmp_path / "missing-gitconfig"))
    folder = tmp_path / "inv"
    inv.create_investigation(folder, "Local").close()
    sync._git(folder, "init", "-q")
    sync._git(folder, "config", "user.name", "Test")
    sync._git(folder, "config", "user.email", "test@example.invalid")
    sync._git(folder, "config", "core.autocrlf", "false")
    sync.write_config_files(folder)
    # Reproduce a notebook linked before the owner sidecar existed.
    ignored = folder / ".gitignore"
    ignored.write_text(ignored.read_text().replace(f"{inv.OWNER_NAME}\n", ""))
    # Exclude the sidecar while preparing history, as the new public services do.
    (folder / ".git" / "info" / "exclude").write_text(f"{inv.OWNER_NAME}\n")
    sync.commit_all(folder, "base")
    base = sync._git(folder, "rev-parse", "HEAD").strip()
    other = tmp_path / "remote-investigation"
    inv.create_investigation(other, "Remote").close()
    remote_bytes = (other / inv.DB_NAME).read_bytes()
    (folder / inv.DB_NAME).write_bytes(remote_bytes)
    sync.commit_all(folder, "remote changes")
    sync._git(folder, "branch", "remote-copy")
    sync._git(folder, "reset", "-q", "--hard", base)
    investigation = inv.open_investigation(folder)
    with investigation.sessions.begin() as session:
        session.get(inv.InvestigationInfo, 1).name = "Local changed"
    investigation.close()
    sync.commit_all(folder, "local changes")
    (folder / ".git" / "info" / "exclude").write_text("")
    monkeypatch.setattr(sync, "_upstream", lambda folder: "remote-copy")

    def status(folder, **kwargs):
        state = (
            "up_to_date"
            if sync._git(folder, "rev-parse", "HEAD").strip()
            == sync._git(folder, "rev-parse", "remote-copy").strip()
            else "diverged"
        )
        return sync.SyncStatus(state, state)

    monkeypatch.setattr(sync, "status", status)
    return folder, remote_bytes


def test_real_resolution_reset_holds_owner_and_preserves_backup(tmp_path, monkeypatch):
    folder, remote_bytes = divergent_repository(tmp_path, monkeypatch)
    local_bytes = (folder / inv.DB_NAME).read_bytes()
    investigation = inv.open_investigation(folder)
    active = investigation.sessions()
    active.get(inv.InvestigationInfo, 1)
    request = request_for(investigation)
    git = sync._git
    resets = []

    def held_git(folder, *args, **kwargs):
        if args[0] == "push":
            raise sync.SyncError("offline test")
        if args[0] == "reset":
            # Try opening from a real second process while the real reset is pending.
            process = worker(tmp_path, "contender", "attempt")
            out, err = process.communicate(timeout=20)
            assert process.returncode == 0, (out, err)
            assert (tmp_path / "result-contender").read_text() == "locked"
            resets.append(args)
        return git(folder, *args, **kwargs)

    monkeypatch.setattr(sync, "_git", held_git)
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            resolving = pool.submit(
                routes.sync_resolve,
                routes.ResolveIn(folder=str(folder), keep="github"),
                request,
            )
            try:
                wait_until(lambda: not investigation.ownership._accept_sessions)
                assert not resolving.done() and not resets
                assert (folder / inv.DB_NAME).read_bytes() == local_bytes
            finally:
                active.close()
            result = resolving.result(timeout=20)
        assert result.state == "up_to_date" and len(resets) == 1
        assert (folder / inv.DB_NAME).read_bytes() == remote_bytes
        assert [path.read_bytes() for path in (folder / inv.BACKUP_DIR).iterdir()] == [local_bytes]
        assert inv.OWNER_NAME not in git(folder, "ls-files").splitlines()
        assert not git(folder, "status", "--porcelain").strip()
    finally:
        active.close()
        investigation.close()
    inv.open_investigation(folder).close()


def test_real_resolution_foreign_lock_cannot_reset_or_stage(tmp_path, monkeypatch):
    folder, _remote_bytes = divergent_repository(tmp_path, monkeypatch)
    before = (folder / inv.DB_NAME).read_bytes()
    head = sync._git(folder, "rev-parse", "HEAD")
    marker = json.dumps({"host": "another-computer", "pid": 42})
    (folder / inv.LOCK_NAME).write_text(marker)
    with pytest.raises(HTTPException) as error:
        routes.sync_resolve(routes.ResolveIn(folder=str(folder), keep="github"), request_for())
    assert error.value.status_code == 409
    assert (folder / inv.DB_NAME).read_bytes() == before
    assert sync._git(folder, "rev-parse", "HEAD") == head
    assert not (folder / inv.BACKUP_DIR).exists()
    assert (folder / inv.LOCK_NAME).read_text() == marker
