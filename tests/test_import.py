"""Import through the API: FR-IMP-*, FR-FILE-*, FR-CALC-*, identity rules ID-6…ID-8.
Test IDs from docs/spec/07 are named in each test."""

from pathlib import Path

import pytest

from chembook3d.models import Calculation, Node, SourceFile
from chembook3d.services import imports
from tests import gaussian_text as g
from tests.test_gaussian_parser import FIXTURES, MINIMUM, TS, TS_SP, truncated_opt, without_route

NAMES = {"basis": {"modDZ", "modQZ"}}


def upload(client, name: str, text: str | bytes, node_id: str | None = None) -> dict:
    data = text.encode() if isinstance(text, str) else text
    params = {"filename": name} | ({"node_id": node_id} if node_id else {})
    response = client.post("/api/imports", params=params, content=data)
    assert response.status_code == 200, response.text
    return response.json()


def fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def named(plan: dict, basis: str = "modDZ", dispersion: str = "GD3MBJ") -> dict:
    """Options that give every unnamed custom basis and dispersion a name."""
    options: dict = {"basis_names": {}, "dispersion_names": {}}
    for request in plan["names"]:
        if not request["recognised"]:
            kind = "basis_names" if request["kind"] == "basis" else "dispersion_names"
            options[kind][request["key"]] = basis if request["kind"] == "basis" else dispersion
    return options


def preview(client, plan: dict, **options) -> dict:
    response = client.post(f"/api/imports/{plan['token']}/preview", json=options)
    assert response.status_code == 200, response.text
    return response.json()


def commit(client, plan: dict, **options) -> dict:
    response = client.post(f"/api/imports/{plan['token']}/commit", json=options)
    assert response.status_code == 200, response.text
    return response.json()


def calculations(client, node_id: str) -> list[dict]:
    return client.get(f"/api/nodes/{node_id}/calculations").json()


def node(client, node_id: str) -> dict:
    return client.get(f"/api/nodes/{node_id}").json()


def folder(client) -> Path:
    return client.app.state.investigation.folder


def copied_files(client) -> list[Path]:
    return [p for p in (folder(client) / "files").rglob("*") if p.is_file()]


def import_ts(client) -> str:
    plan = upload(client, TS, fixture(TS))
    return commit(client, plan, **named(plan))["node_id"]


TS_LABEL = "aminationTS-full-unfrz-c1"
TS_LEVEL = "Gaussian WB97XD/6-31+G(D) SMD(tetrahydrofuran)"


# ---------- T-IMP-01, T-IMP-02 ----------


def test_opt_freq_import(open_client):
    # T-IMP-01: a compound opt(tight) freq job
    plan = upload(open_client, MINIMUM, fixture(MINIMUM))
    assert plan["mode"] == "new" and plan["chosen_step"] == 2
    assert [s["assignment"] for s in plan["steps"]] == ["node", "node"]
    assert plan["suggested"] == {
        "label": "aziridinium-phos-full-c1",
        "role": "minimum",
        "status": "done",
    }
    assert plan["names"] == [] and plan["blockers"] == []  # a standard basis set

    result = commit(open_client, plan)
    imported = node(open_client, result["node_id"])
    assert imported["label"] == "aziridinium-phos-full-c1" and imported["role"] == "minimum"
    assert imported["status"] == "done" and imported["atom_count"] == 91
    assert (imported["charge"], imported["multiplicity"]) == (0, 1)
    assert imported["formula"] == "C31H50BLiNO6P"
    assert imported["warnings"] == []
    first_atom = imported["xyz"].splitlines()[2].split()
    assert first_atom == ["C", "-2.62554100", "0.29012600", "2.71362900"]

    opt, freq = calculations(open_client, result["node_id"])
    assert (opt["type"], freq["type"]) == ("optimization", "frequency")
    for calc in (opt, freq):
        assert calc["composite_label"] == "Gaussian WB97XD/6-31+G(D) SMD(tetrahydrofuran)"
        assert calc["termination"] == "normal" and calc["program_version"] == "16 Rev. C.01"
        assert calc["source_file"]["original_name"] == MINIMUM
    assert opt["result"]["energy"] == -2091.04905931
    assert opt["result"]["optimization_converged"] is True
    r = freq["result"]
    assert (r["zpe"], r["h"], r["g"]) == (0.789644, -2090.213472, -2090.339275)
    assert (r["temperature"], r["pressure"]) == (298.15, 1.0)
    assert len(r["frequencies"]) == 267 and r["imaginary_count"] == 0
    assert opt["level"]["id"] == freq["level"]["id"]


