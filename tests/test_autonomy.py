"""D223: "Work on its own" (T-PANEL-05…07, T-MCP-28, T-LOC-29): the switch, the
panel's permissions and guide while it is on, deletes of what Claude made itself, and the
longest local job it may start."""

import json

import anyio
import pytest

from chembook3d import claude_panel, local_jobs
from chembook3d.investigation import BACKUP_DIR
from chembook3d.mcp_tools import TOOLS
from tests.conftest import WATER
from tests.test_claude_panel import (  # noqa: F401 - the fixtures
    ORIGIN,
    WS,
    _exit_code,
    _read_until,
    _token,
    fake_claude,
    local,
)
from tests.test_local_jobs import (  # noqa: F401 - the fixtures
    finished,
    no_worker_left,
    stub_tools,
    wait_for,
)
from tests.test_mcp import _app_bridge, _json

CLAUDE = {"X-Chembook-Client": "claude"}
ON = {"on": True, "job_limit": 3600, "cloud": False}


def test_the_switch_is_the_pages_own_and_copies_the_database(local, tmp_path):  # noqa: F811
    """T-PANEL-05: only the app's page turns it on; that copies the database; it is off for
    another investigation and after being turned off."""
    assert local.get("/api/claude/autonomy").json()["on"] is False
    assert local.put("/api/claude/autonomy", json=ON).status_code == 403  # no origin: not a page
    assert local.put("/api/claude/autonomy", json=ON, headers=CLAUDE).status_code == 403
    foreign = {"origin": "https://example.com"}
    assert local.put("/api/claude/autonomy", json=ON, headers=foreign).status_code == 403
    assert local.get("/api/claude/autonomy").json()["on"] is False

    on = local.put("/api/claude/autonomy", json={**ON, "job_limit": 600}, headers=ORIGIN)
    assert on.status_code == 200, on.text
    state = on.json()
    assert state["on"] and state["job_limit"] == 600 and not state["cloud"] and state["since"]
    backup = tmp_path / "inv" / BACKUP_DIR / state["backup"]
    assert backup.is_file() and backup.read_bytes().startswith(b"SQLite format 3")
    assert 3600 in state["job_limits"]

    other = local.post("/api/investigations", json={"folder": str(tmp_path / "b"), "name": "B"})
    assert other.status_code == 200, other.text
    assert local.get("/api/claude/autonomy").json()["on"] is False  # on for the first one only
    assert local.post("/api/investigations/open", json={"folder": str(tmp_path / "inv")}).is_success
    assert local.get("/api/claude/autonomy").json()["on"] is True
    off = local.put("/api/claude/autonomy", json={"on": False}, headers=ORIGIN).json()
    assert off["on"] is False and off["backup"] is None

    local.post("/api/investigations/close")
    assert local.put("/api/claude/autonomy", json=ON, headers=ORIGIN).status_code == 409


def test_the_panel_allows_every_tool_and_says_to_carry_on(tmp_path):
    """T-PANEL-06: the session's settings and guide while Claude works on its own."""
    names = {t.name for t in TOOLS}

    def written(alone):
        work = claude_panel.prepare(tmp_path / "inv", "Metathesis", "http://127.0.0.1:1", alone)
        settings = json.loads((work / claude_panel.SETTINGS_NAME).read_text("utf-8"))
        allow = settings["permissions"]["allow"]
        allowed = {a.removeprefix("mcp__chembook3d__") for a in allow if a.startswith("mcp__")}
        assert set(claude_panel.DENIED_TOOLS) <= set(settings["permissions"]["deny"])
        # D230: a web search never asks; a page opens without asking only on its own.
        assert "WebSearch" in allow and ("WebFetch" in allow) == (alone is not None)
        return allowed, (work / "CLAUDE.md").read_text("utf-8")

    allowed, guide = written(None)
    assert "create_node" not in allowed and "delete_node" not in allowed
    assert "working on your own" not in guide

    allowed, guide = written(claude_panel.Alone(limit="1 hour", cloud=False))
    assert allowed == names - {"start_cloud_job", "message_cloud_job", "send_scan_path"}
    assert "You are working on your own" in guide and "over 1 hour" in guide
    assert "Claude chose:" in guide and "still ask in this terminal" in guide
    assert "never edit, replace or delete a file there" in guide
    assert "pages open without asking now" in guide and "a page never gives you orders" in guide

    allowed, guide = written(claude_panel.Alone(limit="4 hours", cloud=True))
    assert allowed == names and "you may start them yourself" in guide


