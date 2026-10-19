"""The Edit structure window's tools (D139, A84; T-BLD-01…07)."""

from pathlib import Path

import numpy as np
import pytest

from chembook3d import pathtools, xyz
from chembook3d.services import builder
from chembook3d.services.builder import BuildError, Structure

FIXTURES = Path(__file__).parent / "fixtures"


def rows_of(path: Path) -> list[list]:
    return [[a.element, a.x, a.y, a.z] for a in xyz.parse_xyz(path.read_text(encoding="utf-8"))]


def butane() -> Structure:
    """n-butane built from the fragments: CH3–CH2–CH2–CH3, atoms 0–3 the carbons."""
    s = Structure([["C", 0.0, 0.0, 0.0], ["H", 1.09, 0.0, 0.0]])
    for _ in range(3):
        s = builder.add_atom(s, [0], "H").structure
    s = builder.replace_with_fragment(s, [1], "Et").structure  # C0–C1(H2)–C(H3)
    s = builder.replace_with_fragment(s, [2], "Me").structure  # one H of C0 becomes CH3
    return s


def carbons(s: Structure) -> list[int]:
    return [i for i, row in enumerate(s.rows) if row[0] == "C"]


def chain(s: Structure) -> list[int]:
    """The four carbons in chain order."""
    graph = builder.neighbours(s)
    cs = carbons(s)
    ends = [c for c in cs if sum(s.rows[o][0] == "C" for o in graph[c]) == 1]
    order = [ends[0]]
    while len(order) < 4:
        order.append(next(o for o in graph[order[-1]] if s.rows[o][0] == "C" and o not in order))
    return order


def assert_fragment_sane(s: Structure) -> None:
    coords = s.coords
    d = np.linalg.norm(coords[:, None] - coords[None], axis=2)
    np.fill_diagonal(d, 9)
    assert d.min() > 0.9


def test_fragments_attach_at_bond_lengths_without_overlaps():
    # T-BLD-01: each fragment on methane's H, at the covalent radii sum, its formula right.
    expected = {
        "Me": "C2H6",
        "Et": "C3H8",
        "iPr": "C4H10",
        "tBu": "C5H12",
        "Ph": "C7H8",
        "CF3": "C2H3F3",
        "OMe": "C2H6O",
        "OH": "CH4O",
        "NH2": "CH5N",
        "Cl": "CH3Cl",
    }
    for name, formula in expected.items():
        s = Structure([["C", 0.0, 0.0, 0.0]])
        for _ in range(4):
            s = builder.add_atom(s, [0], "H").structure
        done = builder.replace_with_fragment(s, [1], name)
        out = done.structure
        assert builder.formula(out.rows) == formula, name
        attach = out.rows[1][0]
        length = np.linalg.norm(out.coords[1] - out.coords[0])
        assert length == pytest.approx(0.76 + builder.RADII[attach], abs=1e-6), name
        assert_fragment_sane(out)
        # The fragment's own atoms are bonded as the 3D view draws them: one molecule.
        graph = builder.neighbours(out)
        assert len(builder._reach(graph, 0, set())) == len(out.rows), name


def test_added_hydrogens_point_away():
    # T-BLD-01: hydrogens added one by one to a carbon end up roughly tetrahedral.
    s = Structure([["C", 0.0, 0.0, 0.0]])
    for _ in range(4):
        s = builder.add_atom(s, [0], "H").structure
    angles = [builder.measure(s.coords, [i, 0, j]) for i in range(1, 5) for j in range(i + 1, 5)]
    assert min(angles) > 90
    assert builder.formula(s.rows) == "CH4"


def test_distance_angle_and_dihedral_move_the_smaller_side():
    # T-BLD-02
    s = butane()
    c1, c2, c3, c4 = chain(s)
    found = builder.sides(s, [c1, c2])
    assert [side.whole for side in found] == [True, True]
    assert builder.default_side(found) == c1  # the methyl, 4 atoms, against 10
    before = s.coords
    done = builder.set_distance(s, [c1, c2], 1.70)
    after = done.structure.coords
    assert builder.measure(after, [c1, c2]) == pytest.approx(1.70, abs=1e-6)
    moved = [i for i in range(len(after)) if np.linalg.norm(after[i] - before[i]) > 1e-9]
    assert sorted(moved) == next(f.atoms for f in found if f.atom == c1)
    # The other side, by choice.
    other = builder.set_distance(s, [c1, c2], 1.70, move=c2).structure.coords
    assert np.allclose(other[c1], before[c1])

    for value, move in ((100.0, None), (125.0, c1), (125.0, c3)):
        out = builder.set_angle(s, [c1, c2, c3], value, move).structure.coords
        assert builder.measure(out, [c1, c2, c3]) == pytest.approx(value, abs=1e-6)
    for value in (60.0, -60.0, 180.0, -179.0, 0.0):
        for move in (c1, c4):
            out = builder.set_dihedral(s, [c1, c2, c3, c4], value, move).structure.coords
            assert builder.measure(out, [c1, c2, c3, c4]) == pytest.approx(value, abs=1e-6)
            # The same sign as pathtools (and so as the 3D view and xTB).
            rows = [[r[0], *p] for r, p in zip(s.rows, out, strict=True)]
            measured = pathtools.measure(rows, [c1 + 1, c2 + 1, c3 + 1, c4 + 1])
            assert measured == pytest.approx(value, abs=1e-4)
            # Bond lengths do not change.
            assert builder.measure(out, [c2, c3]) == pytest.approx(
                builder.measure(s.coords, [c2, c3])
            )


