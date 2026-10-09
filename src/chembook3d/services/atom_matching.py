"""Atom matching between two structures of the same atoms numbered differently (D113, A59).

The ends of a reaction step often come from different jobs, so the same atom can carry another
number in each. A scan path from one end to the other (D112) needs the end's atoms in the
start's order. `match` finds that order without changing either structure:

1. Bond graphs of both ends by the 3D view's rule (`chem.ts` `bonds`: 1.2 × the sum of the
   covalent radii).
2. Atoms are labelled by their element and, round by round, their neighbours' labels
   (Weisfeiler–Lehman). An atom whose label is unique in both ends is an anchor: its
   surroundings up to that many bonds are the same in both, so it is the same atom.
3. The match grows bond by bond from the anchors: a matched atom's unmatched neighbours are
   paired by their surroundings and, among equals (a methyl's hydrogens, an isopropyl's
   methyls), by position once the end is fitted on the start (Kabsch, never mirrored). This
   follows the parts whose bonds stay however far they turned between the ends.
4. Atoms the growth does not reach (around bonds that form or break) are paired by the
   Hungarian assignment on the squared distance plus penalties for differing surroundings and
   for neighbours that would not stay neighbours, refitted and paired again until stable.
5. This runs from several starting orientations; the match changing the fewest bonds and
   turning no centre into its mirror image wins, then the one fitting best. A centre turned
   inside out by swapping two like neighbours is swapped back. When the ends already share
   their numbering and it changes no more than the best match, it is kept as it is.

A match is doubtful when it turns a centre into its mirror image, many bonds change, or the
atoms whose bonds stay lie far apart after fitting; the review view then opens by itself.
Pairs the user fixed by hand are kept as given. Atom numbers outside this module are 1-based,
of the full structure (D75); inside, 0-based.
"""

import itertools
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

import numpy as np
from scipy.optimize import linear_sum_assignment
from scipy.spatial.transform import Rotation
from sqlalchemy.orm import Session

from chembook3d.models import Node
from chembook3d.services.geometry import Rows, _coords, _fit, _rmsd
from chembook3d.services.records import RecordError, get

# Covalent radii in Å as in frontend/src/chem.ts (Cordero et al. 2008); others DEFAULT_RADIUS.
RADII = {
    "H": 0.31, "B": 0.84, "C": 0.76, "N": 0.71, "O": 0.66, "F": 0.57, "Si": 1.11, "P": 1.07,
    "S": 1.05, "Cl": 1.02, "Br": 1.2, "I": 1.39, "Li": 1.28, "Na": 1.66, "K": 2.03, "Mg": 1.41,
    "Al": 1.21, "Fe": 1.32, "Co": 1.26, "Ni": 1.24, "Cu": 1.32, "Zn": 1.22, "Ru": 1.46,
    "Rh": 1.42, "Pd": 1.39, "Ag": 1.45, "Mo": 1.54, "W": 1.62, "Os": 1.44, "Ir": 1.41,
    "Pt": 1.36, "Au": 1.36, "Ti": 1.6, "Zr": 1.75, "Cr": 1.39, "Mn": 1.39, "Sn": 1.39,
}  # fmt: skip
DEFAULT_RADIUS = 1.5
BOND_SCALE = 1.2
MIN_DISTANCE = 0.4  # Å; closer atoms are not bonded but overlapping

ROUNDS = 4  # label rounds: an anchor's surroundings agree up to this many bonds out
NEIGHBOUR_PENALTY = 5.0  # Å² per neighbour that would not stay a neighbour
LABEL_PENALTY = 2.0  # Å² per label round (1 and 2) in which two atoms differ
GROW_LABEL_WEIGHT = 100.0  # while growing, differing surroundings outweigh any distance
MAX_ITERATIONS = 30
PLANAR = 0.15  # |normalised triple product| below this: a flat centre has no handedness
GOOD_ENOUGH = 0.25  # Å: a match changing no bond and fitting this well ends the search

# A match is doubtful beyond these (D113).
MAX_BOND_CHANGES = 4
MAX_RMSD = 2.0  # Å, over the atoms whose bonds stay, fitted on them


@dataclass
class Match:
    mapping: list[int]  # mapping[i]: the end's atom matched to the start's atom i
    rmsd: float  # Å over all atoms, the end fitted on the start in this numbering
    formed: list[tuple[int, int]]  # start's numbering: bonded at the end only
    broken: list[tuple[int, int]]  # bonded at the start only
    inverted: list[int]  # start atoms whose neighbours sit the other way round at the end
    fixed: list[tuple[int, int]]  # (start atom, end atom) kept as the user gave them
    same_numbering: bool  # the ends already shared their numbering
    doubts: list[str] = field(default_factory=list)  # why the match should be checked

    @property
    def confident(self) -> bool:
        return not self.doubts


