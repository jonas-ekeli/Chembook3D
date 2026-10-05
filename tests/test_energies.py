"""Energies through the API and the thermochemistry module: FR-EN-01…09, FR-GRP-03, EN-1…10.
Test IDs from docs/spec/07 §6 and §3 are named in each test. The oracle for G_qh is Jonas's
script docs/reference/thermochem_corr_G16.py and its .thch outputs (D56–D58)."""

import csv
import io
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from chembook3d import thermochem
from chembook3d.models import Calculation, CalculationResult
from chembook3d.parsers import gaussian
from chembook3d.services import levels
from tests import gaussian_text as g
from tests.test_gaussian_parser import FIXTURES, MINIMUM, TS, TS_SP
from tests.test_import import calculations, commit, fixture, import_ts, named, upload
from tests.test_pathway import Example, edge, example, get, node, patch, post  # noqa: F401

SCRIPT = Path(__file__).parents[1] / "docs" / "reference" / "thermochem_corr_G16.py"
KCAL = 627.5094740631
TOLERANCE = 0.01 / KCAL  # P29: 0.01 kcal/mol, in hartree
SVP = {"program": "Gaussian", "method": "B3LYP", "basis": "def2SVP"}
TZVP = {"program": "Gaussian", "method": "B3LYP", "basis": "def2TZVP"}


def thch(name: str) -> float:
    """The script's "Thermic correction" in hartree from its .thch output."""
    text = (FIXTURES / name).read_text(encoding="utf-8")
    return float(re.search(r"Thermic correction = (\S+) H", text).group(1))


def options(client) -> dict[str, list[str]]:
    """Level label → energy types listed in the drop-downs (FR-EN-01)."""
    found = get(client, "/energies/options")
    return {o["label"]: o["types"] for o in found["levels"]}


def level_key(client, label: str) -> str:
    return next(o["key"] for o in get(client, "/energies/options")["levels"] if o["label"] == label)


def view(client, level: str, energy_type: str) -> dict:
    return get(client, f"/energies/view?level={level}&type={energy_type}")


def settings(client, **fields) -> dict:
    response = client.put("/api/settings", json=fields)
    assert response.status_code == 200, response.text
    return response.json()


def import_file(client, name: str, text: str, node_id: str | None = None) -> dict:
    """Import as a new node (a possible duplicate is kept apart), or onto `node_id`."""
    plan = upload(client, name, text, node_id)
    options = named(plan)
    if node_id is None:
        options["duplicate_action"] = "new"
    else:
        options["target_node_id"] = node_id
    return commit(client, plan, **options)


DZ = "Gaussian WB97XD/6-31+G(D) SMD(tetrahydrofuran)"
TZ_DZ = f"Gaussian WB97XD/6-311++G(D,P) SMD(tetrahydrofuran) // {DZ}"
# Real modes below 100 cm⁻¹ in the public samples (tests/fixtures/README.md)
LOW_MODES = {MINIMUM: 18, TS: 17}


# ---------- thermochemistry against the reference script (T-EN-04, T-EN-05) ----------


@pytest.mark.parametrize("name", [MINIMUM, TS])
def test_quasi_harmonic_matches_the_reference_output(open_client, name):
    # T-EN-05 at the defaults (298.15 K, 100 cm⁻¹): the script's .thch "Thermic correction".
    node_id = import_file(open_client, name, fixture(name))["node_id"]
    freq = calculations(open_client, node_id)[-1]
    qh = freq["quasi_harmonic"]
    reference = thch(name.replace(".log", ".thch"))
    assert (qh["temperature"], qh["cutoff"]) == (298.15, 100.0)
    assert qh["raised_modes"] == LOW_MODES[name]
    assert qh["imaginary_excluded"] == (1 if name == TS else 0)
    assert abs(qh["correction"] - reference) < TOLERANCE
    assert qh["correction"] == pytest.approx(reference, abs=1e-12)  # same digits

    g_qh = view(open_client, level_key(open_client, DZ), "G_qh")["values"][node_id]
    assert g_qh["value"] == pytest.approx(freq["result"]["energy"] + reference, abs=1e-9)
    assert g_qh["details"]["temperature"] == 298.15 and g_qh["details"]["cutoff"] == 100.0
    # The job-printed G correction is shown alongside (FR-EN-04).
    assert g_qh["details"]["printed_correction"] == freq["result"]["g_corr"]


