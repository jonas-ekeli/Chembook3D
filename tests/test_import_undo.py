"""Undo an import from the history (D102, A49): T-IMP-18, T-IMP-19."""

import shutil
from pathlib import Path

from chembook3d.models import HistoryEntry, Node, SourceFile
from chembook3d.services import history
from tests import gaussian_text as g
from tests.test_batch_import import nodes, row, run, scan
from tests.test_gaussian_parser import truncated_opt
from tests.test_import import calculations, commit, copied_files, named, node, upload
from tests.test_pre_optimization import (
    DFT_END,
    GUESS,
    OTHER,
    PRE_END,
    pre_optimization,
    pre_optimized_node,
    xyz,
)

CREST = Path(__file__).parent / "fixtures" / "crest" / "crest_conformers.xyz"


def rows(atoms) -> list[list]:
    return [[e, x, y, z] for e, _, x, y, z in atoms]


def entries(client, record_id: str | None = None) -> list[dict]:
    params = {"record_id": record_id} if record_id else {}
    return client.get("/api/history", params=params).json()


def import_entry(client, file: str, record_id: str | None = None) -> dict:
    """The newest "Imported … from file" entry of that file."""
    return next(
        e
        for e in entries(client, record_id)
        if e["record_type"] == "calculation" and e["action"] == "create"
        if e["new_value"]["file"] == file
    )


def preview(client, entry: dict) -> dict:
    response = client.get(f"/api/history/{entry['id']}/undo")
    assert response.status_code == 200, response.text
    return response.json()


def undo(client, entry: dict, status: int = 200) -> dict:
    response = client.post(f"/api/history/{entry['id']}/undo")
    assert response.status_code == status, response.text
    return response.json()


def sources(client) -> int:
    with client.app.state.investigation.sessions() as session:
        return session.query(SourceFile).count()


# ---------- T-IMP-18: one file ----------


def test_undo_an_import_that_made_a_node(open_client):
    water = g.output(g.step("#P B3LYP/6-31G(d) Opt", [g.WATER], converged=True))
    made = commit(open_client, upload(open_client, "w.out", water))["node_id"]
    entry = import_entry(open_client, "w.out")
    assert entry["undo"] == "import"
    assert len(copied_files(open_client)) == 1

    seen = preview(open_client, entry)
    assert seen["kind"] == "import" and seen["blockers"] == []
    (file,) = seen["files"]
    assert file["file"] == "w.out" and len(file["calculations"]) == 1
    assert file["deleted"] == [{"id": made, "label": "w"}]
    assert nodes(open_client) and copied_files(open_client)  # the preview wrote nothing

    undo(open_client, entry)
    assert nodes(open_client) == []
    assert copied_files(open_client) == [] and sources(open_client) == 0
    found = entries(open_client)
    assert [e["action"] for e in found[:2]] == ["undo_import", "delete"]
    assert found[0]["old_value"]["file"] == "w.out"
    # The import's own entries stay (P15) and can no longer be undone.
    assert import_entry(open_client, "w.out")["undo"] is None
    undo(open_client, entry, status=422)


def test_undo_an_import_that_finished_a_planned_node(open_client):
    planned = open_client.post(
        "/api/nodes", json={"label": "guess", "xyz": xyz(GUESS), "charge": 1}
    ).json()
    plan = upload(open_client, "A.log", g.opt_freq(GUESS, DFT_END), planned["id"])
    assert plan["mode"] == "planned"
    commit(open_client, plan, target_node_id=planned["id"], label="A", status="done")
    finished = node(open_client, planned["id"])
    assert finished["label"] == "A" and finished["charge"] == 0

    seen = preview(open_client, import_entry(open_client, "A.log"))["files"][0]
    assert {r["field"] for r in seen["restored"]} >= {"geometry", "charge", "label", "status"}
    undo(open_client, import_entry(open_client, "A.log"))
    back = node(open_client, planned["id"])
    assert (back["label"], back["status"], back["charge"]) == ("guess", "planned", 1)
    assert calculations(open_client, planned["id"]) == []
    with open_client.app.state.investigation.sessions() as session:
        assert session.get(Node, planned["id"]).geometry == rows(GUESS)
    # The node's history says what happened, newest first.
    mine = entries(open_client, planned["id"])
    assert mine[0]["action"] == "undo_import"
    assert {e["field"] for e in mine[1:5]} <= {r["field"] for r in seen["restored"]}


