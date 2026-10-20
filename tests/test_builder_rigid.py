"""Rigid turns of a set of atoms about an axis and lining up two vectors (D150, A95; T-BLD-30,
T-MCP-20). Public structures only."""

import numpy as np
import pytest

from chembook3d.services import builder
from chembook3d.services.builder import BuildError, Structure
from tests.test_builder_sites import palladium


def bound_ethylene() -> tuple[Structure, int, list[int]]:
    """Ethylene bound η² at 2.15 Å to the square-planar Pd of morfeus's SambVca complex 4."""
    s, pd = palladium()
    out = builder.add_molecule(s, [pd], builder.molecule_rows("ethylene"), "ethylene", 2.15)
    first = len(s.rows)
    return out.structure, pd, list(range(first, first + 6))


def line_angle(u: np.ndarray, v: np.ndarray) -> float:
    return float(np.degrees(np.arccos(min(abs(builder._unit(u) @ builder._unit(v)), 1.0))))


def test_the_sign_is_right_handed_about_first_to_second():
    # +90° about A → B (along +z) takes x to y: anticlockwise seen from B looking back at A.
    s = Structure([["C", 0, 0, 0], ["C", 0, 0, 1.5], ["H", 1.1, 0, 0]])
    done = builder.rotate_fragment(s, [2], 90.0, [[0], [1]])
    assert done.structure.coords[2] == pytest.approx([0, 1.1, 0], abs=1e-6)
    assert "anticlockwise seen from the axis's second point" in done.message
    assert done.structure.changed == [2] and done.picks == [2]


def test_a_bound_ligand_turns_rigidly_about_its_binding_axis():
    # T-BLD-30: with no axis given, ethylene turns about Pd → the middle of its C=C; the Pd–C
    # distances, the ligand's own shape and every other atom stay, the C=C turns by the angle.
    s, pd, eth = bound_ethylene()
    c1, c2 = eth[:2]
    done = builder.rotate_fragment(s, eth, 90.0)
    out = done.structure
    for c in (c1, c2):
        assert builder.measure(out.coords, [pd, c]) == pytest.approx(2.15, abs=1e-6)
    inner = np.linalg.norm(s.coords[eth][:, None] - s.coords[eth][None], axis=2)
    moved = np.linalg.norm(out.coords[eth][:, None] - out.coords[eth][None], axis=2)
    assert moved == pytest.approx(inner, abs=1e-6)
    rest = [i for i in range(len(s.rows)) if i not in eth]
    assert np.allclose(out.coords[rest], s.coords[rest])
    assert line_angle(out.coords[c2] - out.coords[c1], s.coords[c2] - s.coords[c1]) == (
        pytest.approx(90, abs=1e-4)
    )
    assert done.message.startswith(
        f"Turned atoms {c1 + 1}–{c1 + 6} (6 atoms) by 90° about Pd {pd + 1} → the middle of "
        f"C {c1 + 1}, C {c2 + 1}"
    )
    assert f"bonds to the rest kept: Pd {pd + 1}–C {c1 + 1} 2.150 Å" in done.message


def test_lining_up_a_vector_and_saying_when_the_axis_falls_short():
    # T-BLD-30: C=C perpendicular to Pd–Cl is reached exactly; parallel is not, since the C=C
    # turns in a plane square to the binding axis, so the closest angle is taken and said.
    s, pd, eth = bound_ethylene()
    c1, c2 = eth[:2]
    chlorine = next(i for i in sorted(builder.neighbours(s)[pd]) if s.elements[i] == "Cl")
    reference = s.coords[chlorine] - s.coords[pd]
    for target in (90.0, 45.0):
        done = builder.align_vectors(s, eth, [[c1], [c2]], [[pd], [chlorine]], target)
        out = done.structure
        assert line_angle(out.coords[c2] - out.coords[c1], reference) == pytest.approx(
            target, abs=0.05
        )
        assert builder.measure(out.coords, [pd, c1]) == pytest.approx(2.15, abs=1e-6)
        assert "closest" not in done.message
    short = builder.align_vectors(s, eth, [[c1], [c2]], [[pd], [chlorine]], 0.0)
    reached = line_angle(short.structure.coords[c2] - short.structure.coords[c1], reference)
    axis = (s.coords[c1] + s.coords[c2]) / 2 - s.coords[pd]
    assert reached == pytest.approx(90 - line_angle(axis, reference), abs=0.05)
    assert f"is now at {reached:.1f}°" in short.message
    assert "(the closest this axis allows to 0°)" in short.message


def test_rigid_moves_refuse_what_they_cannot_do():
    s, pd, eth = bound_ethylene()
    c1, c2 = eth[:2]
    with pytest.raises(BuildError, match="Give the axis: these atoms are bonded to 6 other"):
        builder.rotate_fragment(s, [pd], 30.0)  # the Pd's six bonds: no one axis
    with pytest.raises(BuildError, match="Every atom would move"):
        builder.rotate_fragment(s, list(range(len(s.rows))), 30.0, [[0], [1]])
    with pytest.raises(BuildError, match="among the atoms that move"):
        builder.align_vectors(s, eth, [[c1], [pd]], [[pd], [2]], 0.0)
    with pytest.raises(BuildError, match="must stay where they are"):
        builder.align_vectors(s, eth, [[c1], [c2]], [[pd], [c1]], 0.0)
    with pytest.raises(BuildError, match="on top of atom"):
        clash = Structure([["C", 0, 0, 0], ["H", 1, 0, 0], ["H", -1, 0, 0], ["C", 0, 0, 1.5]])
        builder.rotate_fragment(clash, [1], 180.0, [[0], [3]])


def test_the_steps_through_the_api(client):
    # T-MCP-20: atoms and points from 1, a point given as atoms whose middle counts; the bonds to
    # the rest are named, and a turn that brings atoms together is warned about.
    s, pd, eth = bound_ethylene()
    c1, c2 = eth[0] + 1, eth[1] + 1
    start = {"xyz": builder_xyz(s), "added_bonds": [[pd + 1, c1], [pd + 1, c2]]}
    atoms = [i + 1 for i in eth]
    turned = client.post(
        "/api/builder/run",
        json={
            **start,
            "steps": [
                {
                    "tool": "rotate_fragment",
                    "atoms": atoms,
                    "value": 30,
                    "axis": [pd + 1, [c1, c2]],
                },
                {
                    "tool": "align_vectors",
                    "vector": [c1, c2],
                    "reference": [pd + 1, 2],
                    "value": 90,
                },
            ],  # fmt: skip
        },
    )
    assert turned.status_code == 200, turned.text
    answer = turned.json()
    first, second = answer["steps"]
    assert first["message"].startswith(f"Turned atoms {c1}–{c1 + 5} (6 atoms) by 30° about")
    assert first["picked"] == atoms and "kept: Pd" in first["message"]
    assert "is now at 90.0° to" in second["message"]
    assert {n["atom"]: n["distance"] for n in answer["metals"][0]["bonded"]}[c1] == 2.15

    failed = client.post(
        "/api/builder/run",
        json={**start, "steps": [{"tool": "rotate_fragment", "atoms": [pd + 1], "value": 10}]},
    )
    assert failed.status_code == 422
    assert failed.json()["detail"].startswith("Step 1 (rotate_fragment): Give the axis")


def builder_xyz(s: Structure) -> str:
    return f"{len(s.rows)}\n\n" + "\n".join(f"{e} {x} {y} {z}" for e, x, y, z in s.rows)
