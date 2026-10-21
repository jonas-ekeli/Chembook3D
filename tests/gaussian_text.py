"""Small synthetic Gaussian outputs for import tests, in the layout Gaussian 16 prints.

Real files (tests/fixtures/gaussian, public samples) cover the full format; these cover cases
the samples do not have, such as a geometry moved on purpose, or a --Link1-- chain with a
custom GenECP basis set and dispersion parameters set by IOps (D59), the way Jonas runs his
jobs. Run `python -m tests.gaussian_text <folder>` to write the custom-basis files for the UI
tests.
"""

import sys
from pathlib import Path

WATER = [
    ("O", 8, 0.000000, 0.000000, 0.117300),
    ("H", 1, 0.000000, 0.757200, -0.469200),
    ("H", 1, 0.000000, -0.757200, -0.469200),
]
DASHES = " " + "-" * 69


def moved(atoms, dz):
    """The same atoms with the first one shifted along z (changes the shape, not a rotation)."""
    first = atoms[0]
    return [(first[0], first[1], first[2], first[3], first[4] + dz), *atoms[1:]]


def orientation(atoms) -> list[str]:
    lines = [
        "                          Input orientation:                          ",
        DASHES,
        " Center     Atomic      Atomic             Coordinates (Angstroms)",
        " Number     Number       Type             X           Y           Z",
        DASHES,
    ]
    for n, (_, number, x, y, z) in enumerate(atoms, start=1):
        lines.append(f" {n:6d} {number:10d} {0:11d} {x:15.6f} {y:11.6f} {z:11.6f}")
    lines.append(DASHES)
    return lines


def frequency_block(frequencies, natoms) -> list[str]:
    lines = []
    for start in range(0, len(frequencies), 3):
        chunk = frequencies[start : start + 3]
        lines.append(" " + "".join(f"{start + i + 1:>23d}" for i in range(len(chunk))))
        lines.append(" " + "".join(f"{'A':>23}" for _ in chunk))
        lines.append(" Frequencies --" + "".join(f"{f:>23.4f}" for f in chunk))
        lines.append(" Red. masses --" + "".join(f"{1.0:>23.4f}" for _ in chunk))
        lines.append(" Frc consts  --" + "".join(f"{1.0:>23.4f}" for _ in chunk))
        lines.append(" IR Inten    --" + "".join(f"{1.0:>23.4f}" for _ in chunk))
        lines.append("  Atom  AN" + "      X      Y      Z  " * len(chunk))
        for atom in range(1, natoms + 1):
            lines.append(f" {atom:5d} {8:3d}" + "   0.00   0.00   0.10  " * len(chunk))
    return lines


THERMO = """ Temperature   298.150 Kelvin.  Pressure   1.00000 Atm.
 Molecular mass:    18.01056 amu.
 Rotational symmetry number  2.
 Rotational temperatures (Kelvin)     40.05000    20.12000    13.39000
 Zero-point correction=                           0.021000 (Hartree/Particle)
 Thermal correction to Energy=                    0.023900
 Thermal correction to Enthalpy=                  0.024800
 Thermal correction to Gibbs Free Energy=         0.003400
 Sum of electronic and zero-point Energies=            -76.387900
 Sum of electronic and thermal Energies=               -76.385000
 Sum of electronic and thermal Enthalpies=             -76.384100
 Sum of electronic and thermal Free Energies=          -76.405500"""


