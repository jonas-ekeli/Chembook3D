"""G's own temperature and the standard state of free species (D160, FR-EN-15, T-EN-34…38).

- G is the file's, at its frequency job's temperature, so its 1 M shift is at that temperature
  and W-TEMP says where H or G values at another temperature than G_qh's meet.
- The standard state is always named, 1 atm included.
- W-STDSTATE: free species joining or leaving at 1 atm while the level has a solvation model.
- A free species may have its own concentration (1 M, neat solvent, any molarity), added as
  RT ln(c / 1 M) on top of the 1 M shift wherever it balances a pathway.
"""

import math

import pytest

from chembook3d import thermochem
from chembook3d.services import concentrations
from chembook3d.units import GAS_CONSTANT
from tests.test_energies import KCAL, SVP, add_energies, settings, view
from tests.test_pathway import get, history, patch, post
from tests.test_species import Cycle, E, cycle  # noqa: F401

SMD = {**SVP, "solvation_model": "SMD", "solvent": "tetrahydrofuran"}


def shift(temperature: float = 298.15) -> float:
    return thermochem.standard_state_correction(temperature)


def rt_ln(molarity: float, temperature: float = 298.15) -> float:
    return GAS_CONSTANT * temperature * math.log(molarity) / (4184.0 * KCAL)


def key_of(client, solvated: bool = False) -> str:
    levels = get(client, "/energies/options")["levels"]
    return next(o["key"] for o in levels if ("SMD" in o["label"]) == solvated)


def profile(client, paths, reference_id, energy_type, level) -> dict:
    body = {"paths": paths, "reference_id": reference_id, "level": level, "type": energy_type}
    return post(client, "/energies/profile", body, status=200)


def solvated(cycle: Cycle, temperatures: dict[str, float] | None = None) -> str:  # noqa: F811
    for name, energy in E.items():
        temperature = (temperatures or {}).get(name, 298.15)
        add_energies(cycle.client, cycle.ids[name], energy, level=SMD, temperature=temperature)
    return key_of(cycle.client, solvated=True)


# ---------- T-EN-34: G at its own job temperature ----------


def test_g_takes_its_1_m_shift_at_its_job_temperature(open_client, cycle):  # noqa: F811
    # M2: a frequency job at 353.15 K, the investigation at 298.15 K and 1 M.
    level = solvated(cycle, {"A": 353.15})
    a = cycle.ids["A"]
    settings(open_client, standard_state="1 M")
    g = view(open_client, level, "G")["values"][a]
    assert g["value"] == pytest.approx(E["A"] + 0.02 + shift(353.15), abs=1e-12)
    assert shift(353.15) * KCAL == pytest.approx(2.362, abs=1e-3)  # not 1.894 at 298.15 K
    assert g["details"]["standard_state_temperature"] == 353.15
    assert g["details"]["job_temperature"] == 353.15
    assert g["check"]["code"] == "W-TEMP"
    assert (
        g["check"]["message"] == "G is at the frequency job's 353.15 K, not at 298.15 K like G_qh"
    )
    assert view(open_client, level, "H")["values"][a]["check"]["code"] == "W-TEMP"
    # G_qh is recomputed at the G_qh temperature, so it has no W-TEMP.
    g_qh = view(open_client, level, "G_qh")["values"][a]
    assert g_qh["details"]["standard_state_temperature"] == 298.15
    assert g_qh["check"] is None
    assert view(open_client, level, "E")["values"][a]["check"] is None
    # A node at 298.15 K has none.
    assert view(open_client, level, "G")["values"][cycle.ids["B"]]["check"] is None


# ---------- T-EN-35: values at different temperatures meet ----------


