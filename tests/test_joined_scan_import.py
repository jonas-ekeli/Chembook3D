"""A scan joined in the scan editor, imported (D137, T-IMP-37): read as a scan step at the level
of its pieces, a scan path node whose energies stay on its own chart, shown on an edge with its
program on the chip."""

import json

from chembook3d.models import Node
from chembook3d.parsers import joined
from chembook3d.services import energies, imports, levels
from chembook3d.services import scan_editor as editor
from tests.test_import import calculations, commit, upload
from tests.test_scan_editor import RELAXED, _scans
from tests.test_scan_path_on_edge import edge_of, ends
from tests.test_scan_path_trim import trim
from tests.test_step_movie import steps


def _joined(first=0, last=6) -> str:
    [scan] = _scans(RELAXED)
    path = editor.join({scan.key: scan}, [editor.Piece(scan.key, first, last)])
    return path.text


def _session(client):
    return client.app.state.investigation.sessions.begin()


def test_the_saved_file_reads_as_a_scan_at_the_level_of_its_pieces():
    parsed = joined.parse(_joined(), "dvb_joined.xyz")
    assert parsed.program == "Gaussian"
    [step] = parsed.steps
    assert step.route.text == "joined scan: Gaussian B3LYP/STO-3G"
    assert (step.route.method, step.route.basis, step.resolved_basis) == (
        "B3LYP",
        "STO-3G",
        "STO-3G",
    )
    assert step.scan == "relaxed" and step.scan_coordinates == [[3, 4, 9, 10]]
    assert len(step.geometries) == 7 and step.geometry_points == [1, 2, 3, 4, 5, 6, 7]
    # The node takes the path's top, as an xTB path's does (D112): 270°, the fourth point.
    assert step.node_geometry == 3 and step.scf_energy == step.geometry_energies[3]
    assert (step.charge, step.multiplicity) == (0, 1)


def test_a_joined_scan_imports_at_its_level_and_keeps_its_energies_to_itself(open_client):
    client = open_client
    plan = upload(client, RELAXED.name, RELAXED.read_text())
    original = commit(client, plan)["node_id"]
    [scan_calc] = calculations(client, original)

    plan = upload(client, "dvb_joined.xyz", _joined())
    assert plan["blockers"] == [], plan["blockers"]
    node_id = commit(client, plan)["node_id"]
    [calc] = calculations(client, node_id)
    assert calc["program"] == "Gaussian" and calc["type"] == "other"
    assert calc["route"].startswith("joined scan")
    assert calc["level"]["id"] == scan_calc["level"]["id"]  # the same level of theory

    # A scan path: the movie plays its seven points and it can be trimmed (D117).
    data = steps(client, calc["id"])
    assert len(data["frames"]) == 7 and data["trim"] is not None
    assert data["trim"]["top"] == 3
    trim(client, calc["id"], remove=[7])
    assert calculations(client, node_id)[0]["removed_points"] == [7]

    # Its energies never reach profiles or the energy table (the scan's own node keeps its).
    with _session(client) as session:
        found = energies.Energies(session)
        assert found.nodes[node_id].available() == {}
        assert found.nodes[original].available() != {}


def test_a_joined_scan_on_an_edge_names_its_program(open_client):
    client = open_client
    a, b, edge_id = ends(client)
    plan = upload(client, "dvb_joined.xyz", _joined())
    response = client.post(
        f"/api/imports/{plan['token']}/commit", json={"path_edge_id": edge_id, "on_edge_only": True}
    )
    assert response.status_code == 200, response.text
    node_id = response.json()["node_id"]
    (path,) = edge_of(client, edge_id)["scan_paths"]
    assert path["node_id"] == node_id and path["program"] == "Gaussian"
    assert path["on_edge"] is True and path["top"] > 0

    gone = client.post(
        f"/api/imports/{upload(client, 'again.xyz', _joined())['token']}/commit",
        json={"path_edge_id": "no-such-edge"},
    )
    assert gone.status_code == 422


def _with_basis(text: str, prints: dict[str, str]) -> str:
    """The saved file as if its pieces had a basis set defined in the file (GEN)."""
    lines = text.splitlines(keepends=True)
    fields = joined.comment_fields(lines[1].rstrip("\n"))
    level = json.loads(fields["level fields"])
    level["basis"] = "GEN:0123456789abcdef"
    old = f"level fields: {fields['level fields']}"
    new = f"level fields: {json.dumps(level, sort_keys=True)}"
    new += f" | basis fingerprints: {json.dumps(prints, sort_keys=True)}"
    lines[1] = lines[1].replace(old, new)
    return "".join(lines)


def test_a_basis_set_defined_in_the_files_is_named_by_its_fingerprints(open_client):
    client = open_client
    prints = {"C": "aaaaaaaaaaaaaaaa", "H": "bbbbbbbbbbbbbbbb"}
    text = _with_basis(_joined(), prints)
    [step] = joined.parse(text).steps
    assert step.route.basis == "GEN" and step.basis_fingerprints == prints

    # Unknown here: the import says to import one of its own files first.
    plan = upload(client, "dvb_joined.xyz", text)
    assert any("custom basis set of this joined scan" in b for b in plan["blockers"])

    with _session(client) as session:
        levels.register_basis(session, "modSTO", {"C": "c basis", "H": "h basis"}, prints)
    plan = upload(client, "dvb_joined.xyz", text)
    assert plan["blockers"] == [], plan["blockers"]
    node_id = commit(client, plan)["node_id"]
    [calc] = calculations(client, node_id)
    assert calc["level"]["basis"] == "modSTO"


def test_the_editor_saves_a_gen_basis_set_s_fingerprints():
    [scan] = _scans(RELAXED)
    scan.basis_prints = {"C": "aaaaaaaaaaaaaaaa"}
    text = editor.join({scan.key: scan}, [editor.Piece(scan.key, 0, 3)]).text
    head = joined.head(text.splitlines()[1])
    assert head.basis_prints == {"C": "aaaaaaaaaaaaaaaa"}
    [again] = editor.read_joined(text, "a.xyz", "a.xyz", "f")
    assert again.basis_prints == {"C": "aaaaaaaaaaaaaaaa"}


def test_an_imported_joined_scan_counts_as_a_scan_path(open_client):
    client = open_client
    plan = upload(client, "dvb_joined.xyz", _joined())
    node_id = commit(client, plan)["node_id"]
    with _session(client) as session:
        node = session.get(Node, node_id)
        from chembook3d.services import scan_edges, trajectory

        assert trajectory.changes_in_place(node)
        assert scan_edges.has_scan_path(session, node_id)
    assert imports.read_file(_joined(), "a.xyz").program == "Gaussian"
