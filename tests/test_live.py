"""D91: the change counter open tabs wait on, the reported selection, and the confirmations
Claude needs from the user before anything is deleted (T-MCP-01…05)."""

import json
from types import SimpleNamespace
from urllib.parse import quote

import pytest
from fastapi import HTTPException, Request
from starlette.middleware.exceptions import ExceptionMiddleware

from chembook3d.api.identity import check_investigation_identity
from chembook3d.api.live import ChangeTracker, LiveState, SelectionIn, report_selection
from chembook3d.api.routes import _with_investigation
from chembook3d.app import create_app
from chembook3d.investigation import InvestigationError
from tests.conftest import WATER

ORIGIN = {"Origin": "http://127.0.0.1:8765"}
CLAUDE = {"X-Chembook-Client": "claude"}


def identity_request(path, expected, actual, method="POST"):
    headers = [] if expected is None else [(b"x-chembook-investigation", expected.encode())]
    app = SimpleNamespace(
        state=SimpleNamespace(
            investigation=None if actual is None else SimpleNamespace(folder=actual)
        )
    )
    return Request(
        {
            "type": "http",
            "method": method,
            "path": path,
            "headers": headers,
            "app": app,
            "query_string": b"",
        }
    )


@pytest.mark.parametrize(
    "path",
    [
        "nodes",
        "view-state",
        "selection",
        "settings",
        "investigations/close",
        "energies/table.csv",
        "nodes/shared-id/xyz",
        "source-files/shared-id/download",
        "custom-bases/shared-id/file",
        "turnovers/shared-id/table.csv",
        "note-images/shared-id",
    ],
)
@pytest.mark.parametrize("transport", ["header", "query"])
def test_stale_tab_request_never_reaches_handler(path, transport):
    """Exercise the real ASGI middleware without opening an OS event loop/socket pair."""
    request = identity_request(f"/api/{path}", quote("A"), "B")
    if transport == "query":
        request.scope["headers"] = []
        request.scope["query_string"] = b"expected_investigation=A"
    sent = []

    async def handler(scope, receive, send):
        pytest.fail("A stale tab reached the handler for B")

    async def send(message):
        sent.append(message)

    async def receive():
        return {"type": "http.request", "body": b""}

    coroutine = ChangeTracker(handler, LiveState())(request.scope, receive, send)
    with pytest.raises(StopIteration):
        coroutine.send(None)
    assert sent[0]["status"] == 409
    assert "investigation changed" in json.loads(sent[1]["body"])["detail"]


@pytest.mark.parametrize(
    "expected,actual", [(None, "B"), ("", None), (quote("C:/β notebook"), "C:/β notebook")]
)
def test_identity_header_accepts_matching_and_legacy_clients(expected, actual):
    check_investigation_identity(identity_request("/api/nodes", expected, actual))


def test_identity_rechecked_after_waiting_for_a_worker():
    request = identity_request("/api/nodes", "A", "A")
    check_investigation_identity(request)
    request.app.state.investigation = SimpleNamespace(folder="B")
    with pytest.raises(HTTPException) as error:
        check_investigation_identity(request)
    assert error.value.status_code == 409


def test_settings_recheck_the_captured_investigation_after_worker_dispatch():
    request = identity_request("/api/settings", "A", "A", method="PUT")
    check_investigation_identity(request)
    request.app.state.investigation = SimpleNamespace(folder="B")
    with pytest.raises(HTTPException) as error:
        _with_investigation(request, lambda session: pytest.fail("Stale settings targeted B"))
    assert error.value.status_code == 409


def test_identity_can_validate_the_captured_object_including_none():
    request = identity_request("/api/nodes", "A", "B")
    check_investigation_identity(request, SimpleNamespace(folder="A"))
    with pytest.raises(HTTPException):
        check_investigation_identity(request, None)


def test_selection_rechecks_the_captured_investigation_after_dispatch():
    request = identity_request("/api/selection", "A", "A", method="PUT")
    check_investigation_identity(request)
    request.app.state.live = LiveState()
    request.app.state.investigation = SimpleNamespace(folder="B")
    with pytest.raises(HTTPException) as error:
        report_selection(SelectionIn(nodes=["shared-id"]), request)
    assert error.value.status_code == 409
    assert request.app.state.live.selection is None


