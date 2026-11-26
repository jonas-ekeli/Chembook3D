"""The minima a TS links, searched from the TS (D263c, A184, A186, FR-MCP-25, T-MCP-51).

Synthetic structures only: the hydrogen moving between two carbons of `test_mechanism_tools`
(the TS halfway, its mode moving the hydrogen), with decoys of its formula (another isomer, a
conformer further away, a copy numbered otherwise); and two methyl radicals joining into ethane,
whose TS is a Gaussian frequency job with its mode, imported as a node, for the + side made of a
methyl node and the methyl free species summed.
"""

import anyio
import numpy as np
import pytest

from chembook3d.claude_panel import NOTEBOOK_GUIDE
from chembook3d.mcp_server import INSTRUCTIONS
from chembook3d.mcp_tools import BY_NAME, PANEL_ALLOWED, input_schema
from chembook3d.services import structure_compare as sc
from chembook3d.services import ts_partners
from tests import gaussian_text as g
from tests.test_atom_matching import scrambled
from tests.test_import import commit, upload
from tests.test_local_follow_mode import frequency_lines
from tests.test_mcp import _app_bridge, _json
from tests.test_mechanism_tools import ETHANE, ref, transfer, ts_structure


def _candidate(label, rows, charge=0, multiplicity=2) -> ts_partners.Candidate:
    return ts_partners.Candidate(sc.Structure(label, rows, charge, multiplicity), {"label": label})


def _turned(rows, atoms, degrees):
    """`atoms` (0-based) turned about the x axis through the first atom."""
    angle = np.radians(degrees)
    turn = np.array(
        [[1, 0, 0], [0, np.cos(angle), -np.sin(angle)], [0, np.sin(angle), np.cos(angle)]]
    )
    centre = np.array(rows[0][1:4])
    out = [r[:] for r in rows]
    for k in atoms:
        out[k] = [rows[k][0], *map(float, (np.array(rows[k][1:4]) - centre) @ turn.T + centre)]
    return out


def _isomer():
    """The moving hydrogen on the fluorine instead: the TS's formula, other bonds."""
    rows = transfer(1.1)
    fluorine = np.array(rows[7][1:4])
    rows[8] = ["H", *map(float, fluorine + [0.0, -0.5, -0.78])]
    return rows


def _sides(found) -> dict[str, list[str]]:
    return {s["side"]: [c["label"] for c in s["candidates"]] for s in found["sides"]}


@pytest.mark.parametrize("order", [None, [8, 3, 2, 1, 0, 7, 6, 5, 4]])
def test_the_pair_is_found_among_decoys_whatever_the_atom_order(order):
    """T-MCP-51: of an isomer, a conformer further away, a copy of the product numbered
    otherwise and another formula, the reactant and product lie on the two sides of the mode,
    nearest first; the best pair links by A184's bond check; the isomer and the other formula
    are on neither side, whatever the TS's own atom order."""
    shuffled, _ = scrambled(transfer(1.9), seed=3)
    candidates = [
        _candidate("isomer", _isomer()),
        _candidate("far conformer", _turned(transfer(1.1), [1, 2, 3], 60)),
        _candidate("product", shuffled),
        _candidate("reactant", transfer(1.1)),
        _candidate("water", [["O", 0, 0, 0], ["H", 0.96, 0, 0], ["H", -0.24, 0.93, 0]]),
    ]
    found = ts_partners.find(ts_structure(order), candidates, [], 0.05)
    assert _sides(found) == {"−": ["reactant", "far conformer"], "+": ["product"]}
    minus, plus = found["sides"]
    assert minus["candidates"][0]["rmsd"] < minus["candidates"][1]["rmsd"]
    assert plus["candidates"][0]["renumbered"] is True
    if order is None:
        assert minus["bonds"] == {"forms": ["C1–H9"], "breaks": ["C5–H9"]}
        assert plus["bonds"] == {"forms": ["C5–H9"], "breaks": ["C1–H9"]}
    check = found["check"]
    assert check["verdict"] == "links", check
    assert (check["a"]["label"], check["b"]["label"]) == ("reactant", "product")
    assert len(check["changed"]) == 2 and found["pairs_checked"] == 1
    assert found["searched"]["structures"] == 5 and found["searched"]["with_the_atoms"] == 4
    assert "medium confidence" in found["next"] and "links" in found["summary"]


