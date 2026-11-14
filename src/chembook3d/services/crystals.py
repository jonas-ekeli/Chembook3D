"""Crystal structures (D234, FR-MCP-22): a CIF read as the molecules it holds, and the open
Crystallography Open Database (COD) searched and read, with no account, key or licence (Jonas,
2026-11-14: Chembook3D relies on none; the CSD is left out for that reason).

A CIF gives the asymmetric unit and the symmetry. gemmi (MPL-2.0) applies the symmetry to fill
the unit cell; the molecules are then grown along bonds (covalent radii plus `TOLERANCE`), each
bond taken to the nearest copy of its partner, so a molecule cut by the cell's edge comes out
whole. Copies made by symmetry are one molecule ("copies" says how many); molecules of the
asymmetric unit that are told apart by their labels (Z' > 1, solvent, counter-ions) are listed
apart. Of disordered parts, part 1 (and the atoms in no part) is kept. A crystal structure has
no energy and no level of theory: a node made from it holds its coordinates only.
"""

import re
from collections import Counter
from dataclasses import dataclass, field

import gemmi
import httpx
import numpy as np

from chembook3d import xyz

TOLERANCE = 0.45  # Å over the covalent radii sum for a bond, as most structure viewers use
TOO_CLOSE = 0.1  # Å: the same site reached twice through symmetry
MAX_SITES = 20_000  # atoms in the unit cell

COD = "https://www.crystallography.net/cod"
COD_ID = re.compile(r"^\d{7}$")
MAX_RESULTS = 50  # entries listed from one search
TIMEOUT = 30.0
# Outside requests say what made them and nothing about the person (no e-mail address).
USER_AGENT = "Chembook3D (crystal structure lookup)"


class CrystalFailed(Exception):
    """The CIF or the COD cannot give what was asked; the message says why."""


@dataclass
class Molecule:
    atoms: list[xyz.Atom]
    labels: list[str]
    copies: int  # in the unit cell
    formula: str = ""
    warnings: list[str] = field(default_factory=list)


@dataclass
class Crystal:
    name: str  # the CIF's data block
    space_group: str
    cell: list[float]  # a, b, c (Å), alpha, beta, gamma (degrees)
    molecules: list[Molecule]
    details: dict[str, str]  # formula, names, reference, database codes as the CIF gives them
    warnings: list[str] = field(default_factory=list)


# CIF items worth showing: where the structure comes from and what it is.
DETAILS = {
    "_chemical_formula_sum": "formula",
    "_chemical_name_systematic": "name",
    "_chemical_name_common": "common_name",
    "_journal_paper_doi": "doi",
    "_journal_name_full": "journal",
    "_journal_year": "year",
    "_journal_volume": "volume",
    "_journal_page_first": "page",
    "_publ_section_title": "title",
    "_database_code_depnum_ccdc_archive": "ccdc",
    "_database_code_CSD": "csd_refcode",
    "_cod_database_code": "cod_id",
    "_diffrn_ambient_temperature": "temperature_K",
}


def looks_like_cif(text: str) -> bool:
    head = text[:200_000]
    return bool(re.search(r"(?m)^\s*data_\S", head)) and "_atom_site_fract_x" in head


def _value(block: gemmi.cif.Block, tag: str) -> str | None:
    found = block.find_value(tag)
    if found is None:
        return None
    value = gemmi.cif.as_string(found).strip()
    value = " ".join(value.split())
    return value if value and value not in ("?", ".") else None


def read_cif(text: str, name: str = "") -> list[Crystal]:
    """Every data block with atom sites, as its molecules."""
    try:
        document = gemmi.cif.read_string(text)
    except (RuntimeError, ValueError) as exc:
        raise CrystalFailed(f"{name or 'The file'} is not a CIF gemmi can read: {exc}") from exc
    crystals = []
    for block in document:
        if block.find_value("_cell_length_a") is None or not block.find_loop("_atom_site_fract_x"):
            continue
        crystals.append(_crystal(block))
    if not crystals:
        raise CrystalFailed(f"{name or 'The file'} has no structure with atom sites and a cell.")
    return crystals


