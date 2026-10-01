"""Buried volume and steric maps (FR-STER-01…05, D81, A32; T-STER-01…05)."""

import csv
import io
import math
from pathlib import Path

import numpy as np
import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import create_engine

from chembook3d import investigation as inv
from chembook3d.models import Base
from tests.test_overlay import xyz_text
from tests.test_pathway import get, history, node, patch, post

DATA = Path(__file__).parent / "fixtures" / "sterics"
REFERENCE = list(csv.DictReader(io.StringIO((DATA / "reference_data.csv").read_text())))

# A centre at the origin with one carbon (1.7 × 1.17 Å) far enough into the NE quadrant to
# stay in it, mostly below the xy-plane; atom 2 sets the z-axis (it goes to -z) and atom 3
# the xz-plane (at +x). Both are left out of the volume.
PROBE = [
    ("Ru", 0.0, 0.0, 0.0),
    ("N", 0.0, 0.0, -2.0),
    ("Cl", 2.0, 0.0, 0.0),
    ("C", 2.2, 2.2, -1.0),
]
PROBE_ATOMS = {"centre": [1], "z_axis": [2], "xz_plane": [3], "excluded": [2, 3]}


def turned(atoms):
    """The atoms turned about an odd axis and shifted: a frame-independent result must not
    change."""
    angle = 0.8
    axis = np.array([0.3, -1.0, 2.0])
    axis /= np.linalg.norm(axis)
    k = np.array([[0, -axis[2], axis[1]], [axis[2], 0, -axis[0]], [-axis[1], axis[0], 0]])
    rotation = np.eye(3) + np.sin(angle) * k + (1 - np.cos(angle)) * k @ k
    return [(e, *(rotation @ np.array(v) + np.array([4.0, -1.0, 2.5]))) for e, *v in atoms]


def profile(client, name="Ru pocket", **fields) -> dict:
    return post(client, "/steric-profiles", {"name": name, **fields})


def compute(client, profile_id: str, node_ids: list[str], maps=False) -> list[dict]:
    return post(
        client,
        f"/steric-profiles/{profile_id}/compute",
        {"node_ids": node_ids, "maps": maps},
        status=200,
    )


def test_migration_matches_the_models(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'db.sqlite'}")
    with engine.begin() as connection:
        inv.command.upgrade(inv._alembic_config(connection), "head")
        context = MigrationContext.configure(connection)
        assert compare_metadata(context, Base.metadata) == []


@pytest.mark.parametrize("row", REFERENCE, ids=[r["idx"] for r in REFERENCE])
def test_buried_volume_matches_sambvca(open_client, row):
    # T-STER-01, B6: SambVca's %V_bur (3.5 Å, Bondi ×1.17, no hydrogens, 0.1 Å mesh) for 18
    # complexes, among them Ni(CO)3 with an NHC (2) and an NHC iridium complex (3), as
    # tabulated in morfeus's test data. The metal is atom 1; the listed atoms are left out.
    text = (DATA / f"{row['idx']}.xyz").read_text()
    n = node(open_client, label=f"complex {row['idx']}", xyz=text)
    excluded = [int(i) for i in row["excluded_atoms"].split()]
    p = profile(open_client, atoms={n["id"]: {"centre": [1], "excluded": excluded}})
    (result,) = compute(open_client, p["id"], [n["id"]])
    assert result["error"] is None
    assert result["values"]["buried_percent"] == pytest.approx(float(row["buried_volume"]), abs=0.3)
    assert result["values"]["quadrants"] is None  # no orientation given


