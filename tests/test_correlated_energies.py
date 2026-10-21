"""Correlated single-point energies (D153): T-IMP-38…40. Gaussian prints the SCF energy of a
correlated method's reference first; the energy of a double hybrid, MPn, CCSD(T) or QCISD(T)
step is its own printed total, and a correlated method whose total is not read gives no
energy instead of the SCF part."""

import pytest

from chembook3d.parsers import gaussian
from tests import gaussian_text as g
from tests.test_energies import import_file, level_key, view
from tests.test_import import calculations, upload

SCF = -76.40
OPT_END = g.moved(g.WATER, 0.01)


def correlated_sp(route: str, *lines: str, atoms=g.WATER) -> str:
    """A single point whose correlated totals follow its SCF line, as Gaussian 16 prints them."""
    text = g.single_point(atoms, route=route, energy=SCF)
    scf = next(line for line in text.splitlines() if line.startswith(" SCF Done:"))
    return text.replace(scf, "\n".join([scf, *lines]))


DOUBLE_HYBRID = (" E2(B2PLYPD3) =    -0.3000000000D+00 E(B2PLYPD3) =    -0.76700000000D+02",)
MP_SERIES = (
    " E2 =    -0.2000000000D+00 EUMP2 =    -0.76600000000D+02",
    " E3=       -0.40000000D-02        EUMP3=      -0.76604000D+02",
    " E4(DQ)=   -0.10000000D-02        UMP4(DQ)=   -0.76605000D+02",
    " E4(SDQ)=  -0.20000000D-02        UMP4(SDQ)=  -0.76606000D+02",
    " E4(SDTQ)= -0.30000000D-02        UMP4(SDTQ)= -0.76607000D+02",
)
TRIPLES = (*MP_SERIES[:4], " CCSD(T)= -0.76610000000D+02")


@pytest.mark.parametrize(
    ("route", "lines", "energy", "label"),
    [
        ("#P B2PLYPD3/def2TZVP SP", DOUBLE_HYBRID, -76.7, "E(B2PLYPD3)"),
        # The printed name follows the dispersion, not the route's spelling.
        ("#P B2PLYP/def2TZVP EmpiricalDispersion=GD3 SP", DOUBLE_HYBRID, -76.7, "E(B2PLYPD3)"),
        ("#P MP2/def2TZVP SP", MP_SERIES[:1], -76.6, "EUMP2"),
        ("#P MP2(Full)/def2TZVP SP", MP_SERIES[:1], -76.6, "EUMP2"),
        ("#P MP3/def2TZVP SP", MP_SERIES[:2], -76.604, "EUMP3"),
        ("#P MP4/def2TZVP SP", MP_SERIES, -76.607, "UMP4(SDTQ)"),
        ("#P MP4(SDQ)/def2TZVP SP", MP_SERIES, -76.606, "UMP4(SDQ)"),
        # CCSD(T) prints MP2, MP3 and MP4(SDQ) totals on the way; only its own total counts.
        ("#P CCSD(T)/cc-pVTZ SP", TRIPLES, -76.61, "CCSD(T)"),
    ],
)
def test_a_correlated_single_point_takes_its_own_total(route, lines, energy, label):
    # T-IMP-38
    step = gaussian.parse(correlated_sp(route, *lines)).steps[0]
    assert step.scf_energy == pytest.approx(energy, abs=1e-9)
    assert step.energy_label == label
    assert label in step.printed["energy"]
    assert step.missing == []


def test_scf_methods_keep_the_scf_energy():
    step = gaussian.parse(g.single_point(g.WATER, energy=SCF)).steps[0]
    assert step.scf_energy == SCF and step.energy_label is None and "energy" not in step.printed


def test_a_correlated_method_without_its_total_has_no_energy():
    # T-IMP-39: CCSD's total is not read, and a cut-off MP2 job never printed EUMP2. Neither
    # falls back to the SCF energy of the reference.
    for route, lines in (("#P CCSD/cc-pVTZ SP", TRIPLES[:3]), ("#P MP2/def2TZVP SP", ())):
        step = gaussian.parse(correlated_sp(route, *lines)).steps[0]
        assert step.scf_energy is None
        assert step.correlated_missing
        assert step.missing == [f"correlated energy ({step.route.method})"]


def test_correlated_energies_follow_an_optimization():
    # An MP2 optimization prints EUMP2 after each structure's SCF; the step movie and the
    # final energy both use the MP2 totals.
    text = g.step("#P MP2/6-31G(d) Opt", [g.WATER, OPT_END], -76.2, converged=True)
    scf = " SCF Done:  E(RB3LYP) =  -76.20000000     A.U. after   10 cycles"
    parts = text.split(scf)
    text = parts[0] + scf + "\n" + MP_SERIES[0] + parts[1] + scf + "\n"
    text += MP_SERIES[0].replace("76600000000", "76650000000") + parts[2]
    step = gaussian.parse(g.output(text)).steps[0]
    assert step.geometry_energies == pytest.approx([-76.6, -76.65])
    assert step.scf_energy == pytest.approx(-76.65)


def test_the_energy_view_uses_the_correlated_total(open_client):
    # T-IMP-40: a double-hybrid single point on a B3LYP geometry gives E at DH//B3LYP from
    # E(B2PLYPD3), and the calculation says which total it is.
    opt = g.opt_freq(g.WATER, OPT_END, energy=-76.45)
    node_id = import_file(open_client, "opt.log", opt)["node_id"]
    sp = correlated_sp("#P B2PLYPD3/def2TZVP SP", *DOUBLE_HYBRID, atoms=OPT_END)
    import_file(open_client, "dh.log", sp, node_id)
    key = level_key(open_client, "Gaussian B2PLYPD3/DEF2TZVP // Gaussian B3LYP/6-31G(D)")
    assert view(open_client, key, "E")["values"][node_id]["value"] == pytest.approx(-76.7)
    single = next(c for c in calculations(open_client, node_id) if c["type"] == "single_point")
    assert single["result"]["printed"]["energy_label"] == "E(B2PLYPD3)"
    assert not single["parse_warnings"]


def test_a_missing_correlated_total_is_reported_on_import(open_client):
    plan = upload(open_client, "ccsd.log", correlated_sp("#P CCSD/cc-pVTZ SP"))
    assert any("correlated energy (CCSD)" in w["message"] for w in plan["warnings"])
