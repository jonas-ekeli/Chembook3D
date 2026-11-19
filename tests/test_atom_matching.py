"""Atom matching between two ends numbered differently (D113, A59; T-MAT-01…04), and saving the
end renumbered (D242, A176; T-MAT-05)."""

from pathlib import Path

import numpy as np
import pytest

from chembook3d import xyz
from chembook3d.models import Calculation
from chembook3d.services import atom_matching, nodes
from chembook3d.services.records import RecordError
from tests.test_overlay import xyz_text
from tests.test_pathway import get, history, node, post

FIXTURES = Path(__file__).parent / "fixtures"


def frames(path: Path) -> list[list[list]]:
    """Every structure of a (multi-)xyz file as rows."""
    lines = path.read_text(encoding="utf-8").splitlines()
    out, i = [], 0
    while i < len(lines):
        if not lines[i].strip():
            i += 1
            continue
        count = int(lines[i])
        text = "\n".join(lines[i : i + 2 + count]) + "\n"
        out.append([[a.element, a.x, a.y, a.z] for a in xyz.parse_xyz(text)])
        i += 2 + count
    return out


def scrambled(rows, seed: int, noise: float = 0.0):
    """The rows shuffled, turned, shifted and optionally jiggled; with, for each original atom,
    its index in the result."""
    rng = np.random.default_rng(seed)
    order = rng.permutation(len(rows))
    q, _ = np.linalg.qr(rng.normal(size=(3, 3)))
    if np.linalg.det(q) < 0:
        q[:, 0] *= -1
    coords = np.array([r[1:4] for r in rows], dtype=float) @ q + np.array([4.0, -3.0, 2.0])
    coords += rng.normal(scale=noise, size=coords.shape)
    moved = [[rows[k][0], *map(float, coords[k])] for k in order]
    truth = [int(np.flatnonzero(order == i)[0]) for i in range(len(rows))]
    return moved, truth


@pytest.mark.parametrize("name", ["3", "8", "13", "14", "18"])
def test_shuffled_and_perturbed_complexes_are_matched_back(name):
    # T-MAT-01: public metal complexes (morfeus's SambVca set), shuffled, turned and moved by
    # up to ~0.1 Å per atom: every atom is matched to itself.
    start = frames(FIXTURES / "sterics" / f"{name}.xyz")[0]
    end, truth = scrambled(start, seed=int(name), noise=0.03)
    result = atom_matching.match(start, end)
    assert result.mapping == truth
    assert result.rmsd < 0.1
    assert not result.inverted and result.confident and not result.same_numbering


@pytest.mark.parametrize("name", ["4", "6", "16"])
def test_symmetric_complexes_match_as_well_as_their_own_numbering(name):
    # A symmetric ligand can be matched through its symmetry; the match then fits and keeps
    # every bond as well as the true numbering does.
    start = frames(FIXTURES / "sterics" / f"{name}.xyz")[0]
    end, truth = scrambled(start, seed=7)
    result = atom_matching.match(start, end)
    assert result.rmsd < 0.01
    assert not result.formed and not result.broken and not result.inverted


@pytest.mark.parametrize("index", [1, 10, 40, 73])
def test_conformers_are_matched_through_their_bonds(index):
    # T-MAT-02: two CREST conformers of glycerol differ by 1-1.5 Å after fitting; matching by
    # distance alone swaps hydrogens, the bond graph and handedness do not.
    ensemble = frames(FIXTURES / "crest" / "crest_conformers.xyz")
    start = ensemble[0]
    end, truth = scrambled(ensemble[index], seed=index)
    result = atom_matching.match(start, end)
    assert result.mapping == truth
    assert not result.formed and not result.broken and not result.inverted


def test_a_hydrogen_moving_to_another_atom_is_found():
    # T-MAT-03: one of a CH2's hydrogens moves onto the other arm's oxygen: one bond forms,
    # one breaks, and every other atom is still matched to itself.
    start = frames(FIXTURES / "crest" / "crest_conformers.xyz")[0]
    ends = [row[:] for row in start]
    c, h, o = 1, 10, 4  # a CH2 carbon, one of its hydrogens, the oxygen on the other CH2
    oxygen = np.array(start[o][1:4])
    away = oxygen - np.mean([start[k][1:4] for k in (2, 8)], axis=0)  # from its C and H
    target = oxygen + 0.97 * away / np.linalg.norm(away)
    ends[h] = ["H", *map(float, target)]
    assert np.linalg.norm(np.array(ends[h][1:]) - np.array(start[c][1:])) > 2.0
    end, truth = scrambled(ends, seed=3)
    result = atom_matching.match(start, end)
    assert result.mapping == truth
    assert result.formed == [(o, h)] and result.broken == [(c, h)]
    assert result.confident


