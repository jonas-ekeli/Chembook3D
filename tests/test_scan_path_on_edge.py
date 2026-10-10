"""A scan path shown on the edge it was run on (D121, A68; T-PATH-27…31): the path node
remembers its edge, the edge lists it with the path's own top, and "on the edge only" takes
its box off the canvas. Nothing is rewired and no edge energy changes."""

import json

import pytest

from chembook3d import investigation as inv
from chembook3d.models import InvestigationInfo, Node
from tests import gaussian_text
from tests.test_cloud_jobs import (  # noqa: F401  (fixtures)
    app_client,
    fake_claude,
    folder_of,
    git,
    git_config,
    linked_client,
    remote,
)
from tests.test_import import calculations, commit, folder, upload
from tests.test_overlay import xyz_text
from tests.test_scan_path_import import ENERGIES, RESULT, cloud_returns, path_text, setup
from tests.test_scan_path_trim import scan_path, trim
from tests.test_step_movie import steps
from tests.test_xtb_scan_path import SCAN, TOP, frames_of, scan_text

KCAL = 1 / 627.5094740631


def ends(client, at=((0.0, 0.0), (600.0, 200.0))) -> tuple[str, str, str]:
    """Two dichloroethane nodes joined by an edge: their ids and the edge's."""
    rows = [
        [e, *map(float, xyz)] for e, *xyz in (line.split() for line in frames_of(scan_text())[0][1])
    ]
    ids = []
    for label, (x, y) in zip(("A", "B"), at, strict=True):
        response = client.post(
            "/api/nodes", json={"label": label, "xyz": xyz_text(rows), "pos_x": x, "pos_y": y}
        )
        assert response.status_code == 201, response.text
        ids.append(response.json()["id"])
    edge = client.post("/api/transitions", json={"source_id": ids[0], "target_id": ids[1]})
    assert edge.status_code == 201, edge.text
    return ids[0], ids[1], edge.json()["id"]


def link(client, node_id: str, edge_id: str | None, status: int = 200, **more) -> dict:
    response = client.put(f"/api/nodes/{node_id}/path-edge", json={"edge_id": edge_id, **more})
    assert response.status_code == status, response.text
    return response.json()


def edge_of(client, edge_id: str) -> dict:
    return next(t for t in client.get("/api/canvas").json()["transitions"] if t["id"] == edge_id)


def node_of(client, node_id: str) -> dict:
    return next(n for n in client.get("/api/canvas").json()["nodes"] if n["id"] == node_id)


def test_a_returned_path_comes_back_on_its_edge(linked_client, remote, fake_claude, tmp_path):  # noqa: F811
    # T-PATH-27: the D115 import links the node to the edge the job was run on and shows it on
    # the edge only (A68); the edge lists it with the top above its first point and the
    # session's gate verdict; nothing is rewired.
    client = linked_client
    made = setup(client)
    job_id = made["job"]
    result = {**RESULT, "path": {**RESULT["path"], "gate": "missed", "active_end_rmsd": 0.31}}
    cloud_returns(remote, tmp_path, job_id, {"job": job_id, **result}, path_text())
    client.get("/api/jobs", params={"detail": True, "refresh": True})
    node_id = client.post(f"/api/jobs/{job_id}/import-path").json()["node_id"]

    canvas = client.get("/api/canvas").json()
    (edge,) = canvas["transitions"]
    node = next(n for n in canvas["nodes"] if n["id"] == node_id)
    assert node["path_edge_id"] == edge["id"]
    assert node["on_edge_only"] is True and node["on_edge"] is True
    assert (edge["source_id"], edge["target_id"]) == (made["A"], made["B"])
    (path,) = edge["scan_paths"]
    assert path["node_id"] == node_id and path["label"] == "Path A to B"
    assert path["top"] == pytest.approx(ENERGIES[3] - ENERGIES[0])
    assert path["gate"] == "missed" and path["reached_end"] is True
    assert path["active_rmsd"] == 0.31 and path["end_rmsd"] == 0.04 and path["on_edge"] is True
    (calc,) = calculations(client, node_id)
    assert path["calculation_id"] == calc["id"]
    listed = client.get("/api/transitions").json()
    assert listed[0]["scan_paths"] == edge["scan_paths"]
    # The history says it was created on the edge; undoing the import removes it and its chip.
    created = next(h for h in client.get("/api/history").json() if h["record_id"] == node_id)
    assert created["action"] == "create" and created["new_value"]["path_edge_id"] == edge["id"]


