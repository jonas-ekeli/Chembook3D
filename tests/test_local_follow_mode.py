"""Following a TS's imaginary mode both ways on this computer (D263c, A184, FR-LOC-20,
FR-LOC-21, T-LOC-51 to T-LOC-57).

The TS is a synthetic Gaussian frequency job (any level may be followed): two carbons 2.0 Å
apart, each with a hydrogen, the imaginary mode stretching the C1–C2 distance. The job runs with
the stand-in engine `stub-saddle`, whose double well for a mode-follow sits on that distance with
its top at the TS and wells 0.5 Å either side: the + end breaks the contact (2.5 Å) and the −
end makes the bond (1.5 Å). Minimum nodes made from the two wells are what the ends match.
"""

import anyio
import numpy as np
import pytest

from chembook3d import local_worker
from chembook3d.claude_panel import NOTEBOOK_GUIDE
from chembook3d.mcp_server import INSTRUCTIONS
from chembook3d.mcp_tools import BY_NAME, PANEL_ALLOWED
from chembook3d.models import Calculation
from chembook3d.services import follow_mode
from tests import gaussian_text as g
from tests.test_import import commit, upload
from tests.test_local_jobs import (  # noqa: F401 - the fixtures
    finished,
    folder_of,
    no_worker_left,
    stub_tools,
    wait_for,
)
from tests.test_mcp import _app_bridge

TS = [
    ("C", 6, 0.0, 0.0, 0.0),
    ("C", 6, 2.0, 0.0, 0.0),
    ("H", 1, -0.6, 0.9, 0.0),
    ("H", 1, 2.6, 0.9, 0.0),
]
# The imaginary mode stretches C1–C2; the others are any motion (only the lowest is followed).
MODES = [
    [[-0.5, 0.0, 0.0], [0.5, 0.0, 0.0], [0.0, 0.0, 0.0], [0.0, 0.0, 0.0]],
    [[0.0, 0.0, 0.0], [0.0, 0.0, 0.0], [0.0, 0.7, 0.0], [0.0, -0.7, 0.0]],
    [[0.0, 0.0, 0.1], [0.0, 0.0, -0.1], [0.0, 0.0, 0.0], [0.0, 0.0, 0.0]],
    [[0.0, 0.1, 0.0], [0.0, -0.1, 0.0], [0.0, 0.0, 0.0], [0.0, 0.0, 0.0]],
    [[0.0, 0.0, 0.0], [0.0, 0.0, 0.0], [0.5, 0.0, 0.0], [0.0, 0.0, 0.0]],
    [[0.0, 0.0, 0.0], [0.0, 0.0, 0.0], [0.0, 0.0, 0.0], [0.5, 0.0, 0.0]],
]


def frequency_lines(frequencies, modes, atoms) -> list[str]:
    """Gaussian's frequency block with the given displacements."""
    lines = []
    for start in range(0, len(frequencies), 3):
        chunk = list(range(start, min(start + 3, len(frequencies))))
        lines.append(" " + "".join(f"{k + 1:>23d}" for k in chunk))
        lines.append(" " + "".join(f"{'A':>23}" for _ in chunk))
        lines.append(" Frequencies --" + "".join(f"{frequencies[k]:>23.4f}" for k in chunk))
        for name in ("Red. masses --", "Frc consts  --", "IR Inten    --"):
            lines.append(f" {name}" + "".join(f"{1.0:>23.4f}" for _ in chunk))
        lines.append("  Atom  AN" + "      X      Y      Z  " * len(chunk))
        for n, atom in enumerate(atoms):
            row = f" {n + 1:5d} {atom[1]:3d}"
            for k in chunk:
                x, y, z = modes[k][n]
                row += f" {x:6.2f} {y:6.2f} {z:6.2f}  "
            lines.append(row)
    return lines


def ts_output(frequencies=(-500.0, 300.0, 600.0, 900.0, 1200.0, 3000.0)) -> str:
    """A TS frequency job at a DFT level (only `frequencies` change for a minimum's)."""
    return g.output(
        g.step(
            "#P B3LYP/6-31G(d) Freq",
            [TS],
            -77.0,
            title="ts",
            after=[*frequency_lines(list(frequencies), MODES, TS), *g.THERMO.splitlines()],
        )
    )


def shifted(dx: float) -> list[tuple]:
    """The TS with C2 and its hydrogen moved `dx` Å along x: a well of the stub's double
    well."""
    return [(e, n, x + (dx if k in (1, 3) else 0.0), y, z) for k, (e, n, x, y, z) in enumerate(TS)]


