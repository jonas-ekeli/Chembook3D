"""D231: while Claude works on its own, the end of its turn asks the app first (T-PANEL-08,
T-PANEL-09, T-PANEL-10): the app waits while a job Claude started runs and then wakes it; with
nothing running it asks once whether the request is done, and again only after more work."""

import json
import os
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import anyio
import httpx
import pytest

from chembook3d import claude_panel, keep_going
from tests.conftest import WATER
from tests.test_claude_panel import ORIGIN, local  # noqa: F401 - the fixture
from tests.test_local_jobs import (  # noqa: F401 - the fixtures
    finished,
    no_worker_left,
    stub_tools,
    wait_for,
)
from tests.test_mcp import running_app  # noqa: F401 - the fixture

CLAUDE = {"X-Chembook-Client": "claude"}
HOOK = {"X-Chembook-Client": "claude-stop"}
ON = {"on": True, "job_limit": 3600, "cloud": False}
CHECK = "/api/claude/stop-check"


def _job(kind_id: str, status: str, by: str | None = "claude", hours: float = 0.0) -> dict:
    created = (datetime.now(UTC) - timedelta(hours=hours)).isoformat()
    return {"id": kind_id, "name": kind_id.upper(), "status": status, "started_by": by,
            "created": created}  # fmt: skip


def test_only_claudes_jobs_under_way_are_waited_for():
    here = [
        _job("a", "running"),
        _job("b", "queued"),
        _job("c", "converged"),
        _job("d", "running", by=None),  # the person's own
        _job("e", "running", hours=30),  # a day old: it may have hung
    ]
    cloud = [_job("f", "running"), _job("g", "waiting_for_answer"), _job("h", "finished")]
    found = keep_going.claude_jobs(here, cloud)
    assert set(found) == {("local", "a"), ("local", "b"), ("cloud", "f")}
    assert found[("cloud", "f")].text() == "“F” (cloud session, running)"


def test_the_wait_ends_when_a_job_ends_or_the_time_is_up():
    """Cloud jobs are looked at less often than local ones, as each look fetches."""
    now = [0.0]
    looks = {"local": 0, "cloud": 0}

    async def sleep(seconds: float) -> None:
        now[0] += seconds

    def local_jobs() -> list[dict]:
        looks["local"] += 1
        return [_job("a", "running" if now[0] < 10 else "converged")]

    def cloud_jobs() -> list[dict]:
        looks["cloud"] += 1
        return [_job("f", "running")]

    def wait(**kwargs):
        return anyio.run(
            lambda: keep_going.wait_for_jobs(
                local_jobs, cloud_jobs, lambda: True, sleep=sleep, clock=lambda: now[0], **kwargs
            )
        )

    running, done, waited = wait(wait=60, every=2, cloud_every=6)
    assert [(job.id, status) for job, status in done] == [("a", "converged")]
    assert [job.id for job in running] == ["f"] and waited == 10
    assert looks == {"local": 6, "cloud": 2}  # at 0 and 6 s

    now[0], looks["cloud"] = 0.0, 0
    running, done, waited = wait(wait=5, every=2, cloud_every=60)
    assert done == [] and waited == 5 and {job.id for job in running} == {"a", "f"}
    assert looks["cloud"] == 1


def test_the_panel_writes_the_hook_only_while_claude_works_alone(tmp_path):
    """T-PANEL-08: the Stop hook runs the app's own Python in exec form (no shell), with a
    timeout above the longest wait; the guide says to end the turn while a job runs."""
    alone = claude_panel.Alone(limit="60 min", cloud=False)
    for given, hooked in ((None, False), (alone, True)):
        work = claude_panel.prepare(tmp_path / "inv", "T", "http://127.0.0.1:8765", given)
        settings = json.loads((work / claude_panel.SETTINGS_NAME).read_text(encoding="utf-8"))
        assert ("hooks" in settings) == hooked
        guide = (work / "CLAUDE.md").read_text(encoding="utf-8")
        assert ("Waiting on a job" in guide) == hooked
    (stop,) = settings["hooks"]["Stop"]
    (hook,) = stop["hooks"]
    assert hook["type"] == "command" and hook["command"] == sys.executable
    assert hook["args"] == ["-m", "chembook3d.cli", "claude-stop", "--url", "http://127.0.0.1:8765"]
    assert hook["timeout"] > keep_going.WAIT + 60
    assert "45 minutes" in guide


