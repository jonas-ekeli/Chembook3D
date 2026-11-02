"""g-xTB on this computer (D216, FR-LOC-07, T-LOC-13..15): the optional install of the Grimme
group's pinned xtb build, its gradient file, and a refine with it.

The normal run uses a stand-in xtb program (a script that writes a Turbomole gradient file for
harmonic bonds); the real g-xTB runs only with CHEMBOOK3D_REAL_TOOLS=1 after
`python -m chembook3d.local_tools install-gxtb` (the CI job "Local tools").
"""

import hashlib
import io
import json
import sys
import tarfile
import zipfile

import numpy as np
import pytest

from chembook3d import local_jobs, local_tools, local_worker
from tests.conftest import WATER
from tests.test_local_jobs import (  # noqa: F401 - the fixtures
    REAL,
    finished,
    folder_of,
    no_worker_left,
    stub_tools,
    wait_for,
)

GRADIENT = """$grad
  cycle =      1    SCF energy =    -5.07054364337   |dE/xyz| =  0.011111
      0.00000000000000      0.00000000000000      0.22166700000000      o
      0.00000000000000      1.43090000000000     -0.88660000000000      h
      0.00000000000000     -1.43090000000000     -0.88660000000000      h
   0.0000000000000D+00   0.0000000000000D+00  -1.2345678901234D-02
   0.0000000000000D+00   3.0000000000000D-03   6.1728394506170D-03
   0.0000000000000E+00  -3.0000000000000E-03   6.1728394506170E-03
$end
"""

# A stand-in for xtb --gxtb --grad: harmonic bonds (X-H 1.8 bohr, H-H 2.9 bohr) on coord.xyz,
# written as xtb writes its gradient file; it fails on purpose for --chrg 9.
FAKE_XTB = """
import sys
import numpy as np
argv = sys.argv[1:]
if argv[argv.index("--chrg") + 1] == "9":
    print("abnormal termination of xtb")
    sys.exit(1)
lines = open("coord.xyz").read().splitlines()
n = int(lines[0])
rows = [line.split() for line in lines[2 : 2 + n]]
el = [r[0] for r in rows]
x = np.array([[float(v) for v in r[1:4]] for r in rows]) / 0.529177210903
e, g = 0.0, np.zeros_like(x)
for i in range(n):
    for j in range(i + 1, n):
        t = 2.9 if el[i] == el[j] == "H" else 1.8
        d = x[i] - x[j]
        r = np.linalg.norm(d)
        e += 0.1 * (r - t) ** 2
        g[i] += 0.2 * (r - t) * d / r
        g[j] -= 0.2 * (r - t) * d / r
out = ["$grad", f"  cycle =      1    SCF energy = {e - 5.0:20.11f}   |dE/xyz| =  0.1"]
out += [f"{a:22.14f}{b:22.14f}{c:22.14f}      {s.lower()}" for (a, b, c), s in zip(x, el)]
out += ["".join(f"{v:22.13E}".replace("E", "D") for v in row) for row in g]
out.append("$end")
open("gradient", "w").write("\\n".join(out) + "\\n")
"""


@pytest.fixture
def fake_gxtb(tmp_path, monkeypatch):
    script = tmp_path / "fake_xtb.py"
    script.write_text(FAKE_XTB, encoding="utf-8")
    if sys.platform == "win32":
        program = tmp_path / "xtb.cmd"
        program.write_text(f'@"{sys.executable}" "{script}" %*\n', encoding="utf-8")
    else:
        program = tmp_path / "xtb"
        program.write_text(f"#!{sys.executable}\nexec(open({str(script)!r}).read())\n")
        program.chmod(0o755)
    monkeypatch.setenv("CHEMBOOK3D_GXTB", str(program))
    return program


def test_the_gradient_file_is_read():
    """T-LOC-13: energy and gradient after the last cycle line, D or E exponents."""
    energy, grad = local_worker.read_xtb_gradient(GRADIENT, 3)
    assert energy == pytest.approx(-5.07054364337)
    assert grad.shape == (3, 3) and grad[0, 2] == pytest.approx(-0.012345678901234)
    assert grad[2, 1] == pytest.approx(-0.003)
    with pytest.raises(ValueError):
        local_worker.read_xtb_gradient(GRADIENT, 4)


