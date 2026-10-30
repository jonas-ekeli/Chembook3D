"""Two gaps in how levels of theory were told apart, found on published outputs: dispersion
parameters set by IOp with no dispersion model (D199), and a node whose optimization was run
elsewhere, with only its frequency job imported (D200)."""

from chembook3d import investigation as inv
from chembook3d.models import (
    Calculation,
    CustomDispersion,
    HistoryEntry,
    InvestigationInfo,
    LevelOfTheory,
    Selectivity,
)
from chembook3d.services import levels, repairs
from tests import gaussian_text as g
from tests.test_energies import import_file, level_key, view
from tests.test_import import calculations, upload

END = g.moved(g.WATER, 0.01)
IOPS = {"3/174": "1000000", "3/175": "358940", "3/177": "12092", "3/178": "5938951"}


def opt_freq(route_words: str = "") -> str:
    """An optimization and frequency job at B3LYP/6-31G(d) with `route_words` added."""
    return g.output(
        g.step(f"#P B3LYP/6-31G(d) Opt {route_words}", [g.WATER, END], converged=True),
        g.step(
            f"#P B3LYP/6-31G(d) Freq Geom=Check {route_words}",
            [END],
            frequencies=[1650.0, 3700.0, 3800.0],
            internal=True,
        ),
    )


def frequency_only(stationary: bool = True) -> str:
    """D200: a frequency job run from an optimization's checkpoint, the optimization not
    imported."""
    after = [] if stationary else g.convergence_table(False)
    return g.output(
        g.step(
            "#P B3LYP/6-31G(d) Freq Geom=Check",
            [END],
            frequencies=[1650.0, 3700.0, 3800.0],
            after=after,
        )
    )


# ---------- D199: dispersion IOps without a dispersion model (T-IMP-61) ----------


def test_iops_without_a_dispersion_model_name_no_dispersion(open_client):
    plan = upload(open_client, "plain.log", opt_freq(g.GD3MBJ_IOPS))
    assert plan["names"] == []  # nothing to name: Gaussian adds no dispersion
    node_id = import_file(open_client, "plain.log", opt_freq(g.GD3MBJ_IOPS))["node_id"]
    plain = import_file(open_client, "plain2.log", opt_freq())["node_id"]
    labels = {c["composite_label"] for n in (node_id, plain) for c in calculations(open_client, n)}
    assert len(labels) == 1  # one level, with the IOps or without
    with_model = upload(
        open_client, "d3.log", opt_freq(f"EmpiricalDispersion=GD3BJ {g.GD3MBJ_IOPS}")
    )
    assert [n["kind"] for n in with_model["names"]] == ["dispersion"]


def test_opening_an_older_notebook_drops_the_dispersion(open_client):
    # T-IMP-62: what an earlier version stored, a level whose dispersion is the IOps' name,
    # is moved to the level without dispersion, with every saved key that names it.
    node_id = import_file(open_client, "plain.log", opt_freq(g.GD3MBJ_IOPS))["node_id"]
    investigation = open_client.app.state.investigation
    folder = investigation.folder
    with investigation.sessions.begin() as session:
        named = CustomDispersion(name="D3 IOps", base="", iops=IOPS)
        session.add(named)
        plain = session.get(Calculation, calculations(open_client, node_id)[0]["id"]).level
        old = levels.get_or_create(session, {**levels.fields_of(plain), "dispersion": "D3 IOps"})
        for calculation in session.query(Calculation):
            calculation.level_id = old.id
        key = f"{old.id}~{old.id}"
        session.add(Selectivity(name="Old", level=key))
        info = session.get(InvestigationInfo, 1)
        info.view_state = {**(info.view_state or {}), "level": key}
        info.repairs = [r for r in info.repairs if r != "dispersion_without_model"]
        plain_id, old_id = plain.id, old.id
    assert "D3 IOps" in calculations(open_client, node_id)[0]["composite_label"]
    open_client.post("/api/investigations/close")
    opened = inv.open_investigation(folder)
    try:
        with opened.sessions() as session:
            assert {c.level_id for c in session.query(Calculation)} == {plain_id}
            assert session.get(LevelOfTheory, old_id) is None
            assert session.query(CustomDispersion).count() == 0
            assert session.query(Selectivity).one().level == f"{plain_id}~{plain_id}"
            assert session.get(InvestigationInfo, 1).view_state["level"] == f"{plain_id}~{plain_id}"
            changes = session.query(HistoryEntry).filter_by(source=repairs.SOURCE, field="level")
            assert changes.count() == 2  # the optimization and the frequency job
    finally:
        opened.close()


# ---------- D200: a frequency job without its optimization (T-IMP-63) ----------


def test_a_frequency_job_gives_the_geometry_level_on_a_node_without_optimization(open_client):
    node_id = import_file(open_client, "freq.log", frequency_only())["node_id"]
    (frequency,) = calculations(open_client, node_id)
    sp = g.single_point(END, route="#P B3LYP/def2TZVP SP", energy=-76.45)
    import_file(open_client, "sp.log", sp, node_id)
    label = calculations(open_client, node_id)[-1]["composite_label"]
    assert label.endswith(f" // {frequency['composite_label']}")
    values = view(open_client, level_key(open_client, label), "G")["values"]
    assert values[node_id]["value"] is not None  # E of the single point, G from the frequencies


def test_a_frequency_job_that_found_no_stationary_point_gives_none(open_client):
    node_id = import_file(open_client, "freq.log", frequency_only(stationary=False))["node_id"]
    sp = g.single_point(END, route="#P B3LYP/def2TZVP SP", energy=-76.45)
    import_file(open_client, "sp.log", sp, node_id)
    # Its geometry level stays unknown until set by hand (D100), as before D200.
    assert " // " not in calculations(open_client, node_id)[-1]["composite_label"]
