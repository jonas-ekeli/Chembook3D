"""Minimize's "relax" for metals (D146): each metal's bonds pulled to UFF's length and the
angles between its ligands to the ideal shape nearest to how they sit, since RDKit cannot type
Ru and most other metals for UFF itself (D140).

Bond lengths are UFF's rule, r_ij = r_i + r_j + r_BO - r_EN (Rappé et al., J. Am. Chem. Soc.
1992, 114, 10024), from its radii and electronegativities as RDKit carries them. The order is 1,
except 2 for an oxo or an alkylidene (a carbon with two other bonds, none to N, O or S), 1.5 for
a carbonyl or isocyanide carbon, and 0.5 for a saturated neutral O or N donor (an ether, alcohol,
water or amine), which UFF would otherwise make far too short. On public Ni, Pd, Ti, Mn, Zr and
Ir complexes this lands within about 0.1 Å; early metals' alkoxides come out long.

The shape is the one of the ideal shapes for that many ligands (linear; trigonal planar or T;
tetrahedral or square planar; trigonal bipyramid or square pyramid; octahedral) that fits the
ligand directions best, each ligand on the corner it fits. A metal with fewer than two or more
than six ligands, or with two ligand atoms bonded to each other (an η² olefin, a Cp), is held in
its shape instead.
"""

from dataclasses import dataclass
from itertools import permutations
from math import log, sqrt

import numpy as np
from rdkit import Chem

# UFF's radius r1 (Å) and GMP electronegativity per atom type, as in RDKit's UFF parameters.
UFF = {
    "H_": (0.354, 4.528), "H_b": (0.46, 4.528), "He4+4": (0.849, 9.66), "Li": (1.336, 3.006),
    "Be3+2": (1.074, 4.877), "B_3": (0.838, 5.11), "B_2": (0.828, 5.11), "C_3": (0.757, 5.343),
    "C_R": (0.729, 5.343), "C_2": (0.732, 5.343), "C_1": (0.706, 5.343), "N_3": (0.7, 6.899),
    "N_R": (0.699, 6.899), "N_2": (0.685, 6.899), "N_1": (0.656, 6.899), "O_3": (0.658, 8.741),
    "O_3_z": (0.528, 8.741), "O_R": (0.68, 8.741), "O_2": (0.634, 8.741), "O_1": (0.639, 8.741),
    "F_": (0.668, 10.874), "Ne4+4": (0.92, 11.04), "Na": (1.539, 2.843), "Mg3+2": (1.421, 3.951),
    "Al3": (1.244, 4.06), "Si3": (1.117, 4.168), "P_3+3": (1.101, 5.463), "P_3+5": (1.056, 5.463),
    "P_3+q": (1.056, 5.463), "S_3+2": (1.064, 6.928), "S_3+4": (1.049, 6.928),
    "S_3+6": (1.027, 6.928), "S_R": (1.077, 6.928), "S_2": (0.854, 6.928), "Cl": (1.044, 8.564),
    "Ar4+4": (1.032, 9.465), "K_": (1.953, 2.421), "Ca6+2": (1.761, 3.231),
    "Sc3+3": (1.513, 3.395), "Ti3+4": (1.412, 3.47), "Ti6+4": (1.412, 3.47),
    "V_3+5": (1.402, 3.65), "Cr6+3": (1.345, 3.415), "Mn6+2": (1.382, 3.325),
    "Fe3+2": (1.27, 3.76), "Fe6+2": (1.335, 3.76), "Co6+3": (1.241, 4.105),
    "Ni4+2": (1.164, 4.465), "Cu3+1": (1.302, 4.2), "Zn3+2": (1.193, 5.106),
    "Ga3+3": (1.26, 3.641), "Ge3": (1.197, 4.051), "As3+3": (1.211, 5.188),
    "Se3+2": (1.19, 6.428), "Br": (1.192, 7.79), "Kr4+4": (1.147, 8.505), "Rb": (2.26, 2.331),
    "Sr6+2": (2.052, 3.024), "Y_3+3": (1.698, 3.83), "Zr3+4": (1.564, 3.4),
    "Nb3+5": (1.473, 3.55), "Mo6+6": (1.467, 3.465), "Mo3+6": (1.484, 3.465),
    "Tc6+5": (1.322, 3.29), "Ru6+2": (1.478, 3.575), "Rh6+3": (1.332, 3.975),
    "Pd4+2": (1.338, 4.32), "Ag1+1": (1.386, 4.436), "Cd3+2": (1.403, 5.034),
    "In3+3": (1.459, 3.506), "Sn3": (1.398, 3.987), "Sb3+3": (1.407, 4.899),
    "Te3+2": (1.386, 5.816), "I_": (1.382, 6.822), "Xe4+4": (1.267, 7.595), "Cs": (2.57, 2.183),
    "Ba6+2": (2.277, 2.814), "La3+3": (1.943, 2.8355), "Ce6+3": (1.841, 2.774),
    "Pr6+3": (1.823, 2.858), "Nd6+3": (1.816, 2.8685), "Pm6+3": (1.801, 2.881),
    "Sm6+3": (1.78, 2.9115), "Eu6+3": (1.771, 2.8785), "Gd6+3": (1.735, 3.1665),
    "Tb6+3": (1.732, 3.018), "Dy6+3": (1.71, 3.0555), "Ho6+3": (1.696, 3.127),
    "Er6+3": (1.673, 3.1865), "Tm6+3": (1.66, 3.2514), "Yb6+3": (1.637, 3.2889),
    "Lu6+3": (1.671, 2.9629), "Hf3+4": (1.611, 3.7), "Ta3+5": (1.511, 5.1),
    "W_6+6": (1.392, 4.63), "W_3+4": (1.526, 4.63), "W_3+6": (1.38, 4.63), "Re6+5": (1.372, 3.96),
    "Re3+7": (1.314, 3.96), "Os6+6": (1.372, 5.14), "Ir6+3": (1.371, 5), "Pt4+2": (1.364, 4.79),
    "Au4+3": (1.262, 4.894), "Hg1+2": (1.34, 6.27), "Tl3+3": (1.518, 3.2), "Pb3": (1.459, 3.9),
    "Bi3+3": (1.512, 4.69), "Po3+2": (1.5, 4.21), "At": (1.545, 4.75), "Rn4+4": (1.42, 5.37),
    "Fr": (2.88, 2), "Ra6+2": (2.512, 2.843), "Ac6+3": (1.983, 2.835), "Th6+4": (1.721, 3.175),
    "Pa6+4": (1.711, 2.985), "U_6+4": (1.684, 3.341), "Np6+4": (1.666, 3.549),
    "Pu6+4": (1.657, 3.243), "Am6+4": (1.66, 2.9895), "Cm6+3": (1.801, 2.8315),
    "Bk6+3": (1.761, 3.1935), "Cf6+3": (1.75, 3.197), "Es6+3": (1.724, 3.333),
    "Fm6+3": (1.712, 3.4), "Md6+3": (1.689, 3.47), "No6+3": (1.679, 3.475), "Lw6+3": (1.698, 3.5),
}  # fmt: skip

