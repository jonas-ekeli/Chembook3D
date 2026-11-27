"""Steps between two minima with no TS: a free species joining or leaving, or a change of
coordination mode or conformation (D263, FR-MCP-27, A190).

`find_ts_partners` starts from a TS, so it never finds a step that has none: an olefin binding
a 14-electron complex, a ligand leaving a precatalyst, a π-complex turning from one face to the
other. `find` lists those candidates in the open investigation (a playground in practice) and
changes nothing:

- `association`: minimum B has the atoms of minimum A and one free species S together (charge
  added; S closed shell), and B's bonds are A's and S's but for S's bonds to the core (A181's
  rule, metal–hydrogen contacts weak), each to a metal of the core (A193: a bond between S and
  a non-metal atom of the core is a cycloaddition or the like, a step with a TS, never an
  association): S's atoms are found in B by their bonds (`_embeddings`),
  the rest of B (the core) must be A's molecule, matched whatever its atom order (the same
  numbering, by position, then Match atoms ended at the first match keeping every bond), and
  the pair is ranked by the core's atom-matched RMSD. Read either way: A + S → B (association)
  or B → A + S (dissociation). S is a free species node or a node that should be one: a small
  closed-shell molecule without a metal (`small_molecule`, said in the answer, A190).
- `same_atoms`: two minima with the same atoms whose bonds differ only at a metal
  (coordination mode) or not at all (conformation), further apart than the duplicate tolerance,
  in different units, each with its nearest such partners only (`SAME_PER_UNIT`).

Pairs already joined (by an edge, through one TS, or through one minimum with the same atoms as
one of them) and pairs inside one group are left out; for each pair of units (a group stands
for its members) the nearest pair of members is kept, and of those only the nearest per pair of
molecules (A193: conformers not grouped are one molecule, a group its own), with the number of
other conformer pairs, so the answer is a line per step. It is cut at `MAX_ANSWER` characters
with `more`. Energies are never read.
"""

import json
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Any

import numpy as np
from sqlalchemy import select
from sqlalchemy.orm import Session

from chembook3d.models import GroupNode, Node, NodeKind
from chembook3d.pathtools import METALS
from chembook3d.services import atom_matching, gaps, geometry, levels, thermo_settings, ts_partners
from chembook3d.services import nodes as node_service
from chembook3d.services import structure_compare as compare
from chembook3d.services.geometry import Rows

SMALL_ATOMS = 40  # A190: a metal-free closed-shell molecule up to this size may be a free species
SPECIES_BONDS = 4  # A190: at most this many bonds join the species to the core
MEDIUM_RMSD = 0.5  # A190: a core RMSD up to this suggests medium confidence, beyond it low (Å)
MAX_EMBEDDINGS = 64  # places a species' atoms are looked for in one structure
METAL_CHANGES = 2  # A190: a coordination mode change moves at most this many metal bonds
SAME_PER_UNIT = 2  # A190: each unit's nearest same-atom partners listed
MAX_ANSWER = 20_000  # characters of an answer, as find_ts_partners' batch (A187)
KINDS = ("association", "same_atoms")


@dataclass
class _Item:
    """A structure of the search: a node with what the search needs of it, made once."""

    node: Node
    unit: str  # its unit in the mechanism (its group, else itself)
    rows: Rows
    charge: int | None
    multiplicity: int | None
    level: str | None  # its geometry level's id
    group: str | None = None  # its group's label
    _counts: Any = None
    _adjacency: Any = None
    _print: Any = None
    _shape: Any = None

    @property
    def label(self) -> str:
        return self.node.label or "Untitled node"

    @property
    def atoms(self) -> Counter:
        return Counter(r[0] for r in self.rows)

    def counts(self) -> Any:
        if self._counts is None:
            self._counts = compare._counts(geometry.elements(self.rows), *self.adjacency())
        return self._counts

    def adjacency(self) -> tuple[np.ndarray, np.ndarray]:
        if self._adjacency is None:
            self._adjacency = compare._adjacency(self.rows)
        return self._adjacency

    def print_(self, table: dict) -> tuple[int, ...]:
        if self._print is None:
            self._print = compare.fingerprint(self.rows, table)
        return self._print

    def shape(self) -> compare.Print:
        if self._shape is None:
            self._shape = compare.signature(self.rows)
        return self._shape


