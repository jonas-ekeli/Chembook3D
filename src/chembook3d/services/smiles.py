"""Structures from SMILES for the Edit structure window (D141): RDKit adds the hydrogens, makes
3D coordinates (ETKDG, from a fixed seed so the same SMILES gives the same structure) and
cleans them up with MMFF94 or UFF where it has the parameters. A metal's shape is only a rough
guess; the window says so.

A fragment's SMILES names where it attaches with one `*` (as `*c1ccccc1` for phenyl); the `*`
stands for the atom it replaces.
"""

from dataclasses import dataclass

import numpy as np
from rdkit import Chem, RDLogger
from rdkit.Chem import rdDistGeom, rdForceFieldHelpers

from chembook3d.services import builder
from chembook3d.services.builder import BuildError, Rows

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
                "RDKit could not make 3D coordinates for this SMILES; build it in parts and "
                "bond them here"
            )
    if rdForceFieldHelpers.MMFFHasAllMoleculeParams(mol):
        rdForceFieldHelpers.MMFFOptimizeMolecule(mol, maxIters=STEPS)
    elif rdForceFieldHelpers.UFFHasAllMoleculeParams(mol):
        rdForceFieldHelpers.UFFOptimizeMolecule(mol, maxIters=STEPS)
    coords = mol.GetConformer().GetPositions()
    elements = [a.GetSymbol() for a in mol.GetAtoms()]
    metals = any(e not in ORGANIC for e in elements)
    if not dummies:
        rows = [[e, *(float(c) for c in p)] for e, p in zip(elements, coords, strict=True)]
        return Built(rows, None, None, metals)
    (dummy,) = dummies
    attach = mol.GetAtomWithIdx(dummy).GetNeighbors()[0].GetIdx()
    keep = [i for i in range(len(elements)) if i != dummy]
    rows = [[elements[i], *(float(c) for c in coords[i])] for i in keep]
    return Built(rows, keep.index(attach), coords[dummy].astype(float), metals)


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
