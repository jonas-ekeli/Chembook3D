"""Gaussian output parser (FR-IMP-01…04, docs/spec/05 §3.1).

A hand-written parser rather than cclib (D61): cclib merges the steps of a
multi-step file into one record and does not read the route keywords, GEN/GENECP basis
blocks, dispersion IOps or per-step termination, which is most of what an import needs.

Each job step (every --Link1-- job, and every internal step of a compound route such as
`opt freq`) becomes one ParsedStep. Every field is optional: what cannot be read is listed in
ParsedStep.missing so the import can report it as W-PARSE (FR-IMP-04). Energies stay in
hartree exactly as printed.
"""

import contextlib
import hashlib
import json
import re
from typing import Any

from chembook3d.parsers.common import (
    BSSE,
    FREQUENCY_SCALE,
    NOT_STATIONARY,
    S2,
    S2_AFTER,
    Criterion,
    IrcPoint,
    IrcRun,
    ParsedFile,
    ParsedStep,
    Route,
    add_constraint,
    coordinate_name,
    set_thermo_model,
)
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
# D153: a method written with options before the basis set, such as CCSD(T)/…, MP2(Full)/…
# or MP4(SDQ)/… (IOp(3/174=…) has an "=" and stays a keyword).
_METHOD_WITH_OPTIONS = re.compile(r"^(?!IOP\()[A-Z][A-Z0-9-]*\([A-Z0-9,]+\)$", re.IGNORECASE)
# D161: an ONIOM route word, `ONIOM(B3LYP/6-31G(d):UFF)` or with options after it
# (`ONIOM(B3LYP/6-31G(d):PM6)=EmbedCharge`).
_ONIOM_WORD = re.compile(r"^ONIOM\s*\(", re.IGNORECASE)


def _basis_spelling(text: str) -> str:
    """6-31G* is 6-31G(d) and 6-31G** is 6-31G(d,p); Gaussian writes the second form in the
    route of a compound job's frequency step, so both must name the same basis set."""
    return text.replace("**", "(D,P)").replace("*", "(D)")


def is_oniom(method: str | None) -> bool:
    """D161: an ONIOM method, `ONIOM(high:low)` with each layer's method and basis set."""
    return bool(method) and bool(_ONIOM_WORD.match(method))


def _scale_in_route(keywords: dict[str, list[str]]) -> str | None:
    """D163: the frequency scale factor a route sets for the thermochemistry: `Scale=0.97` on
    its own or as an option of Freq; None when it sets none (Gaussian then uses 1)."""
    found = keywords.get("SCALE")
    if found and _SCALE_VALUE.match(found[0]):
        return found[0]
    for option in keywords.get("FREQ", []):
        key, _, value = option.partition("=")
        if key.strip() == "SCALE" and _SCALE_VALUE.match(value.strip()):
            return value.strip()
    return None


_SCALE_VALUE = re.compile(r"^\d*\.?\d+$")


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

    oniom = next((word for word in words if _ONIOM_WORD.match(word)), None)
    if oniom is not None:
        # D161: the layers as written are the method, each with its own basis set; options
        # such as EmbedCharge change the energy and stay part of it.
        route.method = _basis_spelling(re.sub(r"\s+", "", oniom).upper())
    else:
        for word in words:
            slash = word.find("/")
            if slash > 0 and (
                not re.search(r"[=(]", word[:slash]) or _METHOD_WITH_OPTIONS.match(word[:slash])
            ):
                route.method, route.basis = word[:slash].upper(), word[slash + 1 :].upper()
                break
        else:
            for i, word in enumerate(words):
                if _BASIS_WORD.match(word.upper()) and i > 0 and "=" not in words[i - 1]:
                    route.method, route.basis = words[i - 1].upper(), word.upper()
                    break
    if route.basis:
        route.basis = _basis_spelling(route.basis)
    counterpoise = next((o for k, o in keywords.items() if k.startswith("COUNTERPOISE")), None)
    if counterpoise and counterpoise[0].isdigit():
        route.counterpoise = int(counterpoise[0])  # D161
    route.frequency_scale = _scale_in_route(keywords)  # D163

    if "OPT" in keywords:
        opt = keywords["OPT"]
        is_ts = any(o in ("TS", "QST2", "QST3") for o in opt)
        route.job_type = "ts_optimization" if is_ts else "optimization"
        route.compound_freq = "FREQ" in keywords
    elif "FREQ" in keywords:
        route.job_type = "frequency"
    elif OTHER_JOB_KEYWORDS & keywords.keys():
        route.job_type = "other"
        if "IRC" in keywords:
            route.irc = keywords["IRC"]
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


