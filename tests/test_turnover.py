"""Turnover (D86, FR-TOF-01…05, T-TOF-*): the TOF of a closed catalytic cycle from the
energetic-span model. The model is checked against the exact steady-state rate of the same
cycle (which the energetic-span formula equals, Kozuch and Shaik 2011) and against numbers
written out by hand. The example cycle, in kcal/mol relative to the catalyst A:

    A    0       (reference)       A → TS1   + substrate joins
    TS1  20
    B    5
    TS2  15                        TS2 → A   − product leaves (closes the cycle at ΔE_r = −10)
"""

import math

import numpy as np
import pytest

from chembook3d.services.turnover import K_OVER_H, R_HARTREE, span_model
from tests.test_energies import add_energies, level_key
from tests.test_pathway import edge, get, history, node, patch, post
from tests.test_species import delete, put, xyz

KCAL = 627.5094740631
LEVEL = "Gaussian B3LYP/def2SVP"
T = 298.15


def rt_kcal(temperature: float = T) -> float:
    return R_HARTREE * temperature * KCAL


def hand_tof(values, is_ts, reaction, temperature=T) -> float:
    """The energetic-span TOF written out term by term (kcal/mol in, 1/s out)."""
    rt = rt_kcal(temperature)
    total = sum(
        math.exp((values[i] - values[j] - (reaction if i > j else 0.0)) / rt)
        for i in range(len(values))
        if is_ts[i]
        for j in range(len(values))
        if not is_ts[j]
    )
    return K_OVER_H * temperature * (math.exp(-reaction / rt) - 1) / total


def steady_state_tof(intermediates, tss, reaction, temperature=T) -> float:
    """The exact steady-state TOF of I1 → T1 → I2 → T2 → … → In → Tn → I1 (+ΔE_r), from
    Eyring rate constants for every forward and backward step (kcal/mol in, 1/s out)."""
    n = len(intermediates)
    rt = rt_kcal(temperature)
    prefactor = K_OVER_H * temperature
    after = [*intermediates[1:], intermediates[0] + reaction]
    forward = [prefactor * math.exp(-(t - i) / rt) for t, i in zip(tss, intermediates, strict=True)]
    backward = [prefactor * math.exp(-(t - i) / rt) for t, i in zip(tss, after, strict=True)]
    # Unknowns: the fractions x_1…x_n of the catalyst in each intermediate, and the flux J.
    matrix = np.zeros((n + 1, n + 1))
    rhs = np.zeros(n + 1)
    for k in range(n):
        matrix[k, k] += forward[k]
        matrix[k, (k + 1) % n] -= backward[k]
        matrix[k, n] = -1.0
    matrix[n, :n] = 1.0
    rhs[n] = 1.0
    return float(np.linalg.solve(matrix, rhs)[n])


@pytest.mark.parametrize(
    ("intermediates", "tss", "reaction"),
    [
        ([0.0], [18.0], -12.0),
        ([0.0, 5.0], [20.0, 15.0], -10.0),
        ([0.0, -20.0], [20.0, -5.0], -15.0),  # the TDTS comes before the TDI
        ([0.0, -3.0, 2.0], [14.0, 12.0, 16.0], -25.0),
        ([0.0, 4.0, -2.0, 1.0], [15.0, 17.0, 13.0, 16.0], -4.0),
        ([0.0, 2.0], [16.0, 18.0], 3.0),  # endergonic: the reverse cycle runs
    ],
)
def test_span_model_equals_the_steady_state_rate(intermediates, tss, reaction):
    # T-TOF-01, T6: the energetic-span formula is exact for a cycle at steady state.
    values = [v for pair in zip(intermediates, tss, strict=True) for v in pair]
    is_ts = [False, True] * len(intermediates)
    model = span_model([v / KCAL for v in values], is_ts, reaction / KCAL, T)
    expected = steady_state_tof(intermediates, tss, reaction)
    assert model["tof"] == pytest.approx(expected, rel=1e-9)
    assert model["tof"] == pytest.approx(hand_tof(values, is_ts, reaction), rel=1e-9)
    # The degrees of TOF control of the TSs add up to one, and so do those of the intermediates.
    assert sum(c for c, ts in zip(model["control"], is_ts, strict=True) if ts) == pytest.approx(1)
    assert sum(c for c, ts in zip(model["control"], is_ts, strict=True) if not ts) == (
        pytest.approx(1)
    )


