"""A mirror image is another molecule (D267, T-MCP-56, T-LOC-58).

Synthetic structures only: the hydrogen moving between two carbons of `test_mechanism_tools`,
and the C–C stretch of `test_local_follow_mode`, each beside a bromochlorofluoromethane
(CHFClBr, a stereocentre with four different neighbours) that takes no part, so every structure
of a system has one handedness and its mirror image (`atom_matching.mirrored`) the other.
"""

import numpy as np
import pytest

from chembook3d.mcp_tools import BY_NAME, PANEL_ALLOWED, input_schema
from chembook3d.services import atom_matching, gaps, ts_partners
from chembook3d.services import structure_compare as sc
from tests import gaussian_text as g
from tests.test_import import commit, upload
from tests.test_local_follow_mode import MODES, ends, follow, frequency_lines, shifted
from tests.test_local_follow_mode import TS as STRETCH
from tests.test_local_jobs import (  # noqa: F401 - the fixtures
    finished,
    folder_of,
    no_worker_left,
    stub_tools,
    wait_for,
)
from tests.test_mechanism_tools import transfer, ts_structure

# Unit vectors of a tetrahedron, and the bond lengths of C–H, C–F, C–Cl and C–Br.
TETRAHEDRON = [(0, 0, 1), (0.943, 0, -0.333), (-0.471, 0.816, -0.333), (-0.471, -0.816, -0.333)]
SPECTATOR = (("H", 1, 1.09), ("F", 9, 1.38), ("Cl", 17, 1.78), ("Br", 35, 1.94))


def spectator(origin, swap=False) -> list[tuple]:
    """CHFClBr at `origin` as (element, Z, x, y, z); `swap` puts F and Cl the other way round
    (the other enantiomer, in place)."""
    atoms = [("C", 6, *origin)]
    order = [0, 2, 1, 3] if swap else [0, 1, 2, 3]
    for (element, number, length), k in zip(SPECTATOR, order, strict=True):
        atoms.append((element, number, *(np.array(origin) + length * np.array(TETRAHEDRON[k]))))
    return atoms


def rows(atoms) -> list[list]:
    return [[a[0], float(a[2]), float(a[3]), float(a[4])] for a in atoms]


def with_spectator(base: list[list], swap=False, origin=(1.5, 0.0, 7.0)) -> list[list]:
    return [r[:] for r in base] + rows(spectator(origin, swap))


def _ts() -> sc.Structure:
    ts = ts_structure()
    ts.rows = with_spectator(ts.rows)
    ts.modes = [ts.modes[0] + [[0.0, 0.0, 0.0]] * 5]
    ts.mode_rows = ts.rows
    return ts


def _candidate(label, rows_) -> ts_partners.Candidate:
    return ts_partners.Candidate(sc.Structure(label, rows_, 0, 2), {"label": label})


REACTANT = with_spectator(transfer(1.1))
PRODUCT = with_spectator(transfer(1.9))


def test_a_mirror_image_is_never_the_same_structure_nor_a_link():
    """T-MCP-56: the mirror image matches no structure (`same_structure`, `end_match`) and is
    another molecule in `group`, which names it the mirror image of the first; with A184's
    bond check of the TS against the reactant and the product's mirror image it never links,
    however well every distance fits."""
    image = atom_matching.mirrored(PRODUCT)
    assert atom_matching.stereo_flips(PRODUCT, image, list(range(len(image)))) == [9]
    assert sc.same_structure(PRODUCT, PRODUCT, 0.05) is not None
    assert sc.same_structure(PRODUCT, image, 0.05) is None
    assert sc.end_match(PRODUCT, PRODUCT) is not None
    assert sc.end_match(PRODUCT, image) is None
    # A methyl's hydrogens or the spectator turned about one axis are no stereocentre's flip.
    turned = [r[:] for r in PRODUCT]
    turned[1], turned[2] = turned[2], turned[1]
    assert atom_matching.stereo_flips(PRODUCT, turned, list(range(len(turned)))) == []

    found = sc.group(
        [
            sc.Structure("product", PRODUCT, 0, 2),
            sc.Structure("mirror", image, 0, 2),
            sc.Structure("product again", [r[:] for r in PRODUCT], 0, 2),
        ],
        0.05,
    )
    (formula,) = found["formulas"]
    first, second = formula["molecules"]
    assert [m["label"] for c in first["conformers"] for m in c["members"]] == [
        "product",
        "product again",
    ]
    assert second == {**second, "mirror_image_of": 1}
    assert "mirror image of another" in found["summary"]

    checked = sc.compare(
        sc.Structure("reactant", REACTANT, 0, 2), sc.Structure("mirror", image, 0, 2), _ts()
    )
    assert checked["ts_check"]["verdict"] != "links"
    assert any("D267" in r for r in checked["ts_check"]["reasons"])
    proper = sc.compare(
        sc.Structure("reactant", REACTANT, 0, 2), sc.Structure("product", PRODUCT, 0, 2), _ts()
    )
    assert proper["ts_check"]["verdict"] == "links", proper["ts_check"]