def xyz_of(atoms) -> str:
    return f"{len(atoms)}\n\n" + "\n".join(f"{a[0]} {a[2]} {a[3]} {a[4]}" for a in atoms) + "\n"


def make_node(client, label, atoms) -> str:
    response = client.post("/api/nodes", json={"label": label, "xyz": xyz_of(atoms)})
    assert response.status_code == 201, response.text
    return response.json()["id"]


def import_ts(client, text=None) -> tuple[str, dict]:
    made = commit(client, upload(client, "ts.log", text or ts_output()))["node_id"]
    client.patch(f"/api/nodes/{made}", json={"label": "TS", "role": "transition_state"})
    calcs = client.get(f"/api/nodes/{made}/calculations").json()
    return made, next(c for c in calcs if c["type"] == "frequency")


def follow(client, node_id, **body) -> dict:
    response = client.post(
        "/api/local/follow-mode", json={"node_id": node_id, "method": "stub-saddle", **body}
    )
    assert response.status_code == 200, response.text
    job = response.json()
    done = wait_for(client, job["id"], finished, timeout=120)
    assert done["status"] in ("converged", "not_converged"), done
    return done


def ends(client, job_id) -> dict:
    response = client.get(f"/api/local/jobs/{job_id}/ends")
    assert response.status_code == 200, response.text
    return response.json()


@pytest.mark.usefixtures("stub_tools")
def test_both_ends_of_the_mode_are_relaxed_and_matched(open_client, tmp_path):
    """T-LOC-51: the job starts from the frequency job's structure (a DFT level) with its
    lowest imaginary mode, relaxes both ends for at most 40 steps and is never imported; the
    + end breaks the C1–C2 contact and matches node A and a folder's output, the − end makes
    the bond and matches node B: it links A and B, with high confidence (A184). Inspect shows
    each end's frames."""
    ts_id, frequency = import_ts(open_client)
    a_id = make_node(open_client, "A", shifted(0.5))
    b_id = make_node(open_client, "B", shifted(-0.5))
    outputs = tmp_path / "outputs"
    outputs.mkdir()
    (outputs / "a.log").write_text(g.output(g.step("#P B3LYP/6-31G(d) SP", [shifted(0.5)])))
    # A second job on the same structure (a single point at another level) is the same one.
    (outputs / "a_sp.log").write_text(g.output(g.step("#P PBE0/def2TZVP SP", [shifted(0.5)])))

    before = len(open_client.get(f"/api/nodes/{ts_id}/calculations").json())
    done = follow(open_client, ts_id, folder=str(outputs))
    assert done["kind"] == "follow_mode" and done["method"] == "stub-saddle"
    spec = done["follow_mode"]
    assert spec["calculation_id"] == frequency["id"] and spec["ts_node_id"] == ts_id
    assert spec["shift"] == 0.4 and spec["steps"] == 60 and spec["wavenumber"] == -500.0  # A188
    assert spec["folder"] == str(outputs) and "mode" not in spec
    assert done["nothing_to_import"] and not done["importable"] and not done["awaiting_choice"]
    found = done["result"]["follow_mode"]
    assert set(found["ends"]) == {"+", "-"}
    assert all(0 < e["cycles"] <= 61 for e in found["ends"].values())
    assert [e["shift"] for e in found["ends"].values()] == [0.4, -0.4]

    refused = open_client.post(f"/api/local/jobs/{done['id']}/import")
    assert refused.status_code == 422 and "never imported" in refused.json()["detail"]
    assert len(open_client.get(f"/api/nodes/{ts_id}/calculations").json()) == before
    assert len(open_client.get("/api/nodes").json()) == 3

    matched = ends(open_client, done["id"])
    plus, minus = matched["ends"]["+"], matched["ends"]["-"]
    assert [b["bond"] for b in plus["broken"]] == [] and [b["bond"] for b in plus["formed"]] == []
    assert [b["bond"] for b in minus["formed"]] == ["C1–C2"]
    assert [m["node_id"] for m in plus["matches"]] == [a_id]
    assert [m["node_id"] for m in minus["matches"]] == [b_id]
    assert plus["matches"][0]["rmsd"] < 0.5
    assert sorted(f["path"] for f in plus["files"]) == ["a.log", "a_sp.log"]
    assert len({f["structure"] for f in plus["files"]}) == 1 and minus["files"] == []
    assert matched["outcome"] == "links" and matched["confidence"] == "high"
    assert matched["verdict"] == "Followed the mode of “TS”: links “A” and “B”"

    steps = open_client.get(f"/api/local/jobs/{done['id']}/steps").json()["steps"]
    assert len(steps) == 2 and all(s["frames"] for s in steps)
    assert [len(s["frames"]) for s in steps] == [e["cycles"] for e in found["ends"].values()]


