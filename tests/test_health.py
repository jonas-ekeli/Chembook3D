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


def test_interface_page_is_checked_on_every_load(monkeypatch, tmp_path):
    # After a rebuild the browser must fetch the new page, which names the new bundle; the
    # hashed bundle itself may be cached.
    (tmp_path / "assets").mkdir()
    (tmp_path / "index.html").write_text("<!doctype html><title>Chembook3D</title>")
    (tmp_path / "assets" / "index-abc123.js").write_text("console.log(1)")
    monkeypatch.setattr(app_module, "static_dir", lambda: tmp_path)
    client = TestClient(app_module.create_app())
    for path in ("/", "/index.html"):
        response = client.get(path)
        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-cache"
    assert "cache-control" not in client.get("/assets/index-abc123.js").headers
