"""MACE-OMOL-0 on this computer (D237, FR-LOC-12, T-LOC-31..33): the optional install of its
own environment and model, offered only once installed and only after its licence is accepted,
and a refine with it.

The normal run uses the test's Python as the MACE environment's and the stand-in method
`mace-stub`; the real model runs only with CHEMBOOK3D_REAL_TOOLS=1 after
`python -m chembook3d.local_tools install-mace --accept-licence` (the CI job "MACE tools").
"""

import json
import sys

import pytest

from chembook3d import local_jobs, local_tools
from chembook3d.services import levels
from tests.conftest import WATER
from tests.test_local_jobs import (  # noqa: F401 - the fixtures
    REAL,
    finished,
    folder_of,
    no_worker_left,
    stub_tools,
    wait_for,
)


@pytest.fixture
def stub_mace(monkeypatch, tmp_path):
    """The test's Python stands in for the MACE environment, a small file for the model."""
    if REAL and local_tools.mace_python() is not None:
        return
    model = tmp_path / local_tools.MACE_MODEL_FILE
    model.write_bytes(b"model")
    monkeypatch.setenv("CHEMBOOK3D_MACE_PYTHON", sys.executable)
    monkeypatch.setenv("CHEMBOOK3D_MACE_MODEL", str(model))


def test_mace_is_offered_only_once_installed(open_client, monkeypatch, tmp_path):
    """T-LOC-31: without the MACE environment and model the method is not offered and a job
    asking for it is refused with what to install."""
    monkeypatch.delenv("CHEMBOOK3D_MACE_PYTHON", raising=False)
    monkeypatch.delenv("CHEMBOOK3D_MACE_MODEL", raising=False)
    monkeypatch.setenv("CHEMBOOK3D_TOOLS_DIR", str(tmp_path / "tools"))
    monkeypatch.setenv("CHEMBOOK3D_LOCAL_PYTHON", sys.executable)
    monkeypatch.setenv("CHEMBOOK3D_LOCAL_STUB", "1")
    status = open_client.get("/api/local/tools").json()
    offered = [m["value"] for m in status["methods"]]
    assert "mace-omol" not in offered and "mace-stub" not in offered
    assert status["mace"]["installed"] is False and status["mace"]["install"]["state"] == "idle"
    assert status["mace"]["licence"]["name"].startswith("Academic Software License")
    node_id = open_client.post("/api/nodes", json={"label": "w", "xyz": WATER}).json()["id"]
    response = open_client.post("/api/local/jobs", json={"node_id": node_id, "method": "mace-omol"})
    assert response.status_code == 422 and "Unknown method" in response.text
    # the model alone, or the environment without its model, is not an install
    (tmp_path / "tools" / "mace" / "env").mkdir(parents=True)
    assert local_tools.mace_python() is None and local_tools.mace_model() is None


def test_the_settings_page_installs_mace_after_the_licence(open_client, monkeypatch, config_dir):
    """T-LOC-32: Install MACE-OMOL-0 (POST /api/local/tools/mace) is refused until the user
    accepts the model's licence and from another web page; then it runs in the app's own thread
    and shows its progress. The install itself is replaced here (the CI job "MACE tools" runs
    the real one)."""
    if local_tools.conda_platform() is None:
        pytest.skip("MACE-OMOL-0 is offered on Windows and Linux (x86-64) only")
    monkeypatch.setenv("CHEMBOOK3D_TOOLS_DIR", str(config_dir / "tools"))
    installed = []

    def install_mace(conda, say):
        say("Downloading the MACE-OMOL-0 model")
        installed.append(conda)

    monkeypatch.setattr(local_tools, "install_mace", install_mace)
    response = open_client.post("/api/local/tools/mace", json={})
    assert response.status_code == 422 and "Academic Software License" in response.text
    bad = {"origin": "https://example.com"}
    body = {"accept_licence": True}
    assert open_client.post("/api/local/tools/mace", json=body, headers=bad).status_code == 403
    response = open_client.post("/api/local/tools/mace", json=body)
    assert response.status_code == 200, response.text
    assert response.json()["mace"]["install"]["state"] in ("running", "done")
    open_client.app.state.mace_installer.wait(30)
    status = open_client.get("/api/local/tools").json()
    assert status["mace"]["install"]["state"] == "done"
    assert status["mace"]["install"]["step"] == "Installed"
    assert installed == [local_tools.conda_platform()]


