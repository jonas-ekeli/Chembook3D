"""Conformer equilibrium (D171, FR-EQ-01…06, T-EQ-*): Boltzmann populations of conformers, the
Eyring rates of the TSs between them and the time the set takes to reach equilibrium. The
kinetics is checked against the analytic two-state solution and numpy's eigenvalues of a rate
matrix written out by hand; the API against numbers by hand. Energies are E values written
directly (only differences matter), in kcal/mol relative to the lowest conformer."""

import math

import anyio
import numpy as np
import pytest

from chembook3d.services.equilibrium import (
    K_OVER_H,
    R_HARTREE,
    Relaxation,
    bottlenecks,
    rate_matrix,
    shares,
)
from tests.test_energies import add_energies, level_key
from tests.test_mcp import _app_bridge, _json
from tests.test_pathway import edge, get, history, node, patch, post
from tests.test_species import delete, put, xyz

KCAL = 627.5094740631
LEVEL = "Gaussian B3LYP/def2SVP"
T = 298.15
E0 = -100.0
ATOMS = ("Ru", "C", "C", "H", "H")


def rt(temperature: float = T) -> float:
    return R_HARTREE * temperature * KCAL  # kcal/mol


def k(barrier: float, temperature: float = T) -> float:
    return K_OVER_H * temperature * math.exp(-barrier / rt(temperature))


# ---------- the kinetics on plain numbers ----------


def test_two_conformers_relax_as_the_analytic_solution():
    # T-EQ-01: A at 0, B at 1.00, TS at 10.00 kcal/mol.
    values, counts = [0.0, 1.0 / KCAL], [1, 1]
    links = [(0, 1, 10.0 / KCAL, 1)]
    forward, backward = k(10.0), k(9.0)
    matrix = rate_matrix(values, counts, links, T)
    assert matrix[1, 0] == pytest.approx(forward) and matrix[0, 1] == pytest.approx(backward)
    pi = shares(values, counts, T)
    assert pi[1] / pi[0] == pytest.approx(math.exp(-1.0 / rt()))
    solved = Relaxation(matrix, pi)
    assert solved.resolved == [pytest.approx(forward + backward)]
    # From pure B, both populations are π + (1 − π_B)·e^(−λt) away from equilibrium.
    expected = math.log((1 - pi[1]) / 0.01) / (forward + backward)
    assert solved.time_to_equilibrium(1) == pytest.approx(expected, rel=1e-6)
    later = solved.populations(1, 2 * expected)
    decay = math.exp(-2 * expected * (forward + backward))
    assert later[1] - pi[1] == pytest.approx((1 - pi[1]) * decay)


def test_counts_keep_detailed_balance():
    # T-EQ-01, D171d: B stands for two mirror images, so it weighs twice and its rate over the
    # TS (drawn once for both) is shared between them.
    values, counts = [0.0, 1.0 / KCAL], [1, 2]
    pi = shares(values, counts, T)
    assert pi[1] / pi[0] == pytest.approx(2 * math.exp(-1.0 / rt()))
    matrix = rate_matrix(values, counts, [(0, 1, 10.0 / KCAL, 2)], T)
    assert matrix[1, 0] == pytest.approx(2 * k(10.0))
    assert matrix[0, 1] == pytest.approx(k(9.0))
    assert matrix[1, 0] * pi[0] == pytest.approx(matrix[0, 1] * pi[1])  # detailed balance
    assert (matrix @ np.array(pi)) == pytest.approx([0.0, 0.0], abs=1e-6)


def test_three_conformers_against_numpy_and_a_parallel_ts():
    # T-EQ-02: 0, 2, 1 with TSs at 8 (A–B) and 12 (B–C); then a second A–B TS at 9.
    values = [v / KCAL for v in (0.0, 2.0, 1.0)]
    links = [(0, 1, 8.0 / KCAL, 1), (1, 2, 12.0 / KCAL, 1)]
    by_hand = np.array(
        [
            [-k(8.0), k(6.0), 0.0],
            [k(8.0), -k(6.0) - k(10.0), k(11.0)],
            [0.0, k(10.0), -k(11.0)],
        ]
    )
    matrix = rate_matrix(values, [1, 1, 1], links, T)
    assert matrix == pytest.approx(by_hand, rel=1e-12)
    rates = sorted(-np.linalg.eigvals(by_hand).real)[1:]
    solved = Relaxation(matrix, shares(values, [1, 1, 1], T))
    assert sorted(solved.resolved) == pytest.approx(rates, rel=1e-8)

    parallel = rate_matrix(values, [1, 1, 1], [*links, (0, 1, 9.0 / KCAL, 1)], T)
    assert parallel[1, 0] == pytest.approx(k(8.0) + k(9.0))


