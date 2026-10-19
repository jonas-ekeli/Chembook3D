"""HTTP API of the "Edit structure" window (D139), its Minimize (D140), SMILES (D141) and the
Ketcher drawer (D142, D143). The window keeps the structure being edited and its undo history;
each tool here takes that structure and gives back the changed one, so nothing is stored until
the window saves through `PUT /api/nodes/{id}/geometry`. Quiet for the change counter. The
window's routes are the page's own; Claude has `/run` and `/check` (D144), which take the same
tools as a list of steps on atoms numbered from 1 and also store nothing. All rules are in
`services/builder.py`, `services/minimize.py`, `services/smiles.py` and `services/redraw.py`."""

import math
from typing import Any, Literal

import numpy as np
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from chembook3d import xyz
from chembook3d.api.routes import _investigation
from chembook3d.models import Node
from chembook3d.services import builder, minimize, redraw, smiles

router = APIRouter(prefix="/api/builder")

MAX_ATOMS = 5000


class HoldIO(BaseModel):
    atoms: list[int]  # 0-based; one atom is frozen, 2 to 4 hold a distance, angle or dihedral
    value: float | None = None


class StructureIn(BaseModel):
    rows: list[list[Any]] = Field(default_factory=list)  # [[element, x, y, z], ...]
    added: list[tuple[int, int]] = Field(default_factory=list)
    removed: list[tuple[int, int]] = Field(default_factory=list)
    changed: list[int] = Field(default_factory=list)  # D140: atoms changed in the window
    holds: list[HoldIO] = Field(default_factory=list)  # D140: what Minimize keeps
    picks: list[int] = Field(default_factory=list)  # 0-based


class DescribeIn(StructureIn):
    charge: int | None = None  # the node's; without both, no electron count check
    multiplicity: int | None = None


class SideOut(BaseModel):
    atom: int
    count: int
    whole: bool


class DescribeOut(BaseModel):
    formula: str
    atom_count: int
    kind: Literal["distance", "angle", "dihedral"] | None
    value: float | None
    bonded: bool | None  # two atoms picked: whether they are bonded
    sides: list[SideOut]
    default_side: int | None
    electrons_note: str | None


class EditIn(StructureIn):
    tool: Literal[
        "distance",
        "angle",
        "dihedral",
        "rotate",
        "bond",
        "element",
        "delete",
        "add_atom",
        "fragment",
        "molecule",
        "freeze",
        "unfreeze",
        "hold",
        "release",
        "minimize",
        "smiles",
        "hydrogens",
        "drawing",
        "redraw",
    ]
    value: float | None = None  # distance (Å), angle, dihedral or turn (degrees)
    move: int | None = None  # the end atom whose side moves; None: the default side
    element: str | None = None
    name: str | None = None  # a fragment or built-in molecule
    atoms: list[list[Any]] | None = None  # a molecule's rows (another node's structure)
    what: str | None = None  # how the message names those atoms
    present: bool = True  # bond: add (True) or remove
    with_hydrogens: bool = False  # delete: also the H atoms bonded only to the picked ones
    index: int | None = None  # release: which hold
    smiles: str | None = None  # smiles, or a fragment's with one * where it attaches (D141)
    molfile: str | None = None  # drawing, redraw: Ketcher's MOL file (D142, D143)
    bonds: int | None = None  # hydrogens: until each picked atom has this many bonds
    everything: bool = False  # minimize: every atom, not only round the changed ones


class EditOut(BaseModel):
    rows: list[list[Any]]
    added: list[tuple[int, int]]
    removed: list[tuple[int, int]]
    changed: list[int]
    holds: list[HoldIO]
    picks: list[int]
    message: str


class PartsOut(BaseModel):
    fragments: list[str]
    molecules: list[str]