def test_query_identity_decoded_once_and_header_takes_precedence():
    request = identity_request("/api/nodes/shared/xyz", None, "C:/A%20B")
    request.scope["query_string"] = b"expected_investigation=C%3A%2FA%2520B"
    check_investigation_identity(request)
    request.scope["headers"] = [(b"x-chembook-investigation", b"C%3A%2FA%2520B")]
    request.scope["query_string"] = b"expected_investigation=wrong"
    check_investigation_identity(Request(request.scope))


def test_closing_session_admission_has_a_controlled_http_conflict():
    app = create_app()
    request = identity_request("/api/nodes", "A", "A")
    sent = []

    async def handler(scope, receive, send):
        raise InvestigationError("the investigation is closing or closed; open it again")

    async def send(message):
        sent.append(message)

    async def receive():
        return {"type": "http.request", "body": b""}

    middleware = ExceptionMiddleware(handler, handlers=app.exception_handlers)
    coroutine = middleware(request.scope, receive, send)
    with pytest.raises(StopIteration):
        coroutine.send(None)
    assert sent[0]["status"] == 409
    assert "closing or closed" in json.loads(sent[1]["body"])["detail"]


@pytest.mark.parametrize(
    "path",
    ["live", "presence", "investigation", "folders", "investigations/open", "investigations/clone"],
)
def test_stale_tab_can_discover_or_explicitly_open_identity(path):
    request = identity_request(f"/api/{path}", "A", "B")
    reached = []

    async def handler(scope, receive, send):
        reached.append(scope["path"])

    async def send(message):
        pass

    async def receive():
        return {"type": "http.request", "body": b""}

    coroutine = ChangeTracker(handler, LiveState())(request.scope, receive, send)
    with pytest.raises(StopIteration):
        coroutine.send(None)
    assert reached == [f"/api/{path}"]


def live(test_client, **params):
    response = test_client.get("/api/live", params=params)
    assert response.status_code == 200, response.text
    return response.json()


def node(client, headers=None, **fields):
    response = client.post("/api/nodes", json=fields, headers=headers or {})
    assert response.status_code == 201, response.text
    return response.json()


def ask(client, action, **params):
    return client.post("/api/confirmations", json={"action": action, "params": params})


def test_changes_by_other_clients_are_reported(open_client):
    start = live(open_client)["version"]
    node(open_client, {"X-Chembook-Client": "tab-1"}, label="A")
    mine = live(open_client, since=start, client="tab-1")
    assert mine["version"] == start + 1 and mine["changed"] is False
    theirs = live(open_client, since=start, client="tab-2")
    assert theirs["changed"] is True
    assert live(open_client, since=theirs["version"], client="tab-2")["changed"] is False


def test_reads_failures_and_quiet_requests_do_not_count(open_client):
    start = live(open_client)["version"]
    open_client.get("/api/nodes")
    open_client.put("/api/selection", json={"nodes": []})
    open_client.post("/api/pathways/extend", json={"path": []})
    assert open_client.patch("/api/nodes/missing", json={"label": "x"}).status_code == 404
    assert live(open_client)["version"] == start


def test_long_poll_returns_after_the_wait(open_client):
    answer = live(open_client, since=live(open_client)["version"], client="tab", wait=0.3)
    assert answer["changed"] is False


def test_claude_changes_are_marked_in_the_history(open_client):
    made = node(open_client, CLAUDE, label="by Claude")
    open_client.patch(f"/api/nodes/{made['id']}", json={"label": "by me"})
    entries = open_client.get("/api/history", params={"record_id": made["id"]}).json()
    assert [e["source"] for e in entries] == ["manual", "claude"]


def test_selection_is_read_with_names(open_client):
    assert open_client.get("/api/selection").json()["reported"] is False
    a = node(open_client, label="TS1")
    b = node(open_client, label="INT2")
    edge = open_client.post("/api/transitions", json={"source_id": a["id"], "target_id": b["id"]})
    report = {
        "nodes": [a["id"], b["id"]],
        "transitions": [edge.json()["id"]],
        "level": "x~y",
        "energy_type": "G_qh",
        "reference_id": b["id"],
    }
    assert open_client.put("/api/selection", json=report).status_code == 204
    found = open_client.get("/api/selection").json()
    assert [n["label"] for n in found["nodes"]] == ["TS1", "INT2"]
    assert found["transitions"][0]["label"] == "TS1 → INT2"
    assert found["energy_view"] == {
        "level": "x~y",
        "type": "G_qh",
        "reference": {"id": b["id"], "label": "INT2"},
    }
    open_client.delete(f"/api/nodes/{a['id']}")
    assert [n["label"] for n in open_client.get("/api/selection").json()["nodes"]] == ["INT2"]


