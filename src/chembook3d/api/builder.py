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
from chembook3d.api import builds
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
    # D145: the coordinates Minimize takes the bonds and the metals' shape from, as it gave them
    minimized_from: list[list[Any]] | None = None
    minimized_metals: Literal["keep", "relax"] | None = None  # D146: how those Minimizes did


class DescribeIn(StructureIn):
    charge: int | None = None  # the node's; without both, no electron count check
    multiplicity: int | None = None
    ignore: list[tuple[int, int]] = Field(default_factory=list)  # D166: bonds a flip ignores


class SideOut(BaseModel):
    atom: int
    count: int
    whole: bool


class FlipOut(BaseModel):
    """D166: what Flip would do with the three atoms picked."""

    count: int  # atoms that would turn
    tie: list[tuple[int, int]]  # tied to the rest: the bonds of the shortest way round
    error: str | None  # why it cannot flip (the third atom on the line of the first two)


class DescribeOut(BaseModel):
    formula: str
    atom_count: int
    kind: Literal["distance", "angle", "dihedral"] | None
    value: float | None
    bonded: bool | None  # two atoms picked: whether they are bonded
    sides: list[SideOut]
    default_side: int | None
    electrons_note: str | None
    flip: FlipOut | None = None  # three atoms picked


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
        "rotate_fragment",
        "align_vectors",
        "flip",
    ]
    value: float | None = None  # distance (Å), angle, dihedral or turn (degrees); D148: a new
    # atom's bond length, a molecule's distance from the picked atom to each of its donors
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
    count: int | None = None  # hydrogens: exactly this many on each picked atom (D148)
    donors: list[int] | None = None  # molecule, smiles: its atoms (0-based) facing the pick
    everything: bool = False  # minimize: every atom, not only round the changed ones
    metals: Literal["keep", "relax"] = "keep"  # minimize: hold metals in shape, or relax (D146)
    # D150, rotate_fragment and align_vectors: two points each, a point an atom (0-based) or the
    # atoms whose middle counts; the picks are the atoms that move
    axis: list[list[int]] | None = None
    vector: list[list[int]] | None = None
    reference: list[list[int]] | None = None
    ignore: list[tuple[int, int]] | None = None  # flip: bonds left out (0-based, D166)


class EditOut(BaseModel):
    rows: list[list[Any]]
    added: list[tuple[int, int]]
    removed: list[tuple[int, int]]
    changed: list[int]
    holds: list[HoldIO]
    picks: list[int]
    message: str
    minimized_from: list[list[Any]] | None = None
    minimized_metals: Literal["keep", "relax"] | None = None


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
    minimized_from = None
    if body.minimized_from is not None:
        minimized_from = _rows(body.minimized_from, "The structure before minimizing")
        if [row[0] for row in minimized_from] != [row[0] for row in rows]:
            minimized_from = None  # not this structure's atoms: Minimize starts afresh
    return builder.Structure(
        rows,
        [tuple(p) for p in body.added],
        [tuple(p) for p in body.removed],
        sorted(set(body.changed)),
        holds,
        minimized_from,
        body.minimized_metals if minimized_from is not None else None,
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
        flip=_flip_out(structure, picks, body.ignore)
        if value is not None and kind == "angle"
        else None,
    )


def _flip_out(
    structure: builder.Structure, picks: list[int], ignore: list[tuple[int, int]]
) -> FlipOut:
    try:
        plan = builder.flip_plan(structure, picks, ignore)
    except builder.BuildError as exc:
        return FlipOut(count=0, tie=[], error=str(exc))
    return FlipOut(count=len(plan.moving), tie=plan.tie, error=None)


def _value(body: EditIn) -> float:
    if body.value is None or not math.isfinite(body.value):
        raise HTTPException(422, "Give a value")
    return body.value


