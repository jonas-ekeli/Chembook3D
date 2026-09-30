"""Import of ORCA, xTB and CREST files through the API (FR-IMP-06, 07, 10, 11; T-IMP-09,
T-IMP-10), and a mixed-program investigation (phase 5 exit)."""

import pytest

from chembook3d.models import GroupNode, Node
from tests.test_energies import level_key, options, view
from tests.test_import import calculations, commit, node, preview, upload
from tests.test_orca_xtb_parsers import read

KCAL = 627.5094740631


def import_new(client, folder: str, name: str, **extra) -> dict:
    plan = upload(client, name, read(folder, name))
    assert plan["blockers"] == [], plan["blockers"]
    return commit(client, plan, **extra)


# ---------- ORCA and xTB (T-IMP-10) ----------


def test_orca_frequency_job_import(open_client):
    plan = upload(open_client, "dvb_ir.out", read("orca", "dvb_ir.out"))
    assert (plan["program"], plan["program_version"]) == ("ORCA", "6.0.1")
    assert plan["mode"] == "new" and plan["blockers"] == []
    (step,) = plan["steps"]
    assert step["level_label"] == "ORCA B3LYP/STO-3G" and step["assignment"] == "node"
    assert step["frequency_count"] == 54 and step["imaginary_count"] == 0
    assert plan["suggested"] == {"label": "dvb_ir", "role": "minimum", "status": "done"}

    node_id = commit(open_client, plan)["node_id"]
    (calc,) = calculations(open_client, node_id)
    assert calc["program"] == "ORCA" and calc["type"] == "frequency"
    assert calc["result"]["g"] == -381.91114546
    assert calc["level"]["label"] == "ORCA B3LYP/STO-3G"
    # G uses the printed correction; G_qh is computed from the frequencies (D56).
    key = level_key(open_client, "ORCA B3LYP/STO-3G")
    assert options(open_client)["ORCA B3LYP/STO-3G"] == ["E", "H", "G", "G_qh"]
    g = view(open_client, key, "G")["values"][node_id]["value"]
    # E from the final single point energy plus ORCA's G−E(el), as for Gaussian (EN-4).
    assert g == pytest.approx(-382.055107107616 + 0.14396165, abs=1e-9)
    g_qh = view(open_client, key, "G_qh")["values"][node_id]["value"]
    assert g_qh == pytest.approx(-382.055107107616 + 0.14402183, abs=1e-7)


def test_orca_single_point_attaches_to_the_same_geometry(open_client):
    node_id = import_new(open_client, "orca", "dvb_ir.out")["node_id"]
    plan = upload(open_client, "dvb_sp_un_dft.out", read("orca", "dvb_sp_un_dft.out"), node_id)
    assert plan["mode"] == "onto"
    assert plan["steps"][0]["assignment"] == "node"
    # The cation's charge and multiplicity differ from the node's: W-CHG.
    assert any(w["code"] == "W-CHG" for w in plan["warnings"])


def test_xtb_file_without_coordinates_needs_a_node(open_client):
    # A15: an xTB single point prints no geometry, so it cannot make a node by itself.
    plan = upload(open_client, "dvb_sp.out", read("xtb", "dvb_sp.out"))
    assert plan["program"] == "xTB"
    assert any("import the file onto the node" in b for b in plan["blockers"])


def test_xtb_optimization_then_frequency_job_onto_it(open_client):
    opt = upload(open_client, "dvb_opt.out", read("xtb", "dvb_opt.out"))
    assert opt["steps"][0]["level_label"] == "xTB GFN2-xTB"
    node_id = commit(open_client, opt)["node_id"]

    # The frequency job ran on the optimized geometry, which its output does not print.
    plan = upload(open_client, "dvb_ir.out", read("xtb", "dvb_ir.out"), node_id)
    assert plan["mode"] == "onto" and plan["blockers"] == []
    assert plan["steps"][0]["assignment"] == "node"
    assert any(w["code"] == "NO-COORD" for w in plan["warnings"])
    commit(open_client, plan, target_node_id=node_id)
    freq = next(c for c in calculations(open_client, node_id) if c["type"] == "frequency")
    assert freq["parse_warnings"] == ["geometry"]

    key = level_key(open_client, "xTB GFN2-xTB")
    assert options(open_client)["xTB GFN2-xTB"] == ["E", "H", "G"]
    g = view(open_client, key, "G")["values"][node_id]
    # The xTB optimization's energy plus the frequency job's printed G(RRHO) contribution.
    assert g["value"] == pytest.approx(-26.438242468348 + 0.127242830965, abs=1e-9)
    # xTB prints no mass or rotational constants, so G_qh is not available (A15).
    g_qh = view(open_client, key, "G_qh")["values"][node_id]
    assert g_qh["value"] is None and g_qh["code"] == "W-PARSE"


