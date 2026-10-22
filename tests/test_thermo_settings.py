"""The investigation's own thermochemistry settings (D159, FR-EN-14, T-EN-30…33): the G_qh
temperature and cutoff and the standard state are stored in the investigation and every change
is in its history; the app's settings are only the default a new investigation starts from, and
an investigation saved before D159 takes them once when it is opened."""

import pytest

from chembook3d import investigation as inv
from chembook3d import settings as app_settings
from chembook3d.models import HistoryEntry, InvestigationInfo
from chembook3d.services import repairs
from tests.test_energies import MINIMUM, import_file, settings
from tests.test_import import calculations, fixture
from tests.test_pathway import get
from tests.test_snapshot import data_of, export, page  # noqa: F401

THERMO = ("qh_temperature", "qh_cutoff", "standard_state")


def thermo(client) -> tuple:
    found = get(client, "/settings")
    return tuple(found[f] for f in THERMO)


def thermo_history(client) -> list[dict]:
    return [e for e in get(client, "/history") if e["record_type"] == "thermochemistry"]


def set_app_defaults(**fields) -> None:
    saved = app_settings.load()
    for key, value in fields.items():
        setattr(saved, key, value)
    app_settings.save(saved)


def g_qh(client, node_id: str) -> float:
    return calculations(client, node_id)[-1]["quasi_harmonic"]["correction"]


def test_changes_are_the_investigations_and_in_its_history(open_client):
    # T-EN-30: a change with an investigation open is stored in it, one history entry per
    # value; the app's default for new investigations stays as it was.
    found = get(open_client, "/settings")
    assert found["thermo_scope"] == "investigation"
    assert thermo(open_client) == (298.15, 100.0, "1 atm")
    assert thermo_history(open_client) == []  # a new investigation starts with no entry
    node_id = import_file(open_client, MINIMUM, fixture(MINIMUM))["node_id"]
    before = g_qh(open_client, node_id)

    changed = settings(open_client, qh_temperature=350, qh_cutoff=50, standard_state="1 M")
    assert (changed["qh_temperature"], changed["qh_cutoff"], changed["standard_state"]) == (
        350,
        50,
        "1 M",
    )
    assert changed["thermo_defaults"] == {
        "qh_temperature": 298.15,
        "qh_cutoff": 100.0,
        "standard_state": "1 atm",
    }
    saved = app_settings.load()
    assert (saved.qh_temperature, saved.qh_cutoff, saved.standard_state) == (
        298.15,
        100.0,
        "1 atm",
    )
    entries = thermo_history(open_client)
    assert [(e["field"], e["old_value"], e["new_value"]) for e in reversed(entries)] == [
        ("qh_temperature", 298.15, 350.0),
        ("qh_cutoff", 100.0, 50.0),
        ("standard_state", "1 atm", "1 M"),
    ]
    assert {e["record_id"] for e in entries} == {"investigation"}
    assert {e["action"] for e in entries} == {"update"}
    # The values use them, and the energy view names them.
    qh = calculations(open_client, node_id)[-1]["quasi_harmonic"]
    assert (qh["temperature"], qh["cutoff"]) == (350, 50)
    assert qh["correction"] != pytest.approx(before, abs=1e-6)
    options = get(open_client, "/energies/options")
    assert (options["temperature"], options["cutoff"], options["standard_state"]) == (
        350,
        50,
        "1 M",
    )
    # Setting a value it already has records nothing; a wrong value is refused.
    settings(open_client, qh_temperature=350)
    assert len(thermo_history(open_client)) == 3
    assert open_client.put("/api/settings", json={"qh_temperature": 0}).status_code == 422
    assert open_client.put("/api/settings", json={"standard_state": "1 bar"}).status_code == 422
    assert len(thermo_history(open_client)) == 3


