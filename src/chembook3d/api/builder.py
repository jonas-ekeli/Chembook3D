"""HTTP API of the "Edit structure" window (D139), its Minimize (D140), SMILES (D141) and the
Ketcher drawer (D142, D143). The window keeps the structure being edited and its undo history;
each tool here takes that structure and gives back the changed one, so nothing is stored until
the window saves through `PUT /api/nodes/{id}/geometry`. Page only (not MCP tools) and quiet for
the change counter. All rules are in `services/builder.py`, `services/minimize.py`,
`services/smiles.py` and `services/redraw.py`."""

import math
from typing import Any, Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from chembook3d import xyz
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
    structure = _structure(body)
    picks = body.picks
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
