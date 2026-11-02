"""Calculations on this computer (D213, FR-LOC, T-LOC-01..05).

The worker runs with the test's own Python (CHEMBOOK3D_LOCAL_PYTHON) and the "stub" method
(CHEMBOOK3D_LOCAL_STUB): harmonic bonds, optimized by the real geomeTRIC (a dev dependency), so
no tools environment is needed. `test_real_tools_refine_water` uses the installed tools and runs
only with CHEMBOOK3D_REAL_TOOLS=1 (the CI job "Local tools").
"""

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from chembook3d import local_jobs, local_tools
from chembook3d.parsers import local_result
from chembook3d.services import imports
from tests.conftest import WATER

REAL = os.environ.get("CHEMBOOK3D_REAL_TOOLS") == "1"


@pytest.fixture
def stub_tools(monkeypatch):
    """With CHEMBOOK3D_REAL_TOOLS the stub runs in the installed tools' Python."""
    if not REAL:
        monkeypatch.setenv("CHEMBOOK3D_LOCAL_PYTHON", sys.executable)
        monkeypatch.setenv("CHEMBOOK3D_TOOLS_DIR", os.environ["CHEMBOOK3D_CONFIG_DIR"])
    monkeypatch.setenv("CHEMBOOK3D_LOCAL_STUB", "1")


@pytest.fixture(autouse=True)
def no_worker_left():
    """Each test ends every worker it started."""
    yield
    for process in local_jobs._started:
        try:
            process.wait(10)  # a finished job's process ends just after its result.json
        except subprocess.TimeoutExpired:
            pass
    left = [p for p in local_jobs._started if p.poll() is None]
    for process in left:
        process.kill()
        process.wait()
    local_jobs._started.clear()
    assert not left, "a worker was still running"


@pytest.fixture
def no_tools(monkeypatch, config_dir):
    monkeypatch.setenv("CHEMBOOK3D_TOOLS_DIR", str(config_dir / "tools"))
    monkeypatch.delenv("CHEMBOOK3D_LOCAL_PYTHON", raising=False)


@pytest.fixture
def water(open_client):
    response = open_client.post("/api/nodes", json={"label": "water", "xyz": WATER})
    assert response.status_code == 201, response.text
    return response.json()["id"]


