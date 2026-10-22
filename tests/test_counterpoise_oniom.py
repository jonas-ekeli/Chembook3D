"""Counterpoise and ONIOM energies (D161): T-IMP-44…47, T-EN-39…40. A Counterpoise=N job's
energy is its counterpoise-corrected total, with the BSSE kept; an ONIOM job's is its
extrapolated energy at a level of its own named by its layers. A job cut off before its total
has no energy and W-PARSE. The energy view counts a counterpoise job as the complex's
uncorrected energy unless "with BSSE" is chosen, which adds each node's BSSE at the level
(the counterpoise correction); it is off by default, so no value changes unless asked."""

import pytest

from chembook3d import investigation as inv
from chembook3d.models import Calculation, InvestigationInfo
from chembook3d.parsers import gaussian
from chembook3d.parsers.common import BSSE
from chembook3d.services import repairs
from tests import gaussian_text as g
from tests.test_energies import import_file, level_key, view
from tests.test_import import calculations, upload
from tests.test_pathway import edge, get, post

LEVEL = "Gaussian B3LYP/6-31G(D)"
ONIOM_LEVEL = "Gaussian ONIOM(B3LYP/6-31G(D):UFF)"


# ---------- T-IMP-44: counterpoise ----------


@pytest.mark.parametrize(
    "options", [{}, {"g16": False}, {"fragment_structures": True}], ids=["g16", "g09", "ghosts"]
)
def test_a_counterpoise_job_takes_its_corrected_total(options):
    # The fragments' SCF energies (the last one −76.4500) and structures are not the complex's.
    step = gaussian.parse(g.counterpoise_sp(**options)).steps[0]
    assert step.route.counterpoise == 2
    assert step.scf_energy == pytest.approx(g.DIMER_CORRECTED)
    assert float(step.printed[BSSE]) == pytest.approx(g.DIMER_BSSE)
    assert step.energy_label == "E(counterpoise)"
    assert "corrected energy" in step.printed["energy"]
    assert len(step.final_geometry) == 6 and step.geometry_energies == [step.scf_energy]
    assert step.missing == []


def test_an_imported_counterpoise_job_keeps_its_bsse(open_client):
    node_id = import_file(open_client, "cp.log", g.counterpoise_sp())["node_id"]
    (calculation,) = calculations(open_client, node_id)
    assert calculation["result"]["energy"] == pytest.approx(g.DIMER_CORRECTED)
    assert calculation["result"]["printed"]["energy_label"] == "E(counterpoise)"
    assert float(calculation["result"]["printed"][BSSE]) == pytest.approx(g.DIMER_BSSE)
    assert not calculation["parse_warnings"]
    # A counterpoise job is a level like any other: the same method and basis set.
    assert calculation["level"]["method"] == "B3LYP"


# ---------- T-IMP-45: ONIOM ----------


def test_an_oniom_job_takes_its_extrapolated_energy():
    first, freq = gaussian.parse(g.oniom_opt_freq()).steps
    assert first.route.method == "ONIOM(B3LYP/6-31G(D):UFF)" and first.route.basis is None
    assert first.geometry_energies == pytest.approx(
        [g.oniom_energy(), g.oniom_energy((-76.4100, 0.0070, 0.0110))]
    )
    assert first.scf_energy == pytest.approx(-76.4060)
    assert first.energy_label == freq.energy_label == "E(ONIOM)"
    assert first.missing == [] and freq.missing == []
    assert (first.charge, first.multiplicity) == (0, 1)
    # Options after the layers change the energy and stay part of the method.
    route = gaussian.parse_route("#P ONIOM(B3LYP/6-31G*:UFF)=EmbedCharge SP")
    assert route.method == "ONIOM(B3LYP/6-31G(D):UFF)=EMBEDCHARGE"


def test_an_oniom_opt_freq_is_one_level_with_g(open_client):
    node_id = import_file(open_client, "oniom.log", g.oniom_opt_freq())["node_id"]
    found = calculations(open_client, node_id)
    assert {c["level"]["method"] for c in found} == {"ONIOM(B3LYP/6-31G(D):UFF)"}
    assert all(not c["parse_warnings"] for c in found)
    key = level_key(open_client, ONIOM_LEVEL)
    assert view(open_client, key, "E")["values"][node_id]["value"] == pytest.approx(-76.4060)
    assert view(open_client, key, "G")["values"][node_id]["value"] == pytest.approx(-76.4026)


# ---------- T-IMP-46: cut off before the total ----------


def test_a_job_cut_off_before_its_total_has_no_energy(open_client):
    cut = gaussian.parse(g.counterpoise_sp(cut_off=True)).steps[0]
    assert cut.scf_energy is None and BSSE not in cut.printed
    assert cut.missing[0].startswith("counterpoise-corrected energy (Counterpoise=2")
    layers = [g.ONIOM_LAYERS, None]  # the second structure ends before its extrapolation
    cut_oniom = g.output(g.oniom_step(g.ONIOM_ROUTE, [g.WATER_DIMER, g.WATER_DIMER], layers))
    oniom = gaussian.parse(cut_oniom).steps[0]
    assert oniom.scf_energy is None
    assert oniom.missing[0].startswith("ONIOM extrapolated energy")
    for name, text, missing in (
        ("cp.log", g.counterpoise_sp(cut_off=True), "counterpoise-corrected energy"),
        ("oniom.log", cut_oniom, "ONIOM extrapolated energy"),
    ):
        plan = upload(open_client, name, text)
        assert any(w["code"] == "W-PARSE" and missing in w["message"] for w in plan["warnings"]), (
            plan["warnings"]
        )


