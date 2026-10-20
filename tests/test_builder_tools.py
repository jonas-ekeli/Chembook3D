"""Claude's structure tools (D144, A89; T-MCP-12…14; D148, D149: T-MCP-15…19): the Edit structure
window's tools as steps, on atoms numbered from 1, storing nothing."""

import json
from dataclasses import replace

import anyio
import numpy as np

from chembook3d import xyz
from chembook3d.mcp_tools import PANEL_ALLOWED, READ_ONLY_TOOLS
from tests.conftest import WATER
from tests.test_mcp import _app_bridge, _json
from tests.test_minimize import STERICS


def ru_xyz() -> str:
    """morfeus's SambVca complex 3 with its Ir made Ru (as test_minimize does)."""
    atoms = xyz.parse_xyz((STERICS / "3.xyz").read_text(encoding="utf-8"))
    return xyz.format_xyz([replace(a, element="Ru") if a.element == "Ir" else a for a in atoms])


def rows(text: str) -> np.ndarray:
    return np.array([[a.x, a.y, a.z] for a in xyz.parse_xyz(text)])


def metal_bonds(answer: dict) -> dict[int, float]:
    (metal,) = answer["metals"]
    return {n["atom"]: n["distance"] for n in metal["bonded"]}


def test_steps_build_from_nothing_and_go_on(client):
    built = client.post(
        "/api/builder/run",
        json={
            "steps": [{"tool": "smiles", "smiles": "CCO"}, {"tool": "freeze", "atoms": [1]}],
            "include_xyz": True,
        },
    )
    assert built.status_code == 200, built.text
    answer = built.json()
    assert answer["formula"] == "C2H6O" and answer["atom_count"] == 9
    assert answer["steps"][0]["message"].startswith("Added “CCO” as atoms 1–9")
    assert answer["holds"] == [{"atoms": [1], "value": None}]
    assert answer["close_contacts"] == [] and answer["metals"] == []
    assert answer["xyz"].startswith("9\n")

    # Going on from the answer: a C–O distance set, which a hold then keeps through Minimize.
    carbon, oxygen = 2, 3
    more = client.post(
        "/api/builder/run",
        json={
            "xyz": answer["xyz"],
            "changed": answer["changed"],
            "holds": answer["holds"],
            "steps": [
                {"tool": "distance", "atoms": [carbon, oxygen], "value": 1.60},
                {"tool": "hold"},  # the atoms the step before picked
                {"tool": "minimize"},
            ],
            "include_xyz": True,
        },
    ).json()
    assert more["steps"][1]["message"].startswith(f"Distance {carbon}–{oxygen} held at 1.600")
    assert more["holds"][0] == {"atoms": [1], "value": None}
    checked = client.post(
        "/api/builder/check", json={"xyz": more["xyz"], "measure": [[carbon, oxygen]]}
    ).json()
    assert abs(checked["measurements"][0]["value"] - 1.60) < 0.03  # held, against UFF
    assert checked["measurements"][0]["bonded"] is True
    assert np.allclose(rows(more["xyz"])[0], rows(answer["xyz"])[0], atol=1e-4)  # frozen


def test_a_failing_step_names_itself_and_nothing_changes(client):
    failed = client.post(
        "/api/builder/run",
        json={
            "xyz": WATER,
            "steps": [
                {"tool": "element", "atoms": [1], "element": "S"},
                {"tool": "distance", "atoms": [1, 9], "value": 1.0},
            ],
        },
    )
    assert failed.status_code == 422
    assert failed.json()["detail"] == "Step 2 (distance): There is no atom 9"
    no_value = client.post(
        "/api/builder/run", json={"xyz": WATER, "steps": [{"tool": "angle", "atoms": [2, 1, 3]}]}
    )
    assert no_value.json()["detail"] == "Step 1 (angle): Give a value"
    both = client.post(
        "/api/builder/run", json={"xyz": WATER, "node_id": "x", "steps": [{"tool": "minimize"}]}
    )
    assert both.status_code == 422 and "not both" in both.json()["detail"]
    for wrong in ([0, 1], [1], [1, 1]):
        bad = client.post("/api/builder/check", json={"xyz": WATER, "measure": [wrong]})
        assert bad.status_code == 422, wrong