def test_custom_basis_chain_import(open_client):
    # T-IMP-01 for a --Link1-- chain with a custom basis (synthetic, as Jonas runs his jobs)
    plan = upload(open_client, "MeI_min.out", g.custom_chain())
    assert plan["chosen_step"] == 5
    # Steps 1-3 ran on the start geometry, so they are shown but not imported (05 §3.1).
    assert [s["assignment"] for s in plan["steps"]] == ["preview"] * 3 + ["node"] * 2
    assert {n["kind"] for n in plan["names"]} == {"basis", "dispersion"}
    assert plan["blockers"]  # names are needed first

    result = commit(open_client, plan, **named(plan))
    opt, freq = calculations(open_client, result["node_id"])
    for calc in (opt, freq):
        assert calc["composite_label"] == "Gaussian PBEPBE-GD3MBJ/modDZ"
    assert opt["level"]["id"] == freq["level"]["id"]


def test_ts_import_and_imaginary_frequency_warnings(open_client):
    # T-IMP-02: one imaginary frequency; W-IMAG-TS absent with role TS
    node_id = import_ts(open_client)
    ts = node(open_client, node_id)
    assert ts["role"] == "transition_state" and ts["warnings"] == []
    assert calculations(open_client, node_id)[1]["result"]["imaginary_count"] == 1

    open_client.patch(f"/api/nodes/{node_id}", json={"role": "minimum"})
    assert [w["code"] for w in node(open_client, node_id)["warnings"]] == ["W-IMAG-MIN"]


# ---------- T-IMP-03 ----------


def test_truncated_optimization(open_client):
    # T-IMP-03, FR-IMP-03, D26
    plan = upload(open_client, "failed.log", truncated_opt())
    assert plan["chosen_step"] == 1
    assert plan["suggested"]["status"] == "failed"
    codes = {w["code"] for w in plan["warnings"]}
    assert {"W-TERM", "W-OPT-INC"} <= codes

    result = commit(open_client, plan, **named(plan))
    failed = node(open_client, result["node_id"])
    assert failed["tags"] == ["optimization-incomplete"]
    assert failed["status"] == "failed"
    assert {w["code"] for w in failed["warnings"]} >= {"W-OPT-INC", "W-TERM"}
    (opt,) = calculations(open_client, result["node_id"])
    assert opt["termination"] == "abnormal" and opt["result"]["optimization_converged"] is False
    # the last geometry printed in the truncated step
    last = g_last_geometry(truncated_opt())
    assert failed["xyz"].splitlines()[2].split()[1] == f"{last:.8f}"


def g_last_geometry(text: str) -> float:
    lines = text.splitlines()
    start = max(i for i, line in enumerate(lines) if "Input orientation:" in line)
    return float(lines[start + 5].split()[3])


# ---------- T-IMP-04 ----------