def bonds(coords: np.ndarray, elements: list[str]) -> np.ndarray:
    """Adjacency matrix (bool) by the 3D view's bond rule."""
    radii = np.array([RADII.get(e, DEFAULT_RADIUS) for e in elements])
    limit = BOND_SCALE * (radii[:, None] + radii[None, :])
    diff = coords[:, None, :] - coords[None, :, :]
    d2 = (diff * diff).sum(axis=2)
    adjacency = (d2 < limit * limit) & (d2 > MIN_DISTANCE * MIN_DISTANCE)
    np.fill_diagonal(adjacency, False)
    return adjacency


def _labels(elements: list[str], adjacency: np.ndarray, table: dict) -> list[list[int]]:
    """Weisfeiler–Lehman labels for rounds 0 to ROUNDS. `table` is shared by both ends, so the
    same label means the same surroundings in either."""
    neighbours = [np.flatnonzero(row).tolist() for row in adjacency]
    labels = [table.setdefault(("element", e), len(table)) for e in elements]
    rounds = [labels]
    for _ in range(ROUNDS):
        labels = [
            table.setdefault((labels[i], tuple(sorted(labels[j] for j in near))), len(table))
            for i, near in enumerate(neighbours)
        ]
        rounds.append(labels)
    return rounds


def _anchors(start: list[list[int]], end: list[list[int]], fixed: dict[int, int]) -> dict[int, int]:
    """Pairs of atoms whose label is unique in both ends, the most specific round first."""
    pairs = dict(fixed)
    taken = set(fixed.values())
    for round_ in range(ROUNDS, -1, -1):
        in_start = Counter(start[round_])
        in_end = {label: j for j, label in enumerate(end[round_])}
        in_end_count = Counter(end[round_])
        for i, label in enumerate(start[round_]):
            if i in pairs or in_start[label] != 1 or in_end_count.get(label) != 1:
                continue
            j = in_end[label]
            if j not in taken:
                pairs[i] = j
                taken.add(j)
    return pairs


def _placed(moving: np.ndarray, reference: np.ndarray, pairs: dict[int, int]) -> np.ndarray:
    """`moving` (the end) fitted on `reference` (the start) through the paired atoms."""
    starts = list(pairs)
    ends = [pairs[i] for i in starts]
    a, b = moving[ends], reference[starts]
    a_centre, b_centre = a.mean(axis=0), b.mean(axis=0)
    rotation, _ = _fit(a - a_centre, b - b_centre, allow_mirror=False)
    return (moving - a_centre) @ rotation + b_centre


def _spread(points: np.ndarray) -> bool:
    """True when the points fix a rotation (not all on one line)."""
    if len(points) < 3:
        return False
    singular = np.linalg.svd(points - points.mean(axis=0), compute_uv=False)
    return bool(singular[1] > 0.1)


def _seed_frames(moving: np.ndarray, reference: np.ndarray) -> list[np.ndarray]:
    """The end placed on the start in several ways to start from: by matching principal axes
    (four proper ways), then turned by each rotation of the icosahedral group."""

    def axes(points: np.ndarray) -> np.ndarray:
        centred = points - points.mean(axis=0)
        _, vectors = np.linalg.eigh(centred.T @ centred)
        vectors = vectors[:, ::-1]
        if np.linalg.det(vectors) < 0:
            vectors[:, 2] *= -1
        return vectors

    a, b = axes(moving), axes(reference)
    centred = moving - moving.mean(axis=0)
    frames = []
    for signs in ((1, 1, 1), (1, -1, -1), (-1, 1, -1), (-1, -1, 1)):
        frames.append(centred @ (a @ np.diag(signs) @ b.T) + reference.mean(axis=0))
    for turn in Rotation.create_group("I").as_matrix():
        frames.append(centred @ (a @ b.T) @ turn + reference.mean(axis=0))
    return frames


