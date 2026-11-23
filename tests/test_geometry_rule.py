"""One rule for which geometry a calculation is at (D253, T-IMP-69…71): the import preview, the
commit, energies, warnings and import undo all use `levels.Sources`, so they agree."""

from datetime import timedelta

from chembook3d.models import Calculation, CalculationType, Node
from chembook3d.services import levels
from tests import gaussian_text as g
from tests.test_energies import import_file
from tests.test_import import calculations, upload
from tests.test_import_undo import import_entry, preview
from tests.test_level_gaps import END, frequency_only


def _shifted(rows, dz: float) -> list:
    """The rows with the first atom moved along z."""
    first = rows[0]
    return [[first[0], first[1], first[2], first[3] + dz], *[list(r) for r in rows[1:]]]


SP = g.single_point(END, route="#P B3LYP/def2TZVP SP", energy=-76.45)


def test_the_preview_names_a_frequency_jobs_geometry_level_as_the_commit_stores_it(open_client):
    # T-IMP-69, D204: on a node whose optimization ran elsewhere, the preview shows the geometry
    # level the commit stores (it showed none before D253).
    node_id = import_file(open_client, "freq.log", frequency_only())["node_id"]
    (frequency,) = calculations(open_client, node_id)
    plan = upload(open_client, "sp.log", SP, node_id)
    (step,) = plan["steps"]
    assert step["assignment"] == "node"
    assert step["geometry_level_label"] == frequency["composite_label"]
    import_file(open_client, "sp.log", SP, node_id)
    stored = calculations(open_client, node_id)[-1]
    assert stored["composite_label"].endswith(f" // {step['geometry_level_label']}")


def test_the_shared_rule(open_client):
    # T-IMP-70, D253 on records: a frequency job is a geometry source only while the node has
    # no free optimization (D204), also one added after it in the same file; a frequency job
    # whose coordinates match none is at the geometry the node's geometry came from (A11), not
    # at the latest optimization (one added beside it, D246); a single point's is unknown.
    node_id = import_file(open_client, "freq.log", frequency_only())["node_id"]
    with open_client.app.state.investigation.sessions.begin() as session:
        node = session.get(Node, node_id)
        (frequency,) = node.calculations
        tolerance = 0.001
        sources = levels.Sources(node, lambda c: c)
        assert sources.at(frequency.geometry, tolerance) is frequency
        sources.add(frequency.geometry, "an optimization in the file")
        assert sources.at(frequency.geometry, tolerance) == "an optimization in the file"

        def optimization(level_basis: str, geometry, later: int) -> Calculation:
            level = levels.get_or_create(
                session, {**levels.fields_of(frequency.level), "basis": level_basis}
            )
            found = Calculation(
                node_id=node.id,
                type=CalculationType.OPTIMIZATION,
                program="Gaussian",
                level_id=level.id,
                geometry=geometry,
                created_at=frequency.created_at + timedelta(seconds=later),
            )
            session.add(found)
            session.flush()
            session.refresh(node)
            return found

        own = optimization("6-311G(d)", frequency.geometry, 1)  # the node's geometry
        added = optimization("STO-3G", _shifted(frequency.geometry, 0.3), 2)  # beside, later
        sources = levels.Sources(node, lambda c: c)
        far = _shifted(frequency.geometry, -0.3)
        assert sources.source(CalculationType.FREQUENCY, far, node.geometry, tolerance) is own
        assert sources.source(CalculationType.SINGLE_POINT, far, node.geometry, tolerance) is None
        assert sources.source(CalculationType.SINGLE_POINT, None, node.geometry, tolerance) is own
        assert sources.at(added.geometry, tolerance) is added
        assert levels.node_geometry_optimization(node, tolerance) is own


def test_undo_is_blocked_by_what_takes_its_geometry_level_from_it(open_client):
    # T-IMP-71, D253: a single point whose geometry level is the frequency job of a node
    # without optimization (D204), and a frequency job at another level whose geometry level
    # is an optimization, block undoing the import they depend on.
    node_id = import_file(open_client, "first.log", g.single_point(END))["node_id"]
    import_file(open_client, "freq.log", frequency_only(), node_id)
    import_file(open_client, "sp.log", SP, node_id)
    blockers = preview(open_client, import_entry(open_client, "freq.log"))["blockers"]
    assert any("single point from sp.log" in b for b in blockers), blockers

    base = import_file(open_client, "base.log", g.single_point(g.WATER, energy=-76.4))["node_id"]
    opt = g.output(g.step("#P B3LYP/6-31G(d) Opt", [g.WATER], converged=True))
    import_file(open_client, "opt.log", opt, base)
    other = g.output(
        g.step("#P PBE1PBE/def2SVP Freq", [g.WATER], frequencies=[1600.0, 3700.0, 3800.0])
    )
    import_file(open_client, "pbe.log", other, base)
    blockers = preview(open_client, import_entry(open_client, "opt.log"))["blockers"]
    assert any("frequency job from pbe.log" in b for b in blockers), blockers
    # Undone first, the frequency job no longer holds the optimization's import back.
    open_client.post(f"/api/history/{import_entry(open_client, 'pbe.log')['id']}/undo")
    assert preview(open_client, import_entry(open_client, "opt.log"))["blockers"] == []
