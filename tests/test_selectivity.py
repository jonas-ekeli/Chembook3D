"""Selectivity (D83, FR-SEL-01…06, T-SEL-*): ΔΔG‡ and the predicted ratio from competing TSs.
The expected numbers are written out by hand from ratio = e^(−ΔΔG‡/RT)."""

import math

import pytest

from chembook3d import thermochem
from chembook3d.units import GAS_CONSTANT, J_PER_MOL_HARTREE
from tests import gaussian_text as g
from tests.test_energies import import_file, level_key, settings
from tests.test_pathway import get, history, node, patch, post

KCAL = 627.5094740631
R = GAS_CONSTANT / J_PER_MOL_HARTREE  # hartree/K
LEVEL = "Gaussian B3LYP/6-31G(D)"
E0 = -76.4089
# The synthetic water frequency job (tests/gaussian_text.py), for G_qh by hand.
WATER_FREQ = {
    "frequencies": [1650.0, 3700.0, 3800.0],
    "molecular_mass": 18.01056,
    "multiplicity": 1,
    "rotational_temperatures": [40.05, 20.12, 13.39],
    "symmetry_number": 2,
}


def energy(kcal: float) -> float:
    """E0 + kcal as the output file prints it (8 decimals)."""
    return round(E0 + kcal / KCAL, 8)


def gap(kcal: float) -> float:
    """The ΔG in hartree between TSs written at E0 + kcal and at E0."""
    return energy(kcal) - energy(0.0)


def ts(client, label: str, kcal: float, shift: float) -> str:
    """A TS (water, so all have the same atoms) at E0 + kcal, with the same frequencies, so
    ΔG = ΔE. `shift` keeps the geometries apart."""
    atoms = g.moved(g.WATER, shift)
    node_id = import_file(client, f"{label}.out", g.opt_freq(atoms, atoms, energy(kcal)))["node_id"]
    patch(client, f"/nodes/{node_id}", {"label": label, "role": "transition_state"})
    return node_id


def selectivity(client, outcomes: dict[str, list[str]], **fields) -> dict:
    body = {
        "name": fields.pop("name", "Facial"),
        "level": level_key(client, LEVEL),
        "outcomes": [{"name": n, "members": m} for n, m in outcomes.items()],
        **fields,
    }
    return post(client, "/selectivities", body)


def result(client, selectivity_id: str) -> dict:
    return get(client, f"/selectivities/{selectivity_id}/result")


def by_name(found: dict) -> dict[str, dict]:
    return {o["name"]: o for o in found["outcomes"]}


def test_two_transition_states_give_the_boltzmann_ratio(open_client):
    # T-SEL-01, S5: ΔΔG‡ = RT ln 10 at 298.15 K (1.364 kcal/mol) is a 10:1 ratio, ee 81.8 %
    # (to the 8 decimals of the file's energies).
    kcal = R * 298.15 * math.log(10) * KCAL
    ddg = gap(kcal)
    r = ts(open_client, "TS-R", 0.0, 0.0)
    s = ts(open_client, "TS-S", kcal, 0.05)
    created = selectivity(open_client, {"R": [r], "S": [s]})
    assert created["energy_type"] == "G_qh" and created["conformers"] == "boltzmann"
    assert created["temperature"] is None and created["excess"] == "ee"

    found = result(open_client, created["id"])
    assert found["status"] == "ok" and found["message"] is None
    assert found["temperature"] == 298.15 and found["temperature_from_settings"]
    assert found["level_label"] == LEVEL
    outcomes = by_name(found)
    assert outcomes["R"]["boltzmann_ddg"] == pytest.approx(0.0, abs=1e-12)
    assert outcomes["S"]["boltzmann_ddg"] == pytest.approx(ddg, abs=1e-9)
    assert outcomes["R"]["boltzmann_percent"] == pytest.approx(100 * 10 / 11, abs=1e-4)
    assert outcomes["S"]["boltzmann_percent"] == pytest.approx(100 / 11, abs=1e-4)
    # One TS per outcome: the lowest TS gives the same numbers.
    assert outcomes["S"]["lowest_percent"] == pytest.approx(outcomes["S"]["boltzmann_percent"])
    assert found["excess"]["label"] == "ee"
    assert found["excess"]["boltzmann"]["value"] == pytest.approx(100 * 9 / 11, abs=1e-4)
    assert found["excess"]["boltzmann"]["major"] == "R"
    member = outcomes["S"]["members"][0]
    assert member["node_id"] == s and member["label"] == "TS-S"
    assert member["relative"] == pytest.approx(ddg, abs=1e-9)
    assert member["share"] == pytest.approx(100 / 11, abs=1e-4)
    assert member["share_in_outcome"] == pytest.approx(100.0)
    assert found["notes"] == []

    # No ee for a ratio that is not one of enantiomers or diastereomers.
    patch(open_client, f"/selectivities/{created['id']}", {"excess": "none"})
    assert result(open_client, created["id"])["excess"] is None


