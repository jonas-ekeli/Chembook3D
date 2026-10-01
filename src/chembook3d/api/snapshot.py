"""Read-only copy of an investigation to share (D79, A30, FR-SHARE).

The copy is one HTML file: the snapshot viewer built by `npm run build` (frontend/src/snapshot)
with every record it shows written into it as JSON. It opens from disk with no server, so
everything the viewer can show is computed here, by the same code the API answers with:
energies at every composite level and type, and the profiles and tables of the exported
pathways for each reference the reader may choose. It holds no local paths, no copied output
files and no history (FR-SHARE-04)."""

import base64
import gzip
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel
from sqlalchemy.orm import Session

from chembook3d import __version__, units
from chembook3d import settings as app_settings
from chembook3d.api import energies as energy_api
from chembook3d.api import pathway as pathway_api
from chembook3d.api.routes import DbSession, _calculation_out, _investigation
from chembook3d.models import Calculation, CalculationType
from chembook3d.services import nodes as node_service
from chembook3d.services import notes as note_service
from chembook3d.services import pathways, species
from chembook3d.services.energies import Energies, LevelKey
from chembook3d.services.records import RecordError

router = APIRouter(prefix="/api")

# The viewer page marks where the data goes; `npm run build` writes it (vite.snapshot.config.ts).
DATA_MARK = "__CHEMBOOK3D_SNAPSHOT_DATA__"
TITLE_MARK = "__CHEMBOOK3D_SNAPSHOT_TITLE__"

# A30: the lowest real modes kept for animation, besides every imaginary one.
REAL_MODES = 10
# Displacements are unit vectors per mode; four decimals is far below what an animation shows.
MODE_DECIMALS = 4

# FR-SHARE-04: what a copy keeps of an imported file. Its path, device and the copy's own
# path in the investigation folder stay on this computer.
SOURCE_FIELDS = ("original_name", "size", "checksum", "imported_at")


class PathIn(BaseModel):
    ids: list[str]
    branch_id: str | None = None


class SnapshotIn(BaseModel):
    paths: list[PathIn] = []  # the drawer's pathways; none: one per branch (A30)
    reference_id: str | None = None


def template_path() -> Path | None:
    """The built viewer page: packaged copy first, then the repo's frontend build."""
    here = Path(__file__).resolve()
    packaged = here.parents[1] / "static-snapshot"
    repo_build = here.parents[3] / "frontend" / "dist-snapshot"
    for folder in (packaged, repo_build):
        if (folder / "snapshot.html").is_file():
            return folder / "snapshot.html"
    return None


def _kept_modes(out: pathway_api.ModesOut) -> dict[str, Any]:
    imaginary = [i for i in out.order if out.frequencies[i] < 0]
    real = [i for i in out.order if out.frequencies[i] >= 0][:REAL_MODES]
    kept = imaginary + real
    return {
        "xyz": out.xyz,
        "frequencies": out.frequencies,
        "order": kept,
        # Only the kept modes carry vectors; the others are empty lists, so indices still match.
        "modes": [
            [[round(v, MODE_DECIMALS) for v in atom] for atom in mode] if i in kept else []
            for i, mode in enumerate(out.modes)
        ],
    }


def _calculation(request: Request, calculation: Calculation) -> dict[str, Any]:
    out = _calculation_out(request, calculation).model_dump(mode="json")
    source = out["source_file"]
    if source is not None:
        out["source_file"] = {k: source[k] for k in SOURCE_FIELDS}
    return out


def _paths(session: Session, body: SnapshotIn) -> list[dict[str, Any]]:
    if body.paths:
        for path in body.paths:
            pathways.resolve(session, path.ids)  # refuses a pathway with a gap (INV-2)
        return [{"ids": p.ids, "branch_id": p.branch_id} for p in body.paths]
    found = []
    for branch in pathway_api.list_branches(session):
        try:
            extended = pathways.branch_pathway(session, branch.id)
        except RecordError:
            continue  # a branch without nodes has no pathway
        found.append({"ids": extended.path, "branch_id": branch.id})
    return found


def _note_images(session: Session, notes: list[Any]) -> dict[str, str]:
    used = set().union(*(note_service.images_in(n.body) for n in notes)) if notes else set()
    return {
        image_id: f"data:{image.media_type};base64,{base64.b64encode(image.data).decode('ascii')}"
        for image_id, image in note_service.image_data(session, used).items()
    }


