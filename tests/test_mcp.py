"""D91: `chembook3d mcp`, the MCP server through which Claude works in the open investigation
(T-MCP-06…10)."""

import os
import re
import socket
import sys
import threading
import time
from pathlib import Path

import anyio
import httpx
import pytest
import uvicorn
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from chembook3d.api.live import ACTIONS
from chembook3d.app import create_app
from chembook3d.mcp_server import Bridge, build_server, check_url
from chembook3d.mcp_tools import BY_NAME, READ_ONLY_TOOLS, TOOLS, input_schema, openapi
from tests.conftest import WATER

ORIGIN = {"Origin": "http://127.0.0.1:8765"}

# Requests that compute without changing anything; read tools may use them.
READ_POSTS = {
    "/api/pathways/extend",
    "/api/energies/profile",
    "/api/energies/table",
    "/api/overlay",
}
# Changes that delete nothing and are undone at once, so they do not ask (A38).
UNASKED_DELETES = {
    "/api/groups/{group_id}/members/{node_id}",
    "/api/transitions/{transition_id}/species/{species_id}",
    "/api/imports/{token}",
}
# Never offered to Claude (D91).
NOT_OFFERED = re.compile(
    r"^/api/(investigations?(/.*)?|sync(/.*)?|folders|settings|snapshot"
    r"|source-files/[^/]+/(open|download)|imports|note-images(/.*)?|health"
    r"|live|selection$|confirmations(/.*)?|nodes/[^/]+/delete-preview"
    r"|groups/[^/]+/delete-preview|steric-profiles/[^/]+/(difference|table\.csv)"
    r"|energies/table\.csv|turnovers/[^/]+/table\.csv)$"
)


def test_every_tool_has_a_schema_and_matches_the_api():
    names = [t.name for t in TOOLS]
    assert len(names) == len(set(names))
    for spec in TOOLS:
        schema = input_schema(spec)
        assert schema["type"] == "object"
        assert "$ref" not in str(schema), spec.name
        assert spec.description, spec.name
        if spec.kind == "read":
            assert spec.method == "GET" or spec.path in READ_POSTS, spec.name
        if spec.kind == "confirm":
            assert spec.name in ACTIONS and "reason" in schema["properties"]


def test_every_route_is_a_tool_or_deliberately_left_out():
    offered = {(t.method, t.path) for t in TOOLS}
    for path, operations in openapi()["paths"].items():
        for method in operations:
            if (method.upper(), path) in offered or NOT_OFFERED.match(path):
                continue
            pytest.fail(f"{method.upper()} {path} is neither a tool nor left out on purpose")


def test_every_delete_asks_the_user():
    for spec in TOOLS:
        if spec.method == "DELETE" and spec.kind != "confirm":
            assert spec.path in UNASKED_DELETES, spec.name
    assert BY_NAME["remove_coordinates"].kind == "confirm"
    assert "get_node" in READ_ONLY_TOOLS and "update_node" not in READ_ONLY_TOOLS
    assert not READ_ONLY_TOOLS & {t.name for t in TOOLS if t.kind != "read"}
    assert BY_NAME["dissolve_group"].kind == "confirm"


def test_only_an_app_on_this_computer():
    assert check_url("http://127.0.0.1:8765/") == "http://127.0.0.1:8765"
    assert check_url("http://localhost:9000") == "http://localhost:9000"
    for url in ("http://192.168.1.4:8765", "https://example.org", "file:///tmp/x"):
        with pytest.raises(ValueError):
            check_url(url)


def _app_bridge(**kwargs) -> tuple[Bridge, httpx.AsyncClient]:
    app = create_app()
    page = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=ORIGIN["Origin"])
    claude = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=ORIGIN["Origin"])
    return Bridge(ORIGIN["Origin"], http=claude, **kwargs), page


def _json(result):
    import json

    assert not result.isError, result.content[0].text
    return json.loads(result.content[0].text)


def test_tools_work_through_the_app(tmp_path):
    async def run():
        bridge, page = _app_bridge()
        closed = await bridge.call("list_nodes", {})
        assert closed.isError and "No investigation is open" in closed.content[0].text
        await page.post("/api/investigations", json={"folder": str(tmp_path / "i"), "name": "T"})

        made = _json(await bridge.call("create_node", {"label": "INT1", "xyz": WATER}))
        assert made["formula"] == "H2O"
        renamed = _json(await bridge.call("update_node", {"node_id": made["id"], "label": "A"}))
        assert renamed["label"] == "A"
        xyz = await bridge.call("get_node_xyz", {"node_id": made["id"]})
        assert xyz.content[0].text.startswith("3\n")
        empty = await bridge.call("set_coordinates", {"node_id": made["id"], "xyz": " "})
        assert empty.isError and "remove_coordinates" in empty.content[0].text
        refused = await bridge.call("update_node", {"node_id": made["id"], "role": "nonsense"})
        assert refused.isError and "The app refused (422)" in refused.content[0].text

        history = _json(await bridge.call("get_history", {"record_id": made["id"]}))
        assert {e["source"] for e in history} == {"claude"}

        await page.put("/api/selection", json={"nodes": [made["id"]]})
        selected = _json(await bridge.call("get_selection", {}))
        assert selected["nodes"][0]["label"] == "A"

        version = (await page.get("/api/live")).json()["version"]
        assert version >= 2  # the page's tabs are told to reload

    anyio.run(run)


