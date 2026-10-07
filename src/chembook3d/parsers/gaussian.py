"""Gaussian output parser (FR-IMP-01…04, docs/spec/05 §3.1).

A hand-written parser rather than cclib (D61): cclib merges the steps of a
multi-step file into one record and does not read the route keywords, GEN/GENECP basis
blocks, dispersion IOps or per-step termination, which is most of what an import needs.

Each job step (every --Link1-- job, and every internal step of a compound route such as
`opt freq`) becomes one ParsedStep. Every field is optional: what cannot be read is listed in
ParsedStep.missing so the import can report it as W-PARSE (FR-IMP-04). Energies stay in
hartree exactly as printed.
"""

import hashlib
import json
import re
from typing import Any

from chembook3d.parsers.common import ParsedFile, ParsedStep, Route
from chembook3d.xyz import ELEMENTS, Atom

PROGRAM = "Gaussian"


class NotGaussianOutput(ValueError):
    pass


# ---------- route section ----------

# Dispersion parameters set by IOp (D59): overlay 3, options 124 (model) and 174–179
# (s6, s8, a1, a2, …). Any other IOp does not change the level of theory.
DISPERSION_IOPS = {"3/124"} | {f"3/{n}" for n in range(174, 180)}

SOLVATION_MODELS = {"PCM": "IEFPCM", "IEFPCM": "IEFPCM", "CPCM": "CPCM", "SMD": "SMD"}
SOLVATION_MODELS |= {"IPCM": "IPCM", "SCIPCM": "SCIPCM", "DIPOLE": "DIPOLE"}

OTHER_JOB_KEYWORDS = {"IRC", "IRCMAX", "SCAN", "STABLE", "NMR", "POLAR", "TD", "ADMP", "BOMD"}

# Basis sets written as their own route word (`B3LYP 6-31G(d)`) rather than `method/basis`.
_BASIS_WORD = re.compile(
    r"^(STO-\dG|\d-\d+\+*G.*|CC-P.*|AUG-CC-P.*|DEF2.*|LANL.*|SDD.*|GEN|GENECP|CHKBASIS|"
    r"MIDIX|DGDZVP.*|TZVP|SVP|QZVP|SV|EPR-I+|UGBS.*|D95.*|CBSB.*|(JUN|JUL|MAY|APR)-CC-P.*)$"
)


def split_route_words(route: str) -> list[str]:
    """Split a route into words at spaces and '#' outside parentheses."""
    route = re.sub(r"^\s*#[PNTpnt]?", " ", route)
    words: list[str] = []
    depth = 0
    current = ""
    for char in route:
        if char == "(":
            depth += 1
        elif char == ")":
            depth = max(0, depth - 1)
        if depth == 0 and (char.isspace() or char == "#"):
            if current:
                words.append(current)
            current = ""
        else:
            current += char
    if current:
        words.append(current)
    return words


def _split_options(text: str) -> list[str]:
    """`(a,b=(c,d),e)` or `a` → ['a', 'b=(c,d)', 'e']."""
    text = text.strip()
    if text.startswith("(") and text.endswith(")"):
        text = text[1:-1]
    options, depth, current = [], 0, ""
    for char in text:
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
        if char == "," and depth == 0:
            options.append(current.strip())
            current = ""
        else:
            current += char
    if current.strip():
        options.append(current.strip())
    return options


def keyword_of(word: str) -> tuple[str, list[str]]:
    """`Opt=(TS,NoEigenTest)` → ('OPT', ['TS', 'NOEIGENTEST'])."""
    match = re.match(r"^([A-Za-z0-9_\-+*]+)\s*=?\s*(.*)$", word)
    if not match:
        return word.upper(), []
    name, rest = match.group(1).upper(), match.group(2)
    return name, [o.upper() for o in _split_options(rest)] if rest else []


_FROM_CHECKPOINT = "CHECK"


