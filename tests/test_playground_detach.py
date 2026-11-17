"""Making a playground its own investigation (D236, FR-INV-09, T-OPS-18): its folder moved out of
the original (a rename on one disk, a copy to another), its link and base dropped, the refusals
that move nothing, a merged playground opened writable, and the original's Undo merge after."""

import errno
import sqlite3
from contextlib import closing
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from chembook3d import local_jobs, playgrounds
from chembook3d import settings as app_settings
from chembook3d.app import create_app
from chembook3d.investigation import DB_NAME, acquire_ownership
from tests.conftest import WATER
from tests.test_playground_merge_undo import _merge
from tests.test_playgrounds import APP, ORIGIN, _info, _make, _original


@pytest.fixture
def page():
    """A client that reaches the app as its own page in the browser on this computer does."""
    with TestClient(create_app(), base_url=APP, client=("127.0.0.1", 50000)) as test_client:
        yield test_client


@pytest.fixture
def trashed(monkeypatch):
    """The folders sent to the recycle bin (left where they are: the test's folder goes)."""
    sent: list[Path] = []
    monkeypatch.setattr(playgrounds, "send2trash", lambda path: sent.append(Path(path)))
    return sent


def _detach(client, folder: Path, target: Path, name: str = "New study", headers=ORIGIN):
    return client.post(
        "/api/playgrounds/detach",
        json={"folder": str(folder), "target": str(target), "name": name},
        headers=headers,
    )


def _history(folder: Path) -> list[tuple]:
    with closing(sqlite3.connect(folder / DB_NAME)) as db:
        return db.execute(
            "SELECT record_type, record_id, action, new_value FROM history ORDER BY id"
        ).fetchall()


def test_a_playground_becomes_its_own_investigation(page, tmp_path):
    original = _original(page, tmp_path)
    assert _make(page, original).status_code == 200
    folder = (original / "playgrounds" / "Playground 1").resolve()
    page.post("/api/nodes", json={"label": "Only here", "xyz": WATER})
    own_id = _info(folder)["uuid"]
    before = _history(original)

    preview = page.get("/api/playgrounds/detach-preview", params={"folder": str(folder)}).json()
    assert preview["new_name"] == "RuCAAC cycle (Playground 1)"
    assert Path(preview["place"]) == original.resolve().parent and preview["merged"] is False

    target = tmp_path / "New study"
    assert _detach(page, folder, target, headers={}).status_code == 403  # not the app's page
    assert folder.is_dir()
    done = _detach(page, folder, target)
    assert done.status_code == 200, done.text
    out = done.json()
    assert Path(out["folder"]) == target.resolve()
    assert out["name"] == "New study" and out["playground"] is None and out["read_only"] is False
    assert "Playground 1 is now its own investigation" in out["notice"]

    # Moved: nothing left in the original's playgrounds/, no base, no link
    assert not folder.exists()
    assert not list((original / "playgrounds").iterdir())
    assert not (target / playgrounds.BASE_DIR).exists()
    assert not list(tmp_path.glob(f"{playgrounds.DETACHING}*"))
    info = _info(target)
    assert info["name"] == "New study" and info["uuid"] == own_id
    assert all(info[key] is None for key in info if key.startswith("playground_"))
    record_type, record_id, action, value = _history(target)[-1]
    assert (record_type, record_id, action) == ("playground", own_id, "detach")
    assert '"Playground 1"' in value and '"RuCAAC cycle"' in value

    # It is open, keeps what was done in it, and is on the recent list; the original is untouched
    assert "Only here" in {n["label"] for n in page.get("/api/nodes").json()}
    assert page.get("/api/investigation").json()["playground"] is None
    recent = app_settings.load().recent
    assert Path(recent[0]).resolve() == target.resolve() and any(
        Path(r).resolve() == original.resolve() for r in recent
    )
    assert page.get("/api/playgrounds").json() == {}
    assert _history(original) == before
    assert next((target / "files").rglob("*.log")).is_file()
    assert next((original / "files").rglob("*.log")).is_file()


