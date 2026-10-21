"""Spin contamination (D162): T-IMP-48…50. ⟨S²⟩ is read from Gaussian (before and after
annihilation, the last of the step) and ORCA (with the ideal value it prints) and kept with the
result; W-SPIN says when it is further from S(S+1) than 10 % (a singlet: above 0.1), on the
calculation, the node card and the import preview, and goes with the values into profiles, the
energy table and the analyses (D154, D158). It never changes a value or a status (D19)."""

import pytest

from chembook3d import investigation as inv
from chembook3d.models import Calculation, InvestigationInfo
from chembook3d.parsers import gaussian, orca
from chembook3d.parsers.common import S2, S2_AFTER, S2_IDEAL, SPIN_KEYS
from chembook3d.services import repairs, spin
from tests import gaussian_text as g
from tests.test_energies import import_file, level_key, view
from tests.test_import import calculations, node, upload
from tests.test_import_programs import import_new
from tests.test_orca_xtb_parsers import read
from tests.test_pathway import edge, patch, post
from tests.test_selectivity import LEVEL

CATION = g.moved(g.WATER, 0.02)  # H2O+, a doublet


def doublet(before: float, after: float | None = 0.7501, shift: float = 0.0, **extra) -> str:
    """A UB3LYP opt freq of H2O+ printing ⟨S²⟩ after every SCF."""
    atoms = g.moved(CATION, shift)
    common = {"charge": 1, "multiplicity": 2, "spin": (before, after)}
    return g.output(
        g.step(
            "#P UB3LYP/6-31G(d) Opt Freq",
            [atoms, atoms],
            -75.98,
            converged=True,
            terminate=False,
            **common,
        ),
        g.step(
            "#P Geom=AllCheck Guess=TCheck SCRTyp=Grad UB3LYP/6-31G(d) Freq",
            [atoms],
            -75.98,
            frequencies=[1650.0, 3700.0, 3800.0],
            internal=True,
            **common,
            **extra,
        ),
    )


def codes(client, node_id: str) -> list[str]:
    return [w["code"] for w in node(client, node_id)["warnings"]]


# ---------- T-IMP-48: reading ⟨S²⟩ ----------


def test_gaussian_keeps_the_last_values_of_the_step():
    text = g.output(
        g.step(
            "#P UB3LYP/6-31G(d) Opt",
            [CATION, CATION, CATION],
            charge=1,
            multiplicity=2,
            converged=True,
            spin=[(0.9100, 0.7600), (0.8000, 0.7550), (0.7590, 0.7501)],
        )
    )
    (step,) = gaussian.parse(text).steps
    assert (step.printed[S2], step.printed[S2_AFTER]) == ("0.7590", "0.7501")
    assert S2_IDEAL not in step.printed
    # Only the summary line (no annihilation printed): its value, nothing after.
    text = g.output(g.step("#P ROB3LYP/6-31G(d) SP", [CATION], multiplicity=2, spin=(0.75, None)))
    (step,) = gaussian.parse(text).steps
    assert step.printed[S2] == "0.7500" and S2_AFTER not in step.printed
    # A closed-shell job prints none.
    (step,) = gaussian.parse(g.opt_freq(g.WATER, g.WATER)).steps[:1]
    assert not set(SPIN_KEYS) & set(step.printed)


def test_orca_reads_the_expectation_value_and_the_ideal_one(open_client):
    # The public fixture: <S**2> = 0.778154 for a doublet, 3.8 % off 0.75: no W-SPIN.
    (step,) = orca.parse(read("orca", "dvb_sp_un_dft.out")).steps
    assert (step.printed[S2], step.printed[S2_IDEAL]) == ("0.778154", "0.750000")
    node_id = import_new(open_client, "orca", "dvb_sp_un_dft.out")["node_id"]
    (calculation,) = calculations(open_client, node_id)
    assert calculation["result"]["printed"][S2] == "0.778154"
    assert calculation["spin"] == {
        "s2": 0.778154,
        "after": None,
        "ideal": 0.75,
        "multiplicity": 2,
        "contaminated": False,
    }
    assert "W-SPIN" not in codes(open_client, node_id)
    assert calculation["warnings"] == []


