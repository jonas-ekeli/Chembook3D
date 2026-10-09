"""A free species that joins or leaves on a scan path (D120, A66).

The edge's free species (D69) says which species joins or leaves, so the two ends differ by its
atoms. The start and the species are put side by side as one structure (the start's atoms first,
then the species'), or the end and the species when it leaves, and matched to the bound end
with the usual matcher (D113): the bonds the species makes come out as `formed` (or `broken`),
like any other bond change, so the review, hand fixes and the reacting atoms work as before.

The path's far end is the separated structure, which the app builds rather than the agent: the
bound structure with the complex's own geometry fitted on the complex and the species' own
geometry fitted on the bound species, then pulled straight out along the line from what it
binds to through its centre, until no atom of the species comes within `clearance` of the
complex. The species so leaves the way it sits when bound, so it comes in to the face it binds
to, turned the way it binds.
"""

from collections import Counter
from dataclasses import dataclass, replace
from typing import Any

import numpy as np
from sqlalchemy.orm import Session

from chembook3d import pathtools
from chembook3d.models import Node, Transition
from chembook3d.services import atom_matching
from chembook3d.services.geometry import _coords, _fit, _rmsd
from chembook3d.services.records import RecordError
from chembook3d.services.species import JOINS, LEAVES, label

CLEARANCE = 4.0  # Å: the separated species' closest contact with the complex (A66)
CLEARANCE_RANGE = (3.0, 12.0)
GAP = 4.0  # Å between the start and the species put side by side for the match
PULL_STEP = 0.02  # Å
MAX_PULL = 40.0  # Å
COMPLEX_RMSD = 2.0  # Å: the complex's own geometry this far from its bound pose is doubtful


@dataclass
class Joining:
    """The species on a scan path, in the start's numbering (atoms from 1)."""

    node: Node
    direction: str  # JOINS or LEAVES along the path, from its start to its end
    bound: str  # "end" when the species joins, "start" when it leaves
    atoms: list[int]  # the species' atoms
    bonds: list[list[int]]  # the bonds between it and the complex where it is bound
    anchor: list[int]  # the complex atoms the line out starts from
    separated: list[list[Any]]  # the separated end
    clearance: float  # Å, the closest contact asked for
    pull: float  # Å the species was moved out along the line from its bound place
    closest: float  # Å, its closest contact with the complex once separated
    complex_rmsd: float  # Å, the complex's own geometry against its bound pose

    @property
    def name(self) -> str:
        return label(self.node)


def on_edge(session: Session, edge: Transition, start_ids: set[str]) -> tuple[Node, str] | None:
    """The one species joining or leaving on the edge, with its direction along the path from
    the start (the edge may run the other way). None when the edge has none."""
    entries = list(edge.species)
    if not entries:
        return None
    if len(entries) > 1:
        names = " and ".join(f"“{label(session.get(Node, e.species_id))}”" for e in entries)
        raise RecordError(
            f"{names} join or leave on this edge; a scan path follows one species at a time"
        )
    (entry,) = entries
    node = session.get(Node, entry.species_id)
    if entry.count != 1:
        raise RecordError(
            f"“{label(node)}” {entry.direction} {entry.count} times on this edge; a scan "
            "path follows one molecule of it"
        )
    forward = edge.source_id in start_ids
    direction = entry.direction if forward else (LEAVES if entry.direction == JOINS else JOINS)
    return node, direction


def check_atoms(
    small: list[list[Any]], species: list[list[Any]], bound: list[list[Any]], names: tuple
) -> None:
    """The small end and the species together hold the bound end's atoms."""
    small_name, species_name, bound_name = names
    together = Counter(r[0] for r in small) + Counter(r[0] for r in species)
    have = Counter(r[0] for r in bound)
    if together != have:
        differ = sorted(e for e in set(together) | set(have) if together[e] != have[e])
        details = ", ".join(f"{e} {together[e]} and {have[e]}" for e in differ)
        raise RecordError(
            f"“{small_name}” with “{species_name}” does not hold the atoms of “{bound_name}” "
            f"({details}); check the species on the edge"
        )


def beside(rows: list[list[Any]], species: list[list[Any]]) -> list[list[Any]]:
    """`rows` and the species side by side as one structure, the species GAP Å clear of it."""
    a, b = _coords(rows), _coords(species)
    reach_a = float(np.linalg.norm(a - a.mean(axis=0), axis=1).max())
    reach_b = float(np.linalg.norm(b - b.mean(axis=0), axis=1).max()) if len(b) > 1 else 0.0
    shift = a.mean(axis=0) + np.array([reach_a + reach_b + GAP, 0.0, 0.0]) - b.mean(axis=0)
    return [list(r) for r in rows] + [
        [r[0], *map(float, np.array(r[1:4]) + shift)] for r in species
    ]


def _fitted(own: np.ndarray, onto: np.ndarray) -> np.ndarray:
    """`own` turned and moved onto `onto` (the same atoms in the same order)."""
    if len(own) == 1:
        return onto.copy()
    centre_own, centre_onto = own.mean(axis=0), onto.mean(axis=0)
    rotation, _ = _fit(own - centre_own, onto - centre_onto, allow_mirror=False)
    return (own - centre_own) @ rotation + centre_onto


