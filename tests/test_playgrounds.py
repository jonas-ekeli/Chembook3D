"""Playgrounds (D235, FR-INV-04): a working copy of an investigation in its playgrounds/ folder,
with the same record ids, a frozen base and hard-linked files (T-OPS-08, T-OPS-09)."""

import os
import sqlite3
import subprocess
from contextlib import closing
from pathlib import Path

import pytest

from chembook3d import playgrounds, sync
from chembook3d import settings as app_settings
from chembook3d.investigation import BACKUP_DIR, DB_NAME, LOCK_NAME
from tests.conftest import WATER

FIXTURES = Path(__file__).parent / "fixtures" / "gaussian"
SINGLE_POINT = FIXTURES / "aminationTS-full-unfrz-c1_sp_tzpop.log"


def _import(client) -> str:
    plan = client.post(
        "/api/imports", params={"filename": SINGLE_POINT.name}, content=SINGLE_POINT.read_bytes()
    ).json()
    names = {"basis_names": {}, "dispersion_names": {}}
    for request in plan["names"]:
        kind = "basis_names" if request["kind"] == "basis" else "dispersion_names"
        names[kind][request["key"]] = "modQZ" if kind == "basis_names" else "GD3MBJ"
    done = client.post(f"/api/imports/{plan['token']}/commit", json=names)
    assert done.status_code == 200, done.text
    return done.json()["node_id"]


def _original(client, tmp_path) -> Path:
    folder = tmp_path / "RuCAAC"
    made = client.post("/api/investigations", json={"folder": str(folder), "name": "RuCAAC cycle"})
    assert made.status_code == 200, made.text
    client.post("/api/nodes", json={"label": "INT1", "xyz": WATER})
    _import(client)
    for name in ("exports", "local-jobs", "jobs", ".claude", BACKUP_DIR):
        (folder / name).mkdir(exist_ok=True)
        (folder / name / "kept.txt").write_text("the original's own", encoding="utf-8")
    assert client.post("/api/investigations/close").status_code == 200
    return folder


def _info(db_path: Path) -> dict:
    db_path = db_path / DB_NAME if db_path.is_dir() else db_path
    with closing(sqlite3.connect(db_path)) as db:
        db.row_factory = sqlite3.Row
        return dict(db.execute("SELECT * FROM investigation_info WHERE id = 1").fetchone())


def _make(client, folder: Path, name: str = "Playground 1"):
    return client.post("/api/playgrounds", json={"folder": str(folder), "name": name})


def test_a_playground_copies_the_investigation_with_its_ids(client, tmp_path):
    # T-OPS-08
    original = _original(client, tmp_path)
    client.post("/api/investigations/open", json={"folder": str(original)})
    nodes = client.get("/api/nodes").json()
    history = client.get("/api/history").json()
    client.post("/api/investigations/close")

    made = _make(client, original)
    assert made.status_code == 200, made.text
    out = made.json()
    folder = original / "playgrounds" / "Playground 1"
    assert Path(out["folder"]) == folder.resolve()
    assert out["name"] == "RuCAAC cycle (Playground 1)"
    assert out["linked"] is False
    band = out["playground"]
    assert band["name"] == "Playground 1"
    assert Path(band["original_folder"]) == original.resolve()
    assert band["original_name"] == "RuCAAC cycle" and band["original_found"]
    assert band["created_at"] and band["merged_at"] is None
    assert out["notice"] is None  # every file hard-linked (on this test's disk)

    # The same records, ids and history; the playground is the open investigation.
    assert client.get("/api/nodes").json() == nodes
    assert client.get("/api/history").json() == history
    imported = next(n for n in nodes if n["label"] != "INT1")
    calculation = client.get(f"/api/nodes/{imported['id']}/calculations").json()[0]
    copy = client.get(f"/api/source-files/{calculation['source_file']['id']}/download")
    assert copy.content == SINGLE_POINT.read_bytes()

    # Files are hard links of the original's; jobs, exports, backups and Claude's folder are not
    # copied; the base is the original's database as it was, with the original's own id.
    stored = next((original / "files").rglob("*.log"))
    twin = folder / stored.relative_to(original)
    assert twin.is_file() and os.path.samefile(stored, twin)
    for name in ("exports", "local-jobs", "jobs", ".claude", "playgrounds"):
        assert not (folder / name).exists(), name
    assert not (folder / BACKUP_DIR / "kept.txt").exists()
    base = _info(folder / "playground" / "base.sqlite")
    mine = _info(folder)
    theirs = _info(original)
    assert base["uuid"] == theirs["uuid"] and base["playground_of"] is None
    assert mine["uuid"] not in (None, theirs["uuid"])
    assert mine["playground_of"] == theirs["uuid"]
    assert mine["playground_original"] == "../.."
    assert mine["playground_history_id"] == history[0]["id"]  # newest first

    # Work in the playground leaves the original as it was.
    client.post("/api/nodes", json={"label": "Only here", "xyz": WATER})
    client.post("/api/investigations/close")
    client.post("/api/investigations/open", json={"folder": str(original)})
    assert client.get("/api/nodes").json() == nodes
    assert client.get("/api/investigation").json()["playground"] is None


