"""Node behaviour through the API: FR-NODE-*, FR-HIST-*, identity rules ID-4 and ID-5."""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from chembook3d.models import Calculation, Node
from tests.conftest import WATER

MOVED_WATER = WATER.replace("0.117300", "0.120000")


def _create(client, **fields):
    response = client.post("/api/nodes", json=fields)
    assert response.status_code == 201, response.text
    return response.json()


def _history(client, node_id):
    return client.get("/api/history", params={"record_id": node_id}).json()


def test_node_with_no_fields_is_a_planned_node(open_client):
    node = _create(open_client)
    assert node["status"] == "planned"
    assert node["role"] == "unspecified"
    assert node["xyz"] is None and node["formula"] is None


def test_create_with_coordinates_and_notes(open_client):
    node = _create(open_client, label="INT1", charge=0, multiplicity=1, notes="guess", xyz=WATER)
    assert node["formula"] == "H2O"
    assert node["atom_count"] == 3
    assert node["notes"] == "guess"


def test_edit_coordinates_in_place_without_calculations(open_client):
    # T-ID-01
    node = _create(open_client, xyz=WATER)
    response = open_client.put(f"/api/nodes/{node['id']}/geometry", json={"xyz": MOVED_WATER})
    assert response.status_code == 200
    assert response.json()["derived"] is False
    assert response.json()["node"]["id"] == node["id"]

    geometry_changes = [e for e in _history(open_client, node["id"]) if e["field"] == "geometry"]
    assert len(geometry_changes) == 1
    assert geometry_changes[0]["old_value"][0] == ["O", 0.0, 0.0, 0.1173]
    assert geometry_changes[0]["new_value"][0] == ["O", 0.0, 0.0, 0.12]


def test_edit_coordinates_with_a_calculation_creates_derived_node(open_client):
    # T-ID-02, D23
    node = _create(open_client, label="INT1", xyz=WATER)
    investigation = open_client.app.state.investigation
    with investigation.sessions.begin() as session:
        session.add(Calculation(node_id=node["id"], type="single_point", program="Gaussian"))

    response = open_client.put(f"/api/nodes/{node['id']}/geometry", json={"xyz": MOVED_WATER})
    assert response.status_code == 200
    result = response.json()
    assert result["derived"] is True
    derived = result["node"]
    assert derived["id"] != node["id"]
    assert derived["derived_from_id"] == node["id"]
    assert derived["label"] == "INT1 (derived)"
    assert derived["calculation_count"] == 0

    original = open_client.get(f"/api/nodes/{node['id']}").json()
    assert original["xyz"] == node["xyz"]
    assert original["calculation_count"] == 1


def test_invalid_xyz_reports_lines_and_saves_nothing(open_client):
    # T-ID-07
    node = _create(open_client, xyz=WATER)
    response = open_client.put(
        f"/api/nodes/{node['id']}/geometry", json={"xyz": "O 0 0 0\nRu 0.0 abc 1.0\n"}
    )
    assert response.status_code == 422
    assert response.json()["detail"]["xyz_errors"] == [
        {"line": 2, "message": "coordinates must be numbers"}
    ]
    assert open_client.get(f"/api/nodes/{node['id']}").json()["xyz"] == node["xyz"]
    assert not any(e["field"] == "geometry" for e in _history(open_client, node["id"]))


def test_empty_xyz_removes_coordinates_without_calculations(open_client):
    # T-ID-08, D90
    node = _create(open_client, label="INT1", xyz=WATER)
    response = open_client.put(f"/api/nodes/{node['id']}/geometry", json={"xyz": "  \n"})
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["derived"] is False
    assert result["node"]["id"] == node["id"]
    assert result["node"]["xyz"] is None and result["node"]["formula"] is None
    assert result["node"]["label"] == "INT1"

    geometry_changes = [e for e in _history(open_client, node["id"]) if e["field"] == "geometry"]
    assert len(geometry_changes) == 1
    assert geometry_changes[0]["old_value"][0] == ["O", 0.0, 0.0, 0.1173]
    assert geometry_changes[0]["new_value"] is None

    # Saving empty text again changes nothing and records nothing.
    again = open_client.put(f"/api/nodes/{node['id']}/geometry", json={"xyz": ""})
    assert again.status_code == 200
    assert len([e for e in _history(open_client, node["id"]) if e["field"] == "geometry"]) == 1
    assert open_client.get(f"/api/nodes/{node['id']}/xyz").status_code == 404


