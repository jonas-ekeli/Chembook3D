"""Lower levels beside a node's geometry (D246, FR-CALC-08, FR-SPC-08): T-IMP-64..67, T-LOC-46.

A free species optimized with DFT should also hold a MACE or xTB geometry, with local single
points on it, so relative energies at those composite levels balance with one free species."""

import json

import pytest

from chembook3d.api import builds
from chembook3d.services import builder, levels
from tests import gaussian_text as g
from tests.test_import import calculations, commit, node, upload
from tests.test_local_jobs import (  # noqa: F401
    finished,
    folder_of,
    no_worker_left,
    stub_tools,
    wait_for,
)
from tests.test_pre_optimization import DFT_END, GUESS, labels, option_labels, xyz

DFT = "Gaussian B3LYP/6-31G(D)"
LOW_END = g.moved(g.WATER, 0.03)  # where the xTB optimization from the DFT geometry ended
ELSEWHERE = g.moved(g.WATER, 0.2)  # a structure built in the app


def low_result(start=DFT_END, end=LOW_END, level="GFN2-xTB", charge=0, multiplicity=1) -> str:
    """A local optimization's result.json (the worker's format) from `start` to `end`."""
    return json.dumps(
        {
            "format": "chembook3d-local-result",
            "program": "xTB",
            "method": "gfn2",
            "level": level,
            "charge": charge,
            "multiplicity": multiplicity,
            "state": "converged",
            "elements": [a[0] for a in start],
            "frames": [[a[2:] for a in start], [a[2:] for a in end]],
            "energies": [-5.0, -5.01],
            "energy": -5.01,
        }
    )


def onto(client, plan: dict, **options) -> dict:
    return commit(client, plan, target_node_id=plan["target_node_id"], **options)


def dft_node(client, kind: str = "species") -> str:
    made = client.post("/api/nodes", json={"label": "S", "xyz": xyz(GUESS), "kind": kind}).json()
    onto(client, upload(client, "S_dft.log", g.opt_freq(GUESS, DFT_END), made["id"]))
    return made["id"]


def rows(atoms) -> list[list]:
    return [[a[0], a[2], a[3], a[4]] for a in atoms]


def geometry_is(client, node_id: str, atoms) -> bool:
    got = [line.split() for line in node(client, node_id)["xyz"].splitlines()[2:]]
    return all(
        abs(float(x) - a) < 1e-5
        for line, atom in zip(got, atoms, strict=True)
        for x, a in zip(line[1:], atom[2:], strict=True)
    )


# ---------- T-IMP-64: a lower-ranked optimization from the node's geometry goes beside it ----


def test_a_lower_level_optimization_is_added_beside_the_node_geometry(open_client):
    species = dft_node(open_client)
    plan = upload(open_client, "S_xtb.json", low_result(), species)
    assert plan["continues_from"] is None and not plan["derived_offered"]
    assert plan["beside"] == {
        "step": 1,
        "level_label": "xTB GFN2-xTB",
        "default": True,
        "chosen": True,
        "alternative": "continue",
    }
    assert "BESIDE" in [w["code"] for w in plan["warnings"]]
    assert [s["assignment"] for s in plan["steps"]] == ["node"]
    result = onto(open_client, plan)
    assert result["node_id"] == species and result["derived_node_id"] is None

    assert geometry_is(open_client, species, DFT_END)  # the node keeps its DFT geometry
    pre = {
        c["composite_label"]: c["pre_optimization"]
        for c in calculations(open_client, species)
        if c["type"] == "optimization"
    }
    assert pre == {DFT: False, "xTB GFN2-xTB": False}  # the DFT one is no pre-optimization
    geometries = open_client.get(f"/api/nodes/{species}/geometries").json()
    assert [(x["level_label"], x["node_geometry"]) for x in geometries] == [
        (DFT, True),
        ("xTB GFN2-xTB", False),
    ]