@pytest.mark.usefixtures("stub_tools")
def test_an_end_that_matches_nothing_becomes_a_node(open_client):
    """T-LOC-52: with no minimum there, no end matches; the − end is made a planned minimum
    beside the TS (coordinates only, the TS's charge and multiplicity), which it then matches,
    so a second one is refused unless asked anyway; with node A the TS links A and the new
    node, and a duplicate of the new node makes the − end the nearest of two (A191: medium)."""
    ts_id, _ = import_ts(open_client)
    done = follow(open_client, ts_id)
    matched = ends(open_client, done["id"])
    assert matched["outcome"] == "none" and matched["confidence"] is None
    assert "files" not in matched["ends"]["+"]
    assert "No end matches a structure" in matched["flags"][0]
    assert matched["verdict"].endswith("+ end matches nothing, − end matches nothing")

    made = open_client.post(f"/api/local/jobs/{done['id']}/ends/-/node")
    assert made.status_code == 201, made.text
    made = made.json()
    node = open_client.get(f"/api/nodes/{made['node_id']}").json()
    ts = open_client.get(f"/api/nodes/{ts_id}").json()
    assert node["label"] == "TS end −" and node["role"] == "minimum" and node["status"] == "planned"
    assert (node["charge"], node["multiplicity"]) == (0, 1)
    assert node["pos_x"] == ts["pos_x"] - follow_mode.NODE_DX
    assert node["canvas_id"] == ts["canvas_id"]
    assert open_client.get(f"/api/nodes/{made['node_id']}/calculations").json() == []
    history = open_client.get("/api/history").json()
    assert any(h["record_id"] == made["node_id"] and h["action"] == "create" for h in history)

    matched = ends(open_client, done["id"])
    assert [m["node_id"] for m in matched["ends"]["-"]["matches"]] == [made["node_id"]]
    assert matched["ends"]["-"]["node_made"] == made["node_id"]
    assert matched["outcome"] == "one_end"
    again = open_client.post(f"/api/local/jobs/{done['id']}/ends/-/node")
    assert again.status_code == 422 and "“TS end −”" in again.json()["detail"]
    bad = open_client.post(f"/api/local/jobs/{done['id']}/ends/up/node")
    assert bad.status_code == 422

    a_id = make_node(open_client, "A", shifted(0.5))
    matched = ends(open_client, done["id"])
    assert matched["outcome"] == "links"
    assert matched["verdict"] == "Followed the mode of “TS”: links “A” and “TS end −”"
    assert matched["links"][0]["node_id"] == a_id

    anyway = open_client.post(
        f"/api/local/jobs/{done['id']}/ends/-/node", json={"label": "B2", "anyway": True}
    )
    assert anyway.status_code == 201 and anyway.json()["label"] == "B2"
    matched = ends(open_client, done["id"])  # A191: the nearest of one molecule's two
    assert matched["outcome"] == "links" and matched["confidence"] == "medium"
    assert matched["ends"]["-"]["settled"]["as"] == "nearest"
    assert "matches 2 conformers of one molecule" in matched["flags"][0]
    assert "and 1 other conformer of the same molecule" in matched["verdict"]


