"""The structure editor's Minimize (D140): a quick clean-up with RDKit's UFF that keeps frozen
atoms and held distances, angles and dihedrals, and works with metals such as Ru. A placeholder
clean-up after editing, not a conformer search or an energy; xTB or DFT is run through Claude
as before (D9, D93).

UFF needs bond orders, and RDKit cannot type Ru and most other metals. So the bonds to every
atom UFF cannot type (a metal) are taken away, the bond orders of each ligand left are found
on their own (RDKit's DetermineBondOrders), the metal bonds are put back as single bonds, and
UFF is built for everything it can type. Each metal is then held in its current shape by stiff
restraints: its bonds, the angles between its ligands, the angles on at each ligand atom, and a
torsion about each bond. The organic parts relax; the metal keeps its shape.

Bonds are the editor's (the 3D view's, with the bonds the user added or removed). A bond the
user added to a metal is held at the sum of the covalent radii until a hold says otherwise.
"""

from dataclasses import dataclass
from itertools import combinations

import numpy as np
from rdkit import Chem, RDLogger
from rdkit.Chem import rdDetermineBonds, rdForceFieldHelpers
from rdkit.Geometry import Point3D

from chembook3d.services import builder, geometry
from chembook3d.services.builder import BuildError, Structure

# Elements UFF types reliably from bond orders; every other element is held by restraints.
TYPED = {"H", "B", "C", "N", "O", "F", "Si", "P", "S", "Cl", "Ge", "As", "Se", "Br", "Sn", "I"}
# Most bonds to non-metals an atom keeps (see `_bonds`).
VALENCE = {"H": 1, "F": 1, "Cl": 1, "Br": 1, "I": 1, "O": 2, "B": 4, "C": 4, "N": 4, "Si": 4}
NEAR = 2  # bonds out from a changed atom that also move
CHARGES = (0, -1, 1, -2, 2)  # tried in turn when finding a ligand's bond orders
METAL_BOND = 500.0  # kcal/mol/Å², restraint constants
METAL_ANGLE = 100.0  # kcal/mol/rad²
METAL_TORSION = 20.0
HOLD_DISTANCE = 1e4
HOLD_ANGLE = 1e3
STEPS = 2000
ROUNDS = 5  # STEPS each, until converged

RDLogger.DisableLog("rdApp.*")


@dataclass
class Minimized:
    structure: Structure
    moved: int  # atoms free to move
    largest: float  # Å, the largest move
    converged: bool


def _ligand_orders(elements: list[str], coords: np.ndarray, atoms: list[int], graph) -> Chem.Mol:
    """One ligand (a connected fragment without metals) with its bond orders, or single bonds
    when no charge gives a sensible Lewis structure."""
    index = {a: n for n, a in enumerate(atoms)}
    mol = Chem.RWMol()
    for a in atoms:
        atom = Chem.Atom(elements[a])
        atom.SetNoImplicit(True)
        mol.AddAtom(atom)
    for a in atoms:
        for b in graph[a]:
            if b in index and a < b:
                mol.AddBond(index[a], index[b], Chem.BondType.SINGLE)
    conf = Chem.Conformer(len(atoms))
    for a in atoms:
        conf.SetAtomPosition(index[a], Point3D(*map(float, coords[a])))
    mol.AddConformer(conf, assignId=True)
    for charge in CHARGES:
        trial = Chem.Mol(mol)
        try:
            rdDetermineBonds.DetermineBondOrders(trial, charge=charge, embedChiral=False)
        except (ValueError, RuntimeError):
            continue
        return trial
    plain = mol.GetMol()
    plain.UpdatePropertyCache(strict=False)
    return plain