DONOR_ANGLE = {  # M–L–X, degrees, for a donor with one other bond, by its hybridization
    Chem.HybridizationType.SP: 180.0,
    Chem.HybridizationType.SP2: 120.0,
    Chem.HybridizationType.SP3: 109.47,
}


def _shape(*corners) -> np.ndarray:
    points = np.array(corners, dtype=float)
    return points / np.linalg.norm(points, axis=1)[:, None]


_TBP = [(0, 0, 1), (0, 0, -1), (1, 0, 0), (-0.5, sqrt(3) / 2, 0), (-0.5, -sqrt(3) / 2, 0)]
SHAPES = {
    2: {"linear": _shape((1, 0, 0), (-1, 0, 0))},
    3: {
        "trigonal planar": _shape((1, 0, 0), (-0.5, sqrt(3) / 2, 0), (-0.5, -sqrt(3) / 2, 0)),
        "T-shaped": _shape((1, 0, 0), (-1, 0, 0), (0, 1, 0)),
    },
    4: {
        "tetrahedral": _shape((1, 1, 1), (1, -1, -1), (-1, 1, -1), (-1, -1, 1)),
        "square planar": _shape((1, 0, 0), (-1, 0, 0), (0, 1, 0), (0, -1, 0)),
    },
    5: {
        "trigonal bipyramidal": _shape(*_TBP),
        "square pyramidal": _shape((0, 0, 1), (1, 0, 0), (-1, 0, 0), (0, 1, 0), (0, -1, 0)),
    },
    6: {"octahedral": _shape((1, 0, 0), (-1, 0, 0), (0, 1, 0), (0, -1, 0), (0, 0, 1), (0, 0, -1))},
}


