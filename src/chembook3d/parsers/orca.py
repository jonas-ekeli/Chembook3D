"""ORCA output parser (FR-IMP-06, docs/spec/05 §3; D64).

Written by hand like the Gaussian parser (D61) rather than with cclib: cclib does not read
the input keywords, the solvation model or the thermochemistry summary, and it merges an
`opt freq` job into one record.

The level of theory comes from the `!` keyword lines of the input that ORCA echoes at the top
of the output, and the solvation model from the CPCM block it prints. An `opt freq` job
becomes two steps (the optimization, then the frequency job on its final geometry), the way
Gaussian runs a compound route. Only real (not translation or rotation) modes are kept as
frequencies, imaginary ones negative as ORCA prints them.
"""

import re

from chembook3d.parsers.common import ParsedFile, ParsedStep, Route
from chembook3d.xyz import Atom, element_of

PROGRAM = "ORCA"

# cm⁻¹ → GHz, and cm⁻¹ → K (h·c/k), for rotational constants and temperatures (CODATA 2018).
GHZ_PER_WAVENUMBER = 29.9792458
KELVIN_PER_WAVENUMBER = 1.438776877


class NotOrcaOutput(ValueError):
    pass


# ---------- keywords ----------

OPT_KEYWORDS = {
    "OPT", "COPT", "ZOPT", "GDIIS-OPT", "TIGHTOPT", "VERYTIGHTOPT", "LOOSEOPT", "NORMALOPT",
    "CRUDEOPT", "SLOPPYOPT", "L-OPT",
}  # fmt: skip
TS_KEYWORDS = {"OPTTS", "SCANTS"}
FREQ_KEYWORDS = {"FREQ", "NUMFREQ", "ANFREQ"}
SINGLE_POINT_KEYWORDS = {"SP", "ENGRAD", "NUMGRAD"}
OTHER_JOB_KEYWORDS = {"MD", "IRC", "NEB", "NEB-TS", "NEB-CI", "ZOOM-NEB", "GOAT", "NMR", "EPR"}
REFERENCES = {"RHF": "R", "UHF": "U", "ROHF": "RO", "RKS": "R", "UKS": "U", "ROKS": "RO"}
# Keywords that set accuracy, approximations, printing or resources: not part of the level.
SETTINGS = set(
    """
    ALLPOP ANGS AUTOAUX AUTOSTART AUTOTRAH BOHRS CHELPG CONV COSX DAMP DECONTRACT DEFGRID1
    DEFGRID2 DEFGRID3 DIRECT EXTREMESCF FROZENCORE HCORE HIRSHFELD HUECKEL KDIIS KEEPDENS
    KEEPINTS LARGEPRINT LOOSEPNO LOOSESCF LSHIFT MBIS MINIPRINT MOREAD NBO NOAUTOSTART
    NOCOSX NODAMP NOFINALGRID NOFROZENCORE NOITER NOLSHIFT NOPOP NOPRINTMOS NORI NORMALPNO
    NORMALPRINT NORMALSCF NOSOSCF NOSPLIT-RI-J NOSYM NOTRAH NOUSESYM PATOM PDBFILE PMODEL
    PRINTBASIS PRINTMOS READINTS RI RIJCOSX RIJDX RIJK RIJONX SCANTS SCFCONV10 SCFCONV6
    SCFCONV7 SCFCONV8 SCFCONV9 SEMIDIRECT SLOPPYSCF SLOWCONV SMALLPRINT SOSCF SPLIT-RI-J
    STRONGSCF TIGHTPNO TIGHTSCF TRAH UCO UNO USESYM VERYSLOWCONV VERYTIGHTSCF XYZFILE
    """.split()
)
_DISPERSION = re.compile(r"^(D2|D3|D3BJ|D3ZERO|D3TZ|D4|NL|SCNL|ABC)$")
_SOLVATION = re.compile(r"^(CPCM|CPCMC|SMD|ALPB|DDCOSMO|COSMORS)(?:\((.*)\))?$")
_BASIS = re.compile(
    r"^((MA-|DKH-|ZORA-|X2C-)?(DEF2|DEF)-.*|(AUG-|MAUG-|JUN-|JUL-|MAY-|APR-)?CC-P.*|"
    r"\d-\d+\+*G.*|STO-\dG|SV|SVP|SV\(P\)|TZV|TZVP|TZVPP|QZVP|QZVPP|(AUG-)?PC(SEG|SSEG|J)?-\d|"
    r"X2C-.*|ANO-.*|SARC-.*|SARC2-.*|LANL.*|EPR-.*|IGLO-.*|MINI|MINIS|MIDI|MINIX|6-31.*|"
    r"(OLD-|DKH-|ZORA-)?(SV|TZV|QZV).*|W1-.*|PCSEG-\d|CC-PWCV.*)$"
)
# Methods that carry their own basis set (composite "3c" methods and semi-empirical xTB).
_BUILT_IN_BASIS = re.compile(r"(-3C$|^XTB\d?$|^GFN\d?-XTB$|^GFN-FF$|^NATIVE-GFN)")
_PAL = re.compile(r"^PAL\d+$")
_GRID = re.compile(r"^(FINAL)?GRID\d+$|^DEFGRID\d$")