def test_link1_opt_then_higher_level_single_point(open_client):
    # T-IMP-04: the TS chain followed by the modQZ single point as a sixth --Link1-- step
    text = g.custom_chain(ts=True) + g.custom_single_point()
    plan = upload(open_client, "MeI_TS_QZ.out", text)
    assert plan["chosen_step"] == 6
    assert [s["assignment"] for s in plan["steps"]] == ["preview"] * 3 + ["node"] * 3
    assert plan["steps"][5]["geometry_level_label"].endswith("(unnamed)")

    options = named(plan)
    qz_key = next(n["key"] for n in plan["names"] if n["elements"] and n["title"].endswith("modQZ"))
    options["basis_names"][qz_key] = "modQZ"
    sp = preview(open_client, plan, **options)["steps"][5]
    assert sp["level_label"] == "Gaussian PBEPBE-GD3MBJ/modQZ"
    assert sp["geometry_level_label"] == "Gaussian PBEPBE-GD3MBJ/modDZ"
    result = commit(open_client, plan, **options)
    calcs = calculations(open_client, result["node_id"])
    assert [c["type"] for c in calcs] == ["ts_optimization", "frequency", "single_point"]
    assert calcs[2]["composite_label"] == (
        "Gaussian PBEPBE-GD3MBJ/modQZ // Gaussian PBEPBE-GD3MBJ/modDZ"
    )
    assert calcs[2]["result"]["energy"] == -52.61


def test_single_point_file_onto_the_ts(open_client):
    # T-IMP-04 with a separate file: the larger-basis single point attaches to the TS node.
    node_id = import_ts(open_client)
    plan = upload(open_client, TS_SP, fixture(TS_SP), node_id)
    assert plan["mode"] == "onto" and not plan["derived_offered"]
    commit(open_client, plan, target_node_id=node_id)
    sp = calculations(open_client, node_id)[2]
    assert sp["composite_label"] == (
        f"Gaussian WB97XD/6-311++G(D,P) SMD(tetrahydrofuran) // {TS_LEVEL}"
    )
    assert sp["result"]["energy"] == -2091.36217372


def test_chosen_step_can_be_overridden(open_client):
    # T-IMP-04: "the override works" (FR-IMP-02)
    plan = upload(open_client, "MeI_TS.out", g.custom_chain(ts=True))
    changed = preview(open_client, plan, step=2, **named(plan))
    assert changed["chosen_step"] == 2
    assert [s["assignment"] for s in changed["steps"]] == ["node"] * 3 + ["preview"] * 2
    result = commit(open_client, plan, step=2, **named(plan))
    calcs = calculations(open_client, result["node_id"])
    assert [c["step_index"] for c in calcs] == [1, 2, 3]
    assert calcs[0]["composite_label"] == "Gaussian HF/modDZ"


# ---------- T-IMP-05 and D59 ----------


def test_custom_basis_and_dispersion_are_named_once(open_client):
    # T-IMP-05, FR-CALC-03, D59
    plan = upload(open_client, "MeI_TS.out", g.custom_chain(ts=True))
    assert "Give the custom basis set a name" in plan["blockers"]
    assert "Give the custom dispersion a name" in plan["blockers"]
    ts_id = commit(open_client, plan, **named(plan))["node_id"]

    # A second file with the same basis block and IOps is recognised: nothing to name.
    again = upload(open_client, "MeI_min.out", g.custom_chain())
    assert again["blockers"] == []
    assert all(n["recognised"] for n in again["names"])
    min_id = commit(open_client, again, duplicate_action="new")["node_id"]
    ts_level = calculations(open_client, ts_id)[0]["level"]
    min_level = calculations(open_client, min_id)[0]["level"]
    assert ts_level["id"] == min_level["id"]  # equality uses the names

    names = open_client.get("/api/custom-names").json()
    assert [b["name"] for b in names["bases"]] == ["modDZ"]
    assert names["dispersions"][0]["name"] == "GD3MBJ"


def test_a_name_cannot_stand_for_two_different_basis_sets(open_client):
    plan = upload(open_client, "MeI_TS.out", g.custom_chain(ts=True))
    node_id = commit(open_client, plan, **named(plan))["node_id"]
    plan = upload(open_client, "MeI_TS_QZ.out", g.custom_single_point(), node_id)
    same_name = preview(open_client, plan, target_node_id=node_id, **named(plan, basis="modDZ"))
    assert any("already the name" in b for b in same_name["blockers"])


