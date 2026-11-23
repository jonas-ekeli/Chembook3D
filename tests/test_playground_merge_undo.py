"""Undoing a playground's merge (D235j, FR-INV-07): the original put back as it was before the
merge, the playground writable and mergeable again (T-OPS-16), and the refusals while later work
builds on the merge, with what was changed again since kept (T-OPS-17)."""

import json
import sqlite3
from contextlib import closing
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from chembook3d.app import create_app
from chembook3d.investigation import BACKUP_DIR, DB_NAME, acquire_ownership
from chembook3d.services.playground_merge import read_rows
from tests.test_playground_merge import APP, ORIGIN, _changes, _node, _ok, _rows, _set_up


@pytest.fixture
def page():
    """A client that reaches the app as its own page in the browser on this computer does."""
    with TestClient(create_app(), base_url=APP, client=("127.0.0.1", 50000)) as test_client:
        yield test_client


def _state(client) -> dict:
    """Every row of the open investigation but the history."""
    with client.app.state.investigation.sessions() as session:
        return read_rows(session.connection())


def _merge(page, tmp_path, *removals: str):
    """Merge every ticked change, and the removals named, of the usual playground; in it, the
    node with the imported calculation is removed too."""
    original, playground, ids = _set_up(page, tmp_path)
    _ok(page.post("/api/investigations/open", json={"folder": str(playground)}))
    _ok(page.delete(f"/api/nodes/{ids['imported']}"))
    _ok(page.post("/api/investigations/close"))
    started = page.post(
        "/api/playgrounds/merge/start", json={"folder": str(playground)}, headers=ORIGIN
    )
    assert started.status_code == 200, started.text
    changes = _changes(started.json()["review"])
    ticked = [cid for cid, c in changes.items() if c["ticked"]]
    ticked += [f"node:{ids[name]}" for name in removals]
    before = _state(page)
    done = page.post(
        "/api/playgrounds/merge", json={"folder": str(playground), "ticked": ticked}, headers=ORIGIN
    )
    assert done.status_code == 200, done.text
    return original, playground, ids, before, done.json()


def _history_undo(page, entry_id: int) -> str | None:
    return next(e["undo"] for e in page.get("/api/history").json() if e["id"] == entry_id)


def _undo_backups(folder: Path) -> list[Path]:
    return list((folder / BACKUP_DIR).glob("*.undo-merge-*.bak"))


def test_undo_puts_the_original_back(page, tmp_path):
    # T-OPS-16
    original, playground, ids, before, merged = _merge(page, tmp_path, "TS1", "imported")
    entry_id = merged["history_id"]
    source = _rows(
        original, "SELECT source_file_id FROM calculations WHERE node_id = ?", (ids["hcn_play"],)
    )[0]["source_file_id"]
    assert (original / "files" / source).is_dir()
    assert _history_undo(page, entry_id) == "merge"
    after_merge = _state(page)

    preview = page.get(f"/api/playgrounds/merge/{entry_id}/undo")
    assert preview.status_code == 200, preview.text
    shown = preview.json()
    assert shown["name"] == "Try" and shown["blockers"] == []
    assert "NEW" in shown["removed"] and "Branch “B”" in shown["removed"]
    assert "Edge INT1-P → NEW" in shown["removed"]
    imported = _rows(original, "SELECT label FROM nodes WHERE id = ?", (ids["imported"],))
    assert imported == []  # removed with its calculation by the merge
    assert any(r.startswith("Calculation: single point") for r in shown["restored"])
    assert "TS1" in shown["restored"] and "Edge INT1-P → TS1" in shown["restored"]
    put_back = {p["label"]: p["fields"] for p in shown["put_back"]}
    assert set(put_back["INT1-P"]) == {"label", "status"}
    assert put_back["Thermochemistry settings"] == ["qh_temperature", "geometry_tolerance"]  # D252
    assert put_back["Moved"] == ["position"]
    assert shown["kept"] == [] and shown["playground_back"] is True
    assert shown["backup"] is None and shown["history_id"] is None
    assert shown["counts"]["removed"] == len(shown["removed"])
    # The preview writes nothing.
    assert _state(page) == after_merge and _undo_backups(original) == []
    assert _history_undo(page, entry_id) == "merge"

    # Only the app's own page undoes a merge.
    assert page.post(f"/api/playgrounds/merge/{entry_id}/undo").status_code == 403
    done = page.post(f"/api/playgrounds/merge/{entry_id}/undo", headers=ORIGIN)
    assert done.status_code == 200, done.text
    result = done.json()
    assert result["blockers"] == [] and result["history_id"] and result["playground_back"]
    assert (original / BACKUP_DIR / result["backup"]).is_file()
    assert result["backup"].startswith("investigation.sqlite.undo-merge-")

    # Every row as it was before the merge; the history keeps the merge and adds the undo.
    assert _state(page) == before
    assert not (original / "files" / source).exists()
    assert (playground / "files" / source).is_dir()  # the playground's own copy stays
    entry = _rows(original, "SELECT * FROM history WHERE id = ?", (result["history_id"],))[0]
    assert (entry["record_type"], entry["action"], entry["source"]) == (
        "playground",
        "undo_merge",
        "manual",
    )
    assert json.loads(entry["new_value"])["entry_id"] == entry_id
    assert _rows(original, "SELECT id FROM history WHERE id = ?", (entry_id,))
    assert _rows(original, "SELECT id FROM history WHERE merge_entry_id = ?", (entry_id,))
    assert _history_undo(page, entry_id) is None
    again = page.post(f"/api/playgrounds/merge/{entry_id}/undo", headers=ORIGIN)
    assert again.status_code == 422 and "undone already" in again.json()["detail"]
    assert page.get(f"/api/playgrounds/merge/{entry_id}/undo").status_code == 422
    not_a_merge = page.get(f"/api/playgrounds/merge/{result['history_id']}/undo")
    assert not_a_merge.status_code == 422

    # The playground is not merged any more, and merges again.
    merged_at = _rows(playground, "SELECT playground_merged_at FROM investigation_info")[0]
    assert merged_at["playground_merged_at"] is None
    started = page.post(
        "/api/playgrounds/merge/start", json={"folder": str(playground)}, headers=ORIGIN
    )
    assert started.status_code == 200, started.text
    ticked = [cid for cid, c in _changes(started.json()["review"]).items() if c["ticked"]]
    remerged = page.post(
        "/api/playgrounds/merge", json={"folder": str(playground), "ticked": ticked}, headers=ORIGIN
    )
    assert remerged.status_code == 200, remerged.text
    assert "NEW" in {n["label"] for n in page.get("/api/nodes").json()}
    assert _history_undo(page, remerged.json()["history_id"]) == "merge"


