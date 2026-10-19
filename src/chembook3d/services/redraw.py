"""Draw from this structure (D143): the Edit structure window's structure goes into the Ketcher
drawer in 2D, and the drawing comes back onto it with every unchanged atom where it was in 3D.

To 2D: the structure as Minimize sees it (D140: bond orders found per ligand), a neutral ligand
drawn with a dative bond to its metal and an anionic one with a plain bond that takes its
charge; hydrogens left out, wedges from the 3D handedness.

Back: the drawing is matched to the structure by their largest common piece (element to
element, any bond, ring atoms to ring atoms), again for what is left, so separate molecules and
pieces cut apart are found too. A matched atom keeps its number and its place, and so do its
hydrogens when it has as many as before; one whose bonds, charge or hydrogens changed counts as
changed. New atoms are made 3D with the matched atoms round them held where they are (at least
three, two bonds out or further), and fitted on; a new molecule on its own goes beside the
structure. Unmatched atoms of the structure are deleted. Minimize then cleans up what changed
and two bonds round it.
"""

import re

import numpy as np
from rdkit import Chem
from rdkit.Chem import rdDepictor, rdDistGeom, rdFMCS
from rdkit.Geometry import Point3D

from chembook3d.services import builder, geometry, minimize, smiles
from chembook3d.services.builder import BuildError, Edited, Hold, Structure

MCS_SECONDS = 5  # for each piece matched
PIECES = 10  # pieces matched one after another, at most
AROUND = 2  # bonds out from the new atoms held while they are made 3D
ANCHORS = 3  # at least this many held atoms, going further out when needed
SEED = smiles.SEED
SIMILAR = {Chem.BondType.SINGLE, Chem.BondType.DOUBLE, Chem.BondType.AROMATIC}


def _drawable(structure: Structure) -> Chem.Mol:
    """The structure with its hydrogens, atom for atom, with its bonds as they are drawn."""
    mol, graph, metals = minimize._molecule(structure)
    metal = set(metals)
    organic = [
        set() if i in metal else {j for j in graph[i] if j not in metal}
        for i in range(mol.GetNumAtoms())
    ]
    charge: dict[int, int] = {}
    for start in range(mol.GetNumAtoms()):
        if start not in metal and start not in charge:
            piece = builder._reach(organic, start, set())
            total = sum(mol.GetAtomWithIdx(a).GetFormalCharge() for a in piece)
            charge.update(dict.fromkeys(piece, total))
    editable = Chem.RWMol(mol)
    for bond in mol.GetBonds():
        i, j = bond.GetBeginAtomIdx(), bond.GetEndAtomIdx()
        if (i in metal) == (j in metal):
            continue
        ligand, centre = (j, i) if i in metal else (i, j)
        atom = editable.GetAtomWithIdx(ligand)
        if charge[ligand] < 0 and atom.GetFormalCharge() < 0:
            atom.SetFormalCharge(atom.GetFormalCharge() + 1)  # the bond takes the charge
        else:
            editable.RemoveBond(i, j)
            editable.AddBond(ligand, centre, Chem.BondType.DATIVE)
    out = editable.GetMol()
    out.UpdatePropertyCache(strict=False)
    _radicals(out, metal)
    Chem.FastFindRings(out)
    return out