@pytest.mark.parametrize(
    ("s2", "multiplicity", "contaminated"),
    [
        (0.82, 2, False),  # 9.3 % off 0.75
        (0.83, 2, True),  # 10.7 %
        (2.15, 3, False),  # 7.5 % off 2
        (2.25, 3, True),  # 12.5 %
        (3.70, 4, False),  # 5.7 % off 3.75
        (0.05, 1, False),  # a singlet: up to 0.1
        (0.11, 1, True),
        (1.01, 1, True),  # a broken-symmetry singlet
    ],
)
def test_the_threshold(s2, multiplicity, contaminated):
    found = spin.read({S2: str(s2)}, multiplicity)
    assert found.contaminated is contaminated
    assert found.ideal == pytest.approx(((multiplicity - 1) / 2) * ((multiplicity + 1) / 2))


# ---------- T-IMP-49: W-SPIN ----------


def test_a_contaminated_doublet_warns_everywhere_it_is_used(open_client):
    text = doublet(0.9512, 0.7612)
    plan = upload(open_client, "cation.out", text)
    expected = (
        "spin contamination: ⟨S²⟩ = 0.9512 before annihilation (0.7612 after), "
        "expected 0.7500 for multiplicity 2"
    )
    found = [w["message"] for w in plan["warnings"] if w["code"] == "W-SPIN"]
    assert found == [f"Step 1: {expected} (D162).", f"Step 2: {expected} (D162)."]

    a = import_file(open_client, "cation.out", text)["node_id"]
    status = node(open_client, a)["status"]
    for calculation in calculations(open_client, a):
        assert calculation["spin"]["contaminated"] is True
        assert calculation["spin"]["after"] == 0.7612
        assert {"code": "W-SPIN", "message": expected, "calculation_id": calculation["id"]} in (
            calculation["warnings"]
        )
    assert codes(open_client, a).count("W-SPIN") == 2

    key = level_key(open_client, LEVEL)
    for energy_type in ("E", "G", "G_qh"):
        value = view(open_client, key, energy_type)["values"][a]
        assert value["value"] is not None  # D19: the value stays
        assert value["check"]["code"] == "W-SPIN"
        assert value["check"]["message"] == (
            "spin contamination: ⟨S²⟩ of the energy calculation is 0.9512 before annihilation "
            "(0.7612 after), expected 0.7500 for multiplicity 2"
        )

    # The profile, the table and its CSV carry it.
    b = import_file(open_client, "b.out", doublet(0.7590, shift=0.2))["node_id"]
    patch(open_client, f"/nodes/{a}", {"label": "A"})
    patch(open_client, f"/nodes/{b}", {"label": "B"})
    assert "W-SPIN" not in codes(open_client, b)
    edge(open_client, a, b)
    body = {"paths": [[a, b]], "level": key, "type": "G"}
    points = post(open_client, "/energies/profile", body, status=200)["profiles"][0]["points"]
    assert points[0]["check"]["code"] == "W-SPIN" and points[1]["check"] is None
    rows = post(open_client, "/energies/table", body, status=200)["rows"]
    assert rows[0][-1].startswith("spin contamination: ⟨S²⟩ of the energy calculation is 0.9512")
    assert rows[1][-1] == ""
    response = open_client.post("/api/energies/table.csv", json=body)
    assert "spin contamination" in response.content.decode("utf-8-sig")
    assert node(open_client, a)["status"] == status  # D19


