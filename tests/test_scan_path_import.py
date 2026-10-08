"""A scan path job's result imported as a new node between its ends (D115, A61; T-PATH-07…08)."""

import json

import numpy as np
import pytest

from chembook3d import cloud_jobs, pathtools
from tests.test_cloud_jobs import (  # noqa: F401  (fixtures)
    app_client,
    fake_claude,
    folder_of,
    git,
    git_config,
    linked_client,
    remote,
)
from tests.test_import_undo import import_entry, undo
from tests.test_overlay import xyz_text
from tests.test_scan_path import WATER_A, WATER_B

ENERGIES = [-5.07, -5.06, -5.04, -5.03, -5.045, -5.05, -5.055]  # a top at the fourth point


def path_text() -> str:
    """Seven structures from water A to water B (in A's order), with a top in the middle."""
    start = [list(r) for r in WATER_A]
    end = [list(WATER_B[1]), list(WATER_B[0]), list(WATER_B[2])]
    out = []
    for t, energy in zip(np.linspace(0, 1, len(ENERGIES)), ENERGIES, strict=True):
        rows = [
            [e, *(np.array(a[1:]) + t * (np.array(b[1:]) - np.array(a[1:])))]
            for (e, *_), a, b in zip(start, start, end, strict=True)
        ]
        call = "xtb start.xyz --opt --input scan.inp --gfn 2 --chrg 0 --uhf 0 --alpb toluene"
        out.append(pathtools.format_frame(rows, f" energy: {energy:.10f} stage: 1 call: {call}"))
    return "".join(out)


def cloud_returns(origin, tmp_path, job_id: str, result: dict, path: str | None) -> None:
    work = tmp_path / "cloud"
    git(tmp_path, "clone", "-q", str(origin), str(work))
    git(work, "checkout", "-q", "-b", "claude/scan-path-x1")
    base = work / "jobs" / job_id
    if path is not None:
        (base / "outputs").mkdir(parents=True)
        (base / "outputs" / "path.xyz").write_text(path, encoding="utf-8")
    (base / "result.json").write_text(json.dumps(result), encoding="utf-8")
    git(work, "add", "-A")
    git(work, "-c", "user.name=c", "-c", "user.email=c@x", "commit", "-q", "-m", "path")
    git(work, "push", "-q", "origin", "claude/scan-path-x1")


def setup(client) -> dict:
    """Two waters joined by an edge, the start in a step and branch, and a sent scan path."""
    step = client.post("/api/steps", json={"name": "Hydrogen shift"}).json()
    branch = client.post("/api/branches", json={"name": "A"}).json()
    ids = {}
    for label, rows, x, y in (("A", WATER_A, 100.0, 200.0), ("B", WATER_B, 700.0, 400.0)):
        response = client.post(
            "/api/nodes",
            json={
                "label": label,
                "xyz": xyz_text(rows),
                "charge": 0,
                "multiplicity": 1,
                "pos_x": x,
                "pos_y": y,
            },
        )
        assert response.status_code == 201, response.text
        ids[label] = response.json()["id"]
    for field, value in (("step_id", step["id"]), ("branch_id", branch["id"])):
        response = client.patch(f"/api/nodes/{ids['A']}", json={field: value})
        assert response.status_code == 200, response.text
    edge = client.post("/api/transitions", json={"source_id": ids["A"], "target_id": ids["B"]})
    assert edge.status_code == 201, edge.text
    sent = client.post(
        "/api/scan-paths",
        json={"start_id": ids["A"], "end_id": ids["B"], "pairs": [[2, 1]], "solvent": "toluene"},
    )
    assert sent.status_code == 200, sent.text
    out = sent.json()
    assert out["start_error"] is None, out["start_error"]
    return {**ids, "step": step["id"], "branch": branch["id"], "job": out["job"]["id"]}


RESULT = {
    "status": "done",
    "summary": "One concerted scan of the O–H distance reached the end.",
    "outputs": [{"file": "outputs/path.xyz", "program": "xtb"}],
    "path": {
        "reached_end": True,
        "end_rmsd": 0.04,
        "barrier_kcal": 25.1,
        "design": "Stretch O1–H2 from 0.96 to 1.59 Å in 6 steps.",
    },
}


