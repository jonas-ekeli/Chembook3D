"""Batch import of a results folder (D97, A44, FR-IMP-14): T-IMP-12, T-IMP-13, T-IMP-14."""

import shutil
from pathlib import Path

import pytest

from chembook3d.models import Node
from chembook3d.services import batch_import, imports
from tests import gaussian_text as g
from tests.test_gaussian_parser import FIXTURES, MINIMUM, TS, TS_SP
from tests.test_import import calculations, commit, copied_files, fixture, named, node, upload

XTB = Path(__file__).parent / "fixtures" / "xtb"

# A water molecule that has nothing to do with the others: a different shape.
OTHER = g.moved(g.WATER, 0.3)


def water_opt(start, end=None) -> str:
    return g.opt_freq(start, end or start)


def scan(client, folder: Path, recursive: bool = True) -> dict:
    response = client.post(
        "/api/batch-imports", json={"folder": str(folder), "recursive": recursive}
    )
    assert response.status_code == 200, response.text
    return response.json()


def preview(client, plan: dict, **options) -> dict:
    response = client.post(f"/api/batch-imports/{plan['token']}/preview", json=options)
    assert response.status_code == 200, response.text
    return response.json()


def run(client, plan: dict, **options):
    return client.post(f"/api/batch-imports/{plan['token']}/commit", json=options)


def row(plan: dict, path: str) -> dict:
    return next(r for r in plan["rows"] if r["path"] == path)


def nodes(client) -> list[dict]:
    return client.get("/api/nodes").json()


def batch_names(plan: dict) -> dict:
    options: dict = {"basis_names": {}, "dispersion_names": {}}
    for request in plan["names"]:
        kind = "basis_names" if request["kind"] == "basis" else "dispersion_names"
        options[kind][request["key"]] = f"{request['kind']}-{len(options[kind]) + 1}"
    return options


# ---------- T-IMP-12 ----------


def test_folder_of_results(open_client, tmp_path):
    folder = tmp_path / "results"
    (folder / "ts").mkdir(parents=True)
    # The single point sorts first by name; it still attaches to the node the TS file makes.
    shutil.copy(FIXTURES / TS_SP, folder / "ts" / "A_sp.log")
    shutil.copy(FIXTURES / TS, folder / "ts" / TS)
    shutil.copy(FIXTURES / MINIMUM, folder / MINIMUM)
    (folder / "job.gjf").write_text(
        "%chk=job.chk\n#P B3LYP/6-31G(d) Opt\n\njob\n\n0 1\n", encoding="utf-8"
    )
    (folder / "job.chk").write_bytes(b"\x00\x01binary checkpoint")
    (folder / "slurm-1234.out").write_text("Starting job on node c12\n", encoding="utf-8")
    (folder / ".hidden.log").write_text("ignored", encoding="utf-8")

    plan = scan(open_client, folder)
    assert plan["other_count"] == 3  # the input, the checkpoint and the scheduler log
    assert [r["path"] for r in plan["rows"]] == [MINIMUM, f"ts/{TS}", "ts/A_sp.log"]
    assert row(plan, MINIMUM)["match"] == "new" and row(plan, MINIMUM)["mode"] == "new"
    ts = row(plan, f"ts/{TS}")
    assert ts["target"]["kind"] == "new" and ts["target"]["label"] == TS.removesuffix(".log")
    sp = row(plan, "ts/A_sp.log")
    assert sp["match"] == "geometry" and sp["mode"] == "onto"
    assert sp["target"] == {
        "kind": "file",
        "file_id": ts["id"],
        "node_id": None,
        "label": TS.removesuffix(".log"),
        "file": f"ts/{TS}",
    }
    assert plan["counts"] == {
        "files": 3,
        "included": 3,
        "new": 2,
        "attached": 1,
        "finished": 0,
        "skipped": 0,
    }
    # The preview wrote nothing.
    assert nodes(open_client) == [] and copied_files(open_client) == []

    options = batch_names(plan)
    planned = preview(open_client, plan, **options)
    assert planned["blockers"] == []
    response = run(open_client, plan, origin_device="cluster", **options)
    assert response.status_code == 200, response.text
    assert response.json()["imported"] == 3

    made = {n["label"]: n for n in nodes(open_client)}
    assert set(made) == {TS.removesuffix(".log"), MINIMUM.removesuffix(".log")}
    on_ts = calculations(open_client, made[TS.removesuffix(".log")]["id"])
    assert "A_sp.log" in {c["source_file"]["original_name"] for c in on_ts}
    assert len(copied_files(open_client)) == 3
    entry = next(
        e for e in open_client.get("/api/history").json() if e["record_type"] == "batch_import"
    )
    assert entry["new_value"]["count"] == 3
    assert entry["new_value"]["folder"] == str(folder.resolve())
    assert [f["file"] for f in entry["new_value"]["files"]] == [MINIMUM, f"ts/{TS}", "ts/A_sp.log"]
    source = on_ts[0]["source_file"]
    assert source["origin_device"] == "cluster"
    assert source["origin_path"].endswith(TS)

    # Scanning again skips everything: each file was imported before.
    again = scan(open_client, folder)
    assert all(r["skipped"] == "imported_before" and not r["included"] for r in again["rows"])
    assert "Tick at least one file to import" in again["blockers"]


