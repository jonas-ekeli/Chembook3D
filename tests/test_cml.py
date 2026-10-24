"""ioChem-BD's CML of Gaussian outputs (D186): the parser against published files and the
import through the API. The files are from ioChem-BD collection 6-113 (CC BY 4.0; sources in
tests/fixtures/README.md); expected values were read from the CML and the item pages."""

from pathlib import Path

import pytest

from chembook3d import thermochem
from chembook3d.parsers import cml, common
from chembook3d.services import batch_import, imports
from tests.test_batch_import import batch_names, run, scan
from tests.test_batch_import import preview as batch_preview
from tests.test_energies import level_key, view
from tests.test_import import calculations, commit, named, node, preview, upload

FIXTURES = Path(__file__).parent / "fixtures" / "iochem"
TS = "ts2prime-2_caac1_opt-freq.cml"  # HF guess, PBE opt, opt(ts), freq: 6 jobs
TS_PBE = "ts2prime-2_caac1_sp-pbe-d3mbj.cml"  # GENECP single point, D3M(BJ) by IOp
TS_M06L = "ts2prime-2_caac1_sp-m06l-d3.cml"  # CHKBASIS single point
ETHYLENE = "ethylene_opt-freq.cml"
TRIPLET = "mcb-fragment-triplet_sp-pbe-d3mbj.cml"  # unrestricted, multiplicity 3
KCAL = 627.5094740631


