"""Single points on this computer (D240, FR-LOC-15, T-LOC-39..41).

The worker runs with the test's own Python and the "stub" level (CHEMBOOK3D_LOCAL_STUB), whose
energy is the stub engine's, so no tools environment is needed. `test_real_tools_single_points`
runs every level with Psi4 on water, only with CHEMBOOK3D_REAL_TOOLS=1 (the CI job "Local
tools").
"""

import json

import pytest

from chembook3d import local_jobs
from chembook3d.services import imports
from tests.conftest import WATER
from tests.test_local_jobs import (  # noqa: F401 - fixtures
    REAL,
    finished,
    folder_of,
    no_tools,
    no_worker_left,
    stub_tools,
    wait_for,
)

WATER_2 = WATER.replace("0.757200", "0.760000")


def _node(client, label: str, xyz: str | None) -> str:
    body = {"label": label, **({"xyz": xyz} if xyz else {})}
    response = client.post("/api/nodes", json=body)
    assert response.status_code == 201, response.text
    return response.json()["id"]


def _done(client, job_id: str) -> dict:
    done = wait_for(client, job_id, finished)
    assert done["status"] == "converged", done.get("result")
    return done


def _import(client, job_id: str) -> dict:
    response = client.post(f"/api/local/jobs/{job_id}/import")
    assert response.status_code == 200, response.text
    return response.json()["imported"]


@pytest.mark.usefixtures("stub_tools")
def test_single_points_are_imported_onto_their_nodes_at_the_composite_level(open_client):
    """T-LOC-39: the levels with estimates; one job per node; each imported onto its node as a
    single point whose geometry level is the node's optimization, so G follows (EN-4)."""
    water = _node(open_client, "water", WATER)
    status = open_client.get("/api/local/tools?atoms=70").json()
    levels = status["single_point_levels"]
    assert levels[0]["value"] == "pbe-tzvp" and levels[0]["default"]
    assert levels[0]["label"] == "PBE-D3M(BJ)/def2-TZVP"
    assert {lv["value"] for lv in levels} >= {
        "pbe-svp",
        "r2scan-3c",
        "b97-3c",
        "pbe0-tzvp",
        "stub",
    }
    # about 10 min for 70 atoms on 4 cores, scaled to the cores this computer gives a job
    scaled = levels[0]["estimate"]["seconds"] * (status["cores"] / 4) ** 0.85
    assert 300 < scaled < 1200
    assert "barrier" in next(lv for lv in levels if lv["value"] == "r2scan-3c")["note"]

    # The first node has its own optimization with frequencies; the second is a drawn structure.
    refine = open_client.post(
        "/api/local/jobs", json={"node_id": water, "method": "stub", "frequencies": True}
    ).json()
    _done(open_client, refine["id"])
    _import(open_client, refine["id"])
    other = _node(open_client, "drawn", WATER_2)
    before = open_client.get(f"/api/nodes/{water}").json()["xyz"]

    response = open_client.post(
        "/api/local/single-points", json={"node_ids": [water, other], "level": "stub"}
    )
    assert response.status_code == 200, response.text
    jobs = response.json()
    assert [j["node_id"] for j in jobs] == [water, other]
    assert all(j["kind"] == "single_point" and j["method"] == "stub-sp" for j in jobs)
    assert jobs[0]["level"] == "stub-sp/def2-SVP"

    done = [_done(open_client, j["id"]) for j in jobs]
    result = json.loads(
        (folder_of(open_client) / "local-jobs" / jobs[0]["id"] / "result.json").read_text()
    )
    assert result["single_point"]["level"] == "stub" and result["cycles"] == 1
    assert result["run_s"] <= result["elapsed_s"]
    assert done[0]["result"]["energy"] == result["energy"]

    imported = [_import(open_client, j["id"]) for j in jobs]
    assert [i["node_id"] for i in imported] == [water, other]
    assert not any(i["derived"] for i in imported)
    assert open_client.get(f"/api/nodes/{water}").json()["xyz"] == before  # nothing moved

    calcs = open_client.get(f"/api/nodes/{water}/calculations").json()
    sp = next(c for c in calcs if c["type"] == "single_point")
    opt = next(c for c in calcs if c["type"] == "optimization")
    assert sp["program"] == "stub" and sp["geometry_level"]["id"] == opt["level"]["id"]
    assert sp["composite_label"].startswith("stub stub-sp/def2-SVP // stub")
    assert sp["result"]["energy"] == pytest.approx(result["energy"])

    # EN-4: free energy at the composite level from the single point and the frequency job.
    found = open_client.get("/api/energies/options").json()["levels"]
    composite = next(o for o in found if o["label"] == sp["composite_label"])
    assert "G_qh" in composite["types"]  # the app works G_qh out from local frequencies (D217)

    # The drawn node has no optimization: its single point has no geometry level.
    drawn = open_client.get(f"/api/nodes/{other}/calculations").json()
    assert [c["type"] for c in drawn] == ["single_point"] and drawn[0]["geometry_level"] is None


