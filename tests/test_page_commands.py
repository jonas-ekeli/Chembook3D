"""D226: Claude asks the open page to do what only the page can (Tidy, open groups, show a
record, change the view), and sends a scan path as the dialog's Send does (T-MCP-31, 32)."""

import anyio

from chembook3d import claude_panel, sync
from chembook3d.api import page as page_api
from chembook3d.api import snapshot as snapshot_api
from chembook3d.mcp_tools import BY_NAME, PANEL_ALLOWED, READ_ONLY_TOOLS, input_schema
from tests.conftest import WATER
from tests.test_mcp import ORIGIN, _app_bridge, _json

VIEW_TOOLS = {"open_groups", "show_in_app", "open_canvas", "set_view"}
EXPORT_TOOLS = {"save_canvas_image", "save_profile_image", "save_read_only_copy"}
PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 32
TURNED = "3\nturned\nO 0.0 0.0 0.1173\nH 0.0 0.70 -0.52\nH 0.0 -0.7572 -0.4692\n"


def test_the_page_tools_and_which_of_them_ask():
    # T-MCP-31: tools that change only what the page shows are allowed in the panel without a
    # question; a Tidy moves the cards and sending a scan path starts a cloud session, so
    # those ask (and the second stays asked while Claude works on its own without the tick).
    asked = {"tidy_canvas", "undo_tidy", "send_scan_path", *EXPORT_TOOLS}
    for name in VIEW_TOOLS | asked:
        assert BY_NAME[name].kind == "write" and name not in READ_ONLY_TOOLS
    assert VIEW_TOOLS <= PANEL_ALLOWED
    assert not asked & PANEL_ALLOWED
    assert "send_scan_path" in claude_panel.CLOUD_TOOLS
    assert "send_scan_path" not in claude_panel.allowed_alone(cloud=False)
    assert {"tidy_canvas", "send_scan_path"} <= set(claude_panel.allowed_alone(cloud=True))
    assert input_schema(BY_NAME["tidy_canvas"])["properties"]["mode"]["enum"] == [
        "columns",
        "springs",
        "settle",
    ]
    assert {"mode", "level", "energy_type", "reference_id", "drawer", "profile_id"} <= set(
        input_schema(BY_NAME["set_view"])["properties"]
    )
    assert {"start_id", "end_id", "held", "solvent", "start"} <= set(
        input_schema(BY_NAME["send_scan_path"])["properties"]
    )
    for name in ("show_in_app", "tidy_canvas", "send_scan_path", *EXPORT_TOOLS):
        assert name in claude_panel.NOTEBOOK_GUIDE


async def _poll(page) -> dict:
    return (await page.get("/api/live", params={"client": "tab-1"})).json()


def test_a_tab_does_what_claude_asks_and_answers(tmp_path):
    # T-MCP-31: with no tab open the tool says so. With one, the command comes with the tab's
    # poll; the first tab to take it does it and its report is the tool's answer. Only the page
    # can take and answer.
    async def run():
        bridge, page = _app_bridge()
        await page.post("/api/investigations", json={"folder": str(tmp_path / "i"), "name": "T"})
        a = _json(await bridge.call("create_node", {"label": "A"}))

        nobody = await bridge.call("tidy_canvas", {})
        assert nobody.isError and "No page of the app is open" in nobody.content[0].text

        assert (await _poll(page))["commands"] == []
        answers = {}

        async def ask(key, tool, arguments, report):
            async def tab():
                while not (asked := (await _poll(page))["commands"]):
                    await anyio.sleep(0.05)
                (command,) = asked
                answers[key + " asked"] = command
                url = f"/api/page/commands/{command['id']}"
                assert (await bridge.http.post(url + "/claim")).status_code == 403
                assert (await page.post(url + "/claim", headers=ORIGIN)).status_code == 204
                assert (await page.post(url + "/claim", headers=ORIGIN)).status_code == 409
                assert (await _poll(page))["commands"] == []  # taken: no other tab is asked
                assert (await bridge.http.post(url + "/report", json={})).status_code == 403
                done = await page.post(url + "/report", json=report, headers=ORIGIN)
                assert done.status_code == 204, done.text
                assert (
                    await page.post(url + "/report", json=report, headers=ORIGIN)
                ).status_code == 409

            async with anyio.create_task_group() as tasks:
                tasks.start_soon(tab)
                answers[key] = await bridge.call(tool, arguments)

        await ask("tidy", "tidy_canvas", {"mode": "springs"}, {"result": {"moved": 2}})
        assert answers["tidy asked"]["action"] == "tidy"
        assert answers["tidy asked"]["params"] == {"mode": "springs"}
        assert _json(answers["tidy"]) == {"moved": 2}
        await ask("view", "set_view", {"level": "nope"}, {"error": "No level has this key."})
        assert answers["view asked"]["params"] == {"level": "nope"}  # only what was given
        assert (
            answers["view"].isError and "No level has this key" in answers["view"].content[0].text
        )

        await ask("show", "show_in_app", {"id": a["id"]}, {"result": {"shown": "A"}})
        assert answers["show asked"] == {
            "id": answers["show asked"]["id"],
            "action": "show",
            "params": {"id": a["id"]},
        }
        nothing = await bridge.call("set_view", {})
        assert nothing.isError and "at least one" in nothing.content[0].text

    anyio.run(run)


