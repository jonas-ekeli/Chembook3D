"""Scan path between two connected nodes as a cloud job (D114, A60; T-PATH-01…05), and the
helper the cloud session uses (`pathtools.py`)."""

import json

import numpy as np
import pytest

from chembook3d import cloud_jobs, cloud_templates, pathtools
from chembook3d.parsers import xtb
from chembook3d.services import atom_matching, geometry
from tests.test_atom_matching import FIXTURES, frames, scrambled
from tests.test_import import TS, commit, fixture, named, upload
from tests.test_overlay import xyz_text
from tests.test_pathway import edge, node, patch, post


def rows_of(text: str) -> list[list]:
    return [[p[0], *map(float, p[1:4])] for p in (line.split() for line in text.splitlines()[2:])]


def import_ts(client) -> dict:
    plan = upload(client, TS, fixture(TS))
    node_id = commit(client, plan, **named(plan))["node_id"]
    return client.get(f"/api/nodes/{node_id}").json()


def ts_and_mode(client) -> tuple[dict, list[list], np.ndarray]:
    ts = import_ts(client)
    calcs = client.get(f"/api/nodes/{ts['id']}/calculations").json()
    freq = next(c for c in calcs if c["result"] and c["result"]["frequencies"])
    modes = client.get(f"/api/calculations/{freq['id']}/modes").json()
    return ts, rows_of(ts["xyz"]), np.array(modes["modes"][modes["order"][0]])


def plan(client, body: dict, status: int = 200) -> dict:
    response = client.post("/api/scan-paths/plan", json=body)
    assert response.status_code == status, response.text
    return response.json()


def test_a_ts_end_is_held_along_its_imaginary_mode(open_client):
    # T-PATH-01: the end is a TS with a frequency job; the start is the TS pushed down its
    # imaginary mode, numbered differently. The match finds the TS's atoms, the suggested
    # coordinates are the distances the mode changes most, ticked, and the solvent is THF.
    ts, ts_rows, mode = ts_and_mode(open_client)
    assert ts["role"] == "transition_state"
    pushed = [[e, *(np.array(r) + 0.6 * m)] for (e, *r), m in zip(ts_rows, mode, strict=True)]
    start_rows, truth = scrambled(pushed, seed=4)
    start = node(open_client, label="before", xyz=xyz_text(start_rows), role="minimum",
                 charge=ts["charge"], multiplicity=ts["multiplicity"])  # fmt: skip
    edge(open_client, start["id"], ts["id"])
    out = plan(open_client, {"start_id": start["id"], "end_id": ts["id"]})
    assert out["start"]["node_id"] == start["id"] and out["end"]["node_id"] == ts["id"]
    assert out["solvent"] == "thf" and "thf" in out["solvents"]
    assert (out["charge"], out["multiplicity"]) == (ts["charge"], ts["multiplicity"])
    # start atom i is TS atom order[i]
    order = [int(np.flatnonzero(np.array(truth) == i)[0]) for i in range(len(ts_rows))]
    assert [m - 1 for m in out["match"]["mapping"]] == order
    (ts_end,) = out["ts_ends"]
    assert ts_end["end"] == "end" and not ts_end["guess"] and ts_end["imaginary"] < 0
    held = [s for s in ts_end["suggested"] if s["ticked"]]
    assert held and all(s["kind"] == "distance" for s in held)
    # The most-changing distance along the mode, found here directly from the TS's own atoms.
    xyz = np.array([r[1:] for r in ts_rows])
    best = max(
        (
            abs(np.dot(mode[i] - mode[j], xyz[i] - xyz[j]) / np.linalg.norm(xyz[i] - xyz[j])),
            i,
            j,
        )
        for i in range(len(xyz))
        for j in range(i + 1, len(xyz))
        if np.linalg.norm(xyz[i] - xyz[j])
        < 1.8 * sum(atom_matching.RADII.get(ts_rows[k][0], 1.5) for k in (i, j))
    )
    in_start = sorted([order.index(best[1]) + 1, order.index(best[2]) + 1])
    assert sorted(held[0]["atoms"]) == in_start
    i, j = in_start
    assert held[0]["end_value"] == pytest.approx(
        np.linalg.norm(xyz[best[1]] - xyz[best[2]]), abs=1e-6
    )
    assert held[0]["start_value"] == pytest.approx(
        np.linalg.norm(np.array(start_rows[i - 1][1:]) - np.array(start_rows[j - 1][1:])), abs=1e-6
    )


