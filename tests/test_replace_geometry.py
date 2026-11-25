"""Replace a node's geometry at one level (D260): T-IMP-78..80.

An optimization that ends elsewhere at a level the node already has goes to a derived node
(D246 f) unless the user picks Replace: the optimizations at that level go with what sits on
them (D259), and the new one takes their place, beside the node's geometry or as it."""

import json

import pytest

from tests import gaussian_text as g
from tests.test_import import calculations, copied_files, upload
from tests.test_live import ORIGIN, ask
from tests.test_local_jobs import (  # noqa: F401
    finished,
    folder_of,
    no_worker_left,
    stub_tools,
    wait_for,
)
from tests.test_node_geometries import (
    DFT,
    ELSEWHERE,
    LOW_END,
    dft_node,
    geometry_is,
    low_result,
    onto,
    xyz,
)
from tests.test_pre_optimization import DFT_END

SP_ROUTE = "#P B3LYP/def2SVP SP"
NEW_LOW_END = g.moved(g.WATER, 0.12)  # where the corrected xTB optimization ended
NEW_DFT_END = g.moved(g.WATER, 0.08)


def types(client, node_id: str) -> list[tuple[str, str]]:
    return sorted((c["type"], c["composite_label"]) for c in calculations(client, node_id))


def preview(client, plan: dict, **options) -> dict:
    response = client.post(
        f"/api/imports/{plan['token']}/preview",
        json={"target_node_id": plan["target_node_id"], **options},
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_replace_a_geometry_beside_the_node_s(open_client):
    """T-IMP-78: a second xTB optimization of a DFT free species, from another structure, is
    offered as a derived node by default; Replace removes the old xTB optimization and the
    single point at it, and the new one goes beside the DFT geometry, which the node keeps."""
    species = dft_node(open_client)
    onto(open_client, upload(open_client, "S_xtb.json", low_result(), species))
    onto(open_client, upload(open_client, "S_sp.log", g.single_point(LOW_END, SP_ROUTE), species))
    copies = len(copied_files(open_client))

    plan = upload(open_client, "S_xtb_twisted.json", low_result(ELSEWHERE, NEW_LOW_END), species)
    offer = plan["replace"]
    assert offer is not None and offer["chosen"] is False and plan["derived_offered"]
    assert offer["level_label"] == "xTB GFN2-xTB" and offer["node_geometry"] is False
    assert [(c["type"], c["level"]) for c in offer["removes"]] == [
        ("optimization", "xTB GFN2-xTB"),
        ("single_point", "Gaussian B3LYP/DEF2SVP // xTB GFN2-xTB"),
    ]
    assert sorted(offer["files"]) == ["S_sp.log", "S_xtb.json"] and offer["blockers"] == []

    chosen = preview(open_client, plan, replace=True)
    assert chosen["replace"]["chosen"] and not chosen["derived_offered"]
    assert [s["assignment"] for s in chosen["steps"]] == ["node"]
    assert "REPLACE" in [w["code"] for w in chosen["warnings"]]
    result = onto(open_client, plan, replace=True)
    assert result["node_id"] == species and result["derived_node_id"] is None

    assert geometry_is(open_client, species, DFT_END)
    assert types(open_client, species) == [
        ("frequency", DFT),
        ("optimization", DFT),
        ("optimization", "xTB GFN2-xTB"),
    ]
    (new,) = [c for c in calculations(open_client, species) if c["composite_label"] != DFT]
    assert new["source_file"]["original_name"] == "S_xtb_twisted.json"
    assert len(copied_files(open_client)) == copies + 1 - 2  # the new file in, two copies out
    entries = open_client.get("/api/history", params={"record_id": species}).json()
    assert "remove_calculations" in [e["action"] for e in entries]


def test_replace_the_node_s_own_geometry_or_be_refused(open_client):
    """T-IMP-79: on a canvas node, a DFT optimization at the node's own level from elsewhere
    replaces its geometry: the old optimization and frequency job go and the coordinates move.
    A calculation at another level still on the old geometry blocks it."""
    canvas_node = dft_node(open_client, kind="node")
    plan = upload(open_client, "N_dft2.log", g.opt_freq(ELSEWHERE, NEW_DFT_END), canvas_node)
    assert plan["replace"]["node_geometry"] is True and plan["derived_offered"]
    assert [c["type"] for c in plan["replace"]["removes"]] == ["optimization", "frequency"]
    onto(open_client, plan, replace=True)
    assert geometry_is(open_client, canvas_node, NEW_DFT_END)
    added = calculations(open_client, canvas_node)
    assert [c["source_file"]["original_name"] for c in added] == ["N_dft2.log", "N_dft2.log"]
    assert all(not c["warnings"] for c in added)

    species = dft_node(open_client)
    onto(open_client, upload(open_client, "S_xtb.json", low_result(ELSEWHERE, DFT_END), species))
    plan = upload(open_client, "S_dft2.log", g.opt_freq(ELSEWHERE, NEW_DFT_END), species)
    assert plan["replace"]["blockers"] == [
        "The optimization at xTB GFN2-xTB is still at the geometry replaced and would be left "
        "without it; remove it first (D260)"
    ]
    refused = open_client.post(
        f"/api/imports/{plan['token']}/commit", json={"target_node_id": species, "replace": True}
    )
    assert refused.status_code == 422
    assert (
        geometry_is(open_client, species, DFT_END) and len(calculations(open_client, species)) == 3
    )


@pytest.mark.usefixtures("stub_tools")
def test_a_local_job_with_replace_waits_for_the_user(open_client):
    """T-IMP-80: a local refine started with replace whose result would replace the node's
    stub geometry waits instead of going to a derived node; Claude's replace_with_local_result
    waits for the user's confirm, then replaces it. Without the offer it imports as usual."""
    open_client.put("/api/settings", json={"geometry_tolerance": 1e-5})
    species = dft_node(open_client)
    first = open_client.post(
        "/api/local/jobs", json={"node_id": species, "method": "stub", "replace": True}
    ).json()
    assert wait_for(open_client, first["id"], finished)["status"] == "converged"
    done = open_client.post(f"/api/local/jobs/{first['id']}/import").json()
    assert done["imported"]["node_id"] == species  # a new level: beside, no offer

    body = {"node_id": species, "method": "stub", "replace": True, "xyz": xyz(ELSEWHERE)}
    second = open_client.post("/api/local/jobs", json=body).json()
    assert wait_for(open_client, second["id"], finished)["status"] == "converged"
    waiting = open_client.post(f"/api/local/jobs/{second['id']}/import").json()
    assert waiting.get("imported") is None and waiting["awaiting_choice"]
    assert [c["type"] for c in waiting["replace_offer"]["removes"]] == ["optimization"]
    assert len(calculations(open_client, species)) == 3  # nothing yet

    asked = ask(open_client, "replace_with_local_result", job_id=second["id"])
    assert asked.status_code == 201, asked.text
    request = asked.json()
    assert request["summary"].startswith("Replace the stub")
    assert "removing the optimization at stub" in request["summary"]
    url = f"/api/confirmations/{request['id']}/answer"
    answer = open_client.post(url, json={"confirm": True}, headers=ORIGIN).json()
    assert answer["status"] == "done", answer
    assert answer["result"]["imported"]["replaced"] is True
    stub = [c for c in calculations(open_client, species) if c["program"] == "stub"]
    assert len(stub) == 1 and stub[0]["id"] in answer["result"]["imported"]["calculation_ids"]
    assert geometry_is(open_client, species, DFT_END)
    job = json.loads(
        (folder_of(open_client) / "local-jobs" / second["id"] / "job.json").read_text()
    )
    assert job["replace_offer"] is None
    again = ask(open_client, "replace_with_local_result", job_id=second["id"])
    assert again.status_code == 422  # waiting for nothing now

    body["xyz"] = xyz(g.moved(g.WATER, 0.3))  # from the same start it would end on the same
    third = open_client.post("/api/local/jobs", json=body).json()
    assert wait_for(open_client, third["id"], finished)["status"] == "converged"
    assert open_client.post(f"/api/local/jobs/{third['id']}/import").json()["awaiting_choice"]
    derived = open_client.post(f"/api/local/jobs/{third['id']}/import", json={"derived": True})
    assert derived.status_code == 200, derived.text
    assert derived.json()["imported"]["derived"] is True
