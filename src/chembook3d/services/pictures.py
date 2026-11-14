"""Claude draws a structure in 2D to check it against a paper's drawing (D233, FR-MCP-21): a
SMILES it read from a page, or a structure it built or a node holds, drawn as a PNG it sees
beside the page from `read_pdf` (D232). This stands in for a structure-recognition model
(Q59): Claude reads the drawing itself and checks its reading by eye.

RDKit draws (already a dependency); the 2D layout is the Ketcher drawer's (`redraw.layout`,
whichever of RDKit's and CoordGen's crowds the atoms least, which matters round a metal).
"""

import base64

from rdkit import Chem
from rdkit.Chem import rdMolDescriptors
from rdkit.Chem.Draw import rdMolDraw2D

from chembook3d.services import builder, minimize, redraw
from chembook3d.services.builder import BuildError, Structure

WIDTH, HEIGHT = 900, 700  # px; Claude sees up to about 1600 on the longer side
MAX_ATOMS = 300  # heavy atoms drawn


def _summary(mol: Chem.Mol, numbered: bool) -> dict:
    """What RDKit made of it, so a misreading shows in words as well: formula, charge and the
    atoms each metal is bonded to."""
    mol.UpdatePropertyCache(strict=False)
    metals = []
    for atom in mol.GetAtoms():
        if atom.GetSymbol() in minimize.TYPED or atom.GetAtomicNum() == 0:
            continue
        bonded = [_name(n, numbered) for n in sorted(atom.GetNeighbors(), key=_order)]
        metals.append({"atom": _name(atom, numbered), "bonded": bonded})
    answer: dict = {
        "formula": rdMolDescriptors.CalcMolFormula(mol),
        "charge": Chem.GetFormalCharge(mol),
        "heavy_atoms": mol.GetNumHeavyAtoms(),
    }
    if metals:
        answer["metals"] = metals
    return answer


def _order(atom: Chem.Atom) -> int:
    return int(atom.GetProp("atomNote")) if atom.HasProp("atomNote") else atom.GetIdx()


def _name(atom: Chem.Atom, numbered: bool) -> str:
    if numbered and atom.HasProp("atomNote"):
        return f"{atom.GetSymbol()}{atom.GetProp('atomNote')}"
    return atom.GetSymbol()


def _png(mol: Chem.Mol, legend: str) -> dict:
    if mol.GetNumAtoms() > MAX_ATOMS:
        raise BuildError(f"{mol.GetNumAtoms()} atoms are more than {MAX_ATOMS} to draw")
    drawer = rdMolDraw2D.MolDraw2DCairo(WIDTH, HEIGHT)
    options = drawer.drawOptions()
    options.addStereoAnnotation = True
    options.annotationFontScale = 0.7
    options.legendFontSize = 18
    try:
        rdMolDraw2D.PrepareAndDrawMolecule(drawer, mol, legend=legend, kekulize=False)
    except (ValueError, RuntimeError) as exc:
        raise BuildError(f"RDKit could not draw it: {exc}") from exc
    drawer.FinishDrawing()
    return {
        "width": WIDTH,
        "height": HEIGHT,
        "png": base64.b64encode(drawer.GetDrawingText()).decode("ascii"),
    }


def from_smiles(text: str, numbered: bool = False, legend: str = "") -> dict:
    """A SMILES drawn as written; numbers from 1 in the SMILES's order of atoms."""
    text = text.strip()
    if not text:
        raise BuildError("Give a SMILES")
    mol = Chem.MolFromSmiles(text)
    if mol is None:
        loose = Chem.MolFromSmiles(text, sanitize=False)
        if loose is None:
            raise BuildError(f"“{text}” is not a SMILES RDKit can read")
        problems = Chem.DetectChemistryProblems(loose)
        reason = problems[0].Message() if problems else "check its charges and bonds"
        raise BuildError(f"RDKit reads “{text}” but not as a molecule: {reason}")
    if numbered:
        for atom in mol.GetAtoms():
            atom.SetProp("atomNote", str(atom.GetIdx() + 1))
    redraw.layout(mol)
    answer = {"smiles": Chem.MolToSmiles(mol), **_summary(mol, numbered)}
    answer["images"] = [_png(mol, legend)]
    return answer


def from_structure(structure: Structure, numbered: bool = False, legend: str = "") -> dict:
    """A 3D structure drawn as the Ketcher drawer's Draw from this structure draws it (D143):
    bonds as Minimize finds them, hydrogens left out; numbers are the structure's, from 1, as
    build_structure counts its atoms."""
    mol = redraw.flat(structure, numbered=numbered)
    answer: dict = {}
    try:
        answer["smiles"] = Chem.MolToSmiles(mol)
    except (ValueError, RuntimeError):
        pass
    answer.update(_summary(mol, numbered))
    answer["formula"] = builder.formula(structure.rows)  # with its hydrogens, which are not drawn
    answer["images"] = [_png(mol, legend)]
    return answer
