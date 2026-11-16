"""Merging a playground into its original (D235h, FR-INV-06): the review of what the playground
added, changed and removed against its base and the original as it is, the preview in a
rolled-back session, the merge itself with its backup and history, and the merged playground
opening read-only (Q64): T-OPS-13 to T-OPS-15."""

import json
import os
import sqlite3
from contextlib import closing
from pathlib import Path
from urllib.parse import quote

import pytest
from fastapi.testclient import TestClient

from chembook3d.app import create_app
from chembook3d.investigation import BACKUP_DIR, DB_NAME, acquire_ownership
from tests.conftest import WATER

APP = "http://127.0.0.1:8765"
ORIGIN = {"origin": APP}  # the app's own page
CLAUDE = {"x-chembook-client": "claude"}
FIXTURES = Path(__file__).parent / "fixtures" / "gaussian"
SINGLE_POINT = FIXTURES / "aminationTS-full-unfrz-c1_sp_tzpop.log"
HCN = FIXTURES / "HCN_singlet.out"


@pytest.fixture
def page():
    """A client that reaches the app as its own page in the browser on this computer does."""
    with TestClient(create_app(), base_url=APP, client=("127.0.0.1", 50000)) as test_client:
        yield test_client


def _import(client, path: Path) -> str:
    plan = client.post(
        "/api/imports", params={"filename": path.name}, content=path.read_bytes()
    ).json()
    names = {"basis_names": {}, "dispersion_names": {}}
    for request in plan["names"]:
        kind = "basis_names" if request["kind"] == "basis" else "dispersion_names"
        names[kind][request["key"]] = "modQZ" if kind == "basis_names" else "GD3MBJ"
    done = client.post(f"/api/imports/{plan['token']}/commit", json=names)
    assert done.status_code == 200, done.text
    return done.json()["node_id"]


def _node(client, label: str, headers=None) -> str:
    made = client.post("/api/nodes", json={"label": label, "xyz": WATER}, headers=headers or {})
    assert made.status_code in (200, 201), made.text
    return made.json()["id"]


def _ok(response):
    assert response.status_code < 300, response.text
    return response.json() if response.content else None


def _original(client, tmp_path) -> tuple[Path, dict[str, str]]:
    """An original with nodes, an imported calculation, a branch, an edge, a note, a
    selectivity and an energy profile, closed again."""
    folder = tmp_path / "RuCAAC"
    _ok(client.post("/api/investigations", json={"folder": str(folder), "name": "RuCAAC cycle"}))
    ids = {label: _node(client, label) for label in ("INT1", "INT2", "TS1", "R", "Moved")}
    ids["imported"] = _import(client, SINGLE_POINT)
    ids["branch"] = _ok(client.post("/api/branches", json={"name": "A"}))["id"]
    ids["edge"] = _ok(
        client.post("/api/transitions", json={"source_id": ids["INT1"], "target_id": ids["TS1"]})
    )["id"]
    ids["note"] = _ok(
        client.post(
            f"/api/nodes/{ids['INT1']}/notes", json={"title": "Rotamer", "body": "<p>x</p>"}
        )
    )["id"]
    outcomes = [{"name": "R", "members": [ids["TS1"]]}, {"name": "S", "members": [ids["INT2"]]}]
    ids["selectivity"] = _ok(
        client.post("/api/selectivities", json={"name": "Facial", "outcomes": outcomes})
    )["id"]
    ids["profile"] = _ok(client.post("/api/energy-profiles", json={}))["id"]
    _ok(client.put("/api/positions", json={"positions": {ids["INT1"]: {"x": 10, "y": 10}}}))
    _ok(client.post("/api/investigations/close"))
    return folder, ids


