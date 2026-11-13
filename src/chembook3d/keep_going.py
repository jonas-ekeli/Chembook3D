"""D231: Claude keeps going on its own until the person's request is done.

While "Work on its own" is on, the panel's Claude Code runs `chembook3d claude-stop` as its Stop
hook (`claude_panel.prepare`). Each time Claude is about to end its turn, the hook asks the app
(`POST /api/claude/stop-check`) whether it may:

- While a job Claude started is still running (a local calculation, or a cloud session it
  started), the app waits with it, up to `WAIT`, and then wakes Claude with what finished, or
  with what is still running if nothing did.
- With nothing running, it asks Claude once to check whether part of the request is still open,
  and again only after Claude did more work in the notebook (so it never loops).
- With the switch off, another investigation open, or the app gone, the turn ends as usual.

The wording of what Claude is told is factual, not an order: the person asked for the work, the
app only reminds Claude of it and of the jobs.
"""

from __future__ import annotations

import json
import sys
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

import anyio
import anyio.to_thread

WAIT = 45 * 60.0  # seconds one stop check may wait for a job
HOOK_TIMEOUT = int(WAIT + 10 * 60)  # seconds Claude Code lets the hook run (above WAIT)
LOCAL_EVERY = 2.0  # seconds between looks at the local jobs
CLOUD_EVERY = 60.0  # seconds between looks at cloud jobs (each one fetches from GitHub)
MAX_AGE = 24 * 3600.0  # a job older than this is no longer waited for (it may have hung)
PATH = "/api/claude/stop-check"


@dataclass(frozen=True)
class Job:
    kind: str  # "local" or "cloud"
    id: str
    name: str
    status: str

    def text(self) -> str:
        where = "local job" if self.kind == "local" else "cloud session"
        return f"“{self.name}” ({where}, {self.status.replace('_', ' ')})"


LOCAL_ACTIVE = ("starting", "queued", "running", "cancelling")
CLOUD_ACTIVE = ("starting", "running")


def _age(job: dict[str, Any]) -> float:
    from datetime import UTC, datetime

    stamp = job.get("started") or job.get("created")
    try:
        when = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
    except ValueError:
        return 0.0
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    return (datetime.now(UTC) - when).total_seconds()


def claude_jobs(
    local: list[dict[str, Any]], cloud: list[dict[str, Any]]
) -> dict[tuple[str, str], Job]:
    """The jobs Claude started that are still under way, by (kind, id)."""
    out: dict[tuple[str, str], Job] = {}
    for kind, jobs, active in (("local", local, LOCAL_ACTIVE), ("cloud", cloud, CLOUD_ACTIVE)):
        for job in jobs:
            if job.get("started_by") != "claude" or job.get("status") not in active:
                continue
            if _age(job) > MAX_AGE:
                continue
            name = str(job.get("name") or job["id"])
            out[(kind, job["id"])] = Job(kind, job["id"], name, str(job["status"]))
    return out


def _minutes(seconds: float) -> str:
    minutes = max(1, round(seconds / 60))
    return "1 minute" if minutes == 1 else f"{minutes} minutes"


GOAL_CHECK = (
    "Work on its own is on, and the person is not watching: if this turn ends, the work stops "
    "until they come back. If part of what they asked for is still open and you can do it "
    "without them, go on with it now. If it is all done, or only they can unblock the rest, "
    "end the turn: your last message already says so, so do not repeat it."
)


def finished_reason(done: list[tuple[Job, str]], running: list[Job]) -> str:
    ended = "; ".join(f"“{job.name}” ({status.replace('_', ' ')})" for job, status in done)
    text = (
        "Work on its own is on. While this turn was ending, "
        + ("these jobs you started ended: " if len(done) > 1 else "a job you started ended: ")
        + ended
        + ". Look at the result (get_local_job for a local job, get_cloud_job for a cloud "
        "session) and go on with what is left of the person's request."
    )
    if running:
        text += " Still running: " + "; ".join(job.text() for job in running) + "."
    return text


def waiting_reason(running: list[Job], waited: float) -> str:
    return (
        "Work on its own is on. Still running after this check waited "
        f"{_minutes(waited)} with you: "
        + "; ".join(job.text() for job in running)
        + ". If there is other work in the request that does not need these results, do it now. "
        "Otherwise end the turn with one short line: the app waits again and wakes you when a "
        "job ends."
    )


Jobs = Callable[[], list[dict[str, Any]]]


async def wait_for_jobs(
    local_jobs: Jobs,
    cloud_jobs: Jobs,
    still_on: Callable[[], bool],
    gone: Callable[[], Awaitable[bool]] | None = None,
    wait: float = WAIT,
    every: float = LOCAL_EVERY,
    cloud_every: float = CLOUD_EVERY,
    sleep: Callable[[float], Awaitable[None]] = anyio.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> tuple[list[Job], list[tuple[Job, str]], float]:
    """Wait until a job Claude started ends, the switch goes off or `wait` is over. Returns the
    jobs still running, those that ended with their status, and the time waited. `gone`: the
    request's client went away, so the wait ends. Cloud jobs
    are looked at only every `cloud_every` seconds, as each look fetches from GitHub. The
    lists are read in a worker thread; the wait itself is cancelled with the request."""
    start = clock()
    local = await anyio.to_thread.run_sync(local_jobs)
    cloud = await anyio.to_thread.run_sync(cloud_jobs)
    running = claude_jobs(local, cloud)
    last_cloud = start
    while running and still_on() and clock() - start < wait:
        await sleep(min(every, max(wait - (clock() - start), 0.0)))
        if gone is not None and await gone():
            break  # nobody waits for the answer any more (the terminal closed)
        local = await anyio.to_thread.run_sync(local_jobs)
        if any(kind == "cloud" for kind, _ in running) and clock() - last_cloud >= cloud_every:
            cloud, last_cloud = await anyio.to_thread.run_sync(cloud_jobs), clock()
        now = claude_jobs(local, cloud)
        ended = [key for key in running if key not in now]
        if ended:
            statuses = {
                (kind, job["id"]): str(job.get("status"))
                for kind, jobs in (("local", local), ("cloud", cloud))
                for job in jobs
            }
            done = [(running[key], statuses.get(key, "gone")) for key in ended]
            return list(now.values()), done, clock() - start
        running = now
    return list(running.values()), [], clock() - start


# ---------- the hook itself, run by Claude Code ----------


def hook_main(argv: list[str]) -> None:
    """`chembook3d claude-stop --url <app>`: read the Stop hook's input, ask the app, and block
    the stop when it says so. Any failure lets the turn end, as if there were no hook."""
    import argparse

    import httpx

    parser = argparse.ArgumentParser(prog="chembook3d claude-stop")
    parser.add_argument("--url", required=True)
    args = parser.parse_args(argv)
    try:
        data = json.load(sys.stdin)
    except (ValueError, OSError):
        return
    body = {
        "session_id": str(data.get("session_id") or ""),
        "stop_hook_active": bool(data.get("stop_hook_active")),
    }
    try:
        response = httpx.post(
            args.url.rstrip("/") + PATH,
            json=body,
            headers={"X-Chembook-Client": "claude-stop"},
            timeout=httpx.Timeout(WAIT + 120.0, connect=5.0),
        )
        answer = response.json() if response.is_success else {}
    except (httpx.HTTPError, ValueError):
        return
    if answer.get("decision") == "block" and answer.get("reason"):
        print(json.dumps({"decision": "block", "reason": answer["reason"]}))