def test_the_start_page_lists_playgrounds_under_their_original(client, tmp_path):
    # T-OPS-08: not in the recent list themselves; "original changed N things since"
    original = _original(client, tmp_path)
    assert _make(client, original, "Playground 1").status_code == 200
    client.post("/api/investigations/close")
    assert _make(client, original, "Second: try / other").status_code == 200
    client.post("/api/investigations/close")

    recent = client.get("/api/settings").json()["recent"]
    assert recent == [str(original.resolve())]
    listed = client.get("/api/playgrounds").json()[str(original.resolve())]
    assert [row["name"] for row in listed] == ["Playground 1", "Second: try / other"]
    assert Path(listed[1]["folder"]).name == "Second- try - other"
    assert [row["changed"] for row in listed] == [0, 0]
    assert not any(row["foreign"] for row in listed)

    client.post("/api/investigations/open", json={"folder": str(original)})
    client.post("/api/nodes", json={"label": "INT2", "xyz": WATER})
    client.post("/api/investigations/close")
    listed = client.get("/api/playgrounds").json()[str(original.resolve())]
    assert [row["changed"] for row in listed] == [1, 1]

    # Opening a playground keeps its original on the list; removing the original hides both.
    client.delete("/api/settings/recent", params={"folder": str(original.resolve())})
    assert client.get("/api/playgrounds").json() == {}
    client.post("/api/investigations/open", json={"folder": listed[0]["folder"]})
    assert client.get("/api/settings").json()["recent"] == [str(original.resolve())]


def test_playground_rules(client, tmp_path):
    original = _original(client, tmp_path)
    assert _make(client, original).status_code == 200
    # Q66: no playground of a playground
    nested = _make(client, original / "playgrounds" / "Playground 1", "Deeper")
    assert nested.status_code == 422 and "is a playground" in nested.json()["detail"]
    client.post("/api/investigations/close")
    again = _make(client, original)
    assert again.status_code == 422 and "already exists" in again.json()["detail"]
    blank = _make(client, original, "   ")
    assert blank.status_code == 422
    assert sorted(p.name for p in (original / "playgrounds").iterdir()) == ["Playground 1"]
    # Q71: never linked to GitHub
    client.post(
        "/api/investigations/open", json={"folder": str(original / "playgrounds" / "Playground 1")}
    )
    refused = client.post("/api/sync/link", json={"url": "https://github.com/a/b.git"})
    assert refused.status_code == 422 and "never synced" in refused.json()["detail"]
    # Made from the original while it is open, too (the Work on its own switch, PR 2).
    client.post("/api/investigations/open", json={"folder": str(original)})
    assert _make(client, original, "From inside").status_code == 200
    assert not (original / LOCK_NAME).exists()  # the original was closed again


