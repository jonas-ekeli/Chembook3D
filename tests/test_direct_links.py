"""Steps between minima with no TS: a species joining or leaving, a conformation change
(FR-MCP-27, A190, T-MCP-53, T-MCP-54).

Synthetic structures: a ruthenium carbene with two chlorides (A, "the 14-electron complex"),
ethene, and two π-complexes of them, the ethene's C=C along y (B) or along x (B2, turned
90°), each numbered otherwise than A with the ethene's atoms among the complex's."""

import json

import anyio

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
    association = [p for p in pairs if p["kind"] == "association"]
    assert {(p["a"]["node_id"], p["b"]["node_id"]) for p in association} == {(a, b), (a, b2)}
    for p in association:
        assert p["species"] == {"label": "ethene", "node_id": ethene, "make_species": True}
        assert len(p["bonds_to_core"]) == 2
        assert all("Ru" in bond and "C" in bond for bond in p["bonds_to_core"])
        assert p["core_rmsd"] < direct_links.MEDIUM_RMSD and p["confidence"] == "medium"
    assert association[0]["alike"] == [1, 2] and association[1]["alike"] == [2, 2]
    (turn,) = [p for p in pairs if p["kind"] == "same_atoms"]
    assert {turn["a"]["node_id"], turn["b"]["node_id"]} == {b, b2}
    assert turn["note"] == "no TS: direct link?" and turn["confidence"] == "low"
    assert turn["rmsd"] > 0.05 and "metal_bonds" not in turn
    assert found["counts"] == {"association": 2, "same_atoms": 1}
    assert found["make_species"] == ["ethene"] and "more" not in found
    assert "should be free species" in found["summary"]
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
    assert len(rest["pairs"]) == 2 and "more" not in rest

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
