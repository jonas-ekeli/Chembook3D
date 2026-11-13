"""D227: Claude makes, changes and copies saved energy profiles, changes the investigation's
thermochemistry and reads a steric difference map in numbers (T-MCP-34, 35, 36)."""

import anyio
import pytest

from chembook3d import claude_panel, settings
from chembook3d.mcp_tools import BY_NAME, PANEL_ALLOWED, READ_ONLY_TOOLS, input_schema
from tests.conftest import WATER
from tests.test_mcp import ORIGIN, _app_bridge, _json
from tests.test_overlay import xyz_text
from tests.test_sterics import PROBE, PROBE_ATOMS

PROFILE_TOOLS = {"create_energy_profile", "update_energy_profile", "duplicate_energy_profile"}
CLAUDE = {"X-Chembook-Client": "claude"}


def test_the_profile_tools_and_which_of_them_ask():
    # T-MCP-34: the profile tools and the thermochemistry change something, so the panel
    # asks; the steric difference only reads. Deleting a profile is a confirm action with no
    # "own" rule: it asks also while Claude works on its own.
    for name in PROFILE_TOOLS | {"set_thermochemistry"}:
        assert BY_NAME[name].kind == "write" and name not in PANEL_ALLOWED
        assert name in claude_panel.NOTEBOOK_GUIDE
        assert name in claude_panel.allowed_alone(cloud=False)
    assert BY_NAME["delete_energy_profile"].kind == "confirm"
    assert "get_steric_difference" in READ_ONLY_TOOLS & PANEL_ALLOWED
    schema = input_schema(BY_NAME["update_energy_profile"])
    assert {"energy_profile_id", "name", "paths", "reference_id"} == set(schema["properties"])
    assert schema["required"] == ["energy_profile_id"]
    assert set(input_schema(BY_NAME["set_thermochemistry"])["properties"]) == {
        "qh_temperature",
        "qh_cutoff",
        "standard_state",
    }
    assert input_schema(BY_NAME["get_steric_difference"])["properties"]["grid"]["default"] is False
    assert "set_thermochemistry" in claude_panel.AUTONOMY_GUIDE