def test_link_by_hand_and_the_chip_follows_the_trim(open_client):
    # T-PATH-28: a path imported from a file is linked by hand, in the history; the top is the
    # path's top above its first kept point and follows trimming (D117); unlinking puts the
    # box back.
    client = open_client
    a, b, edge = ends(client)
    node_id, calc = scan_path(client)
    assert node_of(client, node_id)["path_edge_id"] is None
    assert edge_of(client, edge)["scan_paths"] == []

    plain = client.post("/api/nodes", json={"label": "plain"}).json()["id"]
    assert "Only a node with a scan path" in link(client, plain, edge, status=422)["detail"]
    assert "Transition not found" in link(client, node_id, "nope", status=422)["detail"]
    assert "on an edge first" in link(client, node_id, None, 422, on_edge_only=True)["detail"]

    linked = link(client, node_id, edge)
    assert linked["path_edge_id"] == edge and linked["on_edge"] is False
    entry = client.get("/api/history").json()[0]
    assert (entry["record_id"], entry["field"]) == (node_id, "path_edge_id")
    assert (entry["old_value"], entry["new_value"]) == (None, edge)
    energies = [e for e, _ in frames_of(scan_text())]
    (path,) = edge_of(client, edge)["scan_paths"]
    assert path["top"] == pytest.approx(energies[TOP] - energies[0])
    assert path["gate"] is None and path["on_edge"] is False

    trimmed = trim(client, calc["id"], remove=[1, 2])
    assert edge_of(client, edge)["scan_paths"][0]["top"] == pytest.approx(
        trimmed["steps"]["trim"]["barrier"]
    )
    assert trimmed["steps"]["trim"]["barrier"] == pytest.approx(energies[TOP] - energies[2])
    assert node_of(client, node_id)["path_edge_id"] == edge  # the link survives the trim

    hidden = link(client, node_id, edge, on_edge_only=True)
    assert hidden["on_edge_only"] is True and hidden["on_edge"] is True
    assert client.get("/api/history").json()[0]["field"] == "removed_points"  # layout: no entry
    unlinked = link(client, node_id, None)
    assert unlinked["path_edge_id"] is None and unlinked["on_edge_only"] is False
    assert (unlinked["pos_x"], unlinked["pos_y"]) == (300.0, 100.0)  # halfway along the edge
    assert edge_of(client, edge)["scan_paths"] == []


def test_the_chip_never_changes_an_energy(open_client):
    # T-PATH-29: linking changes no edge's direct marker, energy view or profile (D27, EN-3).
    client = open_client
    a, b, edge = ends(client)
    node_id, _ = scan_path(client)
    output = (SCAN / "dce_scan.out").read_text(encoding="utf-8")
    for record in (a, b):
        plan = upload(client, "dce_scan.out", output, record)
        commit(client, plan, target_node_id=record)
    options = client.get("/api/energies/options").json()
    level = options["levels"][0]["key"]

    def energies() -> tuple:
        view = client.get(
            "/api/energies/view", params={"level": level, "type": "E", "reference": a}
        )
        profile = client.post(
            "/api/energies/profile",
            json={"paths": [[a, b]], "level": level, "type": "E", "reference_id": a},
        )
        assert view.status_code == 200 and profile.status_code == 200, profile.text
        transition = edge_of(client, edge)
        return view.json(), profile.json(), transition["direct"], transition["warnings"]

    before = energies()
    link(client, node_id, edge, on_edge_only=True)
    assert energies() == before