class _Matcher:
    def __init__(self, start: Rows, end: Rows, fixed: dict[int, int], loose: Iterable[int] = ()):
        self.elements = [row[0] for row in start]
        self.end_elements = [row[0] for row in end]
        self.p = _coords(start)
        self.q = _coords(end)
        self.adj_p = bonds(self.p, self.elements)
        self.adj_q = bonds(self.q, self.end_elements)
        self.near_p = [np.flatnonzero(row).tolist() for row in self.adj_p]
        self.near_q = [np.flatnonzero(row).tolist() for row in self.adj_q]
        table: dict = {}
        self.lab_p = _labels(self.elements, self.adj_p, table)
        self.lab_q = _labels(self.end_elements, self.adj_q, table)
        self.fixed = fixed
        self.loose = set(loose)
        # How far apart two atoms' surroundings are: rounds 1 to ROUNDS in which labels differ.
        self.label_rounds = np.zeros((len(start), len(end)))
        for round_ in range(1, ROUNDS + 1):
            a = np.array(self.lab_p[round_])
            b = np.array(self.lab_q[round_])
            self.label_rounds += a[:, None] != b[None, :]

    def changes(self, mapping: list[int]) -> tuple[list[tuple[int, int]], list[tuple[int, int]]]:
        order = np.array(mapping)
        end_in_start = self.adj_q[np.ix_(order, order)]
        formed = np.argwhere(np.triu(end_in_start & ~self.adj_p))
        broken = np.argwhere(np.triu(self.adj_p & ~end_in_start))
        return [tuple(map(int, b)) for b in formed], [tuple(map(int, b)) for b in broken]

    def inverted(self, mapping: list[int]) -> list[int]:
        """Start atoms whose neighbours sit the other way round at the end: a centre keeping
        three or more of its neighbours, whose first three kept span opposite handedness. A
        match that swaps a CH2's hydrogens would make the path invert that carbon. Centres matched
        to a `loose` end atom are never counted."""
        found = []
        for c, near in enumerate(self.near_p):
            if mapping[c] in self.loose:
                continue
            kept = [k for k in near if self.adj_q[mapping[c], mapping[k]]]
            if len(kept) < 3:
                continue
            near, ends = kept, [mapping[k] for k in kept]
            a = self.p[near[:3]] - self.p[c]
            b = self.q[ends[:3]] - self.q[mapping[c]]
            va = np.linalg.det(a) / np.prod(np.linalg.norm(a, axis=1))
            vb = np.linalg.det(b) / np.prod(np.linalg.norm(b, axis=1))
            if abs(va) > PLANAR and abs(vb) > PLANAR and va * vb < 0:
                found.append(c)
        return found

    def repair(self, mapping: list[int], score) -> list[int]:
        """Inverted centres put right where swapping two of their like neighbours (and letting
        the atoms beyond follow) scores better."""
        best = mapping
        for _ in range(len(self.elements)):
            improved = False
            for c in self.inverted(best):
                near = [k for k in self.near_p[c] if k not in self.fixed]
                for x, y in itertools.combinations(near, 2):
                    if self.elements[x] != self.elements[y]:
                        continue
                    trial = list(best)
                    trial[x], trial[y] = trial[y], trial[x]
                    trial = self.refine(trial, keep={c: trial[c], x: trial[x], y: trial[y]})
                    if score(trial) < score(best):
                        best, improved = trial, True
                        break
                if improved:
                    break
            if not improved:
                return best
        return best

    def _frame(self, pairs: dict[int, int], fallback: np.ndarray | None) -> np.ndarray | None:
        if len(pairs) >= 3 and _spread(self.p[list(pairs)]):
            return _placed(self.q, self.p, pairs)
        return fallback

    def grow(self, pairs: dict[int, int], seed: np.ndarray | None) -> dict[int, int]:
        """Pairs extended bond by bond from the matched atoms: a matched atom's unmatched
        neighbours are paired element by element, by their surroundings first and, among
        equals (a methyl's hydrogens, an isopropyl's methyls), by position."""
        pairs = dict(pairs)
        taken = set(pairs.values())
        grown = True
        while grown:
            grown = False
            frame = self._frame(pairs, seed)
            for i in list(pairs):
                j = pairs[i]
                ni = [k for k in self.near_p[i] if k not in pairs]
                nj = [m for m in self.near_q[j] if m not in taken]
                for element in sorted({self.elements[k] for k in ni}):
                    a = [k for k in ni if self.elements[k] == element]
                    b = [m for m in nj if self.end_elements[m] == element]
                    if not b:
                        continue
                    cost = GROW_LABEL_WEIGHT * self.label_rounds[np.ix_(a, b)]
                    if frame is not None:
                        diff = self.p[a][:, None, :] - frame[b][None, :, :]
                        cost = cost + (diff * diff).sum(axis=2)
                    for r, c in zip(*linear_sum_assignment(cost), strict=True):
                        pairs[a[r]] = b[c]
                        taken.add(b[c])
                        grown = True
                    ni = [k for k in ni if k not in pairs]
                    nj = [m for m in nj if m not in taken]
        return pairs

    def _assign(
        self, placed: np.ndarray, mapping: dict[int, int], keep: dict[int, int]
    ) -> list[int]:
        """Every atom not in `keep` paired by the Hungarian assignment, element by element, on
        the squared distance plus the surroundings that differ and the neighbours (under
        `mapping`) that would not stay neighbours."""
        diff = self.p[:, None, :] - placed[None, :, :]
        cost = (diff * diff).sum(axis=2) + LABEL_PENALTY * np.minimum(self.label_rounds, 2)
        if mapping:
            n = len(self.elements)
            permutation = np.zeros((n, n))
            rows = list(mapping)
            permutation[rows, [mapping[i] for i in rows]] = 1.0
            a = self.adj_p.astype(float)
            b = self.adj_q.astype(float)
            common = a @ permutation @ b
            missed = a.sum(axis=1)[:, None] + b.sum(axis=0)[None, :] - 2 * common
            cost = cost + NEIGHBOUR_PENALTY * missed
        result = dict(keep)
        used = set(keep.values())
        for element in sorted(set(self.elements)):
            rows = [i for i, e in enumerate(self.elements) if e == element and i not in keep]
            cols = [j for j, e in enumerate(self.end_elements) if e == element and j not in used]
            if not rows:
                continue
            for r, c in zip(*linear_sum_assignment(cost[np.ix_(rows, cols)]), strict=True):
                result[rows[r]] = cols[c]
        return [result[i] for i in range(len(self.elements))]

    def complete(self, pairs: dict[int, int], seed: np.ndarray | None) -> list[int]:
        """The grown pairs kept; the atoms they did not reach paired by position."""
        frame = self._frame(pairs, seed)
        if frame is None:
            frame = self.q - self.q.mean(axis=0) + self.p.mean(axis=0)
        return self._assign(frame, pairs, pairs)

    def refine(self, mapping: list[int], keep: dict[int, int] | None = None) -> list[int]:
        """Refitted on all atoms and paired again, apart from the fixed pairs, until stable."""
        keep = {**self.fixed, **(keep or {})}
        for _ in range(MAX_ITERATIONS):
            pairs = dict(enumerate(mapping))
            again = self._assign(_placed(self.q, self.p, pairs), pairs, keep)
            if again == mapping:
                break
            mapping = again
        return mapping

    def rmsd(self, mapping: list[int]) -> float:
        placed = _placed(self.q, self.p, dict(enumerate(mapping)))
        return _rmsd(placed[mapping], self.p)