def snapshot_data(request: Request, session: Session, body: SnapshotIn) -> dict[str, Any]:
    investigation = _investigation(request)
    settings = app_settings.load()
    canvas = pathway_api.canvas(session)
    records = [*canvas.nodes, *canvas.species]

    calculations: dict[str, list[dict[str, Any]]] = {}
    modes: dict[str, dict[str, Any]] = {}
    for record in records:
        node = node_service.get(session, record.id)
        ordered = sorted(node.calculations, key=lambda c: (c.created_at, c.step_index or 0))
        if ordered:
            calculations[record.id] = [_calculation(request, c) for c in ordered]
        for c in ordered:
            if c.type != CalculationType.FREQUENCY:
                continue
            try:
                out = pathway_api.calculation_modes(c.id, session)
            except HTTPException:
                continue  # no normal modes, or they do not match the geometry
            modes[c.id] = _kept_modes(out)

    options = energy_api.energy_options(session)
    paths = _paths(session, body)
    on_paths = list(dict.fromkeys(i for p in paths for i in p["ids"]))
    energies = Energies(session)
    known = {*energies.nodes, *energies.groups}
    # A30: the reader may pick a reference on the exported pathways, or keep the one chosen
    # when exporting.
    references = [*on_paths]
    if body.reference_id in known and body.reference_id not in references:
        references.append(body.reference_id)
    unit = settings.energy_unit
    # Worked out once per reference, not once per level and type: they depend on neither.
    balances = {ref: species.balances_from(session, ref) for ref in references if ref in known}
    ids = [p["ids"] for p in paths]
    prepared = {ref: pathways.prepare(session, ids, ref) for ref in on_paths} if ids else {}
    views: dict[str, Any] = {}
    profiles: dict[str, Any] = {}
    for option in options.levels:
        key = LevelKey.decode(option.key)
        for energy_type in option.types:
            name = f"{option.key}|{energy_type}"
            values, edges = energy_api.view_values(session, energies, key, energy_type)
            views[name] = {
                "values": {k: v.model_dump(mode="json") for k, v in values.items()},
                "edges": {k: v.model_dump(mode="json") for k, v in edges.items()},
                "relative": {
                    ref: {
                        k: v.model_dump(mode="json")
                        for k, v in energy_api.relative_values(
                            session, energies, values, key, energy_type, ref, balances[ref]
                        ).items()
                    }
                    for ref in balances
                },
            }
            for ref, ready in prepared.items():
                shown = pathways.profiles(
                    session, ids, ref, key, energy_type, prepared=ready, energies=energies
                )
                profiles[f"{name}|{ref}"] = {
                    "profiles": shown,
                    "table": pathways.table(
                        session, ids, ref, key, energy_type, unit, data=shown, energies=energies
                    ),
                }

    return {
        "format": "chembook3d-snapshot",
        "version": 1,
        "app_version": __version__,
        "exported_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "investigation": {"name": investigation.name},
        "settings": {
            "energy_unit": unit,
            "energy_factors": units.HARTREE_IN,
            "energy_decimals": units.DECIMALS,
            "qh_temperature": settings.qh_temperature,
            "qh_cutoff": settings.qh_cutoff,
            "hydrogens": settings.hydrogens,
        },
        "canvas": canvas.model_dump(mode="json"),
        # D85: the pictures in the notes, as data URLs the viewer shows without a server.
        "note_images": _note_images(session, canvas.notes),
        # FR-OV-01 without its recent changes: the copy holds no history (FR-SHARE-04).
        "overview": pathway_api.overview(session).model_dump(mode="json") | {"recent": []},
        "calculations": calculations,
        "modes": modes,
        "energy_options": options.model_dump(mode="json"),
        "views": views,
        "paths": paths,
        "reference_id": body.reference_id if body.reference_id in known else None,
        "profiles": profiles,
    }


def encode(data: dict[str, Any]) -> str:
    """The data as gzip-compressed JSON in base64, which the viewer unpacks with the browser's
    DecompressionStream. Profiles for every reference repeat a lot, so this keeps the file
    small. Base64 has no '<', so no text in the investigation can close the <script> element
    that holds it."""
    text = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    return base64.b64encode(gzip.compress(text.encode("utf-8"), mtime=0)).decode("ascii")


def decode(text: str) -> dict[str, Any]:
    return json.loads(gzip.decompress(base64.b64decode(text)).decode("utf-8"))


def render(template: str, data: dict[str, Any], title: str) -> str:
    """The viewer page with the data in it."""
    if template.count(DATA_MARK) != 1 or template.count(TITLE_MARK) != 1:
        raise RecordError("The snapshot viewer is not built correctly; build the interface again")
    safe_title = title.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return template.replace(TITLE_MARK, safe_title).replace(DATA_MARK, encode(data))


def file_name(name: str, when: datetime) -> str:
    """ "<name> read-only YYYY-MM-DD.html", with characters Windows refuses in names replaced."""
    clean = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "-", name).strip(" .-") or "investigation"
    return f"{clean} read-only {when:%Y-%m-%d}.html"


@router.post("/snapshot")
def export_snapshot(body: SnapshotIn, request: Request, session: DbSession) -> Response:
    """FR-SHARE-01: the open investigation as one self-contained, read-only HTML file."""
    template = template_path()
    if template is None:
        raise HTTPException(
            409,
            "The read-only viewer has not been built. Build the interface again "
            "(uv run python scripts/build_frontend.py) and restart the app.",
        )
    data = snapshot_data(request, session, body)
    name = data["investigation"]["name"]
    html = render(template.read_text(encoding="utf-8"), data, f"{name} · Chembook3D (read-only)")
    filename = file_name(name, datetime.now())
    fallback = filename.encode("ascii", "replace").decode("ascii").replace("?", "_")
    return Response(
        content=html.encode("utf-8"),
        media_type="text/html; charset=utf-8",
        headers={
            "Content-Disposition": (
                f"attachment; filename=\"{fallback}\"; filename*=UTF-8''{quote(filename)}"
            )
        },
    )
