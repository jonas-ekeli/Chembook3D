"""The DFT core of a local Quick DFT refine (D214): which atoms ONIOM treats with r2SCAN-3c,
and where hydrogen link atoms cap the bonds it cuts.

Nothing here is tied to one metal (Jonas, 2026-11-02: versatile, not Ru only). The suggestion
starts from seeds: every metal atom, or the atoms the user picks (a metal-free molecule, or a
TS's reacting atoms). The core is the seeds, their neighbours, the rings those neighbours are
in, and the atoms within two bonds of a carbon bonded to a metal (an alkylidene, a
metallacycle); hydrogens on core atoms join it. A bond may only be cut where it is single:
an outer atom that is not saturated (a carbon with fewer than four neighbours, a nitrogen or
phosphorus with fewer than three, an oxygen or sulfur bonded once) is taken into the core, so a
link atom never replaces part of a double bond or an aromatic ring. Bonds to metals are never
cut. Each cut bond inner–outer gets a link H on the line from the inner atom to the outer one,
at the fraction g = (r_inner + r_H) / (r_inner + r_outer) of the bond (covalent radii).
"""

from dataclasses import dataclass, field

import numpy as np

from chembook3d.services.atom_matching import DEFAULT_RADIUS, RADII, bonds
from chembook3d.services.builder import is_metal

MAX_RING = 8  # rings up to this size join the core with a neighbour of a seed
SATURATED = {"C": 4, "Si": 4, "N": 3, "P": 3, "O": 2, "S": 2, "B": 3}
MAX_GROW = 200  # rounds of taking unsaturated outer atoms in; far more than any molecule needs


def atomic_number(element: str) -> int:
    from chembook3d.local_worker import ELEMENTS

    return ELEMENTS.index(element) + 1


@dataclass
class Core:
    atoms: list[int]  # 0-based, sorted
    links: list[tuple[int, int, float]]  # (inner, outer, g)
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def model_size(self) -> int:
        return len(self.atoms) + len(self.links)


def _neighbours(adjacency: np.ndarray) -> list[set[int]]:
    return [set(np.flatnonzero(row).tolist()) for row in adjacency]


def _rings_through(atom: int, near: list[set[int]]) -> set[int]:
    """Atoms of the small rings (up to MAX_RING) that pass through `atom`."""
    found: set[int] = set()
    for first in near[atom]:
        # shortest path from `first` back to `atom` not using the bond atom-first
        previous = {first: -1}
        frontier = [first]
        depth = 1
        while frontier and depth < MAX_RING:
            nxt = []
            for a in frontier:
                for b in near[a]:
                    if a == first and b == atom:
                        continue
                    if b == atom:
                        path = [a]
                        while previous[path[-1]] != -1:
                            path.append(previous[path[-1]])
                        found.update(path)
                        found.add(atom)
                        nxt = []
                        frontier = []
                        break
                    if b not in previous:
                        previous[b] = a
                        nxt.append(b)
                else:
                    continue
                break
            frontier = nxt
            depth += 1
    return found


def _saturated(atom: int, elements: list[str], near: list[set[int]]) -> bool:
    need = SATURATED.get(elements[atom])
    return need is None or len(near[atom]) >= need


def suggest(elements: list[str], coords: np.ndarray, seeds: list[int] | None = None) -> list[int]:
    """The suggested core: from the metals, or from `seeds` when given."""
    near = _neighbours(bonds(coords, elements))
    metals = [i for i, e in enumerate(elements) if is_metal(e)]
    start = sorted(set(seeds)) if seeds else metals
    if not start:
        return []
    core: set[int] = set(start)
    for seed in start:
        for n in near[seed]:
            core.add(n)
            if not is_metal(elements[n]):
                core |= _rings_through(n, near)
    for metal in metals:
        if metal not in core:
            continue
        for carbon in near[metal]:
            if elements[carbon] != "C":
                continue
            ring = {carbon}
            for _ in range(2):
                ring |= {b for a in ring for b in near[a]}
            core |= ring
    return close(elements, coords, sorted(core), near)


def close(
    elements: list[str],
    coords: np.ndarray,
    atoms: list[int],
    near: list[set[int]] | None = None,
) -> list[int]:
    """`atoms` with what a valid cut needs: hydrogens and metals bonded to them, and outer atoms
    that are not saturated (so every cut bond is single)."""
    near = near if near is not None else _neighbours(bonds(coords, elements))
    core = set(atoms)
    for _ in range(MAX_GROW):
        added = set()
        for a in core:
            for b in near[a]:
                if b in core:
                    continue
                if elements[b] == "H" or is_metal(elements[b]) or is_metal(elements[a]):
                    added.add(b)
                elif not _saturated(b, elements, near) and not _saturated(a, elements, near):
                    added.add(b)
                elif not _saturated(b, elements, near) and elements[b] != "C":
                    added.add(b)
        if not added:
            break
        core |= added
    return sorted(core)


def link_fraction(inner: str, outer: str) -> float:
    r = RADII.get
    return (r(inner, DEFAULT_RADIUS) + r("H", 0.31)) / (
        r(inner, DEFAULT_RADIUS) + r(outer, DEFAULT_RADIUS)
    )


def build(
    elements: list[str], coords: np.ndarray, atoms: list[int], charge: int, multiplicity: int
) -> Core:
    """The core as given, with its link atoms and what is wrong with it."""
    near = _neighbours(bonds(coords, elements))
    core = sorted(set(atoms))
    inside = set(core)
    found = Core(atoms=core, links=[])
    if not core:
        found.errors.append("The core is empty: pick its atoms, or use a full-molecule method")
        return found
    if len(core) == len(elements):
        found.errors.append("The core is the whole molecule: use B97-3c or r2SCAN-3c instead")
        return found
    for a in core:
        for b in sorted(near[a] - inside):
            if is_metal(elements[a]) or is_metal(elements[b]):
                found.errors.append(
                    f"The core cuts the bond {elements[a]}{a + 1}–{elements[b]}{b + 1} to a metal; "
                    "take both atoms in or leave both out"
                )
            elif elements[b] == "H":
                found.errors.append(
                    f"The core cuts the bond to hydrogen {b + 1}; take it in with "
                    f"{elements[a]}{a + 1}"
                )
            else:
                if not _saturated(a, elements, near) and not _saturated(b, elements, near):
                    found.warnings.append(
                        f"The bond {elements[a]}{a + 1}–{elements[b]}{b + 1} may not be single; "
                        "a link atom there is a rough cap"
                    )
                found.links.append((a, b, round(link_fraction(elements[a], elements[b]), 4)))
    electrons = sum(atomic_number(elements[i]) for i in core) + len(found.links) - charge
    if (electrons - (multiplicity - 1)) % 2:
        links = len(found.links)
        found.errors.append(
            f"The core with its {links} link atom{'' if links == 1 else 's'} has {electrons} "
            f"electrons, which cannot have multiplicity {multiplicity}; add or remove an atom"
        )
    return found
