"""Saved energy profiles (D190, FR-EN-19, A127, T-EN-47): named figures of pathways the drawer
opens, each pathway with "Show on the figure". Not in the history, quiet for other tabs, cleaned
of deleted nodes when read; the drawer's old pathways become "Profile 1" once. The drawer itself
is checked in frontend/e2e/energy-drawer.spec.ts."""

from chembook3d.models import InvestigationInfo
from tests.test_pathway import (  # noqa: F401
    Example,
    edge,
    example,
    get,
    history,
    node,
    patch,
    post,
)


def paths(*runs: tuple[list[str], bool], branch_id: str | None = None) -> list[dict]:
    return [{"ids": ids, "branch_id": branch_id, "shown": shown} for ids, shown in runs]


def test_profiles_are_saved_named_and_not_in_the_history(open_client, example):  # noqa: F811
    ids = example.nodes
    before = len(history(open_client))
    start = get(open_client, "/live")["version"]
    first = post(open_client, "/energy-profiles", {})
    assert first["name"] == "Profile 1" and first["paths"] == []
    second = post(open_client, "/energy-profiles", {})
    assert second["name"] == "Profile 2"

    a = [ids["T-pre"], ids["A1-S1"], ids["A1-S2"]]
    b = [ids["T-pre"], ids["B1-S1"]]
    body = {"name": "NHC vs CAAC", "paths": paths((a, True), (b, False))}
    changed = patch(open_client, f"/energy-profiles/{first['id']}", body)
    assert changed["name"] == "NHC vs CAAC"
    assert changed["paths"] == body["paths"]
    # Several pathways may be shown together; "shown" defaults to true.
    shown = patch(open_client, f"/energy-profiles/{first['id']}", {"paths": [{"ids": b}]})
    assert shown["paths"] == [{"ids": b, "branch_id": None, "shown": True}]

    response = open_client.patch(
        f"/api/energy-profiles/{second['id']}", json={"name": "nhc VS caac"}
    )
    assert response.status_code == 422 and "already" in response.json()["detail"]
    copy = post(open_client, f"/energy-profiles/{first['id']}/duplicate", status=200)
    assert copy["name"] == "NHC vs CAAC (copy)" and copy["paths"] == shown["paths"]
    again = post(open_client, f"/energy-profiles/{first['id']}/duplicate", status=200)
    assert again["name"] == "NHC vs CAAC (copy 2)"

    assert open_client.delete(f"/api/energy-profiles/{second['id']}").status_code == 204
    names = [p["name"] for p in get(open_client, "/energy-profiles")]
    assert names == ["NHC vs CAAC", "NHC vs CAAC (copy)", "NHC vs CAAC (copy 2)"]
    assert len(history(open_client)) == before  # layout-like, not in the history (A127)
    assert get(open_client, "/live")["version"] == start  # the drawer saves on every step


def test_bad_pathways_are_refused(open_client):
    profile = post(open_client, "/energy-profiles", {})
    for bad in (
        [{"ids": []}],
        [{"ids": "x"}],
        [{"ids": ["a"], "shown": "yes"}],
        [{"ids": ["a"], "branch_id": 3}],
        ["x"],
        [{"ids": ["a"]}] * 51,
    ):
        response = open_client.patch(f"/api/energy-profiles/{profile['id']}", json={"paths": bad})
        assert response.status_code == 422, bad
    response = open_client.patch(f"/api/energy-profiles/{profile['id']}", json={"name": "  "})
    assert response.status_code == 422


def test_a_deleted_node_drops_its_pathways(open_client, example):  # noqa: F811
    ids = example.nodes
    gone = node(open_client, label="Gone")["id"]
    edge(open_client, ids["T-pre"], gone)
    profile = post(open_client, "/energy-profiles", {})
    body = {
        "paths": [
            {"ids": [ids["T-pre"], gone]},
            {"ids": [ids["T-pre"], ids["A1-S1"]], "branch_id": example.branches["A"]},
        ]
    }
    patch(open_client, f"/energy-profiles/{profile['id']}", body)
    open_client.delete(f"/api/nodes/{gone}")
    found = get(open_client, "/energy-profiles")[0]
    assert found["paths"] == [
        {"ids": [ids["T-pre"], ids["A1-S1"]], "branch_id": example.branches["A"], "shown": True}
    ]


def test_the_view_names_the_open_profile(open_client):
    profile = post(open_client, "/energy-profiles", {})
    open_client.put("/api/view-state", json={"drawer": {"open": True, "profile_id": profile["id"]}})
    assert get(open_client, "/view-state")["drawer"] == {
        "open": True,
        "tab": "profile",
        "profile_id": profile["id"],
    }
    open_client.delete(f"/api/energy-profiles/{profile['id']}")
    assert get(open_client, "/view-state")["drawer"]["profile_id"] is None


def test_the_drawers_pathways_become_profile_1(open_client, example, tmp_path):  # noqa: F811
    ids = example.nodes
    old = [
        {"ids": [ids["T-pre"], ids["A1-S1"]], "branch_id": example.branches["A"], "choices": []},
        {"ids": [ids["T-pre"], ids["B1-S1"]], "branch_id": None},
        {"ids": []},
    ]
    investigation = open_client.app.state.investigation
    with investigation.sessions.begin() as session:
        info = session.get(InvestigationInfo, 1)
        info.view_state = {"drawer": {"open": True, "tab": "table", "paths": old}}
        info.repairs = [r for r in info.repairs if r != "energy_profiles"]
    open_client.post("/api/investigations/close")
    response = open_client.post("/api/investigations/open", json={"folder": str(tmp_path / "inv")})
    assert response.status_code == 200, response.text

    [profile] = get(open_client, "/energy-profiles")
    assert profile["name"] == "Profile 1"
    assert profile["paths"] == [
        {"ids": old[0]["ids"], "branch_id": example.branches["A"], "shown": True},
        {"ids": old[1]["ids"], "branch_id": None, "shown": True},
    ]
    drawer = get(open_client, "/view-state")["drawer"]
    assert drawer == {"open": True, "tab": "table", "profile_id": profile["id"]}
