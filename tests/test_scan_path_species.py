"""A free species joining or leaving on a scan path (D120, T-PATH-20…22): the species from the
edge, the match of the start beside the species against the bound end, and the separated
structure the app builds by pulling the species straight out. The complex is made up
(`olefin_complex`), never one of Jonas's structures."""

import math

import numpy as np
import pytest

from chembook3d import pathtools
from chembook3d.services import scan_species
from tests.test_atom_matching import scrambled
from tests.test_overlay import xyz_text
from tests.test_pathway import edge, node
from tests.test_scan_path import rows_of
from tests.test_scan_path_drive import olefin_complex
from tests.test_species import put

OLEFIN = [0, 1, 8, 9, 10, 11]  # ethylene's atoms in `olefin_complex`
BOUND = olefin_complex(math.radians(15))
ALKYLIDENE = [r for i, r in enumerate(BOUND) if i not in OLEFIN]  # C3, H, H, Ru, Cl, Cl


def _ethylene() -> list[list]:
    """Free ethylene, numbered H, C, H, C, H, H, turned and far away."""
    rows = [BOUND[i] for i in (8, 0, 9, 1, 10, 11)]
    c, s = math.cos(0.7), math.sin(0.7)
    return [[e, x * c - y * s + 20, x * s + y * c - 5, z + 3] for e, x, y, z in rows]


def binding(client, *, scramble: bool = True, **fields) -> dict:
    """The alkylidene, ethylene as a free species and the bound complex (numbered and turned
    differently), joined by an edge on which ethylene joins."""
    end_rows = scrambled(BOUND, seed=7)[0] if scramble else BOUND
    ids = {
        "start": node(client, label="alkylidene", xyz=xyz_text(ALKYLIDENE), charge=0,
                      multiplicity=1)["id"],
        "end": node(client, label="π-complex", xyz=xyz_text(end_rows), charge=0, multiplicity=1,
                    **fields)["id"],
        "ethylene": node(client, label="ethylene", kind="species", xyz=xyz_text(_ethylene()),
                         charge=0, multiplicity=1)["id"],
    }  # fmt: skip
    ids["edge"] = edge(client, ids["start"], ids["end"])["id"]
    return ids


def attach(client, ids, species: str = "ethylene", direction: str = "joins", count: int = 1):
    body = {"species_id": ids[species], "direction": direction, "count": count}
    put(client, f"/transitions/{ids['edge']}/species", body)


def same(a: list[list], b: list[list]) -> bool:
    return [r[0] for r in a] == [r[0] for r in b] and np.allclose(
        [r[1:] for r in a], [r[1:] for r in b], atol=1e-5
    )


def plan(client, body: dict, status: int = 200) -> dict:
    response = client.post("/api/scan-paths/plan", json=body)
    assert response.status_code == status, response.text
    return response.json()


def _expected_bonds() -> set[tuple[int, int]]:
    """The bound complex's bonds in the start's numbering: the alkylidene's atoms, then
    ethylene's in the species node's order."""
    order = [i for i in range(len(BOUND)) if i not in OLEFIN] + [8, 0, 9, 1, 10, 11]
    where = {k: n + 1 for n, k in enumerate(order)}
    return {tuple(sorted((where[a - 1], where[b - 1]))) for a, b in pathtools.bonds(BOUND)}


def test_a_species_that_joins_is_matched_and_pulled_out(open_client):
    # T-PATH-20 (D120): the start beside the species is matched to the bound end; the
    # ethylene's atoms follow the start's (7 to 12), its two bonds to Ru form, and the
    # separated start has the alkylidene's own geometry with ethylene's own geometry pulled
    # straight out until it is 4 Å from the complex.
    ids = binding(open_client)
    attach(open_client, ids)
    out = plan(open_client, {"start_id": ids["start"], "end_id": ids["end"]})
    species = out["species"]
    assert species["label"] == "ethylene" and species["direction"] == "joins"
    assert species["bound"] == "end" and species["atoms"] == [7, 8, 9, 10, 11, 12]
    assert {tuple(b) for b in species["bonds"]} == {(4, 8), (4, 10)}
    assert species["anchor"] == [4]
    assert species["closest"] == pytest.approx(4.0, abs=0.03) and species["pull"] > 1.0
    match = out["match"]
    assert match["confident"] and match["doubts"] == []
    assert sorted(map(tuple, match["formed"])) == [(4, 8), (4, 10)] and match["broken"] == []
    renumbered = rows_of(match["renumbered_xyz"])
    assert {tuple(sorted(p)) for p in pathtools.bonds(renumbered)} == _expected_bonds()

    separated = rows_of(species["separated_xyz"])
    assert len(separated) == 12 and same(rows_of(match["start_xyz"]), separated)
    # The complex keeps its own geometry, ethylene its own, and nothing bonds across.
    assert pathtools.rmsd(separated[:6], ALKYLIDENE) < 1e-6
    assert pathtools.rmsd(separated[6:], _ethylene()) < 1e-6
    assert pathtools.bonds(separated) == {
        (a, b) for a, b in pathtools.bonds(renumbered) if (a > 6) == (b > 6)
    }
    # It left along the line from Ru through its centre, so it faces Ru as it does bound.
    xyz = np.array([r[1:] for r in separated])
    bound = np.array([r[1:] for r in renumbered])
    line = bound[6:].mean(axis=0) - bound[3]
    moved = xyz[6:].mean(axis=0) - bound[6:].mean(axis=0)
    assert np.dot(line, moved) / np.linalg.norm(line) / np.linalg.norm(moved) > 0.99
    # Farther out on request.
    far = plan(open_client, {"start_id": ids["start"], "end_id": ids["end"], "clearance": 6})
    assert far["species"]["closest"] == pytest.approx(6.0, abs=0.03)
    assert far["species"]["pull"] == pytest.approx(species["pull"] + 2.0, abs=0.3)
    assert (
        "3 to 12 Å"
        in plan(open_client, {"start_id": ids["start"], "end_id": ids["end"], "clearance": 1}, 422)[
            "detail"
        ]
    )


