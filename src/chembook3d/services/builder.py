"""The structure editor's tools (D139): change a structure atom by atom in the "Edit structure"
window. Each tool takes the structure as rows and gives back new rows; nothing is stored until
the window saves the coordinates through the node's own rules (D23, D24, ID-4, ID-5).

Atoms are counted from 0 here (the window shows them from 1). Bonds are the 3D view's
(`atom_matching.bonds`, as `chem.ts`), changed by the bonds the user added or removed in the
window, which only decide which atoms move together.

Setting a distance, angle or dihedral moves one side of it: the atoms reached from the end atom
without passing the picked atom next to it. When the two sides are joined some other way (a
ring, a chelate, a metallacycle), only the end atom and the atoms bonded to nothing else move.
"""

import math
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from chembook3d import xyz
from chembook3d.services.atom_matching import DEFAULT_RADIUS, RADII, bonds

Rows = list[list[Any]]  # [[element, x, y, z], ...]
Pair = tuple[int, int]

MAX_PICKS = 4
CLASH = 1.0  # Å: a new atom closer than this to another is moved round its partner
TOO_CLOSE = 0.5  # Å: a move that leaves two atoms this close is refused
PLACE_TRIES = 24  # turns tried about the new bond when placing a fragment
TETRAHEDRAL = np.degrees(np.arccos(-1 / 3))  # 109.47°
BENT = 115.0  # degrees: two bonds closer than this are an sp3 centre's, wider an sp2 one's
# D148: a metal's open site is the direction with the most room, at least SITE_GAP from its bonds.
SITE_TRIES = 400  # directions tried
SITE_GAP = 50.0  # degrees
SITE_LENGTH = 2.1  # Å out from the metal where the room is measured (and SITE_BEYOND further)
SITE_BEYOND = 1.2
# D149: how much room a new atom or molecule has is its closest atom's distance to another over
# the sum of their van der Waals radii (Bondi's; H as Rowland and Taylor's, metals METAL_VDW).
# Below ROOMY a site is no better for more room; below CROWDED the message says so; below
# TOO_TIGHT nothing is placed and the message says what is in the way and where it fits.
VDW = {
    "H": 1.10, "B": 1.92, "C": 1.70, "N": 1.55, "O": 1.52, "F": 1.47, "Si": 2.10, "P": 1.80,
    "S": 1.80, "Cl": 1.75, "Se": 1.90, "Br": 1.85, "Te": 2.06, "I": 1.98,
}  # fmt: skip
OTHER_VDW = 1.8
METAL_VDW = 2.0
ROOMY = 0.85
CROWDED = 0.7
TOO_TIGHT = 0.65
DRAWN = 1.3  # times the covalent radii sum: a new atom this close to another would look bonded
WIDE = 100.0  # degrees: a new atom's bond this far from the others needs no better place
OFF_BONDS = 60.0  # degrees: a new atom's bond is put at least this far from the others if it can
FITS_STEP = 0.1  # Å: longer distances tried for the one where a refused molecule fits
NON_METALS = {
    "H", "He", "B", "C", "N", "O", "F", "Ne", "Si", "P", "S", "Cl", "Ar", "Ge", "As", "Se",
    "Br", "Kr", "Te", "I", "Xe", "At", "Rn",
}  # fmt: skip


class BuildError(ValueError):
    """A tool that cannot be used on these atoms; the message says why, for the user."""


@dataclass
class Hold:
    """D140: one atom frozen, or a distance, angle or dihedral (2 to 4 atoms) held at `value`
    (Å or degrees) when minimizing."""

    atoms: list[int]
    value: float | None = None


@dataclass
class Structure:
    rows: Rows
    added: list[Pair] = field(default_factory=list)  # bonds the user added
    removed: list[Pair] = field(default_factory=list)  # bonds the user removed
    changed: list[int] = field(default_factory=list)  # atoms changed in the window (D140)
    holds: list[Hold] = field(default_factory=list)  # what Minimize keeps (D140)
    # D145: the coordinates before the first of the Minimize runs since the atoms last changed
    # otherwise; Minimize takes the bonds and the metals' shape from them, so it stays put.
    minimized_from: Rows | None = None
    minimized_metals: str | None = None  # D146: "keep" or "relax", as those Minimizes did

    def but(
        self,
        rows: Rows | None = None,
        *,
        added: list[Pair] | None = None,
        removed: list[Pair] | None = None,
        changed: list[int] | tuple[int, ...] = (),
        holds: list[Hold] | None = None,
        minimized_from: Rows | None = None,
        minimized_metals: str | None = None,
    ) -> "Structure":
        """A copy with these parts replaced and `changed` atoms added to those changed. New
        rows drop `minimized_from` unless it is given (Minimize gives it)."""
        return Structure(
            self.rows if rows is None else rows,
            list(self.added if added is None else added),
            list(self.removed if removed is None else removed),
            sorted(set(self.changed) | set(changed)),
            [Hold(list(h.atoms), h.value) for h in (self.holds if holds is None else holds)],
            minimized_from if rows is not None else (minimized_from or self.minimized_from),
            minimized_metals if rows is not None else (minimized_metals or self.minimized_metals),
        )

    @property
    def coords(self) -> np.ndarray:
        return np.array([row[1:4] for row in self.rows], dtype=float).reshape(-1, 3)

    @property
    def elements(self) -> list[str]:
        return [row[0] for row in self.rows]


@dataclass
class Side:
    atom: int  # the end atom whose side this is
    atoms: list[int]  # every atom that moves, the end atom included
    whole: bool  # False: the sides are joined, so only the atom and its ends move


@dataclass
class Edited:
    structure: Structure
    picks: list[int]  # what the window picks after the edit
    message: str


# ---------- bonds and sides ----------


def _pair(i: int, j: int) -> Pair:
    return (i, j) if i < j else (j, i)


def neighbours(structure: Structure) -> list[set[int]]:
    """Each atom's bonded atoms: the 3D view's bonds with the user's changes."""
    n = len(structure.rows)
    found: list[set[int]] = [set() for _ in range(n)]
    if n:
        adjacency = bonds(structure.coords, structure.elements)
        for i, j in zip(*np.nonzero(np.triu(adjacency)), strict=True):
            found[int(i)].add(int(j))
            found[int(j)].add(int(i))
    for i, j in structure.added:
        found[i].add(j)
        found[j].add(i)
    for i, j in structure.removed:
        found[i].discard(j)
        found[j].discard(i)
    return found


def bonded(structure: Structure, i: int, j: int) -> bool:
    return j in neighbours(structure)[i]


def _reach(graph: list[set[int]], start: int, blocked: set[int]) -> set[int]:
    seen = {start}
    queue = [start]
    while queue:
        atom = queue.pop()
        for other in graph[atom]:
            if other not in seen and other not in blocked:
                seen.add(other)
                queue.append(other)
    return seen


def _sides(
    structure: Structure,
    ends: tuple[int, int],
    roots: tuple[int, int],
    pivots: tuple[int, int],
    keep: set[int],
) -> list[Side]:
    """The atoms that move with each end: those reached from its root without passing its
    pivot. When the two sides meet (a ring, a chelate, a metallacycle) or a side misses its
    end, only the end and the atoms bonded to it alone move."""
    graph = neighbours(structure)
    reached = [_reach(graph, root, {pivot}) for root, pivot in zip(roots, pivots, strict=True)]
    whole = not (reached[0] & reached[1]) and all(
        e in r for e, r in zip(ends, reached, strict=True)
    )
    found = []
    for end, atoms in zip(ends, reached, strict=True):
        if whole:
            found.append(Side(end, sorted(atoms), whole=True))
        else:
            tips = {o for o in graph[end] if len(graph[o]) == 1 and o not in keep}
            found.append(Side(end, sorted({end} | tips), whole=False))
    return found