def test_to_another_disk_it_is_copied_then_sent_to_the_recycle_bin(
    page, tmp_path, trashed, monkeypatch
):
    original = _original(page, tmp_path)
    assert _make(page, original).status_code == 200
    page.post("/api/investigations/close")
    folder = (original / "playgrounds" / "Playground 1").resolve()
    renamed = playgrounds._rename

    def across(source, target):
        if Path(source) == folder:
            raise OSError(errno.EXDEV, "Invalid cross-device link")
        renamed(source, target)

    monkeypatch.setattr(playgrounds, "_rename", across)

    # Too little room on the other disk: refused, nothing copied or moved
    class Full:
        free = 0

    monkeypatch.setattr(playgrounds.shutil, "disk_usage", lambda _path: Full)
    target = tmp_path / "Other disk"
    refused = _detach(page, folder, target)
    assert refused.status_code == 422 and "nothing was moved" in refused.json()["detail"]
    assert (folder / DB_NAME).is_file() and not target.exists()
    assert not list(tmp_path.glob(f"{playgrounds.DETACHING}*"))
    monkeypatch.undo()
    monkeypatch.setattr(playgrounds, "_rename", across)
    monkeypatch.setattr(playgrounds, "send2trash", lambda path: trashed.append(Path(path)))

    done = _detach(page, folder, target)
    assert done.status_code == 200, done.text
    assert trashed == [folder]
    assert "recycle bin" in done.json()["notice"]
    assert _info(target)["playground_of"] is None
    linked = next((target / "files").rglob("*.log"))
    assert linked.stat().st_nlink == 1  # a plain copy on the other disk


def test_refusals_move_nothing(page, tmp_path, monkeypatch):
    original = _original(page, tmp_path)
    assert _make(page, original).status_code == 200
    page.post("/api/investigations/close")
    folder = original / "playgrounds" / "Playground 1"
    taken = tmp_path / "Taken"
    taken.mkdir()

    for target, said in (
        (taken, "already exists"),
        (tmp_path / "missing" / "New", "does not exist"),
        (original / "Inside", "outside the original"),
        (folder / "Inside", "outside the original"),
        (Path("relative"), "full path"),
    ):
        answer = _detach(page, folder, target)
        assert answer.status_code == 422 and said in answer.json()["detail"], answer.text
    for refused in (original, tmp_path):
        answer = _detach(page, refused, tmp_path / "New")
        assert answer.status_code == 422 and "not a playground" in answer.json()["detail"]
    answer = page.post(
        "/api/playgrounds/detach",
        json={"folder": str(folder), "target": str(tmp_path / "New"), "name": "  "},
        headers=ORIGIN,
    )
    assert answer.status_code == 422 and "name" in answer.json()["detail"]

    # Open in another app
    owner = acquire_ownership(folder)
    try:
        assert _detach(page, folder, tmp_path / "New").status_code == 409
    finally:
        owner.release()

    # A local job still running in it
    monkeypatch.setattr(local_jobs, "busy", lambda _folder=None: True)
    answer = _detach(page, folder, tmp_path / "New")
    assert answer.status_code == 422 and "local job" in answer.json()["detail"]
    monkeypatch.undo()

    # A database that cannot be changed: moved back where it was
    def broken(*_args):
        raise sqlite3.OperationalError("disk I/O error")

    monkeypatch.setattr(playgrounds, "_unlink", broken)
    answer = _detach(page, folder, tmp_path / "New")
    assert answer.status_code == 422 and "could not be changed" in answer.json()["detail"]

    assert _info(folder)["playground_of"] and (folder / "playground" / "base.sqlite").is_file()
    assert not (tmp_path / "New").exists()
    assert not list(tmp_path.glob(f"{playgrounds.DETACHING}*"))
    assert page.get("/api/investigation").json() is None


def test_an_open_playground_refused_opens_again(page, tmp_path, monkeypatch):
    original = _original(page, tmp_path)
    assert _make(page, original).status_code == 200
    folder = (original / "playgrounds" / "Playground 1").resolve()

    def broken(*_args):
        raise sqlite3.OperationalError("disk I/O error")

    monkeypatch.setattr(playgrounds, "_unlink", broken)
    assert _detach(page, folder, tmp_path / "New").status_code == 422
    shown = page.get("/api/investigation").json()
    assert Path(shown["folder"]) == folder and shown["playground"]["name"] == "Playground 1"


def test_a_merged_playground_opens_writable_and_the_merge_can_still_be_undone(page, tmp_path):
    original, playground, _ids, _before, merged = _merge(page, tmp_path)
    page.post("/api/investigations/close")
    preview = page.get("/api/playgrounds/detach-preview", params={"folder": str(playground)})
    assert preview.json()["merged"] is True

    target = tmp_path / "Rest of the study"
    done = _detach(page, playground, target)
    assert done.status_code == 200, done.text
    assert done.json()["read_only"] is False
    assert page.post("/api/nodes", json={"label": "Writable", "xyz": WATER}).status_code == 201

    page.post("/api/investigations/open", json={"folder": str(original)})
    undone = page.post(f"/api/playgrounds/merge/{merged['history_id']}/undo", headers=ORIGIN)
    assert undone.status_code == 200, undone.text
    assert undone.json()["playground_back"] is False
    assert _info(target)["playground_merged_at"] is None
