"""Scan path between two connected nodes as a cloud job (D114, A60; T-PATH-01…05), and the
helper the cloud session uses (`pathtools.py`)."""

import json
import math

import numpy as np
import pytest

from chembook3d import cloud_jobs, cloud_templates, pathtools
from chembook3d.parsers import xtb
from chembook3d.services import atom_matching, geometry, scan_path
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
    assert pathtools.bonds(complex_) == {
        (int(i) + 1, int(j) + 1) for i, j in np.argwhere(np.triu(same))
    }
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
    report = pathtools.check(str(out), start, end, allowed=[[2, 1]])
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


def reference_dihedral(a, b, c, d):
    """IUPAC, as xTB measures it (an independent formula: the vectors projected onto the plane
    normal to the b–c bond)."""
    a, b, c, d = (np.array(p[1:4], dtype=float) for p in (a, b, c, d))
    axis = (c - b) / np.linalg.norm(c - b)
    v = (a - b) - np.dot(a - b, axis) * axis
    w = (d - c) - np.dot(d - c, axis) * axis
    return float(np.degrees(np.arctan2(np.dot(np.cross(axis, v), w), np.dot(v, w))))


def test_pathtools_dihedrals_follow_xtb(tmp_path):
    # T-PATH-09: cis is 0°, trans 180°, the sign as xTB and IUPAC give it; atoms from 1 in the
    # functions as on the command line; the app's held values use the same.
    cis = [["C", 1, 0, 0], ["C", 0, 0, 0], ["C", 0, 1, 0], ["C", 1, 1, 0]]
    trans = [["C", 1, 0, 0], ["C", 0, 0, 0], ["C", 0, 1, 0], ["C", -1, 1, 0]]
    turned = [["C", 1, 0, 0], ["C", 0, 0, 0], ["C", 0, 1, 0], ["C", 0.5, 1, 0.8]]
    assert pathtools.measure(cis, [1, 2, 3, 4]) == pytest.approx(0.0, abs=1e-9)
    assert abs(pathtools.measure(trans, [1, 2, 3, 4])) == pytest.approx(180.0)
    assert pathtools.measure(turned, [1, 2, 3, 4]) == pytest.approx(-57.99, abs=0.01)
    assert scan_path.value(turned, [1, 2, 3, 4]) == pytest.approx(-57.99, abs=0.01)
    rng = np.random.default_rng(3)
    for _ in range(50):
        rows = [["C", *rng.normal(size=3)] for _ in range(4)]
        assert pathtools.measure(rows, [1, 2, 3, 4]) == pytest.approx(reference_dihedral(*rows))
    with pytest.raises(ValueError):
        pathtools.measure(cis, [0, 1, 2])
    # The command line gives the same, one value per structure.
    (tmp_path / "t.xyz").write_text(pathtools.format_frame(turned, "t"), encoding="utf-8")
    assert pathtools.main(["measure", str(tmp_path / "t.xyz"), "1", "2", "3", "4"]) == 0