def step(
    route,
    geometries,
    energy=-76.4089,
    *,
    title="water",
    charge=0,
    multiplicity=1,
    converged=None,
    frequencies=None,
    terminate=True,
    internal=False,
    body=(),
    after=(),
) -> str:
    lines = [
        " Link1:  Proceeding to internal job step number  2."
        if internal
        else " Entering Link 1 = /g16/l1.exe PID=     1.",
        DASHES,
        f" {route}",
        DASHES,
        " 1/18=20,19=15/1;",
        " " + "-" * len(title),
        f" {title}",
        " " + "-" * len(title),
        " Symbolic Z-matrix:",
        f" Charge = {charge:2d} Multiplicity = {multiplicity}",
        *body,
    ]
    for atoms in geometries:
        lines += orientation(atoms)
        lines.append(f" SCF Done:  E(RB3LYP) =  {energy:.8f}     A.U. after   10 cycles")
    if converged:
        lines += [" Optimization completed.", "    -- Stationary point found."]
    if frequencies:
        lines += frequency_block(frequencies, len(geometries[-1]))
        lines += THERMO.splitlines()
    lines += after
    if terminate:
        lines.append(" Normal termination of Gaussian 16 at Tue Sep 29 12:00:00 2026.")
    return "\n".join(lines) + "\n"


def output(*steps: str) -> str:
    header = " Entering Gaussian System, Link 0=g16\n Gaussian 16:  ES64L-G16RevC.01  3-Jul-2019\n"
    return header + "".join(steps)


def opt_freq(start, end, energy=-76.4089, frequencies=(1650.0, 3700.0, 3800.0)) -> str:
    """A compound `opt freq` job: the optimization, then the frequency job as internal step 2.
    A transition state's gets an imaginary mode on top: `frequencies=TS_FREQUENCIES`."""
    return output(
        step("#P B3LYP/6-31G(d) Opt Freq", [start, end], energy, converged=True, terminate=False),
        step(
            "#P Geom=AllCheck Guess=TCheck SCRTyp=Grad RB3LYP/6-31G(d) Freq",
            [end],
            energy,
            frequencies=list(frequencies),
            internal=True,
        ),
    )


def convergence_table(stationary: bool) -> list[str]:
    """D158: the check Gaussian prints after a frequency job; not met, the maximum
    displacement says NO and no stationary point is found (as in aziridinium-phos-full-c1)."""
    lines = [
        "         Item               Value     Threshold  Converged?",
        " Maximum Force            0.000001     0.000015     YES",
        " RMS     Force            0.000000     0.000010     YES",
        " Maximum Displacement     0.000083     0.000060     " + ("YES" if stationary else "NO "),
        " RMS     Displacement     0.000018     0.000040     YES",
        " Predicted change in Energy=-6.884102D-11",
    ]
    if stationary:
        lines += [" Optimization completed.", "    -- Stationary point found."]
    return lines


# The water modes with an imaginary one first (not a real water TS: what a TS's job prints).
TS_FREQUENCIES = (-500.0, 1650.0, 3700.0, 3800.0)


def single_point(atoms, route="#P B3LYP/def2TZVP SP", energy=-76.45) -> str:
    return output(step(route, [atoms], energy))


# ---------- custom basis sets and IOp dispersion (D59) ----------

METHYL_IODIDE = [
    ("C", 6, 0.000000, 0.000000, -1.810000),
    ("H", 1, 0.000000, 1.030000, -2.170000),
    ("H", 1, 0.892000, -0.515000, -2.170000),
    ("H", 1, -0.892000, -0.515000, -2.170000),
    ("I", 53, 0.000000, 0.000000, 0.340000),
]
# D3M(BJ) parameters for PBE (Smith et al., J. Phys. Chem. Lett. 2016, 7, 2197) as IOps.
GD3MBJ_IOPS = "IOp(3/174=1000000,3/175=358940,3/177=12092,3/178=5938951)"


def _d(value: float) -> str:
    return f"{value:.10E}".replace("E", "D")


def basis_block(atoms, scale: float) -> list[str]:
    """A `General basis read from cards` block; `scale` tells the DZ and QZ sets apart."""
    lines = [" General basis read from cards:  (5D, 7F)"]
    for n, (_, number, *_) in enumerate(atoms, start=1):
        lines.append(f" Centers:{n:8d}")
        for shell, exponents in (("S", (40.0, 6.0, 1.2)), ("P", (3.0, 0.6))):
            lines.append(f" {shell} {len(exponents)} 1.000")
            for k, exponent in enumerate(exponents):
                value = exponent * number * scale
                coefficient = _d(0.3 + 0.1 * k)
                lines.append(f"     Exponent= {_d(value):>17} Coefficients= {coefficient:>17}")
        lines.append(" ****")
    return lines