# ---------- T-IMP-06 ----------


def test_partial_parse_lists_missing_fields(open_client):
    # T-IMP-06, FR-IMP-04
    plan = upload(open_client, "no_route.out", without_route(fixture(TS_SP)))
    assert plan["blockers"] == []
    result = commit(open_client, plan)
    imported = node(open_client, result["node_id"])
    assert imported["atom_count"] == 91
    (calc,) = calculations(open_client, result["node_id"])
    assert calc["level"] is None
    assert {"route section", "method", "basis set"} <= set(calc["parse_warnings"])
    assert "W-PARSE" in {w["code"] for w in calc["warnings"]}
    assert copied_files(open_client)  # the file is still copied


# ---------- T-IMP-07, T-IMP-08 ----------


def test_cancel_writes_nothing(open_client):
    # T-IMP-07, FR-IMP-05
    plan = upload(open_client, TS, fixture(TS))
    staged = open_client.app.state.staging.get(plan["token"])
    assert staged.path.is_file()
    assert open_client.delete(f"/api/imports/{plan['token']}").status_code == 204
    assert not staged.folder.exists()
    assert open_client.get("/api/nodes").json() == []
    assert open_client.get("/api/history").json() == []
    assert copied_files(open_client) == []
    assert open_client.post(f"/api/imports/{plan['token']}/commit", json={}).status_code == 404


def test_failure_during_import_leaves_no_records_or_files(open_client, monkeypatch):
    # T-IMP-08, NFR-DATA-04: an import that stops after copying the file
    plan = upload(open_client, TS, fixture(TS))

    def crash(*args, **kwargs):
        raise RuntimeError("process killed")

    monkeypatch.setattr(imports, "_create_calculation", crash)
    with pytest.raises(RuntimeError):
        open_client.post(f"/api/imports/{plan['token']}/commit", json=named(plan))
    assert open_client.get("/api/nodes").json() == []
    assert copied_files(open_client) == []


def test_orphaned_copies_are_removed_on_open(open_client, tmp_path):
    # P26: a killed process can leave files/<id>/ without records; reopening cleans it up
    node_id = import_ts(open_client)
    orphan = folder(open_client) / "files" / ("ab" * 16)
    orphan.mkdir()
    (orphan / "half.out").write_text("partial")
    unrelated = folder(open_client) / "files" / "my notes"
    unrelated.mkdir()
    inv_folder = str(folder(open_client))
    open_client.post("/api/investigations/close")
    assert (
        open_client.post("/api/investigations/open", json={"folder": inv_folder}).status_code == 200
    )
    assert not orphan.exists() and unrelated.exists()
    assert len(calculations(open_client, node_id)) == 2
    assert len(copied_files(open_client)) == 1


# ---------- T-ID-03…06, T-IMP-11 ----------


def test_import_onto_planned_node_replaces_the_guess(open_client):
    # T-ID-03, ID-6, FR-IMP-12, WF-03
    guess = open_client.post(
        "/api/nodes", json={"label": "A1-S2 guess", "xyz": "1\n\nRu 0 0 0\n"}
    ).json()
    plan = upload(open_client, TS, fixture(TS), guess["id"])
    assert plan["mode"] == "planned"
    assert plan["suggested"]["label"] == "A1-S2 guess"
    result = commit(open_client, plan, target_node_id=guess["id"], **named(plan))
    assert result["node_id"] == guess["id"]
    replaced = node(open_client, guess["id"])
    assert replaced["atom_count"] == 91 and replaced["status"] == "done"
    history = open_client.get("/api/history", params={"record_id": guess["id"]}).json()
    change = next(e for e in history if e["field"] == "geometry")
    assert change["old_value"] == [["Ru", 0.0, 0.0, 0.0]] and change["source"] == "import"