def test_quadrants_octants_and_map_in_the_frame(open_client):
    # T-STER-02: the frame puts the z-axis atom at -z and the xz-plane atom at +x, so the
    # carbon is buried only in the NE quadrant, below the xy-plane, wherever the molecule is.
    a = node(open_client, label="probe", xyz=xyz_text(PROBE))
    b = node(open_client, label="probe turned", xyz=xyz_text(turned(PROBE)))
    p = profile(open_client, atoms={a["id"]: PROBE_ATOMS, b["id"]: {"same_as": a["id"]}})
    first, second = compute(open_client, p["id"], [a["id"], b["id"]], maps=True)
    values = first["values"]
    assert values["buried_percent"] > 0
    assert values["quadrants"]["NE"] > 0
    assert values["quadrants"]["NW"] == values["quadrants"]["SW"] == values["quadrants"]["SE"] == 0
    octants = values["octants"]
    assert octants["NE-"] > octants["NE+"] > 0
    assert (octants["NE+"] + octants["NE-"]) / 2 == pytest.approx(values["quadrants"]["NE"])
    assert sum(octants.values()) == octants["NE+"] + octants["NE-"]
    assert sum(values["quadrants"].values()) / 4 == pytest.approx(values["buried_percent"])
    for key in ("buried_percent",):
        assert second["values"][key] == pytest.approx(values[key], abs=0.2)
    assert second["values"]["quadrants"]["NE"] == pytest.approx(values["quadrants"]["NE"], abs=0.5)

    # The map: the carbon's top over (2.2, 2.2) is z = -1 + 1.7 × 1.17; nothing over the SW.
    grid = first["map"]
    x = grid["x"]
    assert grid["limit"] == 3.5 and x[0] == -3.5 and x[-1] == 3.5 and len(x) == 71
    top = grid["z"][x.index(2.2)][x.index(2.2)]
    assert top == pytest.approx(-1 + 1.7 * 1.17, abs=1e-3)
    assert grid["z"][x.index(-1.0)][x.index(-1.0)] is None
    turned_top = second["map"]["z"][x.index(2.2)][x.index(2.2)]
    assert turned_top == pytest.approx(top, abs=1e-3)

    # A difference map of the two is zero where both have a surface.
    diff = post(
        open_client,
        f"/steric-profiles/{p['id']}/difference",
        {"first_id": a["id"], "second_id": b["id"]},
        status=200,
    )
    known = [v for row in diff["z"] for v in row if v is not None]
    assert known and max(abs(v) for v in known) < 1e-3