def test_determining_states_and_the_span():
    # T-TOF-02: δE = T_TDTS − I_TDI when the TDTS comes after the TDI, plus ΔG_r when before.
    rt = rt_kcal()
    after = span_model([0.0, 20 / KCAL, 5 / KCAL, 15 / KCAL], [False, True] * 2, -10 / KCAL, T)
    assert (after["tdts"], after["tdi"]) == (1, 0)
    assert after["span"] * KCAL == pytest.approx(20.0)
    assert after["tof_span"] == pytest.approx(K_OVER_H * T * math.exp(-20 / rt))

    before = span_model([0.0, 20 / KCAL, -20 / KCAL, -5 / KCAL], [False, True] * 2, -15 / KCAL, T)
    assert (before["tdts"], before["tdi"]) == (1, 2)
    assert before["span"] * KCAL == pytest.approx(20 + 20 - 15)
    # One term dominates, so the TOF is close to the energetic-span approximation.
    assert before["tof"] == pytest.approx(before["tof_span"], rel=0.01)


def test_very_exergonic_cycle_does_not_overflow():
    found = span_model([0.0, 25 / KCAL], [False, True], -900 / KCAL, T)
    assert found["tof"] == pytest.approx(K_OVER_H * T * math.exp(-25 / rt_kcal()), rel=1e-9)


# ---------- through the API ----------


class Cycle:
    """The example cycle above, with E values written directly (only differences matter)."""

    E0 = -100.0

    def __init__(self, client, energies: dict[str, float] | None = None):
        self.client = client
        kcal = {"A": 0.0, "TS1": 20.0, "B": 5.0, "TS2": 15.0, **(energies or {})}
        atoms = ("Ru", "C", "C", "H", "H")
        self.ids: dict[str, str] = {}
        for name in ("A", "TS1", "B", "TS2"):
            role = "transition_state" if name.startswith("TS") else "minimum"
            self.ids[name] = node(client, label=name, role=role, charge=0, xyz=xyz(*atoms))["id"]
        for name in ("substrate", "product"):
            self.ids[name] = node(client, label=name, kind="species", xyz=xyz("C", "H"))["id"]
        self.edges = {
            pair: edge(client, self.ids[pair[0]], self.ids[pair[1]])["id"]
            for pair in (("A", "TS1"), ("TS1", "B"), ("B", "TS2"), ("TS2", "A"))
        }
        self.species(("A", "TS1"), "substrate", "joins")
        self.species(("TS2", "A"), "product", "leaves")
        # Substrate at −10 hartree, product 10 kcal/mol lower: ΔE_r = −10 kcal/mol.
        add_energies(client, self.ids["substrate"], -10.0)
        add_energies(client, self.ids["product"], -10.0 - 10 / KCAL)
        for name, value in kcal.items():
            if value is not None:
                offset = 0.0 if name == "A" else -10.0  # the others hold the substrate
                add_energies(client, self.ids[name], self.E0 + offset + value / KCAL)

    def species(self, pair, species, direction):
        body = {"species_id": self.ids[species], "direction": direction, "count": 1}
        put(self.client, f"/transitions/{self.edges[pair]}/species", body)

    def path(self, *names) -> list[str]:
        return [self.ids[n] for n in names]


def turnover(client, path: list[str], **fields) -> dict:
    body = {
        "name": fields.pop("name", "Cycle"),
        "path": path,
        "level": level_key(client, LEVEL),
        "energy_type": "E",
        **fields,
    }
    return post(client, "/turnovers", body)


def result(client, turnover_id: str) -> dict:
    return get(client, f"/turnovers/{turnover_id}/result")


CYCLE = ("A", "TS1", "B", "TS2", "A")


def test_closed_cycle_gives_the_tof_and_its_determining_states(open_client):
    # T-TOF-03, T1, T4: ΔE_r comes from the free species balance; TDTS TS1, TDI A, δE 20.
    cycle = Cycle(open_client)
    created = turnover(open_client, cycle.path(*CYCLE))
    assert created["temperature"] is None and created["compare_id"] is None
    found = result(open_client, created["id"])
    assert found["status"] == "ok" and found["message"] is None
    assert found["level_label"] == LEVEL and found["temperature"] == T
    assert found["cycle"] == cycle.path(*CYCLE)
    assert found["reaction"] * KCAL == pytest.approx(-10.0, abs=1e-6)
    assert [p["label"] for p in found["points"]] == ["A", "TS1", "B", "TS2"]
    assert [p["is_ts"] for p in found["points"]] == [False, True, False, True]
    expected = hand_tof([0, 20, 5, 15], [False, True] * 2, -10)
    assert found["tof"] == pytest.approx(expected, rel=1e-5)
    assert found["points"][found["tdts"]]["label"] == "TS1"
    assert found["points"][found["tdi"]]["label"] == "A"
    assert found["span"] * KCAL == pytest.approx(20.0, abs=1e-6)
    assert found["notes"] == ["E is the electronic energy, with no thermal correction."]

    # T4: the table, already formatted in the energy unit, and the same text as CSV.
    table = found["table"]
    assert table["columns"][5] == "ΔE (kcal/mol)"
    rows = [(r[0], r[1], r[4], r[5], r[7]) for r in table["rows"]]
    assert rows == [
        ("A", "intermediate", "", "0.00", "TDI"),
        ("TS1", "TS", "− substrate", "20.00", "TDTS"),
        ("B", "intermediate", "− substrate", "5.00", ""),
        ("TS2", "TS", "− substrate", "15.00", ""),
        ("A (cycle closed)", "", "− substrate + product", "-10.00", ""),
    ]
    assert float(table["rows"][1][6]) > 99.0
    response = open_client.get(f"/api/turnovers/{created['id']}/table.csv")
    assert response.status_code == 200
    text = response.content.decode("utf-8")
    assert text.startswith("﻿Label,Kind,")
    assert f"TS1,TS,,{LEVEL},− substrate,20.00," in text

    # The profile of the cycle comes with the result, for the chart.
    points = found["profile"]["profiles"][0]["points"]
    assert [p["label"] for p in points] == ["A", "TS1", "B", "TS2", "A"]


