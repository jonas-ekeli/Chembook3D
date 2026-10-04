"""`chembook3d mcp`: the MCP server through which Claude Code or Claude Desktop works in the
investigation open in the running app (D91, FR-MCP).

It runs on the same computer as the app and talks to it over stdio with Claude and over HTTP
with the app at 127.0.0.1, so every change goes through the app's own rules and history, and
the app's tabs show it at once. It never opens the database itself.
"""

import argparse
import json
import time
from typing import Any
from urllib.parse import quote, urlsplit

import anyio
import httpx
from mcp import types
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server

from chembook3d import __version__
from chembook3d.api.live import AGENT, CLIENT_HEADER, DEFAULT_TIMEOUT, LOOPBACK
from chembook3d.mcp_tools import BY_NAME, TOOLS, ToolSpec, body_fields, description, input_schema

DEFAULT_URL = "http://127.0.0.1:8765"

INSTRUCTIONS = """\
Chembook3D is the user's local notebook of a computational chemistry mechanism investigation
(e.g. a catalytic cycle). These tools read and change the investigation open in the running
Chembook3D app on this computer. Every change goes through the app's own rules and history,
shows in the app at once, and is marked "claude" in its history.

How the notebook is organised:
- A node is one structure (an intermediate, a transition state, ...): coordinates, role
  (minimum, transition_state, unspecified), status, charge, multiplicity, notes, and the
  calculations imported on it. A node of kind "species" is a free species (a substrate or
  fragment) kept off the canvas; it joins or leaves on transitions so energies stay balanced.
- A reaction step is a stage of the mechanism (a column of the energy profile). It is not a
  transition: intermediates and TSs both belong to steps.
- A transition (edge) is a concrete connection between two nodes or groups. "direct" means no
  TS at either end ("no TS").
- A branch is a mechanistic alternative; its parents form the lineage. A group node stands for
  several nodes (e.g. conformers); its representative is the user's choice.
- Calculations are imported from Gaussian, ORCA, xTB or CREST outputs. Changing the
  coordinates of a node that has calculations makes a new derived node; the original keeps the
  geometry its calculations ran on.

Rules:
- Energies are compared only at one composite level of theory, the key
  "levelId~geometryLevelId" from get_energy_options (e.g. a QZ single point on a DZ geometry).
  Never mix levels or subtract energies from different levels. Energy types: E, H, G and G_qh
  (quasi-harmonic free energy). get_energy_view and get_energy_profile give hartree;
  get_energy_table gives the app's formatted values in kcal/mol (or the unit asked).
- Use the app's numbers (energy view, profiles, tables, selectivity and turnover results)
  rather than recomputing them from raw energies.
- Never choose pathways, branches, lineage or a group's representative from energies: ask the
  user (X5). The app never launches or monitors calculations (X1).
- "This node", "these", "the selected": call get_selection first.
- Import only files at paths the user gave you: import_file, check the plan, then
  commit_import.
- Deleting anything, dissolving a group and removing coordinates are asked in the app's
  window; the tool waits for the user's answer. Give a short reason. If they refuse, do not
  ask again.
"""


class AppNotRunning(Exception):
    pass


def check_url(url: str) -> str:
    """Only an app on this computer (NFR-SEC-01)."""
    parts = urlsplit(url)
    if parts.scheme != "http" or parts.hostname not in LOOPBACK:
        raise ValueError(f"{url}: the app must be on this computer (http://127.0.0.1:<port>)")
    return url.rstrip("/")


def _detail(response: httpx.Response) -> str:
    try:
        detail = response.json().get("detail", response.json())
    except (ValueError, AttributeError):
        return response.text or response.reason_phrase
    return detail if isinstance(detail, str) else json.dumps(detail, ensure_ascii=False)


def _text(text: str, error: bool = False) -> types.CallToolResult:
    return types.CallToolResult(content=[types.TextContent(type="text", text=text)], isError=error)


