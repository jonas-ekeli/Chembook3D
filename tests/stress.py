"""A synthetic stress investigation (NFR-PERF-01, 07 "stress case"): 500 nodes and 1,000 edges.

    uv run python -m tests.stress <empty-or-new-folder> [nodes]

Ten branches across ten reaction steps, each node with an optimization and a frequency job at
one level and every second one with a single point at another, about 60 atoms each, and a free
species joining on every tenth edge. The edges run along each branch, across to the next branch,
and skip one node, until there are twice as many edges as nodes. The geometries and energies are
made up; the build writes the records directly, as `scripts/make_demo.py` does, so it is quick.
`tests/test_stress.py` times the routes the canvas loads on it, and
`frontend/e2e/stress.spec.ts` times the canvas drawing it.
"""

import math
import sys
from pathlib import Path

from chembook3d.investigation import create_investigation
from chembook3d.models import Calculation, CalculationResult
from chembook3d.services import branches, levels, nodes, species, steps, transitions

BRANCHES = 10
HARTREE_PER_KCAL = 1 / 627.5094740631
OPT_LEVEL = {"program": "Gaussian", "method": "B3LYP", "basis": "def2SVP", "dispersion": "GD3BJ"}
SP_LEVEL = {"program": "ORCA", "method": "DLPNO-CCSD(T)", "basis": "def2-TZVP"}


def molecule(seed: int, carbons: int = 20) -> str:
    """A made-up chain of `carbons` carbons with two hydrogens on each (60 atoms by default)."""
    lines = [str(3 * carbons), f"stress {seed}"]
    for k in range(carbons):
        x, y = 1.54 * k, 0.4 * math.sin(k + seed)
        lines.append(f"C {x:.6f} {y:.6f} 0.000000")
        lines.append(f"H {x:.6f} {y + 0.9:.6f} 0.700000")
        lines.append(f"H {x:.6f} {y + 0.9:.6f} -0.700000")
    return "\n".join(lines) + "\n"


def _calculations(session, node, energy_kcal: float, ts: bool, single_point: bool) -> None:
    opt = levels.get_or_create(session, OPT_LEVEL)
    atoms = len(node.geometry or [])
    frequencies = [30.0 + 25.0 * k for k in range(3 * atoms - 6)]
    if ts:
        frequencies[0] = -400.0
    energy = -1234.5 + energy_kcal * HARTREE_PER_KCAL
    jobs = [("optimization", opt), ("frequency", opt)]
    if single_point:
        jobs.append(("single_point", levels.get_or_create(session, SP_LEVEL)))
    for index, (kind, level) in enumerate(jobs, start=1):
        calculation = Calculation(
            node_id=node.id,
            type=kind,
            program=level.program,
            level_id=level.id,
            step_index=index,
            step_count=len(jobs),
            termination="normal",
            charge=0,
            multiplicity=1,
            geometry=node.geometry,
        )
        session.add(calculation)
        session.flush()
        extra = {}
        if kind == "frequency":
            extra = {
                "zpe": 0.25,
                "h_corr": 0.28,
                "g_corr": 0.21,
                "temperature": 298.15,
                "pressure": 1.0,
                "molecular_mass": 280.5,
                "symmetry_number": 1,
                "rotational_temperatures": [0.05, 0.01, 0.01],
                "frequencies": frequencies,
                "imaginary_count": 1 if ts else 0,
            }
        if kind == "optimization":
            extra = {"optimization_converged": True}
        offset = -0.4 if kind == "single_point" else 0.0
        calculation.result = CalculationResult(energy=energy + offset, **extra)


def make_stress(folder: Path, count: int = 500) -> None:
    """`count` nodes (a multiple of ten) and twice as many edges in a new investigation."""
    per_branch = count // BRANCHES
    investigation = create_investigation(folder, f"Stress {count}")
    try:
        with investigation.sessions.begin() as session:
            step_ids = [steps.create(session, f"Step {k + 1}").id for k in range(BRANCHES)]
            water = nodes.create(
                session,
                {"kind": "species", "label": "H2O", "charge": 0, "multiplicity": 1},
                "3\nwater\nO 0 0 0.1173\nH 0 0.7572 -0.4692\nH 0 -0.7572 -0.4692\n",
            )
            _calculations(session, water, 0.0, ts=False, single_point=True)
            grid: list[list[str]] = []
            for row in range(BRANCHES):
                branch = branches.create(session, {"name": chr(ord("A") + row)})
                ids = []
                for column in range(per_branch):
                    ts = column % 2 == 1
                    node = nodes.create(
                        session,
                        {
                            "label": f"{branch.name}-{column + 1}",
                            "role": "transition_state" if ts else "minimum",
                            "status": "done",
                            "charge": 0,
                            "multiplicity": 1,
                            "step_id": step_ids[column * BRANCHES // per_branch],
                            "branch_id": branch.id,
                            "pos_x": 240.0 * column,
                            "pos_y": 220.0 * row,
                        },
                        molecule(row * per_branch + column),
                    )
                    energy = (18.0 if ts else -2.0) + 0.5 * row - 0.1 * column
                    _calculations(session, node, energy, ts, single_point=column % 2 == 0)
                    ids.append(node.id)
                grid.append(ids)
            along = [(r[c], r[c + 1]) for r in grid for c in range(per_branch - 1)]
            across = [
                (grid[r][c], grid[r + 1][c]) for r in range(BRANCHES - 1) for c in range(per_branch)
            ]
            skips = [(r[c], r[c + 2]) for r in grid for c in range(per_branch - 2)]
            edges = (along + across + skips)[: 2 * count]
            for n, (source, target) in enumerate(edges):
                edge = transitions.create(session, {"source_id": source, "target_id": target})
                if n % 10 == 0:
                    species.attach(session, edge.id, water.id, "joins", 1)
    finally:
        investigation.close()


if __name__ == "__main__":
    if len(sys.argv) not in (2, 3):
        sys.exit(__doc__)
    make_stress(Path(sys.argv[1]), int(sys.argv[2]) if len(sys.argv) == 3 else 500)
    print(f"Stress investigation created in {sys.argv[1]}")
