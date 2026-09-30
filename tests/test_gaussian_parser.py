"""Gaussian parser (FR-IMP-01…04) against public sample outputs and small synthetic files.
Expected values were read by hand from the files (sources in tests/fixtures/README.md)."""

from pathlib import Path

import pytest

from chembook3d.parsers import gaussian
from tests import gaussian_text as g

FIXTURES = Path(__file__).parent / "fixtures" / "gaussian"
MINIMUM = "aziridinium-phos-full-c1.log"  # opt(tight) freq, SMD(THF), G16 C.01
TS = "aminationTS-full-unfrz-c1.log"  # opt(ts) freq on the same atoms
TS_SP = "aminationTS-full-unfrz-c1_sp_tzpop.log"  # a larger-basis single point on the TS


def read(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def truncated_opt() -> str:
    """The minimum's file cut inside its optimization (a failed job has no Normal termination)."""
    lines = read(MINIMUM).splitlines(keepends=True)
    return "".join(lines[:30000])


def without_route(text: str) -> str:
    """Remove every route section, as T-IMP-06 asks."""
    lines = text.splitlines(keepends=True)
    kept, skipping = [], False
    for n, line in enumerate(lines):
        if line.startswith(" #") and n and set(lines[n - 1].strip()) == {"-"}:
            skipping = True
        if skipping and set(line.strip()) == {"-"}:
            skipping = False
        if not skipping:
            kept.append(line)
    return "".join(kept)


@pytest.fixture(scope="module")
def minimum():
    return gaussian.parse(read(MINIMUM))


@pytest.fixture(scope="module")
def ts():
    return gaussian.parse(read(TS))


def test_opt_freq_steps(minimum):
    # A compound `opt freq` job: the frequency job runs as internal step 2.
    assert minimum.program == "Gaussian" and minimum.version == "16 Rev. C.01"
    assert [s.job_type for s in minimum.steps] == ["optimization", "frequency"]
    assert all(s.termination == "normal" for s in minimum.steps)
    assert all(not s.missing for s in minimum.steps)
    assert minimum.steps[0].optimization_converged is True
    assert minimum.steps[0].scf_energy == -2091.04905931
    assert all((s.charge, s.multiplicity) == (0, 1) for s in minimum.steps)


def test_opt_freq_geometry_and_level(minimum):
    opt, freq = minimum.steps
    assert len(opt.final_geometry) == 91
    assert opt.final_geometry[0] == gaussian.Atom("C", -2.625541, 0.290126, 2.713629)
    assert opt.final_geometry == freq.final_geometry
    assert (opt.route.method, freq.route.method) == ("WB97XD", "RWB97XD")
    # 6-31+G* in the input and 6-31+G(d) in the frequency step's route are one basis set.
    assert opt.resolved_basis == freq.resolved_basis == "6-31+G(D)"
    # The frequency step's SCRF=Check reads the solvation model of the optimization.
    for step in (opt, freq):
        assert (step.route.solvation_model, step.route.solvent) == ("SMD", "tetrahydrofuran")
    assert opt.title == "aziridinium-phos-full-c1"


def test_frequencies_and_thermochemistry(minimum):
    # T-IMP-01: values as printed in the file
    freq = minimum.steps[1]
    assert len(freq.frequencies) == 267 and freq.imaginary_count == 0
    assert freq.frequencies[0] == 14.2476
    assert sum(1 for f in freq.frequencies if f < 100) == 18
    assert len(freq.normal_modes) == 267 and len(freq.normal_modes[0]) == 91
    assert freq.thermo == {
        "point_group": "C1",
        "rotational_constants": [0.09962, 0.07291, 0.06866],
        "temperature": 298.15,
        "pressure": 1.0,
        "molecular_mass": 581.36289,
        "symmetry_number": 1,
        "rotational_temperatures": [0.00478, 0.0035, 0.0033],
        "zpe": 0.789644,
        "e_corr": 0.834644,
        "h_corr": 0.835588,
        "g_corr": 0.709784,
        "e_zpe": -2090.259415,
        "e_thermal": -2090.214416,
        "h": -2090.213472,
        "g": -2090.339275,
    }
    assert freq.printed["g"] == "Sum of electronic and thermal Free Energies=        -2090.339275"


def test_ts_has_one_imaginary_frequency(ts):
    # T-IMP-02
    opt, freq = ts.steps
    assert opt.job_type == "ts_optimization" and opt.optimization_converged is True
    assert freq.imaginary_count == 1 and freq.frequencies[0] == -622.0695
    assert freq.scf_energy == -2090.9290441


def test_separate_single_point():
    sp = gaussian.parse(read(TS_SP)).steps
    assert len(sp) == 1 and sp[0].job_type == "single_point"
    assert sp[0].resolved_basis == "6-311++G(D,P)"
    assert sp[0].scf_energy == -2091.36217372


def test_custom_basis_is_read_from_the_checkpoint_in_later_steps():
    # A --Link1-- chain as Jonas runs it (synthetic): the GenECP basis is printed in step 1
    # only, and later steps read it from the checkpoint (ChkBasis).
    chain = gaussian.parse(g.custom_chain())
    assert [s.job_type for s in chain.steps] == [
        "single_point",
        "single_point",
        "other",  # Stable=Opt
        "optimization",
        "frequency",
    ]
    opt = chain.steps[3]
    assert opt.route.method == "PBEPBE"
    assert opt.route.basis == "CHKBASIS" and opt.resolved_basis == "GENECP"
    assert opt.resolved_basis_definition == chain.steps[0].basis_definition
    assert set(opt.resolved_basis_definition) == {"C", "H", "I"}
    assert "ECP" in opt.resolved_basis_definition["I"]
    assert "ECP" not in opt.resolved_basis_definition["C"]
    assert opt.route.dispersion == "GD3BJ"
    assert gaussian.dispersion_iops(opt.route.iops) == {
        "3/174": "1000000",
        "3/175": "358940",
        "3/177": "12092",
        "3/178": "5938951",
    }
    assert opt.title == "Step 4 - GeomOpt (MIN) PBEPBE-GD3MBJ modDZ"


def test_ts_chain_iops_that_are_not_part_of_the_level():
    ts = gaussian.parse(g.custom_chain(ts=True))
    opt, freq = ts.steps[3], ts.steps[4]
    assert opt.job_type == "ts_optimization"
    assert gaussian.dispersion_iops(opt.route.iops) == gaussian.dispersion_iops(
        ts.steps[1].route.iops
    )
    assert opt.route.iops["1/8"] == "2"  # not a level-of-theory IOp (D59)
    assert freq.imaginary_count == 1


def test_separate_single_point_defines_its_own_basis():
    sp = gaussian.parse(g.custom_single_point()).steps
    assert len(sp) == 1 and sp[0].job_type == "single_point"
    assert sp[0].resolved_basis == "GENECP"
    assert sp[0].title == "Step 1 - PBEPBE-GD3MBJ modQZ"


def test_basis_fingerprints_tell_basis_sets_apart():
    dz = gaussian.basis_fingerprints(gaussian.parse(g.custom_chain()).steps[0].basis_definition)
    qz_step = gaussian.parse(g.custom_single_point()).steps[0]
    qz = gaussian.basis_fingerprints(qz_step.basis_definition)
    ts_chain = gaussian.parse(g.custom_chain(ts=True))
    ts_dz = gaussian.basis_fingerprints(ts_chain.steps[0].basis_definition)
    assert dz == ts_dz
    assert all(dz[el] != qz[el] for el in dz)


def test_truncated_optimization():
    # T-IMP-03 input: no final Normal termination, optimization not finished
    steps = gaussian.parse(truncated_opt()).steps
    assert len(steps) == 1
    assert steps[0].termination == "abnormal"
    assert steps[0].optimization_converged is False
    assert len(steps[0].geometries) > 1


def test_route_removed_still_gives_geometry():
    # T-IMP-06 input
    steps = gaussian.parse(without_route(read(TS_SP))).steps
    assert len(steps) == 1
    assert len(steps[0].final_geometry) == 91
    assert steps[0].scf_energy == -2091.36217372
    assert {"route section", "method", "basis set"} <= set(steps[0].missing)


def test_compound_opt_freq_is_two_steps():
    text = g.opt_freq(g.WATER, g.moved(g.WATER, 0.01))
    opt, freq = gaussian.parse(text).steps
    assert (opt.job_type, freq.job_type) == ("optimization", "frequency")
    # The first internal step has no termination line; the second one starting means it ended.
    assert opt.termination == "normal" and freq.termination == "normal"
    assert opt.optimization_converged is True
    assert freq.frequencies == [1650.0, 3700.0, 3800.0]
    assert freq.thermo["symmetry_number"] == 2
    assert freq.route.method == "RB3LYP" and freq.resolved_basis == "6-31G(D)"


def test_not_a_gaussian_file():
    with pytest.raises(gaussian.NotGaussianOutput):
        gaussian.parse("3\nwater\nO 0 0 0\n")


@pytest.mark.parametrize(
    ("route", "expected"),
    [
        ("#P B3LYP/6-31G(d,p) Opt", ("B3LYP", "6-31G(D,P)", "optimization", None, None)),
        (
            "# opt=(ts,calcfc,noeigentest) freq wb97xd/def2svp",
            ("WB97XD", "DEF2SVP", "ts_optimization", None, None),
        ),
        (
            "#p M06/def2TZVP SCRF=(SMD,Solvent=Toluene)",
            ("M06", "DEF2TZVP", "single_point", "SMD", "toluene"),
        ),
        (
            "# B3LYP 6-31G(d) scrf=(cpcm,solvent=water) sp",
            ("B3LYP", "6-31G(D)", "single_point", "CPCM", "water"),
        ),
        (
            "# PBE1PBE/GEN SCRF(Solvent=Dichloromethane)",
            ("PBE1PBE", "GEN", "single_point", "IEFPCM", "dichloromethane"),
        ),
        ("# B3LYP/def2SVP SCRF=PCM irc=(calcfc)", ("B3LYP", "DEF2SVP", "other", "IEFPCM", "water")),
        (
            "# opt(ts) b3lyp/genecp empiricaldispersion=gd3bj",
            ("B3LYP", "GENECP", "ts_optimization", None, None),
        ),
    ],
)
def test_route_parsing(route, expected):
    parsed = gaussian.parse_route(route)
    got = (parsed.method, parsed.basis, parsed.job_type, parsed.solvation_model, parsed.solvent)
    assert got == expected


def test_route_words_keep_parenthesised_options_together():
    words = gaussian.split_route_words(
        "#P # PBEPBE/CHKBASIS # IOP(3/174=1000000,3/175=358940) # SCF=(VSHIFT=300,CONVER=9)"
    )
    assert words == [
        "PBEPBE/CHKBASIS",
        "IOP(3/174=1000000,3/175=358940)",
        "SCF=(VSHIFT=300,CONVER=9)",
    ]
    assert gaussian.parse_route("# b3lyp/genecp empiricaldispersion=gd3bj").dispersion == "GD3BJ"