def _crystal(block: gemmi.cif.Block) -> Crystal:
    try:
        small = gemmi.make_small_structure_from_block(block)
    except (RuntimeError, ValueError) as exc:
        raise CrystalFailed(f"Block {block.name}: {exc}") from exc
    if small.spacegroup is None:
        raise CrystalFailed(f"Block {block.name} names no space group or symmetry operations.")
    cell = small.cell
    if cell.volume <= 0:
        raise CrystalFailed(f"Block {block.name} has no usable cell.")
    warnings: list[str] = []
    sites = small.get_all_unit_cell_sites()
    if len(sites) > MAX_SITES:
        raise CrystalFailed(f"Block {block.name}: {len(sites)} atoms in the cell are too many.")
    kept = []
    left_out: Counter[int] = Counter()
    unknown = set()
    for site in sites:
        if site.disorder_group not in (0, 1):
            left_out[site.disorder_group] += 1
            continue
        if site.element.atomic_number < 1:
            unknown.add(site.label)
            continue
        kept.append(site)
    if left_out:
        parts = ", ".join(str(p) for p in sorted(left_out))
        warnings.append(
            f"Disordered: part 1 is kept; part {parts} ({sum(left_out.values())} sites in the "
            "cell) is left out."
        )
    if unknown:
        warnings.append(f"Sites with no element left out: {', '.join(sorted(unknown))}.")
    if not kept:
        raise CrystalFailed(f"Block {block.name} has no atoms to place.")
    molecules = _molecules(kept, np.array(cell.orth.mat.tolist()))
    details = {key: v for tag, key in DETAILS.items() if (v := _value(block, tag)) is not None}
    return Crystal(
        name=block.name,
        space_group=small.spacegroup.hm,
        cell=[cell.a, cell.b, cell.c, cell.alpha, cell.beta, cell.gamma],
        molecules=molecules,
        details=details,
        warnings=warnings,
    )


def _molecules(sites: list, orth: np.ndarray) -> list[Molecule]:
    frac = np.array([[s.fract.x, s.fract.y, s.fract.z] for s in sites])
    elements = [s.element.name for s in sites]
    radii = np.array([s.element.covalent_r for s in sites])
    hydrogen = np.array([e in ("H", "D") for e in elements])
    unplaced = set(range(len(sites)))
    found: list[tuple[list[int], np.ndarray]] = []
    while unplaced:
        seed = min(unplaced)
        where = {seed: frac[seed] - np.floor(frac[seed])}
        queue = [seed]
        unplaced.discard(seed)
        while queue:
            i = queue.pop()
            step = frac - where[i]
            step -= np.round(step)  # to the nearest copy of each site
            apart = np.linalg.norm(step @ orth.T, axis=1)
            near = (apart > TOO_CLOSE) & (apart < radii + radii[i] + TOLERANCE)
            if hydrogen[i]:
                near &= ~hydrogen  # two hydrogens are never bonded here
            for j in np.nonzero(near)[0]:
                j = int(j)
                if j in unplaced:
                    unplaced.discard(j)
                    where[j] = where[i] + step[j]
                    queue.append(j)
        members = sorted(where)
        found.append((members, np.array([where[m] for m in members]) @ orth.T))
    groups: dict[tuple[str, ...], list[tuple[list[int], np.ndarray]]] = {}
    for members, points in found:
        key = tuple(sorted(sites[m].label for m in members))
        groups.setdefault(key, []).append((members, points))
    molecules = []
    for copies in groups.values():
        members, points = copies[0]
        points = points - points.mean(axis=0)
        atoms = [
            xyz.Atom(elements[m], *map(float, p)) for m, p in zip(members, points, strict=True)
        ]
        molecule = Molecule(
            atoms=atoms,
            labels=[sites[m].label for m in members],
            copies=len(copies),
            formula=xyz.formula(atoms),
        )
        if not any(a.element == "H" for a in atoms) and len(atoms) > 1:
            heavy = {a.element for a in atoms}
            if heavy & {"C", "N", "O"}:
                molecule.warnings.append(
                    "No hydrogens: the CIF has none placed on this molecule; add them before "
                    "any calculation."
                )
        molecules.append(molecule)
    molecules.sort(key=lambda m: (-_metals(m), -len(m.atoms)))
    return molecules


def _metals(molecule: Molecule) -> int:
    return sum(1 for a in molecule.atoms if gemmi.Element(a.element).is_metal)


