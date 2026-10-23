"""Pre-optimizations and the geometry level of single points (D100, FR-CALC-04): T-IMP-15,
T-IMP-16, T-IMP-17, T-IMP-54, T-IMP-56."""

from chembook3d import investigation as inv
from chembook3d.models import Calculation, HistoryEntry, InvestigationInfo, Node
from chembook3d.services import history, levels, repairs, warnings
from tests import gaussian_text as g
from tests.test_batch_import import nodes, preview, row, run, scan
from tests.test_constrained import MODREDUNDANT
from tests.test_import import calculations, commit, node, upload

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


# ---------- T-IMP-54: a free optimization continues a constrained pre-optimization ----------


def held_pre_optimized_node(client, label: str = "A") -> str:
    """A planned node pre-optimized with restraints, like a cloud xTB job with a
    constraints file (D157)."""
    planned = client.post("/api/nodes", json={"label": label, "xyz": xyz(GUESS)}).json()
    held = g.output(
        g.step(
            f"{PRE_ROUTE}=ModRedundant", [GUESS, PRE_END], -75.0, converged=True, body=MODREDUNDANT
        )
    )
    plan = upload(client, f"{label}_pre.log", held, planned["id"])
    assert "W-CONSTRAINED" in {w["code"] for w in plan["warnings"]}
    commit(client, plan, target_node_id=planned["id"])
    return planned["id"]


def guess_then_opt_freq() -> str:
    """A guess and a single point at the start geometry, then the optimization and frequency
    job, as one Gaussian file with several steps."""
    return g.output(
        g.step("#P HF/STO-3G SP", [PRE_END], -74.9),
        g.step("#P B3LYP/6-31G(d) SP", [PRE_END], -76.3),
        g.step("#P B3LYP/6-31G(d) Opt Freq", [PRE_END, DFT_END], converged=True, terminate=False),
        g.step(
            "#P Geom=AllCheck Guess=TCheck SCRTyp=Grad RB3LYP/6-31G(d) Freq",
            [DFT_END],
            frequencies=[1650.0, 3700.0, 3800.0],
            internal=True,
        ),
    )


def test_optimization_from_a_constrained_pre_optimization_continues_the_node(open_client):
    node_id = held_pre_optimized_node(open_client)
    plan = upload(open_client, "A_dft.log", g.opt_freq(PRE_END, DFT_END), node_id)
    assert plan["mode"] == "onto" and not plan["derived_offered"]
    assert plan["continues_from"]["level_label"] == f"{PRE} with constraints"
    assert [s["assignment"] for s in plan["steps"]] == ["node", "node"]
    assert [w["code"] for w in plan["warnings"]] == ["PRE-OPT"]

    result = commit(open_client, plan, target_node_id=node_id)
    assert result["derived_node_id"] is None and len(nodes(open_client)) == 1
    sp = upload(open_client, "A_SP.log", g.single_point(DFT_END), node_id)
    assert sp["steps"][0]["geometry_level_label"] == DFT
    commit(open_client, sp, target_node_id=node_id)
    # The held optimization stays a pre-optimization and gives no geometry level (D157).
    assert labels(open_client, node_id) == [
        ("optimization", PRE),
        ("optimization", DFT),
        ("frequency", DFT),
        ("single_point", f"{SP} // {DFT}"),
    ]


def test_batch_import_continues_a_constrained_pre_optimization(open_client, tmp_path):
    node_id = held_pre_optimized_node(open_client, "Ru-π-complex")
    folder = tmp_path / "results"
    folder.mkdir()
    # π became pi in the file name, so the name fits no label.
    (folder / "Ru-pi-complex.log").write_text(guess_then_opt_freq(), encoding="utf-8")
    plan = scan(open_client, folder)
    opt = row(plan, "Ru-pi-complex.log")
    assert opt["match"] == "first_geometry" and opt["mode"] == "onto" and not opt["derived"]
    assert opt["target"]["node_id"] == node_id
    assert "PRE-OPT" in {w["code"] for w in opt["warnings"]}

    # Choosing the node by hand plans the same.
    chosen = preview(open_client, plan, rows={opt["id"]: {"target": f"node:{node_id}"}})
    by_hand = row(chosen, "Ru-pi-complex.log")
    assert by_hand["match"] == "chosen" and not by_hand["derived"]
    assert "PRE-OPT" in {w["code"] for w in by_hand["warnings"]}

    response = run(open_client, plan)
    assert response.status_code == 200, response.text
    assert len(nodes(open_client)) == 1
    assert ("optimization", DFT) in labels(open_client, node_id)