def sides(structure: Structure, picks: list[int]) -> list[Side]:
    """The two sides a distance, angle or dihedral can move, the first picked end's first."""
    if len(picks) == 2:
        i, j = picks
        return _sides(structure, (i, j), (i, j), (j, i), {i, j})
    if len(picks) == 3:
        i, j, k = picks
        return _sides(structure, (i, k), (i, k), (j, j), {i, j, k})
    if len(picks) == 4:
        # A dihedral turns about j–k: the side of i turns with j, the last atom's with k.
        i, j, k, last = picks
        return _sides(structure, (i, last), (j, k), (k, j), {i, j, k, last})
    return []


def default_side(found: list[Side]) -> int:
    """The end atom whose side moves by default: the smaller side, else the last picked."""
    first, last = found
    if first.whole != last.whole:
        return first.atom if first.whole else last.atom
    return first.atom if len(first.atoms) < len(last.atoms) else last.atom


# ---------- measuring ----------


def measure(coords: np.ndarray, picks: list[int]) -> float | None:
    """Distance (Å), angle or dihedral (degrees, IUPAC sign as `chem.ts`) of 2, 3 or 4 atoms."""
    p = [coords[i] for i in picks]
    if len(p) == 2:
        return float(np.linalg.norm(p[1] - p[0]))
    if len(p) == 3:
        return _angle(p[0] - p[1], p[2] - p[1])
    if len(p) == 4:
        b1, b2, b3 = p[1] - p[0], p[2] - p[1], p[3] - p[2]
        n1, n2 = np.cross(b1, b2), np.cross(b2, b3)
        return float(np.degrees(np.arctan2(np.linalg.norm(b2) * b1 @ n2, n1 @ n2)))
    return None


def _angle(a: np.ndarray, b: np.ndarray) -> float:
    cos = a @ b / (np.linalg.norm(a) * np.linalg.norm(b))
    return float(np.degrees(np.arccos(np.clip(cos, -1.0, 1.0))))


def _unit(v: np.ndarray) -> np.ndarray:
    length = np.linalg.norm(v)
    return v / length if length > 1e-9 else v


def _rotation(axis: np.ndarray, degrees: float) -> np.ndarray:
    """Right-handed rotation matrix about a unit axis (Rodrigues)."""
    k = _unit(axis)
    t = np.radians(degrees)
    cross = np.array([[0, -k[2], k[1]], [k[2], 0, -k[0]], [-k[1], k[0], 0]])
    return np.eye(3) + np.sin(t) * cross + (1 - np.cos(t)) * cross @ cross


def _perpendicular(v: np.ndarray) -> np.ndarray:
    other = np.array([1.0, 0.0, 0.0]) if abs(_unit(v)[0]) < 0.9 else np.array([0.0, 1.0, 0.0])
    return _unit(np.cross(v, other))


def _with(
    structure: Structure,
    coords: np.ndarray,
    elements: list[str] | None = None,
    changed: list[int] | tuple[int, ...] = (),
) -> Structure:
    names = elements if elements is not None else structure.elements
    rows = [[e, *(round(float(c), 8) for c in xyz_)] for e, xyz_ in zip(names, coords, strict=True)]
    return structure.but(rows, changed=changed)


def _check_picks(structure: Structure, picks: list[int], count: int | None = None) -> None:
    if count is not None and len(picks) != count:
        raise BuildError(f"Pick {count} atom{'s' if count > 1 else ''} for this")
    if len(set(picks)) != len(picks):
        raise BuildError("Pick each atom once")
    for i in picks:
        if not 0 <= i < len(structure.rows):
            raise BuildError(f"There is no atom {i + 1}")


def _moving(structure: Structure, picks: list[int], move: int | None) -> Side:
    found = sides(structure, picks)
    chosen = default_side(found) if move is None else move
    for candidate in found:
        if candidate.atom == chosen:
            return candidate
    raise BuildError(f"Atom {chosen + 1} is not an end of the picked atoms")


def _overlap(coords: np.ndarray, moved: list[int]) -> tuple[int, int] | None:
    """Two atoms, one moved and one not, closer than TOO_CLOSE."""
    still = [i for i in range(len(coords)) if i not in set(moved)]
    if not still or not moved:
        return None
    d = np.linalg.norm(coords[moved][:, None, :] - coords[still][None, :, :], axis=2)
    a, b = np.unravel_index(np.argmin(d), d.shape)
    return (moved[a], still[b]) if d[a, b] < TOO_CLOSE else None


def _atoms(count: int) -> str:
    return f"{count} atom{'s' if count != 1 else ''}"


def _done(
    structure: Structure, coords: np.ndarray, moving: Side, picks: list[int], what: str
) -> Edited:
    clash = _overlap(coords, moving.atoms)
    if clash:
        a, b = clash
        # D148: say which side moved and which other side could move instead.
        others = [s for s in sides(structure, picks) if s.atom != moving.atom]
        instead = (
            f"move atom {others[0].atom + 1}'s side instead ({_atoms(len(others[0].atoms))})"
            if others
            else "move the other side"
        )
        raise BuildError(
            f"Moving atom {moving.atom + 1}'s side ({_atoms(len(moving.atoms))}) puts atom "
            f"{a + 1} on top of atom {b + 1}; {instead}, or pick atoms to move apart first"
        )
    how = (
        f"moved atom {moving.atom + 1}'s side ({_atoms(len(moving.atoms))})"
        if moving.whole
        else f"moved atom {moving.atom + 1} and the atoms bonded only to it (the sides are joined)"
    )
    result = _with(structure, coords, changed=moving.atoms)
    _set_hold(result, picks)
    return Edited(result, picks, f"{what}: {how}")


# ---------- tools on 2 to 4 atoms ----------


def set_distance(
    structure: Structure, picks: list[int], value: float, move: int | None = None
) -> Edited:
    _check_picks(structure, picks, 2)
    if not 0.3 <= value <= 20:
        raise BuildError("Give a distance between 0.3 and 20 Å")
    moving = _moving(structure, picks, move)
    coords = structure.coords
    i, j = picks
    still = i if moving.atom == j else j
    direction = coords[moving.atom] - coords[still]
    if np.linalg.norm(direction) < 1e-6:
        raise BuildError("The two atoms are on top of each other")
    shift = _unit(direction) * (value - np.linalg.norm(direction))
    coords[moving.atoms] += shift
    done = _done(structure, coords, moving, picks, f"Distance {i + 1}–{j + 1} set to {value:.3f} Å")
    if bonded(structure, i, j) and not bonded(done.structure, i, j):
        # D140: Minimize uses the bonds drawn, so a bond stretched out of sight is gone.
        done.message += "; no longer drawn bonded (Add bond keeps them bonded)"
    return done


def set_angle(
    structure: Structure, picks: list[int], value: float, move: int | None = None
) -> Edited:
    _check_picks(structure, picks, 3)
    if not 0 <= value <= 180:
        raise BuildError("Give an angle between 0 and 180°")
    moving = _moving(structure, picks, move)
    coords = structure.coords
    i, j, k = picks
    a, b = coords[i] - coords[j], coords[k] - coords[j]
    if min(np.linalg.norm(a), np.linalg.norm(b)) < 1e-6:
        raise BuildError("The middle atom is on top of an end atom")
    normal = np.cross(a, b)
    if np.linalg.norm(normal) < 1e-6:
        normal = _perpendicular(a)
    # Turning k about a × b takes it away from i; turning i the same way takes it towards k.
    delta = value - _angle(a, b)
    turn = delta if moving.atom == k else -delta
    rotation = _rotation(normal, turn)
    coords[moving.atoms] = (coords[moving.atoms] - coords[j]) @ rotation.T + coords[j]
    return _done(
        structure, coords, moving, picks, f"Angle {i + 1}–{j + 1}–{k + 1} set to {value:.1f}°"
    )


