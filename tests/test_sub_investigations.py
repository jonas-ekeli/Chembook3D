"""Sub-investigations (D193, FR-SUB-01…05): a box holding a canvas of its own. Test IDs from
docs/spec/07 are named in each test."""

from tests.conftest import WATER
from tests.test_import import TS, commit, fixture, named, upload
from tests.test_live import ask
from tests.test_pathway import edge, get, history, node, patch, post


def subs(client) -> dict[str, dict]:
    return {s["label"]: s for s in get(client, "/sub-investigations")}


def canvas_of(client, record_id: str) -> str | None:
    found = get(client, "/canvas")
    for record in [*found["nodes"], *found["groups"]]:
        if record["id"] == record_id:
            return record["canvas_id"]
    raise AssertionError(record_id)


def rotation_detour(client) -> dict[str, str]:
    """Ru-14e-1 → TS → Ru-14e-2, with Ru-14e-2 going on to a π-complex outside."""
    ids = {
        "a": node(client, label="Ru-14e-1", role="minimum", pos_x=0, pos_y=0)["id"],
        "ts": node(client, label="Ru-14e-1-rotation", role="transition_state", pos_x=200, pos_y=0)[
            "id"
        ],
        "b": node(client, label="Ru-14e-2", role="minimum", pos_x=400, pos_y=100)["id"],
        "pi": node(client, label="Ru-π-complex", role="minimum", pos_x=800, pos_y=0)["id"],
    }
    ids["e1"] = edge(client, ids["a"], ids["ts"])["id"]
    ids["e2"] = edge(client, ids["ts"], ids["b"])["id"]
    ids["e3"] = edge(client, ids["b"], ids["pi"])["id"]
    return ids


def test_moving_a_detour_into_a_new_sub_investigation(open_client):
    # T-SUB-01: the box goes where the selection was, the cards keep their arrangement inside,
    # the face is the first node selected, and every edge stays, the one leaving crossing.
    ids = rotation_detour(open_client)
    edges_before = get(open_client, "/canvas")["transitions"]
    sub = post(
        open_client,
        "/sub-investigations",
        {"label": "Ru-14e rotations", "ids": [ids["a"], ids["ts"], ids["b"]]},
    )
    assert sub["parent_id"] is None and sub["path"] == [sub["id"]]
    assert (sub["pos_x"], sub["pos_y"]) == (200, 50)
    assert sub["face_id"] == ids["a"]
    assert (sub["node_count"], sub["ts_count"], sub["group_count"], sub["sub_count"]) == (
        3,
        1,
        0,
        0,
    )
    canvas = get(open_client, "/canvas")
    assert canvas["sub_investigations"] == [sub]
    by_id = {n["id"]: n for n in canvas["nodes"]}
    assert [by_id[ids[k]]["canvas_id"] for k in ("a", "ts", "b", "pi")] == [sub["id"]] * 3 + [None]
    assert [(by_id[ids[k]]["pos_x"], by_id[ids[k]]["pos_y"]) for k in ("a", "ts", "b")] == [
        (0, 0),
        (200, 0),
        (400, 100),
    ]
    assert canvas["transitions"] == edges_before  # Q59: no edge ends at the box

    entries = history(open_client, sub["id"])
    assert [e["action"] for e in entries] == ["create"]
    assert entries[0]["new_value"]["label"] == "Ru-14e rotations"
    moved = [e for e in history(open_client, ids["a"]) if e["field"] == "canvas_id"]
    assert moved[0]["old_value"] is None and moved[0]["new_value"] == sub["id"]

    # Layout is not in the history (as A35); the name, notes and face are.
    response = open_client.put("/api/positions", json={"positions": {sub["id"]: {"x": 5, "y": 6}}})
    assert response.status_code == 204
    renamed = patch(
        open_client, f"/sub-investigations/{sub['id']}", {"label": "Rotations", "face_id": ids["b"]}
    )
    assert (renamed["label"], renamed["face_id"], renamed["pos_x"]) == ("Rotations", ids["b"], 5)
    assert [e["field"] for e in history(open_client, sub["id"])][:2] == ["face_id", "label"]
    response = open_client.patch(
        f"/api/sub-investigations/{sub['id']}", json={"face_id": ids["pi"]}
    )
    assert response.status_code == 422  # the face is a node inside