def _radicals(mol: Chem.Mol, metals: set[int]) -> None:
    """A drawing gives each atom the hydrogens its valence asks for. Where the structure has
    fewer (an O on a metal with none), the missing ones are drawn as radical electrons, so the
    drawing reads back as it is."""
    for atom in mol.GetAtoms():
        atom.SetIntProp("index", atom.GetIdx())
    # RemoveHs keeps a hydrogen bonded to a metal (a hydride), so atoms are matched by index.
    probe = Chem.RemoveHs(mol, sanitize=False)
    for atom in probe.GetAtoms():
        atom.SetNoImplicit(False)
        atom.SetNumRadicalElectrons(
            mol.GetAtomWithIdx(atom.GetIntProp("index")).GetNumRadicalElectrons()
        )
    probe.UpdatePropertyCache(strict=False)
    for atom in probe.GetAtoms():
        index = atom.GetIntProp("index")
        if index in metals or atom.GetAtomicNum() == 1:
            continue
        original = mol.GetAtomWithIdx(index)
        has = sum(1 for n in original.GetNeighbors() if n.GetAtomicNum() == 1)
        missing = atom.GetNumImplicitHs() - has
        if missing > 0:
            original.SetNumRadicalElectrons(original.GetNumRadicalElectrons() + missing)


def to_drawing(structure: Structure) -> str:
    """The structure as a 2D MOL file for Ketcher (V3000, which has dative bonds)."""
    if not structure.rows:
        raise BuildError("There is no structure to draw from")
    mol = _drawable(structure)
    try:
        Chem.AssignStereochemistryFrom3D(mol)
    except (ValueError, RuntimeError):
        pass  # drawn without wedges
    heavy = Chem.RemoveHs(mol, sanitize=False)
    heavy.UpdatePropertyCache(strict=False)
    _layout(heavy)
    try:
        Chem.WedgeMolBonds(heavy, heavy.GetConformer())
    except (ValueError, RuntimeError):
        pass
    metals = {a.GetIdx() + 1 for a in heavy.GetAtoms() if a.GetSymbol() not in minimize.TYPED}
    return "\n".join(_no_valence(line, metals) for line in Chem.MolToV3KMolBlock(heavy).split("\n"))


def _crowding(mol: Chem.Mol) -> tuple[int, float]:
    """Pairs of unbonded atoms drawn closer than 0.8 bond lengths, and the closest such pair."""
    points = mol.GetConformer().GetPositions()[:, :2]
    d = np.linalg.norm(points[:, None] - points[None], axis=2)
    np.fill_diagonal(d, np.inf)
    for bond in mol.GetBonds():
        i, j = bond.GetBeginAtomIdx(), bond.GetEndAtomIdx()
        d[i, j] = d[j, i] = np.inf
    return int((d < 0.8).sum()) // 2, -float(d.min()) if len(d) > 1 else 0.0


def _layout(mol: Chem.Mol) -> None:
    """RDKit's own 2D layout or CoordGen's, whichever leaves fewer atoms on top of each other
    (round a metal one or the other often crowds)."""
    tries = []
    for coordgen in (False, True):
        trial = Chem.Mol(mol)
        rdDepictor.SetPreferCoordGen(coordgen)
        try:
            rdDepictor.Compute2DCoords(trial)
        except (ValueError, RuntimeError):
            continue
        finally:
            rdDepictor.SetPreferCoordGen(False)
        tries.append((_crowding(trial), coordgen, trial))
    if not tries:
        raise BuildError("RDKit could not draw this structure in 2D")
    best = min(tries, key=lambda t: t[:2])[2]
    mol.RemoveAllConformers()
    mol.AddConformer(best.GetConformer(), assignId=True)


def _no_valence(line: str, metals: set[int]) -> str:
    """A metal's atom line without the valence RDKit writes: Ketcher would fill a metal up to
    that valence with hydrogens, not counting its dative bonds."""
    parts = line.split()
    if line.startswith("M  V30 ") and len(parts) > 3 and parts[2].isdigit():
        if int(parts[2]) in metals and parts[3] not in ("BEGIN", "END"):
            return re.sub(r" VAL=-?\d+", "", line)
    return line


def _heavy_part(mol: Chem.Mol, leave: set[int]) -> tuple[Chem.Mol, list[int]]:
    """The heavy atoms not in `leave` as a molecule of their own, and their indices in `mol`."""
    keep = [a.GetIdx() for a in mol.GetAtoms() if a.GetAtomicNum() != 1 and a.GetIdx() not in leave]
    part = Chem.RWMol(mol)
    for a in sorted(set(range(mol.GetNumAtoms())) - set(keep), reverse=True):
        part.RemoveAtom(a)
    out = part.GetMol()
    out.UpdatePropertyCache(strict=False)
    Chem.FastFindRings(out)
    return out, keep


