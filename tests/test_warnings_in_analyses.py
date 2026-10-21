"""Warnings per frequency job and in the analyses (D158): T-EN-25, T-EN-27…29. Every frequency
job a node's energies use is checked against its role, naming its level; a frequency job that
is not at a stationary point is W-FREQ-NONSTAT; and W-OPT-INC, W-TERM and W-FREQ-NONSTAT go with
the values into profiles, the energy table, selectivities and turnovers. None changes a value or
a status (D19)."""

from chembook3d import investigation as inv
from chembook3d.models import Calculation, InvestigationInfo
from chembook3d.parsers.common import NOT_STATIONARY
from chembook3d.services import repairs
from tests import gaussian_text as g
from tests.test_energies import DZ, import_file, level_key, view
from tests.test_gaussian_parser import MINIMUM, TS
from tests.test_import import calculations, fixture, node, upload
from tests.test_pathway import edge, get, patch, post
from tests.test_selectivity import LEVEL, result, selectivity
from tests.test_ts_checks import NO_IMAGINARY, point

PBE0 = "Gaussian PBE1PBE/6-31G(D)"
UNMET = "Maximum displacement 0.000083 > 0.000060"


def codes(client, node_id: str) -> list[str]:
    return [w["code"] for w in node(client, node_id)["warnings"]]


def pbe0_frequency(frequencies, shift: float = 0.0) -> str:
    atoms = g.moved(g.WATER, shift)
    return g.output(g.step("#P PBE1PBE/6-31G(d) Freq", [atoms], -76.3, frequencies=frequencies))


def unconverged_frequency(atoms, frequencies=g.TS_FREQUENCIES, energy=-76.40) -> str:
    """An opt freq job whose frequency step Gaussian's check finds not at a stationary point."""
    return g.output(
        g.step(
            "#P B3LYP/6-31G(d) Opt Freq", [atoms, atoms], energy, converged=True, terminate=False
        ),
        g.step(
            "#P Geom=AllCheck Guess=TCheck SCRTyp=Grad RB3LYP/6-31G(d) Freq",
            [atoms],
            energy,
            frequencies=list(frequencies),
            internal=True,
            after=g.convergence_table(stationary=False),
        ),
    )


def test_each_frequency_job_is_checked_at_its_own_level(open_client):
    # T-EN-25: a later PBE0 frequency job without an imaginary mode no longer hides the
    # B3LYP one with one, on the card or in the B3LYP values; and the other way round.
    bent = point(open_client, "Bent", g.TS_FREQUENCIES, 0.0, "minimum")
    import_file(open_client, "pbe0.out", pbe0_frequency(list(NO_IMAGINARY)), bent)
    (warning,) = [w for w in node(open_client, bent)["warnings"] if w["code"].startswith("W-IMAG")]
    assert warning["code"] == "W-IMAG-MIN"
    assert warning["message"] == f"minimum with 1 imaginary frequency at {LEVEL} (-500.0 cm⁻¹)"
    b3lyp = view(open_client, level_key(open_client, LEVEL), "G_qh")["values"][bent]
    assert b3lyp["check"]["code"] == "W-IMAG-MIN" and b3lyp["value"] is not None

    good = point(open_client, "Good", NO_IMAGINARY, 0.1, "minimum")
    import_file(open_client, "pbe0-bad.out", pbe0_frequency(list(g.TS_FREQUENCIES), 0.1), good)
    found = [w for w in node(open_client, good)["warnings"] if w["code"].startswith("W-IMAG")]
    assert [w["message"] for w in found] == [
        f"minimum with 1 imaginary frequency at {PBE0} // {LEVEL} (-500.0 cm⁻¹)"
    ]
    assert view(open_client, level_key(open_client, LEVEL), "G")["values"][good]["check"] is None

    # A transition state with one good job and one bad one names only the bad level.
    ts = point(open_client, "TS", g.TS_FREQUENCIES, 0.2, "transition_state")
    import_file(open_client, "pbe0-ts.out", pbe0_frequency(list(NO_IMAGINARY), 0.2), ts)
    found = [w for w in node(open_client, ts)["warnings"] if w["code"].startswith("W-IMAG")]
    assert [w["message"] for w in found] == [
        f"transition state with 0 imaginary frequencies at {PBE0} // {LEVEL} (expected 1)"
    ]


