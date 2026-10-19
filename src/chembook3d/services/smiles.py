"""Structures from SMILES for the Edit structure window (D141): RDKit adds the hydrogens, makes
3D coordinates (ETKDG, from a fixed seed so the same SMILES gives the same structure) and
cleans them up with MMFF94 or UFF where it has the parameters. A metal's shape is only a rough
guess; the window says so.

A fragment's SMILES names where it attaches with one `*` (as `*c1ccccc1` for phenyl); the `*`
stands for the atom it replaces.

A drawing from the Ketcher drawer (D142) comes as a MOL file and is made 3D the same way: its
charges and the stereo its wedges and double bonds show are kept, and an attachment point (or an
`R#` or `*` atom) makes it a fragment.
"""

from dataclasses import dataclass

import numpy as np
from rdkit import Chem, RDLogger
from rdkit.Chem import rdDistGeom, rdForceFieldHelpers

from chembook3d.services import builder, minimize
from chembook3d.services.builder import BuildError, Rows, Structure

MAX_ATOMS = 300  # with hydrogens
SEED = 0xC0FFEE
STEPS = 2000
ORGANIC = {"H", "B", "C", "N", "O", "F", "Si", "P", "S", "Cl", "Se", "Br", "I"}

RDLogger.DisableLog("rdApp.*")


@dataclass
class Built:
    rows: Rows
    attach: int | None  # a fragment's attaching atom (the `*`'s neighbour), in rows
    dummy: np.ndarray | None  # where the `*` was: the direction of the atom it replaces
    metals: bool  # its shape round a metal is only a guess


def build(smiles: str, fragment: bool = False) -> Built:
    text = smiles.strip()
    if not text:
        raise BuildError("Give a SMILES")
    mol = Chem.MolFromSmiles(text)
    if mol is None:
        raise BuildError(f"“{text}” is not a SMILES RDKit can read")
    dummies = [a.GetIdx() for a in mol.GetAtoms() if a.GetAtomicNum() == 0]
    if fragment:
        if len(dummies) != 1 or mol.GetAtomWithIdx(dummies[0]).GetDegree() != 1:
            raise BuildError("A fragment's SMILES needs one * where it attaches, as in *c1ccccc1")
    elif dummies:
        raise BuildError("Leave out the * here; it marks where a fragment attaches")
    return _built(mol, dummies)


def from_molfile(text: str) -> Built:
    """A Ketcher drawing (D142): a molecule, or a fragment when it has one attachment point."""
    mol = Chem.MolFromMolBlock(text, removeHs=False)
    if mol is None:
        mol = Chem.MolFromMolBlock(text, sanitize=False, removeHs=False)
        if mol is None:
            raise BuildError("RDKit could not read the drawing")
        problems = Chem.DetectChemistryProblems(mol)
        raise BuildError(
            "RDKit could not read the drawing: "
            + (problems[0].Message() if problems else "check its charges and bonds")
        )
    if not mol.GetNumAtoms():
        raise BuildError("Draw a molecule first")
    editable = Chem.RWMol(mol)
    for atom in mol.GetAtoms():
        # An attachment point is an M  APO on its atom: 1 or 2, or 3 for both (-1 here).
        points = atom.GetIntProp("molAttchpt") if atom.HasProp("molAttchpt") else 0
        for _ in range(2 if points == -1 else int(points > 0)):
            dummy = editable.AddAtom(Chem.Atom(0))
            editable.AddBond(atom.GetIdx(), dummy, Chem.BondType.SINGLE)
    mol = editable.GetMol()
    mol.RemoveAllConformers()  # the 2D drawing's coordinates; its stereo is already assigned
    dummies = [a.GetIdx() for a in mol.GetAtoms() if a.GetAtomicNum() == 0]
    if len(dummies) > 1 or any(mol.GetAtomWithIdx(d).GetDegree() != 1 for d in dummies):
        raise BuildError(
            "A drawn fragment needs one attachment point (or one R# atom bonded to one atom)"
        )
    if mol.GetNumAtoms() == len(dummies):
        raise BuildError("Draw a molecule first")
    return _built(mol, dummies)