def _match(old: Chem.Mol, new: Chem.Mol) -> dict[int, int]:
    """Heavy atoms of the drawing (new) matched to the structure's (old), piece by piece."""
    pairs: dict[int, int] = {}
    params = rdFMCS.MCSParameters()
    params.AtomTyper = rdFMCS.AtomCompare.CompareElements
    params.BondTyper = rdFMCS.BondCompare.CompareAny
    params.BondCompareParameters.RingMatchesRingOnly = True
    params.Timeout = MCS_SECONDS
    for _ in range(PIECES):
        old_part, old_index = _heavy_part(old, set(pairs.values()))
        new_part, new_index = _heavy_part(new, set(pairs))
        if not old_part.GetNumAtoms() or not new_part.GetNumAtoms():
            break
        found = rdFMCS.FindMCS([old_part, new_part], params)
        if found.numAtoms < (2 if pairs else 1):
            break
        query = found.queryMol
        a = old_part.GetSubstructMatch(query)
        b = new_part.GetSubstructMatch(query)
        if not a or len(a) != len(b):
            break
        pairs.update({new_index[j]: old_index[i] for i, j in zip(a, b, strict=True)})
    return pairs


def _hydrogens(mol: Chem.Mol, atom: int) -> list[int]:
    return sorted(
        n.GetIdx() for n in mol.GetAtomWithIdx(atom).GetNeighbors() if n.GetAtomicNum() == 1
    )


def _bonds(mol: Chem.Mol, atom: int) -> dict[int, Chem.BondType]:
    """An atom's bonds to heavy atoms, a dative bond counted as a single one."""
    found = {}
    for bond in mol.GetAtomWithIdx(atom).GetBonds():
        other = bond.GetOtherAtomIdx(atom)
        if mol.GetAtomWithIdx(other).GetAtomicNum() != 1:
            kind = bond.GetBondType()
            found[other] = Chem.BondType.SINGLE if kind == Chem.BondType.DATIVE else kind
    return found


def _same_bond(a: Chem.BondType, b: Chem.BondType, aromatic: bool) -> bool:
    # An aromatic ring may come back in Kekulé form, or the other way round.
    return a == b or (aromatic and {a, b} <= SIMILAR)


def _changed(old: Chem.Mol, new: Chem.Mol, pairs: dict[int, int]) -> set[int]:
    """Matched atoms (of the drawing) whose bonds, charge or hydrogens are not as before."""
    found = set()
    for n, o in pairs.items():
        mine = {pairs.get(k, -1 - k): v for k, v in _bonds(new, n).items()}
        before = _bonds(old, o)
        aromatic = old.GetAtomWithIdx(o).GetIsAromatic() or new.GetAtomWithIdx(n).GetIsAromatic()
        same = (
            mine.keys() == before.keys()
            and all(_same_bond(mine[k], before[k], aromatic) for k in mine)
            and len(_hydrogens(new, n)) == len(_hydrogens(old, o))
            and new.GetAtomWithIdx(n).GetFormalCharge() == old.GetAtomWithIdx(o).GetFormalCharge()
        )
        if not same:
            found.add(n)
    return found


def _graph(mol: Chem.Mol) -> list[set[int]]:
    graph: list[set[int]] = [set() for _ in range(mol.GetNumAtoms())]
    for bond in mol.GetBonds():
        i, j = bond.GetBeginAtomIdx(), bond.GetEndAtomIdx()
        graph[i].add(j)
        graph[j].add(i)
    return graph