def has_built_in_basis(method: str) -> bool:
    return bool(_BUILT_IN_BASIS.search(method.upper()))


def _is_auxiliary(word: str) -> bool:
    """RI/COSX auxiliary basis sets (def2/J, def2-TZVP/C, AutoAux) do not change the level."""
    return "/" in word or word.startswith("AUTOAUX")


def parse_keywords(words: list[str], multiplicity: int | None) -> Route:
    """The level and job type from ORCA's `!` keywords (case-insensitive)."""
    route = Route(text="! " + " ".join(words))
    upper = [w.upper() for w in words]
    reference = None
    is_opt = is_ts = is_freq = is_other = False
    for word in upper:
        if word in OPT_KEYWORDS:
            is_opt = True
        elif word in TS_KEYWORDS:
            is_opt = is_ts = True
        elif word in FREQ_KEYWORDS:
            is_freq = True
        elif word in OTHER_JOB_KEYWORDS:
            is_other = True
        elif word in SINGLE_POINT_KEYWORDS:
            pass
        elif word in REFERENCES:
            reference = word
        elif _DISPERSION.match(word):
            route.dispersion = word
        elif match := _SOLVATION.match(word):
            route.solvation_model = match.group(1)
            if match.group(2):
                route.solvent = match.group(2).strip().strip('"').lower()
        elif _is_auxiliary(word):
            pass
        elif _BASIS.match(word):
            route.basis = route.basis or word
        elif word in SETTINGS or _PAL.match(word) or _GRID.match(word):
            pass
        elif route.method is None:
            route.method = word

    if route.method is None and reference and reference.endswith("HF"):
        route.method = "HF"
    if route.method and reference:
        # A12, as for Gaussian: the default reference (restricted for singlets, unrestricted
        # otherwise) is not written; RO and a non-default choice are.
        prefix = REFERENCES[reference]
        default = "R" if multiplicity == 1 else "U" if multiplicity else None
        if prefix != default:
            route.method = prefix + route.method

    if is_other:
        route.job_type = "other"
    elif is_opt:
        route.job_type = "ts_optimization" if is_ts else "optimization"
        route.compound_freq = is_freq
    elif is_freq:
        route.job_type = "frequency"
    else:
        route.job_type = "single_point"
    return route


# ---------- output sections ----------

_FLOAT = r"[-+]?\d+\.\d*(?:[Ee][-+]?\d+)?"
_ECHO = re.compile(r"^\|\s*\d+>\s?(.*)$")
_THERMO = {
    "electronic": re.compile(r"^Electronic energy\s+\.\.\.\s+(" + _FLOAT + ")"),
    "zpe": re.compile(r"^Zero point energy\s+\.\.\.\s+(" + _FLOAT + ")"),
    "e_thermal": re.compile(r"^Total thermal energy\s+(?:\.\.\.\s+)?(" + _FLOAT + ")"),
    "e_corr": re.compile(r"^Total correction\s+(" + _FLOAT + ")"),
    "h": re.compile(r"^Total Enthalpy\s+\.\.\.\s+(" + _FLOAT + ")"),
    "g": re.compile(r"^Final Gibbs free energy\s+\.\.\.\s+(" + _FLOAT + ")"),
    "g_corr": re.compile(r"^G-E\(el\)\s+\.\.\.\s+(" + _FLOAT + ")"),
    "temperature": re.compile(r"^Temperature\s+\.\.\.\s+(" + _FLOAT + ") K"),
    "pressure": re.compile(r"^Pressure\s+\.\.\.\s+(" + _FLOAT + ") atm"),
    "molecular_mass": re.compile(r"^Total Mass\s+\.\.\.\s+(" + _FLOAT + ") AMU"),
}
_POINT_GROUP = re.compile(r"^Point Group:\s+(\S+),\s+Symmetry Number:\s+(\d+)")
_ROTATIONAL = re.compile(r"^Rotational constants in cm-1:\s+(.*)$")
_LINEAR_GROUPS = {"DINFH": "D*H", "CINFV": "C*V"}
_FREQUENCY = re.compile(r"^\s*(\d+):\s+(" + _FLOAT + r")\s+cm\*\*-1")