def test_files_are_copied_where_the_disk_cannot_link_them(client, tmp_path, monkeypatch):
    # T-OPS-09, Q63: a disk without hard links (FAT, some network folders) gets copies
    original = _original(client, tmp_path)

    def refuse(*_args, **_kwargs):
        raise OSError("hard links are not supported here")

    monkeypatch.setattr(playgrounds.os, "link", refuse)
    made = _make(client, original)
    assert made.status_code == 200, made.text
    assert "could not be hard-linked" in made.json()["notice"]
    stored = next((original / "files").rglob("*.log"))
    twin = original / "playgrounds" / "Playground 1" / stored.relative_to(original)
    assert twin.read_bytes() == stored.read_bytes() and not os.path.samefile(stored, twin)


def test_a_failed_copy_leaves_nothing_behind(client, tmp_path, monkeypatch):
    original = _original(client, tmp_path)

    def broken(*_args, **_kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(playgrounds.shutil, "copyfile", broken)
    failed = _make(client, original)
    assert failed.status_code == 422 and "disk full" in failed.json()["detail"]
    assert list((original / "playgrounds").iterdir()) == []


def test_folder_names_open_on_windows():
    assert playgrounds.folder_name("a/b\\c:d*e?") == "a-b-c-d-e-"
    assert playgrounds.folder_name("con") == "con-"
    assert playgrounds.folder_name("  try  this. ") == "try this"
    assert playgrounds.folder_name("...") == "playground"
    with pytest.raises(playgrounds.PlaygroundError):
        playgrounds.folder_name("x" * 101)


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    ).stdout


def test_a_linked_original_keeps_its_playgrounds_out_of_git(client, tmp_path, monkeypatch):
    gitconfig = tmp_path / "gitconfig"
    gitconfig.write_text("", encoding="utf-8")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(gitconfig))
    remote = tmp_path / "remote.git"
    remote.mkdir()
    git(remote, "init", "-q", "--bare")
    git(remote, "symbolic-ref", "HEAD", "refs/heads/main")
    original = _original(client, tmp_path)
    client.post("/api/investigations/open", json={"folder": str(original)})
    assert client.post("/api/sync/link", json={"url": str(remote)}).json()["state"] == "up_to_date"
    client.post("/api/investigations/close")
    # An older link's .gitignore without the entry: the local exclusion still keeps it out.
    ignore = original / ".gitignore"
    ignore.write_text(ignore.read_text().replace("playgrounds/\n", ""), encoding="utf-8")

    made = _make(client, original)
    assert made.status_code == 200, made.text
    assert "playgrounds/" in ignore.read_text().splitlines()
    assert not sync.is_linked(original / "playgrounds" / "Playground 1")
    client.post("/api/nodes", json={"label": "Only here", "xyz": WATER})
    client.post("/api/investigations/close")
    files = git(remote, "ls-tree", "-r", "--name-only", "main").split()
    assert ".gitignore" in files and not any(f.startswith("playgrounds/") for f in files)
    assert app_settings.load().recent == [str(original.resolve())]


def test_migration_0032_gives_older_investigations_an_id(tmp_path):
    from chembook3d import investigation as inv

    folder = tmp_path / "pre-d235"
    folder.mkdir()
    engine = inv._make_engine(folder / inv.DB_NAME)
    with engine.begin() as connection:
        inv.command.upgrade(inv._alembic_config(connection), "0031")
        connection.exec_driver_sql(
            "INSERT INTO investigation_info (id, name, created_at) VALUES (1, 'Old', '2026-10-01')"
        )
    engine.dispose()
    opened = inv.open_investigation(folder)
    try:
        info = opened.info()
        assert info is not None and len(info.uuid or "") == 32 and info.playground_of is None
        assert int(info.uuid, 16) >= 0
    finally:
        opened.close()