def test_empty_xyz_is_refused_on_a_node_with_calculations(open_client):
    # T-ID-08, D90: the coordinates are the geometry the calculations were run on.
    node = _create(open_client, xyz=WATER)
    investigation = open_client.app.state.investigation
    with investigation.sessions.begin() as session:
        session.add(Calculation(node_id=node["id"], type="single_point", program="Gaussian"))

    response = open_client.put(f"/api/nodes/{node['id']}/geometry", json={"xyz": ""})
    assert response.status_code == 422
    assert "calculations" in response.json()["detail"]
    assert open_client.get(f"/api/nodes/{node['id']}").json()["xyz"] == node["xyz"]
    assert not any(e["field"] == "geometry" for e in _history(open_client, node["id"]))
    assert len(open_client.get("/api/nodes").json()) == 1


def test_field_updates_are_validated_and_recorded(open_client):
    node = _create(open_client, tags=["optimization-incomplete", "A1"])
    response = open_client.patch(
        f"/api/nodes/{node['id']}", json={"status": "done", "tags": ["A1"], "charge": 1}
    )
    assert response.status_code == 200
    changes = {
        e["field"]: (e["old_value"], e["new_value"]) for e in _history(open_client, node["id"])
    }
    assert changes["status"] == ("planned", "done")
    # FR-NODE-08: removing the system tag is recorded.
    assert changes["tags"] == (["optimization-incomplete", "A1"], ["A1"])
    assert changes["charge"] == (None, 1)

    assert (
        open_client.patch(f"/api/nodes/{node['id']}", json={"status": "maybe"}).status_code == 422
    )
    assert (
        open_client.patch(f"/api/nodes/{node['id']}", json={"multiplicity": 0}).status_code == 422
    )


def test_layout_moves_are_not_recorded_in_history(open_client):
    node = _create(open_client)
    open_client.patch(f"/api/nodes/{node['id']}", json={"pos_x": 120, "pos_y": -40})
    assert [e["action"] for e in _history(open_client, node["id"])] == ["create"]


def test_xyz_download(open_client):
    # FR-3D-06
    node = _create(open_client, label="water 1", xyz=WATER)
    response = open_client.get(f"/api/nodes/{node['id']}/xyz")
    assert response.status_code == 200
    assert response.headers["content-disposition"] == (
        "attachment; filename=\"water 1.xyz\"; filename*=UTF-8''water%201.xyz"
    )
    assert response.text.splitlines()[:2] == ["3", "water 1"]


@pytest.mark.parametrize(
    ("label", "ascii_name", "utf8_name"),
    [
        ("Ru-CAAC α", "Ru-CAAC _", "Ru-CAAC%20%CE%B1"),  # was a 500 (headers are Latin-1)
        ("café", "caf_", "caf%C3%A9"),
        ("TS1–2 ‡", "TS1_2 _", "TS1%E2%80%932%20%E2%80%A1"),
        ("TS1-2", "TS1-2", "TS1-2"),  # Jonas's primes stay, so these three stay apart
        ("TS1-2'", "TS1-2'", "TS1-2%27"),
        ("TS1-2''", "TS1-2''", "TS1-2%27%27"),
        ('a/b: "c"?.', "a_b_ _c__", "a_b_%20_c__"),  # only what Windows refuses goes
    ],
)
def test_xyz_download_is_named_as_the_node(open_client, label, ascii_name, utf8_name):
    node = _create(open_client, label=label, xyz=WATER)
    response = open_client.get(f"/api/nodes/{node['id']}/xyz")
    assert response.status_code == 200
    assert response.headers["content-disposition"] == (
        f"attachment; filename=\"{ascii_name}.xyz\"; filename*=UTF-8''{utf8_name}.xyz"
    )
    assert response.text.splitlines()[:2] == ["3", label]


def test_delete_previews_then_removes_and_records(open_client):
    node = _create(open_client, label="gone", xyz=WATER)
    preview = open_client.get(f"/api/nodes/{node['id']}/delete-preview").json()
    assert preview == {
        "node": node["id"],
        "calculations": 0,
        "transitions": [],
        "species_on": [],
    }
    assert open_client.delete(f"/api/nodes/{node['id']}").status_code == 200
    assert open_client.get(f"/api/nodes/{node['id']}").status_code == 404
    last = _history(open_client, node["id"])[0]
    assert last["action"] == "delete"
    assert last["old_value"]["label"] == "gone"


def test_history_is_newest_first_across_the_investigation(open_client):
    first = _create(open_client, label="a")
    second = _create(open_client, label="b")
    entries = open_client.get("/api/history").json()
    assert [e["record_id"] for e in entries[:2]] == [second["id"], first["id"]]


def test_node_endpoints_need_an_open_investigation(client):
    assert client.get("/api/nodes").status_code == 409


def test_nodes_are_listed_in_creation_order(open_client):
    # Creation times can tie on Windows, so the order must not depend on them or on ids.
    labels = [f"n{i}" for i in range(12)]
    for label in labels:
        _create(open_client, label=label)
    assert [n["label"] for n in open_client.get("/api/nodes").json()] == labels