@pytest.mark.parametrize(("temperature", "cutoff"), [(350, 50), (298, 150)])
def test_quasi_harmonic_matches_the_script_at_other_settings(
    open_client, tmp_path, temperature, cutoff
):
    # T-EN-05 at non-default T and cutoff: run Jonas's script itself as the oracle.
    source = tmp_path / MINIMUM
    shutil.copyfile(FIXTURES / MINIMUM, source)
    run = subprocess.run(
        [sys.executable, str(SCRIPT), str(source), "-t", str(temperature), "-c", str(cutoff)],
        capture_output=True,
        text=True,
        check=True,
        cwd=tmp_path,
    )
    expected = float(re.search(r"Thermic correction: (\S+) H", run.stdout).group(1))

    settings(open_client, qh_temperature=temperature, qh_cutoff=cutoff)
    node_id = import_file(open_client, MINIMUM, fixture(MINIMUM))["node_id"]
    qh = calculations(open_client, node_id)[-1]["quasi_harmonic"]
    assert (qh["temperature"], qh["cutoff"]) == (temperature, cutoff)
    assert abs(qh["correction"] - expected) < TOLERANCE
    assert qh["correction"] == pytest.approx(expected, abs=1e-12)
    default = thch("aziridinium-phos-full-c1.thch")
    assert abs(qh["correction"] - default) > TOLERANCE  # the settings matter


@pytest.mark.parametrize("name", [MINIMUM, TS])
def test_quasi_harmonic_without_low_modes_equals_the_printed_correction(name):
    # T-EN-04: with no mode below the cutoff (lowest real mode ~14 cm⁻¹, cutoff 10) and the
    # job's T, G_qh equals Gaussian's printed G correction within 0.01 kcal/mol (P29).
    step = gaussian.parse(fixture(name)).steps[-1]
    t = step.thermo
    qh = thermochem.quasi_harmonic(
        frequencies=step.frequencies,
        scf_energy=step.scf_energy,
        molecular_mass=t["molecular_mass"],
        multiplicity=step.multiplicity,
        rotational_temperatures=t["rotational_temperatures"],
        symmetry_number=t["symmetry_number"],
        point_group=t["point_group"],
        temperature=t["temperature"],
        cutoff=10.0,
    )
    assert qh.raised_modes == 0
    assert abs(qh.g_corr - t["g_corr"]) < TOLERANCE
    assert abs(qh.h_corr - t["h_corr"]) < TOLERANCE
    assert abs(qh.zpe - t["zpe"]) < TOLERANCE


def test_linear_molecule_has_no_quasi_harmonic_value(open_client):
    # T-EN-10, D58: G_qh "n/a" with a warning; G from the job is still available.
    linear = g.opt_freq(g.WATER, g.WATER).replace(
        " Rotational temperatures (Kelvin)     40.05000    20.12000    13.39000",
        " Rotational temperature (Kelvin)      2.05000",
    )
    node_id = import_file(open_client, "co2.out", linear)["node_id"]
    freq = calculations(open_client, node_id)[-1]
    assert freq["quasi_harmonic"]["correction"] is None
    assert freq["quasi_harmonic"]["code"] == "W-LINEAR"

    key = level_key(open_client, "Gaussian B3LYP/6-31G(D)")
    assert options(open_client)["Gaussian B3LYP/6-31G(D)"] == ["E", "H", "G"]
    value = view(open_client, key, "G_qh")["values"][node_id]
    assert value["value"] is None and value["code"] == "W-LINEAR"
    assert view(open_client, key, "G")["values"][node_id]["value"] is not None
    with pytest.raises(thermochem.NotComputable):
        thermochem.quasi_harmonic(
            frequencies=[500.0, 1300.0, 2300.0],
            scf_energy=-188.0,
            molecular_mass=44.0,
            multiplicity=1,
            rotational_temperatures=[0.56],
            symmetry_number=2,
            point_group="D*H",
        )


def test_two_frequency_jobs_in_one_file_use_only_the_selected_step(open_client):
    # T-EN-11: the script would collect the modes of both steps; the app uses one step.
    first = g.step("#P B3LYP/6-31G(d) Freq", [g.WATER], frequencies=[50.0, 1650.0, 3700.0])
    second = g.step("#P B3LYP/6-31G(d) Freq", [g.WATER], frequencies=[80.0, 1600.0, 3750.0])
    node_id = import_file(open_client, "two-freq.out", g.output(first, second))["node_id"]
    freqs = [c for c in calculations(open_client, node_id) if c["type"] == "frequency"]
    assert len(freqs) == 2

    def qh(frequencies):
        return thermochem.quasi_harmonic(
            frequencies=frequencies,
            scf_energy=-76.4089,
            molecular_mass=18.01056,
            multiplicity=1,
            rotational_temperatures=[40.05, 20.12, 13.39],
            symmetry_number=2,
        ).g_corr

    assert freqs[0]["quasi_harmonic"]["correction"] == pytest.approx(qh([50.0, 1650.0, 3700.0]))
    assert freqs[1]["quasi_harmonic"]["correction"] == pytest.approx(qh([80.0, 1600.0, 3750.0]))
    both = qh([50.0, 1650.0, 3700.0, 80.0, 1600.0, 3750.0])
    value = view(open_client, level_key(open_client, "Gaussian B3LYP/6-31G(D)"), "G_qh")
    chosen = value["values"][node_id]
    # The latest frequency step at the geometry level is the thermal-correction source.
    assert chosen["thermo_calculation_id"] == freqs[1]["id"]
    assert chosen["value"] == pytest.approx(-76.4089 + qh([80.0, 1600.0, 3750.0]))
    assert chosen["value"] != pytest.approx(-76.4089 + both)


