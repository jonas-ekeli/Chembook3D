"""A TS guess between two minima on this computer (D238, FR-LOC-13, T-LOC-34..36): both ends
optimized, an IDPP band, Sella's saddle search from its top and a partial-Hessian mode check,
imported as a new TS node halfway between the ends.

The normal run uses the test's Python as the MACE environment's (ASE and Sella are development
dependencies) with the double-well test engine `stub-saddle`: its O–H bond has minima at 1.60
and 2.20 bohr and its barrier top at 1.90 bohr. The real model runs only with
CHEMBOOK3D_REAL_TOOLS=1 after `install-mace` (the CI job "MACE tools").
"""

import json
import sys

import numpy as np
import pytest

from chembook3d import local_jobs, local_tools, local_worker
from tests.test_local_jobs import (  # noqa: F401 - the fixtures
    REAL,
    finished,
    folder_of,
    no_worker_left,
    stub_tools,
    wait_for,
)
from tests.test_local_mace import stub_mace  # noqa: F401 - the fixture

BOHR = local_worker.BOHR_TO_ANGSTROM
START = "3\nwater\nO 0.0 0.0 0.0\nH 0.958 0.0 0.0\nH -0.24 0.927 0.0\n"
# the same water with its first O–H at 2.2 bohr, the other minimum of the test engine
END = f"3\nstretched\nO 0.0 0.0 0.0\nH {2.2 * BOHR:.6f} 0.0 0.0\nH -0.24 0.927 0.0\n"


def two_ends(client, edge=True):
    ids = []
    for label, xyz, x in (("reactant", START, 0), ("product", END, 400)):
        body = {
            "label": label,
            "xyz": xyz,
            "pos_x": x,
            "pos_y": 100,
            "charge": 0,
            "multiplicity": 1,
        }
        ids.append(client.post("/api/nodes", json=body).json()["id"])
    if edge:
        made = client.post("/api/transitions", json={"source_id": ids[0], "target_id": ids[1]})
        assert made.status_code in (200, 201), made.text
    return ids


def test_band_maxima_are_counted():
    """T-LOC-34: a band's maxima count when they stand 1 kcal/mol above their valleys."""
    assert local_worker.humps([0, 3, 6, 3, 1]) == [2]
    assert local_worker.humps([0, 5, 2, 6, 0]) == [1, 3]
    assert local_worker.humps([0, 5, 4.5, 6, 0]) == [3]  # a shoulder is not a second maximum
    assert local_worker.humps([0, 1, 2, 3, 4]) == []


def test_partial_mode_check_finds_the_saddle_mode():
    """T-LOC-34: the Hessian of a region (the O–H bond, the other H held) of the double well at
    its top has one imaginary mode, on that bond; a region that is the
    whole molecule gets translations and rotations projected out."""
    start = np.array([[0.0, 0.0, 0.0], [0.958, 0.0, 0.0], [-0.24, 0.927, 0.0]]) / BOHR
    engine = local_worker._saddle(start, 0.0)
    top = start.copy()
    top[1, 0] = 1.05 * start[1, 0]
    symbols = ["O", "H", "H"]
    check = local_worker.partial_mode_check(symbols, top, engine, [0, 1], [0, 1])
    assert len(check["imaginary"]) == 1 and check["on_reacting"]
    assert check["atoms"][0] == 1 and check["partial"]  # the light H moves most
    assert check["mode"][2] == [0.0, 0.0, 0.0]  # held
    whole = local_worker.partial_mode_check(symbols, top, engine, [0, 1, 2], [0, 1])
    assert len(whole["imaginary"]) == 1 and not whole["partial"]


