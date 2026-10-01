"""Overlays on chosen atoms and alignment sets (FR-3D-04, FR-3D-07, D80, A31;
T-OVL-01…03)."""

import numpy as np
import pytest

from tests.conftest import WATER
from tests.test_pathway import get, history, node, patch, post

# A chiral CHFClBr: a mirror image cannot be rotated onto it.
CHIRAL = [
    ("C", 0.0, 0.0, 0.0),
    ("H", 0.63, 0.63, 0.63),
    ("F", -0.80, -0.80, 0.80),
    ("Cl", -1.02, 1.02, -1.02),
    ("Br", 1.12, -1.12, -1.12),
]


def xyz_text(atoms, comment="") -> str:
    lines = [str(len(atoms)), comment]
    lines += [f"{e} {x:.6f} {y:.6f} {z:.6f}" for e, x, y, z in atoms]
    return "\n".join(lines) + "\n"


def moved(atoms, mirror=False):
    """The atoms turned about an odd axis and shifted, optionally reflected first."""
    angle = 1.1
    axis = np.array([1.0, 2.0, 0.5])
    axis /= np.linalg.norm(axis)
    k = np.array([[0, -axis[2], axis[1]], [axis[2], 0, -axis[0]], [-axis[1], axis[0], 0]])
    rotation = np.eye(3) + np.sin(angle) * k + (1 - np.cos(angle)) * k @ k
    out = []
    for e, *xyz in atoms:
        v = np.array(xyz, dtype=float)
        if mirror:
            v[0] = -v[0]
        v = rotation @ v + np.array([3.0, -2.0, 5.0])
        out.append((e, *v))
    return out


def coords(xyz: str) -> np.ndarray:
    return np.array([[float(v) for v in line.split()[1:4]] for line in xyz.splitlines()[2:]])


def overlay(client, body: dict, status: int = 200) -> dict:
    response = client.post("/api/overlay", json=body)
    assert response.status_code == status, response.text
    return response.json()


def test_overlay_on_all_atoms_when_they_correspond(open_client):
    rotated = """3
water turned
O 0.117300 0.000000 1.000000
H -0.469200 0.757200 1.000000
H -0.469200 -0.757200 1.000000
"""
    a = node(open_client, label="a", xyz=WATER)
    b = node(open_client, label="b", xyz=rotated)
    result = overlay(open_client, {"node_ids": [a["id"], b["id"]]})
    reference, placed = result["structures"]
    assert reference["reference"] is True and reference["rmsd_atoms"] is None
    assert placed["rotated"] is True and placed["mirrored"] is False
    assert placed["rmsd_atoms"] < 1e-6 and placed["rmsd_all"] < 1e-6
    first_atom = placed["xyz"].splitlines()[2].split()
    assert first_atom[0] == "O"
    assert [float(v) for v in first_atom[1:]] == pytest.approx([0.0, 0.0, 0.1173], abs=1e-6)

    # Different atoms and no chosen ones: only the centres are matched, as before.
    other = node(open_client, label="c", xyz="2\n\nH 0 0 0\nH 0 0 0.74\n")
    centred = overlay(open_client, {"node_ids": [a["id"], other["id"]]})["structures"][1]
    assert centred["rotated"] is False and centred["rmsd_atoms"] is None


def test_overlay_on_chosen_atoms_with_different_numbering(open_client):
    # T-OVL-01: the moving structure has two extra atoms, one before the core, so its
    # numbers differ; it still lands exactly on the reference's core.
    reference = node(open_client, label="core", xyz=xyz_text(CHIRAL))
    extra = [("O", 9.0, 9.0, 9.0), *moved(CHIRAL), ("N", -7.0, 1.0, 2.0)]
    bigger = node(open_client, label="with olefin", xyz=xyz_text(extra))
    body = {
        "node_ids": [reference["id"], bigger["id"]],
        "align": "atoms",
        "atoms": {reference["id"]: [1, 2, 3, 4, 5], bigger["id"]: [2, 3, 4, 5, 6]},
    }
    placed = overlay(open_client, body)["structures"][1]
    assert placed["rmsd_atoms"] == pytest.approx(0.0, abs=1e-6)
    assert placed["rmsd_all"] is None  # the atoms do not correspond one to one
    assert coords(placed["xyz"])[1:6] == pytest.approx(np.array([a[1:] for a in CHIRAL]), abs=1e-6)

    # The reference can be any of the structures.
    swapped = overlay(open_client, {**body, "reference_id": bigger["id"]})["structures"]
    assert [s["reference"] for s in swapped] == [False, True]
    assert swapped[0]["rmsd_atoms"] == pytest.approx(0.0, abs=1e-6)


@pytest.mark.parametrize(
    ("atoms", "message"),
    [
        ([[1, 2, 3, 4, 5], [2, 3, 4, 6, 5]], "is Br, but the reference atom"),
        ([[1, 2], [2, 3]], "at least 3 atoms"),
        ([[1, 2, 3, 4, 5], [2, 3, 4, 5, 9]], "has atoms 1 to 7, so atom 9 does not exist"),
        ([[1, 2, 3, 3], [2, 3, 4, 4]], "atom 3 of “core” is listed twice"),
        ([[1, 2, 3, 4], [2, 3, 4, 5, 6]], "5 atoms are listed, but the reference has 4"),
    ],
)
def test_overlay_refuses_atoms_that_cannot_be_paired(open_client, atoms, message):
    reference = node(open_client, label="core", xyz=xyz_text(CHIRAL))
    extra = [("O", 9.0, 9.0, 9.0), *moved(CHIRAL), ("N", -7.0, 1.0, 2.0)]
    bigger = node(open_client, label="with olefin", xyz=xyz_text(extra))
    body = {
        "node_ids": [reference["id"], bigger["id"]],
        "align": "atoms",
        "atoms": {reference["id"]: atoms[0], bigger["id"]: atoms[1]},
    }
    response = open_client.post("/api/overlay", json=body)
    assert response.status_code == 422
    assert message in response.json()["detail"]