def test_centroid_centre_hydrogens_radii_and_mesh(open_client):
    # T-STER-03: a centroid centre, hydrogens, CRC radii and a coarser mesh all change the
    # value, and every setting is checked.
    atoms = [("C", -0.5, 0.0, 0.0), ("C", 0.5, 0.0, 0.0), ("H", 0.0, 1.8, 0.0), ("Ru", 9, 9, 9)]
    n = node(open_client, label="pair", xyz=xyz_text(atoms))
    p = profile(open_client, atoms={n["id"]: {"centre": [1, 2]}})
    base = compute(open_client, p["id"], [n["id"]])[0]["values"]
    assert base["atoms_counted"] == 3  # both carbons and the far ruthenium; no hydrogen
    assert base["missing_radii"] == ["Ru"]  # Bondi has no Ru: morfeus uses 2.0 Å
    with_h = patch(open_client, f"/steric-profiles/{p['id']}", {"include_hydrogens": True})
    assert with_h["include_hydrogens"] is True
    hydrogens = compute(open_client, p["id"], [n["id"]])[0]["values"]
    assert hydrogens["buried_percent"] > base["buried_percent"]
    # CRC gives H 1.10 Å against Bondi's 1.20 Å, and has Ru.
    patch(open_client, f"/steric-profiles/{p['id']}", {"radii": "crc"})
    crc = compute(open_client, p["id"], [n["id"]])[0]["values"]
    assert crc["missing_radii"] == [] and crc["buried_percent"] < hydrogens["buried_percent"]
    patch(
        open_client,
        f"/steric-profiles/{p['id']}",
        {"include_hydrogens": False, "radii": "bondi", "radii_scale": 1.0},
    )
    smaller = compute(open_client, p["id"], [n["id"]])[0]["values"]
    assert smaller["buried_percent"] < base["buried_percent"]
    patch(open_client, f"/steric-profiles/{p['id']}", {"radii_scale": 1.17, "mesh": 0.2})
    coarse = compute(open_client, p["id"], [n["id"]])[0]["values"]
    assert coarse["buried_percent"] == pytest.approx(base["buried_percent"], abs=1.0)

    for change, reason in [
        ({"radius": 0.5}, "between 1.0 and 10.0"),
        ({"radii": "vdw"}, "radii must be one of"),
        ({"mesh": 0.05, "radius": 9}, "coarser mesh"),
        ({"name": ""}, "needs a name"),
        ({"atoms": {n["id"]: {"centre": []}}}, "choose the centre"),
        ({"atoms": {n["id"]: {"centre": [1], "z_axis": [3]}}}, "both z-axis and xz-plane"),
        ({"atoms": {n["id"]: {"centre": [1], "excluded": [9]}}}, "excluded atom 9 does not"),
        ({"atoms": {n["id"]: {"centre": [1, 1]}}}, "listed twice"),
    ]:
        response = open_client.patch(f"/api/steric-profiles/{p['id']}", json=change)
        assert response.status_code == 422 and reason in response.json()["detail"], change

    # Orientation atoms that fix no frame are refused when computing.
    patch(
        open_client,
        f"/steric-profiles/{p['id']}",
        {"atoms": {n["id"]: {"centre": [1], "z_axis": [1], "xz_plane": [2]}}},
    )
    (failed,) = compute(open_client, p["id"], [n["id"]])
    assert failed["values"] is None and "lie on the centre" in failed["error"]
    patch(
        open_client,
        f"/steric-profiles/{p['id']}",
        {"atoms": {n["id"]: {"centre": [1], "z_axis": [2], "xz_plane": [2]}}},
    )
    assert "do not fix a plane" in compute(open_client, p["id"], [n["id"]])[0]["error"]


