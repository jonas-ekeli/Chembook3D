"""Saved custom basis sets, read back for inspection and download (D94).

A `CustomBasis.definition` holds, per element, the text Gaussian printed under `General basis
read from cards` and, for an ECP, under `Pseudopotential Parameters` (parsers/gaussian.py,
FR-CALC-03). This module reads that text back into shells and ECP terms, summarises it as a
contraction scheme, and writes it as a Gaussian basis file (`.gbs`, the Gen/GenECP input
format). Numbers are written exactly as Gaussian printed them, so nothing is rounded twice.
"""

import re
from dataclasses import dataclass, field

from chembook3d import xyz

_VARIANT_SEPARATOR = "\n----\n"
_ECP_MARK = "\nECP\n"
_SHELL = re.compile(r"^([A-Z]+) (\d+) (\S+)$")
_ANGULAR = "SPDFGHIK"


@dataclass
class Shell:
    kind: str  # S, P, D, ... or SP
    scale: str
    # [exponent, coefficient, ...] as printed (two coefficients for an SP shell)
    primitives: list[list[str]] = field(default_factory=list)


@dataclass
class EcpBlock:
    title: str  # as Gaussian printed it, e.g. "G and up", "S - G"
    terms: list[list[str]] = field(default_factory=list)  # [power, exponent, coefficient]
    spin_orbit: bool = False  # a non-zero spin-orbit coefficient was printed (not written)


@dataclass
class Ecp:
    valence: int | None
    blocks: list[EcpBlock] = field(default_factory=list)


@dataclass
class Variant:
    """One element's definition. An element has several when its atoms in the first file
    carried different basis sets or ECPs."""

    header: str  # e.g. "(5D, 7F)"
    shells: list[Shell] = field(default_factory=list)
    ecp: Ecp | None = None


def read_element(text: str) -> list[Variant]:
    return [_read_variant(part) for part in text.split(_VARIANT_SEPARATOR)]


def _read_variant(text: str) -> Variant:
    basis_text, _, ecp_text = text.partition(_ECP_MARK)
    lines = basis_text.split("\n")
    header = lines.pop(0).strip() if lines and not _SHELL.match(lines[0]) else ""
    variant = Variant(header=header)
    for line in lines:
        if match := _SHELL.match(line):
            variant.shells.append(Shell(kind=match.group(1), scale=match.group(3)))
        elif line.startswith("Exponent=") and variant.shells:
            exponent, _, coefficients = line[len("Exponent=") :].partition("Coefficients=")
            variant.shells[-1].primitives.append([exponent.strip(), *coefficients.split()])
    if ecp_text:
        variant.ecp = _read_ecp(ecp_text)
    return variant


def _read_ecp(text: str) -> Ecp:
    lines = text.split("\n")
    first = lines.pop(0).strip() if lines else ""
    ecp = Ecp(valence=int(first) if first.isdigit() else None)
    for line in lines:
        words = line.split()
        if not words:
            continue
        if _is_number(words[0]) and ecp.blocks:
            block = ecp.blocks[-1]
            block.terms.append(words[:3])
            if len(words) > 3 and float(words[3].replace("D", "E")) != 0.0:
                block.spin_orbit = True
        else:
            ecp.blocks.append(EcpBlock(title=line.strip()))
    return ecp


def _is_number(word: str) -> bool:
    try:
        float(word.replace("D", "E"))
    except ValueError:
        return False
    return True


def _shell_letters(kind: str) -> list[str]:
    return [letter for letter in kind if letter in _ANGULAR]


def contraction(variant: Variant) -> str:
    """The usual scheme, primitives/contracted, e.g. "(7s4p1d)/[3s2p1d]"."""
    primitives: dict[str, int] = {}
    contracted: dict[str, int] = {}
    for shell in variant.shells:
        for letter in _shell_letters(shell.kind):
            primitives[letter] = primitives.get(letter, 0) + len(shell.primitives)
            contracted[letter] = contracted.get(letter, 0) + 1
    order = sorted(primitives, key=_ANGULAR.index)
    if not order:
        return ""
    left = "".join(f"{primitives[k]}{k.lower()}" for k in order)
    right = "".join(f"{contracted[k]}{k.lower()}" for k in order)
    return f"({left})/[{right}]"


