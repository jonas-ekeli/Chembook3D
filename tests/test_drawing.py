"""The Ketcher drawer's Convert to 3D (D142, A87; T-BLD-15…18, 23). Ketcher hands over a MOL file;
these are written by RDKit in the same V2000 form, with Ketcher's `M  APO` attachment points."""

import numpy as np
import pytest
from rdkit import Chem
from rdkit.Chem import AllChem, rdDepictor
from rdkit.Geometry import Point2D

from chembook3d.services import builder, smiles
from chembook3d.services.builder import BuildError, Structure
from tests.test_builder import assert_fragment_sane
from tests.test_smiles import one_molecule


def drawn(text: str, points: dict[int, int] | None = None) -> str:
    """A 2D drawing of a SMILES as Ketcher writes it, with wedges, and attachment points on
    atoms (0-based) as `M  APO` lines."""
    mol = Chem.MolFromSmiles(text)
    AllChem.Compute2DCoords(mol)
    Chem.WedgeMolBonds(mol, mol.GetConformer())
    block = Chem.MolToMolBlock(mol)
    if points:
        line = f"M  APO{len(points):3d}" + "".join(
            f" {atom + 1:3d} {kind:3d}" for atom, kind in points.items()
        )
        block = block.replace("M  END", line + "\nM  END")
    return block


def stereo_of(built: smiles.Built) -> list[tuple[int, str]]:
    block = "\n".join(f"{r[0]} {r[1]} {r[2]} {r[3]}" for r in built.rows)
    mol = Chem.MolFromXYZBlock(f"{len(built.rows)}\n\n{block}")
    from rdkit.Chem import rdDetermineBonds

    rdDetermineBonds.DetermineBonds(mol, charge=0)
    Chem.AssignStereochemistryFrom3D(mol)
    return Chem.FindMolChiralCenters(mol)


def test_drawing_to_3d_keeps_charges_and_stereo():
    # T-BLD-15: a drawn molecule comes out in 3D with its hydrogens, charges, wedged centres
    # and double-bond geometry; the same drawing gives the same structure.
    for text in ("F[C@H](Cl)Br", "F[C@@H](Cl)Br"):
        built = smiles.from_molfile(drawn(text))
        assert builder.formula(built.rows) == "CHBrClF"
        expected = Chem.FindMolChiralCenters(Chem.MolFromSmiles(text))
        assert stereo_of(built) == expected
    trans = Structure(smiles.from_molfile(drawn("C/C=C/C")).rows)
    carbons = [i for i, r in enumerate(trans.rows) if r[0] == "C"]
    assert abs(builder.measure(trans.coords, carbons)) == pytest.approx(180, abs=10)
    cis = Structure(smiles.from_molfile(drawn("C/C=C\\C")).rows)
    assert abs(builder.measure(cis.coords, carbons)) == pytest.approx(0, abs=10)
    acetate = smiles.from_molfile(drawn("CC(=O)[O-]"))
    assert builder.formula(acetate.rows) == "C2H3O2"
    assert smiles.from_molfile(drawn("CCO")).rows == smiles.from_molfile(drawn("CCO")).rows
    assert_fragment_sane(Structure(smiles.from_molfile(drawn("c1ccccc1O")).rows))
    assert smiles.from_molfile(drawn("Cl[Ru](Cl)=C")).metals


def test_drawn_metal_bonds_start_at_the_radii_sum():
    # T-BLD-16: a drawn Ru complex (dative P→Ru bonds, so a V3000 file as Ketcher writes one)
    # comes out with each bond to Ru near the sum of the covalent radii, no H drawn onto Ru.
    mol = Chem.MolFromSmiles("Cl[Ru](Cl)(=Cc1ccccc1)(<-P(C)(C)C)<-P(C)(C)C")
    AllChem.Compute2DCoords(mol)
    built = smiles.from_molfile(Chem.MolToV3KMolBlock(mol))
    s = Structure(built.rows)
    assert builder.formula(s.rows) == "C13H24Cl2P2Ru" and built.metals
    ru = next(i for i, r in enumerate(s.rows) if r[0] == "Ru")
    bonded = builder.neighbours(s)[ru]
    assert sorted(s.rows[i][0] for i in bonded) == ["C", "Cl", "Cl", "P", "P"]
    for i in bonded:
        expected = builder._bond_length("Ru", s.rows[i][0])
        assert builder.measure(s.coords, [ru, i]) == pytest.approx(expected, abs=0.1)


def drawn_at(text: str, places: dict[int, tuple[float, float]]) -> str:
    """A drawing with some atoms (0-based) put where Ketcher's user drew them."""
    mol = Chem.MolFromSmiles(text)
    rdDepictor.Compute2DCoords(mol, coordMap={i: Point2D(*p) for i, p in places.items()})
    return Chem.MolToV3KMolBlock(mol)