def test_temperature_changes_the_tof(open_client):
    # T-TOF-04, as S4: the turnover's own temperature, or the G_qh setting.
    cycle = Cycle(open_client)
    created = turnover(open_client, cycle.path(*CYCLE), temperature=350.0)
    found = result(open_client, created["id"])
    assert found["temperature"] == 350.0 and not found["temperature_from_settings"]
    expected = hand_tof([0, 20, 5, 15], [False, True] * 2, -10, 350.0)
    assert found["tof"] == pytest.approx(expected, rel=1e-5)


def test_cycle_closing_at_a_later_node_leaves_out_what_comes_before(open_client):
    # T-TOF-10, A13: the cycle runs from the node the pathway returns to; earlier points are
    # left out.
    cycle = Cycle(open_client)
    pre = node(open_client, label="Precatalyst", role="minimum", xyz=xyz("Ru", "C"))["id"]
    edge(open_client, pre, cycle.ids["A"])
    add_energies(open_client, pre, -120.0)
    created = turnover(open_client, [pre, *cycle.path(*CYCLE)])
    found = result(open_client, created["id"])
    assert found["status"] == "ok"
    assert found["cycle"] == cycle.path(*CYCLE)
    assert found["notes"][0] == "“Precatalyst” come before the cycle closes and are left out."
    assert found["span"] * KCAL == pytest.approx(20.0, abs=1e-6)


def test_no_ts_connection_makes_the_tof_an_upper_bound(open_client):
    # T-TOF-05, T3: a direct connection adds no barrier, and the result says so.
    cycle = Cycle(open_client)
    c = node(open_client, label="C", role="minimum", xyz=xyz("Ru", "C", "C", "H", "H"))["id"]
    add_energies(open_client, c, Cycle.E0 - 10.0 + 2 / KCAL)
    edge(open_client, cycle.ids["B"], c)
    edge(open_client, c, cycle.ids["TS2"])
    created = turnover(open_client, [*cycle.path("A", "TS1", "B"), c, *cycle.path("TS2", "A")])
    found = result(open_client, created["id"])
    assert found["status"] == "ok"
    assert any("“B” → “C” has no TS" in n and "upper bound" in n for n in found["notes"])
    expected = hand_tof([0, 20, 5, 2, 15], [False, True, False, False, True], -10)
    assert found["tof"] == pytest.approx(expected, rel=1e-5)


def test_missing_values_and_refusals(open_client):
    # T-TOF-06, T1: no fallback; an open pathway is refused when saved.
    cycle = Cycle(open_client, {"B": None})
    created = turnover(open_client, cycle.path(*CYCLE))
    found = result(open_client, created["id"])
    assert found["status"] == "n/a" and "“B”" in found["message"]
    assert found["tof"] is None and found["table"] is None

    response = open_client.post(
        "/api/turnovers", json={"name": "Open", "path": cycle.path("A", "TS1", "B")}
    )
    assert response.status_code == 422 and "closed cycle" in response.json()["detail"]
    response = open_client.post(
        "/api/turnovers", json={"name": "Gap", "path": cycle.path("A", "B", "A")}
    )
    assert response.status_code == 422 and "No transition joins" in response.json()["detail"]

    # A cycle without a TS has no energetic span.
    patch(open_client, f"/nodes/{cycle.ids['TS1']}", {"role": "minimum"})
    patch(open_client, f"/nodes/{cycle.ids['TS2']}", {"role": "unspecified"})
    add_energies(open_client, cycle.ids["B"], Cycle.E0 - 10.0 + 5 / KCAL)
    found = result(open_client, created["id"])
    assert found["status"] == "refused" and "no transition state" in found["message"]
    assert any("“TS2” is not marked" in n for n in found["notes"])

    # No pathway yet, and no level yet.
    empty = post(open_client, "/turnovers", {"name": "Empty"})
    assert empty["path"] == [] and empty["energy_type"] == "G_qh"
    assert result(open_client, empty["id"])["status"] == "incomplete"
    patch(open_client, f"/turnovers/{empty['id']}", {"path": cycle.path(*CYCLE)})
    found = result(open_client, empty["id"])
    assert found["status"] == "incomplete" and found["message"] == "Choose a level of theory."