def test_mirror_image_only_when_allowed(open_client):
    # T-OVL-02
    a = node(open_client, label="pro-R", xyz=xyz_text(CHIRAL))
    b = node(open_client, label="pro-S", xyz=xyz_text(moved(CHIRAL, mirror=True)))
    body = {"node_ids": [a["id"], b["id"]], "align": "all"}
    plain = overlay(open_client, body)["structures"][1]
    assert plain["mirrored"] is False and plain["rmsd_all"] > 0.1
    mirrored = overlay(open_client, {**body, "allow_mirror": True})["structures"][1]
    assert mirrored["mirrored"] is True and mirrored["rmsd_all"] == pytest.approx(0, abs=1e-6)

    # A structure that is not a mirror image is not reported as one, even when allowed.
    c = node(open_client, label="same", xyz=xyz_text(moved(CHIRAL)))
    same = overlay(open_client, {"node_ids": [a["id"], c["id"]], "allow_mirror": True})
    assert same["structures"][1]["mirrored"] is False


def test_no_alignment_keeps_the_stored_coordinates(open_client):
    a = node(open_client, label="a", xyz=xyz_text(CHIRAL))
    shifted = [(e, x + 1.0, y, z) for e, x, y, z in CHIRAL]
    b = node(open_client, label="b", xyz=xyz_text(shifted))
    result = overlay(open_client, {"node_ids": [a["id"], b["id"]], "align": "none"})
    placed = result["structures"][1]
    assert placed["rotated"] is False
    assert coords(placed["xyz"]) == pytest.approx(np.array([s[1:] for s in shifted]))
    assert placed["rmsd_all"] == pytest.approx(1.0)
    assert placed["rmsd_atoms"] is None
    measured = overlay(
        open_client,
        {
            "node_ids": [a["id"], b["id"]],
            "align": "none",
            "atoms": {a["id"]: [1, 2, 3], b["id"]: [1, 2, 3]},
        },
    )
    assert measured["structures"][1]["rmsd_atoms"] == pytest.approx(1.0)


def test_overlay_of_many_structures_and_its_limit(open_client):
    ids = [node(open_client, label=f"n{i}", xyz=xyz_text(moved(CHIRAL)))["id"] for i in range(13)]
    result = overlay(open_client, {"node_ids": ids[:12]})
    assert len(result["structures"]) == 12
    assert all(s["rmsd_all"] < 1e-6 for s in result["structures"][1:])
    too_many = open_client.post("/api/overlay", json={"node_ids": ids})
    assert too_many.status_code == 422 and "2 to 12" in too_many.json()["detail"]
    empty = node(open_client, label="plan")
    no_xyz = open_client.post("/api/overlay", json={"node_ids": [ids[0], empty["id"]]})
    assert no_xyz.status_code == 422 and "no coordinates" in no_xyz.json()["detail"]


def test_alignment_sets_are_saved_extended_and_follow_deletions(open_client, tmp_path):
    # T-OVL-03
    a = node(open_client, label="a", xyz=xyz_text(CHIRAL))
    b = node(open_client, label="b", xyz=xyz_text([("O", 9, 9, 9), *moved(CHIRAL)]))
    before = len(history(open_client))
    created = post(
        open_client,
        "/alignment-sets",
        {"name": "Ru–CAAC core", "atoms": {a["id"]: [1, 2, 3, 4], b["id"]: [2, 3, 4, 5]}},
    )
    assert created["atoms"] == {a["id"]: [1, 2, 3, 4], b["id"]: [2, 3, 4, 5]}

    clash = open_client.post("/api/alignment-sets", json={"name": "Ru–CAAC core", "atoms": {}})
    assert clash.status_code == 422 and "already" in clash.json()["detail"]
    bad = open_client.patch(
        f"/api/alignment-sets/{created['id']}", json={"atoms": {a["id"]: [1, 2, 30]}}
    )
    assert bad.status_code == 422

    c = node(open_client, label="c", xyz=xyz_text(moved(CHIRAL)))
    extended = patch(
        open_client,
        f"/alignment-sets/{created['id']}",
        {"name": "Core", "atoms": {c["id"]: [1, 2, 3, 4]}},
    )
    assert extended["name"] == "Core" and set(extended["atoms"]) == {a["id"], b["id"], c["id"]}
    removed = patch(open_client, f"/alignment-sets/{created['id']}", {"atoms": {a["id"]: None}})
    assert set(removed["atoms"]) == {b["id"], c["id"]}
    # Layout-like data: no history entries (A31); only creating node c wrote one.
    assert len(history(open_client)) == before + 1

    # Reopening keeps the set; deleting a node takes it out of the set.
    folder = get(open_client, "/investigation")["folder"]
    assert open_client.post("/api/investigations/close").status_code in (200, 204)
    reopened = open_client.post("/api/investigations/open", json={"folder": folder})
    assert reopened.status_code == 200, reopened.text
    assert get(open_client, "/alignment-sets")[0]["atoms"][b["id"]] == [2, 3, 4, 5]
    assert open_client.delete(f"/api/nodes/{c['id']}").status_code in (200, 204)
    assert set(get(open_client, "/alignment-sets")[0]["atoms"]) == {b["id"]}

    assert open_client.delete(f"/api/alignment-sets/{created['id']}").status_code == 204
    assert get(open_client, "/alignment-sets") == []
