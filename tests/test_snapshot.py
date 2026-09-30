"""The read-only copy to share (D79, A30): FR-SHARE-*, T-SHARE-02. The page itself is built by
`npm run build`; here a small stand-in page with the same marks takes the data, and the UI
test frontend/e2e/snapshot.spec.ts opens the real one from disk (T-SHARE-01)."""

import shutil
from datetime import datetime
from pathlib import Path

import pytest

from chembook3d.api import snapshot
from scripts.make_demo import make_demo
from tests.test_gaussian_parser import FIXTURES, TS
from tests.test_import import commit, named

PAGE = f"<title>{snapshot.TITLE_MARK}</title><script>{snapshot.DATA_MARK}</script>"


@pytest.fixture
def page(tmp_path, monkeypatch) -> Path:
    path = tmp_path / "viewer" / "snapshot.html"
    path.parent.mkdir()
    path.write_text(PAGE, encoding="utf-8")
    monkeypatch.setattr(snapshot, "template_path", lambda: path)
    return path


def export(client, body: dict | None = None, status: int = 200):
    response = client.post("/api/snapshot", json=body or {})
    assert response.status_code == status, response.text
    return response


def data_of(response) -> dict:
    text = response.text.split("<script>")[1].split("</script>")[0]
    return snapshot.decode(text)


def import_ts_from(client, folder: Path) -> str:
    """Import the TS sample from a folder on this computer, so its path is the origin path."""
    folder.mkdir(parents=True)
    source = folder / TS
    shutil.copy(FIXTURES / TS, source)
    response = client.post("/api/imports/from-path", json={"path": str(source)})
    assert response.status_code == 200, response.text
    plan = response.json()
    return commit(client, plan, **named(plan), origin_device="cluster.example.org")["node_id"]


def test_copy_is_one_page_with_the_investigation(open_client, page, tmp_path):
    # FR-SHARE-01, FR-SHARE-02
    node_id = import_ts_from(open_client, tmp_path / "results")
    response = export(open_client)
    assert response.headers["content-type"].startswith("text/html")
    today = f"{datetime.now():%Y-%m-%d}"
    assert f'filename="Test read-only {today}.html"' in response.headers["content-disposition"]
    assert "<title>Test · Chembook3D (read-only)</title>" in response.text

    data = data_of(response)
    assert data["format"] == "chembook3d-snapshot" and data["investigation"] == {"name": "Test"}
    assert [n["id"] for n in data["canvas"]["nodes"]] == [node_id]
    calcs = data["calculations"][node_id]
    assert len(calcs) == len(open_client.get(f"/api/nodes/{node_id}/calculations").json())
    assert calcs[0]["source_file"] == {
        "original_name": TS,
        "size": (FIXTURES / TS).stat().st_size,
        "checksum": calcs[0]["source_file"]["checksum"],
        "imported_at": calcs[0]["source_file"]["imported_at"],
    }

    # A30: every imaginary mode and the lowest real ones carry vectors; the rest are left out.
    freq = next(c for c in calcs if c["type"] == "frequency")
    modes = data["modes"][freq["id"]]
    assert modes["frequencies"][modes["order"][0]] < 0
    assert len(modes["order"]) == 1 + snapshot.REAL_MODES
    atoms = data["canvas"]["nodes"][0]["atom_count"]
    assert all(len(modes["modes"][i]) == atoms for i in modes["order"])
    assert sum(1 for m in modes["modes"] if m) == len(modes["order"])


def test_copy_holds_no_paths_devices_files_or_history(open_client, page, tmp_path):
    # T-SHARE-02, FR-SHARE-04
    import_ts_from(open_client, tmp_path / "cluster-results" / "run-7")
    response = export(open_client)
    text = str(snapshot.decode(response.text.split("<script>")[1].split("</script>")[0]))
    for secret in (str(tmp_path), tmp_path.as_posix(), "cluster-results", "cluster.example.org"):
        assert secret not in text and secret not in response.text
    assert "files/" not in text  # the copy's path in the investigation folder
    data = data_of(response)
    assert data["overview"]["recent"] == []
    assert "history" not in data


