"""Free species and mass balance (D69, FR-SPC-01…06): species records, species that join or
leave on a transition, balanced profiles, the energy table and edge differences, and the
W-BALANCE atom check. The example is a small propene metathesis at a Ru methylidene:

    A  Ru=CH2                      (reference)
    B  A·propene        A → B      + propene
    TS                  B → TS
    C  Ru=CHCH3         TS → C     − ethylene
    A  again            C → A      + propene, − 2-butene (closes the cycle)
"""

import pytest

from tests import gaussian_text as g
from tests.test_energies import KCAL, SVP, add_energies, level_key, view
from tests.test_import import commit, upload
from tests.test_pathway import edge, get, history, node, patch, post


def xyz(*elements: str) -> str:
    rows = [f"{e} {i * 1.5:.3f} 0.000 0.000" for i, e in enumerate(elements)]
    return "\n".join([str(len(elements)), "", *rows]) + "\n"


def put(client, path: str, body: dict, status: int = 200) -> dict:
    response = client.put(f"/api{path}", json=body)
    assert response.status_code == status, response.text
    return response.json()


def delete(client, path: str, status: int = 200) -> dict:
    response = client.delete(f"/api{path}")
    assert response.status_code == status, response.text
    return response.json() if response.content else {}


RU_CH2 = ("Ru", "C", "H", "H")
PROPENE = ("C", "C", "C", "H", "H", "H", "H", "H", "H")
ETHYLENE = ("C", "C", "H", "H", "H", "H")
BUTENE = ("C",) * 4 + ("H",) * 8
COMPLEX = ("Ru",) + ("C",) * 4 + ("H",) * 8
RU_CHCH3 = ("Ru", "C", "C", "H", "H", "H", "H")

E = {  # hartree; only the differences matter
    "A": -100.0,
    "propene": -10.0,
    "B": -110.01,
    "TS": -109.99,
    "C": -95.0,
    "ethylene": -15.02,
    "butene": -24.99,
}


class Cycle:
    def __init__(self, client):
        self.client = client
        self.ids: dict[str, str] = {}
        for name, atoms in (("A", RU_CH2), ("B", COMPLEX), ("TS", COMPLEX), ("C", RU_CHCH3)):
            role = "transition_state" if name == "TS" else "minimum"
            self.ids[name] = node(client, label=name, role=role, charge=0, xyz=xyz(*atoms))["id"]
        for name, atoms in (("propene", PROPENE), ("ethylene", ETHYLENE), ("butene", BUTENE)):
            created = node(client, label=name, kind="species", charge=0, xyz=xyz(*atoms))
            assert created["kind"] == "species"
            self.ids[name] = created["id"]
        self.edges = {
            pair: edge(client, self.ids[pair[0]], self.ids[pair[1]])["id"]
            for pair in (("A", "B"), ("B", "TS"), ("TS", "C"))
        }

    def attach(self, pair, species, direction, count=1, status=200) -> dict:
        body = {"species_id": self.ids[species], "direction": direction, "count": count}
        return put(self.client, f"/transitions/{self.edges[pair]}/species", body, status)

    def energies(self, *names):
        for name in names or E:
            add_energies(self.client, self.ids[name], E[name])

    def path(self, *names) -> list[str]:
        return [self.ids[n] for n in names]


@pytest.fixture
def cycle(open_client) -> Cycle:
    found = Cycle(open_client)
    found.attach(("A", "B"), "propene", "joins")
    found.attach(("TS", "C"), "ethylene", "leaves")
    return found


def svp(client) -> str:
    return level_key(client, "Gaussian B3LYP/def2SVP")


def profile(client, paths, reference_id, energy_type="E") -> dict:
    body = {"paths": paths, "reference_id": reference_id, "level": svp(client), "type": energy_type}
    return post(client, "/energies/profile", body, status=200)


def test_species_are_records_kept_off_the_canvas(open_client, cycle):
    # FR-SPC-01: a species is a node record of its own kind, listed apart from the canvas.
    canvas = get(open_client, "/canvas")
    assert {n["label"] for n in canvas["species"]} == {"propene", "ethylene", "butene"}
    assert "propene" not in {n["label"] for n in canvas["nodes"]}
    propene = get(open_client, f"/nodes/{cycle.ids['propene']}")
    assert propene["formula"] == "C3H6" and propene["kind"] == "species"

    # FR-SPC-02: no edges, branch, step or group for a species.
    response = open_client.post(
        "/api/transitions", json={"source_id": cycle.ids["A"], "target_id": cycle.ids["propene"]}
    )
    assert response.status_code == 422 and "free species" in response.json()["detail"]
    branch = post(open_client, "/branches", {"name": "A"})
    patch(open_client, f"/nodes/{cycle.ids['propene']}", {"branch_id": branch["id"]}, status=422)
    response = open_client.post(
        "/api/groups/reconnect", json={"member_ids": [cycle.ids["propene"], cycle.ids["ethylene"]]}
    )
    assert response.status_code == 422 and "free species" in response.json()["detail"]


