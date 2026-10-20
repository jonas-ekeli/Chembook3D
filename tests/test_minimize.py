"""The Edit structure window's holds and Minimize (D140, A85; T-BLD-08…11). Metal complexes
are morfeus's public SambVca set; Ru is tried by making its Ir complex a Ru one."""

from pathlib import Path

import numpy as np
import pytest

from chembook3d.services import builder, minimize, smiles
from chembook3d.services.builder import BuildError, Hold, Structure
from tests.test_builder import butane, chain, rows_of

STERICS = Path(__file__).parent / "fixtures" / "sterics"


def ru_complex() -> tuple[Structure, int]:
    rows = [["Ru" if r[0] == "Ir" else r[0], *r[1:]] for r in rows_of(STERICS / "3.xyz")]
    s = Structure(rows)
    return s, next(i for i, r in enumerate(rows) if r[0] == "Ru")


def heavy_rmsd(a: Structure, b: Structure) -> float:
    heavy = [i for i, r in enumerate(a.rows) if r[0] != "H"]
    d = a.coords[heavy] - b.coords[heavy]
    return float(np.sqrt((d * d).sum(axis=1).mean()))


def closest_unbonded(s: Structure) -> float:
    graph = builder.neighbours(s)
    coords = s.coords
    d = np.linalg.norm(coords[:, None] - coords[None], axis=2)
    for i, near in enumerate(graph):
        d[i, i] = np.inf
        for j in near:
            d[i, j] = np.inf
    return float(d.min())


@pytest.mark.parametrize("name", ["1.xyz", "4.xyz", "10.xyz", "13.xyz", "15.xyz"])
def test_everything_keeps_metal_complexes(name):
    # T-BLD-08: Ni, Pd, Ti, Mn and Zr complexes barely move when everything is minimized.
    s = Structure(rows_of(STERICS / name))
    done = minimize.minimize(s, everything=True)
    assert done.converged
    assert heavy_rmsd(s, done.structure) < 0.4


def test_ru_keeps_its_shape():
    # T-BLD-08: Ru, which UFF cannot type, is held in its own shape by restraints.
    s, ru = ru_complex()
    done = minimize.minimize(s, everything=True)
    assert done.converged and done.moved == len(s.rows)
    out = done.structure
    assert heavy_rmsd(s, out) < 0.6
    ligands = sorted(builder.neighbours(s)[ru])
    assert len(ligands) == 4
    for lig in ligands:
        before = builder.measure(s.coords, [ru, lig])
        assert builder.measure(out.coords, [ru, lig]) == pytest.approx(before, abs=0.05)
    for a in ligands:
        for b in ligands:
            if a < b:
                before = builder.measure(s.coords, [a, ru, b])
                assert builder.measure(out.coords, [a, ru, b]) == pytest.approx(before, abs=3)


def test_minimize_moves_only_what_changed_and_its_surroundings():
    # T-BLD-09: a tBu put in place of an H, clashing, comes out at a C–C bond length with
    # room round it; atoms more than two bonds from the change and Ru's sphere stay put.
    s, ru = ru_complex()
    graph = builder.neighbours(s)
    hydrogen = 31  # an aryl H on the carbene's ligand
    assert s.rows[hydrogen][0] == "H"
    parent = next(iter(graph[hydrogen]))
    edited = builder.replace_with_fragment(s, [hydrogen], "tBu").structure
    assert edited.changed == [hydrogen, *range(len(s.rows), len(edited.rows))]
    done = minimize.minimize(edited)
    out = done.structure
    near = minimize._near(minimize._molecule(edited)[1], edited.changed)
    assert done.moved == len(near)
    still = [i for i in range(len(out.rows)) if i not in near]
    assert np.allclose(out.coords[still], edited.coords[still])
    assert ru in still
    assert builder.measure(out.coords, [parent, hydrogen]) == pytest.approx(1.54, abs=0.04)
    assert closest_unbonded(out) > 1.6
    assert out.changed == edited.changed  # minimizing does not widen what counts as changed