def test_pathtools_frames_trace_and_spikes(tmp_path, capsys):
    # T-PATH-10: one structure of a scan log out as xyz; a trace of the driven coordinate, which
    # fails when the scan moved nothing; a conformer flipping in one step is reported as a
    # spike, with the top found without it; the cloud files keep Python's cache out of git.
    start = [list(r) for r in WATER_A]
    stretched = [[e, x, y * f, z * f] for (e, x, y, z), f in zip(start, (1, 1.4, 1), strict=True)]
    steps = [
        [[e, x, y + t * (yy - y), z + t * (zz - z)]
         for (e, x, y, z), (_, _, yy, zz) in zip(start, stretched, strict=True)]
        for t in np.linspace(0, 1, 5)
    ]  # fmt: skip
    log = tmp_path / "xtbscan.log"
    log.write_text(scan_log(steps, [-5.0, -4.995, -4.99, -4.993, -4.996]), encoding="utf-8")
    last = tmp_path / "last.xyz"
    assert pathtools.main(["frames", str(log), "--last", "-o", str(last)]) == 0
    assert pathtools.rmsd(pathtools.read_xyz(last), steps[-1]) < 1e-6
    third = tmp_path / "third.xyz"
    assert pathtools.main(["frames", str(log), "--index", "3", "-o", str(third)]) == 0
    assert pathtools.rmsd(pathtools.read_xyz(third), steps[2]) < 1e-6
    target = pathtools.measure(stretched, [1, 2])
    report = pathtools.trace(str(log), [[1, 2]], [target])
    assert [r["kcal"] for r in report["structures"]][2] == pytest.approx(0.01 * 627.509, abs=0.01)
    assert report["targets"][0]["moved"] and report["targets"][0]["reached"]
    capsys.readouterr()
    assert pathtools.main(["trace", str(log), "--atoms", "1", "2", "--to", f"{target}"]) == 0
    # Driving a coordinate the scan never touched: it ends normally, the trace says so.
    assert pathtools.main(["trace", str(log), "--atoms", "1", "3", "--to", "2.5"]) == 1
    assert "moved False" in capsys.readouterr().out
    # A flip in one structure: high above both neighbours, with a jump into it.
    flipped = [r[:] for r in steps[2]]
    flipped[2] = ["H", 0.0, -0.2, 2.5]
    spiky = tmp_path / "spiky.xyz"
    spiky.write_text(
        scan_log([*steps[:2], flipped, *steps[3:]], [-5.0, -4.995, -4.9, -4.993, -4.996]),
        encoding="utf-8",
    )
    report = pathtools.check(str(spiky), start, stretched)
    assert report["spikes"] == [3] and report["top"] == 3
    assert report["top_without_spikes"] is None and not report["good"]
    smooth = pathtools.check(str(log), start, stretched)
    assert smooth["spikes"] == [] and smooth["top"] == smooth["top_without_spikes"] == 3
    assert cloud_templates.TOOLS_IGNORE_TEXT.strip() == "__pycache__/"


def g98_text(wavenumbers, modes, numbers) -> str:
    """A Gaussian-style frequency block as `xtb --hess` writes it to g98.out."""
    lines = [" Harmonic frequencies (cm**-1), IR intensities (KM/Mole)", ""]
    for k in range(0, len(wavenumbers), 3):
        chunk = range(k, min(k + 3, len(wavenumbers)))
        lines.append(" Frequencies --" + "".join(f"{wavenumbers[m]:14.4f}" for m in chunk))
        lines.append(" Red. masses --" + "".join(f"{1.0:14.4f}" for _ in chunk))
        lines.append("  Atom AN" + "      X      Y      Z  " * len(chunk))
        for i, z in enumerate(numbers):
            cells = "".join(f"{x:7.2f}" for m in chunk for x in modes[m][i])
            lines.append(f"{i + 1:6d}{z:4d}  {cells}")
    return "\n".join(lines) + "\n"


