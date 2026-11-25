"""Fix an imaginary mode of a minimum on this computer (D261, FR-LOC-19, T-LOC-49, T-LOC-50).

The job runs with the stand-in engine `stub-saddle`: a TS search with it gives a frequency job
with one imaginary mode on the node, which the fix then pushes off. The choice between the
tries is checked on its own with stand-in optimizations.
"""

import json

import numpy as np
import pytest

from chembook3d import local_worker
from tests.conftest import WATER
from tests.test_local_jobs import (  # noqa: F401 - the fixtures
    finished,
    folder_of,
    no_worker_left,
    stub_tools,
    wait_for,
)


class Progress:
    def __init__(self):
        self.phases = []

    def update(self, **values):
        if values.get("phase"):
            self.phases.append(values["phase"])


def tries_with(ends):
    """Stand-in optimizations: `ends(shift)` gives (energy, imaginary wavenumbers) of the try
    started `shift` Å along the mode (signed), or None when it does not converge."""
    start = np.zeros((2, 3))
    mode = [[0.0, 0.0, 2.0], [0.0, 0.0, -1.0]]  # atom 1 moves most

    def optimize_from(x):
        shift = round(float(x[0][2]), 4)
        found = ends(shift)
        return {
            "converged": found is not None,
            "lists": [[x.tolist()], [found[0] if found else 0.0], [[]], [1]],
            "coords": x,
            "energy": found[0] if found else 1.0,
            "files": ["", ""],
            "shift": shift,
        }

    def frequencies_at(x):
        shift = round(float(x[0][2]) * local_worker.BOHR_TO_ANGSTROM, 4)
        energy, imaginary = ends(shift)
        return {"wavenumbers": [*imaginary, 50.0, 120.0], "energy": energy}

    fix = {"mode": mode, "shifts": [0.1, 0.3], "wavenumber": -30.0, "start_energy": -1.0}
    progress = Progress()
    found = local_worker.fix_mode(fix, start, optimize_from, frequencies_at, progress)
    return found, progress


def test_the_fix_tries_both_ways_and_keeps_the_lower_clean_try():
    """T-LOC-49: both directions are tried, the atom moving most moved by the shift; of the
    tries without an imaginary mode the lower is kept; a second, larger shift runs only when
    the first leaves an imaginary mode both ways."""
    ends = {0.1: (-1.002, []), -0.1: (-1.004, [])}
    found, progress = tries_with(lambda s: ends[s])
    assert [t["shift"] for t in found["tries"]] == [0.1, -0.1]
    assert found["kept"] == 1 and found["fixed"]
    assert found["kept_state"]["shift"] == -0.1
    assert found["change_kcal"] == pytest.approx(-0.004 * local_worker.HARTREE_KCAL, abs=1e-3)
    assert progress.phases == ["Try 1: +0.1 Å along the mode", "Try 2: -0.1 Å along the mode"]

    # the lower try keeps an imaginary mode: the clean one is kept
    ends = {0.1: (-1.002, []), -0.1: (-1.004, [-12.0])}
    found, _ = tries_with(lambda s: ends[s])
    assert found["kept"] == 0 and found["fixed"]
    assert found["tries"][1]["imaginary"] == [-12.0]

    # neither is clean at 0.1 Å: 0.3 Å is tried; one not converging is never kept
    ends = {
        0.1: (-1.001, [-8.0]),
        -0.1: (-1.001, [-9.0]),
        0.3: None,
        -0.3: (-1.003, []),
    }
    found, _ = tries_with(lambda s: ends[s])
    assert [t["shift"] for t in found["tries"]] == [0.1, -0.1, 0.3, -0.3]
    assert found["tries"][2]["converged"] is False and found["tries"][2]["imaginary"] is None
    assert found["kept"] == 3 and found["fixed"]

    # nothing clean: the lowest converged try is kept and the result says it is not fixed
    ends = {0.1: (-1.001, [-8.0]), -0.1: (-1.002, [-9.0]), 0.3: None, -0.3: None}
    found, _ = tries_with(lambda s: ends[s])
    assert found["kept"] == 1 and found["fixed"] is False


def ts_frequency(client, node_id: str) -> dict:
    """A stub-saddle TS search on the node, imported: a frequency job with one imaginary mode
    at the stub-saddle level. The node is then called a minimum (W-IMAG-MIN)."""
    job = client.post(
        "/api/local/jobs",
        json={"node_id": node_id, "method": "stub-saddle", "transition": True},
    ).json()
    assert wait_for(client, job["id"], finished, timeout=120)["status"] == "converged"
    assert client.post(f"/api/local/jobs/{job['id']}/import").status_code == 200
    assert client.patch(f"/api/nodes/{node_id}", json={"role": "minimum"}).status_code == 200
    calcs = client.get(f"/api/nodes/{node_id}/calculations").json()
    frequency = next(c for c in calcs if c["type"] == "frequency")
    assert frequency["result"]["imaginary_count"] == 1
    return frequency


