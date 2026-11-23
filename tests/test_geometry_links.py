"""Stored geometry links (D255, Q80, T-IMP-72…75): a single point or frequency job stores the
calculation whose geometry it is at; a level override keeps the links and the levels follow;
a level set by hand against the coordinates warns W-LINK; older investigations are linked once
by the rule they were read with."""

from datetime import timedelta

from chembook3d import investigation as inv
from chembook3d.models import Calculation, CalculationType, InvestigationInfo, Node
from chembook3d.services import energies, levels, repairs
from tests import gaussian_text as g
from tests.test_energies import level_key, view
from tests.test_import import calculations, upload
from tests.test_node_geometries import DFT_END, LOW_END, dft_node, low_result, onto
from tests.test_pathway import history, patch

SP = g.single_point(DFT_END, route="#P PBE1PBE/def2TZVP SP", energy=-76.5)


def by_type(client, node_id: str, kind: str) -> list[dict]:
    return [c for c in calculations(client, node_id) if c["type"] == kind]


def test_imports_store_the_calculation_they_are_at(open_client):
    # T-IMP-72: the frequency job and a later single point are linked to the optimization.
    species = dft_node(open_client)
    (optimization,) = by_type(open_client, species, "optimization")
    (frequency,) = by_type(open_client, species, "frequency")
    assert frequency["geometry_source_id"] == optimization["id"]
    onto(open_client, upload(open_client, "sp.log", SP, species))
    (sp,) = by_type(open_client, species, "single_point")
    assert sp["geometry_source_id"] == optimization["id"]
    assert sp["composite_label"].endswith(f" // {optimization['composite_label']}")


def test_a_level_override_keeps_the_links_and_the_levels_follow(open_client):
    # T-IMP-73, Q80: correcting the optimization's level moves the single point's geometry
    # level with it (in the history) and the frequency job's; nothing is matched again.
    species = dft_node(open_client)
    onto(open_client, upload(open_client, "sp.log", SP, species))
    (optimization,) = by_type(open_client, species, "optimization")
    patch(open_client, f"/calculations/{optimization['id']}", {"basis": "6-311G(d)"})
    (moved,) = by_type(open_client, species, "optimization")
    (sp,) = by_type(open_client, species, "single_point")
    (frequency,) = by_type(open_client, species, "frequency")
    assert sp["geometry_source_id"] == optimization["id"]
    assert sp["geometry_level"]["id"] == moved["level"]["id"]
    assert frequency["geometry_level"]["id"] == moved["level"]["id"]
    entry = next(e for e in history(open_client) if e["record_id"] == sp["id"])
    assert entry["field"] == "geometry_level" and entry["new_value"] == moved["composite_label"]
    assert "W-LINK" not in {w["code"] for w in sp["warnings"]}


def test_a_level_set_by_hand_against_the_coordinates_warns(open_client):
    # T-IMP-74, W-LINK: a single point at the xTB geometry beside a DFT node, its geometry
    # level then set to the DFT one by hand: the level set counts, and a warning says the
    # coordinates are the xTB geometry.
    species = dft_node(open_client)
    onto(open_client, upload(open_client, "S_xtb.json", low_result(), species))
    xtb = next(c for c in by_type(open_client, species, "optimization") if "xTB" in c["program"])
    dft = next(c for c in by_type(open_client, species, "optimization") if c["id"] != xtb["id"])
    at_low = g.single_point(LOW_END, route="#P PBE1PBE/def2TZVP SP", energy=-76.4)
    onto(open_client, upload(open_client, "sp.log", at_low, species))
    (sp,) = by_type(open_client, species, "single_point")
    assert sp["geometry_source_id"] == xtb["id"]
    assert "W-LINK" not in {w["code"] for w in sp["warnings"]}
    patch(open_client, f"/calculations/{sp['id']}", {"geometry_level_id": dft["level"]["id"]})
    (sp,) = by_type(open_client, species, "single_point")
    assert sp["geometry_source_id"] is None
    assert sp["geometry_level"]["id"] == dft["level"]["id"]
    (warning,) = [w for w in sp["warnings"] if w["code"] == "W-LINK"]
    assert "its coordinates are the geometry of the optimization at" in warning["message"]


def test_older_investigations_are_linked_once_with_no_value_moving(open_client, tmp_path):
    # T-IMP-75: the repair links what an earlier version read by the rule; the values stay.
    species = dft_node(open_client)
    onto(open_client, upload(open_client, "S_xtb.json", low_result(), species))
    onto(open_client, upload(open_client, "sp.log", SP, species))
    keys = [c["composite_label"] for c in calculations(open_client, species)]
    label = by_type(open_client, species, "single_point")[0]["composite_label"]
    before = view(open_client, level_key(open_client, label), "E")["values"][species]["value"]
    linked = {c["id"]: c["geometry_source_id"] for c in calculations(open_client, species)}
    with open_client.app.state.investigation.sessions.begin() as session:
        for calculation in session.query(Calculation):
            calculation.geometry_source_id = None
        info = session.get(InvestigationInfo, 1)
        info.repairs = [r for r in info.repairs if r != "geometry_links"]
    open_client.post("/api/investigations/close")
    opened = inv.open_investigation(tmp_path / "inv")
    opened.close()
    response = open_client.post("/api/investigations/open", json={"folder": str(tmp_path / "inv")})
    assert response.status_code == 200, response.text
    again = calculations(open_client, species)
    assert {c["id"]: c["geometry_source_id"] for c in again} == linked
    assert [c["composite_label"] for c in again] == keys
    after = view(open_client, level_key(open_client, label), "E")["values"][species]["value"]
    assert after == before
    with open_client.app.state.investigation.sessions() as session:
        assert "geometry_links" in session.get(InvestigationInfo, 1).repairs
        assert list(repairs.REPAIRS)[-1] == "geometry_links"


def test_a_job_without_coordinates_stays_at_the_geometry_of_its_time(open_client):
    # D255 (A15): a frequency job that printed no coordinates is linked to the node's
    # optimization when it is imported, so a later optimization that becomes the node's
    # geometry (D100) does not move it; without the link the rule would.
    species = dft_node(open_client)
    with open_client.app.state.investigation.sessions.begin() as session:
        node = session.get(Node, species)
        (first,) = [c for c in node.calculations if c.type == CalculationType.OPTIMIZATION]
        (frequency,) = [c for c in node.calculations if c.type == CalculationType.FREQUENCY]
        frequency.geometry = None  # as an xTB hessian prints none
        frequency.geometry_source = first
        level = levels.get_or_create(
            session, {**levels.fields_of(first.level), "basis": "def2TZVP"}
        )
        later = Calculation(
            node_id=node.id,
            type=CalculationType.OPTIMIZATION,
            program="Gaussian",
            level_id=level.id,
            geometry=[list(r) for r in first.geometry],
            created_at=first.created_at + timedelta(seconds=5),
        )
        later.geometry[0][3] += 0.2
        session.add(later)
        node.geometry = later.geometry
        session.flush()
        session.refresh(node)
        tolerance = 0.001
        assert energies.calculation_key(frequency, level.id, tolerance).geometry_level_id == (
            first.level_id
        )
        frequency.geometry_source = None
        assert energies.calculation_key(frequency, level.id, tolerance).geometry_level_id == (
            level.id
        )
