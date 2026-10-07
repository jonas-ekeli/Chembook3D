"""The structures of an optimization or a scan, for the movie in the 3D view (D101, FR-3D-08)."""

import numpy as np
import pytest

from chembook3d.parsers import gaussian, orca
from chembook3d.services import geometry
from tests.test_import import calculations, commit, folder, upload
from tests.test_orca_xtb_parsers import read


def steps(client, calculation_id: str) -> dict:
    response = client.get(f"/api/calculations/{calculation_id}/steps")
    assert response.status_code == 200, response.text
    return response.json()


def import_file(client, program: str, name: str, text: str | None = None) -> dict:
    plan = upload(client, name, text if text is not None else read(program, name))
    assert plan["blockers"] == [], plan["blockers"]
    node_id = commit(client, plan)["node_id"]
    return calculations(client, node_id)[0]


# ---------- parsers ----------


def test_gaussian_optimization_pairs_each_structure_with_its_energy():
    step = gaussian.parse(read("gaussian", "aziridinium-phos-full-c1.log")).steps[0]
    assert step.scan is None and len(step.geometries) == len(step.geometry_energies)
    # Every structure has its SCF energy but the final one, printed again after the optimization.
    assert all(e is not None for e in step.geometry_energies[:-1])
    assert step.geometry_energies[-1] is None
    assert step.geometry_energies[-2] == step.scf_energy
    assert step.converged_geometries == [len(step.geometries) - 2]


def test_gaussian_relaxed_scan_points_and_converged_structures():
    step = gaussian.parse(read("gaussian", "dvb_scan_relaxed.log")).steps[0]
    assert step.job_type == "optimization" and step.scan == "relaxed"
    assert len(step.converged_geometries) == 13
    points = [step.geometry_points[i] for i in step.converged_geometries]
    assert points == list(range(1, 14))
    # The first point starts on an optimized structure and converges at once.
    assert step.converged_geometries[0] == 0 and step.geometry_energies[0] == -382.308266559


def test_gaussian_rigid_scan_every_structure_is_a_point():
    step = gaussian.parse(read("gaussian", "dvb_scan_unrelaxed.log")).steps[0]
    assert step.scan == "rigid" and len(step.geometries) == 13
    assert step.geometry_points == list(range(1, 14))
    assert step.converged_geometries == list(range(13))
    assert step.geometry_energies[0] == -382.294279146


def test_orca_parameter_scan():
    step = orca.parse(read("orca", "dvb_scan_unrelaxed.out")).steps[0]
    assert step.scan == "rigid" and len(step.geometries) == 12
    assert step.converged_geometries == list(range(12))
    assert all(e is not None for e in step.geometry_energies)


def relaxed_orca_scan() -> str:
    """An ORCA relaxed scan of one point: the optimization's cycles under a scan step banner, as
    ORCA prints them (the real sample is 6 MB)."""
    lines = read("orca", "dvb_gopt.out").splitlines()
    first = next(i for i, line in enumerate(lines) if "GEOMETRY OPTIMIZATION CYCLE   1" in line)
    banner = "         *               RELAXED SURFACE SCAN STEP   1               *"
    return "\n".join([*lines[: first - 1], banner, *lines[first - 1 :]]) + "\n"


def test_orca_optimization_and_relaxed_scan_mark_the_final_evaluation():
    step = orca.parse(read("orca", "dvb_gopt.out")).steps[0]
    assert step.scan is None and len(step.geometries) == 4
    assert step.geometry_energies[-1] == step.scf_energy
    assert step.converged_geometries == [3]  # the final evaluation at the stationary point
    scan = orca.parse(relaxed_orca_scan()).steps[0]
    assert scan.scan == "relaxed" and scan.geometry_points == [1, 1, 1, 1]
    assert scan.converged_geometries == [3]


# ---------- the API ----------


