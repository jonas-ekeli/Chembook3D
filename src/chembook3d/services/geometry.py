"""Geometry comparison for attaching calculations (ID-7) and duplicate checks (ID-8), and the
placement of structures in an overlay (FR-3D-04, D80).

Geometries are compared atom by atom in their stored order, as the RMSD after the best
rigid-body superposition (Kabsch). Two geometries with different element sequences never
match.
"""

from dataclasses import dataclass
from typing import Any

import numpy as np

Rows = list[list[Any]]  # [[element, x, y, z], ...]


def elements(rows: Rows) -> list[str]:
    return [row[0] for row in rows]


def aligned_rmsd(a: Rows, b: Rows) -> float | None:
    """RMSD in Å after superposition, or None when the atoms differ."""
    if not a or not b or elements(a) != elements(b):
        return None
    p = np.array([row[1:4] for row in a], dtype=float)
    q = np.array([row[1:4] for row in b], dtype=float)
    p -= p.mean(axis=0)
    q -= q.mean(axis=0)
    u, _, vt = np.linalg.svd(p.T @ q)
    d = np.sign(np.linalg.det(u @ vt)) or 1.0
    rotation = u @ np.diag([1.0, 1.0, d]) @ vt
    diff = p @ rotation - q
    return float(np.sqrt((diff * diff).sum(axis=1).mean()))


def matches(a: Rows | None, b: Rows | None, tolerance: float) -> bool:
    if not a or not b:
        return False
    rmsd = aligned_rmsd(a, b)
    return rmsd is not None and rmsd <= tolerance


class AlignmentError(ValueError):
    """The atoms chosen for an overlay cannot be aligned; the message says why."""


@dataclass
class Placed:
    """One structure of an overlay, placed on the reference (FR-3D-04, D80)."""

    rows: Rows
    rmsd_atoms: float | None  # Å, over the alignment atoms
    rmsd_all: float | None  # Å, over all atoms, when they correspond one to one
    mirrored: bool
    rotated: bool  # False when only the centres were matched, or nothing was moved


def _coords(rows: Rows) -> np.ndarray:
    return np.array([row[1:4] for row in rows], dtype=float)


def _rmsd(a: np.ndarray, b: np.ndarray) -> float:
    diff = a - b
    return float(np.sqrt((diff * diff).sum(axis=1).mean()))


def _fit(moving: np.ndarray, reference: np.ndarray, allow_mirror: bool) -> tuple[np.ndarray, bool]:
    """Kabsch: the orthogonal matrix R with `moving @ R` closest to `reference` (both centred).
    A reflection is used only when mirrors are allowed and it fits clearly better, so a planar
    set of atoms, which fits as well either way, is never reported as mirrored (D80)."""
    u, _, vt = np.linalg.svd(moving.T @ reference)
    d = np.sign(np.linalg.det(u @ vt)) or 1.0
    proper = u @ np.diag([1.0, 1.0, d]) @ vt
    if not allow_mirror:
        return proper, False
    improper = u @ np.diag([1.0, 1.0, -d]) @ vt
    if _rmsd(moving @ improper, reference) < _rmsd(moving @ proper, reference) - 1e-6:
        return improper, True
    return proper, False


def _pairs(reference: Rows, moving: Rows, ref_atoms: list[int], mov_atoms: list[int]) -> None:
    if len(ref_atoms) != len(mov_atoms):
        raise AlignmentError(
            f"{len(mov_atoms)} atoms are listed, but the reference has {len(ref_atoms)}; "
            "the lists are paired in order"
        )
    for i, j in zip(ref_atoms, mov_atoms, strict=True):
        if reference[i][0] != moving[j][0]:
            raise AlignmentError(
                f"atom {j + 1} is {moving[j][0]}, but the reference atom it is paired with "
                f"({i + 1}) is {reference[i][0]}"
            )


def place(
    reference: Rows,
    moving: Rows,
    align: str = "all",
    ref_atoms: list[int] | None = None,
    mov_atoms: list[int] | None = None,
    allow_mirror: bool = False,
) -> Placed:
    """FR-3D-04, D80: `moving` placed on `reference`.

    - "all": the best rotation and translation over all atoms, when both have the same elements
      in the same order; otherwise only the centres are matched.
    - "atoms": fitted on the paired atoms (0-based, in matching order, same elements); every
      atom is then moved the same way.
    - "none": the stored coordinates, unchanged.

    `allow_mirror` lets the fit reflect as well as rotate. The RMSD over all atoms is measured
    on the structures as placed, not refitted, and only when their atoms correspond one to one.
    """
    p, q = _coords(moving), _coords(reference)
    same_atoms = elements(reference) == elements(moving)
    if align == "all" and not same_atoms:
        placed = p - p.mean(axis=0) + q.mean(axis=0)
        return Placed(_rows(moving, placed), None, None, False, False)
    if align == "all":
        ref_atoms = mov_atoms = list(range(len(reference)))
    if align in ("atoms", "all"):
        assert ref_atoms is not None and mov_atoms is not None
        _pairs(reference, moving, ref_atoms, mov_atoms)
        a, b = p[mov_atoms], q[ref_atoms]
        a_centre, b_centre = a.mean(axis=0), b.mean(axis=0)
        rotation, mirrored = _fit(a - a_centre, b - b_centre, allow_mirror)
        placed = (p - a_centre) @ rotation + b_centre
        rotated = True
    elif align == "none":
        placed, mirrored, rotated = p, False, False
    else:
        raise AlignmentError(f"unknown alignment '{align}'")
    rmsd_atoms = None
    if ref_atoms is not None and mov_atoms is not None:
        if align == "none":
            _pairs(reference, moving, ref_atoms, mov_atoms)
        rmsd_atoms = _rmsd(placed[mov_atoms], q[ref_atoms])
    rmsd_all = _rmsd(placed, q) if same_atoms else None
    return Placed(_rows(moving, placed), rmsd_atoms, rmsd_all, mirrored, rotated)


def _rows(moving: Rows, placed: np.ndarray) -> Rows:
    return [[row[0], *map(float, xyz)] for row, xyz in zip(moving, placed, strict=True)]