def test_claude_saves_profiles_of_real_pathways_and_the_tabs_hear(tmp_path):
    # T-MCP-34: what Claude gives as a pathway is checked as one (the page's own saves are
    # not); a profile's pathways through a deleted node stay; the tabs' poll says that a saved
    # profile changed, for Claude's writes only; and deleting one always asks.
    async def run():
        bridge, page = _app_bridge(confirm_timeout=1)
        await page.post("/api/investigations", json={"folder": str(tmp_path / "i"), "name": "T"})
        a, b, c = [
            _json(await bridge.call("create_node", {"label": label, "xyz": WATER}))["id"]
            for label in "ABC"
        ]
        _json(await bridge.call("create_transition", {"source_id": a, "target_id": b}))

        async def heard() -> int:
            return (await page.get("/api/live", params={"client": "tab"})).json()[
                "profiles_version"
            ]

        assert await heard() == 0
        made = _json(
            await bridge.call(
                "create_energy_profile",
                {"name": "Main", "paths": [{"ids": [a, b]}], "reference_id": a},
            )
        )
        assert made["name"] == "Main" and made["reference_id"] == a
        assert made["paths"] == [
            {"ids": [a, b], "branch_id": None, "shown": True, "shifts": [], "colour": None}
        ]
        assert await heard() == 1

        for paths, why in (
            ([{"ids": [a, c]}], "No transition joins “A” and “C”"),
            ([{"ids": [a, "nothing"]}], "No node or group has the id nothing"),
            ([{"ids": [a, b], "branch_id": "nothing"}], "No branch has the id nothing"),
        ):
            refused = await bridge.call("create_energy_profile", {"paths": paths})
            assert refused.isError and why in refused.content[0].text, refused.content[0].text
        assert await heard() == 1  # a refusal is no change
        assert len(_json(await bridge.call("list_energy_profiles", {}))) == 1

        # The page's own save is not checked (the drawer keeps a pathway that lost a node,
        # D221) and is quiet: no tab starts its drawer anew for it.
        url = f"/api/energy-profiles/{made['id']}"
        kept = await page.patch(url, json={"paths": [{"ids": [a, b]}, {"ids": [c]}]})
        assert kept.is_success, kept.text
        assert await heard() == 1
        assert (await page.delete(f"/api/nodes/{c}")).is_success
        (stored,) = (await page.get("/api/energy-profiles")).json()
        assert [p["ids"] for p in stored["lost"]] == [[c]]

        # Claude replaces the pathways: the lost one stays, as only the user removes it.
        changed = _json(
            await bridge.call(
                "update_energy_profile",
                {
                    "energy_profile_id": made["id"],
                    "paths": [{"ids": [b, a], "colour": "#AA0000"}],
                    "reference_id": b,
                },
            )
        )
        assert [p["ids"] for p in changed["paths"]] == [[b, a]]
        assert changed["paths"][0]["colour"] == "#aa0000" and changed["reference_id"] == b
        assert [p["ids"] for p in changed["lost"]] == [[c]]
        assert await heard() == 2
        renamed = _json(
            await bridge.call(
                "update_energy_profile", {"energy_profile_id": made["id"], "name": "Both ways"}
            )
        )
        assert renamed["name"] == "Both ways" and [p["ids"] for p in renamed["lost"]] == [[c]]

        copy = _json(
            await bridge.call("duplicate_energy_profile", {"energy_profile_id": made["id"]})
        )
        assert copy["name"] == "Both ways (copy)" and copy["id"] != made["id"]
        assert await heard() == 4

        # Deleting asks, also for the copy Claude made itself while it works on its own.
        on = await page.put(
            "/api/claude/autonomy",
            json={"on": True, "accepted": True, "job_limit": 3600, "cloud": False},
            headers=ORIGIN,
        )
        assert on.is_success, on.text
        asked = await bridge.call("delete_energy_profile", {"energy_profile_id": copy["id"]})
        assert "No answer in the app" in asked.content[0].text
        assert len((await page.get("/api/energy-profiles")).json()) == 2

        # Confirmed in the page, it goes as Claude's change: the tabs hear of it.
        waiting = await page.post(
            "/api/confirmations",
            json={"action": "delete_energy_profile", "params": {"energy_profile_id": copy["id"]}},
            headers=CLAUDE,
        )
        assert waiting.json()["status"] == "pending", waiting.text
        assert waiting.json()["summary"] == "Delete the energy profile “Both ways (copy)”"
        done = await page.post(
            f"/api/confirmations/{waiting.json()['id']}/answer",
            json={"confirm": True},
            headers=ORIGIN,
        )
        assert done.json()["status"] == "done", done.text
        assert [p["id"] for p in (await page.get("/api/energy-profiles")).json()] == [made["id"]]
        assert await heard() == 5

    anyio.run(run)


