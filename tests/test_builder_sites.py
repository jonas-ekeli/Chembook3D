"""A metal's open site, molecules bound there, hydrogens counted on a metal's ligand atoms and
clashes named by their sides (D148, A93; T-BLD-27, T-BLD-28). Public structures only."""

import numpy as np
import pytest

from chembook3d.services import builder, minimize
from chembook3d.services.builder import BuildError, Structure
from tests.test_builder import FIXTURES, rows_of
from tests.test_minimize import ru_complex


def palladium() -> tuple[Structure, int]:
    s = Structure(rows_of(FIXTURES / "sterics" / "4.xyz"))
    return s, s.elements.index("Pd")


def bond_angles(s: Structure, metal: int, direction: np.ndarray) -> list[float]:
    graph = builder.neighbours(s)
    return [
        float(np.degrees(np.arccos(direction @ builder._unit(s.coords[b] - s.coords[metal]))))
        for b in graph[metal]
    ]


def ratio_to_rest(s: Structure, new: list[int], metal: int) -> float:
    """The closest new atom to an old one but the metal, over their covalent radii sum."""
    old = [i for i in range(len(s.rows)) if i not in set(new) and i != metal]
    radii = np.array([builder._radius(e) for e in s.elements])
    d = np.linalg.norm(s.coords[new][:, None] - s.coords[old][None], axis=2)
    return float((d / (radii[new][:, None] + radii[old][None])).min())


def test_open_site_and_a_molecule_bound_there():
    # T-BLD-27: the open site of a square-planar Pd lies off its plane; ethylene bound η² sits
    # with both carbons at the distance asked for, its C=C square to the metal, clear of the
    # rest, and the carbons are bonded to the metal for Minimize.
    s, pd = palladium()
    site = builder.open_site(s.coords, builder.neighbours(s), pd)
    assert min(bond_angles(s, pd, site)) > 70
    done = builder.add_molecule(s, [pd], builder.molecule_rows("ethylene"), "ethylene", 2.15)
    out, first = done.structure, len(s.rows)
    assert done.message == (
        f"Added ethylene as atoms {first + 1}–{first + 6}, bonded to atom {pd + 1} through "
        f"{first + 1}, {first + 2} at 2.15 Å"
    )
    c1, c2 = first, first + 1
    assert builder.measure(out.coords, [pd, c1]) == pytest.approx(2.15)
    assert builder.measure(out.coords, [pd, c2]) == pytest.approx(2.15)
    middle = (out.coords[c1] + out.coords[c2]) / 2
    axis = builder._unit(out.coords[c2] - out.coords[c1])
    assert abs(axis @ builder._unit(middle - out.coords[pd])) < 1e-6
    assert ratio_to_rest(out, list(range(first, first + 6)), pd) > 1.2  # nothing drawn bonded
    assert {builder._pair(pd, c1), builder._pair(pd, c2)} <= set(out.added)
    for h in range(first + 2, first + 6):
        assert builder.measure(out.coords, [pd, h]) > 2.5

    # Water binds through its O with its H turned away; CO through its C, end on.
    water = builder.add_molecule(s, [pd], builder.molecule_rows("water"), "water", 2.2).structure
    o, h1, h2 = first, first + 1, first + 2
    assert builder.measure(water.coords, [pd, o]) == pytest.approx(2.2)
    assert min(builder.measure(water.coords, [pd, h]) for h in (h1, h2)) > 2.5
    co = builder.add_molecule(s, [pd], builder.molecule_rows("CO"), "CO", 1.9).structure
    assert builder.measure(co.coords, [pd, first, first + 1]) == pytest.approx(180, abs=1e-3)

    # A SMILES or another structure binds by the donors given (its own atoms from 0).
    pyridine = [
        ["C", 1.39, 0.0, 0.0],
        ["N", 0.69, 1.2, 0.0],
        ["C", -0.69, 1.2, 0.0],
        ["C", -1.39, 0.0, 0.0],
        ["C", -0.69, -1.2, 0.0],
        ["C", 0.69, -1.2, 0.0],
    ]
    bound = builder.add_molecule(s, [pd], pyridine, "pyridine", 2.1, [1]).structure
    assert builder.measure(bound.coords, [pd, first + 1]) == pytest.approx(2.1)
    far = builder.measure(bound.coords, [pd, first + 1, first + 4])
    assert far == pytest.approx(180, abs=1)  # the ring points away along the N's lone pair

    with pytest.raises(BuildError, match="Pick the atom the molecule binds to"):
        builder.add_molecule(s, [], builder.molecule_rows("water"), "water", 2.2)
    with pytest.raises(BuildError, match="numbered 1 to 3"):
        builder.add_molecule(s, [pd], builder.molecule_rows("water"), "water", 2.2, [3])
    with pytest.raises(BuildError, match="between 1 and 5"):
        builder.add_molecule(s, [pd], builder.molecule_rows("water"), "water", 9.0)