def _built(mol: Chem.Mol, dummies: list[int]) -> Built:
    if dummies:
        # Made an H for the coordinates: the atom the fragment replaces.
        editable = Chem.RWMol(mol)
        editable.GetAtomWithIdx(dummies[0]).SetAtomicNum(1)
        mol = editable.GetMol()
        Chem.SanitizeMol(mol)
    mol = Chem.AddHs(mol)
    if mol.GetNumAtoms() > MAX_ATOMS:
        raise BuildError(f"That makes more than {MAX_ATOMS} atoms")
    params = rdDistGeom.ETKDGv3()
    params.randomSeed = SEED
    if rdDistGeom.EmbedMolecule(mol, params) != 0:
        params.useRandomCoords = True
        if rdDistGeom.EmbedMolecule(mol, params) != 0:
            raise BuildError(
                "RDKit could not make 3D coordinates for this; build it in parts and bond them here"
            )
    if rdForceFieldHelpers.MMFFHasAllMoleculeParams(mol):
        rdForceFieldHelpers.MMFFOptimizeMolecule(mol, maxIters=STEPS)
    elif rdForceFieldHelpers.UFFHasAllMoleculeParams(mol):
        rdForceFieldHelpers.UFFOptimizeMolecule(mol, maxIters=STEPS)
    coords = mol.GetConformer().GetPositions()
    elements = [a.GetSymbol() for a in mol.GetAtoms()]
    metals = any(e not in ORGANIC for e in elements)
    if metals:
        coords = _metal_bonds(mol, elements, coords)
    if not dummies:
        rows = [[e, *(float(c) for c in p)] for e, p in zip(elements, coords, strict=True)]
        return Built(rows, None, None, metals)
    (dummy,) = dummies
    attach = mol.GetAtomWithIdx(dummy).GetNeighbors()[0].GetIdx()
    keep = [i for i in range(len(elements)) if i != dummy]
    rows = [[elements[i], *(float(c) for c in coords[i])] for i in keep]
    return Built(rows, keep.index(attach), coords[dummy].astype(float), metals)


def _metal_bonds(mol: Chem.Mol, elements: list[str], coords: np.ndarray) -> np.ndarray:
    """ETKDG leaves a metal's bonds far too long. Each is set to the sum of the covalent radii,
    moving the ligand's side as Set does, and Minimize (D140) then cleans up with the metal held
    in that shape (D142d). The shape round the metal stays a rough guess."""
    bonds = [
        tuple(sorted((b.GetBeginAtomIdx(), b.GetEndAtomIdx())))
        for b in mol.GetBonds()
        if {elements[b.GetBeginAtomIdx()], elements[b.GetEndAtomIdx()]} - ORGANIC
    ]
    rows = [[e, *(float(c) for c in p)] for e, p in zip(elements, coords, strict=True)]
    structure = Structure(rows, added=bonds)
    for a, b in bonds:
        ligand = b if elements[a] not in ORGANIC else a
        target = builder._bond_length(elements[a], elements[b])
        try:
            structure = builder.set_distance(structure, [a, b], target, ligand).structure
        except BuildError:
            continue  # left as ETKDG put it
    try:
        done = minimize.minimize(Structure(structure.rows), everything=True)
    except BuildError:
        return structure.coords
    return done.structure.coords


def as_fragment(built: Built) -> list[tuple[str, np.ndarray]]:
    """A fragment as `builder._fragment` gives one: the attaching atom first at the origin, the
    atom it replaces along -x."""
    assert built.attach is not None and built.dummy is not None
    coords = np.array([row[1:4] for row in built.rows], dtype=float)
    origin = coords[built.attach]
    turn = builder._frame(built.dummy - origin, np.array([-1.0, 0.0, 0.0]))
    placed = (coords - origin) @ turn.T
    order = [built.attach, *(i for i in range(len(coords)) if i != built.attach)]
    return [(built.rows[i][0], placed[i]) for i in order]
