"""The result of a calculation run on this computer (D213, FR-LOC-04).

`local_worker.py` writes `result.json` at the end of a local job: the program and method, the
charge and multiplicity, the energy and structure of every optimizer step, whether the
optimization converged, and what was held. It is read here as one optimization step, so the
import, the step movie (D101) and the pre-optimization rules (D100, D165) treat it like any
optimization output.

Levels: `tblite GFN2-xTB`, `Psi4 B97-3c`, `Psi4 r2SCAN-3c`; the 3c methods and GFN2 carry
their own basis (`levels.needs_basis`).

D217: a job that computed frequencies at its result (a GFN2-xTB or g-xTB TS search) adds a second
step, a frequency calculation at the same level on the final structure, as Gaussian's `opt freq`
does. Nothing is printed for it, so the molecular mass and rotational temperatures are worked
out here from the structure (most abundant isotopes); the symmetry number is taken as 1 (the
worker finds no point group), and the app computes G_qh from the frequencies (D56).

D218: a relaxed scan's result is read as Gaussian's relaxed scan is: every structure with the
point it belongs to, each point's last structure converged when its optimization was, the
scanned coordinate (`scan B(1,2)`, D157), and the node's structure the highest top of the
converged points (else the middle one), as an xTB scan's (D112).

D219: a 2D grid scan is read the same way, with `scan_grid` (the two coordinates' names, units
and values, and each point's cell), its constraint `grid scan B(1,2) × B(3,4)` (not a `scan`
one, so it is never shown on an edge as a path, D200) and the node's structure the lowest
converged point.
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
# h² / (8π² k) in K amu Å²: a rotational temperature from a moment of inertia
ROTATIONAL = 24.254367090467216
LINEAR_INERTIA = 1e-3  # amu Å²: a smallest moment below this is a linear molecule's zero


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


def rotor(atoms: list[Atom]) -> tuple[float, list[float]]:
    """The molecular mass (amu) and rotational temperatures (K) of a structure: three for a
    non-linear molecule, one for a linear one (D164)."""
    import numpy as np
    from morfeus.data import atomic_masses

    from chembook3d.local_worker import ELEMENTS

    masses = np.array([atomic_masses[ELEMENTS.index(a.element) + 1] for a in atoms], float)
    xyz = np.array([[a.x, a.y, a.z] for a in atoms], float)
    xyz -= (masses[:, None] * xyz).sum(axis=0) / masses.sum()
    tensor = -np.einsum("i,ij,ik->jk", masses, xyz, xyz)
    tensor += np.eye(3) * (masses * (xyz**2).sum(axis=1)).sum()
    moments = sorted(float(m) for m in np.linalg.eigvalsh(tensor))
    if moments[0] < LINEAR_INERTIA:
        moments = moments[1:2]
    return float(masses.sum()), [round(ROTATIONAL / m, 5) for m in moments if m > 0]


def frequency_step(data: dict, final: list[Atom], index: int) -> ParsedStep:
    """D217: the frequencies the job computed at its final structure, as a frequency step."""
    found = data["frequencies"]
    route = Route(
        text=f"local {data.get('method', '')} frequencies",
        method=found.get("level") or data.get("level") or data.get("method"),
        basis="",
    )
    route.job_type = "frequency"
    step = ParsedStep(
        index=index,
        route=route,
        charge=data.get("charge"),
        multiplicity=data.get("multiplicity"),
        geometries=[final],
    )
    energy = found.get("energy", data.get("energy"))
    step.scf_energy = float(energy) if energy is not None else None
    step.geometry_energies = [step.scf_energy]
    step.frequencies = [float(f) for f in found.get("wavenumbers") or []]
    step.normal_modes = [
        [[float(v) for v in atom] for atom in mode] for mode in found.get("modes") or []
    ]
    mass, temperatures = rotor(final)
    step.thermo = {
        "molecular_mass": round(mass, 5),
        "rotational_temperatures": temperatures,
        "symmetry_number": 1,
    }
    step.termination = "normal"
    step.missing = [] if step.scf_energy is not None else ["total energy"]
    return step


def _linspace(start: float, end: float, count: int) -> list[float]:
    if count < 2:
        return [float(start)]
    return [start + (end - start) * k / (count - 1) for k in range(count)]


def _scan(data: dict, geometries: list[list[Atom]], energies: list) -> ParsedFile:
    from chembook3d.parsers.xtb import representative

    scan = data["scan"]
    letters = {"distance": "B", "angle": "A", "dihedral": "D"}
    scanned = [([int(a) + 1 for a in c["atoms"]], letters[c["kind"]]) for c in scan["coordinates"]]
    route = Route(
        text=f"local {data.get('method', '')} relaxed scan",
        method=data.get("level") or data.get("method"),
        basis="",
    )
    route.job_type = "optimization"
    points = scan.get("points") or []
    finals = [
        p["frame"] for p in points if p.get("converged") and 0 <= p["frame"] < len(geometries)
    ]
    step = ParsedStep(
        index=1,
        route=route,
        charge=data.get("charge"),
        multiplicity=data.get("multiplicity"),
        geometries=geometries,
        geometry_energies=[float(e) for e in energies][: len(geometries)],
        geometry_points=[int(n) for n in data.get("frame_points") or []][: len(geometries)],
        converged_geometries=finals,
        scan="relaxed",
        scan_coordinates=[atoms for atoms, _ in scanned],
    )
    grid = bool(scan.get("grid"))
    if finals:
        energies_at = [step.geometry_energies[i] for i in finals]
        if grid:
            known = [n for n, e in enumerate(energies_at) if e is not None]
            chosen = min(known, key=lambda n: energies_at[n]) if known else 0
        else:
            chosen = representative(energies_at)
        step.node_geometry = finals[chosen]
        step.scf_energy = step.geometry_energies[finals[chosen]]
    names = [coordinate_name([str(a) for a in atoms], letter) for atoms, letter in scanned]
    if grid:
        units = {"distance": "Å", "angle": "°", "dihedral": "°"}
        step.scan_grid = {
            "coordinates": [
                {
                    "name": name,
                    "unit": units[c["kind"]],
                    "values": [
                        round(float(v), 6)
                        for v in _linspace(c["start"], c["end"], int(c["points"]))
                    ],
                }
                for name, c in zip(names, scan["coordinates"], strict=True)
            ],
            "cells": [list(p.get("cell") or [0, 0]) for p in points],
        }
    state = data.get("state")
    step.optimization_converged = state == "converged"
    step.termination = "normal" if state in ("converged", "not_converged") else "abnormal"
    if grid:
        add_constraint(step, f"grid scan {' × '.join(names)}")
    else:
        for name in names:
            add_constraint(step, f"scan {name}")
    for description in constraints_of(data.get("constraints")):
        add_constraint(step, description)
    step.missing = [] if step.scf_energy is not None else ["total energy"]
    if not geometries:
        step.missing.append("geometry")
    version = (data.get("versions") or {}).get(str(data.get("program", "")).lower())
    return ParsedFile(program=str(data.get("program") or "local"), version=version, steps=[step])


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
    if data.get("scan"):
        return _scan(data, geometries, energies)
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
    steps = [step]
    if data.get("frequencies") and geometries and converged:
        steps.append(frequency_step(data, geometries[-1], 2))
    version = (data.get("versions") or {}).get(str(data.get("program", "")).lower())
    return ParsedFile(program=str(data.get("program") or "local"), version=version, steps=steps)
