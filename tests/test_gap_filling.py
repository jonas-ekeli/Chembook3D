"""Gaps in a mechanism built from a folder, found and filled with MACE guesses (D265, A184,
A185, FR-PANEL-10, FR-MCP-24): T-MCP-50 (the holes of each kind, and none in a complete
mechanism) and T-PANEL-18 (a filled node planned, "MACE guess", MACE level only, its energy
apart from DFT levels; its edges low and "Filled by Claude"; at most 10 holes a run).

Synthetic structures only: the two-carbon TS of the mode-follow tests (a Gaussian frequency job
whose imaginary mode stretches C1–C2) with its two wells, ethane against acetylene and two H2,
and a made-up RuCl2 for a cycle. MACE is the tests' stand-in `mace-stub`.
"""

import json
import time
import typing

import pytest
from fastapi.testclient import TestClient

from chembook3d import local_jobs
from chembook3d.api import proposals as proposals_api
from chembook3d.app import create_app
from chembook3d.mcp_tools import BY_NAME, PANEL_ALLOWED, READ_ONLY_TOOLS, input_schema
from chembook3d.services import gaps
from tests.test_local_follow_mode import TS, follow, import_ts, shifted, xyz_of
from tests.test_local_jobs import (  # noqa: F401 - the fixtures
    finished,
    no_worker_left,
    stub_tools,
    wait_for,
)
from tests.test_local_mace import stub_mace  # noqa: F401 - the fixture
from tests.test_mechanism_tools import ETHANE, ETHYLENE
from tests.test_proposals import APP, CLAUDE, ON, ORIGIN, _ok, _playground

ACETYLENE_H2 = [
    ["C", -0.6, 0.0, 0.0],
    ["C", 0.6, 0.0, 0.0],
    ["H", -1.66, 0.0, 0.0],
    ["H", 1.66, 0.0, 0.0],
    ["H", 0.0, 5.0, 0.0],
    ["H", 0.74, 5.0, 0.0],
    ["H", 0.0, -5.0, 0.0],
    ["H", 0.74, -5.0, 0.0],
]
CATALYST = [["Ru", 0.0, 0.0, 0.0], ["Cl", 2.3, 0.0, 0.0], ["Cl", -2.3, 0.0, 0.0]]
CATALYST_H2 = [*CATALYST, ["H", 0.0, 1.6, 0.0], ["H", 0.0, -1.6, 0.0]]


@pytest.fixture
def page():
    with TestClient(create_app(), base_url=APP, client=("127.0.0.1", 50000)) as test_client:
        yield test_client


def _xyz(rows) -> str:
    return f"{len(rows)}\n\n" + "\n".join(f"{r[0]} {r[1]} {r[2]} {r[3]}" for r in rows) + "\n"


def _node(client, label, rows=None, atoms=None, **fields) -> str:
    text = xyz_of(atoms) if atoms is not None else _xyz(rows)
    made = client.post("/api/nodes", json={"label": label, "xyz": text, **fields}, headers=CLAUDE)
    assert made.status_code == 201, made.text
    return made.json()["id"]


def _edge(client, source, target) -> str:
    made = client.post("/api/transitions", json={"source_id": source, "target_id": target})
    assert made.status_code in (200, 201), made.text
    return made.json()["id"]


def _gaps(client) -> dict:
    return _ok(client.get("/api/mechanism/gaps", headers=CLAUDE))


def _holes(found, kind) -> list[dict]:
    return [h for h in found["holes"] if h["kind"] == kind]


def _ids(hole) -> set[str]:
    return {n["id"] for n in hole["nodes"]}


def _propose(client, edges=(), **more):
    return client.post(
        "/api/mechanism/propose", json={"edges": list(edges), **more}, headers=CLAUDE
    )