# ---------- composite levels (T-EN-01, 02, 03, 07, 09) ----------


def test_composite_free_energy_and_mixed_levels(open_client):
    ts_id = import_ts(open_client)
    import_file(open_client, TS_SP, fixture(TS_SP), ts_id)
    min_id = import_file(open_client, MINIMUM, fixture(MINIMUM))["node_id"]
    edge_id = edge(open_client, min_id, ts_id)["id"]

    # T-EN-07: only combinations with values are listed.
    assert options(open_client) == {DZ: ["E", "H", "G", "G_qh"], TZ_DZ: ["E", "H", "G", "G_qh"]}

    # T-EN-02: G(TZ//DZ) = E(SP, TZ) + (G − E) from the DZ frequency job (EN-4).
    sp = next(c for c in calculations(open_client, ts_id) if c["type"] == "single_point")
    freq = next(c for c in calculations(open_client, ts_id) if c["type"] == "frequency")
    tz = view(open_client, level_key(open_client, TZ_DZ), "G")
    assert sp["result"]["energy"] == -2091.36217372
    value = tz["values"][ts_id]
    assert value["value"] == pytest.approx(-2091.36217372 + freq["result"]["g_corr"], abs=1e-9)
    assert (value["energy_calculation_id"], value["thermo_calculation_id"]) == (
        sp["id"],
        freq["id"],
    )

    # T-EN-01: the minimum has no TZ single point, so the edge shows n/a; no fallback to DZ
    # (EN-3).
    assert tz["values"][min_id]["value"] is None
    assert tz["edges"][edge_id]["delta"] is None
    dz = view(open_client, level_key(open_client, DZ), "G")
    min_freq = calculations(open_client, min_id)[-1]
    # With printed G at one level, ΔG is the difference of the job-printed free energies.
    printed = freq["result"]["g"] - min_freq["result"]["g"]
    assert dz["edges"][edge_id]["delta"] == pytest.approx(printed, abs=2e-6)


def test_free_energy_needs_a_frequency_calculation_at_the_geometry_level(open_client):
    # T-EN-03, FR-EN-03: with E selected the node has a value; with G it is n/a + W-NOFREQ.
    opt_only = g.output(g.step("#P B3LYP/6-31G(d) Opt", [g.WATER], converged=True))
    node_id = import_file(open_client, "opt.out", opt_only)["node_id"]
    key = level_key(open_client, "Gaussian B3LYP/6-31G(D)")
    assert options(open_client) == {"Gaussian B3LYP/6-31G(D)": ["E"]}  # T-EN-07
    assert view(open_client, key, "E")["values"][node_id]["value"] == -76.4089
    missing = view(open_client, key, "G")["values"][node_id]
    assert missing["value"] is None and missing["code"] == "W-NOFREQ"
    # D96: the card says why below "n/a", and the energy table names the reason.
    assert missing["short"] == "no frequency job"
    card = get(open_client, f"/energies/view?level={key}&type=G&reference={node_id}")
    assert card["relative"][node_id]["short"] == "no frequency job"
    body = {"paths": [[node_id]], "reference_id": node_id, "level": key, "type": "G"}
    table = post(open_client, "/energies/table", body, status=200)
    assert table["rows"][0][9:] == [
        "n/a",
        "H, G, G_qh: no frequency calculation at the geometry level (W-NOFREQ)",
    ]


def test_frequency_at_another_level_is_not_used_for_free_energy(open_client):
    # T-EN-09: a later freq at def2TZVP on a 6-31G(d) geometry. G at the geometry level uses
    # only the 6-31G(d) freq, and the TZVP freq's own energy is at TZVP // 6-31G(d).
    node_id = import_file(open_client, "opt-freq.out", g.opt_freq(g.WATER, g.WATER))["node_id"]
    other = g.output(
        g.step("#P B3LYP/def2TZVP Freq", [g.WATER], -76.46, frequencies=[1600.0, 3600.0, 3700.0])
    ).replace("0.003400", "0.009900")
    plan = upload(open_client, "tzvp-freq.out", other, node_id)
    assert any("geometry level" in w["message"] for w in plan["warnings"])  # FR-IMP-13 flag
    commit(open_client, plan, target_node_id=node_id)
    tzvp_freq = next(
        c for c in calculations(open_client, node_id) if c["result"]["g_corr"] == 0.0099
    )

    listed = options(open_client)
    composite = "Gaussian B3LYP/DEF2TZVP // Gaussian B3LYP/6-31G(D)"
    assert listed == {
        "Gaussian B3LYP/6-31G(D)": ["E", "H", "G", "G_qh"],
        composite: ["E", "H", "G", "G_qh"],
    }
    at_geometry = view(open_client, level_key(open_client, "Gaussian B3LYP/6-31G(D)"), "G")
    assert at_geometry["values"][node_id]["value"] == pytest.approx(-76.4089 + 0.0034)
    at_tzvp = view(open_client, level_key(open_client, composite), "G")["values"][node_id]
    assert at_tzvp["value"] == pytest.approx(-76.46 + 0.0034)
    assert at_tzvp["thermo_calculation_id"] != tzvp_freq["id"]