def test_checks_find_clashes_metals_and_electrons(client):
    atoms = xyz.parse_xyz(WATER)
    other = [replace(a, y=a.y + 2.4, z=a.z + 0.2) for a in atoms]  # its H 1.0 Å from an H
    answer = client.post(
        "/api/builder/check",
        json={"xyz": xyz.format_xyz(atoms + other), "charge": 0, "multiplicity": 2},
    ).json()
    assert answer["formula"] == "H4O2"
    assert answer["close_contacts"][0]["elements"] == ["H", "H"]
    assert answer["close_contacts"][0]["distance"] < 1.1
    assert "cannot have multiplicity 2" in answer["electrons_note"]

    ru = client.post("/api/builder/check", json={"xyz": ru_xyz()}).json()
    assert ru["metals"][0]["element"] == "Ru"
    assert sorted(n["element"] for n in ru["metals"][0]["bonded"]) == ["C", "C", "C", "Cl"]
    assert ru["close_contacts"] == []


def test_a_node_is_changed_only_by_saving(open_client):
    made = open_client.post(
        "/api/nodes", json={"label": "Ru-1", "xyz": ru_xyz(), "charge": 0, "multiplicity": 2}
    ).json()
    before = open_client.get("/api/live").json()["version"]
    # An aryl H (atom 24) made a methyl, then Minimize: the Ru keeps its shape (D140).
    built = open_client.post(
        "/api/builder/run",
        json={
            "node_id": made["id"],
            "steps": [{"tool": "fragment", "atoms": [24], "name": "Me"}, {"tool": "minimize"}],
        },
    )
    assert built.status_code == 200, built.text
    answer = built.json()
    assert answer["atom_count"] == 76 and answer["xyz"] is None  # saved by its build_id
    assert answer["electrons_note"] == "311 electrons with charge 0: multiplicity 2 fits"
    assert answer["close_contacts"] == []
    start = open_client.post("/api/builder/check", json={"node_id": made["id"]}).json()
    singlet = {"node_id": made["id"], "multiplicity": 1}  # the node's charge, this multiplicity
    assert (
        "303 electrons"
        in open_client.post("/api/builder/check", json=singlet).json()["electrons_note"]
    )
    old, new = metal_bonds(start), metal_bonds(answer)
    assert old.keys() == new.keys()
    assert all(abs(old[a] - new[a]) < 0.05 for a in old)
    # Nothing stored, nothing for the tabs to reload.
    assert open_client.get("/api/live").json()["version"] == before
    assert open_client.get(f"/api/nodes/{made['id']}").json()["formula"] == start["formula"]

    saved = open_client.put(
        f"/api/nodes/{made['id']}/geometry", json={"build_id": answer["build_id"]}
    )
    assert saved.status_code == 200
    assert saved.json()["node"]["formula"] == answer["formula"]

    missing = open_client.post("/api/builder/check", json={"node_id": "nope"})
    assert missing.status_code == 404


def test_the_tools_through_the_mcp_server(tmp_path):
    assert {"build_structure", "check_structure"} <= READ_ONLY_TOOLS & PANEL_ALLOWED

    async def run():
        bridge, page = _app_bridge()
        await page.post("/api/investigations", json={"folder": str(tmp_path / "i"), "name": "T"})
        made = _json(await bridge.call("create_node", {"label": "W", "xyz": WATER}))
        answer = _json(
            await bridge.call(
                "build_structure",
                {
                    "node_id": made["id"],
                    "steps": [
                        {"tool": "fragment", "atoms": [2], "name": "Me"},
                        {"tool": "minimize"},
                    ],
                },
            )
        )
        assert answer["formula"] == "CH4O"
        refused = await bridge.call(
            "build_structure", {"node_id": made["id"], "steps": [{"tool": "delete"}]}
        )
        assert refused.isError and "Step 1 (delete)" in refused.content[0].text
        checked = _json(await bridge.call("check_structure", {"build_id": answer["build_id"]}))
        assert checked["atom_count"] == 6
        node = _json(await bridge.call("get_node", {"node_id": made["id"]}))
        assert node["formula"] == "H2O"  # unchanged until set_coordinates
        json.dumps(answer)

    anyio.run(run)


