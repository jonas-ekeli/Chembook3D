"""ORCA and xTB parsers (FR-IMP-06, FR-IMP-07, T-IMP-10) against real outputs from cclib's
regression data (tests/fixtures/README.md), plus keyword strings for cases the files lack.
Expected values were read by hand from the files."""

from pathlib import Path

import pytest

from chembook3d import thermochem
from chembook3d.parsers import orca, xtb

FIXTURES = Path(__file__).parent / "fixtures"


def read(folder: str, name: str) -> str:
    return (FIXTURES / folder / name).read_text(encoding="utf-8")


# ---------- ORCA ----------


def test_orca_frequency_job():
    # T-IMP-10: the same checks as T-IMP-01 where ORCA prints the values.
    parsed = orca.parse(read("orca", "dvb_ir.out"))
    assert (parsed.program, parsed.version) == ("ORCA", "6.0.1")
    (step,) = parsed.steps
    assert step.job_type == "frequency" and step.termination == "normal"
    assert (step.route.method, step.route.basis, step.route.dispersion) == ("B3LYP", "STO-3G", None)
    assert (step.charge, step.multiplicity) == (0, 1)
    assert len(step.final_geometry) == 20 and step.final_geometry[0].element == "C"
    assert step.scf_energy == -382.055107107616
    # The six translations and rotations are left out; 54 = 3·20 − 6 vibrations.
    assert len(step.frequencies) == 54 and step.frequencies[:2] == [43.87, 77.06]
    assert step.imaginary_count == 0
    assert len(step.normal_modes) == 54 and len(step.normal_modes[0]) == 20
    thermo = step.thermo
    assert thermo["temperature"] == 298.15 and thermo["pressure"] == 1.0
    assert thermo["zpe"] == 0.17701463
    assert thermo["h"] == -381.86823509 and thermo["g"] == -381.91114546
    assert thermo["g_corr"] == 0.14396165 and thermo["e_corr"] == 0.18592781
    assert thermo["h_corr"] == pytest.approx(-381.86823509 + 382.05510711, abs=1e-12)
    assert (thermo["point_group"], thermo["symmetry_number"]) == ("C2h", 2)
    assert thermo["molecular_mass"] == 130.19
    assert step.printed["g"] == "Final Gibbs free energy         ...   -381.91114546 Eh"
    assert step.missing == []


def test_orca_values_feed_the_quasi_harmonic_free_energy():
    # The rotational constants and mass ORCA prints give the same enthalpy and rotational and
    # translational entropy terms as ORCA's own summary (G itself differs: ORCA uses Grimme's
    # qRRHO for S_vib, Chembook3D uses Truhlar's, D56).
    step = orca.parse(read("orca", "dvb_ir.out")).steps[0]
    qh = thermochem.quasi_harmonic(
        frequencies=step.frequencies,
        scf_energy=step.scf_energy,
        molecular_mass=step.thermo["molecular_mass"],
        multiplicity=step.multiplicity,
        rotational_temperatures=step.thermo["rotational_temperatures"],
        symmetry_number=step.thermo["symmetry_number"],
        point_group=step.thermo["point_group"],
    )
    assert qh.h_corr == pytest.approx(step.thermo["h_corr"], abs=1e-6)
    assert qh.zpe == pytest.approx(step.thermo["zpe"], abs=1e-6)
    assert qh.s_rot * 298.15 == pytest.approx(0.01337276, abs=1e-6)  # "Rotational entropy"
    assert qh.s_trans * 298.15 == pytest.approx(0.01924489, abs=1e-6)  # "Translational entropy"


def test_orca_5_output():
    parsed = orca.parse(read("orca", "orca5_dvb_ir.out"))
    assert parsed.version == "5.0.1"
    step = parsed.steps[0]
    assert step.job_type == "frequency" and not step.missing
    assert step.thermo["g"] == -381.91112705 and len(step.frequencies) == 54


def test_orca_optimization():
    (step,) = orca.parse(read("orca", "dvb_gopt.out")).steps
    assert step.job_type == "optimization" and step.optimization_converged is True
    assert step.termination == "normal"
    assert len(step.geometries) == 4  # three cycles and the final energy evaluation
    assert step.scf_energy == -382.055133399486
    assert step.route.text == "! rks b3lyp sto-3g usesym opt"