def small_molecule(rows: Rows, multiplicity: int | None) -> bool:
    """A190: a molecule that should be a free species when it joins or leaves: no metal, at
    most SMALL_ATOMS atoms, closed shell (or not known), and in one piece by its bonds."""
    if not rows or len(rows) > SMALL_ATOMS or multiplicity not in (None, 1):
        return False
    if any(r[0] in METALS for r in rows):
        return False
    if len(rows) == 1:
        return True
    strict, _ = compare._adjacency(rows)
    seen, todo = {0}, [0]
    while todo:
        for j in np.flatnonzero(strict[todo.pop()]):
            if int(j) not in seen:
                seen.add(int(j))
                todo.append(int(j))
    return len(seen) == len(rows)


def _items(session: Session, mechanism: Any, tolerance: float) -> tuple[list[_Item], list[_Item]]:
    """The true minima (ts_partners.left_out) and the species: free species nodes and small
    molecules (`small_molecule`)."""
    groups = {g.id: g.label or "Untitled group" for g in session.scalars(select(GroupNode))}
    minima, species = [], []
    for node in node_service.list_nodes(session, with_calculations=True):
        if not node.geometry or ts_partners.left_out(node, tolerance) is not None:
            continue
        level = levels.node_geometry_level(node, tolerance)
        rows = [[r[0], float(r[1]), float(r[2]), float(r[3])] for r in node.geometry]
        item = _Item(
            node,
            mechanism.of_node.get(node.id, node.id),
            rows,
            node.charge,
            node.multiplicity,
            level.id if level is not None else None,
            groups.get(node.group_id) if node.group_id else None,
        )
        if node.kind == NodeKind.SPECIES:
            species.append(item)
            continue
        minima.append(item)
        if small_molecule(rows, node.multiplicity):
            species.append(item)
    return minima, species


def _joined(mechanism: Any) -> set[frozenset[str]]:
    """The pairs of units the mechanism joins already: by an edge, through one TS, or through
    one minimum with the same atoms as one of them (a conformer or coordination step beside
    the step: A + S → B → B' joins A and B')."""

    def atoms(unit_id: str) -> Counter | None:
        unit = mechanism.units.get(unit_id)
        if unit is None or unit.node is None or not unit.node.geometry:
            return None
        return Counter(r[0] for r in unit.node.geometry)

    out: set[frozenset[str]] = set()
    for unit_id, unit in mechanism.units.items():
        around = sorted(mechanism.neighbours(unit_id))
        for other in around:
            out.add(frozenset((unit_id, other)))
        own = None if unit.ts else atoms(unit_id)
        for i, a in enumerate(around):
            for b in around[i + 1 :]:
                if unit.ts or (own is not None and own in (atoms(a), atoms(b))):
                    out.add(frozenset((a, b)))
    return out


# ---------- a species found in a larger structure ----------


def _embeddings(species: _Item, big: _Item) -> list[list[int]]:
    """The places the species' atoms lie in `big` with the species' own bonds (A181: a bond of
    one within the outer reach in the other, either way), each a list of big's atoms in the
    species' order; heavy atoms by their bonds, then each one's hydrogens."""
    s_strict, s_loose = species.adjacency()
    b_strict, b_loose = big.adjacency()
    s_el, b_el = geometry.elements(species.rows), geometry.elements(big.rows)
    heavy = [i for i, e in enumerate(s_el) if e != "H"]
    if not heavy:
        return []

    def hydrogens(adjacency: np.ndarray, elements: list[str], i: int) -> list[int]:
        return [int(j) for j in np.flatnonzero(adjacency[i]) if elements[j] == "H"]

    s_h = {i: hydrogens(s_strict, s_el, i) for i in heavy}
    if sum(len(h) for h in s_h.values()) + len(heavy) != len(s_el):
        return []  # a hydrogen on no heavy atom, or on two: not looked for
    # The heavy atoms in an order each one bonded to one before it where it can be.
    order = [max(heavy, key=lambda i: (int(s_strict[i][heavy].sum()), -i))]
    while len(order) < len(heavy):
        rest = [i for i in heavy if i not in order]
        bonded = [i for i in rest if any(s_strict[i, k] for k in order)]
        order.append(min(bonded or rest))
    b_h = [hydrogens(b_strict, b_el, j) if b_el[j] != "H" else [] for j in range(len(b_el))]
    options = {
        i: [j for j in range(len(b_el)) if b_el[j] == s_el[i] and len(b_h[j]) == len(s_h[i])]
        for i in heavy
    }
    found: list[list[int]] = []
    seen: set[frozenset[int]] = set()
    placed: dict[int, int] = {}

    def fits(i: int, j: int) -> bool:
        for k, m in placed.items():
            if s_strict[i, k] and not b_loose[j, m]:
                return False
            if b_strict[j, m] and not s_loose[i, k]:
                return False
        return True

    def grow(n: int) -> None:
        if len(found) >= MAX_EMBEDDINGS:
            return
        if n == len(order):
            mapping = [0] * len(s_el)
            for i, j in placed.items():
                mapping[i] = j
                for h, g in zip(s_h[i], b_h[j], strict=True):
                    mapping[h] = g
            key = frozenset(mapping)
            if key not in seen:
                seen.add(key)
                found.append(mapping)
            return
        i = order[n]
        used = set(placed.values())
        for j in options[i]:
            if j in used or not fits(i, j):
                continue
            placed[i] = j
            grow(n + 1)
            del placed[i]

    grow(0)
    return found


