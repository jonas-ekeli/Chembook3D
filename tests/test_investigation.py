"""Investigation folders: FR-INV-*, T-OPS-01, T-OPS-03, T-OPS-04."""

import json
import shutil
import socket

import pytest
from sqlalchemy import text

from chembook3d import investigation as inv
from chembook3d.models import Calculation, Node
from tests.conftest import WATER


def test_new_investigation_is_one_folder_with_database_and_files(tmp_path):
    investigation = inv.create_investigation(tmp_path / "study", "Ru-CAAC")
    try:
        assert (tmp_path / "study" / "investigation.sqlite").is_file()
        assert (tmp_path / "study" / "files").is_dir()
        assert investigation.name == "Ru-CAAC"
    finally:
        investigation.close()
    assert not (tmp_path / "study" / inv.LOCK_NAME).exists()


def test_refuses_non_empty_folder(tmp_path):
    (tmp_path / "busy").mkdir()
    (tmp_path / "busy" / "something.txt").write_text("x")
    with pytest.raises(inv.InvestigationError, match="not empty"):
        inv.create_investigation(tmp_path / "busy", "x")


def test_moved_folder_opens_with_everything_intact(tmp_path, client):
    # T-OPS-01 / FR-INV-03: nothing refers to the original location.
    original = tmp_path / "here"
    client.post("/api/investigations", json={"folder": str(original), "name": "Portable"})
    node = client.post("/api/nodes", json={"label": "INT1", "xyz": WATER}).json()
    client.post("/api/investigations/close")

    assert str(tmp_path).encode() not in (original / "investigation.sqlite").read_bytes()
    moved = tmp_path / "elsewhere" / "copy"
    shutil.copytree(original, moved)
    shutil.rmtree(original)

    response = client.post("/api/investigations/open", json={"folder": str(moved)})
    assert response.status_code == 200
    assert response.json()["name"] == "Portable"
    reopened = client.get(f"/api/nodes/{node['id']}").json()
    assert reopened["xyz"] == node["xyz"]
    assert len(client.get("/api/history").json()) == 1


def _foreign_lock(folder, host="other-machine", pid=4242):
    (folder / inv.LOCK_NAME).write_text(
        json.dumps({"host": host, "pid": pid, "opened_at": "2026-09-29T10:00:00"})
    )


def test_locked_elsewhere_is_refused_unless_forced(tmp_path, client):
    # T-OPS-03, P22
    folder = tmp_path / "shared"
    inv.create_investigation(folder, "Shared").close()
    _foreign_lock(folder)

    response = client.post("/api/investigations/open", json={"folder": str(folder)})
    assert response.status_code == 409
    assert response.json()["detail"]["locked"]["host"] == "other-machine"

    forced = client.post("/api/investigations/open", json={"folder": str(folder), "force": True})
    assert forced.status_code == 200


def test_stale_lock_from_this_machine_is_taken_over(tmp_path):
    folder = tmp_path / "crashed"
    inv.create_investigation(folder, "Crashed").close()
    _foreign_lock(folder, host=socket.gethostname(), pid=2**22 + 12345)  # no such process
    inv.open_investigation(folder).close()


def test_schema_upgrade_backs_up_the_database_first(tmp_path, monkeypatch):
    # T-OPS-04 / NFR-DATA-03: simulate a newer app whose head revision is ahead of the file.
    folder = tmp_path / "old"
    investigation = inv.create_investigation(folder, "Old")
    with investigation.sessions.begin() as session:
        session.add(Node(label="keep me"))
    investigation.close()

    upgrades = []
    monkeypatch.setattr(inv.ScriptDirectory, "get_current_head", lambda self: "9999")
    monkeypatch.setattr(inv.command, "upgrade", lambda config, rev: upgrades.append(rev))
    inv.open_investigation(folder).close()

    assert upgrades == ["head"]
    backups = list((folder / inv.BACKUP_DIR).iterdir())
    assert len(backups) == 1 and backups[0].name.startswith("investigation.sqlite.0007.")
    assert b"keep me" in backups[0].read_bytes()


def test_phase_1_investigation_is_migrated_with_its_calculations(tmp_path):
    # T-OPS-04 on a real revision: a phase 1 file (0001) with a calculation opens in phase 2.
    folder = tmp_path / "phase1"
    folder.mkdir()
    engine = inv._make_engine(folder / inv.DB_NAME)
    with engine.begin() as connection:
        inv.command.upgrade(inv._alembic_config(connection), "0001")
        connection.exec_driver_sql("INSERT INTO investigation_info VALUES (1, 'Old', '2026-09-29')")
        connection.exec_driver_sql(
            "INSERT INTO nodes (id, seq, label, role, status, tags, notes, pos_x, pos_y,"
            " created_at, updated_at) VALUES ('n1', 1, 'kept', 'minimum', 'done', '[]', '',"
            " 0, 0, '2026-09-29', '2026-09-29')"
        )
        connection.exec_driver_sql(
            "INSERT INTO calculations (id, node_id, type, program, notes, created_at)"
            " VALUES ('c1', 'n1', 'single_point', 'Gaussian', '', '2026-09-29')"
        )
    engine.dispose()

    investigation = inv.open_investigation(folder)
    try:
        with investigation.sessions() as session:
            calculation = session.get(Calculation, "c1")
            assert calculation.node.label == "kept"
            assert calculation.termination == "unknown" and calculation.parse_warnings == []
            assert calculation.level is None and calculation.result is None
            # 0003 rebuilt the nodes table; the node keeps its calculation and gains no links.
            assert calculation.node.step_id is None and calculation.node.branch_id is None
            assert session.execute(text("PRAGMA foreign_keys")).scalar() == 1
    finally:
        investigation.close()
    assert len(list((folder / inv.BACKUP_DIR).iterdir())) == 1


