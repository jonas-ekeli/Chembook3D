"""Buried volume and steric maps (FR-STER-01…05, D81, A32).

A steric profile is a named parameter set (sphere radius, radii, hydrogens, mesh) with, per
node, the atoms that place the sphere: its centre (one atom, or the centroid of several), the
orientation (z-axis and xz-plane atoms, needed for quadrants, octants and the map) and the
atoms left out. Atom numbers are those the user sees: 1-based, of the full structure (D75).

The volumes come from morfeus (B1), which reproduces SambVca: the sphere is filled with
points one mesh step apart, and a point is buried when it lies inside an atom's scaled van der
Waals radius. A single centre atom is always left out, as the metal is in SambVca. The frame is
the one SambVca and morfeus use: the centre at the origin, the z-axis atoms on the negative
z-axis (so the ligand lies below the centre) and the xz-plane atoms in the xz-plane at
positive x. The steric map is the height of the ligand's surface over the xy-plane, looking
down the z-axis from the centre's side, computed the way morfeus draws it.

Each node keeps its last result with the inputs it came from, so a result is marked out of
date when the coordinates, the profile or the node's atoms change (B4). Profiles and results
are layout-like: saved in the investigation, not in the history (A32), and deleting a node
takes it out of every profile (the database cascades).
"""

import csv
import hashlib
import io
import json
import math
from typing import Any

import numpy as np
from morfeus.buried_volume import QUADRANT_NAMES, QUADRANT_OCTANT_MAP, BuriedVolume
from morfeus.data import radii_bondi, radii_crc
from morfeus.utils import convert_elements, get_radii
from sqlalchemy import select
from sqlalchemy.orm import Session

from chembook3d.models import Node, StericProfile, StericProfileAtoms, utcnow
from chembook3d.services.records import RecordError, get, number_value, text_value

RADII = {"bondi": "Bondi", "crc": "CRC"}
RADII_TABLES = {"bondi": radii_bondi, "crc": radii_crc}
MISSING_RADIUS = 2.0  # morfeus's radius for an element its table lacks (before scaling)
DEFAULTS: dict[str, Any] = {
    "radius": 3.5,
    "radii": "bondi",
    "radii_scale": 1.17,
    "include_hydrogens": False,
    "mesh": 0.1,
    "map_limit": None,
}
PARAMETERS = tuple(DEFAULTS)
# Allowed ranges; the point count keeps one calculation well under a few seconds.
RANGES = {
    "radius": (1.0, 10.0),
    "radii_scale": (0.5, 2.0),
    "mesh": (0.05, 0.5),
    "map_limit": (0.1, 10.0),
}
MAX_POINTS = 2_000_000
QUADRANTS = [QUADRANT_NAMES[q] for q in sorted(QUADRANT_NAMES)]  # NE, NW, SW, SE
OCTANTS = [f"{q}{side}" for q in QUADRANTS for side in "+-"]  # NE+ is NE above the xy-plane
ATOM_LISTS = ("centre", "z_axis", "xz_plane", "excluded")
LIST_NAMES = {
    "centre": "centre",
    "z_axis": "z-axis",
    "xz_plane": "xz-plane",
    "excluded": "excluded",
}


def _name_of(node: Node) -> str:
    return node.label or "Untitled node"


# ---------- profiles ----------


def list_profiles(session: Session) -> list[StericProfile]:
    return list(
        session.scalars(select(StericProfile).order_by(StericProfile.name, StericProfile.id))
    )


def _profile_name(session: Session, value: Any, own_id: str | None = None) -> str:
    name = text_value("name", value).strip()
    if not name:
        raise RecordError("a steric profile needs a name")
    clash = session.scalar(select(StericProfile).where(StericProfile.name == name))
    if clash is not None and clash.id != own_id:
        raise RecordError(f"there is already a steric profile called “{name}”")
    return name


def _parameter(field: str, value: Any) -> Any:
    if field == "radii":
        if value not in RADII:
            raise RecordError(f"radii must be one of {', '.join(RADII)}")
        return value
    if field == "include_hydrogens":
        if not isinstance(value, bool):
            raise RecordError("include_hydrogens must be true or false")
        return value
    if field == "map_limit" and value is None:
        return None
    number = number_value(field, value)
    low, high = RANGES[field]
    if not low <= number <= high:
        raise RecordError(f"{field.replace('_', ' ')} must be between {low} and {high}")
    return number