def _region(graph: list[set[int]], start: set[int], kept: dict[int, int]) -> set[int]:
    """The new atoms and the matched atoms round them made 3D together: AROUND bonds out, then
    further until each connected piece holds ANCHORS matched atoms or can grow no more."""
    region = set(start)
    edge = set(start)
    step = 0
    while edge:
        step += 1
        grown = {o for a in edge for o in graph[a]} - region
        if not grown:
            break
        if step > AROUND:
            pieces = _pieces(graph, region)
            short = [p for p in pieces if len([a for a in p if a in kept]) < ANCHORS]
            if not short:
                break
            near = set().union(*short)
            grown = {o for o in grown if graph[o] & near}
            if not grown:
                break
        region |= grown
        edge = grown
    return region


def _pieces(graph: list[set[int]], atoms: set[int]) -> list[set[int]]:
    pieces, seen = [], set()
    for a in atoms:
        if a not in seen:
            piece = builder._reach([g & atoms for g in graph], a, set())
            seen |= piece
            pieces.append(piece)
    return pieces


def _embed(new: Chem.Mol, atoms: list[int], held: dict[int, np.ndarray]) -> np.ndarray:
    """3D positions for `atoms` of the drawing, made together with those `held` where they are.
    Returns the positions in the order of `atoms`, fitted onto the held atoms."""
    whole = Chem.Mol(new)
    try:
        Chem.Kekulize(whole, clearAromaticFlags=True)  # a ring cut open is no longer aromatic
    except (ValueError, RuntimeError):
        pass
    part = Chem.RWMol(whole)
    for a in sorted(set(range(new.GetNumAtoms())) - set(atoms), reverse=True):
        part.RemoveAtom(a)
    mol = part.GetMol()
    mol.UpdatePropertyCache(strict=False)
    Chem.FastFindRings(mol)
    index = {a: n for n, a in enumerate(atoms)}
    params = rdDistGeom.ETKDGv3()
    params.randomSeed = SEED
    params.useRandomCoords = True
    if held:
        params.SetCoordMap({index[a]: Point3D(*map(float, p)) for a, p in held.items()})
    try:
        failed = rdDistGeom.EmbedMolecule(mol, params) != 0
    except (ValueError, RuntimeError):
        failed = True
    if failed:
        raise BuildError(
            "RDKit could not make the new part 3D; draw it as a fragment with Draw… instead"
        )
    coords = np.array(mol.GetConformer().GetPositions())
    if len(held) >= ANCHORS:
        moving = coords[[index[a] for a in held]]
        target = np.array(list(held.values()))
        turn, _ = geometry._fit(moving - moving.mean(0), target - target.mean(0), False)
        coords = (coords - moving.mean(0)) @ turn + target.mean(0)
    elif held:
        a, p = next(iter(held.items()))
        coords += p - coords[index[a]]
    return coords