@pytest.mark.usefixtures("stub_tools", "stub_mace")
def test_ts_guess_between_two_minima(open_client):
    """T-LOC-35: a TS guess along an edge: both ends optimized, the band's top refined to the
    saddle, the mode on the O–H bond; imported as a TS node halfway between the ends with
    notes; the ends keep their structures."""
    if REAL:
        pytest.skip("the double-well engine is the tests' own")
    start_id, end_id = two_ends(open_client)
    status = open_client.get("/api/local/tools?atoms=3").json()
    assert status["ts_guess"]["seconds"] > 0
    job = open_client.post(
        "/api/local/ts-guess",
        json={"start_id": start_id, "end_id": end_id, "method": "stub-saddle"},
    ).json()
    assert job["kind"] == "ts_guess" and "end" not in job["ts_guess"]
    done = wait_for(open_client, job["id"], finished, timeout=300)
    folder = folder_of(open_client) / "local-jobs" / job["id"]
    assert done["status"] == "converged", (folder / "worker.log").read_text() + (
        folder / "worker.err"
    ).read_text()
    result = json.loads((folder / "result.json").read_text())
    saddle = np.array(result["coordinates"]) / BOHR
    assert np.linalg.norm(saddle[0] - saddle[1]) == pytest.approx(1.90, abs=0.02)
    found = result["ts_guess"]
    assert found["ends"]["start"]["converged"] and found["ends"]["end"]["converged"]
    assert len(found["band"]["kcal"]) == local_worker.TS_IMAGES + 2
    assert found["mode_check"]["imaginary"] and found["mode_check"]["on_reacting"]
    stretched = found["mode_check"]["stretch"]  # the O–H bond, more than the other O–H
    assert stretched["changing"] > stretched["other"]
    assert result["transition"] and result["ts_check"]["end"]["mode"]
    for name in ("band.xyz", "start-minimum.xyz", "end-minimum.xyz", "trajectory.xyz"):
        assert (folder / name).is_file(), name
    assert done["result"]["ts_guess"]["band"]["kcal"]

    imported = open_client.post(f"/api/local/jobs/{job['id']}/import")
    assert imported.status_code == 200, imported.text
    node_id = imported.json()["imported"]["node_id"]
    node = open_client.get(f"/api/nodes/{node_id}").json()
    assert node["role"] == "transition_state" and node["status"] == "planned"
    assert node["label"] == "TS guess reactant to product"
    assert node["pos_x"] == pytest.approx(200) and node["pos_y"] == pytest.approx(100)
    assert "IDPP band" in node["notes"] and "Mode check" in node["notes"]
    canvas = open_client.get("/api/canvas").json()
    assert not [t for t in canvas["transitions"] if node_id in (t["source_id"], t["target_id"])]
    calcs = open_client.get(f"/api/nodes/{node_id}/calculations").json()
    assert calcs and "stub-saddle" in json.dumps(calcs[0])
    reactant = open_client.get(f"/api/nodes/{start_id}/calculations").json()
    assert reactant == []  # the ends are not changed


def test_forming_bond_follows_the_mode():
    """T-LOC-37: of the bonds that change, the correction holds the one the saddle's mode
    stretches most, else the one that changes most between the ends (D239)."""
    x = np.array([[0.0, 0.0, 0.0], [2.0, 0.0, 0.0], [0.0, 2.0, 0.0]])
    bonds = [
        {"atoms": [0, 1], "start": 1.5, "saddle": 2.0, "end": 2.8},
        {"atoms": [0, 2], "start": 2.9, "saddle": 2.0, "end": 1.4},
    ]
    along_y = np.zeros((3, 3))
    along_y[2, 1] = 1.0
    assert local_worker.forming_bond(bonds, x, along_y)["atoms"] == [0, 2]
    assert local_worker.forming_bond(bonds, x, None)["atoms"] == [0, 2]  # 1.5 Å against 1.3
    bonds[0]["end"] = 3.5
    assert local_worker.forming_bond(bonds, x, None)["atoms"] == [0, 1]


