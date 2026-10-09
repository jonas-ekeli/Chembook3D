"""The user's own coordinates for a scan path (D119, T-PATH-17…19): checked and sent as
`drive`, run first by the brief, followed by `pathtools.py check`, named in the notes; and the
helper's rotation of a ligand bound side-on to a metal."""

import json
import math

import pytest

from chembook3d import cloud_jobs, pathtools
from chembook3d.services import scan_path
from tests.test_overlay import xyz_text
from tests.test_pathway import edge, node
from tests.test_scan_path import scan_log


def _olefin(angle: float) -> list[list]:
    """Ethylene bound side-on 2.1 Å from a metal at the origin, turned `angle` (radians) about
    the line from the metal through its centre."""
    c, s = math.cos(angle), math.sin(angle)
    atoms = [("C", 2.10, 0.70, 0.0), ("C", 2.10, -0.70, 0.0),
             ("H", 2.45, 1.25, 0.90), ("H", 2.45, 1.25, -0.90),
             ("H", 2.45, -1.25, 0.90), ("H", 2.45, -1.25, -0.90)]  # fmt: skip
    return [[e, x, y * c - z * s, y * s + z * c] for e, x, y, z in atoms]


def olefin_complex(angle: float = 0.0) -> list[list]:
    """A made-up ruthenium alkylidene with ethylene bound side-on (atoms 1 and 2), the
    alkylidene carbon 3 and ruthenium 6, as in Jonas's dihedral C2–C1–Ru–C3."""
    olefin = _olefin(angle)
    return [
        olefin[0],
        olefin[1],
        ["C", -0.6, 0.0, 1.75],
        ["H", -0.1, 0.0, 2.72],
        ["H", -1.68, 0.0, 1.85],
        ["Ru", 0.0, 0.0, 0.0],
        ["Cl", 0.0, 0.0, -2.35],
        ["Cl", -2.3, 0.3, -0.3],
        *olefin[2:],
    ]


def test_a_ligand_bound_side_on_turns_about_the_metal():
    # T-PATH-17 (D119): the Ru–C1–C2 triangle made Ru–C1 a ring bond, so the olefin's turn was
    # left out; it is now one rotation per ligand, partner–atom–metal–other, in the scan block.
    start, end = olefin_complex(0.0), olefin_complex(math.radians(80))
    assert {(1, 2), (1, 6), (2, 6)} <= pathtools.bonds(start)
    report = pathtools.diff(start, end)
    (rotation,) = report["rotations"]
    first, atom, metal, other = rotation["atoms"]
    assert metal == 6 and {first, atom} == {1, 2} and other in (3, 7, 8)
    assert abs(rotation["scan"][1] - rotation["scan"][0]) > 30
    assert abs(rotation["other_way"][1] - rotation["scan"][1]) == pytest.approx(360, abs=0.2)
    assert "dihedral: " + ", ".join(map(str, rotation["atoms"])) in report["scan_block"]
    # Turned less than 30°: nothing to drive.
    assert pathtools.diff(start, olefin_complex(math.radians(20)))["rotations"] == []
    # A metal in a four-membered ring (a metallacyclobutane) is still left out.
    ring = [["Ru", 0, 0, 0], ["C", 2.0, 0, 0], ["C", 2.0, 1.55, 0], ["C", 0.4, 1.9, 0]]
    assert pathtools._side_on(ring, {0: {1, 3}, 1: {0, 2}, 2: {1, 3}, 3: {0, 2}}, 0, 1) is None
    # η³ (three atoms of the ligand on the metal) is left out too.
    allyl = {0: {1, 2, 3}, 1: {0, 2}, 2: {0, 1, 3}, 3: {0, 2}}
    rows = [["Ru", 0, 0, 0], ["C", 2, 1, 0], ["C", 2, 0, 0], ["C", 2, -1, 0]]
    assert pathtools._side_on(rows, allyl, 0, 1) is None


def complex_pair(client, role: str = "minimum") -> tuple[dict, dict]:
    a = node(client, label="A", xyz=xyz_text(olefin_complex(0.0)), charge=0, multiplicity=1)
    b = node(
        client,
        label="B",
        xyz=xyz_text(olefin_complex(math.radians(80))),
        charge=0,
        multiplicity=1,
        role=role,
    )
    edge(client, a["id"], b["id"])
    return a, b


def send(client, body: dict, status: int = 200) -> dict:
    response = client.post("/api/scan-paths", json={"start": False, **body})
    assert response.status_code == status, response.text
    return response.json()