def test_a_copy_opened_with_other_app_settings_keeps_its_values(open_client, tmp_path):
    # T-EN-31: the copy synced to another computer, whose app settings differ, shows the same
    # G_qh as the original.
    node_id = import_file(open_client, MINIMUM, fixture(MINIMUM))["node_id"]
    settings(open_client, qh_temperature=330, qh_cutoff=80)
    original = g_qh(open_client, node_id)
    open_client.post("/api/investigations/close")
    set_app_defaults(qh_temperature=273.15, qh_cutoff=10.0, standard_state="1 M")
    response = open_client.post("/api/investigations/open", json={"folder": str(tmp_path / "inv")})
    assert response.status_code == 200, response.text
    assert thermo(open_client) == (330, 80, "1 atm")
    assert g_qh(open_client, node_id) == pytest.approx(original, abs=1e-12)
    assert get(open_client, "/settings")["thermo_defaults"]["qh_temperature"] == 273.15


def test_an_older_investigation_takes_the_app_settings_once(open_client, tmp_path):
    # T-EN-32: an investigation saved before D159 has no values of its own; opened, it takes
    # the app's current ones, recorded once in its history, and keeps them after that.
    folder = tmp_path / "inv"
    investigation = open_client.app.state.investigation
    with investigation.sessions.begin() as session:
        info = session.get(InvestigationInfo, 1)
        info.qh_temperature = info.qh_cutoff = info.standard_state = None
        info.repairs = [r for r in info.repairs if r != "thermochemistry"]
    open_client.post("/api/investigations/close")
    set_app_defaults(qh_temperature=310.0, qh_cutoff=60.0, standard_state="1 M")

    opened = inv.open_investigation(folder)
    try:
        with opened.sessions() as session:
            info = session.get(InvestigationInfo, 1)
            assert (info.qh_temperature, info.qh_cutoff, info.standard_state) == (
                310.0,
                60.0,
                "1 M",
            )
            assert info.repairs == list(repairs.REPAIRS)
            entries = [e for e in session.query(HistoryEntry) if e.record_type == "thermochemistry"]
            assert [(e.action, e.source) for e in entries] == [("create", "repair")]
            assert entries[0].new_value == {
                "qh_temperature": 310.0,
                "qh_cutoff": 60.0,
                "standard_state": "1 M",
                "from": "app",
            }
    finally:
        opened.close()

    # Once: the app's settings changing later does not move it.
    set_app_defaults(qh_temperature=400.0)
    again = inv.open_investigation(folder)
    try:
        with again.sessions() as session:
            assert session.get(InvestigationInfo, 1).qh_temperature == 310.0
            entries = session.query(HistoryEntry).filter_by(record_type="thermochemistry")
            assert entries.count() == 1
    finally:
        again.close()


def test_new_investigations_start_from_the_app_defaults(client, tmp_path, page):  # noqa: F811
    # T-EN-33: with none open the settings are the defaults for new investigations; a new one
    # starts with them; "use for new ones" copies an investigation's back; the read-only copy
    # carries the investigation's.
    assert get(client, "/settings")["thermo_scope"] == "app"
    settings(client, qh_temperature=320, standard_state="1 M")
    assert app_settings.load().qh_temperature == 320
    assert client.put("/api/settings", json={"qh_cutoff": -1}).status_code == 422

    response = client.post("/api/investigations", json={"folder": str(tmp_path / "a"), "name": "A"})
    assert response.status_code == 200, response.text
    assert thermo(client) == (320, 100.0, "1 M")
    settings(client, qh_temperature=340, qh_cutoff=90)
    copy = data_of(export(client))["settings"]
    assert (copy["qh_temperature"], copy["qh_cutoff"], copy["standard_state"]) == (
        340,
        90,
        "1 M",
    )
    assert app_settings.load().qh_temperature == 320
    found = settings(client, thermo_as_default=True)
    assert found["thermo_defaults"] == {
        "qh_temperature": 340,
        "qh_cutoff": 90,
        "standard_state": "1 M",
    }
    client.post("/api/investigations/close")
    assert thermo(client) == (340, 90, "1 M")