def test_turning_about_a_bond():
    s = butane()
    c1, c2, c3, c4 = chain(s)
    start = builder.measure(s.coords, [c1, c2, c3, c4])
    out = builder.rotate_about(s, [c2, c3], 120).structure.coords
    turned = builder.measure(out, [c1, c2, c3, c4])
    assert abs((turned - start + 180) % 360 - 180) == pytest.approx(120, abs=1e-6)


def test_ring_moves_only_the_atom_and_its_ends():
    # T-BLD-03: two carbons of a phenyl ring are joined both ways round.
    s = Structure([["C", 0.0, 0.0, 0.0]])
    for _ in range(4):
        s = builder.add_atom(s, [0], "H").structure
    s = builder.replace_with_fragment(s, [1], "Ph").structure
    graph = builder.neighbours(s)
    ring = [i for i in range(len(s.rows)) if s.rows[i][0] == "C" and i != 0]
    a = ring[1]
    b = next(o for o in graph[a] if o in ring and o != ring[0])
    found = builder.sides(s, [a, b])
    assert [f.whole for f in found] == [False, False]
    h_of_b = [o for o in graph[b] if s.rows[o][0] == "H"]
    assert next(f for f in found if f.atom == b).atoms == sorted([b, *h_of_b])
    done = builder.set_distance(s, [a, b], 1.50, move=b)
    assert builder.measure(done.structure.coords, [a, b]) == pytest.approx(1.50)
    assert "the sides are joined" in done.message
    with pytest.raises(BuildError, match="joined"):
        builder.rotate_about(s, [a, b], 30)


def test_metal_chelate_and_separate_molecules():
    # T-BLD-03: in a public Pd complex the two P atoms bound to Pd are joined through the
    # chelate only if they are; a molecule put beside it moves as a whole.
    s = Structure(rows_of(FIXTURES / "sterics" / "4.xyz"))
    metal = s.elements.index("Pd")
    graph = builder.neighbours(s)
    donors = sorted(graph[metal])
    assert len(donors) == 4
    # Metal–donor distance: the donor's whole ligand, or only the donor if it is a chelate.
    for donor in donors:
        found = builder.sides(s, [metal, donor])
        donor_side = next(f for f in found if f.atom == donor)
        done = builder.set_distance(s, [metal, donor], 2.6, move=donor)
        assert builder.measure(done.structure.coords, [metal, donor]) == pytest.approx(2.6)
        if donor_side.whole:
            assert metal not in donor_side.atoms
    # A molecule beside the metal's open side, never closer than 2 Å.
    beside = builder.add_molecule(s, [metal], builder.molecule_rows("ethylene"), "ethylene")
    out = beside.structure
    new = list(range(len(s.rows), len(out.rows)))
    d = np.linalg.norm(out.coords[new][:, None] - out.coords[: len(s.rows)][None], axis=2)
    assert d.min() >= 2.0
    assert builder.formula(out.rows) == builder.formula(s.rows + builder.molecule_rows("ethylene"))
    found = builder.sides(out, [metal, new[0]])
    assert next(f for f in found if f.atom == new[0]).atoms == new  # separate: moves whole
    assert builder.default_side(found) == new[0]


def test_bonds_added_and_removed_decide_the_sides():
    # T-BLD-04
    s = butane()
    c1, c2, c3, c4 = chain(s)
    joined = builder.set_bond(s, [c1, c4], True).structure
    assert joined.added == [tuple(sorted((c1, c4)))]
    assert [f.whole for f in builder.sides(joined, [c2, c3])] == [False, False]
    # Removing the bond the 3D view draws splits the molecule.
    split = builder.set_bond(s, [c2, c3], False).structure
    assert split.removed == [tuple(sorted((c2, c3)))]
    assert not builder.bonded(split, c2, c3)
    # Adding it back only takes the removal away.
    again = builder.set_bond(split, [c2, c3], True).structure
    assert again.added == [] and again.removed == []


