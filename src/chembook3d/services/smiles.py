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
from rdkit.DistanceGeometry import DoTriangleSmoothing

from chembook3d.services import atom_matching, builder, geometry, minimize
from chembook3d.services.builder import BuildError, Rows, Structure

MAX_ATOMS = 300  # with hydrogens
SEED = 0xC0FFEE
STEPS = 2000
TRIES = 10  # seeds tried for coordinates with only the drawn bonds
LIMIT_SLACK = 0.05  # Å either side of a metal bond's length, and above a raised lower bound
CLEAR_BOND = 1.1  # atoms not bonded to a metal: at least this times the 3D view's bond limit
LIGAND_ANGLE = 75  # degrees: the least angle between two ligand atoms of a metal
ORGANIC = {"H", "B", "C", "N", "O", "F", "Si", "P", "S", "Cl", "Se", "Br", "I"}
TRANS = 150  # two bonds to a metal drawn at least this far apart are trans (D142f)
SPINS = 36  # turns tried for a ligand about its bond to the metal

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
    mol = read_drawing(text)
    dummies = [a.GetIdx() for a in mol.GetAtoms() if a.GetAtomicNum() == 0]
    if len(dummies) > 1 or any(mol.GetAtomWithIdx(d).GetDegree() != 1 for d in dummies):
        raise BuildError(
            "A drawn fragment needs one attachment point (or one R# atom bonded to one atom)"
        )
    if mol.GetNumAtoms() == len(dummies):
        raise BuildError("Draw a molecule first")
    return _built(mol, dummies)


def read_drawing(text: str) -> Chem.Mol:
    """Ketcher's MOL file as a molecule without coordinates (its stereo already read from the
    wedges), each attachment point as a `*` atom bonded to its atom."""
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
    if mol.GetNumConformers():
        # Where each atom was drawn: which bonds to a metal are trans (D142f).
        for atom, (x, y, _) in zip(mol.GetAtoms(), mol.GetConformer().GetPositions(), strict=True):
            atom.SetDoubleProp("drawn_x", float(x))
            atom.SetDoubleProp("drawn_y", float(y))
    mol.RemoveAllConformers()  # the 2D drawing's coordinates; its stereo is already assigned
    return mol


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
    elements = [a.GetSymbol() for a in mol.GetAtoms()]
    metals = any(e not in ORGANIC for e in elements)
    coords = _coordinates(mol, elements, metals)
    if not dummies:
        rows = [[e, *(float(c) for c in p)] for e, p in zip(elements, coords, strict=True)]
        return Built(rows, None, None, metals)
    (dummy,) = dummies
    attach = mol.GetAtomWithIdx(dummy).GetNeighbors()[0].GetIdx()
    keep = [i for i in range(len(elements)) if i != dummy]
    rows = [[elements[i], *(float(c) for c in coords[i])] for i in keep]
    return Built(rows, keep.index(attach), coords[dummy].astype(float), metals)


def _coordinates(mol: Chem.Mol, elements: list[str], metals: bool) -> np.ndarray:
    """3D coordinates whose bonds, as the 3D view finds them, are the ones drawn (T-BLD-23).
    RDKit's own distance bounds round a metal are loose, so ETKDG can fold a chelate's ring onto
    the metal (RDKit 2026.09.2 did so for the drawn CAAC Hoveyda precatalyst); `_metal_limits`
    tightens them. A ligand set on its corner (`_placed`) can still land on another ligand's H,
    so a guess whose bonds are not the drawn ones is made again from the next seed, up to TRIES,
    and the first is kept when none comes out clean."""
    drawn = {tuple(sorted((b.GetBeginAtomIdx(), b.GetEndAtomIdx()))) for b in mol.GetBonds()}
    limits = _metal_limits(mol, elements) if metals else None
    first = None
    for attempt in range(TRIES):
        if not _embedded(mol, SEED + attempt, limits, fallback=attempt == 0):
            if attempt == 0:
                raise BuildError(
                    "RDKit could not make 3D coordinates for this; build it in parts and bond "
                    "them here"
                )
            continue
        coords = _cleaned(mol, elements, metals)
        if first is None:
            first = coords
        adjacency = atom_matching.bonds(coords, elements)
        found = {(int(i), int(j)) for i, j in zip(*np.nonzero(np.triu(adjacency)), strict=True)}
        if found == drawn:
            return coords
    assert first is not None
    return first