def test_single_points_attach_at_either_geometry(open_client):
    """T-IMP-65: a single point at the DFT geometry or at the xTB one attaches to the node with
    that geometry level, rather than to a derived node."""
    species = dft_node(open_client)
    onto(open_client, upload(open_client, "S_xtb.json", low_result(), species))
    route = "#P B3LYP/def2SVP SP"
    at_dft = upload(open_client, "S_sp1.log", g.single_point(DFT_END, route), species)
    at_low = upload(open_client, "S_sp2.log", g.single_point(LOW_END, route), species)
    for plan, geometry_level in ((at_dft, DFT), (at_low, "xTB GFN2-xTB")):
        assert not plan["derived_offered"]
        assert [s["assignment"] for s in plan["steps"]] == ["node"]
        assert plan["steps"][0]["geometry_level_label"] == geometry_level
        onto(open_client, plan)
    found = option_labels(open_client)
    assert {
        f"Gaussian B3LYP/DEF2SVP // {DFT}",
        "Gaussian B3LYP/DEF2SVP // xTB GFN2-xTB",
        "xTB GFN2-xTB",
        DFT,
    } <= found


def test_the_preview_can_continue_instead_and_a_same_rank_one_continues(open_client):
    """T-IMP-66: with beside off, the lower optimization continues the node as D100 says; an
    optimization of the same rank continues by default and can be put beside instead."""
    species = dft_node(open_client)
    plan = upload(open_client, "S_xtb.json", low_result(), species)
    preview = open_client.post(
        f"/api/imports/{plan['token']}/preview", json={"target_node_id": species, "beside": False}
    )
    preview = preview.json()
    assert preview["beside"]["chosen"] is False and preview["continues_from"] is not None
    onto(open_client, plan, beside=False)
    assert geometry_is(open_client, species, LOW_END)
    pre = {
        c["composite_label"]: c["pre_optimization"]
        for c in calculations(open_client, species)
        if c["type"] == "optimization"
    }
    assert pre == {DFT: True, "xTB GFN2-xTB": False}

    other = dft_node(open_client, kind="node")
    same_rank = g.output(g.step("#P B3LYP/def2SVP Opt", [DFT_END, LOW_END], -76.0, converged=True))
    plan = upload(open_client, "B_svp.log", same_rank, other)
    assert plan["beside"]["default"] is False and plan["continues_from"] is not None
    onto(open_client, plan, beside=True)
    assert geometry_is(open_client, other, DFT_END)


def test_a_free_species_takes_a_new_level_from_elsewhere(open_client):
    """T-IMP-67: a free species takes an optimization at a level it lacks that started from a
    built structure; a canvas node, the same level again or other atoms give a derived node."""
    species = dft_node(open_client)
    plan = upload(open_client, "S_xtb.json", low_result(ELSEWHERE, LOW_END), species)
    assert plan["beside"]["alternative"] == "derived" and plan["beside"]["chosen"]
    assert not plan["derived_offered"]
    onto(open_client, plan)
    assert geometry_is(open_client, species, DFT_END)
    assert labels(open_client, species) == [
        ("optimization", DFT),
        ("frequency", DFT),
        ("optimization", "xTB GFN2-xTB"),
    ]
    again = upload(open_client, "S_xtb2.json", low_result(ELSEWHERE, ELSEWHERE), species)
    assert again["beside"] is None and again["derived_offered"]  # GFN2-xTB is there already

    canvas_node = dft_node(open_client, kind="node")
    plan = upload(open_client, "N_xtb.json", low_result(ELSEWHERE, LOW_END), canvas_node)
    assert plan["beside"] is None and plan["derived_offered"]

    ion = low_result(ELSEWHERE, g.moved(g.WATER, 0.25), level="g-xTB", charge=1)
    charged = upload(open_client, "S_ion.json", ion, species)
    assert charged["beside"] is None and charged["derived_offered"]


def test_method_ranks():
    def rank(program, method):
        return levels.method_rank({"program": program, "method": method})

    assert rank("Gaussian", "UFF") == levels.FORCE_FIELD
    assert rank("xTB", "GFN-FF") == levels.FORCE_FIELD
    assert rank("MACE", "MACE-OMOL-0") == levels.MACHINE_LEARNED
    assert rank("xTB", "g-xTB") == levels.SEMI_EMPIRICAL
    assert rank("xTB", "GFN2-xTB") == levels.SEMI_EMPIRICAL
    assert rank("Gaussian", "PM6") == levels.SEMI_EMPIRICAL
    for method in ("PBEPBE", "B3LYP", "r2SCAN-3c", "HF", "DLPNO-CCSD(T)", "ONIOM(B3LYP:UFF)"):
        assert rank("ORCA", method) == levels.ELECTRONIC