def test_claude_sets_the_investigations_thermochemistry(tmp_path):
    # T-MCP-35: the values given change in the investigation, each one entry of Claude's in
    # the history; the app's defaults for new investigations stay; a value the settings do
    # not take, or none at all, is refused.
    async def run():
        bridge, page = _app_bridge()
        await page.post("/api/investigations", json={"folder": str(tmp_path / "i"), "name": "T"})
        before = _json(await bridge.call("get_energy_options", {}))
        assert (before["temperature"], before["standard_state"]) == (298.15, "1 atm")

        after = _json(
            await bridge.call(
                "set_thermochemistry", {"qh_temperature": 323.15, "standard_state": "1 M"}
            )
        )
        assert after == {
            "qh_temperature": 323.15,
            "qh_cutoff": before["cutoff"],
            "standard_state": "1 M",
        }
        options = _json(await bridge.call("get_energy_options", {}))
        assert (options["temperature"], options["standard_state"]) == (323.15, "1 M")
        entries = [
            e
            for e in (await page.get("/api/history")).json()
            if e["record_type"] == "thermochemistry" and e["action"] == "update"
        ]
        assert {e["field"] for e in entries} == {"qh_temperature", "standard_state"}
        assert {e["source"] for e in entries} == {"claude"}
        defaults = settings.load()
        assert (defaults.qh_temperature, defaults.standard_state) == (298.15, "1 atm")

        for arguments, why in (
            ({}, "Give at least one value to change"),
            ({"qh_temperature": 0}, "The G_qh temperature must be above zero"),
            ({"standard_state": "2 M"}, "Unknown standard state"),
        ):
            refused = await bridge.call("set_thermochemistry", arguments)
            assert refused.isError and why in refused.content[0].text, refused.content[0].text

    anyio.run(run)


def test_a_steric_difference_in_numbers(tmp_path):
    # T-MCP-36: the difference of two maps as a summary; the grid only when asked. The
    # probe's carbon sits over the NE quadrant; raised by 0.4 Å in the second node, the first
    # map is 0.4 Å below the second where both cover, and the other quadrants hold nothing.
    async def run():
        bridge, page = _app_bridge()
        await page.post("/api/investigations", json={"folder": str(tmp_path / "i"), "name": "T"})
        raised = [*PROBE[:3], ("C", 2.2, 2.2, -0.6)]
        first, second = [
            _json(await bridge.call("create_node", {"label": label, "xyz": xyz_text(atoms)}))["id"]
            for label, atoms in (("low", PROBE), ("high", raised))
        ]
        profile = _json(
            await bridge.call(
                "create_steric_profile",
                {"name": "Pocket", "atoms": {first: PROBE_ATOMS, second: {"same_as": first}}},
            )
        )
        arguments = {"profile_id": profile["id"], "first_id": first, "second_id": second}
        found = _json(await bridge.call("get_steric_difference", arguments))
        assert found["x"] == [] and found["z"] == []
        summary = found["summary"]
        assert summary["points"] > 100
        assert summary["mean"] == pytest.approx(-0.4, abs=1e-3)
        assert summary["quadrants"] == {
            "NE": pytest.approx(-0.4, abs=1e-3),
            "NW": None,
            "SW": None,
            "SE": None,
        }
        for end in ("highest", "lowest"):
            assert summary[end]["difference"] == pytest.approx(-0.4, abs=1e-3)
            assert summary[end]["x"] > 0 and summary[end]["y"] > 0

        whole = _json(await bridge.call("get_steric_difference", {**arguments, "grid": True}))
        assert len(whole["x"]) == 71 and len(whole["z"]) == 71
        assert whole["summary"] == summary
        # The page's own request gives the grid, with the summary beside it.
        url = f"/api/steric-profiles/{profile['id']}/difference"
        mine = await page.post(url, json={"first_id": first, "second_id": second})
        assert len(mine.json()["z"]) == 71 and mine.json()["summary"]["points"] == summary["points"]

        # Two maps with no point in common: nothing to compare.
        apart = [*PROBE[:3], ("C", -2.2, -2.2, -1.0)]
        third = _json(await bridge.call("create_node", {"label": "SW", "xyz": xyz_text(apart)}))
        _json(
            await bridge.call(
                "update_steric_profile",
                {"profile_id": profile["id"], "atoms": {third["id"]: {"same_as": first}}},
            )
        )
        none = _json(
            await bridge.call("get_steric_difference", {**arguments, "second_id": third["id"]})
        )
        assert none["summary"] == {
            "points": 0,
            "mean": None,
            "quadrants": {"NE": None, "NW": None, "SW": None, "SE": None},
            "highest": None,
            "lowest": None,
        }

    anyio.run(run)