def _bonds(structure: Structure, metals: set[int]) -> list[set[int]]:
    """The editor's bonds, less those an atom has too many of: an atom placed into a crowd (a
    fragment's H next to a methyl) is drawn bonded to both, but keeps only its shortest bonds
    to non-metals, relative to the covalent radii. Bonds to metals and bonds the user added
    always stay (agostic and bridging atoms)."""
    graph = builder.neighbours(structure)
    coords = structure.coords
    elements = structure.elements
    added = {tuple(p) for p in structure.added}

    def stretch(i: int, j: int) -> float:
        length = float(np.linalg.norm(coords[i] - coords[j]))
        return length / builder._bond_length(elements[i], elements[j])

    # Atoms with one bond first, so a crowded H gives up its bond before a carbon has to.
    for i in sorted(range(len(elements)), key=lambda i: VALENCE.get(elements[i], 9)):
        element = elements[i]
        most = VALENCE.get(element)
        if most is None:
            continue
        loose = [j for j in graph[i] if j not in metals and (min(i, j), max(i, j)) not in added]
        fixed = len([j for j in graph[i] if j not in metals]) - len(loose)
        for j in sorted(loose, key=lambda j: stretch(i, j))[max(most - fixed, 0) :]:
            graph[i].discard(j)
            graph[j].discard(i)
    return graph


def _molecule(structure: Structure) -> tuple[Chem.Mol, list[set[int]], list[int]]:
    """The structure as an RDKit molecule with bond orders, its bonds, and its metals."""
    elements = structure.elements
    coords = structure.coords
    metals = [i for i, e in enumerate(elements) if e not in TYPED]
    graph = _bonds(structure, set(metals))
    organic = [
        set() if i in metals else {j for j in graph[i] if j not in metals}
        for i in range(len(elements))
    ]
    mol = Chem.RWMol()
    for e in elements:
        atom = Chem.Atom(e)
        atom.SetNoImplicit(True)
        mol.AddAtom(atom)
    seen: set[int] = set()
    for start in range(len(elements)):
        if start in seen or start in metals:
            continue
        fragment = sorted(builder._reach(organic, start, set()))
        seen.update(fragment)
        ligand = _ligand_orders(elements, coords, fragment, organic)
        for n, a in enumerate(fragment):
            source = ligand.GetAtomWithIdx(n)
            mol.GetAtomWithIdx(a).SetFormalCharge(source.GetFormalCharge())
            mol.GetAtomWithIdx(a).SetNumRadicalElectrons(source.GetNumRadicalElectrons())
        for bond in ligand.GetBonds():
            a, b = fragment[bond.GetBeginAtomIdx()], fragment[bond.GetEndAtomIdx()]
            mol.AddBond(a, b, bond.GetBondType())
    for m in metals:
        for other in graph[m]:
            if other not in metals or m < other:
                if mol.GetBondBetweenAtoms(m, other) is None:
                    mol.AddBond(m, other, Chem.BondType.SINGLE)
    conf = Chem.Conformer(len(elements))
    for i, p in enumerate(coords):
        conf.SetAtomPosition(i, Point3D(*map(float, p)))
    mol.AddConformer(conf, assignId=True)
    result = mol.GetMol()
    result.UpdatePropertyCache(strict=False)
    Chem.GetSymmSSSR(result)
    try:
        Chem.SetAromaticity(result)
    except (ValueError, RuntimeError):
        pass  # kept as Kekulé bonds
    Chem.SetHybridization(result)
    for m in metals:  # RDKit has no UFF type for these; restraints hold them instead
        result.GetAtomWithIdx(m).SetHybridization(Chem.HybridizationType.UNSPECIFIED)
    return result, graph, metals


def _near(graph: list[set[int]], changed: list[int]) -> set[int]:
    found = set(changed)
    edge = set(changed)
    for _ in range(NEAR):
        edge = {o for a in edge for o in graph[a]} - found
        found |= edge
    return found


def _hold_metals(ff, structure: Structure, graph: list[set[int]], metals: list[int]) -> None:
    """Hold each metal in its current shape. A bond the user added to it has no shape yet: it
    is held at the covalent radii sum and left out of the angles and torsions."""
    coords = structure.coords
    added = {tuple(p) for p in structure.added}
    for m in metals:
        ligands = sorted(graph[m])
        new = {lig for lig in ligands if (min(m, lig), max(m, lig)) in added}
        for lig in ligands:
            if lig in new:
                length = builder._bond_length(structure.elements[m], structure.elements[lig])
            else:
                length = float(np.linalg.norm(coords[m] - coords[lig]))
            ff.UFFAddDistanceConstraint(m, lig, False, length, length, METAL_BOND)
        kept = [lig for lig in ligands if lig not in new]
        for a, b in combinations(kept, 2):
            angle = builder.measure(coords, [a, m, b])
            ff.UFFAddAngleConstraint(a, m, b, False, angle, angle, METAL_ANGLE)
        for lig in kept:
            others = [o for o in kept if o != lig]
            # A torsion about the bond, from the ligand most nearly across, keeps its turn.
            far = max(others, key=lambda o: builder.measure(coords, [o, m, lig]), default=None)
            for x in graph[lig] - {m}:
                angle = builder.measure(coords, [m, lig, x])
                ff.UFFAddAngleConstraint(m, lig, x, False, angle, angle, METAL_ANGLE)
                if far is not None and builder.measure(coords, [far, m, lig]) < 175:
                    torsion = builder.measure(coords, [far, m, lig, x])
                    ff.UFFAddTorsionConstraint(
                        far, m, lig, x, False, torsion, torsion, METAL_TORSION
                    )


