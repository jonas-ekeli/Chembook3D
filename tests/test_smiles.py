"""The Edit structure window from nothing and from SMILES (D141, A86; T-BLD-12…14)."""

import numpy as np
import pytest

from chembook3d.services import builder, smiles
from chembook3d.services.builder import BuildError, Structure
from tests.test_builder import assert_fragment_sane


def one_molecule(s: Structure) -> bool:
    return len(builder._reach(builder.neighbours(s), 0, set())) == len(s.rows)


def test_molecule_from_smiles():
    # T-BLD-12: hydrogens added, one molecule, the same coordinates every time; beside a
    # structure it keeps 2 Å away.
    built = smiles.build("OCC")
    s = Structure(built.rows)
    assert builder.formula(s.rows) == "C2H6O" and one_molecule(s)
    assert_fragment_sane(s)
    assert not built.metals
    assert smiles.build("OCC").rows == built.rows
    benzoic = Structure(smiles.build("c1ccccc1C(=O)O").rows)
    assert builder.formula(benzoic.rows) == "C7H6O2" and one_molecule(benzoic)
    beside = builder.add_molecule(benzoic, [], built.rows, "“OCC”").structure
    new = beside.coords[len(benzoic.rows) :]
    d = np.linalg.norm(new[:, None] - benzoic.coords[None], axis=2)
    assert d.min() >= 2.0
    assert beside.changed == list(range(len(benzoic.rows), len(beside.rows)))
    assert smiles.build("Cl[Ru](Cl)=C").metals
    with pytest.raises(BuildError, match="not a SMILES"):
        smiles.build("C1CC")
    with pytest.raises(BuildError, match="Leave out the"):
        smiles.build("*CC")
    with pytest.raises(BuildError, match="Give a SMILES"):
        smiles.build("  ")


def test_fragment_from_smiles():
    # T-BLD-13: a fragment with * where it attaches goes on in place of an H, as the built-in
    # ones do; without exactly one * it is refused.
    methane = Structure([["C", 0.0, 0.0, 0.0]])
    for _ in range(4):
        methane = builder.add_atom(methane, [0], "H").structure
    for text, formula, attach in (("*c1ccccc1", "C7H8", "C"), ("*OC", "C2H6O", "O")):
        fragment = smiles.as_fragment(smiles.build(text, fragment=True))
        assert fragment[0][0] == attach and np.allclose(fragment[0][1], 0)
        done = builder.replace_with_fragment(methane, [1], text, fragment)
        out = done.structure
        assert builder.formula(out.rows) == formula and done.message == f"H 2 replaced by {text}"
        assert np.linalg.norm(out.coords[1] - out.coords[0]) == pytest.approx(
            0.76 + builder.RADII[attach], abs=1e-6
        )
        assert_fragment_sane(out)
        assert one_molecule(out)
    for text in ("c1ccccc1", "*C*", "C(*)(*)"):
        with pytest.raises(BuildError, match="one \\*"):
            smiles.build(text, fragment=True)


def test_from_nothing_with_hydrogens():
    # T-BLD-14: a lone atom to start from, hydrogens up to its usual or a given number of
    # bonds, and a second lone atom beside it.
    s = builder.add_atom(Structure([]), [], "C").structure
    assert s.rows == [["C", 0.0, 0.0, 0.0]]
    done = builder.add_hydrogens(s, [0])
    assert done.message == "Added 4 H to atom 1"
    s = done.structure
    assert builder.formula(s.rows) == "CH4"
    angles = [builder.measure(s.coords, [i, 0, j]) for i in range(1, 5) for j in range(i + 1, 5)]
    assert min(angles) > 100
    assert builder.add_hydrogens(s, [0]).message == "Atom 1 already has enough bonds"
    lone = builder.add_atom(s, [], "N")
    assert lone.message == "Added N as atom 6"
    s = lone.structure
    assert np.linalg.norm(s.coords[:5] - s.coords[5], axis=1).min() >= 2.0
    s = builder.add_hydrogens(s, [5], 2).structure  # an NH2 (an amide-like N)
    assert builder.formula(s.rows) == "CH6N"
    with pytest.raises(BuildError, match="how many bonds Ru"):
        builder.add_hydrogens(Structure([["Ru", 0.0, 0.0, 0.0]]), [0])
    with pytest.raises(BuildError, match="between 1 and 8"):
        builder.add_hydrogens(s, [0], 0)


def test_api_smiles_and_hydrogens(open_client):
    # T-BLD-14: the D141 tools through the API, nothing stored.
    def edit(body):
        response = open_client.post("/api/builder/edit", json=body)
        assert response.status_code == 200, response.text
        return response.json()

    out = edit({"rows": [], "tool": "smiles", "smiles": "CC=O"})
    assert out["message"] == "Added “CC=O” as atoms 1–7"
    out = edit({**out, "picks": [], "tool": "add_atom", "element": "O"})
    out = edit({**out, "picks": [7], "tool": "hydrogens"})
    assert builder.formula(out["rows"]) == "C2H6O2"
    hydrogen = next(i for i, row in enumerate(out["rows"]) if row[0] == "H")
    out = edit({**out, "picks": [hydrogen], "tool": "fragment", "smiles": "*C(F)(F)F"})
    assert builder.formula(out["rows"]) == "C3H5F3O2"
    metal = edit({"rows": [], "tool": "smiles", "smiles": "Cl[Ru](Cl)=C"})
    assert "rough guess" in metal["message"]
    refused = open_client.post(
        "/api/builder/edit", json={"rows": [], "tool": "smiles", "smiles": "C1CC"}
    )
    assert refused.status_code == 422 and "not a SMILES" in refused.json()["detail"]
    assert open_client.get("/api/nodes").json() == []
