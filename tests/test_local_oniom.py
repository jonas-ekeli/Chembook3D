"""ONIOM Quick DFT on this computer (D214, FR-LOC-05, T-LOC-07..09): the DFT core, its H link
atoms, the ONIOM energy and gradient, and a refine with it.

The metal complex is the public ioChem-BD Ru-CAAC transition state in tests/fixtures/iochem
(CC BY 4.0). The refine runs with the stub engines, as in test_local_jobs.py; the real
r2SCAN-3c:GFN2-xTB one only with CHEMBOOK3D_REAL_TOOLS=1.
"""

import json
from pathlib import Path

import numpy as np
import pytest

from chembook3d import local_worker
from chembook3d.services import oniom
from chembook3d.services.imports import read_file
from tests.test_local_jobs import (  # noqa: F401 - the fixtures
    REAL,
    finished,
    folder_of,
    no_worker_left,
    stub_tools,
    wait_for,
)

FIXTURE = Path(__file__).parent / "fixtures" / "iochem" / "ts2prime-2_caac1_opt-freq.cml"

ETHANE = """8
ethane
C  0.000000  0.000000  0.765000
C  0.000000  0.000000 -0.765000
H  1.018000  0.000000  1.160000
H -0.509000  0.881600  1.160000
H -0.509000 -0.881600  1.160000
H -1.018000  0.000000 -1.160000
H  0.509000 -0.881600 -1.160000
H  0.509000  0.881600 -1.160000
"""


def complex_atoms():
    step = read_file(FIXTURE.read_text(encoding="utf-8"), FIXTURE.name).steps[-1]
    atoms = step.geometries[-1]
    return [a.element for a in atoms], np.array([[a.x, a.y, a.z] for a in atoms])


def xyz_text(elements, coords, title="complex"):
    rows = [f"{e} {x:.6f} {y:.6f} {z:.6f}" for e, (x, y, z) in zip(elements, coords, strict=True)]
    return "\n".join([str(len(elements)), title, *rows]) + "\n"


def test_core_of_a_metal_complex():
    """T-LOC-07: the core holds the metal, its ligand atoms and the rings on them, and is cut
    only on single bonds between non-metal heavy atoms."""
    elements, coords = complex_atoms()
    core = oniom.build(elements, coords, oniom.suggest(elements, coords), 0, 1)
    assert not core.errors and not core.warnings
    metal = elements.index("Ru")
    near = oniom._neighbours(oniom.bonds(coords, elements))
    assert metal in core.atoms and near[metal] <= set(core.atoms)
    assert 0 < len(core.atoms) < len(elements) and core.links
    for inner, outer, g in core.links:
        assert inner in core.atoms and outer not in core.atoms
        assert "H" not in (elements[inner], elements[outer])
        assert not oniom.is_metal(elements[inner]) and not oniom.is_metal(elements[outer])
        assert oniom._saturated(outer, elements, near)  # never inside a double bond or ring
        assert g == pytest.approx((0.76 + 0.31) / (0.76 + 0.76), abs=1e-3)  # C-C to C-H
    hydrogens = [i for i in core.atoms if elements[i] == "H"]
    assert all(near[h] <= set(core.atoms) for h in hydrogens)


def test_core_from_seed_atoms_without_a_metal():
    """T-LOC-07: a metal-free molecule grows its core from chosen atoms."""
    elements = ["C", "C", "H", "H", "H", "H", "H", "H"]
    coords = np.array([[float(v) for v in row.split()[1:]] for row in ETHANE.splitlines()[2:]])
    assert oniom.suggest(elements, coords) == []
    core = oniom.build(elements, coords, [0, 2, 3, 4], 0, 1)
    assert not core.errors and core.links == [(0, 1, pytest.approx(0.7039, abs=1e-4))]
    assert core.model_size == 5  # methane


def test_a_core_that_cannot_be_used_says_why():
    elements, coords = complex_atoms()
    good = oniom.suggest(elements, coords)
    metal = elements.index("Ru")
    chlorine = next(i for i in good if elements[i] == "Cl")
    cut_metal = oniom.build(elements, coords, [i for i in good if i != chlorine], 0, 1)
    assert any("to a metal" in e for e in cut_metal.errors)
    hydrogen = next(i for i in good if elements[i] == "H")
    assert any(
        "hydrogen" in e
        for e in oniom.build(elements, coords, [i for i in good if i != hydrogen], 0, 1).errors
    )
    assert any("multiplicity" in e for e in oniom.build(elements, coords, good, 0, 2).errors)
    assert oniom.build(elements, coords, [], 0, 1).errors
    assert oniom.build(elements, coords, list(range(len(elements))), 0, 1).errors
    assert metal in good