def test_the_effective_barrier_is_the_lowest_way_over():
    # T-EQ-03, D171g: A–B over 12, B–C over 8, A–C over 15: C is reached over 12, through B.
    values = [0.0, 2.0 / KCAL, 1.0 / KCAL, -50.0]
    links = [(0, 1, 12.0 / KCAL, 1), (1, 2, 8.0 / KCAL, 1), (0, 2, 15.0 / KCAL, 1)]
    heights = bottlenecks(values, links, 0)
    assert [h * KCAL for h in heights[:3]] == pytest.approx([0.0, 12.0, 12.0])
    assert heights[3] is None


def test_a_relaxation_too_slow_to_resolve_is_said():
    # A112: a rate 10⁻¹² of the fastest is not resolved, so no time to equilibrium.
    values = [0.0, 0.0, 0.0]
    links = [(0, 1, 2.0 / KCAL, 1), (1, 2, 40.0 / KCAL, 1)]
    solved = Relaxation(rate_matrix(values, [1, 1, 1], links, T), shares(values, [1, 1, 1], T))
    assert solved.unresolved == 1 and len(solved.resolved) == 1
    assert solved.time_to_equilibrium(0) is None


# ---------- through the API ----------


def conformer(client, label: str, kcal: float | None, role: str = "minimum", **fields) -> str:
    made = node(client, label=label, role=role, charge=0, xyz=xyz(*ATOMS), **fields)["id"]
    if kcal is not None:
        add_energies(client, made, E0 + kcal / KCAL)
    return made


def equilibrium(client, members: list, **fields) -> dict:
    body = {
        "name": fields.pop("name", "Rotamers"),
        "members": members,
        "level": level_key(client, LEVEL),
        "energy_type": "E",
        **fields,
    }
    return post(client, "/equilibria", body)


def result(client, equilibrium_id: str) -> dict:
    return get(client, f"/equilibria/{equilibrium_id}/result")


def rows(found: dict) -> dict[str, dict]:
    return {r["label"]: r for r in found["conformers"] + found["barriers"]}


def two_state(client) -> dict[str, str]:
    ids = {
        "A": conformer(client, "A", 0.0),
        "TS": conformer(client, "TS", 10.0, role="transition_state"),
        "B": conformer(client, "B", 1.0),
    }
    edge(client, ids["A"], ids["TS"])
    edge(client, ids["TS"], ids["B"])
    return ids


def test_populations_rates_and_time_to_equilibrium(open_client):
    # T-EQ-01 through the API.
    ids = two_state(open_client)
    created = equilibrium(open_client, [ids["A"], ids["TS"], ids["B"]])
    assert created["members"] == [{"id": i, "count": 1} for i in (ids["A"], ids["TS"], ids["B"])]
    found = result(open_client, created["id"])
    assert found["status"] == "ok" and found["level_label"] == LEVEL
    assert found["lowest_id"] == ids["A"]
    shown = rows(found)
    b = math.exp(-1.0 / rt())
    assert shown["A"]["population"] == pytest.approx(100 / (1 + b), rel=1e-6)
    assert shown["B"]["relative"] * KCAL == pytest.approx(1.0, abs=1e-6)
    assert shown["B"]["effective_barrier"] * KCAL == pytest.approx(10.0, abs=1e-6)
    ts = shown["TS"]
    assert ts["joins"] == sorted([ids["A"], ids["B"]])
    directions = {d["from_id"]: d for d in ts["directions"]}
    assert directions[ids["A"]]["rate"] == pytest.approx(k(10.0), rel=1e-6)
    assert directions[ids["B"]]["barrier"] * KCAL == pytest.approx(9.0, abs=1e-6)
    assert directions[ids["B"]]["half_life"] == pytest.approx(math.log(2) / k(9.0), rel=1e-6)
    [joined] = found["sets"]
    assert joined["slowest"] == pytest.approx(1 / (k(10.0) + k(9.0)), rel=1e-6)
    # No start chosen: the slowest start, B, whose way from pure B is the longest.
    assert joined["start_id"] == ids["B"] and not joined["start_chosen"]
    pi_b = b / (1 + b)
    expected = math.log((1 - pi_b) / 0.01) / (k(10.0) + k(9.0))
    assert joined["equilibrium_time"] == pytest.approx(expected, rel=1e-5)
    curve = joined["curve"]
    assert len(curve["times"]) == 80 and len(curve["populations"]) == 2
    assert curve["populations"][1][0] == pytest.approx(1.0, abs=0.01)  # starts as pure B
    assert curve["populations"][1][-1] == pytest.approx(pi_b, abs=1e-6)
    assert found["notes"] == [
        "E is the electronic energy, with no thermal correction.",
        f"An error of 1 kcal/mol in a ΔG changes a ratio by a factor of "
        f"{math.exp(1 / rt()):.1f} at 298.15 K, and a rate by the same factor.",
    ]

    # D171d: B counted twice, and a chosen start.
    patch(
        open_client,
        f"/equilibria/{created['id']}",
        {
            "members": [ids["A"], ids["TS"], {"id": ids["B"], "count": 2}],
            "start_id": ids["A"],
        },
    )
    found = result(open_client, created["id"])
    assert rows(found)["A"]["population"] == pytest.approx(100 / (1 + 2 * b), rel=1e-6)
    assert found["sets"][0]["start_id"] == ids["A"] and found["sets"][0]["start_chosen"]