def test_optimization_steps_end_on_the_final_geometry(open_client):
    calc = import_file(open_client, "gaussian", "aziridinium-phos-full-c1.log")
    data = steps(open_client, calc["id"])
    parsed = gaussian.parse(read("gaussian", "aziridinium-phos-full-c1.log")).steps[0]
    # The final structure, printed again without an energy, is shown once.
    assert data["scan"] is None and data["points"] == 0
    assert len(data["frames"]) == len(parsed.geometries) - 1
    assert [f["energy"] for f in data["frames"]] == parsed.geometry_energies[:-1]
    assert [f["converged"] for f in data["frames"]] == [False] * (len(data["frames"]) - 1) + [True]
    # The last structure is the calculation's final geometry, as stored, unmoved.
    final = data["frames"][-1]["geometry"]
    stored = [[a.element, a.x, a.y, a.z] for a in parsed.final_geometry]
    assert all(
        p[0] == q[0] and max(abs(a - b) for a, b in zip(p[1:], q[1:], strict=True)) < 1e-5
        for p, q in zip(final, stored, strict=True)
    )
    # Earlier structures are turned onto the final one: no better fit is left.
    first = data["frames"][0]["geometry"]
    assert geometry.place(final, first).rmsd_all == pytest.approx(
        geometry.aligned_rmsd(final, first), abs=1e-6
    )


def test_relaxed_scan_gives_one_converged_structure_per_point(open_client):
    calc = import_file(open_client, "gaussian", "dvb_scan_relaxed.log")
    data = steps(open_client, calc["id"])
    assert data["scan"] == "relaxed" and data["points"] == 13
    converged = [f for f in data["frames"] if f["converged"]]
    assert [f["point"] for f in converged] == list(range(1, 14))
    assert all(f["energy"] is not None for f in data["frames"])
    # The scanned dihedral (atoms 10 9 4 3) moves 30° from point to point.
    angles = [xyz_dihedral(f["geometry"], (10, 9, 4, 3)) for f in converged]
    steps_between = [((b - a + 180) % 360) - 180 for a, b in zip(angles, angles[1:], strict=False)]
    assert steps_between == pytest.approx([steps_between[0]] * 12, abs=0.05)
    assert abs(steps_between[0]) == pytest.approx(30, abs=0.05)


def test_rigid_scan_and_orca_steps(open_client):
    calc = import_file(open_client, "gaussian", "dvb_scan_unrelaxed.log")
    data = steps(open_client, calc["id"])
    assert data["scan"] == "rigid" and data["points"] == 13
    assert all(f["converged"] for f in data["frames"])
    calc = import_file(open_client, "orca", "dvb_gopt.out")
    data = steps(open_client, calc["id"])
    # ORCA evaluates the energy once more after its last step; that structure is the converged one.
    assert len(data["frames"]) == 4 and data["frames"][-1]["converged"]
    assert data["frames"][-1]["energy"] == calc["result"]["energy"]


def test_steps_need_the_copied_file(open_client):
    calc = import_file(open_client, "gaussian", "aziridinium-phos-full-c1.log")
    for path in (folder(open_client) / "files").rglob("*.log"):
        path.unlink()
    response = open_client.get(f"/api/calculations/{calc['id']}/steps")
    assert response.status_code == 422 and "cannot be read" in response.json()["detail"]
    assert open_client.get("/api/calculations/nope/steps").status_code == 404


def xyz_dihedral(rows: list, atoms: tuple[int, int, int, int]) -> float:
    p = [np.array(rows[i - 1][1:4], dtype=float) for i in atoms]
    b0, b1, b2 = p[0] - p[1], p[2] - p[1], p[3] - p[2]
    b1 /= np.linalg.norm(b1)
    v = b0 - np.dot(b0, b1) * b1
    w = b2 - np.dot(b2, b1) * b1
    return float(np.degrees(np.arctan2(np.dot(np.cross(b1, v), w), np.dot(v, w))))