def test_a_ts_end_sends_its_mode_and_the_session_can_read_it(open_client, tmp_path):
    # T-PATH-11 (D116): a TS end with a frequency job sends its imaginary mode in the start's
    # numbering, turned with the end as it is placed on the start, so pathtools finds the
    # held distance along it; the brief puts the downhill run first (reversed when the TS is
    # the end) and explains xTB's scan input; a held coordinate carries both ends' values; a
    # g98.out gives the same mode, and the push goes toward the other end.
    ts, ts_rows, mode = ts_and_mode(open_client)
    pushed = [[e, *(np.array(r) + 0.6 * m)] for (e, *r), m in zip(ts_rows, mode, strict=True)]
    start_rows, _ = scrambled(pushed, seed=4)
    start = node(open_client, label="before", xyz=xyz_text(start_rows), role="minimum",
                 charge=ts["charge"], multiplicity=ts["multiplicity"])  # fmt: skip
    edge(open_client, start["id"], ts["id"])
    out = plan(open_client, {"start_id": start["id"], "end_id": ts["id"]})
    held = [s for s in out["ts_ends"][0]["suggested"] if s["ticked"]]
    body = {
        "start_id": start["id"],
        "end_id": ts["id"],
        "held": [{"end": "end", "atoms": s["atoms"]} for s in held],
        "start": False,
    }
    response = open_client.post("/api/scan-paths", json=body)
    assert response.status_code == 200, response.text
    job = response.json()["job"]
    folder = cloud_jobs.jobs_dir(open_client.app.state.investigation.folder) / job["id"]
    end_rows = pathtools.read_xyz(folder / "inputs" / "end.xyz")
    wavenumber, vectors = pathtools.read_mode(folder / "inputs" / "end_mode.json")
    assert wavenumber == out["ts_ends"][0]["imaginary"] and len(vectors) == len(end_rows)
    assert not (folder / "inputs" / "start_mode.json").exists()
    report = pathtools.mode_report(end_rows, (wavenumber, vectors), [s["atoms"] for s in held])
    assert report["imaginary"] and report["runs_along"] is True
    top = report["top_distances"][0]["atoms"]
    assert top == "-".join(map(str, sorted(held[0]["atoms"])))
    assert report["coordinates"][0]["share"] == pytest.approx(1.0)
    # Pushed along the mode, the TS comes closer to the start than it was.
    start_xyz = pathtools.read_xyz(folder / "inputs" / "start.xyz")
    downhill = pathtools.displace(end_rows, (wavenumber, vectors), start_xyz, 0.2)
    assert pathtools.rmsd(downhill, start_xyz) < pathtools.rmsd(end_rows, start_xyz)
    settings = json.loads((folder / "inputs" / "path.json").read_text(encoding="utf-8"))
    first = settings["held"][0]
    assert first["value"] == pytest.approx(held[0]["end_value"])
    assert first["other_value"] == pytest.approx(held[0]["start_value"])
    text = (folder / "job.md").read_text(encoding="utf-8")
    for words in (
        "Downhill from the TS first",
        "displace inputs/end.xyz inputs/end_mode.json --toward inputs/start.xyz",
        "join it with `--reverse`",
        "by their **position in\nthe `$constrain` block**",
        "A later stage keeps what earlier stages did",
        "about 60 minutes of xTB time and at most 6 strategies",
        '"gate": "missed"',
        "inputs/end_mode.json`: the end's imaginary mode",
    ):
        assert words in text, words
    assert "A TS end that is only a guess" not in text

    # The session's own frequency job reads the same: g98.out holds the mode among others.
    numbers = [{"C": 6, "H": 1, "O": 8, "N": 7}.get(r[0], 6) for r in end_rows]
    others = [[[0.1, 0.0, 0.0]] * len(end_rows), [[0.0, 0.1, 0.0]] * len(end_rows)]
    g98 = tmp_path / "g98.out"
    g98.write_text(g98_text([35.0, wavenumber, 80.0], [others[0], vectors, others[1]], numbers))
    read = pathtools.read_mode(g98)
    assert read[0] == pytest.approx(wavenumber, abs=1e-3)
    assert np.allclose(read[1], vectors, atol=0.006)
    assert pathtools.main(["mode", str(folder / "inputs" / "end.xyz"), str(g98), "--atoms",
                           *map(str, held[0]["atoms"])]) == 0  # fmt: skip


