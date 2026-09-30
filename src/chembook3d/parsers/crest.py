"""CREST conformer ensemble reader (FR-IMP-10, docs/spec/05 §3).

A CREST ensemble (`crest_conformers.xyz`, also `crest_rotamers.xyz` and `crest_best.xyz`) is a
multi-structure xyz file whose comment lines start with the conformer's energy in hartree. The
ensemble does not say which method made those energies, nor the charge and multiplicity; the
import preview asks for them (A16).
"""

from dataclasses import dataclass

from chembook3d.xyz import Atom, element_of

PROGRAM = "CREST"


class NotCrestEnsemble(ValueError):
    pass


@dataclass
class Conformer:
    index: int  # 1-based position in the file
    energy: float  # hartree, as printed
    atoms: list[Atom]


@dataclass
class Ensemble:
    program: str
    conformers: list[Conformer]

    @property
    def atom_count(self) -> int:
        return len(self.conformers[0].atoms) if self.conformers else 0


def _energy(comment: str) -> float | None:
    first = comment.split()[0] if comment.split() else ""
    try:
        return float(first)
    except ValueError:
        return None


def looks_like_ensemble(text: str) -> bool:
    lines = text.lstrip("﻿").splitlines()
    return len(lines) >= 3 and lines[0].strip().isdigit() and _energy(lines[1]) is not None


def parse(text: str) -> Ensemble:
    if not looks_like_ensemble(text):
        raise NotCrestEnsemble("not a CREST conformer ensemble")
    lines = text.lstrip("﻿").splitlines()
    conformers: list[Conformer] = []
    i = 0
    while i < len(lines):
        if not lines[i].strip():
            i += 1
            continue
        header = lines[i].strip()
        if not header.isdigit() or i + 1 >= len(lines):
            raise NotCrestEnsemble(f"line {i + 1}: expected an atom count")
        count = int(header)
        energy = _energy(lines[i + 1])
        if energy is None:
            raise NotCrestEnsemble(f"line {i + 2}: expected the conformer's energy")
        atoms = []
        for number in range(i + 2, i + 2 + count):
            parts = lines[number].split() if number < len(lines) else []
            element = element_of(parts[0]) if len(parts) >= 4 else None
            if element is None:
                raise NotCrestEnsemble(f"line {number + 1}: expected an atom")
            atoms.append(Atom(element, *(float(v) for v in parts[1:4])))
        if conformers and [a.element for a in atoms] != [a.element for a in conformers[0].atoms]:
            raise NotCrestEnsemble(f"conformer {len(conformers) + 1} has different atoms")
        conformers.append(Conformer(len(conformers) + 1, energy, atoms))
        i += 2 + count
    return Ensemble(program=PROGRAM, conformers=conformers)