def _length(body: EditIn) -> float | None:
    if body.value is not None and not math.isfinite(body.value):
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
        minimized_from=result.minimized_from,
        minimized_metals=result.minimized_metals,
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
            done = builder.add_atom(structure, picks, body.element or "H", _length(body))
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
            done = minimize.run(structure, body.everything, body.metals == "relax")
        elif body.tool == "fragment" and body.smiles is not None:
            built = smiles.build(body.smiles, fragment=True)
            fragment = smiles.as_fragment(built)
            done = builder.replace_with_fragment(structure, picks, body.smiles.strip(), fragment)
        elif body.tool == "fragment":
            done = builder.replace_with_fragment(structure, picks, body.name or "")
        elif body.tool == "smiles":
            text = (body.smiles or "").strip()
            built = smiles.build(text)
            done = builder.add_molecule(
                structure, picks, built.rows, f"“{text}”", _length(body), body.donors
            )
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
            done = builder.add_hydrogens(structure, picks, body.bonds, body.count)
        elif body.tool == "rotate_fragment":
            done = builder.rotate_fragment(structure, picks, _value(body), body.axis)
        elif body.tool == "flip":
            done = builder.flip(structure, picks, body.ignore or [])
        elif body.tool == "align_vectors":
            target = 0.0 if body.value is None else _value(body)
            done = builder.align_vectors(
                structure, picks, body.vector or [], body.reference or [], target, body.axis
            )
        else:
            if body.atoms is not None:
                what = body.what or "the structure"
                atoms = _rows(body.atoms, what[:1].upper() + what[1:])
            else:
                what = body.name or ""
                atoms = builder.molecule_rows(what)
            done = builder.add_molecule(structure, picks, atoms, what, _length(body), body.donors)
    except builder.BuildError as exc:
        raise HTTPException(422, str(exc)) from exc
    return done


# ---------- Claude's structure tools (D144) ----------

MAX_STEPS = 50
CONTACT = 0.4  # Å past the covalent radii: atoms three bonds apart closer than this
SHORT = 0.75  # times the covalent radii sum: a drawn bond this short is a clash (D149)
MAX_CONTACTS = 20
MAX_WARNINGS = 10
SIDE_ATOMS = 30  # a measurement lists the atoms of a side up to this many
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
    "rotate_fragment",
    "align_vectors",
    "flip",
]
POINTS = (
    "Two points, each an atom (from 1) or a list of atoms whose middle counts, e.g. [4, [62, 63]]"
)


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
        "bonded to the one atom (`value`: the bond length; default the covalent radii sum; at a "
        "metal's open site), or alone with none. fragment: replace one H (or other terminal "
        "atom) with `name` or a SMILES with one * where it attaches. molecule: put `name` or "
        "`xyz` beside the structure, its `donors` facing the one atom from its free side (a "
        "metal's open site), or past the structure with none; with `value`, each donor is "
        "bonded to the atom at that distance (Å). smiles: build `smiles` in 3D and put it "
        "there the same way. hydrogens: add H to the atoms until each has its usual bonds "
        "(every drawn bond counts, one to a metal too; or `bonds`), or exactly `count` H to "
        "each. freeze, unfreeze: keep "
        "atoms where they are when minimizing, or not. hold: keep the distance, angle or "
        "dihedral of 2 to 4 atoms at `value` (now: its current value) when minimizing. "
        "release: stop the hold numbered `index` in `holds`. minimize: RDKit UFF with metals "
        'held in their shape by restraints (`metals`: "relax" pulls them to UFF\'s bond '
        "lengths and the nearest ideal shape instead), moving the changed atoms and two "
        "bonds round them (`everything`: all atoms), keeping frozen atoms and holds. "
        "rotate_fragment (D150): turn `atoms` as one rigid body by `value` degrees about `axis`, "
        "whatever the bonds; right-handed, positive anticlockwise seen from the axis's second "
        "point looking back at its first; default axis: from the one atom outside bonded to "
        "them to the middle of their atoms bonded to it (a ligand's binding axis); the message "
        "gives each bond to the rest before and after. align_vectors (D150): the same rigid "
        "turn about `axis`, by the smallest angle that puts the line `vector` (two points among "
        "`atoms`) at `value` degrees (0 parallel, default; 90 perpendicular) to the line "
        "`reference` (two points that stay); says the angle reached when the axis cannot reach "
        "it. flip (D166): on 3 atoms, swap the first two by a 180° turn about the line from "
        "their middle towards the third (which stays), everything reached from them without "
        "passing the third turning rigidly (an η² ligand end over end about the metal, an "
        "aryl's two ortho C about its N); refused when that is tied to the rest some other way, "
        "naming the bonds of the tie: give one in `ignore_bonds`, or remove it first."
    )
    atoms: list[int] | None = Field(
        None,
        description="The atoms the tool works on, numbered from 1. Leave out to use those the "
        "step before picked (its `picked`); [] for none.",
    )
    value: float | None = Field(
        None,
        description="Å for a distance, add_atom's bond length and a molecule's or smiles' "
        "distance to each donor; degrees otherwise",
    )
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
    count: int | None = Field(None, description="hydrogens: H added to each atom (D148)")
    donors: list[int] | None = Field(
        None,
        description="molecule, smiles: the molecule's own atoms (from 1, in its xyz or SMILES "
        "order) that bind to the picked atom; default: a built-in molecule's C=C, C or O, else "
        "its first atom",
    )
    everything: bool = Field(False, description="minimize: move every atom")
    metals: Literal["keep", "relax"] = Field(
        "keep", description="minimize: keep each metal's shape, or relax it (D146)"
    )
    axis: list[int | list[int]] | None = Field(
        None, description="rotate_fragment, align_vectors: the axis. " + POINTS
    )
    vector: list[int | list[int]] | None = Field(
        None, description="align_vectors: the line that turns with `atoms`. " + POINTS
    )
    reference: list[int | list[int]] | None = Field(
        None, description="align_vectors: the line that stays. " + POINTS
    )
    ignore_bonds: list[tuple[int, int]] | None = Field(
        None, description="flip: bonds (atom pairs from 1) to leave out when finding what turns"
    )