@pytest.mark.parametrize(
    ("text", "places", "trans", "cis"),
    [
        # trans-[PdCl2(NH3)2] drawn with the chlorides left and right
        (
            "Cl[Pd](Cl)(<-N)<-N",
            {1: (0, 0), 0: (-1.5, 0), 2: (1.5, 0), 3: (0, 1.5), 4: (0, -1.5)},
            [(0, 1, 2), (3, 1, 4)],
            [(0, 1, 3)],
        ),
        # cis, each chloride opposite an NH3
        (
            "Cl[Pd](Cl)(<-N)<-N",
            {1: (0, 0), 0: (-1.5, 0), 2: (0, 1.5), 3: (1.5, 0), 4: (0, -1.5)},
            [(0, 1, 3), (2, 1, 4)],
            [(0, 1, 2), (3, 1, 4)],
        ),
        # a Grubbs I-like Ru: chlorides left and right, phosphines above and below
        (
            "Cl[Ru](Cl)(=Cc1ccccc1)(<-P(C)(C)C)<-P(C)(C)C",
            {1: (0, 0), 0: (-1.5, 0), 2: (1.5, 0), 3: (-0.9, -1.2), 10: (0, 1.5), 14: (0, -1.5)},
            [(0, 1, 2), (10, 1, 14)],
            [(0, 1, 3), (3, 1, 10)],
        ),
        # a CAAC Hoveyda precatalyst, the ether chelating: chlorides left and right, the CAAC
        # above and the ether oxygen below it
        (
            "CC1(C)CC(C)(C)N(c2c(C(C)C)cccc2C(C)C)C1=[Ru]1(Cl)(Cl)=Cc2ccccc2O1C(C)C",
            {
                21: (0, 0),
                22: (-1.5, 0),
                23: (1.5, 0),
                20: (0, 1.5),
                31: (0.3, -1.5),
                24: (-1, -1.1),
            },
            [(22, 21, 23), (20, 21, 31)],
            [(20, 21, 22), (24, 21, 22)],
        ),
    ],
)
def test_drawn_metal_keeps_trans_ligands(text, places, trans, cis):
    # T-BLD-23 (D142f): ligands drawn on opposite sides of a metal come out trans, the others
    # cis, and the 3D view finds the bonds that were drawn, no more (no Cl–Cl, nothing extra on
    # the metal).
    block = drawn_at(text, places)
    built = smiles.from_molfile(block)
    s = Structure(built.rows)
    for angle in trans:
        assert builder.measure(s.coords, list(angle)) > 160
    for angle in cis:
        assert 75 < builder.measure(s.coords, list(angle)) < 110
    drawing = Chem.MolFromMolBlock(block)
    heavy = drawing.GetNumAtoms()
    graph = builder.neighbours(s)
    found = {(i, j) for i in range(heavy) for j in graph[i] if i < j < heavy}
    drawn_bonds = {
        tuple(sorted((b.GetBeginAtomIdx(), b.GetEndAtomIdx()))) for b in drawing.GetBonds()
    }
    assert found == drawn_bonds


def test_drawn_fragment_with_attachment_point():
    # T-BLD-17: an attachment point (or an R# atom) makes the drawing a fragment that goes on
    # in place of the picked atom; two attachment points, or none drawn, are refused.
    methane = Structure([["C", 0.0, 0.0, 0.0]])
    methane = builder.add_hydrogens(methane, [0]).structure
    for block in (drawn("c1ccccc1", {0: 1}), drawn("*c1ccccc1")):
        built = smiles.from_molfile(block)
        assert built.attach is not None
        out = builder.replace_with_fragment(
            methane, [1], "the drawn fragment", smiles.as_fragment(built)
        ).structure
        assert builder.formula(out.rows) == "C7H8"
        assert one_molecule(out)
        assert_fragment_sane(out)
    with pytest.raises(BuildError, match="one attachment point"):
        smiles.from_molfile(drawn("c1ccccc1", {0: 1, 3: 1}))
    with pytest.raises(BuildError, match="one attachment point"):
        smiles.from_molfile(drawn("c1ccccc1", {0: 3}))
    empty = "\n  Ketcher\n\n  0  0  0  0  0  0  0  0  0  0999 V2000\nM  END\n"
    with pytest.raises(BuildError, match="Draw a molecule first"):
        smiles.from_molfile(empty)
    with pytest.raises(BuildError, match="could not read"):
        smiles.from_molfile("not a MOL file")


def test_api_drawing(open_client):
    # T-BLD-18: Convert to 3D through the API, nothing stored.
    def edit(body):
        response = open_client.post("/api/builder/edit", json=body)
        assert response.status_code == 200, response.text
        return response.json()

    out = edit({"rows": [], "tool": "drawing", "molfile": drawn("CC(=O)O")})
    assert out["message"] == "Added the drawing as atoms 1–8"
    hydrogen = next(i for i, row in enumerate(out["rows"]) if row[0] == "H")
    fragment = drawn("C(F)(F)F", {0: 1})
    refused = open_client.post(
        "/api/builder/edit", json={**out, "picks": [], "tool": "drawing", "molfile": fragment}
    )
    assert refused.status_code == 422 and "pick the atom it replaces" in refused.json()["detail"]
    out = edit({**out, "picks": [hydrogen], "tool": "drawing", "molfile": fragment})
    assert out["message"] == f"H {hydrogen + 1} replaced by the drawn fragment"
    assert builder.formula(out["rows"]) == "C3H3F3O2"
    metal = edit({"rows": [], "tool": "drawing", "molfile": drawn("Cl[Ru](Cl)=C")})
    assert "rough guess" in metal["message"]
    assert open_client.get("/api/nodes").json() == []
    assert np.isfinite(np.array([r[1:] for r in out["rows"]], dtype=float)).all()