def _rows(folder: Path, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
    with closing(sqlite3.connect(folder / DB_NAME)) as db:
        db.row_factory = sqlite3.Row
        return db.execute(sql, params).fetchall()


def _changes(review: dict) -> dict[str, dict]:
    return {c["id"]: c for s in review["sections"] for c in s["changes"]}


def _set_up(page, tmp_path):
    """Make a playground, then change things in it and in the original after it was made."""
    original, ids = _original(page, tmp_path)
    made = page.post("/api/playgrounds", json={"folder": str(original), "name": "Try"})
    assert made.status_code == 200, made.text
    playground = Path(made.json()["folder"])

    # In the playground: Claude adds a node, an edge to it and an imported calculation.
    ids["new"] = _node(page, "NEW", CLAUDE)
    # on a new branch: the node is written before its branch, so the foreign keys are checked
    # at the end of the merge, not at each write
    ids["new_branch"] = _ok(page.post("/api/branches", json={"name": "B"}))["id"]
    _ok(page.patch(f"/api/nodes/{ids['new']}", json={"branch_id": ids["new_branch"]}))
    ids["new_edge"] = _ok(
        page.post("/api/transitions", json={"source_id": ids["INT1"], "target_id": ids["new"]})
    )["id"]
    ids["hcn_play"] = _import(page, HCN)
    _ok(page.patch(f"/api/nodes/{ids['INT1']}", json={"label": "INT1-P", "status": "done"}))
    _ok(page.patch(f"/api/nodes/{ids['INT2']}", json={"label": "INT2-P"}))
    _ok(page.delete(f"/api/nodes/{ids['TS1']}"))
    _ok(page.delete(f"/api/notes/{ids['note']}"))
    _ok(page.delete(f"/api/nodes/{ids['R']}"))
    _ok(page.patch(f"/api/energy-profiles/{ids['profile']}", json={"name": "Cycle"}))
    _ok(page.put("/api/thermochemistry", json={"qh_temperature": 350.0}))
    _ok(page.put("/api/positions", json={"positions": {ids["Moved"]: {"x": 500, "y": 600}}}))
    _ok(page.post("/api/investigations/close"))

    # In the original: another field of INT1, the same field of INT2, a note added to R (which
    # the playground removed), and the same file imported (its level made again, another id).
    _ok(page.post("/api/investigations/open", json={"folder": str(original)}))
    _ok(page.patch(f"/api/nodes/{ids['INT1']}", json={"notes": "kept from the original"}))
    _ok(page.patch(f"/api/nodes/{ids['INT2']}", json={"label": "INT2-O"}))
    _ok(page.post(f"/api/nodes/{ids['R']}/notes", json={"title": "Mine"}))
    ids["hcn_orig"] = _import(page, HCN)
    _ok(page.post("/api/investigations/close"))
    return original, playground, ids


def test_the_review_classifies_every_change(page, tmp_path):
    # T-OPS-13
    original, playground, ids = _set_up(page, tmp_path)
    body = {"folder": str(playground)}
    assert page.post("/api/playgrounds/merge/start", json=body).status_code == 403  # Claude
    started = page.post("/api/playgrounds/merge/start", json=body, headers=ORIGIN)
    assert started.status_code == 200, started.text
    out = started.json()
    assert Path(out["folder"]) == original.resolve() and out["playground"] is None
    review = out["review"]
    assert review["playground"]["name"] == "Try" and review["original_name"] == "RuCAAC cycle"
    assert review["changed_since"] >= 4
    changes = _changes(review)

    def kind(change_id):
        return changes[change_id]["kind"], changes[change_id]["ticked"]

    assert kind(f"node:{ids['new']}") == ("added", True)
    assert kind(f"transition:{ids['new_edge']}") == ("added", True)
    assert changes[f"transition:{ids['new_edge']}"]["requires"] == [f"node:{ids['new']}"]
    assert changes[f"transition:{ids['new_edge']}"]["label"] == "Edge INT1-P → NEW"
    assert kind(f"node:{ids['INT1']}") == ("changed", True)
    int1 = changes[f"node:{ids['INT1']}"]
    assert {f["field"] for f in int1["fields"]} == {"label", "status"}
    assert "label: INT1 → INT1-P" in int1["summary"]
    assert kind(f"node:{ids['INT2']}") == ("conflict", False)
    assert changes[f"node:{ids['INT2']}"]["fields"][0]["original"] == "INT2-O"
    # Removals are not ticked (Q65); one the original added to is blocked.
    assert kind(f"node:{ids['TS1']}") == ("removed", False)
    assert kind(f"note:{ids['note']}") == ("removed", False)
    assert kind(f"node:{ids['R']}") == ("removed", False)
    assert "Note “Mine”" in changes[f"node:{ids['R']}"]["blocked"]
    # TS1's removal takes its edge with it; what it did to the selectivity holding it comes
    # with the removal and is not a change of its own.
    assert f"transition:{ids['edge']}" in changes[f"node:{ids['TS1']}"]["requires"]
    assert f"selectivity:{ids['selectivity']}" not in changes
    assert kind(f"energy_profile:{ids['profile']}") == ("changed", True)
    assert kind("thermochemistry:investigation") == ("changed", True)
    assert kind("positions:main") == ("changed", True)
    assert changes["positions:main"]["label"] == "Positions of 1 card"
    # Grouped: the nodes' sections first, then Edges … Canvas.
    keys = [s["key"] for s in review["sections"]]
    assert keys[: keys.index("edges")] == [k for k in keys if k.startswith("node:")]
    assert keys[-1] == "canvas" and "settings" in keys and "analyses" in keys
    int1_section = next(s for s in review["sections"] if s["key"] == f"node:{ids['INT1']}")
    assert int1_section["title"] == "Node INT1-P"

    again = page.get("/api/playgrounds/merge/review", params={"folder": str(playground)})
    assert _changes(again.json()).keys() == changes.keys()


def test_preview_changes_nothing_and_merge_applies_only_the_ticked(page, tmp_path):
    # T-OPS-14
    original, playground, ids = _set_up(page, tmp_path)
    started = page.post(
        "/api/playgrounds/merge/start", json={"folder": str(playground)}, headers=ORIGIN
    ).json()
    changes = _changes(started["review"])
    nodes_before = page.get("/api/nodes").json()
    history_before = page.get("/api/history").json()

    # A ticked edge brings the node it needs.
    edge = f"transition:{ids['new_edge']}"
    preview = page.post(
        "/api/playgrounds/merge/preview",
        json={"folder": str(playground), "ticked": [edge]},
        headers=ORIGIN,
    )
    assert preview.status_code == 200, preview.text
    assert preview.json()["brought"] == [f"node:{ids['new']}", f"branch:{ids['new_branch']}"]
    assert preview.json()["counts"] == {"added": 3, "changed": 0, "removed": 0}
    assert preview.json()["blockers"] == [] and preview.json()["backup"] is None
    blocked = page.post(
        "/api/playgrounds/merge/preview",
        json={"folder": str(playground), "ticked": [f"node:{ids['R']}"]},
        headers=ORIGIN,
    ).json()
    assert blocked["blockers"] and "Mine" in blocked["blockers"][0]
    removal = page.post(
        "/api/playgrounds/merge/preview",
        json={"folder": str(playground), "ticked": [f"node:{ids['TS1']}"]},
        headers=ORIGIN,
    ).json()
    assert removal["brought"] == [f"transition:{ids['edge']}"] and removal["blockers"] == []
    assert removal["counts"] == {"added": 0, "changed": 0, "removed": 2}
    assert page.get("/api/nodes").json() == nodes_before
    assert page.get("/api/history").json() == history_before
    assert not (original / BACKUP_DIR).exists() or not list(
        (original / BACKUP_DIR).glob("*.merge-*.bak")
    )

    # A blocked merge is refused and writes nothing.
    refused = page.post(
        "/api/playgrounds/merge",
        json={"folder": str(playground), "ticked": [f"node:{ids['R']}"]},
        headers=ORIGIN,
    )
    assert refused.status_code == 409 and refused.json()["detail"]["blockers"]
    assert page.get("/api/nodes").json() == nodes_before

    ticked = [cid for cid, c in changes.items() if c["ticked"]]
    done = page.post(
        "/api/playgrounds/merge", json={"folder": str(playground), "ticked": ticked}, headers=ORIGIN
    )
    assert done.status_code == 200, done.text
    result = done.json()
    assert result["blockers"] == [] and result["history_id"]
    assert (original / BACKUP_DIR / result["backup"]).is_file()
    assert result["backup"].startswith("investigation.sqlite.merge-")

    nodes = {n["id"]: n for n in page.get("/api/nodes").json()}
    # Only the playground's own changes, and the original's own changes stay.
    assert nodes[ids["INT1"]]["label"] == "INT1-P" and nodes[ids["INT1"]]["status"] == "done"
    assert nodes[ids["INT1"]]["notes"] == "kept from the original"
    assert nodes[ids["INT2"]]["label"] == "INT2-O"  # an unticked conflict
    assert ids["TS1"] in nodes and ids["R"] in nodes  # removals were not ticked
    assert nodes[ids["new"]]["label"] == "NEW"
    assert nodes[ids["new"]]["branch_id"] == ids["new_branch"]
    assert ids["hcn_play"] in nodes and ids["hcn_orig"] in nodes
    seq = {
        row["id"]: row["seq"]
        for row in _rows(
            original, "SELECT id, seq FROM nodes WHERE id IN (?, ?)", (ids["new"], ids["hcn_orig"])
        )
    }
    assert seq[ids["new"]] > seq[ids["hcn_orig"]]  # numbered after the original's own

    # The same level made on both sides is the original's one; the file's folder is linked.
    calcs = {
        row["node_id"]: row
        for row in _rows(
            original,
            "SELECT node_id, level_id, source_file_id FROM calculations WHERE node_id IN (?, ?)",
            (ids["hcn_play"], ids["hcn_orig"]),
        )
    }
    assert calcs[ids["hcn_play"]]["level_id"] == calcs[ids["hcn_orig"]]["level_id"]
    assert len(_rows(original, "SELECT id FROM levels_of_theory")) == len(
        {r["id"] for r in _rows(original, "SELECT id FROM levels_of_theory")}
    )
    source = calcs[ids["hcn_play"]]["source_file_id"]
    stored = next((original / "files" / source).iterdir())
    assert os.path.samefile(stored, playground / "files" / source / stored.name)
    profile = _rows(original, "SELECT name FROM energy_profiles WHERE id = ?", (ids["profile"],))
    assert profile[0]["name"] == "Cycle"
    info = _rows(original, "SELECT qh_temperature FROM investigation_info")[0]
    assert info["qh_temperature"] == 350.0
    moved = _rows(original, "SELECT pos_x, pos_y FROM nodes WHERE id = ?", (ids["Moved"],))[0]
    assert (moved["pos_x"], moved["pos_y"]) == (500, 600)

    # The merge's entry, then the playground's entries of what was merged, keeping their source.
    entry = _rows(original, "SELECT * FROM history WHERE id = ?", (result["history_id"],))[0]
    assert (entry["record_type"], entry["action"], entry["source"]) == (
        "playground",
        "merge",
        "manual",
    )
    copied = _rows(
        original,
        "SELECT * FROM history WHERE merge_entry_id = ? ORDER BY id",
        (result["history_id"],),
    )
    by_record = {(r["record_id"], r["action"], r["field"]): r for r in copied}
    assert by_record[(ids["new"], "create", None)]["source"] == "claude"
    assert (ids["INT1"], "update", "label") in by_record
    assert (ids["INT2"], "update", "label") not in by_record  # not merged
    assert all(r["id"] > result["history_id"] for r in copied)
    new_value = json.loads(entry["new_value"])
    assert new_value["name"] == "Try" and new_value["folder"] == "playgrounds/Try"
    assert new_value["counts"]["added"] >= 3
    old_value = json.loads(entry["old_value"])
    assert [ids["new"]] in old_value["inserted"]["nodes"]
    assert source in old_value["linked_files"]

    # The playground is merged: read-only, and not merged twice.
    again = page.post(
        "/api/playgrounds/merge/start", json={"folder": str(playground)}, headers=ORIGIN
    )
    assert again.status_code == 422 and "merged already" in again.json()["detail"]
    opened = _ok(page.post("/api/investigations/open", json={"folder": str(playground)}))
    assert opened["read_only"] and opened["playground"]["merged_at"]
    refused = page.post("/api/nodes", json={"label": "More", "xyz": WATER})
    assert refused.status_code == 409 and "is read-only" in refused.json()["detail"]
    assert "was merged into RuCAAC cycle" in refused.json()["detail"]
    assert page.get("/api/nodes").status_code == 200
    assert page.put("/api/view-state", json={"a": 1}).status_code == 204
    alone = page.put(
        "/api/claude/autonomy", json={"on": True, "job_limit": 3600, "cloud": False}, headers=ORIGIN
    )
    assert alone.status_code == 409
    # Behind the app's refusal, the database itself takes no writes.
    investigation = page.app.state.investigation
    with pytest.raises(Exception, match="readonly|read-only|query_only|attempt to write"):
        with investigation.sessions.begin() as session:
            session.connection().exec_driver_sql("UPDATE nodes SET label = 'x'")


def test_merge_is_refused_while_another_app_has_the_playground_open(page, tmp_path):
    # T-OPS-15
    original, ids = _original(page, tmp_path)
    made = _ok(page.post("/api/playgrounds", json={"folder": str(original), "name": "Try"}))
    playground = Path(made["folder"])
    _ok(page.post("/api/investigations/close"))
    owner = acquire_ownership(playground)
    try:
        answer = page.post(
            "/api/playgrounds/merge/start", json={"folder": str(playground)}, headers=ORIGIN
        )
        assert answer.status_code == 409 and "locked" in answer.json()["detail"]
        assert page.get("/api/investigation").json() is None  # nothing was opened
    finally:
        owner.release()
    # Not a playground, or a playground merged into another investigation's folder
    answer = page.post(
        "/api/playgrounds/merge/start", json={"folder": str(original)}, headers=ORIGIN
    )
    assert answer.status_code == 422


def test_a_merge_from_the_playground_itself_closes_it_first(page, tmp_path):
    original, ids = _original(page, tmp_path)
    made = _ok(page.post("/api/playgrounds", json={"folder": str(original), "name": "Try"}))
    playground = Path(made["folder"])
    added = _node(page, "Only here")
    # The tab still names the playground when it asks (the identity header, as the page sends it).
    tab = {**ORIGIN, "X-Chembook-Investigation": quote(str(playground))}
    started = page.post(
        "/api/playgrounds/merge/start", json={"folder": str(playground)}, headers=tab
    )
    assert started.status_code == 200, started.text
    assert Path(page.get("/api/investigation").json()["folder"]) == original.resolve()
    changes = _changes(started.json()["review"])
    assert list(changes) == [f"node:{added}"]
    done = page.post(
        "/api/playgrounds/merge",
        json={"folder": str(playground), "ticked": list(changes)},
        headers=ORIGIN,
    )
    assert done.status_code == 200, done.text
    assert "Only here" in {n["label"] for n in page.get("/api/nodes").json()}


def test_migration_0033_adds_the_merge_pointer(tmp_path):
    from chembook3d import investigation as inv

    folder = tmp_path / "pre-merge"
    folder.mkdir()
    engine = inv._make_engine(folder / inv.DB_NAME)
    with engine.begin() as connection:
        inv.command.upgrade(inv._alembic_config(connection), "0032")
        connection.exec_driver_sql(
            "INSERT INTO investigation_info (id, name, created_at) VALUES (1, 'Old', '2026-10-01')"
        )
        connection.exec_driver_sql(
            "INSERT INTO history (timestamp, record_type, record_id, action, source) "
            "VALUES ('2026-10-01', 'node', 'n', 'create', 'manual')"
        )
    engine.dispose()
    opened = inv.open_investigation(folder)
    try:
        assert not opened.read_only
        with opened.sessions() as session:
            row = (
                session.connection()
                .exec_driver_sql("SELECT merge_entry_id FROM history WHERE record_id = 'n'")
                .one()
            )
        assert row[0] is None
    finally:
        opened.close()


def test_an_older_base_is_read_from_a_migrated_copy(page, tmp_path):
    # The base is never written, not even to bring it to this app's schema.
    from sqlalchemy import create_engine

    from chembook3d import investigation as inv

    original, _ids = _original(page, tmp_path)
    made = _ok(page.post("/api/playgrounds", json={"folder": str(original), "name": "Try"}))
    playground = Path(made["folder"])
    added = _node(page, "Only here")
    _ok(page.post("/api/investigations/close"))
    base = playground / "playground" / "base.sqlite"
    engine = create_engine(f"sqlite:///{base}")  # foreign keys off, as while migrating
    with engine.begin() as connection:
        inv.command.downgrade(inv._alembic_config(connection), "0032")
    engine.dispose()
    before = base.read_bytes()

    started = page.post(
        "/api/playgrounds/merge/start", json={"folder": str(playground)}, headers=ORIGIN
    )
    assert started.status_code == 200, started.text
    assert list(_changes(started.json()["review"])) == [f"node:{added}"]
    assert base.read_bytes() == before
