"""Crystal structures for Claude (D234): the open Crystallography Open Database searched, and
a structure from it or a CIF on this computer read as its molecules, each kept as a build
(D148) that `create_node` or `build_structure` takes, so no coordinates are copied. Nothing is
written to the investigation. Page and Claude on this computer only: a search goes out to the
COD."""

import asyncio
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from chembook3d import xyz
from chembook3d.api import builds
from chembook3d.api.builder import StructureIn, _structure
from chembook3d.api.jobs import _local_page_only
from chembook3d.services import crystals, inspection

router = APIRouter(prefix="/api/crystals")


@router.get("/search")
async def find_crystal_structures(
    request: Request,
    doi: str | None = None,
    text: str | None = None,
    formula: str | None = None,
    elements: Annotated[list[str] | None, Query()] = None,
    year: int | None = None,
) -> dict:
    """COD entries by a paper's DOI, words, a formula or elements (D234)."""
    _local_page_only(request)
    try:
        return await asyncio.to_thread(crystals.search, doi, text, formula, elements, year)
    except crystals.CrystalFailed as exc:
        raise HTTPException(422, str(exc)) from exc


class CrystalIn(BaseModel):
    cod_id: str | None = Field(None, description="A COD entry's id (seven digits)")
    path: str | None = Field(None, description="Or the full path of a CIF on this computer")
    include_xyz: bool = Field(False, description="Also give each molecule as XYZ text")


def _text_of(body: CrystalIn) -> tuple[str, str]:
    if (body.cod_id is None) == (body.path is None):
        raise HTTPException(422, "Give a cod_id or a path")
    if body.cod_id is not None:
        return crystals.fetch_cif(body.cod_id), f"COD {body.cod_id}"
    try:
        local = Path(body.path or "").expanduser().resolve()
        return inspection.read_text_of(local), str(local)
    except (OSError, inspection.InspectFailed) as exc:
        raise HTTPException(422, f"Cannot read {body.path}: {exc}") from exc


@router.post("/read")
async def get_crystal_structure(body: CrystalIn, request: Request) -> dict:
    """A crystal structure as its molecules (D234), each kept as a build by its build_id."""
    _local_page_only(request)
    try:
        text, source = await asyncio.to_thread(_text_of, body)
        found = await asyncio.to_thread(crystals.read_cif, text, source)
    except crystals.CrystalFailed as exc:
        raise HTTPException(422, str(exc)) from exc
    answers = []
    for crystal in found:
        answer = crystals.summary(crystal)
        for row, molecule in zip(answer["molecules"], crystal.molecules, strict=True):
            structure = _structure(
                StructureIn(rows=[[a.element, a.x, a.y, a.z] for a in molecule.atoms])
            )
            row["build_id"] = request.app.state.builds.add(builds.Build(structure, None, None))
            if body.include_xyz:
                row["xyz"] = xyz.format_xyz(molecule.atoms, f"{source} {row['formula']}")
        answers.append(answer)
    return {
        "source": source,
        "structures": answers,
        "note": "A crystal structure has no energy and no level of theory. Save a molecule "
        "with create_node by its build_id, naming the source in its notes; the charge is not "
        "in the CIF.",
    }
