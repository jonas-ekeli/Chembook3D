"""Removing points from a scan path by hand (D117, A63): the file stays as it is, the movie and
chart leave the points out, the top is chosen again over the points kept, and each cut says
how far the structure jumps across it."""

from chembook3d.models import Calculation, Node
from chembook3d.services import trajectory
from tests.test_import import calculations, commit, upload
from tests.test_step_movie import steps
from tests.test_xtb_scan_path import TOP, frames_of, max_difference, path_text, scan_text, stored

KCAL = 1 / 627.5094740631


def scan_path(client, text: str | None = None) -> tuple[str, dict]:
    """A scan path node made from the dichloroethane scan; its node id and calculation."""
    plan = upload(client, "path.xyz", text if text is not None else path_text())
    assert plan["blockers"] == [], plan["blockers"]
    node_id = commit(client, plan)["node_id"]
    return node_id, calculations(client, node_id)[0]


def trim(client, calc_id: str, remove=(), restore=(), status: int = 200) -> dict:
    response = client.post(
        f"/api/calculations/{calc_id}/steps/trim",
        json={"remove": list(remove), "restore": list(restore)},
    )
    assert response.status_code == status, response.text
    return response.json()


def test_a_scan_path_starts_whole_with_the_node_at_its_top(open_client):
    _, calc = scan_path(open_client)
    assert calc["removed_points"] == []
    data = steps(open_client, calc["id"])
    assert not any(f["removed"] for f in data["frames"])
    t = data["trim"]
    assert t["removed"] == [] and t["kept"] == 13 and t["cuts"] == [] and t["spikes"] == []
    assert t["top"] == TOP and t["shown"] == TOP and t["automatic"] is True
    energies = [e for e, _ in frames_of(scan_text())]
    assert abs(t["barrier"] - (energies[TOP] - energies[0])) < 1e-9
    assert t["first_removed"] is False and t["last_removed"] is False


def test_removing_the_top_moves_the_node_to_the_new_top(open_client):
    # T-PATH-13: the node showed the automatic top, so it follows it (A63).
    node_id, calc = scan_path(open_client)
    result = trim(open_client, calc["id"], remove=[TOP + 1])
    data = result["steps"]
    assert [f["point"] for f in data["frames"] if f["removed"]] == [TOP + 1]
    assert len(data["frames"]) == 13  # the file is read whole; nothing is deleted
    t = data["trim"]
    assert t["removed"] == [TOP + 1] and t["kept"] == 12
    # Point 6 is now the highest point between lower kept neighbours.
    assert t["top"] == TOP + 1 and result["moved"] == TOP + 1
    assert t["shown"] == TOP + 1 and t["automatic"] is True
    ((cut),) = t["cuts"]
    assert (cut["before"], cut["after"], cut["removed"]) == (TOP - 1, TOP + 1, 1)
    assert 0.2 < cut["jump"] < 0.5 and cut["large"] is False
    after = stored(open_client, Node, node_id)
    assert max_difference(after, data["frames"][TOP + 1]["geometry"]) < 1e-5
    (calc,) = calculations(open_client, node_id)
    assert calc["removed_points"] == [TOP + 1]
    assert calc["result"]["energy"] == frames_of(scan_text())[TOP + 1][0]
    # One history entry for the removal, then the node's and the scan's new point.
    history = open_client.get("/api/history").json()
    fields = [(h["record_type"], h["field"]) for h in history if h["action"] == "update"]
    assert ("calculation", "removed_points") in fields
    assert {("node", "geometry"), ("calculation", "geometry"), ("calculation", "energy")} <= set(
        fields
    )
    entry = next(h for h in history if h["field"] == "removed_points")
    assert (entry["old_value"], entry["new_value"]) == ([], [TOP + 1])


def test_a_removed_section_leaves_a_cut_with_a_large_jump(open_client):
    _, calc = scan_path(open_client)
    preview = open_client.post(
        f"/api/calculations/{calc['id']}/steps/trim-preview", json={"remove": [3, 4, 5, 6, 7]}
    )
    assert preview.status_code == 200, preview.text
    ((cut),) = preview.json()["cuts"]
    assert (cut["before"], cut["after"], cut["removed"]) == (1, 7, 5)
    assert cut["jump"] > 0.5 and cut["large"] is True
    # The preview changed nothing.
    assert steps(open_client, calc["id"])["trim"]["removed"] == []
    assert calculations(open_client, calc["node_id"])[0]["removed_points"] == []
    data = trim(open_client, calc["id"], remove=[3, 4, 5, 6, 7])["steps"]
    assert abs(data["trim"]["cuts"][0]["jump"] - cut["jump"]) < 1e-9