def parse_route(text: str) -> Route:
    route = Route(text=text)
    words = split_route_words(text)
    keywords = {}
    for word in words:
        name, options = keyword_of(word)
        keywords.setdefault(name, options)
        if name == "IOP":
            for option in options:
                key, _, value = option.partition("=")
                route.iops[key.strip()] = value.strip()

    for word in words:
        slash = word.find("/")
        if slash > 0 and not re.search(r"[=(]", word[:slash]):
            route.method, route.basis = word[:slash].upper(), word[slash + 1 :].upper()
            break
    else:
        for i, word in enumerate(words):
            if _BASIS_WORD.match(word.upper()) and i > 0 and "=" not in words[i - 1]:
                route.method, route.basis = words[i - 1].upper(), word.upper()
                break
    if route.basis:
        # 6-31G* is 6-31G(d) and 6-31G** is 6-31G(d,p); Gaussian writes the second form in the
        # route of a compound job's frequency step, so both must name the same basis set.
        route.basis = route.basis.replace("**", "(D,P)").replace("*", "(D)")

    if "OPT" in keywords:
        opt = keywords["OPT"]
        is_ts = any(o in ("TS", "QST2", "QST3") for o in opt)
        route.job_type = "ts_optimization" if is_ts else "optimization"
        route.compound_freq = "FREQ" in keywords
    elif "FREQ" in keywords:
        route.job_type = "frequency"
    elif OTHER_JOB_KEYWORDS & keywords.keys():
        route.job_type = "other"
    else:
        route.job_type = "single_point"

    if "EMPIRICALDISPERSION" in keywords and keywords["EMPIRICALDISPERSION"]:
        route.dispersion = keywords["EMPIRICALDISPERSION"][0]

    if "SCRF" in keywords and "CHECK" in keywords["SCRF"]:
        # SCRF=Check (a compound job's frequency step) reads the solvation model from the
        # checkpoint; _carry_forward takes it from the step before.
        route.solvation_model = _FROM_CHECKPOINT
    elif "SCRF" in keywords:
        model, solvent = "IEFPCM", "water"  # Gaussian's defaults
        for option in keywords["SCRF"]:
            key, _, value = option.partition("=")
            if key in SOLVATION_MODELS:
                model = SOLVATION_MODELS[key]
            elif key == "SOLVENT" and value:
                solvent = value.lower()
        route.solvation_model, route.solvent = model, solvent
    return route


def dispersion_iops(iops: dict[str, str]) -> dict[str, str]:
    return {k: v for k, v in sorted(iops.items()) if k in DISPERSION_IOPS}


# ---------- steps ----------


_FLOAT = r"[-+]?\d+\.\d*(?:[DEde][-+]?\d+)?"
_THERMO_LINES = {
    "zpe": r"^ Zero-point correction=\s+(" + _FLOAT + ")",
    "e_corr": r"^ Thermal correction to Energy=\s+(" + _FLOAT + ")",
    "h_corr": r"^ Thermal correction to Enthalpy=\s+(" + _FLOAT + ")",
    "g_corr": r"^ Thermal correction to Gibbs Free Energy=\s+(" + _FLOAT + ")",
    "e_zpe": r"^ Sum of electronic and zero-point Energies=\s+(" + _FLOAT + ")",
    "e_thermal": r"^ Sum of electronic and thermal Energies=\s+(" + _FLOAT + ")",
    "h": r"^ Sum of electronic and thermal Enthalpies=\s+(" + _FLOAT + ")",
    "g": r"^ Sum of electronic and thermal Free Energies=\s+(" + _FLOAT + ")",
    "molecular_mass": r"^ Molecular mass:\s+(" + _FLOAT + ")",
}
_THERMO_RE = {key: re.compile(pattern) for key, pattern in _THERMO_LINES.items()}
_TEMPERATURE_RE = re.compile(
    r"^ Temperature\s+(" + _FLOAT + r") Kelvin\.\s+Pressure\s+(" + _FLOAT + r") Atm\."
)
_CHARGE_RE = re.compile(r"^ Charge =\s*(-?\d+) Multiplicity =\s*(\d+)")
_ORIENTATIONS = ("Input orientation:", "Z-Matrix orientation:", "Standard orientation:")
_SCAN_POINT_RE = re.compile(r"^ Step number\s+\d+ out of a maximum of\s+\d+ on scan point\s+(\d+)")


def _is_dashes(line: str) -> bool:
    stripped = line.strip()
    return len(stripped) >= 10 and set(stripped) == {"-"}


