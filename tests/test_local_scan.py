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
    REAL,
    finished,
    folder_of,
    no_worker_left,
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