def test_builds_are_saved_and_continued_by_id(open_client):
    # T-MCP-15 (D148): an answer's build_id saves exactly its coordinates, starts the next run
    # with its bonds, changed atoms and holds, and gives a new node its charge.
    made = open_client.post(
        "/api/nodes", json={"label": "Ru-1", "xyz": ru_xyz(), "charge": 0, "multiplicity": 2}
    ).json()
    first = open_client.post(
        "/api/builder/run",
        json={
            "node_id": made["id"],
            "steps": [{"tool": "fragment", "atoms": [24], "name": "Me"}, {"tool": "freeze"}],
            "include_xyz": True,
        },
    ).json()
    assert first["build_id"].startswith("build-")
    more = open_client.post(
        "/api/builder/run",
        json={"build_id": first["build_id"], "steps": [{"tool": "minimize"}]},
    )
    assert more.status_code == 200, more.text
    answer = more.json()
    assert answer["holds"] == first["holds"] and answer["changed"] == first["changed"]
    assert answer["electrons_note"] == "311 electrons with charge 0: multiplicity 2 fits"
    assert answer["xyz"] is None

    saved = open_client.put(
        f"/api/nodes/{made['id']}/geometry", json={"build_id": first["build_id"]}
    ).json()
    assert saved["node"]["xyz"].splitlines()[2:] == first["xyz"].splitlines()[2:]
    node = open_client.post("/api/nodes", json={"label": "Ru-2", "build_id": answer["build_id"]})
    assert node.status_code == 201
    assert (node.json()["charge"], node.json()["multiplicity"]) == (0, 2)
    assert node.json()["formula"] == answer["formula"]

    gone = open_client.put(f"/api/nodes/{made['id']}/geometry", json={"build_id": "build-x"})
    assert gone.status_code == 404 and "run build_structure again" in gone.json()["detail"]
    both = {"build_id": answer["build_id"], "xyz": WATER}
    assert open_client.put(f"/api/nodes/{made['id']}/geometry", json=both).status_code == 422
    assert open_client.put(f"/api/nodes/{made['id']}/geometry", json={}).status_code == 422
    twice = {"build_id": answer["build_id"], "node_id": made["id"], "steps": [{"tool": "minimize"}]}
    assert open_client.post("/api/builder/run", json=twice).status_code == 422


def test_step_warnings_sides_and_electrons(client):
    # T-MCP-16 (D148): a step that bonds or crowds atoms it was not asked to says so; a
    # measurement says which atoms setting it moves; an unchecked electron count says so.
    oxygen = 74  # the next atom of the 73-atom complex
    answer = client.post(
        "/api/builder/run",
        json={
            "xyz": ru_xyz(),
            "steps": [
                {"tool": "add_atom", "atoms": [1], "element": "C", "value": 1.9},
                {"tool": "add_atom", "atoms": [oxygen], "element": "O", "value": 1.15},
                {"tool": "angle", "atoms": [1, oxygen, oxygen + 1], "value": 60},
            ],
        },
    ).json()
    first, second, third = answer["steps"]
    assert first["message"] == f"Added C {oxygen} bonded to atom 1 at 1.90 Å"
    assert first["warnings"] == second["warnings"] == []
    assert third["warnings"] and f"O {oxygen + 1} are now drawn bonded" in third["warnings"][0]
    assert answer["electrons_note"].startswith("Not checked")

    checked = client.post(
        "/api/builder/check", json={"build_id": answer["build_id"], "measure": [[1, oxygen]]}
    ).json()
    (measured,) = checked["measurements"]
    assert measured["moves"] == oxygen
    # The O drawn bonded to Ru joins the sides, so only the C itself would move.
    small = next(s for s in measured["sides"] if s["atom"] == oxygen)
    assert small == {"atom": oxygen, "count": 1, "whole": False, "atoms": [oxygen]}
    apart = client.post("/api/builder/check", json={"xyz": ru_xyz(), "measure": [[1, 2]]}).json()[
        "measurements"
    ][0]
    big = next(s for s in apart["sides"] if s["atom"] == 1)
    assert big["count"] > 30 and big["atoms"] is None


