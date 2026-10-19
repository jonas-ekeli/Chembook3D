"""The Ketcher drawer's Convert to 3D (D142, A87; T-BLD-15…18). Ketcher hands over a MOL file;
these are written by RDKit in the same V2000 form, with Ketcher's `M  APO` attachment points."""

import numpy as np
import pytest
from rdkit import Chem
from rdkit.Chem import AllChem

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