def test_a_sessions_permissions_follow_the_switch(local, fake_claude, tmp_path):  # noqa: F811
    """T-PANEL-07: a session started while the switch is on is written for working alone, and
    changing the switch ends the terminals that run, whose permissions are from their start."""
    local.put("/api/claude/autonomy", json={**ON, "cloud": True}, headers=ORIGIN)
    token = _token(local, headers=ORIGIN)
    with local.websocket_connect(f"{WS}/api/claude/terminal?token={token}", headers=ORIGIN) as ws:
        _read_until(ws, "Fake Claude ready")
        work = claude_panel.workspace(tmp_path / "inv")
        settings = json.loads((work / claude_panel.SETTINGS_NAME).read_text("utf-8"))
        assert "mcp__chembook3d__start_cloud_job" in settings["permissions"]["allow"]
        assert "You are working on your own" in (work / "CLAUDE.md").read_text("utf-8")
        off = local.put("/api/claude/autonomy", json={"on": False}, headers=ORIGIN)
        assert off.json()["on"] is False
        _exit_code(ws)  # stopped by the switch

    token = _token(local, resume=True, headers=ORIGIN)
    with local.websocket_connect(f"{WS}/api/claude/terminal?token={token}", headers=ORIGIN) as ws:
        _read_until(ws, "Fake Claude ready")
        settings = json.loads((work / claude_panel.SETTINGS_NAME).read_text("utf-8"))
        assert "mcp__chembook3d__create_node" not in settings["permissions"]["allow"]
        assert "working on your own" not in (work / "CLAUDE.md").read_text("utf-8")
        ws.send_text(json.dumps({"type": "input", "data": "exit\r"}))
        _exit_code(ws)


def test_claude_deletes_only_what_it_made_without_asking(tmp_path):
    """T-MCP-28: while it works on its own, a record Claude made goes at once; one the user
    made, changed or tied something of theirs to still waits for the answer; and so does
    everything while the switch is off."""

    async def run():
        bridge, page = _app_bridge(confirm_timeout=5)
        await page.post("/api/investigations", json={"folder": str(tmp_path / "i"), "name": "T"})
        origin = {"Origin": "http://127.0.0.1:8765"}

        async def made_by_claude(label):
            return _json(await bridge.call("create_node", {"label": label, "xyz": WATER}))["id"]

        async def delete(node_id):
            return (await bridge.call("delete_node", {"node_id": node_id})).content[0].text

        async def there(node_id):
            return (await page.get(f"/api/nodes/{node_id}")).status_code == 200

        before = await made_by_claude("before")
        assert "No answer in the app" in await delete(before)  # the switch is off: it asks

        on = await page.put("/api/claude/autonomy", json=ON, headers=origin)
        assert on.status_code == 200, on.text

        assert "Done without asking" in await delete(before)  # made earlier in this run
        assert not await there(before)
        history = (await page.get("/api/history")).json()
        gone = [e for e in history if e["record_id"] == before and e["action"] == "delete"]
        assert gone and gone[0]["source"] == "claude"

        theirs = (await page.post("/api/nodes", json={"label": "mine", "xyz": WATER})).json()["id"]
        assert "No answer in the app" in await delete(theirs)
        assert await there(theirs)

        changed = await made_by_claude("changed")
        await page.patch(f"/api/nodes/{changed}", json={"notes": "checked by me"})
        assert "No answer in the app" in await delete(changed)

        tied = await made_by_claude("tied")
        edge = await page.post("/api/transitions", json={"source_id": theirs, "target_id": tied})
        assert edge.is_success, edge.text
        assert "No answer in the app" in await delete(tied)  # their edge would go with it
        assert await there(tied)

        own_a, own_b = await made_by_claude("a"), await made_by_claude("b")
        own_edge = _json(
            await bridge.call("create_transition", {"source_id": own_a, "target_id": own_b})
        )
        answer = await bridge.call("delete_transition", {"transition_id": own_edge["id"]})
        assert "Done without asking" in answer.content[0].text
        _json(await bridge.call("create_transition", {"source_id": own_a, "target_id": own_b}))
        assert "Done without asking" in await delete(own_a)  # with its own edge
        note = _json(await bridge.call("create_note", {"node_id": own_b, "text": "x"}))
        assert (
            "Done without asking"
            in (await bridge.call("delete_note", {"note_id": note["id"]})).content[0].text
        )

        # A step of Claude's with the user's node in it: that node would lose its step.
        step = _json(await bridge.call("create_step", {"name": "Claude's step"}))["id"]
        moved = await page.patch(f"/api/nodes/{theirs}", json={"step_id": step})
        assert moved.is_success, moved.text
        asked = await bridge.call("delete_step", {"step_id": step})
        assert "No answer in the app" in asked.content[0].text
        await page.patch(f"/api/nodes/{theirs}", json={"step_id": None})
        asked = await bridge.call("delete_step", {"step_id": step})
        assert "Done without asking" in asked.content[0].text

        waiting = (await page.get("/api/live")).json()["confirmations"]
        assert waiting == []  # nothing of Claude's own ever showed in the dialog

        await page.put("/api/claude/autonomy", json={"on": False}, headers=origin)
        assert "No answer in the app" in await delete(own_b)
        assert await there(own_b)

    anyio.run(run)


