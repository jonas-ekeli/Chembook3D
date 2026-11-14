"""D234: crystal structures (T-MCP-42..44): a CIF read as its molecules, made whole across the
cell's edges, copies told apart from other molecules, disorder and missing hydrogens; the
Inspect reading of a CIF; the open COD searched and read through a stand-in for its server;
each molecule kept as a build that create_node saves."""

import json

import anyio
import httpx
import numpy as np
import pytest

from chembook3d import xyz
from chembook3d.claude_panel import NOTEBOOK_GUIDE
from chembook3d.mcp_server import INSTRUCTIONS
from chembook3d.mcp_tools import PANEL_ALLOWED
from chembook3d.services import crystals, smiles
from tests.cif_text import write_cif
from tests.test_mcp import _app_bridge, _json


def _atoms(text: str) -> list[xyz.Atom]:
    return [xyz.Atom(e, float(x), float(y), float(z)) for e, x, y, z in smiles.build(text).rows]


ETHANOL = _atoms("CCO")
WATER = _atoms("O")
# Away from the inversion centres and screw axes, across the cell's x = 0 face.
ACROSS = {"shift": (0.2, 2.0, 3.0)}


def _distances(atoms: list[xyz.Atom]) -> np.ndarray:
    p = np.array([[a.x, a.y, a.z] for a in atoms])
    return np.linalg.norm(p[:, None] - p[None], axis=2)


def test_a_cif_as_its_molecules():
    """T-MCP-42: ethanol cut by the cell's edge comes out whole, its four copies in P21/c as
    one molecule; a water beside it is a molecule of its own, listed after; part 2 of a
    disordered water is left out and said; a molecule without hydrogens is said to have none;
    the CIF's reference and codes are given."""
    text = write_cif(
        [
            ("A", ETHANOL, ACROSS),
            ("W", WATER, {"shift": (6.0, 2.5, 8.0)}),
            ("X", WATER, {"shift": (6.5, 2.5, 8.5), "part": "2"}),
        ]
    )
    (crystal,) = crystals.read_cif(text, "test.cif")
    ethanol, water = crystal.molecules
    assert (ethanol.formula, ethanol.copies, len(ethanol.atoms)) == ("C2H6O", 4, 9)
    assert np.abs(_distances(ethanol.atoms) - _distances(ETHANOL)).max() < 1e-4
    assert (water.formula, water.copies) == ("H2O", 4)
    assert crystal.warnings == [
        "Disordered: part 1 is kept; part 2 (12 sites in the cell) is left out."
    ]
    found = crystals.summary(crystal)
    assert found["space_group"] == "P 1 21/c 1" and found["doi"] == "10.0000/test.1"
    assert found["ccdc"] == "CCDC 1234567" and found["cell"][4] == 100.0
    assert [m["formula"] for m in found["molecules"]] == ["C2H6O", "H2O"]

    bare = [a for a in ETHANOL if a.element != "H"]
    (heavy,) = crystals.read_cif(write_cif([("A", bare, ACROSS)]))[0].molecules
    assert heavy.formula == "C2O" and "No hydrogens" in heavy.warnings[0]

    for bad, reason in (
        ("not a cif at all\n", "not a CIF gemmi can read"),
        ("data_x\n_cell_length_a 5\n", "no structure"),
    ):
        with pytest.raises(crystals.CrystalFailed, match=reason):
            crystals.read_cif(bad)


def test_inspect_and_claude_read_a_cif_on_this_computer(open_client, tmp_path):
    """T-MCP-43: summarize_file gives a CIF's molecules as steps and get_file_structure one of
    them; get_crystal_structure gives each a build_id that create_node saves (no energy, no
    calculation); a missing file and a CIF with a COD id are refused."""
    cif = tmp_path / "crystal.cif"
    cif.write_text(write_cif([("A", ETHANOL, ACROSS), ("W", WATER, {"shift": (6, 2.5, 8)})]))
    found = open_client.get("/api/local-files/summary", params={"path": str(cif)}).json()
    assert found["program"] == "CIF"
    assert [s["job_type"] for s in found["steps"]] == ["crystal_molecule"] * 2
    assert "molecule 1 of 2, 4 in the cell (P 1 21/c 1)" in found["steps"][0]["route"]
    water = open_client.get(
        "/api/local-files/structure", params={"path": str(cif), "step": 2}
    ).text.splitlines()
    assert water[0] == "3"

    read = open_client.post("/api/crystals/read", json={"path": str(cif), "include_xyz": True})
    assert read.status_code == 200, read.text
    read = read.json()
    (structure,) = read["structures"]
    first = structure["molecules"][0]
    assert first["formula"] == "C2H6O" and first["xyz"].startswith("9\n")
    assert "no energy" in read["note"]
    made = open_client.post("/api/nodes", json={"label": "X-ray", "build_id": first["build_id"]})
    assert made.status_code in (200, 201), made.text
    node = made.json()
    assert node["formula"] == "C2H6O"
    assert open_client.get(f"/api/nodes/{node['id']}").json().get("calculations", []) == []

    for body, reason in (
        ({"path": str(tmp_path / "none.cif")}, "Cannot read"),
        ({"path": str(cif), "cod_id": "1000000"}, "Give a cod_id or a path"),
        ({}, "Give a cod_id or a path"),
        ({"cod_id": "12ab"}, "is not a COD id"),
    ):
        refused = open_client.post("/api/crystals/read", json=body)
        assert refused.status_code == 422 and reason in refused.json()["detail"], body