def test_results_go_out_of_date_and_profiles_follow_nodes(open_client):
    # T-STER-04, B3, B4, A32
    a = node(open_client, label="a", xyz=xyz_text(PROBE))
    b = node(open_client, label="b", xyz=xyz_text([("O", 9, 9, 9), *PROBE]))
    before = len(history(open_client))
    p = profile(open_client, atoms={a["id"]: PROBE_ATOMS})
    # B3: numbers are shared only between nodes with the same elements in the same order.
    refused = open_client.patch(
        f"/api/steric-profiles/{p['id']}", json={"atoms": {b["id"]: {"same_as": a["id"]}}}
    )
    assert refused.status_code == 422 and "same elements" in refused.json()["detail"]
    shifted = {"centre": [2], "z_axis": [3], "xz_plane": [4], "excluded": [3, 4]}
    p = patch(open_client, f"/steric-profiles/{p['id']}", {"atoms": {b["id"]: shifted}})
    assert p["atoms"][b["id"]] == shifted and p["results"] == {}

    compute(open_client, p["id"], [a["id"], b["id"]])
    listed = get(open_client, "/steric-profiles")[0]
    assert listed["results"][a["id"]]["stale"] is None
    assert listed["results"][a["id"]]["values"]["buried_percent"] == pytest.approx(
        listed["results"][b["id"]]["values"]["buried_percent"]
    )

    # Changing the settings, a node's atoms, or its coordinates marks results out of date;
    # the colour scale of the map does not.
    patch(open_client, f"/steric-profiles/{p['id']}", {"map_limit": 2.0})
    assert get(open_client, "/steric-profiles")[0]["results"][a["id"]]["stale"] is None
    patch(open_client, f"/steric-profiles/{p['id']}", {"radius": 3.0})
    listed = get(open_client, "/steric-profiles")[0]
    assert listed["results"][a["id"]]["stale"] == "the profile's settings changed"
    compute(open_client, p["id"], [a["id"]])
    patch(
        open_client,
        f"/steric-profiles/{p['id']}",
        {"atoms": {a["id"]: {**PROBE_ATOMS, "excluded": [2]}}},
    )
    assert get(open_client, "/steric-profiles")[0]["results"][a["id"]]["stale"] == (
        "the atoms of this node in the profile changed"
    )
    compute(open_client, p["id"], [a["id"]])
    moved = [("Ru", 0.0, 0.0, 0.0), ("N", 0.0, 0.0, -2.0), ("Cl", 2.0, 0.0, 0.0)]
    moved.append(("C", 2.0, 2.2, -1.0))
    response = open_client.put(f"/api/nodes/{a['id']}/geometry", json={"xyz": xyz_text(moved)})
    assert response.status_code == 200, response.text
    assert get(open_client, "/steric-profiles")[0]["results"][a["id"]]["stale"] == (
        "the coordinates changed"
    )

    # A node not in the profile gets the reason, not an error for the whole request.
    c = node(open_client, label="c", xyz=xyz_text(PROBE))
    (missing,) = compute(open_client, p["id"], [c["id"]])
    assert missing["values"] is None and "is not in" in missing["error"]

    # Layout-like: no history (only the new coordinates and creating c wrote entries); the
    # profile survives a reopen, and deleting a node takes it and its result out of the profile.
    assert len(history(open_client)) == before + 2
    folder = get(open_client, "/investigation")["folder"]
    assert open_client.post("/api/investigations/close").status_code in (200, 204)
    reopened = open_client.post("/api/investigations/open", json={"folder": folder})
    assert reopened.status_code == 200, reopened.text
    assert set(get(open_client, "/steric-profiles")[0]["atoms"]) == {a["id"], b["id"]}
    assert open_client.delete(f"/api/nodes/{b['id']}").status_code in (200, 204)
    listed = get(open_client, "/steric-profiles")[0]
    assert set(listed["atoms"]) == {a["id"]} and set(listed["results"]) == {a["id"]}
    clash = open_client.post("/api/steric-profiles", json={"name": "Ru pocket"})
    assert clash.status_code == 422 and "already" in clash.json()["detail"]
    assert open_client.delete(f"/api/steric-profiles/{p['id']}").status_code == 204
    assert get(open_client, "/steric-profiles") == []


def test_table_csv_carries_values_and_settings(open_client):
    # T-STER-05, B5
    a = node(open_client, label="probe", xyz=xyz_text(PROBE))
    b = node(open_client, label="outside", xyz=xyz_text(PROBE))
    p = profile(open_client, atoms={a["id"]: PROBE_ATOMS})
    response = open_client.post(
        f"/api/steric-profiles/{p['id']}/table.csv", json={"node_ids": [a["id"], b["id"]]}
    )
    assert response.status_code == 200
    text = response.content.decode("utf-8")
    assert text.startswith("﻿")
    rows = list(csv.reader(io.StringIO(text.lstrip("﻿"))))
    header, probe, outside = rows
    assert header[:3] == ["Node", "%V_bur", "NE %V_bur"] and "Excluded atoms" in header
    record = dict(zip(header, probe, strict=True))
    assert record["Node"] == "probe" and float(record["NE %V_bur"]) > 0
    assert float(record["%V_bur"]) == pytest.approx(
        sum(float(record[f"{q} %V_bur"]) for q in ("NE", "NW", "SW", "SE")) / 4, abs=0.1
    )
    assert record["Radii"] == "Bondi ×1.17" and record["Hydrogens"] == "left out"
    assert record["Centre atoms"] == "1" and record["Excluded atoms"] == "2 3"
    assert outside[0] == "outside" and "is not in" in outside[-1] and outside[1] == ""
    assert not math.isnan(float(record["Sphere radius (Å)"]))
