"""Create a small demo investigation for trying the app by hand and for the UI tests.

    uv run python scripts/make_demo.py <empty-or-new-folder>

One node gets a placeholder calculation record, so the derived-node rule (ID-5, D23) can be
tried without importing a file: editing its coordinates creates a new node. Real calculations
come from importing output files (try the samples in tests/fixtures/gaussian).

Below those, a small mechanism shows the phase 3 structure: four reaction steps, a trunk
branch split into rotamers A and B, their transitions, and one direct connection ("no TS").
Each mechanism node has an optimization and a frequency record at one made-up level, so the
energy view, profiles and the table (phase 4) have values to show. The geometries and the
energies are placeholders, not results.
"""

import sys
from pathlib import Path

from chembook3d.investigation import create_investigation
from chembook3d.models import Calculation, CalculationResult
from chembook3d.services import branches, levels, nodes, steps, transitions

WATER = """3
water
O 0.000000 0.000000 0.117300
H 0.000000 0.757200 -0.469200
H 0.000000 -0.757200 -0.469200
"""

ETHYLENE = """6
ethylene
C 0.000000 0.000000 0.667500
C 0.000000 0.000000 -0.667500
H 0.000000 0.922800 1.237800
H 0.000000 -0.922800 1.237800
H 0.000000 0.922800 -1.237800
H 0.000000 -0.922800 -1.237800
"""


# Made-up free energies in kcal/mol relative to T-S0; transition states carry one imaginary mode.
DEMO_ENERGIES = {
    "T-S0": 0.0,
    "A-S1": -3.2,
    "A-S2": 14.8,
    "A-S3": -8.5,
    "B-S1": -2.1,
    "B-S2": 17.3,
    "B-S3": -6.0,
}


def _energies(session, node) -> None:
    """Placeholder opt and freq records, so the energy features can be tried by hand."""
    level = levels.get_or_create(
        session,
        {"program": "Gaussian", "method": "B3LYP", "basis": "def2SVP", "dispersion": "GD3BJ"},
    )
    ts = node.role == "transition_state"
    energy = -1234.5 + DEMO_ENERGIES[node.label] / 627.5094740631
    for index, kind in enumerate(("optimization", "frequency"), start=1):
        calculation = Calculation(
            node_id=node.id,
            type=kind,
            program="Gaussian",
            level_id=level.id,
            step_index=index,
            step_count=2,
            termination="normal",
            charge=0,
            multiplicity=1,
            notes="Demo placeholder, not a real result.",
        )
        session.add(calculation)
        session.flush()
        frequencies = [-350.0, 60.0, 800.0, 1600.0] if ts else [45.0, 120.0, 800.0, 1600.0]
        calculation.result = CalculationResult(
            energy=energy,
            optimization_converged=True if kind == "optimization" else None,
            **(
                {
                    "zpe": 0.25,
                    "h_corr": 0.28,
                    "g_corr": 0.21,
                    "temperature": 298.15,
                    "pressure": 1.0,
                    "molecular_mass": 28.05,
                    "symmetry_number": 1,
                    "rotational_temperatures": [5.0, 1.2, 1.0],
                    "frequencies": frequencies,
                    "imaginary_count": 1 if ts else 0,
                }
                if kind == "frequency"
                else {}
            ),
        )


def _mechanism(session) -> None:
    names = ["alkylidene", "π-complex", "[2+2] TS", "metallacyclobutane"]
    step_ids = [steps.create(session, name).id for name in names]
    trunk = branches.create(session, {"name": "T"})
    start = nodes.create(
        session,
        {
            "label": "T-S0",
            "role": "minimum",
            "status": "done",
            "step_id": step_ids[0],
            "branch_id": trunk.id,
            "pos_x": 0.0,
            "pos_y": 330.0,
        },
        ETHYLENE,
    )
    _energies(session, start)
    rotamers = branches.split(session, start.id, [{"name": "A"}, {"name": "B"}])
    for row, branch in enumerate(rotamers):
        previous = start
        for column in range(1, 4):
            ts = column == 2
            node = nodes.create(
                session,
                {
                    "label": f"{branch.name}-S{column}",
                    "role": "transition_state" if ts else "minimum",
                    "status": "planned" if ts else "done",
                    "step_id": step_ids[column],
                    "branch_id": branch.id,
                    "pos_x": 240.0 * column,
                    "pos_y": 220.0 + 220.0 * row,
                },
                ETHYLENE if not ts else WATER,
            )
            _energies(session, node)
            transitions.create(session, {"source_id": previous.id, "target_id": node.id})
            if column == 1:
                first = node
            previous = node
        if branch.name == "A":
            # D53: a direct connection, drawn dotted and marked "no TS".
            transitions.create(session, {"source_id": first.id, "target_id": previous.id})


def make_demo(folder: Path) -> None:
    investigation = create_investigation(folder, "Demo investigation")
    try:
        with investigation.sessions.begin() as session:
            nodes.create(
                session,
                {
                    "label": "water guess",
                    "charge": 0,
                    "multiplicity": 1,
                    "notes": "No calculations yet, so coordinates edit in place.",
                },
                WATER,
            )
            computed = nodes.create(
                session,
                {
                    "label": "ethylene opt",
                    "role": "minimum",
                    "charge": 0,
                    "multiplicity": 1,
                    "status": "done",
                    "tags": ["demo"],
                    "notes": "Has a calculation, so a coordinate edit makes a derived node.",
                    "pos_x": 240.0,
                },
                ETHYLENE,
            )
            session.add(
                Calculation(
                    node_id=computed.id,
                    type="optimization",
                    program="demo",
                    notes="Placeholder record; real calculations come from importing output files.",
                )
            )
            nodes.create(session, {"label": "empty planned node", "pos_x": 480.0})
            _mechanism(session)
    finally:
        investigation.close()


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    make_demo(Path(sys.argv[1]))
    print(f"Demo investigation created in {sys.argv[1]}")
