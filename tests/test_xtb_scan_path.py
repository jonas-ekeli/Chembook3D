"""xTB relaxed scans and scan paths (`xtbscan.log`, `path.xyz`) imported as a scan that plays
in the step movie, and "Use this structure" (D112, A58)."""

import shutil

import pytest

from chembook3d.models import Calculation, CalculationType, Node
from chembook3d.parsers import xtb
from chembook3d.services import imports, trajectory
from tests.test_import import calculations, commit, node, upload
from tests.test_orca_xtb_parsers import FIXTURES
from tests.test_step_movie import import_file, steps

SCAN = FIXTURES / "xtb" / "dce_scan"
TOP = 4  # point 5 (0-based 4): the eclipsed H/Cl rotamer, the only top of the scan


def scan_text() -> str:
    return (SCAN / "xtbscan.log").read_text(encoding="utf-8")


def frames_of(text: str) -> list[tuple[float, list[str]]]:
    """(energy, atom lines) of each structure in an xtbscan.log."""
    lines = text.splitlines()
    found = []
    i = 0
    while i < len(lines):
        count = int(lines[i])
        found.append((float(lines[i + 1].split()[1]), lines[i + 2 : i + 2 + count]))
        i += 2 + count
    return found


def path_text(energies: list[float] | None = None) -> str:
    """A two-stage path.xyz made of the scan's structures, with the xtb call on each line."""
    frames = frames_of(scan_text())
    call = "xtb start.xyz --opt --input path.inp --alpb toluene --chrg 1 --uhf 1"
    out = []
    for n, (energy, atoms) in enumerate(frames):
        value = energies[n] if energies else energy
        stage = 1 if n < 6 else 2
        out += [str(len(atoms)), f" energy: {value:.12f} xtb: 6.7.1 stage: {stage} call: {call}"]
        out += atoms
    return "\n".join(out) + "\n"


# ---------- reading the files ----------


def test_xtbscan_log_alone_is_a_relaxed_scan_at_xtb_defaults():
    parsed = imports.read_file(scan_text(), "xtbscan.log")
    assert parsed.program == "xTB" and parsed.version == "6.7.1"
    (step,) = parsed.steps
    assert step.job_type == "other" and step.scan == "relaxed"
    assert step.route.text == xtb.SCAN_ROUTE and step.route.method == "GFN2-xTB"
    assert step.route.solvation_model is None
    assert len(step.geometries) == 13 and step.converged_geometries == list(range(13))
    assert step.geometry_points == list(range(1, 14)) and step.geometry_stages == []
    energies = [e for e, _ in frames_of(scan_text())]
    assert step.geometry_energies == energies
    # The node takes the top, not the last point, which is higher but ends the path.
    assert max(energies) == energies[-1]
    assert step.node_geometry == TOP and step.final_geometry is step.geometries[TOP]
    assert step.scf_energy == energies[TOP]
    # 1,2-dichloroethane has 50 electrons: charge 0, singlet.
    assert (step.charge, step.multiplicity) == (0, 1)
    assert step.missing == [
        "level of theory (taken as GFN2-xTB, gas phase)",
        "charge and multiplicity (taken as xTB's defaults)",
    ]
    assert step.termination == "normal"


def test_the_xtb_output_beside_it_gives_the_level(tmp_path):
    parsed = imports.read_file(scan_text(), "xtbscan.log", SCAN)
    (step,) = parsed.steps
    assert step.route.text == f"{xtb.SCAN_ROUTE}: xtb dce.xyz --opt --input scan.inp"
    assert step.route.method == "GFN2-xTB" and step.missing == []
    assert (step.charge, step.multiplicity) == (0, 1)
    # Of two outputs that ran a scan, the one whose final structure is the first point.
    output = (SCAN / "dce_scan.out").read_text(encoding="utf-8")
    other = output.replace("--input scan.inp", "--input scan.inp --alpb water").replace(
        "-0.01019694390298", "-0.51019694390298"
    )
    (tmp_path / "a.out").write_text(other.replace("--alpb water", "--alpb hexane"))
    water = output.replace("--input scan.inp", "--input scan.inp --alpb water")
    (tmp_path / "b.out").write_text(water)
    (step,) = imports.read_file(scan_text(), "xtbscan.log", tmp_path).steps
    assert (step.route.solvation_model, step.route.solvent) == ("ALPB", "water")
    # None matches: xTB's defaults.
    (tmp_path / "b.out").write_text(other)
    (step,) = imports.read_file(scan_text(), "xtbscan.log", tmp_path).steps
    assert step.route.solvation_model is None and step.missing