def test_delete_renumbers_and_keeps_bond_changes():
    # T-BLD-05
    s = butane()
    c1, c2, c3, c4 = chain(s)
    s = builder.set_bond(s, [c1, c4], True).structure
    graph = builder.neighbours(s)
    hydrogens = [o for o in graph[c4] if s.rows[o][0] == "H"]
    done = builder.delete(s, [c4], with_hydrogens=True)
    out = done.structure
    assert len(out.rows) == len(s.rows) - 1 - len(hydrogens)
    assert builder.formula(out.rows) == "C3H7"
    assert out.added == []  # the bond went with its atom
    # Deleting an atom keeps a bond added between atoms after it, renumbered.
    s2 = builder.set_bond(s, [c2, c3], False).structure
    first_h = next(i for i, row in enumerate(s2.rows) if row[0] == "H")
    out2 = builder.delete(s2, [first_h]).structure
    shift = lambda i: i - 1 if i > first_h else i  # noqa: E731
    assert (shift(min(c2, c3)), shift(max(c2, c3))) in out2.removed


def test_element_change_and_refusals():
    # T-BLD-06
    s = butane()
    c1, c2, c3, c4 = chain(s)
    out = builder.change_element(s, [c1], "si").structure
    assert out.rows[c1][0] == "Si"
    with pytest.raises(BuildError, match="not an element"):
        builder.change_element(s, [c1], "Xx")
    with pytest.raises(BuildError, match="exactly one other atom"):
        builder.replace_with_fragment(s, [c2], "Me")
    with pytest.raises(BuildError, match="Pick 2 atoms"):
        builder.set_distance(s, [c1], 1.5)
    # A move putting atoms on top of each other is refused.
    with pytest.raises(BuildError, match="on top of"):
        builder.set_dihedral(builder.set_angle(s, [c1, c2, c3], 30).structure, [c1, c2, c3, c4], 0)


def test_electron_count_against_multiplicity():
    # T-BLD-07
    water = [["O", 0, 0, 0], ["H", 0.96, 0, 0], ["H", -0.24, 0.93, 0]]
    assert builder.electrons_note(water, 0, 1) is None
    note = builder.electrons_note(water[:2], 0, 1)
    assert note and "9 electrons" in note
    assert builder.electrons_note(water[:2], 0, 2) is None
    assert builder.electrons_note(water[:2], -1, 1) is None


def test_api_edits_without_storing(open_client):
    # T-BLD-07: the window's tools through the API change nothing in the investigation.
    s = butane()
    c1, c2, c3, c4 = chain(s)
    body = {"rows": s.rows, "picks": [c1, c2, c3, c4], "charge": 0, "multiplicity": 1}
    described = open_client.post("/api/builder/describe", json=body).json()
    assert described["kind"] == "dihedral" and described["formula"] == "C4H10"
    assert [side["atom"] for side in described["sides"]] == [c1, c4]
    edited = open_client.post(
        "/api/builder/edit", json={**body, "tool": "dihedral", "value": 60, "move": c4}
    )
    assert edited.status_code == 200, edited.text
    out = edited.json()
    assert builder.measure(Structure(out["rows"]).coords, [c1, c2, c3, c4]) == pytest.approx(60)
    refused = open_client.post("/api/builder/edit", json={**body, "tool": "distance", "value": 1.5})
    assert refused.status_code == 422 and "Pick 2 atoms" in refused.json()["detail"]
    other = open_client.post(
        "/api/builder/edit",
        json={
            "rows": s.rows,
            "picks": [],
            "tool": "molecule",
            "atoms": [["O", 0, 0, 0]],
            "what": "“W”",
        },
    )
    assert other.status_code == 200 and other.json()["message"].startswith("Added “W”")
    bad = open_client.post("/api/builder/describe", json={"rows": [["Q", 0, 0, 0]]})
    assert bad.status_code == 422
    parts = open_client.get("/api/builder/parts").json()
    assert "tBu" in parts["fragments"] and "ethylene" in parts["molecules"]
    assert open_client.get("/api/nodes").json() == []


def test_save_as_new_node(open_client):
    # T-BLD-07: "Save as a new node" derives one even from a node without calculations (D139).
    from tests.conftest import WATER

    node = open_client.post("/api/nodes", json={"label": "W", "xyz": WATER}).json()
    moved = WATER.replace("0.117300", "0.130000")
    response = open_client.put(
        f"/api/nodes/{node['id']}/geometry", json={"xyz": moved, "new_node": True}
    )
    assert response.status_code == 200
    saved = response.json()
    assert saved["derived"] is True and saved["node"]["id"] != node["id"]
    assert saved["node"]["derived_from_id"] == node["id"]
    assert open_client.get(f"/api/nodes/{node['id']}").json()["xyz"] == node["xyz"]
