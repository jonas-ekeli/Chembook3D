"""A species joining or leaving, sent as a scan path job (D120, T-PATH-23…26): path.json's
`species`, the separated end in start.xyz or end.xyz, the brief's association strategies,
`pathtools.py check` on the separated end with the leftover interaction, a metal that changes
shape as it gains a ligand, and the import notes. The structures are made up: the small
ruthenium alkylidene of `olefin_complex`, and RuCl2(PH3)(=CH2) with ethylene optimised here with
GFN2-xTB 6.7.1; never one of Jonas's structures."""

import json

import numpy as np
import pytest

from chembook3d import cloud_jobs, pathtools
from chembook3d.services import atom_matching, scan_path
from tests.test_overlay import xyz_text
from tests.test_pathway import edge, node
from tests.test_scan_path import rows_of
from tests.test_scan_path_drive import send
from tests.test_scan_path_species import attach, binding, plan, same
from tests.test_species import put

# RuCl2(PH3)(=CH2), its ethylene π-complex and ethylene, each optimised alone (GFN2-xTB).
RU_14E = [
    ["Ru", -0.2094, 0.0914, 0.2602],
    ["C", -0.3296, -0.2271, 2.0912],
    ["H", 0.6337, -0.3111, 2.6114],
    ["H", -1.1985, -0.3450, 2.7375],
    ["Cl", 0.3374, 2.1568, -0.1555],
    ["Cl", 0.3657, -1.7050, -0.8358],
    ["P", -2.4047, 0.0871, -0.0031],
    ["H", -3.0410, 1.2162, -0.5104],
    ["H", -3.2547, -0.1255, 1.0720],
    ["H", -2.9890, -0.8278, -0.8737],
]
RU_COMPLEX = [
    ["C", 2.2838, 0.0001, 0.6455],
    ["C", 2.1082, -0.0027, -0.6978],
    ["H", 2.5027, 0.9255, 1.1690],
    ["H", 2.4981, -0.9243, 1.1729],
    ["H", 2.1758, 0.9203, -1.2687],
    ["H", 2.1716, -0.9284, -1.2643],
    ["Ru", -0.0204, 0.0044, 0.2308],
    ["C", -0.1710, 0.0010, 2.1161],
    ["H", 0.6795, -0.0015, 2.8004],
    ["H", -1.1330, -0.0010, 2.6394],
    ["Cl", -0.1561, 2.1739, -0.4758],
    ["Cl", -0.1805, -2.1602, -0.4904],
    ["P", -2.2708, 0.0024, -0.0005],
    ["H", -3.0641, 1.0564, 0.4134],
    ["H", -3.0611, -1.0277, 0.4735],
    ["H", -2.7126, -0.0377, -1.3129],
]
ETHYLENE = [
    ["C", 2.3778, -0.0003, 0.6119],
    ["C", 2.2027, -0.0028, -0.6930],
    ["H", 2.4546, 0.9152, 1.1756],
    ["H", 2.4505, -0.9136, 1.1796],
    ["H", 2.1295, 0.9101, -1.2608],
    ["H", 2.1250, -0.9180, -1.2567],
]
ENERGIES = {"14e": -19.519891647558, "ethylene": -6.271260262119}  # Eh, GFN2-xTB


def _job(client, body: dict) -> tuple[dict, dict, str, object]:
    job = send(client, body)["job"]
    folder = cloud_jobs.jobs_dir(client.app.state.investigation.folder) / job["id"]
    settings = json.loads((folder / "inputs" / "path.json").read_text(encoding="utf-8"))
    text = (folder / "job.md").read_text(encoding="utf-8")
    return job, settings, " ".join(text.split()), folder  # the brief's words, lines aside


