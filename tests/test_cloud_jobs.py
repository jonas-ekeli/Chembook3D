"""Calculations handed to Claude Code cloud sessions (D93, T-CLOUD-*). A local bare repository
stands in for GitHub, `tests/fake_claude_cloud.py` for `claude --cloud`, and a second clone
pushing a branch for the cloud session."""

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from chembook3d import cloud_jobs, cloud_templates
from chembook3d.app import create_app
from chembook3d.investigation import DB_NAME

from .conftest import WATER

FIXTURES = Path(__file__).parent / "fixtures"


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    ).stdout


@pytest.fixture(autouse=True)
def git_config(tmp_path, monkeypatch):
    """Keep the user's own git settings out (as in test_sync.py)."""
    path = tmp_path / "gitconfig"
    path.write_text("", encoding="utf-8")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(path))


@pytest.fixture
def remote(tmp_path) -> Path:
    path = tmp_path / "remote.git"
    path.mkdir()
    git(path, "init", "-q", "--bare")
    git(path, "symbolic-ref", "HEAD", "refs/heads/main")
    return path


@pytest.fixture
def fake_claude(tmp_path, monkeypatch) -> Path:
    """`claude` as an installer leaves it (a .cmd file on Windows); returns its log file."""
    script = Path(__file__).with_name("fake_claude_cloud.py")
    folder = tmp_path / "bin"
    folder.mkdir()
    if sys.platform == "win32":
        path = folder / "claude.cmd"
        path.write_text(f'@"{sys.executable}" "{script}" %*\r\n', encoding="utf-8")
    else:
        path = folder / "claude"
        path.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{script}" "$@"\n', encoding="utf-8")
        path.chmod(0o755)
    log = tmp_path / "fake-claude.log"
    monkeypatch.setenv("CHEMBOOK3D_CLAUDE", str(path))
    monkeypatch.setenv("FAKE_CLAUDE_LOG", str(log))
    return log


@pytest.fixture
def app_client(tmp_path):
    with TestClient(create_app()) as client:
        response = client.post(
            "/api/investigations", json={"folder": str(tmp_path / "inv"), "name": "Metathesis"}
        )
        assert response.status_code == 200, response.text
        yield client


@pytest.fixture
def linked_client(app_client, remote):
    response = app_client.post("/api/sync/link", json={"url": str(remote)})
    assert response.status_code == 200, response.text
    return app_client


def folder_of(client) -> Path:
    return Path(client.get("/api/investigation").json()["folder"])


def new_node(client, label="TS1-2 guess", xyz=WATER, **fields) -> str:
    response = client.post("/api/nodes", json={"label": label, "xyz": xyz, **fields})
    assert response.status_code == 201, response.text
    return response.json()["id"]


def new_job(client, **body) -> dict:
    node = new_node(client, charge=0, multiplicity=1)
    payload = {
        "name": "TS guess opt",
        "instructions": "GFN2-xTB optimisation with ALPB(toluene). Return opt.out and xtbopt.xyz.",
        "nodes": [{"node_id": node}],
        **body,
    }
    response = client.post("/api/jobs", json=payload)
    assert response.status_code == 200, response.text
    return response.json()


def cloud_pushes(remote: Path, tmp_path: Path, job_id: str, result: dict, extra=None) -> None:
    """What the cloud session does: outputs and result.json on a branch of its own."""
    work = tmp_path / "cloud"
    git(tmp_path, "clone", "-q", str(remote), str(work))
    git(work, "checkout", "-q", "-b", "claude/run-job-x1")
    outputs = work / "jobs" / job_id / "outputs" / "opt"
    outputs.mkdir(parents=True)
    shutil.copy(FIXTURES / "xtb" / "dvb_opt.out", outputs / "opt.out")
    (work / "jobs" / job_id / "result.json").write_text(json.dumps(result), encoding="utf-8")
    for relative, text in (extra or {}).items():
        (work / relative).parent.mkdir(parents=True, exist_ok=True)
        (work / relative).write_text(text, encoding="utf-8")
    git(work, "add", "-A")
    git(work, "-c", "user.name=c", "-c", "user.email=c@x", "commit", "-q", "-m", "results")
    git(work, "push", "-q", "origin", "claude/run-job-x1")


DONE = {
    "job": "",
    "status": "done",
    "summary": "Optimised with GFN2-xTB; normal termination.",
    "outputs": [{"file": "outputs/opt/opt.out", "program": "xtb"}],
}