def _read_orientation(lines: list[str], start: int) -> tuple[list[Atom], int]:
    """Read a coordinate table whose header is at lines[start]."""
    i = start + 5  # header, dashes, two column-title lines, dashes
    atoms = []
    while i < len(lines) and not _is_dashes(lines[i]):
        parts = lines[i].split()
        if len(parts) >= 6:
            number = int(parts[1])
            if number > 0:  # skip dummy and ghost atoms
                atoms.append(Atom(ELEMENTS[number - 1], *(float(v) for v in parts[-3:])))
        i += 1
    return atoms, i


def _read_route_and_title(lines: list[str]) -> tuple[str | None, str | None]:
    for i, line in enumerate(lines):
        if line.startswith(" #") and i > 0 and _is_dashes(lines[i - 1]):
            parts = []
            j = i
            while j < len(lines) and not _is_dashes(lines[j]):
                parts.append(lines[j][1:])
                j += 1
            route = "".join(parts).strip()
            # The title is the next dash-delimited block, before the molecule specification.
            title = None
            k = j + 1
            while k < len(lines) and k < j + 60:
                if lines[k].startswith(" Charge =") or "Symbolic Z-matrix" in lines[k]:
                    break
                if _is_dashes(lines[k]):
                    end = k + 1
                    while end < len(lines) and not _is_dashes(lines[end]):
                        end += 1
                    title = " ".join(line.strip() for line in lines[k + 1 : end]).strip()
                    break
                k += 1
            return route, title or None
    return None, None


def _normalize_block(block: list[str]) -> str:
    return "\n".join(" ".join(line.split()) for line in block if line.strip())


def _read_general_basis(lines: list[str], start: int) -> dict[int, str]:
    """`General basis read from cards` at lines[start]: {center number: shell text}."""
    header = lines[start].split("cards:", 1)[-1].strip()
    centers: dict[int, str] = {}
    i = start + 1
    while i < len(lines) and lines[i].startswith(" Centers:"):
        numbers = [int(n) for n in lines[i].split(":", 1)[1].split()]
        block = []
        i += 1
        while i < len(lines) and lines[i].strip() != "****":
            block.append(lines[i])
            i += 1
        i += 1
        text = f"{header}\n{_normalize_block(block)}"
        for number in numbers:
            centers[number] = text
    return centers


def _read_pseudopotentials(lines: list[str], start: int) -> dict[int, str]:
    """`Pseudopotential Parameters` table at lines[start]: {center number: ECP text}."""
    centers: dict[int, str] = {}
    i = start + 4  # title, '=', column titles (2 lines)
    while i < len(lines) and lines[i].strip().startswith("="):
        i += 1
    current: int | None = None
    block: list[str] = []
    center_re = re.compile(r"^\s+(\d+)\s+(\d+)(\s+\d+)?\s*$")
    while i < len(lines) and not lines[i].strip().startswith("="):
        match = center_re.match(lines[i])
        if match:
            if current is not None:
                centers[current] = _normalize_block(block)
            current, block = int(match.group(1)), [match.group(3) or ""]
        elif current is not None:
            block.append(lines[i])
        i += 1
    if current is not None:
        centers[current] = _normalize_block(block)
    return centers


def _element_basis(
    atoms: list[Atom], basis: dict[int, str], ecp: dict[int, str]
) -> dict[str, str] | None:
    """Per-element definitions, so the same custom basis matches across molecules."""
    if not basis or not atoms:
        return None
    per_element: dict[str, set[str]] = {}
    for number, atom in enumerate(atoms, start=1):
        text = basis.get(number, "")
        if ecp.get(number) and "No pseudopotential" not in ecp[number]:
            text += "\nECP\n" + ecp[number]
        per_element.setdefault(atom.element, set()).add(text)
    return {el: "\n----\n".join(sorted(texts)) for el, texts in sorted(per_element.items())}


def basis_fingerprints(definition: dict[str, str]) -> dict[str, str]:
    return {el: hashlib.sha256(text.encode()).hexdigest()[:16] for el, text in definition.items()}