def test_subfolders_can_be_left_out(open_client, tmp_path):
    folder = tmp_path / "results"
    (folder / "sub").mkdir(parents=True)
    (folder / "a.out").write_text(water_opt(g.WATER), encoding="utf-8")
    (folder / "sub" / "b.out").write_text(water_opt(OTHER), encoding="utf-8")
    assert [r["path"] for r in scan(open_client, folder)["rows"]] == ["a.out", "sub/b.out"]
    assert [r["path"] for r in scan(open_client, folder, recursive=False)["rows"]] == ["a.out"]


def test_existing_and_planned_nodes_are_matched(open_client, tmp_path):
    existing = commit(open_client, upload(open_client, "w.out", water_opt(g.WATER)))["node_id"]
    guess = open_client.post(
        "/api/nodes", json={"label": "B guess", "xyz": "3\n\n" + xyz(OTHER)}
    ).json()
    folder = tmp_path / "results"
    folder.mkdir()
    (folder / "w_SPQZ.out").write_text(g.single_point(g.WATER), encoding="utf-8")
    # Optimised from the guess: the first geometry is the guess, the last one is new.
    (folder / "b.out").write_text(water_opt(OTHER, g.moved(OTHER, 0.1)), encoding="utf-8")
    plan = scan(open_client, folder)
    sp, b = row(plan, "w_SPQZ.out"), row(plan, "b.out")
    assert sp["match"] == "geometry" and sp["target"]["node_id"] == existing
    assert b["match"] == "first_geometry" and b["mode"] == "planned"
    assert b["target"] == {
        "kind": "node",
        "node_id": guess["id"],
        "file_id": None,
        "label": "B guess",
    }
    assert run(open_client, plan).status_code == 200
    assert len(calculations(open_client, existing)) == 3
    finished = node(open_client, guess["id"])
    assert finished["status"] == "done" and finished["label"] == "B guess"
    assert len(nodes(open_client)) == 2


def xyz(atoms) -> str:
    return "".join(f"{a[0]} {a[2]} {a[3]} {a[4]}\n" for a in atoms)


# ---------- T-IMP-13: names ----------


def labelled(*labels: str) -> list[Node]:
    return [Node(id=label, label=label) for label in labels]


@pytest.mark.parametrize(
    ("stem", "how", "found"),
    [
        ("TS1-2", "exact", ["TS1-2"]),
        ("ts1-2", "exact", ["TS1-2"]),
        ("TS1-2'", "exact", ["TS1-2'"]),
        ("TS1-2''", "exact", ["TS1-2''"]),
        ("TS1-2'_SPQZ", "exact", ["TS1-2'"]),
        ("TS1-2''_freq", "exact", ["TS1-2''"]),
        ("TS1-2_SP", "exact", ["TS1-2"]),
        ("TS1-2__", "close", ["TS1-2''"]),
        ("TS1-2_", "close", ["TS1-2'"]),
        ("TS1-2___", None, []),
        ("TS1-3", None, []),
    ],
)
def test_names_keep_primes_apart(stem, how, found):
    nodes_ = labelled("TS1-2", "TS1-2'", "TS1-2''")
    result = batch_import.match_name(stem, list(batch_import.DEFAULT_SUFFIXES), nodes_)
    assert result[0] == how and [n.label for n in result[1]] == found