def ecp_block(atoms) -> list[str]:
    """`Pseudopotential Parameters`: an ECP on iodine, none on the other centres."""
    rule = " " + "=" * 102
    lines = [
        "                                       Pseudopotential Parameters",
        rule,
        "  Center     Atomic      Valence      Angular      Power",
        "  Number     Number     Electrons     Momentum     of R      Exponent        Coefficient",
        rule,
    ]
    for n, (_, number, *_) in enumerate(atoms, start=1):
        if number < 37:
            lines += [f" {n:4d} {number:10d}", " " * 35 + "No pseudopotential on this center."]
        else:
            lines += [
                f" {n:4d} {number:10d} {number - 28:10d}",
                " " * 42 + "F and up",
                " " * 53 + "2      1.0000000        0.0000000",
            ]
    return lines + [rule]


def custom_chain(ts: bool = False) -> str:
    """Five --Link1-- steps as in Jonas's runs: an HF guess that prints the GenECP basis, a
    PBE single point, a stability check, the optimization (or TS search) and the frequency
    job; steps 2-5 read the basis from the checkpoint (ChkBasis) and set the dispersion by
    IOps."""
    start = METHYL_IODIDE
    end = moved(METHYL_IODIDE, 0.02 if ts else -0.25)
    pbe = f"#P PBEPBE/ChkBasis Geom=AllCheck Guess=Read EmpiricalDispersion=GD3BJ {GD3MBJ_IOPS}"
    opt = "Opt=(TS,CalcFC,NoEigenTest) IOp(1/8=2)" if ts else "Opt=Tight"
    frequencies = [-420.5, 60.0, 180.0, 540.0, 880.0, 1250.0, 1420.0, 2950.0, 3050.0]
    if not ts:
        frequencies[0] = 45.0
    kind = "TS" if ts else "MIN"
    return output(
        step(
            "#P HF/GenECP Pseudo=Read SCF=Tight",
            [start],
            -52.10,
            title="Step 1 - HF guess modDZ",
            body=basis_block(start, 1.0) + ecp_block(start),
        ),
        step(f"{pbe} SP", [start], -52.40, title="Step 2 - PBEPBE-GD3MBJ modDZ"),
        step(f"{pbe} Stable=Opt", [start], -52.40, title="Step 3 - Stable PBEPBE-GD3MBJ modDZ"),
        step(
            f"{pbe} {opt}",
            [start, end],
            -52.45,
            title=f"Step 4 - GeomOpt ({kind}) PBEPBE-GD3MBJ modDZ",
            converged=True,
        ),
        step(
            f"{pbe} Freq",
            [end],
            -52.45,
            title="Step 5 - Freq PBEPBE-GD3MBJ modDZ",
            frequencies=frequencies,
        ),
    )


def custom_single_point() -> str:
    """A separate single point on the chain's final geometry with a larger GenECP basis."""
    atoms = moved(METHYL_IODIDE, 0.02)
    route = f"#P PBEPBE/GenECP Pseudo=Read EmpiricalDispersion=GD3BJ {GD3MBJ_IOPS} SP"
    return output(
        step(
            route,
            [atoms],
            -52.61,
            title="Step 1 - PBEPBE-GD3MBJ modQZ",
            body=basis_block(atoms, 1.7) + ecp_block(atoms),
        )
    )