# ---------- T-LOC-46: local jobs on a free species ----------


@pytest.mark.usefixtures("stub_tools")
def test_local_refine_and_single_point_on_a_free_species(open_client):
    """A local refine of a free species with a DFT optimization is added beside its geometry,
    and a local single point can be run at either geometry (geometry_of)."""
    species = dft_node(open_client)
    job = open_client.post("/api/local/jobs", json={"node_id": species, "method": "stub"}).json()
    assert wait_for(open_client, job["id"], finished)["status"] == "converged"
    imported = open_client.post(f"/api/local/jobs/{job['id']}/import")
    assert imported.status_code == 200, imported.text
    assert imported.json()["imported"]["node_id"] == species
    assert geometry_is(open_client, species, DFT_END)

    geometries = open_client.get(f"/api/nodes/{species}/geometries").json()
    assert [x["node_geometry"] for x in geometries] == [True, False]
    low = geometries[1]["calculation_id"]
    body = {"node_ids": [species], "level": "stub", "geometry_of": {species: low}}
    (sp,) = open_client.post("/api/local/single-points", json=body).json()
    assert wait_for(open_client, sp["id"], finished)["status"] == "converged"
    imported = open_client.post(f"/api/local/jobs/{sp['id']}/import")
    assert imported.status_code == 200, imported.text
    last = calculations(open_client, species)[-1]
    assert last["type"] == "single_point"
    assert last["composite_label"].endswith(f"// {geometries[1]['level_label']}")
    assert last["geometry_source_id"] == low  # D255: the chosen one, kept to the import

    wrong = {"node_ids": [species], "level": "stub", "geometry_of": {species: "nope"}}
    refused = open_client.post("/api/local/single-points", json=wrong)
    assert refused.status_code == 422 and "no optimized geometry" in refused.json()["detail"]


def test_node_geometries_lists_each_geometry_once(open_client):
    species = dft_node(open_client)
    onto(open_client, upload(open_client, "S_xtb.json", low_result(), species))
    onto(open_client, upload(open_client, "S_xtb_again.json", low_result(), species))
    geometries = open_client.get(f"/api/nodes/{species}/geometries").json()
    assert len(geometries) == 2
    assert rows(DFT_END) and geometries[0]["node_geometry"]


def test_calculations_at_a_geometry_beside_the_node_give_no_geometry_warning(open_client):
    """T-IMP-68: an optimization added beside the node's geometry, and a single point at it,
    are on the node, so W-GEOM does not flag them (it did for every free species after D246)."""
    species = dft_node(open_client)
    onto(open_client, upload(open_client, "S_xtb.json", low_result(), species))
    route = "#P B3LYP/def2SVP SP"
    onto(open_client, upload(open_client, "S_sp.log", g.single_point(LOW_END, route), species))
    assert [w["code"] for w in node(open_client, species)["warnings"]] == []
    for calculation in calculations(open_client, species):
        assert "W-GEOM" not in [w["code"] for w in calculation["warnings"]]


# ---------- T-LOC-47/48: a local job from a structure (D258, FR-LOC-18) ----------


def refine(client, node_id: str, **body):
    return client.post("/api/local/jobs", json={"node_id": node_id, "method": "stub", **body})


def run_and_import(client, response) -> dict:
    assert response.status_code == 200, response.text
    job = response.json()
    assert wait_for(client, job["id"], finished)["status"] == "converged"
    imported = client.post(f"/api/local/jobs/{job['id']}/import")
    assert imported.status_code == 200, imported.text
    return imported.json()