@pytest.mark.usefixtures("stub_tools")
def test_a_fix_job_pushes_the_minimum_off_its_imaginary_mode(open_client):
    """T-LOC-49: the job starts from the frequency job's structure with its mode, tries both
    ways, keeps a try without an imaginary mode and, confirmed, replaces the geometry at that
    level (D260): the old optimization and frequency job go, the node's warning with them."""
    node_id = open_client.post("/api/nodes", json={"label": "w", "xyz": WATER}).json()["id"]
    frequency = ts_frequency(open_client, node_id)
    codes = [w["code"] for w in open_client.get(f"/api/nodes/{node_id}").json()["warnings"]]
    assert "W-IMAG-MIN" in codes

    response = open_client.post(
        "/api/local/fix-mode", json={"node_id": node_id, "calculation_id": frequency["id"]}
    )
    assert response.status_code == 200, response.text
    job = response.json()
    assert job["method"] == "stub-saddle" and job["frequencies"] and job["replace"]
    assert job["fix_mode"]["calculation_id"] == frequency["id"]
    assert job["fix_mode"]["wavenumber"] < 0 and "mode" not in job["fix_mode"]
    started = json.loads(
        (folder_of(open_client) / "local-jobs" / job["id"] / "job.json").read_text()
    )
    text = open_client.get(f"/api/nodes/{node_id}/xyz").text
    saddle = [[float(v) for v in line.split()[1:]] for line in text.splitlines()[2:]]
    assert np.allclose(started["coordinates"], saddle)  # the frequency job's structure
    assert len(started["fix_mode"]["mode"]) == 3

    done = wait_for(open_client, job["id"], finished, timeout=120)
    assert done["status"] == "converged", done
    fix = done["result"]["fix_mode"]
    assert [t["shift"] for t in fix["tries"]] == [0.1, -0.1]
    assert all(t["converged"] and t["imaginary"] == [] for t in fix["tries"])
    kept = fix["tries"][fix["kept"]]
    assert fix["fixed"] and kept["energy"] == min(t["energy"] for t in fix["tries"])
    assert done["result"]["frequencies"]["imaginary"] == []
    assert done["result"]["energy"] == kept["energy"]

    # the page's automatic import waits: same level at another geometry, Replace offered
    waiting = open_client.post(f"/api/local/jobs/{job['id']}/import").json()
    assert waiting["awaiting_choice"] and waiting["replace_offer"]["removes"]
    replaced = open_client.post(f"/api/local/jobs/{job['id']}/replace")
    assert replaced.status_code == 200, replaced.text
    calcs = open_client.get(f"/api/nodes/{node_id}/calculations").json()
    assert frequency["id"] not in [c["id"] for c in calcs]
    frequencies = [c for c in calcs if c["type"] == "frequency"]
    assert len(frequencies) == 1 and frequencies[0]["result"]["imaginary_count"] == 0
    codes = [w["code"] for w in open_client.get(f"/api/nodes/{node_id}").json()["warnings"]]
    assert "W-IMAG-MIN" not in codes


@pytest.mark.usefixtures("stub_tools")
def test_a_fix_is_refused_without_a_local_imaginary_mode(open_client):
    """T-LOC-50: no fix for a calculation without an imaginary frequency, for one at a level
    without local frequencies (a Gaussian file), for a transition state, or for a calculation
    not on the node."""
    node_id = open_client.post("/api/nodes", json={"label": "w", "xyz": WATER}).json()["id"]
    frequency = ts_frequency(open_client, node_id)
    calcs = open_client.get(f"/api/nodes/{node_id}/calculations").json()
    optimization = next(c for c in calcs if c["type"] != "frequency")

    def fix(calculation_id, node=node_id):
        return open_client.post(
            "/api/local/fix-mode", json={"node_id": node, "calculation_id": calculation_id}
        )

    refused = fix(optimization["id"])
    assert refused.status_code == 422 and "no imaginary frequency" in refused.json()["detail"]
    other = open_client.post("/api/nodes", json={"label": "x", "xyz": WATER}).json()["id"]
    refused = fix(frequency["id"], other)
    assert refused.status_code == 422 and "has no calculation" in refused.json()["detail"]
    open_client.patch(f"/api/nodes/{node_id}", json={"role": "transition_state"})
    refused = fix(frequency["id"])
    assert refused.status_code == 422 and "for a minimum" in refused.json()["detail"]
    jobs = open_client.get("/api/local/jobs").json()
    assert not any(j.get("fix_mode") for j in jobs)  # nothing started


def test_a_level_without_local_frequencies_is_refused():
    """T-LOC-50: the level must be one this computer computes frequencies at; B97-3c and
    imported Gaussian levels are not (D261)."""
    from types import SimpleNamespace

    from chembook3d import local_jobs

    def calc(program, method):
        return SimpleNamespace(level=SimpleNamespace(program=program, method=method))

    assert local_jobs.fix_method(calc("MACE", "MACE-OMOL-0")) == "mace-omol"
    assert local_jobs.fix_method(calc("tblite", "GFN2-xTB")) == "gfn2"
    assert local_jobs.fix_method(calc("xTB", "g-xTB")) == "gxtb"
    assert local_jobs.fix_method(calc("Psi4", "B97-3c")) is None
    assert local_jobs.fix_method(calc("Gaussian", "B3LYP")) is None
    assert local_jobs.fix_method(SimpleNamespace(level=None)) is None