def set_dihedral(
    structure: Structure, picks: list[int], value: float, move: int | None = None
) -> Edited:
    _check_picks(structure, picks, 4)
    if not -360 <= value <= 360:
        raise BuildError("Give a dihedral between −180 and 180°")
    moving = _moving(structure, picks, move)
    coords = structure.coords
    i, j, k, last = picks
    axis = coords[k] - coords[j]
    if np.linalg.norm(axis) < 1e-6:
        raise BuildError("The two middle atoms are on top of each other")
    current = measure(coords, picks)
    assert current is not None
    delta = (value - current + 180) % 360 - 180
    # A right-handed turn of the last atom's side about j→k raises the dihedral (IUPAC).
    turn = delta if moving.atom == last else -delta
    rotation = _rotation(axis, turn)
    coords[moving.atoms] = (coords[moving.atoms] - coords[k]) @ rotation.T + coords[k]
    shown = (value + 180) % 360 - 180
    return _done(
        structure,
        coords,
        moving,
        picks,
        f"Dihedral {i + 1}–{j + 1}–{k + 1}–{last + 1} set to {shown:.1f}°",
    )


def rotate_about(
    structure: Structure, picks: list[int], degrees: float, move: int | None = None
) -> Edited:
    """Turn one side of a bond about it (two atoms picked)."""
    _check_picks(structure, picks, 2)
    moving = _moving(structure, picks, move)
    if not moving.whole:
        raise BuildError("The two sides of this bond are joined, so it cannot be turned")
    coords = structure.coords
    i, j = picks
    still = i if moving.atom == j else j
    axis = coords[moving.atom] - coords[still]
    if np.linalg.norm(axis) < 1e-6:
        raise BuildError("The two atoms are on top of each other")
    rotation = _rotation(axis, degrees)
    coords[moving.atoms] = (coords[moving.atoms] - coords[still]) @ rotation.T + coords[still]
    return _done(structure, coords, moving, picks, f"Turned about {i + 1}–{j + 1} by {degrees:g}°")


def set_bond(structure: Structure, picks: list[int], present: bool) -> Edited:
    """Add or remove the bond between two atoms, for which atoms move together (and, later,
    for the clean-up). Bonds the 3D view draws by distance stay drawn."""
    _check_picks(structure, picks, 2)
    pair = _pair(*picks)
    added = [p for p in structure.added if p != pair]
    removed = [p for p in structure.removed if p != pair]
    plain = Structure(structure.rows, added, removed)
    by_distance = bonded(plain, *pair)
    if present and not by_distance:
        added.append(pair)
    if not present and by_distance:
        removed.append(pair)
    changed = structure.but(added=sorted(added), removed=sorted(removed), changed=picks)
    i, j = pair
    verb = "added" if present else "removed"
    return Edited(changed, picks, f"Bond {i + 1}–{j + 1} {verb}")


# ---------- tools on one atom ----------


def _element(symbol: str) -> str:
    element = xyz.element_of(symbol.strip())
    if element is None:
        raise BuildError(f"“{symbol}” is not an element")
    return element


def change_element(structure: Structure, picks: list[int], symbol: str) -> Edited:
    _check_picks(structure, picks)
    if not picks:
        raise BuildError("Pick the atoms to change")
    element = _element(symbol)
    elements = structure.elements
    for i in picks:
        elements[i] = element
    changed = _with(structure, structure.coords, elements, changed=picks)
    names = ", ".join(str(i + 1) for i in picks)
    return Edited(changed, picks, f"Atom{'s' if len(picks) > 1 else ''} {names} now {element}")


def delete(structure: Structure, picks: list[int], with_hydrogens: bool = False) -> Edited:
    _check_picks(structure, picks)
    if not picks:
        raise BuildError("Pick the atoms to delete")
    gone = set(picks)
    if with_hydrogens:
        graph = neighbours(structure)
        for i in picks:
            gone |= {h for h in graph[i] if structure.rows[h][0] == "H" and len(graph[h]) == 1}
    keep = [i for i in range(len(structure.rows)) if i not in gone]
    number = {old: new for new, old in enumerate(keep)}

    def renumbered(pairs: list[Pair]) -> list[Pair]:
        return sorted(_pair(number[a], number[b]) for a, b in pairs if a in number and b in number)

    rows = [structure.rows[i] for i in keep]
    count = len(gone)
    graph = neighbours(structure)
    # The atoms the deleted ones were bonded to count as changed, so Minimize relaxes them.
    touched = {o for i in gone for o in graph[i]} | set(structure.changed)
    holds = [
        Hold([number[a] for a in h.atoms], h.value)
        for h in structure.holds
        if all(a in number for a in h.atoms)
    ]
    return Edited(
        Structure(
            rows,
            renumbered(structure.added),
            renumbered(structure.removed),
            sorted(number[a] for a in touched if a in number),
            holds,
        ),
        [],
        f"Deleted {count} atom{'s' if count != 1 else ''}; the atoms after them move up",
    )


def _radius(element: str) -> float:
    return RADII.get(element, DEFAULT_RADIUS)


def _bond_length(a: str, b: str) -> float:
    return _radius(a) + _radius(b)


def is_metal(element: str) -> bool:
    return element not in NON_METALS


def _vdw(element: str) -> float:
    return VDW.get(element, METAL_VDW if is_metal(element) else OTHER_VDW)


def _sphere(count: int) -> np.ndarray:
    """`count` directions spread evenly over a sphere (a golden spiral)."""
    golden = np.pi * (3 - np.sqrt(5))
    n = np.arange(count)
    z = 1 - 2 * (n + 0.5) / count
    r = np.sqrt(1 - z * z)
    return np.stack([r * np.cos(golden * n), r * np.sin(golden * n), z], axis=1)


def open_sites(coords: np.ndarray, graph: list[set[int]], atom: int, count: int = 1) -> np.ndarray:
    """D148: a metal's open coordination sites, best first: of the directions at least SITE_GAP
    from every bond, those with the most room SITE_LENGTH out (and SITE_BEYOND further, for the
    ligands' substituents), leaning towards the side its bonds leave open."""
    centre = coords[atom]
    units = np.array([_unit(coords[b] - centre) for b in sorted(graph[atom])]).reshape(-1, 3)
    others = np.delete(coords, atom, axis=0)
    tries = _sphere(SITE_TRIES)
    if len(units):
        apart = (tries @ units.T).max(axis=1) <= np.cos(np.radians(SITE_GAP))
        if apart.any():
            tries = tries[apart]
    if not len(others):
        return tries[:count]
    room = np.full(len(tries), np.inf)
    for length in (SITE_LENGTH, SITE_LENGTH + SITE_BEYOND):
        points = centre + tries * length
        near = np.linalg.norm(points[:, None, :] - others[None, :, :], axis=2).min(axis=1)
        room = np.minimum(room, near)
    total = -units.sum(axis=0) if len(units) else np.zeros(3)
    lean = tries @ _unit(total) if np.linalg.norm(total) > 1e-6 else np.zeros(len(tries))
    return tries[np.argsort(-(room + 0.25 * lean), kind="stable")[:count]]


def open_site(coords: np.ndarray, graph: list[set[int]], atom: int) -> np.ndarray:
    return open_sites(coords, graph, atom)[0]