def test_create_writes_the_job_folder(app_client):
    # T-CLOUD-01, FR-CLOUD-01
    job = new_job(app_client, files=[{"name": "xcontrol", "content": "$fix\n atoms: 1\n$end"}])
    assert job["status"] == "draft" and job["state"] == "draft"
    assert job["id"].endswith("-ts-guess-opt")
    folder = Path(job["folder"])
    assert folder.parent == folder_of(app_client) / "jobs"
    text = (folder / "job.md").read_text(encoding="utf-8")
    assert "inputs/ts1-2-guess.xyz" in text and "charge 0, multiplicity 1" in text
    assert "ALPB(toluene)" in text and "inputs/xcontrol" in text
    assert (folder / "inputs" / "ts1-2-guess.xyz").read_text(encoding="utf-8").startswith("3\n")
    assert (folder / "inputs" / "xcontrol").read_text(encoding="utf-8") == "$fix\n atoms: 1\n$end\n"
    again = new_job(app_client)
    assert again["id"] == job["id"] + "-2"
    listed = app_client.get("/api/jobs").json()
    assert [j["id"] for j in listed] == [job["id"], again["id"]]


@pytest.mark.parametrize("name", ["../evil.xyz", "job.md", ".hidden", "a b.xyz", "outputs"])
def test_input_names_are_checked(app_client, name):
    # T-CLOUD-01
    node = new_node(app_client)
    response = app_client.post(
        "/api/jobs",
        json={"name": "x", "instructions": "run", "nodes": [{"node_id": node, "file": name}]},
    )
    assert response.status_code == 422
    assert not (folder_of(app_client) / "jobs").exists() or not list(
        (folder_of(app_client) / "jobs").iterdir()
    )


def test_a_node_without_coordinates_cannot_go_in(app_client):
    response = app_client.post("/api/nodes", json={"label": "planned"})
    node = response.json()["id"]
    response = app_client.post(
        "/api/jobs", json={"name": "x", "instructions": "run", "nodes": [{"node_id": node}]}
    )
    assert response.status_code == 422 and "no coordinates" in response.text


def test_other_web_pages_cannot_create_or_start(app_client):
    # T-CLOUD-06: a browser names its page's origin; Claude's MCP server sends none
    evil = {"Origin": "https://example.com"}
    response = app_client.post("/api/jobs", json={"name": "x", "instructions": "run"}, headers=evil)
    assert response.status_code == 403
    job = new_job(app_client)
    assert app_client.post(f"/api/jobs/{job['id']}/start", headers=evil).status_code == 403
    assert app_client.post(f"/api/jobs/{job['id']}/fetch", headers=evil).status_code == 403
    page = {"Origin": "http://127.0.0.1:8765"}
    assert app_client.post("/api/jobs", json={"name": "y", "instructions": "r"}, headers=page)


def test_start_needs_github(app_client, fake_claude):
    # T-CLOUD-02
    job = new_job(app_client)
    response = app_client.post(f"/api/jobs/{job['id']}/start")
    assert response.status_code == 422 and "not linked" in response.text
    assert not fake_claude.exists()
    assert app_client.get(f"/api/jobs/{job['id']}").json()["status"] == "draft"


def test_start_needs_claude_code(linked_client, monkeypatch, tmp_path):
    monkeypatch.setenv("CHEMBOOK3D_CLAUDE", str(tmp_path / "missing" / "claude"))
    job = new_job(linked_client)
    response = linked_client.post(f"/api/jobs/{job['id']}/start")
    assert response.status_code == 422 and "not installed" in response.text