def test_a_delete_waits_for_the_users_confirm(open_client):
    a = node(open_client, label="TS1-2", xyz=WATER)
    b = node(open_client, label="INT2")
    open_client.post("/api/transitions", json={"source_id": a["id"], "target_id": b["id"]})
    before = live(open_client)
    asked = ask(open_client, "delete_node", node_id=a["id"])
    assert asked.status_code == 201, asked.text
    request = asked.json()
    assert request["summary"] == "Delete node “TS1-2” with 1 edge"
    assert request["status"] == "pending"
    waiting = live(open_client, confirmations=before["confirm_version"])
    assert [c["id"] for c in waiting["confirmations"]] == [request["id"]]
    assert open_client.get(f"/api/nodes/{a['id']}").status_code == 200

    url = f"/api/confirmations/{request['id']}/answer"
    # Not from a page (the MCP server, or anything else without the page's origin): refused.
    assert open_client.post(url, json={"confirm": True}).status_code == 403
    done = open_client.post(url, json={"confirm": True}, headers=ORIGIN).json()
    assert done["status"] == "done"
    assert done["result"]["transitions"][0]["target_id"] == b["id"]
    assert open_client.get(f"/api/nodes/{a['id']}").status_code == 404
    assert open_client.get(f"/api/confirmations/{request['id']}").json()["status"] == "done"
    assert open_client.post(url, json={"confirm": True}, headers=ORIGIN).status_code == 409
    deleted = [e for e in open_client.get("/api/history").json() if e["action"] == "delete"]
    assert deleted[0]["source"] == "claude"
    assert live(open_client)["confirmations"] == []


def test_refused_and_expired_requests_change_nothing(open_client):
    a = node(open_client, label="A")
    refused = ask(open_client, "delete_node", node_id=a["id"]).json()
    url = f"/api/confirmations/{refused['id']}/answer"
    assert open_client.post(url, json={"confirm": False}, headers=ORIGIN).json()["status"] == (
        "refused"
    )
    expired = ask(open_client, "delete_node", node_id=a["id"]).json()
    live_state = open_client.app.state.live
    live_state.confirmations[expired["id"]].expires = 0
    found = open_client.get(f"/api/confirmations/{expired['id']}", params={"wait": 1}).json()
    assert found["status"] == "expired"
    late = f"/api/confirmations/{expired['id']}/answer"
    assert open_client.post(late, json={"confirm": True}, headers=ORIGIN).status_code == 409
    assert open_client.get(f"/api/nodes/{a['id']}").status_code == 200


def test_requests_are_checked_and_described(open_client):
    a = node(open_client, label="A")
    assert ask(open_client, "delete_everything").status_code == 422
    assert ask(open_client, "delete_node", node_id="../investigations").status_code == 422
    assert ask(open_client, "delete_node", node_id="missing").status_code == 404
    assert "has no coordinates" in ask(open_client, "remove_coordinates", node_id=a["id"]).text

    b = node(open_client, label="B", xyz=WATER)
    asked = ask(open_client, "remove_coordinates", node_id=b["id"]).json()
    assert asked["summary"].startswith("Remove the coordinates of node “B” (3 atoms)")
    url = f"/api/confirmations/{asked['id']}/answer"
    open_client.post(url, json={"confirm": True}, headers=ORIGIN)
    assert open_client.get(f"/api/nodes/{b['id']}").json()["xyz"] is None

    step = open_client.post("/api/steps", json={"name": "Cycloaddition"}).json()
    open_client.patch(f"/api/nodes/{a['id']}", json={"step_id": step["id"]})
    assert ask(open_client, "delete_step", step_id=step["id"]).json()["summary"] == (
        "Delete reaction step “Cycloaddition”; 1 node will have no step"
    )
    group = open_client.post("/api/groups/reconnect", json={"member_ids": [a["id"], b["id"]]})
    group_id = group.json()["id"]
    assert ask(open_client, "dissolve_group", group_id=group_id).json()["summary"] == (
        "Dissolve group “Group”: its 2 members stay as nodes"
    )
    assert ask(open_client, "delete_group", group_id=group_id).json()["summary"] == (
        "Delete group “Group” with its contents: 2 members, 0 calculations and 0 edges"
    )


def test_requests_expire_when_the_investigation_closes(open_client, tmp_path):
    a = node(open_client, label="A")
    asked = ask(open_client, "delete_node", node_id=a["id"]).json()
    open_client.post("/api/investigations/close")
    assert open_client.get(f"/api/confirmations/{asked['id']}").json()["status"] == "expired"