def _metal_limits(mol: Chem.Mol, elements: list[str]) -> np.ndarray | None:
    """RDKit's distance bounds with each metal's bonds at the sum of the covalent radii (as
    `_metal_bonds` sets them, D142d), atoms not bonded to it kept CLEAR_BOND times farther than
    the 3D view's bond limit, and two of its ligand atoms (not bonded to each other or to one
    atom) at least LIGAND_ANGLE apart round it. None when the bounds cannot be smoothed."""
    limits = rdDistGeom.GetMoleculeBoundsMatrix(mol)
    radii = [atom_matching.RADII.get(e, atom_matching.DEFAULT_RADIUS) for e in elements]

    def at_least(i: int, j: int, low: float, high: float | None = None) -> None:
        a, b = min(i, j), max(i, j)  # the upper bound is [a][b], the lower [b][a]
        limits[b][a] = max(limits[b][a], low)
        limits[a][b] = max(limits[a][b], low + LIMIT_SLACK) if high is None else high

    for metal in (a for a in mol.GetAtoms() if a.GetSymbol() not in ORGANIC):
        m = metal.GetIdx()
        ligands = [a.GetIdx() for a in metal.GetNeighbors()]
        lengths = {x: builder._bond_length(elements[m], elements[x]) for x in ligands}
        for x in range(len(elements)):
            if x == m:
                continue
            if x in lengths:
                a, b = min(m, x), max(m, x)
                limits[b][a] = lengths[x] - LIMIT_SLACK
                limits[a][b] = lengths[x] + LIMIT_SLACK
            else:
                at_least(m, x, CLEAR_BOND * atom_matching.BOND_SCALE * (radii[m] + radii[x]))
        for i, x in enumerate(ligands):
            near_x = {a.GetIdx() for a in mol.GetAtomWithIdx(x).GetNeighbors()} - {m}
            for y in ligands[i + 1 :]:
                near_y = {a.GetIdx() for a in mol.GetAtomWithIdx(y).GetNeighbors()} - {m}
                if y in near_x or near_x & near_y:
                    continue  # an η ligand's atoms, or a small bite (κ² carboxylate)
                lx, ly = lengths[x], lengths[y]
                low = np.sqrt(lx * lx + ly * ly - 2 * lx * ly * np.cos(np.radians(LIGAND_ANGLE)))
                at_least(x, y, float(low))
    if not DoTriangleSmoothing(limits):
        return None
    return limits


def _cleaned(mol: Chem.Mol, elements: list[str], metals: bool) -> np.ndarray:
    """The embedded coordinates cleaned up by MMFF94 or UFF, each metal in its drawn shape."""
    if rdForceFieldHelpers.MMFFHasAllMoleculeParams(mol):
        rdForceFieldHelpers.MMFFOptimizeMolecule(mol, maxIters=STEPS)
    elif rdForceFieldHelpers.UFFHasAllMoleculeParams(mol):
        rdForceFieldHelpers.UFFOptimizeMolecule(mol, maxIters=STEPS)
    coords = mol.GetConformer().GetPositions()
    shape = _metal_shape(mol)
    if shape is not None:
        coords = _placed(mol, coords, *shape)
    if metals:
        coords = _metal_bonds(mol, elements, coords)
    return coords


def _embedded(
    mol: Chem.Mol, seed: int = SEED, limits: np.ndarray | None = None, fallback: bool = True
) -> bool:
    """ETKDG from the seed, within `limits` when given; with `fallback`, then with RDKit's own
    bounds and from random coordinates."""
    tries: list[tuple[np.ndarray | None, bool]] = [(limits, False)]
    if fallback:
        tries += [(None, False)] if limits is not None else []
        tries += [(None, True)]
    for bounds, random in tries:
        params = rdDistGeom.ETKDGv3()
        params.randomSeed = seed
        params.useRandomCoords = random
        if bounds is not None:
            params.SetBoundsMat(bounds)
        try:
            if rdDistGeom.EmbedMolecule(mol, params) == 0:
                return True
        except (ValueError, RuntimeError):
            pass
    return False