def test_a_delete_waits_for_the_users_answer(tmp_path):
    async def run():
        bridge, page = _app_bridge()
        await page.post("/api/investigations", json={"folder": str(tmp_path / "i"), "name": "T"})
        made = _json(await bridge.call("create_node", {"label": "TS1-2"}))
        answers = {}

        async def ask(key, confirm):
            async def answer():
                while not (pending := (await page.get("/api/live")).json()["confirmations"]):
                    await anyio.sleep(0.05)
                assert pending[0]["reason"] == "a duplicate"
                url = f"/api/confirmations/{pending[0]['id']}/answer"
                await page.post(url, json={"confirm": confirm}, headers=ORIGIN)

            async with anyio.create_task_group() as tasks:
                tasks.start_soon(answer)
                answers[key] = await bridge.call(
                    "delete_node", {"node_id": made["id"], "reason": "a duplicate"}
                )

        await ask("no", False)
        assert "refused" in answers["no"].content[0].text
        assert (await page.get(f"/api/nodes/{made['id']}")).status_code == 200
        await ask("yes", True)
        assert answers["yes"].content[0].text.startswith("The user confirmed; done")
        assert (await page.get(f"/api/nodes/{made['id']}")).status_code == 404

    anyio.run(run)


def test_an_unanswered_delete_changes_nothing(tmp_path):
    async def run():
        bridge, page = _app_bridge(confirm_timeout=5)
        await page.post("/api/investigations", json={"folder": str(tmp_path / "i"), "name": "T"})
        made = _json(await bridge.call("create_node", {"label": "A"}))
        result = await bridge.call("delete_node", {"node_id": made["id"]})
        assert result.isError and "No answer in the app" in result.content[0].text
        assert (await page.get(f"/api/nodes/{made['id']}")).status_code == 200

    anyio.run(run)


def test_the_app_not_running_is_said_plainly():
    async def run():
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
        result = await Bridge(f"http://127.0.0.1:{port}").call("get_overview", {})
        assert result.isError and "not running" in result.content[0].text

    anyio.run(run)


def test_the_server_lists_its_tools_with_hints():
    async def run():
        server = build_server(Bridge())
        from mcp import types

        handler = server.request_handlers[types.ListToolsRequest]
        listed = (await handler(None)).root.tools
        hints = {t.name: t.annotations for t in listed}
        assert hints["get_node"].readOnlyHint and not hints["get_node"].destructiveHint
        assert hints["delete_node"].destructiveHint
        assert server.instructions and "levelId~geometryLevelId" in server.instructions

    anyio.run(run)


@pytest.fixture
def running_app(tmp_path):
    """The real app on a free port, as `chembook3d` runs it."""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    server = uvicorn.Server(
        uvicorn.Config(create_app(), host="127.0.0.1", port=port, log_level="error")
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{port}"
    for _ in range(400):  # a slow CI runner
        if server.started:
            break
        time.sleep(0.05)
    assert server.started
    httpx.post(f"{url}/api/investigations", json={"folder": str(tmp_path / "i"), "name": "Demo"})
    yield url
    server.should_exit = True
    thread.join(timeout=10)


def test_claude_talks_to_the_running_app_over_stdio(running_app):
    """T-MCP-10: as Claude Code starts it, `chembook3d mcp --url …`, over stdio."""
    root = Path(__file__).resolve().parents[1]
    parameters = StdioServerParameters(
        command=sys.executable,
        args=["-m", "chembook3d.cli", "mcp", "--url", running_app],
        cwd=str(root),
        env={**os.environ, "PYTHONPATH": str(root / "src")},
    )

    async def run():
        async with stdio_client(parameters) as (read, write), ClientSession(read, write) as client:
            started = await client.initialize()
            assert started.serverInfo.name == "chembook3d"
            assert "X5" in (started.instructions or "")
            tools = {t.name for t in (await client.list_tools()).tools}
            assert {"get_selection", "create_node", "delete_node", "import_file"} <= tools
            assert "open_investigation" not in tools
            result = await client.call_tool("get_investigation", {})
            assert '"name": "Demo"' in result.content[0].text
            created = await client.call_tool("create_node", {"label": "from Claude"})
            assert not created.isError
            invalid = await client.call_tool("create_node", {"charge": "two"})
            assert invalid.isError

    anyio.run(run)
    nodes = httpx.get(f"{running_app}/api/nodes").json()
    assert [n["label"] for n in nodes] == ["from Claude"]
