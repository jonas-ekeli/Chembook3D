"""D224: Claude reads and uses the steps of calculations (T-MCP-29, T-LOC-30): the lean steps
view, one structure as XYZ, "Use this structure", trimming a scan path, an IRC end point as a
node, and the steps of a local job while it runs."""

import anyio
import pytest

from chembook3d import xyz
from chembook3d.claude_panel import NOTEBOOK_GUIDE
from chembook3d.mcp_server import INSTRUCTIONS
from chembook3d.mcp_tools import BY_NAME, READ_ONLY_TOOLS, input_schema
from tests.conftest import WATER
from tests.test_import import calculations, node
from tests.test_irc import add, g, import_ts
from tests.test_local_jobs import (  # noqa: F401 - the fixtures
    finished,
    no_worker_left,
    stub_tools,
    wait_for,
)
from tests.test_mcp import _app_bridge, _json
from tests.test_scan_path_trim import scan_path
from tests.test_step_movie import import_file, steps
from tests.test_xtb_scan_path import TOP, path_text

CLAUDE = {"X-Chembook-Client": "claude"}


def test_the_step_tools_and_what_they_take():
    """T-MCP-29: reading is unasked in the panel, changing asks in its terminal; the
    coordinates are left out unless asked for."""
    reads = {
        "get_calculation_steps",
        "get_calculation_step",
        "preview_scan_trim",
        "get_local_job_steps",
        "get_local_job_step",
    }
    assert reads <= READ_ONLY_TOOLS
    for name in ("use_calculation_step", "trim_scan", "make_irc_end_node"):
        assert BY_NAME[name].kind == "write"
    listed = input_schema(BY_NAME["get_calculation_steps"])["properties"]
    assert listed["geometry"]["default"] is False
    one = input_schema(BY_NAME["get_calculation_step"])
    assert set(one["required"]) == {"calculation_id", "frame"}
    trim = input_schema(BY_NAME["trim_scan"])["properties"]
    assert {"remove", "restore"} <= set(trim) and trim["geometry"]["default"] is False
    job = input_schema(BY_NAME["get_local_job_step"])
    assert set(job["required"]) == {"job_id", "frame"} and "step" in job["properties"]
    end = input_schema(BY_NAME["make_irc_end_node"])
    assert set(end["required"]) == {"node_id", "direction"}
    for text in (INSTRUCTIONS, NOTEBOOK_GUIDE):
        for name in (*reads, "use_calculation_step", "trim_scan", "make_irc_end_node"):
            assert name in text or name == "get_local_job_step", name


def test_steps_without_coordinates_and_one_structure(open_client):
    """T-MCP-29: the steps with `geometry=false` keep everything but the coordinates; one
    frame comes as XYZ text, the last as -1."""
    _, calc = scan_path(open_client)
    full = steps(open_client, calc["id"])
    lean = open_client.get(f"/api/calculations/{calc['id']}/steps", params={"geometry": False})
    assert lean.status_code == 200, lean.text
    lean = lean.json()
    assert all(f["geometry"] == [] for f in lean["frames"])
    assert [f["energy"] for f in lean["frames"]] == [f["energy"] for f in full["frames"]]
    assert lean["trim"] == full["trim"] and lean["in_place"] is True

    top = open_client.get(f"/api/calculations/{calc['id']}/steps/{TOP}").json()
    assert top["frame"] == TOP and top["frames"] == 13 and top["point"] == TOP + 1
    assert top["energy"] == full["frames"][TOP]["energy"]
    atoms = xyz.parse_xyz(top["xyz"])
    rows = full["frames"][TOP]["geometry"]
    assert [a.element for a in atoms] == [r[0] for r in rows]
    assert max(abs(a.x - r[1]) for a, r in zip(atoms, rows, strict=True)) < 1e-5
    last = open_client.get(f"/api/calculations/{calc['id']}/steps/-1").json()
    assert last["frame"] == 12
    assert open_client.get(f"/api/calculations/{calc['id']}/steps/13").status_code == 422
    assert open_client.get("/api/calculations/nope/steps/0").status_code == 404

    trimmed = open_client.post(
        f"/api/calculations/{calc['id']}/steps/trim",
        params={"geometry": False},
        json={"remove": [TOP + 1]},
    ).json()
    assert trimmed["moved"] == TOP + 1
    assert all(f["geometry"] == [] for f in trimmed["steps"]["frames"])


