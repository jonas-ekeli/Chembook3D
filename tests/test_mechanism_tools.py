"""Building a mechanism from a folder, the read tools (D263, A184, FR-MCP-23): a folder
surveyed (T-MCP-45), two structures compared whatever their atom order (T-MCP-46), a TS's mode
checked against two minima (T-MCP-47), many structures grouped (T-MCP-48), the tools over MCP
(T-MCP-49), and the import's duplicate check independent of atom order (T-IMP-81)."""

import anyio
import numpy as np
import pytest

from chembook3d.claude_panel import NOTEBOOK_GUIDE
from chembook3d.mcp_server import INSTRUCTIONS
from chembook3d.mcp_tools import BY_NAME, PANEL_ALLOWED, input_schema
from chembook3d.models import Calculation
from chembook3d.services import structure_compare as sc
from tests import gaussian_text as g
from tests.test_atom_matching import FIXTURES, frames, scrambled
from tests.test_import import TS, calculations, commit, node, upload
from tests.test_import_tools import TS_SP
from tests.test_mcp import _app_bridge, _json
from tests.test_node_merge import SWAPPED, WATER, rows

TOOLS = ("survey_folder", "compare_structures", "group_structures")

# A hydrogen moving from methane's carbon (C1) to a fluoromethyl radical's (C2) along x: the
# two ends and the TS halfway, with an imaginary mode that moves the hydrogen towards C2.
METHYLS = [
    ["C", 0.0, 0.0, 0.0],
    ["H", -0.37, 1.03, 0.0],
    ["H", -0.37, -0.515, 0.89],
    ["H", -0.37, -0.515, -0.89],
    ["C", 3.0, 0.0, 0.0],
    ["H", 3.37, 1.03, 0.0],
    ["H", 3.37, -0.515, 0.89],
    ["F", 3.45, -0.6, -1.04],
]


def transfer(x: float) -> list[list]:
    """The moving hydrogen (atom 9) at x."""
    return [row[:] for row in METHYLS] + [["H", x, 0.0, 0.0]]


def ts_structure(order=None) -> sc.Structure:
    rows_ = transfer(1.5)
    mode = [[0.0, 0.0, 0.0] for _ in rows_]
    mode[0], mode[4], mode[8] = [-0.1, 0.0, 0.0], [-0.1, 0.0, 0.0], [0.95, 0.0, 0.0]
    if order is not None:
        rows_ = [rows_[k] for k in order]
        mode = [mode[k] for k in order]
    return sc.Structure("TS", rows_, 0, 2, [-1500.0], [mode], rows_)


def ref(rows_, **extra) -> dict:
    text = f"{len(rows_)}\n\n" + "\n".join(f"{r[0]} {r[1]} {r[2]} {r[3]}" for r in rows_)
    return {"xyz": text + "\n", **extra}


def compare(client, a, b, **body) -> dict:
    response = client.post("/api/structures/compare", json={"a": a, "b": b, **body})
    assert response.status_code == 200, response.text
    return response.json()


# Ethane, and ethylene plus H2: the sum of two species against their product.
ETHANE = [
    ["C", -0.765, 0.0, 0.0],
    ["C", 0.765, 0.0, 0.0],
    *[
        ["H", -1.16, 1.02 * np.cos(np.radians(a)), 1.02 * np.sin(np.radians(a))]
        for a in (0, 120, 240)
    ],
    *[
        ["H", 1.16, 1.02 * np.cos(np.radians(a)), 1.02 * np.sin(np.radians(a))]
        for a in (60, 180, 300)
    ],
]
ETHYLENE = [
    ["C", -0.665, 0.0, 0.0],
    ["C", 0.665, 0.0, 0.0],
    ["H", -1.23, 0.92, 0.0],
    ["H", -1.23, -0.92, 0.0],
    ["H", 1.23, 0.92, 0.0],
    ["H", 1.23, -0.92, 0.0],
]
H2 = [["H", 0.0, 0.0, 0.0], ["H", 0.74, 0.0, 0.0]]