def test_a_selection_on_two_canvases_or_with_a_species_is_refused(open_client):
    # T-SUB-01
    ids = rotation_detour(open_client)
    sub = post(open_client, "/sub-investigations", {"label": "S", "ids": [ids["a"]]})
    response = open_client.post("/api/sub-investigations", json={"ids": [ids["a"], ids["pi"]]})
    assert response.status_code == 422
    assert "one canvas" in response.json()["detail"]
    species = node(open_client, label="ethylene", kind="species")
    response = open_client.post("/api/sub-investigations", json={"ids": [species["id"]]})
    assert response.status_code == 422
    response = open_client.post(
        "/api/sub-investigations/move", json={"ids": [species["id"]], "target_id": sub["id"]}
    )
    assert response.status_code == 422


def test_groups_move_whole(open_client):
    # T-SUB-02, Q60: a member alone is refused until the caller says the group moves too.
    ids = rotation_detour(open_client)
    group = post(
        open_client, "/groups/reconnect", {"member_ids": [ids["a"], ids["b"]], "label": "G"}
    )
    response = open_client.post("/api/sub-investigations", json={"ids": [ids["a"]]})
    assert response.status_code == 422
    assert "“G”" in response.json()["detail"]
    sub = post(
        open_client, "/sub-investigations", {"label": "S", "ids": [ids["a"]], "with_groups": True}
    )
    assert canvas_of(open_client, group["id"]) == sub["id"]
    assert canvas_of(open_client, ids["b"]) == sub["id"]
    assert sub["face_id"] == ids["a"]
    assert sub["group_count"] == 1 and sub["node_count"] == 2

    # A node joining the group moves onto the group's canvas.
    post(open_client, f"/groups/{group['id']}/members", {"node_ids": [ids["pi"]]}, status=200)
    assert canvas_of(open_client, ids["pi"]) == sub["id"]
    # A new group is made on its first member's canvas; the others join it there.
    other = node(open_client, label="X")
    made = post(open_client, "/groups/reconnect", {"member_ids": [ids["ts"], other["id"]]})
    assert made["canvas_id"] is None and canvas_of(open_client, other["id"]) is None


def test_nesting_and_no_loops(open_client):
    # T-SUB-03, Q61
    ids = rotation_detour(open_client)
    outer = post(
        open_client, "/sub-investigations", {"label": "Outer", "ids": [ids["a"], ids["ts"]]}
    )
    inner = post(open_client, "/sub-investigations", {"label": "Inner", "ids": [ids["ts"]]})
    assert inner["parent_id"] == outer["id"]
    assert inner["path"] == [outer["id"], inner["id"]]
    found = subs(open_client)
    assert (found["Outer"]["node_count"], found["Outer"]["ts_count"]) == (2, 1)
    assert found["Outer"]["sub_count"] == 1
    assert found["Inner"]["node_count"] == 1
    for target in (outer["id"], inner["id"]):
        response = open_client.post(
            "/api/sub-investigations/move", json={"ids": [outer["id"]], "target_id": target}
        )
        assert response.status_code == 422
    # An empty box can be made on a given canvas.
    empty = post(
        open_client,
        "/sub-investigations",
        {"label": "Empty", "canvas_id": inner["id"], "pos_x": 10, "pos_y": 20},
    )
    assert (empty["parent_id"], empty["pos_x"], empty["node_count"]) == (inner["id"], 10, 0)
    assert empty["face_id"] is None


