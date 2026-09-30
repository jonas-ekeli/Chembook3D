"""xTB output parser (FR-IMP-07, docs/spec/05 §3; D64).

The level of theory is the Hamiltonian xTB prints (GFN2-xTB, GFN1-xTB, GFN-FF…) with the
implicit solvation given on the command line (`--alpb water`); xTB has no separate basis set.
The job type also comes from the command line: `--opt`, `--hess` and `--ohess` (an
optimization followed by a frequency job, read as two steps like Gaussian's `opt freq`).

xTB prints no coordinates for a single point or a frequency job (they are in the input file),
so such steps have no geometry and can only be imported onto an existing node (A15). An
optimization prints its final structure.
"""

import re
import shlex

from chembook3d.parsers.common import ParsedFile, ParsedStep, Route
from chembook3d.xyz import Atom, element_of

PROGRAM = "xTB"
BOHR_ANGSTROM = 0.529177210903  # CODATA 2018

OPT_FLAGS = {"--opt", "-o", "--ohess"}
HESS_FLAGS = {"--hess", "--ohess", "--bhess"}
OTHER_FLAGS = {"--md", "--omd", "--metadyn", "--scan", "--path", "--modef", "--vip", "--vea",
               "--vipea", "--vomega", "--vfukui", "--stm", "--siman"}  # fmt: skip
SOLVATION_FLAGS = {"--alpb": "ALPB", "--gbsa": "GBSA", "-g": "GBSA", "--cosmo": "COSMO",
                   "--tmcosmo": "COSMO", "--cpcmx": "CPCM-X"}  # fmt: skip

_FLOAT = r"[-+]?\d+\.\d*(?:[EeDd][-+]?\d+)?"


class NotXtbOutput(ValueError):
    pass


def looks_like_xtb(text: str) -> bool:
    head = text[:20000]
    return "x T B" in head and "xtb version" in head


def _call(lines: list[str]) -> list[str]:
    for line in lines[:400]:
        if "program call" in line and ":" in line:
            try:
                return shlex.split(line.split(":", 1)[1], posix=True)
            except ValueError:
                return line.split(":", 1)[1].split()
    return []


def _flag_value(args: list[str], *names: str) -> str | None:
    for n, arg in enumerate(args):
        for name in names:
            if arg == name and n + 1 < len(args) and not args[n + 1].startswith("-"):
                return args[n + 1]
            if arg.startswith(name + "="):
                return arg.split("=", 1)[1]
    return None


def _method(lines: list[str], args: list[str]) -> str | None:
    for line in lines[:2000]:
        if match := re.search(r":\s+Hamiltonian\s+(\S+)\s+:", line):
            return match.group(1)
    if any(line.strip() == "|                 G F N - F F                 |" for line in lines):
        return "GFN-FF"
    if "--gfnff" in args:
        return "GFN-FF"
    level = _flag_value(args, "--gfn")
    return f"GFN{level}-xTB" if level else None


def route_from_call(args: list[str], method: str | None) -> Route:
    flags = set(args)
    route = Route(text=" ".join(args), method=method)
    if flags & OTHER_FLAGS:
        route.job_type = "other"
    elif flags & OPT_FLAGS:
        route.job_type = "optimization"
        route.compound_freq = "--ohess" in flags
    elif flags & HESS_FLAGS:
        route.job_type = "frequency"
    else:
        route.job_type = "single_point"
    for flag, model in SOLVATION_FLAGS.items():
        if flag in flags or any(a.startswith(flag + "=") for a in args):
            route.solvation_model = model
            route.solvent = (_flag_value(args, flag) or "").lower() or None
            break
    return route