def summary(crystal: Crystal) -> dict:
    """What a tool answers: the crystal and its molecules, largest with a metal first."""
    return {
        "block": crystal.name,
        "space_group": crystal.space_group,
        "cell": [round(v, 4) for v in crystal.cell],
        **crystal.details,
        "warnings": crystal.warnings,
        "molecules": [
            {
                "molecule": number,
                "formula": m.formula,
                "atoms": len(m.atoms),
                "copies_in_cell": m.copies,
                **({"warnings": m.warnings} if m.warnings else {}),
            }
            for number, m in enumerate(crystal.molecules, start=1)
        ],
    }


def _client() -> httpx.Client:
    return httpx.Client(timeout=TIMEOUT, headers={"User-Agent": USER_AGENT}, follow_redirects=True)


def _get(path: str, params: dict | None = None) -> httpx.Response:
    try:
        with _client() as client:
            response = client.get(f"{COD}/{path}", params=params)
    except httpx.HTTPError as exc:
        raise CrystalFailed(f"The COD could not be reached ({exc.__class__.__name__}).") from exc
    if response.status_code == 404:
        raise CrystalFailed("The COD has no such entry.")
    if not response.is_success:
        raise CrystalFailed(f"The COD answered {response.status_code}.")
    return response


def _hill(formula: str) -> str:
    """COD writes '- C10 H12 O -'; give 'C10H12O'."""
    return "".join(formula.strip(" -").split())


def search(
    doi: str | None = None,
    text: str | None = None,
    formula: str | None = None,
    elements: list[str] | None = None,
    year: int | None = None,
) -> dict:
    """COD entries by a paper's DOI, words from the entry (a compound's name, an author),
    a formula or elements that must be present (at most 8)."""
    params: dict[str, str] = {}
    if doi:
        params["doi"] = doi.strip().removeprefix("https://doi.org/").removeprefix("doi:")
    if text:
        params["text"] = text.strip()
    if formula:
        params["formula"] = " ".join(re.findall(r"[A-Z][a-z]?\d*", formula.replace(" ", "")))
    for number, element in enumerate(elements or [], start=1):
        if number > 8:
            raise CrystalFailed("At most 8 elements")
        if gemmi.Element(element).atomic_number < 1:
            raise CrystalFailed(f"{element} is not an element")
        params[f"el{number}"] = element
    if year:
        params["year"] = str(year)
    if not params:
        raise CrystalFailed("Give a DOI, words, a formula or elements to search for")
    count = _get("result", {**params, "format": "count"}).text.strip()
    total = int(count) if count.isdigit() else None
    if total == 0:
        return {"total": 0, "entries": []}
    if total is not None and total > 1000:
        return {
            "total": total,
            "entries": [],
            "note": "Too many to list: search with more words, a DOI or more elements.",
        }
    try:
        rows = _get("result", {**params, "format": "json"}).json()
    except ValueError as exc:
        raise CrystalFailed("The COD's answer could not be read.") from exc
    entries = [_entry(row) for row in rows if isinstance(row, dict)]
    answer: dict = {"total": total if total is not None else len(entries)}
    if len(entries) > MAX_RESULTS:
        answer["note"] = f"The first {MAX_RESULTS} of {len(entries)} are listed."
        entries = entries[:MAX_RESULTS]
    answer["entries"] = entries
    return answer


def _entry(row: dict) -> dict:
    keep = {
        "cod_id": row.get("file"),
        "formula": _hill(str(row["formula"])) if row.get("formula") else None,
        "name": row.get("chemname") or row.get("commonname") or row.get("mineral"),
        "title": row.get("title"),
        "authors": row.get("authors"),
        "journal": row.get("journal"),
        "year": row.get("year"),
        "volume": row.get("volume"),
        "page": row.get("firstpage"),
        "doi": row.get("doi"),
        "space_group": row.get("sg"),
        "temperature_K": row.get("celltemp") or row.get("diffrtemp"),
    }
    return {k: v for k, v in keep.items() if v not in (None, "")}


def fetch_cif(cod_id: str) -> str:
    cod_id = str(cod_id).strip()
    if not COD_ID.match(cod_id):
        raise CrystalFailed(f"'{cod_id}' is not a COD id (seven digits, such as 1000000)")
    return _get(f"{cod_id}.cif").text
