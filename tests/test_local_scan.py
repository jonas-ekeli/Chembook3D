"""A relaxed scan on this computer (D218, FR-LOC-09, T-LOC-18..21): one constrained
optimization per point with geomeTRIC, imported as a scan node of its own.

The scans run with the stand-in engine `stub` (harmonic bonds), as in test_local_jobs.py; the
real GFN2-xTB one only with CHEMBOOK3D_REAL_TOOLS=1.
"""

import json

import numpy as np
import pytest

from chembook3d import local_worker
from tests.conftest import WATER
from tests.test_local_jobs import (  # noqa: F401 - the fixtures
    PAGE,
    REAL,
    finished,
    folder_of,
    no_worker_left,
    page,
    stub_tools,
    wait_for,
)

OH = {"kind": "distance", "atoms": [0, 1], "start": 0.9, "end": 1.2}
SCAN = {"coordinates": [OH], "points": 4}


def test_scan_values_and_constraint_text():
    """T-LOC-18: concerted coordinates step together; each point holds them all."""
    assert [v[0] for v in local_worker.scan_values(SCAN)] == pytest.approx([0.9, 1.0, 1.1, 1.2])
    both = {"coordinates": [OH, {**OH, "atoms": [0, 2], "start": 1.2, "end": 0.9}], "points": 4}
    assert local_worker.scan_values(both)[1] == pytest.approx([1.0, 1.1])
    text = local_worker.read_constraints(
        {"constraints": {"frozen": [2]}}, [("distance", [0, 1], 1.05), ("angle", [1, 0, 2], 100)]
    )
    assert text == "$freeze\nxyz 3\n$set\ndistance 1 2 1.050000\nangle 2 1 3 100.000000\n"


@pytest.mark.usefixtures("stub_tools")
def test_relaxed_scan_is_imported_as_a_scan_node(open_client):
    """T-LOC-19: each point holds the distance at its value, starts from the one before and is
    imported as a relaxed scan on a new node beside the start, which keeps its structure."""
    start = open_client.post("/api/nodes", json={"label": "w", "xyz": WATER}).json()
    job = open_client.post(
        "/api/local/jobs", json={"node_id": start["id"], "method": "stub", "scan": SCAN}
    ).json()
    assert job["scan"]["points"] == 4 and job["id"].endswith("scan")
    done = wait_for(open_client, job["id"], finished, timeout=120)
    folder = folder_of(open_client) / "local-jobs" / job["id"]
    assert done["status"] == "converged", (folder / "worker.log").read_text()
    result = json.loads((folder / "result.json").read_text())
    points = result["scan"]["points"]
    assert [p["values"][0] for p in points] == pytest.approx([0.9, 1.0, 1.1, 1.2])
    assert all(p["converged"] for p in points)
    frames = np.array(result["frames"])
    for p in points:
        r = np.linalg.norm(frames[p["frame"]][0] - frames[p["frame"]][1])
        assert r == pytest.approx(p["values"][0], abs=2e-3)
    assert result["frame_points"][0] == 1 and result["frame_points"][-1] == 4
    assert done["result"]["scan"]["points"][0]["values"] == pytest.approx([0.9])

    imported = open_client.post(f"/api/local/jobs/{job['id']}/import").json()["imported"]
    assert imported["node_id"] != start["id"] and not imported["derived"]
    node = open_client.get(f"/api/nodes/{imported['node_id']}").json()
    assert node["label"] == "w scan B(1,2)"
    calc = open_client.get(f"/api/nodes/{imported['node_id']}/calculations").json()[0]
    assert "scan B(1,2)" in calc["constraints"]
    steps = open_client.get(f"/api/calculations/{calc['id']}/steps").json()
    assert steps["scan"] == "relaxed" and steps["points"] == 4
    assert [f["point"] for f in steps["frames"] if f["converged"]] == [1, 2, 3, 4]
    # the start node is left as it was
    assert open_client.get(f"/api/nodes/{start['id']}/calculations").json() == []
    assert open_client.get(f"/api/nodes/{start['id']}").json()["xyz"] == start["xyz"]


