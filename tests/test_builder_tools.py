"""Claude's structure tools (D144, A89; T-MCP-12…14): the Edit structure window's tools as steps,
on atoms numbered from 1, storing nothing."""

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
        json={"steps": [{"tool": "smiles", "smiles": "CCO"}, {"tool": "freeze", "atoms": [1]}]},
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
    assert answer["atom_count"] == 76 and answer["electrons_note"] is None
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

    saved = open_client.put(f"/api/nodes/{made['id']}/geometry", json={"xyz": answer["xyz"]})
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
        checked = _json(await bridge.call("check_structure", {"xyz": answer["xyz"]}))
        assert checked["atom_count"] == 6
        node = _json(await bridge.call("get_node", {"node_id": made["id"]}))
        assert node["formula"] == "H2O"  # unchanged until set_coordinates
        json.dumps(answer)

    anyio.run(run)
