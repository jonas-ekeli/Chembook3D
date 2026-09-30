"""Pathway structure through the API: FR-STEP-*, FR-BR-*, FR-EDGE-*, FR-GRP-01/02/04/05,
FR-CAN-06, FR-3D-03/04. Test IDs from docs/spec/07 are named in each test. The fixture builds
the Ru-CAAC example of docs/spec/02 §6."""

from dataclasses import dataclass, field

import pytest

from tests.conftest import WATER
from tests.test_gaussian_parser import TS
from tests.test_import import calculations, commit, fixture, import_ts, named, upload

STEP_NAMES = [
    "14e alkylidene",
    "olefin π-complex",
    "[2+2] TS",
    "metallacyclobutane",
    "retro-[2+2] TS",
    "product π-complex",
    "regenerated alkylidene",
]


def post(client, path: str, body: dict | None = None, status: int = 201) -> dict:
    response = client.post(f"/api{path}", json=body or {})
    assert response.status_code == status, response.text
    return response.json() if response.content else {}


def patch(client, path: str, body: dict, status: int = 200) -> dict:
    response = client.patch(f"/api{path}", json=body)
    assert response.status_code == status, response.text
    return response.json()


def get(client, path: str):
    response = client.get(f"/api{path}")
    assert response.status_code == 200, response.text
    return response.json()


def history(client, record_id: str | None = None) -> list[dict]:
    return get(client, f"/history?record_id={record_id}" if record_id else "/history")


def node(client, **fields) -> dict:
    return post(client, "/nodes", fields)


def edge(client, source: str, target: str, **fields) -> dict:
    return post(client, "/transitions", {"source_id": source, "target_id": target, **fields})


@dataclass
class Example:
    """02 §6: trunk T split into A and B at initiation; A split into A1/A2 and B into B1/B2 at
    olefin coordination. Nodes are named like 'A1-S3'; TS nodes at S2 and S4."""

    steps: list[str]
    branches: dict[str, str] = field(default_factory=dict)
    nodes: dict[str, str] = field(default_factory=dict)


@pytest.fixture
def example(open_client) -> Example:
    client = open_client
    steps = [post(client, "/steps", {"name": name})["id"] for name in STEP_NAMES]
    ex = Example(steps)

    ex.branches["T"] = post(client, "/branches", {"name": "T"})["id"]
    ex.nodes["T-pre"] = node(client, label="T-pre", branch_id=ex.branches["T"])["id"]
    for name in post(
        client, f"/nodes/{ex.nodes['T-pre']}/split", {"branches": [{"name": "A"}, {"name": "B"}]}
    ):
        ex.branches[name["name"]] = name["id"]

    for parent in ("A", "B"):
        s0 = node(
            client,
            label=f"{parent}-S0",
            role="minimum",
            step_id=steps[0],
            branch_id=ex.branches[parent],
        )
        ex.nodes[f"{parent}-S0"] = s0["id"]
        edge(client, ex.nodes["T-pre"], s0["id"])
        children = post(
            client,
            f"/nodes/{s0['id']}/split",
            {"branches": [{"name": f"{parent}1"}, {"name": f"{parent}2"}]},
        )
        for child in children:
            ex.branches[child["name"]] = child["id"]
            previous = s0["id"]
            for index in range(1, 6):
                role = "transition_state" if index in (2, 4) else "minimum"
                label = f"{child['name']}-S{index}"
                created = node(
                    client,
                    label=label,
                    role=role,
                    step_id=steps[index],
                    branch_id=child["id"],
                )
                ex.nodes[label] = created["id"]
                edge(client, previous, created["id"])
                previous = created["id"]
            s6 = node(client, label=f"{child['name']}-S6", role="minimum", step_id=steps[6])
            ex.nodes[f"{child['name']}-S6"] = s6["id"]
            patch(client, f"/nodes/{s6['id']}", {"branch_id": child["id"]})
            edge(client, previous, s6["id"])
    return ex


def branch(client, branch_id: str) -> dict:
    return get(client, f"/branches/{branch_id}")


def names(ex: Example, ids: list[str]) -> list[str]:
    by_id = {v: k for k, v in ex.branches.items()}
    return [by_id[i] for i in ids]


# ---------- reaction steps (FR-STEP-01…03) ----------