def test_round_trip_through_a_cloud_session(linked_client, remote, fake_claude, tmp_path):
    # T-CLOUD-03, T-CLOUD-04, FR-CLOUD-02…05
    client = linked_client
    folder = folder_of(client)
    job = new_job(client)
    job_id = job["id"]
    started = client.post(f"/api/jobs/{job_id}/start")
    assert started.status_code == 200, started.text
    out = started.json()
    assert out["status"] == "running"
    assert out["session_id"] == "session_01FakeCloudJob42"
    assert out["session_url"].startswith("https://claude.ai/code/session_01FakeCloudJob42")
    # claude --cloud ran in the investigation folder with only the job in its task
    call = json.loads(fake_claude.read_text(encoding="utf-8").splitlines()[0])
    assert call["args"] == ["--cloud", cloud_jobs.task_text(job_id)]
    assert call["tty"]  # without a terminal, Claude Code creates no cloud session
    assert Path(call["cwd"]).resolve() == folder.resolve()
    # GitHub has the job and the cloud files, in one commit that leaves the database alone
    pushed = git(remote, "show", "--name-only", "--format=", "main").split()
    assert f"jobs/{job_id}/job.md" in pushed and f"jobs/{job_id}/inputs/ts1-2-guess.xyz" in pushed
    assert set(pushed) >= {
        cloud_templates.SETUP_SCRIPT,
        cloud_templates.INSTRUCTIONS,
        cloud_templates.SETTINGS,
    }
    assert DB_NAME not in pushed
    assert all(p.startswith(("jobs/", ".claude/")) for p in pushed)
    assert git(folder, "status", "--porcelain", "--", DB_NAME).strip()  # left for the next sync
    # A second start does not start a second session
    again = client.post(f"/api/jobs/{job_id}/start")
    assert again.status_code == 422 and "already running" in again.text

    assert client.get(f"/api/jobs/{job_id}").json()["status"] == "running"
    cloud_pushes(
        remote,
        tmp_path,
        job_id,
        {**DONE, "job": job_id},
        extra={DB_NAME: "touched", f"jobs/{job_id}/notes.txt": "x"},
    )
    status = client.get(f"/api/jobs/{job_id}").json()
    assert status["status"] == "finished"
    assert status["result_branch"] == "origin/claude/run-job-x1"
    assert status["result"]["summary"].startswith("Optimised")

    fetched = client.post(f"/api/jobs/{job_id}/fetch")
    assert fetched.status_code == 200, fetched.text
    result = fetched.json()
    local = folder / "jobs" / job_id / "outputs" / "opt" / "opt.out"
    assert result["files"] == [str(local)]
    assert local.read_bytes() == (FIXTURES / "xtb" / "dvb_opt.out").read_bytes()
    assert (folder / "jobs" / job_id / "result.json").is_file()
    assert result["changed_outside_job"] == [DB_NAME]  # reported, never copied
    assert result["skipped"] == [f"jobs/{job_id}/notes.txt"]
    assert not (folder / "jobs" / job_id / "notes.txt").exists()
    assert (folder / DB_NAME).read_bytes() != b"touched"
    assert client.get(f"/api/jobs/{job_id}").json()["status"] == "fetched"

    # The output imports like any other xTB output
    staged = client.post("/api/imports/from-path", json={"path": result["files"][0]})
    assert staged.status_code == 200, staged.text
    assert staged.json()["program"] == "xTB"


def test_a_failed_launch_says_why_and_can_be_retried(
    linked_client, remote, fake_claude, monkeypatch
):
    # T-CLOUD-05
    job = new_job(linked_client)
    monkeypatch.setenv("FAKE_CLAUDE_CLOUD", "fail")
    out = linked_client.post(f"/api/jobs/{job['id']}/start").json()
    assert out["status"] == "launch_failed"
    assert "organization UUID" in out["launch_error"] and "\x1b" not in out["launch_error"]
    assert "--cloud" in out["launch_error"]  # how to start it in a terminal instead
    pushed = git(remote, "rev-parse", "main")
    monkeypatch.delenv("FAKE_CLAUDE_CLOUD")
    out = linked_client.post(f"/api/jobs/{job['id']}/start").json()
    assert out["status"] == "running" and out["launch_error"] is None
    assert len(fake_claude.read_text(encoding="utf-8").splitlines()) == 2
    assert git(remote, "rev-parse", "main") == pushed  # the job went to GitHub only once


def test_a_launch_waiting_for_an_answer_is_stopped(linked_client, fake_claude, monkeypatch):
    # T-CLOUD-05: nobody sees its terminal, so a question is never answered
    monkeypatch.setattr(cloud_jobs, "LAUNCH_QUIET", 1.0)
    monkeypatch.setenv("FAKE_CLAUDE_CLOUD", "ask")
    job = new_job(linked_client)
    out = linked_client.post(f"/api/jobs/{job['id']}/start").json()
    assert out["status"] == "launch_failed", out
    assert "waiting for an answer" in out["launch_error"]
    assert "Accessing workspace:" in out["launch_error"]  # what it showed, words kept apart
    assert "run `claude`" in out["launch_error"]  # and how to answer it once
    assert "\x1b" not in out["launch_error"]


def test_a_launch_that_keeps_following_the_session(linked_client, fake_claude, monkeypatch):
    monkeypatch.setenv("FAKE_CLAUDE_CLOUD", "attach")
    job = new_job(linked_client)
    out = linked_client.post(f"/api/jobs/{job['id']}/start").json()
    assert out["status"] == "running" and out["launcher_running"]
    assert out["session_id"] == "session_01FakeCloudJob42"


def test_launch_command_for_a_terminal():
    command = cloud_jobs.launch_command("claude", Path("inv"), "2026-10-05-ts-scan")
    assert "--cloud" in command and cloud_jobs.task_text("2026-10-05-ts-scan") in command