def test_a_side_found_only_reflected_says_mirror_image():
    """T-MCP-56: with the reactant and only the product's mirror image, the + side has no
    candidate: the mirror image is listed apart (`mirror_images`) and the side says
    `mirror_of`; the batch line's verdict is "mirror image" and why; with the product itself
    there, it links and the mirror image is not reported. Another stereoisomer (one of two
    stereocentres turned) is never a candidate either (`other_stereoisomers`)."""
    image = atom_matching.mirrored(PRODUCT)
    candidates = [_candidate("reactant", REACTANT), _candidate("mirror", image)]
    found = ts_partners.find(_ts(), candidates, [], 0.05)
    minus, plus = found["sides"]
    assert [c["label"] for c in minus["candidates"]] == ["reactant"]
    assert plus["found"] == 0 and plus["candidates"] == []
    assert [c["label"] for c in plus["mirror_images"]] == ["mirror"]
    assert plus["mirror_of"]["label"] == "mirror" and plus["mirror_of"]["rmsd"] < 0.6
    assert found["check"] is None
    assert "mirror image of “mirror”" in found["summary"]
    assert "not in the investigation" in found["summary"]
    assert "missing_mirror_image" in found["next"]
    block = ts_partners.brief(found)
    assert block["verdict"] == "mirror image" and "+ side: mirror image" in block["why"]
    assert block["+"]["mirror_of"]["label"] == "mirror"
    assert "mirror image" in ts_partners.batch([block], 1, 0, 0.1)["counts"]

    both = ts_partners.find(_ts(), [*candidates, _candidate("product", PRODUCT)], [], 0.05)
    assert both["check"]["verdict"] == "links"
    assert "mirror_of" not in both["sides"][1] and "mirror image" not in both["summary"]

    # Two stereocentres, one turned: neither the structure nor its mirror image fits.
    two = sc.Structure("TS", with_spectator(_ts().rows, origin=(-6.0, 0.0, 0.0)), 0, 2)
    two.frequencies, two.mode_rows = [-1500.0], two.rows
    two.modes = [_ts().modes[0] + [[0.0, 0.0, 0.0]] * 5]
    other = with_spectator(PRODUCT, swap=True, origin=(-6.0, 0.0, 0.0))
    found = ts_partners.find(two, [_candidate("diastereomer", other)], [], 0.05)
    assert found["sides"][1]["found"] == 0 and "mirror_images" not in found["sides"][1]
    assert found["searched"]["other_stereoisomers"] == 1


# ---------- through the app: find_gaps, the builder's mirror step, the mode-follow ----------

TS_ATOMS = [*STRETCH, *spectator((1.0, 0.0, 6.0))]
TS_MODES = [m + [[0.0, 0.0, 0.0]] * 5 for m in MODES]


def _ts_output() -> str:
    frequencies = [-500.0, 300.0, 600.0, 900.0, 1200.0, 3000.0]
    return g.output(
        g.step(
            "#P B3LYP/6-31G(d) Freq",
            [TS_ATOMS],
            -77.0,
            title="ts",
            after=[*frequency_lines(frequencies, TS_MODES, TS_ATOMS), *g.THERMO.splitlines()],
        )
    )


def _side(dx: float) -> list[list]:
    """The stretch's minimum `dx` Å along the mode, with the spectator."""
    return rows(shifted(dx)) + rows(TS_ATOMS[4:])


def _xyz(rows_) -> str:
    return f"{len(rows_)}\n\n" + "\n".join(f"{r[0]} {r[1]} {r[2]} {r[3]}" for r in rows_) + "\n"