@pytest.mark.usefixtures("stub_tools")
def test_a_follow_is_refused_plainly(open_client, monkeypatch):
    """T-LOC-53: refused for a calculation without an imaginary frequency (a minimum's), for
    a calculation not on the node, for a node without any imaginary frequency, for an
    uninstalled method (MACE-OMOL-0 here), for a method that is not offered, for a folder that
    is not one and for a frequency job without stored normal modes; no job starts."""
    ts_id, frequency = import_ts(open_client)
    minimum = commit(
        open_client,
        upload(open_client, "min.log", ts_output((150.0, 300.0, 600.0, 900.0, 1200.0, 3000.0))),
        duplicate_action="new",
    )["node_id"]
    minimum_calcs = open_client.get(f"/api/nodes/{minimum}/calculations").json()

    def start(node_id, **body):
        return open_client.post("/api/local/follow-mode", json={"node_id": node_id, **body})

    refused = start(minimum, method="stub-saddle", calculation_id=minimum_calcs[0]["id"])
    assert refused.status_code == 422 and "no imaginary frequency" in refused.json()["detail"]
    refused = start(minimum, method="stub-saddle")
    assert "no frequency calculation with an imaginary frequency" in refused.json()["detail"]
    refused = start(minimum, method="stub-saddle", calculation_id=frequency["id"])
    assert refused.status_code == 422 and "has no calculation" in refused.json()["detail"]
    monkeypatch.delenv("CHEMBOOK3D_MACE_PYTHON", raising=False)
    refused = start(ts_id)  # mace-omol, the default
    assert refused.status_code == 422 and "MACE-OMOL-0 is not installed" in refused.json()["detail"]
    refused = start(ts_id, method="b97-3c")
    assert refused.status_code == 422 and "mace-omol or gfn2" in refused.json()["detail"]
    refused = start(ts_id, method="stub-saddle", folder="not/a/folder")
    assert refused.status_code == 422 and "not a folder" in refused.json()["detail"]
    with open_client.app.state.investigation.sessions() as session:
        session.get(Calculation, frequency["id"]).result.normal_modes = None
        session.commit()
    refused = start(ts_id, method="stub-saddle")
    assert refused.status_code == 422 and "no normal modes stored" in refused.json()["detail"]
    assert not open_client.get("/api/local/jobs").json()


def test_the_ends_are_displaced_both_ways_along_the_mode():
    """T-LOC-51: + then −, the atom moving most moved by the shift, each relaxed once and kept
    as it ended (one stopping at the step limit too)."""
    start = np.zeros((2, 3))
    follow_spec = {"mode": [[0.0, 0.0, 2.0], [0.0, 0.0, -1.0]], "shift": 0.1, "steps": 40}
    seen = []

    def optimize_from(x):
        seen.append(np.asarray(x).copy())
        stopped = x[0][2] < 0
        return {
            "converged": not stopped,
            "lists": [[x.tolist()] * 3, [1.0, 0.9, 0.8], [[], [], []], [1, 1, 1]],
            "coords": x,
            "energy": 0.8,
        }

    class Progress:
        def update(self, **_):
            pass

    kept = []
    found = local_worker.follow_mode(
        follow_spec, start, optimize_from, Progress(), lambda n, e: kept.append(n)
    )
    assert np.allclose(seen[0], [[0, 0, 0.1], [0, 0, -0.05]])
    assert np.allclose(seen[1], [[0, 0, -0.1], [0, 0, 0.05]])
    assert found["ends"]["+"]["converged"] and not found["ends"]["-"]["converged"]
    assert found["ends"]["-"]["cycles"] == 3 and kept == ["+", "-"]


def test_the_verdict_flags_both_ends_on_one_structure():
    """T-LOC-54: both ends matching the same node is flagged, not called a link."""
    a = {"node_id": "n1", "label": "A", "rmsd": 0.2}
    end = {"converged": True, "cycles": 12, "matches": [a]}
    found = follow_mode._verdict({"+": end, "-": dict(end)}, "TS")
    assert found["outcome"] == "same_structure" and found["confidence"] is None
    assert found["flags"][0] == "Both ends match the same structure, “A”."
    stopped = {"converged": False, "cycles": 40, "matches": []}
    found = follow_mode._verdict({"+": end, "-": stopped}, "TS")
    assert found["outcome"] == "one_end"
    assert (
        found["verdict"]
        == "Followed the mode of “TS”: + end matches “A” (0.20 Å), − end matches nothing"
    )
    assert any("stopped after 40 steps" in f for f in found["flags"])


def test_the_tools_are_offered_and_named_in_the_guides(tmp_path):
    """T-LOC-54: a write tool to start, a read tool for the ends the panel does not ask
    about, a write tool to make a node; all named in both guides."""
    kinds = {
        "start_local_follow_mode": "write",
        "get_follow_mode_ends": "read",
        "make_node_from_end": "write",
    }
    for name, kind in kinds.items():
        assert BY_NAME[name].kind == kind, name
        for text in (INSTRUCTIONS, NOTEBOOK_GUIDE):
            assert name in text, name
    assert "get_follow_mode_ends" in PANEL_ALLOWED
    assert "D263c" in BY_NAME["start_local_follow_mode"].description

    async def run():
        bridge, page = _app_bridge()
        await page.post("/api/investigations", json={"folder": str(tmp_path / "i"), "name": "T"})
        missing = await bridge.call("get_follow_mode_ends", {"job_id": "2026-01-01-none"})
        assert missing.is_error and "No local job" in missing.content[0].text
        made = (await page.post("/api/nodes", json={"label": "x", "xyz": xyz_of(TS)})).json()
        refused = await bridge.call("make_node_from_end", {"job_id": "x", "end": "+"})
        assert refused.is_error and made["id"]

    anyio.run(run)