class Bridge:
    """Turns tool calls into requests to the running app."""

    def __init__(
        self,
        url: str = DEFAULT_URL,
        http: httpx.AsyncClient | None = None,
        confirm_timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        self.url = check_url(url)
        self.http = http or httpx.AsyncClient(
            base_url=self.url, timeout=httpx.Timeout(600.0, connect=5.0)
        )
        self.confirm_timeout = confirm_timeout

    async def request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        headers = {CLIENT_HEADER: AGENT}
        try:
            return await self.http.request(method, path, headers=headers, **kwargs)
        except httpx.ConnectError as exc:
            raise AppNotRunning from exc

    async def call(self, name: str, arguments: dict[str, Any]) -> types.CallToolResult:
        spec = BY_NAME.get(name)
        if spec is None:
            return _text(f"Unknown tool: {name}", error=True)
        try:
            if spec.kind == "confirm":
                return await self._ask(spec, arguments)
            return await self._call(spec, arguments)
        except AppNotRunning:
            return _text(
                f"Chembook3D is not running at {self.url}. Ask the user to start it "
                "(uv run chembook3d) and open the investigation, then try again.",
                error=True,
            )

    async def _call(self, spec: ToolSpec, arguments: dict[str, Any]) -> types.CallToolResult:
        if spec.name == "set_coordinates" and not str(arguments.get("xyz", "")).strip():
            return _text(
                "Empty coordinates would remove them: use remove_coordinates, which asks the "
                "user first.",
                error=True,
            )
        args = dict(arguments)
        path = spec.path
        for name in _path_params(spec.path):
            value = args.pop(name, None)
            if not isinstance(value, str) or not value:
                return _text(f"'{name}' is required", error=True)
            path = path.replace("{" + name + "}", quote(value, safe=""))
        fields = body_fields(spec)
        body = None
        if fields is not None:
            body = {k: args.pop(k) for k in list(args) if k in fields}
        params = {k: v for k, v in args.items() if v is not None}
        response = await self.request(spec.method, path, params=params or None, json=body)
        if not response.is_success:
            return _text(f"The app refused ({response.status_code}): {_detail(response)}", True)
        if response.status_code == 204 or not response.content:
            return _text("Done.")
        if "json" in response.headers.get("content-type", ""):
            return _text(json.dumps(response.json(), ensure_ascii=False, indent=1))
        return _text(response.text)

    async def _ask(self, spec: ToolSpec, arguments: dict[str, Any]) -> types.CallToolResult:
        """D91: the app shows the request; only the user's Confirm there does it."""
        args = dict(arguments)
        reason = str(args.pop("reason", "") or "")
        response = await self.request(
            "POST",
            "/api/confirmations",
            json={
                "action": spec.name,
                "params": args,
                "reason": reason,
                "timeout": self.confirm_timeout,
            },
        )
        if not response.is_success:
            return _text(f"The app refused ({response.status_code}): {_detail(response)}", True)
        asked = response.json()
        deadline = time.monotonic() + self.confirm_timeout + 30
        found = asked
        while found["status"] in ("pending", "running") and time.monotonic() < deadline:
            response = await self.request(
                "GET", f"/api/confirmations/{asked['id']}", params={"wait": 25}
            )
            if not response.is_success:
                return _text(f"The app lost the request: {_detail(response)}", error=True)
            found = response.json()
        summary = asked["summary"]
        if found["status"] == "done":
            result = found.get("result")
            text = f"The user confirmed; done: {summary}."
            if result:
                text += "\n" + json.dumps(result, ensure_ascii=False, indent=1)
            return _text(text)
        if found["status"] == "refused":
            return _text(f"The user refused, so nothing was changed: {summary}.")
        if found["status"] == "failed":
            return _text(f"The user confirmed, but the app refused: {found['error']}", True)
        return _text(
            f"No answer in the app, so nothing was changed: {summary}. The request is shown in "
            "the Chembook3D tab; ask the user whether it is open.",
            error=True,
        )


def _path_params(path: str) -> list[str]:
    return [part[1:-1] for part in path.split("/") if part.startswith("{")]


def build_server(bridge: Bridge) -> Server:
    server: Server = Server("chembook3d", version=__version__, instructions=INSTRUCTIONS)
    tools = [
        types.Tool(
            name=spec.name,
            description=description(spec),
            inputSchema=input_schema(spec),
            annotations=types.ToolAnnotations(
                readOnlyHint=spec.kind == "read",
                destructiveHint=spec.kind == "confirm",
                openWorldHint=False,
            ),
        )
        for spec in TOOLS
    ]

    @server.list_tools()
    async def list_tools() -> list[types.Tool]:
        return tools

    @server.call_tool()
    async def call_tool(name: str, arguments: dict[str, Any]) -> types.CallToolResult:
        return await bridge.call(name, arguments)

    return server


async def serve(url: str, confirm_timeout: float) -> None:
    bridge = Bridge(url, confirm_timeout=confirm_timeout)
    server = build_server(bridge)
    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="chembook3d mcp",
        description="MCP server (stdio) for Claude Code or Claude Desktop, working in the "
        "investigation open in the running Chembook3D app.",
    )
    parser.add_argument("--url", default=DEFAULT_URL, help=f"the app's address ({DEFAULT_URL})")
    parser.add_argument(
        "--confirm-timeout",
        type=float,
        default=DEFAULT_TIMEOUT,
        help="seconds a delete waits for the user's answer in the app (default %(default)s)",
    )
    args = parser.parse_args(argv)
    try:
        url = check_url(args.url)
    except ValueError as exc:
        parser.error(str(exc))
    anyio.run(serve, url, args.confirm_timeout)


if __name__ == "__main__":
    main()