@pytest.mark.usefixtures("stub_tools")
def test_single_points_are_refused(open_client):
    """T-LOC-40: an unknown level, no nodes, a node without coordinates or listed twice."""
    water = _node(open_client, "water", WATER)
    post = open_client.post
    response = post("/api/local/single-points", json={"node_ids": [water], "level": "mp2"})
    assert response.status_code == 422 and "pbe-tzvp" in response.json()["detail"]
    response = post("/api/local/single-points", json={"node_ids": [], "level": "stub"})
    assert response.status_code == 422 and "at least one node" in response.json()["detail"]
    empty = _node(open_client, "empty", None)
    response = post("/api/local/single-points", json={"node_ids": [empty], "level": "stub"})
    assert response.status_code == 422 and "no coordinates" in response.json()["detail"]
    response = post("/api/local/single-points", json={"node_ids": [water, water], "level": "stub"})
    assert response.status_code == 422 and "twice" in response.json()["detail"]
    bad = {"origin": "https://example.com"}
    response = post("/api/local/single-points", json={"node_ids": [water]}, headers=bad)
    assert response.status_code == 403
    assert not local_jobs.list_jobs(folder_of(open_client))


@pytest.mark.usefixtures("no_tools")
def test_single_points_need_the_tools(open_client):
    water = _node(open_client, "water", WATER)
    response = open_client.post("/api/local/single-points", json={"node_ids": [water]})
    assert response.status_code == 422 and "Install the local tools" in response.json()["detail"]


def test_the_single_point_result_parses_at_its_level():
    """T-LOC-41: result.json of a single point read as one single-point step at its level, and
    its time kept per level without the wait for other jobs."""
    text = json.dumps(
        {
            "format": "chembook3d-local-result",
            "program": "Psi4",
            "method": "psi4-sp",
            "level": "PBE-D3M(BJ)/def2-TZVP",
            "charge": 0,
            "multiplicity": 1,
            "state": "converged",
            "single_point": {
                "level": "pbe-tzvp",
                "method": "PBE",
                "dispersion": "D3M(BJ)",
                "basis_name": "def2-TZVP",
            },
            "elements": ["H", "H"],
            "frames": [[[0, 0, 0], [0, 0, 0.74]]],
            "energies": [-1.17],
            "energy": -1.17,
            "versions": {"psi4": "1.11"},
        },
        indent=1,
    )
    parsed = imports.read_file(text, "sp.json")
    (step,) = parsed.steps
    assert (parsed.program, parsed.version) == ("Psi4", "1.11")
    assert step.job_type == "single_point" and step.scf_energy == -1.17
    assert (step.route.method, step.route.dispersion, step.resolved_basis) == (
        "PBE",
        "D3M(BJ)",
        "def2-TZVP",
    )
    assert len(step.geometries) == 1 and step.termination == "normal" and not step.missing


@pytest.mark.usefixtures("stub_tools", "no_tools")
def test_a_single_points_time_is_learnt_per_level():
    first = local_jobs.sp_estimate("pbe-svp", 70, 4)
    assert first == {"seconds": 215, "measured": False}
    job = {
        "kind": "single_point",
        "single_point": {"level": "pbe-svp"},
        "elements": ["C"] * 70,
        "cores": 4,
    }
    small = {**job, "elements": ["O", "H", "H"]}
    local_jobs.calibrate({"elapsed_s": 3.0, "run_s": 3.0}, small)  # mostly start-up: not kept
    assert local_jobs.sp_estimate("pbe-svp", 70, 4)["measured"] is False
    local_jobs.calibrate({"elapsed_s": 900.0, "run_s": 300.0}, job)  # 600 s waiting
    assert local_jobs.sp_estimate("pbe-svp", 70, 4) == {"seconds": 300, "measured": True}
    assert local_jobs.sp_estimate("pbe-tzvp", 70, 4)["measured"] is False
    # twice the atoms: about 2^2.7 times as long
    assert local_jobs.sp_estimate("pbe-svp", 140, 4)["seconds"] == pytest.approx(
        300 * 6.5, rel=0.01
    )


@pytest.mark.skipif(not REAL, reason="needs the installed local tools (CHEMBOOK3D_REAL_TOOLS=1)")
def test_real_tools_single_points(open_client, monkeypatch):
    """T-LOC-41: every level with Psi4 on water, imported at its level."""
    monkeypatch.delenv("CHEMBOOK3D_LOCAL_PYTHON", raising=False)
    names = {}
    for level in local_jobs.SP_LEVELS:
        node = _node(open_client, f"water {level}", WATER)
        job = open_client.post(
            "/api/local/single-points", json={"node_ids": [node], "level": level}
        ).json()[0]
        done = wait_for(open_client, job["id"], finished, timeout=900)
        path = folder_of(open_client) / "local-jobs" / job["id"]
        assert done["status"] == "converged", (path / "worker.log").read_text()
        assert -76.6 < done["result"]["energy"] < -76.0, level  # water, hartree
        _import(open_client, job["id"])
        (calc,) = open_client.get(f"/api/nodes/{node}/calculations").json()
        names[level] = calc["composite_label"]
    assert names["pbe-tzvp"] == "Psi4 PBE-D3M(BJ)/def2-TZVP"
    assert names["r2scan-3c"] == "Psi4 r2SCAN-3c"
    assert names["pbe0-tzvp"] == "Psi4 PBE0-D3(BJ)/def2-TZVP"