@pytest.mark.usefixtures("stub_tools")
def test_a_ts_is_never_an_end_and_an_end_at_the_ts_returned(open_client, tmp_path):
    """T-LOC-55 (A188): at the + end's structure lie a TS node, a folder's TS output and a
    single point on it; none is the + end's match (`ts_matches`, flagged), while the − end
    matches B; the followed TS's own output in the folder is no other TS (A195); each end has
    its side. With both ends set back to the TS's coordinates, they
    returned to it: no matches, outcome "returned"."""
    import json

    from chembook3d import local_jobs

    ts_id, _ = import_ts(open_client)
    other = make_node(open_client, "other TS", shifted(0.5))
    open_client.patch(f"/api/nodes/{other}", json={"role": "transition_state"})
    b_id = make_node(open_client, "B", shifted(-0.5))
    outputs = tmp_path / "outputs"
    outputs.mkdir()
    ts_there = g.output(
        g.step(
            "#P B3LYP/6-31G(d) Freq",
            [shifted(0.5)],
            -77.0,
            after=[*frequency_lines([-400.0, 300.0, 600.0, 900.0, 1200.0, 3000.0], MODES, TS)],
        )
    )
    (outputs / "ts2.log").write_text(ts_there, encoding="utf-8")
    (outputs / "own.log").write_text(ts_output(), encoding="utf-8")  # the followed TS's own
    (outputs / "ts2_sp.log").write_text(
        g.output(g.step("#P PBE0/def2TZVP SP", [shifted(0.5)])), encoding="utf-8"
    )
    done = follow(open_client, ts_id, folder=str(outputs))
    matched = ends(open_client, done["id"])
    plus, minus = matched["ends"]["+"], matched["ends"]["-"]
    assert plus["matches"] == [] and plus["files"] == []
    assert {m.get("node_id") or m.get("path") for m in plus["ts_matches"]} == {
        other,
        "ts2.log",
        "ts2_sp.log",
    }  # A195: never the followed TS's own file
    assert "ts_matches" not in minus
    assert [m["node_id"] for m in minus["matches"]] == [b_id]
    assert (plus["side"], minus["side"]) == ("+", "−")
    assert not plus["returned_to_ts"] and not minus["returned_to_ts"]
    assert matched["outcome"] == "one_end"
    assert any("“other TS”" in f and "never an end's match" in f for f in matched["flags"])

    result = local_jobs.job_dir(folder_of(open_client), done["id"]) / local_jobs.RESULT
    data = json.loads(result.read_text(encoding="utf-8"))
    start = [[x, y, z] for _, _, x, y, z in TS]
    for end in data["follow_mode"]["ends"].values():
        end["coordinates"] = start
    result.write_text(json.dumps(data), encoding="utf-8")
    back = ends(open_client, done["id"])
    assert back["outcome"] == "returned" and back["confidence"] is None
    for end in back["ends"].values():
        assert end["returned_to_ts"] and end["side"] == "none" and end["matches"] == []
    assert sum("returned to the TS" in f for f in back["flags"]) == 2


@pytest.mark.usefixtures("stub_tools")
def test_an_end_on_a_conformer_group_and_a_soft_modes_wider_match(open_client):
    """T-LOC-56 (A191): the + end lies within 0.5 Å of two conformers in one group, so it
    stands for the group and the link stays high; ungrouped they are the nearest "and 1 other
    conformer" (medium)."""
    ts_id, _ = import_ts(open_client)
    a1 = make_node(open_client, "A1", shifted(0.5))
    near = [
        (e, n, x, y + (0.2 if k == 2 else 0.0), z) for k, (e, n, x, y, z) in enumerate(shifted(0.5))
    ]
    a2 = make_node(open_client, "A2", near)
    b_id = make_node(open_client, "B", shifted(-0.5))
    done = follow(open_client, ts_id)
    matched = ends(open_client, done["id"])
    assert matched["ends"]["+"]["settled"]["as"] == "nearest"
    assert matched["outcome"] == "links" and matched["confidence"] == "medium"
    assert "and 1 other conformer of the same molecule" in matched["verdict"]
    grouped = open_client.post(
        "/api/groups/reconnect", json={"member_ids": [a1, a2], "label": "As"}
    )
    assert grouped.status_code == 201, grouped.text
    matched = ends(open_client, done["id"])
    settled = matched["ends"]["+"]["settled"]
    assert settled["as"] == "group" and settled["label"] == "As" and settled["members"] == 2
    assert matched["outcome"] == "links" and matched["confidence"] == "high"
    assert matched["verdict"] == "Followed the mode of “TS”: links the group “As” and “B”"
    assert matched["links"][1]["node_id"] == b_id