def _points(points: list[int | list[int]] | None) -> list[list[int]] | None:
    """Points from 1 (an atom or a list whose middle counts) as lists of atoms from 0."""
    if points is None:
        return None
    return [_from_one([p] if isinstance(p, int) else list(p)) for p in points]


class StartIn(BaseModel):
    build_id: str | None = Field(
        None,
        description="Go on from this build_structure answer, with its bonds, changed atoms, "
        "holds and charge (D148); the lists below are then left out",
    )
    node_id: str | None = Field(
        None, description="Or start from this node's coordinates (and its charge, multiplicity)"
    )
    xyz: str | None = Field(None, description="Or from this XYZ text; none: from nothing")
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
    include_xyz: bool = Field(
        False, description="Also give the coordinates as XYZ text (save with build_id instead)"
    )


class CheckIn(StartIn):
    measure: list[list[int]] = Field(
        default_factory=list,
        description="Atom lists (from 1): 2 for a distance, 3 an angle, 4 a dihedral",
    )


class MeasuredSide(BaseModel):
    atom: int  # the end atom; pass it as `move` to move this side
    count: int
    whole: bool  # false: the sides are joined, so only the end atom and its ends move
    atoms: list[int] | None  # the atoms that move, when there are up to SIDE_ATOMS


class Measured(BaseModel):
    atoms: list[int]
    kind: Literal["distance", "angle", "dihedral"]
    value: float  # Å or degrees, dihedral in IUPAC's sign
    bonded: bool | None  # two atoms: bonded by the 3D view's rule and the changes
    sides: list[MeasuredSide]  # D148: what setting it (or turning about it) would move
    moves: int  # the side that moves when `move` is left out


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
    warnings: list[str] = []  # D148: bonds and close contacts the step made that it did not mean


class BuildOut(CheckOut):
    build_id: str  # D148: pass to set_coordinates, create_node or the next build_structure
    xyz: str | None  # only with include_xyz
    steps: list[StepOut]
    added_bonds: list[tuple[int, int]]
    removed_bonds: list[tuple[int, int]]
    changed: list[int]
    holds: list[AtomsHold]


def _from_one(atoms: list[int]) -> list[int]:
    return [a - 1 for a in atoms]