def test_holds_are_kept():
    # T-BLD-10: a frozen atom stays; held distance, angle and dihedral reach their values.
    s = butane()
    c1, c2, c3, c4 = chain(s)
    s = builder.set_distance(s, [c2, c3], 1.75).structure  # still drawn bonded
    s = builder.freeze(s, [c1]).structure
    s = builder.hold(s, [c1, c2, c3, c4], 60).structure
    s = builder.hold(s, [c1, c2, c3], 118).structure
    s = builder.hold(s, [c3, c4], 1.60).structure
    assert [h.atoms for h in s.holds] == [[c1], [c1, c2, c3, c4], [c1, c2, c3], [c3, c4]]
    out = minimize.minimize(s, everything=True).structure
    coords = out.coords
    assert np.allclose(coords[c1], s.coords[c1])
    assert builder.measure(coords, [c1, c2, c3, c4]) == pytest.approx(60, abs=2)
    assert builder.measure(coords, [c1, c2, c3]) == pytest.approx(118, abs=1)
    assert builder.measure(coords, [c3, c4]) == pytest.approx(1.60, abs=0.02)
    assert builder.measure(coords, [c2, c3]) == pytest.approx(1.53, abs=0.05)  # UFF's C–C


def test_added_bond_to_a_metal_gets_a_length():
    # T-BLD-10: ethylene bonded to Ru by hand comes in to the sum of the covalent radii.
    s, ru = ru_complex()
    s = builder.add_molecule(s, [ru], builder.molecule_rows("ethylene"), "ethylene").structure
    first = len(s.rows) - 6
    for c in (first, first + 1):
        s = builder.set_bond(s, [ru, c], True).structure
    out = minimize.minimize(s).structure
    for c in (first, first + 1):
        assert builder.measure(out.coords, [ru, c]) == pytest.approx(2.22, abs=0.1)


def test_hold_bookkeeping():
    # T-BLD-11: setting a held coordinate moves its hold; deleting renumbers holds and changes;
    # Minimize refuses when nothing changed or everything is frozen.
    s = Structure(butane().rows)
    c1, c2, c3, c4 = chain(s)
    with pytest.raises(BuildError, match="Nothing has changed"):
        minimize.minimize(s)
    held = builder.hold(s, [c1, c2], None).structure
    assert held.holds[0].value == pytest.approx(builder.measure(s.coords, [c1, c2]))
    assert builder.set_distance(held, [c2, c1], 1.7).structure.holds[0].value == pytest.approx(1.7)
    with pytest.raises(BuildError, match="between 0 and 180"):
        builder.hold(s, [c1, c2, c3], 200)
    assert builder.hold(s, [c1, c2, c3, c4], 270).structure.holds[0].value == pytest.approx(-90)
    assert builder.release(held, 0).structure.holds == []
    # A bond stretched out of the drawn range is gone, and the window says so (D140d).
    stretched = builder.set_distance(s, [c2, c3], 1.9)
    assert "no longer drawn bonded" in stretched.message
    every = builder.freeze(s, list(range(len(s.rows)))).structure
    with pytest.raises(BuildError, match="frozen"):
        minimize.minimize(every, everything=True)
    # Deleting an H: its carbon counts as changed, holds move up or go with it.
    hydrogen = next(i for i in builder.neighbours(s)[c1] if s.rows[i][0] == "H")
    s = Structure(s.rows, holds=[Hold([hydrogen]), Hold([c4]), Hold([c3, c4], 1.5)])
    out = builder.delete(s, [hydrogen]).structure

    def shift(i: int) -> int:
        return i - 1 if i > hydrogen else i

    assert out.changed == [shift(c1)]
    assert [h.atoms for h in out.holds] == [[shift(c4)], [shift(c3), shift(c4)]]