def looks_like_orca(text: str) -> bool:
    head = text[:20000]
    return "* O   R   C   A *" in head or ("Program Version" in head and "ORCA" in head)


def _read_input(lines: list[str]) -> tuple[list[str], list[str]]:
    """The echoed input: its `!` keywords, and all its lines (lower case) for blocks."""
    words: list[str] = []
    echoed: list[str] = []
    for line in lines[:3000]:
        match = _ECHO.match(line)
        if not match:
            if echoed and "****END OF INPUT****" in line:
                break
            continue
        text = match.group(1)
        if "****END OF INPUT****" in text:
            break
        echoed.append(text.strip().lower())
        stripped = text.strip()
        if stripped.lower().startswith("$new_job"):
            break  # only the first job is read (A18)
        if stripped.startswith("!"):
            words += stripped[1:].split("#", 1)[0].split()
    return words, echoed


def _read_coordinates(lines: list[str], start: int) -> tuple[list[Atom], int]:
    """`CARTESIAN COORDINATES (ANGSTROEM)` at lines[start], then dashes, then atoms."""
    atoms = []
    i = start + 2
    while i < len(lines):
        parts = lines[i].split()
        if len(parts) != 4:
            break
        element = element_of(parts[0])
        if element is None:
            break
        atoms.append(Atom(element, *(float(v) for v in parts[1:])))
        i += 1
    return atoms, i


def _read_normal_modes(lines: list[str], start: int, atom_count: int) -> tuple[list, int]:
    """The NORMAL MODES table: 3N rows per block of up to six (or ten) columns."""
    columns: dict[int, list[float]] = {}
    i = start
    rows = 3 * atom_count
    while i < len(lines):
        line = lines[i]
        if line.startswith("The first frequency") or line.startswith("-----------"):
            break
        parts = line.split()
        if parts and all(p.isdigit() for p in parts) and rows:
            indices = [int(p) for p in parts]
            i += 1
            if i < len(lines) and not lines[i].split()[0:1] == ["0"]:
                i += 1  # the irrep row printed with UseSym
            for row in range(rows):
                if i + row >= len(lines):
                    break
                values = lines[i + row].split()[1:]
                for index, value in zip(indices, values, strict=False):
                    columns.setdefault(index, []).append(float(value))
            i += rows
            continue
        i += 1
    modes = [
        [columns[k][3 * a : 3 * a + 3] for a in range(atom_count)]
        for k in sorted(columns)
        if len(columns[k]) == rows
    ]
    return modes, i


def _termination(lines: list[str]) -> str:
    tail = lines[-200:]
    return "normal" if any("ORCA TERMINATED NORMALLY" in line for line in tail) else "abnormal"