def dispersion_iops(iops: dict[str, str], dispersion: str | None) -> dict[str, str]:
    """The IOps that change the dispersion (D59). Without a dispersion model, from
    EmpiricalDispersion or IOp(3/124), Gaussian adds none, and parameters set by IOp(3/174–179)
    change nothing: they are left out (D199)."""
    if not dispersion and "3/124" not in iops:
        return {}
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
# D163: the scale factor Gaussian says its thermochemistry uses (set by Scale=, ReadIsotopes
# or an IOp); the frequencies it prints stay unscaled.
_SCALED_BY_RE = re.compile(r"[Ff]requencies scaled by\s+(\d*\.?\d+)")
_CHARGE_RE = re.compile(r"^ Charge =\s*(-?\d+) Multiplicity =\s*(\d+)")

# D153: the total energy of a correlated method, which Gaussian prints after the SCF energy of
# its reference. A double hybrid prints `E2(B2PLYPD3) = … E(B2PLYPD3) = …`, MP2 `E2 = … EUMP2
# = …` (for a restricted reference too), MP3 `EUMP3=`, MP4 `UMP4(DQ)=`, `UMP4(SDQ)=` and
# `UMP4(SDTQ)=`, CCSD(T) and QCISD(T) `CCSD(T)=` and `QCISD(T)=`. The key names which total a
# method's energy is: a CCSD job prints MP2, MP3 and MP4(SDQ) totals on the way.
_NUMBER = r"(-?\d+\.\d+(?:[DE][+-]?\d+)?)"
_CORRELATED_RE = [
    (re.compile(r"^ E2\((\S+)\)\s*=\s*\S+\s+E\(\1\)\s*=\s*" + _NUMBER), "double hybrid"),
    (re.compile(r"^ E2\s*=\s*\S+\s+EUMP2\s*=\s*" + _NUMBER), "MP2"),
    (re.compile(r"^ E3\s*=\s*\S+\s+EUMP3\s*=\s*" + _NUMBER), "MP3"),
    (re.compile(r"^ E4\((DQ|SDQ|SDTQ)\)\s*=\s*\S+\s+UMP4\(\1\)\s*=\s*" + _NUMBER), "MP4"),
    (re.compile(r"^ (CCSD|QCISD)\(T\)\s*=\s*" + _NUMBER), "(T)"),
]
_DOUBLE_HYBRID = re.compile(r"2PLYP|^DSD|^PBE0DH$|^PBEQIDH$")
# A written R, U or RO before one of these is a reference prefix (UMP2, RB2PLYPD3).
_CORRELATED_NAME = re.compile(r"MP\d|CC|QCI|CID|CISD|BD|B2|MPW2|DSD|PBE0DH|PBEQ")
# D161: a counterpoise job computes the complex, then each fragment in the complex's basis set
# and in its own (`Counterpoise: doing DCBS calculation for fragment   1`), and prints the
# corrected total and the BSSE: Gaussian 16 as `Counterpoise corrected energy =` and a
# right-aligned `BSSE energy =`, earlier versions as `Counterpoise: corrected energy =` and
# `Counterpoise: BSSE energy =`. An ONIOM job prints each layer's energy, then
# `ONIOM: extrapolated energy =`. Both print their total after each structure of an
# optimization.
_COUNTERPOISE_FRAGMENT = " Counterpoise: doing "
_COUNTERPOISE_RE = re.compile(r"^\s*Counterpoise:?\s+corrected energy\s*=\s*" + _NUMBER)
_BSSE_RE = re.compile(r"^\s*(?:Counterpoise:\s+)?BSSE energy\s*=\s*" + _NUMBER)
_ONIOM_RE = re.compile(r"^ ONIOM: extrapolated energy\s*=\s*" + _NUMBER)
_ORIENTATIONS = ("Input orientation:", "Z-Matrix orientation:", "Standard orientation:")
# D123b: Berny's convergence table after each optimization step, and its predicted change.
_CRITERION_RE = re.compile(r"^ (Maximum|RMS)\s+(Force|Displacement)\s+(\S+)\s+(\S+)\s+(YES|NO)\s*$")
_PREDICTED_RE = re.compile(r"^ Predicted change in Energy=\s*(\S+)")
# D162: the spin expectation value printed after each unrestricted SCF: the summary line
# ("<Sx>= 0.0000 <Sy>= 0.0000 <Sz>= 0.5000 <S**2>= 0.7590 S= 0.5048") and, after it, the
# values before and after annihilating the first spin contaminant.
_S2_RE = re.compile(r"<S\*\*2>=\s*(-?\d+\.\d+)")
_ANNIHILATION_RE = re.compile(
    r"^ S\*\*2 before annihilation\s+(-?\d+\.\d+),\s+after\s+(-?\d+\.\d+)"
)
_UNITS = {"Force": "Eh/bohr", "Displacement": "bohr"}
# D135: the coordinate a scan drives, from the initial parameter table Gaussian prints
# (`! R5    R(2,3)    2.5262    Scan    !`), else from a ModRedundant line in the echoed input
# (`B 2 3 S 10 0.0250`, the letter optional). A Z-matrix variable scanned (`D7 -180. Scan`)
# names no atoms; the scan editor then finds the coordinate from the structures.
_SCAN_PARAMETER_RE = re.compile(r"^ ! \S+\s+[RAD]\(([\d,]+)\)\s+\S+\s+Scan\s+!")
_SCAN_LINE_RE = re.compile(r"^ (?:[BAD]\s+)?((?:\d+\s+){2,4})S\s+\d+\s+-?[\d.]+\s*$", re.IGNORECASE)
_SCAN_POINT_RE = re.compile(r"^ Step number\s+\d+ out of a maximum of\s+\d+ on scan point\s+(\d+)")
# D157: what holds an optimization in place: a coordinate the parameter table marks Frozen,
# ModRedundant lines with the action S (scan) or F (freeze), and frozen atoms (NQMF, NMMIF).
_FROZEN_PARAMETER_RE = re.compile(r"^ ! \S+\s+([A-Z]\([\d,]+\))\s+\S+\s+Frozen\s+!", re.IGNORECASE)
_MODREDUNDANT = " The following ModRedundant input section has been read:"
_FROZEN_ATOMS_RE = re.compile(r"NQMF=\s*(\d+)\s+NMMI=\s*\d+\s+NMMIF=\s*(\d+)")
_HELD_ACTIONS = {"S": "scan", "F": "frozen"}