def test_name_rules():
    suffixes = list(batch_import.DEFAULT_SUFFIXES)
    # Labels are compared as the app writes them into file names (forbidden characters → _).
    assert batch_import.match_name("A_B", suffixes, labelled("A/B"))[0] == "exact"
    # Greek letters and spaces may have become _, one for one.
    assert batch_import.match_name("Ru_a", suffixes, labelled("Ru α"))[1] == []
    assert batch_import.match_name("Ru__", suffixes, labelled("Ru α"))[0] == "close"
    # "-" and "." were always kept, so "_" never stands for them.
    assert batch_import.match_name("TS1_2", suffixes, labelled("TS1-2"))[1] == []
    # Two nodes with the same label: the user picks; a same-case match wins over case-only.
    assert len(batch_import.match_name("MCB", suffixes, labelled("MCB", "MCB"))[1]) == 2
    assert [n.id for n in batch_import.match_name("MCB", suffixes, labelled("mcb", "MCB"))[1]] == [
        "MCB"
    ]
    # The suffix list takes wildcards and can be edited.
    assert batch_import.name_bases("TS1_SPQZ_freq", ["_SP*"]) == ["TS1_SPQZ_freq", "TS1"]
    assert batch_import.name_bases("TS1_irc", []) == ["TS1_irc"]


def test_file_names_go_to_their_primed_nodes(open_client, tmp_path):
    ids = {}
    for n, label in enumerate(["TS1-2", "TS1-2'", "TS1-2''"]):
        guess = g.moved(g.WATER, 0.2 * (n + 1))
        ids[label] = open_client.post(
            "/api/nodes", json={"label": label, "xyz": "3\n\n" + xyz(guess)}
        ).json()["id"]
    folder = tmp_path / "results"
    folder.mkdir()
    # Geometries that match no node, so only the names can tell.
    (folder / "TS1-2''.out").write_text(water_opt(g.moved(g.WATER, -0.31)), encoding="utf-8")
    (folder / "TS1-2'_SPQZ.out").write_text(
        g.single_point(g.moved(g.WATER, -0.32)), encoding="utf-8"
    )
    (folder / "TS1-2__.out").write_text(g.single_point(g.moved(g.WATER, -0.33)), encoding="utf-8")
    plan = scan(open_client, folder)
    double, single, close = (
        row(plan, "TS1-2''.out"),
        row(plan, "TS1-2'_SPQZ.out"),
        row(plan, "TS1-2__.out"),
    )
    assert double["match"] == "name" and double["target"]["node_id"] == ids["TS1-2''"]
    assert double["mode"] == "planned"
    assert single["match"] == "name" and single["target"]["node_id"] == ids["TS1-2'"]
    # TS1-2'' now has calculations and another geometry: a derived node (FR-IMP-08).
    assert close["match"] == "name_close" and close["target"]["node_id"] == ids["TS1-2''"]
    assert close["derived"]


def test_a_name_fitting_two_nodes_waits_for_a_choice(open_client, tmp_path):
    first = open_client.post("/api/nodes", json={"label": "MCB"}).json()["id"]
    open_client.post("/api/nodes", json={"label": "MCB"})
    folder = tmp_path / "results"
    folder.mkdir()
    (folder / "MCB.out").write_text(water_opt(g.WATER), encoding="utf-8")
    plan = scan(open_client, folder)
    mcb = row(plan, "MCB.out")
    assert mcb["match"] == "none" and len(mcb["candidates"]) == 2
    assert plan["blockers"] == ["MCB.out: Its name fits “MCB”, “MCB”: choose one"]
    assert run(open_client, plan).status_code == 422
    chosen = {"rows": {mcb["id"]: {"target": f"node:{first}"}}}
    assert preview(open_client, plan, **chosen)["blockers"] == []
    assert run(open_client, plan, **chosen).status_code == 200
    assert node(open_client, first)["atom_count"] == 3


# ---------- choices ----------