def test_ends_without_a_bond_change_are_flagged_and_a_missed_gate_is_noted(open_client):
    # T-PATH-12 (D116): two conformers (no bond forms or breaks) get a warning in the plan and
    # a line in the brief; a path that missed the quality check, with a conformer note and a
    # TS check from `pathtools.py mode`, says so in the new node's notes.
    a = node(open_client, label="A", xyz=xyz_text(WATER_A), charge=0, multiplicity=1)
    turned = [list(WATER_A[0]), ["H", 0.0, 0.70, -0.52], list(WATER_A[2])]
    b = node(open_client, label="B", xyz=xyz_text(turned), charge=0, multiplicity=1)
    edge(open_client, a["id"], b["id"])
    out = plan(open_client, {"start_id": a["id"], "end_id": b["id"]})
    assert out["warnings"] and "No bond forms or breaks" in out["warnings"][0]
    response = open_client.post(
        "/api/scan-paths", json={"start_id": a["id"], "end_id": b["id"], "start": False}
    )
    folder = cloud_jobs.jobs_dir(open_client.app.state.investigation.folder)
    text = (folder / response.json()["job"]["id"] / "job.md").read_text(encoding="utf-8")
    assert "mostly a change of conformation" in text and "Downhill" not in text
    assert "Neither end is a transition state" in text

    result = {
        "summary": "The closest path stops in a nearby rotamer.",
        "path": {
            "gate": "missed",
            "missed": ["end RMSD 1.27 Å", "a spike at structure 9"],
            "reached_end": False,
            "end_rmsd": 1.27,
            "conformer_note": "The ends differ in the mesityl rotation only.",
            "ts_check": {
                "wavenumber": -412.3,
                "runs_along": False,
                "coordinates": [{"atoms": "2-3", "share": 0.21}],
            },  # fmt: skip
        },
    }
    notes = scan_path._notes({"id": "job-1"}, result, "A", "B")
    assert (
        "Did not pass the quality check (D116): end RMSD 1.27 Å; a spike at structure 9." in notes
    )
    assert "End not reached (RMSD 1.27 Å)." in notes
    assert "Conformers: The ends differ in the mesityl rotation only." in notes
    assert (
        "TS check: imaginary mode 412i cm⁻¹; does not run along them; held distances 2-3 21% "
        "of the largest change." in notes
    )
    assert "TS check: looks fine" in scan_path._notes(
        {"id": "j"}, {"path": {"ts_check": "looks fine"}}, "A", "B"
    )


DCE = FIXTURES / "xtb" / "dce_scan"