def test_orca_unfinished_optimization():
    # A job cut off during its optimization: no normal termination, not converged.
    lines = read("orca", "dvb_gopt.out").splitlines(keepends=True)
    cut = next(n for n, line in enumerate(lines) if "GEOMETRY OPTIMIZATION CYCLE   3" in line)
    (step,) = orca.parse("".join(lines[:cut])).steps
    assert step.termination == "abnormal" and step.optimization_converged is False
    assert len(step.geometries) == 2


def test_orca_open_shell_cation():
    # `! uks b3lyp`: the unrestricted reference is the default for a doublet (A12).
    (step,) = orca.parse(read("orca", "dvb_sp_un_dft.out")).steps
    assert (step.charge, step.multiplicity) == (1, 2)
    assert step.route.method == "B3LYP" and step.job_type == "single_point"
    assert step.scf_energy == -381.831980903245


def test_orca_solvation_from_the_cpcm_block():
    (smd,) = orca.parse(read("orca", "water_hf_solvent_smd.log")).steps
    assert (smd.route.solvation_model, smd.route.solvent) == ("SMD", "toluene")
    assert (smd.route.method, smd.route.basis) == ("HF", "STO-3G")
    (cpcm,) = orca.parse(read("orca", "water_hf_solvent_cpcm.log")).steps
    # A solvent given only by its dielectric constant is named by it.
    assert (cpcm.route.solvation_model, cpcm.route.solvent) == ("CPCM", "epsilon=2.3741")


@pytest.mark.parametrize(
    ("keywords", "multiplicity", "expected"),
    [
        ("B3LYP D3BJ def2-TZVP CPCM(Water) Opt Freq TightSCF", 1,
         ("B3LYP", "DEF2-TZVP", "D3BJ", "CPCM", "water", "optimization", True)),
        ("DLPNO-CCSD(T) def2-TZVP def2-TZVP/C TightPNO RIJCOSX def2/J", 1,
         ("DLPNO-CCSD(T)", "DEF2-TZVP", None, None, None, "single_point", False)),
        ("r2SCAN-3c OptTS NumFreq", 1,
         ("R2SCAN-3C", None, None, None, None, "ts_optimization", True)),
        ("UKS PBE0 def2-SVP", 1, ("UPBE0", "DEF2-SVP", None, None, None, "single_point", False)),
        ("ROHF cc-pVTZ", 3, ("ROHF", "CC-PVTZ", None, None, None, "single_point", False)),
        ("wB97X-V ma-def2-TZVP SMD(toluene) Freq", 1,
         ("WB97X-V", "MA-DEF2-TZVP", None, "SMD", "toluene", "frequency", False)),
    ],
)  # fmt: skip
def test_orca_keywords(keywords, multiplicity, expected):
    route = orca.parse_keywords(keywords.split(), multiplicity)
    found = (
        route.method,
        route.basis,
        route.dispersion,
        route.solvation_model,
        route.solvent,
        route.job_type,
        route.compound_freq,
    )
    assert found == expected


def test_orca_opt_freq_is_two_steps():
    # No sample runs `opt freq`, so one is made from the optimization and the frequency job
    # (both ran with the same keywords apart from the job type).
    optimization = read("orca", "dvb_gopt.out").replace("usesym opt", "usesym opt freq")
    frequency = read("orca", "dvb_ir.out")
    start = frequency.index("VIBRATIONAL FREQUENCIES") - 200
    text = optimization.split("****ORCA TERMINATED NORMALLY****")[0] + frequency[start:]
    first, second = orca.parse(text).steps
    assert (first.job_type, second.job_type) == ("optimization", "frequency")
    assert first.termination == second.termination == "normal"
    assert first.optimization_converged is True and second.optimization_converged is None
    # The frequency job runs on the optimized geometry and energy.
    assert second.final_geometry == first.final_geometry
    assert second.scf_energy == -382.05510711  # its own "Electronic energy"
    assert len(second.frequencies) == 54 and not second.missing


def test_orca_multi_job_file_reads_the_first_job():
    # A18: no sample has $new_job, so one is made by appending a second job to a frequency job.
    first = read("orca", "dvb_ir.out").replace(
        "| 30>                          ****END OF INPUT****",
        "| 30> $new_job\n| 31> ! hf sto-3g\n| 32>                          ****END OF INPUT****",
    )
    second = read("orca", "dvb_sp_un_dft.out")
    head, tail = first.split("****ORCA TERMINATED NORMALLY****")
    text = head + "\n   $$$$$$$$$$$$$$$$  JOB NUMBER  2 $$$$$$$$$$$$$$\n" + second
    (step,) = orca.parse(text).steps
    assert (step.route.method, step.job_type) == ("B3LYP", "frequency")
    assert step.charge == 0 and len(step.frequencies) == 54
    assert step.scf_energy == orca.parse(read("orca", "dvb_ir.out")).steps[0].scf_energy
    assert step.missing == ["jobs after the first ($new_job)"]


