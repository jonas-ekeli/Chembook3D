"""Pre-optimizations and the geometry level of single points (D100, FR-CALC-04): T-IMP-15,
T-IMP-16, T-IMP-17."""

from chembook3d import investigation as inv
from chembook3d.models import Calculation, HistoryEntry, InvestigationInfo, Node
from chembook3d.services import history, repairs
from tests import gaussian_text as g
from tests.test_batch_import import nodes, row, run, scan
from tests.test_import import calculations, commit, upload

GUESS = g.moved(g.WATER, 0.05)
PRE_END = g.moved(g.WATER, 0.02)  # where the low-level pre-optimization ended
DFT_END = g.WATER
OTHER = g.moved(g.WATER, 0.3)
PRE_ROUTE = "#P HF/STO-3G Opt"


def pre_optimization(start=GUESS, end=PRE_END) -> str:
    return g.output(g.step(PRE_ROUTE, [start, end], -75.0, converged=True))


def xyz(atoms) -> str:
    return f"{len(atoms)}\n\n" + "\n".join(f"{a[0]} {a[2]} {a[3]} {a[4]}" for a in atoms) + "\n"


def pre_optimized_node(client, label: str = "A") -> str:
    """A planned node whose guess was pre-optimized at a low level, like a cloud xTB job."""
    planned = client.post("/api/nodes", json={"label": label, "xyz": xyz(GUESS)}).json()
    plan = upload(client, f"{label}_pre.log", pre_optimization(), planned["id"])
    commit(client, plan, target_node_id=planned["id"])
    return planned["id"]


def labels(client, node_id: str) -> list[tuple[str, str]]:
    return [(c["type"], c["composite_label"]) for c in calculations(client, node_id)]


def option_labels(client) -> set[str]:
    return {o["label"] for o in client.get("/api/energies/options").json()["levels"]}


DFT = "Gaussian B3LYP/6-31G(D)"
SP = "Gaussian B3LYP/DEF2TZVP"
PRE = "Gaussian HF/STO-3G"


# ---------- T-IMP-15: an optimization that continues a pre-optimization ----------


def test_optimization_from_the_pre_optimized_geometry_continues_the_node(open_client):
    node_id = pre_optimized_node(open_client)
    plan = upload(open_client, "A_dft.log", g.opt_freq(PRE_END, DFT_END), node_id)
    assert plan["mode"] == "onto" and not plan["derived_offered"]
    assert plan["continues_from"]["level_label"] == PRE
    assert [s["assignment"] for s in plan["steps"]] == ["node", "node"]
    assert [w["code"] for w in plan["warnings"]] == ["PRE-OPT"]

    result = commit(open_client, plan, target_node_id=node_id)
    assert result["node_id"] == node_id and result["derived_node_id"] is None
    assert len(nodes(open_client)) == 1
    changes = open_client.get("/api/history", params={"record_id": node_id}).json()
    moved = [e for e in changes if e["field"] == "geometry"]
    assert moved[0]["new_value"][0][3] == DFT_END[0][4]  # the DFT result, newest first

    sp = upload(open_client, "A_SP.log", g.single_point(DFT_END), node_id)
    assert sp["steps"][0]["assignment"] == "node"
    assert sp["steps"][0]["geometry_level_label"] == DFT
    commit(open_client, sp, target_node_id=node_id)
    assert labels(open_client, node_id) == [
        ("optimization", PRE),
        ("optimization", DFT),
        ("frequency", DFT),
        ("single_point", f"{SP} // {DFT}"),
    ]
    found = option_labels(open_client)
    assert f"{SP} // {DFT}" in found and f"{DFT} // {PRE}" not in found and DFT in found


def test_restart_at_the_same_level_still_makes_a_derived_node(open_client):
    node_id = pre_optimized_node(open_client)
    restart = g.output(g.step(PRE_ROUTE, [PRE_END, DFT_END], -75.1, converged=True))
    plan = upload(open_client, "A_again.log", restart, node_id)
    assert plan["continues_from"] is None and plan["derived_offered"]


def test_optimization_from_elsewhere_still_makes_a_derived_node(open_client):
    node_id = pre_optimized_node(open_client)
    plan = upload(open_client, "A_dft.log", g.opt_freq(OTHER, DFT_END), node_id)
    assert plan["continues_from"] is None and plan["derived_offered"]


# ---------- T-IMP-16: batch import after a pre-optimization ----------