@pytest.mark.usefixtures("stub_tools")
def test_the_app_waits_for_claudes_job_then_wakes_it(local, monkeypatch):  # noqa: F811
    """T-PANEL-09: with the switch on, a stop while Claude's job runs waits until it ends and
    says so; one that waits past the limit says what still runs."""
    monkeypatch.setattr(keep_going, "LOCAL_EVERY", 0.1)
    monkeypatch.setenv("CHEMBOOK3D_LOCAL_STUB_DELAY", "3")
    local.put("/api/claude/autonomy", json=ON, headers=ORIGIN)
    node = local.post("/api/nodes", json={"label": "w", "xyz": WATER}, headers=CLAUDE).json()["id"]

    def start() -> dict:
        made = local.post(
            "/api/local/jobs", json={"node_id": node, "method": "stub"}, headers=CLAUDE
        )
        assert made.status_code == 200, made.text
        return made.json()

    job = start()
    answer = local.post(CHECK, json={"session_id": "s"}, headers=HOOK).json()
    assert answer["decision"] == "block", answer
    assert f"a job you started ended: “{job['id']}”" in answer["reason"]
    assert "get_local_job" in answer["reason"]
    assert local.get(f"/api/local/jobs/{job['id']}").json()["active"] is False

    monkeypatch.setattr(keep_going, "WAIT", 0.5)
    job = start()
    answer = local.post(CHECK, json={"session_id": "s"}, headers=HOOK).json()
    assert answer["decision"] == "block"
    assert answer["reason"].startswith("Work on its own is on. Still running")
    assert f"“{job['id']}” (local job," in answer["reason"]
    wait_for(local, job["id"], finished)


@pytest.mark.usefixtures("stub_tools")
def test_the_persons_own_job_is_not_waited_for(local, monkeypatch):  # noqa: F811
    monkeypatch.setenv("CHEMBOOK3D_LOCAL_STUB_DELAY", "2")
    local.put("/api/claude/autonomy", json=ON, headers=ORIGIN)
    node = local.post("/api/nodes", json={"label": "w", "xyz": WATER}).json()["id"]
    job = local.post("/api/local/jobs", json={"node_id": node, "method": "stub"}).json()
    answer = local.post(CHECK, json={"session_id": "s"}, headers=HOOK).json()
    assert answer == {"decision": "block", "reason": keep_going.GOAL_CHECK}
    assert local.get(f"/api/local/jobs/{job['id']}").json()["active"] is True
    wait_for(local, job["id"], finished)


def test_the_goal_check_comes_once_per_stretch_of_work(local, tmp_path):  # noqa: F811
    """T-PANEL-10: with nothing running, the first stop is asked about; the next one goes
    ahead unless Claude did more in the notebook since; each conversation counts apart. With
    the switch off, or in another investigation, every stop goes ahead."""

    def check(session: str = "s") -> str:
        response = local.post(CHECK, json={"session_id": session}, headers=HOOK)
        assert response.status_code == 200, response.text
        return response.json()["decision"]

    assert check() == "stop"  # the switch is off
    local.put("/api/claude/autonomy", json=ON, headers=ORIGIN)
    assert check() == "block"
    assert check() == "stop"
    assert check("other") == "block"
    local.get("/api/nodes", headers=CLAUDE)  # Claude worked on
    assert check() == "block"
    assert check() == "stop"
    local.get("/api/nodes")  # the page's own request is not Claude's work
    assert check() == "stop"

    local.get("/api/nodes", headers=CLAUDE)
    other = local.post("/api/investigations", json={"folder": str(tmp_path / "b"), "name": "B"})
    assert other.status_code == 200, other.text
    assert check() == "stop"  # on for the first investigation only

    foreign = local.post(CHECK, json={}, headers={**HOOK, "origin": "https://example.com"})
    assert foreign.status_code == 403


def test_the_hook_command_asks_the_running_app(running_app, tmp_path):  # noqa: F811
    """T-PANEL-08: `chembook3d claude-stop` as Claude Code runs it: it blocks the stop with the
    app's reason, and says nothing (the turn ends) when the app says so or cannot be reached."""
    root = Path(__file__).resolve().parents[1]

    def hook(url: str) -> str:
        done = subprocess.run(
            [sys.executable, "-m", "chembook3d.cli", "claude-stop", "--url", url],
            input=json.dumps({"session_id": "s", "stop_hook_active": False}),
            capture_output=True,
            text=True,
            cwd=str(root),
            timeout=60,
            env={**os.environ, "PYTHONPATH": str(root / "src")},
        )
        assert done.returncode == 0, done.stderr
        return done.stdout.strip()

    assert hook(running_app) == ""  # the switch is off
    on = httpx.put(f"{running_app}/api/claude/autonomy", json=ON, headers=ORIGIN)
    assert on.is_success, on.text
    assert json.loads(hook(running_app)) == {"decision": "block", "reason": keep_going.GOAL_CHECK}
    assert hook(running_app) == ""  # asked once already
    assert hook("http://127.0.0.1:9") == ""  # no app there