def test_optimization_log_is_refused_with_a_hint():
    with pytest.raises(imports.ImportFailed, match="optimization log.*xTB output"):
        imports.read_file((SCAN / "xtbopt.log").read_text(encoding="utf-8"), "xtbopt.log", SCAN)


def test_path_xyz_names_its_stages_and_level():
    rising = [-15.4 + 0.001 * n for n in range(13)]
    (step,) = imports.read_file(path_text(rising), "path.xyz").steps
    assert step.geometry_stages == [1] * 6 + [2] * 7
    assert step.route.method == "GFN2-xTB"
    assert (step.route.solvation_model, step.route.solvent) == ("ALPB", "toluene")
    assert (step.charge, step.multiplicity) == (1, 2) and step.missing == []
    # No top: the middle point.
    assert step.node_geometry == 6 and step.scf_energy == pytest.approx(rising[6])


def test_a_cut_off_file_keeps_its_whole_structures():
    text = scan_text()
    cut = text[: text.rindex("Cl")]
    (step,) = imports.read_file(cut, "xtbscan.log").steps
    assert len(step.geometries) == 12 and step.termination == "abnormal"


@pytest.mark.parametrize(
    ("energies", "chosen"),
    [
        ([0, 2, 1, 3, 2, 1], 3),  # the highest of two tops
        ([0, 1, 2, 3], 1),  # rising: the earlier middle point
        ([3, 2, 1], 1),
        ([0, 1, 1, 0], 1),  # a flat top is not higher than both neighbours: the middle
        ([0, 2, None, 1, 0], 2),  # a point without an energy decides nothing
        ([5], 0),
        ([0, 1], 0),
    ],
)
def test_representative_point(energies, chosen):
    assert xtb.representative(energies) == chosen


# ---------- import, movie and "Use this structure" ----------


def test_import_plays_every_point_and_the_node_is_the_top(open_client):
    plan = upload(open_client, "xtbscan.log", scan_text())
    assert plan["blockers"] == [] and plan["program"] == "xTB"
    assert [s["type"] for s in plan["steps"]] == ["other"]
    assert any(w["code"] == "W-PARSE" for w in plan["warnings"])
    node_id = commit(open_client, plan)["node_id"]
    (calc,) = calculations(open_client, node_id)
    assert calc["route"] == xtb.SCAN_ROUTE and calc["composite_label"] == "xTB GFN2-xTB"
    data = steps(open_client, calc["id"])
    assert data["scan"] == "relaxed" and data["points"] == 13 and data["in_place"] is True
    assert all(f["converged"] for f in data["frames"])
    assert [f["point"] for f in data["frames"]] == list(range(1, 14))
    energies = [e for e, _ in frames_of(scan_text())]
    assert calc["result"]["energy"] == energies[TOP]
    # The node's structure is point 5, and the movie shows it there unmoved.
    assert node(open_client, node_id)["role"] == "unspecified"
    calc_rows = stored(open_client, Calculation, calc["id"])
    assert max_difference(data["frames"][TOP]["geometry"], calc_rows) < 1e-5