def _bonds_to_core(big: _Item, inside: set[int]) -> list[tuple[int, int]]:
    """big's bonds (by the 3D view's rule) between the species' atoms and the rest, metal–
    hydrogen contacts left out (weak, A184)."""
    strict, _ = big.adjacency()
    out = []
    for i in sorted(inside):
        for j in np.flatnonzero(strict[i]):
            j = int(j)
            if j in inside:
                continue
            if compare._metal_hydrogen(big.rows, (i, j)):
                continue
            out.append((min(i, j), max(i, j)))
    return sorted(set(out))


def _core_mapping(a: _Item, core: Rows) -> list[int] | None:
    """core's atom for each of A's under which no real bond changes (A181, metal–hydrogen
    contacts weak): the same numbering, by position, then Match atoms ended at the first match
    that keeps every bond (A187's `enough`)."""
    return ts_partners._mapping(a.rows, core, set())


# ---------- the search ----------


@dataclass
class _Found:
    kind: str
    a: _Item
    b: _Item
    rmsd: float
    species: _Item | None = None
    bonds: list[str] = field(default_factory=list)
    metal: list[str] = field(default_factory=list)  # same_atoms: metal bonds that change
    other_level: bool = False
    renumbered: bool = False

    def key(self) -> tuple:
        return (KINDS.index(self.kind), self.other_level, self.rmsd)


def _associations(
    minima: list[_Item], species: list[_Item], joined: set[frozenset[str]]
) -> list[_Found]:
    by_atoms: dict[tuple, list[_Item]] = defaultdict(list)
    for item in minima:
        by_atoms[tuple(sorted(item.atoms.items()))].append(item)
    found: list[_Found] = []
    for s in species:
        s_atoms = s.atoms
        for b in minima:
            if b.node.id == s.node.id or len(b.rows) <= len(s.rows):
                continue
            rest = b.atoms - s_atoms
            if sum(rest.values()) != len(b.rows) - len(s.rows):
                continue  # B lacks some of S's atoms
            partners = [
                a
                for a in by_atoms.get(tuple(sorted(rest.items())), [])
                if a.node.id not in (b.node.id, s.node.id)
                and a.unit != b.unit
                and frozenset((a.unit, b.unit)) not in joined
                and _adds_up(a, s, b)
            ]
            if not partners:
                continue
            places = _embeddings(s, b)
            cores = []
            elements = geometry.elements(b.rows)
            for mapping in places:
                inside = set(mapping)
                cut = _bonds_to_core(b, inside)
                if len(cut) > SPECIES_BONDS:
                    continue
                if any(elements[i] not in METALS and elements[j] not in METALS for i, j in cut):
                    continue  # A193: a bond to a non-metal of the core is no association
                kept = [j for j in range(len(b.rows)) if j not in inside]
                core = [b.rows[j] for j in kept]
                cores.append((cut, kept, core, compare._bond_counts(core)))
            if not cores:
                continue
            for a in partners:
                best: _Found | None = None
                for cut, kept, core, counts in cores:
                    if not compare._could_be_same(a.counts(), counts, False):
                        continue
                    mapping = _core_mapping(a, core)
                    if mapping is None:
                        continue
                    rmsd = geometry.aligned_rmsd(a.rows, [core[j] for j in mapping])
                    if rmsd is None or (best is not None and rmsd >= best.rmsd):
                        continue
                    best = _Found(
                        "association",
                        a,
                        b,
                        rmsd,
                        s,
                        [compare._bond(b.rows, i, j) for i, j in cut],
                        other_level=None not in (a.level, b.level) and a.level != b.level,
                        renumbered=any(kept[m] != i for i, m in enumerate(mapping)),
                    )
                if best is not None:
                    found.append(best)
    return found


def _adds_up(a: _Item, s: _Item, b: _Item) -> bool:
    """A's and S's charges add to B's, and S closed shell leaves A's multiplicity (where
    known)."""
    if None not in (a.charge, s.charge, b.charge) and a.charge + s.charge != b.charge:
        return False
    if s.multiplicity not in (None, 1):
        return False
    return a.multiplicity is None or b.multiplicity is None or a.multiplicity == b.multiplicity