def test_later_work_blocks_the_undo_and_later_changes_are_kept(page, tmp_path):
    # T-OPS-17
    original, playground, ids, _before, merged = _merge(page, tmp_path)
    entry_id = merged["history_id"]
    url = f"/api/playgrounds/merge/{entry_id}/undo"

    # A field the merge changed and that was changed again since keeps its later value.
    _ok(page.patch(f"/api/nodes/{ids['INT1']}", json={"label": "INT1-X"}))
    shown = page.get(url).json()
    assert shown["blockers"] == []
    assert {"label": "INT1-X", "field": "label"} in shown["kept"]
    assert {p["label"]: p["fields"] for p in shown["put_back"]}["INT1-X"] == ["status"]

    # An edge added since to a node the merge added blocks it.
    edge = _ok(
        page.post("/api/transitions", json={"source_id": ids["Moved"], "target_id": ids["new"]})
    )
    shown = page.get(url).json()
    assert "Edge Moved → NEW uses NEW, added by the merge" in shown["blockers"]
    refused = page.post(url, headers=ORIGIN)
    assert refused.status_code == 409 and refused.json()["detail"]["blockers"]
    assert _undo_backups(original) == []  # refused: no copy left behind
    _ok(page.delete(f"/api/transitions/{edge['id']}"))

    # A node the merge added and that was renamed since blocks its removal.
    _ok(page.patch(f"/api/nodes/{ids['new']}", json={"label": "NEW-2"}))
    shown = page.get(url).json()
    assert shown["blockers"] == ["NEW-2 was changed after the merge"]
    _ok(page.patch(f"/api/nodes/{ids['new']}", json={"label": "NEW"}))
    assert page.get(url).json()["blockers"] == []

    # The playground open in another app blocks it.
    owner = acquire_ownership(playground)
    try:
        shown = page.get(url).json()
        assert shown["blockers"] == ["Close Try in the other app first"]
        assert page.post(url, headers=ORIGIN).status_code == 409
    finally:
        owner.release()
    assert _history_undo(page, entry_id) == "merge"

    done = page.post(url, headers=ORIGIN)
    assert done.status_code == 200, done.text
    nodes = {n["id"]: n for n in page.get("/api/nodes").json()}
    assert ids["new"] not in nodes
    assert nodes[ids["INT1"]]["label"] == "INT1-X" and nodes[ids["INT1"]]["status"] != "done"


def test_an_older_merge_entry_tells_later_work_from_the_history(page, tmp_path):
    # T-OPS-17: a merge made before the entry kept what it wrote
    original, _playground, ids, _before, merged = _merge(page, tmp_path)
    entry_id = merged["history_id"]
    url = f"/api/playgrounds/merge/{entry_id}/undo"
    investigation = page.app.state.investigation
    with investigation.sessions.begin() as session:
        row = session.connection().exec_driver_sql(
            "SELECT old_value FROM history WHERE id = ?", (entry_id,)
        )
        old = json.loads(row.one()[0])
        assert "inserted_rows" in old and "updated_to" in old
        del old["inserted_rows"], old["updated_to"]
        session.connection().exec_driver_sql(
            "UPDATE history SET old_value = ? WHERE id = ?", (json.dumps(old), entry_id)
        )

    assert page.get(url).json()["blockers"] == []
    _ok(page.patch(f"/api/nodes/{ids['INT1']}", json={"label": "INT1-X"}))
    shown = page.get(url).json()
    assert shown["blockers"] == [] and {"label": "INT1-X", "field": "label"} in shown["kept"]
    _ok(page.patch(f"/api/nodes/{ids['new']}", json={"notes": "worked on since"}))
    shown = page.get(url).json()
    assert shown["blockers"] == ["NEW was changed after the merge"]


def test_a_missing_playground_does_not_stop_the_undo(page, tmp_path):
    original, playground, ids, before, merged = _merge(page, tmp_path)
    moved = playground.parent / "Elsewhere"
    playground.rename(moved)
    done = page.post(f"/api/playgrounds/merge/{merged['history_id']}/undo", headers=ORIGIN)
    assert done.status_code == 200, done.text
    assert done.json()["playground_back"] is False
    assert _state(page) == before
    with closing(sqlite3.connect(moved / DB_NAME)) as db:
        assert db.execute("SELECT playground_merged_at FROM investigation_info").fetchone()[0]
    assert _node(page, "Later", None)