def test_waiting_gives_up_after_the_time_asked(linked_client, fake_claude, monkeypatch):
    monkeypatch.setattr(cloud_jobs, "POLL_EVERY", 0.05)
    job = new_job(linked_client)
    linked_client.post(f"/api/jobs/{job['id']}/start")
    out = linked_client.get(f"/api/jobs/{job['id']}", params={"wait": 0.3}).json()
    assert out["status"] == "running" and out["fetch_error"] is None
    assert linked_client.get(f"/api/jobs/{job['id']}", params={"wait": 301}).status_code == 422


def test_a_newer_version_on_github_stops_the_start(linked_client, remote, fake_claude, tmp_path):
    # T-CLOUD-02: the job is never pushed over changes from another computer
    other = tmp_path / "other"
    git(tmp_path, "clone", "-q", str(remote), str(other))
    (other / "readme.txt").write_text("x", encoding="utf-8")
    git(other, "add", "-A")
    git(other, "-c", "user.name=o", "-c", "user.email=o@x", "commit", "-q", "-m", "other")
    git(other, "push", "-q", "origin", "main")
    job = new_job(linked_client)
    response = linked_client.post(f"/api/jobs/{job['id']}/start")
    assert response.status_code == 422 and "newer version" in response.text
    assert not fake_claude.exists()


def test_cloud_settings_keep_what_is_there():
    # T-CLOUD-07
    existing = json.dumps({"model": "opus", "hooks": {"Stop": [{"hooks": []}]}})
    text = cloud_templates.settings_text(existing)
    settings = json.loads(text)
    assert settings["model"] == "opus" and "Stop" in settings["hooks"]
    (entry,) = settings["hooks"]["SessionStart"]
    assert entry["hooks"][0]["command"] == cloud_templates.HOOK_COMMAND
    assert cloud_templates.settings_text(text) == text  # added once
    assert json.loads(cloud_templates.settings_text("not json"))["hooks"]["SessionStart"]


def test_setup_script_is_pinned_and_only_runs_in_the_cloud(tmp_path):
    # T-CLOUD-07
    text = cloud_templates.SETUP_TEXT
    assert cloud_templates.XTB_SHA256 in text and cloud_templates.CREST_SHA256 in text
    assert "sha256sum -c" in text and 'CLAUDE_CODE_REMOTE:-}" != "true"' in text
    assert "\r" not in text
    if sys.platform != "win32" and shutil.which("bash"):
        script = tmp_path / "setup-tools.sh"
        script.write_text(text, encoding="utf-8", newline="\n")
        subprocess.run(["bash", "-n", str(script)], check=True)
        env = {"PATH": "/usr/bin:/bin", "HOME": str(tmp_path)}  # not a cloud session
        done = subprocess.run(["bash", str(script)], env=env, capture_output=True, text=True)
        assert done.returncode == 0 and done.stdout == ""
        assert not (tmp_path / ".local").exists()


@pytest.mark.parametrize(
    ("path", "allowed"),
    [
        ("jobs/J/outputs/opt/opt.out", True),
        ("jobs/J/result.json", True),
        ("jobs/J/outputs", False),
        ("jobs/J/job.md", False),
        ("jobs/J/inputs/a.xyz", False),
        ("jobs/K/outputs/a.out", False),
        ("investigation.sqlite", False),
        ("jobs/J/outputs/a:b.out", False),
        ("jobs/J/outputs/trailing. ", False),
    ],
)
def test_only_outputs_and_the_result_are_copied(path, allowed):
    assert (cloud_jobs._safe_output("J", path) is not None) is allowed


@pytest.mark.parametrize(
    "printed",
    [
        "Created cloud session: x\nSession ID: session_01AbCdEf12\nView: https://claude.ai/code/session_01AbCdEf12?from=cli",
        '{"ok":true,"session_id":"session_01AbCdEf12","url":"https://claude.ai/code/session_01AbCdEf12"}',
        "\x1b[2mView: \x1b[0mhttps://claude.ai/code/session_01AbCdEf12",
        # Windows' ConPTY moves the cursor instead of starting a new line
        "\x1b[2;1HSession ID: session_01AbCdEf12\x1b[3;1HView: \x1b]8;;"
        "https://claude.ai/code/session_01AbCdEf12\x1b\\https://claude.ai/code/"
        "session_01AbCdEf12\x1b]8;;\x1b\\\x1b[4;1HResume with",
    ],
)
def test_the_session_is_read_from_what_claude_prints(printed):
    session, url = cloud_jobs.find_session(printed)
    assert session == "session_01AbCdEf12"
    assert url.startswith("https://claude.ai/code/session_01AbCdEf12")


def test_the_task_is_safe_on_a_windows_command_line():
    task = cloud_jobs.task_text("2026-10-05-ts-guess-opt")
    assert not set(task) & set('&|<>^%"!()')
    with pytest.raises(cloud_jobs.CloudJobError):
        cloud_jobs.task_text("2026-10-05-x & calc")