@pytest.mark.usefixtures("stub_tools")
def test_a_scan_without_a_start_begins_where_the_structure_is(open_client):
    """T-LOC-28 (D222): a coordinate with no start takes its value in the node's structure, as
    the dialog's empty "from" field does; one with no atoms to measure is refused as before."""
    node = open_client.post("/api/nodes", json={"label": "w", "xyz": WATER}).json()
    rows = [line.split() for line in WATER.strip().splitlines()[2:]]
    now = np.linalg.norm(np.array(rows[0][1:4], float) - np.array(rows[1][1:4], float))
    scan = {"coordinates": [{"kind": "distance", "atoms": [0, 1], "end": 1.3}], "points": 2}
    job = open_client.post(
        "/api/local/jobs", json={"node_id": node["id"], "method": "stub", "scan": scan}
    )
    assert job.status_code == 200, job.text
    assert job.json()["scan"]["coordinates"][0]["start"] == pytest.approx(now)
    wait_for(open_client, job.json()["id"], finished, timeout=120)

    same = {"coordinates": [{"kind": "distance", "atoms": [0, 1], "end": float(now)}], "points": 2}
    refused = open_client.post(
        "/api/local/jobs", json={"node_id": node["id"], "method": "stub", "scan": same}
    )
    assert refused.status_code == 422 and "start and end differ" in refused.json()["detail"]
    off = {"coordinates": [{"kind": "distance", "atoms": [0, 9], "end": 1.3}], "points": 2}
    refused = open_client.post(
        "/api/local/jobs", json={"node_id": node["id"], "method": "stub", "scan": off}
    )
    assert refused.status_code == 422 and "numbered 1 to 3" in refused.json()["detail"]


@pytest.mark.usefixtures("stub_tools")
@pytest.mark.parametrize(
    ("scan", "extra", "words"),
    [
        ({**SCAN, "coordinates": [{**OH, "kind": "angle"}]}, {}, "3 different atoms"),
        ({**SCAN, "coordinates": [{**OH, "atoms": [0, 5]}]}, {}, "numbered 1 to 3"),
        ({**SCAN, "coordinates": [{**OH, "end": 0.9}]}, {}, "start and end differ"),
        ({**SCAN, "coordinates": [{**OH, "start": 0.2}]}, {}, "above 0.3"),
        ({**SCAN, "coordinates": [OH, {**OH, "atoms": [1, 0]}]}, {}, "scanned twice"),
        ({**SCAN, "coordinates": []}, {}, "1 to 4 coordinates"),
        (
            {
                "coordinates": [{"kind": "angle", "atoms": [1, 0, 2], "start": 100, "end": 190}],
                "points": 4,
            },
            {},
            "between 0° and 180°",
        ),
        (SCAN, {"transition": True}, "transition state"),
        (SCAN, {"constraints": {"distances": [{"atoms": [1, 0]}]}}, "also held"),
    ],
)
def test_a_scan_that_cannot_run_says_why(open_client, scan, extra, words):
    """T-LOC-20"""
    node_id = open_client.post("/api/nodes", json={"label": "w", "xyz": WATER}).json()["id"]
    refused = open_client.post(
        "/api/local/jobs", json={"node_id": node_id, "method": "stub", "scan": scan, **extra}
    )
    assert refused.status_code == 422 and words in refused.json()["detail"], refused.json()


@pytest.mark.skipif(not REAL, reason="needs the installed local tools (CHEMBOOK3D_REAL_TOOLS=1)")
def test_real_tools_gfn2_scan_of_water(open_client, monkeypatch):
    """T-LOC-05, D218: a GFN2-xTB scan of an O-H bond."""
    monkeypatch.delenv("CHEMBOOK3D_LOCAL_PYTHON", raising=False)
    monkeypatch.setenv("CHEMBOOK3D_LOCAL_STUB", "1")
    node_id = open_client.post("/api/nodes", json={"label": "w", "xyz": WATER}).json()["id"]
    job = open_client.post(
        "/api/local/jobs", json={"node_id": node_id, "method": "gfn2", "scan": SCAN}
    ).json()
    done = wait_for(open_client, job["id"], finished, timeout=600)
    assert done["status"] == "converged", done["result"]["error"]
    energies = [p["energy"] for p in done["result"]["scan"]["points"]]
    assert energies[0] > energies[1] < energies[3]  # a minimum near 0.96 Å
    assert open_client.post(f"/api/local/jobs/{job['id']}/import").status_code == 200


