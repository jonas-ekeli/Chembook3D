"""The investigation's own geometry tolerance (D252, Q79, FR-INV-10, T-ID-10, T-ID-11): the RMSD
within which a calculation is at a geometry (ID-7) is stored in the investigation, so matching
does not depend on the computer; the app's setting is only what a new investigation starts
with, and an investigation saved before D252 takes it once when it is opened."""

from chembook3d import investigation as inv
from chembook3d import settings as app_settings
from chembook3d.models import HistoryEntry, InvestigationInfo
from chembook3d.services import repairs, thermo_settings
from tests import gaussian_text as g
from tests.test_energies import settings
from tests.test_import import calculations, commit, upload
from tests.test_pathway import get
from tests.test_thermo_settings import set_app_defaults, thermo_history


def _water_node(client) -> str:
    water = g.output(g.step("#P B3LYP/6-31G(d) Opt", [g.WATER], converged=True))
    return commit(client, upload(client, "w.out", water))["node_id"]


def _assignments(client, node_id: str) -> list[str]:
    nearby = g.single_point(g.moved(g.WATER, 0.05))  # 0.05 Å from the node's geometry
    plan = upload(client, "sp.out", nearby, node_id)
    return [s["assignment"] for s in plan["steps"]]


def test_the_tolerance_is_the_investigations(open_client, tmp_path):
    # T-ID-10, D252: a new investigation starts with the app's tolerance; a change with it open
    # is stored in it, in its history, and matching uses it; another computer's setting (the
    # app's, changed while it is closed) does not move it.
    assert get(open_client, "/settings")["geometry_tolerance"] == 0.001
    node_id = _water_node(open_client)
    assert _assignments(open_client, node_id) == ["derived"]

    changed = settings(open_client, geometry_tolerance=0.1)
    assert changed["geometry_tolerance"] == 0.1
    assert app_settings.load().geometry_tolerance == 0.001  # the app's default is kept
    entries = thermo_history(open_client)
    assert [(e["field"], e["old_value"], e["new_value"]) for e in entries] == [
        ("geometry_tolerance", 0.001, 0.1)
    ]
    assert _assignments(open_client, node_id) == ["node"]
    assert open_client.put("/api/settings", json={"geometry_tolerance": 0}).status_code == 422

    open_client.post("/api/investigations/close")
    assert get(open_client, "/settings")["geometry_tolerance"] == 0.001  # none open: the app's
    set_app_defaults(geometry_tolerance=0.0005)
    response = open_client.post("/api/investigations/open", json={"folder": str(tmp_path / "inv")})
    assert response.status_code == 200, response.text
    assert get(open_client, "/settings")["geometry_tolerance"] == 0.1
    assert _assignments(open_client, node_id) == ["node"]
    assert len(calculations(open_client, node_id)) == 1


def test_an_older_investigation_takes_the_app_tolerance_once(open_client, tmp_path):
    # T-ID-11, D252: an investigation saved before D252 has no tolerance of its own; opened, it
    # takes the app's current one, recorded once in its history, and keeps it after that.
    folder = tmp_path / "inv"
    with open_client.app.state.investigation.sessions.begin() as session:
        info = session.get(InvestigationInfo, 1)
        info.geometry_tolerance = None
        info.repairs = [r for r in info.repairs if r != "geometry_tolerance"]
        assert thermo_settings.tolerance(session) == 0.001  # unset: the app's
    open_client.post("/api/investigations/close")
    set_app_defaults(geometry_tolerance=0.002)

    opened = inv.open_investigation(folder)
    try:
        with opened.sessions() as session:
            info = session.get(InvestigationInfo, 1)
            assert info.geometry_tolerance == 0.002
            assert info.repairs == list(repairs.REPAIRS)
            entries = [e for e in session.query(HistoryEntry) if e.record_type == "thermochemistry"]
            assert [(e.action, e.source) for e in entries] == [("create", "repair")]
            # Only what it took: its thermochemistry was its own already.
            assert entries[0].new_value == {"geometry_tolerance": 0.002, "from": "app"}
    finally:
        opened.close()

    set_app_defaults(geometry_tolerance=0.01)
    again = inv.open_investigation(folder)
    try:
        with again.sessions() as session:
            assert session.get(InvestigationInfo, 1).geometry_tolerance == 0.002
            assert thermo_settings.tolerance(session) == 0.002
    finally:
        again.close()