def _check_points(profile: StericProfile) -> None:
    points = 4 / 3 * math.pi * profile.radius**3 / profile.mesh**3
    if points > MAX_POINTS:
        raise RecordError(
            f"a {profile.radius:g} Å sphere with a {profile.mesh:g} Å mesh has "
            f"{points:,.0f} points; use a coarser mesh (at most {MAX_POINTS:,} points)"
        )


def create_profile(session: Session, fields: dict[str, Any]) -> StericProfile:
    profile = StericProfile(name=_profile_name(session, fields.get("name", "")), **DEFAULTS)
    for field in PARAMETERS:
        if field in fields:
            setattr(profile, field, _parameter(field, fields[field]))
    _check_points(profile)
    session.add(profile)
    _set_atoms(session, profile, fields.get("atoms") or {})
    session.flush()
    return profile


def update_profile(session: Session, profile_id: str, changes: dict[str, Any]) -> StericProfile:
    profile = get(session, StericProfile, profile_id, "Steric profile")
    for field in changes:
        if field not in ("name", "atoms", *PARAMETERS):
            raise RecordError(f"'{field}' cannot be edited")
    if "name" in changes:
        profile.name = _profile_name(session, changes["name"], profile.id)
    for field in PARAMETERS:
        if field in changes:
            setattr(profile, field, _parameter(field, changes[field]))
    _check_points(profile)
    if "atoms" in changes:
        _set_atoms(session, profile, changes["atoms"] or {})
    session.flush()
    return profile


def delete_profile(session: Session, profile_id: str) -> None:
    session.delete(get(session, StericProfile, profile_id, "Steric profile"))
    session.flush()


# ---------- a node's atoms ----------


def _numbers(node: Node, field: str, value: Any) -> list[int]:
    """Validated 1-based atom numbers of `node` for one of its lists."""
    what = LIST_NAMES[field]
    if not isinstance(value, list) or not all(
        isinstance(n, int) and not isinstance(n, bool) for n in value
    ):
        raise RecordError(f"the {what} atoms of “{_name_of(node)}” must be a list of numbers")
    count = len(node.geometry or [])
    beyond = [n for n in value if n < 1 or n > count]
    if beyond:
        raise RecordError(
            f"“{_name_of(node)}” has atoms 1 to {count}, so {what} atom {beyond[0]} does not exist"
        )
    if len(set(value)) != len(value):
        twice = next(n for n in value if value.count(n) > 1)
        raise RecordError(f"{what} atom {twice} of “{_name_of(node)}” is listed twice")
    return list(value)


def _entry_lists(node: Node, data: dict[str, Any]) -> dict[str, list[int]]:
    """The four lists of a node's entry, checked against its structure."""
    if not node.geometry:
        raise RecordError(f"“{_name_of(node)}” has no coordinates")
    unknown = set(data) - set(ATOM_LISTS)
    if unknown:
        raise RecordError(f"'{sorted(unknown)[0]}' is not one of a node's steric atom lists")
    lists = {field: _numbers(node, field, data.get(field, [])) for field in ATOM_LISTS}
    if not lists["centre"]:
        raise RecordError(f"choose the centre of the sphere for “{_name_of(node)}”")
    if bool(lists["z_axis"]) != bool(lists["xz_plane"]):
        raise RecordError(
            f"the orientation of “{_name_of(node)}” needs both z-axis and xz-plane atoms, "
            "or neither"
        )
    return lists


def _elements(node: Node) -> list[str]:
    return [row[0] for row in node.geometry or []]