def test_steps_are_created_renamed_reordered_and_deleted(open_client):
    first = post(open_client, "/steps", {"name": "S0", "notes": "resting state"})
    second = post(open_client, "/steps", {"name": "S1"})
    assert (first["position"], second["position"]) == (1, 2)
    assert first["notes"] == "resting state"  # FR-STEP-03

    patch(open_client, f"/steps/{second['id']}", {"name": "π-complex"})
    response = open_client.put("/api/steps/order", json={"ids": [second["id"], first["id"]]})
    assert [s["name"] for s in response.json()] == ["π-complex", "S0"]
    bad = open_client.put("/api/steps/order", json={"ids": [first["id"]]})
    assert bad.status_code == 422

    # Deleting a step unassigns its nodes, and the node's history says so.
    assigned = node(open_client, label="n", step_id=first["id"])
    assert open_client.delete(f"/api/steps/{first['id']}").json()["unassigned_nodes"] == 1
    assert get(open_client, f"/nodes/{assigned['id']}")["step_id"] is None
    assert [s["position"] for s in get(open_client, "/steps")] == [1]
    changes = [e for e in history(open_client, assigned["id"]) if e["field"] == "step_id"]
    assert changes[0]["old_value"] == first["id"] and changes[0]["new_value"] is None


def test_unknown_step_or_branch_is_refused(open_client):
    created = node(open_client)
    patch(open_client, f"/nodes/{created['id']}", {"step_id": "nope"}, status=422)
    patch(open_client, f"/nodes/{created['id']}", {"branch_id": "nope"}, status=422)


# ---------- branches (FR-BR-01…04) ----------


def test_lineage_of_a1(open_client, example):
    # T-BR-01, FR-BR-04: A1 → A → T, and A lists its children.
    a1 = branch(open_client, example.branches["A1"])
    assert [names(example, path) for path in a1["lineage_paths"]] == [["A1", "A", "T"]]
    a = branch(open_client, example.branches["A"])
    assert names(example, a["child_ids"]) == ["A1", "A2"]


def test_split_creates_child_branches_and_records_the_split_node(open_client, example):
    # T-BR-02, FR-BR-02
    a1, a2 = (branch(open_client, example.branches[n]) for n in ("A1", "A2"))
    assert a1["parent_ids"] == a2["parent_ids"] == [example.branches["A"]]
    assert a1["split_node_id"] == a2["split_node_id"] == example.nodes["A-S0"]
    # The split node stays where it was.
    assert get(open_client, f"/nodes/{example.nodes['A-S0']}")["branch_id"] == example.branches["A"]
    splits = [e for e in history(open_client, example.nodes["A-S0"]) if e["action"] == "split"]
    assert len(splits) == 1
    assert splits[0]["new_value"] == [example.branches["A1"], example.branches["A2"]]


def test_split_needs_a_branch_on_the_node(open_client):
    loose = node(open_client, label="no branch")
    post(open_client, f"/nodes/{loose['id']}/split", {"branches": [{"name": "X"}]}, status=422)


def test_branch_parents_cannot_form_a_loop(open_client, example):
    patch(
        open_client,
        f"/branches/{example.branches['T']}",
        {"parent_ids": [example.branches["A1"]]},
        status=422,
    )
    patch(
        open_client,
        f"/branches/{example.branches['A']}",
        {"parent_ids": [example.branches["A"]]},
        status=422,
    )


def test_branch_fields_and_colours(open_client):
    first = post(open_client, "/branches", {"name": "T", "status": "done"})
    second = post(open_client, "/branches", {"name": "U", "colour": "#AA0000"})
    assert first["colour"] != second["colour"] and second["colour"] == "#aa0000"
    assert first["status"] == "done"
    post(open_client, "/branches", {"colour": "red"}, status=422)
    changed = patch(open_client, f"/branches/{first['id']}", {"status": "rejected"})
    assert changed["status"] == "rejected"


def test_deleting_a_branch(open_client, example):
    # Its lineage would lose its root, so a parent branch cannot go.
    response = open_client.delete(f"/api/branches/{example.branches['A']}")
    assert response.status_code == 422 and "A1" in response.json()["detail"]
    # A leaf branch can; its nodes stay, with no branch.
    result = open_client.delete(f"/api/branches/{example.branches['A2']}").json()
    assert result["unassigned_nodes"] == 6
    assert get(open_client, f"/nodes/{example.nodes['A2-S3']}")["branch_id"] is None