def _start(request: Request, body: StartIn) -> tuple[builder.Structure, int | None, int | None]:
    charge, multiplicity = body.charge, body.multiplicity
    given = [body.build_id, body.node_id, body.xyz]
    if sum(g is not None for g in given) > 1:
        raise HTTPException(422, "Give one of build_id, node_id or xyz, not both")
    if body.build_id is not None:
        build = builds.get(request, body.build_id)
        charge = build.charge if charge is None else charge
        multiplicity = build.multiplicity if multiplicity is None else multiplicity
        return build.structure, charge, multiplicity
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
        sides = builder.sides(structure, picks)
        found.append(
            Measured(
                atoms=atoms,
                kind=kind,
                value=round(value, 4),
                bonded=bonded,
                sides=[
                    MeasuredSide(
                        atom=side.atom + 1,
                        count=len(side.atoms),
                        whole=side.whole,
                        atoms=sorted(a + 1 for a in side.atoms)
                        if len(side.atoms) <= SIDE_ATOMS
                        else None,
                    )
                    for side in sides
                ],
                moves=builder.default_side(sides) + 1,
            )
        )
    return found


def _within_two(graph: list[set[int]], atom: int) -> set[int]:
    near = set(graph[atom])
    for other in graph[atom]:
        near |= graph[other]
    return near | {atom}


def _within_three(graph: list[set[int]], atom: int) -> set[int]:
    near = _within_two(graph, atom)
    return near | {x for other in near for x in graph[other]}


def _trusted(structure: builder.Structure) -> list[set[int]]:
    """D149: the drawn bonds that look like bonds, so a clash drawn as a bond is still reported:
    an atom keeps only as many bonds to non-metals as it can have (Minimize's rule, the shortest
    for its radii first), and a bond shorter than SHORT times the radii sum is not one. Bonds the
    user added and bonds to metals stay."""
    elements = structure.elements
    metals = {i for i, e in enumerate(elements) if e not in minimize.TYPED}
    graph = minimize._bonds(structure, metals)
    coords = structure.coords
    added = {tuple(p) for p in structure.added}
    for i in range(len(elements)):
        for j in sorted(graph[i]):
            if j <= i or (i, j) in added:
                continue
            length = float(np.linalg.norm(coords[i] - coords[j]))
            if length < SHORT * builder._bond_length(elements[i], elements[j]):
                graph[i].discard(j)
                graph[j].discard(i)
    return graph


def _close(structure: builder.Structure) -> list[tuple[float, int, int]]:
    """Atoms more than two bonds apart (by the bonds that look like bonds, D149) that are too
    close: three bonds apart, closer than their covalent radii and CONTACT; further, closer than
    CROWDED times their van der Waals radii (D149), as a placed molecule is called crowded.
    Most crowded first."""
    count = len(structure.rows)
    if count < 2 or count > CONTACTS_UP_TO:
        return []
    coords = structure.coords
    elements = structure.elements
    radii = np.array([builder._radius(e) for e in elements])
    vdw = np.array([builder._vdw(e) for e in elements])
    distance = np.linalg.norm(coords[:, None, :] - coords[None, :, :], axis=2)
    covalent = distance - (radii[:, None] + radii[None, :] + CONTACT)
    ratio = distance / (vdw[:, None] + vdw[None, :])
    trusted = _trusted(structure)
    near = (covalent < 0) | (ratio < builder.CROWDED)
    found = []
    for i, j in zip(*np.nonzero(np.triu(near, k=1)), strict=True):
        i, j = int(i), int(j)
        if j in _within_two(trusted, i):
            continue
        if j in _within_three(trusted, i) and covalent[i, j] >= 0:
            continue
        found.append((float(ratio[i, j]), i, j))
    return sorted(found)


def _contacts(structure: builder.Structure, graph: list[set[int]]) -> list[Contact] | None:
    if len(structure.rows) > CONTACTS_UP_TO:
        return None
    coords = structure.coords
    elements = structure.elements
    return [
        Contact(
            atoms=(i + 1, j + 1),
            elements=(elements[i], elements[j]),
            distance=round(float(np.linalg.norm(coords[i] - coords[j])), 3),
        )
        for _, i, j in _close(structure)[:MAX_CONTACTS]
    ]