def _set_atoms(session: Session, profile: StericProfile, atoms: dict[str, Any]) -> None:
    """Per node: the lists set its atoms, None takes it out of the profile, and
    {"same_as": other node} copies the other node's atoms when both have the same elements in
    the same order (B3: the app never guesses a mapping it cannot check)."""
    entries = {entry.node_id: entry for entry in profile.entries}
    for node_id, data in atoms.items():
        if data is None:
            if node_id in entries:
                profile.entries.remove(entries.pop(node_id))
            continue
        node = get(session, Node, node_id, "Node")
        if not isinstance(data, dict):
            raise RecordError(f"the steric atoms of “{_name_of(node)}” must be given as lists")
        if "same_as" in data:
            source_id = data["same_as"]
            source = entries.get(source_id)
            if source is None:
                raise RecordError("the node to copy the atoms from is not in this profile")
            source_node = get(session, Node, source_id, "Node")
            if _elements(source_node) != _elements(node):
                raise RecordError(
                    f"“{_name_of(node)}” and “{_name_of(source_node)}” do not have the same "
                    "elements in the same order, so their atom numbers cannot be shared; "
                    "pick the atoms instead"
                )
            data = {field: list(getattr(source, field)) for field in ATOM_LISTS}
        lists = _entry_lists(node, data)
        entry = entries.get(node_id)
        if entry is None:
            entry = StericProfileAtoms(node_id=node_id)
            profile.entries.append(entry)
            entries[node_id] = entry
        for field, numbers in lists.items():
            setattr(entry, field, numbers)


def atoms_of(profile: StericProfile) -> dict[str, dict[str, list[int]]]:
    return {
        entry.node_id: {field: list(getattr(entry, field)) for field in ATOM_LISTS}
        for entry in profile.entries
    }


# ---------- computing ----------


def _fingerprint(geometry: list[Any] | None) -> str:
    text = json.dumps(geometry or [], separators=(",", ":"))
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def inputs(profile: StericProfile, entry: StericProfileAtoms, node: Node) -> dict[str, Any]:
    """Everything a result depends on: the parameters, the node's lists and its geometry.
    The colour scale only changes how the map is drawn, so it is not part of it."""
    values: dict[str, Any] = {
        field: getattr(profile, field) for field in PARAMETERS if field != "map_limit"
    }
    values.update({field: list(getattr(entry, field)) for field in ATOM_LISTS})
    values["geometry"] = _fingerprint(node.geometry)
    return values


def stale_reason(profile: StericProfile, entry: StericProfileAtoms, node: Node) -> str | None:
    """Why the stored result no longer matches (B4), or None while it does."""
    stored = entry.result_inputs
    if entry.result is None or stored is None:
        return None
    now = inputs(profile, entry, node)
    if stored.get("geometry") != now["geometry"]:
        return "the coordinates changed"
    if any(stored.get(field) != now[field] for field in ATOM_LISTS):
        return "the atoms of this node in the profile changed"
    if stored != now:
        return "the profile's settings changed"
    return None


class Frame:
    """The node's atoms in the profile's frame, and the atoms that count."""

    def __init__(self, profile: StericProfile, entry: StericProfileAtoms, node: Node):
        lists = _entry_lists(node, {field: getattr(entry, field) for field in ATOM_LISTS})
        rows = node.geometry or []
        self.elements = [str(row[0]) for row in rows]
        coords = np.array([[float(v) for v in row[1:4]] for row in rows])
        centre = coords[np.array(lists["centre"]) - 1].mean(axis=0)
        coords = coords - centre
        self.oriented = bool(lists["z_axis"])
        if self.oriented:
            rotation = _rotation(
                coords[np.array(lists["z_axis"]) - 1].mean(axis=0),
                coords[np.array(lists["xz_plane"]) - 1].mean(axis=0),
            )
            coords = coords @ rotation.T
        self.coords = coords
        # A single centre atom is always left out, as the metal is in SambVca and morfeus.
        self.excluded = set(lists["excluded"])
        if len(lists["centre"]) == 1:
            self.excluded.add(lists["centre"][0])
        self.radii = np.array(
            get_radii(self.elements, radii_type=profile.radii, scale=profile.radii_scale)
        )
        self.counted = [
            i
            for i, element in enumerate(self.elements)
            if i + 1 not in self.excluded
            and (profile.include_hydrogens or element.capitalize() != "H")
        ]
        table = RADII_TABLES[profile.radii]
        numbers = convert_elements(self.elements, output="numbers") if self.elements else []
        self.missing_radii = sorted(
            {self.elements[i].capitalize() for i in self.counted if numbers[i] not in table}
        )