def _free_direction(
    coords: np.ndarray, graph: list[set[int]], atom: int, metal: bool = False
) -> np.ndarray:
    """Where a new bond from `atom` points: away from its bonds (the third corner of a
    tetrahedron, the open side of a flat or linear centre), away from the rest of the
    structure for a lone atom, and round any atom it would land on. A metal with two or more
    bonds takes its open site (D148)."""
    bonded_to = sorted(graph[atom])
    if metal and len(bonded_to) >= 2:
        return open_site(coords, graph, atom)
    centre = coords[atom]
    units = [_unit(coords[b] - centre) for b in bonded_to]
    if not units:
        others = np.delete(coords, atom, axis=0)
        away = centre - others.mean(axis=0) if len(others) else np.array([1.0, 0.0, 0.0])
        return _unit(away) if np.linalg.norm(away) > 1e-6 else np.array([1.0, 0.0, 0.0])
    if len(units) == 1:
        u = units[0]
        # Away from a neighbour of the neighbour, so the new atom sits anti to it.
        beyond = [b for b in graph[bonded_to[0]] if b != atom]
        if beyond:
            v = coords[beyond[0]] - coords[bonded_to[0]]
            p = v - (v @ u) * u
            p = -_unit(p) if np.linalg.norm(p) > 1e-6 else _perpendicular(u)
        else:
            p = _perpendicular(u)
        t = np.radians(TETRAHEDRAL)
        return _unit(np.cos(t) * u + np.sin(t) * p)
    total = -np.sum(units, axis=0)
    if len(units) == 2 and _angle(units[0], units[1]) < BENT:
        # Two bonds at a tetrahedral angle: the new one goes out of their plane, to the side
        # with more room (the other side is left for the next).
        normal = _unit(np.cross(units[0], units[1]))
        half = np.radians(TETRAHEDRAL / 2)
        options = [_unit(np.cos(half) * _unit(total) + np.sin(half) * s * normal) for s in (1, -1)]
        others = np.delete(coords, [atom, *bonded_to], axis=0)
        if len(others):
            return max(options, key=lambda d: np.linalg.norm(others - (centre + d), axis=1).min())
        return options[0]
    if np.linalg.norm(total) > 0.3:
        return _unit(total)
    normal = np.cross(units[0], units[1])
    if np.linalg.norm(normal) < 1e-6:
        normal = _perpendicular(units[0])
    return _unit(normal)


def _placed(
    structure: Structure, atom: int, element: str, direction: np.ndarray, length: float
) -> np.ndarray:
    """The position `length` from `atom` along `direction`. When that is crowded or too close to
    one of the atom's bonds (D149), the direction with the most room, then the widest from the
    atom's bonds, then the nearest the wanted one. The atom's own neighbours are left out of the
    room: the angle keeps the new atom off them."""
    coords = structure.coords
    centre = coords[atom]
    graph = neighbours(structure)
    bonded_to = sorted(graph[atom])
    others = [i for i in range(len(coords)) if i != atom and i not in graph[atom]]
    units = np.array([_unit(coords[b] - centre) for b in bonded_to]).reshape(-1, 3)
    tries = np.vstack([direction[None], _sphere(SITE_TRIES)])
    if len(units):
        gap = np.degrees(np.arccos(np.clip((tries @ units.T).max(axis=1), -1, 1)))
    else:
        gap = np.full(len(tries), 180.0)
    if others:
        limit = _vdw(element) + np.array([_vdw(structure.elements[i]) for i in others])
        points = centre + tries * length
        d = np.linalg.norm(points[:, None, :] - coords[others][None, :, :], axis=2)
        room = (d / limit[None, :]).min(axis=1)
    else:
        room = np.full(len(tries), np.inf)
    if room[0] >= ROOMY and gap[0] >= WIDE:
        return centre + direction * length
    order = np.lexsort(
        (
            -(tries @ direction),
            -np.round(np.minimum(gap, WIDE) / 5),
            -np.round(np.minimum(room, ROOMY), 2),
            -(gap >= OFF_BONDS).astype(int),
        )
    )
    return centre + tries[order[0]] * length


def add_atom(
    structure: Structure, picks: list[int], symbol: str, length: float | None = None
) -> Edited:
    """A new atom bonded to the picked one, at the sum of their covalent radii or `length` Å (at
    a metal's open site, D148); with nothing picked, a lone atom to start from (D141), at the
    origin or beside the structure."""
    element = _element(symbol)
    if not picks:
        return add_molecule(structure, [], [[element, 0.0, 0.0, 0.0]], element)
    _check_picks(structure, picks, 1)
    if length is not None and not 0.5 <= length <= 5:
        raise BuildError("Give a bond length between 0.5 and 5 Å")
    (atom,) = picks
    coords = structure.coords
    graph = neighbours(structure)
    partner = structure.rows[atom][0]
    wanted = _bond_length(partner, element) if length is None else length
    note = ""
    if is_metal(partner) and len(graph[atom]) >= 2:
        # D149: at a metal's open site, as a molecule of one atom is bound there.
        fit = _site_fit(structure, atom, np.zeros((1, 3)), [element], [0], wanted, element)
        position = fit.points[0]
        note = _crowded_note(structure, fit, [element], len(structure.rows))
    else:
        direction = _free_direction(coords, graph, atom)
        position = _placed(structure, atom, element, direction, wanted)
    rows = [*structure.rows, [element, *(round(float(c), 8) for c in position)]]
    new = len(rows) - 1
    return Edited(
        structure.but(rows, changed=[new]),
        [new],
        f"Added {element} {new + 1} bonded to atom {atom + 1} at {wanted:.2f} Å{note}",
    )


# Bonds an atom usually has, for adding hydrogens to it (D141); the user can give another.
USUAL_BONDS = {"H": 1, "B": 3, "C": 4, "N": 3, "O": 2, "F": 1, "Si": 4, "P": 3, "S": 2}
USUAL_BONDS |= {"Cl": 1, "Se": 2, "Br": 1, "I": 1}


def add_hydrogens(
    structure: Structure, picks: list[int], bonds: int | None = None, count: int | None = None
) -> Edited:
    """Hydrogens on each picked atom until it has `bonds` bonds (its usual number by default),
    each on the atom's free side as Add atom puts it. Every drawn bond counts, a bond to a metal
    too; `count` adds exactly that many to each atom instead (D148)."""
    _check_picks(structure, picks)
    if not picks:
        raise BuildError("Pick the atoms to add hydrogens to")
    if bonds is not None and count is not None:
        raise BuildError("Give the bonds each atom ends with, or how many H to add, not both")
    if bonds is not None and not 1 <= bonds <= 8:
        raise BuildError("Give between 1 and 8 bonds")
    if count is not None and not 1 <= count <= 8:
        raise BuildError("Give between 1 and 8 H")
    result = Edited(structure, picks, "")
    added = 0
    full = []
    for atom in picks:
        element = structure.rows[atom][0]
        if count is not None:
            for _ in range(count):
                result = add_atom(result.structure, [atom], "H")
            added += count
            continue
        wanted = bonds if bonds is not None else USUAL_BONDS.get(element)
        if wanted is None:
            raise BuildError(f"Say how many bonds {element} {atom + 1} should have")
        start = added
        while len(neighbours(result.structure)[atom]) < wanted:
            result = add_atom(result.structure, [atom], "H")
            added += 1
        if added == start:
            full.append(atom)
    names = ", ".join(str(i + 1) for i in picks)
    plural = "s" if len(picks) > 1 else ""
    if added:
        message = f"Added {added} H to atom{plural} {names}"
    else:
        message = f"Atom{plural} {names} already {'have' if plural else 'has'} enough bonds"
    graph = neighbours(result.structure)
    elements = result.structure.elements
    before = neighbours(structure)
    metals = sorted(
        {b for a in picks for b in before[a] if is_metal(elements[b])} if count is None else set()
    )
    # D148, D149: the bonds counted, so a stray bond (an atom too close) or a metal's shows.
    counted = [
        f"{elements[a]} {a + 1}: " + ", ".join(f"{elements[b]} {b + 1}" for b in sorted(graph[a]))
        for a in (picks if metals else full)
    ]
    if counted:
        message += " (bonds now " + "; ".join(counted) + ")"
    if metals:
        names = ", ".join(f"{elements[m]} {m + 1}" for m in metals)
        message += f"; the bond to {names} counted (give count for an exact number of H)"
    return Edited(result.structure, picks, message)


# ---------- fragments and molecules ----------