def water_pair(client, **end_fields):
    a = node(client, label="A", xyz=xyz_text(WATER_A), charge=0, multiplicity=1)
    b = node(client, label="B", xyz=xyz_text(WATER_B), charge=0, multiplicity=1, **end_fields)
    return a, b


WATER_A = [("O", 0.0, 0.0, 0.1173), ("H", 0.0, 0.7572, -0.4692), ("H", 0.0, -0.7572, -0.4692)]
# The same water numbered H, O, H with one O–H stretched to a partial bond.
WATER_B = [("H", 0.0, 1.30, -0.80), ("O", 0.0, 0.0, 0.1173), ("H", 0.0, -0.7572, -0.4692)]


def test_a_ts_guess_must_be_ticked_and_the_ends_are_checked(open_client):
    # T-PATH-02: a TS made by hand has no mode: its suggestions are not ticked and sending
    # without one is refused. The checks refuse ends without an edge, with other atoms, other
    # charges, or no charge at all.
    a, b = water_pair(open_client, role="transition_state")
    assert "edge" in plan(open_client, {"start_id": a["id"], "end_id": b["id"]}, 422)["detail"]
    edge(open_client, b["id"], a["id"])  # either direction
    out = plan(open_client, {"start_id": a["id"], "end_id": b["id"]})
    # A planar water fits either way round, so which hydrogen is the stretched one is read back.
    mapping = out["match"]["mapping"]
    assert mapping[0] == 2 and sorted(mapping) == [1, 2, 3]
    stretched = mapping.index(1) + 1
    (ts_end,) = out["ts_ends"]
    assert ts_end["guess"] and not ts_end["frequency_job"]
    assert ts_end["suggested"] and not any(s["ticked"] for s in ts_end["suggested"])
    assert any(sorted(s["atoms"]) == [1, stretched] for s in ts_end["suggested"])
    body = {"start_id": a["id"], "end_id": b["id"], "start": False}
    refused = open_client.post("/api/scan-paths", json=body)
    assert refused.status_code == 422 and "tick the coordinates" in refused.text

    patch(open_client, f"/nodes/{b['id']}", {"charge": 1, "multiplicity": 2})
    assert (
        "different charges"
        in plan(open_client, {"start_id": a["id"], "end_id": b["id"]}, 422)["detail"]
    )
    patch(open_client, f"/nodes/{b['id']}", {"charge": None, "multiplicity": None})
    patch(open_client, f"/nodes/{a['id']}", {"charge": None})
    assert "neither" in plan(open_client, {"start_id": a["id"], "end_id": b["id"]}, 422)["detail"]
    c = node(open_client, label="C", xyz=xyz_text(WATER_A[:2]), charge=0, multiplicity=1)
    edge(open_client, a["id"], c["id"])
    assert (
        "joins or leaves"
        in plan(open_client, {"start_id": a["id"], "end_id": c["id"]}, 422)["detail"]
    )