def test_rows_can_be_unticked_and_retargeted(open_client, tmp_path):
    folder = tmp_path / "results"
    folder.mkdir()
    (folder / "a.out").write_text(water_opt(g.WATER), encoding="utf-8")
    (folder / "a_SP.out").write_text(g.single_point(g.WATER), encoding="utf-8")
    (folder / "c.out").write_text(water_opt(OTHER), encoding="utf-8")
    (folder / "d.out").write_text(g.single_point(g.moved(g.WATER, 0.7)), encoding="utf-8")
    plan = scan(open_client, folder)
    a, sp, c, d = (row(plan, p) for p in ("a.out", "a_SP.out", "c.out", "d.out"))
    assert d["match"] == "new"
    choices = {
        "rows": {
            sp["id"]: {"target": "new", "label": "a, single point"},
            c["id"]: {"included": False},
            # d waits for a's node, although it would be read first by its name.
            d["id"]: {"target": f"file:{a['id']}"},
        }
    }
    planned = preview(open_client, plan, **choices)
    assert planned["blockers"] == []
    assert row(planned, "c.out")["included"] is False
    assert row(planned, "a_SP.out")["target"]["kind"] == "new"
    assert row(planned, "d.out")["target"]["file_id"] == a["id"]
    assert row(planned, "d.out")["derived"]  # another geometry: a derived node
    response = run(open_client, plan, **choices)
    assert response.status_code == 200, response.text
    made = {n["label"]: n for n in nodes(open_client)}
    assert sorted(made) == ["a", "a, single point", "d"]
    assert made["d"]["derived_from_id"] == made["a"]["id"]  # d's derived node, from a


def test_possible_duplicate_asks(open_client, tmp_path):
    base = commit(open_client, upload(open_client, "w.out", water_opt(g.WATER)))["node_id"]
    folder = tmp_path / "results"
    folder.mkdir()
    (folder / "near.out").write_text(water_opt(g.moved(g.WATER, 0.02)), encoding="utf-8")
    plan = scan(open_client, folder)
    near = row(plan, "near.out")
    assert near["duplicates"][0]["node_id"] == base
    assert near["blockers"] == [
        "Possible duplicate of “w” (RMSD 0.009 Å): attach it there or make a new node"
    ]
    attach = {"rows": {near["id"]: {"duplicate_action": "attach"}}}
    attached = row(preview(open_client, plan, **attach), "near.out")
    assert attached["target"]["node_id"] == base and attached["match"] == "duplicate"
    assert run(open_client, plan, **attach).status_code == 200
    # Attached, but on another geometry: a derived node, as in a single import (FR-IMP-08).
    derived = next(n for n in nodes(open_client) if n["id"] != base)
    assert derived["derived_from_id"] == base


def test_files_without_coordinates_need_a_node(open_client, tmp_path):
    folder = tmp_path / "results"
    folder.mkdir()
    shutil.copy(XTB / "dvb_sp.out", folder / "dvb_sp.out")
    plan = scan(open_client, folder)
    sp = row(plan, "dvb_sp.out")
    assert sp["blockers"] == [
        "It prints no coordinates: choose the node it was run on, or untick it"
    ]
    # The suffix _SP* is taken off before matching: "dvb_sp" goes to the node "dvb".
    dvb = commit(
        open_client,
        upload(open_client, "dvb_opt.out", (XTB / "dvb_opt.out").read_text(encoding="utf-8")),
    )
    open_client.patch(f"/api/nodes/{dvb['node_id']}", json={"label": "dvb"})
    plan = scan(open_client, folder)
    sp = row(plan, "dvb_sp.out")
    assert sp["match"] == "name" and sp["target"]["node_id"] == dvb["node_id"]
    assert run(open_client, plan).status_code == 200
    assert len(calculations(open_client, dvb["node_id"])) == 2


def test_custom_basis_named_once_for_the_batch(open_client, tmp_path):
    folder = tmp_path / "results"
    folder.mkdir()
    for name, make in g.CUSTOM_FILES.items():
        (folder / name).write_text(make(), encoding="utf-8")
    plan = scan(open_client, folder)
    kinds = sorted(r["kind"] for r in plan["names"])
    assert kinds == ["basis", "basis", "dispersion"]  # modDZ, modQZ and the IOp dispersion
    assert plan["blockers"].count("Give the custom basis set a name") == 2
    # The QZ single point goes to the TS by geometry, planned with the placeholder names.
    assert row(plan, "MeI_TS_QZ.out")["match"] == "geometry"
    assert run(open_client, plan).status_code == 422
    assert nodes(open_client) == []
    options = {
        "basis_names": {
            r["key"]: ("modDZ" if "HF" in (r["title"] or "") or n == 0 else "modQZ")
            for n, r in enumerate(x for x in plan["names"] if x["kind"] == "basis")
        },
        "dispersion_names": {
            r["key"]: "GD3MBJ" for r in plan["names"] if r["kind"] == "dispersion"
        },
    }
    response = run(open_client, plan, **options)
    assert response.status_code == 200, response.text
    assert sorted(n["label"] for n in nodes(open_client)) == ["MeI_TS", "MeI_min"]