def _metal_shape(mol: Chem.Mol) -> tuple[int, list[int], list[np.ndarray]] | None:
    """D142f: the shape round the metal as drawn. Bonds drawn on opposite sides of a metal (at
    least TRANS degrees apart in 2D) go trans, along one axis of an octahedron; the other bonds
    take its free corners, cis to them. A metal with no bonds drawn trans, more than six, or a
    ligand bonded through two neighbouring atoms (η²) is left as ETKDG puts it; so is any metal
    after the first."""
    for metal in mol.GetAtoms():
        if metal.GetSymbol() in ORGANIC or not metal.HasProp("drawn_x"):
            continue
        donors = [a for a in metal.GetNeighbors() if a.HasProp("drawn_x")]
        if not 2 <= len(donors) <= 6 or len(donors) != metal.GetDegree():
            continue
        if any(mol.GetBondBetweenAtoms(a.GetIdx(), b.GetIdx()) for a in donors for b in donors):
            continue
        corners = _corners(metal, donors)
        if corners is not None:
            return metal.GetIdx(), [a.GetIdx() for a in donors], corners
    return None


def _placed(
    mol: Chem.Mol, coords: np.ndarray, metal: int, donors: list[int], corners: list[np.ndarray]
) -> np.ndarray:
    """Each ligand bonded through one atom moved rigidly so that atom sits on its corner round
    the metal, at the covalent radii sum, in the turn of the octahedron closest to where ETKDG
    put the ligands; a chelating ligand stays where ETKDG put it and the octahedron is turned
    to it. Minimize (D140) then holds the metal in that shape (`_metal_bonds`)."""
    coords = coords.copy()
    centre = coords[metal]
    symbol = mol.GetAtomWithIdx(metal).GetSymbol()
    lengths = [builder._bond_length(symbol, mol.GetAtomWithIdx(d).GetSymbol()) for d in donors]
    ideal = np.array([c * length for c, length in zip(corners, lengths, strict=True)])
    metals = {a.GetIdx() for a in mol.GetAtoms() if a.GetSymbol() not in ORGANIC}
    ligands = {d: _reached(mol, d, metals) for d in donors}
    single = [i for i, d in enumerate(donors) if not ligands[d] & (set(donors) - {d})]
    fitted = [i for i in range(len(donors)) if i not in single] or list(range(len(donors)))
    turn, _ = geometry._fit(ideal[fitted], coords[[donors[i] for i in fitted]] - centre, True)
    for i in single:
        donor, ligand = donors[i], sorted(ligands[donors[i]])
        target = ideal[i] @ turn + centre
        spin = _rotation(builder._unit(coords[donor] - centre), builder._unit(target - centre))
        coords[ligand] = (coords[ligand] - centre) @ spin.T + centre
        coords[ligand] += target - coords[donor]
        coords = _face_metal(mol, coords, metal, donor)
        coords = _clear(coords, ligand, donor, centre)
    return coords


def _clear(coords: np.ndarray, ligand: list[int], donor: int, centre: np.ndarray) -> np.ndarray:
    """The ligand turned about its bond to the metal to where it keeps furthest from the rest."""
    rest = np.delete(coords, ligand, axis=0)
    axis = builder._unit(coords[donor] - centre)
    best, best_gap = coords, -1.0
    for step in range(SPINS):
        turned = coords.copy()
        spin = _spin(axis, 2 * np.pi * step / SPINS)
        turned[ligand] = (coords[ligand] - coords[donor]) @ spin.T + coords[donor]
        gap = float(np.linalg.norm(turned[ligand][:, None] - rest[None], axis=2).min())
        if gap > best_gap + 1e-6:
            best, best_gap = turned, gap
    return best


def _spin(axis: np.ndarray, angle: float) -> np.ndarray:
    cross = np.array([[0, -axis[2], axis[1]], [axis[2], 0, -axis[0]], [-axis[1], axis[0], 0]])
    return np.eye(3) + np.sin(angle) * cross + (1 - np.cos(angle)) * cross @ cross