def _rows(rows: list[list[Any]], what: str = "The structure") -> list[list[Any]]:
    if len(rows) > MAX_ATOMS:
        raise HTTPException(422, f"{what} has more than {MAX_ATOMS} atoms")
    clean = []
    for number, row in enumerate(rows, start=1):
        element = xyz.element_of(str(row[0])) if len(row) == 4 else None
        try:
            coords = [float(c) for c in row[1:4]]
        except (TypeError, ValueError):
            coords = []
        if element is None or len(coords) != 3 or not all(math.isfinite(c) for c in coords):
            raise HTTPException(422, f"{what}: atom {number} is not an element with x, y and z")
        clean.append([element, *coords])
    return clean


def _structure(body: StructureIn) -> builder.Structure:
    rows = _rows(body.rows)
    count = len(rows)
    for pair in [*body.added, *body.removed]:
        if not all(0 <= i < count for i in pair) or pair[0] == pair[1]:
            raise HTTPException(422, "A changed bond names an atom that is not there")
    if any(not 0 <= i < count for i in [*body.picks, *body.changed]):
        raise HTTPException(422, "A picked or changed atom is not there")
    holds = []
    for hold in body.holds:
        atoms = hold.atoms
        if not 1 <= len(atoms) <= 4 or len(set(atoms)) != len(atoms):
            raise HTTPException(422, "A hold names 1 to 4 different atoms")
        if any(not 0 <= i < count for i in atoms):
            raise HTTPException(422, "A hold names an atom that is not there")
        if len(atoms) > 1 and (hold.value is None or not math.isfinite(hold.value)):
            raise HTTPException(422, "A held distance, angle or dihedral needs its value")
        holds.append(builder.Hold(list(atoms), hold.value if len(atoms) > 1 else None))
    return builder.Structure(
        rows,
        [tuple(p) for p in body.added],
        [tuple(p) for p in body.removed],
        sorted(set(body.changed)),
        holds,
    )


@router.get("/parts", response_model=PartsOut)
def parts():
    return PartsOut(fragments=list(builder.FRAGMENTS), molecules=list(builder.MOLECULES))


class DrawingOut(BaseModel):
    molfile: str


@router.post("/drawing", response_model=DrawingOut)
def drawing(body: StructureIn):
    """The structure in 2D for the Ketcher drawer's Draw from this structure (D143)."""
    try:
        return DrawingOut(molfile=redraw.to_drawing(_structure(body)))
    except builder.BuildError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.post("/describe", response_model=DescribeOut)
def describe(body: DescribeIn):
    structure = _structure(body)
    picks = body.picks
    kind = {2: "distance", 3: "angle", 4: "dihedral"}.get(len(picks))
    value = (
        builder.measure(structure.coords, picks) if kind and len(set(picks)) == len(picks) else None
    )
    found = builder.sides(structure, picks) if value is not None else []
    return DescribeOut(
        formula=builder.formula(structure.rows),
        atom_count=len(structure.rows),
        kind=kind if value is not None else None,
        value=value,
        bonded=builder.bonded(structure, *picks) if len(picks) == 2 else None,
        sides=[SideOut(atom=s.atom, count=len(s.atoms), whole=s.whole) for s in found],
        default_side=builder.default_side(found) if found else None,
        electrons_note=(
            builder.electrons_note(structure.rows, body.charge, body.multiplicity)
            if body.charge is not None and body.multiplicity is not None
            else None
        ),
    )


def _value(body: EditIn) -> float:
    if body.value is None or not math.isfinite(body.value):
        raise HTTPException(422, "Give a value")
    return body.value


def _metal_note(done: builder.Edited, built: smiles.Built) -> None:
    if built.metals:
        done.message += (
            "; the shape round the metal is a rough guess: set its bonds and angles, then Minimize"
        )


@router.post("/edit", response_model=EditOut)
def edit(body: EditIn):
    done = _apply(_structure(body), body.picks, body)
    result = done.structure
    return EditOut(
        rows=result.rows,
        added=result.added,
        removed=result.removed,
        changed=result.changed,
        holds=[HoldIO(atoms=h.atoms, value=h.value) for h in result.holds],
        picks=done.picks,
        message=done.message,
    )


