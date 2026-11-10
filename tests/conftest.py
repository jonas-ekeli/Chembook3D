import pytest
from fastapi.testclient import TestClient

from chembook3d.app import create_app

WATER = """3
water
O 0.000000 0.000000 0.117300
H 0.000000 0.757200 -0.469200
H 0.000000 -0.757200 -0.469200
"""


@pytest.fixture(autouse=True)
def config_dir(tmp_path, monkeypatch):
    """Keep app settings and the recent list out of the real user config folder."""
    path = tmp_path / "config"
    monkeypatch.setenv("CHEMBOOK3D_CONFIG_DIR", str(path))
    return path


@pytest.fixture
def client():
    with TestClient(create_app()) as test_client:
        yield test_client


@pytest.fixture
def open_client(client, tmp_path):
    """A client with a fresh investigation open."""
    response = client.post(
        "/api/investigations", json={"folder": str(tmp_path / "inv"), "name": "Test"}
    )
    assert response.status_code == 200, response.text
    return client


def pytest_terminal_summary(terminalreporter):
    """NFR-PERF-01: the times `tests/test_stress.py` records, printed at the end of every run
    (also under pytest-xdist, which passes the properties back with each report)."""
    timed = [
        (name, value)
        for report in terminalreporter.stats.get("passed", [])
        if report.when == "call"
        for name, value in report.user_properties
        if name.startswith("stress seconds")
    ]
    if timed:
        terminalreporter.section("stress case (500 nodes, 1,000 edges)")
        for name, value in timed:
            terminalreporter.write_line(f"{name.removeprefix('stress seconds, ')}: {value:.3f} s")