def test_energies_for_every_level_type_and_reference(client, page, tmp_path):
    # A30: what the reader can choose is all in the copy, as the API answers it.
    make_demo(tmp_path / "demo")
    opened = client.post("/api/investigations/open", json={"folder": str(tmp_path / "demo")})
    assert opened.status_code == 200
    canvas = client.get("/api/canvas").json()
    ids = {n["label"]: n["id"] for n in canvas["nodes"]}
    data = data_of(export(client, {"reference_id": ids["T-S0"]}))
    options = client.get("/api/energies/options").json()
    assert data["energy_options"] == options
    assert set(data["views"]) == {f"{o['key']}|{t}" for o in options["levels"] for t in o["types"]}
    assert data["reference_id"] == ids["T-S0"]

    # No pathways in the drawer: one per branch that has nodes.
    branches = [b for b in canvas["branches"] if b["node_count"]]
    assert [p["branch_id"] for p in data["paths"]] == [b["id"] for b in branches]
    for path, branch in zip(data["paths"], branches, strict=True):
        assert path["ids"] == client.get(f"/api/branches/{branch['id']}/pathway").json()["path"]
    on_paths = {i for p in data["paths"] for i in p["ids"]}

    level = options["levels"][0]["key"]
    for ref in on_paths:
        params = {"level": level, "type": "G", "reference": ref}
        view = client.get("/api/energies/view", params=params)
        shared = data["views"][f"{level}|G"]
        assert shared["values"] == view.json()["values"]
        assert shared["edges"] == view.json()["edges"]
        assert shared["relative"][ref] == view.json()["relative"]
        paths = [p["ids"] for p in data["paths"]]
        body = {"paths": paths, "reference_id": ref, "level": level, "type": "G"}
        profile = data["profiles"][f"{level}|G|{ref}"]
        assert profile["profiles"] == client.post("/api/energies/profile", json=body).json()
        assert profile["table"] == client.post("/api/energies/table", json=body).json()
    assert set(data["views"][f"{level}|G"]["relative"]) == on_paths


def test_drawer_pathways_are_exported_and_checked(client, page, tmp_path):
    make_demo(tmp_path / "demo")
    client.post("/api/investigations/open", json={"folder": str(tmp_path / "demo")})
    ids = {n["label"]: n["id"] for n in client.get("/api/canvas").json()["nodes"]}
    path = [ids["T-S0"], ids["B-S1"], ids["B-S2"]]
    data = data_of(export(client, {"paths": [{"ids": path, "branch_id": None}]}))
    assert data["paths"] == [{"ids": path, "branch_id": None}]
    assert data["reference_id"] is None
    assert {k.rsplit("|", 1)[1] for k in data["profiles"]} == set(path)
    # A pathway with a gap is refused (INV-2).
    export(client, {"paths": [{"ids": [ids["T-S0"], ids["B-S3"]]}]}, status=422)


def test_viewer_not_built(open_client, monkeypatch):
    monkeypatch.setattr(snapshot, "template_path", lambda: None)
    response = export(open_client, status=409)
    assert "build_frontend.py" in response.json()["detail"]


def test_page_and_file_name():
    data = {"note": "</script><script>alert(1)</script>"}
    html = snapshot.render(PAGE, data, "A <b> & c")
    assert "<title>A &lt;b&gt; &amp; c</title>" in html
    assert "alert" not in html
    assert snapshot.decode(html.split("<script>")[1].split("</script>")[0]) == data
    with pytest.raises(Exception, match="not built correctly"):
        snapshot.render("<title></title>", data, "x")
    when = datetime(2026, 9, 30)
    name = snapshot.file_name('Ru: "CAAC"/cycle?', when)
    assert name == "Ru- -CAAC-cycle read-only 2026-09-30.html"
    assert snapshot.file_name("...", when) == "investigation read-only 2026-09-30.html"
