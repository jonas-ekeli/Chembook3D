"""Transition state search on this computer (D215, FR-LOC-06, T-LOC-10..12): a starting
Hessian, geomeTRIC's TS mode, and the check of the final imaginary mode.

The search runs with the stand-in engine `stub-saddle` (a bond in a double well, so there is a
saddle point to find); the real GFN2-xTB one only with CHEMBOOK3D_REAL_TOOLS=1.
"""

import json
from pathlib import Path

import numpy as np
import pytest

from chembook3d import local_worker
from tests.conftest import WATER
from tests.test_energies import import_file
from tests.test_local_jobs import (  # noqa: F401 - the fixtures
    REAL,
    finished,
    folder_of,
    no_worker_left,
    stub_tools,
    wait_for,
)

FIXTURES = Path(__file__).parent / "fixtures" / "gaussian"


def water_bohr():
    rows = [line.split() for line in WATER.splitlines()[2:5]]
    return np.array([[float(v) for v in r[1:]] for r in rows]) / local_worker.BOHR_TO_ANGSTROM


def test_the_saddle_engine_has_one_imaginary_mode_on_its_bond():
    """T-LOC-10: the numerical Hessian of the stand-in at its barrier top has one imaginary
    frequency, on atoms 1 and 2; the moving atoms and their share are read from it."""
    x = water_bohr()
    engine = local_worker._saddle(x, 0.0)
    # move atoms 1 and 2 apart to the top of the barrier (5 % longer)
    d = x[1] - x[0]
    top = x.copy()
    top[1] = x[0] + 1.05 * d
    hessian = local_worker.numerical_hessian(engine, top)
    assert np.allclose(hessian, hessian.T)
    check = local_worker.mode_check(["O", "H", "H"], top, hessian, reacting=[0, 1])
    assert len(check["imaginary"]) == 1
    assert set(check["atoms"]) >= {1}  # the hydrogen moves most
    assert check["on_reacting"] and check["share_on_reacting"] > 0.9
    assert (
        local_worker.mode_check(["O", "H", "H"], top, hessian, reacting=[2])["on_reacting"] is False
    )


def test_moving_atoms_of_a_mode():
    mode = [[0, 0, 1.0], [0, 0.1, 0], [0.5, 0, 0], [0, 0, 0.2]]
    assert local_worker.moving_atoms(mode) == [0, 2]
    assert local_worker.moving_atoms(mode, share=0.1, limit=3) == [0, 2, 3]


@pytest.mark.usefixtures("stub_tools")
def test_ts_search_finds_the_saddle_and_checks_its_mode(open_client):
    """T-LOC-11: a TS search climbs to the barrier top; the result has the starting and final
    checks, and is imported as a TS optimization."""
    node_id = open_client.post("/api/nodes", json={"label": "w", "xyz": WATER}).json()["id"]
    job = open_client.post(
        "/api/local/jobs",
        json={"node_id": node_id, "method": "stub-saddle", "transition": True, "reacting": [0, 1]},
    ).json()
    assert job["transition"] and job["reacting"] == [0, 1]
    done = wait_for(open_client, job["id"], finished, timeout=120)
    folder = folder_of(open_client) / "local-jobs" / job["id"]
    assert done["status"] == "converged", (folder / "worker.log").read_text()
    assert (folder / "hessian.txt").is_file()
    check = done["result"]["ts_check"]
    assert len(check["start"]["imaginary"]) == 1
    assert len(check["end"]["imaginary"]) == 1 and check["end"]["on_reacting"]
    assert check["level"] == "stub-saddle"
    result = json.loads((folder / "result.json").read_text())
    final = np.array(result["coordinates"])
    start = water_bohr() * local_worker.BOHR_TO_ANGSTROM
    r0 = np.linalg.norm(start[1] - start[0])
    assert np.linalg.norm(final[1] - final[0]) == pytest.approx(1.05 * r0, abs=2e-3)

    assert open_client.post(f"/api/local/jobs/{job['id']}/import").status_code == 200
    calcs = open_client.get(f"/api/nodes/{node_id}/calculations").json()
    assert "ts" in json.dumps(calcs[0]).lower()


@pytest.mark.usefixtures("stub_tools")
def test_reacting_atoms_come_from_the_nodes_imaginary_mode(open_client):
    """T-LOC-12: a node with an imaginary frequency offers the atoms its mode moves."""
    text = (FIXTURES / "aminationTS-full-unfrz-c1.log").read_text(encoding="utf-8")
    imported = import_file(open_client, "aminationTS-full-unfrz-c1.log", text)
    node_id = imported["node_id"]
    found = open_client.post("/api/local/core", json={"node_id": node_id}).json()
    assert 1 <= len(found["mode_atoms"]) <= 8
    water = open_client.post("/api/nodes", json={"label": "w", "xyz": WATER}).json()["id"]
    assert open_client.post("/api/local/core", json={"node_id": water}).json()["mode_atoms"] == []
    bad = open_client.post(
        "/api/local/jobs",
        json={"node_id": water, "method": "stub", "transition": True, "reacting": [5]},
    )
    assert bad.status_code == 422 and "Reacting atoms" in bad.json()["detail"]


@pytest.mark.skipif(not REAL, reason="needs the installed local tools (CHEMBOOK3D_REAL_TOOLS=1)")
def test_real_tools_ts_search_runs_with_a_gfn2_hessian(open_client, monkeypatch):
    """T-LOC-05, D215: a GFN2-xTB TS search on water (no saddle near: it ends somewhere, with
    the Hessians and checks computed)."""
    monkeypatch.delenv("CHEMBOOK3D_LOCAL_PYTHON", raising=False)
    monkeypatch.setenv("CHEMBOOK3D_LOCAL_STUB", "1")
    node_id = open_client.post("/api/nodes", json={"label": "w", "xyz": WATER}).json()["id"]
    job = open_client.post(
        "/api/local/jobs", json={"node_id": node_id, "method": "gfn2", "transition": True}
    ).json()
    done = wait_for(open_client, job["id"], finished, timeout=600)
    assert done["status"] in ("converged", "not_converged"), done["result"]["error"]
    check = done["result"]["ts_check"]
    assert check["level"] == "GFN2-xTB" and "lowest" in check["start"] and "end" in check