def test_a_frequency_job_not_at_a_stationary_point(open_client):
    # T-EN-27: the public fixture's frequency step says "Maximum Displacement … NO".
    plan = upload(open_client, MINIMUM, fixture(MINIMUM))
    assert [w["code"] for w in plan["warnings"]] == ["W-FREQ-NONSTAT"]
    assert "Maximum Displacement 0.000083 0.000060 NO" in plan["warnings"][0]["message"]
    minimum = import_file(open_client, MINIMUM, fixture(MINIMUM))["node_id"]
    frequency = next(c for c in calculations(open_client, minimum) if c["type"] == "frequency")
    (warning,) = frequency["warnings"]
    assert warning == {
        "code": "W-FREQ-NONSTAT",
        "message": f"the frequency job at {DZ} is not at a stationary point ({UNMET})",
        "calculation_id": frequency["id"],
    }
    assert codes(open_client, minimum) == ["W-FREQ-NONSTAT"]
    key = level_key(open_client, DZ)
    for energy_type in ("E", "G", "G_qh"):
        value = view(open_client, key, energy_type)["values"][minimum]
        assert value["value"] is not None  # D19: the value stays
        message = f"the frequency job at the geometry level is not at a stationary point ({UNMET})"
        assert value["check"] == {
            "code": "W-FREQ-NONSTAT",
            "message": message,
            "issues": [{"code": "W-FREQ-NONSTAT", "message": message}],
        }
    assert node(open_client, minimum)["status"] == "done"

    # Gaussian found a stationary point after the TS's frequency job: nothing to say.
    ts = import_file(open_client, TS, fixture(TS))["node_id"]
    assert "W-FREQ-NONSTAT" not in codes(open_client, ts)
    # A met table with "Stationary point found" is not one either.
    met = g.output(
        g.step("#P B3LYP/6-31G(d) Opt Freq", [g.WATER, g.WATER], converged=True, terminate=False),
        g.step(
            "#P Geom=AllCheck RB3LYP/6-31G(d) Freq",
            [g.WATER],
            frequencies=list(NO_IMAGINARY),
            internal=True,
            after=g.convergence_table(stationary=True),
        ),
    )
    assert "W-FREQ-NONSTAT" not in codes(
        open_client, import_file(open_client, "m.out", met)["node_id"]
    )


def test_unfinished_and_failed_calculations_reach_the_analyses(open_client):
    # T-EN-28: a cut-off optimization (W-OPT-INC, W-TERM) and a TS whose frequency job is not
    # at a stationary point carry their warnings into the profile, the table, a selectivity
    # and a turnover; nothing changes a value or a status.
    cut = g.output(
        g.step("#P B3LYP/6-31G(d) Opt", [g.moved(g.WATER, 0.3), g.WATER], -76.42, terminate=False)
    )
    a = import_file(open_client, "cut.out", cut)["node_id"]
    status = node(open_client, a)["status"]
    assert {"W-OPT-INC", "W-TERM"} <= set(codes(open_client, a))
    ts = import_file(open_client, "ts.out", unconverged_frequency(g.moved(g.WATER, 0.05)))
    ts = ts["node_id"]
    patch(open_client, f"/nodes/{ts}", {"label": "TS-R", "role": "transition_state"})
    other = point(open_client, "TS-S", g.TS_FREQUENCIES, 0.1, "transition_state", -76.399)
    patch(open_client, f"/nodes/{a}", {"label": "A"})
    edge(open_client, a, ts)

    key = level_key(open_client, LEVEL)
    value = view(open_client, key, "E")["values"][a]
    assert value["value"] == -76.42
    assert [i["code"] for i in value["check"]["issues"]] == ["W-OPT-INC", "W-TERM"]
    assert value["check"]["message"] == (
        "the optimization at the geometry level did not finish; "
        "the energy calculation did not terminate normally"
    )
    body = {"paths": [[a, ts]], "level": key, "type": "E"}
    points = post(open_client, "/energies/profile", body, status=200)["profiles"][0]["points"]
    assert points[0]["check"]["code"] == "W-OPT-INC"
    assert points[1]["check"]["code"] == "W-FREQ-NONSTAT"
    rows = post(open_client, "/energies/table", body, status=200)["rows"]
    assert rows[0][-1] == value["check"]["message"]
    nonstationary = (
        f"the frequency job at the geometry level is not at a stationary point ({UNMET})"
    )
    assert rows[1][-1] == nonstationary

    created = selectivity(open_client, {"R": [ts], "S": [other]})
    found = result(open_client, created["id"])
    assert found["status"] == "ok"
    assert found["notes"] == [f"“TS-R”: {nonstationary}."]

    edge(open_client, ts, a)
    cycle = post(open_client, "/turnovers", {"name": "Cycle", "path": [a, ts, a], "level": key})
    notes = get(open_client, f"/turnovers/{cycle['id']}/result")["notes"]
    assert f"“TS-R”: {nonstationary}." in notes
    assert node(open_client, a)["status"] == status  # D19


def test_opening_an_older_notebook_reads_the_check_again(open_client):
    # T-EN-29: a frequency job imported before D158 gets W-FREQ-NONSTAT from its copied file
    # once, when the investigation is opened.
    node_id = import_file(open_client, "old.out", unconverged_frequency(g.WATER, NO_IMAGINARY))[
        "node_id"
    ]
    investigation = open_client.app.state.investigation
    folder = investigation.folder
    with investigation.sessions.begin() as session:
        for calculation in session.query(Calculation):
            if calculation.result is not None:
                printed = dict(calculation.result.printed)
                printed.pop(NOT_STATIONARY, None)
                calculation.result.printed = printed
        info = session.get(InvestigationInfo, 1)
        info.repairs = [r for r in info.repairs if r != "frequency_stationary"]
    assert "W-FREQ-NONSTAT" not in codes(open_client, node_id)
    open_client.post("/api/investigations/close")
    opened = inv.open_investigation(folder)
    try:
        with opened.sessions() as session:
            assert sorted(session.get(InvestigationInfo, 1).repairs) == sorted(repairs.REPAIRS)
    finally:
        opened.close()
    open_client.post("/api/investigations/open", json={"folder": str(folder)})
    assert codes(open_client, node_id) == ["W-FREQ-NONSTAT"]
