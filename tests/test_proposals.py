"""Edges Claude proposes and the user's review of them (D264, FR-PANEL-11): T-PANEL-11 to
T-PANEL-15. Synthetic waters only."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from chembook3d.app import create_app
from chembook3d.mcp_tools import BY_NAME, CONFIRM_DESCRIPTIONS, PANEL_ALLOWED
from tests.conftest import WATER

APP = "http://127.0.0.1:8765"
ORIGIN = {"origin": APP}  # the app's own page
CLAUDE = {"x-chembook-client": "claude"}
ON = {"on": True, "job_limit": 3600, "cloud": False}


@pytest.fixture
def page():
    with TestClient(create_app(), base_url=APP, client=("127.0.0.1", 50000)) as test_client:
        yield test_client


def _ok(response):
    assert response.status_code < 300, response.text
    return response.json() if response.content else None


def _node(client, label, headers=CLAUDE, **fields) -> str:
    made = client.post("/api/nodes", json={"label": label, "xyz": WATER, **fields}, headers=headers)
    assert made.status_code in (200, 201), made.text
    return made.json()["id"]


def _playground(page, tmp_path) -> tuple[Path, Path]:
    """An original, closed, and a playground of it, open."""
    original = tmp_path / "Orig"
    _ok(page.post("/api/investigations", json={"folder": str(original), "name": "Orig"}))
    _ok(page.post("/api/investigations/close"))
    made = _ok(page.post("/api/playgrounds", json={"folder": str(original), "name": "Try"}))
    return original, Path(made["folder"])


def _propose(client, edges, headers=CLAUDE, **more):
    return client.post("/api/mechanism/propose", json={"edges": edges, **more}, headers=headers)


def _edge(client, transition_id):
    return next(t for t in client.get("/api/transitions").json() if t["id"] == transition_id)


def _history(client, record_id):
    return client.get("/api/history", params={"record_id": record_id}).json()


def test_claude_proposes_edges_steps_and_groups_in_a_playground(page, tmp_path):
    """T-PANEL-11: edges made with their confidence and evidence and open; an edge already
    there updated, not made twice; nodes placed in a new step and a new group, then in the same
    ones again; Claude's writes are Claude's; a TS to a TS and a bad confidence refuse the
    whole call."""
    _playground(page, tmp_path)
    r = _node(page, "R", role="minimum")
    ts = _node(page, "TS1", role="transition_state")
    p = _node(page, "P", role="minimum")
    c1, c2 = _node(page, "P conf 1"), _node(page, "P conf 2")
    s = _node(page, "HCl", kind="species")
    mine = _ok(page.post("/api/transitions", json={"source_id": r, "target_id": ts}))

    out = _ok(
        _propose(
            page,
            [
                {"source_id": r, "target_id": ts, "confidence": "high", "evidence": "IRC reverse"},
                {
                    "source_id": ts,
                    "target_id": p,
                    "confidence": "medium",
                    "evidence": "C1–H9 breaks along the mode",
                    "species": [{"species_id": s, "direction": "leaves"}],
                },
            ],
            steps=[{"name": "Oxidative addition", "ids": [r, ts, p]}],
            groups=[{"label": "P conformers", "member_ids": [c1, c2]}],
        )
    )
    assert out["created"] == 1 and out["updated"] == 1
    assert out["edges"][0]["id"] == mine["id"]  # the edge already there, not a second one
    assert [(e["confidence"], e["review"]) for e in out["edges"]] == [
        ("high", "open"),
        ("medium", "open"),
    ]
    assert out["edges"][1]["species"][0]["label"] == "HCl"
    assert out["edges"][1]["evidence"] == "C1–H9 breaks along the mode"
    assert out["open"] == {"high": 1, "medium": 1, "low": 0}
    assert "tidy_canvas" in out["next"]
    step = out["steps"][0]
    assert step["name"] == "Oxidative addition" and step["created"]
    assert {page.get(f"/api/nodes/{n}").json()["step_id"] for n in (r, ts, p)} == {step["id"]}
    group = out["groups"][0]
    assert group["created"] and group["name"] == "P conformers"
    groups = page.get("/api/groups").json()
    assert next(g for g in groups if g["id"] == group["id"])["member_ids"] == [c1, c2]
    assert len(page.get("/api/transitions").json()) == 2

    made = _history(page, out["edges"][1]["id"])
    create = next(e for e in made if e["action"] == "create")
    assert create["source"] == "claude" and create["new_value"]["evidence"].startswith("C1–H9")
    updated = {e["field"]: e for e in _history(page, mine["id"]) if e["action"] == "update"}
    assert updated["review"]["new_value"] == "open" and updated["review"]["source"] == "claude"
    assert updated["evidence"]["new_value"] == "IRC reverse"

    # The same step and group by name: nothing new is made.
    c3 = _node(page, "P conf 3")
    again = _ok(
        _propose(
            page,
            [],
            steps=[{"name": "Oxidative addition", "ids": [group["id"]]}],
            groups=[{"label": "P conformers", "member_ids": [c3]}],
        )
    )
    assert not again["steps"][0]["created"] and not again["groups"][0]["created"]
    assert again["groups"][0]["id"] == group["id"]
    assert len(page.get("/api/steps").json()) == 1
    groups = page.get("/api/groups").json()
    found = next(g for g in groups if g["id"] == group["id"])
    assert found["member_ids"] == [c1, c2, c3] and found["step_id"] == step["id"]

    # Refused, and nothing written: two TSs; a confidence that is not one of the three.
    ts2 = _node(page, "TS2", role="transition_state")
    before = len(page.get("/api/transitions").json())
    both = [
        {"source_id": p, "target_id": ts2, "confidence": "low", "evidence": "names only"},
        {"source_id": ts, "target_id": ts2, "confidence": "low", "evidence": "names only"},
    ]
    refused = _propose(page, both)
    assert refused.status_code == 422 and "both transition states" in refused.text
    bad = _propose(
        page, [{"source_id": p, "target_id": ts2, "confidence": "sure", "evidence": "x"}]
    )
    assert bad.status_code == 422
    empty = _propose(
        page, [{"source_id": p, "target_id": ts2, "confidence": "low", "evidence": " "}]
    )
    assert empty.status_code == 422 and "evidence" in empty.text
    assert len(page.get("/api/transitions").json()) == before

    listed = page.get("/api/proposals").json()
    assert listed["open"] == {"high": 1, "medium": 1, "low": 0} and len(listed["edges"]) == 2


def test_proposing_is_refused_outside_a_playground(page, tmp_path):
    """T-PANEL-12: an ordinary investigation takes no proposals."""
    _ok(page.post("/api/investigations", json={"folder": str(tmp_path / "i"), "name": "I"}))
    a, b = _node(page, "A"), _node(page, "B")
    refused = _propose(
        page, [{"source_id": a, "target_id": b, "confidence": "low", "evidence": "x"}]
    )
    assert refused.status_code == 422 and "playground" in refused.text
    assert page.get("/api/transitions").json() == []


def test_the_user_accepts_fixes_and_rejects_proposals(page, tmp_path):
    """T-PANEL-13: Accept keeps the confidence and evidence and clears the review; Accept all
    high takes only the open high ones; Reject deletes the edge, its delete entry keeping the
    evidence; Fix moves one end and says so in the evidence. Only the app's page may press
    them, never Claude."""
    _playground(page, tmp_path)
    a, b, c, d, e = (_node(page, x) for x in "ABCDE")
    edges = _ok(
        _propose(
            page,
            [
                {"source_id": a, "target_id": b, "confidence": "high", "evidence": "IRC"},
                {"source_id": b, "target_id": c, "confidence": "high", "evidence": "follow"},
                {"source_id": c, "target_id": d, "confidence": "medium", "evidence": "bonds"},
                {"source_id": d, "target_id": e, "confidence": "low", "evidence": "names only"},
            ],
        )
    )["edges"]
    ab, bc, cd, de = (x["id"] for x in edges)

    for path in (f"/api/transitions/{ab}/accept", "/api/transitions/accept-high"):
        assert page.post(path, headers=CLAUDE).status_code == 403
    assert page.post(f"/api/transitions/{de}/reject", headers=CLAUDE).status_code == 403

    accepted = _ok(page.post(f"/api/transitions/{ab}/accept", headers=ORIGIN))
    assert accepted["review"] == "accepted"
    assert (accepted["confidence"], accepted["evidence"]) == ("high", "IRC")
    entry = next(x for x in _history(page, ab) if x["field"] == "review")
    assert (entry["old_value"], entry["new_value"], entry["source"]) == (
        "open",
        "accepted",
        "manual",
    )

    high = _ok(page.post("/api/transitions/accept-high", headers=ORIGIN))
    assert [x["id"] for x in high] == [bc]  # ab is accepted already; cd is medium
    assert _edge(page, cd)["review"] == "open"

    fixed = _ok(
        page.post(
            f"/api/transitions/{cd}/fix", json={"end": "target", "node_id": e}, headers=ORIGIN
        )
    )
    assert fixed["target_id"] == e and fixed["review"] == "open"
    assert fixed["evidence"] == "bonds\nFixed by the user: target “D” → “E”."
    moved = next(x for x in _history(page, cd) if x["field"] == "target_id")
    assert (moved["old_value"], moved["new_value"]) == (d, e)
    taken = page.post(
        f"/api/transitions/{cd}/fix", json={"end": "source", "node_id": d}, headers=ORIGIN
    )
    assert taken.status_code == 422 and "already connected" in taken.text  # D → E exists

    assert page.post(f"/api/transitions/{de}/reject", headers=ORIGIN).status_code == 204
    assert all(x["id"] != de for x in page.get("/api/transitions").json())
    gone = next(x for x in _history(page, de) if x["action"] == "delete")
    assert gone["old_value"]["evidence"] == "names only" and gone["old_value"]["review"] == "open"

    assert page.post(f"/api/transitions/{ab}/reject", headers=ORIGIN).status_code == 422
    plain = _ok(page.post("/api/transitions", json={"source_id": e, "target_id": a}))
    assert plain["review"] is None and plain["confidence"] is None
    assert page.post(f"/api/transitions/{plain['id']}/accept", headers=ORIGIN).status_code == 422
    assert page.get("/api/proposals").json()["open"] == {"high": 0, "medium": 1, "low": 0}


def test_the_merge_review_warns_about_open_proposals(page, tmp_path):
    """T-PANEL-14: the playground's merge review counts the proposals still open per
    confidence and warns, without blocking the merge."""
    original, playground = _playground(page, tmp_path)
    a, b, c, d = (_node(page, x) for x in "ABCD")
    _ok(
        _propose(
            page,
            [
                {"source_id": a, "target_id": b, "confidence": "high", "evidence": "IRC"},
                {"source_id": b, "target_id": c, "confidence": "high", "evidence": "IRC"},
                {"source_id": c, "target_id": d, "confidence": "low", "evidence": "names only"},
            ],
        )
    )
    first = page.get("/api/transitions").json()[0]["id"]
    _ok(page.post(f"/api/transitions/{first}/accept", headers=ORIGIN))
    _ok(page.post("/api/investigations/close"))

    started = _ok(
        page.post("/api/playgrounds/merge/start", json={"folder": str(playground)}, headers=ORIGIN)
    )
    warning = "2 proposed edges are still open: high 1, medium 0, low 1"
    assert started["review"]["warnings"] == [warning]
    ticked = [c["id"] for s in started["review"]["sections"] for c in s["changes"] if c["ticked"]]
    body = {"folder": str(playground), "ticked": ticked}
    preview = _ok(page.post("/api/playgrounds/merge/preview", json=body, headers=ORIGIN))
    assert preview["blockers"] == [] and preview["warnings"] == [warning]
    merged = _ok(page.post("/api/playgrounds/merge", json=body, headers=ORIGIN))
    assert merged["blockers"] == []
    reviews = sorted((t["confidence"], t["review"]) for t in page.get("/api/transitions").json())
    assert reviews == [("high", "accepted"), ("high", "open"), ("low", "open")]


def _ask_merge(client, a, b):
    asked = client.post(
        "/api/confirmations",
        json={"action": "merge_nodes", "params": {"node_id": a, "into_id": b}, "timeout": 5},
        headers=CLAUDE,
    )
    assert asked.status_code == 201, asked.text
    return asked.json()


def test_claude_merges_its_own_nodes_unasked_only_in_a_playground(page, tmp_path):
    """T-PANEL-15: while Claude works on its own in a playground, two nodes it made merge
    without asking; a node the user made or changed still asks; in an ordinary investigation
    every merge asks (D262i, D264c)."""
    _playground(page, tmp_path)
    _ok(page.put("/api/claude/autonomy", json=ON, headers=ORIGIN))
    a, b = _node(page, "A"), _node(page, "B")
    done = _ask_merge(page, a, b)
    assert done["status"] == "done", done
    assert page.get(f"/api/nodes/{a}").status_code == 404

    theirs = _node(page, "Mine", headers={})
    assert _ask_merge(page, theirs, b)["status"] == "pending"
    changed = _node(page, "Changed")
    _ok(page.patch(f"/api/nodes/{changed}", json={"notes": "checked"}))
    assert _ask_merge(page, changed, b)["status"] == "pending"

    _ok(page.post("/api/investigations", json={"folder": str(tmp_path / "plain"), "name": "P"}))
    _ok(page.put("/api/claude/autonomy", json=ON, headers=ORIGIN))
    x, y = _node(page, "X"), _node(page, "Y")
    assert _ask_merge(page, x, y)["status"] == "pending"
    assert page.get(f"/api/nodes/{x}").status_code == 200


def test_the_tools_and_guides_name_the_proposals():
    """T-PANEL-15: propose_mechanism writes, list_proposals reads, the review is not a tool;
    the merge's description says when it is not asked."""
    assert BY_NAME["propose_mechanism"].kind == "write"
    assert "A184" in BY_NAME["propose_mechanism"].description
    assert "tidy_canvas" in BY_NAME["propose_mechanism"].description
    assert BY_NAME["list_proposals"].kind == "read" and "list_proposals" in PANEL_ALLOWED
    assert not any("/accept" in t.path or "/reject" in t.path for t in BY_NAME.values())
    assert "D264c" in CONFIRM_DESCRIPTIONS["merge_nodes"]
    from chembook3d import claude_panel, mcp_server

    assert "propose_mechanism" in claude_panel.PLAYGROUND_GUIDE
    assert "propose_mechanism" in mcp_server.INSTRUCTIONS
