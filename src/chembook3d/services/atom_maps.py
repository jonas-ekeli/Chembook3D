"""Atom maps of merged calculations (D262c).

A calculation merged onto another node keeps that node's numbering in the database: its
geometry, start geometry, normal modes and mode check are written in the new order at the
merge. Its stored file is never rewritten, so `Calculation.atom_map` says, for each atom in the
node's numbering, which atom (0-based) of the file's step it is; everything read back from the
file goes through `mapped_step`.

A map `m` takes a list in the file's order to the node's order as `[x[m[i]] for i]`; its
inverse takes a file atom number to the node's.
"""

import dataclasses
import re
from typing import Any

from chembook3d.parsers.common import ParsedStep


def is_identity(mapping: list[int] | None) -> bool:
    return mapping is None or all(i == j for i, j in enumerate(mapping))


def inverse(mapping: list[int]) -> list[int]:
    found = [0] * len(mapping)
    for i, j in enumerate(mapping):
        found[j] = i
    return found


def compose(old: list[int] | None, new: list[int]) -> list[int] | None:
    """The map of a calculation mapped by `old` (None: none) when its node's numbering is
    mapped again by `new` (each atom of the new numbering, the old numbering's atom)."""
    found = list(new) if old is None else [old[j] for j in new]
    return None if is_identity(found) else found


def reordered(items: list[Any] | None, mapping: list[int] | None) -> list[Any] | None:
    """A per-atom list in the other order: item i is `items[mapping[i]]`."""
    if items is None or mapping is None or len(items) != len(mapping):
        return items
    return [items[j] for j in mapping]


def modes(found: list[Any], mapping: list[int] | None) -> list[Any]:
    """Normal modes [mode][atom][xyz] in the other order."""
    if not found or mapping is None:
        return found
    return [reordered(mode, mapping) for mode in found]


def mode_check(found: dict[str, Any] | None, mapping: list[int] | None) -> dict[str, Any] | None:
    """D242's check of a saddle: its region (0-based atoms) renumbered and its mode reordered."""
    if not found or mapping is None:
        return found
    back = inverse(mapping)
    out = dict(found)
    if isinstance(found.get("region"), list):
        out["region"] = sorted(back[a] for a in found["region"] if 0 <= a < len(back))
    if isinstance(found.get("mode"), list):
        out["mode"] = reordered(found["mode"], mapping)
    return out


_COORDINATE = re.compile(r"\b([XBADL])\(([\d,\s]+)\)")


def constraint_text(text: str, mapping: list[int] | None) -> str:
    """A held coordinate as the parser describes it ("scan D(3,4,9,10)") in the node's
    numbering (1-based)."""
    if mapping is None:
        return text
    back = inverse(mapping)

    def renumber(match: re.Match[str]) -> str:
        atoms = []
        for part in match.group(2).split(","):
            n = int(part)
            atoms.append(str(back[n - 1] + 1) if 1 <= n <= len(back) else part.strip())
        return f"{match.group(1)}({','.join(atoms)})"

    return _COORDINATE.sub(renumber, text)


def mapped_step(step: ParsedStep, mapping: list[int] | None) -> ParsedStep:
    """The step as its calculation's node numbers it: a copy (parsed files are cached and
    shared, so the step itself is never changed) with its structures, modes, mode check and
    scan coordinates in the node's order. IRC points name structures by index, so they
    follow."""
    if is_identity(mapping):
        return step
    assert mapping is not None
    back = inverse(mapping)
    return dataclasses.replace(
        step,
        geometries=[
            reordered(atoms, mapping) if len(atoms) == len(mapping) else atoms
            for atoms in step.geometries
        ],
        normal_modes=modes(step.normal_modes, mapping),
        mode_check=mode_check(step.mode_check, mapping),
        scan_coordinates=[
            [back[a - 1] + 1 if 1 <= a <= len(back) else a for a in atoms]
            for atoms in step.scan_coordinates
        ],
        constraints=[constraint_text(c, mapping) for c in step.constraints],
    )
