"""Draw from this structure (D143, A88; T-BLD-19…22, 24): the structure into the Ketcher drawer in
2D and the drawing back onto it. Metal complexes are morfeus's public SambVca set, Ru made from
its Ir complex as in test_minimize."""

from pathlib import Path

import numpy as np
import pytest
from rdkit import Chem

from chembook3d.services import builder, redraw, smiles
from chembook3d.services.builder import BuildError, Hold, Structure
from tests.test_builder import rows_of
from tests.test_minimize import closest_unbonded, ru_complex

STERICS = Path(__file__).parent / "fixtures" / "sterics"


def edited(block: str, change) -> str:
    """The drawing changed as the user would in Ketcher, written back as a MOL file."""
    mol = Chem.RWMol(Chem.MolFromMolBlock(block, removeHs=False))
    change(mol)
    out = mol.GetMol()
    Chem.SanitizeMol(out)
    return Chem.MolToV3KMolBlock(out)


def methyl(mol: Chem.Mol) -> int:
    return next(
        a.GetIdx() for a in mol.GetAtoms() if a.GetSymbol() == "C" and a.GetTotalNumHs() == 3
    )


def trifluoro(mol: Chem.RWMol) -> None:
    carbon = methyl(mol)
    for _ in range(3):
        mol.AddBond(carbon, mol.AddAtom(Chem.Atom(9)), Chem.BondType.SINGLE)


@pytest.mark.parametrize("name", ["1.xyz", "3.xyz", "4.xyz", "10.xyz", "13.xyz", "15.xyz"])
def test_round_trip_changes_nothing(name):
    # T-BLD-19: a metal complex drawn and put back unchanged is found unchanged: every atom,
    # hydrogens included, matched (an O with no H on Mn comes back without any).
    rows = [["Ru" if r[0] == "Ir" else r[0], *r[1:]] for r in rows_of(STERICS / name)]
    structure = Structure(rows)
    with pytest.raises(BuildError, match="has not changed"):
        redraw.from_drawing(structure, redraw.to_drawing(structure))


def test_drawing_of_a_ru_complex():
    # T-BLD-19: neutral ligands are drawn with dative bonds to Ru, chloride with a plain bond,
    # hydrogens left out.
    s, ru = ru_complex()
    mol = Chem.MolFromMolBlock(redraw.to_drawing(s), removeHs=False)
    metal = next(a for a in mol.GetAtoms() if a.GetSymbol() == "Ru")
    kinds = sorted(
        (b.GetOtherAtom(metal).GetSymbol(), str(b.GetBondType())) for b in metal.GetBonds()
    )
    assert kinds == [("C", "DATIVE"), ("C", "DATIVE"), ("C", "DATIVE"), ("Cl", "SINGLE")]
    assert all(a.GetSymbol() != "H" for a in mol.GetAtoms())
    assert mol.GetNumAtoms() == sum(1 for r in s.rows if r[0] != "H")


def test_hydrogen_bonded_to_the_metal_and_its_atom():
    # T-BLD-24: an NH3 hydrogen folded onto Pd, so the 3D view bonds it to both N and Pd,
    # still draws and comes back unchanged (it once failed with HTTP 500).
    s = Structure(smiles.build("Cl[Pd](Cl)(<-N)<-N").rows)
    elements = [r[0] for r in s.rows]
    graph = builder.neighbours(s)
    pd = elements.index("Pd")
    n = next(i for i in graph[pd] if elements[i] == "N")
    h = next(i for i in graph[n] if elements[i] == "H")
    s = builder.set_angle(s, [pd, n, h], 60.0, h).structure
    assert {elements[i] for i in builder.neighbours(s)[h]} == {"N", "Pd"}
    with pytest.raises(BuildError, match="has not changed"):
        redraw.from_drawing(s, redraw.to_drawing(s))