def test_deleting_the_edge_keeps_the_path_and_brings_its_box_back(open_client):
    # T-PATH-30: INV-7: the path node is kept, unlinked in the history, its box halfway along
    # where the edge was; deleting the path node removes its chip.
    client = open_client
    a, b, edge = ends(client)
    node_id, _ = scan_path(client)
    link(client, node_id, edge, on_edge_only=True)
    assert client.delete(f"/api/transitions/{edge}").status_code == 204
    node = node_of(client, node_id)
    assert node["path_edge_id"] is None and node["on_edge_only"] is False
    assert (node["pos_x"], node["pos_y"]) == (300.0, 100.0)
    fields = [
        (h["record_id"], h["field"], h["new_value"]) for h in client.get("/api/history").json()
    ]
    assert (node_id, "path_edge_id", None) in fields

    edge = client.post("/api/transitions", json={"source_id": a, "target_id": b}).json()["id"]
    link(client, node_id, edge)
    assert client.delete(f"/api/nodes/{node_id}").status_code == 200
    assert edge_of(client, edge)["scan_paths"] == []


def test_the_box_comes_back_when_the_path_is_becoming_the_ts(open_client):
    # T-PATH-31: a calculation that is not a scan path ends "on the edge only" by itself, the
    # box halfway along the edge, the chip kept; edges or a group of its own show it too.
    client = open_client
    a, b, edge = ends(client)
    node_id, _ = scan_path(client)
    client.patch(f"/api/nodes/{node_id}", json={"pos_x": 900.0, "pos_y": 900.0})
    link(client, node_id, edge, on_edge_only=True)
    other = client.post("/api/transitions", json={"source_id": a, "target_id": node_id}).json()
    assert node_of(client, node_id)["on_edge"] is False  # it has an edge of its own
    client.delete(f"/api/transitions/{other['id']}")
    assert node_of(client, node_id)["on_edge"] is True

    # A DFT single point on the path's top: on the node itself (ID-4).
    numbers = {"C": 6, "H": 1, "Cl": 17}
    lines = node_of(client, node_id)["xyz"].splitlines()[2:]
    atoms = [(e, numbers[e], *map(float, xyz)) for e, *xyz in (line.split() for line in lines)]
    plan = upload(client, "top_sp.log", gaussian_text.single_point(atoms), node_id)
    commit(client, plan, target_node_id=node_id)
    assert len(calculations(client, node_id)) == 2
    node = node_of(client, node_id)
    assert node["on_edge_only"] is False and node["on_edge"] is False
    assert (node["pos_x"], node["pos_y"]) == (300.0, 100.0)
    assert (
        node["path_edge_id"] == edge and edge_of(client, edge)["scan_paths"][0]["on_edge"] is False
    )


def test_paths_imported_before_are_linked_once(open_client):
    # D121: a repair links path nodes from cloud jobs to their job's edge when the
    # investigation opens; they stay on the canvas.
    client = open_client
    a, b, edge = ends(client)
    node_id, _ = scan_path(client)
    where = folder(client)
    base = where / "jobs" / "2026-10-01-old-path"
    base.mkdir(parents=True)
    record = {
        "id": base.name,
        "kind": "scan_path",
        "scan_path": {"start_id": a, "end_id": b, "edge_id": edge},
        "imported": {"node_id": node_id},
    }
    (base / "job.json").write_text(json.dumps(record), encoding="utf-8")
    (base / "result.json").write_text(json.dumps({"path": {"gate": "passed"}}), encoding="utf-8")
    with client.app.state.investigation.sessions.begin() as session:
        session.get(InvestigationInfo, 1).repairs = ["geometry_levels"]
    client.post("/api/investigations/close")
    opened = inv.open_investigation(where)
    try:
        with opened.sessions() as session:
            node = session.get(Node, node_id)
            assert node.path_edge_id == edge and node.on_edge_only is False
            assert "scan_path_edges" in session.get(InvestigationInfo, 1).repairs
    finally:
        opened.close()
    client.post("/api/investigations/open", json={"folder": str(where)})
    (path,) = edge_of(client, edge)["scan_paths"]
    assert path["gate"] == "passed" and path["on_edge"] is False
    assert steps(client, path["calculation_id"])["trim"] is not None