def test_single_point_on_another_geometry_offers_derived_node(open_client):
    # T-ID-04, ID-7, FR-IMP-08
    water = g.output(g.step("#P B3LYP/6-31G(d) Opt", [g.WATER], converged=True))
    base = commit(open_client, upload(open_client, "w.out", water))["node_id"]
    other = g.single_point(g.moved(g.WATER, 0.05))
    plan = upload(open_client, "sp.out", other, base)
    assert plan["mode"] == "onto" and plan["derived_offered"]
    assert "W-GEOM" in {w["code"] for w in plan["warnings"]}
    assert [s["assignment"] for s in plan["steps"]] == ["derived"]

    declined = preview(open_client, plan, target_node_id=base, create_derived=False)
    assert declined["blockers"]  # nothing left to import without the derived node

    result = commit(open_client, plan, target_node_id=base)
    derived = node(open_client, result["derived_node_id"])
    assert derived["derived_from_id"] == base
    assert len(calculations(open_client, base)) == 1  # the original is unchanged


def test_possible_duplicate_asks_before_anything_is_merged(open_client):
    # T-ID-05, ID-8, FR-IMP-09, D47
    first = commit(open_client, upload(open_client, "a.out", g.single_point(g.WATER)))["node_id"]
    nearly = g.single_point(g.moved(g.WATER, 0.02), route="#P B3LYP/6-31G(d) SP")
    plan = upload(open_client, "b.out", nearly)
    assert [d["node_id"] for d in plan["duplicates"]] == [first]
    assert plan["blockers"]
    assert open_client.post(f"/api/imports/{plan['token']}/commit", json={}).status_code == 422

    as_new = commit(open_client, plan, duplicate_action="new")
    assert as_new["node_id"] != first
    assert len(open_client.get("/api/nodes").json()) == 2

    plan = upload(open_client, "c.out", g.single_point(g.WATER, route="#P B3LYP/6-31G(d) SP"))
    attached = commit(open_client, plan, duplicate_action="attach", duplicate_node_id=first)
    assert attached["node_id"] == first
    assert len(calculations(open_client, first)) == 2


def test_same_file_twice_shows_a_notice(open_client):
    # T-ID-06
    import_ts(open_client)
    plan = upload(open_client, f"copy of {TS}", fixture(TS))
    assert plan["already_imported"][0]["original_name"] == TS
    assert plan["already_imported"][0]["nodes"] == [TS_LABEL]


def test_frequency_job_added_later(open_client):
    # T-IMP-11, D52, FR-IMP-13: an opt+freq file onto a node that has no freq yet
    start = commit(open_client, upload(open_client, "w.out", g.single_point(g.WATER)))["node_id"]

    unchanged = upload(open_client, "of1.out", g.opt_freq(g.WATER, g.WATER), start)
    assert [s["assignment"] for s in unchanged["steps"]] == ["node", "node"]
    commit(open_client, unchanged, target_node_id=start)
    assert [c["type"] for c in calculations(open_client, start)] == [
        "single_point",
        "optimization",
        "frequency",
    ]

    moving = upload(open_client, "of2.out", g.opt_freq(g.WATER, g.moved(g.WATER, 0.03)), start)
    assert moving["derived_offered"]
    assert [s["assignment"] for s in moving["steps"]] == ["derived", "derived"]
    result = commit(open_client, moving, target_node_id=start)
    assert [c["type"] for c in calculations(open_client, result["derived_node_id"])] == [
        "optimization",
        "frequency",
    ]
    assert len(calculations(open_client, start)) == 3


def test_frequency_at_another_level_is_flagged(open_client):
    # FR-IMP-13: the preview flags a freq whose level differs from the geometry level
    opt = g.output(g.step("#P B3LYP/6-31G(d) Opt", [g.WATER], converged=True))
    start = commit(open_client, upload(open_client, "o.out", opt))["node_id"]
    freq = g.output(
        g.step("#P PBE1PBE/def2SVP Freq", [g.WATER], frequencies=[1600.0, 3700.0, 3800.0])
    )
    plan = upload(open_client, "f.out", freq, start)
    assert "FREQ-LEVEL" in {w["code"] for w in plan["warnings"]}


