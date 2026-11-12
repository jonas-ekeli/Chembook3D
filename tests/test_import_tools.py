"""D225: Claude's import tools (T-MCP-30): a folder on this computer listed and imported at
once, an output read without importing it, and what the panel still asks about. The import
from a server is in tests/test_remote.py (T-SSH-27)."""

import anyio

from chembook3d import claude_panel
from chembook3d.claude_panel import NOTEBOOK_GUIDE
from chembook3d.mcp_server import INSTRUCTIONS
from chembook3d.mcp_tools import BY_NAME, PANEL_ALLOWED, READ_ONLY_TOOLS, input_schema
from tests.test_import import FIXTURES
from tests.test_mcp import _app_bridge, _json

TS = "aminationTS-full-unfrz-c1.log"
TS_SP = "aminationTS-full-unfrz-c1_sp_tzpop.log"
CLAUDE = {"X-Chembook-Client": "claude"}


def _outputs(folder):
    folder.mkdir()
    for name in (TS, TS_SP):
        (folder / name).write_bytes((FIXTURES / name).read_bytes())
    (folder / "job.gjf").write_text("#P B3LYP/6-31G(d) Opt\n\njob\n\n0 1\n", encoding="utf-8")
    (folder / "notes.txt").write_text("not an output\n", encoding="utf-8")
    return folder


def test_the_import_tools_and_what_the_panel_asks_about():
    """T-MCP-30: reading a file is unasked; listing a folder is a read the panel still asks
    about, allowed only while Claude works on its own; staging and importing ask."""
    assert {"summarize_file", "get_file_structure"} <= PANEL_ALLOWED
    assert "list_folder" in READ_ONLY_TOOLS and "list_folder" not in PANEL_ALLOWED
    assert "list_folder" in claude_panel.allowed_alone(cloud=False)
    for name in (
        "import_folder",
        "preview_folder_import",
        "commit_folder_import",
        "cancel_folder_import",
        "import_server_files",
    ):
        assert BY_NAME[name].kind == "write", name
    rows = input_schema(BY_NAME["preview_folder_import"])["properties"]["rows"]
    assert "node:<node id>" in rows["description"]
    server = input_schema(BY_NAME["import_server_files"])
    assert set(server["required"]) == {"server_id", "paths"}
    for text in (INSTRUCTIONS, NOTEBOOK_GUIDE):
        for name in ("import_folder", "summarize_file", "import_server_files", "list_folder"):
            assert name in text, name


def test_an_output_on_this_computer_is_read_without_importing_it(open_client, tmp_path):
    """T-MCP-30: the Inspect window's reading of a local output and one of its structures;
    nothing is staged or imported, and a file that is no output is refused."""
    folder = _outputs(tmp_path / "outputs")
    found = open_client.get("/api/local-files/summary", params={"path": str(folder / TS)})
    assert found.status_code == 200, found.text
    found = found.json()
    assert found["path"] == str((folder / TS).resolve()) and found["program"] == "Gaussian"
    steps = found["steps"]
    assert [s["job_type"] for s in steps] == ["ts_optimization", "frequency"]
    assert steps[0]["optimization_converged"] is True and steps[1]["imaginary"] == 1
    assert steps[0]["structure_list"][-1]["converged"] is True

    last = open_client.get("/api/local-files/structure", params={"path": str(folder / TS)})
    head = last.text.splitlines()
    assert int(head[0]) == steps[-1]["atoms"] and " step 2 structure 1 of 1 E=-" in head[1]
    first = {"path": str(folder / TS), "step": 1, "frame": 1}
    assert (
        " step 1 structure 1 of "
        in open_client.get("/api/local-files/structure", params=first).text.splitlines()[1]
    )
    bad = {"path": str(folder / TS), "step": 9}
    assert open_client.get("/api/local-files/structure", params=bad).status_code == 422

    for path in (folder / "notes.txt", folder / "missing.log", folder):
        refused = open_client.get("/api/local-files/summary", params={"path": str(path)})
        assert refused.status_code == 422, path
    foreign = {"origin": "https://example.com"}
    refused = open_client.get(
        "/api/local-files/summary", params={"path": str(folder / TS)}, headers=foreign
    )
    assert refused.status_code == 403
    assert open_client.get("/api/canvas").json()["nodes"] == []


def test_claude_lists_and_imports_a_folder(tmp_path):
    """T-MCP-30: through the MCP server: the folder's files, the plan of all its outputs, a
    choice, the import, and the files marked as Claude's in the history."""

    async def run():
        bridge, page = _app_bridge()
        await page.post("/api/investigations", json={"folder": str(tmp_path / "i"), "name": "T"})
        folder = _outputs(tmp_path / "outputs")

        listed = _json(await bridge.call("list_folder", {"path": str(folder), "files": True}))
        names = {e["name"] for e in listed["entries"] if e["is_file"]}
        assert names == {TS, TS_SP, "job.gjf", "notes.txt"}

        plan = _json(await bridge.call("import_folder", {"folder": str(folder)}))
        rows = {r["path"]: r for r in plan["rows"]}
        assert set(rows) == {TS, TS_SP}
        assert (await page.get("/api/canvas")).json()["nodes"] == []  # nothing imported yet

        choice = {"token": plan["token"], "rows": {rows[TS_SP]["id"]: {"included": False}}}
        again = _json(await bridge.call("preview_folder_import", choice))
        assert {r["path"]: r["included"] for r in again["rows"]} == {TS: True, TS_SP: False}
        done = _json(await bridge.call("commit_folder_import", choice))
        assert done["imported"] == 1
        nodes = (await page.get("/api/canvas")).json()["nodes"]
        assert len(nodes) == 1
        history = (await page.get("/api/history")).json()
        assert {e["source"] for e in history if e["record_id"] == nodes[0]["id"]} <= {
            "claude",
            "import",
        }

        other = _json(await bridge.call("import_folder", {"folder": str(folder)}))
        dropped = await bridge.call("cancel_folder_import", {"token": other["token"]})
        assert not dropped.isError
        gone = await bridge.call("preview_folder_import", {"token": other["token"]})
        assert gone.isError and "expired" in gone.content[0].text

    anyio.run(run)