def test_pathtools_round_two(tmp_path, capsys):
    # T-PATH-14 (D118): the turning bonds of a real xTB scan, one dihedral each and both ways
    # round, with a concerted block; methyl groups and rings left out; trace reads the stage's
    # scan.inp and warns about a scan that drove nothing and a first point that slipped; the
    # path finder's kcal/mol energies are turned into Eh and refused by join as they are; a
    # final optimisation joins as one structure; the end is judged on the reacting atoms.
    log = pathtools.read_frames(DCE / "xtbscan.log")
    first, last = log[0][1], log[-1][1]
    report = pathtools.diff(first, last)
    (rotation,) = report["rotations"]
    assert rotation["bond"] == "1-2" and sorted(rotation["atoms"][::3]) == [3, 6]
    assert abs(rotation["scan"][1] - rotation["scan"][0]) == pytest.approx(180, abs=0.5)
    assert abs(rotation["other_way"][1] - rotation["other_way"][0]) == pytest.approx(180, abs=0.5)
    assert "mode=concerted" in report["scan_block"] and "dihedral: " in report["scan_block"]
    assert "dihedrals" not in report
    # Ethane turned: only hydrogens beyond each carbon, so nothing to drive.
    ethane = [["C", 0, 0, 0.76], ["C", 0, 0, -0.76]] + [
        ["H", math.cos(a), math.sin(a), s * 1.15] for s in (1, -1) for a in (0.0, 2.09, 4.19)
    ]
    turned = [r[:] for r in ethane]
    for r in turned[5:]:
        x, y = r[1], r[2]
        r[1], r[2] = x * math.cos(1.0) - y * math.sin(1.0), x * math.sin(1.0) + y * math.cos(1.0)
    assert pathtools.diff(ethane, turned)["rotations"] == []
    ring = {0: {1, 3}, 1: {0, 2}, 2: {1, 3}, 3: {2, 0, 4}, 4: {3}}
    assert pathtools._in_ring(ring, 0, 1) and not pathtools._in_ring(ring, 3, 4)

    # trace with the fixture's own input: the dihedral went from 180° to 0° as asked.
    report = pathtools.trace_input(DCE / "xtbscan.log", DCE / "scan.inp")
    (target,) = report["targets"]
    assert target["moved"] and target["reached"] and not target["first_slipped"]
    assert report["warnings"] == []
    # A scan line naming the wrong constraint: the held distance was "scanned", the dihedral
    # moved though it was meant to be held.
    wrong = tmp_path / "wrong.inp"
    wrong.write_text(
        "$constrain\n  force constant=1.0\n  dihedral: 3,1,2,6,180.0\n  distance: 1,2,1.52\n"
        "$scan\n  2: 1.52,2.50,13\n$end\n",
        encoding="utf-8",
    )
    report = pathtools.trace_input(DCE / "xtbscan.log", wrong)
    held, scanned = report["targets"]
    assert not held["reached"] and not scanned["moved"]
    assert any("did not drive it" in w for w in report["warnings"])
    assert any("held at 180.0 but ended at 0.005" in w for w in report["warnings"])
    # A first point that slipped: the scan was asked to start at 120°.
    slipped = tmp_path / "slipped.inp"
    slipped.write_text(
        "$constrain\n  dihedral: 3,1,2,6,120.0\n$scan\n  1: 120.0,0.0,13\n$end\n", encoding="utf-8"
    )
    report = pathtools.trace_input(DCE / "xtbscan.log", slipped)
    assert report["targets"][0]["first_slipped"]
    assert "relaxed away before the scan began" in report["warnings"][0]
    assert pathtools.main(["trace", str(DCE / "xtbscan.log"), "--input", str(slipped)]) == 1
    missing = tmp_path / "missing.inp"
    missing.write_text("$constrain\n  dihedral: 3,1,2,6,180.0\n$scan\n  2: 1,2,3\n$end\n")
    with pytest.raises(ValueError, match="names no \\$constrain line"):
        pathtools.trace_input(DCE / "xtbscan.log", missing)

    # The path finder's file: kcal/mol from its first structure; its output the total energy.
    finder = tmp_path / "xtbpath.xyz"
    finder.write_text(
        "".join(
            pathtools.format_frame(rows, f" energy: {k:.12f} xtb: 6.7.1 (edcfbbe)")
            for (_, rows), k in zip(log[:3], [0.0, 1.5, -0.4], strict=True)
        ),
        encoding="utf-8",
    )
    output = tmp_path / "path.out"
    output.write_text(
        "          | TOTAL ENERGY             -15.416221781784 Eh   |\n", encoding="utf-8"
    )
    with pytest.raises(ValueError, match="pathtools.py pathfinder"):
        pathtools.join([finder], ["xtb a.xyz --path b.xyz"])
    stage = tmp_path / "stage-1.xyz"
    assert pathtools.main(["pathfinder", str(finder), str(output), "-o", str(stage)]) == 0
    energies = [pathtools.energy_of(c) for c, _ in pathtools.read_frames(stage)]
    assert energies[0] == pytest.approx(-15.416221781784)
    assert (energies[1] - energies[0]) * pathtools.HARTREE_KCAL == pytest.approx(1.5)
    # A final optimisation's xtbopt.xyz joins as it is, as one more structure.
    final = tmp_path / "xtbopt.xyz"
    final.write_text(
        pathtools.format_frame(last, " energy: -15.4162 gnorm: 0.0001 xtb: 6.7.1 (edcfbbe)"),
        encoding="utf-8",
    )
    text = pathtools.join([stage, final], ["xtb a.xyz --path b.xyz", "xtb x.xyz --opt"])
    assert text.count(" energy: ") == 4 and "stage: 2 call: xtb x.xyz --opt" in text

    # The end judged on the reacting atoms: a water whose far hydrogen differs.
    start = [list(r) for r in WATER_A]
    end = [["O", 0.0, 0.0, 0.1173], ["H", 0.0, 1.30, -0.80], ["H", 0.0, -0.7572, -0.4692]]
    assert pathtools.active_atoms(start, end) == [1, 2, 3]
    assert pathtools.active_atoms(start, start) == []
    assert pathtools.active_atoms(start, start, held=[[1, 2]]) == [1, 2, 3]
    capsys.readouterr()