def _parse_step(lines: list[str], index: int) -> ParsedStep:
    step = ParsedStep(index=index)
    route_text, step.title = _read_route_and_title(lines)
    if route_text:
        step.route = parse_route(route_text)

    orientations: dict[str, list[list[Atom]]] = {name: [] for name in _ORIENTATIONS}
    # D101: per orientation, the energy and scan point of each structure and the converged ones.
    energies: dict[str, list[float | None]] = {name: [] for name in _ORIENTATIONS}
    points: dict[str, list[int | None]] = {name: [] for name in _ORIENTATIONS}
    converged: dict[str, list[int]] = {name: [] for name in _ORIENTATIONS}
    basis: dict[int, str] = {}
    ecp: dict[int, str] = {}
    frequencies: list[float] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if step.charge is None and (match := _CHARGE_RE.match(line)):
            step.charge, step.multiplicity = int(match.group(1)), int(match.group(2))
        elif line.strip() in _ORIENTATIONS:
            atoms, i = _read_orientation(lines, i)
            if atoms:
                orientations[line.strip()].append(atoms)
                energies[line.strip()].append(None)
                points[line.strip()].append(None)
            continue
        elif line.startswith(" SCF Done:"):
            step.scf_energy = float(line.split()[4])
            for name in _ORIENTATIONS:
                if energies[name]:
                    energies[name][-1] = step.scf_energy
        elif "Optimization completed." in line or "-- Stationary point found." in line:
            step.optimization_converged = True
            for name in _ORIENTATIONS:
                last = len(orientations[name]) - 1
                if last >= 0 and last not in converged[name]:
                    converged[name].append(last)
        elif (match := _SCAN_POINT_RE.match(line)) is not None:
            step.scan = "relaxed"
            for name in _ORIENTATIONS:
                if points[name]:
                    points[name][-1] = int(match.group(1))
        elif line.startswith(" Summary of the potential surface scan"):
            step.scan = "rigid"
        elif line.startswith(" General basis read from cards"):
            basis = _read_general_basis(lines, i)
        elif line.strip() == "Pseudopotential Parameters":
            ecp = _read_pseudopotentials(lines, i)
        elif re.match(r"^ Frequencies --\s", line):  # HPModes blocks use '---' and are skipped
            values = [float(v) for v in line.split()[2:]]
            frequencies += values
            i = _read_mode_block(lines, i, len(values), step)
            continue
        elif (match := _TEMPERATURE_RE.match(line)) is not None:
            step.thermo["temperature"] = float(match.group(1))
            step.thermo["pressure"] = float(match.group(2))
            step.printed["temperature"] = line.strip()
        elif line.startswith(" Rotational symmetry number"):
            step.thermo["symmetry_number"] = int(line.split()[3].split(".")[0])
        elif line.startswith(" Rotational temperature"):
            values = line.split(")", 1)[-1].split()
            try:
                step.thermo["rotational_temperatures"] = [float(v) for v in values]
            except ValueError:
                step.thermo["rotational_temperatures"] = None  # '*****' for linear molecules
        elif line.startswith(" Rotational constants (GHZ):"):
            try:
                step.thermo["rotational_constants"] = [float(v) for v in line.split()[3:]]
            except ValueError:
                pass
        elif line.startswith(" Full point group"):
            step.thermo["point_group"] = line.split()[3]
        elif line.startswith(" Normal termination of Gaussian"):
            step.termination = "normal"
        else:
            for key, pattern in _THERMO_RE.items():
                if (match := pattern.match(line)) is not None:
                    step.thermo[key] = float(match.group(1).replace("D", "E"))
                    step.printed[key] = line.strip()
                    break
        i += 1

    step.frequencies = frequencies
    for name in _ORIENTATIONS:
        if orientations[name]:
            step.geometries = orientations[name]
            step.geometry_energies = energies[name]
            step.geometry_points = points[name]
            step.converged_geometries = converged[name]
            break
    if step.scan == "rigid":  # every structure of a rigid scan is one of its points
        step.geometry_points = list(range(1, len(step.geometries) + 1))
        step.converged_geometries = list(range(len(step.geometries)))
    if step.job_type in ("optimization", "ts_optimization") and not step.optimization_converged:
        step.optimization_converged = False
    elif step.job_type not in ("optimization", "ts_optimization"):
        step.optimization_converged = None
    if basis and step.geometries:
        step.basis_definition = _element_basis(step.geometries[0], basis, ecp)
    return step