# ---------- transitions (FR-EDGE-01…03) ----------


def test_interconversion_leaves_every_branch_unchanged(open_client, example):
    # T-BR-03, INV-4, D17: A-S3 → rot-TS → B-S3 across branches.
    before = {n["id"]: n["branch_id"] for n in get(open_client, "/nodes")}
    rot = node(open_client, label="rot-TS", role="transition_state", step_id=example.steps[3])
    first = edge(open_client, example.nodes["A1-S3"], rot["id"])
    second = edge(open_client, rot["id"], example.nodes["B1-S3"])
    after = {n["id"]: n["branch_id"] for n in get(open_client, "/nodes")}
    assert {k: after[k] for k in before} == before
    assert first["direct"] is False and second["direct"] is False
    # A1 to B1 through the TS; the TS has no branch, so it is not between two branches.
    assert first["cross_branch"] is False
    direct = edge(open_client, example.nodes["A1-S3"], example.nodes["B1-S3"])
    assert direct["cross_branch"] is True and direct["direct"] is True


def test_direct_connection_is_marked_no_ts(open_client, example):
    # T-BR-13, FR-EDGE-03, D53: A1-S1 straight to A1-S3.
    shortcut = edge(open_client, example.nodes["A1-S1"], example.nodes["A1-S3"])
    assert shortcut["direct"] is True
    listed = {t["id"]: t for t in get(open_client, "/transitions")}
    assert listed[shortcut["id"]]["direct"] is True
    through_ts = [
        t
        for t in listed.values()
        if t["source_id"] == example.nodes["A1-S1"] and t["target_id"] == example.nodes["A1-S2"]
    ]
    assert through_ts[0]["direct"] is False
    # It follows the roles: marking A1-S3 as a TS makes it an ordinary transition.
    patch(open_client, f"/nodes/{example.nodes['A1-S3']}", {"role": "transition_state"})
    assert get(open_client, "/transitions")[-1]["direct"] is False


def test_transition_rules(open_client):
    a, b = node(open_client, label="a"), node(open_client, label="b")
    created = edge(open_client, a["id"], b["id"], notes="guess", status="running_externally")
    assert created["status"] == "running_externally" and created["notes"] == "guess"
    assert created["source_kind"] == created["target_kind"] == "node"
    post(open_client, "/transitions", {"source_id": a["id"], "target_id": a["id"]}, status=422)
    post(open_client, "/transitions", {"source_id": a["id"], "target_id": b["id"]}, status=422)
    edge(open_client, b["id"], a["id"])  # the other direction is a different transition
    post(open_client, "/transitions", {"source_id": a["id"], "target_id": "x"}, status=422)

    updated = patch(open_client, f"/transitions/{created['id']}", {"status": "done"})
    assert updated["status"] == "done"
    assert open_client.delete(f"/api/transitions/{created['id']}").status_code == 204
    kinds = [e["action"] for e in history(open_client, created["id"])]
    assert kinds == ["delete", "update", "create"]


def test_superseded_ts_keeps_both_candidates_and_edges(open_client, example):
    # T-BR-11: a second TS candidate for A2 at S2; the first one is marked superseded.
    second = node(
        open_client,
        label="A2-S2b",
        role="transition_state",
        step_id=example.steps[2],
        branch_id=example.branches["A2"],
    )
    edge(open_client, example.nodes["A2-S1"], second["id"])
    before = get(open_client, "/transitions")
    patch(open_client, f"/nodes/{example.nodes['A2-S2']}", {"status": "superseded"})
    assert get(open_client, "/transitions") == before
    assert get(open_client, f"/nodes/{example.nodes['A2-S2']}")["status"] == "superseded"


def test_deleting_a_node_lists_and_removes_its_edges_only(open_client, example):
    # T-BR-12, INV-7: A1-S3 gets a third edge; its deletion lists exactly those three.
    extra = edge(open_client, example.nodes["A1-S1"], example.nodes["A1-S3"])
    target = example.nodes["A1-S3"]
    preview = get(open_client, f"/nodes/{target}/delete-preview")
    assert len(preview["transitions"]) == 3
    assert extra["id"] in {t["id"] for t in preview["transitions"]}
    others = [
        t
        for t in get(open_client, "/transitions")
        if target not in (t["source_id"], t["target_id"])
    ]
    open_client.delete(f"/api/nodes/{target}")
    assert get(open_client, "/transitions") == others
    deleted = [e for e in history(open_client) if e["record_type"] == "transition"]
    assert sum(1 for e in deleted if e["action"] == "delete") == 3