def _holds(ff, structure: Structure, frozen: set[int]) -> None:
    for hold in structure.holds:
        atoms = hold.atoms
        if len(atoms) == 1:
            continue  # frozen atoms are fixed points
        if all(a in frozen for a in atoms):
            continue
        value = hold.value
        if len(atoms) == 2:
            ff.UFFAddDistanceConstraint(*atoms, False, value, value, HOLD_DISTANCE)
        elif len(atoms) == 3:
            ff.UFFAddAngleConstraint(*atoms, False, value, value, HOLD_ANGLE)
        else:
            ff.UFFAddTorsionConstraint(*atoms, False, value, value, HOLD_ANGLE)


def minimize(structure: Structure, everything: bool = False) -> Minimized:
    """Minimize the changed atoms and those within NEAR bonds of them (or every atom), keeping
    frozen atoms where they are and held coordinates at their values."""
    count = len(structure.rows)
    if count < 2:
        raise BuildError("There is nothing to minimize")
    mol, graph, metals = _molecule(structure)
    frozen = {h.atoms[0] for h in structure.holds if len(h.atoms) == 1}
    if everything:
        free = set(range(count))
    else:
        if not structure.changed:
            raise BuildError(
                "Nothing has changed since the window opened; choose “Everything” to minimize "
                "the whole structure"
            )
        free = _near(graph, structure.changed)
        # A held coordinate moves the atoms in it, so they are free too.
        free |= {a for h in structure.holds if len(h.atoms) > 1 for a in h.atoms}
    free -= frozen
    if not free:
        raise BuildError("Every atom that would move is frozen")
    try:
        ff = rdForceFieldHelpers.UFFGetMoleculeForceField(mol, ignoreInterfragInteractions=False)
    except (ValueError, RuntimeError) as exc:
        raise BuildError(f"UFF could not be set up for this structure ({exc})") from exc
    if ff is None:
        raise BuildError("UFF could not be set up for this structure")
    ff.Initialize()
    _hold_metals(ff, structure, graph, metals)
    _holds(ff, structure, frozen)
    for i in range(count):
        if i not in free:
            ff.AddFixedPoint(i)
    converged = False
    for _ in range(ROUNDS):
        if ff.Minimize(maxIts=STEPS) == 0:
            converged = True
            break
    coords = np.array(ff.Positions(), dtype=float).reshape(-1, 3)
    before = structure.coords
    if len(free) == count:
        # Nothing was fixed, so the whole may have drifted: put it back where it was.
        turn, _ = geometry._fit(coords - coords.mean(0), before - before.mean(0), False)
        coords = (coords - coords.mean(0)) @ turn + before.mean(0)
    largest = float(np.linalg.norm(coords - before, axis=1).max())
    rows = [
        [e, *(round(float(c), 8) for c in p)]
        for e, p in zip(structure.elements, coords, strict=True)
    ]
    return Minimized(structure.but(rows), len(free), largest, converged)


def run(structure: Structure, everything: bool = False) -> builder.Edited:
    """Minimize, with the message the window shows."""
    done = minimize(structure, everything)
    plural = "s" if done.moved != 1 else ""
    message = (
        f"Minimized {done.moved} atom{plural} with UFF; the largest move was {done.largest:.2f} Å"
    )
    if not done.converged:
        message += " (stopped before converging; Minimize again to go on)"
    return builder.Edited(done.structure, [], message)