def test_species_join_and_leave_on_transitions_with_history(open_client, cycle):
    # FR-SPC-03: the transition lists its species; every change is in the history.
    transitions = {t["id"]: t for t in get(open_client, "/canvas")["transitions"]}
    joined = transitions[cycle.edges[("A", "B")]]["species"]
    assert joined == [
        {
            "species_id": cycle.ids["propene"],
            "label": "propene",
            "formula": "C3H6",
            "direction": "joins",
            "count": 1,
        }
    ]
    changed = cycle.attach(("A", "B"), "propene", "joins", count=2)
    assert changed["species"][0]["count"] == 2
    entry = history(open_client, cycle.edges[("A", "B")])[0]
    assert entry["field"] == "species"
    assert entry["old_value"][0]["count"] == 1 and entry["new_value"][0]["count"] == 2

    cycle.attach(("A", "B"), "propene", "sideways", status=422)
    cycle.attach(("A", "B"), "propene", "joins", count=0, status=422)
    body = {"species_id": cycle.ids["B"], "direction": "joins"}
    put(open_client, f"/transitions/{cycle.edges[('A', 'B')]}/species", body, status=422)

    removed = delete(
        open_client, f"/transitions/{cycle.edges[('A', 'B')]}/species/{cycle.ids['propene']}"
    )
    assert removed["species"] == []


def test_profile_adds_leaving_and_subtracts_joining_species(open_client, cycle):
    # T-SPC-01: every point has the reference's atoms.
    cycle.energies()
    found = profile(open_client, [cycle.path("A", "B", "TS", "C")], cycle.ids["A"])
    points = found["profiles"][0]["points"]
    relative = [p["relative"] for p in points]
    assert relative[0] == 0.0
    assert relative[1] == pytest.approx(E["B"] - E["A"] - E["propene"])
    assert relative[2] == pytest.approx(E["TS"] - E["A"] - E["propene"])
    assert relative[3] == pytest.approx(E["C"] + E["ethylene"] - E["A"] - E["propene"])
    assert [s["label"] for s in points[3]["species"]] == ["propene", "ethylene"]
    assert [s["count"] for s in points[3]["species"]] == [-1, 1]

    # Reference in the middle: walking backwards swaps the signs.
    found = profile(open_client, [cycle.path("A", "B", "TS", "C")], cycle.ids["C"])
    relative = [p["relative"] for p in found["profiles"][0]["points"]]
    assert relative[0] == pytest.approx(E["A"] + E["propene"] - E["ethylene"] - E["C"])
    assert relative[1] == pytest.approx(E["B"] - E["ethylene"] - E["C"])
    assert relative[3] == 0.0


def test_edge_difference_includes_its_species(open_client, cycle):
    # T-SPC-02: ΔX along one edge in the energy view.
    cycle.energies()
    edges = view(open_client, svp(open_client), "E")["edges"]
    assert edges[cycle.edges[("A", "B")]]["delta"] == pytest.approx(E["B"] - E["A"] - E["propene"])
    assert edges[cycle.edges[("TS", "C")]]["delta"] == pytest.approx(
        E["C"] + E["ethylene"] - E["TS"]
    )
    assert edges[cycle.edges[("B", "TS")]]["delta"] == pytest.approx(E["TS"] - E["B"])


def test_species_without_the_level_leaves_the_point_without_a_value(open_client, cycle):
    # T-SPC-03, EN-3: no fallback to another level for a species either.
    cycle.energies("A", "B", "TS", "C", "propene")
    add_energies(
        open_client, cycle.ids["ethylene"], E["ethylene"], level={**SVP, "basis": "def2TZVP"}
    )
    found = profile(open_client, [cycle.path("A", "B", "TS", "C")], cycle.ids["A"])
    last = found["profiles"][0]["points"][3]
    assert last["value"] == pytest.approx(E["C"]) and last["relative"] is None
    assert "ethylene" in last["species_message"]
    edge_value = view(open_client, svp(open_client), "E")["edges"][cycle.edges[("TS", "C")]]
    assert edge_value["delta"] is None and "ethylene" in edge_value["message"]


