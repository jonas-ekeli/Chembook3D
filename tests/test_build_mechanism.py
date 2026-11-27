"""D263 a, f, g, h and D265 (FR-PANEL-09, FR-PANEL-10, FR-MCP-25): the `/build-mechanism`
playbook in the panel's working folder, and the job limit "Build mechanism from folder…" turns
Work on its own on with (T-PANEL-16, T-PANEL-17)."""

import json

import pytest

from chembook3d import claude_panel, local_jobs
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

APP = "http://127.0.0.1:1"
CLAUDE = {"X-Chembook-Client": "claude"}
FOUR_HOURS = 4 * 3600


def _playbook(work):
    return (work / ".claude" / "commands" / "build-mechanism.md").read_text("utf-8")


def test_the_panel_gets_the_build_mechanism_command(tmp_path, monkeypatch):
    """T-PANEL-16: every start writes `.claude/commands/build-mechanism.md` (Claude Code's
    project command) with the playbook's order of work and rules, over what was there; the
    panel's guide points to it."""
    monkeypatch.setattr(claude_panel, "workspace", lambda folder: tmp_path / "panel")
    work = claude_panel.prepare(tmp_path / "inv", "RuCAAC", APP)
    text = _playbook(work)
    flat = " ".join(text.split())  # the phrases below, whatever the wrapping
    assert text.startswith("---\ndescription:") and "argument-hint:" in text
    assert "$ARGUMENTS" in text  # the folder and options
    # The order of work and its tools, which settle a TS cheapest first.
    order = [
        "survey_folder",
        "import_folder",
        "group_structures",
        "irc_end",
        "find_ts_partners",
        "compare_structures` with `ts`",
        "start_local_follow_mode",
        "get_follow_mode_ends",
        "propose_mechanism",
        "tidy_canvas",
        "create_note",
    ]
    places = [text.index(word) for word in order]
    assert places == sorted(places)
    assert "TSGO" in flat and "SPQZ" in flat and "Trust what the files say" in flat
    assert "merge_nodes" in flat and "D264c" in flat
    assert "Never make, merge or discard a playground yourself" in flat
    # The rules: lineage and connections never from energies, one level, no local energies.
    assert "Lineage never comes from energies (D169, P5)" in flat
    assert "never decide a connection" in flat and "the geometry level included" in flat
    assert "MACE or xTB" in flat and "Invent no structure" in flat
    assert "high from an IRC" in flat and "medium from the bond check alone" in flat
    assert "low from names only" in flat and "A TS still unsettled is flagged" in flat
    # FR-MCP-25/26: the search from the TS before any guess, then the mode-follows in batches,
    # every TS worked through; duplicates attached; Tidy without a page is no stop.
    for line in (
        "never guess the two minima",
        'A "links" (one candidate per side',
        "is medium (A184 bond check)",
        '`all_ts` "unlinked" once for all of them',  # FR-MCP-26, A187
        "Never merge a minimum and a TS",  # A189
        "keeps the target the plan shows (`file:<its id>`)",
        "true minima at the TS's geometry level first",
        "`other_level` is a weaker link",
        "raise a medium link to high",
        "propose what they settle (step 6) before you start any mode-follow",
        "both ends land on one structure or return to the TS (`returned_to_ts`)",
        "Start several mode-follows, then collect their ends",
        "do not stop while a TS is unsettled and a tool remains to try",
        '`duplicate_action` "attach", not a new node, unless it is another conformer',
        "no page is open, say so in the summary and go on",
    ):
        assert line in flat, line
    # What it flags rather than decides.
    for flag in (
        "match nothing, or both match the same minimum",
        "more than one imaginary frequency",
        "did not converge",
        "without a free species",
        "rests on names only",
        "near_cut",
    ):
        assert flag in flat, flag
    # The literature only where D263h says, and none with --no-literature.
    assert "--no-literature" in flat and "D230c" in flat and "read_pdf" in flat
    assert "never raises a confidence on its own" in flat
    assert "numbers are never energies" in flat
    # D265: gaps filled after linking unless --no-gaps, at most 10, MACE guesses only.
    assert text.index("propose_mechanism") < text.index("find_gaps")
    assert "argument-hint: <folder> [--no-literature] [--no-gaps]" in text
    for rule in (
        "## Filling gaps (D265)",
        "Skip this with `--no-gaps`",
        "Fill at most 10 a run",
        "cheapest and most certain first",
        "never a new branch",
        "`start_local_refine` at `mace-omol`",
        "`start_local_ts_guess`",
        "`correct` only when the person asked",
        "consult the literature first (unless `--no-literature`)",
        "from SMILES",
        "Filled by Claude",
        'planned "(MACE guess)" nodes',
        "each gap with what you built for it, the gaps left flagged",
    ):
        assert rule in flat, rule

    # Written over on every start, so an old copy follows the app.
    (work / ".claude" / "commands" / "build-mechanism.md").write_text("old", encoding="utf-8")
    again = claude_panel.prepare(tmp_path / "inv", "RuCAAC", APP)
    assert _playbook(again) == text

    guide = (work / "CLAUDE.md").read_text("utf-8")
    assert "`/build-mechanism <folder>`" in guide and "Build mechanism from folder…" in guide