def _apply(structure: builder.Structure, picks: list[int], body: EditIn) -> builder.Edited:
    """One tool of the window on the picked atoms (0-based)."""
    try:
        if body.tool == "distance":
            done = builder.set_distance(structure, picks, _value(body), body.move)
        elif body.tool == "angle":
            done = builder.set_angle(structure, picks, _value(body), body.move)
        elif body.tool == "dihedral":
            done = builder.set_dihedral(structure, picks, _value(body), body.move)
        elif body.tool == "rotate":
            done = builder.rotate_about(structure, picks, _value(body), body.move)
        elif body.tool == "bond":
            done = builder.set_bond(structure, picks, body.present)
        elif body.tool == "element":
            done = builder.change_element(structure, picks, body.element or "")
        elif body.tool == "delete":
            done = builder.delete(structure, picks, body.with_hydrogens)
        elif body.tool == "add_atom":
            done = builder.add_atom(structure, picks, body.element or "H")
        elif body.tool in ("freeze", "unfreeze"):
            done = builder.freeze(structure, picks, body.tool == "freeze")
        elif body.tool == "hold":
            value = body.value
            if value is not None and not math.isfinite(value):
                raise HTTPException(422, "Give a value")
            done = builder.hold(structure, picks, value)
        elif body.tool == "release":
            done = builder.release(structure, -1 if body.index is None else body.index)
        elif body.tool == "minimize":
            done = minimize.run(structure, body.everything)
        elif body.tool == "fragment" and body.smiles is not None:
            built = smiles.build(body.smiles, fragment=True)
            fragment = smiles.as_fragment(built)
            done = builder.replace_with_fragment(structure, picks, body.smiles.strip(), fragment)
        elif body.tool == "fragment":
            done = builder.replace_with_fragment(structure, picks, body.name or "")
        elif body.tool == "smiles":
            text = (body.smiles or "").strip()
            built = smiles.build(text)
            done = builder.add_molecule(structure, picks, built.rows, f"“{text}”")
            _metal_note(done, built)
        elif body.tool == "drawing":
            built = smiles.from_molfile(body.molfile or "")
            if built.attach is None:
                done = builder.add_molecule(structure, picks, built.rows, "the drawing")
            elif len(picks) != 1:
                raise builder.BuildError(
                    "The drawing has an attachment point: pick the atom it replaces (an H)"
                )
            else:
                fragment = smiles.as_fragment(built)
                done = builder.replace_with_fragment(
                    structure, picks, "the drawn fragment", fragment
                )
            _metal_note(done, built)
        elif body.tool == "redraw":
            done = redraw.from_drawing(structure, body.molfile or "")
        elif body.tool == "hydrogens":
            done = builder.add_hydrogens(structure, picks, body.bonds)
        else:
            if body.atoms is not None:
                what = body.what or "the structure"
                atoms = _rows(body.atoms, what[:1].upper() + what[1:])
            else:
                what = body.name or ""
                atoms = builder.molecule_rows(what)
            done = builder.add_molecule(structure, picks, atoms, what)
    except builder.BuildError as exc:
        raise HTTPException(422, str(exc)) from exc
    return done


# ---------- Claude's structure tools (D144) ----------

MAX_STEPS = 50
CONTACT = 0.4  # Å past the covalent radii: atoms more than two bonds apart closer than this
MAX_CONTACTS = 20
CONTACTS_UP_TO = 1500  # atoms; larger structures are not searched for close contacts
STEP_TOOLS = Literal[
    "distance",
    "angle",
    "dihedral",
    "rotate",
    "bond",
    "element",
    "delete",
    "add_atom",
    "fragment",
    "molecule",
    "smiles",
    "hydrogens",
    "freeze",
    "unfreeze",
    "hold",
    "release",
    "minimize",
]


class AtomsHold(BaseModel):
    atoms: list[int] = Field(
        description="1 atom (frozen), or 2 to 4 atoms whose distance, angle or dihedral is held"
    )
    value: float | None = Field(None, description="Å or degrees; null for a frozen atom")