def test_batch_import_continues_the_pre_optimization(open_client, tmp_path):
    node_id = pre_optimized_node(open_client, "RuMCB")
    folder = tmp_path / "results"
    folder.mkdir()
    # Named so that no file name fits: the start geometry finds the node.
    (folder / "run7_opt.log").write_text(g.opt_freq(PRE_END, DFT_END), encoding="utf-8")
    (folder / "run7_SP.log").write_text(g.single_point(DFT_END), encoding="utf-8")
    plan = scan(open_client, folder)
    opt = row(plan, "run7_opt.log")
    assert opt["match"] == "first_geometry" and opt["mode"] == "onto"
    assert opt["target"]["node_id"] == node_id
    assert [w["code"] for w in opt["warnings"]] == ["PRE-OPT"]
    sp = row(plan, "run7_SP.log")
    assert sp["match"] == "geometry" and sp["target"]["node_id"] == node_id

    response = run(open_client, plan)
    assert response.status_code == 200, response.text
    assert len(nodes(open_client)) == 1
    assert labels(open_client, node_id)[-1] == ("single_point", f"{SP} // {DFT}")


# ---------- T-IMP-17: notebooks imported before D100 are repaired once ----------


def _broken_notebook(client, tmp_path):
    """The records an import made before D100: the DFT optimization on the pre-optimized node
    without moving its geometry, and single points given the pre-optimization's level."""
    node_id = pre_optimized_node(client)
    # Import the DFT file as its own node to get its copied file, then move its records.
    plan = upload(client, "A_dft.log", g.opt_freq(PRE_END, DFT_END))
    other = commit(client, plan, duplicate_action="new")["node_id"]
    investigation = client.app.state.investigation
    with investigation.sessions.begin() as session:
        a = session.get(Node, node_id)
        pre = next(c for c in a.calculations)
        for calculation in list(session.get(Node, other).calculations):
            calculation.node_id = node_id
        session.flush()
        session.delete(session.get(Node, other))
        sps = {}
        for name, rows in (("dft", DFT_END), ("lost", OTHER), ("hand", DFT_END)):
            sp = Calculation(
                node_id=node_id,
                type="single_point",
                program="Gaussian",
                level_id=pre.level_id,  # stands in for the SP level; only the geometry matters
                geometry_level_id=pre.level_id,
                geometry=[[e, x, y, z] for e, _, x, y, z in rows],
            )
            session.add(sp)
            session.flush()
            sps[name] = sp.id
        history.record(session, "calculation", sps["hand"], "update", "geometry_level", None, PRE)
        session.get(InvestigationInfo, 1).repairs = []
    return node_id, sps, investigation.folder


def test_opening_an_old_notebook_repairs_the_geometry_levels(open_client, tmp_path):
    node_id, sps, folder = _broken_notebook(open_client, tmp_path)
    open_client.post("/api/investigations/close")
    opened = inv.open_investigation(folder)
    try:
        with opened.sessions() as session:
            a = session.get(Node, node_id)
            assert a.geometry == [[e, x, y, z] for e, _, x, y, z in DFT_END]
            by_id = {c.id: c for c in a.calculations}
            dft = next(
                c
                for c in a.calculations
                if c.type == "optimization" and c.id not in sps and c.level.method == "B3LYP"
            )
            assert by_id[sps["dft"]].geometry_level_id == dft.level_id
            assert by_id[sps["lost"]].geometry_level_id is None  # left for the user
            assert by_id[sps["hand"]].geometry_level_id != dft.level_id  # set by hand: kept
            info = session.get(InvestigationInfo, 1)
            assert info.repairs == list(repairs.REPAIRS)
            sources = {e.source for e in session.query(HistoryEntry) if e.source == "repair"}
            assert sources == {"repair"}
    finally:
        opened.close()

    # It runs once: a change made afterwards stays.
    reopened = inv.open_investigation(folder)
    with reopened.sessions.begin() as session:
        session.get(Calculation, sps["dft"]).geometry_level_id = None
    reopened.close()
    again = inv.open_investigation(folder)
    try:
        with again.sessions() as session:
            assert session.get(Calculation, sps["dft"]).geometry_level_id is None
    finally:
        again.close()


def test_a_new_investigation_needs_no_repair(tmp_path):
    created = inv.create_investigation(tmp_path / "new", "New")
    try:
        with created.sessions() as session:
            assert session.get(InvestigationInfo, 1).repairs == list(repairs.REPAIRS)
    finally:
        created.close()


def test_a_failing_repair_does_not_keep_the_notebook_closed(open_client, tmp_path, monkeypatch):
    _, _, folder = _broken_notebook(open_client, tmp_path)
    open_client.post("/api/investigations/close")
    from chembook3d.services import repairs

    def broken(session, folder):
        raise RuntimeError("boom")

    monkeypatch.setitem(repairs.REPAIRS, "geometry_levels", broken)
    opened = inv.open_investigation(folder)
    with opened.sessions() as session:
        assert session.get(InvestigationInfo, 1).repairs == []  # tried again next time
    opened.close()