def test_claude_reads_trims_and_uses_a_scan_path(tmp_path):
    """T-MCP-29: through the MCP server: the steps come without coordinates, a trim is
    previewed and made, and a frame is used in place on the scan path's node."""

    async def run():
        bridge, page = _app_bridge()
        await page.post("/api/investigations", json={"folder": str(tmp_path / "i"), "name": "T"})
        plan = await page.post(
            "/api/imports", params={"filename": "path.xyz"}, content=path_text().encode()
        )
        made = await page.post(f"/api/imports/{plan.json()['token']}/commit", json={})
        node_id = made.json()["node_id"]
        calc = _json(await bridge.call("list_calculations", {"node_id": node_id}))[0]["id"]

        listed = _json(await bridge.call("get_calculation_steps", {"calculation_id": calc}))
        assert len(listed["frames"]) == 13 and listed["frames"][0]["geometry"] == []
        assert listed["trim"]["top"] == TOP and listed["in_place"] is True
        one = _json(await bridge.call("get_calculation_step", {"calculation_id": calc, "frame": 0}))
        assert one["xyz"].splitlines()[0].strip() == str(len(xyz.parse_xyz(one["xyz"])))

        ask = {"calculation_id": calc, "remove": [TOP + 1]}
        preview = _json(await bridge.call("preview_scan_trim", ask))
        assert preview["removed"] == [TOP + 1] and preview["top"] == TOP + 1
        same = _json(await bridge.call("get_calculation_steps", {"calculation_id": calc}))
        assert same["trim"]["removed"] == []  # the preview changed nothing
        done = _json(await bridge.call("trim_scan", ask))
        assert done["moved"] == TOP + 1 and done["steps"]["frames"][0]["geometry"] == []
        back = _json(await bridge.call("trim_scan", {"calculation_id": calc, "restore": [TOP + 1]}))
        assert back["steps"]["trim"]["removed"] == []

        used = _json(
            await bridge.call("use_calculation_step", {"calculation_id": calc, "frame": 2})
        )
        assert used["derived"] is False and used["node"]["id"] == node_id
        history = (await page.get("/api/history")).json()
        mine = [e for e in history if e["record_id"] == node_id and e["field"] == "geometry"]
        assert mine and mine[0]["source"] == "claude"

    anyio.run(run)


def test_using_a_step_of_an_optimization_makes_a_derived_node(open_client):
    """T-MCP-29: a node with calculations keeps its geometry (ID-4), so the frame goes onto a
    new node."""
    calc = import_file(open_client, "orca", "dvb_gopt.out")
    lean = open_client.get(f"/api/calculations/{calc['id']}/steps", params={"geometry": False})
    assert lean.json()["in_place"] is False and len(lean.json()["frames"]) > 1
    used = open_client.post(f"/api/calculations/{calc['id']}/steps/0/use", headers=CLAUDE).json()
    assert used["derived"] is True and used["node"]["id"] != calc["node_id"]
    assert calculations(open_client, used["node"]["id"]) == []


def test_claude_makes_an_irc_end_point_a_node(open_client):
    """T-MCP-29: the tool's route as Claude calls it; the new node is Claude's in the history."""
    ts = import_ts(open_client)
    add(open_client, ts, "reverse.out", g.irc(("reverse",), points=3))
    add(open_client, ts, "end.out", g.end_point_opt_freq("reverse", 3, -52.45), "end", "reverse")
    spec = BY_NAME["make_irc_end_node"]
    path = spec.path.format(node_id=ts, direction="reverse")
    made = open_client.request(spec.method, path, headers=CLAUDE)
    assert made.status_code == 200, made.text
    assert node(open_client, made.json()["node_id"])["label"] == "TS reverse end"
    refused = open_client.request(
        spec.method, spec.path.format(node_id=ts, direction="forward"), headers=CLAUDE
    )
    assert refused.status_code == 422 and "no optimization" in refused.json()["detail"]


@pytest.mark.usefixtures("stub_tools")
def test_the_steps_of_a_local_job_while_it_runs(open_client, monkeypatch):
    """T-LOC-30: a running job's structures so far with energies and criteria, without
    coordinates; the latest as XYZ; and the same once it has finished."""
    monkeypatch.setenv("CHEMBOOK3D_LOCAL_STUB_DELAY", "0.5")
    water = open_client.post("/api/nodes", json={"label": "water", "xyz": WATER}).json()["id"]
    job = open_client.post("/api/local/jobs", json={"node_id": water, "method": "stub"}).json()
    empty = open_client.get(f"/api/local/jobs/{job['id']}/steps")
    assert empty.status_code == 200, empty.text  # nothing written yet is not an error
    wait_for(open_client, job["id"], lambda j: j["progress"]["cycle"] >= 3 or not j["active"])
    shown = open_client.get(f"/api/local/jobs/{job['id']}/steps", headers=CLAUDE)
    assert shown.status_code == 200, shown.text
    shown = shown.json()
    assert shown["job"]["id"] == job["id"] and shown["program"] == "stub"
    step = shown["steps"][0]
    assert step["job_type"] == "optimization" and len(step["frames"]) >= 3
    assert all("geometry" not in f and f["energy"] is not None for f in step["frames"])
    assert [f["frame"] for f in step["frames"]] == list(range(len(step["frames"])))
    assert [c["name"] for c in step["frames"][1]["criteria"]][:2] == [
        "Energy change",
        "RMS gradient",
    ]
    latest = open_client.get(f"/api/local/jobs/{job['id']}/steps/-1").json()
    assert latest["frame"] >= 2 and len(xyz.parse_xyz(latest["xyz"])) == 3
    assert "water" in latest["xyz"].splitlines()[1]

    wait_for(open_client, job["id"], finished)
    final = open_client.get(f"/api/local/jobs/{job['id']}/steps").json()
    assert final["job"]["status"] == "converged"
    assert final["steps"][0]["optimization_converged"] is True
    assert final["steps"][0]["frames"][-1]["converged"] is True
    assert open_client.get(f"/api/local/jobs/{job['id']}/steps/99").status_code == 422
    assert open_client.get(f"/api/local/jobs/{job['id']}/steps/0?step=7").status_code == 422
    assert open_client.get("/api/local/jobs/nope/steps").status_code == 404