def _rotation(z_point: np.ndarray, xz_point: np.ndarray) -> np.ndarray:
    """Rows: the new x, y and z axes. The z-axis atoms go to the negative z-axis, the xz-plane
    atoms to the xz-plane at positive x (as morfeus orients, with the centre at the origin)."""
    if np.linalg.norm(z_point) < 1e-6:
        raise RecordError("the z-axis atoms lie on the centre, so they give no direction")
    normal = np.cross(xz_point, z_point)
    if np.linalg.norm(normal) < 1e-6 * max(1.0, np.linalg.norm(xz_point)):
        raise RecordError(
            "the xz-plane atoms lie on the z-axis, so they do not fix a plane; choose atoms "
            "off the axis"
        )
    ez = -z_point / np.linalg.norm(z_point)
    ey = normal / np.linalg.norm(normal)
    ex = np.cross(ey, ez)
    return np.vstack([ex, ey, ez])


def _volumes(profile: StericProfile, frame: Frame) -> dict[str, Any]:
    """%V_bur, and quadrants and octants when the frame is oriented, via morfeus. The centre
    is passed as one extra point at the origin, which morfeus treats as the metal and leaves
    out, so a centroid works as well as an atom."""
    n = len(frame.elements)
    volume = BuriedVolume(
        [*frame.elements, "H"],
        np.vstack([frame.coords, np.zeros(3)]),
        n + 1,
        excluded_atoms=sorted(frame.excluded),
        include_hs=profile.include_hydrogens,
        radius=profile.radius,
        radii_type=profile.radii,
        radii_scale=profile.radii_scale,
        density=profile.mesh**3,
    )
    values: dict[str, Any] = {
        "buried_percent": volume.fraction_buried_volume * 100,
        "buried_volume": volume.buried_volume,
        "sphere_volume": volume.buried_volume + volume.free_volume,
        "atoms_counted": len(frame.counted),
        "quadrants": None,
        "octants": None,
        "missing_radii": frame.missing_radii,
    }
    if frame.oriented:
        volume.octant_analysis()
        octants = volume.octants["percent_buried_volume"]
        values["quadrants"] = {
            QUADRANT_NAMES[q]: volume.quadrants["percent_buried_volume"][q] for q in QUADRANT_NAMES
        }
        values["octants"] = {}
        for q, (upper, lower) in QUADRANT_OCTANT_MAP.items():
            values["octants"][f"{QUADRANT_NAMES[q]}+"] = octants[upper]
            values["octants"][f"{QUADRANT_NAMES[q]}-"] = octants[lower]
    return values


def _grid(profile: StericProfile) -> np.ndarray:
    steps = int(round(profile.radius / profile.mesh))
    return np.arange(-steps, steps + 1) * profile.mesh


def steric_map(profile: StericProfile, frame: Frame) -> np.ndarray:
    """B2, B4: the height z (Å) of the counted atoms' surface over each grid point of the
    xy-plane inside the sphere's circle, the highest where several overlap; NaN where no atom
    is. As in morfeus, a surface below the xy-plane counts only inside the sphere."""
    xs = _grid(profile)
    gx, gy = np.meshgrid(xs, xs)  # rows run along y, columns along x
    top = np.full(gx.shape, -np.inf)
    for i in frame.counted:
        x, y, z = frame.coords[i]
        reach = frame.radii[i] ** 2 - (gx - x) ** 2 - (gy - y) ** 2
        inside = reach >= 0
        height = np.where(inside, z + np.sqrt(np.where(inside, reach, 0.0)), -np.inf)
        top = np.maximum(top, height)
    r = profile.radius
    flat = gx**2 + gy**2
    missing = (
        (flat > r**2 + 1e-9)
        | np.isneginf(top)
        | ((top < 0) & (flat + np.where(np.isneginf(top), 0.0, top) ** 2 >= r**2))
    )
    return np.where(missing, np.nan, top)


def map_out(profile: StericProfile, grid: np.ndarray) -> dict[str, Any]:
    return {
        "x": [round(float(v), 4) for v in _grid(profile)],
        "z": [[None if math.isnan(v) else round(float(v), 3) for v in row] for row in grid],
        "limit": profile.map_limit if profile.map_limit is not None else profile.radius,
    }