def test_the_end_is_judged_on_the_reacting_atoms(tmp_path):
    # T-PATH-15 (D118): a chain whose far end ends in another conformer reaches the end over
    # the reacting atoms only; without `active` it does not.
    chain = [["C", 1.5 * k, 0.0, 0.0] for k in range(8)]
    start = [r[:] for r in chain]
    end = [r[:] for r in chain]
    end[0] = ["C", -0.5, 1.2, 0.0]  # the reacting end moves (a bond 0-1 breaks)
    for k in range(5, 8):  # the far end bends away
        end[k] = ["C", 1.5 * 4 + 1.5 * (k - 4) * 0.5, 1.5 * (k - 4) * 0.85, 0.0]
    # The path moves only the reacting carbon; the far end keeps the start's conformer.
    path = tmp_path / "path.xyz"
    steps = []
    for t in np.linspace(0, 1, 8):
        rows = [r[:] for r in start]
        rows[0] = ["C", *(a + t * (b - a) for a, b in zip(start[0][1:], end[0][1:], strict=True))]
        steps.append(rows)
    path.write_text(scan_log(steps, [-5.0 + 0.001 * k for k in range(8)]), encoding="utf-8")
    active = pathtools.active_atoms(start, end)
    assert active == [1, 2, 3, 4]
    whole = pathtools.check(str(path), start, end)
    judged = pathtools.check(str(path), start, end, active=active)
    assert whole["judged_on"] == "all atoms" and not whole["reached_end"]
    assert judged["judged_on"] == "the reacting atoms" and judged["active_atoms"] == 4
    assert judged["reached_end"] and judged["active_end_rmsd"] < 0.01
    assert judged["end_rmsd"] == pytest.approx(whole["end_rmsd"])
    settings = tmp_path / "path.json"
    settings.write_text(json.dumps({"active": active}), encoding="utf-8")
    assert pathtools.main(["check", str(path), *_saved(tmp_path, start, end),
                           "--settings", str(settings), "--json"]) == 0  # fmt: skip


def _saved(folder, start, end) -> list[str]:
    for name, rows in (("s.xyz", start), ("e.xyz", end)):
        (folder / name).write_text(pathtools.format_frame(rows, name), encoding="utf-8")
    return [str(folder / "s.xyz"), str(folder / "e.xyz")]


def test_the_job_names_the_reacting_atoms(open_client):
    # T-PATH-16 (D118): path.json lists the atoms the end is judged on, the brief gates on
    # them and gives the tested path finder settings, the stage checks and the feedback file;
    # the notes give both RMSDs and point at the session's feedback.
    a, b = water_pair(open_client, role="transition_state")
    edge(open_client, a["id"], b["id"])
    body = {
        "start_id": a["id"],
        "end_id": b["id"],
        "pairs": [[2, 1]],
        "held": [{"end": "end", "atoms": [1, 2]}],
        "start": False,
    }
    response = open_client.post("/api/scan-paths", json=body)
    assert response.status_code == 200, response.text
    job = response.json()["job"]
    folder = cloud_jobs.jobs_dir(open_client.app.state.investigation.folder) / job["id"]
    settings = json.loads((folder / "inputs" / "path.json").read_text(encoding="utf-8"))
    assert settings["active"] == [1, 2, 3] and job["scan_path"]["active"] == [1, 2, 3]
    text = (folder / "job.md").read_text(encoding="utf-8")
    for words in (
        "`active`: the 3\n  atoms the end is judged on",
        "(3 of 3 here)",
        "--settings inputs/path.json",
        "`active_end_rmsd` at most\n0.5 Å",
        "A **strategy** is one design",
        "kpush=0.003",
        "pathtools.py pathfinder xtbpath.xyz path.out -o stage-1.xyz",
        "trace stage-1/xtbscan.log --input stage-1/scan.inp",
        "`60.0, 300.0` turns a dihedral through 180°",
        "outputs/feedback.md",
        "Stop early only when a path\npasses",
    ):
        assert words in text, words
    assert "stop after 6 strategies" not in text

    result = {"path": {"reached_end": True, "active_end_rmsd": 0.18, "end_rmsd": 1.25}}
    notes = scan_path._notes({"id": "job-2"}, result, "A", "B", feedback=True)
    assert "End reached over the reacting atoms (RMSD 0.18 Å, 1.25 Å over all atoms)." in notes
    assert "The session left feedback in jobs/job-2/outputs/feedback.md." in notes
    old = scan_path._notes({"id": "j"}, {"path": {"reached_end": False, "end_rmsd": 1.3}}, "A", "B")
    assert "End not reached (RMSD 1.30 Å)." in old and "feedback" not in old