def test_conformers_of_an_outcome_are_summed_or_only_the_lowest_counts(open_client):
    # T-SEL-02, S2 (Curtin–Hammett): two equal TSs for R against one for S is 2:1 summed and
    # 1:1 with only the lowest; the summed ΔΔG‡ of S is RT ln 2.
    r1 = ts(open_client, "R-a", 0.0, 0.0)
    r2 = ts(open_client, "R-b", 0.0, 0.05)
    s = ts(open_client, "S-a", 0.0, 0.1)
    created = selectivity(open_client, {"R": [r1, r2], "S": [s]})
    outcomes = by_name(result(open_client, created["id"]))
    assert outcomes["R"]["boltzmann_percent"] == pytest.approx(200 / 3)
    assert outcomes["S"]["boltzmann_ddg"] == pytest.approx(R * 298.15 * math.log(2))
    assert outcomes["R"]["lowest_percent"] == pytest.approx(50.0)
    assert outcomes["S"]["lowest_ddg"] == pytest.approx(0.0, abs=1e-12)
    shares = [m["share_in_outcome"] for m in outcomes["R"]["members"]]
    assert shares == [pytest.approx(50.0), pytest.approx(50.0)]
    assert [m["share"] for m in outcomes["R"]["members"]] == [pytest.approx(100 / 3)] * 2

    patched = patch(open_client, f"/selectivities/{created['id']}", {"conformers": "lowest"})
    assert patched["conformers"] == "lowest"
    found = result(open_client, created["id"])
    assert found["conformers"] == "lowest"
    # Both are always computed; the setting says which one leads.
    assert by_name(found)["R"]["boltzmann_percent"] == pytest.approx(200 / 3)


def test_a_group_counts_with_all_its_members(open_client):
    # T-SEL-03, S1: a TS group in an outcome stands for every member, as they are now.
    r1 = ts(open_client, "R-a", 0.0, 0.0)
    r2 = ts(open_client, "R-b", 0.5, 0.05)
    r3 = ts(open_client, "R-c", 1.0, 0.1)
    s = ts(open_client, "S-a", 1.5, 0.15)
    group = post(open_client, "/groups/reconnect", {"member_ids": [r1, r2], "label": "R TSs"})
    created = selectivity(open_client, {"R": [group["id"]], "S": [s]})
    assert created["outcomes"][0]["members"] == [group["id"]]
    members = by_name(result(open_client, created["id"]))["R"]["members"]
    assert [m["node_id"] for m in members] == [r1, r2]
    assert {m["group_label"] for m in members} == {"R TSs"}

    rt = R * 298.15
    weights = [math.exp(-gap(k) / rt) for k in (0.0, 0.5, 1.0)]
    post(open_client, f"/groups/{group['id']}/members", {"node_ids": [r3]}, status=200)
    outcomes = by_name(result(open_client, created["id"]))
    assert len(outcomes["R"]["members"]) == 3
    s_weight = math.exp(-gap(1.5) / rt)
    expected = 100 * sum(weights) / (sum(weights) + s_weight)
    assert outcomes["R"]["boltzmann_percent"] == pytest.approx(expected)


