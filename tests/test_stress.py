"""NFR-PERF-01: no time targets are set, so this reports how long the routes the canvas loads take
on the stress case (500 nodes, 1,000 edges, `tests/stress.py`). The times are recorded as test
properties and printed at the end of the run (`conftest.pytest_terminal_summary`); the bound is
generous on purpose, so a slow runner never fails it. (`record_property` would do the same, but
warns under the default junit family, which `-W error` turns into a failure.)"""

from time import perf_counter
from urllib.parse import quote

from tests.stress import make_stress

BOUND = 30.0  # seconds; a report, not a target


def test_the_canvas_of_500_nodes_loads(client, tmp_path, request):
    def record(name: str, value: float) -> None:
        request.node.user_properties.append((name, value))

    started = perf_counter()
    make_stress(tmp_path / "stress")
    record("stress seconds, build", round(perf_counter() - started, 3))

    def timed(name: str, method: str, path: str, **kwargs):
        started = perf_counter()
        response = client.request(method, path, **kwargs)
        seconds = perf_counter() - started
        assert response.status_code == 200, response.text
        record(f"stress seconds, {name}", round(seconds, 3))
        assert seconds < BOUND, f"{name} took {seconds:.1f} s"
        return response.json()

    timed("open", "POST", "/api/investigations/open", json={"folder": str(tmp_path / "stress")})
    canvas = timed("GET /api/canvas", "GET", "/api/canvas")
    assert len(canvas["nodes"]) == 500 and len(canvas["transitions"]) == 1000
    timed("GET /api/overview", "GET", "/api/overview")
    timed("GET /api/nodes (brief)", "GET", "/api/nodes?include_xyz=false&brief=true")
    options = timed("GET /api/energies/options", "GET", "/api/energies/options")
    # The single point on the optimized geometry: values on every second node only.
    composite = next(o for o in options["levels"] if "DLPNO" in o["label"])
    assert "G_qh" in composite["types"]
    level = quote(composite["key"])
    view = timed("GET /api/energies/view", "GET", f"/api/energies/view?level={level}&type=G_qh")
    known = [v for v in view["values"].values() if v["value"] is not None]
    assert len(known) == 251  # the nodes with a single point, and the free species
    reference = canvas["nodes"][0]["id"]
    timed(
        "GET /api/energies/view (reference)",
        "GET",
        f"/api/energies/view?level={level}&type=G_qh&reference={reference}",
    )
