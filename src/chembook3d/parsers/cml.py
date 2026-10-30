"""ioChem-BD's CML of a Gaussian output (D186).

ioChem-BD (https://www.iochem-bd.org) publishes calculations as CML in the compchem convention,
converted from the program's output; for Gaussian the deposit holds the input and this
`output.cml`, not the log. Each `cc:job` module is one Gaussian job (a --Link1-- step) and
becomes one ParsedStep, as `gaussian.parse` makes them, so the import treats both alike: the
route from the job's `g:keyword` parameters (read by `gaussian.parse_route`), the title, charge
and multiplicity, the structures (the job's first orientation and the final molecule), the SCF
energies, the optimizer's convergence tables, the GEN/GENECP basis set and pseudopotentials
printed by GFInput, the frequencies with their modes and the thermochemistry, ⟨S²⟩ from the
archive, and normal termination (the archive is written only when a job ends normally).

What the CML does not keep is not guessed: an optimization's intermediate structures (ioChem-BD
keeps the first and the last), a correlated method's total, a counterpoise or ONIOM total and a
scan's points are listed in ParsedStep.missing or left out, as for a log that lacks them.
"""

import re
import xml.etree.ElementTree as ET

from chembook3d.parsers import gaussian
from chembook3d.parsers.common import (
    NOT_STATIONARY,
    S2,
    S2_AFTER,
    Criterion,
    ParsedFile,
    ParsedStep,
    add_constraint,
    coordinate_name,
)
from chembook3d.xyz import Atom

CML = "{http://www.xml-cml.org/schema}"
TEMPLATE = "{http://www.xml-cml.org/schema/cmlx}templateRef"

# The thermochemistry Gaussian prints after a frequency job, by its CML name (hartree).
_THERMO = {
    "cc:zpe.correction": "zpe",
    "cc:zpe.thermalcorrener": "e_corr",
    "cc:zpe.thermalcorrenthalpy": "h_corr",
    "cc:zpe.thermalcorrgfe": "g_corr",
    "cc:zpe.sumelectzpe": "e_zpe",
    "cc:zpe.sumelectthermal": "e_thermal",
    "cc:zpe.sumelectthermalent": "h",
    "cc:zpe.sumelectthermalfe": "g",
}
_CRITERIA = {
    "maximum force": ("Maximum force", "Eh/bohr"),
    "rms force": ("RMS force", "Eh/bohr"),
    "maximum displacement": ("Maximum displacement", "bohr"),
    "rms displacement": ("RMS displacement", "bohr"),
}


class NotCml(ValueError):
    pass


def looks_like_cml(text: str) -> bool:
    head = text[:4000]
    return "<module" in head and "xml-cml.org/schema" in head and "compchem" in head


def _ref(element: ET.Element) -> str:
    return element.get("dictRef") or ""


def _template(element: ET.Element) -> str:
    return element.get(TEMPLATE) or ""


def _values(element: ET.Element | None) -> list[str]:
    if element is None or not element.text:
        return []
    text = element.text.strip()
    return text.split(element.get("delimiter")) if element.get("delimiter") else text.split()


def _first(parent: ET.Element, ref: str) -> ET.Element | None:
    return next((e for e in parent.iter() if _ref(e) == ref), None)


def _text(parent: ET.Element | None, ref: str) -> str | None:
    if parent is None:
        return None
    found = _first(parent, ref)
    if found is None:
        return None
    if found.tag in (CML + "parameter", CML + "property"):
        found = found.find(CML + "scalar")
    return found.text.strip() if found is not None and found.text else None


def _float(value: str | None) -> float | None:
    try:
        return float(value.replace("D", "E")) if value is not None else None
    except ValueError:
        return None


def _section(job: ET.Element, ref: str) -> ET.Element | None:
    return next((c for c in job if _ref(c) == ref), None)


def _atoms(molecule: ET.Element) -> list[Atom]:
    atoms = []
    for atom in molecule.iter(CML + "atom"):
        element = atom.get("elementType")
        coordinates = [_float(atom.get(axis)) for axis in ("x3", "y3", "z3")]
        if element and all(c is not None for c in coordinates):
            atoms.append(Atom(element, *coordinates))
    return atoms