def test_api_holds_and_minimize(open_client):
    # T-BLD-11: holds and Minimize through the API, nothing stored.
    s = butane()
    c1, c2, c3, c4 = chain(s)
    body = {"rows": s.rows, "picks": [c2, c3]}
    held = open_client.post("/api/builder/edit", json={**body, "tool": "hold", "value": 1.6})
    assert held.status_code == 200, held.text
    out = held.json()
    assert out["holds"] == [{"atoms": [c2, c3], "value": 1.6}]
    refused = open_client.post("/api/builder/edit", json={**out, "tool": "minimize"})
    assert refused.status_code == 422 and "Nothing has changed" in refused.json()["detail"]
    done = open_client.post(
        "/api/builder/edit", json={**out, "tool": "minimize", "everything": True}
    )
    assert done.status_code == 200, done.text
    result = done.json()
    assert result["message"].startswith(f"Minimized {len(s.rows)} atoms with UFF")
    assert builder.measure(Structure(result["rows"]).coords, [c2, c3]) == pytest.approx(
        1.6, abs=0.02
    )
    released = open_client.post(
        "/api/builder/edit", json={**result, "tool": "release", "index": 0}
    ).json()
    assert released["holds"] == []
    bad = open_client.post(
        "/api/builder/edit",
        json={"rows": s.rows, "holds": [{"atoms": [0, 1]}], "tool": "minimize"},
    )
    assert bad.status_code == 422 and "needs its value" in bad.json()["detail"]
    assert open_client.get("/api/nodes").json() == []


def drawn_hoveyda() -> Structure:
    """A CAAC Hoveyda precatalyst made from a drawing (D142f), its chlorides left and right."""
    from tests.test_drawing import drawn_at

    text = "CC1(C)CC(C)(C)N(c2c(C(C)C)cccc2C(C)C)C1=[Ru]1(Cl)(Cl)=Cc2ccccc2O1C(C)C"
    places = {21: (0, 0), 22: (-1.5, 0), 23: (1.5, 0), 20: (0, 1.5), 31: (0.3, -1.5)}
    return Structure(smiles.from_molfile(drawn_at(text, {**places, 24: (-1, -1.1)})).rows)


def metal_bonds(s: Structure) -> list[set[int]]:
    graph = builder.neighbours(s)
    return [graph[i] for i, e in enumerate(s.elements) if e not in minimize.TYPED]


@pytest.mark.parametrize("make", ["ru", "4.xyz", "drawn"])
def test_minimize_again_stays_put(make):
    # T-BLD-25 (D145): a second and third Minimize with nothing changed in between do not move
    # the atoms, and nothing collapses onto the metal (the drawn Ru complex took a methyl onto
    # Ru on the first Minimize and could not be minimized again).
    if make == "ru":
        s = ru_complex()[0]
    elif make == "drawn":
        s = drawn_hoveyda()
    else:
        s = Structure(rows_of(STERICS / make))
    bonds = metal_bonds(s)
    first = minimize.minimize(s, everything=True)
    assert first.converged
    assert first.structure.minimized_from == s.rows
    assert metal_bonds(first.structure) == bonds
    again = first.structure
    for _ in range(2):
        done = minimize.minimize(again, everything=True)
        assert done.converged and done.largest < 1e-3
        again = done.structure
    assert again.minimized_from == s.rows
    near = minimize.minimize(Structure(again.rows, changed=[0], minimized_from=s.rows))
    assert near.largest < 1e-3


def test_minimized_from_bookkeeping(open_client):
    # T-BLD-25 (D145): holds and bonds keep where Minimize started from; anything that moves
    # atoms starts afresh, and so does the API given another structure's atoms.
    s = butane()
    c1, c2, c3, c4 = chain(s)
    done = minimize.minimize(s, everything=True).structure
    assert done.minimized_from == s.rows
    held = builder.hold(done, [c2, c3], None).structure
    assert held.minimized_from == s.rows
    moved = builder.set_distance(held, [c2, c3], 1.6, c3).structure
    assert moved.minimized_from is None
    body = {"rows": s.rows, "picks": [], "tool": "minimize", "everything": True}
    out = open_client.post("/api/builder/edit", json=body).json()
    assert out["minimized_from"] == s.rows
    again = open_client.post(
        "/api/builder/edit", json={**out, "tool": "minimize", "everything": True}
    ).json()
    assert "the largest move was 0.00 Å" in again["message"]
    other = [["N", *row[1:]] if n == 0 else row for n, row in enumerate(s.rows)]
    ignored = open_client.post(
        "/api/builder/edit",
        json={**out, "tool": "minimize", "everything": True, "minimized_from": other},
    )
    assert ignored.status_code == 200 and ignored.json()["minimized_from"] == out["rows"]