def test_holes_of_each_kind_are_found_and_none_in_a_complete_mechanism(page, tmp_path):
    """T-MCP-50: a TS with both sides and nothing else has no hole; a TS left without its
    product side (only its mode known), two minima one bond apart joined without a TS, two
    six bonds apart, an edge that does not balance and a chain of steps from a ruthenium
    complex that ends with H2 more are each a hole with its bonds or difference and a fill;
    ids are stable; nothing is written."""
    _playground(page, tmp_path)
    ts, _ = import_ts(page)
    m1 = _node(page, "M1", atoms=shifted(-0.5), role="minimum")
    m2 = _node(page, "M2", atoms=shifted(0.5), role="minimum")
    _edge(page, m1, ts)
    back = _edge(page, ts, m2)
    found = _gaps(page)
    assert found["count"] == 0 and found["holes"] == [] and "No holes" in found["summary"]
    assert found["filled"] == {"count": 0, "limit": 10, "ids": []}

    # (i) the TS's product side: no IRC and no mode-follow, so only its mode is known.
    assert page.delete(f"/api/transitions/{back}").status_code in (200, 204)
    p1 = _node(page, "P1", atoms=shifted(-0.5), role="minimum")
    p2 = _node(page, "P2", atoms=shifted(0.5), role="minimum")
    _edge(page, p1, p2)  # (ii) one bond apart, no TS
    q1 = _node(page, "Q1", ETHANE, role="minimum")
    q2 = _node(page, "Q2", ACETYLENE_H2, role="minimum")
    _edge(page, q1, q2)  # (iii) six bonds apart
    u1 = _node(page, "U1", ETHYLENE, role="minimum")
    u2 = _node(page, "U2", ETHANE, role="minimum")
    _edge(page, u1, u2)  # (iv) H2 more after
    cat = _node(page, "Cat", CATALYST, role="minimum")
    last = _node(page, "Cat-H2", CATALYST_H2, role="minimum")
    start = _ok(page.post("/api/steps", json={"name": "Start"}))["id"]
    end = _ok(page.post("/api/steps", json={"name": "End"}))["id"]
    _ok(page.patch(f"/api/nodes/{cat}", json={"step_id": start}))
    _ok(page.patch(f"/api/nodes/{last}", json={"step_id": end}))  # (v) H2 never released
    before = len(page.get("/api/history").json())

    found = _gaps(page)
    kinds = [h["kind"] for h in found["holes"]]
    # A195: most useful first across kinds: one TS guess between minima an edge joins, a
    # species an edge lacks, then a TS never followed, then the holes that need a choice.
    assert kinds[:3] == ["missing_ts", "unbalanced", "ts_end"]
    assert set(kinds[3:]) == {"missing_intermediate", "cycle"}
    assert found["weak"] == {"count": 0, "why": {}, "holes": []}
    (end_hole,) = _holes(found, "ts_end")
    assert _ids(end_hole) == {ts} and "product side" in end_hole["what"]
    assert "Only that is known" in end_hole["what"]
    assert end_hole["bonds"]["along_mode"] == ["C1–C2"]
    assert "start_local_follow_mode" in end_hole["fill"]
    (missing,) = _holes(found, "missing_ts")
    assert _ids(missing) == {p1, p2}
    assert missing["bonds"]["formed"] + missing["bonds"]["broken"] == ["C1–C2"]
    assert "start_local_ts_guess" in missing["fill"]
    middle = {frozenset(_ids(h)): h for h in _holes(found, "missing_intermediate")}
    far = middle[frozenset({q1, q2})]
    assert len(far["bonds"]["formed"]) + len(far["bonds"]["broken"]) >= 4
    assert "build_structure" in far["fill"] and "D263h" in far["fill"]
    steps = middle[frozenset({cat, last})]  # the steps' order links them, nothing joins them
    assert (
        "follow each other" in steps["what"] and steps["difference"] == "H2 more after than before"
    )
    (unbalanced,) = _holes(found, "unbalanced")
    assert _ids(unbalanced) == {u1, u2} and unbalanced["difference"] == "H2 more after than before"
    assert "SMILES" in unbalanced["fill"]
    (cycle,) = _holes(found, "cycle")
    assert _ids(cycle) == {cat, last} and "not given back" in cycle["what"]
    assert "release of H2" in cycle["fill"]
    assert found["count"] == len(found["holes"]) == 6 and found["more"] == 0
    assert all(gaps._HOLE_ID.fullmatch(h["id"]) for h in found["holes"])
    assert [h["id"] for h in _gaps(page)["holes"]] == [h["id"] for h in found["holes"]]
    assert len(page.get("/api/history").json()) == before  # nothing written


