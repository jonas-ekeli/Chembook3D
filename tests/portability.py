"""T-OPS-01 / FR-INV-03: an investigation zipped on one OS opens intact on the other.

CI runs `create` on Windows and Linux, then `check` on the other OS with that zip:

    uv run python -m tests.portability create investigation.zip
    uv run python -m tests.portability check investigation.zip
"""

import json
import platform
import sys
import tempfile
import zipfile
from pathlib import Path

from fastapi.testclient import TestClient

from chembook3d.app import create_app
from chembook3d.models import Calculation
from tests.conftest import WATER

MANIFEST = "expected.json"
FIXTURES = Path(__file__).parent / "fixtures" / "gaussian"
SINGLE_POINT = FIXTURES / "aminationTS-full-unfrz-c1_sp_tzpop.log"


def create(zip_path: Path) -> None:
    with tempfile.TemporaryDirectory() as tmp, TestClient(create_app()) as client:
        folder = Path(tmp) / f"made on {platform.system()}"
        name = f"Portable ({platform.system()})"
        created = client.post("/api/investigations", json={"folder": str(folder), "name": name})
        assert created.status_code == 200, created.text
        node = client.post(
            "/api/nodes", json={"label": "INT1 é Ru", "notes": "ünïcode notes", "xyz": WATER}
        ).json()
        investigation = client.app.state.investigation
        with investigation.sessions.begin() as session:
            session.add(Calculation(node_id=node["id"], type="single_point", program="Gaussian"))
        derived = client.put(
            f"/api/nodes/{node['id']}/geometry", json={"xyz": WATER.replace("0.117300", "0.12")}
        ).json()["node"]
        client.patch(f"/api/nodes/{node['id']}", json={"status": "done", "tags": ["A1"]})
        (folder / "files" / "note.txt").write_text("copied file placeholder", encoding="utf-8")

        # An imported Gaussian output: its copy under files/ must travel with the folder.
        plan = client.post(
            "/api/imports",
            params={"filename": SINGLE_POINT.name},
            content=SINGLE_POINT.read_bytes(),
        ).json()
        names = {"basis_names": {}, "dispersion_names": {}}
        for request in plan["names"]:
            kind = "basis_names" if request["kind"] == "basis" else "dispersion_names"
            names[kind][request["key"]] = "modQZ" if kind == "basis_names" else "GD3MBJ"
        imported = client.post(f"/api/imports/{plan['token']}/commit", json=names)
        assert imported.status_code == 200, imported.text
        imported_id = imported.json()["node_id"]

        expected = {
            "name": name,
            "nodes": client.get("/api/nodes").json(),
            "history": client.get("/api/history").json(),
            "derived": derived["id"],
            "imported": imported_id,
            "calculations": client.get(f"/api/nodes/{imported_id}/calculations").json(),
        }
        client.post("/api/investigations/close")

        assert str(folder).encode() not in (folder / "investigation.sqlite").read_bytes()
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as archive:
            for path in sorted(folder.rglob("*")):
                archive.write(path, Path("investigation") / path.relative_to(folder))
            archive.writestr(MANIFEST, json.dumps(expected))
    print(f"Created {zip_path} on {platform.system()}")


def check(zip_path: Path) -> None:
    with tempfile.TemporaryDirectory() as tmp, TestClient(create_app()) as client:
        with zipfile.ZipFile(zip_path) as archive:
            archive.extractall(tmp)
        expected = json.loads((Path(tmp) / MANIFEST).read_text(encoding="utf-8"))
        folder = Path(tmp) / "investigation"

        opened = client.post("/api/investigations/open", json={"folder": str(folder)})
        assert opened.status_code == 200, opened.text
        assert opened.json()["name"] == expected["name"]
        assert client.get("/api/nodes").json() == expected["nodes"]
        assert client.get("/api/history").json() == expected["history"]
        derived = client.get(f"/api/nodes/{expected['derived']}").json()
        assert derived["derived_from_id"] in {n["id"] for n in expected["nodes"]}
        assert (folder / "files" / "note.txt").read_text(encoding="utf-8").startswith("copied")
        calculations = client.get(f"/api/nodes/{expected['imported']}/calculations").json()
        assert calculations == expected["calculations"]
        source = calculations[0]["source_file"]
        assert source["exists"]
        copy = client.get(f"/api/source-files/{source['id']}/download")
        assert copy.content == SINGLE_POINT.read_bytes()
        client.post("/api/investigations/close")
    print(f"{expected['name']} opened intact on {platform.system()}")


if __name__ == "__main__":
    commands = {"create": create, "check": check}
    if len(sys.argv) != 3 or sys.argv[1] not in commands:
        sys.exit(__doc__)
    commands[sys.argv[1]](Path(sys.argv[2]))
