"""Steps between minima with no TS: a species joining or leaving, a conformation change
(FR-MCP-27, A190, A193, T-MCP-53 to T-MCP-55).

Synthetic structures: a ruthenium carbene with two chlorides (A, "the 14-electron complex"),
ethene, and two π-complexes of them, the ethene's C=C along y (B) or along x (B2, turned
90°), each numbered otherwise than A with the ethene's atoms among the complex's."""

import json

import anyio
import numpy as np

from chembook3d.claude_panel import NOTEBOOK_GUIDE
from chembook3d.mcp_server import INSTRUCTIONS
from chembook3d.mcp_tools import BY_NAME, PANEL_ALLOWED
from chembook3d.services import direct_links
from tests.test_mcp import _app_bridge

CORE = [
    ("Ru", 0.0, 0.0, 0.0),
    ("Cl", 2.35, 0.0, 0.0),
    ("Cl", -2.35, 0.0, 0.0),
    ("C", 0.0, 0.0, 1.85),
    ("H", 0.93, 0.0, 2.42),
    ("H", -0.93, 0.0, 2.42),
]
ETHENE = [
    ("C", 0.0, 0.67, 0.0),
    ("C", 0.0, -0.67, 0.0),
    ("H", 0.92, 1.24, 0.0),
    ("H", -0.92, 1.24, 0.0),
    ("H", 0.92, -1.24, 0.0),
    ("H", -0.92, -1.24, 0.0),
]


def bound(turned: bool) -> list[tuple]:
    """Ethene 2.1 Å below the ruthenium (both Ru–C bonds), its C=C along y, or along x."""
    out = []
    for e, x, y, z in ETHENE:
        if turned:
            x, y = y, -x
        out.append((e, x, y, z - 2.1 - (0.25 if e == "H" else 0.0)))
    return out


def complex_rows(turned: bool) -> list[tuple]:
    """The π-complex, the core a little relaxed, numbered ethene and complex mixed."""
    core = [(e, x * 1.02, y, z + (0.03 if e == "Cl" else 0.0)) for e, x, y, z in CORE]
    olefin = bound(turned)
    return [olefin[0], core[1], olefin[1], core[0], *olefin[2:], core[3], core[2], *core[4:]]


def xyz(rows) -> str:
    return f"{len(rows)}\n\n" + "\n".join(f"{e} {x} {y} {z}" for e, x, y, z in rows) + "\n"


def node(client, label: str, rows, **fields) -> str:
    body = {"label": label, "xyz": xyz(rows), "role": "minimum", **fields}
    response = client.post("/api/nodes", json=body)
    assert response.status_code == 201, response.text
    return response.json()["id"]


def links(client, **params) -> dict:
    response = client.get("/api/mechanism/direct-links", params=params)
    assert response.status_code == 200, response.text
    return response.json()


def test_associations_and_a_turn_with_no_ts_are_listed(open_client):
    """T-MCP-53: A + ethene against both π-complexes (any atom order), the bonds to the core
    and a small core RMSD (medium); the two π-complexes as a same-atom pair, "no TS"; ethene
    as an ordinary node is a species to make; then a complete small mechanism lists none."""
    a = node(open_client, "A", CORE)
    ethene = node(open_client, "ethene", ETHENE)
    b = node(open_client, "B", complex_rows(False))
    b2 = node(open_client, "B2", complex_rows(True))

    found = links(open_client)
    pairs = found["pairs"]
    # A193: B and B2 are conformers of one molecule, so one line, the nearest pair.
    (association,) = [p for p in pairs if p["kind"] == "association"]
    assert association["a"]["node_id"] == a and association["b"]["node_id"] in (b, b2)
    assert association["species"] == {"label": "ethene", "node_id": ethene, "make_species": True}
    assert len(association["bonds_to_core"]) == 2
    assert all("Ru" in bond for bond in association["bonds_to_core"])
    assert association["core_rmsd"] < direct_links.MEDIUM_RMSD
    assert association["confidence"] == "medium" and association["other_pairs"] == 1
    (turn,) = [p for p in pairs if p["kind"] == "same_atoms"]
    assert {turn["a"]["node_id"], turn["b"]["node_id"]} == {b, b2}
    assert turn["note"] == "no TS: direct link?" and turn["confidence"] == "low"
    assert turn["rmsd"] > 0.05 and "metal_bonds" not in turn and "other_pairs" not in turn
    assert found["counts"] == {"association": 1, "same_atoms": 1}
    assert found["make_species"] == ["ethene"] and "more" not in found
    assert "should be free species" in found["summary"]
    assert "no chemistry choice" in found["next"]
    only = links(open_client, kind="same_atoms")
    assert [p["kind"] for p in only["pairs"]] == ["same_atoms"]

    # The answer is paged: one pair per call, then the rest from the offset.
    first = direct_links.MAX_ANSWER
    try:
        direct_links.MAX_ANSWER = 10
        paged = links(open_client)
    finally:
        direct_links.MAX_ANSWER = first
    assert len(paged["pairs"]) == 1 and paged["more"]["offset"] == 1
    rest = links(open_client, offset=1)
    assert len(rest["pairs"]) == 1 and "more" not in rest

    # Ethene a free species, A + ethene → B with it joining, B → B2: nothing is left to link.
    kind = open_client.put(f"/api/nodes/{ethene}/kind", json={"kind": "species"})
    assert kind.status_code == 200, kind.text
    found = links(open_client)
    assert all("make_species" not in p["species"] for p in found["pairs"] if "species" in p)
    edge = open_client.post("/api/transitions", json={"source_id": a, "target_id": b}).json()
    joined = open_client.put(
        f"/api/transitions/{edge['id']}/species",
        json={"species_id": ethene, "direction": "joins"},
    )
    assert joined.status_code == 200, joined.text
    open_client.post("/api/transitions", json={"source_id": b, "target_id": b2})
    found = links(open_client)
    assert found["pairs"] == [] and found["of"] == 0


