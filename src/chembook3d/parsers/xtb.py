"""xTB output parser (FR-IMP-07, docs/spec/05 §3; D64).

The level of theory is the Hamiltonian xTB prints (GFN2-xTB, GFN1-xTB, GFN-FF…) with the
implicit solvation given on the command line (`--alpb water`); xTB has no separate basis set.
The job type also comes from the command line: `--opt`, `--hess` and `--ohess` (an
optimization followed by a frequency job, read as two steps like Gaussian's `opt freq`).

xTB prints no coordinates for a single point or a frequency job (they are in the input file),
so such steps have no geometry and can only be imported onto an existing node (A15). An
optimization prints its final structure.

A relaxed scan (`$scan`) writes the optimized structure of each point to `xtbscan.log`, not to
its output; that file, and the `path.xyz` a scan path job writes in the same layout, are read
by `parse_structures` as one relaxed scan (D112).
"""

import re
import shlex

from chembook3d.parsers.common import ParsedFile, ParsedStep, Route
from chembook3d.xyz import ELEMENTS, Atom, element_of

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


class OptimizationLog(ValueError):
    """xTB's optimization log (`xtbopt.log`): every optimizer step, not results."""


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


def _version(lines: list[str]) -> str | None:
    for line in lines[:100]:
        if match := re.search(r"xtb version (\S+)", line):
            return match.group(1)
    return None


def parse(text: str) -> ParsedFile:
    if not looks_like_xtb(text):
        raise NotXtbOutput("not an xTB output file")
    lines = text.splitlines()
    version = _version(lines)
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


# ---------- relaxed scans and scan paths (D112) ----------

SCAN_ROUTE = "relaxed scan"  # the route text of every calculation read from such a file
DEFAULT_METHOD = "GFN2-xTB"  # xTB's default Hamiltonian
_COMMENT_KEY = re.compile(r"(\w+):\s*(\S+)")


def _comment(line: str) -> tuple[dict[str, str], list[str]]:
    """`energy: -15.4 gnorm: 0.0004 xtb: 6.7.1 (edcfbbe) stage: 2 call: xtb a.xyz --opt`: the
    keys before `call:` and the command line after it."""
    head, _, call = line.partition("call:")
    keys = {k.lower(): v for k, v in _COMMENT_KEY.findall(head)}
    if not call.strip():
        return keys, []
    try:
        return keys, shlex.split(call.strip(), posix=True)
    except ValueError:
        return keys, call.split()


def looks_like_structures(text: str) -> bool:
    """A multi-structure xyz whose comment lines start with `energy:`, as xTB writes them."""
    lines = text.lstrip("\ufeff").splitlines()
    return (
        len(lines) >= 3
        and lines[0].strip().isdigit()
        and lines[1].strip().startswith("energy:")
        and "energy" in _comment(lines[1])[0]
    )


def ran_relaxed_scan(text: str) -> bool:
    """An xTB output of a run that wrote a relaxed scan to xtbscan.log."""
    return looks_like_xtb(text) and "RELAXED SCAN" in text


def _float(value: str | None) -> float | None:
    try:
        return float(value) if value is not None else None
    except ValueError:
        return None


def _int(value: str | None) -> int | None:
    try:
        return int(value) if value is not None else None
    except ValueError:
        return None


def _frames(text: str) -> tuple[list[tuple[dict[str, str], list[str], list[Atom]]], bool]:
    """Every whole structure in the file, and whether the file ended inside one."""
    lines = text.lstrip("\ufeff").splitlines()
    frames = []
    i = 0
    while i < len(lines):
        if not lines[i].strip():
            i += 1
            continue
        header = lines[i].strip()
        if not header.isdigit() or i + 1 >= len(lines):
            return frames, True
        count = int(header)
        keys, call = _comment(lines[i + 1])
        atoms = []
        for number in range(i + 2, i + 2 + count):
            parts = lines[number].split() if number < len(lines) else []
            element = element_of(parts[0]) if len(parts) >= 4 else None
            if element is None:
                return frames, True
            try:
                atoms.append(Atom(element, *(float(v) for v in parts[1:4])))
            except ValueError:
                return frames, True
        if frames and [a.element for a in atoms] != [a.element for a in frames[0][2]]:
            raise NotXtbOutput(f"structure {len(frames) + 1} has different atoms")
        frames.append((keys, call, atoms))
        i += 2 + count
    return frames, False