@pytest.mark.usefixtures("stub_tools")
def test_a_local_job_starts_from_a_structure_onto_the_node(open_client):
    """T-LOC-47: a refine of a free species started from XYZ text runs from that structure and
    is added beside the node's geometry (a level it lacks), so no derived node is made; on a
    canvas node the same start gives a derived node (Q81)."""
    species = dft_node(open_client)
    response = refine(open_client, species, xyz=xyz(ELSEWHERE), frequencies=True)
    job = response.json()
    assert job["start"] == {"from": "structure", "label": "XYZ text"}
    started = json.loads(
        (folder_of(open_client) / "local-jobs" / job["id"] / "job.json").read_text()
    )
    assert started["coordinates"] == [list(a[2:]) for a in ELSEWHERE]
    done = run_and_import(open_client, response)
    assert done["imported"]["node_id"] == species and not done["imported"]["derived"]
    assert geometry_is(open_client, species, DFT_END)  # the node keeps its geometry
    added = [c for c in calculations(open_client, species) if c["program"] == "stub"]
    assert [c["type"] for c in added] == ["optimization", "frequency"]
    geometries = open_client.get(f"/api/nodes/{species}/geometries").json()
    assert [g_["node_geometry"] for g_ in geometries] == [True, False]

    canvas_node = dft_node(open_client, kind="node")
    done = run_and_import(open_client, refine(open_client, canvas_node, xyz=xyz(ELSEWHERE)))
    assert done["imported"]["derived"] and done["imported"]["node_id"] != canvas_node
    assert geometry_is(open_client, canvas_node, DFT_END)


@pytest.mark.usefixtures("stub_tools")
def test_a_start_structure_must_be_the_node_s_molecule(open_client):
    """T-LOC-48: a start structure with other atoms, another atom order or another charge is
    refused; one of the node's own geometries (start_geometry_of) or a build can start it."""
    species = dft_node(open_client)
    cases = {
        "has 2 atoms": {"xyz": "2\n\nO 0 0 0\nH 0 0 1\n"},
        "Atom 1 of the start structure is H": {"xyz": xyz(ELSEWHERE[1:] + ELSEWHERE[:1])},
        "keeps the charge": {"xyz": xyz(ELSEWHERE), "charge": 1},
        "not valid XYZ": {"xyz": "3\n\nO 0 0\nH 0 0 1\nH 1 0 0\n"},
        "no optimized geometry": {"start_geometry_of": "nope"},
        "Give one of": {"xyz": xyz(ELSEWHERE), "start_geometry_of": "nope"},
        "No build": {"build_id": "build-nope"},
    }
    for message, body in cases.items():
        refused = refine(open_client, species, **body)
        assert refused.status_code in (404, 422), (message, refused.text)
        assert message in refused.json()["detail"], refused.json()["detail"]
    assert not list((folder_of(open_client) / "local-jobs").glob("*"))  # nothing started

    first = run_and_import(open_client, refine(open_client, species))  # the stub level, beside
    jobs = folder_of(open_client) / "local-jobs"
    stub_end = json.loads((jobs / first["id"] / "result.json").read_text())["frames"][-1]
    geometries = open_client.get(f"/api/nodes/{species}/geometries").json()
    low = geometries[1]["calculation_id"]
    response = refine(open_client, species, start_geometry_of=low)
    assert response.json()["start"]["from"] == "geometry"
    assert response.json()["start"]["calculation_id"] == low
    coordinates = json.loads((jobs / response.json()["id"] / "job.json").read_text())
    flat = [v for row in coordinates["coordinates"] for v in row]
    assert flat == pytest.approx([v for row in stub_end for v in row], abs=1e-5)  # not the node's
    assert wait_for(open_client, response.json()["id"], finished)["status"] == "converged"

    build = builds.Build(builder.Structure(rows(ELSEWHERE)), charge=1, multiplicity=None)
    wrong_charge = open_client.app.state.builds.add(build)
    refused = refine(open_client, species, build_id=wrong_charge)
    assert refused.status_code == 422 and "build's charge is 1" in refused.json()["detail"]
    build.charge = 0
    response = refine(open_client, species, build_id=open_client.app.state.builds.add(build))
    assert response.status_code == 200, response.text
    assert response.json()["start"] == {"from": "structure", "label": "a build"}
    assert wait_for(open_client, response.json()["id"], finished)["status"] == "converged"