@pytest.mark.usefixtures("stub_tools")
def test_a_concerted_scan_drives_both_bonds_together(open_client):
    """T-LOC-21: two O-H bonds, one lengthened while the other shortens, point by point."""
    node_id = open_client.post("/api/nodes", json={"label": "w", "xyz": WATER}).json()["id"]
    other = {**OH, "atoms": [0, 2], "start": 1.2, "end": 0.9}
    scan = {"coordinates": [OH, other], "points": 4}
    job = open_client.post(
        "/api/local/jobs", json={"node_id": node_id, "method": "stub", "scan": scan}
    ).json()
    done = wait_for(open_client, job["id"], finished, timeout=120)
    assert done["status"] == "converged"
    folder = folder_of(open_client) / "local-jobs" / job["id"]
    result = json.loads((folder / "result.json").read_text())
    frames = np.array(result["frames"])
    for p in result["scan"]["points"]:
        f = frames[p["frame"]]
        measured = [np.linalg.norm(f[0] - f[1]), np.linalg.norm(f[0] - f[2])]
        assert measured == pytest.approx(p["values"], abs=2e-3)
    imported = open_client.post(f"/api/local/jobs/{job['id']}/import").json()["imported"]
    node = open_client.get(f"/api/nodes/{imported['node_id']}").json()
    assert node["label"] == "w scan B(1,2)+B(1,3)"
    calc = open_client.get(f"/api/nodes/{imported['node_id']}/calculations").json()[0]
    assert {"scan B(1,2)", "scan B(1,3)"} <= set(calc["constraints"])


GRID = {
    "coordinates": [
        {**OH, "points": 3},
        {**OH, "atoms": [0, 2], "start": 1.2, "end": 0.9, "points": 2},
    ],
    "grid": True,
}


def test_grid_points_run_row_by_row_back_and_forth():
    """T-LOC-22, D219: every pair of values, every other row backwards."""
    cells = [cell for _, _, cell in local_worker.grid_cells(GRID)]
    assert cells == [[0, 0], [0, 1], [1, 1], [1, 0], [2, 0], [2, 1]]
    assert local_worker.scan_values(GRID)[2] == pytest.approx([1.05, 0.9])


@pytest.mark.usefixtures("stub_tools")
def test_a_grid_scan_is_imported_with_its_cells(open_client):
    """T-LOC-23, D219: a 3 x 2 grid of water's two O-H bonds; the node is the lowest point, the
    steps carry the grid, and its constraint is not a scan's, so it is not a path on an edge."""
    node_id = open_client.post("/api/nodes", json={"label": "w", "xyz": WATER}).json()["id"]
    job = open_client.post(
        "/api/local/jobs", json={"node_id": node_id, "method": "stub", "scan": GRID}
    ).json()
    assert job["scan"]["grid"] and job["scan"]["points"] == 6
    done = wait_for(open_client, job["id"], finished, timeout=120)
    assert done["status"] == "converged"
    points = done["result"]["scan"]["points"]
    assert [p["cell"] for p in points][:3] == [[0, 0], [0, 1], [1, 1]]
    imported = open_client.post(f"/api/local/jobs/{job['id']}/import").json()["imported"]
    node = open_client.get(f"/api/nodes/{imported['node_id']}").json()
    assert node["label"] == "w grid B(1,2) × B(1,3)"
    calc = open_client.get(f"/api/nodes/{imported['node_id']}/calculations").json()[0]
    assert calc["constraints"] == ["grid scan B(1,2) × B(1,3)"]
    steps = open_client.get(f"/api/calculations/{calc['id']}/steps").json()
    grid = steps["grid"]
    assert [c["name"] for c in grid["coordinates"]] == ["B(1,2)", "B(1,3)"]
    assert grid["coordinates"][0]["values"] == pytest.approx([0.9, 1.05, 1.2])
    assert len(grid["cells"]) == 6 and steps["points"] == 6
    lowest = min((p for p in points if p["converged"]), key=lambda p: p["energy"])
    assert calc["result"]["energy"] == pytest.approx(lowest["energy"])