class StepIn(BaseModel):
    tool: STEP_TOOLS = Field(
        description="distance, angle, dihedral: set the value of 2, 3 or 4 atoms, moving one "
        "end's side. rotate: turn the side of `move` about the bond of 2 atoms by `value` "
        "degrees. bond: add (`present`) or remove a bond between 2 atoms; bonds only decide "
        "which atoms move together and what Minimize keeps bonded. element: change the atoms "
        "to `element`. delete: delete the atoms (later atoms move up). add_atom: add `element` "
        "bonded to the one atom, or alone with none. fragment: replace one H (or other "
        "terminal atom) with `name` or a SMILES with one * where it attaches. molecule: put "
        "`name` or `xyz` beside the structure (off the one atom's free side, or none). smiles: "
        "build `smiles` in 3D and put it beside the structure the same way. hydrogens: add H "
        "to the atoms until each has its usual bonds (or `bonds`). freeze, unfreeze: keep "
        "atoms where they are when minimizing, or not. hold: keep the distance, angle or "
        "dihedral of 2 to 4 atoms at `value` (now: its current value) when minimizing. "
        "release: stop the hold numbered `index` in `holds`. minimize: RDKit UFF with metals "
        "held in their shape by restraints, moving the changed atoms and two bonds round "
        "them (`everything`: all atoms), keeping frozen atoms and holds."
    )
    atoms: list[int] | None = Field(
        None,
        description="The atoms the tool works on, numbered from 1. Leave out to use those the "
        "step before picked (its `picked`); [] for none.",
    )
    value: float | None = Field(None, description="Å for a distance, degrees otherwise")
    move: int | None = Field(
        None, description="Atom (from 1) at the end whose side moves; leave out for the smaller"
    )
    element: str | None = Field(None, description="element: the new element; add_atom: H default")
    name: str | None = Field(
        None,
        description="fragment: "
        + ", ".join(builder.FRAGMENTS)
        + "; molecule: "
        + ", ".join(builder.MOLECULES),
    )
    smiles: str | None = Field(None, description="smiles, or fragment with one * (D141)")
    xyz: str | None = Field(None, description="molecule: its XYZ text (another node's, say)")
    present: bool = Field(True, description="bond: true adds it, false removes it")
    with_hydrogens: bool = Field(False, description="delete: also the H bonded only to them")
    index: int | None = Field(None, description="release: the hold's number from 1")
    bonds: int | None = Field(None, description="hydrogens: bonds each atom ends with")
    everything: bool = Field(False, description="minimize: move every atom")


class StartIn(BaseModel):
    node_id: str | None = Field(
        None, description="Start from this node's coordinates (and its charge, multiplicity)"
    )
    xyz: str | None = Field(None, description="Or from this XYZ text; neither: from nothing")
    added_bonds: list[tuple[int, int]] = Field(
        default_factory=list, description="Bonds added before (from 1), as the last answer gave"
    )
    removed_bonds: list[tuple[int, int]] = Field(
        default_factory=list, description="Bonds removed before (from 1), as the last answer gave"
    )
    changed: list[int] = Field(
        default_factory=list, description="Atoms changed before (from 1), as the last answer gave"
    )
    holds: list[AtomsHold] = Field(
        default_factory=list, description="Frozen atoms and holds, as the last answer gave"
    )
    charge: int | None = Field(None, description="For the electron count check")
    multiplicity: int | None = Field(None, description="For the electron count check")


class BuildIn(StartIn):
    steps: list[StepIn] = Field(min_length=1, max_length=MAX_STEPS)


class CheckIn(StartIn):
    measure: list[list[int]] = Field(
        default_factory=list,
        description="Atom lists (from 1): 2 for a distance, 3 an angle, 4 a dihedral",
    )


class Measured(BaseModel):
    atoms: list[int]
    kind: Literal["distance", "angle", "dihedral"]
    value: float  # Å or degrees, dihedral in IUPAC's sign
    bonded: bool | None  # two atoms: bonded by the 3D view's rule and the changes


class Neighbour(BaseModel):
    atom: int
    element: str
    distance: float


class MetalOut(BaseModel):
    atom: int
    element: str
    bonded: list[Neighbour]


class Contact(BaseModel):
    atoms: tuple[int, int]
    elements: tuple[str, str]
    distance: float