class FakeCod:
    """A stand-in for the COD's server: counts, JSON rows and one CIF."""

    def __init__(self, total: int = 2, down: bool = False):
        self.total = total
        self.down = down
        self.requests: list[httpx.Request] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.down:
            raise httpx.ConnectError("no network")
        assert request.headers["user-agent"] == crystals.USER_AGENT
        path = request.url.path
        if path == "/cod/1234567.cif":
            return httpx.Response(200, text=write_cif([("A", ETHANOL, ACROSS)]))
        if path.endswith(".cif"):
            return httpx.Response(404)
        if request.url.params["format"] == "count":
            return httpx.Response(200, text=f"{self.total}\n")
        rows = [
            {
                "file": "1234567",
                "formula": "- C2 H6 O -",
                "chemname": "Ethanol",
                "authors": "A. Author",
                "journal": "Test Journal",
                "year": "2014",
                "doi": "10.0000/test.1",
                "sg": "P 1 21/c 1",
            },
            {"file": "1234568", "formula": "- H2 O -", "commonname": "water"},
        ]
        return httpx.Response(200, text=json.dumps(rows))

    def client(self) -> httpx.Client:
        return httpx.Client(
            transport=httpx.MockTransport(self.handler),
            headers={"User-Agent": crystals.USER_AGENT},
        )


def test_the_cod_searched_and_read(open_client, monkeypatch):
    """T-MCP-44: a search by DOI sends only the DOI; by formula and elements; too many to
    list; the COD out of reach; a COD entry read as its molecules; an id the COD lacks."""
    cod = FakeCod()
    monkeypatch.setattr(crystals, "_client", cod.client)
    found = open_client.get(
        "/api/crystals/search", params={"doi": "https://doi.org/10.0000/test.1"}
    ).json()
    assert found["total"] == 2
    assert found["entries"][0] == {
        "cod_id": "1234567",
        "formula": "C2H6O",
        "name": "Ethanol",
        "authors": "A. Author",
        "journal": "Test Journal",
        "year": "2014",
        "doi": "10.0000/test.1",
        "space_group": "P 1 21/c 1",
    }
    assert found["entries"][1]["name"] == "water"
    sent = dict(cod.requests[-1].url.params)
    assert sent == {"doi": "10.0000/test.1", "format": "json"}

    open_client.get(
        "/api/crystals/search",
        params={"formula": "C31H38Cl2N2ORu", "elements": ["Ru", "Cl"]},
    )
    sent = dict(cod.requests[-1].url.params)
    assert sent["formula"] == "C31 H38 Cl2 N2 O Ru" and (sent["el1"], sent["el2"]) == ("Ru", "Cl")
    nothing = open_client.get("/api/crystals/search")
    assert nothing.status_code == 422 and "Give a DOI" in nothing.json()["detail"]
    bad = open_client.get("/api/crystals/search", params={"elements": ["Xx"]})
    assert bad.status_code == 422 and "not an element" in bad.json()["detail"]

    many = FakeCod(total=5000)
    monkeypatch.setattr(crystals, "_client", many.client)
    listed = open_client.get("/api/crystals/search", params={"text": "ruthenium"}).json()
    assert listed["entries"] == [] and "Too many" in listed["note"]
    assert len(many.requests) == 1  # only counted

    monkeypatch.setattr(crystals, "_client", cod.client)
    read = open_client.post("/api/crystals/read", json={"cod_id": "1234567"}).json()
    assert read["source"] == "COD 1234567"
    assert read["structures"][0]["molecules"][0]["copies_in_cell"] == 4
    missing = open_client.post("/api/crystals/read", json={"cod_id": "7654321"})
    assert missing.status_code == 422 and "no such entry" in missing.json()["detail"]

    monkeypatch.setattr(crystals, "_client", FakeCod(down=True).client)
    down = open_client.get("/api/crystals/search", params={"doi": "10.0000/test.1"})
    assert down.status_code == 422 and "could not be reached" in down.json()["detail"]


def test_claude_finds_and_saves_a_crystal_structure(tmp_path, monkeypatch):
    """T-MCP-44: through the MCP server: a search by DOI, the entry's molecules and a node
    saved by build_id; both tools read tools the panel does not ask about, named in the
    guides."""
    monkeypatch.setattr(crystals, "_client", FakeCod().client)

    async def run():
        bridge, page = _app_bridge()
        await page.post("/api/investigations", json={"folder": str(tmp_path / "i"), "name": "T"})
        found = _json(await bridge.call("find_crystal_structures", {"doi": "10.0000/test.1"}))
        cod_id = found["entries"][0]["cod_id"]
        read = _json(await bridge.call("get_crystal_structure", {"cod_id": cod_id}))
        build_id = read["structures"][0]["molecules"][0]["build_id"]
        made = _json(
            await bridge.call(
                "create_node", {"label": "X-ray", "build_id": build_id, "notes": "COD 1234567"}
            )
        )
        assert made["formula"] == "C2H6O"

    anyio.run(run)
    assert {"find_crystal_structures", "get_crystal_structure"} <= PANEL_ALLOWED
    for text in (INSTRUCTIONS, NOTEBOOK_GUIDE):
        assert "find_crystal_structures" in text and "D234" in text