def _held_in_modredundant(lines: list[str], start: int) -> list[str]:
    """D157: the scanned and frozen coordinates of an echoed ModRedundant section (`D 3 4 9
    10 S 12 30.0`, `B 1 2 F`, `X 5 F`, `1 2 F`), up to its blank line."""
    held: list[str] = []
    for line in lines[start : start + 500]:
        words = line.split()
        if not words:
            break
        letter = words.pop(0).upper() if words[0].isalpha() else None
        atoms = []
        while words and (words[0].isdigit() or words[0] == "*"):
            atoms.append(words.pop(0))
        action = next((w.upper() for w in words if w[:1].isalpha()), None)
        if atoms and action in _HELD_ACTIONS:
            held.append(f"{_HELD_ACTIONS[action]} {coordinate_name(atoms, letter)}")
    return held


# D131: an IRC's points, directions, coordinates and endings.
_IRC_POINT_RE = re.compile(r"^ Point Number:\s+(\d+)\s+Path Number:\s+(\d+)")
_IRC_DIRECTION_RE = re.compile(r"^ Point Number\s+\d+ in (FORWARD|REVERSE) path direction")
_IRC_COORDINATE_RE = re.compile(r"^\s+NET REACTION COORDINATE UP TO THIS POINT =\s+(\S+)")
_IRC_CORRECTED_RE = re.compile(r"^\s+Corrected End Point Energy =\s+(\S+)")
_IRC_TS_ENERGY_RE = re.compile(
    r"^(?: Energies reported relative to the TS energy of|\s+Energy From Chk =)\s+(\S+)"
)
_IRC_MAX_POINTS_RE = re.compile(r"^ Maximum points per path\s+=\s+(\d+)")
_IRC_PATH_DONE_RE = re.compile(r"^ Calculation of (FORWARD|REVERSE) path complete\.")


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
            try:
                number = int(parts[1])
                if number > 0:  # skip dummy and ghost atoms
                    atoms.append(Atom(ELEMENTS[number - 1], *(float(v) for v in parts[-3:])))
            except (ValueError, IndexError):  # a line cut off in a file still being written
                return [], i
        i += 1
    if i >= len(lines):
        return [], i  # the file ends inside the table (a job still running, D123c)
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


