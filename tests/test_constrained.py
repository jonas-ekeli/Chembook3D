"""Scans and constrained optimizations imported as output files (D157, FR-IMP-20): T-IMP-41,
T-IMP-42, T-IMP-43."""

from chembook3d import investigation as inv
from chembook3d.models import Calculation, HistoryEntry, InvestigationInfo, Node
from chembook3d.parsers import gaussian, orca, xtb
from chembook3d.services import imports, repairs
from chembook3d.xyz import ELEMENTS
from tests import gaussian_text as g
from tests.test_batch_import import nodes, row, run, scan
from tests.test_gaussian_parser import FIXTURES
from tests.test_import import calculations, commit, fixture, node, upload

SCAN = "dvb_scan_relaxed.log"
ROOT = FIXTURES.parent


def held_opt(body, route="#P B3LYP/6-31G(d) Opt=ModRedundant") -> str:
    return g.output(g.step(route, [g.moved(g.WATER, 0.05), g.WATER], converged=True, body=body))


MODREDUNDANT = [
    " The following ModRedundant input section has been read:",
    " B       1       2 F",
    " X       3 F",
    " A       2       1       3",
    "",
]


def last_point() -> list[tuple]:
    """The scan's last point, as gaussian_text writes atoms."""
    (step,) = gaussian.parse(fixture(SCAN)).steps
    return [(a.element, ELEMENTS.index(a.element) + 1, a.x, a.y, a.z) for a in step.final_geometry]


# ---------- T-IMP-41: what held a step, from each program ----------


def test_parsers_say_what_held_the_structure():
    (relaxed,) = gaussian.parse(fixture(SCAN)).steps
    assert relaxed.job_type == "optimization" and relaxed.constraints == ["scan D(3,4,9,10)"]
    (rigid,) = gaussian.parse(fixture("dvb_scan_unrelaxed.log")).steps
    assert rigid.constraints == ["rigid scan"]
    (frozen,) = gaussian.parse(held_opt(MODREDUNDANT)).steps
    assert frozen.constraints == ["frozen B(1,2)", "frozen X(3)"]
    atoms = [" NAtoms=      3 NQM=        3 NQMF=       2 NMMI=      0 NMMIF=      0"]
    (fixed,) = gaussian.parse(held_opt(atoms, "#P B3LYP/6-31G(d) Opt")).steps
    assert fixed.constraints == ["2 frozen atoms"]
    added_only = [MODREDUNDANT[0], MODREDUNDANT[3], ""]  # a coordinate added, nothing held
    assert gaussian.parse(held_opt(added_only)).steps[0].constraints == []
    for name in ("aminationTS-full-unfrz-c1.log", "aziridinium-phos-full-c1.log"):
        assert all(not s.constraints for s in gaussian.parse(fixture(name)).steps)

    text = (ROOT / "orca" / "dvb_gopt.out").read_text(encoding="utf-8")
    assert all(not s.constraints for s in orca.parse(text).steps)
    block = "|  8> %geom Constraints\n|  9>   {B 0 1 C}\n| 10>   {C 4 C}\n| 11> end end\n"
    held = text.replace("|  8> \n", block, 1)
    (step,) = orca.parse(held).steps
    assert step.job_type == "optimization"
    assert step.constraints == ["frozen B(1,2)", "frozen X(5)"]
    (rigid,) = orca.parse((ROOT / "orca" / "dvb_scan_unrelaxed.out").read_text("utf-8")).steps
    assert rigid.constraints == ["rigid scan"]

    output = (ROOT / "xtb" / "dce_scan" / "dce_scan.out").read_text(encoding="utf-8")
    (step,) = xtb.parse(output).steps
    assert step.constraints == ["constrained D(3,1,2,6)", "relaxed scan"]
    assert xtb.parse((ROOT / "xtb" / "dvb_opt.out").read_text("utf-8")).steps[0].constraints == []
    # D112: an xTB scan path is a path, never a held optimization.
    log = (ROOT / "xtb" / "dce_scan" / "xtbscan.log").read_text(encoding="utf-8")
    (path,) = xtb.parse_structures(log, "xtbscan.log").steps
    assert path.scan == "relaxed" and path.constraints == []


# ---------- T-IMP-42: the import of a relaxed scan ----------


