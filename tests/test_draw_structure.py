"""D233: Claude draws a structure in 2D to check its reading of a paper's drawing (T-MCP-40,
T-MCP-41): a SMILES as written with what RDKit made of it, a build or a node drawn with the
numbers build_structure uses, what is refused, and the picture handed to Claude as an image."""

import base64
import io

import anyio
from PIL import Image

from chembook3d.claude_panel import NOTEBOOK_GUIDE
from chembook3d.mcp_server import INSTRUCTIONS
from chembook3d.mcp_tools import PANEL_ALLOWED, READ_ONLY_TOOLS
from tests.conftest import WATER
from tests.test_builder_tools import ru_xyz
from tests.test_mcp import _app_bridge, _json

# Hoveyda-Grubbs II: the ether oxygen bound to Ru by a dative bond.
HOVEYDA = "CC(C)O1->[Ru](Cl)(Cl)(=C2N(c3c(C)cc(C)cc3C)CCN2c2c(C)cc(C)cc2C)=Cc2ccccc21"


def _picture(answer: dict) -> Image.Image:
    (image,) = answer["images"]
    data = base64.b64decode(image["png"])
    assert data.startswith(b"\x89PNG")
    picture = Image.open(io.BytesIO(data))
    assert picture.size == (image["width"], image["height"]) == (900, 700)
    return picture


def test_a_smiles_drawn_with_what_rdkit_made_of_it(client):
    """T-MCP-40: a Ru complex drawn as written, numbered in the SMILES's order, its formula,
    charge and the atoms bonded to Ru; a SMILES RDKit cannot read, one it reads but not as a
    molecule, an empty one, and a SMILES with a structure as well are refused."""
    drawn = client.post(
        "/api/builder/picture", json={"smiles": HOVEYDA, "numbers": True, "legend": "HG-II"}
    )
    assert drawn.status_code == 200, drawn.text
    drawn = drawn.json()
    _picture(drawn)
    assert drawn["formula"] == "C31H38Cl2N2ORu" and drawn["charge"] == 0
    assert drawn["heavy_atoms"] == 37
    assert drawn["metals"] == [{"atom": "Ru5", "bonded": ["O4", "Cl6", "Cl7", "C8", "C31"]}]
    plain = client.post("/api/builder/picture", json={"smiles": "c1ccccc1O"}).json()
    assert plain["smiles"] == "Oc1ccccc1" and "metals" not in plain
    _picture(plain)

    for body, reason in (
        ({"smiles": "C1CC"}, "not a SMILES RDKit can read"),
        ({"smiles": "C(C)(C)(C)(C)C"}, "but not as a molecule"),
        ({"smiles": "  "}, "Give a SMILES"),
        ({"smiles": "CCO", "xyz": WATER}, "not both"),
    ):
        refused = client.post("/api/builder/picture", json=body)
        assert refused.status_code == 422 and reason in refused.json()["detail"], body


def test_a_build_or_a_node_drawn_with_its_own_numbers(open_client):
    """T-MCP-41: a structure from XYZ text drawn with build_structure's numbers (hydrogens
    counted in the numbers and the formula, left out of the drawing); a build drawn by its
    build_id; a node; nothing to draw is refused."""
    ru = open_client.post("/api/builder/picture", json={"xyz": ru_xyz(), "numbers": True})
    assert ru.status_code == 200, ru.text
    ru = ru.json()
    _picture(ru)
    (metal,) = ru["metals"]
    assert metal["atom"].startswith("Ru") and len(metal["bonded"]) >= 4
    assert all(name[len(name.rstrip("0123456789")) :] for name in metal["bonded"])

    built = open_client.post(
        "/api/builder/run", json={"steps": [{"tool": "smiles", "smiles": "CCO"}]}
    ).json()
    ethanol = open_client.post(
        "/api/builder/picture", json={"build_id": built["build_id"], "numbers": True}
    ).json()
    assert ethanol["formula"] == "C2H6O" and ethanol["smiles"] == "CCO"
    _picture(ethanol)
    node = open_client.post("/api/nodes", json={"label": "W", "xyz": WATER}).json()
    water = open_client.post("/api/builder/picture", json={"node_id": node["id"]}).json()
    assert water["formula"] == "H2O" and water["heavy_atoms"] == 1
    nothing = open_client.post("/api/builder/picture", json={})
    assert nothing.status_code == 422 and "no structure" in nothing.json()["detail"]


def test_claude_sees_the_drawing(tmp_path):
    """T-MCP-40: over MCP the summary, then the picture as an image; a read tool the panel
    does not ask about, named in both guides."""

    async def run():
        bridge, _page = _app_bridge()
        result = await bridge.call("draw_structure", {"smiles": HOVEYDA})
        kinds = [c.type for c in result.content]
        assert kinds == ["text", "text", "image"]
        assert result.content[1].text == "Picture (900x700 px):"
        assert _json(result)["formula"] == "C31H38Cl2N2ORu"

    anyio.run(run)
    assert "draw_structure" in READ_ONLY_TOOLS & PANEL_ALLOWED
    for text in (INSTRUCTIONS, NOTEBOOK_GUIDE):
        assert "draw_structure" in text and "D233" in text
