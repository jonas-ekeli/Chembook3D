"""The G model and the frequency scale factor (D163): T-IMP-51…53, T-EN-41…42. "G" as printed
is pure RRHO in Gaussian, Grimme's quasi-RRHO in ORCA and xTB's modified RRHO; ORCA prints its
frequencies already scaled by ScalFreq. Each frequency job keeps its model and scale factor, the
calculation panel and the table show them, and W-SCALE and W-G-MODEL go with the values of one
composite level that mix them (D154). ORCA's G is still used as printed (A17)."""

from chembook3d import investigation as inv
from chembook3d.models import Calculation, InvestigationInfo
from chembook3d.parsers import gaussian, orca, xtb
from chembook3d.parsers.common import FREQUENCY_SCALE, G_MODEL, THERMO_MODEL_KEYS
from chembook3d.services import repairs
from tests import gaussian_text as g
from tests.test_energies import import_file, level_key, view
from tests.test_import import calculations
from tests.test_orca_xtb_parsers import read
from tests.test_pathway import edge, get, patch, post

ORCA_LEVEL = "ORCA B3LYP/STO-3G"
GAUSSIAN_LEVEL = "Gaussian B3LYP/6-31G(D)"
ORCA_SCALE_LINE = "Scaling factor for frequencies =  1.000000000  (already applied!)"


def orca_scaled(scale: str = "0.970000000") -> str:
    """The public ORCA frequency job as ORCA prints it with `%freq ScalFreq 0.97 end`: the same
    file with the scale line changed (the frequencies themselves are left as they are)."""
    text = read("orca", "dvb_ir.out")
    assert ORCA_SCALE_LINE in text
    return text.replace(
        ORCA_SCALE_LINE, f"Scaling factor for frequencies =  {scale}  (already applied!)"
    )


def orca_plain_rrho() -> str:
    """The public ORCA frequency job as ORCA prints it with its quasi-RRHO turned off."""
    text = read("orca", "dvb_ir.out")
    text = text.replace("Quasi RRHO          ...     True", "Quasi RRHO          ...     False")
    return text.replace("Vibrational entropy computed according to the QRRHO of S. Grimme", "")


def gaussian_opt_freq(route_extra: str = "", shift: float = 0.0) -> str:
    atoms = g.moved(g.WATER, shift)
    return g.output(
        g.step(
            f"#P B3LYP/6-31G(d) Opt Freq{route_extra}",
            [atoms, atoms],
            converged=True,
            terminate=False,
        ),
        g.step(
            "#P Geom=AllCheck Guess=TCheck SCRTyp=Grad RB3LYP/6-31G(d) Freq",
            [atoms],
            frequencies=[1650.0, 3700.0, 3800.0],
            internal=True,
        ),
    )


def thermo_of(client, node_id: str) -> dict:
    (found,) = [c["thermo"] for c in calculations(client, node_id) if c["thermo"] is not None]
    return found


def codes(value: dict) -> list[str]:
    return [i["code"] for i in (value["check"] or {}).get("issues", [])]


# ---------- T-IMP-51: reading the model and the scale factor ----------


def test_each_program_names_its_model_and_scale():
    (_, freq) = gaussian.parse(read("gaussian", "aminationTS-full-unfrz-c1.log")).steps
    assert (freq.printed[G_MODEL], freq.printed[FREQUENCY_SCALE]) == ("RRHO", "1")
    (step,) = orca.parse(read("orca", "dvb_ir.out")).steps
    assert (step.printed[G_MODEL], step.printed[FREQUENCY_SCALE]) == ("qRRHO", "1.000000000")
    (step,) = orca.parse(orca_scaled()).steps
    assert step.printed[FREQUENCY_SCALE] == "0.970000000"
    (step,) = orca.parse(orca_plain_rrho()).steps
    assert step.printed[G_MODEL] == "RRHO"
    (step,) = xtb.parse(read("xtb", "dvb_ir.out")).steps
    assert (step.printed[G_MODEL], step.printed[FREQUENCY_SCALE]) == ("mRRHO", "1.0000000")
    # Steps without frequencies name neither.
    (step,) = orca.parse(read("orca", "dvb_sp_un_dft.out")).steps
    assert not set(THERMO_MODEL_KEYS) & set(step.printed)