def _read_current_structure(lines: list[str], start: int) -> list[Atom]:
    """The "CURRENT STRUCTURE" table an IRC prints for each point it has corrected (D131), its
    title at lines[start]: center, atomic number and x, y, z in Å."""
    i, dashes = start + 1, 0
    while i < len(lines) and i < start + 8 and dashes < 2:
        dashes += _is_dashes(lines[i])
        i += 1
    atoms = []
    while i < len(lines) and not _is_dashes(lines[i]):
        parts = lines[i].split()
        try:
            number = int(parts[1])
            if number > 0:
                atoms.append(Atom(ELEMENTS[number - 1], *(float(v) for v in parts[-3:])))
        except (ValueError, IndexError):
            return []
        i += 1
    return atoms if i < len(lines) else []


def _read_checkpoint_structure(lines: list[str], start: int) -> list[Atom]:
    """The structure a Geom=Check job read from its checkpoint (`Structure from the checkpoint
    file` at lines[start]), as element and x, y, z in Å; elements may carry a nuclear charge
    or other note, as in `Cl(GFac=0.82)`."""
    atoms: list[Atom] = []
    for line in lines[start + 1 : start + 4000]:
        parts = line.split()
        if len(parts) == 4:
            symbol = parts[0].split("(")[0]
            element = symbol[:1].upper() + symbol[1:].lower()
            try:
                coordinates = [float(v) for v in parts[1:]]
            except ValueError:
                coordinates = []
            if element in ELEMENTS and coordinates:
                atoms.append(Atom(element, *coordinates))
                continue
        if atoms:
            break
    return atoms