def test_closed_cycle_ends_at_the_reaction_energy(open_client, cycle):
    # T-SPC-04, A13: a pathway back to the catalyst shows ΔE of the overall reaction, and the
    # table lists the catalyst twice, with its two balances.
    cycle.edges[("C", "A")] = edge(open_client, cycle.ids["C"], cycle.ids["A"])["id"]
    cycle.attach(("C", "A"), "propene", "joins")
    cycle.attach(("C", "A"), "butene", "leaves")
    cycle.energies()
    path = cycle.path("A", "B", "TS", "C", "A")
    found = profile(open_client, [path], cycle.ids["A"])
    closing = found["profiles"][0]["points"][-1]
    assert closing["relative"] == pytest.approx(E["ethylene"] + E["butene"] - 2 * E["propene"])

    settings = open_client.put("/api/settings", json={"energy_unit": "kcal/mol"})
    assert settings.status_code == 200
    body = {"paths": [path], "reference_id": cycle.ids["A"], "level": svp(open_client), "type": "E"}
    table = post(open_client, "/energies/table", body, status=200)
    column = table["columns"].index("Free species")
    rows = [(r[0], r[column], r[-1]) for r in table["rows"]]
    reaction = (E["ethylene"] + E["butene"] - 2 * E["propene"]) * KCAL
    assert rows == [
        ("A", "", "0.00"),
        ("B", "− propene", f"{(E['B'] - E['A'] - E['propene']) * KCAL:.2f}"),
        ("TS", "− propene", f"{(E['TS'] - E['A'] - E['propene']) * KCAL:.2f}"),
        (
            "C",
            "− propene + ethylene",
            f"{(E['C'] + E['ethylene'] - E['A'] - E['propene']) * KCAL:.2f}",
        ),
        ("A", "− 2 × propene + ethylene + butene", f"{reaction:.2f}"),
    ]


def relative_view(client, reference_id: str) -> dict:
    return get(client, f"/energies/view?level={svp(client)}&type=E&reference={reference_id}")


def test_node_cards_carry_the_species_along_the_path(open_client, cycle):
    # T-SPC-06, D72: a node card's ΔX keeps every species that joined or left between the
    # reference and the node, not only the one on the edge into it.
    cycle.energies()
    relative = relative_view(open_client, cycle.ids["A"])["relative"]
    assert relative[cycle.ids["A"]]["value"] == 0.0
    assert relative[cycle.ids["B"]]["value"] == pytest.approx(E["B"] - E["A"] - E["propene"])
    assert relative[cycle.ids["TS"]]["value"] == pytest.approx(E["TS"] - E["A"] - E["propene"])
    c = relative[cycle.ids["C"]]
    assert c["value"] == pytest.approx(E["C"] + E["ethylene"] - E["A"] - E["propene"])
    assert [(s["label"], s["count"]) for s in c["species"]] == [("propene", -1), ("ethylene", 1)]
    assert c["joined"] is True

    # The same numbers as the profile along the pathway.
    points = profile(open_client, [cycle.path("A", "B", "TS", "C")], cycle.ids["A"])
    for point in points["profiles"][0]["points"]:
        assert relative[point["id"]]["value"] == pytest.approx(point["relative"])

    # From a reference in the middle, the points before it are balanced backwards.
    relative = relative_view(open_client, cycle.ids["C"])["relative"]
    assert relative[cycle.ids["A"]]["value"] == pytest.approx(
        E["A"] + E["propene"] - E["ethylene"] - E["C"]
    )
    assert relative[cycle.ids["TS"]]["value"] == pytest.approx(E["TS"] - E["ethylene"] - E["C"])


def test_node_cards_in_a_closed_cycle_follow_it_forward(open_client, cycle):
    # D72: with the cycle closed, the points after the reference are balanced forward from it
    # (not backwards through the closing edge), and the reference itself stays at zero.
    cycle.edges[("C", "A")] = edge(open_client, cycle.ids["C"], cycle.ids["A"])["id"]
    cycle.attach(("C", "A"), "propene", "joins")
    cycle.attach(("C", "A"), "butene", "leaves")
    cycle.energies()
    relative = relative_view(open_client, cycle.ids["A"])["relative"]
    assert relative[cycle.ids["A"]]["value"] == 0.0
    assert relative[cycle.ids["C"]]["value"] == pytest.approx(
        E["C"] + E["ethylene"] - E["A"] - E["propene"]
    )


def test_node_cards_without_a_species_value_or_a_route(open_client, cycle):
    # EN-3: no fallback for a species; a node not joined to the reference counts no species.
    cycle.energies("A", "B", "TS", "C", "propene")
    loose = node(open_client, label="loose", xyz=xyz(*RU_CH2))["id"]
    add_energies(open_client, loose, -99.0)
    relative = relative_view(open_client, cycle.ids["A"])["relative"]
    assert relative[cycle.ids["TS"]]["value"] == pytest.approx(E["TS"] - E["A"] - E["propene"])
    assert relative[cycle.ids["C"]]["value"] is None
    assert "ethylene" in relative[cycle.ids["C"]]["message"]
    assert relative[loose]["joined"] is False
    assert relative[loose]["value"] == pytest.approx(-99.0 - E["A"])


