"""Geometry measurements across nodes and groups (D151, A96; T-MCP-21…23): one row per node with
where its geometry came from, a summary per column, numbering by a reference node or SMARTS,
and the level, convergence and value filters. Public structures only."""

from types import SimpleNamespace

import numpy as np
import pytest

from chembook3d import xyz
from chembook3d.services import measurements
from tests.test_energies import DZ, import_file
from tests.test_gaussian_parser import MINIMUM
from tests.test_import import fixture
from tests.test_pathway import get, node, post


def measure(client, **body) -> dict:
    response = client.post("/api/measurements", json=body)
    assert response.status_code == 200, response.text
    return response.json()


def geometry(client, node_id: str) -> list[list]:
    text = get(client, f"/nodes/{node_id}")["xyz"]
    return [[a.element, a.x, a.y, a.z] for a in xyz.parse_xyz(text)]


def as_xyz(rows: list[list]) -> str:
    return xyz.format_xyz([xyz.Atom(e, x, y, z) for e, x, y, z in rows])


def distance(rows: list[list], a: int, b: int) -> float:
    return float(np.linalg.norm(np.subtract(rows[a - 1][1:], rows[b - 1][1:])))


@pytest.fixture
def three(open_client):
    """The optimized public aziridinium minimum; the same structure renumbered (atoms in
    reverse order) and stretched by hand; and a copy kept as entered."""
    imported = import_file(open_client, MINIMUM, fixture(MINIMUM))["node_id"]
    rows = geometry(open_client, imported)
    stretched = [list(r) for r in rows]
    stretched[0][1] += 0.05  # atom 1 moved, so its distances differ a little
    reversed_ = node(open_client, label="reversed", xyz=as_xyz(stretched[::-1]))["id"]
    copy = node(open_client, label="copy", xyz=as_xyz(rows))["id"]
    return imported, reversed_, copy, rows, stretched


def test_rows_sources_and_summary(open_client, three):
    # T-MCP-21: one row per node in the order given, each naming its geometry's optimization
    # (level and convergence) or none, with n, mean, SD, min and max per column.
    imported, reversed_, copy, rows, _ = three
    table = measure(
        open_client,
        node_ids=[imported, copy],
        measurements=[{"atoms": [1, 2]}, {"atoms": [1, 2, 3]}, {"atoms": [1, 2, 3, 4]}],
    )
    assert [c["name"] for c in table["columns"]] == ["d(1–2)", "∠(1–2–3)", "τ(1–2–3–4)"]
    assert [c["unit"] for c in table["columns"]] == ["Å", "°", "°"]
    first, second = table["rows"]
    assert first["node_id"] == imported and second["label"] == "copy"
    assert first["geometry"]["level"] == DZ and first["geometry"]["converged"] is True
    assert first["geometry"]["program"] == "Gaussian"
    assert second["geometry"] is None  # entered by hand
    assert first["values"] == second["values"]
    assert first["values"][0] == round(distance(rows, 1, 2), 3)
    d = table["summary"][0]
    assert d["n"] == 2 and d["sd"] == 0 and d["mean"] == d["min"] == d["max"]
    assert d["min_node"] in ("copy", first["label"])
    assert table["notes"] == ["the geometries come from 2 levels; `by_level` summarises each apart"]
    assert set(table["by_level"]) == {DZ, "no optimization"}


def test_numbering_by_a_reference_node(open_client, three):
    # T-MCP-22: the renumbered node is matched to the reference, so the same atoms are measured;
    # without the reference its own numbering is used.
    imported, reversed_, _, rows, stretched = three
    count = len(rows)
    own = measure(open_client, node_ids=[reversed_], measurements=[{"atoms": [1, 2]}])
    assert own["rows"][0]["values"][0] == round(distance(stretched[::-1], 1, 2), 3)
    matched = measure(
        open_client,
        node_ids=[imported, reversed_],
        reference_node_id=imported,
        measurements=[{"atoms": [1, 2]}, {"atoms": [3, 4]}],
    )
    ref, other = matched["rows"]
    assert other["values"][0] == round(distance(stretched, 1, 2), 3)  # its atoms count and 1
    assert other["values"][0] != ref["values"][0]  # atom 1 was moved
    assert other["values"][1] == ref["values"][1]  # atoms 3 and 4 were not
    assert count == len(geometry(open_client, reversed_))

    water = node(open_client, label="water", xyz="3\n\nO 0 0 0\nH 0.96 0 0\nH -0.24 0.93 0\n")["id"]
    other_atoms = measure(
        open_client,
        node_ids=[imported, water],
        reference_node_id=imported,
        measurements=[{"atoms": [1, 2]}],
    )
    assert [r["node_id"] for r in other_atoms["rows"]] == [imported]
    (skipped,) = other_atoms["skipped"]
    assert skipped["label"] == "water" and "cannot be matched" in skipped["reason"]