# ---------- FR-FILE ----------


def test_copy_survives_deleting_the_original(open_client, tmp_path):
    # T-OPS-02, FR-FILE-01, FR-FILE-04
    original = tmp_path / "cluster" / TS
    original.parent.mkdir()
    original.write_bytes((FIXTURES / TS).read_bytes())
    plan = open_client.post("/api/imports/from-path", json={"path": str(original)}).json()
    assert plan["origin"]["path"] == str(original.resolve())
    result = commit(open_client, plan, origin_device="cluster.example.org", **named(plan))
    original.unlink()

    source = calculations(open_client, result["node_id"])[0]["source_file"]
    assert source["exists"] and source["origin_device"] == "cluster.example.org"
    assert source["stored_path"].startswith("files/") and "\\" not in source["stored_path"]
    download = open_client.get(f"/api/source-files/{source['id']}/download")
    assert download.status_code == 200
    assert download.content == (FIXTURES / TS).read_bytes()
    # FR-FILE-02: the device name is offered again next time
    assert open_client.get("/api/settings").json()["last_device"] == "cluster.example.org"


def test_origin_metadata_is_editable_and_recorded(open_client):
    # FR-FILE-02
    node_id = import_ts(open_client)
    source = calculations(open_client, node_id)[0]["source_file"]
    response = open_client.patch(
        f"/api/source-files/{source['id']}", json={"origin_path": f"/cluster/work/{TS}"}
    )
    assert response.json()["origin_path"] == f"/cluster/work/{TS}"
    history = open_client.get("/api/history", params={"record_id": source["id"]}).json()
    assert history[0]["field"] == "origin_path"


def test_open_and_reveal_the_copy(open_client, monkeypatch):
    # FR-FILE-03
    node_id = import_ts(open_client)
    source = calculations(open_client, node_id)[0]["source_file"]
    launched = []
    from chembook3d.services import files

    monkeypatch.setattr(files, "launch", lambda path, reveal=False: launched.append((path, reveal)))
    open_client.post(f"/api/source-files/{source['id']}/open")
    open_client.post(f"/api/source-files/{source['id']}/open", params={"reveal": True})
    assert [p.name for p, _ in launched] == [TS, TS]
    assert [r for _, r in launched] == [False, True]


# ---------- FR-CALC-05 ----------


def test_level_edits_keep_the_parsed_values(open_client):
    # FR-CALC-05, P13
    node_id = import_ts(open_client)
    calc = calculations(open_client, node_id)[0]
    response = open_client.patch(f"/api/calculations/{calc['id']}", json={"solvent": "toluene"})
    edited = response.json()
    assert edited["composite_label"] == "Gaussian WB97XD/6-31+G(D) SMD(toluene)"
    assert edited["level_edited"] is True
    assert edited["parsed_level"]["solvent"] == "tetrahydrofuran"
    history = open_client.get("/api/history", params={"record_id": calc["id"]}).json()
    assert history[0]["field"] == "level"
    assert history[0]["old_value"] == TS_LEVEL


def test_geometry_level_of_a_single_point_can_be_set(open_client):
    # FR-CALC-04: taken from the node's optimization, or set by the user
    start = commit(open_client, upload(open_client, "w.out", g.single_point(g.WATER)))["node_id"]
    (sp,) = calculations(open_client, start)
    assert sp["geometry_level"] is None
    level = open_client.get("/api/levels").json()[0]
    other = open_client.patch(
        f"/api/calculations/{sp['id']}", json={"geometry_level_id": level["id"]}
    ).json()
    assert other["geometry_level"]["id"] == level["id"]


def test_records_in_database(open_client):
    node_id = import_ts(open_client)
    with open_client.app.state.investigation.sessions() as session:
        stored = session.get(Node, node_id)
        assert len(stored.calculations) == 2
        assert session.query(SourceFile).count() == 1
        assert all(c.source_file_id for c in session.query(Calculation))