def test_pathway_apart_from_the_reference_starts_from_its_balance(open_client, cycle):
    # D72: a pathway that shares no point with one through the reference keeps the species
    # that joined or left before its first point.
    cycle.energies()
    found = profile(open_client, [cycle.path("A", "B"), cycle.path("TS", "C")], cycle.ids["A"])
    ts, c = found["profiles"][1]["points"]
    assert ts["relative"] == pytest.approx(E["TS"] - E["A"] - E["propene"])
    assert c["relative"] == pytest.approx(E["C"] + E["ethylene"] - E["A"] - E["propene"])


def test_unbalanced_edge_is_flagged(open_client):
    # T-SPC-05, W-BALANCE: a forgotten leaving ethylene, then a charge mismatch.
    cycle = Cycle(open_client)
    cycle.attach(("A", "B"), "propene", "joins")

    def warnings(pair):
        found = {t["id"]: t for t in get(open_client, "/canvas")["transitions"]}
        return found[cycle.edges[pair]]["warnings"]

    assert warnings(("A", "B")) == []
    assert warnings(("B", "TS")) == []
    [warning] = warnings(("TS", "C"))
    assert warning["code"] == "W-BALANCE" and "C2H4 more before than after" in warning["message"]
    cycle.attach(("TS", "C"), "ethylene", "leaves")
    assert warnings(("TS", "C")) == []

    patch(open_client, f"/nodes/{cycle.ids['C']}", {"charge": 1})
    [warning] = warnings(("TS", "C"))
    assert "charge 0 before, 1 after" in warning["message"]

    # No coordinates on one side: nothing to compare.
    bare = node(open_client, label="guess")["id"]
    guess = edge(open_client, cycle.ids["C"], bare)
    assert guess["warnings"] == []


def test_deleting_a_species_takes_it_off_its_transitions(open_client, cycle):
    # FR-SPC-01, INV-7: the delete preview lists the transitions it is on.
    species_id = cycle.ids["ethylene"]
    preview = get(open_client, f"/nodes/{species_id}/delete-preview")
    assert [t["id"] for t in preview["species_on"]] == [cycle.edges[("TS", "C")]]
    delete(open_client, f"/nodes/{species_id}")
    transitions = {t["id"]: t for t in get(open_client, "/canvas")["transitions"]}
    assert transitions[cycle.edges[("TS", "C")]]["species"] == []
    entry = history(open_client, cycle.edges[("TS", "C")])[0]
    assert entry["field"] == "species" and entry["new_value"] == []


def test_a_node_becomes_a_species_and_back(open_client, cycle):
    # FR-SPC-04: only a node with no transitions or group; it leaves its branch and step.
    branch = post(open_client, "/branches", {"name": "A"})
    step = post(open_client, "/steps", {"name": "S"})
    loose = node(open_client, label="styrene", branch_id=branch["id"], step_id=step["id"])
    made = put(open_client, f"/nodes/{loose['id']}/kind", {"kind": "species"})
    assert made["kind"] == "species" and made["branch_id"] is None and made["step_id"] is None
    assert put(open_client, f"/nodes/{loose['id']}/kind", {"kind": "node"})["kind"] == "node"

    put(open_client, f"/nodes/{cycle.ids['A']}/kind", {"kind": "species"}, status=422)
    put(open_client, f"/nodes/{cycle.ids['propene']}/kind", {"kind": "node"}, status=422)


def test_import_as_a_free_species(open_client):
    # FR-SPC-05: the import dialog can create a species instead of a node; a file imported
    # onto a species keeps it a species.
    plan = upload(open_client, "ethylene.out", g.opt_freq(g.WATER, g.WATER))
    created = commit(open_client, plan, kind="species", duplicate_action="new")
    species = get(open_client, f"/nodes/{created['node_id']}")
    assert species["kind"] == "species" and species["calculation_count"] == 2
    assert species["id"] in {n["id"] for n in get(open_client, "/canvas")["species"]}

    edited = put(open_client, f"/nodes/{species['id']}/geometry", {"xyz": xyz("O", "H", "H")})
    assert edited["derived"] and edited["node"]["kind"] == "species"


def test_overview_does_not_count_species_as_loose_nodes(open_client, cycle):
    # D69: species are kept off the canvas, so the "No branch" row counts only the four nodes.
    found = get(open_client, "/overview")
    loose = next(b for b in found["branches"] if b["id"] is None)
    assert loose["node_count"] == 4