def _parse_block(lines: list[str], step: ParsedStep, geometries: bool = True) -> dict:
    """Read one step's lines into `step`; return the raw frequency data."""
    energies: list[float] = []
    frequencies: list[tuple[int, float]] = []
    first_vibration: int | None = None
    modes: list = []
    point: int | None = None  # D101: the relaxed scan step being read
    final_evaluation = False  # the next structure is the converged one, printed again
    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        if stripped == "CARTESIAN COORDINATES (ANGSTROEM)":
            atoms, i = _read_coordinates(lines, i)
            if atoms and geometries:
                step.geometries.append(atoms)
                step.geometry_energies.append(None)
                step.geometry_points.append(point)
                if final_evaluation and step.converged_geometries:
                    step.converged_geometries[-1] = len(step.geometries) - 1
                final_evaluation = False
            continue
        if line.startswith("FINAL SINGLE POINT ENERGY"):
            try:
                energies.append(float(line.split()[4]))
            except (IndexError, ValueError):
                pass
            else:
                if step.geometry_energies:
                    step.geometry_energies[-1] = energies[-1]
        elif match := re.search(r"RELAXED SURFACE SCAN STEP\s+(\d+)", line):
            step.scan, point = "relaxed", int(match.group(1))
        elif stripped == "* Parameter Scan Calculation *":
            step.scan = "rigid"
        elif "FINAL ENERGY EVALUATION AT THE STATIONARY POINT" in line:
            final_evaluation = True
        elif step.charge is None and line.startswith(" Total Charge") and "...." in line:
            step.charge = int(line.split()[-1])
        elif step.multiplicity is None and line.startswith(" Multiplicity") and "...." in line:
            step.multiplicity = int(line.split()[-1])
        elif "THE OPTIMIZATION HAS CONVERGED" in line:
            step.optimization_converged = True
            if step.geometries:
                step.converged_geometries.append(len(step.geometries) - 1)
        elif "The optimization did not converge" in line:
            step.optimization_converged = False
        elif stripped == "VIBRATIONAL FREQUENCIES" and not frequencies:
            header = i
            while i < len(lines) and i < header + 20 and not _FREQUENCY.match(lines[i]):
                i += 1
            while i < len(lines) and (match := _FREQUENCY.match(lines[i])):
                frequencies.append((int(match.group(1)), float(match.group(2))))
                i += 1
            continue
        elif stripped == "NORMAL MODES" and not modes:
            atom_count = len(step.geometries[-1]) if step.geometries else len(frequencies) // 3
            modes, i = _read_normal_modes(lines, i + 2, atom_count)
            continue
        elif line.startswith("The first frequency considered to be a vibration is"):
            first_vibration = int(line.split()[-1])
        elif (match := _POINT_GROUP.match(line)) and "symmetry_number" not in step.thermo:
            group = match.group(1)
            step.thermo["point_group"] = _LINEAR_GROUPS.get(group.upper(), group)
            step.thermo["symmetry_number"] = int(match.group(2))
            step.printed["symmetry"] = stripped
        elif (match := _ROTATIONAL.match(line)) and "thermo_rotational" not in step.thermo:
            try:
                constants = [float(v) for v in match.group(1).split()]
            except ValueError:
                constants = []
            if len(constants) == 3:
                step.thermo["thermo_rotational"] = constants
        else:
            for key, pattern in _THERMO.items():
                if key not in step.thermo and (match := pattern.match(line)) is not None:
                    step.thermo[key] = float(match.group(1))
                    step.printed[key] = stripped
                    break
        i += 1
    if energies:
        step.scf_energy = energies[-1]
    if step.scan == "rigid":  # every structure of a rigid scan is one of its points
        step.geometry_points = list(range(1, len(step.geometries) + 1))
        step.converged_geometries = list(range(len(step.geometries)))
    return {"frequencies": frequencies, "first": first_vibration, "modes": modes}


def _finish_frequencies(step: ParsedStep, raw: dict) -> None:
    frequencies = raw["frequencies"]
    if not frequencies:
        return
    first = raw["first"]
    if first is None:
        linear = step.thermo.get("point_group") in _LINEAR_GROUPS.values()
        first = 5 if linear else 6
    step.frequencies = [value for index, value in frequencies if index >= first]
    modes = raw["modes"]
    if len(modes) == len(frequencies):
        step.normal_modes = modes[first:]


def _finish_thermo(step: ParsedStep) -> None:
    thermo = step.thermo
    constants = thermo.pop("thermo_rotational", None)
    if constants is not None and step.frequencies:
        thermo["rotational_constants"] = [c * GHZ_PER_WAVENUMBER for c in constants]
        if thermo.get("point_group") in _LINEAR_GROUPS.values() or 0.0 in constants:
            thermo["rotational_temperatures"] = [
                c * KELVIN_PER_WAVENUMBER for c in constants if c != 0.0
            ]
        else:
            thermo["rotational_temperatures"] = [c * KELVIN_PER_WAVENUMBER for c in constants]
    electronic = thermo.pop("electronic", None)
    if electronic is None:
        return
    step.printed.pop("electronic", None)
    if "zpe" in thermo:
        thermo["e_zpe"] = electronic + thermo["zpe"]
    if "h" in thermo:
        thermo["h_corr"] = thermo["h"] - electronic
    if step.scf_energy is None:
        step.scf_energy = electronic


