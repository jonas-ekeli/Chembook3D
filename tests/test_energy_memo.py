"""Kept energies (D257, T-EN-59…62): a node's computed energies are kept between requests and
used only while nothing they are made of has changed; an edit recomputes only the nodes it
touched, whatever made the change (the app's records, plain SQL), and a value kept is always
the value computed afresh."""

from fastapi.encoders import jsonable_encoder
from sqlalchemy import select, text

from chembook3d.api import energies as energy_routes
from chembook3d.models import Calculation
from chembook3d.services import energies, energy_memo
from tests import gaussian_text as g
from tests.test_energies import settings
from tests.test_import import calculations, upload
from tests.test_node_geometries import DFT_END, dft_node, onto
from tests.test_pathway import patch

SP = g.single_point(DFT_END, route="#P PBE1PBE/def2TZVP SP", energy=-76.5)


def answers(client, memo: bool = True) -> dict:
    """The energy options and every energy view, from the kept values or computed afresh."""
    sessions = client.app.state.investigation.sessions
    with sessions(**({} if memo else {"info": {"energy_memo": None}})) as session:
        options = jsonable_encoder(energy_routes.energy_options(session))
        views = {
            (level["key"], kind): jsonable_encoder(
                energy_routes.energy_view(level["key"], kind, session)
            )
            for level in options["levels"]
            for kind in energies.ENERGY_TYPES
        }
    return {"options": options, "views": views}


def agree(client) -> dict:
    kept = answers(client)
    assert kept == answers(client)  # read twice: the second from what was kept
    assert kept == answers(client, memo=False)
    return kept


def counted_sorts(monkeypatch) -> list[str]:
    """The nodes whose calculations are read and sorted into levels (not kept)."""
    sorted_nodes: list[str] = []
    real = energies.NodeEnergies._sort

    def counting(self):
        sorted_nodes.append(self.node.id)
        real(self)

    monkeypatch.setattr(energies.NodeEnergies, "_sort", counting)
    return sorted_nodes


def test_kept_values_are_the_values_computed_afresh_after_each_edit(open_client):
    # T-EN-59: after each kind of edit the options and every view agree with a computation
    # that keeps nothing.
    client = open_client
    first = dft_node(client)
    second = dft_node(client, kind="node")
    agree(client)
    onto(client, upload(client, "sp.log", SP, first))
    assert agree(client) != {}
    (optimization,) = [c for c in calculations(client, first) if c["type"] == "optimization"]
    patch(client, f"/calculations/{optimization['id']}", {"basis": "6-311G(d)"})  # a level
    agree(client)
    patch(client, f"/nodes/{second}", {"role": "transition_state"})  # a check
    agree(client)
    patch(client, f"/nodes/{second}", {"charge": 1})  # another electronic state (D155)
    agree(client)
    settings(client, qh_temperature=350.0)  # the investigation's settings (D159)
    agree(client)
    with client.app.state.investigation.sessions.begin() as session:
        (sp,) = session.scalars(select(Calculation).where(Calculation.type == "single_point"))
        session.delete(sp)
    agree(client)


def test_an_edit_reads_only_the_nodes_it_touched(open_client, monkeypatch):
    # T-EN-60: a rename or a move reads no calculations again; a node's own edit reads that
    # node's; a level or a setting reads every node's.
    client = open_client
    nodes = [dft_node(client, kind="node") for _ in range(3)]
    sorts = counted_sorts(monkeypatch)
    answers(client)
    assert sorted(set(sorts)) == sorted(nodes)
    sorts.clear()
    answers(client)
    assert sorts == []
    patch(client, f"/nodes/{nodes[0]}", {"label": "renamed", "pos_x": 40.0, "notes": "moved"})
    sorts.clear()  # the node's own panel reads its calculations, kept or not
    answers(client)
    assert sorts == []
    patch(client, f"/nodes/{nodes[1]}", {"role": "transition_state"})
    sorts.clear()
    answers(client)
    assert set(sorts) == {nodes[1]}
    sorts.clear()
    settings(client, qh_cutoff=50.0)
    sorts.clear()
    answers(client)
    assert set(sorts) == set(nodes)


def test_writes_from_plain_sql_and_rolled_back_ones(open_client):
    # T-EN-61: a change made with plain SQL is seen like any other; a write rolled back
    # changes nothing kept.
    client = open_client
    node = dft_node(client, kind="node")
    before = agree(client)
    sessions = client.app.state.investigation.sessions
    with sessions() as session:
        session.execute(text("UPDATE calculation_results SET energy = energy - 0.01"))
        session.rollback()
    assert agree(client) == before
    with sessions.begin() as session:
        session.execute(text("UPDATE calculation_results SET energy = energy - 0.01"))
    after = agree(client)
    assert after != before
    key = next(iter(after["views"]))
    old, new = before["views"][key]["values"][node], after["views"][key]["values"][node]
    assert round(old["value"] - new["value"], 6) == 0.01


def test_a_session_begun_before_a_change_keeps_nothing_across_it(open_client):
    # T-EN-62: what a session read may predate a change that lands while it runs, so it
    # neither keeps its values past that change nor is given values kept before it.
    client = open_client
    node = dft_node(client, kind="node")
    sessions = client.app.state.investigation.sessions
    kept = answers(client)
    with sessions() as early:
        early.execute(text("SELECT 1"))  # begun: the count it may have read at
        with sessions.begin() as writer:
            writer.execute(text("UPDATE calculation_results SET energy = energy + 0.02"))
        memo = energy_memo.memo_of(early)
        assert memo is not None
        settings_key = next(iter(memo._entries))[1]
        entry = memo.entry(early, node, settings_key)
        assert entry is not None and entry.found == {} and entry.values == {}
        assert memo._entries.get((node, settings_key)) is not entry  # not kept
    fresh = agree(client)
    assert fresh != kept