def check_atoms(start: Rows, end: Rows) -> None:
    """Both ends hold the same atoms; otherwise a RecordError saying how they differ."""
    if not start or not end:
        raise RecordError("both structures need coordinates")
    if len(start) != len(end):
        raise RecordError(
            f"the structures have {len(start)} and {len(end)} atoms; a match needs the same "
            "atoms in both"
        )
    a = Counter(row[0] for row in start)
    b = Counter(row[0] for row in end)
    if a != b:
        differ = sorted(e for e in set(a) | set(b) if a[e] != b[e])
        details = ", ".join(f"{e} {a[e]} and {b[e]}" for e in differ)
        raise RecordError(f"the structures hold different atoms ({details})")


def match(
    start: Rows, end: Rows, fixed: Iterable[tuple[int, int]] = (), loose: Iterable[int] = ()
) -> Match:
    """D113: the end's atoms matched to the start's, with the pairs in `fixed` (0-based
    (start atom, end atom)) kept. `loose` end atoms (0-based) may turn their neighbours the
    other way round, as a metal that gains or loses a ligand may (D120): they are never
    counted as mirror images."""
    check_atoms(start, end)
    pinned: dict[int, int] = {}
    for i, j in fixed:
        if not (0 <= i < len(start) and 0 <= j < len(end)):
            raise RecordError(f"atom pair {i + 1}–{j + 1} lies outside the structures")
        if i in pinned or j in pinned.values():
            raise RecordError(f"atom pair {i + 1}–{j + 1} repeats an atom of another pair")
        if start[i][0] != end[j][0]:
            raise RecordError(
                f"atom {i + 1} is {start[i][0]} but atom {j + 1} of the other end is {end[j][0]}"
            )
        pinned[i] = j
    m = _Matcher(start, end, pinned, loose)
    anchors = _anchors(m.lab_p, m.lab_q, pinned)
    if not anchors:  # nothing stands out: start from the atom of the rarest surroundings
        counts = Counter(m.lab_p[1])
        i = min(range(len(start)), key=lambda k: (counts[m.lab_p[1][k]], k))
        same = [j for j, label in enumerate(m.lab_q[1]) if label == m.lab_p[1][i]]
        anchors = {i: same[0]} if same else {}

    scores: dict[tuple[int, ...], tuple[int, float]] = {}

    def score(mapping: list[int]) -> tuple[int, float]:
        key = tuple(mapping)
        if key not in scores:
            formed, broken = m.changes(mapping)
            changed = len(formed) + len(broken) + len(m.inverted(mapping))
            scores[key] = (changed, m.rmsd(mapping))
        return scores[key]

    candidates: list[list[int]] = []
    for seed in [None, *_seed_frames(m.q, m.p)]:
        grown = m.complete(m.grow(anchors, seed), seed)
        for mapping in (grown, m.refine(grown)):
            if tuple(mapping) not in scores:
                candidates.append(mapping)
                score(mapping)
        changed, rmsd = min(scores.values())
        if changed == 0 and rmsd < GOOD_ENOUGH:
            break

    best = m.repair(min(candidates, key=score), score)  # the first of equals
    same = False
    identity = list(range(len(start)))
    if m.elements == m.end_elements and all(i == j for i, j in pinned.items()):
        if score(identity)[0] <= score(best)[0]:
            best, same = identity, True

    formed, broken = m.changes(best)
    rmsd = m.rmsd(best)
    doubts = []
    if m.inverted(best):
        doubts.append("the match turns a centre into its mirror image")
    if len(formed) + len(broken) > MAX_BOND_CHANGES:
        doubts.append(
            f"{len(formed) + len(broken)} bonds form or break, more than the "
            f"{MAX_BOND_CHANGES} a single step usually changes"
        )
    moved = {a for pair in formed + broken for a in pair}
    steady = [i for i in range(len(start)) if i not in moved]
    if steady:
        placed = _placed(m.q, m.p, {i: best[i] for i in steady})
        apart = _rmsd(placed[[best[i] for i in steady]], m.p[steady])
        if apart > MAX_RMSD:
            doubts.append(
                f"the atoms whose bonds stay differ by {apart:.2f} Å after fitting, so the "
                "two ends may not be the same structure"
            )
    return Match(
        mapping=best,
        rmsd=rmsd,
        formed=formed,
        broken=broken,
        inverted=m.inverted(best),
        fixed=sorted(pinned.items()),
        same_numbering=same,
        doubts=doubts,
    )