def test_a_structure_picked_by_hand_stays(open_client):
    node_id, calc = scan_path(open_client)
    assert open_client.post(f"/api/calculations/{calc['id']}/steps/8/use").status_code == 200
    picked = stored(open_client, Node, node_id)
    t = steps(open_client, calc["id"])["trim"]
    assert t["shown"] == 8 and t["automatic"] is False
    result = trim(open_client, calc["id"], remove=[TOP + 1])
    assert result["moved"] is None and stored(open_client, Node, node_id) == picked
    # Removing the picked point itself keeps it too; the panel offers the new top instead.
    t = trim(open_client, calc["id"], remove=[9])["steps"]["trim"]
    assert t["shown"] == 8 and t["automatic"] is False
    assert stored(open_client, Node, node_id) == picked


def test_restore_puts_points_back_and_the_top_follows(open_client):
    node_id, calc = scan_path(open_client)
    trim(open_client, calc["id"], remove=[4, 5, 6])
    result = trim(open_client, calc["id"], restore=[4, 5, 6])
    t = result["steps"]["trim"]
    assert t["removed"] == [] and t["cuts"] == [] and t["top"] == TOP
    assert result["moved"] == TOP and t["automatic"] is True
    (calc,) = calculations(open_client, node_id)
    assert calc["removed_points"] == []
    assert calc["result"]["energy"] == frames_of(scan_text())[TOP][0]
    # Nothing to change: no history entry.
    count = len(open_client.get("/api/history").json())
    assert trim(open_client, calc["id"], restore=[4])["moved"] is None
    assert len(open_client.get("/api/history").json()) == count


def test_cutting_an_end_says_so(open_client):
    _, calc = scan_path(open_client)
    t = trim(open_client, calc["id"], remove=[1, 2])["steps"]["trim"]
    assert t["first_removed"] is True and t["last_removed"] is False
    # A cut at an end leaves no gap between kept points.
    assert t["cuts"] == []
    energies = [e for e, _ in frames_of(scan_text())]
    assert abs(t["barrier"] - (energies[t["top"]] - energies[2])) < 1e-9
    t = trim(open_client, calc["id"], remove=[13])["steps"]["trim"]
    assert t["last_removed"] is True


def test_a_spike_is_suggested_and_never_removed_by_itself(open_client):
    # Point 7 replaced by point 12's structure 15 kcal/mol higher: a jump in and out of it.
    frames = frames_of(scan_text())
    energies = [e for e, _ in frames]
    text = path_text(energies)
    blocks = text.split("\n")
    size = len(frames[0][1]) + 2
    spike = blocks[11 * size : 12 * size]
    spike[1] = spike[1].replace(
        f"{energies[11]:.12f}", f"{max(energies[5], energies[7]) + 15 * KCAL:.12f}"
    )
    blocks[6 * size : 7 * size] = spike
    node_id, calc = scan_path(open_client, "\n".join(blocks))
    t = steps(open_client, calc["id"])["trim"]
    assert t["spikes"] == [6] and t["removed"] == []
    # The spike is the top for now; removing it brings the node back to the real top.
    assert t["top"] == 6
    t = trim(open_client, calc["id"], remove=[7])["steps"]["trim"]
    assert t["spikes"] == [] and t["top"] == TOP
    assert t["cuts"][0]["large"] is False


def test_wrong_requests_are_refused(open_client):
    node_id, calc = scan_path(open_client)
    url = f"/api/calculations/{calc['id']}/steps/trim"
    assert open_client.post(url, json={"remove": [14]}).status_code == 422
    assert open_client.post(url, json={"remove": list(range(2, 14))}).status_code == 422
    assert open_client.post("/api/calculations/nope/steps/trim", json={}).status_code == 404
    # Only a scan path's points can be removed.
    with open_client.app.state.investigation.sessions() as session:
        record = session.get(Calculation, calc["id"])
        record.route = "opt"
        session.commit()
    assert open_client.post(url, json={"remove": [3]}).status_code == 422
    assert steps(open_client, calc["id"])["trim"] is None


def test_the_trim_never_touches_the_copied_file(open_client):
    _, calc = scan_path(open_client)
    with open_client.app.state.investigation.sessions() as session:
        record = session.get(Calculation, calc["id"])
        folder = open_client.app.state.investigation.folder
        path = trajectory.file_service.absolute_path(folder, record.source_file)
    before = path.read_bytes()
    trim(open_client, calc["id"], remove=[3, 4])
    assert path.read_bytes() == before