def separate(
    bound: list[list[Any]],
    own: list[list[Any]],
    species: list[int],
    bonds: list[tuple[int, int]],
    clearance: float = CLEARANCE,
) -> tuple[list[list[Any]], float, float, list[int], float]:
    """The separated structure (D120): `bound` with the complex's and the species' own
    geometries (`own`, the same atoms in the same order) fitted on their bound places, and the
    species pulled out along the line from what it binds to (the complex atoms of `bonds`, else
    the complex atom nearest it) through its centre, until its closest contact with the complex
    is `clearance`. 0-based atoms; returns the rows, how far it was pulled, its closest
    contact, the anchor atoms and the complex's own geometry's RMSD from its bound pose."""
    xyz, mine = _coords(bound), _coords(own)
    inside = set(species)
    complex_atoms = [i for i in range(len(bound)) if i not in inside]
    out = np.empty_like(xyz)
    out[complex_atoms] = _fitted(mine[complex_atoms], xyz[complex_atoms])
    out[species] = _fitted(mine[species], xyz[species])
    complex_rmsd = _rmsd(out[complex_atoms], xyz[complex_atoms])
    anchor = sorted({a if a not in inside else b for a, b in bonds})
    if not anchor:
        centre = out[species].mean(axis=0)
        anchor = [min(complex_atoms, key=lambda i: float(np.linalg.norm(out[i] - centre)))]
    line = out[species].mean(axis=0) - out[anchor].mean(axis=0)
    if np.linalg.norm(line) < 1e-6:  # the species' centre sits on the anchor: away from the rest
        line = out[species].mean(axis=0) - out[complex_atoms].mean(axis=0)
    if np.linalg.norm(line) < 1e-6:
        line = np.array([1.0, 0.0, 0.0])
    line = line / np.linalg.norm(line)
    rest = out[complex_atoms]

    def contact(shift: float) -> float:
        moved = out[species] + shift * line
        return float(np.linalg.norm(moved[:, None, :] - rest[None, :, :], axis=2).min())

    pull = 0.0
    while contact(pull) < clearance and pull < MAX_PULL:
        pull += PULL_STEP
    closest = contact(pull)
    out[species] = out[species] + pull * line
    rows = [[r[0], *map(float, p)] for r, p in zip(bound, out, strict=True)]
    return rows, pull, closest, anchor, complex_rmsd


def _fragment(bound: list[list[Any]], heavy: set[int]) -> list[int]:
    """The species in the bound structure: its heavy atoms and the hydrogens bonded to them."""
    adjacency = atom_matching.bonds(_coords(bound), [r[0] for r in bound])
    found = set(heavy)
    for i in heavy:
        found |= {j for j in np.flatnonzero(adjacency[i]) if bound[j][0] == "H"}
    return sorted(int(i) for i in found)


def matched(
    start_rows: list[list[Any]],
    end_rows: list[list[Any]],
    species_rows: list[list[Any]],
    direction: str,
    pairs: list[tuple[int, int]],
) -> tuple[atom_matching.Match, list[list[Any]], list[list[Any]]]:
    """The match of a path with a species (D113, D120): the side without it is put beside the
    species and matched to the side with it, in three passes. The first finds the species'
    heavy atoms in the bound structure; with the hydrogens bonded to them they are the
    species, and the complex and the species are then matched each on its own, so a hydrogen
    is never swapped between them. Returns the match and the two structures it was made on,
    (start side, end side), in their own numbering; `pairs` are hand-fixed (start side, end
    side) pairs, 0-based."""
    joins = direction == JOINS
    small, bound = (start_rows, end_rows) if joins else (end_rows, start_rows)
    side = beside(small, species_rows)
    n = len(small)
    # Hand-fixed pairs as (beside atom, bound atom).
    fixed = list(pairs) if joins else [(b, a) for a, b in pairs]
    first = atom_matching.match(side, bound, fixed)
    heavy = {first.mapping[i] for i in range(n, len(side)) if side[i][0] != "H"}
    fragment = _fragment(bound, heavy)
    # What the species binds to may turn its other neighbours round as it comes: a metal
    # changes shape when it gains or loses a ligand, so it is never called a mirror image.
    adjacency = atom_matching.bonds(_coords(bound), [r[0] for r in bound])
    inside = set(fragment)
    anchors = {int(k) for i in fragment for k in np.flatnonzero(adjacency[i]) if k not in inside}
    if Counter(bound[k][0] for k in fragment) != Counter(r[0] for r in species_rows):
        mapping = first.mapping  # the bonded hydrogens do not add up: keep the first match
    else:
        rest = [k for k in range(len(bound)) if k not in set(fragment)]
        where = {k: i for i, k in enumerate(rest)}, {k: i for i, k in enumerate(fragment)}
        complex_pairs = [(a, where[0][b]) for a, b in fixed if a < n and b in where[0]]
        species_pairs = [(a - n, where[1][b]) for a, b in fixed if a >= n and b in where[1]]
        on_complex = atom_matching.match(
            small, [bound[k] for k in rest], complex_pairs, {where[0][k] for k in anchors}
        )
        on_species = atom_matching.match(species_rows, [bound[k] for k in fragment], species_pairs)
        mapping = [rest[j] for j in on_complex.mapping] + [fragment[j] for j in on_species.mapping]
    if joins:
        start_side, end_side = side, bound
        pinned = list(enumerate(mapping))
        loose = anchors
    else:
        start_side, end_side = bound, side
        pinned = [(j, i) for i, j in enumerate(mapping)]
        loose = {i for i, j in enumerate(mapping) if j in anchors}
    match = atom_matching.match(start_side, end_side, pinned, loose)
    return replace(match, fixed=sorted(pairs)), start_side, end_side