def test_a_set_split_by_a_direct_edge(open_client):
    # T-EQ-03, Q53: C is joined to B only by a direct edge; a TS with one end; one with no value.
    ids = two_state(open_client)
    ids["C"] = conformer(open_client, "C", 2.0)
    edge(open_client, ids["B"], ids["C"])
    ids["TS-one"] = conformer(open_client, "TS-one", 12.0, role="transition_state")
    edge(open_client, ids["C"], ids["TS-one"])
    ids["TS-none"] = conformer(open_client, "TS-none", None, role="transition_state")
    edge(open_client, ids["A"], ids["TS-none"])
    edge(open_client, ids["TS-none"], ids["C"])
    found = result(open_client, equilibrium(open_client, list(ids.values()))["id"])
    assert found["status"] == "ok"
    assert [len(s["conformer_ids"]) for s in found["sets"]] == [2, 1]
    shown = rows(found)
    assert shown["C"]["set"] == 1 and shown["C"]["effective_barrier"] is None
    assert shown["C"]["population"] > 0
    assert "“TS-one” has edges to 1 of the conformers, not two, so it joins none." in found["notes"]
    assert any(n.startswith("“TS-none” has no E at this level") for n in found["notes"])
    assert (
        "“B” and “C” are joined only by a direct edge (no TS, D53), so no exchange is computed "
        "between them." in found["notes"]
    )
    assert any(n.startswith("No computed path of TSs joins “C” to “A”") for n in found["notes"])
    assert "TS-none" not in shown


def test_a_ts_below_its_conformer_counts_at_its_energy(open_client):
    # T-EQ-04: a TS 0.5 kcal/mol below B.
    a = conformer(open_client, "A", 0.0)
    b = conformer(open_client, "B", 5.0)
    ts = conformer(open_client, "TS", 4.5, role="transition_state")
    edge(open_client, a, ts)
    edge(open_client, ts, b)
    found = result(open_client, equilibrium(open_client, [a, b, ts])["id"])
    directions = {d["from_id"]: d for d in rows(found)["TS"]["directions"]}
    assert directions[b]["barrier"] == 0.0
    assert directions[a]["barrier"] * KCAL == pytest.approx(5.0, abs=1e-6)
    assert "“TS” is 0.50 kcal/mol below “B”, so it counts at that conformer's energy" in " ".join(
        found["notes"]
    )


def test_what_makes_it_na_refused_or_incomplete(open_client):
    # T-EQ-04
    a = conformer(open_client, "A", 0.0)
    b = conformer(open_client, "B", None)
    found = result(open_client, equilibrium(open_client, [a, b])["id"])
    assert found["status"] == "n/a" and "“B”" in found["message"]

    odd = node(open_client, label="Odd", role="minimum", charge=0, xyz=xyz("Ru", "C"))["id"]
    add_energies(open_client, odd, E0)
    found = result(open_client, equilibrium(open_client, [a, odd], name="Atoms")["id"])
    assert found["status"] == "refused" and "same atoms" in found["message"]

    species = node(open_client, label="ethylene", kind="species", xyz=xyz("C", "C"))["id"]
    response = open_client.post(
        "/api/equilibria", json={"name": "Species", "members": [a, species]}
    )
    assert response.status_code == 422 and "free species" in response.text

    found = result(open_client, equilibrium(open_client, [a], name="One")["id"])
    assert found["status"] == "incomplete"
    found = result(open_client, equilibrium(open_client, [a, odd], name="None", level=None)["id"])
    assert found["status"] in ("incomplete", "refused")

    response = open_client.post(
        "/api/equilibria", json={"name": "Count", "members": [{"id": a, "count": 0}]}
    )
    assert response.status_code == 422 and "count" in response.text