def wait_for(client, job_id, done, timeout=60.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        job = client.get(f"/api/local/jobs/{job_id}").json()
        if done(job):
            return job
        time.sleep(0.2)
    raise AssertionError(f"job stayed {job['status']}: {job}")


def finished(job):
    return not job["active"]


def folder_of(client) -> Path:
    return Path(client.get("/api/investigation").json()["folder"])


def test_tools_status_before_install(client, no_tools):
    status = client.get("/api/local/tools").json()
    assert status["installed"] is False
    assert status["cores"] >= 1 and status["memory_mb"] >= 500
    assert status["install"]["state"] == "idle"


def test_cores_and_memory_are_saved(client, no_tools):
    status = client.put("/api/local/tools", json={"cores": 1, "memory_mb": 1500}).json()
    assert (status["cores"], status["memory_mb"]) == (1, 1500)
    status = client.put("/api/local/tools", json={"cores": None, "memory_mb": None}).json()
    assert status["cores"] == status["default_cores"]


def test_a_refine_needs_the_tools(open_client, water, no_tools):
    response = open_client.post("/api/local/jobs", json={"node_id": water})
    assert response.status_code == 422
    assert "Install the local tools" in response.json()["detail"]


def test_other_web_pages_cannot_start_or_install(open_client, water, stub_tools):
    bad = {"origin": "https://example.com"}
    assert (
        open_client.post("/api/local/jobs", json={"node_id": water}, headers=bad).status_code == 403
    )
    assert open_client.post("/api/local/tools/install", headers=bad).status_code == 403


def test_refine_runs_detached_and_imports_onto_the_node(open_client, water, stub_tools):
    """T-LOC-02: the job folder, progress, the result and its import."""
    job = open_client.post("/api/local/jobs", json={"node_id": water, "method": "stub"}).json()
    assert job["status"] in ("starting", "queued", "running", "converged")
    folder = folder_of(open_client) / "local-jobs" / job["id"]
    assert (folder / "job.json").is_file() and (folder / "worker.py").is_file()
    done = wait_for(open_client, job["id"], finished)
    assert done["status"] == "converged", (folder / "worker.log").read_text()
    assert done["progress"]["cycle"] >= 2 and done["importable"]
    result = json.loads((folder / "result.json").read_text())
    assert len(result["frames"]) == result["cycles"] == len(result["energies"])

    imported = open_client.post(f"/api/local/jobs/{job['id']}/import")
    assert imported.status_code == 200, imported.text
    info = imported.json()["imported"]
    assert info["node_id"] == water and not info["derived"]  # a node without calculations
    calcs = open_client.get(f"/api/nodes/{water}/calculations").json()
    assert len(calcs) == 1 and calcs[0]["program"] == "stub"
    node = open_client.get(f"/api/nodes/{water}").json()
    oxygen = [float(v) for v in node["xyz"].splitlines()[2].split()[1:]]
    assert max(abs(a - b) for a, b in zip(oxygen, result["coordinates"][0], strict=True)) < 1e-5
    again = open_client.post(f"/api/local/jobs/{job['id']}/import")
    assert again.status_code == 422  # once only
    listed = open_client.get("/api/local/jobs").json()
    assert listed[0]["imported"]["present"] is True and not listed[0]["importable"]


def test_cancel_stops_the_process(open_client, water, stub_tools, monkeypatch):
    """T-LOC-03"""
    monkeypatch.setenv("CHEMBOOK3D_LOCAL_STUB_DELAY", "1")
    job = open_client.post("/api/local/jobs", json={"node_id": water, "method": "stub"}).json()
    wait_for(open_client, job["id"], lambda j: j["status"] == "running" and j["progress"]["cycle"])
    pid = json.loads(
        (folder_of(open_client) / "local-jobs" / job["id"] / "progress.json").read_text()
    )["pid"]
    cancelled = open_client.post(f"/api/local/jobs/{job['id']}/cancel").json()
    assert cancelled["status"] == "cancelled"
    assert not local_jobs._running(pid)
    assert open_client.post(f"/api/local/jobs/{job['id']}/import").status_code == 422


def test_jobs_wait_for_each_other(open_client, water, stub_tools, monkeypatch):
    """T-LOC-03, FR-LOC-03: one local job at a time on this computer."""
    monkeypatch.setenv("CHEMBOOK3D_LOCAL_STUB_DELAY", "0.5")
    first = open_client.post("/api/local/jobs", json={"node_id": water, "method": "stub"}).json()
    wait_for(open_client, first["id"], lambda j: j["status"] == "running")
    second = open_client.post("/api/local/jobs", json={"node_id": water, "method": "stub"}).json()
    assert second["id"] != first["id"]
    wait_for(open_client, second["id"], lambda j: j["status"] == "queued")
    open_client.post(f"/api/local/jobs/{first['id']}/cancel")
    wait_for(open_client, second["id"], lambda j: j["status"] == "running")
    open_client.post(f"/api/local/jobs/{second['id']}/cancel")


def test_a_job_whose_process_ended_without_a_result_is_stopped(open_client, water, stub_tools):
    folder = folder_of(open_client)
    path = local_jobs.jobs_dir(folder) / "2026-01-01-gone"
    path.mkdir(parents=True)
    (path / "job.json").write_text(
        json.dumps(
            {
                "id": path.name,
                "node_id": water,
                "node_label": "water",
                "method": "stub",
                "created": "2026-01-01T00:00:00Z",
                "elements": ["O"],
                "coordinates": [[0, 0, 0]],
            }
        )
    )
    (path / "progress.json").write_text(
        json.dumps({"state": "running", "pid": 999999, "heartbeat_at": "2026-01-01T00:01:00Z"})
    )
    job = open_client.get(f"/api/local/jobs/{path.name}").json()
    assert job["status"] == "stopped" and not job["active"]
    assert open_client.post(f"/api/local/jobs/{path.name}/cancel").json()["status"] == "stopped"


def test_constraints_are_checked_and_held(open_client, water, stub_tools):
    bad = open_client.post(
        "/api/local/jobs",
        json={
            "node_id": water,
            "method": "stub",
            "constraints": {"distances": [{"atoms": [0, 7]}]},
        },
    )
    assert bad.status_code == 422
    job = open_client.post(
        "/api/local/jobs",
        json={
            "node_id": water,
            "method": "stub",
            "constraints": {"frozen": [0], "distances": [{"atoms": [0, 1], "value": 1.0}]},
        },
    ).json()
    done = wait_for(open_client, job["id"], finished)
    assert done["status"] == "converged"
    result = json.loads(
        (folder_of(open_client) / "local-jobs" / job["id"] / "result.json").read_text()
    )
    o, h = result["coordinates"][0], result["coordinates"][1]
    assert abs(sum((a - b) ** 2 for a, b in zip(o, h, strict=True)) ** 0.5 - 1.0) < 1e-3
    parsed = local_result.parse(json.dumps(result))
    assert parsed.steps[0].constraints == ["1 frozen atom", "constrained B(1,2)"]


def test_the_result_parses_as_an_optimization():
    """T-LOC-04: result.json read as one optimization step with every structure."""
    text = json.dumps(
        {
            "format": local_result.FORMAT,
            "program": "tblite",
            "level": "GFN2-xTB",
            "method": "gfn2",
            "charge": 0,
            "multiplicity": 1,
            "state": "not_converged",
            "elements": ["H", "H"],
            "frames": [[[0, 0, 0], [0, 0, 0.8]], [[0, 0, 0], [0, 0, 0.74]]],
            "energies": [-0.9, -1.0],
            "energy": -1.0,
            "versions": {"tblite": "0.8.0"},
        },
        indent=1,
    )
    parsed = imports.read_file(text, "r.json")
    step = parsed.steps[0]
    assert (parsed.program, parsed.version, step.route.method) == ("tblite", "0.8.0", "GFN2-xTB")
    assert step.job_type == "optimization" and step.optimization_converged is False
    assert len(step.geometries) == 2 and step.geometry_energies == [-0.9, -1.0]
    assert step.termination == "normal" and not step.missing


def test_install_builds_a_new_environment_and_swaps_it_in(tmp_path, monkeypatch):
    """T-LOC-01, with a stand-in for micromamba and the tools' check."""
    monkeypatch.setenv("CHEMBOOK3D_TOOLS_DIR", str(tmp_path / "tools"))
    calls = []

    def fake_run(argv, **kwargs):
        calls.append(argv)
        prefix = Path(argv[argv.index("-p") + 1])
        prefix.mkdir(parents=True)

        class Done:
            returncode = 0

        return Done()

    monkeypatch.setattr(local_tools, "micromamba", lambda conda: ["micromamba"])
    monkeypatch.setattr(local_tools.subprocess, "run", fake_run)
    monkeypatch.setattr(local_tools, "check", lambda env: {"psi4": "1.11"})
    lock = tmp_path / "list.txt"
    lock.write_text("@EXPLICIT\n")
    marker = local_tools.install("linux-64", lock)
    assert marker["versions"] == {"psi4": "1.11"}
    assert (tmp_path / "tools" / "env" / local_tools.MARKER).is_file()
    assert not (tmp_path / "tools" / "env-new").exists()
    assert calls[0][calls[0].index("--file") + 1] == str(lock)


def test_the_package_lists_are_pinned():
    """Every package of each platform's list is a conda-forge URL with its MD5."""
    for conda in ("win-64", "linux-64"):
        lines = local_tools.package_list(conda).read_text().splitlines()
        assert "@EXPLICIT" in lines
        packages = [line for line in lines if line.startswith("https://")]
        assert packages and all(
            line.startswith("https://conda.anaconda.org/conda-forge/") and "#" in line
            for line in packages
        )
        names = " ".join(packages)
        for wanted in ("psi4-1.11", "tblite-python", "geometric-1.1", "gcp-correction", "dftd4"):
            assert wanted in names, (conda, wanted)


def test_methods_come_with_time_estimates_that_learn(open_client, water, stub_tools, no_tools):
    """T-LOC-06: an estimate per method for the node's atoms, replaced by this computer's own
    time once a job has run."""
    open_client.put("/api/local/tools", json={"cores": 4, "memory_mb": 2000})
    methods = open_client.get("/api/local/tools?atoms=70").json()["methods"]
    by = {m["value"]: m for m in methods}
    assert {"gfn2", "b97-3c", "r2scan-3c"} <= set(by)
    assert by["r2scan-3c"]["estimate"]["seconds"] > by["b97-3c"]["estimate"]["seconds"]
    assert by["b97-3c"]["estimate"]["seconds"] > by["gfn2"]["estimate"]["seconds"] * 100
    assert not by["gfn2"]["estimate"]["measured"]
    assert open_client.get("/api/local/tools").json()["methods"][0]["estimate"] is None
    local_jobs.calibrate(
        {"cycles": 10, "elapsed_s": 50.0}, {"method": "gfn2", "elements": ["C"] * 70, "cores": 4}
    )
    gfn2 = open_client.get("/api/local/tools?atoms=70").json()["methods"][0]
    assert gfn2["estimate"]["measured"] and gfn2["estimate"]["seconds_per_step"] == 5.0


@pytest.mark.skipif(not REAL, reason="needs the installed local tools (CHEMBOOK3D_REAL_TOOLS=1)")
def test_real_tools_refine_water_with_dft(open_client, water, monkeypatch):
    """T-LOC-05: a B97-3c refine with Psi4, holding the O-H distance."""
    monkeypatch.delenv("CHEMBOOK3D_LOCAL_PYTHON", raising=False)
    monkeypatch.setenv("CHEMBOOK3D_LOCAL_STUB", "1")
    job = open_client.post(
        "/api/local/jobs",
        json={
            "node_id": water,
            "method": "b97-3c",
            "constraints": {"distances": [{"atoms": [0, 1], "value": 0.95}]},
        },
    ).json()
    done = wait_for(open_client, job["id"], finished, timeout=600)
    assert done["status"] == "converged", done["result"]["error"]
    assert -76.5 < done["result"]["energy"] < -76.3  # B97-3c water, hartree
    assert open_client.post(f"/api/local/jobs/{job['id']}/import").status_code == 200
    calcs = open_client.get(f"/api/nodes/{water}/calculations").json()
    assert calcs[0]["program"] == "Psi4" and calcs[0]["constraints"] == ["constrained B(1,2)"]


@pytest.mark.skipif(not REAL, reason="needs the installed local tools (CHEMBOOK3D_REAL_TOOLS=1)")
def test_real_tools_refine_water(open_client, water, monkeypatch):
    """T-LOC-05: a GFN2-xTB refine with the real tools."""
    monkeypatch.delenv("CHEMBOOK3D_LOCAL_PYTHON", raising=False)
    # CHEMBOOK3D_TOOLS_DIR names the installed tools (python -m chembook3d.local_tools install)
    assert local_tools.worker_python() is not None, "install the tools first"
    job = open_client.post("/api/local/jobs", json={"node_id": water, "method": "gfn2"}).json()
    done = wait_for(open_client, job["id"], finished, timeout=300)
    assert done["status"] == "converged", done
    assert -5.08 < done["result"]["energy"] < -5.06  # GFN2-xTB water, hartree
    assert open_client.post(f"/api/local/jobs/{job['id']}/import").status_code == 200
    calcs = open_client.get(f"/api/nodes/{water}/calculations").json()
    assert calcs[0]["program"] == "tblite"