# ---------- groups and reconnection (FR-GRP-01, 02, 04, 05) ----------


def reconnect_g6(client, ex: Example, **extra) -> dict:
    members = [ex.nodes[f"{b}-S6"] for b in ("A1", "A2", "B1", "B2")]
    body = {"member_ids": members, "label": "G6", "outgoing": {"name": "R"}, **extra}
    return post(client, "/groups/reconnect", body)


def test_reconnect_as_group(open_client, example):
    # T-BR-04, T-BR-10 (no TS into the group), FR-GRP-01, D13
    group = reconnect_g6(open_client, example)
    assert names(example, group["incoming_branch_ids"]) == ["A1", "A2", "B1", "B2"]
    assert group["step_id"] == example.steps[6]  # all members were at S6
    assert group["representative_id"] is None  # FR-GRP-02: never chosen by the app

    r = branch(open_client, group["outgoing_branch_id"])
    assert r["name"] == "R"
    assert names(example, r["parent_ids"]) == ["A1", "A2", "B1", "B2"]
    # One chain to the root per parent: R → A1 → A → T, R → A2 → A → T, …
    assert [names(example, path[1:]) for path in r["lineage_paths"]] == [
        ["A1", "A", "T"],
        ["A2", "A", "T"],
        ["B1", "B", "T"],
        ["B2", "B", "T"],
    ]
    # D66: members keep their branch, and note it as the branch they came from.
    for member in group["member_ids"]:
        found = get(open_client, f"/nodes/{member}")
        assert found["group_id"] == group["id"]
        assert found["branch_id"] == found["origin_branch_id"] is not None
    b2 = get(open_client, f"/nodes/{example.nodes['B2-S6']}")
    assert b2["branch_id"] == b2["origin_branch_id"] == example.branches["B2"]
    reconnects = [e for e in history(open_client) if e["action"] == "reconnect"]
    assert len(reconnects) == 1 and reconnects[0]["record_id"] == group["id"]

    # A member's branch can be changed like any node's (D66).
    moved = patch(
        open_client, f"/nodes/{example.nodes['A1-S6']}", {"branch_id": example.branches["A2"]}
    )
    assert moved["branch_id"] == example.branches["A2"] and moved["group_id"] == group["id"]
    # A node can be in one group only.
    again = {"member_ids": [example.nodes["A1-S6"], example.nodes["A1-S5"]]}
    post(open_client, "/groups/reconnect", again, status=422)


def test_edges_into_and_out_of_a_group(open_client, example):
    # FR-GRP-04 (optional TSs, D14), 02 §2: a transition may start or end at a group.
    group = reconnect_g6(open_client, example)
    into = edge(open_client, example.nodes["A1-S5"], group["id"])
    after = node(open_client, label="R-S0", branch_id=group["outgoing_branch_id"])
    out = edge(open_client, group["id"], after["id"])
    assert into["target_kind"] == "group" and out["source_kind"] == "group"
    assert into["direct"] is True  # no TS into the reconnection, shown as "no TS"
    assert into["cross_branch"] is False  # A1 is one of the group's incoming branches


def test_representative_is_a_member_chosen_by_the_user(open_client, example):
    group = reconnect_g6(open_client, example)
    outsider = example.nodes["A1-S5"]
    patch(open_client, f"/groups/{group['id']}", {"representative_id": outsider}, status=422)
    chosen = example.nodes["B1-S6"]
    updated = patch(open_client, f"/groups/{group['id']}", {"representative_id": chosen})
    assert updated["representative_id"] == chosen
    # Deleting the representative leaves the group without one, and says so.
    open_client.delete(f"/api/nodes/{chosen}")
    assert get(open_client, "/groups")[0]["representative_id"] is None
    fields = [e["field"] for e in history(open_client, group["id"])]
    assert fields.count("representative_id") == 2