def _substituents(
    direction: np.ndarray, length_of: list[float], phase: np.ndarray
) -> list[np.ndarray]:
    """Three positions round an sp3 centre at the origin whose bond to its parent points along
    -`direction`, each at its own length; `phase` points the first one."""
    d = _unit(direction)
    p = phase - (phase @ d) * d
    p = _unit(p) if np.linalg.norm(p) > 1e-6 else _perpendicular(d)
    t = np.radians(180 - TETRAHEDRAL)
    found = []
    for n, length in enumerate(length_of):
        turned = _rotation(d, 120 * n) @ p
        found.append(length * (np.cos(t) * d + np.sin(t) * turned))
    return found


def _methyl(
    at: np.ndarray, direction: np.ndarray, phase: np.ndarray
) -> list[tuple[str, np.ndarray]]:
    return [("H", at + v) for v in _substituents(direction, [1.09] * 3, phase)]


def _fragment(name: str) -> list[tuple[str, np.ndarray]]:
    """A substituent with its attaching atom first at the origin; its parent lies along -x."""
    x = np.array([1.0, 0.0, 0.0])
    y = np.array([0.0, 1.0, 0.0])
    origin = np.zeros(3)
    if name in ("F", "Cl", "Br", "I"):
        return [(name, origin)]
    if name == "Me":
        return [("C", origin), *_methyl(origin, x, y)]
    if name in ("Et", "iPr", "tBu"):
        carbons = {"Et": 1, "iPr": 2, "tBu": 3}[name]
        arms = _substituents(x, [1.53] * carbons + [1.09] * (3 - carbons), y)
        atoms: list[tuple[str, np.ndarray]] = [("C", origin)]
        for n, arm in enumerate(arms):
            atoms.append(("C" if n < carbons else "H", arm))
        for arm in arms[:carbons]:
            atoms.extend(_methyl(arm, _unit(arm), -x))
        return atoms
    if name == "CF3":
        return [("C", origin), *[("F", v) for v in _substituents(x, [1.35] * 3, y)]]
    if name == "OH":
        return [("O", origin), ("H", _substituents(x, [0.96], y)[0])]
    if name == "OMe":
        carbon = _substituents(x, [1.43], y)[0]
        return [("O", origin), ("C", carbon), *_methyl(carbon, _unit(carbon), -x)]
    if name == "NH2":
        return [("N", origin), *[("H", v) for v in _substituents(x, [1.01, 1.01], y)]]
    if name == "Ph":
        centre = x * 1.39
        ring = [
            centre + 1.39 * np.array([np.cos(a), np.sin(a), 0])
            for a in np.radians(180 + 60 * np.arange(6))
        ]
        atoms = [("C", ring[0])] + [("C", r) for r in ring[1:]]
        atoms += [("H", r + 1.08 * _unit(r - centre)) for r in ring[1:]]
        return atoms
    raise BuildError(f"No fragment called “{name}”")


FRAGMENTS = ("Me", "Et", "iPr", "tBu", "Ph", "CF3", "OMe", "OH", "NH2", "F", "Cl", "Br", "I")


def _molecule(name: str) -> list[tuple[str, np.ndarray]]:
    """A small molecule, centred, ready to be placed beside a structure."""
    if name == "ethylene":
        c = [np.array([-0.667, 0.0, 0.0]), np.array([0.667, 0.0, 0.0])]
        h = [np.array([sx * 1.232, sy * 0.924, 0.0]) for sx in (-1, 1) for sy in (-1, 1)]
        return [("C", c[0]), ("C", c[1]), *[("H", v) for v in h]]
    if name == "propene":
        atoms = _molecule("ethylene")
        # One hydrogen of the second carbon becomes a methyl.
        _, position = atoms.pop(5)
        carbon = atoms[1][1] + 1.50 * _unit(position - atoms[1][1])
        atoms.append(("C", carbon))
        atoms.extend(_methyl(carbon, _unit(carbon - atoms[1][1]), np.array([0.0, 0.0, 1.0])))
        return atoms
    if name == "water":
        return [
            ("O", np.zeros(3)),
            ("H", np.array([0.757, 0.586, 0.0])),
            ("H", np.array([-0.757, 0.586, 0.0])),
        ]
    if name == "CO":
        return [("C", np.array([-0.564, 0.0, 0.0])), ("O", np.array([0.564, 0.0, 0.0]))]
    raise BuildError(f"No molecule called “{name}”")


MOLECULES = ("ethylene", "propene", "water", "CO")


def replace_with_fragment(
    structure: Structure,
    picks: list[int],
    name: str,
    atoms: list[tuple[str, np.ndarray]] | None = None,
) -> Edited:
    """Put a substituent in place of an atom bonded to one other atom (usually an H): its
    attaching atom takes the replaced atom's number, its other atoms come last, and it is
    turned about the new bond to keep clear of the rest."""
    _check_picks(structure, picks, 1)
    (replaced,) = picks
    graph = neighbours(structure)
    if len(graph[replaced]) != 1:
        raise BuildError(
            "Pick an atom bonded to exactly one other atom (an H) to replace it with a fragment"
        )
    (parent,) = graph[replaced]
    coords = structure.coords
    if atoms is None:
        atoms = _fragment(name)
    u = _unit(coords[replaced] - coords[parent])
    length = _bond_length(structure.rows[parent][0], atoms[0][0])
    base = _frame(np.array([1.0, 0.0, 0.0]), u)
    local = np.array([p for _, p in atoms]) @ base.T
    others = [i for i in range(len(coords)) if i != replaced]

    def placed(turn: float) -> np.ndarray:
        return local @ _rotation(u, turn).T + coords[parent] + u * length

    def room(points: np.ndarray) -> float:
        rest = [i for i in others if i != parent]
        if not rest or len(points) == 1:
            return np.inf
        return float(np.linalg.norm(points[1:, None, :] - coords[rest][None, :, :], axis=2).min())

    best = max((placed(360 * n / PLACE_TRIES) for n in range(PLACE_TRIES)), key=room)
    rows = [list(row) for row in structure.rows]
    rows[replaced] = [atoms[0][0], *(round(float(c), 8) for c in best[0])]
    rows += [
        [e, *(round(float(c), 8) for c in p)] for (e, _), p in zip(atoms[1:], best[1:], strict=True)
    ]
    old = structure.rows[replaced][0]
    crowded = " (crowded: Minimize cleans it up)" if room(best) < CLASH else ""
    return Edited(
        structure.but(rows, changed=[replaced, *range(len(structure.rows), len(rows))]),
        [replaced],
        f"{old} {replaced + 1} replaced by {name}{crowded}",
    )


