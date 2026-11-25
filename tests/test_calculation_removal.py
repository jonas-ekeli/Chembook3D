"""Removing one calculation (D259, FR-CALC-10): T-IMP-76, T-IMP-77.

A calculation goes with those stored as sitting on it (D255), confirmed by the user in the
app; the node keeps its coordinates and its other calculations."""

from pathlib import Path

from tests import gaussian_text as g
from tests.test_import import calculations, upload
from tests.test_live import ORIGIN, ask
from tests.test_node_geometries import DFT, LOW_END, dft_node, geometry_is, low_result, onto
from tests.test_pre_optimization import DFT_END, option_labels

SP_ROUTE = "#P B3LYP/def2SVP SP"


def folder_of(client) -> Path:
    return Path(client.get("/api/investigation").json()["folder"])


def species_with_two_geometries(client) -> str:
    """A DFT free species (opt + freq in one file) with an xTB optimization beside it and a
    single point at each geometry."""
    species = dft_node(client)
    onto(client, upload(client, "S_xtb.json", low_result(), species))
    onto(client, upload(client, "S_sp_dft.log", g.single_point(DFT_END, SP_ROUTE), species))
    onto(client, upload(client, "S_sp_low.log", g.single_point(LOW_END, SP_ROUTE), species))
    return species


def by_label(client, node_id: str) -> dict[tuple[str, str], dict]:
    return {(c["type"], c["composite_label"]): c for c in calculations(client, node_id)}


def test_an_optimization_goes_with_the_calculations_on_its_geometry(open_client):
    """T-IMP-76: removing the xTB optimization beside a DFT geometry also removes the single
    point run at it; the DFT calculations, the node's coordinates and the other single point
    stay, the xTB files' copies go once nothing comes from them, and the node's history says
    what went."""
    species = species_with_two_geometries(open_client)
    found = by_label(open_client, species)
    xtb = found[("optimization", "xTB GFN2-xTB")]
    at_low = found[("single_point", "Gaussian B3LYP/DEF2SVP // xTB GFN2-xTB")]
    copies = {
        c["source_file"]["original_name"]: folder_of(open_client) / c["source_file"]["stored_path"]
        for c in calculations(open_client, species)
    }
    assert all(p.is_file() for p in copies.values())

    preview = open_client.get(f"/api/calculations/{xtb['id']}/removal")
    assert preview.status_code == 200, preview.text
    preview = preview.json()
    assert preview["calculation"]["id"] == xtb["id"]
    assert [c["id"] for c in preview["linked"]] == [at_low["id"]]
    assert sorted(preview["files"]) == ["S_sp_low.log", "S_xtb.json"]
    assert len(calculations(open_client, species)) == 5  # the preview changes nothing

    removed = open_client.delete(f"/api/calculations/{xtb['id']}")
    assert removed.status_code == 200, removed.text
    left = sorted(by_label(open_client, species))
    assert left == [
        ("frequency", DFT),
        ("optimization", DFT),
        ("single_point", f"Gaussian B3LYP/DEF2SVP // {DFT}"),
    ]
    assert geometry_is(open_client, species, DFT_END)
    assert [
        x["level_label"] for x in open_client.get(f"/api/nodes/{species}/geometries").json()
    ] == [DFT]
    assert not copies["S_xtb.json"].exists() and not copies["S_sp_low.log"].exists()
    assert copies["S_dft.log"].is_file() and copies["S_sp_dft.log"].is_file()
    assert "Gaussian B3LYP/DEF2SVP // xTB GFN2-xTB" not in option_labels(open_client)

    (entry,) = [
        e
        for e in open_client.get("/api/history", params={"record_id": species}).json()
        if e["action"] == "remove_calculations"
    ]
    assert entry["old_value"]["calculation"]["level"] == "xTB GFN2-xTB"
    assert [c["id"] for c in entry["old_value"]["linked"]] == [at_low["id"]]
    assert open_client.get(f"/api/calculations/{xtb['id']}/removal").status_code == 404


def test_one_step_of_a_file_goes_and_the_user_confirms_claude_s_ask(open_client):
    """T-IMP-77: removing the frequency step of an opt+freq file keeps the file's copy for its
    optimization; Claude's remove_calculation waits for the user's confirm in the app, and the
    file's copy goes with its last calculation."""
    species = dft_node(open_client)
    found = by_label(open_client, species)
    freq, opt = found[("frequency", DFT)], found[("optimization", DFT)]
    copy = folder_of(open_client) / opt["source_file"]["stored_path"]

    asked = ask(open_client, "remove_calculation", calculation_id=freq["id"])
    assert asked.status_code == 201, asked.text
    request = asked.json()
    assert request["summary"] == f"Remove the frequency at {DFT} from “S”"
    assert len(calculations(open_client, species)) == 2  # nothing yet
    url = f"/api/confirmations/{request['id']}/answer"
    done = open_client.post(url, json={"confirm": True}, headers=ORIGIN).json()
    assert done["status"] == "done", done
    assert [c["type"] for c in calculations(open_client, species)] == ["optimization"]
    assert copy.is_file()

    asked = ask(open_client, "remove_calculation", calculation_id=opt["id"]).json()
    assert asked["summary"] == (
        f"Remove the optimization at {DFT} from “S”; the copy of S_dft.log goes too"
    )
    url = f"/api/confirmations/{asked['id']}/answer"
    assert open_client.post(url, json={"confirm": True}, headers=ORIGIN).json()["status"] == "done"
    assert calculations(open_client, species) == []
    assert geometry_is(open_client, species, DFT_END)  # the node keeps its coordinates
    assert not copy.exists()
    assert ask(open_client, "remove_calculation", calculation_id="missing").status_code == 404

    other = dft_node(open_client)
    opt = by_label(open_client, other)[("optimization", DFT)]
    preview = open_client.get(f"/api/calculations/{opt['id']}/removal").json()
    assert [c["type"] for c in preview["linked"]] == ["frequency"]  # sits on its geometry
    assert open_client.delete(f"/api/calculations/{opt['id']}").status_code == 200
    assert calculations(open_client, other) == []