def test_g_qh_is_recomputed_at_the_selectivitys_temperature(open_client):
    # T-SEL-04, S4: G_qh at 233.15 K from the stored frequencies; the ratio uses that T.
    r = ts(open_client, "TS-R", 0.0, 0.0)
    s = ts(open_client, "TS-S", 1.0, 0.05)
    created = selectivity(open_client, {"R": [r], "S": [s]}, temperature=233.15)
    found = result(open_client, created["id"])
    assert found["temperature"] == 233.15 and not found["temperature_from_settings"]
    qh = thermochem.quasi_harmonic(scf_energy=energy(0.0), temperature=233.15, **WATER_FREQ)
    member = by_name(found)["R"]["members"][0]
    assert member["value"] == pytest.approx(energy(0.0) + qh.g_corr, abs=1e-9)
    ratio = math.exp(-gap(1.0) / (R * 233.15))
    assert by_name(found)["S"]["boltzmann_percent"] == pytest.approx(100 * ratio / (1 + ratio))
    # The app's own setting is untouched and still applies elsewhere.
    view = get(open_client, f"/energies/view?level={level_key(open_client, LEVEL)}&type=G_qh")
    assert view["values"][r]["details"]["temperature"] == 298.15

    # With no temperature of its own, it follows the setting.
    patch(open_client, f"/selectivities/{created['id']}", {"temperature": None})
    settings(open_client, qh_temperature=250.0)
    assert result(open_client, created["id"])["temperature"] == 250.0

    # G from the file stays at the job temperature, and the result says so.
    patch(open_client, f"/selectivities/{created['id']}", {"energy_type": "G"})
    notes = result(open_client, created["id"])["notes"]
    assert any("job temperature (298.15 K)" in n and "250 K" in n for n in notes)


def test_a_transition_state_without_a_value_makes_the_result_na(open_client):
    # T-SEL-05, S3: no fallback to another level or type.
    r = ts(open_client, "TS-R", 0.0, 0.0)
    planned = node(open_client, label="TS-S", role="transition_state", xyz=g_water())["id"]
    created = selectivity(open_client, {"R": [r], "S": [planned]})
    found = result(open_client, created["id"])
    assert found["status"] == "n/a"
    assert "“TS-S” (no energy at this level)" in found["message"]
    assert all(o["boltzmann_percent"] is None for o in found["outcomes"])
    assert by_name(found)["S"]["members"][0]["message"] == "no energy at this level"


def g_water() -> str:
    return "3\nwater\n" + "\n".join(f"{e} {x} {y} {z}" for e, _, x, y, z in g.WATER) + "\n"


def test_different_atoms_charges_or_shared_transition_states_are_refused(open_client):
    # T-SEL-06, S3: the TSs must be comparable; the reason is given.
    r = ts(open_client, "TS-R", 0.0, 0.0)
    s = ts(open_client, "TS-S", 1.0, 0.05)
    other = node(open_client, label="Bigger", xyz="2\n\nH 0 0 0\nH 0 0 0.74\n")["id"]
    created = selectivity(open_client, {"R": [r], "S": [other]})
    found = result(open_client, created["id"])
    assert found["status"] == "refused"
    assert "same atoms" in found["message"] and "H2O" in found["message"]
    assert "“Bigger” is not marked as a transition state." in found["notes"]

    patch(open_client, f"/nodes/{s}", {"charge": 1})
    patch(
        open_client,
        f"/selectivities/{created['id']}",
        {"outcomes": [{"name": "R", "members": [r]}, {"name": "S", "members": [s]}]},
    )
    found = result(open_client, created["id"])
    assert found["status"] == "refused" and "same charge" in found["message"]
    patch(open_client, f"/nodes/{s}", {"charge": 0})
    assert result(open_client, created["id"])["status"] == "ok"

    s2 = ts(open_client, "TS-S2", 1.0, 0.1)
    group = post(open_client, "/groups/reconnect", {"member_ids": [s, s2], "label": "S TSs"})
    patch(
        open_client,
        f"/selectivities/{created['id']}",
        {"outcomes": [{"name": "R", "members": [r]}, {"name": "S", "members": [group["id"], r]}]},
    )
    found = result(open_client, created["id"])
    assert found["status"] == "refused"
    assert found["message"] == "“TS-R” is in both “R” and “S”."