def read(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def ts():
    return cml.parse(read(TS))


# ---------- the parser (T-IMP-57…59) ----------


def test_jobs_become_steps_as_in_a_log(ts):
    # T-IMP-57: each cc:job is one step, read as gaussian.parse reads a --Link1-- chain.
    assert ts.program == "Gaussian" and ts.version == "16 Rev. C.01"
    assert [s.job_type for s in ts.steps] == [
        "single_point",
        "single_point",
        "other",
        "optimization",
        "ts_optimization",
        "frequency",
    ]
    assert all(s.termination == "normal" and not s.missing for s in ts.steps)
    assert all((s.charge, s.multiplicity) == (0, 1) for s in ts.steps)
    assert ts.steps[0].route.method == "HF" and ts.steps[0].route.basis == "GENECP"
    assert ts.steps[4].route.method == "PBEPBE" and ts.steps[4].route.basis == "CHKBASIS"
    assert (ts.steps[4].route.solvation_model, ts.steps[4].route.solvent) == (
        "IEFPCM",
        "chloroform",
    )
    assert ts.steps[0].title == "CAAC1_CH2_TS_rotation_F1, step1"
    # The basis set printed by GFInput, with Ru's pseudopotential, carried to CHKBASIS steps.
    definition = ts.steps[0].basis_definition
    assert set(definition) == {"C", "Cl", "H", "N", "Ru"}
    assert "ECP" in definition["Ru"] and "ECP" not in definition["C"]
    assert ts.steps[5].resolved_basis == "GENECP"
    assert ts.steps[5].resolved_basis_definition == definition


def test_optimization_structures_energies_and_convergence(ts):
    opt = ts.steps[4]
    # ioChem-BD keeps the job's first orientation and the final structure.
    assert len(opt.geometries) == 2 and len(opt.geometries[-1]) == 52
    assert opt.geometries[-1][0].element == "Ru"
    assert opt.geometries[-1][0].x == pytest.approx(-3.03942134)
    assert opt.optimization_converged is True and opt.converged_geometries == [1]
    assert opt.scf_energy == -1809.92780549  # the last SCF Done (archive: -1809.9278055)
    criteria = {c.name: c for c in opt.geometry_criteria[-1]}
    assert criteria["Maximum displacement"].value == pytest.approx(0.000014)
    assert criteria["Maximum displacement"].threshold == pytest.approx(0.00006)
    assert criteria["RMS force"].converged is True
    assert opt.constraints == []


def test_frequencies_and_thermochemistry(ts):
    # T-IMP-58: the frequency job's modes and Gaussian's thermochemistry.
    freq = ts.steps[5]
    assert len(freq.frequencies) == 150 and freq.imaginary_count == 1
    assert freq.frequencies[0] == pytest.approx(-38.2473)
    assert len(freq.normal_modes) == 150 and len(freq.normal_modes[0]) == 52
    assert len(freq.reduced_masses) == 150
    assert freq.thermo["temperature"] == 298.15 and freq.thermo["pressure"] == 1.0
    assert freq.thermo["symmetry_number"] == 1
    assert freq.thermo["rotational_temperatures"] == [0.01224, 0.00842, 0.00743]
    assert freq.thermo["molecular_mass"] == pytest.approx(443.07141)
    assert freq.thermo["zpe"] == pytest.approx(0.422453)
    assert freq.thermo["g_corr"] == pytest.approx(0.369689)
    assert freq.thermo["g"] == pytest.approx(-1809.558117)
    assert freq.printed[common.G_MODEL] == "RRHO"
    assert common.NOT_STATIONARY not in freq.printed  # "Stationary point found"


@pytest.mark.parametrize("name", [TS, ETHYLENE])
def test_rrho_from_the_cml_matches_gaussians_printed_g(name):
    # T-IMP-58: with no cutoff, our RRHO from the CML's frequencies, mass, rotational
    # temperatures and symmetry number gives Gaussian's own G correction (5 decimals printed).
    freq = cml.parse(read(name)).steps[-1]
    found = thermochem.quasi_harmonic(
        frequencies=freq.frequencies,
        scf_energy=freq.scf_energy,
        molecular_mass=freq.thermo["molecular_mass"],
        multiplicity=freq.multiplicity,
        rotational_temperatures=freq.thermo["rotational_temperatures"],
        symmetry_number=freq.thermo["symmetry_number"],
        cutoff=0.0,
    )
    assert abs(found.g_corr - freq.thermo["g_corr"]) * KCAL < 0.001
    assert abs(found.zpe - freq.thermo["zpe"]) * KCAL < 0.001


def test_single_points():
    # T-IMP-59: a GENECP single point with D3M(BJ) parameters by IOp, a CHKBASIS one, and an
    # unrestricted triplet with its spin expectation values.
    (pbe,) = cml.parse(read(TS_PBE)).steps
    assert pbe.job_type == "single_point" and not pbe.missing
    assert pbe.route.dispersion == "GD3BJ"
    assert pbe.route.iops == {
        "3/174": "1000000",
        "3/175": "358940",
        "3/177": "12092",
        "3/178": "5938951",
    }
    assert pbe.scf_energy == -1810.37394001
    assert "ECP" in pbe.basis_definition["Ru"]
    assert len(pbe.geometries[-1]) == 52

    (m06l,) = cml.parse(read(TS_M06L)).steps
    assert m06l.route.method == "M06L" and m06l.route.basis == "CHKBASIS"
    assert m06l.missing == ["basis set"]  # named at import (D104)
    assert m06l.scf_energy == -1811.63176584

    (triplet,) = cml.parse(read(TRIPLET)).steps
    assert (triplet.charge, triplet.multiplicity) == (0, 3)
    assert triplet.printed[common.S2] == "2.012981"
    assert triplet.printed[common.S2_AFTER] == "2.000084"


def test_what_is_not_read():
    with pytest.raises(cml.NotCml, match="compchem"):
        cml.parse("<html><body>not CML</body></html>")
    orca = read(ETHYLENE).replace("Gaussian 16", "ORCA 6")
    with pytest.raises(cml.NotCml, match="only CML of Gaussian"):
        cml.parse(orca)
    text = read(ETHYLENE)
    head, rest = text.split("\n", 1)
    with pytest.raises(cml.NotCml, match="document type"):
        cml.parse(f'{head}\n<!DOCTYPE module [<!ENTITY a "aaaa">]>\n{rest}')
    with pytest.raises(cml.NotCml, match="well-formed"):
        cml.parse(text[: len(text) // 2])


def test_import_reads_cml_by_its_content():
    assert isinstance(imports.read_file(read(ETHYLENE), "output.cml"), common.ParsedFile)
    assert batch_import._looks_like_output(read(TS_PBE)[:65536])


# ---------- the import (T-IMP-60) ----------


def test_import_a_ts_and_its_single_points(open_client):
    # T-IMP-60: the opt+freq CML as a TS node, both single points onto it at their own
    # levels on its geometry level (D100), and G_qh from the CML's frequencies.
    plan = upload(open_client, "output.cml", read(TS))
    assert plan["program"] == "Gaussian"
    assert plan["chosen_step"] == 6
    assert {n["kind"] for n in plan["names"]} == {"basis"}
    result = commit(open_client, plan, **named(plan), label="TS2'-2 C1Ph", role="transition_state")
    node_id = result["node_id"]
    calcs = calculations(open_client, node_id)
    assert [c["type"] for c in calcs][-2:] == ["ts_optimization", "frequency"]
    assert calcs[-1]["composite_label"] == "Gaussian PBEPBE/modDZ IEFPCM(chloroform)"
    assert node(open_client, node_id)["label"] == "TS2'-2 C1Ph"
    # The step movie reads the stored copy again: the first and the final structure.
    response = open_client.get(f"/api/calculations/{calcs[-2]['id']}/steps")
    assert response.status_code == 200, response.text
    assert len(response.json()["frames"]) == 2

    plan = upload(open_client, "output.cml", read(TS_PBE), node_id)
    assert plan["mode"] == "onto"
    options = named(plan, basis="modQZ")
    sp = preview(open_client, plan, target_node_id=node_id, **options)["steps"][0]
    assert sp["level_label"] == "Gaussian PBEPBE-GD3MBJ/modQZ IEFPCM(chloroform)"
    assert sp["geometry_level_label"] == calcs[-1]["composite_label"]
    commit(open_client, plan, target_node_id=node_id, **options)

    plan = upload(open_client, "output.cml", read(TS_M06L), node_id)
    commit(open_client, plan, target_node_id=node_id)  # no basis set named: "?" (D104)
    labels = [c["composite_label"] for c in calculations(open_client, node_id)]
    assert labels[-1] == (
        "Gaussian M06L-GD3/? IEFPCM(chloroform) // Gaussian PBEPBE/modDZ IEFPCM(chloroform)"
    )

    key = level_key(open_client, labels[-2])
    g_qh = view(open_client, key, "G_qh")["values"][node_id]
    assert g_qh["value"] == pytest.approx(-1810.37394001 + 0.371271, abs=2e-5)
    assert g_qh["check"] is None  # one imaginary mode on a TS: nothing to note (D154)


def test_batch_import_of_a_folder_of_cml(open_client, tmp_path):
    # T-IMP-60: the single points find the TS by their structure; the CHKBASIS one takes the
    # batch's basis set (D104).
    folder = tmp_path / "deposit"
    folder.mkdir()
    for name in (TS, TS_PBE, TS_M06L, ETHYLENE):
        (folder / name).write_bytes((FIXTURES / name).read_bytes())
    plan = scan(open_client, folder)
    assert len(plan["rows"]) == 4 and plan["counts"]["basis_missing"] == 1
    plan = batch_preview(open_client, plan, missing_basis="modQZ", **batch_names(plan))
    response = run(open_client, plan, missing_basis="modQZ", **batch_names(plan))
    assert response.status_code == 200, response.text
    by_label = {n["label"]: n["id"] for n in open_client.get("/api/nodes").json()}
    found = calculations(open_client, by_label[TS.removesuffix(".cml")])
    assert [c["type"] for c in found][-4:] == [
        "ts_optimization",
        "frequency",
        "single_point",
        "single_point",
    ]
    m06l = next(c for c in found if c["level"]["method"] == "M06L")
    assert m06l["level"]["basis"] == "modQZ"
    assert ETHYLENE.removesuffix(".cml") in by_label