def renumbered(start: Rows, end: Rows, mapping: list[int]) -> Rows:
    """The end's rows in the start's order, placed on the start."""
    q = _coords(end)
    placed = _placed(q, _coords(start), dict(enumerate(mapping)))
    return [[end[j][0], *map(float, placed[j])] for j in mapping]


def pairs_from(numbers: list[list[int]]) -> list[tuple[int, int]]:
    """Hand-fixed pairs as the API gives them (1-based), 0-based."""
    pairs = []
    for pair in numbers:
        if len(pair) != 2:
            raise RecordError("each fixed pair names one atom of each end")
        pairs.append((pair[0] - 1, pair[1] - 1))
    return pairs


def match_nodes(
    session: Session, start_id: str, end_id: str, fixed: list[list[int]] | None = None
) -> tuple[Node, Node, Match]:
    """D113: the end node's atoms matched to the start node's numbering. Neither changes."""
    if start_id == end_id:
        raise RecordError("choose two different structures")
    start = get(session, Node, start_id, "Node")
    end = get(session, Node, end_id, "Node")
    for node in (start, end):
        if not node.geometry:
            raise RecordError(f"“{node.label or 'Untitled node'}” has no coordinates")
    return start, end, match(start.geometry, end.geometry, pairs_from(fixed or []))


def summary(result: Match) -> dict[str, Any]:
    """The match with 1-based atom numbers, as the API and the job folder (D113) carry it."""
    return {
        "mapping": [j + 1 for j in result.mapping],
        "rmsd": result.rmsd,
        "formed": [[a + 1, b + 1] for a, b in result.formed],
        "broken": [[a + 1, b + 1] for a, b in result.broken],
        "inverted": [a + 1 for a in result.inverted],
        "fixed": [[a + 1, b + 1] for a, b in result.fixed],
        "same_numbering": result.same_numbering,
        "confident": result.confident,
        "doubts": result.doubts,
    }