def _route(initialization: ET.Element | None) -> str | None:
    """The route as Gaussian echoes it, from the job's keywords (`#P`, `#` between lines)."""
    if initialization is None:
        return None
    words = []
    for parameter in initialization.iter(CML + "parameter"):
        if _ref(parameter) == "g:keyword":
            word = (parameter.findtext(CML + "scalar") or "").strip().strip("#").strip()
            if word:
                words.append(word)
    if not words:
        return None
    prefix = "#"
    if words[0].upper() in ("P", "N", "T"):
        prefix += words.pop(0).upper()
    return f"{prefix} " + " ".join(words)


def _basis(job: ET.Element) -> tuple[dict[int, str], dict[int, str]]:
    """The GFInput basis set ({center: shells}) and pseudopotentials ({center: terms}), as
    `gaussian._read_general_basis` and `_read_pseudopotentials` key them."""
    basis: dict[int, str] = {}
    for module in job.iter(CML + "module"):
        if _template(module) not in ("l301.basis2", "l302.basis2"):
            continue
        header = _text(module, "cc:diffuse") or ""
        for centers in module:
            if _template(centers) != "centers":
                continue
            numbers = [int(n) for n in _values(_first(centers, "cc:atomcount"))]
            lines = []
            for shell in centers:
                if _template(shell) != "shell":
                    continue
                kind, count, scale = (
                    _text(shell, ref) or "" for ref in ("x:itype", "x:ngauss", "x:scale")
                )
                lines.append(f"{kind} {count} {scale}")
                exponents = _values(_first(shell, "x:exponent"))
                coefficients = [_values(c) for c in shell.iter() if _ref(c) == "x:coeficient"]
                for n, exponent in enumerate(exponents):
                    lines.append(" ".join([exponent, *(c[n] for c in coefficients if n < len(c))]))
            text = f"{header}\n" + "\n".join(lines)
            for number in numbers:
                basis[number] = text
    ecp: dict[int, str] = {}
    for module in job.iter(CML + "module"):
        if _template(module) != "pseudopot":
            continue
        for atom in module:
            serial = _text(atom, "cc:serial")
            if serial is None:
                continue
            if _first(atom, "cc:nopseudo") is not None:
                ecp[int(serial)] = "No pseudopotential on this center."
                continue
            lines = [_text(atom, "x:valelectrons") or ""]
            for params in atom.iter(CML + "module"):
                if _template(params) != "params":
                    continue
                lines.append(_text(params, "cc:angmomentum") or "")
                columns = [
                    _values(_first(params, ref))
                    for ref in ("g:powerofr", "cc:basisexponent", "cc:expcoeff", "g:socoeff")
                ]
                lines += [" ".join(row) for row in zip(*columns, strict=False)]
            ecp[int(serial)] = "\n".join(line for line in lines if line)
    return basis, ecp


def _criteria(table: ET.Element) -> tuple[list[Criterion], list[tuple[str, bool]]]:
    names = table.findtext(f".//{CML}array[@dictRef='g:item']") or ""
    items = [i.strip() for i in names.split("|") if i.strip()]
    values = _values(table.find(f".//{CML}array[@dictRef='g:value']"))
    thresholds = _values(table.find(f".//{CML}array[@dictRef='g:threshold']"))
    met = _values(table.find(f".//{CML}array[@dictRef='g:converged']"))
    found, lines = [], []
    for n, item in enumerate(items):
        name, unit = _CRITERIA.get(" ".join(item.lower().split()), (item, ""))
        value = _float(values[n]) if n < len(values) else None
        threshold = _float(thresholds[n]) if n < len(thresholds) else None
        converged = met[n].upper() == "YES" if n < len(met) else None
        lines.append((f"{item} {values[n] if n < len(values) else ''}", bool(converged)))
        if value is not None:
            found.append(Criterion(name, value, threshold, converged, unit))
    return found, lines


def _held(job: ET.Element, step: ParsedStep) -> None:
    """D157: coordinates the optimizer's parameter table marks Scan or Frozen."""
    for table in job.iter(CML + "list"):
        if _template(table) not in ("length", "angle", "dihed"):
            continue
        derivs = (table.findtext(f"{CML}array[@dictRef='g:deriv']") or "").split("|")
        atoms = [
            _values(table.find(f"{CML}array[@dictRef='g:atom{k}']"))
            for k in range(1, 5)
            if table.find(f"{CML}array[@dictRef='g:atom{k}']") is not None
        ]
        for n, deriv in enumerate(derivs):
            action = deriv.strip().lower()
            if action not in ("scan", "frozen"):
                continue
            numbers = [column[n] for column in atoms if n < len(column)]
            letter = {"length": "B", "angle": "A", "dihed": "D"}[_template(table)]
            add_constraint(step, f"{action} {coordinate_name(numbers, letter)}")
            if action == "scan":
                step.scan = "relaxed"
                if [int(a) for a in numbers] not in step.scan_coordinates:
                    step.scan_coordinates.append([int(a) for a in numbers])