def test_a_command_no_tab_takes_is_given_up(tmp_path, monkeypatch):
    # T-MCP-31: the tab is open but does not answer (asleep, or an older page): the tool says
    # it may not have been done, and the command is not left for a tab to do later.
    monkeypatch.setattr(page_api, "WAIT", 0.3)

    async def run():
        bridge, page = _app_bridge()
        await page.post("/api/investigations", json={"folder": str(tmp_path / "i"), "name": "T"})
        await _poll(page)
        late = await bridge.call("undo_tidy", {})
        assert late.isError and "did not answer in time" in late.content[0].text
        assert (await _poll(page))["commands"] == []

    anyio.run(run)


def test_claude_sends_a_scan_path_as_the_dialog_does(tmp_path):
    # T-MCP-32: plan_scan_path, then send_scan_path writes the job (here as a draft, with no
    # GitHub repository to start a session from).
    async def run():
        bridge, page = _app_bridge()
        await page.post("/api/investigations", json={"folder": str(tmp_path / "i"), "name": "T"})
        ends = []
        for label, xyz in (("A", WATER), ("B", TURNED)):
            made = await bridge.call(
                "create_node", {"label": label, "xyz": xyz, "charge": 0, "multiplicity": 1}
            )
            ends.append(_json(made)["id"])
        await bridge.call("create_transition", {"source_id": ends[0], "target_id": ends[1]})
        body = {"start_id": ends[0], "end_id": ends[1]}
        plan = _json(await bridge.call("plan_scan_path", body))
        assert plan["warnings"] and plan["solvent"] is None
        sent = _json(await bridge.call("send_scan_path", {**body, "start": False}))
        assert sent["start_error"] is None and sent["job"]["status"] == "draft"
        assert (tmp_path / "i" / "jobs" / sent["job"]["id"] / "job.md").is_file()
        started = _json(await bridge.call("send_scan_path", body))
        assert started["start_error"] and started["job"]["status"] == "draft"

    anyio.run(run)