def test_a_species_that_leaves_runs_the_other_way(open_client):
    # T-PATH-21 (D120): from the bound complex, the same edge read backwards: ethylene leaves,
    # its atoms are the bound complex's own, and the separated structure is the end.
    ids = binding(open_client, scramble=False)
    attach(open_client, ids)
    out = plan(open_client, {"start_id": ids["end"], "end_id": ids["start"]})
    species = out["species"]
    assert species["direction"] == "leaves" and species["bound"] == "start"
    assert species["atoms"] == [1, 2, 9, 10, 11, 12]
    assert sorted(map(tuple, species["bonds"])) == [(1, 6), (2, 6)]
    assert sorted(map(tuple, out["match"]["broken"])) == [(1, 6), (2, 6)]
    separated = rows_of(species["separated_xyz"])
    assert same(rows_of(out["match"]["renumbered_xyz"]), separated)
    assert pathtools.rmsd([separated[i] for i in OLEFIN], [BOUND[i] for i in OLEFIN]) < 0.05
    assert species["closest"] == pytest.approx(4.0, abs=0.03)
    assert len(rows_of(out["match"]["end_xyz"])) == 12
    # Sending waits for the brief (D120's second part).
    body = {"start_id": ids["end"], "end_id": ids["start"], "start": False}
    refused = open_client.post("/api/scan-paths", json=body)
    assert refused.status_code == 422 and "not sent yet" in refused.text


def test_the_species_on_the_edge_is_checked(open_client):
    # T-PATH-22 (D120): no species says which formula is missing; another species, two
    # species, two molecules, other charges and a TS without the species are refused.
    ids = binding(open_client, scramble=False)
    body = {"start_id": ids["start"], "end_id": ids["end"]}
    assert "C2H4 joins or leaves" in plan(open_client, body, 422)["detail"]
    ids["water"] = node(open_client, label="water", kind="species", charge=0, multiplicity=1,
                        xyz=xyz_text([("O", 0, 0, 0), ("H", 0.76, 0.59, 0), ("H", -0.76, 0.59, 0)])
                        )["id"]  # fmt: skip
    attach(open_client, ids, "water")
    assert "does not hold the atoms" in plan(open_client, body, 422)["detail"]
    attach(open_client, ids)
    assert "one species at a time" in plan(open_client, body, 422)["detail"]
    open_client.delete(f"/api/transitions/{ids['edge']}/species/{ids['water']}")
    attach(open_client, ids, count=2)
    assert "one molecule" in plan(open_client, body, 422)["detail"]
    attach(open_client, ids, count=1)
    open_client.patch(f"/api/nodes/{ids['ethylene']}", json={"charge": 1})
    assert "does not have the charge" in plan(open_client, body, 422)["detail"]
    open_client.patch(f"/api/nodes/{ids['ethylene']}", json={"charge": 0})
    assert plan(open_client, body)["species"]["direction"] == "joins"
    open_client.patch(f"/api/nodes/{ids['start']}", json={"role": "transition_state"})
    assert "a TS end is the one where" in plan(open_client, body, 422)["detail"]


def test_separate_on_its_own():
    # The rule itself: a species with no bond to the complex leaves from its nearest atom, and
    # one already far enough away is not moved.
    complex_rows = [["Ru", 0.0, 0.0, 0.0], ["Cl", 0.0, 0.0, -2.3]]
    water = [["O", 2.6, 0.0, 0.0], ["H", 3.2, 0.7, 0.0], ["H", 3.2, -0.7, 0.0]]
    rows, pull, closest, anchor, rmsd = scan_species.separate(
        complex_rows + water, complex_rows + water, [2, 3, 4], []
    )
    assert anchor == [0] and closest == pytest.approx(4.0, abs=0.03) and rmsd < 1e-9
    assert rows[2][1] > water[0][1] and abs(rows[2][2]) < 1e-9
    far = [[e, x + 10, y, z] for e, x, y, z in water]
    _, pull, closest, _, _ = scan_species.separate(
        complex_rows + far, complex_rows + far, [2, 3, 4], []
    )
    assert pull == 0 and closest > 10