def species_atoms(match: atom_matching.Match, direction: str, small: int, total: int) -> list[int]:
    """The species' atoms (0-based) in the start's numbering: after the start's own when it
    joins, those matched to the species beside the end when it leaves."""
    if direction == JOINS:
        return list(range(small, total))
    return [i for i, j in enumerate(match.mapping) if j >= small]


def crossing(match: atom_matching.Match, atoms: list[int]) -> list[tuple[int, int]]:
    """The bonds between the species and the complex that form (joins) or break (leaves)."""
    inside = set(atoms)
    return sorted((a, b) for a, b in match.formed + match.broken if (a in inside) != (b in inside))


def steady_doubts(
    match: atom_matching.Match,
    complex_rmsd: float,
    bonds: list[tuple[int, int]],
    anchor: list[int],
) -> atom_matching.Match:
    """The matcher's doubts with the species put beside the start: its fit over all atoms says
    nothing (the species is far off), so the complex's own fit stands in for it, and the atoms
    the species binds to may turn their other neighbours as it comes (a metal), so they are not
    called mirror images."""
    inverted = [i for i in match.inverted if i not in set(anchor)]
    doubts = [
        d
        for d in match.doubts
        if not d.startswith("the atoms whose bonds stay")
        and not (d.startswith("the match turns a centre") and not inverted)
    ]
    if complex_rmsd > COMPLEX_RMSD:
        doubts.append(
            f"the complex differs by {complex_rmsd:.2f} Å from its bound pose after fitting, so "
            "the two ends may not be the same structure"
        )
    if not bonds:
        doubts.append("the species makes no bond to the complex at the bound end")
    return replace(match, rmsd=complex_rmsd, doubts=doubts, inverted=inverted)


APPROACH = 3.5  # Å: the first of two stages brings the species' centre this far in (A67)


def approach(
    bound: list[list[Any]], separated: list[list[Any]], atoms: list[int], anchor: list[int]
) -> dict[str, Any] | None:
    """A two-stage approach for the brief (D120, A67), 0-based atoms in, from 1 out: the
    distance from the first anchor atom to the species' heavy atom nearest its centre, driven
    from its separated length to APPROACH Å (or 0.5 Å past its bound length when that is
    longer), with the species' turn held by one angle and one dihedral at their separated
    values. None for a species of one atom or an anchor with no other neighbour."""
    if len(atoms) < 2 or not anchor:
        return None
    xyz, apart = _coords(bound), _coords(separated)
    inside = set(atoms)
    a = anchor[0]
    adjacency = atom_matching.bonds(xyz, [r[0] for r in bound])
    around = [int(j) for j in np.flatnonzero(adjacency[a]) if j not in inside]
    if not around:
        return None
    heavy = [i for i in atoms if bound[i][0] != "H"] or list(atoms)
    centre = apart[atoms].mean(axis=0)
    c = min(heavy, key=lambda i: float(np.linalg.norm(apart[i] - centre)))

    def far(j: int) -> tuple[bool, bool, float]:
        # A heavy neighbour of c first, then another heavy atom, then the nearest atom.
        return (not adjacency[c, j], bound[j][0] == "H", float(np.linalg.norm(apart[j] - apart[c])))

    d = min((j for j in atoms if j != c), key=far)

    def sine(e: int) -> float:
        u, v = apart[e] - apart[a], apart[c] - apart[a]
        return float(np.linalg.norm(np.cross(u, v)) / np.linalg.norm(u) / np.linalg.norm(v))

    e = max(around, key=sine)
    rows = [[r[0], *map(float, p)] for r, p in zip(bound, apart, strict=True)]
    bound_length = float(np.linalg.norm(xyz[a] - xyz[c]))
    return {
        "distance": {
            "atoms": [a + 1, c + 1],
            "from": round(float(np.linalg.norm(apart[a] - apart[c])), 3),
            "to": round(max(APPROACH, bound_length + 0.5), 3),
            "bound": round(bound_length, 3),
        },
        "angle": {
            "atoms": [a + 1, c + 1, d + 1],
            "value": round(pathtools.measure(rows, [a + 1, c + 1, d + 1]), 2),
        },
        "dihedral": {
            "atoms": [e + 1, a + 1, c + 1, d + 1],
            "value": round(pathtools.measure(rows, [e + 1, a + 1, c + 1, d + 1]), 2),
        },
    }