# ---------- T-IMP-47: older investigations ----------


def test_opening_an_older_notebook_reads_the_counterpoise_total_again(open_client):
    node_id = import_file(open_client, "cp.log", g.counterpoise_sp())["node_id"]
    investigation = open_client.app.state.investigation
    folder = investigation.folder
    with investigation.sessions.begin() as session:
        for calculation in session.query(Calculation):
            calculation.result.energy = -76.4500  # what the app read before: the last fragment
            calculation.result.printed = {}
        info = session.get(InvestigationInfo, 1)
        info.repairs = [r for r in info.repairs if r != "counterpoise_energies"]
    open_client.post("/api/investigations/close")
    opened = inv.open_investigation(folder)
    try:
        with opened.sessions() as session:
            assert sorted(session.get(InvestigationInfo, 1).repairs) == sorted(repairs.REPAIRS)
    finally:
        opened.close()
    open_client.post("/api/investigations/open", json={"folder": str(folder)})
    (calculation,) = calculations(open_client, node_id)
    assert calculation["result"]["energy"] == pytest.approx(g.DIMER_CORRECTED)
    assert float(calculation["result"]["printed"][BSSE]) == pytest.approx(g.DIMER_BSSE)


# ---------- T-EN-39, T-EN-40: BSSE in the energy view ----------


def association(client) -> tuple[str, str, str]:
    """A water monomer and the water dimer (opt freq), a Counterpoise=2 single point on the
    dimer, and an edge from monomer to dimer; returns (monomer, dimer, level key)."""
    monomer = import_file(client, "water.log", g.opt_freq(g.WATER, g.WATER, -76.40))["node_id"]
    dimer = g.opt_freq(g.WATER_DIMER, g.WATER_DIMER, g.DIMER_ENERGY)
    complex_id = import_file(client, "dimer.log", dimer)["node_id"]
    import_file(client, "cp.log", g.counterpoise_sp(), complex_id)
    edge(client, monomer, complex_id)
    return monomer, complex_id, level_key(client, LEVEL)


def test_bsse_is_applied_only_when_chosen(open_client):
    # T-EN-39: off by default the counterpoise job counts as the complex's uncorrected energy,
    # the same as its optimization; "with BSSE" adds the correction to E, H, G and G_qh.
    monomer, dimer, key = association(open_client)
    option = next(o for o in get(open_client, "/energies/options")["levels"] if o["key"] == key)
    assert option["bsse"] is True
    for energy_type, correction in (("E", 0.0), ("G", 0.0034)):
        plain = view(open_client, key, energy_type)
        assert plain["bsse"] is False
        value = plain["values"][dimer]
        assert value["value"] == pytest.approx(g.DIMER_ENERGY + correction)
        assert value["details"]["bsse"] == pytest.approx(g.DIMER_BSSE)
        assert value["details"]["bsse_applied"] is False
        shown = get(open_client, f"/energies/view?level={key}&type={energy_type}&bsse=true")
        assert shown["values"][dimer]["value"] == pytest.approx(g.DIMER_CORRECTED + correction)
        assert shown["values"][dimer]["details"]["bsse_applied"] is True
        # A node without a counterpoise job is never changed.
        assert shown["values"][monomer] == plain["values"][monomer]
        assert "bsse" not in plain["values"][monomer]["details"]


def test_profile_and_table_with_bsse(open_client):
    # T-EN-40: the association step is less exothermic by the BSSE, and the table says so.
    monomer, dimer, key = association(open_client)
    body = {"paths": [[monomer, dimer]], "reference_id": monomer, "level": key, "type": "E"}
    plain = post(open_client, "/energies/profile", body, status=200)
    corrected = post(open_client, "/energies/profile", {**body, "bsse": True}, status=200)
    assert plain["bsse"] is False and corrected["bsse"] is True
    before, after = (p["profiles"][0]["points"][1]["relative"] for p in (plain, corrected))
    assert after - before == pytest.approx(g.DIMER_BSSE)
    table = post(open_client, "/energies/table", body, status=200)
    assert "BSSE correction (hartree)" not in table["columns"]
    table = post(open_client, "/energies/table", {**body, "bsse": True}, status=200)
    column = table["columns"].index("BSSE correction (hartree)")
    assert [row[column] for row in table["rows"]] == ["", f"{g.DIMER_BSSE:.8f}"]
    # The chart title and the table's Level column say the BSSE was added.
    assert corrected["level_label"] == f"{LEVEL} with BSSE" and plain["level_label"] == LEVEL
    assert {row[table["columns"].index("Level")] for row in table["rows"]} == {f"{LEVEL} with BSSE"}