def parse(text: str) -> ParsedFile:
    if not looks_like_orca(text):
        raise NotOrcaOutput("not an ORCA output file")
    lines = text.splitlines()
    version = None
    for line in lines[:300]:
        if match := re.search(r"Program Version\s+(\S+)", line):
            version = match.group(1)
            break

    words, echoed = _read_input(lines)
    multiplicity = None
    for line in lines:
        if line.startswith(" Multiplicity") and "...." in line:
            multiplicity = int(line.split()[-1])
            break
    route = parse_keywords(words, multiplicity) if words else None
    if route is not None and any(re.match(r"^scan\b", line) for line in echoed):
        route.job_type = "other"  # a %geom scan (A18)

    termination = _termination(lines)
    # A18: a multi-job input ($new_job) is read up to its second job; the rest is not imported.
    later_jobs = next(
        (n for n, line in enumerate(lines) if re.match(r"^\s*\$+\s+JOB NUMBER\s+2\b", line)),
        None,
    )
    if later_jobs is not None:
        lines = lines[:later_jobs]
    compound = (
        route is not None
        and route.job_type
        in (
            "optimization",
            "ts_optimization",
        )
        and (route.compound_freq)
    )
    if compound:
        done = next(
            (n for n, line in enumerate(lines) if "*** OPTIMIZATION RUN DONE ***" in line),
            None,
        )
        # An optimization that never finished never started its frequency job.
        chunks = [(lines, route.job_type)]
        if done is not None:
            chunks = [(lines[: done + 1], route.job_type), (lines[done + 1 :], "frequency")]
    else:
        chunks = [(lines, route.job_type if route else "other")]

    steps: list[ParsedStep] = []
    for n, (chunk, job_type) in enumerate(chunks, start=1):
        step_route = None
        if route is not None:
            step_route = Route(**{**route.__dict__, "job_type": job_type, "compound_freq": False})
        step = ParsedStep(index=n, route=step_route)
        raw = _parse_block(chunk, step)
        _finish_frequencies(step, raw)
        _finish_thermo(step)
        # A step followed by another one finished; the last one ends with the file.
        step.termination = "normal" if n < len(chunks) else termination
        steps.append(step)
    for n, step in enumerate(steps):
        if n > 0:
            previous = steps[n - 1]
            if step.charge is None:
                step.charge, step.multiplicity = previous.charge, previous.multiplicity
            if not step.geometries and previous.geometries:
                step.geometries = [previous.geometries[-1]]
            if step.scf_energy is None:
                step.scf_energy = previous.scf_energy
        if step.charge is None:
            step.charge, step.multiplicity = _charge_from_input(echoed)
        _read_solvation(lines, step)
        if step.job_type in ("optimization", "ts_optimization"):
            step.optimization_converged = bool(step.optimization_converged)
        else:
            step.optimization_converged = None
        if step.route is not None:
            step.resolved_basis = step.route.basis
        step.missing = _missing_fields(step)
        if later_jobs is not None:
            step.missing.append("jobs after the first ($new_job)")
    return ParsedFile(program=PROGRAM, version=version, steps=steps)


def _charge_from_input(echoed: list[str]) -> tuple[int | None, int | None]:
    for line in echoed:
        if match := re.match(r"^\*\s*(xyz|int|gzmt|xyzfile|pdbfile)\s+(-?\d+)\s+(\d+)", line):
            return int(match.group(2)), int(match.group(3))
    return None, None


def _read_solvation(lines: list[str], step: ParsedStep) -> None:
    """The implicit solvation ORCA reports, which also covers %cpcm blocks (SMD, a solvent
    given only by its dielectric constant)."""
    if step.route is None:
        return
    start = next(
        (n for n, line in enumerate(lines) if line.strip() == "CPCM SOLVATION MODEL"), None
    )
    if start is None:
        return
    block = lines[start : start + 60]
    smd = any("SMD" in line for line in block) or any(
        "utilizes the SMD solvation module" in line for line in lines[:400]
    )
    solvent = epsilon = None
    for line in block:
        if line.startswith("Solvent:"):
            solvent = line.split("...")[-1].strip().lower()
        elif line.strip().startswith("Epsilon ") and "..." in line and epsilon is None:
            epsilon = line.split("...")[-1].strip()
    step.route.solvation_model = "SMD" if smd else (step.route.solvation_model or "CPCM")
    if step.route.solvation_model == "CPCMC":
        step.route.solvation_model = "CPCM"
    if solvent and solvent != "custom":
        step.route.solvent = solvent
    elif not step.route.solvent and epsilon:
        step.route.solvent = f"epsilon={epsilon}"


def _missing_fields(step: ParsedStep) -> list[str]:
    missing = []
    if step.route is None:
        missing += ["input keywords", "method", "basis set", "job type"]
    else:
        if not step.route.method:
            missing.append("method")
        if not step.route.basis and not has_built_in_basis(step.route.method or ""):
            missing.append("basis set")
    if step.charge is None:
        missing.append("charge and multiplicity")
    if not step.geometries:
        missing.append("geometry")
    if step.scf_energy is None and step.job_type != "other":
        missing.append("final single point energy")
    if step.job_type == "frequency":
        if not step.frequencies:
            missing.append("frequencies")
        for key, label in (("g", "free energy"), ("temperature", "temperature")):
            if key not in step.thermo:
                missing.append(label)
    return missing