def test_a_group_end_stands_for_its_representative(open_client):
    # T-PATH-03: an edge drawn to a group joins its members; the group stands for its
    # representative, or a member chosen.
    a, b = water_pair(open_client)
    other = node(open_client, label="B2", xyz=xyz_text(WATER_A), charge=0, multiplicity=1)
    group = post(
        open_client, "/groups/reconnect", {"member_ids": [b["id"], other["id"]], "label": "G"}
    )
    patch(open_client, f"/groups/{group['id']}", {"representative_id": other["id"]})
    edge(open_client, a["id"], group["id"])
    out = plan(open_client, {"start_id": a["id"], "end_id": group["id"]})
    assert out["end"]["node_id"] == other["id"] and out["end"]["group_label"] == "G"
    assert {m["id"] for m in out["end"]["members"]} == {b["id"], other["id"]}
    chosen = plan(
        open_client, {"start_id": a["id"], "end_id": group["id"], "end_member_id": b["id"]}
    )
    assert chosen["end"]["node_id"] == b["id"]
    # A member selected on its own reaches the edge through its group.
    assert plan(open_client, {"start_id": a["id"], "end_id": b["id"]})["end"]["node_id"] == b["id"]


def test_sending_writes_the_job(open_client):
    # T-PATH-04: the job carries the start, the end in the start's order, the match, the held
    # coordinates and settings, and instructions; it is recorded as a scan path job.
    a, b = water_pair(open_client, role="transition_state")
    edge(open_client, a["id"], b["id"])
    body = {
        "start_id": a["id"],
        "end_id": b["id"],
        "pairs": [[2, 1]],  # the stretched hydrogen, fixed by hand
        "held": [{"end": "end", "atoms": [1, 2]}],
        "solvent": "Dichloromethane",
        "start": False,
    }
    response = open_client.post("/api/scan-paths", json=body)
    assert response.status_code == 200, response.text
    out = response.json()
    assert out["start_error"] is None and out["job"]["status"] == "draft"
    job = out["job"]
    assert job["kind"] == "scan_path"
    assert job["scan_path"]["start_id"] == a["id"] and job["scan_path"]["end_id"] == b["id"]
    assert job["scan_path"]["solvent"] == "ch2cl2" and job["scan_path"]["uhf"] == 0
    folder = cloud_jobs.jobs_dir(open_client.app.state.investigation.folder) / job["id"]
    end_rows = pathtools.read_xyz(folder / "inputs" / "end.xyz")
    assert [r[0] for r in end_rows] == ["O", "H", "H"]
    assert pathtools.distance(end_rows[0], end_rows[1]) == pytest.approx(
        np.linalg.norm(np.array(WATER_B[0][1:]) - np.array(WATER_B[1][1:])), abs=1e-6
    )
    mapping = json.loads((folder / "inputs" / "mapping.json").read_text(encoding="utf-8"))
    assert mapping["mapping"] == [2, 1, 3] and mapping["fixed"] == [[2, 1]]
    assert mapping["end"]["label"] == "B"
    settings = json.loads((folder / "inputs" / "path.json").read_text(encoding="utf-8"))
    assert settings["held"][0]["atoms"] == [1, 2] and settings["held"][0]["kind"] == "distance"
    text = (folder / "job.md").read_text(encoding="utf-8")
    assert "scan path job" in text and "--chrg 0 --uhf 0 --alpb ch2cl2" in text
    assert "At the end (“B”, a TS): distance 1–2 = " in text
    assert "A TS end that is only a guess" in text

    bad = dict(body, solvent="lava")
    assert "no solvent" in open_client.post("/api/scan-paths", json=bad).text
    bad = dict(body, held=[{"end": "start", "atoms": [1, 2]}])
    assert (
        "only at an end that is a transition state"
        in open_client.post("/api/scan-paths", json=bad).text
    )


def test_sending_starts_the_job_or_says_why_not(open_client):
    # T-PATH-05: without a GitHub link the job is kept as a draft and the reason given.
    a, b = water_pair(open_client)
    edge(open_client, a["id"], b["id"])
    response = open_client.post("/api/scan-paths", json={"start_id": a["id"], "end_id": b["id"]})
    assert response.status_code == 200, response.text
    out = response.json()
    assert out["job"]["status"] == "draft"
    assert (
        "not linked to a GitHub repository" in out["start_error"]
        or "Claude Code" in out["start_error"]
    )
    other = open_client.post(
        "/api/scan-paths",
        json={"start_id": a["id"], "end_id": b["id"]},
        headers={"Origin": "https://example.com"},
    )
    assert other.status_code == 403