def test_mace_lists_are_pinned():
    """D237: the environment is an explicit conda-forge list per platform and the PyPI wheels
    carry their SHA-256, so nothing is solved on the user's computer."""
    for conda in ("win-64", "linux-64"):
        lines = (local_tools.LOCKS / f"mace-{conda}.txt").read_text().splitlines()
        assert "@EXPLICIT" in lines
        urls = [line for line in lines if line.startswith("https://")]
        assert urls and all("#" in line for line in urls)
        assert any("/pytorch-" in line and "cpu" in line for line in urls)
        assert any("/psi4-" in line for line in urls)  # the DFT single points of D239
    wheels = [
        line
        for line in local_tools.MACE_WHEELS.read_text().splitlines()
        if line and not line.startswith("#")
    ]
    names = {line.split("==")[0] for line in wheels}
    assert {"mace-torch", "sella", "matscipy", "jax", "jaxlib"} <= names
    assert all("--hash=sha256:" in line for line in wheels)


def test_a_mace_level_needs_no_basis():
    assert not levels.needs_basis("MACE", "MACE-OMOL-0")


@pytest.mark.usefixtures("stub_tools", "stub_mace")
def test_refine_runs_in_the_mace_environment(open_client, monkeypatch):
    """T-LOC-33: a MACE job runs with the MACE environment's Python (the tools' own is not
    needed), names the model in its job, and is imported like any refine."""
    if not REAL:
        monkeypatch.setenv("CHEMBOOK3D_LOCAL_PYTHON", str(folder_of(open_client) / "none"))
    status = open_client.get("/api/local/tools?atoms=3").json()
    offered = {m["value"]: m for m in status["methods"]}
    assert "mace-omol" in offered and offered["mace-omol"]["label"] == "MACE-OMOL-0"
    assert offered["mace-omol"]["estimate"]["seconds_per_step"] > 0
    method = "mace-omol" if REAL else "mace-stub"
    node_id = open_client.post("/api/nodes", json={"label": "w", "xyz": WATER}).json()["id"]
    job = open_client.post("/api/local/jobs", json={"node_id": node_id, "method": method}).json()
    done = wait_for(open_client, job["id"], finished, timeout=600)
    folder = folder_of(open_client) / "local-jobs" / job["id"]
    assert done["status"] == "converged", (folder / "worker.log").read_text()
    written = json.loads((folder / "job.json").read_text())
    assert written["mace_model"] == str(local_tools.mace_model())
    assert open_client.post(f"/api/local/jobs/{job['id']}/import").status_code == 200
    calcs = open_client.get(f"/api/nodes/{node_id}/calculations").json()
    if REAL:
        assert calcs[0]["program"] == "MACE" and "MACE-OMOL-0" in json.dumps(calcs[0])
        result = json.loads((folder / "result.json").read_text())
        assert result["versions"]["mace"] and result["versions"]["torch"]


@pytest.mark.usefixtures("stub_tools", "stub_mace")
def test_a_mace_ts_search_ends_with_frequencies_at_its_level(open_client):
    """D237 with D217: MACE is cheap enough for its own Hessian, so a TS search with it starts
    from one and its frequencies are on by default."""
    assert {"mace-omol", "mace-stub"} <= local_jobs.OWN_HESSIAN
    assert {"mace-omol", "mace-stub"} <= local_jobs.FREQUENCY_METHODS
    node_id = open_client.post("/api/nodes", json={"label": "w", "xyz": WATER}).json()["id"]
    method = "mace-omol" if REAL else "mace-stub"
    job = open_client.post(
        "/api/local/jobs", json={"node_id": node_id, "method": method, "transition": True}
    ).json()
    assert job["ts_hessian"] == method and job["frequencies"] is True
    done = wait_for(open_client, job["id"], finished, timeout=600)
    assert done["status"] in ("converged", "not_converged")