def test_a_renumbered_structure_is_the_same_and_a_moved_hydrogen_is_not(open_client):
    """T-MCP-46: a structure against itself shuffled and turned: the same molecule, a duplicate,
    numbered otherwise, RMSD ~0; a hydrogen moved to another atom: one bond formed, one broken,
    with their distances and the largest changes."""
    start = frames(FIXTURES / "crest" / "crest_conformers.xyz")[0]
    shuffled, truth = scrambled(start, seed=5)
    found = compare(open_client, ref(start), ref(shuffled, label="shuffled"))
    assert found["same_atoms"] and found["same_molecule"] and found["same_conformer"]
    assert found["renumbered"] and found["rmsd"] < 1e-3
    assert found["mapping"] == [j + 1 for j in truth]
    assert found["formed"] == [] and found["broken"] == []
    assert found["b"]["label"] == "shuffled" and found["a"]["formula"] == "C3H8O3"

    product = transfer(1.9)
    found = compare(open_client, ref(transfer(1.1)), ref(product))
    assert not found["same_molecule"] and not found["same_conformer"]
    assert [b["bond"] for b in found["formed"]] == ["C5–H9"]
    assert [b["bond"] for b in found["broken"]] == ["C1–H9"]
    assert found["broken"][0]["a"] == 1.1 and found["broken"][0]["b"] == 1.9
    assert {c["bond"] for c in found["largest_changes"]} >= {"C1–H9", "C5–H9"}
    assert any("C5–H9 formed" in n for n in found["notes"])

    # Other atoms: no match, the difference said.
    other = compare(open_client, ref(start), ref(product))
    assert other["same_atoms"] is False and "B has" in other["difference"]


def test_a_sum_of_species_against_their_product(open_client):
    """T-MCP-46: ethylene + H2 against ethane: the species placed apart, the bonds formed
    between them, no RMSD; charges add."""
    found = compare(
        open_client,
        [ref(ETHYLENE, charge=0, multiplicity=1), ref(H2, charge=0, multiplicity=1)],
        ref(ETHANE),
    )
    assert found["a"]["species"] == 2 and found["a"]["charge"] == 0
    assert found["a"]["multiplicity"] == 1 and found["rmsd"] is None
    assert len(found["formed"]) == 2 and all(b["bond"].startswith("C") for b in found["formed"])
    assert {b["a"] for b in found["formed"]} == {"separate species"}
    assert [b["bond"] for b in found["broken"]] == ["H7–H8"]
    assert any("No RMSD" in n for n in found["notes"])

    summed = sc.summed([sc.Structure("a", ETHYLENE, 1, 2), sc.Structure("b", H2, -1, 2)])
    assert summed.charge == 0 and summed.multiplicity is None  # two open shells: unknown
    apart = np.array([r[1:4] for r in summed.rows])
    assert np.linalg.norm(apart[6] - apart[:6], axis=1).min() > sc.APART


def test_the_mode_of_a_ts_is_checked_against_two_minima():
    """T-MCP-47, A184: the bonds that change lie along the TS's imaginary mode and the ends
    lie on its two sides: the TS links them, whatever the TS's atom order; a TS whose mode
    moves other atoms does not."""
    reactant = sc.Structure("R", transfer(1.1), 0, 2)
    product = sc.Structure("P", transfer(1.9), 0, 2)
    found = sc.compare(ts_structure(), product)
    mode = found["mode"]
    assert mode["ts"] == "A" and mode["wavenumber"] == -1500.0
    assert {a["bond"] for a in mode["active"]} == {"C1–H9", "C5–H9"}
    assert mode["side"] == "+" and mode["changes_along_mode"] is True
    assert sc.compare(reactant, ts_structure())["mode"]["side"] == "−"

    order = [8, 3, 2, 1, 0, 7, 6, 5, 4]  # the TS numbered otherwise
    linked = sc.compare(reactant, product, ts_structure(order))["ts_check"]
    assert linked["verdict"] == "links", linked
    assert {linked["side_a"], linked["side_b"]} == {"+", "−"}
    assert sorted(linked["changed"]) == ["C1–H9", "C5–H9"]

    # The mode moves a methyl hydrogen instead: the changing bonds are not along it.
    wrong = ts_structure()
    wrong.modes = [[[0.0, 0.0, 0.0] for _ in range(9)]]
    wrong.modes[0][1] = [0.0, 1.0, 0.0]
    refused = sc.compare(reactant, product, wrong)["ts_check"]
    assert refused["verdict"] == "does not link"
    assert any("not along" in r for r in refused["reasons"])
    # Both ends on one side.
    same_side = sc.compare(product, sc.Structure("P2", transfer(2.0), 0, 2), ts_structure())
    assert same_side["ts_check"]["verdict"] == "does not link"