def test_dissolve_then_delete_with_contents(open_client, example):
    # T-BR-14, D54, P4
    group = reconnect_g6(open_client, example)
    edge(open_client, example.nodes["A1-S5"], group["id"])
    preview = get(open_client, f"/groups/{group['id']}/delete-preview")
    assert len(preview["members"]) == 4 and preview["group_transitions"] == 1
    assert preview["all_transitions"] == 5  # the edge into the group and 4 S5 → S6 edges

    post(open_client, f"/groups/{group['id']}/dissolve", {"restore_branches": True}, status=204)
    assert get(open_client, "/groups") == []
    member = get(open_client, f"/nodes/{example.nodes['A2-S6']}")
    assert member["group_id"] is None and member["branch_id"] == example.branches["A2"]
    assert len(get(open_client, "/transitions")) == 2 + 4 * 6  # the group's edge is gone
    assert any(e["action"] == "dissolve" for e in history(open_client, group["id"]))

    # Rebuild it and delete it with its contents this time.
    group = reconnect_g6(open_client, example, outgoing=None)
    result = open_client.delete(f"/api/groups/{group['id']}").json()
    assert result == {"members": 4, "calculations": 0, "transitions": 4}
    labels = {n["label"] for n in get(open_client, "/nodes")}
    assert not labels & {"A1-S6", "A2-S6", "B1-S6", "B2-S6"}
    assert "A1-S5" in labels
    assert len(get(open_client, "/transitions")) == 2 + 4 * 5


def test_dissolving_without_restoring_leaves_members_without_branch(open_client, example):
    group = reconnect_g6(open_client, example)
    post(open_client, f"/groups/{group['id']}/dissolve", {"restore_branches": False}, status=204)
    assert get(open_client, f"/nodes/{example.nodes['A1-S6']}")["branch_id"] is None


def test_add_nodes_to_a_group(open_client, example):
    # T-BR-15, FR-GRP-06, A20
    group = reconnect_g6(open_client, example)
    other = post(
        open_client,
        "/groups/reconnect",
        {"member_ids": [example.nodes["B1-S5"], example.nodes["B2-S5"]], "label": "G5"},
    )
    moved = example.nodes["B2-S5"]
    patch(open_client, f"/groups/{other['id']}", {"representative_id": moved})
    loose = example.nodes["A1-S5"]

    grown = post(
        open_client, f"/groups/{group['id']}/members", {"node_ids": [loose, moved]}, status=200
    )
    assert set(grown["member_ids"]) >= {loose, moved} and len(grown["member_ids"]) == 6
    added = get(open_client, f"/nodes/{loose}")
    assert added["group_id"] == group["id"] and added["branch_id"] == example.branches["A1"]
    assert added["origin_branch_id"] == example.branches["A1"]
    # The moved node keeps the branch it first came from; its old group loses it and, since
    # it was that group's representative, has none (FR-GRP-02).
    moved_node = get(open_client, f"/nodes/{moved}")
    assert moved_node["branch_id"] == moved_node["origin_branch_id"] == example.branches["B2"]
    shrunk = next(g for g in get(open_client, "/groups") if g["id"] == other["id"])
    assert shrunk["member_ids"] == [example.nodes["B1-S5"]]
    assert shrunk["representative_id"] is None
    # A1 and B2 were incoming branches already, so R's parents are unchanged.
    assert names(example, grown["incoming_branch_ids"]) == ["A1", "A2", "B1", "B2"]
    additions = [e for e in history(open_client, group["id"]) if e["action"] == "add_members"]
    assert len(additions) == 1

    # Adding nodes already in the group, or none, is refused.
    body = {"node_ids": [loose]}
    post(open_client, f"/groups/{group['id']}/members", body, status=422)
    post(open_client, f"/groups/{group['id']}/members", {"node_ids": []}, status=422)


def test_adding_a_node_extends_the_incoming_branches(open_client, example):
    # A20: a new incoming branch also becomes a parent of the outgoing branch, but the
    # outgoing branch itself (or a descendant) never becomes incoming.
    members = [example.nodes["A1-S6"], example.nodes["A2-S6"]]
    group = post(
        open_client, "/groups/reconnect", {"member_ids": members, "outgoing": {"name": "R"}}
    )
    outgoing = group["outgoing_branch_id"]
    after = node(open_client, label="R-S0", branch_id=outgoing)
    grown = post(
        open_client,
        f"/groups/{group['id']}/members",
        {"node_ids": [example.nodes["B1-S6"], after["id"]]},
        status=200,
    )
    assert names(example, grown["incoming_branch_ids"]) == ["A1", "A2", "B1"]
    assert names(example, branch(open_client, outgoing)["parent_ids"]) == ["A1", "A2", "B1"]
    assert get(open_client, f"/nodes/{after['id']}")["origin_branch_id"] == outgoing


