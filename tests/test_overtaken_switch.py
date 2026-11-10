"""A create or open that finishes after a later create, open or close has started must not
take over: it was overtaken, so it closes its own investigation again and answers 409."""

from concurrent.futures import ThreadPoolExecutor
from threading import Event

from chembook3d import investigation as inv
from chembook3d.api import routes


def slow_create(monkeypatch):
    """create_investigation that waits, after building the investigation, until released."""
    started, release = Event(), Event()
    real = routes.create_investigation

    def create(folder, name):
        investigation = real(folder, name)
        started.set()
        assert release.wait(10)
        return investigation

    monkeypatch.setattr(routes, "create_investigation", create)
    return started, release, real


def test_a_create_overtaken_by_a_close_does_not_open(client, tmp_path, monkeypatch):
    started, release, _real = slow_create(monkeypatch)
    folder = tmp_path / "late"
    with ThreadPoolExecutor(1) as pool:
        late = pool.submit(
            client.post, "/api/investigations", json={"folder": str(folder), "name": "Late"}
        )
        assert started.wait(10)
        assert client.post("/api/investigations/close").status_code == 200
        release.set()
        response = late.result(10)
    assert response.status_code == 409
    assert response.json()["detail"]["superseded"] is True
    assert client.get("/api/investigation").json() is None
    # Closed again: its folder can be opened at once, with no lock left behind.
    assert inv.active_ownership(folder) is None
    assert client.post("/api/investigations/open", json={"folder": str(folder)}).status_code == 200
    assert client.get("/api/investigation").json()["name"] == "Late"


def test_a_create_overtaken_by_another_create_keeps_the_later_one(client, tmp_path, monkeypatch):
    started, release, real = slow_create(monkeypatch)
    with ThreadPoolExecutor(1) as pool:
        late = pool.submit(
            client.post,
            "/api/investigations",
            json={"folder": str(tmp_path / "first"), "name": "First"},
        )
        assert started.wait(10)
        monkeypatch.setattr(routes, "create_investigation", real)
        second = client.post(
            "/api/investigations", json={"folder": str(tmp_path / "second"), "name": "Second"}
        )
        assert second.status_code == 200
        release.set()
        assert late.result(10).status_code == 409
    assert client.get("/api/investigation").json()["name"] == "Second"


def test_an_open_overtaken_by_a_create_keeps_the_create(client, tmp_path, monkeypatch):
    client.post("/api/investigations", json={"folder": str(tmp_path / "old"), "name": "Old"})
    client.post("/api/investigations/close")
    started, release = Event(), Event()
    real = routes.open_investigation

    def open_(*args, **kwargs):
        investigation = real(*args, **kwargs)
        started.set()
        assert release.wait(10)
        return investigation

    monkeypatch.setattr(routes, "open_investigation", open_)
    with ThreadPoolExecutor(1) as pool:
        late = pool.submit(
            client.post, "/api/investigations/open", json={"folder": str(tmp_path / "old")}
        )
        assert started.wait(10)
        new = client.post(
            "/api/investigations", json={"folder": str(tmp_path / "new"), "name": "New"}
        )
        assert new.status_code == 200
        release.set()
        assert late.result(10).status_code == 409
    assert client.get("/api/investigation").json()["name"] == "New"
    assert inv.active_ownership(tmp_path / "old") is None


def test_back_to_back_switches_still_open_in_turn(client, tmp_path):
    for name in ("One", "Two", "Three"):
        response = client.post(
            "/api/investigations", json={"folder": str(tmp_path / name), "name": name}
        )
        assert response.status_code == 200
        assert client.get("/api/investigation").json()["name"] == name


def test_slow_requests_are_printed_when_asked(monkeypatch, capsys):
    from fastapi.testclient import TestClient

    from chembook3d.app import create_app

    monkeypatch.setenv("CHEMBOOK3D_SLOW_REQUESTS", "0")
    with TestClient(create_app()) as client:
        client.get("/api/health")
    assert "Chembook3D slow request: GET /api/health took" in capsys.readouterr().err


def test_long_polls_are_not_slow_requests(monkeypatch, capsys):
    from fastapi.testclient import TestClient

    from chembook3d.app import create_app

    monkeypatch.setenv("CHEMBOOK3D_SLOW_REQUESTS", "0")
    with TestClient(create_app()) as client:
        client.get("/api/live", params={"wait": 0.01})
    assert "slow request" not in capsys.readouterr().err