def test_structures_are_grouped_into_isomers_conformers_and_duplicates(open_client):
    """T-MCP-48: glycerol, itself shuffled and slightly moved, another conformer, an isomer
    (a hydrogen moved): one formula, two molecules, the first with two conformers; numbered
    otherwise or not, the duplicates fall together."""
    ensemble = frames(FIXTURES / "crest" / "crest_conformers.xyz")
    first = ensemble[0]
    shuffled, _ = scrambled(first, seed=2)
    nudged = [[r[0], r[1] + 0.01, r[2], r[3]] for r in first]
    nudged[3][2] += 0.01
    moved = [row[:] for row in first]
    oxygen = np.array(first[4][1:4])
    away = oxygen - np.mean([first[k][1:4] for k in (2, 8)], axis=0)
    moved[10] = ["H", *map(float, oxygen + 0.97 * away / np.linalg.norm(away))]
    structures = [first, shuffled, ensemble[10], nudged, moved]
    response = open_client.post(
        "/api/structures/group",
        json={"structures": [ref(s, label=f"s{n + 1}") for n, s in enumerate(structures)]},
    )
    assert response.status_code == 200, response.text
    found = response.json()
    (formula,) = found["formulas"]
    assert formula["formula"] == "C3H8O3"
    molecules = formula["molecules"]
    assert len(molecules) == 2
    conformers = molecules[0]["conformers"]
    assert [[m["index"] for m in c["members"]] for c in conformers] == [[1, 2, 4], [3]]
    assert conformers[0]["members"][1]["renumbered"] is True
    assert conformers[0]["members"][2]["rmsd"] < 0.05
    assert [m["label"] for m in molecules[1]["conformers"][0]["members"]] == ["s5"]
    assert "2 molecule(s)" in found["summary"]

    too_many = open_client.post(
        "/api/structures/group", json={"structures": [ref(first)] * (sc.MAX_GROUP + 1)}
    )
    assert too_many.status_code == 422 and "more than the 300" in too_many.json()["detail"]
    assert open_client.post("/api/structures/group", json={}).status_code == 422


def test_same_structure_is_independent_of_atom_order():
    """T-IMP-81: the order-independent duplicate test: a bound rules out what cannot match."""
    start = frames(FIXTURES / "sterics" / "3.xyz")[0]
    shuffled, truth = scrambled(start, seed=4, noise=0.005)
    found = sc.same_structure(start, shuffled, 0.05)
    assert found is not None and found.mapping == truth and found.rmsd < 0.05
    same = sc.same_structure(start, [r[:] for r in start], 0.05)
    assert same is not None and same.mapping is None and same.rmsd < 1e-9
    other = frames(FIXTURES / "crest" / "crest_conformers.xyz")
    assert sc.lower_bound(sc.signature(other[0]), sc.signature(other[10])) > 0.05
    assert sc.same_structure(other[0], other[10], 0.05) is None
    assert sc.same_structure(other[0], other[0][:-1], 0.05) is None


def _outputs(folder):
    folder.mkdir()
    for name in (TS, TS_SP):
        (folder / name).write_bytes((FIXTURES / "gaussian" / name).read_bytes())
    sub = folder / "water"
    sub.mkdir()
    (sub / "w.log").write_text(g.opt_freq(g.moved(WATER, 0.05), WATER), encoding="utf-8")
    # A single point on its geometry, its atoms numbered H, O, H.
    (sub / "w_sp.log").write_text(g.single_point(SWAPPED), encoding="utf-8")
    (folder / "notes.txt").write_text("not an output\n", encoding="utf-8")
    return folder