def test_no_candidate_gives_the_predicted_bonds_and_the_mode_follow():
    """T-MCP-51: with only the isomer and another formula, both sides are empty; the answer
    still gives each side's predicted bonds and says to follow the mode. A TS without a mode
    is refused."""
    found = ts_partners.find(ts_structure(), [_candidate("isomer", _isomer())], [], 0.05)
    assert [s["found"] for s in found["sides"]] == [0, 0]
    assert found["sides"][0]["bonds"] == {"forms": ["C1–H9"], "breaks": ["C5–H9"]}
    assert found["check"] is None and "start_local_follow_mode" in found["next"]

    # One side only: the follow-mode for the other.
    one = ts_partners.find(ts_structure(), [_candidate("reactant", transfer(1.1))], [], 0.05)
    assert _sides(one) == {"−": ["reactant"], "+": []}
    assert one["check"] is None and "for the + side" in one["next"]

    with pytest.raises(sc.StructureError, match="no imaginary mode"):
        ts_partners.find(sc.Structure("flat", transfer(1.5), 0, 2), [], [], 0.05)


# Two methyl radicals joining: the carbons 2.2 Å apart in the TS, its mode moving them apart.
ANGLES = ((0, 120, 240), (60, 180, 300))


def _methyl_pair(distance: float) -> list[tuple]:
    """Atoms (element, Z, x, y, z) in ETHANE's order: C1, C2, C1's hydrogens, C2's."""
    half = distance / 2
    atoms = [("C", 6, -half, 0.0, 0.0), ("C", 6, half, 0.0, 0.0)]
    for side, angles in zip((-1, 1), ANGLES, strict=True):
        for a in angles:
            r = np.radians(a)
            atoms.append(("H", 1, side * (half + 0.36), 1.03 * np.cos(r), 1.03 * np.sin(r)))
    return atoms


METHYL = [["C", 0.0, 0.0, 0.0]] + [
    ["H", -0.36, 1.03 * np.cos(np.radians(a)), 1.03 * np.sin(np.radians(a))] for a in ANGLES[0]
]


def _ts_output() -> str:
    atoms = _methyl_pair(2.2)
    still = [[0.0, 0.0, 0.0] for _ in atoms]
    apart = [[-0.5, 0.0, 0.0], [0.5, 0.0, 0.0], *still[2:]]
    others = [[[0.0, 0.0, 0.1] if k == n else [0.0, 0.0, 0.0] for k in range(8)] for n in (2, 5)]
    modes = [apart, *others]
    return g.output(
        g.step(
            "#P B3LYP/6-31G(d) Freq",
            [atoms],
            -79.0,
            title="ts",
            after=[*frequency_lines([-450.0, 300.0, 900.0], modes, atoms), *g.THERMO.splitlines()],
        )
    )


def _node(client, label, rows, **fields) -> str:
    made = client.post("/api/nodes", json={"label": label, "xyz": ref(rows)["xyz"], **fields})
    assert made.status_code == 201, made.text
    return made.json()["id"]


def _partners(client, **body):
    return client.post("/api/structures/ts-partners", json=body)


