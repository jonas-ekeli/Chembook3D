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
    return [
        {"ids": ids, "branch_id": branch_id, "shown": shown, "shifts": [], "colour": None}
        for ids, shown in runs
    ]


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
    assert shown["paths"] == [
        {"ids": b, "branch_id": None, "shown": True, "shifts": [], "colour": None}
    ]

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


def test_steps_moved_along_the_axis_are_kept(open_client, example):  # noqa: F811
    """D194, T-EN-48: a step moved later along the axis, with every step after it, is saved
    with its pathway in order, once per step; a step moved 0 columns is not stored; a duplicate
    keeps them."""
    ids = example.nodes
    a = [ids["T-pre"], ids["A1-S1"], ids["A1-S2"]]
    profile = post(open_client, "/energy-profiles", {})
    body = {"paths": [{"ids": a, "shifts": [{"at": 2, "by": 3}, {"at": 0, "by": 1}]}]}
    saved = patch(open_client, f"/energy-profiles/{profile['id']}", body)
    assert saved["paths"][0]["shifts"] == [{"at": 0, "by": 1}, {"at": 2, "by": 3}]
    back = patch(
        open_client,
        f"/energy-profiles/{profile['id']}",
        {"paths": [{"ids": a, "shifts": [{"at": 0, "by": 0}, {"at": 2, "by": 3}]}]},
    )
    assert back["paths"][0]["shifts"] == [{"at": 2, "by": 3}]
    copy = post(open_client, f"/energy-profiles/{profile['id']}/duplicate", status=200)
    assert copy["paths"] == back["paths"]
    assert get(open_client, "/energy-profiles")[0]["paths"] == back["paths"]
    for bad in (
        "x",
        [{"at": 3, "by": 1}],  # past the pathway's end
        [{"at": -1, "by": 1}],
        [{"at": 0, "by": -1}],  # never before where the pathway puts it
        [{"at": 0, "by": 21}],
        [{"at": 0, "by": 1.5}],
        [{"at": True, "by": 1}],
        [{"at": 1, "by": 1}, {"at": 1, "by": 2}],
        [{"at": 1}],
    ):
        response = open_client.patch(
            f"/api/energy-profiles/{profile['id']}", json={"paths": [{"ids": a, "shifts": bad}]}
        )
        assert response.status_code == 422, bad


def test_a_pathways_chosen_colour_is_kept(open_client, example):  # noqa: F811
    """D195, T-EN-49: a colour chosen for a pathway is saved in lower case, kept by a duplicate
    and cleared with null (back to the branch's colour); anything but #rrggbb is refused."""
    ids = example.nodes
    a = [ids["T-pre"], ids["A1-S1"]]
    profile = post(open_client, "/energy-profiles", {})
    url = f"/energy-profiles/{profile['id']}"
    saved = patch(open_client, url, {"paths": [{"ids": a, "colour": "#D55E00"}, {"ids": a}]})
    assert [p["colour"] for p in saved["paths"]] == ["#d55e00", None]
    copy = post(open_client, f"{url}/duplicate", status=200)
    assert copy["paths"] == saved["paths"]
    cleared = patch(open_client, url, {"paths": [{"ids": a, "colour": None}]})
    assert cleared["paths"][0]["colour"] is None
    for bad in ("red", "#abc", "#12345g", " #123456", 3, ["#123456"]):
        response = open_client.patch(f"/api{url}", json={"paths": [{"ids": a, "colour": bad}]})
        assert response.status_code == 422, bad


def test_each_profile_keeps_its_own_reference(open_client, example):  # noqa: F811
    """D199, T-EN-50: a profile keeps one reference for all its pathways, apart from the node
    cards' (the view state's); it must be on one of its pathways, stays when another pathway
    is added, is copied by a duplicate and is dropped when its last pathway is taken out or
    its node deleted."""
    ids = example.nodes
    a = [ids["T-pre"], ids["A1-S1"], ids["A1-S2"]]
    b = [ids["T-pre"], ids["B1-S1"]]
    open_client.put("/api/view-state", json={"reference_id": ids["T-pre"]})
    first = post(open_client, "/energy-profiles", {"paths": [{"ids": a}]})
    assert first["reference_id"] is None
    url = f"/energy-profiles/{first['id']}"
    chosen = patch(open_client, url, {"reference_id": ids["A1-S1"]})
    assert chosen["reference_id"] == ids["A1-S1"]
    other = post(
        open_client, "/energy-profiles", {"paths": [{"ids": b}], "reference_id": ids["B1-S1"]}
    )
    assert other["reference_id"] == ids["B1-S1"]
    assert get(open_client, "/view-state")["reference_id"] == ids["T-pre"]

    added = patch(open_client, url, {"paths": [{"ids": a}, {"ids": b, "shown": False}]})
    assert added["reference_id"] == ids["A1-S1"]
    copy = post(open_client, f"{url}/duplicate", status=200)
    assert copy["reference_id"] == ids["A1-S1"]
    response = open_client.patch(f"/api{url}", json={"reference_id": ids["B2-S1"]})
    assert response.status_code == 422 and "pathways" in response.json()["detail"]
    for bad in (3, "", ["x"]):
        response = open_client.patch(f"/api{url}", json={"reference_id": bad})
        assert response.status_code == 422, bad
    assert patch(open_client, url, {"paths": [{"ids": b}]})["reference_id"] is None
    assert patch(open_client, url, {"reference_id": ids["B1-S1"]})["reference_id"] == ids["B1-S1"]
    assert patch(open_client, url, {"reference_id": None})["reference_id"] is None

    gone = node(open_client, label="Gone")["id"]
    edge(open_client, ids["B1-S1"], gone)
    path = patch(open_client, url, {"paths": [{"ids": [*b, gone]}], "reference_id": gone})
    assert path["reference_id"] == gone
    open_client.delete(f"/api/nodes/{gone}")
    found = next(p for p in get(open_client, "/energy-profiles") if p["id"] == first["id"])
    assert found["paths"] == [] and found["reference_id"] is None


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
        {
            "ids": [ids["T-pre"], ids["A1-S1"]],
            "branch_id": example.branches["A"],
            "shown": True,
            "shifts": [],
            "colour": None,
        }
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
        info.view_state = {
            "reference_id": ids["A1-S1"],
            "drawer": {"open": True, "tab": "table", "paths": old},
        }
        info.repairs = [r for r in info.repairs if r != "energy_profiles"]
    open_client.post("/api/investigations/close")
    response = open_client.post("/api/investigations/open", json={"folder": str(tmp_path / "inv")})
    assert response.status_code == 200, response.text

    [profile] = get(open_client, "/energy-profiles")
    assert profile["name"] == "Profile 1"
    assert profile["reference_id"] == ids["A1-S1"]  # D199: the reference it was shown with
    assert profile["paths"] == [
        {
            "ids": old[0]["ids"],
            "branch_id": example.branches["A"],
            "shown": True,
            "shifts": [],
            "colour": None,
        },
        {"ids": old[1]["ids"], "branch_id": None, "shown": True, "shifts": [], "colour": None},
    ]
    drawer = get(open_client, "/view-state")["drawer"]
    assert drawer == {"open": True, "tab": "table", "profile_id": profile["id"]}