def _read_final_structure(lines: list[str], start: int) -> list[Atom]:
    """`final structure:` at lines[start]: an xyz block, or Turbomole `$coord` in bohr."""
    i = start + 1
    while i < len(lines) and (not lines[i].strip() or set(lines[i].strip()) == {"="}):
        i += 1
    if i >= len(lines):
        return []
    atoms = []
    if lines[i].strip().startswith("$coord"):
        i += 1
        while i < len(lines) and not lines[i].strip().startswith("$"):
            parts = lines[i].split()
            element = element_of(parts[3]) if len(parts) >= 4 else None
            if element is None:
                return []
            x, y, z = (float(v) * BOHR_ANGSTROM for v in parts[:3])
            atoms.append(Atom(element, x, y, z))
            i += 1
        return atoms
    if not lines[i].strip().isdigit():
        return []
    count = int(lines[i].strip())
    for line in lines[i + 2 : i + 2 + count]:
        parts = line.split()
        element = element_of(parts[0]) if len(parts) == 4 else None
        if element is None:
            return []
        atoms.append(Atom(element, *(float(v) for v in parts[1:])))
    return atoms if len(atoms) == count else []


def _read_values(lines: list[str], start: int, pattern: re.Pattern[str]) -> list[str]:
    values = []
    i = start + 1
    while i < len(lines) and pattern.match(lines[i]):
        values += pattern.match(lines[i]).group(1).split()
        i += 1
    return values


_EIGVAL = re.compile(r"^eigval :\s+(.*)$")
_MASSES = re.compile(r"^\s+(\d+:.*)$")
_SUMMARY = {
    "energy": re.compile(r"^\s+:: total energy\s+(" + _FLOAT + ") Eh"),
    "zpe": re.compile(r"^\s+:: zero point energy\s+(" + _FLOAT + ") Eh"),
    "g_corr": re.compile(r"^\s+:: G\(RRHO\) contrib\.\s+(" + _FLOAT + ") Eh"),
    "total": re.compile(r"^\s+\| TOTAL ENERGY\s+(" + _FLOAT + ") Eh"),
    "h": re.compile(r"^\s+\| TOTAL ENTHALPY\s+(" + _FLOAT + ") Eh"),
    "g": re.compile(r"^\s+\| TOTAL FREE ENERGY\s+(" + _FLOAT + ") Eh"),
}


def _parse_block(lines: list[str], step: ParsedStep) -> None:
    energies: list[float] = []
    total: float | None = None
    raw_frequencies: list[float] = []
    reduced_masses: list[float] = []
    linear = False
    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        if stripped == "final structure:":
            atoms = _read_final_structure(lines, i)
            if atoms:
                step.geometries.append(atoms)
        elif "GEOMETRY OPTIMIZATION CONVERGED" in line:
            step.optimization_converged = True
        elif "FAILED TO CONVERGE GEOMETRY OPTIMIZATION" in line:
            step.optimization_converged = False
        elif stripped.startswith("projected vibrational frequencies") and not raw_frequencies:
            raw_frequencies = [float(v) for v in _read_values(lines, i, _EIGVAL)]
        elif stripped == "reduced masses (amu)" and not reduced_masses:
            pairs = _read_values(lines, i, _MASSES)
            reduced_masses = [float(v) for v in pairs if not v.endswith(":")]
        elif re.match(r"^\s+:\s+linear\?\s+true", line):
            linear = True
        elif match := re.match(r"^\s+:\s+symmetry\s+(\S+)\s+:", line):
            step.thermo["point_group"] = match.group(1).upper()
        elif match := re.match(r"^\s+:\s+rotational number\s+(\d+)\s+:", line):
            step.thermo["symmetry_number"] = int(match.group(1))
        elif stripped.startswith("T/K") and "G(T)/Eh" in line and i + 2 < len(lines):
            # The first row of the table below: T/K, H(0)-H(T)+PV, H(T), T*S, G(T)
            row = lines[i + 2].split()
            if "temperature" not in step.thermo and len(row) == 5:
                step.thermo["temperature"] = float(row[0])
        else:
            for key, pattern in _SUMMARY.items():
                if (match := pattern.match(line)) is not None:
                    value = float(match.group(1))
                    if key == "energy":
                        energies.append(value)
                    elif key == "total":
                        total = value
                    else:
                        step.thermo[key] = value
                        step.printed[key] = stripped.strip("|: ")
                    break
        i += 1

    step.scf_energy = total if total is not None else (energies[-1] if energies else None)
    if raw_frequencies:
        # Drop the translations and rotations, printed as ±0.00 (5 for a linear molecule).
        zero = 5 if linear else 6
        kept, dropped = [], []
        for n, value in enumerate(raw_frequencies):
            if len(dropped) < zero and value == 0.0:
                dropped.append(n)
            else:
                kept.append(n)
        step.frequencies = [raw_frequencies[n] for n in kept]
        if len(reduced_masses) == len(raw_frequencies):
            step.reduced_masses = [reduced_masses[n] for n in kept]
    if linear:
        step.thermo["point_group"] = "C*V" if step.thermo.get("point_group") == "C*V" else "D*H"
    if "h" in step.thermo and step.scf_energy is not None:
        step.thermo["h_corr"] = step.thermo["h"] - step.scf_energy
    if "zpe" in step.thermo and step.scf_energy is not None:
        step.thermo["e_zpe"] = step.scf_energy + step.thermo["zpe"]