def test_profiles_tables_and_the_view_say_when_temperatures_differ(open_client, cycle):  # noqa: F811
    level = solvated(cycle, {"TS": 353.15})
    settings(open_client, energy_unit="kcal/mol", standard_state="1 M")
    option = next(o for o in get(open_client, "/energies/options")["levels"] if o["key"] == level)
    assert option["job_temperatures"] == [298.15, 353.15]

    path = [cycle.path("A", "B", "TS", "C")]
    found = profile(open_client, path, cycle.ids["A"], "G", level)
    assert found["job_temperatures"] == [298.15, 353.15]
    assert {
        "code": "W-TEMP",
        "message": "G values here come from frequency jobs at 298.15 K and 353.15 K; G_qh is "
        "at 298.15 K",
    } in found["notes"]
    ts = found["profiles"][0]["points"][2]
    assert ts["check"]["code"] == "W-TEMP"
    # G_qh values are all at the G_qh temperature: no note.
    qh = profile(open_client, path, cycle.ids["A"], "G_qh", level)
    assert qh["job_temperatures"] == [] and qh["notes"] == []

    body = {"paths": path, "reference_id": cycle.ids["A"], "level": level, "type": "G"}
    table = post(open_client, "/energies/table", body, status=200)
    assert table["columns"][6:8] == [
        "G 1 M (298.15 K, 353.15 K) (hartree)",
        "G_qh 298.15 K 100 cm-1 1 M (hartree)",
    ]
    assert table["columns"][9] == "ΔG 1 M (kcal/mol)"
    ts_row = next(row for row in table["rows"] if row[0] == "TS")
    assert "G is at the frequency job's 353.15 K" in ts_row[-1]
    assert [n["code"] for n in table["notes"]] == ["W-TEMP"]
    csv = open_client.post("/api/energies/table.csv", json=body).content.decode("utf-8-sig")
    assert "G is at the frequency job's 353.15 K" in csv

    notes = view(open_client, level, "G")["notes"]
    assert [n["code"] for n in notes] == ["W-TEMP"]
    assert view(open_client, level, "G_qh")["notes"] == []

    # All at 353.15 K, against G_qh at 298.15 K: one temperature, still W-TEMP.
    settings(open_client, qh_temperature=353.15)
    assert [n["code"] for n in view(open_client, level, "G")["notes"]] == ["W-TEMP"]


# ---------- T-EN-36: the standard state is always named ----------


def test_the_table_names_1_atm(open_client, cycle):  # noqa: F811
    cycle.energies()
    settings(open_client, energy_unit="kcal/mol")
    level = key_of(open_client)
    body = {"paths": [cycle.path("A", "B")], "reference_id": cycle.ids["A"], "level": level}
    table = post(open_client, "/energies/table", {**body, "type": "G_qh"}, status=200)
    assert table["columns"][6:8] == [
        "G 1 atm (298.15 K) (hartree)",
        "G_qh 298.15 K 100 cm-1 1 atm (hartree)",
    ]
    assert table["columns"][9] == "ΔG_qh 1 atm (kcal/mol)"
    e_table = post(open_client, "/energies/table", {**body, "type": "E"}, status=200)
    assert e_table["columns"][9] == "ΔE (kcal/mol)"
    # Without a solvation model, no W-STDSTATE.
    assert profile(open_client, [cycle.path("A", "B")], cycle.ids["A"], "G", level)["notes"] == []


# ---------- T-EN-37: W-STDSTATE ----------