@pytest.mark.usefixtures("stub_tools", "stub_mace")
def test_a_filled_gap_is_a_planned_mace_guess_with_low_edges_and_ten_a_run(page, tmp_path):
    """T-PANEL-18: a TS guess refined with MACE fills the missing TS: it becomes planned,
    "(MACE guess)" and tagged, with the hole in its notes; its edges are low and "Filled by
    Claude (hole …)"; it has energies at the MACE level only, never in a DFT level's view;
    a node with a DFT result, a high filled edge or an edge "filled by Claude" without its
    hole is refused; the 11th hole of a run is refused, and a run starts again when Work on
    its own is turned on."""
    _playground(page, tmp_path)
    dft_ts, _ = import_ts(page)
    p1 = _node(page, "P1", atoms=shifted(-0.5), role="minimum")
    p2 = _node(page, "P2", atoms=shifted(0.5), role="minimum")
    _edge(page, p1, p2)
    (hole,) = _holes(_gaps(page), "missing_ts")

    guess = _node(page, "TS guess P1 to P2", atoms=shifted(0.0), role="transition_state")
    job = _ok(page.post("/api/local/jobs", json={"node_id": guess, "method": "mace-stub"}))
    done = wait_for(page, job["id"], finished, timeout=600)
    assert done["status"] == "converged", done
    assert page.post(f"/api/local/jobs/{job['id']}/import").status_code == 200

    edges = [
        {
            "source_id": p1,
            "target_id": guess,
            "confidence": "low",
            "evidence": "x",
            "hole": hole["id"],
        },
        {
            "source_id": guess,
            "target_id": p2,
            "confidence": "low",
            "evidence": "filled by Claude: MACE TS guess (D238)",
            "hole": hole["id"],
        },
    ]
    # Refused, and nothing written: a high filled edge; a node holding a DFT result; an edge
    # "filled by Claude" that names no hole.
    high = [{**edges[0], "confidence": "high"}]
    refused = _propose(page, high)
    assert refused.status_code == 422 and "low confidence" in refused.text
    refused = _propose(page, edges, filled=[{"hole": hole["id"], "node_ids": [dft_ts]}])
    assert refused.status_code == 422 and "MACE guesses only" in refused.text
    nameless = [{**edges[1], "hole": None}]
    refused = _propose(page, nameless)
    assert refused.status_code == 422 and "names the hole" in refused.text
    assert len(page.get("/api/transitions").json()) == 1

    out = _ok(
        _propose(
            page,
            edges,
            filled=[{"hole": hole["id"], "node_ids": [guess], "what": "MACE TS guess"}],
        )
    )
    assert [(e["confidence"], e["review"]) for e in out["edges"]] == [("low", "open")] * 2
    assert out["edges"][0]["evidence"] == f"Filled by Claude (hole {hole['id']}): x"
    assert out["edges"][1]["evidence"] == (
        f"Filled by Claude (hole {hole['id']}): MACE TS guess (D238)"
    )
    assert out["filled"] == [
        {"hole": hole["id"], "nodes": [{"id": guess, "label": "TS guess P1 to P2 (MACE guess)"}]}
    ]
    node = page.get(f"/api/nodes/{guess}").json()
    assert node["status"] == "planned" and node["label"].endswith("(MACE guess)")
    assert "MACE guess" in node["tags"]
    assert f"Filled by Claude (hole {hole['id']}): MACE TS guess" in node["notes"]
    found = _gaps(page)
    assert not _holes(found, "missing_ts") and found["filled"]["ids"] == [hole["id"]]

    # Its energy is at the MACE level only: never in the DFT level's view, nor the DFT TS in
    # the MACE level's (EN-3).
    levels = page.get("/api/energies/options").json()["levels"]
    dft = next(o["key"] for o in levels if "B3LYP" in o["label"])
    mace = next(o["key"] for o in levels if "stub" in o["label"].lower())
    at_dft = page.get(f"/api/energies/view?level={dft}&type=E").json()["values"]
    at_mace = page.get(f"/api/energies/view?level={mace}&type=E").json()["values"]
    assert at_dft[dft_ts]["value"] is not None and at_dft[guess]["value"] is None
    assert at_mace[guess]["value"] is not None and at_mace[dft_ts]["value"] is None

    # D265d: ten holes a run. Nine more (nodes built for them) make ten; the 11th is refused.
    def built(n: int) -> list[dict]:
        out = []
        for k in range(n):
            made = _node(page, f"Built {time.time_ns()} {k}", ETHYLENE, role="minimum")
            out.append({"hole": gaps.hole_id("missing_intermediate", made), "node_ids": [made]})
        return out

    assert _ok(_propose(page, filled=built(9)))
    refused = _propose(page, filled=built(1))
    assert refused.status_code == 422 and "At most 10 gaps" in refused.text
    assert "10 are filled already" in refused.text
    assert _gaps(page)["filled"]["count"] == 10

    # Work on its own turned on starts a new run: what was filled before it no longer counts.
    time.sleep(1.1)  # its start is kept to the second
    _ok(page.put("/api/claude/autonomy", json=ON, headers=ORIGIN))
    assert _gaps(page)["filled"]["count"] == 0
    assert _ok(_propose(page, filled=built(1)))
    assert _gaps(page)["filled"]["count"] == 1


