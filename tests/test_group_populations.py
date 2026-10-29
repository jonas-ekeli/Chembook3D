"""Population % on a group's members (D197, FR-GRP-12, T-BR-23): the Boltzmann shares of a
group's members in the energy view, by hand, with the members left out, refused or n/a."""

import math

import pytest

from tests.test_energies import add_energies, level_key
from tests.test_equilibrium import E0, LEVEL, conformer, rt
from tests.test_pathway import get, node, patch, post
from tests.test_snapshot import data_of, export, page  # noqa: F401 (fixture)
from tests.test_species import xyz


def group(client, members: list[str], label: str = "Rotamers") -> str:
    made = post(client, "/groups/reconnect", {"member_ids": members, "label": label})["id"]
    patch(client, f"/groups/{made}", {"show_populations": True})
    return made


def view(client, energy_type: str = "E") -> dict:
    query = f"/energies/view?level={level_key(client, LEVEL)}&type={energy_type}"
    return get(client, query)["populations"]


def test_members_shares_by_hand(open_client):
    # T-BR-23: three conformers at 0, 1.0 and 2.0 kcal/mol, and the switch itself.
    ids = [conformer(open_client, name, kcal) for name, kcal in (("A", 0), ("B", 1), ("C", 2))]
    made = post(open_client, "/groups/reconnect", {"member_ids": ids, "label": "ABC"})["id"]
    assert view(open_client) == {}  # off by default
    shown = patch(open_client, f"/groups/{made}", {"show_populations": True})
    assert shown["show_populations"] is True
    found = view(open_client)[made]
    weights = [math.exp(-kcal / rt()) for kcal in (0, 1, 2)]
    expected = [100 * w / sum(weights) for w in weights]
    assert found["status"] == "ok" and found["kind"] == "conformers"
    assert (found["counted"], found["total"]) == (3, 3)
    assert [found["members"][i]["percent"] for i in ids] == pytest.approx(expected)
    assert found["temperature"] == 298.15
    assert any("not populations" in n for n in found["notes"])  # E, not G
    # The switch is a view choice: no history entry.
    entries = get(open_client, f"/history?record_id={made}")
    assert all(e["field"] != "show_populations" for e in entries)


def test_left_out_duplicate_and_missing_members(open_client):
    # T-BR-23: a rejected member, a duplicate and one with no value at the level.
    a = conformer(open_client, "A", 0.0)
    b = conformer(open_client, "B", 1.0)
    again = conformer(open_client, "A again", 0.05)
    gone = conformer(open_client, "Gone", -3.0)
    patch(open_client, f"/nodes/{gone}", {"status": "rejected"})
    empty = conformer(open_client, "Empty", None)
    made = group(open_client, [a, b, again, gone, empty])
    found = view(open_client)[made]
    assert found["status"] == "ok" and (found["counted"], found["total"]) == (2, 5)
    members = found["members"]
    share = 100 / (1 + math.exp(-1.0 / rt()))
    assert members[a]["percent"] == pytest.approx(share)
    assert members[gone] == {"percent": None, "message": "rejected, left out"}
    assert members[again]["percent"] is None and "duplicates “A”" in members[again]["message"]
    assert members[empty]["percent"] is None and members[empty]["message"]


def test_what_gives_no_shares(open_client):
    # T-BR-23: TSs mixed with minima, other atoms, other spin states, one value only.
    a = conformer(open_client, "A", 0.0)
    ts = conformer(open_client, "TS", 10.0, role="transition_state")
    mixed = group(open_client, [a, ts], "Mixed")
    found = view(open_client)[mixed]
    assert found["status"] == "refused" and "mixes transition states" in found["message"]
    assert found["members"][a]["percent"] is None

    b = conformer(open_client, "B", 1.0)
    odd = node(open_client, label="Odd", role="minimum", charge=0, xyz=xyz("Ru", "C"))["id"]
    add_energies(open_client, odd, E0)
    atoms = group(open_client, [b, odd], "Atoms")
    assert "same atoms" in view(open_client)[atoms]["message"]

    c = conformer(open_client, "C", 1.0, multiplicity=1)
    d = conformer(open_client, "D", 2.0, multiplicity=3)
    spins = group(open_client, [c, d], "Spins")
    assert "spin states" in view(open_client)[spins]["message"]

    e = conformer(open_client, "E", 0.0)
    f = conformer(open_client, "F", None)
    one = group(open_client, [e, f], "One")
    found = view(open_client)[one]
    assert found["status"] == "incomplete" and found["counted"] == 1
    assert found["members"][e]["percent"] is None
    assert found["members"][f]["message"] != found["message"]  # its own reason stays


def test_a_group_of_transition_states_shares_the_flux(open_client):
    ts = [
        conformer(open_client, name, kcal, role="transition_state")
        for name, kcal in (("TS1", 10.0), ("TS2", 10.5))
    ]
    made = group(open_client, ts, "TSs")
    found = view(open_client, "G")[made]
    assert found["kind"] == "transition_states"
    assert found["members"][ts[0]]["percent"] == pytest.approx(
        100 / (1 + math.exp(-0.5 / rt())), rel=1e-4
    )
    assert any("flux" in n for n in found["notes"])
    assert not any("not populations" in n for n in found["notes"])


def test_read_only_copy_carries_them(open_client, page):  # noqa: F811
    ids = [conformer(open_client, name, kcal) for name, kcal in (("A", 0), ("B", 1))]
    made = group(open_client, ids)
    data = data_of(export(open_client))
    shown = data["views"][f"{level_key(open_client, LEVEL)}|E"]["populations"][made]
    assert shown["members"][ids[0]]["percent"] == pytest.approx(100 / (1 + math.exp(-1.0 / rt())))
    assert next(g for g in data["canvas"]["groups"] if g["id"] == made)["show_populations"]