def test_ensembles_become_groups(open_client, tmp_path):
    folder = tmp_path / "results"
    folder.mkdir()
    shutil.copy(Path(__file__).parent / "fixtures" / "crest" / "crest_conformers.xyz", folder)
    plan = scan(open_client, folder)
    ensemble = row(plan, "crest_conformers.xyz")
    assert ensemble["kind"] == "ensemble" and ensemble["target"]["kind"] == "group"
    response = run(open_client, plan)
    assert response.status_code == 200, response.text
    assert response.json()["files"][0]["how"] == "group"


def test_new_nodes_go_right_of_the_canvas(open_client, tmp_path):
    open_client.post("/api/nodes", json={"label": "far", "pos_x": 1000, "pos_y": -50})
    folder = tmp_path / "results"
    folder.mkdir()
    for n in range(5):
        (folder / f"n{n}.out").write_text(
            water_opt(g.moved(g.WATER, 0.2 * (n + 1))), encoding="utf-8"
        )
    assert run(open_client, scan(open_client, folder)).status_code == 200
    placed = {n["label"]: (n["pos_x"], n["pos_y"]) for n in nodes(open_client)}
    x0 = 1000 + 2 * 240
    assert placed["n0"] == (x0, -50) and placed["n3"] == (x0 + 3 * 240, -50)
    assert placed["n4"] == (x0, -50 + 170)


# ---------- T-IMP-14 ----------


def test_a_failure_writes_nothing(open_client, tmp_path, monkeypatch):
    folder = tmp_path / "results"
    folder.mkdir()
    (folder / "a.out").write_text(water_opt(g.WATER), encoding="utf-8")
    (folder / "b.out").write_text(water_opt(OTHER), encoding="utf-8")
    plan = scan(open_client, folder)
    real = imports._create_calculation
    count = {"n": 0}

    def failing(*args, **kwargs):
        count["n"] += 1
        if count["n"] == 3:  # the second file's first calculation
            raise RuntimeError("disk full")
        return real(*args, **kwargs)

    monkeypatch.setattr(imports, "_create_calculation", failing)
    with pytest.raises(RuntimeError):
        run(open_client, plan)
    monkeypatch.undo()
    assert nodes(open_client) == []
    assert copied_files(open_client) == []
    assert not [e for e in open_client.get("/api/history").json() if e["source"] == "import"]


def test_cancel_drops_the_staged_files(open_client, tmp_path):
    folder = tmp_path / "results"
    folder.mkdir()
    (folder / "a.out").write_text(water_opt(g.WATER), encoding="utf-8")
    plan = scan(open_client, folder)
    staging = open_client.app.state.staging
    staged = staging.get(plan["rows"][0]["id"])
    assert open_client.delete(f"/api/batch-imports/{plan['token']}").status_code == 204
    assert not staged.folder.exists() and plan["token"] not in staging.batches
    assert (
        open_client.post(f"/api/batch-imports/{plan['token']}/preview", json={}).status_code == 404
    )


def test_same_file_twice_in_the_folder(open_client, tmp_path):
    folder = tmp_path / "results"
    (folder / "copy").mkdir(parents=True)
    (folder / "a.out").write_text(water_opt(g.WATER), encoding="utf-8")
    (folder / "copy" / "a.out").write_text(water_opt(g.WATER), encoding="utf-8")
    plan = scan(open_client, folder)
    assert row(plan, "copy/a.out")["skipped"] == "same_file"
    assert row(plan, "a.out")["included"] and not row(plan, "copy/a.out")["included"]


def test_suffixes_are_remembered(open_client, tmp_path):
    folder = tmp_path / "results"
    folder.mkdir()
    (folder / "a.out").write_text(water_opt(g.WATER), encoding="utf-8")
    plan = scan(open_client, folder)
    assert plan["suffixes"] == ["_SP*", "_freq", "_opt", "_irc"]
    assert run(open_client, plan, suffixes=["_SP*", "_hess"]).status_code == 200
    (folder / "b.out").write_text(water_opt(OTHER), encoding="utf-8")
    assert scan(open_client, folder)["suffixes"] == ["_SP*", "_hess"]


def test_named_helper_still_used(open_client):
    # The single-file import is unchanged by the dry-run option.
    plan = upload(open_client, TS, fixture(TS))
    assert commit(open_client, plan, **named(plan))["node_id"]
