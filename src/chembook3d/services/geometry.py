"""Geometry comparison for attaching calculations (ID-7) and duplicate checks (ID-8).

Geometries are compared atom by atom in their stored order, as the RMSD after the best
rigid-body superposition (Kabsch). Two geometries with different element sequences never
match.
"""

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


def superpose(reference: Rows, moving: Rows) -> tuple[Rows, float | None]:
    """FR-3D-04: `moving` placed on `reference` by the best rotation and translation, with the
    RMSD. Without atom correspondence (different elements or order) only the centres are
    matched, and the RMSD is None."""
    p = np.array([row[1:4] for row in moving], dtype=float)
    q = np.array([row[1:4] for row in reference], dtype=float)
    p_centre, q_centre = p.mean(axis=0), q.mean(axis=0)
    if elements(reference) != elements(moving):
        placed = p - p_centre + q_centre
        rmsd = None
    else:
        a, b = p - p_centre, q - q_centre
        u, _, vt = np.linalg.svd(a.T @ b)
        d = np.sign(np.linalg.det(u @ vt)) or 1.0
        rotation = u @ np.diag([1.0, 1.0, d]) @ vt
        placed = a @ rotation + q_centre
        diff = a @ rotation - b
        rmsd = float(np.sqrt((diff * diff).sum(axis=1).mean()))
    rows = [[row[0], *map(float, xyz)] for row, xyz in zip(moving, placed, strict=True)]
    return rows, rmsd
