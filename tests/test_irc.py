"""IRC calculations of a TS node (D131, A76): T-IMP-25 … T-IMP-29.

The files are synthetic (tests/gaussian_text.py), modelled on real Gaussian 16 IRC outputs:
the LQA integrator with the TS read from the checkpoint, the default integrator printing each
corrected point as CURRENT STRUCTURE, one job running both ways, restarts and end-point jobs."""

import numpy as np
import pytest

from chembook3d.models import Calculation, SourceFile
from chembook3d.parsers import gaussian
from chembook3d.services import irc as irc_service
from tests import gaussian_text as g
from tests.test_import import calculations, commit, copied_files, node, preview, upload

TS_ENERGY = -52.44


def import_ts(client, again: bool = False) -> str:
    """A TS (opt + freq with one imaginary frequency) ending on the IRCs' TS structure; `again`
    makes a second such node."""
    options = {"duplicate_action": "new"} if again else {}
    return commit(client, upload(client, "TS.out", g.checkpoint_ts(-0.01)), **options)["node_id"]


def add(client, ts: str, name: str, text: str, part: str = "path", direction=None) -> dict:
    """Adds a file to the TS's IRC; returns the plan it was imported with."""
    options = {"target_node_id": ts, "irc_part": part, "irc_direction": direction}
    plan = preview(client, upload(client, name, text, ts), **options)
    assert plan["kind"] == "irc" and plan["blockers"] == [], plan["blockers"]
    commit(client, plan, **options)
    return plan


def read(client, ts: str) -> dict:
    response = client.get(f"/api/nodes/{ts}/irc")
    assert response.status_code == 200, response.text
    return response.json()


def codes(plan: dict) -> list[str]:
    return [w["code"] for w in plan["warnings"]]


# ---------- T-IMP-25: reading Gaussian IRC outputs ----------


def test_lqa_irc_from_a_checkpoint():
    (step,) = gaussian.parse(g.irc(points=4)).steps
    assert step.route.irc == ["RCFC", "FORWARD", "LQA", "MAXPOINTS=10", "STEPSIZE=10"]
    run = step.irc
    assert run.requested == "forward" and run.directions == {1: "forward"}
    assert run.endings == {1: "minimum"} and run.max_points == 10
    assert run.ts_energy == TS_ENERGY
    assert [p.number for p in run.points] == [0, 1, 2, 3, 4]
    assert [p.coordinate for p in run.points] == [None, 0.1, 0.2, 0.3, 0.4]
    # The TS is the structure read from the checkpoint, in its own frame, put first.
    ts = step.geometries[run.points[0].structure]
    assert ts[0].x == pytest.approx(3.0)
    # A point is the last structure before its "Point Number" line.
    for point in run.points[1:]:
        carbon = step.geometries[point.structure][0]
        assert carbon.z == pytest.approx(g.irc_point("forward", point.number)[0][4])
        assert point.energy == pytest.approx(g.irc_energy(point.number, TS_ENERGY))
    assert step.converged_geometries == [p.structure for p in run.points]


def test_default_integrator_points_are_the_corrected_structures():
    (step,) = gaussian.parse(g.irc(points=3, style="hpc")).steps
    points = step.irc.points[1:]
    for point in points:
        carbon = step.geometries[point.structure][0]
        # Not the predictor step printed as the input orientation before it.
        assert carbon.z == pytest.approx(g.irc_point("forward", point.number)[0][4])
        assert point.energy == pytest.approx(g.irc_energy(point.number, TS_ENERGY))
    assert len(step.geometries) == 1 + 2 * len(points)


def test_one_job_running_both_ways():
    (step,) = gaussian.parse(g.irc(("forward", "reverse"), points=3)).steps
    run = step.irc
    assert run.requested == "both" and run.directions == {1: "forward", 2: "reverse"}
    assert run.endings == {1: "minimum", 2: "minimum"}
    assert [(p.path, p.number) for p in run.points] == [
        (1, 0), (1, 1), (1, 2), (1, 3), (2, 1), (2, 2), (2, 3)
    ]  # fmt: skip