def test_group_layout_is_stored_and_not_in_history(open_client, example):
    # T-BR-16, FR-GRP-07, A21
    group = reconnect_g6(open_client, example)
    assert group["layout"] == "grid"
    assert patch(open_client, f"/groups/{group['id']}", {"layout": "vertical"})["layout"] == (
        "vertical"
    )
    patch(open_client, f"/groups/{group['id']}", {"layout": "horizontal"})
    assert get(open_client, "/canvas")["groups"][0]["layout"] == "horizontal"
    patch(open_client, f"/groups/{group['id']}", {"layout": "diagonal"}, status=422)
    assert not [e for e in history(open_client, group["id"]) if e["field"] == "layout"]


def test_a_group_holds_one_conformer_per_branch(open_client):
    # T-BR-17 (backend part), D66, A26: one group per species with a conformer per branch,
    # joined conformer to conformer; each branch's pathway runs through its own conformers.
    steps = [post(open_client, "/steps", {"name": name})["id"] for name in ("IM1", "TS1", "IM2")]
    b1 = post(open_client, "/branches", {"name": "c1"})["id"]
    b2 = post(open_client, "/branches", {"name": "c2"})["id"]
    ids: dict[str, str] = {}
    groups = []
    for step, species in zip(steps, ("IM1", "TS1", "IM2"), strict=True):
        role = "transition_state" if species.startswith("TS") else "minimum"
        for n, b in ((1, b1), (2, b2)):
            label = f"{species}-{n}"
            ids[label] = node(open_client, label=label, role=role, step_id=step, branch_id=b)["id"]
        groups.append(
            post(
                open_client,
                "/groups/reconnect",
                {"member_ids": [ids[f"{species}-1"], ids[f"{species}-2"]], "label": species},
            )
        )
    for n in (1, 2):
        edge(open_client, ids[f"IM1-{n}"], ids[f"TS1-{n}"])
        edge(open_client, ids[f"TS1-{n}"], ids[f"IM2-{n}"])

    for label, found in ((k, get(open_client, f"/nodes/{v}")) for k, v in ids.items()):
        assert found["branch_id"] == (b1 if label.endswith("-1") else b2)
    counts = {b["id"]: b["node_count"] for b in get(open_client, "/canvas")["branches"]}
    assert counts[b1] == counts[b2] == 3
    extended = post(
        open_client, "/pathways/extend", {"path": [ids["IM1-1"]], "branch_id": b1}, status=200
    )
    assert extended["path"] == [ids["IM1-1"], ids["TS1-1"], ids["IM2-1"]]

    # Arranging a branch places the groups by step and leaves the members inside them.
    assert post(open_client, f"/branches/{b1}/arrange", status=200)["moved"] == 3
    placed = {g["label"]: g["pos_x"] for g in get(open_client, "/groups")}
    assert placed["TS1"] - placed["IM1"] == placed["IM2"] - placed["TS1"] == 240

    # Dissolving keeps each member's branch (A26).
    post(open_client, f"/groups/{groups[1]['id']}/dissolve", {"restore_branches": True}, status=204)
    assert get(open_client, f"/nodes/{ids['TS1-2']}")["branch_id"] == b2


def test_reconnect_needs_two_nodes(open_client):
    single = node(open_client)
    post(open_client, "/groups/reconnect", {"member_ids": [single["id"]]}, status=422)


# ---------- canvas (FR-CAN-01, 06) ----------


def test_canvas_returns_everything_and_positions_persist(open_client, example):
    canvas = get(open_client, "/canvas")
    assert len(canvas["steps"]) == 7 and len(canvas["branches"]) == 7
    assert len(canvas["nodes"]) == 1 + 2 + 4 * 6
    a1 = next(b for b in canvas["branches"] if b["name"] == "A1")
    assert a1["node_count"] == 6
    target = example.nodes["A1-S1"]
    response = open_client.put("/api/positions", json={"positions": {target: {"x": 12, "y": 34}}})
    assert response.status_code == 204
    moved = get(open_client, f"/nodes/{target}")
    assert (moved["pos_x"], moved["pos_y"]) == (12, 34)
    assert not any(e["field"] in ("pos_x", "pos_y") for e in history(open_client, target))


