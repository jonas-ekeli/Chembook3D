"""Parsing and formatting of xyz coordinate text (FR-NODE-03) and formula derivation (P12)."""

from collections import Counter
from dataclasses import dataclass

# fmt: off
ELEMENTS = (
    "H", "He", "Li", "Be", "B", "C", "N", "O", "F", "Ne", "Na", "Mg", "Al", "Si", "P", "S", "Cl",
    "Ar", "K", "Ca", "Sc", "Ti", "V", "Cr", "Mn", "Fe", "Co", "Ni", "Cu", "Zn", "Ga", "Ge", "As",
    "Se", "Br", "Kr", "Rb", "Sr", "Y", "Zr", "Nb", "Mo", "Tc", "Ru", "Rh", "Pd", "Ag", "Cd", "In",
    "Sn", "Sb", "Te", "I", "Xe", "Cs", "Ba", "La", "Ce", "Pr", "Nd", "Pm", "Sm", "Eu", "Gd", "Tb",
    "Dy", "Ho", "Er", "Tm", "Yb", "Lu", "Hf", "Ta", "W", "Re", "Os", "Ir", "Pt", "Au", "Hg", "Tl",
    "Pb", "Bi", "Po", "At", "Rn", "Fr", "Ra", "Ac", "Th", "Pa", "U", "Np", "Pu", "Am", "Cm", "Bk",
    "Cf", "Es", "Fm", "Md", "No", "Lr", "Rf", "Db", "Sg", "Bh", "Hs", "Mt", "Ds", "Rg", "Cn", "Nh",
    "Fl", "Mc", "Lv", "Ts", "Og",
)
# fmt: on
_BY_UPPER = {symbol.upper(): symbol for symbol in ELEMENTS}
_BY_NUMBER = {number: symbol for number, symbol in enumerate(ELEMENTS, start=1)}


@dataclass(frozen=True)
class Atom:
    element: str
    x: float
    y: float
    z: float


@dataclass(frozen=True)
class XyzError:
    line: int  # 1-based line number in the submitted text
    message: str


class XyzParseError(ValueError):
    def __init__(self, errors: list[XyzError]):
        self.errors = errors
        super().__init__("; ".join(f"line {e.line}: {e.message}" for e in errors))


def _element(token: str) -> str | None:
    """Accept element symbols in any case, or atomic numbers as Gaussian writes them."""
    if token.isdigit():
        return _BY_NUMBER.get(int(token))
    return _BY_UPPER.get(token.upper())


def element_of(token: str) -> str | None:
    """The element symbol for a symbol in any case or an atomic number; None if unknown."""
    return _element(token)


def parse_xyz(text: str) -> list[Atom]:
    """Parse xyz text: atom lines `El x y z` in Å, optionally preceded by the standard
    count line and comment line. Every invalid line is reported, and nothing is returned
    unless the whole text is valid."""
    lines = text.splitlines()
    numbered = [(number, line) for number, line in enumerate(lines, start=1) if line.strip()]
    errors: list[XyzError] = []

    declared_count: int | None = None
    body = numbered
    if body and body[0][1].strip().isdigit():
        declared_count = int(body[0][1])
        # The comment line is the line right after the count line; it may be blank or hold
        # anything, so it is skipped rather than parsed.
        comment_line_number = body[0][0] + 1
        body = [(n, line) for n, line in body[1:] if n != comment_line_number]

    atoms: list[Atom] = []
    for number, line in body:
        parts = line.split()
        if len(parts) != 4:
            errors.append(
                XyzError(number, f"expected 4 fields (element x y z), found {len(parts)}")
            )
            continue
        element = _element(parts[0])
        if element is None:
            errors.append(XyzError(number, f"unknown element '{parts[0]}'"))
            continue
        try:
            x, y, z = (float(value) for value in parts[1:])
        except ValueError:
            errors.append(XyzError(number, "coordinates must be numbers"))
            continue
        atoms.append(Atom(element, x, y, z))

    if not errors and declared_count is not None and declared_count != len(atoms):
        errors.append(
            XyzError(1, f"count line says {declared_count} atoms, but {len(atoms)} were given")
        )
    if not errors and not atoms:
        errors.append(XyzError(1, "no atoms found"))
    if errors:
        raise XyzParseError(errors)
    return atoms


def format_xyz(atoms: list[Atom], comment: str = "") -> str:
    """Standard xyz file text: count line, comment line, one atom per line."""
    rows = [f"{a.element:<2} {a.x:14.8f} {a.y:14.8f} {a.z:14.8f}" for a in atoms]
    return "\n".join([str(len(atoms)), comment.replace("\n", " "), *rows]) + "\n"


def formula(atoms: list[Atom]) -> str:
    """Hill-order formula: C first, then H, then the rest alphabetically; with no carbon,
    everything alphabetically."""
    return formula_of(Counter(atom.element for atom in atoms))


def formula_of(counts: Counter[str]) -> str:
    """Hill-order formula of element counts."""
    counts = +counts  # drop zero and negative counts
    if "C" in counts:
        order = ["C"] + (["H"] if "H" in counts else [])
        order += sorted(e for e in counts if e not in ("C", "H"))
    else:
        order = sorted(counts)
    return "".join(e if counts[e] == 1 else f"{e}{counts[e]}" for e in order)