def test_not_orca():
    with pytest.raises(orca.NotOrcaOutput):
        orca.parse(read("xtb", "dvb_sp.out"))


# ---------- xTB ----------


def test_xtb_single_point():
    parsed = xtb.parse(read("xtb", "dvb_sp.out"))
    assert (parsed.program, parsed.version) == ("xTB", "6.6.1")
    (step,) = parsed.steps
    assert step.job_type == "single_point" and step.termination == "normal"
    assert (step.route.method, step.route.basis) == ("GFN2-xTB", None)
    assert (step.charge, step.multiplicity) == (0, 1)
    assert step.scf_energy == -26.425939358406
    # xTB prints no coordinates for a single point (A15).
    assert step.geometries == [] and step.missing == ["geometry"]


def test_xtb_optimization():
    (step,) = xtb.parse(read("xtb", "dvb_opt.out")).steps
    assert step.job_type == "optimization" and step.optimization_converged is True
    assert len(step.final_geometry) == 20
    assert step.final_geometry[0].x == pytest.approx(0.23214923366237)
    assert step.scf_energy == -26.438242468348
    assert step.missing == []


def test_xtb_frequency_job():
    (step,) = xtb.parse(read("xtb", "dvb_ir.out")).steps
    assert step.job_type == "frequency"
    assert len(step.frequencies) == 54 and step.frequencies[0] == 26.15
    assert len(step.reduced_masses) == 54 and step.reduced_masses[0] == 8.66
    thermo = step.thermo
    assert thermo["temperature"] == 298.15
    assert thermo["zpe"] == 0.161236858990 and thermo["g_corr"] == 0.127242830965
    assert thermo["h"] == -26.266467652754 and thermo["g"] == -26.310999637373
    assert thermo["g"] == pytest.approx(step.scf_energy + thermo["g_corr"], abs=1e-9)
    assert (thermo["point_group"], thermo["symmetry_number"]) == ("C2H", 2)
    assert step.missing == ["geometry"]


def test_xtb_ohess_is_two_steps():
    # `--ohess` optimizes, then computes frequencies on the result. Made from the samples,
    # which ran the two jobs separately on the same molecule.
    optimization = read("xtb", "dvb_opt.out").replace("--opt --grad", "--ohess")
    frequency = read("xtb", "dvb_ir.out")
    hessian = frequency.index("Numerical Hessian")
    start = frequency.rfind("\n", 0, frequency.rfind("\n", 0, hessian)) + 1
    text = optimization.split(" * finished run on")[0] + frequency[start:]
    first, second = xtb.parse(text).steps
    assert (first.job_type, second.job_type) == ("optimization", "frequency")
    assert first.termination == second.termination == "normal"
    assert second.final_geometry == first.final_geometry
    assert len(second.frequencies) == 54 and not second.missing


@pytest.mark.parametrize(
    ("call", "expected"),
    [
        ("xtb mol.xyz --ohess --alpb water --chrg 1 --uhf 2",
         ("optimization", True, "ALPB", "water")),
        ("xtb mol.xyz --hess -g toluene", ("frequency", False, "GBSA", "toluene")),
        ("xtb mol.xyz --md", ("other", False, None, None)),
        ("xtb mol.xyz --gfn 1", ("single_point", False, None, None)),
    ],
)  # fmt: skip
def test_xtb_command_line(call, expected):
    route = xtb.route_from_call(call.split(), "GFN2-xTB")
    assert (route.job_type, route.compound_freq, route.solvation_model, route.solvent) == expected


def test_xtb_charge_and_multiplicity_from_the_command_line():
    text = read("xtb", "dvb_sp.out").replace(
        ": xtb dvb_sp.xyz", ": xtb dvb_sp.xyz --chrg 1 --uhf 1"
    )
    (step,) = xtb.parse(text).steps
    assert (step.charge, step.multiplicity) == (1, 2)


def test_xtb_abnormal_termination():
    text = read("xtb", "dvb_opt.out").split(" * finished run on")[0]
    (step,) = xtb.parse(text).steps
    assert step.termination == "abnormal"