def test_energies_come_only_from_calculation_results(open_client):
    # FR-EN-08, EN-1: there is no way to type an energy; unknown fields are not stored.
    node_id = import_file(open_client, "opt-freq.out", g.opt_freq(g.WATER, g.WATER))["node_id"]
    calc = calculations(open_client, node_id)[0]
    open_client.patch(f"/api/calculations/{calc['id']}", json={"energy": 1.0, "g": 2.0})
    open_client.patch(f"/api/nodes/{node_id}", json={"energy": 1.0})
    key = level_key(open_client, "Gaussian B3LYP/6-31G(D)")
    assert view(open_client, key, "E")["values"][node_id]["value"] == -76.4089


def test_unit_switch_changes_display_not_stored_values(open_client):
    # T-EN-06, FR-EN-07: the table's ΔX follows the unit; stored hartree values do not change.
    a = import_file(open_client, "a.out", g.opt_freq(g.WATER, g.WATER, -76.40))["node_id"]
    moved = g.moved(g.WATER, 0.2)
    b = import_file(open_client, "b.out", g.opt_freq(moved, moved, -76.39))["node_id"]
    edge(open_client, a, b)
    key = level_key(open_client, "Gaussian B3LYP/6-31G(D)")
    body = {"paths": [[a, b]], "level": key, "type": "E"}
    shown = {}
    for unit in ("kcal/mol", "kJ/mol", "eV", "hartree"):
        settings(open_client, energy_unit=unit)
        shown[unit] = post(open_client, "/energies/table", body, status=200)["rows"][1][-2]
        assert view(open_client, key, "E")["values"][b]["value"] == -76.39
    assert shown == {"kcal/mol": "6.28", "kJ/mol": "26.25", "eV": "0.272", "hartree": "0.010000"}


def test_quasi_harmonic_settings_are_checked(open_client):
    assert open_client.put("/api/settings", json={"qh_temperature": 0}).status_code == 422
    assert open_client.put("/api/settings", json={"qh_cutoff": -1}).status_code == 422
    assert settings(open_client, qh_cutoff=0)["qh_cutoff"] == 0


# ---------- the Ru-CAAC example: pathways, profiles, groups (T-BR-05…09, 13; T-EN-08) ----------


def add_energies(client, node_id: str, energy: float, g_corr: float = 0.02, level=SVP) -> None:
    """An optimization and a frequency record at `level`, written directly: the example's
    geometries are placeholders, and the energies only need to be distinct."""
    with client.app.state.investigation.sessions.begin() as session:
        lot = levels.get_or_create(session, level)
        for index, kind in enumerate(("optimization", "frequency"), start=1):
            calc = Calculation(
                node_id=node_id,
                type=kind,
                program="Gaussian",
                level_id=lot.id,
                step_index=index,
                termination="normal",
                multiplicity=1,
            )
            session.add(calc)
            session.flush()
            calc.result = CalculationResult(
                energy=energy,
                g_corr=g_corr if kind == "frequency" else None,
                h_corr=g_corr + 0.03 if kind == "frequency" else None,
                frequencies=[120.0, 800.0, 1600.0] if kind == "frequency" else [],
                molecular_mass=300.0,
                rotational_temperatures=[0.1, 0.05, 0.04],
                symmetry_number=1,
            )


@pytest.fixture
def energetic(open_client, example) -> Example:  # noqa: F811
    """The example with G at def2-SVP on every node; TSs 15 kcal/mol above their neighbours."""
    for name, node_id in example.nodes.items():
        index = int(name[-1]) if name[-1].isdigit() else 0
        energy = -100.0 - index * 0.004 + (0.024 if index in (2, 4) else 0.0)
        if name.startswith("B1"):
            energy -= 0.01  # B1 lies lower than A1 throughout
        add_energies(open_client, node_id, energy)
    return example


def svp(client) -> str:
    return level_key(client, "Gaussian B3LYP/def2SVP")


def test_branch_pathway_follows_edges_and_lineage(open_client, energetic):
    # WF-08: a branch's pathway runs from the trunk through its lineage, along edges only.
    ex = energetic
    found = get(open_client, f"/branches/{ex.branches['A1']}/pathway")
    labels = {v: k for k, v in ex.nodes.items()}
    assert [labels[i] for i in found["path"]] == [
        "T-pre",
        "A-S0",
        "A1-S1",
        "A1-S2",
        "A1-S3",
        "A1-S4",
        "A1-S5",
        "A1-S6",
    ]
    assert found["choices"] == []

    # At a split the pathway stops and lists the choices; it never picks one (EN-10).
    fork = post(open_client, "/pathways/extend", {"path": [ex.nodes["T-pre"]]}, status=200)
    assert [labels[i] for i in fork["path"]] == ["T-pre"]
    assert sorted(c["label"] for c in fork["choices"]) == ["A-S0", "B-S0"]