def test_a_change_is_committed_before_the_answer_is_sent(open_client):
    # NFR-DATA-01: the UI refreshes the canvas as soon as a change is answered. If the answer
    # went out before the transaction committed, that refresh could read the old state (the
    # imported node missing on slow Windows runners).
    app = open_client.app
    investigation = app.state.investigation
    seen: list[int] = []

    async def recording(scope, receive, send):
        async def spy(message):
            if message["type"] == "http.response.start":
                with investigation.sessions() as session:
                    seen.append(len(session.scalars(select(Node)).all()))
            await send(message)

        await app(scope, receive, spy)

    response = TestClient(recording).post("/api/nodes", json={"label": "fresh"})
    assert response.status_code == 201
    assert seen == [1]


def test_saved_orientation_is_kept_normalised_and_not_in_history(open_client):
    node = _create(open_client, xyz=WATER)
    assert node["view_rotation"] is None
    response = open_client.patch(f"/api/nodes/{node['id']}", json={"view_rotation": [0, 0, 2, 0]})
    assert response.status_code == 200
    assert response.json()["view_rotation"] == [0.0, 0.0, 1.0, 0.0]
    assert all(e["field"] != "view_rotation" for e in _history(open_client, node["id"]))

    for bad in ([0, 0, 0, 0], [1, 2, 3], ["a", 0, 0, 1]):
        response = open_client.patch(f"/api/nodes/{node['id']}", json={"view_rotation": bad})
        assert response.status_code == 422, bad

    cleared = open_client.patch(f"/api/nodes/{node['id']}", json={"view_rotation": None})
    assert cleared.json()["view_rotation"] is None


def test_derived_node_keeps_the_saved_orientation(open_client):
    node = _create(open_client, xyz=WATER)
    open_client.patch(f"/api/nodes/{node['id']}", json={"view_rotation": [0, 1, 0, 1]})
    investigation = open_client.app.state.investigation
    with investigation.sessions.begin() as session:
        session.add(Calculation(node_id=node["id"], type="single_point", program="Gaussian"))
    response = open_client.put(f"/api/nodes/{node['id']}/geometry", json={"xyz": MOVED_WATER})
    derived = response.json()["node"]
    assert derived["id"] != node["id"]
    assert derived["view_rotation"] == pytest.approx([0, 0.70710678, 0, 0.70710678])


def test_hydrogen_display_setting(open_client):
    assert open_client.get("/api/settings").json()["hydrogens"] == "all"
    response = open_client.put("/api/settings", json={"hydrogens": "polar"})
    assert response.json()["hydrogens"] == "polar"
    assert open_client.get("/api/settings").json()["hydrogens"] == "polar"
    assert open_client.put("/api/settings", json={"hydrogens": "some"}).status_code == 422


def test_steric_map_colours_setting(open_client):
    # D84: the steric maps' colours are an app setting, blue unless changed.
    assert open_client.get("/api/settings").json()["steric_colours"] == "blue"
    response = open_client.put("/api/settings", json={"steric_colours": "green-yellow-red"})
    assert response.json()["steric_colours"] == "green-yellow-red"
    assert open_client.get("/api/settings").json()["steric_colours"] == "green-yellow-red"
    assert open_client.put("/api/settings", json={"steric_colours": "pink"}).status_code == 422


def test_profile_style_setting(open_client):
    # D106: one profile style for the app, "Screen" unless changed; keys given replace the
    # current ones, numbers are kept within bounds and anything unknown is dropped.
    settings = open_client.get("/api/settings").json()
    assert settings["profile_style"] == settings["profile_presets"]["screen"]
    assert settings["profile_presets"]["publication"]["connector"] == "curved"
    response = open_client.put(
        "/api/settings",
        json={
            "profile_style": {
                "connector": "curved",
                "width": 99999,
                "font_size": 14,
                "title": False,
                "colours": "pink",
                "script": "<x>",
            }
        },
    )
    style = response.json()["profile_style"]
    assert style["connector"] == "curved"
    assert style["width"] == 4000
    assert style["font_size"] == 14
    assert style["title"] is False
    assert style["colours"] == "branch"
    assert "script" not in style
    # A later change keeps the earlier ones, and the style survives a reload of the settings.
    open_client.put("/api/settings", json={"profile_style": {"legend": "top-left"}})
    style = open_client.get("/api/settings").json()["profile_style"]
    assert (style["connector"], style["legend"], style["width"]) == ("curved", "top-left", 4000)
    publication = settings["profile_presets"]["publication"]
    open_client.put("/api/settings", json={"profile_style": publication})
    assert open_client.get("/api/settings").json()["profile_style"] == publication
