"""Flipping a fragment: two picked atoms swap places by a 180° turn about the line from their
middle towards a third (D166, A110; T-BLD-31, T-MCP-24). Public structures only."""

import numpy as np
import pytest

from chembook3d.services import builder, smiles
from chembook3d.services.builder import BuildError, Structure
from tests.test_builder_rigid import bound_ethylene, builder_xyz


def aniline() -> tuple[Structure, int, int, list[int], list[int]]:
    """2,6-Diisopropyl-N,N-dimethylaniline: the N, the ipso C, the two ortho C and the N's
    methyl C."""
    s = Structure(smiles.build("CC(C)c1cccc(C(C)C)c1N(C)C").rows)
    graph = builder.neighbours(s)
    n = s.elements.index("N")
    ipso = next(j for j in graph[n] if len(graph[j]) == 3 and s.elements[j] == "C")
    ortho = sorted(k for k in graph[ipso] if k != n)
    methyls = sorted(j for j in graph[n] if j != ipso)
    return s, n, ipso, ortho, methyls


def inner_distances(coords: np.ndarray, atoms: list[int]) -> np.ndarray:
    return np.linalg.norm(coords[atoms][:, None] - coords[atoms][None], axis=2)


def test_a_bound_olefin_turns_end_over_end_about_the_metal():
    # T-BLD-31: ethylene's two C swap places, its Pd–C bonds and shape stay, nothing else moves,
    # and the picks stay, so Flip again puts it back.
    s, pd, eth = bound_ethylene()
    c1, c2 = eth[:2]
    done = builder.flip(s, [c1, c2, pd])
    out = done.structure.coords
    assert out[c1] == pytest.approx(s.coords[c2], abs=1e-6)
    assert out[c2] == pytest.approx(s.coords[c1], abs=1e-6)
    assert inner_distances(out, eth) == pytest.approx(inner_distances(s.coords, eth), abs=1e-6)
    rest = [i for i in range(len(s.rows)) if i not in eth]
    assert np.allclose(out[rest], s.coords[rest])
    assert done.picks == [c1, c2, pd] and done.structure.changed == eth
    assert done.message.startswith(
        f"Flipped C {c1 + 1} ↔ C {c2 + 1}: turned 6 atoms by 180° about the line from their "
        f"middle towards Pd {pd + 1}"
    )
    back = builder.flip(done.structure, done.picks)
    assert np.allclose(back.structure.coords, s.coords, atol=1e-6)


def test_an_aryl_flips_about_its_n_and_keeps_its_bond():
    # T-BLD-31: the ortho C swap, the isopropyl groups go with them, the ipso C stays on the
    # line (as near as the embedding is symmetric), so N–C(ipso) keeps its length and the N's
    # methyl groups stay.
    s, n, ipso, ortho, methyls = aniline()
    done = builder.flip(s, [*ortho, n])
    out = done.structure.coords
    assert out[ortho[0]] == pytest.approx(s.coords[ortho[1]], abs=1e-6)
    assert out[ortho[1]] == pytest.approx(s.coords[ortho[0]], abs=1e-6)
    assert np.linalg.norm(out[ipso] - out[n]) == pytest.approx(
        np.linalg.norm(s.coords[ipso] - s.coords[n]), abs=5e-4
    )
    assert np.allclose(out[[n, *methyls]], s.coords[[n, *methyls]])
    assert len(done.structure.changed) == 29  # C6H3 and two C3H7
    assert f"bonds to the rest kept: N {n + 1}–C {ipso + 1}" in done.message


def test_swapping_two_substituents_inverts_the_centre():
    # T-BLD-31: F and Cl on CHFClBr swap about the C; H and Br stay, so the centre's handedness
    # (the sign of the volume of its four bonds) turns over.
    s = Structure(smiles.build("FC(Cl)Br").rows)
    f, c, cl, br = 0, 1, 2, 3
    h = s.elements.index("H")

    def handedness(coords: np.ndarray) -> float:
        a, b, d = (coords[i] - coords[c] for i in (f, cl, br))
        return float(np.sign(np.cross(a, b) @ d))

    done = builder.flip(s, [f, cl, c])
    out = done.structure.coords
    assert np.allclose(out[[c, h, br]], s.coords[[c, h, br]])
    assert handedness(out) == -handedness(s.coords)
    assert sorted(done.structure.changed) == [f, cl]