def test_bound_ethylene_survives_minimize_at_a_crowded_ru():
    # T-BLD-27: in the crowded Ru complex the open sites are tried in turn; after Minimize the
    # olefin's H stay off the metal (D148: they keep their distance or 2.6 Å).
    s, ru = ru_complex()
    first = len(s.rows)
    out = builder.add_molecule(s, [ru], builder.molecule_rows("ethylene"), "ethylene", 2.2)
    graph = builder.neighbours(out.structure)
    assert graph[first] - {first + 1, first + 2, first + 3} == {ru}  # bonded to Ru, nothing else
    minimized = minimize.minimize(out.structure).structure
    for c in (first, first + 1):
        assert builder.measure(minimized.coords, [ru, c]) == pytest.approx(2.25, abs=0.15)
    for h in range(first + 2, first + 6):
        assert builder.measure(minimized.coords, [ru, h]) > 2.3


def test_added_atoms_hydrogens_and_clashes():
    # T-BLD-28: a new atom at the length given and at a metal's open site; hydrogens counted
    # with the metal's bond (said so) or exactly as many as asked; a refused move names its side.
    s, pd = palladium()
    first = len(s.rows)
    added = builder.add_atom(s, [pd], "O", 2.3)
    assert added.message == f"Added O {first + 1} bonded to atom {pd + 1} at 2.30 Å"
    assert builder.measure(added.structure.coords, [pd, first]) == pytest.approx(2.3)
    assert ratio_to_rest(added.structure, [first], pd) > 1.2
    usual = builder.add_hydrogens(added.structure, [first])
    assert usual.message == (
        f"Added 1 H to atom {first + 1}; the bond to Pd {pd + 1} counted (give count for an "
        "exact number of H)"
    )
    aqua = builder.add_hydrogens(added.structure, [first], count=2)
    assert aqua.message == f"Added 2 H to atom {first + 1}"
    assert builder.formula(aqua.structure.rows) == builder.formula(
        s.rows + [["O", 0, 0, 0], ["H", 0, 0, 0], ["H", 0, 0, 0]]
    )
    with pytest.raises(BuildError, match="not both"):
        builder.add_hydrogens(added.structure, [first], bonds=3, count=2)
    with pytest.raises(BuildError, match="between 1 and 8 H"):
        builder.add_hydrogens(added.structure, [first], count=0)
    with pytest.raises(BuildError, match="between 0.5 and 5"):
        builder.add_atom(s, [pd], "O", 0.2)

    # Pulling the O onto Pd moves the O's side, and the message says which side else to move.
    with pytest.raises(BuildError) as refused:
        builder.set_distance(added.structure, [pd, first], 0.3)
    assert str(refused.value).startswith(f"Moving atom {first + 1}'s side (1 atom) puts atom")
    assert f"move atom {pd + 1}'s side instead ({first} atoms)" in str(refused.value)