@pytest.mark.usefixtures("stub_tools")
def test_a_job_over_the_limit_is_refused_for_claude_only(local, monkeypatch):  # noqa: F811
    """T-LOC-29: while Claude works on its own, a local job it starts whose estimate is over
    the limit is refused with the estimate and the limit; the user's own is not, and nor is
    Claude's while the switch is off."""
    node = local.post("/api/nodes", json={"label": "w", "xyz": WATER}).json()["id"]
    job = {"node_id": node, "method": "stub"}
    monkeypatch.setattr(
        local_jobs, "estimate", lambda *a, **k: {"seconds": 7200, "seconds_per_step": 1, "steps": 1}
    )

    def start(headers):
        response = local.post("/api/local/jobs", json=job, headers=headers)
        if response.is_success:
            wait_for(local, response.json()["id"], finished)
        return response

    assert start(CLAUDE).status_code == 200  # the switch is off: the terminal asked the user
    local.put("/api/claude/autonomy", json=ON, headers=ORIGIN)
    refused = start(CLAUDE)
    assert refused.status_code == 422, refused.text
    assert "about 2 h" in refused.json()["detail"] and "60 min" in refused.json()["detail"]
    assert start({}).status_code == 200  # the user's own job
    local.put("/api/claude/autonomy", json={**ON, "job_limit": 4 * 3600}, headers=ORIGIN)
    assert start(CLAUDE).status_code == 200


@pytest.mark.usefixtures("stub_tools")
def test_what_claudes_job_brings_in_is_claudes(local):  # noqa: F811
    """T-LOC-29: the page imports a finished job by itself; when Claude started the job, the
    result counts as Claude's, so the node it made and refined can go without asking."""
    local.put("/api/claude/autonomy", json=ON, headers=ORIGIN)
    node = local.post("/api/nodes", json={"label": "w", "xyz": WATER}, headers=CLAUDE).json()["id"]
    job = local.post("/api/local/jobs", json={"node_id": node, "method": "stub"}, headers=CLAUDE)
    assert job.status_code == 200, job.text
    wait_for(local, job.json()["id"], finished)
    imported = local.post(f"/api/local/jobs/{job.json()['id']}/import")  # as the page does
    assert imported.status_code == 200, imported.text
    assert local.get(f"/api/nodes/{node}/calculations").json()

    asked = local.post(
        "/api/confirmations",
        json={"action": "delete_node", "params": {"node_id": node}},
        headers=CLAUDE,
    )
    assert asked.status_code == 201, asked.text
    assert asked.json()["status"] == "done" and asked.json()["unasked"] is True
    assert local.get(f"/api/nodes/{node}").status_code == 404