class _IrcReader:
    """D131: reads an IRC job step's points as `_parse_step` meets their lines. A point's
    structure is the "CURRENT STRUCTURE" printed after it (the corrected point of Gaussian's
    default integrator), else the last structure read (LQA and other predictor-only steps). The
    TS (point 0) of a job reading its geometry from a checkpoint is printed only as the structure
    read from it, which `finish` puts first."""

    def __init__(self, route: Route):
        options = route.irc or []
        requested = "both"
        if "FORWARD" in options:
            requested = "forward"
        elif "REVERSE" in options:
            requested = "reverse"
        self.run = IrcRun(requested=requested)
        self.direction: str | None = None
        self.path = 1
        self.corrected: float | None = None
        self.checkpoint: list[Atom] = []
        # (path, number) -> [structure index per orientation (None: the checkpoint's), coordinate]
        self.points: dict[tuple[int, int], list[Any]] = {}
        self.last: tuple[int, int] | None = None

    def read(
        self,
        lines: list[str],
        i: int,
        orientations: dict[str, list[list[Atom]]],
        energies: dict[str, list[float | None]],
        points: dict[str, list[int | None]],
        criteria: dict[str, list[list[Criterion]]],
    ) -> bool:
        """Reads lines[i] if it is one of the IRC's (a structure it adds lengthens every
        per-structure list). Returns whether the line was the IRC's."""
        line = lines[i]
        if (match := _IRC_POINT_RE.match(line)) is not None:
            number, path = int(match.group(1)), int(match.group(2))
            self.path = path
            if number > 0 and self.direction:
                self.run.directions[path] = self.direction
            atoms: list[Atom] = []
            if i + 1 < len(lines) and lines[i + 1].strip() == "CURRENT STRUCTURE":
                atoms = _read_current_structure(lines, i + 1)
            if atoms:
                for name in [n for n in _ORIENTATIONS if orientations[n]] or [_ORIENTATIONS[0]]:
                    orientations[name].append(atoms)
                    energies[name].append(self.corrected)
                    points[name].append(None)
                    criteria[name].append([])
            last = {n: len(orientations[n]) - 1 for n in _ORIENTATIONS if orientations[n]}
            structure: dict[str, int] | None = last or None  # None: the checkpoint's TS
            self.corrected = None
            self.points[(path, number)] = [structure, None]
            self.last = (path, number)
        elif (match := _IRC_DIRECTION_RE.match(line)) is not None:
            self.direction = match.group(1).lower()
        elif (match := _IRC_COORDINATE_RE.match(line)) is not None:
            if self.last is not None:
                with contextlib.suppress(ValueError):
                    self.points[self.last][1] = float(match.group(1))
        elif (match := _IRC_CORRECTED_RE.match(line)) is not None:
            with contextlib.suppress(ValueError):
                self.corrected = float(match.group(1))
        elif (match := _IRC_TS_ENERGY_RE.match(line)) is not None:
            with contextlib.suppress(ValueError):
                self.run.ts_energy = float(match.group(1))
        elif (match := _IRC_MAX_POINTS_RE.match(line)) is not None:
            self.run.max_points = int(match.group(1))
        elif line.startswith(" Maximum number of steps reached."):
            self.run.endings.setdefault(self.path, "max_points")
        elif line.startswith(" PES minimum detected"):
            self.run.endings.setdefault(self.path, "minimum")
        elif (match := _IRC_PATH_DONE_RE.match(line)) is not None:
            self.run.endings.setdefault(self.path, "complete")
            if self.run.requested == "both" and self.path == 1:
                self.direction = None  # the next path's first point names its own
        elif line.startswith(" Structure from the checkpoint file"):
            self.checkpoint = _read_checkpoint_structure(lines, i)
        else:
            return False
        return True

    def finish(self, step: ParsedStep, name: str | None) -> None:
        """Points the chosen orientation's structures (`name`) and marks them converged."""
        run = self.run
        if run.requested != "both":
            run.directions.setdefault(1, run.requested)
        elif not run.directions:
            run.directions[1] = "forward"
        uses_checkpoint = any(s is None for s, _ in self.points.values())
        if uses_checkpoint and self.checkpoint:
            # The TS goes first; every structure after it moves up one.
            step.geometries.insert(0, self.checkpoint)
            step.geometry_energies.insert(0, run.ts_energy)
            step.geometry_points.insert(0, None)
            step.converged_geometries = [n + 1 for n in step.converged_geometries]
            if step.geometry_criteria:
                step.geometry_criteria.insert(0, [])
        shift = 1 if uses_checkpoint and self.checkpoint else 0
        for (path, number), (structure, coordinate) in sorted(self.points.items()):
            if structure is None:
                if not shift:
                    continue
                index = 0
            elif name is None or name not in structure:
                continue
            else:
                index = structure[name] + shift
            energy = step.geometry_energies[index] if index < len(step.geometry_energies) else None
            if number == 0 and run.ts_energy is None:
                run.ts_energy = energy
            run.points.append(IrcPoint(path, number, index, energy, coordinate))
        for path in {p.path for p in run.points if p.number > 0} | set(run.directions):
            run.endings.setdefault(path, "stopped")
        step.converged_geometries = sorted({p.structure for p in run.points})
        step.irc = run


def correlated_total_of(method: str | None) -> str | None:
    """D153: the printed total that is a correlated method's energy (`E(B2PLYPD3)`, `EUMP2`,
    `UMP4(SDTQ)`, `CCSD(T)`…), "" for a correlated method whose total is not read (CCSD,
    QCISD, CISD, BD…), None for SCF methods (HF and hybrid or plain DFT)."""
    if not method:
        return None
    upper = method.upper()
    for prefix in ("RO", "R", "U"):
        rest = upper[len(prefix) :]
        if upper.startswith(prefix) and _CORRELATED_NAME.match(rest):
            upper = rest
            break
    base, _, options = upper.partition("(")
    options = {o.strip() for o in options.rstrip(")").split(",") if o.strip()}
    if _DOUBLE_HYBRID.search(base):
        return f"E({base})"
    if base == "MP2":
        return "EUMP2"
    if base == "MP3":
        return "EUMP3"
    if base == "MP4":
        kind = "SDQ" if "SDQ" in options else "DQ" if "DQ" in options else "SDTQ"
        return f"UMP4({kind})"
    if (base in ("CCSD", "QCISD") and "T" in options) or base in ("CCSD-T", "QCISD-T"):
        return f"{base.removesuffix('-T')}(T)"
    if re.match(r"^(MP\d|CC|QCI|CID|CISD|BD)", base):  # CIS's ground state is HF
        return ""
    return None