TOLERANCE = 10.0  # degrees either side of an ideal angle at the metal
CHELATE_TOLERANCE = 25.0  # between two donors of one chelate: its ring sets the bite
RING_TOLERANCE = 15.0  # M–L–X with X in a chelate ring


@dataclass
class Relaxed:
    shape: str
    lengths: dict[int, float]  # ligand atom: Å
    angles: dict[tuple[int, int], tuple[float, float]]  # ligand pair (a < b): degrees allowed
    donors: dict[tuple[int, int], tuple[float, float]]  # (donor, neighbour): M–L–X allowed


def _labels(element: str) -> list[str]:
    return [
        label
        for label in UFF
        if label.startswith(element) and not label[len(element) :][:1].isalpha()
    ]


def metal_label(element: str, ligands: int) -> str | None:
    """The metal's UFF type for this many ligands: octahedral (6) from five, square planar (4)
    or else tetrahedral (3) at four, tetrahedral below."""
    labels = _labels(element)
    if not labels:
        return None
    wanted = ["6", "4", "3"] if ligands >= 5 else ["4", "3", "6"] if ligands == 4 else ["3"]
    for digit in wanted:
        for label in labels:
            if label[len(element) :].lstrip("_").startswith(digit):
                return label
    return labels[0]


def ligand_label(atom: Chem.Atom) -> str | None:
    """A ligand atom's UFF type from its element, aromaticity and hybridization (the bond to the
    metal not counted)."""
    element = atom.GetSymbol()
    labels = _labels(element)
    if not labels:
        return None
    if len(labels) == 1:
        return labels[0]
    if atom.GetIsAromatic() and f"{element}_R" in UFF:
        return f"{element}_R"
    hybridization = atom.GetHybridization()
    digit = {
        Chem.HybridizationType.SP: "1",
        Chem.HybridizationType.SP2: "2",
        Chem.HybridizationType.SP3: "3",
    }.get(hybridization, "3")
    for label in labels:
        if label[len(element) :].lstrip("_").startswith(digit):
            return label
    return labels[0]


def bond_length(metal: str, ligand: str, order: float) -> float:
    """UFF's natural bond length between two atom types."""
    ri, xi = UFF[metal]
    rj, xj = UFF[ligand]
    r_bo = -0.1332 * (ri + rj) * log(order)
    r_en = ri * rj * (sqrt(xi) - sqrt(xj)) ** 2 / (xi * ri + xj * rj)
    return ri + rj + r_bo - r_en


def bond_order(mol: Chem.Mol, graph: list[set[int]], metal: int, ligand: int) -> float:
    atom = mol.GetAtomWithIdx(ligand)
    others = [o for o in graph[ligand] if o != metal]
    element = atom.GetSymbol()
    other_elements = {mol.GetAtomWithIdx(o).GetSymbol() for o in others}
    if element in ("O", "S", "N") and not others:
        return 2.0  # oxo, sulfido, nitrido
    if (
        element == "C"
        and len(others) == 1
        and mol.GetBondBetweenAtoms(ligand, others[0]).GetBondType()
        in (Chem.BondType.TRIPLE, Chem.BondType.DOUBLE)
    ):
        return 1.5  # carbonyl, isocyanide
    if element == "C" and len(others) == 2 and not other_elements & {"N", "O", "S"}:
        return 2.0  # alkylidene
    if element in ("O", "N") and atom.GetFormalCharge() >= 0 and not atom.GetIsAromatic():
        single = all(
            mol.GetBondBetweenAtoms(ligand, o).GetBondType()
            not in (Chem.BondType.DOUBLE, Chem.BondType.TRIPLE)
            for o in others
        )
        if single and len(others) == (2 if element == "O" else 3):
            return 0.5  # ether, alcohol, water, amine
    return 1.0


def nearest_shape(directions: np.ndarray) -> tuple[str, list[int]] | None:
    """The ideal shape nearest to these unit vectors and the corner each takes."""
    shapes = SHAPES.get(len(directions))
    if not shapes:
        return None
    best: tuple[float, str, list[int]] | None = None
    for name, corners in shapes.items():
        for order in permutations(range(len(corners))):
            ideal = corners[list(order)]
            turn = _fit(ideal, directions)
            misfit = float(((ideal @ turn - directions) ** 2).sum())
            if best is None or misfit < best[0] - 1e-9:
                best = (misfit, name, list(order))
    assert best is not None
    return best[1], best[2]