def test_a_species_that_joins_is_sent_apart(open_client):
    # T-PATH-23 (D120): start.xyz is the separated structure the dialog showed, path.json's
    # `species` names its atoms, bonds (bound and separated lengths), the separated end, the
    # charges alone and a two-stage approach, and the brief pulls it out from the bound end
    # first; the complex and the species alone go along for the leftover interaction.
    ids = binding(open_client)
    attach(open_client, ids)
    body = {"start_id": ids["start"], "end_id": ids["end"], "clearance": 5}
    planned = plan(open_client, body)
    job, settings, text, folder = _job(open_client, body)
    joining = settings["species"]
    assert job["scan_path"]["species"] == joining
    assert joining["label"] == "ethylene" and joining["direction"] == "joins"
    assert joining["separated"] == "start" and joining["atoms"] == [7, 8, 9, 10, 11, 12]
    assert [b["atoms"] for b in joining["bonds"]] == [[4, 8], [4, 10]]
    for bond in joining["bonds"]:
        assert bond["bound"] == pytest.approx(2.214, abs=0.01) and bond["separated"] > 5
    assert joining["anchor"] == [4] and joining["clearance"] == 5
    assert joining["closest"] == pytest.approx(5.0, abs=0.03)
    assert joining["complex"] == {"label": "alkylidene", "charge": 0, "uhf": 0}
    assert joining["alone"] == {"charge": 0, "uhf": 0}
    approach = joining["approach"]
    anchor, centre = approach["distance"]["atoms"]
    assert anchor == 4 and centre in (8, 10) and approach["distance"]["to"] == 3.5
    assert approach["angle"]["atoms"][:2] == [4, centre]
    assert approach["dihedral"]["atoms"][1:3] == [4, centre]
    assert approach["dihedral"]["atoms"][0] not in joining["atoms"]

    inputs = folder / "inputs"
    start = pathtools.read_xyz(str(inputs / "start.xyz"))
    assert same(start, rows_of(planned["species"]["separated_xyz"]))
    assert same(
        pathtools.read_xyz(str(inputs / "end.xyz")), rows_of(planned["match"]["renumbered_xyz"])
    )
    assert len(pathtools.read_xyz(str(inputs / "complex.xyz"))) == 6
    assert len(pathtools.read_xyz(str(inputs / "species.xyz"))) == 6

    assert "## A species joins: “ethylene”" in text
    assert "`inputs/start.xyz`: “alkylidene”, with “ethylene” apart" in text
    assert "it is apart at the start and bound at the end, by 4–8, 4–10." in text
    pull = text.index("**Pull it out from the bound end and turn it round.**")
    assert pull < text.index("**Bring it in.**") < text.index("**In two stages.**")
    assert pull < text.index("- One concerted scan of the bonds that form and break")
    assert "It runs from the end back, so join it with `--reverse`." in text
    assert f"distance 4–{centre} from " in text and "to 3.50 Å" in text
    assert "`--apart complex/xtbopt.xyz species/xtbopt.xyz`" in text
    assert "The path must start there." in text
    assert "`species` reports the separated end from `check`" in text
    brief = (folder / "job.md").read_text(encoding="utf-8")
    section = brief[brief.index("## A species joins") : brief.index("## Running xTB scans")]
    assert max(len(line) for line in section.splitlines()) <= 95  # filled again once named


def test_a_species_that_leaves_is_sent_bound(open_client):
    # T-PATH-24 (D120): read the other way, the start is the bound complex as it is and the
    # end the separated structure; the separated end stands in for the end's RMSD, and the
    # mapping note says where the species' own atoms begin.
    ids = binding(open_client, scramble=False)
    attach(open_client, ids)
    body = {"start_id": ids["end"], "end_id": ids["start"]}
    planned = plan(open_client, body)
    _, settings, text, folder = _job(open_client, body)
    joining = settings["species"]
    assert joining["direction"] == "leaves" and joining["separated"] == "end"
    assert joining["atoms"] == [1, 2, 9, 10, 11, 12] and joining["anchor"] == [6]
    inputs = folder / "inputs"
    assert same(
        pathtools.read_xyz(str(inputs / "end.xyz")), rows_of(planned["species"]["separated_xyz"])
    )
    assert same(
        pathtools.read_xyz(str(inputs / "start.xyz")), rows_of(planned["match"]["start_xyz"])
    )
    assert "## A species leaves: “ethylene”" in text
    assert "`inputs/end.xyz`: “alkylidene”, with “ethylene” apart" in text
    assert "For this end it stands in for `active_end_rmsd`." in text
    assert "past 6 it is “ethylene”'s own order" in text
    assert "**Pull it out from the bound end.**" in text
    assert "starting from `inputs/start.xyz`" in text
    assert "It runs from the end back: join it with `--reverse`." in text


def _ru_ids(client) -> dict:
    ids = {
        "start": node(client, label="14e", xyz=xyz_text(RU_14E), charge=0, multiplicity=1)["id"],
        "end": node(client, label="π-complex", xyz=xyz_text(RU_COMPLEX), charge=0,
                    multiplicity=1)["id"],
        "ethylene": node(client, label="ethylene", kind="species", xyz=xyz_text(ETHYLENE),
                         charge=0, multiplicity=1)["id"],
    }  # fmt: skip
    ids["edge"] = edge(client, ids["start"], ids["end"])["id"]
    put(client, f"/transitions/{ids['edge']}/species",
        {"species_id": ids["ethylene"], "direction": "joins", "count": 1})  # fmt: skip
    return ids


def test_a_metal_that_changes_shape_is_matched_by_where_its_atoms_go(open_client):
    # T-PATH-25 (D120): the 14-electron complex is not the π-complex without ethylene: its
    # chlorides swing round when ethylene leaves, so keeping Ru's handedness paired them the
    # wrong way (1.9 Å). Ru, which ethylene binds to, may change shape: the chlorides now pair
    # by where they go, and the complex fits its bound pose within 0.7 Å.
    strict = atom_matching.match(RU_14E, RU_COMPLEX[6:])
    assert strict.mapping[4:6] == [5, 4] and strict.rmsd > 1.5
    loose = atom_matching.match(RU_14E, RU_COMPLEX[6:], loose={0})
    assert loose.mapping[4:6] == [4, 5] and loose.rmsd < 0.7 and loose.inverted == []
    ids = _ru_ids(open_client)
    out = plan(open_client, {"start_id": ids["start"], "end_id": ids["end"]})
    assert out["match"]["confident"] and out["match"]["doubts"] == []
    assert out["match"]["rmsd"] < 0.7
    assert out["match"]["mapping"][:10] == list(range(7, 17))