def test_group_members_get_their_branch_back_on_upgrade(tmp_path):
    # T-BR-18, A22: before D66 a member lost its branch on joining a group; opening such an
    # investigation puts it back in the branch it came from. CREST members had none.
    folder = tmp_path / "pre-d66"
    folder.mkdir()
    engine = inv._make_engine(folder / inv.DB_NAME)
    with engine.begin() as connection:
        inv.command.upgrade(inv._alembic_config(connection), "0004")
        sql = connection.exec_driver_sql
        sql("INSERT INTO investigation_info VALUES (1, 'Old', '2026-09-29')")
        sql(
            "INSERT INTO branches (id, seq, name, colour, status, notes, created_at)"
            " VALUES ('b1', 1, 'A', '#2459c6', 'planned', '', '2026-09-29')"
        )
        sql(
            "INSERT INTO group_nodes (id, seq, label, notes, pos_x, pos_y, layout, created_at)"
            " VALUES ('g1', 1, 'G', '', 0, 0, 'grid', '2026-09-29')"
        )
        for node_id, origin in (("n1", "'b1'"), ("n2", "NULL")):
            sql(
                "INSERT INTO nodes (id, seq, label, role, status, tags, notes, pos_x, pos_y,"
                " created_at, updated_at, group_id, origin_branch_id) VALUES"
                f" ('{node_id}', 1, '{node_id}', 'minimum', 'done', '[]', '', 0, 0,"
                f" '2026-09-29', '2026-09-29', 'g1', {origin})"
            )
    engine.dispose()

    investigation = inv.open_investigation(folder)
    try:
        with investigation.sessions() as session:
            assert session.get(Node, "n1").branch_id == "b1"
            assert session.get(Node, "n2").branch_id is None
    finally:
        investigation.close()


def test_nodes_stay_pathway_nodes_on_upgrade_to_free_species(tmp_path):
    # D69: opening an investigation made before free species keeps every node a pathway node.
    folder = tmp_path / "pre-d69"
    folder.mkdir()
    engine = inv._make_engine(folder / inv.DB_NAME)
    with engine.begin() as connection:
        inv.command.upgrade(inv._alembic_config(connection), "0005")
        sql = connection.exec_driver_sql
        sql("INSERT INTO investigation_info VALUES (1, 'Old', '2026-09-29')")
        sql(
            "INSERT INTO nodes (id, seq, label, role, status, tags, notes, pos_x, pos_y,"
            " created_at, updated_at) VALUES ('n1', 1, 'kept', 'minimum', 'done', '[]', '',"
            " 0, 0, '2026-09-29', '2026-09-29')"
        )
    engine.dispose()

    investigation = inv.open_investigation(folder)
    try:
        with investigation.sessions() as session:
            assert session.get(Node, "n1").kind == "node"
    finally:
        investigation.close()


def test_recent_list_remembers_opened_folders(tmp_path, client):
    client.post("/api/investigations", json={"folder": str(tmp_path / "a"), "name": "A"})
    client.post("/api/investigations", json={"folder": str(tmp_path / "b"), "name": "B"})
    recent = client.get("/api/settings").json()["recent"]
    assert recent[:2] == [str((tmp_path / "b").resolve()), str((tmp_path / "a").resolve())]


def test_energy_unit_setting(client):
    # FR-SET-01, D28
    assert client.get("/api/settings").json()["energy_unit"] == "kcal/mol"
    assert (
        client.put("/api/settings", json={"energy_unit": "kJ/mol"}).json()["energy_unit"]
        == "kJ/mol"
    )
    assert client.put("/api/settings", json={"energy_unit": "furlongs"}).status_code == 422


def test_folder_browser_lists_subfolders_and_marks_investigations(tmp_path, client):
    # FR-INV-02: the page cannot read local paths itself, so the backend lists folders.
    inv.create_investigation(tmp_path / "study", "Study").close()
    (tmp_path / "plain").mkdir()
    (tmp_path / ".hidden").mkdir()
    (tmp_path / "file.txt").write_text("x")

    listing = client.get("/api/folders", params={"path": str(tmp_path)}).json()
    assert listing["path"] == str(tmp_path.resolve())
    assert listing["parent"] == str(tmp_path.resolve().parent)
    assert [(e["name"], e["is_investigation"]) for e in listing["entries"]] == [
        ("plain", False),
        ("study", True),
    ]
    assert (
        client.get("/api/folders", params={"path": str(tmp_path / "file.txt")}).status_code == 422
    )


def test_times_are_sent_as_utc(open_client):
    node = open_client.post("/api/nodes", json={}).json()
    assert node["created_at"].endswith("Z") or node["created_at"].endswith("+00:00")
    entry = open_client.get("/api/history").json()[0]
    assert entry["timestamp"].endswith("Z") or entry["timestamp"].endswith("+00:00")


def test_demo_script_builds_a_usable_investigation(tmp_path, client):
    from scripts.make_demo import make_demo

    make_demo(tmp_path / "demo")
    client.post("/api/investigations/open", json={"folder": str(tmp_path / "demo")})
    listed = client.get("/api/nodes").json()
    # Phase 1 and 2 nodes, then the phase 3 mechanism (one trunk node, two branches of three).
    assert [n["calculation_count"] for n in listed[:3]] == [0, 1, 0]
    assert len(listed) == 10
    canvas = client.get("/api/canvas").json()
    assert [b["name"] for b in canvas["branches"]] == ["T", "A", "B"]
    assert len(canvas["steps"]) == 4