@pytest.mark.usefixtures("stub_tools")
@pytest.mark.parametrize(
    ("scan", "method", "words"),
    [
        ({**GRID, "coordinates": GRID["coordinates"][:1]}, "stub", "two coordinates"),
        (
            {**GRID, "coordinates": [{**OH, "points": 31}, GRID["coordinates"][1]]},
            "stub",
            "2 to 30 points",
        ),
        (
            {
                "coordinates": [{**OH, "points": 30}, {**GRID["coordinates"][1], "points": 30}],
                "grid": True,
            },
            "stub",
            "at most 400",
        ),
        (GRID, "b97-3c", "GFN2-xTB or g-xTB"),
    ],
)
def test_a_grid_that_cannot_run_says_why(open_client, scan, method, words):
    """T-LOC-24"""
    node_id = open_client.post("/api/nodes", json={"label": "w", "xyz": WATER}).json()["id"]
    refused = open_client.post(
        "/api/local/jobs", json={"node_id": node_id, "method": method, "scan": scan}
    )
    assert refused.status_code == 422 and words in refused.json()["detail"], refused.json()


@pytest.mark.usefixtures("stub_tools")
def test_a_grid_of_more_than_100_points_starts(open_client):
    """T-LOC-25, D219: a 15 x 15 grid sent as the dialog sends it (the product as its points) is
    not held to a plain scan's 100 points."""
    node_id = open_client.post("/api/nodes", json={"label": "w", "xyz": WATER}).json()["id"]
    grid = {
        "coordinates": [{**c, "points": 15} for c in GRID["coordinates"]],
        "points": 225,
        "grid": True,
    }
    started = open_client.post(
        "/api/local/jobs", json={"node_id": node_id, "method": "stub", "scan": grid}
    )
    assert started.status_code == 200, started.json()
    assert started.json()["scan"]["points"] == 225
    assert open_client.post(f"/api/local/jobs/{started.json()['id']}/cancel").status_code == 200


def _point(job: dict) -> int:
    phase = job["progress"].get("phase") or ""
    return int(phase.split()[1]) if phase.startswith("Point ") else 0


@pytest.mark.usefixtures("stub_tools")
def test_a_running_grid_scan_is_inspected_point_by_point(page, monkeypatch):  # noqa: F811
    """T-LOC-27, D220: a running grid shows the points done so far, each structure with its
    point, and every cell of the grid, so its map fills in; when done, all of them."""
    monkeypatch.setenv("CHEMBOOK3D_LOCAL_STUB_DELAY", "0.3")
    node_id = page.post("/api/nodes", json={"label": "w", "xyz": WATER}).json()["id"]
    job = page.post(
        "/api/local/jobs", json={"node_id": node_id, "method": "stub", "scan": GRID}
    ).json()
    wait_for(page, job["id"], lambda j: _point(j) >= 3 or not j["active"], timeout=120)
    body = {"path": "w", "job": job["id"]}
    shown = page.post("/api/inspections", json=body, headers=PAGE).json()
    step = shown["steps"][0]
    assert step["scan"] == "relaxed" and len(step["grid"]["cells"]) == 6 and step["points"] == 6
    assert step["grid"]["cells"][:3] == [[0, 0], [0, 1], [1, 1]]
    frames = step["frames"]
    assert all(f["point"] for f in frames) and frames[0]["point"] == 1
    done = {f["point"] for f in frames if f["converged"]}
    assert {1, 2} <= done
    if shown["job"]["active"]:
        assert step["optimization_converged"] is None and len(done) < 6

    wait_for(page, job["id"], finished, timeout=120)
    again = page.post(f"/api/inspections/{shown['id']}/refresh", headers=PAGE).json()
    step = again["steps"][0]
    assert {f["point"] for f in step["frames"] if f["converged"]} == set(range(1, 7))
    assert step["points"] == 6 and len(step["grid"]["cells"]) == 6
