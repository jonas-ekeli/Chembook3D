"""Which TSs a selectivity counts (D156): T-SEL-10…12. Rejected, failed and superseded TSs are
left out, and a member that duplicates an earlier one of its outcome counts once, each with a
note; before, a rejected TS 2 kcal/mol lower took 97 % of its outcome, and a TS imported twice
added RT ln 2."""

import math

import pytest

from tests.test_pathway import patch, post
from tests.test_selectivity import R, by_name, result, selectivity, ts

T = 298.15


def test_rejected_failed_and_superseded_transition_states_are_left_out(open_client):
    # T-SEL-10
    r = ts(open_client, "TS-R", 0.0, 0.0)
    wrong = ts(open_client, "TS-R wrong", -2.0, 0.05)
    s = ts(open_client, "TS-S", 0.0, 0.1)
    group = post(open_client, "/groups/reconnect", {"member_ids": [r, wrong], "label": "R TSs"})
    created = selectivity(open_client, {"R": [group["id"]], "S": [s]})
    before = by_name(result(open_client, created["id"]))["R"]["boltzmann_percent"]
    assert before > 95.0  # the 2 kcal/mol lower TS dominates while it counts

    for status in ("rejected", "failed", "superseded"):
        patch(open_client, f"/nodes/{wrong}", {"status": status})
        found = result(open_client, created["id"])
        outcomes = by_name(found)
        assert [m["node_id"] for m in outcomes["R"]["members"]] == [r]
        assert outcomes["R"]["boltzmann_percent"] == pytest.approx(50.0)
        assert found["notes"] == [f"“TS-R wrong” is {status} and left out."]


def test_an_outcome_with_only_rejected_transition_states_is_incomplete(open_client):
    r = ts(open_client, "TS-R", 0.0, 0.0)
    s = ts(open_client, "TS-S", 0.0, 0.1)
    patch(open_client, f"/nodes/{r}", {"status": "rejected"})
    found = result(open_client, selectivity(open_client, {"R": [r], "S": [s]})["id"])
    assert found["status"] == "incomplete"
    assert "“TS-R” is rejected and left out." in found["notes"]


def test_a_duplicate_transition_state_counts_once(open_client):
    # T-SEL-11: the same TS imported twice (here 0.001 Å apart) is one TS, not RT ln 2.
    r = ts(open_client, "TS-R", 0.0, 0.0)
    again = ts(open_client, "TS-R again", 0.0, 0.001)
    s = ts(open_client, "TS-S", 0.0, 0.2)
    found = result(open_client, selectivity(open_client, {"R": [r, again], "S": [s]})["id"])
    outcomes = by_name(found)
    assert [m["node_id"] for m in outcomes["R"]["members"]] == [r]
    assert outcomes["R"]["boltzmann_percent"] == pytest.approx(50.0)
    (note,) = found["notes"]
    assert note.startswith("“TS-R again” duplicates “TS-R” (RMSD 0.00") and "counts once" in note


def test_conformers_apart_in_geometry_or_energy_still_count_separately(open_client):
    # T-SEL-12: conformers 0.2 Å apart, or close in geometry but 0.3 kcal/mol apart in energy,
    # are summed as before (Curtin–Hammett).
    r1 = ts(open_client, "R-a", 0.0, 0.0)
    r2 = ts(open_client, "R-b", 0.0, 0.2)
    s = ts(open_client, "S-a", 0.0, 0.4)
    found = result(open_client, selectivity(open_client, {"R": [r1, r2], "S": [s]})["id"])
    assert by_name(found)["S"]["boltzmann_ddg"] == pytest.approx(R * T * math.log(2))
    assert found["notes"] == []

    near = ts(open_client, "R-c", 0.3, 0.001)
    created = selectivity(open_client, {"R": [r1, near], "S": [s]}, name="Near")
    found = result(open_client, created["id"])
    assert [m["node_id"] for m in by_name(found)["R"]["members"]] == [r1, near]
    assert found["notes"] == []