def _frequencies(finalization: ET.Element, step: ParsedStep) -> None:
    vibrations = _first(finalization, "cc:vibrations")
    if vibrations is None:
        return
    step.frequencies = [float(v) for v in _values(_first(vibrations, "cc:frequency"))]
    step.reduced_masses = [float(v) for v in _values(_first(vibrations, "cc:redmass"))]
    displacements = [float(v) for v in _values(_first(vibrations, "cc:displacement"))]
    count = len(step.frequencies)
    atoms = len(displacements) // (3 * count) if count else 0
    if atoms and len(displacements) == 3 * count * atoms:
        step.normal_modes = [
            [displacements[3 * (m * atoms + a) : 3 * (m * atoms + a) + 3] for a in range(atoms)]
            for m in range(count)
        ]


def _thermochemistry(finalization: ET.Element, step: ParsedStep) -> None:
    thermo = _first(finalization, "cc:thermochemistry")
    if thermo is not None:
        temperature, pressure = _float(_text(thermo, "cc:temp")), _float(_text(thermo, "cc:press"))
        if temperature is not None and pressure is not None:
            step.thermo["temperature"], step.thermo["pressure"] = temperature, pressure
            step.printed["temperature"] = (
                f"Temperature {temperature} Kelvin. Pressure {pressure} Atm."
            )
        if (symmetry := _text(thermo, "cc:symmnumber")) is not None:
            step.thermo["symmetry_number"] = int(float(symmetry))
        rottemp = _values(_first(thermo, "cc:rottemp"))
        if rottemp:
            step.thermo["rotational_temperatures"] = [float(v) for v in rottemp]
        rotconst = _values(_first(thermo, "cc:rotconst"))
        if rotconst:
            step.thermo["rotational_constants"] = [float(v) for v in rotconst]
        if (mass := _float(_text(thermo, "cc:molmass"))) is not None:
            step.thermo["molecular_mass"] = mass
            step.printed["molecular_mass"] = f"Molecular mass: {mass} amu."
    zeropoint = _first(finalization, "cc:zeropoint")
    if zeropoint is not None:
        for scalar in zeropoint.iter(CML + "scalar"):
            key = _THERMO.get(_ref(scalar))
            if key and (value := _float(scalar.text)) is not None:
                step.thermo[key] = value
                step.printed[key] = f"{_ref(scalar)} = {scalar.text.strip()}"


