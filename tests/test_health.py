from fastapi.testclient import TestClient

from chembook3d import __version__
from chembook3d import app as app_module
from chembook3d.app import create_app


def test_health_reports_ok_and_version():
    client = TestClient(create_app())
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "version": __version__}


def test_unbuilt_interface_explains_how_to_build(monkeypatch):
    monkeypatch.setattr(app_module, "static_dir", lambda: None)
    response = TestClient(app_module.create_app()).get("/")
    assert response.status_code == 503
    assert "scripts/build_frontend.py" in response.text