def test_arrange_branch_lays_out_by_step_and_leaves_others(open_client, example):
    # FR-CAN-06
    others = {
        n["id"]: (n["pos_x"], n["pos_y"])
        for n in get(open_client, "/nodes")
        if n["branch_id"] != example.branches["A1"]
    }
    open_client.put(
        "/api/positions", json={"positions": {example.nodes["A1-S1"]: {"x": 100, "y": 50}}}
    )
    second_ts = node(
        open_client, label="A1-S2b", step_id=example.steps[2], branch_id=example.branches["A1"]
    )
    loose = node(open_client, label="A1-loose", branch_id=example.branches["A1"])
    moved = post(open_client, f"/branches/{example.branches['A1']}/arrange", status=200)
    assert moved["moved"] == 8

    positions = {n["label"]: (n["pos_x"], n["pos_y"]) for n in get(open_client, "/nodes")}
    assert positions["A1-S1"] == (100, 50)  # the first node in step order anchors the rest
    assert positions["A1-S2"] == (340, 50)
    assert positions["A1-S2b"] == (340, 220)  # a second node at one step is stacked
    assert positions["A1-S6"] == (1300, 50)
    assert positions["A1-loose"] == (1540, 50)  # no step: after the last step
    assert loose["id"] and second_ts["id"]
    after = {n["id"]: (n["pos_x"], n["pos_y"]) for n in get(open_client, "/nodes")}
    assert {k: after[k] for k in others} == others


# ---------- 3D (FR-3D-03, FR-3D-04) ----------


def test_overlay_aligns_the_second_geometry(open_client):
    rotated = """3
water turned
O 0.117300 0.000000 1.000000
H -0.469200 0.757200 1.000000
H -0.469200 -0.757200 1.000000
"""
    a = node(open_client, label="a", xyz=WATER)
    b = node(open_client, label="b", xyz=rotated)
    result = get(open_client, f"/overlay?reference={a['id']}&moving={b['id']}")
    assert result["aligned"] is True and result["rmsd"] < 1e-6
    first_atom = result["moving_xyz"].splitlines()[2].split()
    assert first_atom[0] == "O"
    assert [float(v) for v in first_atom[1:]] == pytest.approx([0.0, 0.0, 0.1173], abs=1e-6)

    other = node(open_client, label="c", xyz="2\n\nH 0 0 0\nH 0 0 0.74\n")
    unaligned = get(open_client, f"/overlay?reference={a['id']}&moving={other['id']}")
    assert unaligned["aligned"] is False and unaligned["rmsd"] is None


def test_normal_modes_list_the_imaginary_mode_first(open_client):
    node_id = import_ts(open_client)
    frequency = next(c for c in calculations(open_client, node_id) if c["type"] == "frequency")
    modes = get(open_client, f"/calculations/{frequency['id']}/modes")
    assert modes["frequencies"][modes["order"][0]] < 0
    atom_count = int(modes["xyz"].splitlines()[0])
    assert len(modes["modes"]) == len(modes["frequencies"])
    assert len(modes["modes"][0]) == atom_count
    other = next(c for c in calculations(open_client, node_id) if c["type"] != "frequency")
    assert open_client.get(f"/api/calculations/{other['id']}/modes").status_code == 404


def test_derived_node_keeps_its_step_and_branch(open_client):
    # ID-5: a coordinate edit on a node with calculations makes a new node at the same place
    # in the mechanism.
    step = post(open_client, "/steps", {"name": "S1"})
    trunk = post(open_client, "/branches", {"name": "T"})
    node_id = import_ts(open_client)
    patch(open_client, f"/nodes/{node_id}", {"step_id": step["id"], "branch_id": trunk["id"]})
    original = get(open_client, f"/nodes/{node_id}")
    moved = original["xyz"].replace(" 0.", " 1.", 1)
    response = open_client.put(f"/api/nodes/{node_id}/geometry", json={"xyz": moved})
    derived = response.json()["node"]
    assert response.json()["derived"] is True
    assert (derived["step_id"], derived["branch_id"]) == (step["id"], trunk["id"])


def test_import_places_a_new_node_where_it_was_dropped(open_client):
    plan = upload(open_client, TS, fixture(TS))
    result = commit(open_client, plan, **named(plan), pos_x=320.0, pos_y=-40.0)
    created = get(open_client, f"/nodes/{result['node_id']}")
    assert (created["pos_x"], created["pos_y"]) == (320.0, -40.0)