def test_find_gaps_is_a_read_tool_and_propose_takes_the_fill():
    """T-MCP-50: find_gaps changes nothing, so the panel allows it; propose_mechanism takes a
    hole per edge and the filled nodes."""
    spec = BY_NAME["find_gaps"]
    assert spec.kind == "read" and spec.path == "/api/mechanism/gaps"
    assert "find_gaps" in READ_ONLY_TOOLS and "find_gaps" in PANEL_ALLOWED
    schema = input_schema(BY_NAME["propose_mechanism"])
    assert "hole" in schema["properties"]["edges"]["items"]["properties"]
    assert {"hole", "node_ids", "what"} <= set(
        schema["properties"]["filled"]["items"]["properties"]
    )
    assert "D265" in BY_NAME["propose_mechanism"].description


@pytest.mark.usefixtures("stub_tools")
def test_a_ts_end_is_read_from_its_mode_follow(page, tmp_path):
    """T-MCP-50: with a finished mode-follow, a TS's missing side is the end that matches
    nothing, with its bonds against the TS and make_node_from_end as the fill; an end that
    matches a minimum no edge joins is said too, as a link rather than a gap."""
    _playground(page, tmp_path)
    ts, _ = import_ts(page)
    bonded = _node(page, "Bonded", atoms=shifted(-0.5), role="minimum")
    _edge(page, bonded, ts)
    job = follow(page, ts)
    (hole,) = _holes(_gaps(page), "ts_end")
    assert _ids(hole) == {ts} and "matches no structure" in hole["what"]
    assert job["id"] in hole["fill"] and "make_node_from_end" in hole["fill"]
    # The + end only lengthens C1–C2, a bond in neither (as in T-LOC-51).
    assert hole["bonds"] == {"formed": [], "broken": []}

    apart = _node(page, "Apart", atoms=shifted(0.5), role="minimum")
    (hole,) = _holes(_gaps(page), "ts_end")
    assert _ids(hole) == {ts, apart} and "no edge joins them" in hole["what"]
    assert "not a filled gap" in hole["fill"]


def _hole(kind, n, tier=gaps.CERTAIN, weight=0.0) -> gaps.Hole:
    return gaps.Hole(kind, gaps.hole_id(kind, str(n)), [], "", "", tier=tier, weight=weight)


def test_the_listing_keeps_every_kind_in_view():
    """T-MCP-58 (A195): holes are ordered by usefulness across kinds, and when one kind would
    fill the listing (a ts_end per unlinked TS) each other kind keeps its first places."""
    ends = [_hole("ts_end", n, weight=n) for n in range(40)]
    late = [_hole("missing_intermediate", n, tier=gaps.CHOICE) for n in range(5)]
    one = _hole("missing_ts", 0, tier=gaps.ONE_STEP)
    ordered = sorted([*late, *ends, one], key=gaps.Hole.order)
    assert ordered[:2] == ends[:2] and ordered[40] is one
    listed = gaps._listed(ordered, gaps.MAX_LISTED)
    assert len(listed) == gaps.MAX_LISTED and listed == sorted(listed, key=gaps.Hole.order)
    assert [h.kind for h in listed].count("missing_intermediate") == gaps.MIN_PER_KIND
    assert one in listed and listed[: gaps.MAX_LISTED - 4] == ends[: gaps.MAX_LISTED - 4]
    # The route's kinds are the service's.
    hint = typing.get_type_hints(proposals_api.find_gaps)["kind"]
    literal, _ = typing.get_args(hint)  # Literal[...] | None
    assert set(typing.get_args(literal)) == {*gaps.KINDS, gaps.WEAK}