def test_the_same_numbering_is_kept_and_many_changes_are_doubtful():
    start = frames(FIXTURES / "crest" / "crest_conformers.xyz")[0]
    other = frames(FIXTURES / "crest" / "crest_conformers.xyz")[5]
    result = atom_matching.match(start, other)
    assert result.same_numbering and result.mapping == list(range(len(start)))

    # Every hydrogen moved onto another atom: far more bonds change than in one step.
    rng = np.random.default_rng(0)
    swapped = [row[:] for row in start]
    hydrogens = [i for i, row in enumerate(start) if row[0] == "H"]
    heavy = [i for i, row in enumerate(start) if row[0] != "H"]
    for h in hydrogens:
        anchor = np.array(start[int(rng.choice(heavy))][1:4])
        swapped[h] = ["H", *map(float, anchor + rng.normal(size=3) * 0.6)]
    doubtful = atom_matching.match(start, swapped)
    assert not doubtful.confident
    assert any("bonds form or break" in d for d in doubtful.doubts)


def test_fixed_pairs_are_kept_and_bad_ones_refused():
    start = frames(FIXTURES / "crest" / "crest_conformers.xyz")[0]
    end, truth = scrambled(start, seed=11)
    # Pin carbon 2 (index 1) onto carbon 3's partner: a deliberate swap of the two CH2OH arms.
    forced = atom_matching.match(start, end, [(1, truth[2]), (2, truth[1])])
    assert forced.mapping[1] == truth[2] and forced.mapping[2] == truth[1]
    assert forced.fixed == sorted([(1, truth[2]), (2, truth[1])])

    with pytest.raises(RecordError, match="is C but atom"):
        atom_matching.match(start, end, [(0, truth[3])])
    with pytest.raises(RecordError, match="repeats an atom"):
        atom_matching.match(start, end, [(0, truth[0]), (0, truth[1])])
    with pytest.raises(RecordError, match="lies outside"):
        atom_matching.match(start, end, [(0, 99)])


def test_different_atoms_are_refused():
    start = frames(FIXTURES / "crest" / "crest_conformers.xyz")[0]
    with pytest.raises(RecordError, match="14 and 13 atoms"):
        atom_matching.match(start, start[:-1])
    other = [row[:] for row in start]
    other[0] = ["N", *other[0][1:]]
    with pytest.raises(RecordError, match=r"different atoms \(C 3 and 2, N 0 and 1\)"):
        atom_matching.match(start, other)


def test_atom_match_route(open_client):
    # T-MAT-04: the API gives 1-based numbers and the end renumbered onto the start.
    start = frames(FIXTURES / "crest" / "crest_conformers.xyz")[0]
    end, truth = scrambled(start, seed=2)
    a = node(open_client, label="start", xyz=xyz_text(start))
    b = node(open_client, label="end", xyz=xyz_text(end))
    response = open_client.post("/api/atom-match", json={"start_id": a["id"], "end_id": b["id"]})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["mapping"] == [j + 1 for j in truth]
    assert body["confident"] and body["doubts"] == [] and body["rmsd"] < 1e-4
    renumbered = [line.split() for line in body["renumbered_xyz"].splitlines()[2:]]
    assert [r[0] for r in renumbered] == [row[0] for row in start]
    assert np.allclose(
        [[float(v) for v in r[1:]] for r in renumbered], [r[1:] for r in start], atol=1e-4
    )

    pinned = open_client.post(
        "/api/atom-match",
        json={"start_id": a["id"], "end_id": b["id"], "pairs": [[2, truth[2] + 1]]},
    )
    assert pinned.status_code == 200 and pinned.json()["fixed"] == [[2, truth[2] + 1]]

    empty = node(open_client, label="empty")
    refused = open_client.post("/api/atom-match", json={"start_id": a["id"], "end_id": empty["id"]})
    assert refused.status_code == 422 and "no coordinates" in refused.text


def _rows(client, node_id: str) -> list[list]:
    text = get(client, f"/nodes/{node_id}")["xyz"]
    return [[a.element, a.x, a.y, a.z] for a in xyz.parse_xyz(text)]