def test_profile_needs_edges_and_is_not_rerouted_by_energies(open_client, energetic):
    ex = energetic
    # T-BR-08: nodes without a connecting edge cannot form a profile (INV-2).
    body = {
        "paths": [[ex.nodes["A1-S1"], ex.nodes["B1-S2"]]],
        "level": svp(open_client),
        "type": "G",
    }
    response = open_client.post("/api/energies/profile", json=body)
    assert response.status_code == 422 and "INV-2" in response.json()["detail"]

    # T-BR-09: B1-S3 lies lower than A1-S3, yet A1's profile still goes through A1-S3.
    path = get(open_client, f"/branches/{ex.branches['A1']}/pathway")["path"]
    profile = post(
        open_client,
        "/energies/profile",
        {
            "paths": [path],
            "reference_id": ex.nodes["A1-S1"],
            "level": svp(open_client),
            "type": "G",
        },
        status=200,
    )
    points = profile["profiles"][0]["points"]
    assert ex.nodes["A1-S3"] in [p["id"] for p in points]
    assert ex.nodes["B1-S3"] not in [p["id"] for p in points]
    by_label = {p["label"]: p for p in points}
    assert by_label["A1-S1"]["relative"] == 0.0
    assert by_label["A1-S2"]["relative"] == pytest.approx(0.020, abs=1e-12)
    assert by_label["A1-S2"]["is_ts"] and not by_label["A1-S3"]["is_ts"]

    # The reference must lie on a pathway (EN-8).
    body = {
        "paths": [path],
        "reference_id": ex.nodes["B1-S3"],
        "level": svp(open_client),
        "type": "G",
    }
    assert open_client.post("/api/energies/profile", json=body).status_code == 422


def test_direct_connection_is_a_no_ts_segment(open_client, energetic):
    # T-BR-13, FR-EN-09: A1-S1 → A1-S3 without a TS is drawn "no TS" in the profile.
    ex = energetic
    direct = edge(open_client, ex.nodes["A1-S1"], ex.nodes["A1-S3"])
    body = {
        "paths": [[ex.nodes["A1-S1"], ex.nodes["A1-S3"]]],
        "level": svp(open_client),
        "type": "G",
    }
    profile = post(open_client, "/energies/profile", body, status=200)["profiles"][0]
    assert profile["segments"] == [
        {"transition_id": direct["id"], "forward": True, "status": "planned", "direct": True}
    ]
    viewed = view(open_client, svp(open_client), "G")
    assert viewed["edges"][direct["id"]]["direct"] is True
    assert viewed["edges"][direct["id"]]["delta"] == pytest.approx(-0.008, abs=1e-12)


def test_group_energies_use_the_representative_only(open_client, energetic):
    ex = energetic
    members = [ex.nodes[f"{b}-S6"] for b in ("A1", "A2", "B1", "B2")]
    group = post(
        open_client,
        "/groups/reconnect",
        {"member_ids": members, "label": "G6", "outgoing": {"name": "R"}},
    )
    after = node(open_client, label="R-S0", role="minimum", branch_id=group["outgoing_branch_id"])
    add_energies(open_client, after["id"], -100.05)
    out = edge(open_client, group["id"], after["id"])
    key = svp(open_client)

    # T-BR-05: no representative, so the group's edges show n/a (EN-7).
    viewed = view(open_client, key, "G")
    assert viewed["values"][group["id"]]["value"] is None
    assert viewed["edges"][out["id"]]["delta"] is None

    # T-BR-06, T-BR-07: members can be sorted by energy (FR-GRP-03), but no view or sort
    # ever sets or changes the representative (EN-10).
    member_values = {m: viewed["values"][m]["value"] for m in members}
    lowest = min(member_values, key=member_values.get)
    assert lowest == ex.nodes["B1-S6"]
    patch(open_client, f"/groups/{group['id']}", {"representative_id": ex.nodes["A2-S6"]})
    for energy_type in ("E", "G", "G_qh"):
        view(open_client, key, energy_type)
    stored = next(x for x in get(open_client, "/groups") if x["id"] == group["id"])
    assert stored["representative_id"] == ex.nodes["A2-S6"]
    viewed = view(open_client, key, "G")
    rep = viewed["values"][group["id"]]
    assert rep["details"]["representative_id"] == ex.nodes["A2-S6"]
    assert viewed["edges"][out["id"]]["delta"] == pytest.approx(
        viewed["values"][after["id"]]["value"] - viewed["values"][ex.nodes["A2-S6"]]["value"]
    )

    # The R branch pathway starts at the group, whose incoming edges form a fork.
    found = get(open_client, f"/branches/{group['outgoing_branch_id']}/pathway")
    assert found["path"] == [group["id"], after["id"]]