def representative(energies: list[float | None]) -> int:
    """D112: the point a path's node takes (0-based): the highest top, a point higher than
    the points on either side of it, never the first or last; else the middle point."""
    count = len(energies)
    tops = [
        n
        for n in range(1, count - 1)
        if None not in energies[n - 1 : n + 2]
        and energies[n] > energies[n - 1]
        and energies[n] > energies[n + 1]
    ]
    if tops:
        return max(tops, key=lambda n: (energies[n], -n))
    return (count - 1) // 2


def _same_structure(a: list[Atom], b: list[Atom], tolerance: float = 1e-4) -> bool:
    return len(a) == len(b) and all(
        p.element == q.element
        and abs(p.x - q.x) <= tolerance
        and abs(p.y - q.y) <= tolerance
        and abs(p.z - q.z) <= tolerance
        for p, q in zip(a, b, strict=True)
    )


def _scan_output(outputs: list[str], first: list[Atom]) -> str | None:
    """The xTB output that wrote this scan: the only one in the folder that ran a relaxed
    scan, or the one whose final structure (the optimized start) is the scan's first point."""
    scans = [text for text in outputs if ran_relaxed_scan(text)]
    if len(scans) == 1:
        return scans[0]
    for text in scans:
        lines = text.splitlines()
        for n, line in enumerate(lines):
            if line.strip() == "final structure:":
                if _same_structure(_read_final_structure(lines, n), first):
                    return text
                break
    return None


def _electrons(atoms: list[Atom], charge: int) -> int:
    return sum(ELEMENTS.index(a.element) + 1 for a in atoms) - charge


def parse_structures(text: str, name: str = "", outputs: list[str] | None = None) -> ParsedFile:
    """An `xtbscan.log` or `path.xyz` as one relaxed scan whose points are its structures
    (D112). Its level of theory, charge and multiplicity come from a `call:` on its comment
    lines, else from the xTB output in `outputs` that ran the scan, else xTB's defaults."""
    frames, cut = _frames(text)
    if not frames:
        raise NotXtbOutput("no structures were found")
    first_keys = frames[0][0]
    if "gnorm" in first_keys and "stage" not in first_keys and "xtbopt" in name.lower():
        raise OptimizationLog(
            f"{name} is xTB's optimization log. Import the xTB output of that run instead; "
            "its optimized structure and energy are there"
        )
    version = first_keys.get("xtb")
    call = next((c for _, c, _ in frames if c), [])
    missing: list[str] = []
    charge: int | None = None
    multiplicity: int | None = None
    if call:
        route = route_from_call(call, _method([], call) or DEFAULT_METHOD)
        charge = _int(_flag_value(call, "--chrg", "-c"))
        unpaired = _int(_flag_value(call, "--uhf", "-u"))
        multiplicity = unpaired + 1 if unpaired is not None else None
    else:
        output = _scan_output(outputs or [], frames[0][2])
        if output is not None:
            lines = output.splitlines()
            args = _call(lines)
            route = route_from_call(args, _method(lines, args) or DEFAULT_METHOD)
            charge, multiplicity = _charge_multiplicity(lines, args)
            version = version or _version(lines)
        else:
            route = Route(text="", method=DEFAULT_METHOD)
            missing += [
                f"level of theory (taken as {DEFAULT_METHOD}, gas phase)",
                "charge and multiplicity (taken as xTB's defaults)",
            ]
    if charge is None:
        charge = 0
    if multiplicity is None:
        multiplicity = 1 if _electrons(frames[0][2], charge) % 2 == 0 else 2
    route.job_type = "other"
    route.compound_freq = False
    route.text = f"{SCAN_ROUTE}: {route.text}" if route.text else SCAN_ROUTE

    energies = [_float(keys.get("energy")) for keys, _, _ in frames]
    stages = [_int(keys.get("stage")) for keys, _, _ in frames]
    chosen = representative(energies)
    step = ParsedStep(
        index=1,
        route=route,
        title=name or None,
        charge=charge,
        multiplicity=multiplicity,
        geometries=[atoms for _, _, atoms in frames],
        geometry_energies=energies,
        geometry_points=list(range(1, len(frames) + 1)),
        geometry_stages=stages if any(s is not None for s in stages) else [],
        converged_geometries=list(range(len(frames))),
        scan="relaxed",
        node_geometry=chosen,
        scf_energy=energies[chosen],
        optimization_converged=None,
        termination="abnormal" if cut else "normal",
    )
    if step.scf_energy is None:
        missing.append("total energy")
    step.missing = missing
    return ParsedFile(program=PROGRAM, version=version, steps=[step])