def _step(job: ET.Element, index: int) -> ParsedStep:
    step = ParsedStep(index=index)
    initialization = _section(job, "cc:initialization")
    calculation = _section(job, "cc:calculation")
    finalization = _section(job, "cc:finalization")
    route = _route(initialization)
    if route:
        step.route = gaussian.parse_route(route)
    titles = [
        s.text.strip()
        for m in job.iter(CML + "module")
        if _template(m) == "l101.title"
        for s in m.iter(CML + "scalar")
        if s.text
    ]
    step.title = titles[0] if titles else None

    if initialization is not None:
        charge = _text(initialization, "g:charge")
        multiplicity = _text(initialization, "g:mult")
        if charge is not None and multiplicity is not None:
            step.charge, step.multiplicity = int(charge), int(multiplicity)
    final = finalization.find(CML + "molecule") if finalization is not None else None
    if step.charge is None and final is not None and final.get("formalCharge") is not None:
        step.charge = int(final.get("formalCharge"))
        step.multiplicity = int(final.get("spinMultiplicity") or 1)

    # The job's first orientation, then the final structure (ioChem-BD keeps no others).
    first = None
    for part in (initialization, calculation):
        if part is None or first is not None:
            continue
        for molecule in part.iter(CML + "molecule"):
            atoms = _atoms(molecule)
            if atoms:
                first = atoms
                break
    energies = [
        _float(s.text)
        for m in job.iter(CML + "module")
        if _template(m) == "l502.footer"
        for s in m.iter(CML + "scalar")
        if _ref(s) == "g:rbhflyp"
    ]
    energies = [e for e in energies if e is not None]
    archive = next((m for m in job.iter(CML + "module") if _template(m) == "l9999.archive"), None)
    last_atoms = _atoms(final) if final is not None else []
    if first:
        step.geometries.append(first)
        step.geometry_energies.append(energies[0] if energies else None)
    if last_atoms and last_atoms != first:
        step.geometries.append(last_atoms)
        step.geometry_energies.append(energies[-1] if energies else None)
    step.geometry_points = [None] * len(step.geometries)
    step.scf_energy = energies[-1] if energies else _float(_text(archive, "cc:hfenergy"))

    # Convergence: the optimizer's tables (the last is the final structure's) and its verdict.
    tables = [m for m in job.iter(CML + "module") if _template(m) == "l103.itemconverge"]
    said = [
        (s.text or "").strip()
        for s in job.iter(CML + "scalar")
        if _ref(s) == "g:optimization" and s.text
    ]
    stationary = any("Stationary point found" in s or "Optimization completed" in s for s in said)
    lines: list[tuple[str, bool]] = []
    if tables:
        found, lines = _criteria(tables[-1])
        predicted = _float(
            next((s.text for s in job.iter(CML + "scalar") if _ref(s) == "g:predchange"), None)
        )
        if predicted is not None:
            found.append(Criterion("Predicted energy change", predicted, None, None, "Eh"))
        if step.geometries:
            step.geometry_criteria = [[] for _ in step.geometries]
            step.geometry_criteria[-1] = found
    if step.job_type in ("optimization", "ts_optimization"):
        step.optimization_converged = stationary
        if stationary and step.geometries:
            step.converged_geometries = [len(step.geometries) - 1]
    if step.job_type == "frequency" and lines and not stationary:
        unmet = [text for text, met in lines if not met]
        step.printed[NOT_STATIONARY] = "; ".join(unmet) or "no stationary point found"
    _held(job, step)

    basis, ecp = _basis(job)
    if basis and step.geometries:
        step.basis_definition = gaussian._element_basis(step.geometries[0], basis, ecp)
    if finalization is not None:
        _frequencies(finalization, step)
        _thermochemistry(finalization, step)
    if archive is not None:
        step.termination = "normal"
        s2, after = _text(archive, "x:S2"), _text(archive, "x:S2A")
        if s2 is not None:
            step.printed[S2] = s2
            if after is not None:
                step.printed[S2_AFTER] = after

    method = step.route.method if step.route else None
    if gaussian.correlated_total_of(method) is not None:
        step.scf_energy, step.correlated_missing = None, True  # D153: never the reference's
    elif step.route is not None and step.route.counterpoise:
        step.scf_energy = None
        step.energy_missing = "counterpoise-corrected energy (not kept in the CML)"
    elif gaussian.is_oniom(method):
        step.scf_energy = None
        step.energy_missing = "ONIOM extrapolated energy (not kept in the CML)"
    return step


def _version(root: ET.Element) -> str | None:
    found = _text(root, "cc:version")
    match = re.search(r"G(\d+)Rev([A-Z]\.\d+)", found or "")
    return f"{match.group(1)} Rev. {match.group(2)}" if match else found


def parse(text: str) -> ParsedFile:
    if not looks_like_cml(text):
        raise NotCml("not a CML file in the compchem convention")
    if "<!DOCTYPE" in text[:4000] or "<!ENTITY" in text:
        raise NotCml("a CML file with a document type definition is not read")
    try:
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        raise NotCml(f"not well-formed XML ({exc})") from exc
    program = _text(root, "cc:program") or ""
    if not program.lower().startswith("gaussian"):
        raise NotCml(f"only CML of Gaussian outputs is read, not of {program or 'this program'}")
    jobs = [m for m in root.iter(CML + "module") if _ref(m) == "cc:job"]
    steps = [_step(job, n) for n, job in enumerate(jobs, start=1)]
    _carry(steps)
    gaussian._carry_forward(steps)
    gaussian._thermo_models(steps)
    for step in steps:
        step.missing = gaussian._missing_fields(step)
    return ParsedFile(program=gaussian.PROGRAM, version=_version(root), steps=steps)


def _carry(steps: list[ParsedStep]) -> None:
    """A job that reads its structure from the checkpoint names no charge and multiplicity in
    the CML: it keeps the one before's, as Gaussian does."""
    state: tuple[int | None, int | None] = (None, None)
    for step in steps:
        if step.charge is None:
            step.charge, step.multiplicity = state
        state = (step.charge, step.multiplicity)