def test_a_tied_fragment_is_refused_until_a_bond_of_the_tie_is_ignored():
    # T-BLD-31: a bond drawn from an isopropyl H to an N-methyl C ties the aryl to the rest past
    # N; the flip names the way round, and ignoring one of its bonds lets it through.
    s, n, ipso, ortho, methyls = aniline()
    graph = builder.neighbours(s)
    isopropyl = next(j for j in graph[ortho[0]] if j != ipso and s.elements[j] == "C")
    h = next(j for j in graph[isopropyl] if s.elements[j] == "H")
    tied = s.but(added=[builder._pair(h, methyls[0])])
    plan = builder.flip_plan(tied, [*ortho, n])
    assert plan.tied == [methyls[0]]
    assert plan.tie == [
        builder._pair(ortho[0], isopropyl),
        builder._pair(isopropyl, h),
        builder._pair(h, methyls[0]),
    ]
    with pytest.raises(BuildError, match=f"tied to the rest past N {n + 1} \\(through C"):
        builder.flip(tied, [*ortho, n])
    done = builder.flip(tied, [*ortho, n], [builder._pair(h, methyls[0])])
    assert done.structure.coords[ortho[0]] == pytest.approx(s.coords[ortho[1]], abs=1e-6)
    assert "; ignored " in done.message and "bonds to the rest changed" in done.message


def test_flip_refuses_what_it_cannot_do():
    s, pd, eth = bound_ethylene()
    c1, c2 = eth[:2]
    with pytest.raises(BuildError, match="Pick 3 atoms"):
        builder.flip(s, [c1, c2])
    with pytest.raises(BuildError, match="not bonded: no bond to ignore"):
        builder.flip(s, [c1, c2, pd], [(0, len(s.rows) - 1)])
    line = Structure([["C", 0, 0, 0], ["C", 1.5, 0, 0], ["H", 3, 0, 0]])
    with pytest.raises(BuildError, match="is on the line through atoms 1 and 2"):
        builder.flip(line, [0, 1, 2])


def test_describe_and_the_step_through_the_api(client):
    # T-MCP-24: three atoms picked describe what Flip would turn and the tie; the agent's step
    # takes atoms and ignored bonds from 1.
    s, n, ipso, ortho, methyls = aniline()
    graph = builder.neighbours(s)
    isopropyl = next(j for j in graph[ortho[0]] if j != ipso and s.elements[j] == "C")
    h = next(j for j in graph[isopropyl] if s.elements[j] == "H")
    added = [list(builder._pair(h, methyls[0]))]
    rows = [list(r) for r in s.rows]
    picks = [*ortho, n]
    described = client.post(
        "/api/builder/describe", json={"rows": rows, "added": added, "picks": picks}
    ).json()
    assert described["kind"] == "angle"
    assert described["flip"]["tie"][-1] == added[0] and described["flip"]["error"] is None
    freed = client.post(
        "/api/builder/describe",
        json={"rows": rows, "added": added, "picks": picks, "ignore": added},
    ).json()
    assert freed["flip"] == {"count": 29, "tie": [], "error": None}
    two = client.post("/api/builder/describe", json={"rows": rows, "picks": ortho}).json()
    assert two["flip"] is None

    start = {"xyz": builder_xyz(s), "added_bonds": [[i + 1 for i in added[0]]]}
    step = {"tool": "flip", "atoms": [i + 1 for i in picks]}
    refused = client.post("/api/builder/run", json={**start, "steps": [step]})
    assert refused.status_code == 422
    assert refused.json()["detail"].startswith("Step 1 (flip): C")
    flipped = client.post(
        "/api/builder/run",
        json={**start, "steps": [{**step, "ignore_bonds": [[i + 1 for i in added[0]]]}]},
    )
    assert flipped.status_code == 200, flipped.text
    (only,) = flipped.json()["steps"]
    assert only["message"].startswith(f"Flipped C {ortho[0] + 1} ↔ C {ortho[1] + 1}")
    assert only["picked"] == [i + 1 for i in picks]
