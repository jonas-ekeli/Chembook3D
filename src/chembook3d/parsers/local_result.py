"""The result of a calculation run on this computer (D213, FR-LOC-04).

`local_worker.py` writes `result.json` at the end of a local job: the program and method, the
charge and multiplicity, the energy and structure of every optimizer step, whether the
optimization converged, and what was held. It is read here as one optimization step, so the
import, the step movie (D101) and the pre-optimization rules (D100, D165) treat it like any
optimization output.

Levels: `tblite GFN2-xTB`, `Psi4 B97-3c`, `Psi4 r2SCAN-3c`; the 3c methods and GFN2 carry
their own basis (`levels.needs_basis`).
"""

import json

from chembook3d.parsers.common import (
    ParsedFile,
    ParsedStep,
    Route,
    add_constraint,
    coordinate_name,
)
from chembook3d.xyz import Atom

FORMAT = "chembook3d-local-result"
_LETTERS = {"distances": "B", "angles": "A", "dihedrals": "D"}


class NotLocalResult(ValueError):
    pass


def looks_like_local_result(text: str) -> bool:
    head = text.lstrip()[:400]
    return head.startswith("{") and f'"format": "{FORMAT}"' in head


def constraints_of(held: object) -> list[str]:
    """D157: what the job held, named as the other parsers name it (atoms from 1)."""
    if not isinstance(held, dict):
        return []
    found = []
    frozen = held.get("frozen") or []
    if frozen:
        found.append(f"{len(frozen)} frozen atom{'s' if len(frozen) > 1 else ''}")
    for key, letter in _LETTERS.items():
        for item in held.get(key) or []:
            atoms = [str(int(i) + 1) for i in item.get("atoms", [])]
            found.append(f"constrained {coordinate_name(atoms, letter)}")
    return found


def parse(text: str) -> ParsedFile:
    try:
        data = json.loads(text)
    except ValueError as exc:
        raise NotLocalResult(f"not a local result: {exc}") from exc
    if not isinstance(data, dict) or data.get("format") != FORMAT:
        raise NotLocalResult("not a local result")
    elements = data.get("elements") or []
    frames = data.get("frames") or []
    energies = data.get("energies") or []
    geometries = [
        [
            Atom(e, float(x), float(y), float(z))
            for e, (x, y, z) in zip(elements, frame, strict=True)
        ]
        for frame in frames
    ]
    if not geometries and data.get("coordinates"):
        geometries = [
            [
                Atom(e, float(x), float(y), float(z))
                for e, (x, y, z) in zip(elements, data["coordinates"], strict=True)
            ]
        ]
    state = data.get("state")
    method = data.get("level") or data.get("method")
    route = Route(text=f"local {data.get('method', '')} optimization", method=method, basis="")
    route.job_type = "ts_optimization" if data.get("transition") else "optimization"
    step = ParsedStep(
        index=1,
        route=route,
        charge=data.get("charge"),
        multiplicity=data.get("multiplicity"),
        geometries=geometries,
        geometry_energies=[float(e) for e in energies][: len(geometries)],
    )
    step.scf_energy = float(data["energy"]) if data.get("energy") is not None else None
    converged = state == "converged"
    step.optimization_converged = converged
    if converged and geometries:
        step.converged_geometries = [len(geometries) - 1]
    step.termination = "normal" if state in ("converged", "not_converged") else "abnormal"
    for description in constraints_of(data.get("constraints")):
        add_constraint(step, description)
    missing = []
    if not geometries:
        missing.append("geometry")
    if step.scf_energy is None:
        missing.append("total energy")
    step.missing = missing
    version = (data.get("versions") or {}).get(str(data.get("program", "")).lower())
    return ParsedFile(program=str(data.get("program") or "local"), version=version, steps=[step])
