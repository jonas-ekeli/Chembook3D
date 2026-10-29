"""Coordinates kept fixed along a scan path (D196, T-PATH-32): checked and sent as `constrain`,
put in every xtb call by the brief, checked by `pathtools.py check` and named in the notes."""

import json
import math

import pytest

from chembook3d import cloud_jobs, pathtools
from chembook3d.services import scan_path
from tests.test_pathway import edge
from tests.test_scan_path import plan, scan_log, water_pair
from tests.test_scan_path_drive import complex_pair, olefin_complex, send


def _job_files(client, job: dict) -> tuple[dict, str]:
    folder = cloud_jobs.jobs_dir(client.app.state.investigation.folder) / job["id"]
    settings = json.loads((folder / "inputs" / "path.json").read_text(encoding="utf-8"))
    return settings, (folder / "job.md").read_text(encoding="utf-8")


def test_kept_fixed_rows_go_into_every_xtb_call(open_client):
    # D196: a row defaults to the start's value, a typed value wins; path.json, job.json and the
    # brief carry them; a coordinate that is held at the TS, driven, listed twice or out of
    # range is refused.
    a, b = complex_pair(open_client, role="transition_state")
    start, end = olefin_complex(0.0), olefin_complex(math.radians(80))
    body = {
        "start_id": a["id"],
        "end_id": b["id"],
        "held": [{"end": "end", "atoms": [1, 6]}],
        "drive": [{"atoms": [2, 1, 6, 3]}],
        "constrain": [{"atoms": [6, 7]}, {"atoms": [7, 6, 8], "value": 95}],
    }
    job = send(open_client, body)["job"]
    settings, text = _job_files(open_client, job)
    bond, angle = settings["constrain"]
    assert job["scan_path"]["constrain"] == settings["constrain"]
    assert bond["kind"] == "distance" and bond["value"] == bond["start_value"]
    assert bond["value"] == pytest.approx(pathtools.measure(start, [6, 7]), abs=1e-3)
    assert angle["kind"] == "angle" and angle["value"] == 95
    assert angle["end_value"] == pytest.approx(pathtools.measure(end, [7, 6, 8]), abs=1e-3)
    assert "## Coordinates the user keeps fixed" in text
    assert f"- distance 6, 7: at {bond['value']:.3f} Å" in text
    assert "- angle 7, 6, 8: at 95.000 °" in text
    assert "With coordinates the user keeps fixed, `constrain` too" in text
    flat = " ".join(text.split())
    assert "`$constrain` block of every xtb call" in flat
    assert "`constrained` is `check`'s `constrained`" in flat
    # 95° is not where either end has the angle: the brief says so for both.
    assert "At the start, angle 7–6–8 is not at the value asked for" in flat
    assert "At the end node, angle 7–6–8 has another value" in flat

    # Without rows the brief is what it was.
    settings, text = _job_files(open_client, send(open_client, dict(body, constrain=[]))["job"])
    assert "constrain" not in settings and "keeps fixed" not in text
    # A row at the start's value that the end shares raises no note.
    _, text = _job_files(
        open_client, send(open_client, dict(body, constrain=[{"atoms": [6, 7]}]))["job"]
    )
    assert "At the start," not in text and "At the end node," not in text

    for rows, words in (
        ([{"atoms": [6, 1]}], "is held at the TS"),
        ([{"atoms": [3, 6, 1, 2]}], "is driven"),
        ([{"atoms": [6, 7]}, {"atoms": [7, 6]}], "listed twice"),
        ([{"atoms": [1, 99]}], "2 to 4 different atoms"),
        ([{"atoms": [6, 7], "value": 20}], "a distance from 0.5 to 15 Å"),
        ([{"atoms": [6, 7, 8], "value": 180}], "an angle between 0° and 180°"),
        ([{"atoms": [6, 7]}] * 9, "at most 8"),
    ):
        assert words in send(open_client, dict(body, constrain=rows), 422)["detail"], words


def test_a_bond_that_forms_or_breaks_is_not_kept_fixed(open_client):
    a, b = water_pair(open_client)
    edge(open_client, a["id"], b["id"])
    match = plan(open_client, {"start_id": a["id"], "end_id": b["id"], "pairs": [[2, 1]]})["match"]
    (pair,) = match["formed"] + match["broken"]
    body = {"start_id": a["id"], "end_id": b["id"], "pairs": [[2, 1]]}
    detail = send(open_client, dict(body, constrain=[{"atoms": pair[::-1]}]), 422)["detail"]
    assert "forms or breaks along the path" in detail
    assert send(open_client, dict(body, constrain=[{"atoms": [2, 1, 3]}]))["job"]


def test_check_says_whether_the_path_kept_them(tmp_path):
    # `check --settings` reports how far each strayed; a path that lets one go is not good.
    start, end = olefin_complex(0.0), olefin_complex(math.radians(80))
    steps = [olefin_complex(math.radians(t)) for t in (0, 20, 40, 60, 80)]
    steps[2] = [row[:] for row in steps[2]]
    steps[2][6][3] -= 0.3  # Cl 7 pulled 0.3 Å away from Ru 6 halfway
    path = tmp_path / "path.xyz"
    path.write_text(scan_log(steps, [-50.0, -49.99, -49.98, -49.99, -50.0]), encoding="utf-8")
    ru_cl = pathtools.measure(start, [6, 8])
    constrain = [{"atoms": [6, 8], "value": ru_cl}, {"atoms": [6, 7], "value": 2.35}]
    report = pathtools.check(str(path), start, end, constrain=constrain)
    other, pulled = report["constrained"]
    assert other["kept"] and other["largest_change"] == pytest.approx(0, abs=1e-3)
    assert not pulled["kept"] and pulled["largest_change"] == pytest.approx(0.3, abs=1e-3)
    assert not report["good"]
    assert pathtools.check(str(path), start, end, constrain=constrain[:1])["good"]
    assert "constrained" not in pathtools.check(str(path), start, end)
    # A dihedral is compared the short way round.
    t0 = pathtools.measure(start, [2, 1, 6, 3])
    (turned,) = pathtools.kept(pathtools.read_frames(str(path))[:1], [
        {"atoms": [2, 1, 6, 3], "value": t0 + 360},
    ])  # fmt: skip
    assert turned["kept"]

    settings = tmp_path / "path.json"
    settings.write_text(json.dumps({"constrain": constrain}))
    for name, rows in (("s.xyz", start), ("e.xyz", end)):
        (tmp_path / name).write_text(pathtools.format_frame(rows, name), encoding="utf-8")
    args = ["check", str(path), str(tmp_path / "s.xyz"), str(tmp_path / "e.xyz")]
    assert pathtools.main([*args, "--settings", str(settings), "--json"]) == 0

    job = {"id": "j", "scan_path": {"constrain": [
        {"kind": "distance", "atoms": [6, 7], "value": 2.35},
        {"kind": "angle", "atoms": [7, 6, 8], "value": 95.0},
    ]}}  # fmt: skip
    result = {"path": {"constrained": [{"atoms": "6-7", "largest_change": 0.3, "kept": False}]}}
    notes = scan_path._notes(job, result, "A", "B")
    assert "Kept fixed as you asked (D196): distance 6–7 at 2.35 Å, angle 7–6–8 at 95.0°." in notes
    assert "The path let go of 6-7 (by 0.30)." in notes
    assert "Kept fixed" not in scan_path._notes({"id": "j"}, {"path": {}}, "A", "B")