def test_the_path_comes_back_as_a_new_node(linked_client, remote, fake_claude, tmp_path):  # noqa: F811
    # T-PATH-07: the job's result is found (one fetch for the list), fetched and imported: a
    # new node halfway between the ends, no edges, the start's step and branch, role
    # unspecified, status planned, notes naming the ends and the summary; its geometry is the
    # top; the import can be undone, and then imported again only when asked.
    client = linked_client
    made = setup(client)
    job_id = made["job"]
    listed = client.get("/api/jobs", params={"detail": True}).json()
    assert [j["status"] for j in listed] == ["running"] and listed[0]["kind"] == "scan_path"
    early = client.post(f"/api/jobs/{job_id}/import-path")
    assert early.status_code == 422 and "No result" in early.text
    cloud_returns(remote, tmp_path, job_id, {"job": job_id, **RESULT}, path_text())
    (listed,) = client.get("/api/jobs", params={"detail": True, "refresh": True}).json()
    assert listed["status"] == "finished" and listed["fetch_error"] is None

    response = client.post(f"/api/jobs/{job_id}/import-path")
    assert response.status_code == 200, response.text
    node_id = response.json()["node_id"]
    node = client.get(f"/api/nodes/{node_id}").json()
    assert node["label"] == "Path A to B"
    assert (node["pos_x"], node["pos_y"]) == (400.0, 300.0)
    assert node["step_id"] == made["step"] and node["branch_id"] == made["branch"]
    assert node["role"] == "unspecified" and node["status"] == "planned"
    assert "Scan path from “A” to “B”" in node["notes"] and "reached the end" in node["notes"]
    assert "25.1 kcal/mol" in node["notes"] and "Stretch O1–H2" in node["notes"]
    canvas = client.get("/api/canvas").json()
    assert not any(node_id in (t["source_id"], t["target_id"]) for t in canvas["transitions"])
    (calc,) = client.get(f"/api/nodes/{node_id}/calculations").json()
    assert calc["route"].startswith("relaxed scan")
    assert calc["result"]["energy"] == pytest.approx(ENERGIES[3])
    job = client.get(f"/api/jobs/{job_id}").json()
    assert job["imported"]["node_id"] == node_id and job["status"] == "fetched"
    for params in ({}, {"again": True}):
        again = client.post(f"/api/jobs/{job_id}/import-path", params=params)
        assert again.status_code == 422 and "imported already" in again.text

    undo(client, import_entry(client, "path.xyz"))
    assert client.get(f"/api/nodes/{node_id}").status_code == 404
    # The job still names the removed node, so the app leaves it, but it can be imported again.
    (listed,) = client.get("/api/jobs", params={"detail": True}).json()
    assert listed["imported"] == {**listed["imported"], "node_id": node_id, "present": False}
    assert client.post(f"/api/jobs/{job_id}/import-path").status_code == 422
    again = client.post(f"/api/jobs/{job_id}/import-path", params={"again": True})
    assert again.status_code == 200 and again.json()["node_id"] != node_id


def test_a_job_without_a_path_says_why(linked_client, remote, fake_claude, tmp_path):  # noqa: F811
    # T-PATH-08: a failed session returns no path.xyz: the import is refused with its summary,
    # recorded on the job so the app does not try again by itself.
    client = linked_client
    made = setup(client)
    job_id = made["job"]
    failed = {
        "job": job_id,
        "status": "failed",
        "summary": "xTB stopped: SCF did not converge.",
        "outputs": [],
    }
    cloud_returns(remote, tmp_path, job_id, failed, None)
    response = client.post(f"/api/jobs/{job_id}/import-path")
    assert response.status_code == 422 and "SCF did not converge" in response.text
    job = client.get(f"/api/jobs/{job_id}").json()
    assert "SCF did not converge" in job["import_error"]
    folder = cloud_jobs.jobs_dir(folder_of(client)) / job_id
    assert (folder / "result.json").is_file()  # fetched all the same