@pytest.mark.parametrize(
    ("kwargs", "ending", "termination"),
    [
        ({"ending": "max_points"}, "max_points", "normal"),
        ({"ending": "stopped"}, "stopped", "abnormal"),
        ({"checkpoint": False}, "minimum", "normal"),
    ],
)
def test_how_an_irc_ended(kwargs, ending, termination):
    (step,) = gaussian.parse(g.irc(**kwargs)).steps
    assert step.irc.endings == {1: ending}
    assert step.termination == termination
    assert step.irc.points[0].energy == TS_ENERGY


def test_mass_weighted_distance():
    rows = [["C", 0.0, 0.0, 0.0], ["H", 0.0, 0.0, 1.0]]
    moved = [["C", 0.0, 0.0, 0.1], ["H", 0.0, 0.0, 1.0]]
    masses = irc_service._masses(rows)
    assert masses == pytest.approx([12.0, 1.007825], abs=1e-4)
    expected = np.sqrt(12.0) * 0.1 / irc_service.BOHR
    assert irc_service.mass_weighted_distance(rows, moved, masses) == pytest.approx(expected)


# ---------- T-IMP-26: an IRC is part of the TS, never one of its calculations ----------


def test_an_irc_leaves_the_node_as_it_was(open_client):
    ts = import_ts(open_client)
    before = node(open_client, ts)
    own = calculations(open_client, ts)
    plan = add(open_client, ts, "irc-f.out", g.irc(points=4))
    assert plan["mode"] == "onto" and plan["irc"] == {"part": "path", "direction": "forward"}
    after = node(open_client, ts)
    assert calculations(open_client, ts) == own
    for field in ("xyz", "calculation_count", "role", "status", "tags", "warnings"):
        assert after[field] == before[field], field
    summary = open_client.get(f"/api/nodes/{ts}/irc/summary").json()
    assert summary["count"] == 1 and summary["paths"] == {"reverse": 0, "forward": 1}
    # Deleting the node removes its IRC too, and says so.
    deleting = open_client.get(f"/api/nodes/{ts}/delete-preview").json()
    assert deleting["calculations"] == len(own) + 1


def test_an_irc_goes_only_on_a_transition_state(open_client):
    water = g.output(g.step("#P B3LYP/6-31G(d) Opt", [g.WATER], converged=True))
    minimum = commit(open_client, upload(open_client, "w.out", water))["node_id"]
    options = {"target_node_id": minimum, "irc_part": "path"}
    plan = preview(open_client, upload(open_client, "irc.out", g.irc(), minimum), **options)
    assert plan["blockers"] == ["IRC calculations go on a node whose role is transition state"]

    ts = import_ts(open_client)
    options = {"target_node_id": ts, "irc_part": "path"}
    plan = preview(open_client, upload(open_client, "w2.out", water, ts), **options)
    assert plan["blockers"] == ["No IRC was found in this file"]
    # An end-point job must say which end it was run on.
    upload_plan = upload(open_client, "end.out", g.end_point_opt_freq("forward", 4, -52.45), ts)
    response = open_client.post(
        f"/api/imports/{upload_plan['token']}/preview",
        json={"target_node_id": ts, "irc_part": "end"},
    )
    assert response.status_code >= 400


def test_an_irc_run_from_another_structure_is_flagged(open_client):
    ts = import_ts(open_client)
    plan = add(open_client, ts, "irc.out", g.irc(start=0.4))
    assert "IRC-START" in codes(plan)
    assert any("starts" in w and "from the TS" in w for w in read(open_client, ts)["warnings"])
    # The checkpoint's TS is in another frame; fitted, the right one starts at the TS.
    ts2 = import_ts(open_client, again=True)
    assert "IRC-START" not in codes(add(open_client, ts2, "irc2.out", g.irc()))