def _node(client, label, rows_, role="minimum", **body) -> str:
    response = client.post(
        "/api/nodes", json={"label": label, "xyz": _xyz(rows_), "role": role, **body}
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def _import_ts(client) -> str:
    made = commit(client, upload(client, "ts.log", _ts_output()))["node_id"]
    client.patch(f"/api/nodes/{made}", json={"label": "TS", "role": "transition_state"})
    return made


def _mirror_holes(client) -> list[dict]:
    response = client.get("/api/mechanism/gaps")
    assert response.status_code == 200, response.text
    return [h for h in response.json()["holes"] if h["kind"] == "missing_mirror_image"]


def test_find_gaps_lists_a_missing_mirror_image_and_the_mirror_step_fills_it(open_client):
    """T-MCP-56: a TS whose + side has only the mirror image of a node is a
    missing_mirror_image hole naming that node and the TS; build_structure's mirror step on
    the node makes the missing minimum exactly, which then lies on that side itself, and the
    hole is gone."""
    _node(open_client, "A", _side(-0.5))
    image = _node(open_client, "B mirrored", atom_matching.mirrored(_side(0.5)))
    ts = _import_ts(open_client)
    (hole,) = _mirror_holes(open_client)
    assert hole["id"] == gaps.hole_id("missing_mirror_image", ts, image)
    assert [n["id"] for n in hole["nodes"]] == [ts, image]
    assert "Missing mirror image of “B mirrored”, needed by “TS”" in hole["what"]
    assert "mirror" in hole["fill"] and image in hole["fill"]
    assert gaps.check_hole_id(hole["id"]) == hole["id"]

    built = open_client.post(
        "/api/builder/run", json={"node_id": image, "steps": [{"tool": "mirror"}]}
    )
    assert built.status_code == 200, built.text
    assert "Mirrored all 9 atoms" in built.json()["steps"][0]["message"]
    made = open_client.post(
        "/api/nodes", json={"label": "B", "build_id": built.json()["build_id"], "role": "minimum"}
    )
    assert made.status_code == 201, made.text
    found = open_client.post("/api/structures/ts-partners", json={"ts": {"node_id": ts}}).json()
    plus = next(
        s for s in found["sides"] if s["candidates"] and s["side"] != found["sides"][0]["side"]
    )
    assert plus["candidates"][0]["node_id"] == made.json()["id"]
    assert plus["candidates"][0]["rmsd"] == pytest.approx(
        plus["mirror_images"][0]["rmsd"], abs=1e-3
    )
    assert found["check"]["verdict"] == "links" and _mirror_holes(open_client) == []


@pytest.mark.usefixtures("stub_tools")
def test_a_mode_follow_end_matching_only_reflected_says_mirror_of(open_client):
    """T-LOC-58 (D267): the + end matches no node, not even as another conformer, but the
    mirror image of one within 0.5 Å: it says `mirror_of`, the verdict says the matching
    minimum is not in the investigation, and find_gaps lists it as missing_mirror_image."""
    _node(open_client, "A", _side(-0.5))
    image = _node(open_client, "B mirrored", atom_matching.mirrored(_side(0.5)))
    ts = _import_ts(open_client)
    done = follow(open_client, ts)
    matched = ends(open_client, done["id"])
    plus = matched["ends"]["+"]
    assert plus["matches"] == [] and "settled" not in plus
    assert plus["mirror_of"]["node_id"] == image and plus["mirror_of"]["rmsd"] < 0.5
    assert matched["outcome"] == "one_end"
    assert "+ end is the mirror image of “B mirrored”" in matched["verdict"]
    assert any("D267" in f for f in matched["flags"])
    (hole,) = _mirror_holes(open_client)
    assert "mode-follow" in hole["what"] and hole["nodes"][1]["id"] == image


def test_the_tools_say_so():
    """T-MCP-56: the tools name D267 over MCP; build_structure, which makes the mirror image,
    stays a read tool the panel may call; commit_import says what irc_end does (D268)."""
    for name in ("find_ts_partners", "group_structures", "find_gaps", "get_follow_mode_ends"):
        assert "D267" in BY_NAME[name].description, name
    assert "mirror" in BY_NAME["build_structure"].description
    assert BY_NAME["build_structure"].kind == "read" and "build_structure" in PANEL_ALLOWED
    steps = input_schema(BY_NAME["build_structure"])["properties"]["steps"]
    assert "mirror" in str(steps)
    for name in ("preview_import", "commit_import"):
        assert "D268" in input_schema(BY_NAME[name])["properties"]["irc_end"]["description"]