def _read_mode_block(lines: list[str], start: int, count: int, step: ParsedStep) -> int:
    """Read reduced masses and displacements that follow a `Frequencies --` line."""
    i = start + 1
    modes: list[list[list[float]]] = [[] for _ in range(count)]
    while i < len(lines) and not lines[i].lstrip().startswith("Atom"):
        if lines[i].startswith(" Red. masses --"):
            step.reduced_masses += [float(v) for v in lines[i].split()[3:]]
        if lines[i].startswith(" Frequencies") or not lines[i].strip():
            return i
        i += 1
    i += 1
    while i < len(lines):
        parts = lines[i].split()
        if len(parts) != 2 + 3 * count or not parts[0].isdigit():
            break
        values = [float(v) for v in parts[2:]]
        for m in range(count):
            modes[m].append(values[3 * m : 3 * m + 3])
        i += 1
    step.normal_modes += modes
    return i


# ---------- file ----------

_STEP_START = (" Entering Link 1 = ", " Link1:  Proceeding to internal job step")


def looks_like_gaussian(text: str) -> bool:
    head = text[:20000]
    return "Entering Gaussian System" in head or "Gaussian, Inc." in head


def parse(text: str) -> ParsedFile:
    if not looks_like_gaussian(text):
        raise NotGaussianOutput("not a Gaussian output file")
    lines = text.splitlines()

    version = None
    for line in lines[:400]:
        if match := re.match(r"^ Gaussian (\d+):\s+\S*Rev([A-Z]\.\d+)", line):
            version = f"{match.group(1)} Rev. {match.group(2)}"
            break

    starts = [i for i, line in enumerate(lines) if line.startswith(_STEP_START)] or [0]
    starts[0] = 0
    chunks: list[tuple[list[str], bool]] = []  # (lines, continued by an internal step)
    for n, start in enumerate(starts):
        end = starts[n + 1] if n + 1 < len(starts) else len(lines)
        chunk = lines[start:end]
        internal_next = n + 1 < len(starts) and lines[starts[n + 1]].startswith(_STEP_START[1])
        # A step marker right before another one (e.g. both markers printed) holds nothing.
        if not any(line.startswith(" #") for line in chunk) and not any(
            _CHARGE_RE.match(line) for line in chunk
        ):
            continue
        chunks.append((chunk, internal_next))

    steps = []
    for n, (chunk, internal_next) in enumerate(chunks, start=1):
        step = _parse_step(chunk, n)
        if internal_next and step.termination != "normal":
            # A compound route's first step has no termination line of its own; the next
            # internal step starting means it finished.
            step.termination = "normal"
        steps.append(step)

    _carry_forward(steps)
    for step in steps:
        step.missing = _missing_fields(step)
    return ParsedFile(program=PROGRAM, version=version, steps=steps)


def _carry_forward(steps: list[ParsedStep]) -> None:
    """Steps without a route of their own, or reading from the checkpoint, inherit what an
    earlier step in the same file defined: the basis (CHKBASIS), the solvation model
    (SCRF=Check), charge and multiplicity, and the geometry (Geom=Check steps that print no
    coordinate table)."""
    basis: str | None = None
    definition: dict[str, str] | None = None
    solvation: tuple[str | None, str | None] = (None, None)
    for n, step in enumerate(steps):
        if step.route and step.route.solvation_model == _FROM_CHECKPOINT:
            step.route.solvation_model, step.route.solvent = solvation
        elif step.route:
            solvation = (step.route.solvation_model, step.route.solvent)
        written = step.route.basis if step.route else None
        if written and written != "CHKBASIS":
            basis = written
            definition = step.basis_definition if written in ("GEN", "GENECP") else None
        if written:
            step.resolved_basis, step.resolved_basis_definition = basis, definition
        if n > 0 and not step.geometries and steps[n - 1].geometries:
            step.geometries = [steps[n - 1].geometries[-1]]


def _missing_fields(step: ParsedStep) -> list[str]:
    missing = []
    if step.route is None:
        missing += ["route section", "method", "basis set", "job type"]
    else:
        if not step.route.method:
            missing.append("method")
        if not step.resolved_basis:
            missing.append("basis set")
        elif step.resolved_basis in ("GEN", "GENECP") and not step.resolved_basis_definition:
            missing.append("custom basis definition")
    if step.charge is None:
        missing.append("charge and multiplicity")
    if not step.geometries:
        missing.append("geometry")
    if step.scf_energy is None and step.job_type != "other":
        missing.append("SCF energy")
    if step.job_type == "frequency":
        if not step.frequencies:
            missing.append("frequencies")
        for key, label in (("g", "free energy"), ("temperature", "temperature and pressure")):
            if key not in step.thermo:
                missing.append(label)
    return missing


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))