def test_reordering_finds_the_same_atoms_in_another_order():
    # D242: rows read back from 8-decimal xyz text are still the same atoms.
    start = frames(FIXTURES / "sterics" / "3.xyz")[0]
    order = list(reversed(range(len(start))))
    back = xyz.parse_xyz(xyz.format_xyz([xyz.Atom(*start[j]) for j in order]))
    assert nodes.reordering(start, [[a.element, a.x, a.y, a.z] for a in back]) == order
    assert nodes.reordering(start, start) is None  # the same order is no reordering
    moved = [list(r) for r in start]
    moved[0][1] += 0.01
    assert nodes.reordering(start, moved) is None  # an atom moved: an edit, not a renumbering


def test_saving_a_renumbered_node_in_place(open_client):
    # T-MAT-05, D242: a node with no calculations takes the start's numbering at its own
    # coordinates; its alignment and steric atoms follow, an up-to-date steric result stays so,
    # and restoring the old coordinates from the history takes the atoms back.
    start = frames(FIXTURES / "sterics" / "3.xyz")[0]
    end, truth = scrambled(start, seed=5)
    a = node(open_client, label="start", xyz=xyz_text(start))
    b = node(open_client, label="end", xyz=xyz_text(end))
    first = [truth[i] + 1 for i in range(3)]  # b's numbers of the start's atoms 1, 2 and 3
    aligned = post(open_client, "/alignment-sets", {"name": "core", "atoms": {b["id"]: first}})
    steric = post(
        open_client,
        "/steric-profiles",
        {"name": "pocket", "atoms": {b["id"]: {"centre": [truth[0] + 1], "excluded": first[1:]}}},
    )
    (before,) = post(
        open_client,
        f"/steric-profiles/{steric['id']}/compute",
        {"node_ids": [b["id"]]},
        status=200,
    )
    old_xyz = get(open_client, f"/nodes/{b['id']}")["xyz"]

    saved = post(
        open_client, "/atom-match/save", {"start_id": a["id"], "end_id": b["id"]}, status=200
    )
    assert saved["derived"] is False and saved["node"]["id"] == b["id"]
    rows = _rows(open_client, b["id"])
    assert [r[0] for r in rows] == [r[0] for r in start]
    assert np.allclose([r[1:] for r in rows], [end[j][1:] for j in truth], atol=1e-6)
    assert get(open_client, "/alignment-sets")[0]["atoms"][b["id"]] == [1, 2, 3]
    (profile,) = get(open_client, "/steric-profiles")
    assert profile["atoms"][b["id"]]["centre"] == [1]
    assert profile["atoms"][b["id"]]["excluded"] == [2, 3]
    result = profile["results"][b["id"]]
    assert result["stale"] is None
    assert result["values"]["buried_percent"] == before["values"]["buried_percent"]
    (entry,) = [e for e in history(open_client, b["id"]) if e["field"] == "geometry"][:1]
    assert entry["action"] == "update"

    again = open_client.post("/api/atom-match/save", json={"start_id": a["id"], "end_id": b["id"]})
    assert again.status_code == 422 and "already numbered" in again.text

    # "Restore old value" in the history puts the old order back, and the atoms with it.
    restored = open_client.put(f"/api/nodes/{b['id']}/geometry", json={"xyz": old_xyz})
    assert restored.status_code == 200 and restored.json()["derived"] is False
    assert get(open_client, "/alignment-sets")[0]["atoms"][b["id"]] == first
    assert aligned["id"] == get(open_client, "/alignment-sets")[0]["id"]


def test_saving_a_renumbered_node_with_calculations_derives_one(open_client):
    # T-MAT-05, D242, ID-5: the node and its calculations stay; a new node has the new order.
    start = frames(FIXTURES / "crest" / "crest_conformers.xyz")[0]
    end, truth = scrambled(start, seed=3)
    a = node(open_client, label="start", xyz=xyz_text(start))
    b = node(open_client, label="end", xyz=xyz_text(end))
    with open_client.app.state.investigation.sessions.begin() as session:
        session.add(Calculation(node_id=b["id"], type="single_point", program="Gaussian"))
    pairs = [[1, truth[0] + 1]]
    saved = post(
        open_client,
        "/atom-match/save",
        {"start_id": a["id"], "end_id": b["id"], "pairs": pairs},
        status=200,
    )
    made = saved["node"]
    assert saved["derived"] is True and made["id"] != b["id"]
    assert made["label"] == "end (numbered as start)"
    assert made["derived_from_id"] == b["id"] and made["status"] == "planned"
    assert [r[0] for r in _rows(open_client, made["id"])] == [r[0] for r in start]
    assert np.allclose(
        [r[1:] for r in _rows(open_client, b["id"])], [r[1:] for r in end], atol=1e-6
    )