def test_a_launched_session_has_the_command(local, fake_claude, tmp_path):  # noqa: F811
    """T-PANEL-16: a session the page starts runs where the command is, and what the page
    types reaches Claude as typed."""
    token = _token(local, headers=ORIGIN)
    with local.websocket_connect(f"{WS}/api/claude/terminal?token={token}", headers=ORIGIN) as ws:
        _read_until(ws, "Fake Claude ready")
        work = claude_panel.workspace(tmp_path / "inv")
        assert "survey_folder" in _playbook(work)
        folder = str(tmp_path / "outputs")
        ws.send_text(json.dumps({"type": "input", "data": f"/build-mechanism {folder}"}))
        ws.send_text(json.dumps({"type": "input", "data": "\r"}))
        _read_until(ws, f"Fake Claude got: /build-mechanism {folder}")
        ws.send_text(json.dumps({"type": "input", "data": "exit\r"}))
        _exit_code(ws)


@pytest.mark.usefixtures("stub_tools")
def test_the_build_actions_job_limit_holds(local, fake_claude, tmp_path, monkeypatch):  # noqa: F811
    """T-PANEL-17: "Build mechanism from folder…" makes a playground with Work on its own on at
    the start dialog's limit (4 hours by default, D263f); Claude's local jobs over it are
    refused and those under it start, and the panel's guide names it."""
    local.post("/api/nodes", json={"label": "w", "xyz": WATER})
    made = local.post(
        "/api/playgrounds",
        json={"folder": str(tmp_path / "inv"), "alone": {"job_limit": FOUR_HOURS, "cloud": False}},
        headers=ORIGIN,
    )
    assert made.status_code == 200, made.text
    state = local.get("/api/claude/autonomy").json()
    assert state["on"] and state["job_limit"] == FOUR_HOURS and not state["cloud"]
    assert FOUR_HOURS in state["job_limits"]

    node = local.get("/api/nodes").json()[0]["id"]
    job = {"node_id": node, "method": "stub"}
    estimate = {"seconds": 5 * 3600}
    monkeypatch.setattr(
        local_jobs,
        "estimate",
        lambda *a, **k: {"seconds": estimate["seconds"], "seconds_per_step": 1, "steps": 1},
    )
    refused = local.post("/api/local/jobs", json=job, headers=CLAUDE)
    assert refused.status_code == 422, refused.text
    assert "about 5 h, over the 4 h" in refused.json()["detail"]
    estimate["seconds"] = 3 * 3600
    started = local.post("/api/local/jobs", json=job, headers=CLAUDE)
    assert started.status_code == 200, started.text
    wait_for(local, started.json()["id"], finished)

    # The session the action starts is told the limit, and that it works in a playground.
    session = local.post("/api/claude/sessions", json={"resume": False}, headers=ORIGIN)
    launch = local.app.state.claude_panel.launches[session.json()["token"]]
    assert launch.alone == claude_panel.Alone(limit="4 hours", cloud=False)
    assert launch.playground is not None