def _pulled(separated, bound, atoms, steps=12, closer=None):
    """A made-up path from the separated structure to the bound one: the species slid in
    along a straight line as the complex changes shape, the first species `closer` to the
    complex, energies falling."""
    a, b = np.array([r[1:] for r in separated]), np.array([r[1:] for r in bound])
    frames = []
    for k in range(steps):
        xyz = a + k / (steps - 1) * (b - a)
        if k == 0 and closer is not None:
            xyz[atoms] = xyz[atoms] - closer
        rows = [[r[0], *map(float, p)] for r, p in zip(bound, xyz, strict=True)]
        frames.append(pathtools.format_frame(rows, f" energy: {-25.79 - 0.0035 * k:.6f}"))
    return "".join(frames)


def test_check_judges_the_separated_end(open_client, tmp_path):
    # T-PATH-26 (D120): `check --settings` judges the separated end on the complex and on the
    # species' closest contact, and `--apart` gives the leftover interaction from the complex
    # and the species each optimised alone; the import notes name the species.
    ids = _ru_ids(open_client)
    _, settings, _, folder = _job(open_client, {"start_id": ids["start"], "end_id": ids["end"]})
    inputs = folder / "inputs"
    start = pathtools.read_xyz(str(inputs / "start.xyz"))
    end = pathtools.read_xyz(str(inputs / "end.xyz"))
    joining = settings["species"]
    atoms = [a - 1 for a in joining["atoms"]]
    path = tmp_path / "path.xyz"
    path.write_text(_pulled(start, end, atoms), encoding="utf-8")
    report = pathtools.check(
        str(path), start, end, active=settings["active"], species=joining,
        apart=[ENERGIES["14e"], ENERGIES["ethylene"]],
    )  # fmt: skip
    separated = report["separated"]
    assert separated["end"] == "start" and separated["reached"] and report["reached_start"]
    assert separated["complex_rmsd"] < 0.01
    assert separated["closest_contact"] == pytest.approx(joining["closest"], abs=0.01)
    leftover = (-25.79 - ENERGIES["14e"] - ENERGIES["ethylene"]) * pathtools.HARTREE_KCAL
    assert separated["leftover_kcal"] == pytest.approx(leftover)
    assert report["good"] and report["stray_bond_changes"] == []
    # The species too close at the start: not apart.
    line = np.array(start[atoms[0]][1:]) - np.array(start[0][1:])
    path.write_text(_pulled(start, end, atoms, closer=0.25 * line), encoding="utf-8")
    report = pathtools.check(str(path), start, end, species=joining)
    assert report["separated"]["closest_contact"] < pathtools.SEPARATED
    assert not report["reached_start"] and not report["good"]
    # Read the other way, the separated end is the end and stands in for its RMSD.
    back = tmp_path / "back.xyz"
    path.write_text(_pulled(start, end, atoms), encoding="utf-8")
    frames = pathtools.read_frames(str(path))
    back.write_text("".join(pathtools.format_frame(r, c) for c, r in reversed(frames)), "utf-8")
    leaving = dict(joining, separated="end")
    report = pathtools.check(str(back), end, start, active=settings["active"], species=leaving)
    assert report["judged_on"] == "the complex, with the species apart"
    assert report["reached_end"] and report["good"] and "reached_start" not in report

    # The command line, with the energies of each alone from their xtbopt.xyz.
    for name, rows, energy in (("c.xyz", RU_14E, ENERGIES["14e"]),
                               ("s.xyz", ETHYLENE, ENERGIES["ethylene"])):  # fmt: skip
        (tmp_path / name).write_text(
            pathtools.format_frame(rows, f" energy: {energy} gnorm: 0.0007 xtb: 6.7.1"), "utf-8"
        )
    args = ["check", str(path), str(inputs / "start.xyz"), str(inputs / "end.xyz")]
    apart = ["--apart", str(tmp_path / "c.xyz"), str(tmp_path / "s.xyz")]
    assert pathtools.main([*args, "--settings", str(inputs / "path.json"), *apart, "--json"]) == 0
    with pytest.raises(SystemExit):
        pathtools.main([*args, *apart])  # --apart needs the species from --settings

    def notes(report):
        job = {"id": "j", "scan_path": {"species": joining}}
        return scan_path._notes(job, {"path": {"species": report}}, "14e", "π-complex")

    text = notes({"closest_contact": 4.04, "leftover_kcal": -2.2})
    assert "“ethylene” joins along this path (D120)." in text
    assert "a few Å from the complex at the start end, not apart (closest contact 4.0 Å)." in text
    assert "lies 2.2 kcal/mol below the complex and “ethylene” each optimised alone." in text
    assert "each optimised alone" not in notes(None) and "ethylene" in notes(None)
    assert "ethylene" not in scan_path._notes({"id": "j"}, {"path": {}}, "A", "B")