def test_a_catalytic_cycle_closes_at_its_resting_state(open_client):
    # A13: a pathway may end at a node it visited, closing the cycle once.
    client = open_client
    steps = [post(client, "/steps", {"name": f"C{i}"})["id"] for i in range(4)]
    cycle = post(client, "/branches", {"name": "Cycle"})["id"]
    names = ["Rest", "TS1", "Int", "TS2"]
    ids = {}
    for i, name in enumerate(names):
        role = "transition_state" if name.startswith("TS") else "minimum"
        made = node(client, label=name, role=role, step_id=steps[i], branch_id=cycle)
        ids[name] = made["id"]
        add_energies(
            client, made["id"], -100.0 + (0.02 if role == "transition_state" else -0.005 * i)
        )
    for a, b in zip(names, names[1:], strict=False):
        edge(client, ids[a], ids[b])
    closing = edge(client, ids["TS2"], ids["Rest"])
    label = {v: k for k, v in ids.items()}

    first = post(client, "/pathways/extend", {"path": [ids["Rest"]]}, status=200)
    assert [label[i] for i in first["path"]] == [*names, "Rest"]
    assert first["closed"] is True and first["choices"] == []
    # A closed pathway is complete: extending it changes nothing.
    again = post(client, "/pathways/extend", {"path": first["path"]}, status=200)
    assert again["path"] == first["path"] and again["closed"] is True
    # Only the last node may return to an earlier one; no second turnover (A13).
    twice = [*first["path"], ids["TS1"]]
    response = client.post("/api/pathways/extend", json={"path": twice})
    assert response.status_code == 422 and "A13" in response.json()["detail"]

    # The branch pathway starts at the resting state; the closing edge is not the way in.
    found = get(client, f"/branches/{cycle}/pathway")
    assert found["path"] == first["path"] and found["closed"] is True

    profile = post(
        client,
        "/energies/profile",
        {"paths": [first["path"]], "level": svp(client), "type": "G"},
        status=200,
    )["profiles"][0]
    # The closing point is the resting state at its own value (A13).
    assert [p["relative"] for p in profile["points"][::4]] == [0.0, 0.0]
    assert profile["segments"][-1]["transition_id"] == closing["id"]
    assert len(profile["segments"]) == 4

    # The table still has one row per node.
    table = post(
        client,
        "/energies/table",
        {"paths": [first["path"]], "level": svp(client), "type": "G", "unit": "kcal/mol"},
        status=200,
    )
    assert [row[0] for row in table["rows"]] == names


def test_a_branch_profile_includes_the_branchless_nodes_it_starts_and_ends_at(open_client):
    # T-EN-12, D67: IM1 is on no branch and splits into TS1-1 (branch 1) and TS1-2 (branch 2);
    # both branches end at a shared product P. Each branch's profile runs IM1 → … → P.
    client = open_client
    steps = [post(client, "/steps", {"name": n})["id"] for n in ("IM1", "TS1", "IM2", "P")]
    branches = [post(client, "/branches", {"name": f"b{n}"})["id"] for n in (1, 2)]
    ids = {"IM1": node(client, label="IM1", step_id=steps[0])["id"]}
    ids["P"] = node(client, label="P", step_id=steps[3])["id"]
    for n, branch in enumerate(branches, start=1):
        ts = node(
            client, label=f"TS1-{n}", role="transition_state", step_id=steps[1], branch_id=branch
        )
        im = node(client, label=f"IM2-{n}", step_id=steps[2], branch_id=branch)
        ids[ts["label"]], ids[im["label"]] = ts["id"], im["id"]
        edge(client, ids["IM1"], ts["id"])
        edge(client, ts["id"], im["id"])
        edge(client, im["id"], ids["P"])
    for i, record_id in enumerate(ids.values()):
        add_energies(client, record_id, -100.0 - 0.001 * i)
    label = {v: k for k, v in ids.items()}

    for n, branch in enumerate(branches, start=1):
        found = get(client, f"/branches/{branch}/pathway")
        assert [label[i] for i in found["path"]] == ["IM1", f"TS1-{n}", f"IM2-{n}", "P"]
    profile = post(
        client,
        "/energies/profile",
        {"paths": [found["path"]], "level": svp(client), "type": "G"},
        status=200,
    )["profiles"][0]
    assert profile["points"][0]["relative"] == 0.0  # IM1 is the default reference
    assert profile["points"][0]["branch_id"] is None

    # A node on another branch is still never entered.
    other = post(client, "/branches", {"name": "other"})["id"]
    before = node(client, label="pre", branch_id=other)
    edge(client, before["id"], ids["IM1"])
    assert get(client, f"/branches/{branches[0]}/pathway")["path"][0] == ids["IM1"]
    # One more node on no branch leading in is followed; two ways in are a fork, so the
    # trace stops at IM1 (EN-10: the app never chooses between them).
    first = node(client, label="R1")
    edge(client, first["id"], ids["IM1"])
    assert get(client, f"/branches/{branches[0]}/pathway")["path"][0] == first["id"]
    second = node(client, label="R2")
    edge(client, second["id"], ids["IM1"])
    assert get(client, f"/branches/{branches[0]}/pathway")["path"][0] == ids["IM1"]