def test_the_scale_comes_from_the_route_or_the_input_when_not_printed():
    # Gaussian: Scale= in the route of an opt freq reaches its frequency step; Freq(Scale=…).
    (opt, freq) = gaussian.parse(gaussian_opt_freq(" Scale=0.9700")).steps
    assert freq.printed[FREQUENCY_SCALE] == "0.9700" and FREQUENCY_SCALE not in opt.printed
    text = g.output(
        g.step(
            "#P B3LYP/6-31G(d) Freq=(Scale=0.98)", [g.WATER], frequencies=[1650.0, 3700.0, 3800.0]
        )
    )
    (freq,) = gaussian.parse(text).steps
    assert freq.printed[FREQUENCY_SCALE] == "0.98"
    # ORCA: without the printed line, the input's %freq ScalFreq.
    text = read("orca", "dvb_ir.out").replace(ORCA_SCALE_LINE, "")
    text = text.replace(
        "|  1> !  b3lyp sto-3g usesym freq",
        "|  1> !  b3lyp sto-3g usesym freq\n|  2> %freq ScalFreq 0.96 end",
    )
    (step,) = orca.parse(text).steps
    assert step.printed[FREQUENCY_SCALE] == "0.96"
    # xTB: the scaling factor of its thermo setup.
    text = read("xtb", "dvb_ir.out").replace(
        ":  scaling factor                  1.0000000      :",
        ":  scaling factor                  0.9500000      :",
    )
    (step,) = xtb.parse(text).steps
    assert step.printed[FREQUENCY_SCALE] == "0.9500000"


# ---------- T-IMP-52: shown with the calculation ----------


def test_the_calculation_shows_its_model_and_scale(open_client):
    node_id = import_file(open_client, "dvb_ir.out", orca_scaled())["node_id"]
    assert thermo_of(open_client, node_id) == {
        "program": "ORCA",
        "model": "qRRHO",
        "model_text": "Grimme's quasi-RRHO entropy (low modes treated as free rotors)",
        "scale": 0.97,
        "frequencies_scaled": True,
        "qh_scale": 0.97,
    }
    other = import_file(open_client, "water.out", gaussian_opt_freq(" Scale=0.9700"))["node_id"]
    found = thermo_of(open_client, other)
    assert (found["model"], found["scale"], found["frequencies_scaled"], found["qh_scale"]) == (
        "RRHO",
        0.97,
        False,
        1.0,
    )
    # Calculations without frequencies have none.
    sp = import_file(open_client, "sp.out", g.single_point(g.moved(g.WATER, 0.4)))["node_id"]
    assert all(c["thermo"] is None for c in calculations(open_client, sp))
    # The value's details name it; the level's options and the profile list the models.
    key = level_key(open_client, ORCA_LEVEL)
    value = view(open_client, key, "G")["values"][node_id]
    assert value["details"]["thermo"]["model"] == "qRRHO"
    option = next(o for o in get(open_client, "/energies/options")["levels"] if o["key"] == key)
    assert option["g_models"] == ["ORCA's qRRHO"]
    profile = post(
        open_client,
        "/energies/profile",
        {"paths": [[node_id]], "level": key, "type": "G"},
        status=200,
    )
    assert profile["g_models"] == ["ORCA's qRRHO"]


# ---------- T-EN-41: mixed scale factors and models ----------


def test_mixed_scale_factors_warn_with_the_values(open_client):
    plain = import_file(open_client, "plain.out", read("orca", "dvb_ir.out"))["node_id"]
    scaled = import_file(open_client, "scaled.out", orca_scaled())["node_id"]
    assert plain != scaled
    patch(open_client, f"/nodes/{plain}", {"label": "A"})
    patch(open_client, f"/nodes/{scaled}", {"label": "B"})
    key = level_key(open_client, ORCA_LEVEL)
    assert codes(view(open_client, key, "E")["values"][scaled]) == []
    for energy_type in ("H", "G", "G_qh"):
        values = view(open_client, key, energy_type)["values"]
        assert values[scaled]["value"] is not None  # D19: the value stays
        assert "W-SCALE" in codes(values[scaled]) and "W-SCALE" in codes(values[plain])
        assert "W-G-MODEL" not in codes(values[scaled])
    issue = next(
        i
        for i in view(open_client, key, "G_qh")["values"][scaled]["check"]["issues"]
        if i["code"] == "W-SCALE"
    )
    assert issue["message"] == (
        "the frequencies G_qh is computed from are scaled by 0.97 here, but unscaled on other "
        "nodes at this level (mixed frequency scale factors)"
    )
    # The profile, the table and its CSV carry it; the table names each row's model and scale.
    edge(open_client, plain, scaled)
    body = {"paths": [[plain, scaled]], "level": key, "type": "G"}
    points = post(open_client, "/energies/profile", body, status=200)["profiles"][0]["points"]
    assert all(p["check"]["code"] == "W-SCALE" for p in points)
    table = post(open_client, "/energies/table", body, status=200)
    column = table["columns"].index("Thermochemistry")
    assert [r[column] for r in table["rows"]] == [
        "ORCA's qRRHO; scale 1",
        "ORCA's qRRHO; scale 0.97 (in its frequencies and so in G_qh)",
    ]
    assert all("mixed frequency scale factors" in r[-1] for r in table["rows"])
    csv = open_client.post("/api/energies/table.csv", json=body).content.decode("utf-8-sig")
    assert "mixed frequency scale factors" in csv and "ORCA's qRRHO; scale 0.97" in csv