def test_a_relaxed_scan_imports_with_no_role_and_gives_no_geometry_level(open_client):
    client = open_client
    plan = upload(client, SCAN, fixture(SCAN))
    assert plan["suggested"]["role"] == "unspecified"
    assert plan["suggested"]["status"] == "done"  # D137(c): the scan still imports
    [warning] = [w for w in plan["warnings"] if w["code"] == "W-CONSTRAINED"]
    assert "scan D(3,4,9,10)" in warning["message"]
    node_id = commit(client, plan)["node_id"]
    assert node(client, node_id)["role"] == "unspecified"
    [scan_calc] = calculations(client, node_id)
    assert scan_calc["constraints"] == ["scan D(3,4,9,10)"]
    assert "W-CONSTRAINED" in {w["code"] for w in scan_calc["warnings"]}
    assert "W-CONSTRAINED" in {w["code"] for w in node(client, node_id)["warnings"]}

    # A single point on the scan's last point takes no geometry level from it (D100).
    sp = upload(client, "sp.log", g.single_point(last_point()), node_id)
    assert sp["mode"] == "onto" and [s["assignment"] for s in sp["steps"]] == ["node"]
    assert sp["steps"][0]["geometry_level_label"] is None
    commit(client, sp, target_node_id=node_id)
    by_type = {c["type"]: c for c in calculations(client, node_id)}
    assert by_type["single_point"]["geometry_level"] is None
    labels = {o["label"] for o in client.get("/api/energies/options").json()["levels"]}
    assert "Gaussian B3LYP/DEF2TZVP // unknown geometry level" in labels
    assert not any("// Gaussian B3LYP/STO-3G" in label for label in labels)


def test_a_constrained_optimization_continues_no_pre_optimization(open_client):
    client = open_client
    start, end = g.moved(g.WATER, 0.05), g.WATER
    pre = g.output(g.step("#P HF/STO-3G Opt", [g.moved(g.WATER, 0.1), start], converged=True))
    node_id = commit(client, upload(client, "pre.log", pre))["node_id"]
    held = g.output(
        g.step(
            "#P B3LYP/6-31G(d) Opt=ModRedundant", [start, end], converged=True, body=MODREDUNDANT
        )
    )
    plan = upload(client, "held.log", held, node_id)
    assert plan["continues_from"] is None and plan["derived_offered"]
    assert "PRE-OPT" not in {w["code"] for w in plan["warnings"]}
    assert [s.constrained for s in imports.read_file(held, "held.log").steps] == [True]


def test_a_batch_import_flags_the_scan(open_client, tmp_path):
    folder = tmp_path / "batch"
    folder.mkdir()
    (folder / SCAN).write_text(fixture(SCAN), encoding="utf-8")
    batch = scan(open_client, folder)
    assert "W-CONSTRAINED" in {w["code"] for w in row(batch, SCAN)["warnings"]}
    response = run(open_client, batch)
    assert response.status_code == 200, response.text
    [made] = nodes(open_client)
    assert made["role"] == "unspecified"


# ---------- T-IMP-43: the one-off repair of older investigations ----------


def test_opening_an_old_notebook_marks_its_scans(open_client):
    client = open_client
    plan = upload(client, SCAN, fixture(SCAN))
    node_id = commit(client, plan)["node_id"]
    sp = upload(client, "sp.log", g.single_point(last_point()), node_id)
    commit(client, sp, target_node_id=node_id)
    investigation = client.app.state.investigation
    with investigation.sessions.begin() as session:
        # What an import made before D157: no constraints, the SP at the scan's level.
        a = session.get(Node, node_id)
        a.role = "minimum"
        held = next(c for c in a.calculations if c.type == "optimization")
        held.constraints = []
        single = next(c for c in a.calculations if c.type == "single_point")
        single.geometry_level_id = held.level_id
        ids = held.id, single.id
        session.get(InvestigationInfo, 1).repairs = ["geometry_levels", "scan_path_edges"]
    folder = investigation.folder
    client.post("/api/investigations/close")

    opened = inv.open_investigation(folder)
    try:
        with opened.sessions() as session:
            assert session.get(Calculation, ids[0]).constraints == ["scan D(3,4,9,10)"]
            assert session.get(Calculation, ids[1]).geometry_level_id is None
            assert session.get(Node, node_id).role == "minimum"  # the user's role is kept
            assert session.get(InvestigationInfo, 1).repairs == list(repairs.REPAIRS)
            entry = (
                session.query(HistoryEntry)
                .filter_by(record_id=ids[1], field="geometry_level")
                .one()
            )
            assert entry.source == "repair" and entry.new_value is None
    finally:
        opened.close()