def _correlated_total(line: str, wanted: str) -> tuple[str, float] | None:
    """(the printed name, the total) on a line Gaussian prints for a correlated method, when it
    is the total `wanted`."""
    if not wanted:
        return None
    for pattern, kind in _CORRELATED_RE:
        if (match := pattern.match(line)) is None:
            continue
        if kind == "double hybrid":
            # The printed name may differ from the route's spelling (B2PLYPD3 for B2PLYP with
            # EmpiricalDispersion=GD3): any E2(…)/E(…) pair is a double hybrid's total.
            found = f"E({match.group(1).upper()})"
            matches = wanted.startswith("E(")
        else:
            found = {"MP2": "EUMP2", "MP3": "EUMP3"}.get(kind) or (
                f"UMP4({match.group(1)})" if kind == "MP4" else f"{match.group(1)}(T)"
            )
            matches = wanted == found
        return (found, float(match.group(match.lastindex).replace("D", "E"))) if matches else None
    return None


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
    criteria: dict[str, list[list[Criterion]]] = {name: [] for name in _ORIENTATIONS}
    basis: dict[int, str] = {}
    ecp: dict[int, str] = {}
    frequencies: list[float] = []
    irc = _IrcReader(step.route) if step.route and step.route.irc is not None else None
    scanned_table: list[list[int]] = []
    scanned_input: list[list[int]] = []
    # D157: scanned and frozen coordinates from the parameter table, else the ModRedundant
    # input, and the number of frozen atoms (-1 in the molecule specification).
    held_table: list[str] = []
    held_input: list[str] = []
    frozen_atoms = 0
    wanted = correlated_total_of(step.route.method) if step.route else None
    correlated: float | None = None
    # D161: a counterpoise job's corrected total and whether a fragment's own calculation is
    # being read (its structure and energies are not the complex's); an ONIOM job's total.
    counterpoise = step.route.counterpoise if step.route else None
    in_fragment = False
    corrected: float | None = None
    oniom = step.route is not None and is_oniom(step.route.method)
    extrapolated: float | None = None
    # D158: the last convergence table printed (each line, whether it was met) and whether
    # Gaussian then said "Stationary point found": after a frequency job it judges the
    # structure with the Hessian just computed.
    table: list[tuple[str, bool]] = []
    stationary = False
    i = 0
    while i < len(lines):
        line = lines[i]
        if irc is not None and irc.read(lines, i, orientations, energies, points, criteria):
            pass
        elif line.startswith(_COUNTERPOISE_FRAGMENT):
            in_fragment = True
            counterpoise = counterpoise or 2
        elif in_fragment and line.strip() in _ORIENTATIONS:
            i = _read_orientation(lines, i)[1]  # a fragment's structure, ghost atoms left out
            continue
        elif in_fragment and (
            line.startswith((" SCF Done:", " <Sx>=", " S**2 before annihilation"))
            or (wanted and _correlated_total(line, wanted))
        ):
            pass  # a fragment's energy and spin (D161)
        elif (match := _COUNTERPOISE_RE.match(line)) is not None:
            in_fragment = False
            corrected, step.printed["energy"] = (
                float(match.group(1).replace("D", "E")),
                line.strip(),
            )
            for name in _ORIENTATIONS:
                if energies[name]:
                    energies[name][-1] = corrected
        elif (match := _BSSE_RE.match(line)) is not None:
            step.printed[BSSE] = match.group(1).replace("D", "E")
        elif oniom and (match := _ONIOM_RE.match(line)) is not None:
            extrapolated, step.printed["energy"] = (
                float(match.group(1).replace("D", "E")),
                line.strip(),
            )
            for name in _ORIENTATIONS:
                if energies[name]:
                    energies[name][-1] = extrapolated
        elif step.charge is None and (match := _CHARGE_RE.match(line)):
            step.charge, step.multiplicity = int(match.group(1)), int(match.group(2))
        elif line.strip() in _ORIENTATIONS:
            atoms, i = _read_orientation(lines, i)
            if atoms:
                orientations[line.strip()].append(atoms)
                energies[line.strip()].append(None)
                points[line.strip()].append(None)
                criteria[line.strip()].append([])
            continue
        elif line.startswith(" SCF Done:") and len(line.split()) > 4:
            step.scf_energy = float(line.split()[4])
            # D161: a new structure's (or layer's) SCF: its own total is still to come.
            corrected = extrapolated = None
            for name in _ORIENTATIONS:
                if energies[name]:
                    energies[name][-1] = step.scf_energy
        elif wanted and (total := _correlated_total(line, wanted)) is not None:
            (step.energy_label, correlated), step.printed["energy"] = total, line.strip()
            for name in _ORIENTATIONS:
                if energies[name]:
                    energies[name][-1] = correlated
        elif (match := _ANNIHILATION_RE.match(line)) is not None:
            # D162: the last one of the step counts, as the step's energy does.
            step.printed[S2], step.printed[S2_AFTER] = match.group(1), match.group(2)
        elif line.startswith(" <Sx>=") and (match := _S2_RE.search(line)) is not None:
            step.printed[S2] = match.group(1)
            step.printed.pop(S2_AFTER, None)  # its own annihilation line follows, if any
        elif "Optimization completed." in line or "-- Stationary point found." in line:
            step.optimization_converged = True
            stationary = True
            for name in _ORIENTATIONS:
                last = len(orientations[name]) - 1
                if last >= 0 and last not in converged[name]:
                    converged[name].append(last)
        elif line.split() == ["Item", "Value", "Threshold", "Converged?"]:
            table, stationary = [], False
        elif (match := _CRITERION_RE.match(line)) is not None:
            # The table belongs to the structure the forces were computed on: the last one.
            size, kind = match.group(1), match.group(2)
            table.append((" ".join(line.split()), match.group(5) == "YES"))
            try:
                value, threshold = (float(match.group(k).replace("D", "E")) for k in (3, 4))
            except ValueError:  # ******** for a value too large to print
                pass
            else:
                found = Criterion(
                    f"{size} {kind.lower()}",
                    value,
                    threshold,
                    match.group(5) == "YES",
                    _UNITS[kind],
                )
                for name in _ORIENTATIONS:
                    if criteria[name]:
                        criteria[name][-1].append(found)
        elif (match := _PREDICTED_RE.match(line)) is not None:
            try:
                value = float(match.group(1).replace("D", "E"))
            except ValueError:
                pass
            else:
                found = Criterion("Predicted energy change", value, None, None, "Eh")
                for name in _ORIENTATIONS:
                    if criteria[name]:
                        criteria[name][-1].append(found)
        elif (match := _SCAN_PARAMETER_RE.match(line)) is not None:
            atoms = [int(a) for a in match.group(1).split(",")]
            if atoms not in scanned_table:
                scanned_table.append(atoms)
            held_table.append(f"scan {line.split()[2]}")
        elif (match := _FROZEN_PARAMETER_RE.match(line)) is not None:
            held_table.append(f"frozen {match.group(1)}")
        elif line.startswith(_MODREDUNDANT):
            held_input += _held_in_modredundant(lines, i + 1)
        elif (match := _FROZEN_ATOMS_RE.search(line)) is not None:
            frozen_atoms = max(frozen_atoms, int(match.group(1)) + int(match.group(2)))
        elif (match := _SCAN_LINE_RE.match(line)) is not None:
            atoms = [int(a) for a in match.group(1).split()]
            if atoms not in scanned_input:
                scanned_input.append(atoms)
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
        elif (match := _SCALED_BY_RE.search(line)) is not None:
            step.printed[FREQUENCY_SCALE] = match.group(1)  # D163
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
    if step.job_type == "frequency" and table and not stationary:
        # D158: W-FREQ-NONSTAT, kept with the printed lines as the criteria not met.
        unmet = [text for text, met in table if not met]
        step.printed[NOT_STATIONARY] = "; ".join(unmet) or "no stationary point found"
    if wanted is not None:
        # D153: the step's energy is the method's own total; never its SCF reference's.
        step.scf_energy = correlated
        step.correlated_missing = correlated is None
    if counterpoise:
        # D161: the counterpoise-corrected total, never a fragment's energy; the BSSE is kept.
        step.scf_energy, step.correlated_missing = corrected, False
        step.energy_label = "E(counterpoise)"
        if corrected is None:
            step.printed.pop(BSSE, None)
            step.printed.pop("energy", None)
            step.energy_missing = (
                f"counterpoise-corrected energy (Counterpoise={counterpoise}: not printed, the "
                "job may have been cut off; the complex and fragment energies are not used)"
            )
    elif oniom:
        # D161: the ONIOM extrapolated total, never one layer's energy.
        step.scf_energy, step.correlated_missing = extrapolated, False
        step.energy_label = "E(ONIOM)"
        if extrapolated is None:
            step.printed.pop("energy", None)
            step.energy_missing = (
                "ONIOM extrapolated energy (not printed, the job may have been cut off; the "
                "layer energies are not used)"
            )
    chosen = None
    for name in _ORIENTATIONS:
        if orientations[name]:
            chosen = name
            step.geometries = orientations[name]
            step.geometry_energies = energies[name]
            step.geometry_points = points[name]
            step.converged_geometries = converged[name]
            if any(criteria[name]):
                step.geometry_criteria = criteria[name]
            break
    if irc is not None:
        irc.finish(step, chosen)
    if step.scan == "rigid":  # every structure of a rigid scan is one of its points
        step.geometry_points = list(range(1, len(step.geometries) + 1))
        step.converged_geometries = list(range(len(step.geometries)))
    if step.scan:
        step.scan_coordinates = scanned_table or scanned_input
    for held in held_table or held_input:
        add_constraint(step, held)
    if frozen_atoms:
        add_constraint(step, f"{frozen_atoms} frozen atom{'s' if frozen_atoms > 1 else ''}")
    if step.scan and not any(c.startswith("scan") for c in step.constraints):
        add_constraint(step, f"{step.scan} scan")
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
            with contextlib.suppress(ValueError):
                step.reduced_masses += [float(v) for v in lines[i].split()[3:]]
        if lines[i].startswith(" Frequencies") or not lines[i].strip():
            return i
        i += 1
    i += 1
    while i < len(lines):
        parts = lines[i].split()
        if len(parts) != 2 + 3 * count or not parts[0].isdigit():
            break
        try:
            values = [float(v) for v in parts[2:]]
        except ValueError:  # cut off in a file still being written (D123c)
            break
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
    _thermo_models(steps)
    for step in steps:
        step.missing = _missing_fields(step)
    return ParsedFile(program=PROGRAM, version=version, steps=steps)


def _thermo_models(steps: list[ParsedStep]) -> None:
    """D163: Gaussian's printed G is pure RRHO; the scale factor is the one the output says the
    thermochemistry used, else the route's (a compound job's frequency step takes the first
    step's), else 1."""
    for n, step in enumerate(steps):
        scale = step.printed.pop(FREQUENCY_SCALE, None)
        scale = scale or (step.route.frequency_scale if step.route else None)
        before = steps[n - 1].route if n > 0 else None
        if scale is None and before is not None and before.compound_freq:
            scale = before.frequency_scale
        set_thermo_model(step, "RRHO", scale)


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
        if is_oniom(step.route.method):
            pass  # D161: each layer names its own basis set in the method
        elif not step.resolved_basis:
            missing.append("basis set")
        elif step.resolved_basis in ("GEN", "GENECP") and not step.resolved_basis_definition:
            missing.append("custom basis definition")
    if step.charge is None:
        missing.append("charge and multiplicity")
    if not step.geometries:
        missing.append("geometry")
    if step.energy_missing:
        missing.append(step.energy_missing)  # D161
    elif step.correlated_missing:
        missing.append(f"correlated energy ({step.route.method})")
    elif step.scf_energy is None and step.job_type != "other":
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