def test_comparing_two_cycles_reads_as_a_selectivity(open_client):
    # T-TOF-07, T5: the TOF ratio as percentages and an effective ΔΔG‡.
    first = Cycle(open_client)
    second = Cycle(open_client, {"TS1": 21.0})
    a = turnover(open_client, first.path(*CYCLE), name="Z cycle")
    b = turnover(open_client, second.path(*CYCLE), name="E cycle")
    patch(open_client, f"/turnovers/{a['id']}", {"compare_id": b["id"]})
    found = result(open_client, a["id"])
    comparison = found["comparison"]
    assert comparison["name"] == "E cycle" and comparison["message"] is None
    ratio = found["tof"] / comparison["tof"]
    assert comparison["ratio"] == pytest.approx(ratio)
    assert comparison["percent"] == pytest.approx(100 * ratio / (1 + ratio))
    # Only TS1 differs, by 1 kcal/mol, and it dominates: about e^(1/RT).
    assert comparison["ddg"] * KCAL == pytest.approx(1.0, abs=0.01)

    patch(open_client, f"/turnovers/{b['id']}", {"temperature": 310.0})
    comparison = result(open_client, a["id"])["comparison"]
    assert comparison["ratio"] is None and "same" in comparison["message"]

    response = open_client.patch(f"/api/turnovers/{a['id']}", json={"compare_id": a["id"]})
    assert response.status_code == 422
    # Deleting the other turnover clears the comparison.
    delete(open_client, f"/turnovers/{b['id']}", status=204)
    assert get(open_client, "/turnovers")[0]["compare_id"] is None


def test_turnovers_are_in_the_history_and_notice_deleted_nodes(open_client):
    # T-TOF-08, as S7: created, changed and deleted are recorded.
    cycle = Cycle(open_client)
    created = turnover(open_client, cycle.path(*CYCLE))
    tid = created["id"]
    patch(open_client, f"/turnovers/{tid}", {"name": "Metathesis", "notes": "draft"})
    patch(open_client, f"/turnovers/{tid}", {"name": "Metathesis"})  # no change
    entries = history(open_client, tid)
    assert [(e["action"], e["field"]) for e in entries] == [
        ("update", "notes"),
        ("update", "name"),
        ("create", None),
    ]
    assert entries[2]["new_value"]["path"] == cycle.path(*CYCLE)
    response = open_client.post("/api/turnovers", json={"name": "Metathesis"})
    assert response.status_code == 422 and "already" in response.json()["detail"]

    delete(open_client, f"/nodes/{cycle.ids['B']}")
    found = result(open_client, tid)
    assert found["status"] == "refused" and "deleted" in found["message"]

    delete(open_client, f"/turnovers/{tid}", status=204)
    assert history(open_client, tid)[0]["action"] == "delete"
    assert get(open_client, "/turnovers") == []


def test_group_counts_through_its_representative(open_client):
    # T-TOF-09, T3, EN-7: a TS group is one point, valued by the representative the user picks.
    cycle = Cycle(open_client)
    other = node(open_client, label="TS1b", role="transition_state", xyz=xyz("Ru", "C"))["id"]
    add_energies(open_client, other, Cycle.E0 - 10.0 + 22 / KCAL)
    group = post(
        open_client,
        "/groups/reconnect",
        {"member_ids": [cycle.ids["TS1"], other], "label": "TS1 conformers"},
    )
    into = edge(open_client, cycle.ids["A"], group["id"])["id"]
    edge(open_client, group["id"], cycle.ids["B"])
    body = {"species_id": cycle.ids["substrate"], "direction": "joins", "count": 1}
    put(open_client, f"/transitions/{into}/species", body)
    created = turnover(open_client, [cycle.ids["A"], group["id"], *cycle.path("B", "TS2", "A")])
    found = result(open_client, created["id"])
    assert found["status"] == "n/a" and "representative" in found["message"]

    patch(open_client, f"/groups/{group['id']}", {"representative_id": other})
    found = result(open_client, created["id"])
    assert found["status"] == "ok"
    assert found["points"][found["tdts"]]["label"] == "TS1 conformers"
    assert found["points"][1]["kind"] == "group" and found["points"][1]["is_ts"]
    assert found["span"] * KCAL == pytest.approx(22.0, abs=1e-6)