def test_a_field_changed_after_the_import_keeps_its_later_value(open_client):
    planned = open_client.post("/api/nodes", json={"label": "B", "xyz": xyz(GUESS)}).json()
    plan = upload(open_client, "B.log", g.opt_freq(GUESS, DFT_END), planned["id"])
    commit(open_client, plan, target_node_id=planned["id"], status="done")
    open_client.patch(f"/api/nodes/{planned['id']}", json={"status": "failed"})

    seen = preview(open_client, import_entry(open_client, "B.log"))["files"][0]
    assert seen["kept"] == [
        {"node_id": planned["id"], "label": "B", "field": "status", "value": "failed"}
    ]
    undo(open_client, import_entry(open_client, "B.log"))
    assert node(open_client, planned["id"])["status"] == "failed"


def test_undo_a_frequency_job_attached_later(open_client):
    water = g.output(g.step("#P B3LYP/6-31G(d) Opt", [g.WATER], converged=True))
    base = commit(open_client, upload(open_client, "w.out", water))["node_id"]
    plan = upload(open_client, "sp.out", g.single_point(g.WATER), base)
    assert plan["mode"] == "onto"
    commit(open_client, plan, target_node_id=base)
    assert len(calculations(open_client, base)) == 2

    seen = preview(open_client, import_entry(open_client, "sp.out"))["files"][0]
    assert seen["deleted"] == [] and seen["restored"] == []
    undo(open_client, import_entry(open_client, "sp.out"))
    assert [c["source_file"]["original_name"] for c in calculations(open_client, base)] == ["w.out"]
    assert len(copied_files(open_client)) == 1


def test_undo_an_import_that_made_a_derived_node(open_client):
    water = g.output(g.step("#P B3LYP/6-31G(d) Opt", [g.WATER], converged=True))
    base = commit(open_client, upload(open_client, "w.out", water))["node_id"]
    plan = upload(open_client, "sp.out", g.single_point(g.moved(g.WATER, 0.05)), base)
    derived = commit(open_client, plan, target_node_id=base)["derived_node_id"]

    assert preview(open_client, import_entry(open_client, "sp.out"))["files"][0]["deleted"] == [
        {"id": derived, "label": "sp"}
    ]
    undo(open_client, import_entry(open_client, "sp.out"))
    assert [n["id"] for n in nodes(open_client)] == [base]
    assert len(calculations(open_client, base)) == 1


def test_undo_takes_the_optimization_incomplete_tag_off_again(open_client):
    water = g.output(g.step("#P B3LYP/6-31G(d) Opt", [g.WATER], converged=True))
    base = commit(open_client, upload(open_client, "w.out", water))["node_id"]
    open_client.patch(f"/api/nodes/{base}", json={"tags": ["keep"]})
    plan = upload(open_client, "failed.log", truncated_opt(), base)
    result = commit(open_client, plan, target_node_id=base, **named(plan))
    tagged = result["derived_node_id"] or base
    assert "optimization-incomplete" in node(open_client, tagged)["tags"]
    undo(open_client, import_entry(open_client, "failed.log"))
    assert node(open_client, base)["tags"] == ["keep"]


def test_undo_a_crest_ensemble(open_client):
    plan = upload(open_client, "crest_conformers.xyz", CREST.read_text(encoding="utf-8"))
    result = commit(open_client, plan)
    assert result["group_id"] and nodes(open_client)
    entry = import_entry(open_client, "crest_conformers.xyz")
    seen = preview(open_client, entry)["files"][0]
    assert seen["group"]["id"] == result["group_id"]
    assert len(seen["deleted"]) == len(nodes(open_client))
    undo(open_client, entry)
    assert nodes(open_client) == []
    assert open_client.get("/api/canvas").json()["groups"] == []
    assert entries(open_client)[0]["record_type"] == "group"


# ---------- what blocks an undo ----------