def _fit(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """The rotation (mirror allowed: the shapes are their own mirror images) taking a onto b."""
    u, _, vt = np.linalg.svd(a.T @ b)
    return u @ vt


def relaxed(
    mol: Chem.Mol, coords: np.ndarray, graph: list[set[int]], metals: set[int], metal: int
) -> Relaxed | None:
    """The bond lengths and angles a metal relaxes to, or None to hold it in its shape."""
    ligands = sorted(graph[metal])
    if not 2 <= len(ligands) <= 6:
        return None
    if any(b in graph[a] for i, a in enumerate(ligands) for b in ligands[i + 1 :]):
        return None  # an η ligand
    label = metal_label(mol.GetAtomWithIdx(metal).GetSymbol(), len(ligands))
    if label is None:
        return None
    lengths = {}
    for lig in ligands:
        atom = mol.GetAtomWithIdx(lig)
        other = (
            metal_label(atom.GetSymbol(), len(graph[lig])) if lig in metals else ligand_label(atom)
        )
        if other is None:
            return None
        lengths[lig] = bond_length(label, other, bond_order(mol, graph, metal, lig))
    directions = np.array([coords[lig] - coords[metal] for lig in ligands])
    directions /= np.linalg.norm(directions, axis=1)[:, None]
    found = nearest_shape(directions)
    if found is None:
        return None
    name, order = found
    corners = SHAPES[len(ligands)][name][order]
    angles = {}
    for i, a in enumerate(ligands):
        for j in range(i + 1, len(ligands)):
            b = ligands[j]
            cosine = float(np.clip(corners[i] @ corners[j], -1.0, 1.0))
            ideal = float(np.degrees(np.arccos(cosine)))
            together = b in _reach(graph, a, {metal})
            spread = CHELATE_TOLERANCE if together else TOLERANCE
            angles[(a, b)] = (max(ideal - spread, 0.0), min(ideal + spread, 180.0))
    donors = {}
    for lig in ligands:
        if lig not in metals:
            donors.update(_donor_angles(mol, coords, graph, metal, lig, set(ligands)))
    return Relaxed(name, lengths, angles, donors)


def _donor_angles(
    mol: Chem.Mol,
    coords: np.ndarray,
    graph: list[set[int]],
    metal: int,
    lig: int,
    ligands: set[int],
) -> dict[tuple[int, int], tuple[float, float]]:
    """M–L–X for each neighbour X of a donor: the metal on the donor's open side (its lone
    pair: away from its other bonds), or by its hybridization when it has one other bond. A
    neighbour in a ring back to the metal (a chelate) gets RING_TOLERANCE either side."""
    others = sorted(graph[lig] - {metal})
    found = {}
    if len(others) == 1:
        atom = mol.GetAtomWithIdx(lig)
        angle = 120.0 if atom.GetIsAromatic() else DONOR_ANGLE.get(atom.GetHybridization())
        if angle is not None:
            found[(lig, others[0])] = angle
    elif others:
        units = [coords[x] - coords[lig] for x in others]
        units = [u / np.linalg.norm(u) for u in units]
        open_side = -np.sum(units, axis=0)
        if np.linalg.norm(open_side) < 0.1:
            return {}  # planar with three others: no open side
        open_side /= np.linalg.norm(open_side)
        for x, u in zip(others, units, strict=True):
            found[(lig, x)] = float(np.degrees(np.arccos(np.clip(open_side @ u, -1.0, 1.0))))
    ranges = {}
    for (a, x), angle in found.items():
        ring = bool(_reach(graph, x, {metal, lig}) & (ligands - {lig}))
        spread = RING_TOLERANCE if ring else 0.0
        ranges[(a, x)] = (angle - spread, min(angle + spread, 180.0))
    return ranges


def _reach(graph: list[set[int]], start: int, blocked: set[int]) -> set[int]:
    found, edge = {start}, [start]
    while edge:
        edge = [o for a in edge for o in graph[a] if o not in found and o not in blocked]
        found.update(edge)
    return found