class CheckOut(BaseModel):
    formula: str
    atom_count: int
    electrons_note: str | None
    measurements: list[Measured]
    metals: list[MetalOut]  # each metal's bonded atoms: check its coordination
    close_contacts: list[Contact] | None  # None: not searched (too many atoms)


class StepOut(BaseModel):
    tool: str
    message: str
    picked: list[int]  # what the next step works on when it names no atoms


class BuildOut(CheckOut):
    xyz: str
    steps: list[StepOut]
    added_bonds: list[tuple[int, int]]
    removed_bonds: list[tuple[int, int]]
    changed: list[int]
    holds: list[AtomsHold]


def _from_one(atoms: list[int]) -> list[int]:
    return [a - 1 for a in atoms]


def _start(request: Request, body: StartIn) -> tuple[builder.Structure, int | None, int | None]:
    charge, multiplicity = body.charge, body.multiplicity
    if body.node_id is not None and body.xyz is not None:
        raise HTTPException(422, "Give node_id or xyz, not both")
    if body.node_id is not None:
        with _investigation(request).sessions.begin() as session:
            node = session.get(Node, body.node_id)
            if node is None:
                raise HTTPException(404, f"No node {body.node_id}")
            if not node.geometry:
                raise HTTPException(422, f"Node “{node.label}” has no coordinates")
            rows = [list(row) for row in node.geometry]
            charge = node.charge if charge is None else charge
            multiplicity = node.multiplicity if multiplicity is None else multiplicity
    elif body.xyz is not None:
        try:
            rows = [[a.element, a.x, a.y, a.z] for a in xyz.parse_xyz(body.xyz)]
        except xyz.XyzParseError as exc:
            raise HTTPException(422, f"xyz: {exc}") from exc
    else:
        rows = []
    structure = _structure(
        StructureIn(
            rows=rows,
            added=[tuple(_from_one(list(p))) for p in body.added_bonds],
            removed=[tuple(_from_one(list(p))) for p in body.removed_bonds],
            changed=_from_one(body.changed),
            holds=[HoldIO(atoms=_from_one(h.atoms), value=h.value) for h in body.holds],
        )
    )
    return structure, charge, multiplicity


def _measured(structure: builder.Structure, lists: list[list[int]]) -> list[Measured]:
    found = []
    count = len(structure.rows)
    for atoms in lists:
        kind = builder.HOLD_KINDS.get(len(atoms))
        picks = _from_one(atoms)
        if kind is None or len(set(atoms)) != len(atoms):
            raise HTTPException(422, f"Measure {atoms}: give 2, 3 or 4 different atoms")
        if any(not 0 <= i < count for i in picks):
            raise HTTPException(422, f"Measure {atoms}: there is no such atom")
        value = builder.measure(structure.coords, picks)
        if value is None or not math.isfinite(value):
            raise HTTPException(422, f"Measure {atoms}: two of the atoms are on top of each other")
        bonded = builder.bonded(structure, *picks) if len(picks) == 2 else None
        found.append(Measured(atoms=atoms, kind=kind, value=round(value, 4), bonded=bonded))
    return found


def _within_two(graph: list[set[int]], atom: int) -> set[int]:
    near = set(graph[atom])
    for other in graph[atom]:
        near |= graph[other]
    return near | {atom}


def _contacts(structure: builder.Structure, graph: list[set[int]]) -> list[Contact] | None:
    """Atoms more than two bonds apart that are closer than their covalent radii and CONTACT:
    a clash, or a bond the 3D view does not draw."""
    count = len(structure.rows)
    if count > CONTACTS_UP_TO:
        return None
    if count < 2:
        return []
    coords = structure.coords
    radii = np.array([builder._radius(e) for e in structure.elements])
    distance = np.linalg.norm(coords[:, None, :] - coords[None, :, :], axis=2)
    excess = distance - (radii[:, None] + radii[None, :] + CONTACT)
    found = []
    for i, j in zip(*np.nonzero(np.triu(excess < 0, k=1)), strict=True):
        i, j = int(i), int(j)
        if j not in _within_two(graph, i):
            found.append((float(excess[i, j]), i, j))
    elements = structure.elements
    return [
        Contact(
            atoms=(i + 1, j + 1),
            elements=(elements[i], elements[j]),
            distance=round(float(distance[i, j]), 3),
        )
        for _, i, j in sorted(found)[:MAX_CONTACTS]
    ]