def test_an_image_the_page_drew_is_saved_in_exports_and_never_over_a_file(tmp_path):
    # T-MCP-33: the tab hands the image over for the command it took, and only then; it is a
    # new file in the investigation's exports folder, numbered when the name is taken. Sync
    # leaves the folder out.
    async def run():
        bridge, page = _app_bridge()
        folder = tmp_path / "i"
        await page.post("/api/investigations", json={"folder": str(folder), "name": "T"})
        await _poll(page)
        answers = []

        async def ask(name, content):
            async def tab():
                while not (asked := (await _poll(page))["commands"]):
                    await anyio.sleep(0.05)
                (command,) = asked
                assert command["action"] == "canvas_image"
                assert command["params"] == {"what": "full", "format": "png"}
                url = f"/api/page/commands/{command['id']}"
                query = {"name": name}
                early = await page.post(url + "/file", params=query, content=PNG, headers=ORIGIN)
                assert early.status_code == 409  # not taken yet
                await page.post(url + "/claim", headers=ORIGIN)
                denied = await bridge.http.post(url + "/file", params=query, content=PNG)
                assert denied.status_code == 403
                saved = await page.post(
                    url + "/file", params=query, content=content, headers=ORIGIN
                )
                report = (
                    {"result": saved.json()}
                    if saved.is_success
                    else {"error": saved.json()["detail"]}
                )
                await page.post(url + "/report", json=report, headers=ORIGIN)

            async with anyio.create_task_group() as tasks:
                tasks.start_soon(tab)
                answers.append(await bridge.call("save_canvas_image", {}))

        await ask("canvas-full.png", PNG)
        await ask("canvas-full.png", PNG + b"1")
        assert [_json(a) for a in answers] == [
            {"file": "exports/canvas-full.png"},
            {"file": "exports/canvas-full 2.png"},
        ]
        assert (folder / "exports" / "canvas-full.png").read_bytes() == PNG
        assert (folder / "exports" / "canvas-full 2.png").read_bytes() == PNG + b"1"
        await ask("../../evil.png", PNG)
        assert _json(answers[2]) == {"file": "exports/.._.._evil.png"}
        assert not (tmp_path / "evil.png").exists()
        await ask("script.exe", PNG)
        assert answers[3].isError and "An export is one of" in answers[3].content[0].text
        await ask("empty.svg", b"")
        assert answers[4].isError and "empty" in answers[4].content[0].text
        assert "exports/" in sync.IGNORED

    anyio.run(run)


def test_claude_saves_the_read_only_copy(tmp_path, monkeypatch):
    # T-MCP-33: the copy goes to the exports folder (Claude has no download), with one pathway
    # per branch or a saved profile's ticked pathways, and is refused when the viewer page is
    # not built.
    template = tmp_path / "viewer.html"
    template.write_text(
        f"<title>{snapshot_api.TITLE_MARK}</title><script>{snapshot_api.DATA_MARK}</script>",
        encoding="utf-8",
    )

    async def run():
        bridge, page = _app_bridge()
        folder = tmp_path / "i"
        await page.post("/api/investigations", json={"folder": str(folder), "name": "Ru: A/B"})
        a = _json(await bridge.call("create_node", {"label": "A", "xyz": WATER}))
        b = _json(await bridge.call("create_node", {"label": "B", "xyz": TURNED}))
        await bridge.call("create_transition", {"source_id": a["id"], "target_id": b["id"]})
        monkeypatch.setattr(snapshot_api, "template_path", lambda: None)
        unbuilt = await bridge.call("save_read_only_copy", {})
        assert unbuilt.isError and "has not been built" in unbuilt.content[0].text
        monkeypatch.setattr(snapshot_api, "template_path", lambda: template)

        saved = _json(await bridge.call("save_read_only_copy", {}))
        name = saved["file"].removeprefix("exports/")
        assert name.startswith("Ru_ A_B read-only 20") and name.endswith(".html")
        assert "Ru: A/B · Chembook3D (read-only)" in (folder / saved["file"]).read_text("utf-8")

        profile = (
            await page.post(
                "/api/energy-profiles",
                json={
                    "name": "Main",
                    "paths": [
                        {"ids": [a["id"], b["id"]], "branch_id": None},
                        {"ids": [b["id"]], "branch_id": None, "shown": False},
                    ],
                },
            )
        ).json()
        seen = {}
        real = snapshot_api.snapshot_data

        def spy(request, session, body):
            seen["paths"] = [p.ids for p in body.paths]
            return real(request, session, body)

        monkeypatch.setattr(snapshot_api, "snapshot_data", spy)
        again = _json(await bridge.call("save_read_only_copy", {"profile_id": profile["id"]}))
        assert seen["paths"] == [[a["id"], b["id"]]]  # the ticked pathway only
        assert again["file"] == saved["file"].replace(".html", " 2.html")
        missing = await bridge.call("save_read_only_copy", {"profile_id": "nope"})
        assert missing.isError and "404" in missing.content[0].text

    anyio.run(run)