def test_move_to_another_canvas(open_client):
    # T-SUB-04: onto a canvas holding something, the moved cards keep their arrangement right
    # of it; a face that leaves its box is cleared.
    ids = rotation_detour(open_client)
    sub = post(open_client, "/sub-investigations", {"label": "S", "ids": [ids["a"], ids["ts"]]})
    moved = post(
        open_client,
        "/sub-investigations/move",
        {"ids": [ids["a"], ids["ts"], ids["pi"]], "target_id": None},
        status=200,
    )
    assert moved == {"moved": 2}  # Ru-π-complex was there already
    canvas = {n["id"]: n for n in get(open_client, "/canvas")["nodes"]}
    # Right of what the main canvas holds (Ru-14e-2 at 400, Ru-π-complex at 800, the box at 100).
    assert canvas[ids["a"]]["pos_x"] == 800 + 480 and canvas[ids["a"]]["pos_y"] == 0
    assert canvas[ids["ts"]]["pos_x"] == 800 + 480 + 200
    assert subs(open_client)["S"]["face_id"] is None
    assert [e["field"] for e in history(open_client, sub["id"])] == ["face_id", None]

    # Onto an empty canvas, positions are kept.
    post(
        open_client,
        "/sub-investigations/move",
        {"ids": [ids["b"]], "target_id": sub["id"]},
        status=200,
    )
    b = next(n for n in get(open_client, "/canvas")["nodes"] if n["id"] == ids["b"])
    assert (b["canvas_id"], b["pos_x"], b["pos_y"]) == (sub["id"], 400, 100)


def test_dissolve_puts_everything_back_where_the_box_was(open_client):
    # T-SUB-05
    ids = rotation_detour(open_client)
    sub = post(open_client, "/sub-investigations", {"label": "S", "ids": [ids["a"], ids["b"]]})
    inner = post(open_client, "/sub-investigations", {"label": "I", "ids": [ids["b"]]})
    open_client.put("/api/positions", json={"positions": {sub["id"]: {"x": 1000, "y": 1000}}})
    assert post(open_client, f"/sub-investigations/{sub['id']}/dissolve", status=200) == {
        "moved": 2
    }
    canvas = get(open_client, "/canvas")
    a = next(n for n in canvas["nodes"] if n["id"] == ids["a"])
    assert a["canvas_id"] is None
    # The node and the inner box were 400 × 100 apart around their centre; it is now at the box.
    assert (a["pos_x"], a["pos_y"]) == (1000 - 200, 1000 - 50)
    assert [s["id"] for s in canvas["sub_investigations"]] == [inner["id"]]
    assert canvas["sub_investigations"][0]["parent_id"] is None
    assert canvas["sub_investigations"][0]["face_id"] == ids["b"]
    assert history(open_client, sub["id"])[0]["action"] == "dissolve"
    assert len(canvas["transitions"]) == 3


def test_delete_with_contents(open_client):
    # T-SUB-06: the preview lists what goes; nested boxes, groups, nodes and edges all go.
    ids = rotation_detour(open_client)
    extra = node(open_client, label="X")["id"]
    group = post(open_client, "/groups/reconnect", {"member_ids": [ids["b"], extra]})
    sub = post(
        open_client,
        "/sub-investigations",
        {"label": "S", "ids": [ids["a"], ids["ts"], group["id"]]},
    )
    inner = post(open_client, "/sub-investigations", {"label": "I", "ids": [ids["ts"]]})
    assert get(open_client, f"/sub-investigations/{sub['id']}/delete-preview") == {
        "nodes": 4,
        "transition_states": 1,
        "groups": 1,
        "sub_investigations": 1,
        "calculations": 0,
        "transitions": 3,
    }
    response = open_client.delete(f"/api/sub-investigations/{sub['id']}")
    assert response.status_code == 200, response.text
    assert response.json()["nodes"] == 4
    canvas = get(open_client, "/canvas")
    assert [n["id"] for n in canvas["nodes"]] == [ids["pi"]]
    assert canvas["groups"] == [] and canvas["transitions"] == []
    assert canvas["sub_investigations"] == []
    assert history(open_client, inner["id"])[0]["action"] == "delete"

    # An empty box goes too; deleting a box's face clears the face.
    empty = post(open_client, "/sub-investigations", {"label": "E"})
    assert open_client.delete(f"/api/sub-investigations/{empty['id']}").status_code == 200
    face = post(open_client, "/sub-investigations", {"label": "F", "ids": [ids["pi"]]})
    assert open_client.delete(f"/api/nodes/{ids['pi']}").status_code == 200
    assert subs(open_client)["F"]["face_id"] is None
    assert history(open_client, face["id"])[0]["field"] == "face_id"