def test_smarts_vectors_filters_and_groups(open_client, three):
    # T-MCP-23: atoms found by SMARTS, an angle between two vectors (as lines too), the
    # level and convergence filters, rows kept by value, and a group's members measured.
    imported, reversed_, copy, rows, _ = three
    elements = [r[0] for r in rows]
    phosphorus = elements.index("P") + 1
    found = measure(
        open_client,
        node_ids=[imported, reversed_],
        measurements=[{"smarts": "[#15]~[#6]", "atoms": [1, 2], "name": "P–C"}],
    )
    for row in found["rows"]:  # several P–C bonds: no value, a note asking for one place
        assert row["values"] == [None] and "matches" in row["notes"][0]
    one = measure(
        open_client,
        node_ids=[imported, reversed_],
        measurements=[{"smarts": "[#15]", "atoms": [1, 1]}],
    )
    assert one["rows"][0]["values"] == [0.0] and one["rows"][1]["values"] == [0.0]

    vectors = measure(
        open_client,
        node_ids=[imported],
        measurements=[
            {"vectors": [[1, 2], [2, 1]]},
            {"vectors": [[1, 2], [2, 1]], "lines": True},
            {"vectors": [[1, [2, 3]], [phosphorus, 1]]},
        ],
    )
    assert vectors["rows"][0]["values"][:2] == [180.0, 0.0]
    assert vectors["columns"][2]["name"] == f"∠(1→mid(2,3), {phosphorus}→1)"

    at_level = measure(
        open_client,
        node_ids=[imported, copy],
        level=DZ,
        converged_only=True,
        measurements=[{"atoms": [1, 2]}],
    )
    assert [r["node_id"] for r in at_level["rows"]] == [imported]
    assert at_level["skipped"][0]["reason"].startswith("geometry from no optimization")
    unconverged = measure(
        open_client, node_ids=[copy], converged_only=True, measurements=[{"atoms": [1, 2]}]
    )
    assert unconverged["rows"] == [] and unconverged["skipped"][0]["reason"] == "no optimization"
    assert unconverged["summary"][0]["n"] == 0

    value = at_level["rows"][0]["values"][0]
    kept = measure(
        open_client,
        node_ids=[imported, reversed_],
        measurements=[{"atoms": [1, 2], "name": "d"}],
        keep=[{"measurement": "d", "below": value + 0.001}],
    )
    assert [r["node_id"] for r in kept["rows"]] == [imported]
    assert "d below" in kept["skipped"][0]["reason"]

    group = post(open_client, "/groups/reconnect", {"member_ids": [imported, copy], "label": "G"})
    members = measure(open_client, group_ids=[group["id"]], measurements=[{"atoms": [1, 2]}])
    assert {r["node_id"] for r in members["rows"]} == {imported, copy}
    assert {r["group"] for r in members["rows"]} == {"G"}


def test_requests_it_refuses(open_client, three):
    imported = three[0]
    for body, message in [
        ({"node_ids": [imported], "measurements": [{"atoms": [1]}]}, "2 (distance)"),
        ({"node_ids": [], "measurements": [{"atoms": [1, 2]}]}, "give the nodes"),
        ({"node_ids": [imported], "measurements": [{"atoms": [1, 2], "smarts": "C(("}]}, "SMARTS"),
        ({"node_ids": [imported], "measurements": [{"atoms": [0, 2]}]}, "from 1"),
        ({"node_ids": [imported], "measurements": [{"atoms": [1, 2], "smarts": "[#15]"}]},
         "no atom 2"),
    ]:  # fmt: skip
        response = open_client.post("/api/measurements", json=body)
        assert response.status_code == 422, body
        assert message in response.text, response.text
    missing = open_client.post(
        "/api/measurements", json={"node_ids": [imported], "measurements": [{"atoms": [1, 999]}]}
    ).json()
    assert missing["rows"][0]["values"] == [None]
    assert missing["rows"][0]["notes"] == ["d(1–999): there is no atom 999"]


def test_dihedrals_are_summarised_round_their_mean():
    # T-MCP-23: 179° and -179° are 2° apart, so their mean is 180°, not 0°.
    m = measurements.Measurement("τ", "dihedral", [[0], [1], [2], [3]])
    rows = [
        measurements.Row(SimpleNamespace(id=str(n)), None, None, [v])  # type: ignore[arg-type]
        for n, v in enumerate([179.0, -179.0, 177.0])
    ]
    (s,) = measurements._summaries([m], rows)
    assert (s.n, s.mean, s.min, s.max) == (3, 179.0, 177.0, 181.0)
    assert s.sd == pytest.approx(2.0)
    assert (s.min_node_id, s.max_node_id) == ("2", "1")