def test_later_work_on_a_node_the_import_made_blocks_the_undo(open_client):
    water = g.output(g.step("#P B3LYP/6-31G(d) Opt", [g.WATER], converged=True))
    made = commit(open_client, upload(open_client, "w.out", water))["node_id"]
    other = open_client.post("/api/nodes", json={"label": "other"}).json()
    edge = open_client.post(
        "/api/transitions", json={"source_id": made, "target_id": other["id"]}
    ).json()
    commit(
        open_client,
        upload(open_client, "sp.out", g.single_point(g.WATER), made),
        target_node_id=made,
    )

    entry = import_entry(open_client, "w.out")
    blockers = preview(open_client, entry)["blockers"]
    assert any("sp.out" in b for b in blockers)
    assert any("edge" in b for b in blockers)
    refused = undo(open_client, entry, status=409)
    assert refused["detail"]["blockers"] == blockers
    assert len(nodes(open_client)) == 2 and len(copied_files(open_client)) == 2

    # Undoing the later import and deleting the edge clears the way.
    undo(open_client, import_entry(open_client, "sp.out"))
    open_client.delete(f"/api/transitions/{edge['id']}")
    undo(open_client, entry)
    assert [n["label"] for n in nodes(open_client)] == ["other"]


def test_continued_pre_optimization_is_undone_back_to_its_geometry(open_client):
    # D100: the DFT optimization moved the node onto its result; undoing it moves it back.
    node_id = pre_optimized_node(open_client)
    plan = upload(open_client, "A_dft.log", g.opt_freq(PRE_END, DFT_END), node_id)
    commit(open_client, plan, target_node_id=node_id)
    commit(
        open_client,
        upload(open_client, "A_SP.log", g.single_point(DFT_END), node_id),
        target_node_id=node_id,
    )

    dft = import_entry(open_client, "A_dft.log")
    blockers = preview(open_client, dft)["blockers"]
    assert any("A_SP.log" in b for b in blockers)  # it is at the DFT geometry and level
    undo(open_client, dft, status=409)

    undo(open_client, import_entry(open_client, "A_SP.log"))
    assert preview(open_client, dft)["blockers"] == []
    undo(open_client, dft)
    with open_client.app.state.investigation.sessions() as session:
        assert session.get(Node, node_id).geometry == rows(PRE_END)
    assert [c["source_file"]["original_name"] for c in calculations(open_client, node_id)] == [
        "A_pre.log"
    ]

    # The pre-optimization cannot be undone while a later import continued from it.
    plan = upload(open_client, "A_dft2.log", g.opt_freq(PRE_END, DFT_END), node_id)
    commit(open_client, plan, target_node_id=node_id)
    pre = import_entry(open_client, "A_pre.log")
    (blocker,) = preview(open_client, pre)["blockers"]
    assert "coordinates" in blocker and "A_dft2.log" in blocker


def test_a_repaired_pre_optimization_is_undone_too(open_client):
    """An import made before D100 left the node on its pre-optimization's geometry and the
    one-off repair moved it onto the DFT result later (source "repair")."""
    node_id = pre_optimized_node(open_client)
    plan = upload(open_client, "A_dft.log", g.opt_freq(PRE_END, DFT_END), node_id)
    commit(open_client, plan, target_node_id=node_id)
    with open_client.app.state.investigation.sessions.begin() as session:
        moved = session.scalars(
            HistoryEntry.__table__.select()
            .where(HistoryEntry.record_id == node_id, HistoryEntry.field == "geometry")
            .order_by(HistoryEntry.id.desc())
        ).first()
        session.delete(session.get(HistoryEntry, moved))
        history.record(
            session,
            "node",
            node_id,
            "update",
            "geometry",
            rows(PRE_END),
            rows(DFT_END),
            source="repair",
        )
    undo(open_client, import_entry(open_client, "A_dft.log"))
    with open_client.app.state.investigation.sessions() as session:
        assert session.get(Node, node_id).geometry == rows(PRE_END)


# ---------- T-IMP-19: a batch ----------