def _face_metal(mol: Chem.Mol, coords: np.ndarray, metal: int, donor: int) -> np.ndarray:
    """ETKDG leaves the angle at an atom bonded to a metal almost free, so the hydrogens of an
    NH3 can fold onto the metal. A ligand bonded through one atom is turned rigidly about that
    atom until the side its bonds leave open (where a lone pair sits) faces the metal."""
    bonded = [n.GetIdx() for n in mol.GetAtomWithIdx(donor).GetNeighbors() if n.GetIdx() != metal]
    if not bonded:
        return coords
    open_side = -np.sum([builder._unit(coords[b] - coords[donor]) for b in bonded], axis=0)
    if np.linalg.norm(open_side) < 0.1:
        return coords  # its bonds leave no side open: left as it is
    metals = {a.GetIdx() for a in mol.GetAtoms() if a.GetSymbol() not in ORGANIC}
    moving = sorted(_reached(mol, donor, metals) - {donor})
    turn = _rotation(builder._unit(open_side), builder._unit(coords[metal] - coords[donor]))
    coords = coords.copy()
    coords[moving] = (coords[moving] - coords[donor]) @ turn.T + coords[donor]
    return coords


def _reached(mol: Chem.Mol, start: int, stops: set[int]) -> set[int]:
    seen, todo = {start}, [start]
    while todo:
        for n in mol.GetAtomWithIdx(todo.pop()).GetNeighbors():
            if n.GetIdx() not in seen and n.GetIdx() not in stops:
                seen.add(n.GetIdx())
                todo.append(n.GetIdx())
    return seen


def _rotation(u: np.ndarray, v: np.ndarray) -> np.ndarray:
    """The rotation taking unit vector u onto unit vector v."""
    axis = np.cross(u, v)
    sine, cosine = float(np.linalg.norm(axis)), float(u @ v)
    if sine < 1e-9:
        if cosine > 0:
            return np.eye(3)
        other = np.eye(3)[int(np.argmin(np.abs(u)))]
        axis = builder._unit(np.cross(u, other))
        return 2 * np.outer(axis, axis) - np.eye(3)
    k = axis / sine
    cross = np.array([[0, -k[2], k[1]], [k[2], 0, -k[0]], [-k[1], k[0], 0]])
    return np.eye(3) + sine * cross + (1 - cosine) * cross @ cross


def _corners(metal: Chem.Atom, donors: list[Chem.Atom]) -> list[np.ndarray] | None:
    """Each donor's corner of the octahedron round the metal: trans pairs on the x, y, z axes in
    turn, then the others on the free corners, opposite each other only when they must be."""
    centre = _drawn(metal)
    directions = [_drawn(a) - centre for a in donors]
    if any(np.linalg.norm(d) < 1e-6 for d in directions):
        return None
    count = len(donors)
    angle = {
        (i, j): _drawn_angle(directions[i], directions[j])
        for i in range(count)
        for j in range(count)
        if i != j
    }
    trans: list[tuple[int, int]] = []
    paired: set[int] = set()
    for i, j in sorted(angle, key=lambda k: -angle[k]):
        if angle[i, j] >= TRANS and i not in paired and j not in paired:
            trans.append((i, j))
            paired |= {i, j}
    if not trans:
        return None
    axes = np.eye(3)
    corners: list[np.ndarray | None] = [None] * count
    for axis, (i, j) in enumerate(trans):
        corners[i], corners[j] = axes[axis], -axes[axis]
    free = [axes[axis] for axis in range(len(trans), 3)]
    free += [-axes[axis] for axis in range(len(trans), 3)]
    for i in (i for i in range(count) if i not in paired):
        # The free corner whose opposite holds the donor drawn furthest round from this one.
        def score(corner: np.ndarray, i: int = i) -> float:
            across = [
                j for j in range(count) if corners[j] is not None and corners[j] @ corner < -0.5
            ]
            return -sum(angle[i, j] for j in across) if across else 0.0

        best = min(free, key=score) if any(score(c) for c in free) else free[0]
        corners[i] = best
        free = [c for c in free if c is not best]
    return [c for c in corners if c is not None]


def _drawn(atom: Chem.Atom) -> np.ndarray:
    return np.array([atom.GetDoubleProp("drawn_x"), atom.GetDoubleProp("drawn_y")])


def _drawn_angle(u: np.ndarray, v: np.ndarray) -> float:
    cosine = float(u @ v / (np.linalg.norm(u) * np.linalg.norm(v)))
    return float(np.degrees(np.arccos(np.clip(cosine, -1.0, 1.0))))


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
        done = minimize.minimize(Structure(structure.rows), everything=True, clear=True)
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
