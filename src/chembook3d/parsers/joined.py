"""A joined scan saved by the scan editor (D135), read as an output file (D137).

The file is a multi-structure xyz whose first comment line starts with `HEADER` and names the
program, version, level (as a label and as the fields the editor compared), whether the basis
set was named, a GEN basis set's fingerprints, charge, multiplicity and coordinate; every
comment line gives its structure's energy in Eh, its x and where it came from. Read for an
import it is one step of type other with route `ROUTE`, scan "relaxed", at the level its
pieces were run at, whose node takes the path's top as an xTB scan path's does (D112).
"""

import json
from dataclasses import dataclass
from typing import Any

from chembook3d.parsers import xtb
from chembook3d.parsers.common import ParsedFile, ParsedStep, Route
from chembook3d.xyz import Atom, element_of

HEADER = "chembook3d joined scan 1"
ROUTE = "joined scan"  # the route text of every calculation read from such a file


class NotJoinedScan(ValueError):
    pass


def looks_like_joined(text: str) -> bool:
    lines = text.lstrip("﻿").splitlines()
    return len(lines) > 1 and lines[1].startswith(HEADER)


def comment_fields(comment: str) -> dict[str, str]:
    """`key: value | key: value …` as the editor writes its comment lines."""
    out = {}
    for part in comment.split(" | "):
        key, sep, value = part.partition(": ")
        if sep:
            out[key.strip()] = value.strip()
    return out


def number(value: str | None) -> float | None:
    if value is None:
        return None
    try:
        return float(value.split()[0])
    except (ValueError, IndexError):
        return None


def frames(text: str) -> list[tuple[str, list[Atom]]]:
    """Every whole structure with its comment line; a structure cut off ends the list."""
    lines = text.lstrip("﻿").splitlines()
    found: list[tuple[str, list[Atom]]] = []
    i = 0
    while i < len(lines):
        if not lines[i].strip():
            i += 1
            continue
        if not lines[i].strip().isdigit():
            break
        count = int(lines[i].strip())
        if i + 2 + count > len(lines):
            break
        atoms = []
        for line in lines[i + 2 : i + 2 + count]:
            parts = line.split()
            element = element_of(parts[0]) if len(parts) >= 4 else None
            if element is None:
                return found
            try:
                atoms.append(Atom(element, *(float(v) for v in parts[1:4])))
            except ValueError:
                return found
        found.append((lines[i + 1], atoms))
        i += 2 + count
    return found


@dataclass
class Head:
    program: str
    version: str | None
    label: str
    level: dict[str, Any]  # the fields the editor compared (D135d)
    basis_named: bool
    basis_prints: dict[str, str]  # a GEN basis set's fingerprints by element (D137)
    charge: int | None
    multiplicity: int | None
    atoms: list[int]  # the coordinate, atoms from 1; empty when the file names none


def _int(value: str | None) -> int | None:
    try:
        return int(value) if value not in (None, "") else None
    except ValueError:
        return None


def head(comment: str) -> Head:
    fields = comment_fields(comment)
    try:
        level = json.loads(fields.get("level fields", "{}"))
        prints = json.loads(fields.get("basis fingerprints", "{}"))
        atoms = [int(a) for a in fields.get("coordinate", "").split()]
    except ValueError as exc:
        raise NotJoinedScan("its first comment line cannot be read") from exc
    if not isinstance(level, dict) or not isinstance(prints, dict):
        raise NotJoinedScan("its first comment line cannot be read")
    charge = _int(fields.get("charge"))
    multiplicity = _int(fields.get("multiplicity"))
    return Head(
        program=fields.get("program", level.get("program", "")),
        version=fields.get("version") or None,
        label=fields.get("level", ""),
        level=level,
        basis_named=fields.get("basis named", "yes") == "yes",
        basis_prints={str(k): str(v) for k, v in prints.items()},
        charge=charge if charge is not None else level.get("charge"),
        multiplicity=multiplicity if multiplicity is not None else level.get("multiplicity"),
        atoms=atoms if 2 <= len(atoms) <= 4 else [],
    )


def parse(text: str, name: str = "") -> ParsedFile:
    """The joined scan as one step at the level of its pieces (D137)."""
    found = frames(text)
    if len(found) < 2:
        raise NotJoinedScan("it holds fewer than two structures")
    if any([a.element for a in f[1]] != [a.element for a in found[0][1]] for f in found):
        raise NotJoinedScan("its structures hold different atoms")
    top = head(found[0][0])
    level = top.level
    basis = str(level.get("basis") or "")
    if basis.partition(":")[0] in ("GEN", "GENECP"):
        basis = basis.partition(":")[0]  # named by its fingerprints at import
    route = Route(
        text=f"{ROUTE}: {top.label}" if top.label else ROUTE,
        method=str(level.get("method") or "") or None,
        basis=basis or None,
        job_type="other",
        dispersion=str(level.get("dispersion") or "") or None,
        iops={str(k): str(v) for k, v in (level.get("iops") or {}).items()},
        solvation_model=str(level.get("solvation_model") or "") or None,
        solvent=str(level.get("solvent") or "") or None,
    )
    energies = [number(comment_fields(comment).get("energy")) for comment, _ in found]
    chosen = xtb.representative(energies)
    step = ParsedStep(
        index=1,
        route=route,
        title=name or None,
        charge=top.charge,
        multiplicity=top.multiplicity,
        geometries=[atoms for _, atoms in found],
        geometry_energies=energies,
        geometry_points=list(range(1, len(found) + 1)),
        converged_geometries=list(range(len(found))),
        scan="relaxed",
        scan_coordinates=[top.atoms] if top.atoms else [],
        node_geometry=chosen,
        scf_energy=energies[chosen],
        optimization_converged=None,
        termination="normal",
        resolved_basis=(basis or None) if top.basis_named else None,
        basis_fingerprints=top.basis_prints if basis in ("GEN", "GENECP") else {},
    )
    missing = []
    if step.scf_energy is None:
        missing.append("total energy")
    if top.charge is None or top.multiplicity is None:
        missing.append("charge and multiplicity")
    step.missing = missing
    return ParsedFile(program=top.program, version=top.version, steps=[step])