def test_the_users_coordinates_go_first(open_client):
    # T-PATH-18 (D119): rows default to the ends' values, a dihedral may go the other way
    # round, and path.json, job.json and the brief carry them as strategy 1; held coordinates
    # are not driven twice, and bad rows are refused.
    a, b = complex_pair(open_client, role="transition_state")
    start, end = olefin_complex(0.0), olefin_complex(math.radians(80))
    turn = pathtools.measure(end, [2, 1, 6, 3]) - pathtools.measure(start, [2, 1, 6, 3])
    other_way = pathtools.measure(start, [2, 1, 6, 3]) + turn - math.copysign(360, turn)
    body = {
        "start_id": a["id"],
        "end_id": b["id"],
        "held": [{"end": "end", "atoms": [1, 6]}],
        "drive": [{"atoms": [2, 1, 6, 3], "to": other_way}, {"atoms": [1, 3]}],
    }
    job = send(open_client, body)["job"]
    folder = cloud_jobs.jobs_dir(open_client.app.state.investigation.folder) / job["id"]
    settings = json.loads((folder / "inputs" / "path.json").read_text(encoding="utf-8"))
    drive = settings["drive"]
    assert drive["order"] == "together" and job["scan_path"]["drive"] == drive
    dihedral, distance = drive["coordinates"]
    assert dihedral["kind"] == "dihedral" and dihedral["to"] == pytest.approx(other_way, abs=1e-3)
    assert dihedral["from"] == pytest.approx(pathtools.measure(start, [2, 1, 6, 3]), abs=1e-3)
    assert distance["from"] == distance["start_value"]
    assert distance["to"] == pytest.approx(pathtools.measure(end, [1, 3]), abs=1e-3)
    # The reacting atoms take in no driven atom by themselves; the gate is unchanged.
    text = (folder / "job.md").read_text(encoding="utf-8")
    assert "## Coordinates the user asked to drive" in text
    assert "drive these coordinates together, in one concerted scan" in text
    assert f"- dihedral 2, 1, 6, 3: from {dihedral['from']:.3f} to {other_way:.3f} °" in text
    first = text.index("**The user's design first, run as given.**")
    assert first < text.index("**Then downhill from the TS.**")
    assert "The TS is the end here: you may run it from the end back" in text
    assert "`user_strategy` reports the user's design either way" in text
    assert "With the user's own coordinates, `drive` too" in text

    staged = send(open_client, dict(body, drive_order="staged"))["job"]
    folder = cloud_jobs.jobs_dir(open_client.app.state.investigation.folder) / staged["id"]
    text = (folder / "job.md").read_text(encoding="utf-8")
    assert "1. dihedral 2, 1, 6, 3" in text and "2. distance 1, 3" in text
    assert "One stage per line, in the order given" in text

    # Without rows the brief is what it was.
    plain = send(open_client, dict(body, drive=[]))["job"]
    folder = cloud_jobs.jobs_dir(open_client.app.state.investigation.folder) / plain["id"]
    text = (folder / "job.md").read_text(encoding="utf-8")
    assert "user asked to drive" not in text and "**Downhill from the TS first.**" in text
    assert "drive" not in json.loads((folder / "inputs" / "path.json").read_text("utf-8"))

    for rows, words in (
        ([{"atoms": [6, 1]}], "is held at the TS"),
        ([{"atoms": [1, 3]}, {"atoms": [3, 1]}], "listed twice"),
        ([{"atoms": [1, 99]}], "2 to 4 different atoms"),
        ([{"atoms": [1, 3], "to": 0.1}], "a distance from 0.5 to 15 Å"),
        ([{"atoms": [1, 6, 3], "to": 190}], "an angle between 0° and 180°"),
        ([{"atoms": [1, 3]}] * 9, "at most 8"),
    ):
        assert words in send(open_client, dict(body, drive=rows), 422)["detail"], words
    assert send(open_client, dict(body, drive_order="random"), 422)


def test_check_says_whether_the_path_followed_them(tmp_path):
    # T-PATH-19 (D119): `check --settings` reports each of the user's rows, a dihedral past
    # ±180° compared the short way round; the notes say whose design the path is.
    start, end = olefin_complex(0.0), olefin_complex(math.radians(80))
    steps = [olefin_complex(math.radians(t)) for t in (0, 20, 40, 60, 80)]
    path = tmp_path / "path.xyz"
    path.write_text(scan_log(steps, [-50.0, -49.99, -49.98, -49.99, -50.0]), encoding="utf-8")
    t0, t1 = (pathtools.measure(r, [2, 1, 6, 3]) for r in (start, end))
    drive = [
        {"atoms": [2, 1, 6, 3], "from": t0, "to": t1 - 360},
        {"atoms": [1, 3], "from": 3.0, "to": 3.0},  # never where the path is
    ]
    settings = tmp_path / "path.json"
    settings.write_text(json.dumps({"drive": {"order": "together", "coordinates": drive}}))
    for name, rows in (("s.xyz", start), ("e.xyz", end)):
        (tmp_path / name).write_text(pathtools.format_frame(rows, name), encoding="utf-8")
    report = pathtools.check(str(path), start, end, drive=drive)
    turned, missed = report["drive"]
    assert turned["followed"] and turned["last"] == pytest.approx(t1, abs=1e-3)
    assert not missed["followed"]
    assert "drive" not in pathtools.check(str(path), start, end)
    args = ["check", str(path), str(tmp_path / "s.xyz"), str(tmp_path / "e.xyz")]
    assert pathtools.main([*args, "--settings", str(settings), "--json"]) == 0

    def notes(user):
        return scan_path._notes({"id": "j"}, {"path": {"user_strategy": user}}, "A", "B")

    assert "Driven by your coordinates (D119)." in notes({"gate": "passed", "chosen": True})
    assert "The agent's own design (yours missed: stopped 1.6 Å short)." in notes(
        {"gate": "missed", "why": "stopped 1.6 Å short", "chosen": False}
    )
    assert "your coordinates" not in scan_path._notes({"id": "j"}, {"path": {}}, "A", "B")