def test_a_free_optimization_at_the_constrained_level_continues_it_too(open_client):
    node_id = held_pre_optimized_node(open_client)
    free = g.output(g.step(PRE_ROUTE, [PRE_END, DFT_END], -75.1, converged=True))
    plan = upload(open_client, "A_free.log", free, node_id)
    assert plan["continues_from"]["level_label"] == f"{PRE} with constraints"
    assert not plan["derived_offered"]
    assert [s["assignment"] for s in plan["steps"]] == ["node"]


# ---------- T-IMP-56: a continued pre-optimization warns on its own row only ----------


def codes(warnings: list[dict]) -> set[str]:
    return {w["code"] for w in warnings}


def test_a_continued_pre_optimization_does_not_warn_about_the_node_geometry(open_client):
    node_id = pre_optimized_node(open_client)
    plan = upload(open_client, "A_dft.log", g.opt_freq(PRE_END, DFT_END), node_id)
    commit(open_client, plan, target_node_id=node_id)
    pre, dft, freq = calculations(open_client, node_id)
    assert pre["pre_optimization"] and not dft["pre_optimization"]
    assert "W-GEOM" not in codes(pre["warnings"])
    assert "W-GEOM" not in codes(node(open_client, node_id)["warnings"])


def test_a_continued_constrained_pre_optimization_warns_on_its_row_only(open_client):
    node_id = held_pre_optimized_node(open_client)
    assert "W-CONSTRAINED" in codes(node(open_client, node_id)["warnings"])  # nothing follows

    plan = upload(open_client, "A_dft.log", g.opt_freq(PRE_END, DFT_END), node_id)
    commit(open_client, plan, target_node_id=node_id)
    pre = calculations(open_client, node_id)[0]
    assert pre["pre_optimization"]
    assert codes(pre["warnings"]) == {"W-CONSTRAINED"}
    found = codes(node(open_client, node_id)["warnings"])
    assert "W-CONSTRAINED" not in found and "W-GEOM" not in found
    overview = open_client.get("/api/overview").json()
    assert not [i for i in overview["open_items"] if i["status"] == "warning"]


def test_an_optimization_from_elsewhere_is_no_pre_optimization(open_client):
    node_id = held_pre_optimized_node(open_client)
    # A free optimization from another structure goes to a derived node (D100).
    plan = upload(open_client, "A_dft.log", g.opt_freq(OTHER, DFT_END), node_id)
    derived = commit(open_client, plan, target_node_id=node_id)["derived_node_id"]
    assert derived is not None
    [pre] = calculations(open_client, node_id)
    assert not pre["pre_optimization"]
    assert "W-CONSTRAINED" in codes(node(open_client, node_id)["warnings"])


def test_opening_an_old_notebook_finds_its_pre_optimizations(open_client):
    node_id = held_pre_optimized_node(open_client)
    plan = upload(open_client, "A_dft.log", g.opt_freq(PRE_END, DFT_END), node_id)
    commit(open_client, plan, target_node_id=node_id)
    investigation = open_client.app.state.investigation
    with investigation.sessions.begin() as session:
        for calculation in session.get(Node, node_id).calculations:
            calculation.start_geometry = None  # as imported before D168
        info = session.get(InvestigationInfo, 1)
        info.repairs = [n for n in info.repairs if n != "start_geometries"]
    assert "W-CONSTRAINED" in codes(node(open_client, node_id)["warnings"])
    open_client.post("/api/investigations/close")

    opened = inv.open_investigation(investigation.folder)
    try:
        with opened.sessions() as session:
            pre, dft, _ = sorted(
                session.get(Node, node_id).calculations, key=lambda c: c.created_at
            )
            assert dft.start_geometry == [[e, x, y, z] for e, _, x, y, z in PRE_END]
            assert levels.continued_by(pre) is dft
            found = {f.code for f in warnings.node_warnings(session.get(Node, node_id))}
            assert "W-CONSTRAINED" not in found and "W-GEOM" not in found
    finally:
        opened.close()


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