def _same_atoms(
    minima: list[_Item], joined: set[frozenset[str]], tolerance: float, table: dict
) -> list[_Found]:
    buckets: dict[tuple, list[_Item]] = defaultdict(list)
    for item in minima:
        key = (tuple(sorted(item.atoms.items())), item.charge, item.multiplicity)
        buckets[(*key, item.print_(table))].append(item)
    found: dict[frozenset[str], _Found] = {}
    for members in buckets.values():
        if len({m.unit for m in members}) < 2:
            continue
        for a in members:
            others = [
                b for b in members if b.unit != a.unit and frozenset((a.unit, b.unit)) not in joined
            ]
            others.sort(key=lambda b: compare.lower_bound(a.shape(), b.shape()))
            kept = 0
            for b in others:
                if kept >= SAME_PER_UNIT:
                    break
                pair = frozenset((a.node.id, b.node.id))
                if pair in found:
                    kept += 1
                    continue
                result = _coordination(a, b, tolerance)
                if result is None:
                    continue
                found[pair] = result
                kept += 1
    return list(found.values())


def _coordination(a: _Item, b: _Item, tolerance: float) -> _Found | None:
    """B matched onto A whatever its atom order, its real bond changes all at a metal (at most
    METAL_CHANGES) and further apart than the duplicate tolerance; else None."""
    elements_a = geometry.elements(a.rows)

    def metal_only(mapping: list[int]) -> list[tuple[int, int]] | None:
        formed, broken = atom_matching.changes(a.rows, b.rows, mapping)
        real_formed, real_broken, _, _ = compare.split(a.rows, b.rows, mapping, formed, broken)
        changed = real_formed + real_broken
        if len(changed) > METAL_CHANGES:
            return None
        if any(elements_a[i] not in METALS and elements_a[j] not in METALS for i, j in changed):
            return None
        return changed

    mapping = None
    if elements_a == geometry.elements(b.rows):
        identity = list(range(len(a.rows)))
        if metal_only(identity) is not None:
            mapping = identity
    if mapping is None:
        by_position = compare._by_position(a.rows, b.rows)[1]
        if metal_only(by_position) is not None:
            mapping = by_position
    if mapping is None:
        try:
            matched = atom_matching.match(
                a.rows, b.rows, enough=lambda m: metal_only(m) is not None
            )
        except Exception:  # noqa: BLE001 - a match that cannot be made pairs nothing
            return None
        if metal_only(matched.mapping) is None:
            return None
        mapping = matched.mapping
    changed = metal_only(mapping) or []
    rmsd = geometry.aligned_rmsd(a.rows, [b.rows[j] for j in mapping])
    if rmsd is None or rmsd <= tolerance:
        return None  # a duplicate: merged, not linked
    return _Found(
        "same_atoms",
        a,
        b,
        rmsd,
        metal=[compare._bond(a.rows, i, j) for i, j in changed],
        other_level=None not in (a.level, b.level) and a.level != b.level,
        renumbered=any(i != j for i, j in enumerate(mapping)),
    )


# ---------- the answer ----------


def _end(item: _Item) -> dict[str, Any]:
    out: dict[str, Any] = {"label": item.label, "node_id": item.node.id}
    if item.group:
        out["group"] = item.group
    return out


def _entry(found: _Found) -> dict[str, Any]:
    small = found.rmsd <= MEDIUM_RMSD and not found.other_level
    out: dict[str, Any] = {"kind": found.kind, "a": _end(found.a)}
    if found.kind == "association":
        s = found.species
        assert s is not None
        species: dict[str, Any] = {"label": s.label, "node_id": s.node.id}
        if s.node.kind != NodeKind.SPECIES:
            species["make_species"] = True
        out["species"] = species
        out["b"] = _end(found.b)
        out["core_rmsd"] = round(found.rmsd, 3)
        out["bonds_to_core"] = found.bonds
        out["confidence"] = "medium" if small else "low"
    else:
        out["b"] = _end(found.b)
        out["rmsd"] = round(found.rmsd, 3)
        if found.metal:
            out["metal_bonds"] = found.metal
        out["note"] = "no TS: direct link?"
        out["confidence"] = "low"
    if found.other_level:
        out["other_level"] = True
    return out


def _molecules(found: _Found, table: dict) -> tuple:
    """A193: the pair's molecules: a grouped end is its group, any other its molecule (the
    metal-free bond print, so its conformers are one), with the species."""

    def molecule(item: _Item) -> Any:
        return ("group", item.unit) if item.group else item.print_(table)

    species = found.species.print_(table) if found.species is not None else ()
    return (found.kind, *sorted((molecule(found.a), molecule(found.b)), key=repr), species)


