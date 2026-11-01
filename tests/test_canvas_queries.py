"""The canvas, overview and node list ask the database a fixed number of queries, whatever the
number of nodes: loaded one node at a time, a canvas of 300 nodes took about 2,100 queries and
two seconds, and the app reloads it after every edit."""

from sqlalchemy import event

from chembook3d.models import Calculation, CalculationResult, Node
from chembook3d.services import levels
from tests.conftest import WATER

ATOMS = [
    ["O", 0.0, 0.0, 0.1173],
    ["H", 0.0, 0.7572, -0.4692],
    ["H", 0.0, -0.7572, -0.4692],
]


def add_nodes(client, count: int) -> None:
    """`count` nodes, each with an optimization, a frequency job and a single point, joined in a
    chain of edges carrying a species."""
    species = client.post("/api/nodes", json={"kind": "species", "label": "H2O"}).json()
    previous = None
    for n in range(count):
        node = client.post(
            "/api/nodes",
            json={
                "label": f"N{n}",
                "role": "transition_state" if n % 2 else "minimum",
                "charge": 0,
                "multiplicity": 1,
                "xyz": WATER,
            },
        ).json()
        investigation = client.app.state.investigation
        with investigation.sessions.begin() as session:
            level = levels.get_or_create(
                session, {"program": "Gaussian", "method": "B3LYP", "basis": "def2SVP"}
            )
            geometry = session.get(Node, node["id"]).geometry
            for index, kind in enumerate(("optimization", "frequency", "single_point"), 1):
                calculation = Calculation(
                    node_id=node["id"],
                    type=kind,
                    program="Gaussian",
                    level_id=level.id,
                    step_index=index,
                    step_count=3,
                    termination="normal",
                    charge=0,
                    multiplicity=1,
                    geometry=geometry,
                )
                session.add(calculation)
                session.flush()
                extra = {}
                if kind == "frequency":
                    extra = {
                        "zpe": 0.02,
                        "h_corr": 0.03,
                        "g_corr": 0.01,
                        "temperature": 298.15,
                        "frequencies": [1600.0, 3700.0, 3800.0],
                        "imaginary_count": 0,
                    }
                calculation.result = CalculationResult(energy=-76.4 - n / 1000, **extra)
        if previous is not None:
            edge = client.post(
                "/api/transitions", json={"source_id": previous, "target_id": node["id"]}
            )
            assert edge.status_code == 201, edge.text
            added = client.put(
                f"/api/transitions/{edge.json()['id']}/species",
                json={"species_id": species["id"], "direction": "joins"},
            )
            assert added.status_code == 200, added.text
        previous = node["id"]


def queries(client, path: str) -> int:
    engine = client.app.state.investigation.engine
    count = [0]

    def counted(*_args) -> None:
        count[0] += 1

    event.listen(engine, "before_cursor_execute", counted)
    try:
        response = client.get(path)
    finally:
        event.remove(engine, "before_cursor_execute", counted)
    assert response.status_code == 200, response.text
    return count[0]


def test_listing_every_node_takes_the_same_queries_for_few_or_many(open_client):
    add_nodes(open_client, 3)
    few = {path: queries(open_client, path) for path in ("/api/canvas", "/api/overview")}
    few["/api/nodes"] = queries(open_client, "/api/nodes")
    canvas = open_client.get("/api/canvas").json()
    assert all(n["calculation_count"] == 3 for n in canvas["nodes"])
    assert all(len(t["species"]) == 1 for t in canvas["transitions"])

    add_nodes(open_client, 9)
    many = {path: queries(open_client, path) for path in few}
    assert many == few
