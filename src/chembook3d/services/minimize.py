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
UFF has no metal to push other atoms off, so atoms not bonded to a metal or its ligand atoms are
kept from coming closer to it than 3.0 Å (H 2.6 Å), or than they already are.

With "relax" (D146, `metal_shapes`), each metal's bonds go to UFF's lengths and the angles
between its ligands to the nearest ideal shape, its donors facing it, with no torsions, so the
ligands turn; a metal with an η ligand or not two to six bonds is held as before.

Minimize again stays put (D145): the bonds, the metals' shape and how close atoms may come to a
metal are taken from the coordinates before the first Minimize since the atoms last changed
otherwise (`Structure.minimized_from`), not from the minimized ones, so the next run starts at
the minimum of the same function.
"""

from dataclasses import dataclass, field
from itertools import combinations

import numpy as np
from rdkit import Chem, RDLogger
from rdkit.Chem import rdDetermineBonds, rdForceFieldHelpers
from rdkit.Geometry import Point3D

from chembook3d.services import builder, geometry, metal_shapes
from chembook3d.services.builder import BuildError, Structure

# Elements UFF types reliably from bond orders; every other element is held by restraints, or
# relaxed (D146). Those in MAIN_GROUP (D146) are held when UFF cannot type them where they are.
MAIN_GROUP = {"Al", "Sb", "Bi", "Te", "Tl", "Hg"}
TYPED = {
    "H", "B", "C", "N", "O", "F", "Si", "P", "S", "Cl", "Ge", "As", "Se", "Br", "Sn", "I",
    *MAIN_GROUP,
}  # fmt: skip
# Most bonds to non-metals an atom keeps (see `_bonds`).
VALENCE = {"H": 1, "F": 1, "Cl": 1, "Br": 1, "I": 1, "O": 2, "B": 4, "C": 4, "N": 4, "Si": 4}
NEAR = 2  # bonds out from a changed atom that also move
CHARGES = (0, -1, 1, -2, 2)  # tried in turn when finding a ligand's bond orders
METAL_BOND = 500.0  # kcal/mol/Å², restraint constants
METAL_ANGLE = 100.0  # kcal/mol/rad²
METAL_TORSION = 20.0
HOLD_DISTANCE = 1e4
CLEAR = {"H": 2.6}  # Å an unbonded atom is kept from a metal; 3.0 for the others
CLEAR_HEAVY = 3.0
CLEAR_FORCE = 100.0  # kcal/mol/Å²
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
    shapes: list[str] = field(default_factory=list)  # with `relax`: "Ru 22 octahedral"
    held: list[str] = field(default_factory=list)  # with `relax`: metals held in their shape


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


def _molecule(
    structure: Structure, held: frozenset[str] = frozenset()
) -> tuple[Chem.Mol, list[set[int]], list[int]]:
    """The structure as an RDKit molecule with bond orders, its bonds, and its metals (with the
    elements `held` counted as metals)."""
    elements = structure.elements
    coords = structure.coords
    metals = [i for i, e in enumerate(elements) if e not in TYPED or e in held]
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


def _clear_metals(
    ff, structure: Structure, graph: list[set[int]], metals: list[int], push: bool
) -> None:
    """Keep atoms not bonded to a metal or its ligand atoms from coming closer to it than CLEAR
    (an agostic H already closer stays where it is), or with `push` out to CLEAR. Atoms bonded
    to its ligand atoms keep at least their distance or CLEAR (D148: the H of an η² olefin, whose
    carbons UFF types without the metal, would swing onto it)."""
    coords = structure.coords
    for m in metals:
        beyond = {x for lig in graph[m] for x in graph[lig]} - graph[m] - {m}
        for i, element in enumerate(structure.elements):
            if i == m or i in graph[m]:
                continue
            low = CLEAR.get(element, CLEAR_HEAVY)
            if not push or i in beyond:
                low = min(low, float(np.linalg.norm(coords[i] - coords[m])))
            ff.UFFAddDistanceConstraint(m, i, False, low, 1e3, CLEAR_FORCE)


def _typed(mol: Chem.Mol, metals: list[int]) -> bool:
    """Whether UFF has parameters for every atom but the metals."""
    rest = Chem.RWMol(mol)
    for m in sorted(metals, reverse=True):
        rest.RemoveAtom(m)
    return rdForceFieldHelpers.UFFHasAllMoleculeParams(rest)


def _relax_metals(
    ff, mol: Chem.Mol, structure: Structure, graph: list[set[int]], metals: list[int]
) -> tuple[list[str], list[int]]:
    """D146: pull each metal's bonds to UFF's lengths, the angles between its ligands to the
    nearest ideal shape and those at each ligand atom to its own; no torsions, so ligands turn.
    Gives the shapes found and the metals that cannot relax (held instead)."""
    coords = structure.coords
    shapes, held = [], []
    for m in metals:
        found = metal_shapes.relaxed(mol, coords, graph, set(metals), m)
        if found is None:
            held.append(m)
            continue
        shapes.append(f"{structure.elements[m]} {m + 1} {found.shape}")
        for lig, length in found.lengths.items():
            ff.UFFAddDistanceConstraint(m, lig, False, length, length, METAL_BOND)
        for (a, b), (low, high) in found.angles.items():
            ff.UFFAddAngleConstraint(a, m, b, False, low, high, METAL_ANGLE)
        for (lig, x), (low, high) in found.donors.items():
            if x not in metals:
                ff.UFFAddAngleConstraint(m, lig, x, False, low, high, METAL_ANGLE)
    return shapes, held


def _reference(structure: Structure, metals: str) -> Structure:
    """D145: the structure Minimize takes its bonds and the metals' shape from: where the
    Minimizes since the atoms last changed started, if they treated the metals the same way."""
    rows = structure.minimized_from
    if (
        rows is None
        or structure.minimized_metals != metals
        or [row[0] for row in rows] != structure.elements
    ):
        rows = structure.rows
    return Structure(rows, structure.added, structure.removed, structure.changed, structure.holds)


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


def minimize(
    structure: Structure, everything: bool = False, clear: bool = False, relax: bool = False
) -> Minimized:
    """Minimize the changed atoms and those within NEAR bonds of them (or every atom), keeping
    frozen atoms where they are and held coordinates at their values. With `clear` (a structure
    just made from a drawing, D142f), atoms not bonded to a metal or to its ligand atoms are
    pushed out to 3.0 Å (H 2.6 Å) from it, not only kept from coming closer. With `relax`
    (D146), metals relax to UFF's bond lengths and the nearest ideal shape instead of being held
    in theirs."""
    count = len(structure.rows)
    if count < 2:
        raise BuildError("There is nothing to minimize")
    how = "relax" if relax else "keep"
    reference = _reference(structure, how)
    mol, graph, metals = _molecule(reference)
    if not _typed(mol, metals) and MAIN_GROUP & set(reference.elements):
        mol, graph, metals = _molecule(reference, frozenset(MAIN_GROUP))
    conformer = mol.GetConformer()
    for i, p in enumerate(structure.coords):
        conformer.SetAtomPosition(i, Point3D(*map(float, p)))
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
    shapes: list[str] = []
    held = metals
    if relax:
        shapes, held = _relax_metals(ff, mol, reference, graph, metals)
    _hold_metals(ff, reference, graph, held)
    _clear_metals(ff, reference, graph, metals, clear)
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
    out = structure.but(rows, minimized_from=reference.rows, minimized_metals=how)
    names = [f"{structure.elements[m]} {m + 1}" for m in held] if relax else []
    return Minimized(out, len(free), largest, converged, shapes, names)


def run(structure: Structure, everything: bool = False, relax: bool = False) -> builder.Edited:
    """Minimize, with the message the window shows."""
    done = minimize(structure, everything, relax=relax)
    plural = "s" if done.moved != 1 else ""
    message = (
        f"Minimized {done.moved} atom{plural} with UFF; the largest move was {done.largest:.2f} Å"
    )
    if done.shapes:
        message += "; relaxed " + ", ".join(done.shapes)
        if any(e not in TYPED for e in structure.elements):
            # D148: UFF's lengths and angles for transition metals are rough.
            message += " (UFF is rough for transition metals: check their bonds, or keep them)"
    if done.held:
        message += f"; {', '.join(done.held)} kept in shape (an η ligand, or not two to six bonds)"
    if not done.converged:
        message += " (stopped before converging; Minimize again to go on)"
    return builder.Edited(done.structure, [], message)