def find(
    session: Session,
    kinds: tuple[str, ...] = KINDS,
    offset: int = 0,
    limit: int | None = None,
) -> dict[str, Any]:
    """FR-MCP-27, A190: the candidate direct links of the open investigation, nearest first,
    as many as fit in `limit` characters from `offset`. See the module's docstring."""
    started = time.perf_counter()
    limit = MAX_ANSWER if limit is None else limit
    tolerance = thermo_settings.settings(session).duplicate_tolerance
    mechanism = gaps._mechanism(session)
    minima, species = _items(session, mechanism, tolerance)
    joined = _joined(mechanism)
    table: dict = {}
    found: list[_Found] = []
    if "association" in kinds:
        found += _associations(minima, species, joined)
    if "same_atoms" in kinds:
        found += _same_atoms(minima, joined, tolerance, table)
    found.sort(key=_Found.key)

    # The nearest pair of members per pair of units (and species) (A190); then only the
    # nearest per pair of molecules, with the number of other conformer pairs (A193).
    by_units: dict[tuple, _Found] = {}
    for f in found:
        key = (f.kind, *sorted((f.a.unit, f.b.unit)), f.species.unit if f.species else None)
        by_units.setdefault(key, f)
    alike: dict[tuple, list[_Found]] = defaultdict(list)
    for f in by_units.values():
        alike[_molecules(f, table)].append(f)
    entries = []
    for pairs in alike.values():  # each list nearest first, as `found`
        entry = _entry(pairs[0])
        if len(pairs) > 1:
            entry["other_pairs"] = len(pairs) - 1
        entries.append(entry)

    page: list[dict[str, Any]] = []
    size = 0
    for entry in entries[offset:]:
        size += len(json.dumps(entry, ensure_ascii=False)) + 2
        if page and size > limit:
            break
        page.append(entry)
    counts = Counter(e["kind"] for e in entries)
    to_make = sorted(
        {e["species"]["label"] for e in entries if e.get("species", {}).get("make_species")}
    )
    out: dict[str, Any] = {
        "pairs": page,
        "counts": dict(counts),
        "shown": {"from": offset + 1 if page else offset, "to": offset + len(page)},
        "of": len(entries),
        "searched": {"minima": len(minima), "species": len(species)},
        "seconds": round(time.perf_counter() - started, 1),
    }
    left = len(entries) - offset - len(page)
    if left > 0:
        out["more"] = {
            "left": left,
            "offset": offset + len(page),
            "hint": f"{left} more pairs: call again with offset {offset + len(page)}.",
        }
    if to_make:
        out["make_species"] = to_make
    out["summary"] = _summary(counts, to_make)
    out["next"] = (
        "Propose each association as an edge between the two minima (or their groups) with the "
        "species joining (A + S → B) or leaving (B → A + S): the atoms fix the species, so this "
        "needs no chemistry choice. Medium when `confidence` says so (the bonds balance and the "
        "core RMSD is small), else low, naming the core RMSD in the evidence. other_pairs: "
        "other conformers of the same two molecules; group the conformers (group_structures) "
        "rather than linking each. A species marked make_species is an ordinary node: make it "
        "a free species first (set_node_kind, kind species). same_atoms pairs are low, and "
        "only where no TS you have links them (a rotation TS would: find_ts_partners)."
    )
    out["key"] = (
        "association: B has A's and the species' atoms, and B's bonds are theirs but for "
        "bonds_to_core (the species' bonds to A's metal, in B's numbering; a bond to another "
        "atom of A is a cycloaddition, left out: it needs a TS); core_rmsd: A "
        "against B without the species, atoms matched. same_atoms: the same atoms, bonds "
        "differing only at a metal (metal_bonds) or not at all, further apart than the "
        "duplicate tolerance. Pairs an edge or one TS joins, and pairs in one group, are left "
        "out; per pair of molecules (a group counts as its own) the nearest pair of "
        "conformers, other_pairs the number of others. other_level: their geometries are at "
        "different levels. Nothing was changed."
    )
    return out


def _summary(counts: Counter, to_make: list[str]) -> str:
    parts = [f"{counts.get('association', 0)} association or dissociation pair(s)"]
    parts.append(f"{counts.get('same_atoms', 0)} same-atom pair(s) with no TS")
    text = "Direct links between minima: " + ", ".join(parts) + "."
    if to_make:
        text += " " + ", ".join(f"“{n}”" for n in to_make[:6]) + " should be free species."
    return text
