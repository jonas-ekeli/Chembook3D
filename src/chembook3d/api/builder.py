"""HTTP API of the "Edit structure" window (D139). The window keeps the structure being edited
and its undo history; each tool here takes that structure and gives back the changed one, so
nothing is stored until the window saves through `PUT /api/nodes/{id}/geometry`. Page only
(not MCP tools) and quiet for the change counter. All rules are in `services/builder.py`."""

import math
from typing import Any, Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from chembook3d import xyz
from chembook3d.services import builder

router = APIRouter(prefix="/api/builder")

MAX_ATOMS = 5000


class StructureIn(BaseModel):
    rows: list[list[Any]] = Field(default_factory=list)  # [[element, x, y, z], ...]
    added: list[tuple[int, int]] = Field(default_factory=list)
    removed: list[tuple[int, int]] = Field(default_factory=list)
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
    ]
    value: float | None = None  # distance (Å), angle, dihedral or turn (degrees)
    move: int | None = None  # the end atom whose side moves; None: the default side
    element: str | None = None
    name: str | None = None  # a fragment or built-in molecule
    atoms: list[list[Any]] | None = None  # a molecule's rows (another node's structure)
    what: str | None = None  # how the message names those atoms
    present: bool = True  # bond: add (True) or remove
    with_hydrogens: bool = False  # delete: also the H atoms bonded only to the picked ones


class EditOut(BaseModel):
    rows: list[list[Any]]
    added: list[tuple[int, int]]
    removed: list[tuple[int, int]]
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
    if any(not 0 <= i < count for i in body.picks):
        raise HTTPException(422, "A picked atom is not there")
    return builder.Structure(rows, [tuple(p) for p in body.added], [tuple(p) for p in body.removed])


@router.get("/parts", response_model=PartsOut)
def parts():
    return PartsOut(fragments=list(builder.FRAGMENTS), molecules=list(builder.MOLECULES))


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
        elif body.tool == "fragment":
            done = builder.replace_with_fragment(structure, picks, body.name or "")
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
        picks=done.picks,
        message=done.message,
    )