def max_angular(ecp: Ecp) -> int:
    """L of the first block ("G and up" is 4); Gaussian prints that block and one per lower
    L after it."""
    title = ecp.blocks[0].title if ecp.blocks else ""
    if (match := re.match(r"^([A-Z]) and up", title)) and match.group(1) in _ANGULAR:
        return _ANGULAR.index(match.group(1))
    return max(len(ecp.blocks) - 1, 0)


def core_electrons(element: str, ecp: Ecp) -> int | None:
    if ecp.valence is None or element not in xyz.ELEMENTS:
        return None
    return xyz.ELEMENTS.index(element) + 1 - ecp.valence


def describe(definition: dict[str, str]) -> list[dict]:
    """Per element: the scheme, the ECP and the shells and terms, for the basis set view."""
    return [
        {
            "element": element,
            "variants": [_describe(element, v) for v in read_element(definition[element])],
        }
        for element in sort_elements(definition)
    ]


def _describe(element: str, variant: Variant) -> dict:
    ecp = variant.ecp
    text = _basis_lines(element, variant)
    if ecp is not None:
        text += ["", *_ecp_lines(element, variant)]
    return {
        "header": variant.header,
        "contraction": contraction(variant),
        "shells": [
            {"kind": s.kind, "scale": s.scale, "primitives": s.primitives} for s in variant.shells
        ],
        "ecp": None
        if ecp is None
        else {
            "core_electrons": core_electrons(element, ecp),
            "max_angular": max_angular(ecp),
            "blocks": [{"title": b.title, "terms": b.terms} for b in ecp.blocks],
        },
        "text": text,
    }


def sort_elements(elements) -> list[str]:
    """Periodic-table order, unknown symbols last."""
    rank = {symbol: n for n, symbol in enumerate(xyz.ELEMENTS)}
    return sorted(elements, key=lambda el: (rank.get(el, len(rank)), el))


def _basis_lines(element: str, variant: Variant) -> list[str]:
    lines = [f"{element}     0"]
    for shell in variant.shells:
        lines.append(f"{shell.kind}   {len(shell.primitives)}   {shell.scale}")
        lines += ["      " + "  ".join(f"{v:>17}" for v in p) for p in shell.primitives]
    return [*lines, "****"]


def _ecp_lines(element: str, variant: Variant) -> list[str]:
    ecp = variant.ecp
    if ecp is None:
        return []
    core = core_electrons(element, ecp)
    lines = [f"{element}     0", f"{element}-ECP     {max_angular(ecp)}     {core}"]
    for block in ecp.blocks:
        lines += [block.title, f"  {len(block.terms)}"]
        lines += [f"{t[0]}  {t[1]:>14}  {t[2]:>16}" for t in block.terms]
    return lines


def gaussian_file(name: str, definition: dict[str, str], elements: list[str] | None = None) -> str:
    """The basis set as Gaussian Gen/GenECP input: the basis blocks, each ending in ****,
    then (when there are ECPs) a blank line and the ECP blocks. `!` lines are comments."""
    chosen = sort_elements(el for el in definition if elements is None or el in elements)
    basis: list[str] = []
    ecps: list[str] = []
    notes: list[str] = []
    headers: set[str] = set()
    for element in chosen:
        variants = read_element(definition[element])
        variant = variants[0]
        if len(variants) > 1:
            notes.append(
                f"! {element}: the first file gave its atoms {len(variants)} different "
                "definitions; only the first is written here."
            )
        if variant.ecp and any(b.spin_orbit for b in variant.ecp.blocks):
            notes.append(f"! {element}: spin-orbit ECP coefficients are not written.")
        if variant.ecp and len(variant.ecp.blocks) != max_angular(variant.ecp) + 1:
            count = len(variant.ecp.blocks)
            notes.append(
                f"! {element}: Gaussian printed {count} ECP block{'s' if count != 1 else ''}"
                f" for L up to {max_angular(variant.ecp)}; check the ECP before using it."
            )
        if variant.header:
            headers.add(variant.header)
        basis += _basis_lines(element, variant)
        ecps += _ecp_lines(element, variant)
    lines = [f"! Custom basis set {name}, saved by Chembook3D"]
    lines.append(f"! Elements: {' '.join(chosen)}")
    if headers:
        lines.append(f"! Gaussian read it as {', '.join(sorted(headers))}")
    lines.append(f"! Use with {'GenECP' if ecps else 'Gen'}")
    lines += notes
    lines += basis
    if ecps:
        lines += ["", *ecps]
    return "\n".join(lines) + "\n"