def test_endings_are_warned(open_client):
    ts = import_ts(open_client)
    plan = add(open_client, ts, "f.out", g.irc(ending="max_points"))
    assert "IRC-END" in codes(plan)
    assert (
        "maximum number of points (10)" in plan["warnings"][codes(plan).index("IRC-END")]["message"]
    )
    plan = add(open_client, ts, "r.out", g.irc(("reverse",), ending="stopped"), "path")
    assert {"IRC-END", "W-TERM"} <= set(codes(plan))


# ---------- T-IMP-27: the path through the TS ----------


def test_the_path_through_the_ts(open_client):
    ts = import_ts(open_client)
    add(open_client, ts, "forward.out", g.irc(points=4))
    add(open_client, ts, "reverse.out", g.irc(("reverse",), points=3, style="hpc"))
    found = read(open_client, ts)
    assert found["is_ts"] and found["warnings"] == [] and found["ts_energy"] == TS_ENERGY
    sides = [p["side"] for p in found["points"]]
    assert sides == ["reverse"] * 3 + ["ts"] + ["forward"] * 4
    assert [p["number"] for p in found["points"]] == [3, 2, 1, 0, 1, 2, 3, 4]
    coordinates = [p["coordinate"] for p in found["points"]]
    assert coordinates == sorted(coordinates) and coordinates[3] == 0
    assert coordinates[0] < 0 < coordinates[-1]
    # The reverse run's points are spaced like the forward run's: the same steps, mirrored.
    assert -coordinates[0] == pytest.approx(coordinates[-2], rel=1e-6)
    assert [p["relative"] for p in found["points"][3:]] == pytest.approx(
        [g.irc_energy(n, TS_ENERGY) - TS_ENERGY for n in range(5)]
    )
    assert [p["program_coordinate"] for p in found["points"][4:]] == [0.1, 0.2, 0.3, 0.4]
    # Every point is fitted onto the TS node's structure.
    assert found["points"][3]["rows"][0][1] == pytest.approx(0.0, abs=1e-6)
    by_side = {s["direction"]: s for s in found["sides"]}
    assert by_side["forward"]["ending"] == "minimum" and by_side["forward"]["points"] == 4
    assert by_side["reverse"]["points"] == 3
    assert by_side["reverse"]["last_relative"] == pytest.approx(g.irc_energy(3, 0))
    assert {p["file"]: p["used"] for p in found["parts"]} == {
        "forward.out": True,
        "reverse.out": True,
    }


def test_one_job_both_ways_fills_both_sides(open_client):
    ts = import_ts(open_client)
    plan = add(open_client, ts, "both.out", g.irc(("forward", "reverse"), points=2))
    assert plan["irc"]["direction"] == "both"
    found = read(open_client, ts)
    assert [p["side"] for p in found["points"]] == [
        "reverse",
        "reverse",
        "ts",
        "forward",
        "forward",
    ]
    (part,) = found["parts"]
    assert part["direction"] == "both"
    assert part["endings"] == {"forward": "minimum", "reverse": "minimum"}
    summary = open_client.get(f"/api/nodes/{ts}/irc/summary").json()
    assert summary["paths"] == {"reverse": 1, "forward": 1}


def test_a_restarted_irc_continues_and_a_new_one_replaces(open_client):
    ts = import_ts(open_client)
    add(open_client, ts, "first.out", g.irc(points=4, ending="max_points"))
    # A restart starts where the first one stopped.
    add(open_client, ts, "restart.out", g.irc(points=3, first=4))
    found = read(open_client, ts)
    forward = [p for p in found["points"] if p["side"] == "forward"]
    assert [p["number"] for p in forward] == [1, 2, 3, 4, 5, 6]
    assert [p["file"] for p in forward] == ["first.out"] * 4 + ["restart.out"] * 2
    assert all(p["used"] for p in found["parts"])
    assert {s["direction"]: s["ending"] for s in found["sides"]}["forward"] == "minimum"

    # One that starts again from the TS replaces both.
    add(open_client, ts, "again.out", g.irc(points=2))
    found = read(open_client, ts)
    assert [p["file"] for p in found["points"] if p["side"] == "forward"] == ["again.out"] * 2
    used = {p["file"]: (p["used"], p["note"]) for p in found["parts"]}
    assert used["again.out"] == (True, "")
    assert used["first.out"] == (False, "Replaced by a later IRC: again.out (forward)")
    assert used["restart.out"][0] is False