def test_a_branch_profile_runs_through_every_merge_point_it_passes(open_client):
    # T-EN-13, D67: branches 1 and 2 split at IM1, merge at IM3 (no branch) and split again.
    # Each branch's profile includes both IM1 and IM3.
    client = open_client
    names = ("IM1", "TS1", "IM2", "IM3", "TS3", "IM4")
    steps = {n: post(client, "/steps", {"name": n})["id"] for n in names}
    branches = [post(client, "/branches", {"name": f"b{n}"})["id"] for n in (1, 2)]
    ids = {n: node(client, label=n, step_id=steps[n])["id"] for n in ("IM1", "IM3")}

    def chain(n: int, branch: str, first: str, second: str, start: str) -> None:
        ts = node(
            client,
            label=f"{first}-{n}",
            role="transition_state",
            step_id=steps[first],
            branch_id=branch,
        )
        im = node(client, label=f"{second}-{n}", step_id=steps[second], branch_id=branch)
        ids[ts["label"]], ids[im["label"]] = ts["id"], im["id"]
        edge(client, ids[start], ts["id"])
        edge(client, ts["id"], im["id"])

    for n, branch in enumerate(branches, start=1):
        chain(n, branch, "TS1", "IM2", "IM1")
        edge(client, ids[f"IM2-{n}"], ids["IM3"])
        chain(n, branch, "TS3", "IM4", "IM3")
    label = {v: k for k, v in ids.items()}
    for n, branch in enumerate(branches, start=1):
        found = get(client, f"/branches/{branch}/pathway")
        assert [label[i] for i in found["path"]] == [
            "IM1",
            f"TS1-{n}",
            f"IM2-{n}",
            "IM3",
            f"TS3-{n}",
            f"IM4-{n}",
        ]

    # After the merge, the way on may be a new branch; its parent says which one it continues.
    b3 = post(client, "/branches", {"name": "b3", "parent_ids": [branches[0]]})["id"]
    chain(3, b3, "TS3", "IM4", "IM3")
    label = {v: k for k, v in ids.items()}
    found = get(client, f"/branches/{b3}/pathway")
    assert [label[i] for i in found["path"]] == ["IM1", "TS1-1", "IM2-1", "IM3", "TS3-3", "IM4-3"]
    # Branch 1 now has two ways on from IM3 (its own TS3-1 and its child b3), so it stops there.
    found = get(client, f"/branches/{branches[0]}/pathway")
    assert label[found["path"][-1]] == "IM3"
    assert {label[c["node_id"]] for c in found["choices"]} == {"TS3-1", "TS3-3"}
    # A new branch with both merged branches as parents cannot tell which way it came in.
    b4 = post(client, "/branches", {"name": "b4", "parent_ids": branches})["id"]
    chain(4, b4, "TS3", "IM4", "IM3")
    label = {v: k for k, v in ids.items()}
    assert label[get(client, f"/branches/{b4}/pathway")["path"][0]] == "IM3"


def test_a_branch_profile_runs_through_a_reconnection_and_on_into_its_outgoing_branch(
    open_client,
):
    # T-EN-13, D67: IM1 (no branch) splits into branches 1 and 2, which merge by reconnecting
    # into group G with outgoing branch R. Branch 1's profile runs IM1 → TS1-1 → G → R's nodes.
    client = open_client
    names = ("IM1", "TS1", "IM2", "TS3", "IM4")
    steps = {n: post(client, "/steps", {"name": n})["id"] for n in names}
    branches = [post(client, "/branches", {"name": f"b{n}"})["id"] for n in (1, 2)]
    ids = {"IM1": node(client, label="IM1", step_id=steps["IM1"])["id"]}
    for n, branch in enumerate(branches, start=1):
        ts = node(
            client,
            label=f"TS1-{n}",
            role="transition_state",
            step_id=steps["TS1"],
            branch_id=branch,
        )
        im = node(client, label=f"IM2-{n}", step_id=steps["IM2"], branch_id=branch)
        ids[ts["label"]], ids[im["label"]] = ts["id"], im["id"]
        edge(client, ids["IM1"], ts["id"])
    group = post(
        client,
        "/groups/reconnect",
        {"member_ids": [ids["IM2-1"], ids["IM2-2"]], "label": "G", "outgoing": {"name": "R"}},
    )
    ids["G"] = group["id"]
    for n in (1, 2):
        edge(client, ids[f"TS1-{n}"], ids["G"])
    ts = node(
        client,
        label="TS3",
        role="transition_state",
        step_id=steps["TS3"],
        branch_id=group["outgoing_branch_id"],
    )
    im = node(client, label="IM4", step_id=steps["IM4"], branch_id=group["outgoing_branch_id"])
    ids["TS3"], ids["IM4"] = ts["id"], im["id"]
    edge(client, ids["G"], ts["id"])
    edge(client, ts["id"], im["id"])
    label = {v: k for k, v in ids.items()}

    for n, branch in enumerate(branches, start=1):
        found = get(client, f"/branches/{branch}/pathway")
        assert [label[i] for i in found["path"]] == [
            "IM1",
            f"TS1-{n}",
            "G",
            "TS3",
            "IM4",
        ]
    # R continues both branches, so the app cannot say which way it came in: it starts at G.
    found = get(client, f"/branches/{group['outgoing_branch_id']}/pathway")
    assert [label[i] for i in found["path"]] == ["G", "TS3", "IM4"]