@pytest.mark.usefixtures("stub_tools")
def test_a_failed_follow_is_a_weak_hole_listed_apart(page, tmp_path):
    """T-MCP-58 (A195): the open end of a TS joined on its other side completes a step; a
    mode-follow end that returned to the TS is no missing minimum: it is left out of the holes
    and their count, listed under weak ("follow failed") with a fill that flags it, and `kind`
    weak lists it; `kind` lists one kind of the real holes."""
    _, folder = _playground(page, tmp_path)
    ts, _ = import_ts(page)
    bonded = _node(page, "Bonded", atoms=shifted(-0.5), role="minimum")
    _edge(page, bonded, ts)
    u1 = _node(page, "U1", ETHYLENE, role="minimum")
    u2 = _node(page, "U2", ETHANE, role="minimum")
    _edge(page, u1, u2)  # an unbalanced edge
    job = follow(page, ts)
    (hole,) = _holes(_gaps(page), "ts_end")
    assert "it left the TS" in hole["what"] and "make_node_from_end" in hole["fill"]
    assert "joined on its other side, so its fill completes the step" in hole["what"]

    result = local_jobs.job_dir(folder, job["id"]) / local_jobs.RESULT
    data = json.loads(result.read_text(encoding="utf-8"))
    data["follow_mode"]["ends"]["+"]["coordinates"] = [[x, y, z] for _, _, x, y, z in TS]
    result.write_text(json.dumps(data), encoding="utf-8")
    found = _gaps(page)
    assert _holes(found, "ts_end") == [] and found["by_kind"]["ts_end"] == 0
    assert found["count"] == 1 and found["holes"][0]["kind"] == "unbalanced"
    assert found["weak"]["count"] == 1
    assert found["weak"]["why"] == {"follow failed: the end returned to the TS": 1}
    (weak,) = found["weak"]["holes"]
    assert weak["id"] == hole["id"] and "returned to the TS" in weak["what"]
    assert weak["fill"].startswith("Flag it") and "make_node_from_end" not in weak["fill"]
    assert "1 weak" in found["summary"]

    listed = _ok(page.get("/api/mechanism/gaps", params={"kind": "weak"}, headers=CLAUDE))
    assert [h["id"] for h in listed["holes"]] == [hole["id"]] and listed["weak"]["holes"] == []
    only = _ok(page.get("/api/mechanism/gaps", params={"kind": "unbalanced"}, headers=CLAUDE))
    assert [h["kind"] for h in only["holes"]] == ["unbalanced"] and only["more"] == 0
    wrong = page.get("/api/mechanism/gaps", params={"kind": "nonsense"}, headers=CLAUDE)
    assert wrong.status_code == 422


def test_ends_of_several_tss_on_one_structure_are_one_fill():
    """T-MCP-58 (A195): open ends of two TSs that are the same structure (A184) say that one
    node fills both and rank before another certain fill; an end of the same TS, a weak one
    and another structure are left alone."""

    def end(ts, n, rows, weak=None):
        hole = gaps.Hole(
            "ts_end", gaps.hole_id("ts_end", ts, n), [{"id": ts, "label": ts}], "", "", rows=rows
        )
        hole.tier, hole.weak = gaps.CERTAIN, weak
        return hole

    moved = [[e, x + 0.01, y, z] for e, x, y, z in ETHANE]
    a, b = end("TS1", "+", ETHANE), end("TS2", "-", moved)
    same_ts, weak = end("TS1", "-", moved), end("TS3", "+", ETHANE, weak="follow failed")
    other = end("TS4", "+", ACETYLENE_H2)
    gaps._shared_ends([other, a, b, same_ts, weak])
    assert "“TS2”" in a.what and "one node fills 3 holes" in a.what
    assert "“TS1”" in b.what and "one node fills 3 holes" in b.what
    assert other.what == "" and weak.what == ""
    ordered = sorted([other, a, b], key=gaps.Hole.order)
    assert ordered[-1] is other