def _report(
    structure: builder.Structure,
    charge: int | None,
    multiplicity: int | None,
    measure: list[list[int]],
) -> dict[str, Any]:
    graph = builder.neighbours(structure)
    coords = structure.coords
    elements = structure.elements
    metals = [
        MetalOut(
            atom=i + 1,
            element=element,
            bonded=[
                Neighbour(
                    atom=j + 1,
                    element=elements[j],
                    distance=round(float(np.linalg.norm(coords[j] - coords[i])), 3),
                )
                for j in sorted(graph[i])
            ],
        )
        for i, element in enumerate(elements)
        if element not in minimize.TYPED
    ]
    return {
        "formula": builder.formula(structure.rows),
        "atom_count": len(structure.rows),
        "electrons_note": (
            builder.electrons_note(structure.rows, charge, multiplicity)
            if charge is not None and multiplicity is not None
            else None
        ),
        "measurements": _measured(structure, measure),
        "metals": metals,
        "close_contacts": _contacts(structure, graph),
    }


def _edit_of(step: StepIn) -> EditIn:
    atoms = what = None
    if step.tool == "molecule" and step.xyz is not None:
        try:
            atoms = [[a.element, a.x, a.y, a.z] for a in xyz.parse_xyz(step.xyz)]
        except xyz.XyzParseError as exc:
            raise builder.BuildError(f"xyz: {exc}") from exc
        what = "the molecule"
    return EditIn(
        tool=step.tool,
        value=step.value,
        move=None if step.move is None else step.move - 1,
        element=step.element,
        name=step.name,
        atoms=atoms,
        what=what,
        present=step.present,
        with_hydrogens=step.with_hydrogens,
        index=None if step.index is None else step.index - 1,
        smiles=step.smiles,
        bonds=step.bonds,
        everything=step.everything,
    )


@router.post("/check", response_model=CheckOut)
def check(body: CheckIn, request: Request):
    """D144: what Claude checks before saving a structure: formula, electrons, measurements,
    each metal's bonded atoms and close contacts."""
    structure, charge, multiplicity = _start(request, body)
    return CheckOut(**_report(structure, charge, multiplicity, body.measure))


@router.post("/run", response_model=BuildOut)
def run(body: BuildIn, request: Request):
    """D144: the window's tools as steps, in order, on a node's structure, XYZ text or nothing.
    Stores nothing: Claude saves the result with set_coordinates (ID-5 as for the window)."""
    structure, charge, multiplicity = _start(request, body)
    picks: list[int] = []
    done_steps = []
    for number, step in enumerate(body.steps, start=1):
        try:
            on = picks if step.atoms is None else _from_one(step.atoms)
            done = _apply(structure, on, _edit_of(step))
        except builder.BuildError as exc:
            raise HTTPException(422, f"Step {number} ({step.tool}): {exc}") from exc
        except HTTPException as exc:
            raise HTTPException(
                exc.status_code, f"Step {number} ({step.tool}): {exc.detail}"
            ) from exc
        structure, picks = done.structure, done.picks
        done_steps.append(
            StepOut(tool=step.tool, message=done.message, picked=[i + 1 for i in picks])
        )
    atoms = [xyz.Atom(e, x, y, z) for e, x, y, z in structure.rows]
    return BuildOut(
        xyz=xyz.format_xyz(atoms, "Built with build_structure; not saved") if atoms else "",
        steps=done_steps,
        added_bonds=[(i + 1, j + 1) for i, j in structure.added],
        removed_bonds=[(i + 1, j + 1) for i, j in structure.removed],
        changed=[i + 1 for i in structure.changed],
        holds=[AtomsHold(atoms=[i + 1 for i in h.atoms], value=h.value) for h in structure.holds],
        **_report(structure, charge, multiplicity, []),
    )