def _termination(lines: list[str]) -> str:
    tail = lines[-80:]
    if any("abnormal termination" in line or "#ERROR!" in line for line in tail):
        return "abnormal"
    return "normal" if any("* finished run on" in line for line in tail) else "abnormal"


def _charge_multiplicity(lines: list[str], args: list[str]) -> tuple[int | None, int | None]:
    charge = _flag_value(args, "--chrg", "-c")
    unpaired = _flag_value(args, "--uhf", "-u")
    value = None
    if charge is not None:
        try:
            value = int(charge)
        except ValueError:
            value = None
    if value is None:
        for line in lines:
            if match := re.match(r"^\s+:: total charge\s+(" + _FLOAT + ") e", line):
                value = round(float(match.group(1)))
                break
    electrons = None
    for line in lines[:2000]:
        if match := re.search(r":\s+# electrons\s+(\d+)\s+:", line):
            electrons = int(match.group(1))
            break
    if unpaired is not None:
        try:
            return value, int(unpaired) + 1
        except ValueError:
            pass
    if electrons is None:
        return value, None
    # xTB's default: all electrons paired, or one unpaired for an odd count.
    return value, 1 if electrons % 2 == 0 else 2


def parse(text: str) -> ParsedFile:
    if not looks_like_xtb(text):
        raise NotXtbOutput("not an xTB output file")
    lines = text.splitlines()
    version = None
    for line in lines[:100]:
        if match := re.search(r"xtb version (\S+)", line):
            version = match.group(1)
            break
    args = _call(lines)
    route = route_from_call(args, _method(lines, args))
    charge, multiplicity = _charge_multiplicity(lines, args)
    termination = _termination(lines)

    chunks = [(lines, route.job_type)]
    if route.compound_freq:
        start = next((n for n, line in enumerate(lines) if "Numerical Hessian" in line), None)
        if start is not None:
            chunks = [(lines[:start], "optimization"), (lines[start:], "frequency")]

    steps: list[ParsedStep] = []
    for n, (chunk, job_type) in enumerate(chunks, start=1):
        step_route = Route(**{**route.__dict__, "job_type": job_type, "compound_freq": False})
        step = ParsedStep(index=n, route=step_route, charge=charge, multiplicity=multiplicity)
        _parse_block(chunk, step)
        step.termination = "normal" if n < len(chunks) else termination
        if n > 1 and not step.geometries and steps[-1].geometries:
            step.geometries = [steps[-1].geometries[-1]]
        if job_type == "optimization":
            step.optimization_converged = bool(step.optimization_converged)
        else:
            step.optimization_converged = None
        step.missing = _missing_fields(step)
        steps.append(step)
    return ParsedFile(program=PROGRAM, version=version, steps=steps)


def _missing_fields(step: ParsedStep) -> list[str]:
    missing = []
    if step.route is None or not step.route.method:
        missing.append("method")
    if step.charge is None:
        missing.append("charge and multiplicity")
    if not step.geometries:
        missing.append("geometry")
    if step.scf_energy is None and step.job_type != "other":
        missing.append("total energy")
    if step.job_type == "frequency":
        if not step.frequencies:
            missing.append("frequencies")
        if "g" not in step.thermo:
            missing.append("free energy")
    return missing