def test_an_incomplete_selectivity_says_what_is_missing(open_client):
    r = ts(open_client, "TS-R", 0.0, 0.0)
    created = post(open_client, "/selectivities", {"name": "New"})
    assert created["level"] is None and created["outcomes"] == []
    found = result(open_client, created["id"])
    assert found["status"] == "incomplete" and "two outcomes" in found["message"]
    patch(
        open_client,
        f"/selectivities/{created['id']}",
        {"outcomes": [{"name": "R", "members": [r]}, {"name": "S", "members": []}]},
    )
    assert result(open_client, created["id"])["status"] == "incomplete"
    s = ts(open_client, "TS-S", 1.0, 0.05)
    patch(
        open_client,
        f"/selectivities/{created['id']}",
        {"outcomes": [{"name": "R", "members": [r]}, {"name": "S", "members": [s]}]},
    )
    found = result(open_client, created["id"])
    assert found["status"] == "incomplete" and found["message"] == "Choose a level of theory."


def test_experiment_is_shown_with_the_ddg_it_corresponds_to(open_client):
    # T-SEL-07, S6: 95:5 is ee 90 % and ΔΔG‡ = RT ln 19.
    r = ts(open_client, "TS-R", 0.0, 0.0)
    s = ts(open_client, "TS-S", 1.0, 0.05)
    created = post(
        open_client,
        "/selectivities",
        {
            "name": "Facial",
            "level": level_key(open_client, LEVEL),
            "outcomes": [
                {"name": "R", "members": [r], "experimental": 95},
                {"name": "S", "members": [s], "experimental": 5},
            ],
        },
    )
    found = result(open_client, created["id"])
    outcomes = by_name(found)
    assert outcomes["R"]["experimental_percent"] == pytest.approx(95.0)
    assert outcomes["R"]["experimental_ddg"] == pytest.approx(0.0, abs=1e-12)
    assert outcomes["S"]["experimental_ddg"] == pytest.approx(R * 298.15 * math.log(19))
    assert found["excess"]["experimental"] == {"value": pytest.approx(90.0), "major": "R"}

    # An amount for only some outcomes is not compared.
    patch(
        open_client,
        f"/selectivities/{created['id']}",
        {
            "outcomes": [
                {"name": "R", "members": [r], "experimental": 95},
                {"name": "S", "members": [s]},
            ]
        },
    )
    found = result(open_client, created["id"])
    assert by_name(found)["R"]["experimental_percent"] is None
    assert any("experimental amount for every outcome" in n for n in found["notes"])


def test_selectivities_are_in_the_history_and_follow_deleted_nodes(open_client):
    # T-SEL-08, S7: created, changed and deleted are recorded; a deleted TS leaves its outcome.
    r = ts(open_client, "TS-R", 0.0, 0.0)
    s = ts(open_client, "TS-S", 1.0, 0.05)
    created = selectivity(open_client, {"R": [r], "S": [s]})
    sid = created["id"]
    patch(open_client, f"/selectivities/{sid}", {"name": "Facial (B3LYP)", "notes": "draft"})
    patch(open_client, f"/selectivities/{sid}", {"name": "Facial (B3LYP)"})  # no change
    entries = history(open_client, sid)
    assert [(e["action"], e["field"]) for e in entries] == [
        ("update", "notes"),
        ("update", "name"),
        ("create", None),
    ]
    assert entries[2]["new_value"]["outcomes"] == [
        {"name": "R", "members": [r], "experimental": None},
        {"name": "S", "members": [s], "experimental": None},
    ]

    response = open_client.post("/api/selectivities", json={"name": "Facial (B3LYP)"})
    assert response.status_code == 422 and "already" in response.json()["detail"]
    response = open_client.patch(
        f"/api/selectivities/{sid}", json={"outcomes": [{"name": "R"}, {"name": "R"}]}
    )
    assert response.status_code == 422 and "two outcomes called" in response.json()["detail"]
    response = open_client.patch(f"/api/selectivities/{sid}", json={"temperature": -5})
    assert response.status_code == 422

    open_client.delete(f"/api/nodes/{s}")
    listed = get(open_client, "/selectivities")
    assert listed[0]["outcomes"][1]["members"] == []

    assert open_client.delete(f"/api/selectivities/{sid}").status_code == 204
    assert get(open_client, "/selectivities") == []
    assert history(open_client, sid)[0]["action"] == "delete"
