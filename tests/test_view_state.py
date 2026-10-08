"""What the notebook showed when last used (D105, FR-UI-10): saved in the investigation, quiet
for other tabs, cleaned of junk and of records deleted since. The restoring itself is checked in
frontend/e2e/view-state.spec.ts."""

from tests.test_pathway import (  # noqa: F401
    Example,
    edge,
    example,
    get,
    history,
    node,
    reconnect_g6,
)


def save(client, state: dict) -> None:
    response = client.put("/api/view-state", json=state)
    assert response.status_code == 204, response.text


def test_a_new_investigation_remembers_nothing(open_client):
    assert get(open_client, "/view-state") == {
        "level": None,
        "energy_type": None,
        "reference_id": None,
        "edge_energies": True,
        "filters": {"branches": [], "statuses": [], "steps": []},
        "expanded": [],
        "drawer": {"open": False, "tab": "profile", "paths": []},
    }


def test_saved_view_survives_closing_and_opening(open_client, example, tmp_path):  # noqa: F811
    ids = example.nodes
    group = reconnect_g6(open_client, example)
    state = {
        "level": "abc~def",
        "energy_type": "G_qh",
        "reference_id": ids["T-pre"],
        "edge_energies": False,
        "filters": {
            "branches": [example.branches["B"], "none"],
            "statuses": ["failed"],
            "steps": [example.steps[2]],
        },
        "expanded": [group["id"]],
        "drawer": {
            "open": True,
            "tab": "table",
            "paths": [
                {
                    "ids": [ids["T-pre"], ids["A1-S1"]],
                    "branch_id": example.branches["A"],
                    "choices": [{"node_id": ids["A1-S2"], "label": "old", "status": "planned"}],
                }
            ],
        },
    }
    before = len(history(open_client))
    save(open_client, state)
    assert len(history(open_client)) == before  # view only, not in the history (D105)

    open_client.post("/api/investigations/close")
    response = open_client.post("/api/investigations/open", json={"folder": str(tmp_path / "inv")})
    assert response.status_code == 200, response.text
    found = get(open_client, "/view-state")
    # The fork's choices carry their current label and status.
    state["drawer"]["paths"][0]["choices"] = [
        {"node_id": ids["A1-S2"], "label": "A1-S2", "status": "planned"}
    ]
    assert found == state


def test_deleted_records_are_dropped(open_client, example):  # noqa: F811
    ids = example.nodes
    group = reconnect_g6(open_client, example)
    gone = node(open_client, label="Gone")["id"]
    edge(open_client, ids["T-pre"], gone)
    save(
        open_client,
        {
            "reference_id": gone,
            "expanded": [group["id"], "missing"],
            "filters": {
                "branches": ["missing", "none"],
                "statuses": ["failed", "nonsense"],
                "steps": ["missing"],
            },
            "drawer": {
                "paths": [
                    {"ids": [ids["T-pre"], gone], "branch_id": None},
                    {"ids": [ids["T-pre"]], "branch_id": "missing", "choices": [{"node_id": gone}]},
                ]
            },
        },
    )
    open_client.delete(f"/api/nodes/{gone}")
    found = get(open_client, "/view-state")
    assert found["reference_id"] is None
    assert found["expanded"] == [group["id"]]
    assert found["filters"] == {"branches": ["none"], "statuses": ["failed"], "steps": []}
    # The pathway through the deleted node is dropped; the other loses its gone branch and choice.
    assert found["drawer"]["paths"] == [{"ids": [ids["T-pre"]], "branch_id": None, "choices": []}]


def test_junk_is_not_kept(open_client):
    save(
        open_client,
        {
            "energy_type": "Gibbs",
            "level": ["not", "text"],
            "edge_energies": "no",
            "expanded": "all",
            "drawer": {"open": "yes", "tab": "chart", "paths": [{"ids": []}, "x"]},
            "secret": "x" * 10000,
        },
    )
    found = get(open_client, "/view-state")
    assert found["energy_type"] is None and found["level"] is None
    assert found["edge_energies"] is True and found["expanded"] == []
    assert found["drawer"] == {"open": False, "tab": "profile", "paths": []}
    assert "secret" not in found


def test_saving_is_quiet_for_other_tabs(open_client):
    start = get(open_client, "/live")["version"]
    save(open_client, {"energy_type": "E"})
    assert get(open_client, "/live")["version"] == start