def _electrons(rows: builder.Rows, charge: int | None, multiplicity: int | None) -> str | None:
    """D148: whether the electron count fits, said either way."""
    if not rows:
        return None
    if charge is None or multiplicity is None:
        return (
            "Not checked: no charge and multiplicity (a node's own are used when starting from it)"
        )
    note = builder.electrons_note(rows, charge, multiplicity)
    if note is not None:
        return note
    electrons = builder.electrons_of(rows, charge)
    return f"{electrons} electrons with charge {charge}: multiplicity {multiplicity} fits"


def _name(structure: builder.Structure, atom: int) -> str:
    return f"{structure.elements[atom]} {atom + 1}"


def _warnings(before: builder.Structure, after: builder.Structure, on: list[int]) -> list[str]:
    """D148: bonds the 3D view now draws and close contacts that a step made without meaning to:
    not to the atoms it worked on, not within what it added, not a bond it was asked for."""
    count = len(before.rows)
    if len(after.rows) < count or len(after.rows) > CONTACTS_UP_TO:
        return []  # deleted atoms renumber the rest
    old, new = builder.neighbours(before), builder.neighbours(after)
    meant = {tuple(p) for p in after.added} | {tuple(p) for p in before.added}
    coords = after.coords
    found = []
    for i in range(len(after.rows)):
        for j in sorted(new[i]):
            if j <= i or (i < count and j in old[i]) or (i, j) in meant:
                continue
            if i >= count or (j >= count and i in on) or (i in on and j in on):
                continue  # within what was added, onto the atoms worked on, or between them
            distance = float(np.linalg.norm(coords[i] - coords[j]))
            found.append(
                f"{_name(after, i)} and {_name(after, j)} are now drawn bonded ({distance:.2f} Å)"
            )
    was = {(i, j) for _, i, j in _close(before)}
    for _, i, j in _close(after):
        if (i, j) not in was:
            distance = float(np.linalg.norm(coords[i] - coords[j]))
            found.append(f"{_name(after, i)} and {_name(after, j)} are only {distance:.2f} Å apart")
    return found[:MAX_WARNINGS]


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
        "electrons_note": _electrons(structure.rows, charge, multiplicity),
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
        count=step.count,
        donors=None if step.donors is None else _from_one(step.donors),
        everything=step.everything,
        metals=step.metals,
        axis=_points(step.axis),
        vector=_points(step.vector),
        reference=_points(step.reference),
        ignore=None
        if step.ignore_bonds is None
        else [(i - 1, j - 1) for i, j in step.ignore_bonds],
    )


@router.post("/check", response_model=CheckOut)
def check(body: CheckIn, request: Request):
    """D144: what Claude checks before saving a structure: formula, electrons, measurements,
    each metal's bonded atoms and close contacts."""
    structure, charge, multiplicity = _start(request, body)
    return CheckOut(**_report(structure, charge, multiplicity, body.measure))


@router.post("/run", response_model=BuildOut)
def run(body: BuildIn, request: Request):
    """D144: the window's tools as steps, in order, on a node's structure, XYZ text, an earlier
    build or nothing. Stores nothing in the investigation: the result is kept by its build_id
    (D148), which set_coordinates and create_node take (ID-5 as for the window)."""
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
        warnings = _warnings(structure, done.structure, on)
        structure, picks = done.structure, done.picks
        done_steps.append(
            StepOut(
                tool=step.tool,
                message=done.message,
                picked=[i + 1 for i in picks],
                warnings=warnings,
            )
        )
    atoms = [xyz.Atom(e, x, y, z) for e, x, y, z in structure.rows]
    build_id = request.app.state.builds.add(builds.Build(structure, charge, multiplicity))
    return BuildOut(
        build_id=build_id,
        xyz=xyz.format_xyz(atoms, "Built with build_structure; not saved")
        if atoms and body.include_xyz
        else None,
        steps=done_steps,
        added_bonds=[(i + 1, j + 1) for i, j in structure.added],
        removed_bonds=[(i + 1, j + 1) for i, j in structure.removed],
        changed=[i + 1 for i in structure.changed],
        holds=[AtomsHold(atoms=[i + 1 for i in h.atoms], value=h.value) for h in structure.holds],
        **_report(structure, charge, multiplicity, []),
    )