@pytest.mark.usefixtures("stub_tools")
def test_a_soft_modes_end_takes_the_nearest_minimum_within_one_angstrom(open_client):
    """T-LOC-56 (A191): a soft mode (50i cm⁻¹) whose + end changes no bond and matches
    nothing within 0.5 Å takes the nearest minimum with its bonds within 1.0 Å (medium)."""
    far = make_node(open_client, "far", shifted(1.9))
    make_node(open_client, "B", shifted(-0.5))
    soft_ts, _ = import_ts(open_client, ts_output((-50.0, 300.0, 600.0, 900.0, 1200.0, 3000.0)))
    soft = ends(open_client, follow(open_client, soft_ts)["id"])
    plus = soft["ends"]["+"]
    assert plus["settled"]["as"] == "soft" and plus["matches"][0]["node_id"] == far
    assert plus["matches"][0]["soft"] and 0.5 <= plus["matches"][0]["rmsd"] < 1.0
    assert soft["outcome"] == "links" and soft["confidence"] == "medium"
    assert any("soft mode" in f and "1.0 Å" in f for f in soft["flags"])


@pytest.mark.usefixtures("stub_tools")
def test_any_end_takes_another_conformer_and_makes_no_node_of_it(open_client):
    """T-LOC-57 (A193): the same minimum, with the mode at 500i cm⁻¹, matches nothing within
    0.5 Å but has the + end's bonds within 1.0 Å: another conformer of that molecule, taken
    (medium, flagged); making a node from that end is refused unless asked anyway; an end with
    no such minimum still becomes one; a node of no role at the TS's structure is a TS."""
    far = make_node(open_client, "far", shifted(1.9))
    ts_id, _ = import_ts(open_client)
    done = follow(open_client, ts_id)
    hard = ends(open_client, done["id"])
    plus = hard["ends"]["+"]
    assert plus["settled"]["as"] == "conformer" and plus["settled"]["node_id"] == far
    assert plus["matches"] == [{**plus["matches"][0], "conformer": True}]
    assert 0.5 <= plus["matches"][0]["rmsd"] < 1.0
    assert hard["outcome"] == "one_end" and hard["confidence"] is None
    assert any("another conformer" in f and "1.0 Å" in f for f in hard["flags"])
    refused = open_client.post(f"/api/local/jobs/{done['id']}/ends/+/node")
    assert refused.status_code == 422 and "another conformer" in refused.json()["detail"]
    assert "“far”" in refused.json()["detail"]
    anyway = open_client.post(f"/api/local/jobs/{done['id']}/ends/+/node", json={"anyway": True})
    assert anyway.status_code == 201, anyway.text
    minus = open_client.post(f"/api/local/jobs/{done['id']}/ends/-/node")
    assert minus.status_code == 201, minus.text  # nothing with the − end's bonds

    # A node of no role at the TS's structure (a single point on it) counts as the TS.
    sp = make_node(open_client, "sp", TS)
    with open_client.app.state.investigation.sessions.begin() as session:
        roles = {
            c.entry["node_id"]: c.entry["role"]
            for c in follow_mode._node_candidates(session, ts_id)
        }
    assert roles[sp] == "transition_state" and roles[far] != "transition_state"

    b_id = make_node(open_client, "B", shifted(-0.5))
    open_client.delete(f"/api/nodes/{anyway.json()['node_id']}")
    open_client.delete(f"/api/nodes/{minus.json()['node_id']}")
    linked = ends(open_client, done["id"])
    assert linked["outcome"] == "links" and linked["confidence"] == "medium"
    assert [m["node_id"] for m in linked["links"]] == [far, b_id]