# ---------- T-IMP-28: the end points ----------


def test_end_point_energies_relative_to_the_ts(open_client):
    ts = import_ts(open_client)
    add(open_client, ts, "forward.out", g.irc(points=4))
    plan = add(
        open_client, ts, "end-f.out", g.end_point_opt_freq("forward", 4, -52.46), "end", "forward"
    )
    assert plan["irc"] == {"part": "end", "direction": "forward"}
    assert codes(plan).count("IRC-IMAG") == 0
    found = read(open_client, ts)
    by_side = {s["direction"]: s for s in found["sides"]}
    end = by_side["forward"]["end_point"]
    assert end["optimization_id"] and end["frequency_id"]
    assert end["converged"] is True and end["imaginary"] == 0
    # The optimization starts on the IRC's last point.
    assert end["start_rmsd"] == pytest.approx(0, abs=1e-6)
    assert end["values"]["E"]["relative"] == pytest.approx(-0.02)
    # Both have the same printed G correction, so ΔG is ΔE.
    assert end["values"]["G"]["relative"] == pytest.approx(-0.02)
    assert end["values"]["G_qh"]["relative"] is not None
    assert by_side["reverse"]["end_point"] is None
    summary = open_client.get(f"/api/nodes/{ts}/irc/summary").json()
    assert summary["optimizations"] == {"reverse": 0, "forward": 1}
    assert summary["frequencies"] == {"reverse": 0, "forward": 1}
    # The TS's own energies are untouched: its frequency job is still its own.
    assert [c["type"] for c in calculations(open_client, ts)] == ["ts_optimization", "frequency"]


def test_an_end_point_that_is_not_a_minimum(open_client):
    ts = import_ts(open_client)
    plan = add(
        open_client,
        ts,
        "end-r.out",
        g.end_point_opt_freq("reverse", 3, -52.45, imaginary=True),
        "end",
        "reverse",
    )
    assert "IRC-IMAG" in codes(plan)
    end = {s["direction"]: s for s in read(open_client, ts)["sides"]}["reverse"]["end_point"]
    assert end["imaginary"] == 1 and end["start_rmsd"] is None  # no reverse IRC yet


# ---------- T-IMP-29: removing a file ----------


def test_remove_a_file_is_the_undo_of_its_import(open_client):
    ts = import_ts(open_client)
    add(open_client, ts, "forward.out", g.irc(points=2))
    add(open_client, ts, "reverse.out", g.irc(("reverse",), points=2))
    copies = len(copied_files(open_client))
    found = read(open_client, ts)
    forward = next(p for p in found["parts"] if p["file"] == "forward.out")
    response = open_client.delete(f"/api/nodes/{ts}/irc/files/{forward['source_file_id']}")
    assert response.status_code == 204, response.text
    assert len(copied_files(open_client)) == copies - 1
    found = read(open_client, ts)
    assert [p["file"] for p in found["parts"]] == ["reverse.out"]
    assert [p["side"] for p in found["points"]] == ["reverse", "reverse", "ts"]
    history = open_client.get("/api/history", params={"record_id": ts}).json()
    assert history[0]["action"] == "undo_import"
    with open_client.app.state.investigation.sessions() as session:
        assert session.query(SourceFile).filter_by(original_name="forward.out").count() == 0
        assert session.query(Calculation).filter(Calculation.irc_part.is_not(None)).count() == 1

    # The TS's own file is not part of its IRC.
    own = calculations(open_client, ts)[0]["source_file"]["id"]
    assert open_client.delete(f"/api/nodes/{ts}/irc/files/{own}").status_code == 422