@pytest.mark.parametrize("kind", ["zip", "tar.xz"])
def test_gxtb_installs_from_its_pinned_archive(tmp_path, monkeypatch, kind):
    """T-LOC-14: the archive is checked by SHA-256, unpacked beside the old one and swapped in;
    a wrong checksum is refused and keeps the old one."""
    monkeypatch.setenv("CHEMBOOK3D_TOOLS_DIR", str(tmp_path / "tools"))
    monkeypatch.delenv("CHEMBOOK3D_GXTB", raising=False)
    binary = "pkg/bin/xtb"
    buffer = io.BytesIO()
    if kind == "zip":
        with zipfile.ZipFile(buffer, "w") as z:
            z.writestr(binary, "program")
    else:
        with tarfile.open(fileobj=buffer, mode="w:xz") as t:
            info = tarfile.TarInfo(binary)
            info.size = 7
            t.addfile(info, io.BytesIO(b"program"))
    archive = tmp_path / f"gxtb.{kind}"
    archive.write_bytes(buffer.getvalue())
    sha = hashlib.sha256(archive.read_bytes()).hexdigest()
    monkeypatch.setattr(local_tools, "GXTB", {"test": (archive.name, sha, binary)})
    monkeypatch.setattr(local_tools, "GXTB_URL", archive.parent.as_uri() + "/{file}")
    assert local_tools.gxtb_binary() is None
    steps: list[str] = []
    marker = local_tools.install_gxtb("test", steps.append)
    assert marker["version"] == local_tools.GXTB_VERSION and steps[-1] == "Installed"
    assert local_tools.gxtb_binary() == local_tools.gxtb_dir() / binary
    assert local_tools.gxtb_status()["installed"] and local_tools.gxtb_status()["up_to_date"]
    assert "gxtb" in local_jobs.methods()

    monkeypatch.setattr(local_tools, "GXTB", {"test": (archive.name, "0" * 64, binary)})
    with pytest.raises(local_tools.ToolsError, match="SHA-256"):
        local_tools.install_gxtb("test", steps.append)
    assert local_tools.gxtb_binary() is not None  # the old one stays


@pytest.mark.usefixtures("stub_tools")
def test_gxtb_is_offered_only_once_installed(open_client, monkeypatch, tmp_path):
    monkeypatch.delenv("CHEMBOOK3D_GXTB", raising=False)
    monkeypatch.setenv("CHEMBOOK3D_TOOLS_DIR", str(tmp_path / "tools"))
    status = open_client.get("/api/local/tools").json()
    assert "gxtb" not in [m["value"] for m in status["methods"]]
    assert status["gxtb"]["installed"] is False and status["gxtb"]["install"]["state"] == "idle"


@pytest.mark.usefixtures("stub_tools")
def test_refine_with_gxtb(open_client, fake_gxtb):
    """T-LOC-15: a refine with (a stand-in for) g-xTB, imported at the level "xTB g-xTB"; a
    failing run is reported with xtb's own words."""
    status = open_client.get("/api/local/tools?atoms=3").json()
    assert "gxtb" in [m["value"] for m in status["methods"]]
    node_id = open_client.post("/api/nodes", json={"label": "w", "xyz": WATER}).json()["id"]
    job = open_client.post("/api/local/jobs", json={"node_id": node_id, "method": "gxtb"}).json()
    done = wait_for(open_client, job["id"], finished, timeout=120)
    folder = folder_of(open_client) / "local-jobs" / job["id"]
    assert done["status"] == "converged", (folder / "worker.log").read_text()
    result = json.loads((folder / "result.json").read_text())
    final = np.array(result["coordinates"]) / local_worker.BOHR_TO_ANGSTROM
    assert np.linalg.norm(final[0] - final[1]) == pytest.approx(1.8, abs=0.01)
    assert not (folder / "gxtb-run").exists()
    assert open_client.post(f"/api/local/jobs/{job['id']}/import").status_code == 200
    calcs = open_client.get(f"/api/nodes/{node_id}/calculations").json()
    assert calcs[0]["program"] == "xTB" and "g-xTB" in json.dumps(calcs[0])

    bad = open_client.post(
        "/api/local/jobs", json={"node_id": node_id, "method": "gxtb", "charge": 9}
    ).json()
    failed = wait_for(open_client, bad["id"], finished, timeout=60)
    assert failed["status"] == "failed"
    assert "abnormal termination" in failed["result"]["error"]


@pytest.mark.skipif(not REAL, reason="needs the installed local tools (CHEMBOOK3D_REAL_TOOLS=1)")
def test_real_gxtb_refine_of_water(open_client, monkeypatch):
    """T-LOC-05, D216: a real g-xTB refine (install-gxtb first)."""
    monkeypatch.delenv("CHEMBOOK3D_LOCAL_PYTHON", raising=False)
    monkeypatch.delenv("CHEMBOOK3D_GXTB", raising=False)
    if local_tools.gxtb_binary() is None:
        pytest.skip("g-xTB is not installed (python -m chembook3d.local_tools install-gxtb)")
    node_id = open_client.post("/api/nodes", json={"label": "w", "xyz": WATER}).json()["id"]
    job = open_client.post("/api/local/jobs", json={"node_id": node_id, "method": "gxtb"}).json()
    done = wait_for(open_client, job["id"], finished, timeout=600)
    assert done["status"] == "converged", done["result"]["error"]
    assert np.isfinite(done["result"]["energy"]) and done["result"]["energy"] < 0
    assert open_client.post(f"/api/local/jobs/{job['id']}/import").status_code == 200