def batch(open_client, tmp_path) -> tuple[str, dict]:
    planned = open_client.post("/api/nodes", json={"label": "P", "xyz": xyz(GUESS)}).json()
    folder = tmp_path / "results"
    folder.mkdir()
    (folder / "P.log").write_text(pre_optimization(), encoding="utf-8")
    (folder / "N_opt.log").write_text(g.opt_freq(OTHER, g.moved(g.WATER, 0.6)), encoding="utf-8")
    (folder / "N_SP.log").write_text(g.single_point(g.moved(g.WATER, 0.6)), encoding="utf-8")
    plan = scan(open_client, folder)
    assert row(plan, "P.log")["mode"] == "planned"
    assert row(plan, "N_SP.log")["target"]["kind"] == "file"
    response = run(open_client, plan)
    assert response.status_code == 200, response.text
    entry = next(e for e in entries(open_client) if e["record_type"] == "batch_import")
    return planned["id"], entry


def test_undo_a_batch(open_client, tmp_path):
    planned, entry = batch(open_client, tmp_path)
    assert entry["undo"] == "batch"
    assert len(nodes(open_client)) == 2 and len(copied_files(open_client)) == 3

    seen = preview(open_client, entry)
    assert seen["kind"] == "batch" and seen["blockers"] == []
    # Last file first, so the single point does not block the node its batch made.
    order = [f["file"] for f in seen["files"]]
    assert sorted(order) == ["N_SP.log", "N_opt.log", "P.log"]
    assert order.index("N_SP.log") < order.index("N_opt.log")
    undo(open_client, entry)
    assert [n["id"] for n in nodes(open_client)] == [planned]
    assert node(open_client, planned)["status"] == "planned"
    assert copied_files(open_client) == [] and sources(open_client) == 0
    found = entries(open_client)
    assert (found[0]["record_type"], found[0]["action"]) == ("batch_import", "undo")
    assert next(e for e in found if e["id"] == entry["id"])["undo"] is None


def test_one_file_of_a_batch_and_later_work_on_it(open_client, tmp_path):
    planned, entry = batch(open_client, tmp_path)
    made = next(n["id"] for n in nodes(open_client) if n["id"] != planned)
    commit(
        open_client,
        upload(open_client, "late.out", g.single_point(g.moved(g.WATER, 0.6)), made),
        target_node_id=made,
    )
    blockers = preview(open_client, entry)["blockers"]
    assert any(b.startswith("N_opt.log:") and "late.out" in b for b in blockers)
    undo(open_client, entry, status=409)
    assert len(copied_files(open_client)) == 4  # nothing was undone

    undo(open_client, import_entry(open_client, "P.log"))  # one file of the batch alone
    assert node(open_client, planned)["status"] == "planned"
    undo(open_client, import_entry(open_client, "late.out"))
    seen = preview(open_client, entry)
    assert [f["file"] for f in seen["files"]] == ["N_SP.log", "N_opt.log"]
    undo(open_client, entry)
    assert [n["id"] for n in nodes(open_client)] == [planned]


def test_a_batch_imported_before_this_change(open_client, tmp_path):
    """Batches written before D102 do not name their files' records; the files just before
    the batch's entry are matched by name."""
    planned, entry = batch(open_client, tmp_path)
    with open_client.app.state.investigation.sessions.begin() as session:
        stored = session.get(HistoryEntry, entry["id"])
        value = dict(stored.new_value)
        value["files"] = [
            {k: v for k, v in f.items() if k != "source_file_id"} for f in value["files"]
        ]
        stored.new_value = value
    assert next(e for e in entries(open_client) if e["id"] == entry["id"])["undo"] == "batch"
    undo(open_client, entry)
    assert [n["id"] for n in nodes(open_client)] == [planned]


def test_copies_are_kept_until_the_undo_commits(open_client, monkeypatch):
    water = g.output(g.step("#P B3LYP/6-31G(d) Opt", [g.WATER], converged=True))
    commit(open_client, upload(open_client, "w.out", water))
    removed = []
    monkeypatch.setattr(shutil, "rmtree", lambda path, **_: removed.append(Path(path)))
    preview(open_client, import_entry(open_client, "w.out"))
    assert removed == []
    undo(open_client, import_entry(open_client, "w.out"))
    assert [p.parent.name for p in removed] == ["files"]