def test_mixed_models_warn_on_g_only(open_client):
    qrrho = import_file(open_client, "q.out", read("orca", "dvb_ir.out"))["node_id"]
    rrho = import_file(open_client, "r.out", orca_plain_rrho())["node_id"]
    key = level_key(open_client, ORCA_LEVEL)
    values = view(open_client, key, "G")["values"]
    assert codes(values[qrrho]) == ["W-G-MODEL"] and codes(values[rrho]) == ["W-G-MODEL"]
    assert values[rrho]["check"]["message"] == (
        "this G is ORCA's RRHO as printed, but other nodes at this level have ORCA's qRRHO (G "
        "from different thermochemistry models; G_qh is computed one way for all)"
    )
    # G_qh is the app's own for both; H does not depend on the entropy model.
    for energy_type in ("H", "G_qh"):
        assert codes(view(open_client, key, energy_type)["values"][rrho]) == []
    option = next(o for o in get(open_client, "/energies/options")["levels"] if o["key"] == key)
    assert option["g_models"] == ["ORCA's RRHO", "ORCA's qRRHO"]


# ---------- T-EN-42: Gaussian's scale is in its printed thermochemistry only ----------


def test_gaussian_scale_changes_g_but_not_g_qh(open_client):
    plain = import_file(open_client, "a.out", gaussian_opt_freq())["node_id"]
    scaled = import_file(open_client, "b.out", gaussian_opt_freq(" Scale=0.9700", 0.3))["node_id"]
    key = level_key(open_client, GAUSSIAN_LEVEL)
    for energy_type in ("H", "G"):
        values = view(open_client, key, energy_type)["values"]
        assert codes(values[scaled]) == ["W-SCALE"] and codes(values[plain]) == ["W-SCALE"]
    values = view(open_client, key, "G_qh")["values"]
    assert codes(values[scaled]) == [] and codes(values[plain]) == []


# ---------- T-IMP-53: older investigations ----------


def test_opening_an_older_notebook_reads_the_scale_again(open_client):
    plain = import_file(open_client, "plain.out", read("orca", "dvb_ir.out"))["node_id"]
    scaled = import_file(open_client, "scaled.out", orca_scaled())["node_id"]
    investigation = open_client.app.state.investigation
    folder = investigation.folder
    with investigation.sessions.begin() as session:
        for calculation in session.query(Calculation):
            if calculation.result is not None:
                printed = dict(calculation.result.printed)
                for key in THERMO_MODEL_KEYS:
                    printed.pop(key, None)
                calculation.result.printed = printed
        info = session.get(InvestigationInfo, 1)
        info.repairs = [r for r in info.repairs if r != "thermo_models"]
    # Before the repair: the program's model and a scale of 1, so nothing is mixed.
    assert thermo_of(open_client, scaled)["scale"] == 1.0
    assert thermo_of(open_client, scaled)["model"] == "qRRHO"
    key = level_key(open_client, ORCA_LEVEL)
    assert codes(view(open_client, key, "G")["values"][scaled]) == []
    open_client.post("/api/investigations/close")
    opened = inv.open_investigation(folder)
    try:
        with opened.sessions() as session:
            assert sorted(session.get(InvestigationInfo, 1).repairs) == sorted(repairs.REPAIRS)
    finally:
        opened.close()
    open_client.post("/api/investigations/open", json={"folder": str(folder)})
    assert thermo_of(open_client, scaled)["scale"] == 0.97
    assert thermo_of(open_client, plain)["scale"] == 1.0
    assert codes(view(open_client, key, "G")["values"][scaled]) == ["W-SCALE"]
