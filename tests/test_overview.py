"""The resume overview (FR-OV-01, WF-10, D45, D53) on the four-branch example (T-UI-01's data;
the clicking is checked in frontend/e2e/phase5.spec.ts)."""

from datetime import date, timedelta

from tests.test_pathway import STEP_NAMES, Example, edge, example, get, node, patch  # noqa: F401


def overview(client, since: str | None = None) -> dict:
    return get(client, f"/overview?since={since}" if since else "/overview")


def items(found: dict, reason: str) -> list[str]:
    return [i["label"] for i in found["open_items"] if i["reason"] == reason]


def test_branch_status_and_counts(open_client, example):  # noqa: F811
    patch(open_client, f"/branches/{example.branches['A1']}", {"status": "done"})
    for label in ("A1-S1", "A1-S2"):
        patch(open_client, f"/nodes/{example.nodes[label]}", {"status": "done"})
    patch(open_client, f"/nodes/{example.nodes['A1-S3']}", {"status": "failed"})
    found = overview(open_client)
    by_name = {b["name"]: b for b in found["branches"]}
    assert set(by_name) == {"T", "A", "B", "A1", "A2", "B1", "B2"}
    a1 = by_name["A1"]
    assert a1["status"] == "done" and a1["node_count"] == 6
    assert a1["counts"] == {"done": 2, "failed": 1, "planned": 3}
    assert by_name["T"]["counts"] == {"planned": 1}


def test_open_items(open_client, example):  # noqa: F811
    ids = example.nodes
    patch(open_client, f"/nodes/{ids['A1-S3']}", {"status": "failed"})
    patch(open_client, f"/nodes/{ids['B2-S1']}", {"status": "running_externally"})
    for label, node_id in ids.items():
        if label not in ("A1-S3", "B2-S1"):
            patch(open_client, f"/nodes/{node_id}", {"status": "done"})
    # A direct connection, intermediate to intermediate (D53).
    direct = edge(open_client, ids["A1-S1"], ids["A1-S3"])
    patch(open_client, f"/transitions/{direct['id']}", {"status": "done"})
    found = overview(open_client)

    assert items(found, "failed") == ["A1-S3"]
    assert items(found, "running_externally") == ["B2-S1"]
    # Every transition is still planned; the direct one is listed as "no TS" with its branch.
    assert "A1-S1 → A1-S2" in items(found, "planned")
    assert "A1-S1 → A1-S3" not in items(found, "planned")
    (no_ts,) = [
        i for i in found["open_items"] if i["reason"] == "direct" and i["id"] == direct["id"]
    ]
    assert no_ts["detail"] == "no TS" and no_ts["branch_ids"] == [example.branches["A1"]]
    # Failed items come first.
    assert found["open_items"][0]["reason"] == "failed"


def test_nodes_with_warnings_are_open_items(open_client):
    created = node(open_client, label="Guess", role="minimum")
    patch(open_client, f"/nodes/{created['id']}", {"tags": ["optimization-incomplete"]})
    (warning,) = [i for i in overview(open_client)["open_items"] if i["reason"] == "warning"]
    assert (warning["label"], warning["detail"]) == ("Guess", "W-OPT-INC")
    assert overview(open_client)["branches"][-1]["name"] == "No branch"


def test_recent_changes_and_step_notes(open_client, example):  # noqa: F811
    patch(open_client, f"/steps/{example.steps[2]}", {"notes": "Check the TS conformers"})
    found = overview(open_client)
    assert found["recent"][0]["record_type"] == "step" and found["recent"][0]["field"] == "notes"
    assert [s["name"] for s in found["steps"]] == STEP_NAMES
    assert found["steps"][2]["notes"] == "Check the TS conformers"
    # Filterable by date: nothing was changed after tomorrow.
    tomorrow = (date.today() + timedelta(days=2)).isoformat()
    assert overview(open_client, tomorrow)["recent"] == []
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    assert len(overview(open_client, yesterday)["recent"]) == 50