def test_a_branch_cycle_back_to_the_trunk_and_a_fork_that_can_close_it(open_client, energetic):
    # A13: closing A1 back into the trunk keeps its pathway starting at the trunk.
    ex = energetic
    edge(open_client, ex.nodes["A1-S6"], ex.nodes["T-pre"])
    labels = {v: k for k, v in ex.nodes.items()}
    found = get(open_client, f"/branches/{ex.branches['A1']}/pathway")
    assert [labels[i] for i in found["path"]] == [
        "T-pre",
        "A-S0",
        "A1-S1",
        "A1-S2",
        "A1-S3",
        "A1-S4",
        "A1-S5",
        "A1-S6",
        "T-pre",
    ]
    assert found["closed"] is True

    # A fork lists a node already visited, so the user can close the cycle there.
    extra = node(open_client, label="A1-S7", role="minimum", branch_id=ex.branches["A1"])
    edge(open_client, ex.nodes["A1-S6"], extra["id"])
    fork = get(open_client, f"/branches/{ex.branches['A1']}/pathway")
    assert labels[fork["path"][-1]] == "A1-S6" and fork["closed"] is False
    assert sorted(c["label"] for c in fork["choices"]) == ["A1-S7", "T-pre"]


def test_energy_table_and_csv_show_the_same_values(open_client, energetic):
    # T-EN-08, FR-EN-06: columns as specified, CSV text equal to the on-screen table.
    ex = energetic
    settings(open_client, energy_unit="kcal/mol")
    path = get(open_client, f"/branches/{ex.branches['A1']}/pathway")["path"]
    body = {
        "paths": [path],
        "reference_id": ex.nodes["A-S0"],
        "level": svp(open_client),
        "type": "G",
    }
    table = post(open_client, "/energies/table", body, status=200)
    assert table["columns"] == [
        "Label",
        "Step",
        "Branch",
        "Level",
        "E (hartree)",
        "H (hartree)",
        "G (hartree)",
        "G_qh 298.15 K 100 cm-1 (hartree)",
        "Free species",
        "ΔG (kcal/mol)",
        "Why n/a",
    ]
    assert [row[0] for row in table["rows"]][:3] == ["T-pre", "A-S0", "A1-S1"]
    a1s2 = next(row for row in table["rows"] if row[0] == "A1-S2")
    assert a1s2[1:4] == ["[2+2] TS", "A1", "Gaussian B3LYP/def2SVP"]
    assert a1s2[4] == "-99.98400000" and a1s2[6] == "-99.96400000"
    assert a1s2[8] == "" and a1s2[9] == f"{(0.024 - 0.008) * KCAL:.2f}" and a1s2[10] == ""

    response = open_client.post("/api/energies/table.csv", json=body)
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    rows = list(csv.reader(io.StringIO(response.content.decode("utf-8-sig"))))
    assert rows == [table["columns"], *table["rows"]]


def test_overlaid_profiles_share_one_reference(open_client, energetic):
    # FR-EN-05: several branches overlaid, all relative to one reference node.
    ex = energetic
    paths = [get(open_client, f"/branches/{ex.branches[b]}/pathway")["path"] for b in ("A1", "B1")]
    body = {
        "paths": paths,
        "reference_id": ex.nodes["T-pre"],
        "level": svp(open_client),
        "type": "G",
    }
    profile = post(open_client, "/energies/profile", body, status=200)
    first, second = profile["profiles"]
    assert first["points"][0]["relative"] == second["points"][0]["relative"] == 0.0
    b1_s3 = next(p for p in second["points"] if p["label"] == "B1-S3")
    a1_s3 = next(p for p in first["points"] if p["label"] == "A1-S3")
    assert b1_s3["relative"] == pytest.approx(a1_s3["relative"] - 0.01)
    assert [p["step_position"] for p in first["points"]][1:] == [1, 2, 3, 4, 5, 6, 7]


@pytest.mark.parametrize(
    ("method", "multiplicity", "expected"),
    [
        ("RB3LYP", 1, "B3LYP"),  # the freq step of an `opt freq` job
        ("UB3LYP", 2, "B3LYP"),
        ("UB3LYP", 1, "UB3LYP"),  # broken-symmetry singlet: not the default
        ("ROB3LYP", 3, "ROB3LYP"),
        ("RHF", 1, "HF"),
        ("REVTPSSREVTPSS", 1, "REVTPSSREVTPSS"),
        ("UFF", 2, "UFF"),
        ("PBEPBE", 1, "PBEPBE"),
    ],
)
def test_default_reference_prefix_is_not_part_of_the_level(method, multiplicity, expected):
    # A12: `B3LYP opt freq` gives one level for both steps, so its G is available.
    assert levels.canonical_method(method, multiplicity) == expected