def test_species_at_1_atm_in_a_solvation_model_are_noted(open_client, cycle):  # noqa: F811
    level = solvated(cycle)
    settings(open_client, energy_unit="kcal/mol")
    path = [cycle.path("A", "B", "TS", "C")]
    found = profile(open_client, path, cycle.ids["A"], "G", level)
    assert found["notes"] == [
        {
            "code": "W-STDSTATE",
            "message": "Free species join or leave here and the calculations use "
            "SMD(tetrahydrofuran), but G is at the 1 atm gas-phase standard state: each molecule "
            "gained or lost is 1.89 kcal/mol off 1 M in solution (298.15 K). Choose 1 M for "
            "this investigation in Settings, or give the free species a concentration.",
        }
    ]
    assert [n["code"] for n in view(open_client, level, "G")["notes"]] == ["W-STDSTATE"]
    assert profile(open_client, path, cycle.ids["A"], "E", level)["notes"] == []
    # A path without species: nothing to note.
    assert profile(open_client, [cycle.path("B", "TS")], cycle.ids["B"], "G", level)["notes"] == []
    # Both species with their own concentration: nothing left at 1 atm.
    for name in ("propene", "ethylene"):
        patch(open_client, f"/nodes/{cycle.ids[name]}", {"concentration": {"kind": "1 M"}})
    assert profile(open_client, path, cycle.ids["A"], "G", level)["notes"] == []
    patch(open_client, f"/nodes/{cycle.ids['ethylene']}", {"concentration": None})
    assert profile(open_client, path, cycle.ids["A"], "G", level)["notes"]
    # At 1 M, nothing to note.
    settings(open_client, standard_state="1 M")
    assert profile(open_client, path, cycle.ids["A"], "G", level)["notes"] == []


# ---------- T-EN-38: a free species' own concentration ----------


def test_a_species_concentration_is_added_where_it_balances(open_client, cycle):  # noqa: F811
    level = solvated(cycle)
    propene = cycle.ids["propene"]
    path = [cycle.path("A", "B", "TS", "C")]

    def relative(energy_type="G") -> list[float]:
        found = profile(open_client, path, cycle.ids["A"], energy_type, level)
        return [p["relative"] for p in found["profiles"][0]["points"]]

    before, before_qh, before_e = relative(), relative("G_qh"), relative("E")
    neat = patch(
        open_client, f"/nodes/{propene}", {"concentration": {"kind": "neat", "solvent": "THF"}}
    )
    assert neat["concentration"] == {"kind": "neat", "solvent": "THF", "molarity": 12.3}
    # propene joins at B: its G is at 12.3 M, so B and everything after it fall by its shift.
    added = shift() + rt_ln(12.3)
    assert rt_ln(12.3) * KCAL == pytest.approx(1.49, abs=0.005)
    after = relative()
    assert after[0] == 0.0
    for i in (1, 2, 3):
        assert after[i] == pytest.approx(before[i] - added, abs=1e-12)
    assert relative("G_qh")[1] == pytest.approx(before_qh[1] - added, abs=1e-12)
    assert relative("E") == before_e  # E never changes
    g = view(open_client, level, "G")["values"][propene]
    assert g["details"]["standard_state"] == "neat THF, 12.3 M"
    assert g["details"]["standard_state_correction"] == pytest.approx(added, abs=1e-15)
    # The table's free species column names it; node cards and edges use it too.
    body = {"paths": path, "reference_id": cycle.ids["A"], "level": level, "type": "G"}
    rows = post(open_client, "/energies/table", body, status=200)["rows"]
    assert rows[1][8] == "− propene (neat THF, 12.3 M)"
    edges = view(open_client, level, "G")["edges"]
    assert edges[cycle.edges[("A", "B")]]["delta"] == pytest.approx(after[1], abs=1e-12)
    card = get(open_client, f"/energies/view?level={level}&type=G&reference={cycle.ids['A']}")[
        "relative"
    ][cycle.ids["B"]]
    assert card["value"] == pytest.approx(after[1], abs=1e-12)
    assert card["species"][0]["concentration"] == "neat THF, 12.3 M"

    # Water, a custom molarity, 1 M; the investigation at 1 M changes nothing for it.
    patch(open_client, f"/nodes/{propene}", {"concentration": {"kind": "neat", "solvent": "water"}})
    assert rt_ln(55.4) * KCAL == pytest.approx(2.38, abs=0.005)
    assert relative()[1] == pytest.approx(before[1] - shift() - rt_ln(55.4), abs=1e-12)
    patch(open_client, f"/nodes/{propene}", {"concentration": {"kind": "molar", "molarity": 0.1}})
    assert relative()[1] == pytest.approx(before[1] - shift() - rt_ln(0.1), abs=1e-12)
    patch(open_client, f"/nodes/{propene}", {"concentration": {"kind": "1 M"}})
    assert relative()[1] == pytest.approx(before[1] - shift(), abs=1e-12)
    settings(open_client, standard_state="1 M")
    at_1_m = relative()
    assert at_1_m[1] == pytest.approx(before[1] - shift(), abs=1e-12)
    # ethylene (unset) follows the investigation's 1 M: at C the two shifts cancel.
    assert at_1_m[3] == pytest.approx(before[3], abs=1e-12)

    # Recorded in the history; refused on a node, for an unknown solvent or a bad molarity.
    entries = [
        e
        for e in history(open_client)
        if e["record_id"] == propene and e["field"] == "concentration"
    ]
    assert len(entries) == 4
    assert entries[-1]["old_value"] is None
    assert entries[-1]["new_value"] == {"kind": "neat", "solvent": "THF", "molarity": 12.3}
    for refused in (
        {"kind": "neat", "solvent": "bromoform"},
        {"kind": "molar", "molarity": 0},
        {"kind": "2 M"},
    ):
        response = open_client.patch(f"/api/nodes/{propene}", json={"concentration": refused})
        assert response.status_code == 422, refused
    response = open_client.patch(
        f"/api/nodes/{cycle.ids['A']}", json={"concentration": {"kind": "1 M"}}
    )
    assert response.status_code == 422
    assert "Only a free species" in response.json()["detail"]
    # A neat solvent not in the table, with its molarity.
    own = {"kind": "neat", "solvent": "sulfolane", "molarity": 10.5}
    assert patch(open_client, f"/nodes/{propene}", {"concentration": own})["concentration"] == own
    # Settings lists the table.
    solvents = get(open_client, "/settings")["solvents"]
    assert {"name": "THF", "molarity": 12.3} in solvents
    assert len(solvents) == len(concentrations.SOLVENTS)