def test_a_folder_is_surveyed_without_importing_it(open_client, tmp_path):
    """T-MCP-45: per file and step what it is; the single point on the TS and the renumbered
    one on the water share their geometry; the TS's single point starts where it ended; after
    importing the TS, it is imported before and its structure is a node's."""
    folder = _outputs(tmp_path / "outputs")

    def survey() -> dict:
        response = open_client.get("/api/folder-survey", params={"path": str(folder)})
        assert response.status_code == 200, response.text
        return response.json()

    found = survey()
    assert found["outputs"] == 4 and found["other_files"] == 1
    files = {f["path"]: f for f in found["files"]}
    assert set(files) == {TS, TS_SP, "water/w.log", "water/w_sp.log"}
    ts = files[TS]
    assert ts["role"] == "transition_state" and ts["program"] == "Gaussian"
    assert [s["job"] for s in ts["steps"]] == ["ts_optimization", "frequency"]
    freq = ts["steps"][1]
    assert freq["imaginary_count"] == 1 and freq["imaginary"][0] < 0 and "g" in freq
    assert ts["steps"][0]["converged"] is True
    assert found["levels"][ts["level"] - 1].startswith("Gaussian")
    assert files[TS_SP]["geometry"] == ts["geometry"]
    assert files[TS_SP]["starts_from"] == [ts["geometry"]]
    water, water_sp = files["water/w.log"], files["water/w_sp.log"]
    assert water["formula"] == "H2O" and water["role"] == "minimum"
    assert water["steps"][1]["lowest_real"] == [1650.0, 3700.0, 3800.0]
    assert water_sp["geometry"] == water["geometry"] and water_sp["renumbered"] is True
    assert "already_imported" not in ts and "matches_node" not in ts
    assert open_client.get("/api/canvas").json()["nodes"] == []  # nothing imported

    plan = upload(open_client, TS, (FIXTURES / "gaussian" / TS).read_text(encoding="utf-8"))
    made = commit(open_client, plan)["node_id"]
    files = {f["path"]: f for f in survey()["files"]}
    assert files[TS]["already_imported"][0]["nodes"] == [node(open_client, made)["label"]]
    assert files[TS]["matches_node"][0]["node_id"] == made
    assert files[TS_SP]["matches_node"][0]["node_id"] == made

    missing = open_client.get("/api/folder-survey", params={"path": str(folder / "none")})
    assert missing.status_code == 422
    foreign = open_client.get(
        "/api/folder-survey", params={"path": str(folder)}, headers={"origin": "https://x.org"}
    )
    assert foreign.status_code == 403


def test_references_to_files_nodes_and_calculations(open_client, tmp_path):
    """T-MCP-46: a step and frame of a file, an IRC's end, a node with its frequency job, a
    calculation; a reference naming two sources or none is refused."""
    path = tmp_path / "irc.log"
    path.write_text(g.irc(("forward", "reverse"), points=4, checkpoint=False), encoding="utf-8")
    end = [[a[0], a[2], a[3], a[4]] for a in g.irc_point("forward", 4)]
    found = compare(open_client, {"path": str(path), "irc_end": "forward"}, ref(end))
    assert found["same_conformer"] and found["rmsd"] < 1e-4
    reverse = compare(open_client, {"path": str(path), "irc_end": "reverse"}, ref(end))
    assert reverse["same_molecule"] and not reverse["same_conformer"] and reverse["rmsd"] > 0.3

    ts_path = FIXTURES / "gaussian" / TS
    first = compare(
        open_client, {"path": str(ts_path), "step": 1, "frame": 1}, {"path": str(ts_path)}
    )
    assert first["same_molecule"] and first["rmsd"] > 0
    assert first["b"]["imaginary"][0] < 0 and "mode" in first

    plan = upload(open_client, "w.log", g.opt_freq(g.moved(WATER, 0.05), WATER))
    made = commit(open_client, plan)["node_id"]
    optimization = next(c for c in calculations(open_client, made) if c["type"] == "optimization")
    found = compare(
        open_client, {"node_id": made}, {"calculation_id": optimization["id"], "frame": 1}
    )
    assert found["a"]["label"] == node(open_client, made)["label"]
    assert "imaginary" in found["a"] and found["a"]["imaginary"] == []  # its frequency job
    assert found["same_molecule"] and found["rmsd"] > 0.01

    for bad in ({}, {"node_id": made, "xyz": "1\n\nH 0 0 0\n"}, {"node_id": "nope"}):
        response = open_client.post("/api/structures/compare", json={"a": bad, "b": bad})
        assert response.status_code == 422, bad
    wrong_end = {"path": str(ts_path), "irc_end": "forward"}
    response = open_client.post("/api/structures/compare", json={"a": wrong_end, "b": wrong_end})
    assert response.status_code == 422 and "no IRC" in response.json()["detail"]