def _entry(profile: StericProfile, node_id: str) -> StericProfileAtoms | None:
    return next((e for e in profile.entries if e.node_id == node_id), None)


def compute(
    session: Session, profile_id: str, node_ids: list[str], maps: bool = False
) -> list[dict[str, Any]]:
    """B4: compute and store the result of each node in the profile; return them in the order
    given, with the map if asked. A node not in the profile, or whose atoms no longer fit its
    structure, gets the reason instead and keeps its old result."""
    profile = get(session, StericProfile, profile_id, "Steric profile")
    out = []
    for node_id in node_ids:
        node = get(session, Node, node_id, "Node")
        item: dict[str, Any] = {"node_id": node_id, "label": _name_of(node), "values": None}
        item.update({"map": None, "error": None, "computed_at": None})
        entry = _entry(profile, node_id)
        if entry is None:
            item["error"] = f"“{_name_of(node)}” is not in “{profile.name}”"
            out.append(item)
            continue
        try:
            frame = Frame(profile, entry, node)
            values = _volumes(profile, frame)
        except RecordError as exc:
            item["error"] = str(exc)
            out.append(item)
            continue
        entry.result = values
        entry.result_inputs = inputs(profile, entry, node)
        entry.computed_at = utcnow()
        item["values"] = values
        item["computed_at"] = entry.computed_at
        if maps and frame.oriented:
            item["map"] = map_out(profile, steric_map(profile, frame))
        out.append(item)
    session.flush()
    return out


def difference(session: Session, profile_id: str, first_id: str, second_id: str) -> dict:
    """B5: the map of the first node minus that of the second, on the profile's grid; empty
    where either has no surface."""
    profile = get(session, StericProfile, profile_id, "Steric profile")
    grids = []
    for node_id in (first_id, second_id):
        node = get(session, Node, node_id, "Node")
        entry = _entry(profile, node_id)
        if entry is None:
            raise RecordError(f"“{_name_of(node)}” is not in “{profile.name}”")
        frame = Frame(profile, entry, node)
        if not frame.oriented:
            raise RecordError(
                f"“{_name_of(node)}” has no orientation in “{profile.name}”; a map needs "
                "z-axis and xz-plane atoms"
            )
        grids.append(steric_map(profile, frame))
    return map_out(profile, grids[0] - grids[1])


# ---------- table (B5) ----------


def _list_text(numbers: list[int]) -> str:
    return " ".join(str(n) for n in numbers)


def table_csv(session: Session, profile_id: str, node_ids: list[str]) -> str:
    """B5: %V_bur by quadrant and octant for the nodes, freshly computed, with the parameters
    and atoms on every row so a value can be reproduced from the file alone."""
    results = compute(session, profile_id, node_ids)
    profile = get(session, StericProfile, profile_id, "Steric profile")
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\r\n")
    writer.writerow(
        ["Node", "%V_bur"]
        + [f"{q} %V_bur" for q in QUADRANTS]
        + [f"{o} %V_bur" for o in OCTANTS]
        + ["Sphere radius (Å)", "Radii", "Hydrogens", "Mesh (Å)"]
        + ["Centre atoms", "z-axis atoms", "xz-plane atoms", "Excluded atoms", "Note"]
    )
    for item in results:
        values = item["values"] or {}
        entry = _entry(profile, item["node_id"])
        quadrants = values.get("quadrants") or {}
        octants = values.get("octants") or {}
        number = lambda v: "" if v is None else f"{v:.1f}"  # noqa: E731
        writer.writerow(
            [item["label"], number(values.get("buried_percent"))]
            + [number(quadrants.get(q)) for q in QUADRANTS]
            + [number(octants.get(o)) for o in OCTANTS]
            + [
                f"{profile.radius:g}",
                f"{RADII[profile.radii]} ×{profile.radii_scale:g}",
                "included" if profile.include_hydrogens else "left out",
                f"{profile.mesh:g}",
            ]
            + [_list_text(getattr(entry, f)) if entry else "" for f in ATOM_LISTS]
            + [item["error"] or ""]
        )
    return buffer.getvalue()