def test_which_members_count(open_client):
    # T-EQ-05: rejected left out; a duplicate counts once; a group's members count; an
    # unspecified node is a conformer with a note.
    a = conformer(open_client, "A", 0.0)
    again = conformer(open_client, "A again", 0.05)
    gone = conformer(open_client, "Gone", -3.0)
    patch(open_client, f"/nodes/{gone}", {"status": "rejected"})
    b = conformer(open_client, "B", 1.0, role="unspecified")
    c = node(open_client, label="C", role="minimum", charge=0, xyz=xyz("Ru", "C", "H", "C", "H"))
    add_energies(open_client, c["id"], E0 + 2.0 / KCAL)
    group = post(open_client, "/groups/reconnect", {"member_ids": [b, c["id"]], "label": "BC"})
    found = result(open_client, equilibrium(open_client, [a, again, gone, group["id"]])["id"])
    assert found["status"] == "ok"
    assert [r["label"] for r in found["conformers"]] == ["A", "B", "C"]
    assert "“Gone” is rejected and left out." in found["notes"]
    assert any(n.startswith("“A again” duplicates “A” (RMSD 0.000") for n in found["notes"])
    assert "“B” is not marked as a minimum; it counts as a conformer." in found["notes"]


def test_history_records_each_change(open_client):
    # T-EQ-06
    ids = two_state(open_client)
    created = equilibrium(open_client, [ids["A"], ids["B"]])
    path = f"/equilibria/{created['id']}"
    patch(open_client, path, {"name": "Ru-14e"})
    patch(open_client, path, {"members": [ids["A"], ids["TS"], ids["B"]]})
    patch(open_client, path, {"notes": "iPr and Ru=C rotations"})
    patch(open_client, path, {"name": "Ru-14e"})  # no change, no entry
    entries = history(open_client, created["id"])
    assert sorted((e["action"], e["field"]) for e in entries) == [
        ("create", None),
        ("update", "members"),
        ("update", "name"),
        ("update", "notes"),
    ]
    assert [e["name"] for e in get(open_client, "/equilibria")] == ["Ru-14e"]
    delete(open_client, path, status=204)
    assert get(open_client, "/equilibria") == []
    assert history(open_client, created["id"])[0]["action"] == "delete"