def test_short_node_list_through_the_mcp_server(tmp_path):
    # T-MCP-17 (D148): list_nodes leaves the coordinates out unless asked; the app's own list
    # keeps them; set_coordinates takes a build_id without xyz.
    async def run():
        bridge, page = _app_bridge()
        await page.post("/api/investigations", json={"folder": str(tmp_path / "i"), "name": "T"})
        made = _json(await bridge.call("create_node", {"label": "W", "xyz": WATER}))
        short = _json(await bridge.call("list_nodes", {}))
        assert short[0]["xyz"] is None and short[0]["formula"] == "H2O"
        full = _json(await bridge.call("list_nodes", {"include_xyz": True}))
        assert full[0]["xyz"].startswith("3\n")
        assert (await page.get("/api/nodes")).json()[0]["xyz"].startswith("3\n")
        built = _json(
            await bridge.call(
                "build_structure",
                {
                    "node_id": made["id"],
                    "steps": [{"tool": "fragment", "atoms": [2], "name": "Me"}],
                },
            )
        )
        saved = _json(
            await bridge.call(
                "set_coordinates", {"node_id": made["id"], "build_id": built["build_id"]}
            )
        )
        assert saved["node"]["formula"] == "CH4O"
        refused = await bridge.call("set_coordinates", {"node_id": made["id"], "xyz": ""})
        assert refused.isError and "remove_coordinates" in refused.content[0].text

    anyio.run(run)


def test_schemas_without_null_and_a_brief_node_list(tmp_path):
    # T-MCP-18 (D149): optional fields are their plain type in the schema Claude sees, so
    # build_id and count show; list_nodes for Claude is a brief list, and a long answer is sent
    # without indentation.
    from chembook3d import mcp_server
    from chembook3d.mcp_tools import BY_NAME, TOOLS, input_schema

    for name in ("set_coordinates", "create_node", "build_structure", "check_structure"):
        prop = input_schema(BY_NAME[name])["properties"]["build_id"]
        assert prop["type"] == "string" and "build_structure" in prop["description"]
    step = input_schema(BY_NAME["build_structure"])["properties"]["steps"]["items"]
    assert step["properties"]["count"]["type"] == "integer"
    for spec in TOOLS:
        assert '{"type": "null"}' not in json.dumps(input_schema(spec)), spec.name

    async def run():
        bridge, page = _app_bridge()
        await page.post("/api/investigations", json={"folder": str(tmp_path / "i"), "name": "T"})
        await bridge.call("create_node", {"label": "W", "xyz": WATER, "notes": "long " * 50})
        (short,) = _json(await bridge.call("list_nodes", {}))
        assert short["formula"] == "H2O" and short["xyz"] is None
        assert "notes" not in short and "pos_x" not in short and short["warnings"] == []
        (full,) = _json(await bridge.call("list_nodes", {"brief": False}))
        assert full["notes"].startswith("long")

    anyio.run(run)
    many = [{"id": n, "label": "x" * 40} for n in range(400)]
    assert "\n" not in mcp_server._json_text(many) and "\n" in mcp_server._json_text(many[:2])


def test_a_clash_drawn_as_a_bond_is_still_a_contact(client):
    # T-MCP-19 (D149): an H 1.0 Å from another water's O is drawn bonded to both, but an H has
    # one bond, so the closer pair is reported as a contact; so is a "bond" far too short.
    text = "6\n\nO 0 0 0\nH 0.96 0 0\nH -0.24 0.93 0\nO 1.96 0 0\nH 2.2 0.93 0\nH 2.4 -0.9 0\n"
    found = client.post("/api/builder/check", json={"xyz": text}).json()["close_contacts"]
    assert {"atoms": [2, 4], "elements": ["H", "O"], "distance": 1.0} in found
    squeezed = "2\n\nC 0 0 0\nCl 1.2 0 0\n"
    found = client.post("/api/builder/check", json={"xyz": squeezed}).json()["close_contacts"]
    assert found == [{"atoms": [1, 2], "elements": ["C", "Cl"], "distance": 1.2}]
    # Ordinary molecules report nothing.
    assert client.post("/api/builder/check", json={"xyz": ru_xyz()}).json()["close_contacts"] == []
