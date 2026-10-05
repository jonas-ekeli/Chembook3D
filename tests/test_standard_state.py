"""The 1 M standard state (D95, T-EN-15): an option in Settings, off by default, that adds
RT ln(V_m / 1 L mol⁻¹) to the free energy (G and G_qh, never E or H) of every node and free
species, at the G_qh temperature. With it off, every value is the 1 atm one of the files and
the reference script (D57); the tests in test_energies.py check those against the script."""

import math

import pytest

from chembook3d import thermochem
from chembook3d.units import GAS_CONSTANT
from tests.test_energies import KCAL, MINIMUM, import_file, level_key, settings, view
from tests.test_import import calculations, fixture
from tests.test_pathway import get, post
from tests.test_species import E, cycle, profile, svp  # noqa: F401

DZ = "Gaussian WB97XD/6-31+G(D) SMD(tetrahydrofuran)"


def shift(temperature: float = 298.15) -> float:
    return thermochem.standard_state_correction(temperature)


def test_the_correction_is_rt_ln_of_the_molar_volume():
    # 1 atm → 1 M at 298.15 K: RT ln(24.465) = 1.894 kcal/mol = 7.926 kJ/mol.
    assert shift() * KCAL == pytest.approx(1.89433, abs=1e-5)
    volume = GAS_CONSTANT * 298.15 / 101325.0 * 1000.0
    assert volume == pytest.approx(24.4654, abs=1e-4)
    rt = GAS_CONSTANT * 298.15 / 4184.0  # kcal/mol
    assert shift() * KCAL == pytest.approx(rt * math.log(volume), rel=1e-12)
    # It grows with the temperature, through RT and the molar volume both.
    assert shift(373.15) * KCAL == pytest.approx(
        GAS_CONSTANT * 373.15 / 4184.0 * math.log(GAS_CONSTANT * 373.15 / 101.325), rel=1e-12
    )
    with pytest.raises(ValueError):
        shift(0)


def test_the_setting_is_off_by_default_and_checked(open_client):
    found = get(open_client, "/settings")
    assert found["standard_state"] == "1 atm"
    assert found["standard_states"] == ["1 atm", "1 M"]
    assert open_client.put("/api/settings", json={"standard_state": "1 bar"}).status_code == 422
    assert settings(open_client, standard_state="1 M")["standard_state"] == "1 M"
    assert get(open_client, "/settings")["standard_state"] == "1 M"


def test_free_energies_of_a_node_include_the_correction_at_the_settings_temperature(
    open_client,
):
    node_id = import_file(open_client, MINIMUM, fixture(MINIMUM))["node_id"]
    key = level_key(open_client, DZ)
    before = {t: view(open_client, key, t)["values"][node_id] for t in ("E", "H", "G", "G_qh")}
    assert "standard_state" not in before["G_qh"]["details"]
    assert get(open_client, "/energies/options")["standard_state"] == "1 atm"

    settings(open_client, standard_state="1 M")
    assert get(open_client, "/energies/options")["standard_state"] == "1 M"
    after = {t: view(open_client, key, t)["values"][node_id] for t in ("E", "H", "G", "G_qh")}
    assert after["E"]["value"] == before["E"]["value"]
    assert after["H"]["value"] == before["H"]["value"]
    for energy_type in ("G", "G_qh"):
        assert after[energy_type]["value"] == pytest.approx(
            before[energy_type]["value"] + shift(), abs=1e-12
        )
        details = after[energy_type]["details"]
        assert details["standard_state"] == "1 M"
        assert details["standard_state_correction"] == pytest.approx(shift(), abs=1e-15)
        assert details["standard_state_temperature"] == 298.15
    # The G_qh correction itself is still the script's (shown in the calculation's details).
    freq = calculations(open_client, node_id)[-1]
    assert after["G_qh"]["details"]["correction"] == freq["quasi_harmonic"]["correction"]

    # D95: the temperature in Settings sets both the G_qh and the 1 M correction.
    settings(open_client, qh_temperature=350)
    hot = view(open_client, key, "G")["values"][node_id]
    assert hot["value"] == pytest.approx(before["G"]["value"] + shift(350), abs=1e-12)
    assert hot["details"]["standard_state_temperature"] == 350


def test_it_cancels_on_unimolecular_steps_and_counts_each_species(open_client, cycle):  # noqa: F811
    # A + propene → B: one molecule fewer, so ΔG falls by one correction; B → TS is unchanged;
    # TS → C + ethylene: one more, so ΔG rises by one.
    cycle.energies()
    path = [cycle.path("A", "B", "TS", "C")]
    before = [
        p["relative"]
        for p in profile(open_client, path, cycle.ids["A"], "G")["profiles"][0]["points"]
    ]
    settings(open_client, standard_state="1 M")
    found = profile(open_client, path, cycle.ids["A"], "G")
    assert found["standard_state"] == "1 M"
    after = [p["relative"] for p in found["profiles"][0]["points"]]
    assert after[0] == 0.0
    assert after[1] == pytest.approx(before[1] - shift(), abs=1e-12)
    assert after[2] == pytest.approx(before[2] - shift(), abs=1e-12)
    assert after[3] == pytest.approx(before[3], abs=1e-12)
    assert after[1] == pytest.approx(E["B"] - E["A"] - E["propene"] - 0.02 - shift(), abs=1e-9)

    edges = view(open_client, svp(open_client), "G")["edges"]
    assert edges[cycle.edges[("B", "TS")]]["delta"] == pytest.approx(E["TS"] - E["B"], abs=1e-9)
    assert edges[cycle.edges[("A", "B")]]["delta"] == pytest.approx(after[1], abs=1e-12)
    # E is never corrected.
    e_path = profile(open_client, path, cycle.ids["A"], "E")["profiles"][0]["points"]
    assert e_path[1]["relative"] == pytest.approx(E["B"] - E["A"] - E["propene"], abs=1e-9)


def test_the_energy_table_names_the_standard_state(open_client, cycle):  # noqa: F811
    cycle.energies()
    settings(open_client, standard_state="1 M", energy_unit="kcal/mol")
    body = {
        "paths": [cycle.path("A", "B")],
        "reference_id": cycle.ids["A"],
        "level": svp(open_client),
        "type": "G",
    }
    table = post(open_client, "/energies/table", body, status=200)
    assert table["columns"][6:8] == [
        "G 1 M (298.15 K) (hartree)",
        "G_qh 298.15 K 100 cm-1 1 M (hartree)",
    ]
    expected = (E["B"] - E["A"] - E["propene"] - 0.02 - shift()) * KCAL
    assert table["rows"][1][-2] == f"{expected:.2f}"