def _frame(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """A rotation taking unit vector a onto unit vector b."""
    a, b = _unit(a), _unit(b)
    axis = np.cross(a, b)
    if np.linalg.norm(axis) < 1e-9:
        return np.eye(3) if a @ b > 0 else _rotation(_perpendicular(a), 180)
    return _rotation(axis, np.degrees(np.arccos(np.clip(a @ b, -1, 1))))


# D148: the atoms of each built-in molecule that bind to the picked atom (its C=C, C or O).
DONORS = {"ethylene": [0, 1], "propene": [0, 1], "water": [0], "CO": [0]}


def _facing(new: np.ndarray, donors: list[int]) -> list[np.ndarray]:
    """Directions in the molecule (centred on its donors) that should point away from the atom
    it binds to: out from the donors to the rest, square to the donors' bond for two; for a flat
    molecule whose rest sits round its donors (ethylene), either side of its plane."""
    rest = [i for i in range(len(new)) if i not in set(donors)]
    away = new[rest].mean(axis=0) if rest else np.zeros(3)
    if len(donors) >= 2:
        span = new[donors]
        _, _, vt = np.linalg.svd(span - span.mean(axis=0))
        along = vt[: min(len(donors) - 1, 2)]
        away = away - along.T @ (along @ away)
    if np.linalg.norm(away) > 0.3:
        return [_unit(away)]
    if len(new) >= 3:
        _, _, vt = np.linalg.svd(new - new.mean(axis=0))
        normal = vt[2]
    elif len(new) == 2:
        normal = _perpendicular(new[1] - new[0])
    else:
        normal = np.array([1.0, 0.0, 0.0])
    return [normal, -normal]


@dataclass
class _Fit:
    points: np.ndarray
    room: float  # D149: the closest pair's distance over the sum of their van der Waals radii
    pair: tuple[int, int]  # that pair: an atom of the molecule (from 0 in it), another atom


def _site_directions(
    coords: np.ndarray, graph: list[set[int]], atom: int
) -> tuple[np.ndarray, np.ndarray]:
    """D149: every direction tried for a metal's open site (at least SITE_GAP from its bonds,
    or all when none is) and how far each is from the nearest bond, in degrees."""
    centre = coords[atom]
    units = np.array([_unit(coords[b] - centre) for b in sorted(graph[atom])]).reshape(-1, 3)
    tries = _sphere(SITE_TRIES)
    gap = np.degrees(np.arccos(np.clip((tries @ units.T).max(axis=1), -1, 1)))
    apart = gap >= SITE_GAP
    if apart.any():
        tries, gap = tries[apart], gap[apart]
    return tries, gap


def _fits(
    structure: Structure,
    atom: int,
    local: np.ndarray,
    elements: list[str],
    donors: list[int],
    faces: list[np.ndarray],
    directions: np.ndarray,
    gaps: np.ndarray,
    height: float,
) -> _Fit:
    """The molecule (centred on its donors) along each direction, `height` out, each face turned
    to the atom and spun about the direction: the one with the most room (D149), up to ROOMY,
    then the furthest from the atom's bonds. Its atoms but the donors keep off the atom too."""
    coords = structure.coords
    centre = coords[atom]
    others = [i for i in range(len(coords)) if i != atom]
    own = np.array([_vdw(e) for e in elements])
    against = coords[others + [atom]]
    limit = own[:, None] + np.array([_vdw(structure.elements[i]) for i in others + [atom]])[None]
    covalent = (
        np.array([_radius(e) for e in elements])[:, None]
        + np.array([_radius(structure.elements[i]) for i in others + [atom]])[None]
    )
    mask = np.zeros(limit.shape, dtype=bool)
    mask[donors, -1] = True  # a donor binds to the atom: no room needed there
    spins = PLACE_TRIES if len(local) > 1 else 1
    best: tuple[float, float] | None = None
    found: _Fit | None = None
    for direction, gap in zip(directions, gaps, strict=True):
        for face in faces:
            turned = local @ _frame(face, direction).T
            spun = np.stack(
                [turned @ _rotation(direction, 360 * n / spins).T for n in range(spins)]
            )
            points = spun + centre + direction * height  # (spins, atoms, 3)
            apart = np.linalg.norm(points[:, :, None] - against[None, None], axis=3)
            ratio = apart / limit
            # An atom the 3D view would draw bonded to another counts as having no room.
            drawn = apart / covalent
            ratio = np.where(drawn < DRAWN, np.minimum(ratio, TOO_TIGHT * drawn / DRAWN), ratio)
            ratio[:, mask] = np.inf
            flat = ratio.reshape(spins, -1)
            closest = flat.min(axis=1)
            n = int(np.argmax(closest))
            score = (round(min(float(closest[n]), ROOMY), 2), float(gap))
            if best is None or score > best:
                i, j = np.unravel_index(int(np.argmin(flat[n])), ratio.shape[1:])
                partner = (others + [atom])[j]
                best = score
                found = _Fit(points[n], float(closest[n]), (int(i), partner))
    assert found is not None
    return found


def _site_fit(
    structure: Structure,
    atom: int,
    new: np.ndarray,
    elements: list[str],
    donors: list[int],
    distance: float,
    what: str,
) -> _Fit:
    """D148, D149: the molecule bound with each donor `distance` Å from `atom` where it has the
    most room: at a metal over all its open directions, else along the atom's free side. Refused
    when even that leaves less than TOO_TIGHT, naming what is in the way and the distance at
    which it fits."""
    coords = structure.coords
    graph = neighbours(structure)
    metal = is_metal(structure.elements[atom]) and len(graph[atom]) >= 2
    local = new - new[donors].mean(axis=0)
    spread = float(np.sqrt((np.linalg.norm(local[donors], axis=1) ** 2).mean()))
    if distance <= spread:
        raise BuildError(f"Give a distance over {spread:.2f} Å for these donor atoms")
    faces = _facing(local, donors)
    if metal:
        directions, gaps = _site_directions(coords, graph, atom)
    else:
        directions, gaps = _free_direction(coords, graph, atom)[None], np.zeros(1)

    def at(length: float) -> _Fit:
        height = float(np.sqrt(length**2 - spread**2))
        return _fits(structure, atom, local, elements, donors, faces, directions, gaps, height)

    fit = at(distance)
    if fit.room >= TOO_TIGHT:
        return fit
    i, j = fit.pair
    apart = float(np.linalg.norm(fit.points[i] - coords[j]))
    first = len(structure.rows)
    where = "the best open site" if metal else "its free side"
    message = (
        f"No room for {what} at {distance:.2f} Å from {structure.elements[atom]} {atom + 1}: at "
        f"{where} {elements[i]} {first + i + 1} would be {apart:.2f} Å from "
        f"{structure.elements[j]} {j + 1}"
    )
    metal_name = f"{structure.elements[atom]} {atom + 1}"
    length = distance + FITS_STEP
    while length <= 5.0 + 1e-9:
        if at(length).room >= CROWDED:
            message += f". The nearest it fits is {length:.2f} Å"
            break
        length += FITS_STEP
    near = f"{structure.elements[j]} {j + 1}"
    if j in graph[atom]:
        message += f". {near} is bonded to {metal_name}"
    elif j != atom:
        message += f". Or turn the group {near} is in out of the way (dihedral or rotate)"
    if metal:
        message += (
            f"; to free a site, break a bond to {metal_name} (bond tool) and move that ligand away"
        )
    raise BuildError(message)


def _crowded_note(structure: Structure, fit: _Fit, elements: list[str], first: int) -> str:
    if fit.room >= CROWDED:
        return ""
    i, j = fit.pair
    apart = float(np.linalg.norm(fit.points[i] - structure.coords[j]))
    return (
        f" (crowded: {elements[i]} {first + i + 1} is {apart:.2f} Å from "
        f"{structure.elements[j]} {j + 1}; Minimize, or try another distance)"
    )


def _bound(
    structure: Structure,
    atom: int,
    new: np.ndarray,
    elements: list[str],
    donors: list[int],
    distance: float | None,
    what: str,
) -> np.ndarray | _Fit:
    """D148: the molecule turned so its donors face `atom` from its free side, each donor
    `distance` Å from it where it has the most room (`_site_fit`). Without `distance`, beside it
    along its free side, 2 Å clear of everything."""
    if distance is not None:
        return _site_fit(structure, atom, new, elements, donors, distance, what)
    coords = structure.coords
    centre = coords[atom]
    graph = neighbours(structure)
    metal = is_metal(structure.elements[atom])
    local = new - new[donors].mean(axis=0)
    spread = float(np.sqrt((np.linalg.norm(local[donors], axis=1) ** 2).mean()))
    faces = _facing(local, donors)
    direction = _free_direction(coords, graph, atom, metal)
    turned = local @ _frame(faces[0], direction).T
    height = 2.5 + spread
    moved = turned + centre + direction * height
    while np.linalg.norm(moved[:, None, :] - coords[None, :, :], axis=2).min() < 2.0:
        height += 0.5
        moved = turned + centre + direction * height
    return moved


def add_molecule(
    structure: Structure,
    picks: list[int],
    atoms: Rows,
    what: str,
    distance: float | None = None,
    donors: list[int] | None = None,
) -> Edited:
    """Put a molecule beside the structure: off the picked atom's free side (a metal's open
    site, D148), or past the structure's edge, never closer than 2 Å to it. Its `donors` (its
    own atoms from 0; a built-in molecule's own by default) face the picked atom; with
    `distance` each donor is that far from it and bonded to it. Its atoms come last."""
    if not atoms:
        raise BuildError(f"{what} has no coordinates")
    _check_picks(structure, picks)
    if len(picks) > 1:
        raise BuildError("Pick one atom to put the molecule beside, or none")
    if donors is not None and (
        not donors
        or len(set(donors)) != len(donors)
        or any(not 0 <= i < len(atoms) for i in donors)
    ):
        raise BuildError(f"The donor atoms are numbered 1 to {len(atoms)} in {what}, each once")
    if distance is not None and not picks:
        raise BuildError("Pick the atom the molecule binds to")
    if distance is not None and not 1.0 <= distance <= 5.0:
        raise BuildError("Give a distance between 1 and 5 Å")
    if picks and donors is None:
        donors = DONORS.get(what, [0])
    coords = structure.coords
    new = np.array([row[1:4] for row in atoms], dtype=float)
    first = len(structure.rows)
    note = ""
    added = list(structure.added)
    if picks and donors:
        (atom,) = picks
        elements = [row[0] for row in atoms]
        found = _bound(structure, atom, new, elements, donors, distance, what)
        if isinstance(found, _Fit):
            moved = found.points
            added += [_pair(atom, first + d) for d in donors]
            names = ", ".join(str(first + d + 1) for d in donors)
            note = f", bonded to atom {atom + 1} through {names} at {distance:.2f} Å"
            note += _crowded_note(structure, found, elements, first)
        else:
            moved = found
    else:
        new -= new.mean(axis=0)
        extent = float(np.linalg.norm(new, axis=1).max()) if len(new) else 0.0
        if len(coords) == 0:
            moved = new
        else:
            direction = np.array([1.0, 0.0, 0.0])
            start = coords[np.argmax(coords[:, 0])]
            placed_at = 2.5 + extent
            moved = new + start + direction * placed_at
            while np.linalg.norm(moved[:, None, :] - coords[None, :, :], axis=2).min() < 2.0:
                placed_at += 0.5
                moved = new + start + direction * placed_at
    rows = [list(row) for row in structure.rows]
    rows += [
        [row[0], *(round(float(c), 8) for c in p)] for row, p in zip(atoms, moved, strict=True)
    ]
    done = structure.but(rows, changed=range(first, len(rows)))
    done.added = added
    return Edited(
        done,
        [],
        (
            f"Added {what} as atom {first + 1}"
            if len(rows) == first + 1
            else f"Added {what} as atoms {first + 1}–{len(rows)}"
        )
        + note,
    )


def molecule_rows(name: str) -> Rows:
    return [[e, *(float(c) for c in p)] for e, p in _molecule(name)]


# ---------- rigid moves of a set of atoms (D150) ----------

Point = list[int]  # atoms (from 0); more than one means their centroid
ALIGN_STEP = 0.5  # degrees: the turns tried before refining
REACHED = 0.5  # degrees: an aligned angle this far from the target is said to fall short


def _runs(atoms: list[int]) -> str:
    """Atoms from 0 written from 1 with runs joined: 74–79, 82."""
    found: list[str] = []
    ordered = sorted(set(atoms))
    start = previous = None
    for a in [*ordered, None]:
        if a is not None and previous is not None and a == previous + 1:
            previous = a
            continue
        if start is not None:
            found.append(str(start + 1) if start == previous else f"{start + 1}–{previous + 1}")
        start = previous = a
    return ", ".join(found)


def _check_point(structure: Structure, point: Point, what: str) -> None:
    if not point:
        raise BuildError(f"Give the atoms of {what}")
    _check_picks(structure, sorted(set(point)))


def _at(coords: np.ndarray, point: Point) -> np.ndarray:
    return coords[point].mean(axis=0)


def _name_point(structure: Structure, point: Point) -> str:
    if len(point) == 1:
        return f"{structure.elements[point[0]]} {point[0] + 1}"
    names = ", ".join(f"{structure.elements[a]} {a + 1}" for a in point)
    return f"the middle of {names}"


def _moving_set(structure: Structure, atoms: list[int]) -> list[int]:
    if not atoms:
        raise BuildError("Give the atoms that move together")
    _check_picks(structure, sorted(set(atoms)))
    moving = sorted(set(atoms))
    if len(moving) == len(structure.rows):
        raise BuildError("Every atom would move: leave out the atoms that stay")
    return moving


def _rigid_axis(
    structure: Structure, moving: list[int], axis: list[Point] | None
) -> tuple[list[Point], np.ndarray, np.ndarray]:
    """The axis (two points) and its origin and unit direction. Without one, the bond axis of a
    set bonded to the rest through one atom: from that atom to the middle of the set's atoms
    bonded to it (a ligand's binding axis, η² or not)."""
    if axis is None:
        graph = neighbours(structure)
        partners = sorted({b for a in moving for b in graph[a]} - set(moving))
        if len(partners) != 1:
            raise BuildError(
                "Give the axis: these atoms are bonded to "
                + ("nothing else" if not partners else f"{len(partners)} other atoms")
                + ", so there is no bond to turn them about"
            )
        (partner,) = partners
        axis = [[partner], sorted(a for a in moving if partner in graph[a])]
    if len(axis) != 2:
        raise BuildError("Give the axis as two points (each an atom or atoms whose middle counts)")
    for n, point in enumerate(axis, start=1):
        _check_point(structure, point, f"the axis's point {n}")
    coords = structure.coords
    origin, end = _at(coords, axis[0]), _at(coords, axis[1])
    if np.linalg.norm(end - origin) < 1e-6:
        raise BuildError("The axis's two points are on top of each other")
    return axis, origin, _unit(end - origin)


def _turned(coords: np.ndarray, moving: list[int], origin, direction, degrees: float):
    out = coords.copy()
    out[moving] = (coords[moving] - origin) @ _rotation(direction, degrees).T + origin
    return out


def _kept_bonds(structure: Structure, before: np.ndarray, after: np.ndarray, moving: list[int]):
    """The bonds from the moved atoms to the rest, as they were and are (D150)."""
    graph = neighbours(structure)
    elements = structure.elements
    kept, changed = [], []
    for a in moving:
        for b in sorted(graph[a] - set(moving)):
            was = float(np.linalg.norm(before[a] - before[b]))
            now = float(np.linalg.norm(after[a] - after[b]))
            name = f"{elements[b]} {b + 1}–{elements[a]} {a + 1}"
            if abs(now - was) < 5e-4:
                kept.append(f"{name} {now:.3f} Å")
            else:
                changed.append(f"{name} {was:.3f} → {now:.3f} Å")
    text = ""
    if kept:
        text += "; bonds to the rest kept: " + ", ".join(kept)
    if changed:
        text += "; bonds to the rest changed: " + ", ".join(changed)
    return text


def _rigid_done(structure: Structure, coords: np.ndarray, moving: list[int], what: str) -> Edited:
    clash = _overlap(coords, moving)
    if clash:
        a, b = clash
        raise BuildError(f"That puts atom {a + 1} on top of atom {b + 1}")
    note = _kept_bonds(structure, structure.coords, coords, moving)
    return Edited(_with(structure, coords, changed=moving), moving, what + note)


SIGN = "positive turns anticlockwise seen from the axis's second point looking back at its first"


def rotate_fragment(
    structure: Structure, atoms: list[int], degrees: float, axis: list[Point] | None = None
) -> Edited:
    """D150: turn the atoms given as one rigid body about an axis, whatever the bonds say: two
    points, each an atom or the middle of several, from the first to the second. Right-handed:
    a positive turn is anticlockwise seen from the second point looking back at the first."""
    moving = _moving_set(structure, atoms)
    if not math.isfinite(degrees):
        raise BuildError("Give the turn in degrees")
    axis, origin, direction = _rigid_axis(structure, moving, axis)
    coords = _turned(structure.coords, moving, origin, direction, degrees)
    what = (
        f"Turned atoms {_runs(moving)} ({_atoms(len(moving))}) by {degrees:g}° about "
        f"{_name_point(structure, axis[0])} → {_name_point(structure, axis[1])} ({SIGN})"
    )
    return _rigid_done(structure, coords, moving, what)


def _line_angle(u: np.ndarray, v: np.ndarray) -> float:
    """The angle between two lines, 0 to 90°: a C=C parallel to Ru=C either way round is 0°."""
    cos = abs(float(_unit(u) @ _unit(v)))
    return float(np.degrees(np.arccos(min(cos, 1.0))))


def align_vectors(
    structure: Structure,
    atoms: list[int],
    vector: list[Point],
    reference: list[Point],
    target: float = 0.0,
    axis: list[Point] | None = None,
) -> Edited:
    """D150: turn the atoms given, rigidly about the axis (as rotate_fragment), by the smallest
    turn that puts the line of `vector` (two points among them) at `target` degrees to the line
    of `reference` (two points that stay): 0 parallel, 90 perpendicular. When the axis cannot
    reach the target, the closest it can is taken and said."""
    moving = _moving_set(structure, atoms)
    if not 0 <= target <= 90:
        raise BuildError("Give the angle between the two lines from 0 (parallel) to 90°")
    for name, pair in (("vector", vector), ("reference", reference)):
        if len(pair) != 2:
            raise BuildError(f"Give the {name} as two points (each an atom or atoms)")
        for n, point in enumerate(pair, start=1):
            _check_point(structure, point, f"the {name}'s point {n}")
    inside = set(moving)
    if not all(set(p) <= inside for p in vector):
        raise BuildError("The vector's atoms must be among the atoms that move")
    if any(set(p) & inside for p in reference):
        raise BuildError("The reference's atoms must stay where they are, not move")
    axis, origin, direction = _rigid_axis(structure, moving, axis)
    coords = structure.coords
    ref = _at(coords, reference[1]) - _at(coords, reference[0])
    if np.linalg.norm(ref) < 1e-6:
        raise BuildError("The reference's two points are on top of each other")
    vec = _at(coords, vector[1]) - _at(coords, vector[0])
    if np.linalg.norm(vec) < 1e-6:
        raise BuildError("The vector's two points are on top of each other")

    def miss(degrees: float) -> float:
        turned = vec @ _rotation(direction, degrees).T
        return abs(_line_angle(turned, ref) - target)

    tries = np.arange(-180.0, 180.0, ALIGN_STEP)
    misses = np.array([miss(t) for t in tries])
    best = None
    for t in tries[misses <= misses.min() + 2.0]:
        low, high = t - ALIGN_STEP, t + ALIGN_STEP  # refine each candidate by golden search
        for _ in range(40):
            a, b = low + 0.382 * (high - low), low + 0.618 * (high - low)
            if miss(a) < miss(b):
                high = b
            else:
                low = a
        found = (low + high) / 2
        found = (found + 180.0) % 360.0 - 180.0
        key = (round(miss(found), 2), abs(found))
        if best is None or key < best[0]:
            best = (key, found)
    assert best is not None
    degrees = float(best[1])
    turned = _turned(coords, moving, origin, direction, degrees)
    reached = _line_angle(_at(turned, vector[1]) - _at(turned, vector[0]), ref)
    what = (
        f"Turned atoms {_runs(moving)} ({_atoms(len(moving))}) by {degrees:.1f}° about "
        f"{_name_point(structure, axis[0])} → {_name_point(structure, axis[1])} ({SIGN}): "
        f"{_name_point(structure, vector[0])} → {_name_point(structure, vector[1])} is now at "
        f"{reached:.1f}° to {_name_point(structure, reference[0])} → "
        f"{_name_point(structure, reference[1])}"
    )
    if abs(reached - target) > REACHED:
        what += f" (the closest this axis allows to {target:g}°)"
    return _rigid_done(structure, turned, moving, what)


# ---------- holding for Minimize (D140) ----------

HOLD_KINDS = {2: "distance", 3: "angle", 4: "dihedral"}


def _same(atoms: list[int], picks: list[int]) -> bool:
    return atoms == picks or atoms == picks[::-1]


def _set_hold(structure: Structure, picks: list[int]) -> None:
    """A distance, angle or dihedral just set is held at its new value if it was held."""
    for hold in structure.holds:
        if len(hold.atoms) > 1 and _same(hold.atoms, picks):
            hold.value = measure(structure.coords, hold.atoms)


def freeze(structure: Structure, picks: list[int], frozen: bool = True) -> Edited:
    """Freeze the picked atoms where they are for Minimize, or let them move again."""
    _check_picks(structure, picks)
    if not picks:
        raise BuildError("Pick the atoms to freeze")
    holds = [h for h in structure.holds if not (len(h.atoms) == 1 and h.atoms[0] in picks)]
    if frozen:
        holds += [Hold([i]) for i in sorted(picks)]
    names = ", ".join(str(i + 1) for i in picks)
    plural = "s" if len(picks) > 1 else ""
    verb = "frozen" if frozen else "free to move"
    return Edited(structure.but(holds=holds), picks, f"Atom{plural} {names} {verb}")


def hold(structure: Structure, picks: list[int], value: float | None = None) -> Edited:
    """Hold the picked distance, angle or dihedral at a value (its current one by default)."""
    _check_picks(structure, picks)
    kind = HOLD_KINDS.get(len(picks))
    if kind is None:
        raise BuildError("Pick 2, 3 or 4 atoms to hold a distance, angle or dihedral")
    current = measure(structure.coords, picks)
    if current is None:
        raise BuildError("Two of the picked atoms are on top of each other")
    if value is None:
        value = current
    elif kind == "distance" and not 0.3 <= value <= 20:
        raise BuildError("Give a distance between 0.3 and 20 Å")
    elif kind == "angle" and not 0 <= value <= 180:
        raise BuildError("Give an angle between 0 and 180°")
    elif kind == "dihedral":
        value = (value + 180) % 360 - 180
    holds = [h for h in structure.holds if not _same(h.atoms, picks)]
    holds.append(Hold(list(picks), float(value)))
    names = "–".join(str(i + 1) for i in picks)
    shown = f"{value:.3f} Å" if kind == "distance" else f"{value:.1f}°"
    return Edited(structure.but(holds=holds), picks, f"{kind.capitalize()} {names} held at {shown}")


def release(structure: Structure, index: int) -> Edited:
    """Stop holding one entry of the list of holds."""
    if not 0 <= index < len(structure.holds):
        raise BuildError("That is not held")
    atoms = structure.holds[index].atoms
    holds = [h for n, h in enumerate(structure.holds) if n != index]
    names = "–".join(str(i + 1) for i in atoms)
    what = f"Atom {names}" if len(atoms) == 1 else f"{HOLD_KINDS[len(atoms)].capitalize()} {names}"
    return Edited(structure.but(holds=holds), [], f"{what} no longer held")


# ---------- what the window shows ----------


def electrons_of(rows: Rows, charge: int) -> int:
    return sum(xyz.ELEMENTS.index(row[0]) + 1 for row in rows) - charge


def electrons_note(rows: Rows, charge: int, multiplicity: int) -> str | None:
    """D139: a structure whose electron count cannot have the node's multiplicity (an odd count
    as a singlet) says so, as the node's charge and multiplicity are not changed by editing."""
    if not rows:
        return None
    electrons = electrons_of(rows, charge)
    if electrons < 0 or (electrons + multiplicity) % 2 == 1:
        return None
    return (
        f"{electrons} electrons with charge {charge} cannot have multiplicity {multiplicity}; "
        "set the charge or multiplicity in the node's details after saving"
    )


def formula(rows: Rows) -> str:
    return xyz.formula_of(Counter(row[0] for row in rows))