def test_new_nodes_land_on_the_open_canvas(open_client):
    # T-SUB-07, A128: add node and imports go on the canvas given; a derived node on its
    # parent's; a node that becomes a free species leaves its canvas.
    sub = post(open_client, "/sub-investigations", {"label": "S"})
    made = node(open_client, label="inside", canvas_id=sub["id"], xyz=WATER)
    assert made["canvas_id"] == sub["id"]
    response = open_client.post("/api/nodes", json={"label": "x", "canvas_id": "missing"})
    assert response.status_code == 422
    response = open_client.patch(f"/api/nodes/{made['id']}", json={"canvas_id": None})
    assert response.status_code == 422  # Move to… changes a canvas

    derived = open_client.put(
        f"/api/nodes/{made['id']}/geometry", json={"xyz": WATER, "new_node": True}
    ).json()
    assert canvas_of(open_client, derived["node"]["id"]) == sub["id"]

    plan = upload(open_client, TS, fixture(TS))
    result = commit(open_client, plan, **named(plan), canvas_id=sub["id"])
    assert canvas_of(open_client, result["node_id"]) == sub["id"]

    response = open_client.put(f"/api/nodes/{made['id']}/kind", json={"kind": "species"})
    assert response.status_code == 200, response.text
    assert response.json()["canvas_id"] is None
    assert subs(open_client)["S"]["face_id"] is None


def test_the_view_remembers_the_open_canvas(open_client):
    # T-SUB-08, D105: the open sub-investigation and expanded boxes; a deleted one is dropped.
    sub = post(open_client, "/sub-investigations", {"label": "S"})
    state = get(open_client, "/view-state")
    state["canvas"] = sub["id"]
    state["expanded"] = [sub["id"]]
    assert open_client.put("/api/view-state", json=state).status_code == 204
    assert get(open_client, "/view-state")["canvas"] == sub["id"]
    assert get(open_client, "/view-state")["expanded"] == [sub["id"]]
    open_client.delete(f"/api/sub-investigations/{sub['id']}")
    after = get(open_client, "/view-state")
    assert after["canvas"] is None and after["expanded"] == []


def test_claude_is_asked_before_deleting_or_dissolving(open_client):
    # T-MCP-26: delete and dissolve are confirm actions whose dialog says what goes.
    from chembook3d.mcp_tools import BY_NAME

    ids = rotation_detour(open_client)
    sub = post(open_client, "/sub-investigations", {"label": "S", "ids": [ids["a"], ids["ts"]]})
    assert BY_NAME["delete_sub_investigation"].kind == "confirm"
    assert BY_NAME["dissolve_sub_investigation"].kind == "confirm"
    assert BY_NAME["create_sub_investigation"].kind == "write"
    for action, words in (
        ("delete_sub_investigation", "with its contents: 2 nodes and 2 edges"),
        ("dissolve_sub_investigation", "goes back onto the main canvas"),
    ):
        asked = ask(open_client, action, sub_investigation_id=sub["id"])
        assert asked.status_code == 201, asked.text
        assert words in asked.json()["summary"], asked.json()