def test_a_renumbered_duplicate_is_attached_in_the_nodes_numbering(open_client):
    """T-IMP-81: a single point on a node's geometry with its atoms numbered H, O, H is found
    as a possible duplicate numbered otherwise; attached, its calculation keeps the map to its
    file (D262c) and its geometry and frames come in the node's numbering."""
    plan = upload(open_client, "w.log", g.opt_freq(g.moved(WATER, 0.05), WATER))
    made = commit(open_client, plan)["node_id"]
    plan = upload(open_client, "w_sp.log", g.single_point(SWAPPED, "#P B3LYP/def2TZVP SP"))
    (duplicate,) = plan["duplicates"]
    assert duplicate["node_id"] == made and duplicate["renumbered"] is True
    assert "atom_map" not in duplicate
    assert any(
        "numbered otherwise" in w["message"] for w in plan["warnings"] if w["code"] == "W-DUP"
    )
    assert plan["blockers"]

    attached = commit(open_client, plan, duplicate_action="attach")
    assert attached["node_id"] == made
    single = next(c for c in calculations(open_client, made) if c["type"] == "single_point")
    assert single["renumbered"]
    with open_client.app.state.investigation.sessions() as session:
        stored = session.get(Calculation, single["id"])
        assert stored.atom_map == [1, 0, 2]
        assert stored.geometry == rows(WATER) == stored.node.geometry
    last = open_client.get(f"/api/calculations/{single['id']}/steps/-1").json()
    assert [line.split()[0] for line in last["xyz"].splitlines()[2:]] == ["O", "H", "H"]


def test_the_tools_over_mcp_and_in_the_guides(tmp_path):
    """T-MCP-49: three read tools the panel does not ask about, named in both guides, whose
    inputs say what a reference is; a comparison through the MCP server."""
    for name in TOOLS:
        assert BY_NAME[name].kind == "read" and name in PANEL_ALLOWED, name
        for text in (INSTRUCTIONS, NOTEBOOK_GUIDE):
            assert name in text, name
    schema = input_schema(BY_NAME["compare_structures"])
    assert "calculation_id" in schema["properties"]["a"]["description"]
    assert (
        "irc_end"
        in input_schema(BY_NAME["group_structures"])["properties"]["structures"]["description"]
    )

    async def run():
        bridge, page = _app_bridge()
        await page.post("/api/investigations", json={"folder": str(tmp_path / "i"), "name": "T"})
        found = _json(
            await bridge.call(
                "compare_structures", {"a": ref(transfer(1.1)), "b": ref(transfer(1.9))}
            )
        )
        assert [b["bond"] for b in found["broken"]] == ["C1–H9"]
        folder = _outputs(tmp_path / "outputs")
        surveyed = _json(await bridge.call("survey_folder", {"path": str(folder)}))
        assert surveyed["outputs"] == 4

    anyio.run(run)


@pytest.mark.parametrize("weak", [False, True])
def test_weak_contacts_are_apart_unless_counted(open_client, weak):
    """T-MCP-46, A181: an O–H stretched to 1.3 Å is a weak change, not a broken bond, unless
    count_weak."""
    stretched = rows([WATER[0], WATER[1], ("H", 1, 0.0, -1.0, -0.85)])
    found = compare(open_client, ref(rows(WATER)), ref(stretched), count_weak=weak)
    if weak:
        assert [b["bond"] for b in found["broken"]] == ["O1–H3"] and "weak" not in found
    else:
        assert found["broken"] == [] and found["weak"][0]["bond"] == "O1–H3"
        assert found["same_molecule"]