def from_drawing(structure: Structure, molfile: str) -> Edited:
    """Put the drawing onto the structure (see the module)."""
    if not structure.rows:
        raise BuildError("There is no structure to draw onto; use Draw… to add the drawing")
    old = _drawable(structure)
    new = smiles.read_drawing(molfile)
    if any(a.GetAtomicNum() == 0 for a in new.GetAtoms()):
        raise BuildError("Attachment points are for a drawn fragment (Draw…); leave them out here")
    new = Chem.AddHs(new)
    Chem.FastFindRings(new)
    pairs = _match(old, new)
    if not pairs:
        raise BuildError("Nothing in the drawing matches the structure; use Draw… to add it")
    changed = _changed(old, new, pairs)
    kept = dict(pairs)  # drawing atom -> structure atom
    for n, o in pairs.items():
        if n not in changed:
            kept.update(zip(_hydrogens(new, n), _hydrogens(old, o), strict=True))
    fresh = [a for a in range(new.GetNumAtoms()) if a not in kept]
    gone = set(range(old.GetNumAtoms())) - set(kept.values())
    if not fresh and not gone and not changed:
        raise BuildError("The drawing has not changed")

    # Where the new atoms go.
    coords = structure.coords
    graph = _graph(new)
    region = _region(graph, set(fresh) | changed, kept)
    placed: dict[int, np.ndarray] = {}
    alone: list[set[int]] = []
    for piece in _pieces(graph, region):
        if not piece & set(fresh):
            continue
        held = {a: coords[kept[a]] for a in sorted(piece) if a in kept}
        if not held:
            alone.append(piece)
            continue
        atoms = sorted(piece)
        try:
            made = _embed(new, atoms, held)
        except BuildError:
            # The changed atoms cannot stay where they are (a ring closed between far atoms):
            # they move with the new ones.
            held = {a: p for a, p in held.items() if a not in changed}
            if not held:
                raise
            made = _embed(new, atoms, held)
        placed.update({a: made[k] for k, a in enumerate(atoms) if a not in held})

    # The structure's atoms that stay, in their order, then the new ones.
    order = sorted(kept.values())
    number = {o: k for k, o in enumerate(order)}
    rows = [list(structure.rows[o]) for o in order]
    for n, o in kept.items():
        if n in placed:  # a changed atom that had to move
            rows[number[o]] = [rows[number[o]][0], *(round(float(c), 8) for c in placed[n])]
    newcomers = [a for a in fresh if a in placed]
    for a in newcomers:
        number[-1 - a] = len(rows)
        rows.append([new.GetAtomWithIdx(a).GetSymbol(), *(round(float(c), 8) for c in placed[a])])

    def at(n: int) -> int:
        return number[kept[n]] if n in kept else number[-1 - n]

    old_graph = _graph(old)
    drawn_as = {o: n for n, o in kept.items()}
    added, removed = [], []
    for bond in new.GetBonds():
        i, j = bond.GetBeginAtomIdx(), bond.GetEndAtomIdx()
        if i in kept and j in kept and kept[j] not in old_graph[kept[i]]:
            added.append(tuple(sorted((at(i), at(j)))))
    for o in range(old.GetNumAtoms()):
        for p in old_graph[o]:
            if o < p and o in number and p in number:
                if new.GetBondBetweenAtoms(drawn_as[o], drawn_as[p]) is None:
                    removed.append((number[o], number[p]))
    carried = [(number[a], number[b]) for a, b in structure.added if a in number and b in number]
    dropped = [(number[a], number[b]) for a, b in structure.removed if a in number and b in number]
    near_gone = {number[p] for o in gone for p in old_graph[o] if p in number}
    marked = (
        {number[c] for c in structure.changed if c in number}
        | {at(a) for a in changed}
        | {number[-1 - a] for a in newcomers}
        | near_gone
    )
    holds = [
        Hold([number[a] for a in h.atoms], h.value)
        for h in structure.holds
        if all(a in number for a in h.atoms)
    ]
    result = Structure(
        rows,
        sorted(set(carried) | set(added)),
        sorted(set(dropped) | set(removed)),
        sorted(marked),
        holds,
    )
    for piece in alone:
        atoms = sorted(piece)
        made = _embed(new, atoms, {})
        beside = [
            [new.GetAtomWithIdx(a).GetSymbol(), *map(float, made[k])] for k, a in enumerate(atoms)
        ]
        result = builder.add_molecule(result, [], beside, "a drawn molecule").structure

    note = []
    if result.changed and len(result.rows) > 1:
        try:
            done = minimize.minimize(result)
        except BuildError as exc:
            note.append(f"; not minimized: {exc}")
        else:
            result = done.structure
            note.append("; Minimize cleaned up the changed atoms and two bonds round them")
    added_count = len(result.rows) - len(order)
    return Edited(
        result,
        [],
        f"Drawing put on: {added_count} atom{'s' if added_count != 1 else ''} added, "
        f"{len(gone)} deleted, {len(changed)} changed" + "".join(note),
    )