def checkpoint_ts(dz: float) -> str:
    """A TS search and its frequency job that read the basis set from another job's checkpoint
    (ChkBasis in the first step), so the file names no basis set at all (D104)."""
    start = moved(METHYL_IODIDE, dz)
    end = moved(METHYL_IODIDE, dz + 0.01)
    pbe = "#P PBEPBE/ChkBasis Geom=Check Guess=Read EmpiricalDispersion=GD3BJ"
    return output(
        step(
            f"{pbe} Opt=(TS,CalcFC,NoEigenTest)",
            [start, end],
            -52.44,
            title="TS search from the pre-optimization's checkpoint",
            converged=True,
            terminate=False,
        ),
        step(
            f"{pbe.replace('Geom=Check', 'Geom=AllCheck')} Freq",
            [end],
            -52.44,
            title="Freq",
            frequencies=[-400.0, 60.0, 180.0, 540.0, 880.0, 1250.0, 1420.0, 2950.0, 3050.0],
            internal=True,
        ),
    )


def current_structure(atoms) -> list[str]:
    """The table Gaussian's default IRC integrator prints for each corrected point (D131)."""
    lines = [
        "                    CURRENT STRUCTURE",
        "                Cartesian Coordinates (Ang):                ",
        DASHES,
        " Center     Atomic                     Coordinates (Angstroms)",
        " Number     Number                        X           Y           Z",
        DASHES,
    ]
    for n, (_, number, x, y, z) in enumerate(atoms, start=1):
        lines.append(f" {n:6d} {number:10d} {x:19.6f} {y:11.6f} {z:11.6f}")
    lines.append(DASHES)
    return lines


def irc_point(direction: str, number: int, start: float = 0.0):
    """Methyl iodide with its carbon moved along z: 0.15 Å per point, up in the forward
    direction, down in the reverse; the TS (point 0) moved by `start`."""
    sign = 1 if direction == "forward" else -1
    return moved(METHYL_IODIDE, start + sign * 0.15 * number)


def irc_energy(number: int, ts_energy: float) -> float:
    return ts_energy - 0.0004 * number * number


def irc(
    directions=("forward",),
    points=4,
    *,
    style="lqa",
    checkpoint=True,
    ts_energy=-52.44,
    ending="minimum",
    start=0.0,
    first=1,
    max_points=10,
) -> str:
    """A Gaussian IRC in the layout Gaussian 16 prints (D131), modelled on real outputs.

    `directions` is one direction (`IRC=(Forward)` or `(Reverse)`) or both, run as one job
    whose paths are numbered 1 and 2. With `style` "lqa" a point's structure is the last input
    orientation before its "Point Number" line; with "hpc" (the default integrator) it is the
    CURRENT STRUCTURE printed after that line, while the input orientation before it is the
    predictor step. With `checkpoint` the TS is only the structure read from the checkpoint
    (Geom=Check), in another frame; otherwise it is printed as point 0. `ending` is "minimum",
    "max_points" or "stopped" (the file ends during the last path). `first` numbers the first
    point, as a restarted IRC continues from a later point."""
    both = len(directions) == 2
    which = "" if both else f"{directions[0].capitalize()},"
    integrator = "LQA," if style == "lqa" else ""
    route = (
        "#P PBEPBE/ChkBasis Geom=Check Guess=Read EmpiricalDispersion=GD3BJ "
        f"IRC=(RCFC,{which}{integrator}MaxPoints={max_points},StepSize=10)"
    )
    if not checkpoint:
        route = route.replace(" Geom=Check", "")
    ts = irc_point(directions[0], 0, start)
    body: list[str] = []
    if checkpoint:
        body += [' Structure from the checkpoint file:  "ts.chk"', " Charge =  0 Multiplicity = 1"]
        # Another frame, as a checkpoint's structure is: everything shifted along x.
        body += [f"  {el}{x + 3.0:30.12f}{y:20.12f}{z:20.12f}" for el, _, x, y, z in ts]
        body.append(" ")
    lines = [f" Maximum points per path      =  {max_points}"]
    if checkpoint:
        lines.append(f"   Energy From Chk =  {ts_energy:.7f}")
    else:
        lines += orientation(ts)
        lines.append(f" SCF Done:  E(RPBE-PBE) =  {ts_energy:.8f}     A.U. after   10 cycles")
    lines.append(" Point Number:   0          Path Number:   1")
    for path, direction in enumerate(directions, start=1):
        upper = direction.upper()
        for number in range(first, first + points):
            atoms = irc_point(direction, number, start)
            energy = irc_energy(number, ts_energy)
            lines.append(f" Point Number {number:2d} in {upper} path direction.")
            if style == "hpc":
                lines += orientation(moved(atoms, 0.02))  # the predictor step
                lines.append(
                    f" SCF Done:  E(RPBE-PBE) =  {energy + 0.0001:.8f}     A.U. after 9 cycles"
                )
                lines.append(f"   Corrected End Point Energy =  {energy:.6f}")
                lines.append(f" Point Number: {number:3d}          Path Number: {path:3d}")
                lines += current_structure(atoms)
            else:
                lines += orientation(atoms)
                lines.append(f" SCF Done:  E(RPBE-PBE) =  {energy:.8f}     A.U. after 9 cycles")
                lines.append(f" Point Number: {number:3d}          Path Number: {path:3d}")
            lines.append(f"   NET REACTION COORDINATE UP TO THIS POINT =  {0.1 * number:10.5f}")
        last = path == len(directions)
        if ending == "stopped" and last:
            return output(
                step(route, [], ts_energy, title="IRC", body=body + lines, terminate=False)
            )
        if ending == "max_points":
            lines.append(" Maximum number of steps reached.")
        else:
            lines.append(" PES minimum detected on this side of the pathway.")
        lines.append(f" Calculation of {upper} path complete.")
    lines += [
        " Reaction path calculation complete.",
        f" Energies reported relative to the TS energy of {ts_energy:16.6f}",
    ]
    return output(step(route, [], ts_energy, title="IRC", body=body + lines))