def test_oniom_gradient_is_the_derivative_of_its_energy():
    """T-LOC-08: E = E_low(real) + E_high(model) - E_low(model), with each link atom's force
    shared between the atoms of its cut bond; checked against finite differences."""
    elements, coords = complex_atoms()
    core = oniom.build(elements, coords, oniom.suggest(elements, coords), 0, 1)
    x0 = coords / local_worker.BOHR_TO_ANGSTROM
    model = local_worker.model_coordinates(x0, core.atoms, core.links)
    calculate = local_worker.oniom(
        local_worker._stub(x0, 0.95, 0.05, 0.0),
        local_worker._stub(model, 0.98, 0.1, 0.0),
        local_worker._stub(model, 0.95, 0.05, 0.0),
        core.atoms,
        core.links,
    )
    rng = np.random.default_rng(7)
    x = x0 + rng.normal(0, 0.05, x0.shape)
    _, grad = calculate(x)
    inner, outer, _ = core.links[0]
    step = 1e-5
    for atom in (
        inner,
        outer,
        core.atoms[0],
        next(i for i in range(len(x)) if i not in core.atoms),
    ):
        for axis in range(3):
            plus, minus = x.copy(), x.copy()
            plus[atom, axis] += step
            minus[atom, axis] -= step
            numeric = (calculate(plus)[0] - calculate(minus)[0]) / (2 * step)
            assert grad[atom, axis] == pytest.approx(numeric, rel=1e-5, abs=1e-8)


@pytest.mark.usefixtures("stub_tools")
def test_oniom_refine_with_the_suggested_core(open_client):
    """T-LOC-09: the core check, then a refine with it; its level names the core's size."""
    elements, coords = complex_atoms()
    node = open_client.post("/api/nodes", json={"label": "TS", "xyz": xyz_text(elements, coords)})
    node_id = node.json()["id"]
    checked = open_client.post("/api/local/core", json={"node_id": node_id}).json()
    assert checked["suggested"] and not checked["errors"]
    assert checked["model_size"] == len(checked["core"]) + len(checked["links"])
    assert checked["estimate"]["seconds_per_step"] > 0
    given = open_client.post(
        "/api/local/core", json={"node_id": node_id, "core": checked["core"][1:]}
    ).json()
    assert given["errors"] and not given["suggested"]

    refused = open_client.post(
        "/api/local/jobs",
        json={"node_id": node_id, "method": "oniom-stub", "core": checked["core"][1:]},
    )
    assert refused.status_code == 422
    job = open_client.post(
        "/api/local/jobs", json={"node_id": node_id, "method": "oniom-stub"}
    ).json()
    done = wait_for(open_client, job["id"], finished, timeout=120)
    folder = folder_of(open_client) / "local-jobs" / job["id"]
    assert done["status"] == "converged", (folder / "worker.log").read_text()
    result = json.loads((folder / "result.json").read_text())
    assert result["oniom"]["core"] == checked["core"]
    size = len(checked["core"])
    assert result["level"] == f"ONIOM(stub:stub-low), {size}-atom core"
    assert open_client.post(f"/api/local/jobs/{job['id']}/import").status_code == 200
    calcs = open_client.get(f"/api/nodes/{node_id}/calculations").json()
    assert f"{size}-atom core" in json.dumps(calcs[0])


@pytest.mark.skipif(not REAL, reason="needs the installed local tools (CHEMBOOK3D_REAL_TOOLS=1)")
def test_real_tools_oniom_refine_of_ethane(open_client, monkeypatch):
    """T-LOC-05, D214: r2SCAN-3c on a methyl group capped to methane, GFN2-xTB on the rest."""
    monkeypatch.delenv("CHEMBOOK3D_LOCAL_PYTHON", raising=False)
    monkeypatch.setenv("CHEMBOOK3D_LOCAL_STUB", "1")
    node_id = open_client.post("/api/nodes", json={"label": "ethane", "xyz": ETHANE}).json()["id"]
    job = open_client.post(
        "/api/local/jobs", json={"node_id": node_id, "method": "oniom", "core": [0, 2, 3, 4]}
    ).json()
    done = wait_for(open_client, job["id"], finished, timeout=900)
    assert done["status"] == "converged", done["result"]["error"]
    # GFN2-xTB ethane (about -7.34) plus r2SCAN-3c minus GFN2-xTB methane (about -40.5 + 4.2)
    assert -44.0 < done["result"]["energy"] < -43.0
    assert open_client.post(f"/api/local/jobs/{job['id']}/import").status_code == 200
    calcs = open_client.get(f"/api/nodes/{node_id}/calculations").json()
    assert calcs[0]["program"] == "Psi4"
