"""Stationary-point checks where energies are used (D154): T-EN-18…21. The frequency job at a
value's geometry level is checked against the node's role, and what it says goes with the value
into profiles, the energy table, selectivities and turnovers. It never changes a value (D19)."""

from tests import gaussian_text as g
from tests.test_energies import import_file, level_key, view
from tests.test_import import node
from tests.test_pathway import edge, get, patch, post
from tests.test_selectivity import LEVEL, by_name, result, selectivity

NO_IMAGINARY = (1650.0, 3700.0, 3800.0)
TWO_IMAGINARY = (-500.0, -120.5, 1650.0, 3700.0, 3800.0)


def point(client, label: str, frequencies, shift: float, role: str, energy=-76.40) -> str:
    atoms = g.moved(g.WATER, shift)
    text = g.opt_freq(atoms, atoms, energy, frequencies=frequencies)
    node_id = import_file(client, f"{label}.out", text)["node_id"]
    patch(client, f"/nodes/{node_id}", {"label": label, "role": role})
    return node_id


def codes(client, node_id: str) -> list[str]:
    return [w["code"] for w in node(client, node_id)["warnings"]]


def test_the_value_carries_the_check_at_its_geometry_level(open_client):
    # T-EN-18
    good = point(open_client, "TS-1", g.TS_FREQUENCIES, 0.0, "transition_state")
    flat = point(open_client, "TS-0", NO_IMAGINARY, 0.05, "transition_state")
    two = point(open_client, "TS-2", TWO_IMAGINARY, 0.1, "transition_state")
    bent = point(open_client, "Min", g.TS_FREQUENCIES, 0.15, "minimum")
    values = view(open_client, level_key(open_client, LEVEL), "G")["values"]
    assert values[good]["check"] is None
    assert values[flat]["check"] == {
        "code": "W-IMAG-TS",
        "message": "0 imaginary frequencies at the geometry level, not 1",
    }
    assert values[two]["check"] == {
        "code": "W-IMAG-TS",
        "message": "2 imaginary frequencies at the geometry level, not 1 (-500.0, -120.5 cm⁻¹)",
    }
    assert values[bent]["check"] == {
        "code": "W-IMAG-MIN",
        "message": "1 imaginary frequency at the geometry level (-500.0 cm⁻¹)",
    }
    # The value itself is unchanged (D19).
    assert values[flat]["value"] is not None and values[bent]["value"] is not None


def test_a_transition_state_without_a_frequency_job(open_client):
    # T-EN-19: W-TS-NOFREQ on the node, and on its E wherever it is used.
    opt = g.output(g.step("#P B3LYP/6-31G(d) Opt", [g.WATER], converged=True))
    ts_id = import_file(open_client, "ts.out", opt)["node_id"]
    patch(open_client, f"/nodes/{ts_id}", {"role": "transition_state"})
    assert "W-TS-NOFREQ" in codes(open_client, ts_id)
    value = view(open_client, level_key(open_client, LEVEL), "E")["values"][ts_id]
    assert value["value"] == -76.4089 and value["check"]["code"] == "W-TS-NOFREQ"
    # A planned TS with no calculations yet has nothing to check.
    planned = post(open_client, "/nodes", {"label": "planned", "role": "transition_state"})
    assert "W-TS-NOFREQ" not in codes(open_client, planned["id"])


def test_a_later_frequency_job_at_another_level_does_not_hide_the_check(open_client):
    # T-EN-20: the TS's frequency job at B3LYP has no imaginary mode; a PBE0 frequency job on
    # the same geometry with one does not make the B3LYP value pass.
    ts_id = point(open_client, "TS", NO_IMAGINARY, 0.0, "transition_state")
    pbe0 = g.output(
        g.step("#P PBE1PBE/6-31G(d) Freq", [g.WATER], -76.3, frequencies=list(g.TS_FREQUENCIES))
    )
    import_file(open_client, "pbe0.out", pbe0, ts_id)
    b3lyp = view(open_client, level_key(open_client, LEVEL), "G")["values"][ts_id]
    assert b3lyp["check"]["code"] == "W-IMAG-TS"


def test_checks_reach_profiles_the_table_selectivities_and_turnovers(open_client):
    # T-EN-21
    a = point(open_client, "A", NO_IMAGINARY, 0.0, "minimum", -76.42)
    ts_r = point(open_client, "TS-R", NO_IMAGINARY, 0.05, "transition_state")
    ts_s = point(open_client, "TS-S", g.TS_FREQUENCIES, 0.1, "transition_state", -76.399)
    edge(open_client, a, ts_r)
    key = level_key(open_client, LEVEL)
    body = {"paths": [[a, ts_r]], "level": key, "type": "G"}
    profile = post(open_client, "/energies/profile", body, status=200)
    points = profile["profiles"][0]["points"]
    assert points[0]["check"] is None and points[1]["check"]["code"] == "W-IMAG-TS"
    table = post(open_client, "/energies/table", body, status=200)
    assert table["columns"][-1] == "Check"
    assert [row[-1] for row in table["rows"]] == [
        "",
        "0 imaginary frequencies at the geometry level, not 1",
    ]

    created = selectivity(open_client, {"R": [ts_r], "S": [ts_s]})
    found = result(open_client, created["id"])
    assert found["status"] == "ok"
    assert by_name(found)["R"]["members"][0]["check"]["code"] == "W-IMAG-TS"
    assert by_name(found)["S"]["members"][0]["check"] is None
    assert found["notes"] == ["“TS-R”: 0 imaginary frequencies at the geometry level, not 1."]

    edge(open_client, ts_r, a)
    cycle = post(open_client, "/turnovers", {"name": "Cycle", "path": [a, ts_r, a], "level": key})
    notes = get(open_client, f"/turnovers/{cycle['id']}/result")["notes"]
    assert "“TS-R”: 0 imaginary frequencies at the geometry level, not 1." in notes