def test_curtin_hammett_against_the_next_step(open_client):
    # T-EQ-07, D173: A (0) and B (1.0) joined by a rotation TS; the next step goes over N1 from
    # A (15.0) and N2 from B with ethylene joining (14.0 with it); N3 is joined to neither and
    # N4 has no value.
    a = conformer(open_client, "A", 0.0)
    b = conformer(open_client, "B", 1.0)
    rotation = conformer(open_client, "R", 10.0, role="transition_state")
    edge(open_client, a, rotation)
    edge(open_client, rotation, b)
    ethylene = node(open_client, label="ethylene", kind="species", charge=0, xyz=xyz(*"CCHHHH"))
    add_energies(open_client, ethylene["id"], -78.0)
    n1 = conformer(open_client, "N1", 15.0, role="transition_state")
    edge(open_client, a, n1)
    n2 = node(
        open_client, label="N2", role="transition_state", charge=0, xyz=xyz(*ATOMS, *"CCHHHH")
    )["id"]
    add_energies(open_client, n2, E0 - 78.0 + 14.0 / KCAL)
    joined = edge(open_client, b, n2)["id"]
    body = {"species_id": ethylene["id"], "direction": "joins", "count": 1}
    put(open_client, f"/transitions/{joined}/species", body, 200)
    n3 = conformer(open_client, "N3", 5.0, role="transition_state")
    n4 = conformer(open_client, "N4", None, role="transition_state")
    edge(open_client, a, n4)
    created = equilibrium(open_client, [a, rotation, b], next_ids=[n1, n2, n3, n4])
    assert created["next_ids"] == [n1, n2, n3, n4]
    found = result(open_client, created["id"])["next_step"]
    shown = {r["label"]: r for r in found["members"]}
    assert shown["N1"]["barrier"] * KCAL == pytest.approx(15.0, abs=1e-6)
    assert shown["N2"]["barrier"] * KCAL == pytest.approx(14.0, abs=1e-6)
    assert [s["label"] for s in shown["N2"]["species"]] == ["ethylene"]
    assert shown["N3"]["barrier"] is None and "not joined" in shown["N3"]["message"]
    assert shown["N4"]["barrier"] is None and shown["N4"]["message"]
    pool = -rt() * math.log(1 + math.exp(-1.0 / rt()))
    expected = -rt() * math.log(math.exp(-15.0 / rt()) + math.exp(-14.0 / rt())) - pool
    assert found["pool"] * KCAL == pytest.approx(pool, abs=1e-6)
    assert found["effective_barrier"] * KCAL == pytest.approx(expected, abs=1e-6)
    assert found["rate"] == pytest.approx(k(expected), rel=1e-6)
    interconversion = 1 / (k(10.0) + k(9.0))
    assert found["interconversion_time"] == pytest.approx(interconversion, rel=1e-6)
    assert found["interconversion_barrier"] * KCAL == pytest.approx(10.0, abs=1e-6)
    ratio = 1 / k(expected) / interconversion
    assert found["ratio"] == pytest.approx(ratio, rel=1e-5)
    assert found["ddg"] * KCAL == pytest.approx(rt() * math.log(ratio), abs=1e-6)
    assert found["faster"] == "interconversion"
    assert any("pseudo-first-order" in n for n in found["notes"])

    # The rotation made slower than the next step flips it.
    slow = conformer(open_client, "R slow", 20.0, role="transition_state")
    edge(open_client, a, slow)
    edge(open_client, slow, b)
    patch(open_client, f"/equilibria/{created['id']}", {"members": [a, slow, b]})
    found = result(open_client, created["id"])["next_step"]
    assert found["faster"] == "next step" and found["ratio"] < 1 and found["ddg"] < 0

    # Without a TS between the conformers, how fast they interconvert is not known.
    patch(open_client, f"/equilibria/{created['id']}", {"members": [a, b]})
    found = result(open_client, created["id"])["next_step"]
    assert found["effective_barrier"] is not None and found["ratio"] is None
    assert "interconvert is not known" in found["message"]

    response = open_client.patch(
        f"/api/equilibria/{created['id']}", json={"next_ids": [ethylene["id"]]}
    )
    assert response.status_code == 422 and "free species" in response.text
    assert (
        result(open_client, equilibrium(open_client, [a, b], name="None")["id"])["next_step"]
        is None
    )


def test_claudes_tools(tmp_path):
    # T-MCP-25: create, read the result, change and list through the MCP tools; the delete
    # waits for the user.
    async def run():
        bridge, page = _app_bridge(confirm_timeout=5)
        await page.post("/api/investigations", json={"folder": str(tmp_path / "i"), "name": "T"})
        a = _json(await bridge.call("create_node", {"label": "A", "xyz": xyz(*ATOMS)}))
        b = _json(await bridge.call("create_node", {"label": "B", "xyz": xyz(*ATOMS)}))
        made = _json(
            await bridge.call(
                "create_conformer_equilibrium",
                {"name": "Rotamers", "members": [{"id": a["id"]}, {"id": b["id"], "count": 2}]},
            )
        )
        assert made["members"][1] == {"id": b["id"], "count": 2}
        found = _json(
            await bridge.call("get_conformer_equilibrium_result", {"equilibrium_id": made["id"]})
        )
        assert found["status"] == "incomplete"  # no level yet
        changed = _json(
            await bridge.call(
                "update_conformer_equilibrium", {"equilibrium_id": made["id"], "notes": "x"}
            )
        )
        assert changed["notes"] == "x"
        listed = _json(await bridge.call("list_conformer_equilibria", {}))
        assert [e["name"] for e in listed] == ["Rotamers"]
        unanswered = await bridge.call(
            "delete_conformer_equilibrium", {"equilibrium_id": made["id"]}
        )
        assert unanswered.isError and "No answer in the app" in unanswered.content[0].text
        assert len((await page.get("/api/equilibria")).json()) == 1

    anyio.run(run)