def end_point_opt_freq(direction: str, number: int, energy: float, imaginary: bool = False) -> str:
    """The optimization and frequency job of an IRC end point (D131): from the IRC's last point
    on to a structure 0.15 Å further."""
    first = irc_point(direction, number)
    last = irc_point(direction, number + 1)
    pbe = "#P PBEPBE/ChkBasis Geom=Check Guess=Read EmpiricalDispersion=GD3BJ"
    frequencies = [60.0, 180.0, 540.0, 880.0, 1250.0, 1420.0, 2950.0, 3050.0, 3100.0]
    if imaginary:
        frequencies[0] = -60.0
    return output(
        step(
            f"{pbe} Opt", [first, last], energy, title="End point", converged=True, terminate=False
        ),
        step(
            f"{pbe.replace('Geom=Check', 'Geom=AllCheck')} Freq",
            [last],
            energy,
            title="Freq",
            frequencies=frequencies,
            internal=True,
        ),
    )


CHECKPOINT_FILES = {
    "TS_a_chk.out": lambda: checkpoint_ts(0.4),
    "TS_b_chk.out": lambda: checkpoint_ts(0.8),
}


CUSTOM_FILES = {
    "MeI_min.out": lambda: custom_chain(),
    "MeI_TS.out": lambda: custom_chain(ts=True),
    "MeI_TS_QZ.out": custom_single_point,
}


# D131: a TS with its IRC both ways (the reverse by the default integrator) and the job on
# the forward end point.
IRC_FILES = {
    "TS.out": lambda: checkpoint_ts(-0.01),
    "irc-forward.out": lambda: irc(points=6, ending="max_points"),
    "irc-reverse.out": lambda: irc(("reverse",), points=5, style="hpc"),
    "end-forward.out": lambda: end_point_opt_freq("forward", 6, -52.46),
}


if __name__ == "__main__":
    # `python -m tests.gaussian_text [--checkpoint | --irc] <folder>`
    files = {"--checkpoint": CHECKPOINT_FILES, "--irc": IRC_FILES}.get(sys.argv[1], CUSTOM_FILES)
    folder = Path(sys.argv[-1])
    folder.mkdir(parents=True, exist_ok=True)
    for name, make in files.items():
        (folder / name).write_text(make(), encoding="utf-8")