# ---------- pathtools ----------


def test_pathtools_agrees_with_the_app():
    ensemble = frames(FIXTURES / "crest" / "crest_conformers.xyz")
    a, b = ensemble[0], ensemble[7]
    assert pathtools.rmsd(a, b) == pytest.approx(geometry.aligned_rmsd(a, b), abs=1e-6)
    complex_ = frames(FIXTURES / "sterics" / "8.xyz")[0]
    turned, _ = scrambled(complex_, seed=1)
    same = atom_matching.bonds(np.array([r[1:] for r in complex_]), [r[0] for r in complex_])
    assert pathtools.bonds(complex_) == {(int(i), int(j)) for i, j in np.argwhere(np.triu(same))}
    # Written into the repository with the other cloud files, word for word.
    assert cloud_templates.PATHTOOLS_TEXT == (
        __import__("pathlib").Path(pathtools.__file__).read_text(encoding="utf-8")
    )


def scan_log(rows_list, energies) -> str:
    return "".join(
        pathtools.format_frame(rows, f" energy: {e:.10f} gnorm: 0.0001 xtb: 6.7.1 (edcfbbe)")
        for rows, e in zip(rows_list, energies, strict=True)
    )


def test_join_check_and_import(tmp_path):
    start = [list(r) for r in WATER_A]
    end = [["O", 0.0, 0.0, 0.1173], ["H", 0.0, 1.30, -0.80], ["H", 0.0, -0.7572, -0.4692]]
    steps = [
        [
            [e, x, y + t * (yy - y), z + t * (zz - z)]
            for (e, x, y, z), (_, _, yy, zz) in zip(start, end, strict=True)
        ]
        for t in np.linspace(0, 1, 7)
    ]
    (tmp_path / "s1.log").write_text(scan_log(steps[:4], [-5.0, -4.99, -4.98, -4.985]))
    # Stage 2 scanned from the end back to where stage 1 stopped.
    (tmp_path / "s2.log").write_text(scan_log(steps[3:][::-1], [-4.995, -4.99, -4.987, -4.985]))
    for name, rows in (("start.xyz", start), ("end.xyz", end)):
        (tmp_path / name).write_text(pathtools.format_frame(rows, name))
    call = "xtb s.xyz --opt --input scan.inp --gfn 2 --chrg 0 --uhf 0 --alpb water"
    out = tmp_path / "path.xyz"
    assert pathtools.main(["join", str(out), str(tmp_path / "s1.log"), str(tmp_path / "s2.log"),
                           "--call", call, "--reverse", "2"]) == 0  # fmt: skip
    step = xtb.parse_structures(out.read_text(encoding="utf-8"), "path.xyz").steps[0]
    assert step.geometry_stages == [1] * 4 + [2] * 4
    assert (step.route.solvation_model, step.route.solvent) == ("ALPB", "water")
    report = pathtools.check(str(out), start, end, allowed=[[1, 0]])
    assert report["reached_end"] and report["end_rmsd"] < 1e-6 and report["start_rmsd"] < 1e-6
    assert report["stray_bond_changes"] == [] and report["top"] == 3
    assert report["largest_jump"] < pathtools.GOOD_JUMP and report["good"]
    # A bond the ends do not differ by, formed on the way, is reported.
    stray = [r[:] for r in steps[2]]
    stray[2] = ["H", 0.0, 0.938, 0.05]  # close to the first hydrogen: an H–H bond
    (tmp_path / "stray.xyz").write_text(scan_log([start, stray, end], [-5.0, -4.9, -4.95]))
    bad = pathtools.check(str(tmp_path / "stray.xyz"), start, end)
    assert any(s["structure"] == 2 for s in bad["stray_bond_changes"]) and not bad["good"]
    diff = pathtools.diff(start, end)
    assert diff["broken"] == [[1, 2]] and diff["formed"] == []
    assert diff["distances"][0]["atoms"] in ([1, 2], [2, 3])