# A metallacyclobutane of A and ethene: the ethene's carbons bonded to the ruthenium and one
# to the carbene carbon (a C–C bond, so no association: a cycloaddition, with a TS).
METALLACYCLE = [
    ("Ru", 0.0, 0.0, 0.0),
    ("Cl", 2.35, 0.0, 0.0),
    ("Cl", -2.35, 0.0, 0.0),
    ("C", 0.0, 0.0, 1.95),
    ("H", 0.9, -0.5, 2.4),
    ("H", -0.9, -0.5, 2.4),
    ("C", 0.0, 1.95, 0.0),
    ("C", 0.0, 1.55, 1.45),
    ("H", 0.9, 2.5, -0.4),
    ("H", -0.9, 2.5, -0.4),
    ("H", 0.9, 2.0, 1.9),
    ("H", -0.9, 2.0, 1.9),
]


def turned_by(degrees: float) -> list[tuple]:
    """The π-complex with its ethene turned about z by `degrees`."""
    c, s = np.cos(np.radians(degrees)), np.sin(np.radians(degrees))
    core = [(e, x * 1.02, y, z + (0.03 if e == "Cl" else 0.0)) for e, x, y, z in CORE]
    olefin = [(e, c * x - s * y, s * x + c * y, z) for e, x, y, z in bound(False)]
    return [*core, *olefin]


def test_an_association_bonds_to_a_metal_and_one_line_per_molecule_pair(open_client):
    """T-MCP-55 (A193): a metallacyclobutane of A and ethene (a C–C bond to the core) is no
    association; three π-complex conformers are one line with 2 other pairs, two of them
    grouped a line each (a group counts as its own molecule); group_structures names each
    node's id and marks a TS, and says to group before linking."""
    a = node(open_client, "A", CORE)
    ethene = node(open_client, "ethene", ETHENE)
    open_client.put(f"/api/nodes/{ethene}/kind", json={"kind": "species"})
    node(open_client, "MCB", METALLACYCLE)
    b = [node(open_client, f"B{n}", turned_by(angle)) for n, angle in enumerate((0, 45, 90))]

    found = links(open_client, kind="association")
    (line,) = found["pairs"]
    assert line["b"]["node_id"] in b and line["other_pairs"] == 2
    assert found["of"] == 1

    grouped = open_client.post("/api/groups/reconnect", json={"member_ids": b[:2], "label": "Bs"})
    assert grouped.status_code == 201, grouped.text
    found = links(open_client, kind="association")
    ends = sorted((p["b"].get("group") or "", p["b"]["node_id"]) for p in found["pairs"])
    assert len(ends) == 2 and ends[0] == ("", b[2]) and ends[1][0] == "Bs"
    assert all("other_pairs" not in p for p in found["pairs"])

    ts = node(open_client, "TS", turned_by(20), role="transition_state")
    group = open_client.post("/api/structures/group", json={"all_nodes": True})
    assert group.status_code == 200, group.text
    group = group.json()
    members = [
        m
        for f in group["formulas"]
        for mol in f["molecules"]
        for c in mol["conformers"]
        for m in c["members"]
    ]
    by_id = {m["node_id"]: m for m in members}
    assert set(b) | {a, ts} <= set(by_id)
    assert by_id[ts]["ts"] is True and "ts" not in by_id[b[0]]
    assert "before linking" in group["next"]


def test_the_tool_over_mcp_and_in_the_guides(tmp_path):
    """T-MCP-54: a read tool the panel does not ask about, named in both guides; over MCP it
    answers like the route."""
    tool = BY_NAME["find_direct_links"]
    assert tool.kind == "read" and "find_direct_links" in PANEL_ALLOWED
    assert tool.path == "/api/mechanism/direct-links"
    for text in (INSTRUCTIONS, NOTEBOOK_GUIDE):
        assert "find_direct_links" in text

    async def run():
        bridge, page = _app_bridge()
        await page.post("/api/investigations", json={"folder": str(tmp_path / "i"), "name": "T"})
        for label, rows in (("A", CORE), ("ethene", ETHENE), ("B", complex_rows(False))):
            await page.post(
                "/api/nodes", json={"label": label, "xyz": xyz(rows), "role": "minimum"}
            )
        result = await bridge.call("find_direct_links", {})
        assert not result.is_error, result.content
        found = json.loads(result.content[0].text)
        assert [p["kind"] for p in found["pairs"]] == ["association"]
        assert found["pairs"][0]["b"]["label"] == "B"

    anyio.run(run)


def test_a_small_molecule_is_told_apart():
    """A190: metal-free, closed shell, in one piece and at most 40 atoms."""
    assert direct_links.small_molecule([list(r) for r in ETHENE], 1)
    assert direct_links.small_molecule([list(r) for r in ETHENE], None)
    assert not direct_links.small_molecule([list(r) for r in ETHENE], 3)
    assert not direct_links.small_molecule([list(r) for r in CORE], 1)  # a metal
    apart = [list(r) for r in ETHENE] + [["H", 9.0, 0.0, 0.0], ["H", 9.74, 0.0, 0.0]]
    assert not direct_links.small_molecule(apart, 1)  # two pieces
