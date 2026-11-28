"""A194 (FR-MCP-29, T-MCP-57): two runs of one structure stay two nodes, an association is
proposed on the groups its ends stand for, and an end only at a lower kind of method than the
TS's is weak."""

import pytest

from tests import gaussian_text as g
from tests.test_direct_links import CORE, ETHENE, complex_rows, links
from tests.test_direct_links import node as minimum
from tests.test_import import upload
from tests.test_local_follow_mode import (  # noqa: F401 - the fixtures
    ends,
    finished,
    folder_of,
    follow,
    import_ts,
    make_node,
    no_worker_left,
    shifted,
    stub_tools,
    wait_for,
)
from tests.test_node_geometries import onto
from tests.test_node_merge import water
from tests.test_proposals import CLAUDE, ON, ORIGIN, _ask_merge, _ok, _playground, page  # noqa: F401


def _members(found: dict) -> list[dict]:
    return [c for f in found["formulas"] for m in f["molecules"] for c in m["conformers"]]


def test_two_runs_of_one_structure_are_never_merged_unasked(page, tmp_path):  # noqa: F811
    """T-MCP-57: two nodes Claude made, each with its own optimization at one level ending on
    one structure, are one conformer with `runs` in group_structures; the merge preview names
    the level in `runs`; Claude's own merge in a playground is refused, naming the runs, and
    goes with anyway."""
    _playground(page, tmp_path)
    _ok(page.put("/api/claude/autonomy", json=ON, headers=ORIGIN))
    page.headers.update(CLAUDE)
    try:
        a = water(page, "A", energy=-76.40891)
        b = water(page, "B")
    finally:
        del page.headers["x-chembook-client"]

    found = _ok(page.post("/api/structures/group", json={"all_nodes": True}, headers=CLAUDE))
    (conformer,) = [c for c in _members(found) if len(c["members"]) > 1]
    assert {m["node_id"] for m in conformer["members"]} == {a, b}
    assert conformer["runs"] == sorted(m["index"] for m in conformer["members"])
    assert "A194" in found["next"] and "never merged" in found["next"]

    preview = _ok(page.post(f"/api/nodes/{a}/merge-preview", json={"into_id": b}))
    assert preview["runs"] == ["Gaussian B3LYP/6-31G(D)"]
    assert any("two runs" in change for change in preview["changes"])

    refused = page.post(
        "/api/confirmations",
        json={"action": "merge_nodes", "params": {"node_id": a, "into_id": b}, "timeout": 5},
        headers=CLAUDE,
    )
    assert refused.status_code == 422, refused.text
    assert "two runs" in refused.json()["detail"] and "A194" in refused.json()["detail"]
    asked = page.post(
        "/api/confirmations",
        json={
            "action": "merge_nodes",
            "params": {"node_id": a, "into_id": b, "anyway": True},
            "timeout": 5,
        },
        headers=CLAUDE,
    ).json()
    assert asked["status"] == "done", asked
    assert page.get(f"/api/nodes/{a}").status_code == 404


def test_an_association_is_proposed_on_its_groups(open_client):
    """T-MCP-57: an association whose ends are grouped names each group's id, and its `edge`
    joins the two groups with the species joining, ready for propose_mechanism."""
    a = minimum(open_client, "A", CORE)
    a2 = minimum(open_client, "A2", [(e, x * 1.05, y, z) for e, x, y, z in CORE])
    ethene = minimum(open_client, "ethene", ETHENE)
    open_client.put(f"/api/nodes/{ethene}/kind", json={"kind": "species"})
    b = minimum(open_client, "B", complex_rows(False))
    grouped = open_client.post("/api/groups/reconnect", json={"member_ids": [a, a2], "label": "As"})
    assert grouped.status_code == 201, grouped.text
    group = grouped.json()["id"]

    (line,) = links(open_client, kind="association")["pairs"]
    assert line["a"]["group"] == "As" and line["a"]["group_id"] == group
    assert line["edge"] == {
        "source_id": group,
        "target_id": b,
        "species": [{"species_id": ethene, "direction": "joins"}],
    }
    assert "group_id" in links(open_client)["next"]


@pytest.mark.usefixtures("stub_tools")
def test_an_end_only_at_a_lower_kind_of_method_is_weak(open_client):
    """T-MCP-57: a mode-follow end matching a node optimized only at PM6 under a DFT TS is
    `other_level`, after any node at the TS's level, and makes the link low, flagged."""
    ts_id, _ = import_ts(open_client)
    a_id = make_node(open_client, "A", shifted(0.5))
    start = [(e, n, x, y, z + 0.05) for e, n, x, y, z in shifted(0.5)]
    pm6 = g.output(g.step("#P PM6/ZDO Opt", [start, shifted(0.5)], -12.0, converged=True))
    pm6 = pm6.replace("E(RB3LYP)", "E(RPM6)")
    onto(open_client, upload(open_client, "A_pm6.log", pm6, a_id))
    b_id = make_node(open_client, "B", shifted(-0.5))
    done = follow(open_client, ts_id)
    matched = ends(open_client, done["id"])
    plus = matched["ends"]["+"]
    assert plus["matches"][0]["node_id"] == a_id and plus["matches"][0]["other_level"] is True
    assert matched["outcome"] == "links" and matched["confidence"] == "low"
    assert any("A194" in flag for flag in matched["flags"])
    assert matched["links"][1]["node_id"] == b_id