def test_a_species_made_a_node_loses_its_concentration(open_client, cycle):  # noqa: F811
    butene = cycle.ids["butene"]
    patch(open_client, f"/nodes/{butene}", {"concentration": {"kind": "1 M"}})
    response = open_client.put(f"/api/nodes/{butene}/kind", json={"kind": "node"})
    assert response.status_code == 200, response.text
    assert get(open_client, f"/nodes/{butene}")["concentration"] is None


def test_the_solvent_table_matches_solvation_model_names():
    assert concentrations.solvent("Tetrahydrofuran")["name"] == "THF"
    assert concentrations.solvent("dichloromethane")["molarity"] == 15.6
    assert concentrations.solvent("unknown") is None
    assert concentrations.describe(None, "1 M") == "1 M"
    assert concentrations.describe({"kind": "molar", "molarity": 0.5}) == "0.5 M"


def test_analyses_note_temperatures_and_the_standard_state_once(open_client):
    # T-EN-38: a turnover through free species at 1 atm in SMD gets W-STDSTATE in its notes;
    # with its own temperature, G read at 298.15 K gets one W-TEMP note, not one per point.
    from tests import test_turnover as tof

    found = tof.Cycle(open_client)
    offsets = {"A": 0.0, "TS1": 20.0, "B": 5.0, "TS2": 15.0}
    for name, kcal in offsets.items():
        base = tof.Cycle.E0 + (0.0 if name == "A" else -10.0) + kcal / KCAL
        add_energies(open_client, found.ids[name], base, level=SMD)
    add_energies(open_client, found.ids["substrate"], -10.0, level=SMD)
    add_energies(open_client, found.ids["product"], -10.0 - 10 / KCAL, level=SMD)
    level = key_of(open_client, solvated=True)
    created = tof.turnover(
        open_client, found.path(*tof.CYCLE), level=level, energy_type="G", temperature=350
    )
    result = tof.result(open_client, created["id"])
    assert result["job_temperatures"] == [298.15]
    notes = result["notes"]
    assert sum("W-TEMP" in n for n in notes) == 1
    assert not any("frequency job's" in n for n in notes)
    assert any(n.startswith("Free species join or leave here") for n in notes)