def test_new_group_keeps_the_rest_in_place():
    # T-BLD-20: a methyl drawn as CF3 on the Ru complex: its H atoms go, three F come last at a
    # C–F bond length, and every atom more than two bonds from the change stays exactly put.
    s, ru = ru_complex()
    s = Structure(s.rows, holds=[Hold([ru])])
    done = redraw.from_drawing(s, edited(redraw.to_drawing(s), trifluoro))
    out = done.structure
    assert builder.formula(out.rows) == "C29H35ClF3N2O2Ru"
    assert done.message.startswith("Drawing put on: 3 atoms added, 3 deleted, 1 changed")
    fluorines = [i for i, r in enumerate(out.rows) if r[0] == "F"]
    assert fluorines == [len(out.rows) - 3, len(out.rows) - 2, len(out.rows) - 1]
    carbon = next(iter(builder.neighbours(out)[fluorines[0]]))
    for f in fluorines:
        assert builder.measure(out.coords, [carbon, f]) == pytest.approx(1.36, abs=0.05)
    assert set(fluorines) | {carbon} <= set(out.changed)
    assert closest_unbonded(out) > 1.6
    # The atoms before the methyl's hydrogens keep their numbers; Ru stays frozen in place,
    # and so does every atom far from the change.
    assert out.rows[ru] == s.rows[ru] and out.holds == [Hold([ru])]
    for i in range(len(out.rows) - 3):
        if np.linalg.norm(out.coords[i] - out.coords[carbon]) > 5:
            assert out.rows[i][0] in ("C", "H", "N", "O", "Cl", "Ru")
            assert any(np.allclose(out.coords[i], c) for c in s.coords)


def test_deleting_bonding_and_adding_a_molecule():
    # T-BLD-21: on 1-butanol, the O left out, C1 and C4 joined into a ring, and a water drawn
    # apart: the carbons keep their numbers, the ring bond is added and pulled to length, and
    # the water goes beside, 2 Å clear.
    s = Structure(smiles.build("CCCCO").rows)
    assert [r[0] for r in s.rows[:5]] == ["C", "C", "C", "C", "O"]

    def change(mol: Chem.RWMol) -> None:
        mol.RemoveAtom(4)
        mol.AddBond(0, 3, Chem.BondType.SINGLE)
        mol.AddAtom(Chem.Atom(8))

    done = redraw.from_drawing(s, edited(redraw.to_drawing(s), change))
    out = done.structure
    assert builder.formula(out.rows) == "C4H10O"  # cyclobutane and a water
    assert [r[0] for r in out.rows[:4]] == ["C", "C", "C", "C"]
    assert (0, 3) in out.added
    assert builder.measure(out.coords, [0, 3]) == pytest.approx(1.55, abs=0.08)
    water = [i for i, r in enumerate(out.rows) if r[0] == "O"]
    assert len(water) == 1
    assert np.linalg.norm(out.coords[:4] - out.coords[water[0]], axis=1).min() >= 2.0


def test_refusals():
    # T-BLD-21: an attachment point, a drawing with nothing in common, an empty structure.
    s = Structure(smiles.build("CCO").rows)
    with pytest.raises(BuildError, match="Attachment points"):
        redraw.from_drawing(s, Chem.MolToMolBlock(Chem.MolFromSmiles("*CCO")))
    with pytest.raises(BuildError, match="Nothing in the drawing matches"):
        redraw.from_drawing(s, Chem.MolToMolBlock(Chem.MolFromSmiles("[Na+].[Cl-]")))
    with pytest.raises(BuildError, match="no structure"):
        redraw.to_drawing(Structure([]))


def test_api_redraw(open_client):
    # T-BLD-22: the structure to 2D and the drawing back through the API, nothing stored.
    s, _ = ru_complex()
    response = open_client.post("/api/builder/drawing", json={"rows": s.rows})
    assert response.status_code == 200, response.text
    block = response.json()["molfile"]
    assert "V3000" in block
    unchanged = open_client.post(
        "/api/builder/edit", json={"rows": s.rows, "tool": "redraw", "molfile": block}
    )
    assert unchanged.status_code == 422 and "has not changed" in unchanged.json()["detail"]
    done = open_client.post(
        "/api/builder/edit",
        json={"rows": s.rows, "tool": "redraw", "molfile": edited(block, trifluoro)},
    )
    assert done.status_code == 200, done.text
    assert builder.formula(done.json()["rows"]) == "C29H35ClF3N2O2Ru"
    assert open_client.get("/api/nodes").json() == []