def test_mixed_program_composite_level(open_client):
    # Phase 5 exit, on the files at hand: an xTB single point on an ORCA-optimized node is
    # written with the ORCA geometry level (D31, FR-CALC-04).
    plan = upload(open_client, "dvb_gopt.out", read("orca", "dvb_gopt.out"))
    node_id = commit(open_client, plan)["node_id"]
    xtb_plan = upload(open_client, "dvb_sp.out", read("xtb", "dvb_sp.out"), node_id)
    assert xtb_plan["steps"][0]["geometry_level_label"] == "ORCA B3LYP/STO-3G"
    commit(open_client, xtb_plan, target_node_id=node_id)
    labels = options(open_client)
    assert "xTB GFN2-xTB // ORCA B3LYP/STO-3G" in labels
    assert labels["xTB GFN2-xTB // ORCA B3LYP/STO-3G"] == ["E"]


def test_unknown_file_is_refused(open_client):
    response = open_client.post(
        "/api/imports", params={"filename": "notes.txt"}, content=b"just some notes\n"
    )
    assert response.status_code == 422
    assert "Gaussian, ORCA or xTB" in response.json()["detail"]


# ---------- CREST (T-IMP-09, FR-IMP-10, FR-IMP-11) ----------


def crest_plan(client, **extra) -> dict:
    plan = upload(client, "crest_conformers.xyz", read("crest", "crest_conformers.xyz"))
    return preview(client, plan, **extra) if extra else plan


def test_crest_preview_ticks_the_lowest_n(open_client):
    plan = crest_plan(open_client)
    assert plan["kind"] == "ensemble" and plan["program"] == "CREST"
    ensemble = plan["ensemble"]
    assert len(ensemble["conformers"]) == 74 and ensemble["count"] == 10
    assert ensemble["level_label"] == "CREST GFN2-xTB"
    listed = ensemble["conformers"]
    assert [c["energy"] for c in listed] == sorted(c["energy"] for c in listed)
    assert [c["selected"] for c in listed] == [True] * 10 + [False] * 64
    assert listed[0]["relative"] == 0.0
    assert listed[1]["relative"] * KCAL == pytest.approx((-22.68040256 + 22.68187449) * KCAL)
    # N comes from the settings (FR-SET-01).
    open_client.put("/api/settings", json={"crest_count": 5})
    assert crest_plan(open_client)["ensemble"]["selected_count"] == 5


def test_crest_import_as_group(open_client):
    # T-IMP-09: N = 10 gives a group of 10; unticking 2 gives 8.
    plan = crest_plan(open_client)
    ticked = [c["index"] for c in plan["ensemble"]["conformers"] if c["selected"]]
    unticked = ticked[:8]
    plan = preview(open_client, plan, conformers=unticked, charge=0, multiplicity=1)
    assert plan["ensemble"]["selected_count"] == 8 and plan["blockers"] == []
    result = commit(open_client, plan, conformers=unticked, charge=0, multiplicity=1, label="Ens")
    assert result["group_id"] and result["node_id"] is None
    assert len(result["calculation_ids"]) == 8

    canvas = open_client.get("/api/canvas").json()
    (group,) = canvas["groups"]
    assert group["label"] == "Ens" and group["representative_id"] is None
    assert len(group["member_ids"]) == 8
    member = node(open_client, group["member_ids"][0])
    assert member["label"] == f"Ens-{unticked[0]}"
    assert (member["charge"], member["multiplicity"], member["status"]) == (0, 1, "done")
    (calc,) = calculations(open_client, member["id"])
    assert calc["type"] == "conformer_search" and calc["program"] == "CREST"
    assert calc["result"]["energy"] == -22.68187449

    # The members can be ranked by their CREST energies (FR-GRP-03).
    key = level_key(open_client, "CREST GFN2-xTB")
    values = view(open_client, key, "E")["values"]
    assert values[group["member_ids"][1]]["value"] == -22.68040256


def test_crest_members_can_be_removed_after_import(open_client):
    # FR-IMP-11: removing a member deletes that node (the dialog confirms first).
    plan = crest_plan(open_client)
    group_id = commit(open_client, plan)["group_id"]
    group = next(g for g in open_client.get("/api/canvas").json()["groups"] if g["id"] == group_id)
    removed = group["member_ids"][3]
    assert open_client.delete(f"/api/nodes/{removed}").status_code in (200, 204)
    group = next(g for g in open_client.get("/api/canvas").json()["groups"] if g["id"] == group_id)
    assert len(group["member_ids"]) == 9 and removed not in group["member_ids"]


def test_crest_needs_a_ticked_conformer(open_client):
    plan = crest_plan(open_client, conformers=[])
    assert plan["blockers"] == ["Tick at least one conformer to import"]


def test_crest_cancel_writes_nothing(open_client):
    plan = crest_plan(open_client)
    assert open_client.delete(f"/api/imports/{plan['token']}").status_code == 204
    investigation = open_client.app.state.investigation
    with investigation.sessions.begin() as session:
        assert session.query(Node).count() == 0 and session.query(GroupNode).count() == 0