def test_the_frequency_job_is_judged_for_free_energies(open_client):
    # Only the frequency job is contaminated: E (from a clean single point) has no W-SPIN,
    # H, G and G_qh, which use the frequency job, have it.
    clean = g.output(
        g.step(
            "#P UB3LYP/6-31G(d) Opt",
            [CATION, CATION],
            -75.98,
            charge=1,
            multiplicity=2,
            converged=True,
            spin=(0.7590, 0.7501),
        )
    )
    node_id = import_file(open_client, "opt.out", clean)["node_id"]
    freq = g.output(
        g.step(
            "#P UB3LYP/6-31G(d) Freq",
            [CATION],
            -75.97,
            charge=1,
            multiplicity=2,
            frequencies=[1650.0, 3700.0, 3800.0],
            spin=(0.9000, 0.7600),
        )
    )
    import_file(open_client, "freq.out", freq, node_id)
    key = level_key(open_client, LEVEL)
    # The frequency job is the latest calculation with an energy, so it is E's source too.
    e = view(open_client, key, "E")["values"][node_id]
    assert e["check"]["message"].startswith("spin contamination: ⟨S²⟩ of the energy calculation")
    sp = g.output(
        g.step(
            "#P UB3LYP/6-31G(d) SP",
            [CATION],
            -75.99,
            charge=1,
            multiplicity=2,
            spin=(0.7590, 0.7501),
        )
    )
    import_file(open_client, "sp.out", sp, node_id)
    assert view(open_client, key, "E")["values"][node_id]["check"] is None
    value = view(open_client, key, "G")["values"][node_id]
    assert value["check"]["issues"] == [
        {
            "code": "W-SPIN",
            "message": "spin contamination: ⟨S²⟩ of the frequency calculation is 0.9000 before "
            "annihilation (0.7600 after), expected 0.7500 for multiplicity 2",
        }
    ]


def test_a_singlet_with_spin_is_contaminated_above_one_tenth(open_client):
    text = g.output(g.step("#P UB3LYP/6-31G(d) SP", [g.WATER], -76.40, spin=(1.0100, 0.1200)))
    node_id = import_file(open_client, "bs.out", text)["node_id"]
    (calculation,) = calculations(open_client, node_id)
    (warning,) = [w for w in calculation["warnings"] if w["code"] == "W-SPIN"]
    assert warning["message"] == (
        "spin contamination: ⟨S²⟩ = 1.0100 before annihilation (0.1200 after), "
        "expected 0 for a singlet (more than 0.1 is contaminated)"
    )
    atoms = g.moved(g.WATER, 0.3)
    quiet = g.output(g.step("#P UB3LYP/6-31G(d) SP", [atoms], -76.40, spin=(0.0500, 0.0)))
    other = import_file(open_client, "quiet.out", quiet)["node_id"]
    assert other != node_id
    (calculation,) = calculations(open_client, other)
    assert calculation["spin"]["contaminated"] is False
    assert "W-SPIN" not in [w["code"] for w in calculation["warnings"]]


# ---------- T-IMP-50: older investigations ----------


def test_opening_an_older_notebook_reads_the_spin_again(open_client):
    node_id = import_file(open_client, "old.out", doublet(0.9512, 0.7612))["node_id"]
    investigation = open_client.app.state.investigation
    folder = investigation.folder
    with investigation.sessions.begin() as session:
        for calculation in session.query(Calculation):
            if calculation.result is not None:
                printed = dict(calculation.result.printed)
                for key in SPIN_KEYS:
                    printed.pop(key, None)
                calculation.result.printed = printed
        info = session.get(InvestigationInfo, 1)
        info.repairs = [r for r in info.repairs if r != "spin_values"]
    assert "W-SPIN" not in codes(open_client, node_id)
    assert all(c["spin"] is None for c in calculations(open_client, node_id))
    open_client.post("/api/investigations/close")
    opened = inv.open_investigation(folder)
    try:
        with opened.sessions() as session:
            assert sorted(session.get(InvestigationInfo, 1).repairs) == sorted(repairs.REPAIRS)
    finally:
        opened.close()
    open_client.post("/api/investigations/open", json={"folder": str(folder)})
    assert codes(open_client, node_id).count("W-SPIN") == 2
    assert all(c["spin"]["s2"] == 0.9512 for c in calculations(open_client, node_id))