def test_a_side_is_a_minimum_and_a_free_species_from_the_investigation(open_client):
    """T-MCP-51: a TS node imported with its frequency job; by default every minimum and free
    species is searched, and a methyl node plus the methyl free species is the + side (its
    RMSD over the methyl); ethane is the − side; the pair links, with references to pass on.
    The TS itself, another TS and a node of other atoms are not candidates; `species` [] leaves
    sums out; a node without a mode as the TS is refused; another site is refused."""
    ts = commit(open_client, upload(open_client, "ts.log", _ts_output()))["node_id"]
    open_client.patch(f"/api/nodes/{ts}", json={"label": "TS", "role": "transition_state"})
    ethane = _node(open_client, "ethane", ETHANE, role="minimum")
    _node(open_client, "methyl", METHYL, role="minimum", multiplicity=2)
    radical = _node(open_client, "CH3", METHYL, kind="species", multiplicity=2)
    _node(open_client, "water", [["O", 0, 0, 0], ["H", 0.96, 0, 0], ["H", -0.24, 0.93, 0]])
    _node(open_client, "other TS", ETHANE, role="transition_state")

    response = _partners(open_client, ts={"node_id": ts})
    assert response.status_code == 200, response.text
    found = response.json()
    assert found["mode"]["active"] == [{"bond": "C1–C2", "share": 1.0}]
    minus, plus = found["sides"]
    assert [c["node_id"] for c in minus["candidates"]] == [ethane]
    assert minus["bonds"] == {"forms": ["C1–C2"]} and plus["bonds"] == {"breaks": ["C1–C2"]}
    labels = [c["label"] for c in plus["candidates"]]
    assert set(labels) == {"methyl + CH3", "CH3 + CH3"}, labels
    first = plus["candidates"][0]
    assert first["species_node_id"] == radical and first["rmsd"] < 0.1
    check = found["check"]
    assert check["verdict"] == "links", check
    assert check["a"]["node_id"] == ethane and check["a"]["ref"] == {"node_id": ethane}
    assert check["b"]["species_node_id"] == radical and isinstance(check["b"]["ref"], list)
    assert check["changed"] == ["C1–C2"]
    assert found["searched"]["structures"] == 4  # ethane, methyl, CH3, water: no TS

    alone = _partners(open_client, ts={"node_id": ts}, species=[]).json()
    assert [s["found"] for s in alone["sides"]] == [1, 0] and alone["check"] is None
    assert "for the + side" in alone["next"]

    given = _partners(
        open_client, ts={"node_id": ts}, candidates=[{"node_id": ethane}], species=[]
    ).json()
    assert [s["found"] for s in given["sides"]] == [1, 0] and given["check"] is None

    refused = _partners(open_client, ts={"node_id": ethane})
    assert refused.status_code == 422 and "no imaginary mode" in refused.json()["detail"]
    foreign = open_client.post(
        "/api/structures/ts-partners",
        json={"ts": {"node_id": ts}},
        headers={"origin": "https://x.org"},
    )
    assert foreign.status_code == 403


def test_the_tool_over_mcp_and_in_the_guides(tmp_path):
    """T-MCP-51: a read tool the panel does not ask about, named in both guides and the
    playbook's linking step; over MCP with the TS as an output file and the candidates as XYZ
    text."""
    tool = BY_NAME["find_ts_partners"]
    assert tool.kind == "read" and "find_ts_partners" in PANEL_ALLOWED
    for text in (INSTRUCTIONS, NOTEBOOK_GUIDE):
        assert "find_ts_partners" in text
    schema = input_schema(tool)["properties"]
    assert "calculation_id" in schema["ts"]["description"]
    assert {"candidates", "species", "top"} <= set(schema)
    path = tmp_path / "ts.log"
    path.write_text(_ts_output(), encoding="utf-8")

    async def run():
        bridge, page = _app_bridge()
        await page.post("/api/investigations", json={"folder": str(tmp_path / "i"), "name": "T"})
        found = _json(
            await bridge.call(
                "find_ts_partners",
                {
                    "ts": {"path": str(path)},
                    "candidates": [ref(ETHANE, label="ethane")],
                    "species": [ref(METHYL, label="CH3", multiplicity=2)],
                },
            )
        )
        assert _sides(found) == {"−": ["ethane"], "+": ["CH3 + CH3"]}
        assert found["check"]["verdict"] == "links"
        assert "ref" not in found["check"]["a"]  # XYZ text is not echoed back

    anyio.run(run)