@pytest.mark.usefixtures("stub_tools", "stub_mace")
def test_corrected_ts_guess_is_a_second_node(open_client):
    """T-LOC-38: a TS guess with the correction (D239; stand-in single points peaking past
    the saddle): the O–H bond held from the saddle toward the longer end, 0.1 Å apart and not
    past that end; the highest single point picked with a warning when it is the last frame;
    imported as a second TS node below the raw one, held at that length, with notes on the
    single points; the raw guess stays the job's import."""
    if REAL:
        pytest.skip("the double-well engine is the tests' own")
    start_id, end_id = two_ends(open_client)
    status = open_client.get("/api/local/tools?atoms=3").json()
    assert status["ts_guess"]["correction_seconds"] > 0
    job = open_client.post(
        "/api/local/ts-guess",
        json={"start_id": start_id, "end_id": end_id, "method": "stub-saddle", "correct": True},
    ).json()
    done = wait_for(open_client, job["id"], finished, timeout=300)
    folder = folder_of(open_client) / "local-jobs" / job["id"]
    assert done["status"] == "converged", (folder / "worker.err").read_text()
    found = done["result"]["ts_guess"]["correction"]
    assert found["bond"] == [0, 1] and found["level"] == "PBE-D3M(BJ)/def2-SVP"
    values = [f["value"] for f in found["frames"]]
    end = 2.2 * BOHR
    assert values[0] == pytest.approx(1.90 * BOHR, abs=0.02)
    assert all(
        b - a == pytest.approx(0.1, abs=1e-3) for a, b in zip(values, values[1:], strict=False)
    )
    assert values[-1] <= end < values[-1] + 0.1
    assert found["picked"] == len(values) - 1 and "last frame" in found["warnings"][0]
    assert "coordinates" not in found

    imported = open_client.post(f"/api/local/jobs/{job['id']}/import").json()["imported"]
    raw = open_client.get(f"/api/nodes/{imported['node_id']}").json()
    assert raw["label"] == "TS guess reactant to product" and "PBE" in raw["notes"]
    node = open_client.get(f"/api/nodes/{imported['corrected_node_id']}").json()
    assert node["label"] == "TS guess reactant to product, PBE-corrected"
    assert node["role"] == "transition_state" and node["status"] == "planned"
    assert node["pos_x"] == pytest.approx(200) and node["pos_y"] == pytest.approx(240)
    assert "Single points" in node["notes"] and "last frame" in node["notes"]
    xyz = np.array([[float(v) for v in line.split()[1:4]] for line in node["xyz"].splitlines()[2:]])
    assert np.linalg.norm(xyz[0] - xyz[1]) == pytest.approx(values[-1], abs=1e-3)
    calcs = open_client.get(f"/api/nodes/{node['id']}/calculations").json()
    assert "constrained" in json.dumps(calcs[0])
    again = open_client.get(f"/api/local/jobs/{job['id']}").json()
    assert again["imported"]["node_id"] == raw["id"]


@pytest.mark.usefixtures("stub_tools", "stub_mace")
def test_ts_guess_is_refused(open_client, monkeypatch):
    """T-LOC-36: without an edge, with another method, or without MACE installed."""
    start_id, end_id = two_ends(open_client, edge=False)
    body = {"start_id": start_id, "end_id": end_id, "method": "stub-saddle"}
    response = open_client.post("/api/local/ts-guess", json=body)
    assert response.status_code == 422 and "runs along an edge" in response.text
    open_client.post("/api/transitions", json={"source_id": start_id, "target_id": end_id})
    response = open_client.post("/api/local/ts-guess", json={**body, "method": "gfn2"})
    assert response.status_code == 422 and "mace-omol" in response.text
    monkeypatch.delenv("CHEMBOOK3D_MACE_PYTHON", raising=False)
    monkeypatch.delenv("CHEMBOOK3D_MACE_MODEL", raising=False)
    monkeypatch.setenv("CHEMBOOK3D_TOOLS_DIR", str(folder_of(open_client) / "no-tools"))
    if local_tools.mace_python() is None:
        response = open_client.post("/api/local/ts-guess", json=body)
        assert response.status_code == 422 and "Install MACE-OMOL-0 first" in response.text
    assert sys.executable  # the fixtures' Python stays the tools' one
    assert "stub-saddle" in local_jobs.TS_GUESS_TEST_METHODS