def test_import_from_the_file_browser_reads_the_output_beside_it(open_client, tmp_path):
    source = tmp_path / "run"
    shutil.copytree(SCAN, source)
    plan = open_client.post(
        "/api/imports/from-path", json={"path": str(source / "xtbscan.log")}
    ).json()
    assert plan["blockers"] == [] and plan["warnings"] == []
    assert plan["steps"][0]["route"].endswith("--input scan.inp")
    node_id = commit(open_client, plan)["node_id"]
    (calc,) = calculations(open_client, node_id)
    assert calc["parse_warnings"] == [] and calc["program_version"] == "6.7.1"


def test_use_this_structure_changes_a_path_node_in_place(open_client):
    node_id = commit(open_client, upload(open_client, "xtbscan.log", scan_text()))["node_id"]
    (calc,) = calculations(open_client, node_id)
    before = stored(open_client, Node, node_id)
    response = open_client.post(f"/api/calculations/{calc['id']}/steps/8/use")
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["derived"] is False and result["node"]["id"] == node_id
    after = stored(open_client, Node, node_id)
    data = steps(open_client, calc["id"])
    assert max_difference(after, data["frames"][8]["geometry"]) < 1e-5
    assert after != before
    (calc,) = calculations(open_client, node_id)
    assert calc["result"]["energy"] == frames_of(scan_text())[8][0]
    assert max_difference(stored(open_client, Calculation, calc["id"]), after) < 1e-5
    # The movie still has every point, and the history keeps the old values.
    assert len(data["frames"]) == 13
    history = open_client.get("/api/history").json()
    changed = {(h["record_type"], h["field"]) for h in history if h["action"] == "update"}
    assert {("node", "geometry"), ("calculation", "geometry"), ("calculation", "energy")} <= changed
    # Only this node: no derived node was made.
    assert len(open_client.get("/api/nodes").json()) == 1
    assert open_client.post(f"/api/calculations/{calc['id']}/steps/13/use").status_code == 422
    assert open_client.post("/api/calculations/nope/steps/0/use").status_code == 404


def test_use_this_structure_elsewhere_makes_a_derived_node(open_client):
    calc = import_file(open_client, "gaussian", "dvb_scan_relaxed.log")
    original = node(open_client, calc["node_id"])
    original_rows = stored(open_client, Node, original["id"])
    data = steps(open_client, calc["id"])
    assert data["in_place"] is False
    response = open_client.post(f"/api/calculations/{calc['id']}/steps/3/use")
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["derived"] is True and result["node"]["id"] != original["id"]
    assert result["node"]["derived_from_id"] == original["id"]
    derived = stored(open_client, Node, result["node"]["id"])
    assert max_difference(derived, data["frames"][3]["geometry"]) < 1e-5
    assert stored(open_client, Node, original["id"]) == original_rows


def test_a_path_node_with_another_calculation_is_not_changed_in_place():
    def calc(route: str, program: str = "xTB", kind: str = CalculationType.OTHER) -> Calculation:
        return Calculation(program=program, type=kind, route=route)

    path = Node(calculations=[calc(xtb.SCAN_ROUTE), calc(f"{xtb.SCAN_ROUTE}: xtb a.xyz --opt")])
    assert trajectory.changes_in_place(path)
    path.calculations.append(calc("xtb a.xyz --opt", kind=CalculationType.OPTIMIZATION))
    assert not trajectory.changes_in_place(path)
    assert not trajectory.changes_in_place(Node(calculations=[calc(xtb.SCAN_ROUTE, "ORCA")]))
    assert not trajectory.changes_in_place(Node(calculations=[]))


def stored(client, model: type, record_id: str) -> list:
    """The stored geometry of a node or calculation, as rows."""
    with client.app.state.investigation.sessions() as session:
        return session.get(model, record_id).geometry


def max_difference(a: list, b: list) -> float:
    assert [r[0] for r in a] == [r[0] for r in b]
    pairs = zip(a, b, strict=True)
    return max(abs(p - q) for r, s in pairs for p, q in zip(r[1:], s[1:], strict=True))
