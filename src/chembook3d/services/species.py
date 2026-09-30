"""Free species and mass balance (D69).

- A free species (a substrate, or a fragment such as ethylene) is a node of kind "species":
  it has calculations, a geometry and energies like any node, but no step, branch, group or
  edges, and it is not drawn on the canvas.
- A transition lists the species that join (association) or leave (dissociation) on it,
  each with a count.
- The balance of a point on a pathway is what must be added to its energy so it has the
  atoms of the reference: walking from the reference, a species that leaves is added and one
  that joins is subtracted. The same change applies to the difference along one edge.
- Every node and group joined to the reference by transitions has one balance, found along
  the route from the reference with the fewest transitions walked backwards, then the fewest
  transitions (D72). Node cards in energy mode use it, and so does a pathway that does not
  pass through the reference, from its first point with a balance.
- W-BALANCE: when both ends and every attached species have coordinates, the atoms and the
  charge before an edge (source + joining species) must equal those after it (target +
  leaving species).
"""

import heapq
from collections import Counter, defaultdict
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from chembook3d import xyz
from chembook3d.models import GroupNode, Node, NodeKind, Transition, TransitionSpecies
from chembook3d.services import history
from chembook3d.services import transitions as transition_service
from chembook3d.services.energies import ENERGY_TYPES, Energies, LevelKey
from chembook3d.services.records import RecordError, get
from chembook3d.services.warnings import Finding

JOINS, LEAVES = "joins", "leaves"
DIRECTIONS = (JOINS, LEAVES)

Balance = dict[str, int]  # species id → how many times its energy is added (negative: taken)


def is_species(node: Node | None) -> bool:
    return node is not None and node.kind == NodeKind.SPECIES


def list_species(session: Session) -> list[Node]:
    query = select(Node).where(Node.kind == NodeKind.SPECIES).order_by(Node.seq)
    return list(session.scalars(query))


def label(node: Node) -> str:
    return node.label or "Untitled species"


def listing(transition: Transition) -> list[dict[str, Any]]:
    """The species on a transition as stored in the history."""
    return [
        {"species_id": e.species_id, "direction": e.direction, "count": e.count}
        for e in transition.species
    ]


def _species(session: Session, species_id: Any) -> Node:
    node = get(session, Node, species_id if isinstance(species_id, str) else None, "Species")
    if not is_species(node):
        raise RecordError(f"“{node.label or 'Untitled node'}” is not a free species")
    return node


def attach(
    session: Session, transition_id: str, species_id: Any, direction: Any, count: Any = 1
) -> Transition:
    """Add a species to a transition, or change its direction or count if it is on it."""
    transition = get(session, Transition, transition_id, "Transition")
    species = _species(session, species_id)
    if direction not in DIRECTIONS:
        raise RecordError("direction must be 'joins' or 'leaves'")
    if not isinstance(count, int) or isinstance(count, bool) or count < 1:
        raise RecordError("count must be a whole number of at least 1")
    before = listing(transition)
    entry = next((e for e in transition.species if e.species_id == species.id), None)
    if entry is None:
        transition.species.append(
            TransitionSpecies(species_id=species.id, direction=direction, count=count)
        )
    else:
        entry.direction, entry.count = direction, count
    session.flush()
    after = listing(transition)
    if after != before:
        history.record(session, "transition", transition.id, "update", "species", before, after)
    return transition


def detach(session: Session, transition_id: str, species_id: str) -> Transition:
    transition = get(session, Transition, transition_id, "Transition")
    entry = next((e for e in transition.species if e.species_id == species_id), None)
    if entry is None:
        raise RecordError("This species is not on this transition")
    before = listing(transition)
    transition.species.remove(entry)
    session.flush()
    history.record(
        session, "transition", transition.id, "update", "species", before, listing(transition)
    )
    return transition


def usage(session: Session, species_id: str) -> list[TransitionSpecies]:
    query = select(TransitionSpecies).where(TransitionSpecies.species_id == species_id)
    return list(session.scalars(query))


def set_kind(session: Session, node_id: str, kind: Any) -> Node:
    """Turn a node without edges or group into a free species, or a species that is on no
    transition back into a node. A new species leaves its step and branch."""
    node = get(session, Node, node_id, "Node")
    if kind not in {k.value for k in NodeKind}:
        raise RecordError(f"unknown kind '{kind}'")
    if node.kind == kind:
        return node
    if kind == NodeKind.SPECIES:
        if transition_service.touching(session, [node.id]):
            raise RecordError("Remove this node's transitions before making it a free species")
        if node.group_id is not None:
            raise RecordError("Take this node out of its group before making it a free species")
        for field in ("step_id", "branch_id"):
            if getattr(node, field) is not None:
                history.record(session, "node", node.id, "update", field, getattr(node, field))
                setattr(node, field, None)
    elif usage(session, node.id):
        raise RecordError("Remove this species from its transitions before making it a node")
    history.record(session, "node", node.id, "update", "kind", node.kind, kind)
    node.kind = kind
    session.flush()
    return node


def refuse_on_canvas(node: Node) -> None:
    """A species has no edges, group, step or branch (D69)."""
    if is_species(node):
        raise RecordError(f"“{label(node)}” is a free species; add it to a transition instead")


# ---------- balance ----------


def change(transition: Transition, forward: bool) -> Balance:
    """What following this transition adds to the balance: a leaving species is added, a
    joining one subtracted; walking the transition backwards swaps them."""
    found: Balance = {}
    for entry in transition.species:
        sign = 1 if entry.direction == LEAVES else -1
        found[entry.species_id] = found.get(entry.species_id, 0) + sign * entry.count * (
            1 if forward else -1
        )
    return found


def combine(balance: Balance, other: Balance, sign: int = 1) -> Balance:
    found = dict(balance)
    for species_id, count in other.items():
        found[species_id] = found.get(species_id, 0) + sign * count
    return {k: v for k, v in found.items() if v}


def energy(
    session: Session, energies: Energies, balance: Balance, key: LevelKey, energy_type: str
) -> tuple[float | None, str | None]:
    """The energy the balance adds, or None and the reason when a species has no value at
    this composite level (EN-3: no fallback)."""
    if energy_type not in ENERGY_TYPES:
        raise RecordError(f"unknown energy type '{energy_type}'")
    total = 0.0
    for species_id, count in balance.items():
        value = energies.value(species_id, key, energy_type)
        if value.value is None:
            name = label(session.get(Node, species_id))
            return None, f"free species “{name}”: {value.message or 'no value at this level'}"
        total += count * value.value
    return total, None


def balances_from(session: Session, reference_id: str) -> dict[str, Balance]:
    """D72: the balance of every node and group joined to the reference, whatever its
    distance. Following transitions forward is preferred to walking them backwards, so in a
    catalytic cycle the points after the reference are balanced forward from it and only the
    points before it (a precatalyst) backwards. A group and its members share one balance."""
    links: dict[str, list[tuple[str, Balance, int]]] = defaultdict(list)
    for t in session.scalars(select(Transition).order_by(Transition.seq)):
        links[t.source_id].append((t.target_id, change(t, forward=True), 0))
        links[t.target_id].append((t.source_id, change(t, forward=False), 1))
    for member in session.scalars(select(Node).where(Node.group_id.is_not(None))):
        links[member.group_id].append((member.id, {}, 0))
        links[member.id].append((member.group_id, {}, 0))
    found: dict[str, Balance] = {}
    order = 0  # ties go to the transition created first
    queue: list[tuple[int, int, int, str, Balance]] = [(0, 0, order, reference_id, {})]
    while queue:
        backward, steps, _, record_id, balance = heapq.heappop(queue)
        if record_id in found:
            continue
        found[record_id] = balance
        for other, step, back in links[record_id]:
            if other not in found:
                order += 1
                entry = (backward + back, steps + 1, order, other, combine(balance, step))
                heapq.heappush(queue, entry)
    return found


def describe(session: Session, balance: Balance) -> list[dict[str, Any]]:
    """The balance for display, in the order the species were created."""
    nodes = [session.get(Node, species_id) for species_id in balance]
    return [
        {"species_id": n.id, "label": label(n), "count": balance[n.id]}
        for n in sorted(nodes, key=lambda n: n.seq)
    ]


def text(entries: list[dict[str, Any]]) -> str:
    """ "+ ethylene − 2 × styrene" for the energy table."""
    parts = []
    for entry in entries:
        count = abs(entry["count"])
        amount = f"{count} × " if count != 1 else ""
        parts.append(f"{'+' if entry['count'] > 0 else '−'} {amount}{entry['label']}")
    return " ".join(parts)


# ---------- W-BALANCE ----------


def _composition(session: Session, record_id: str) -> tuple[Counter[str], int | None] | None:
    """Element counts and charge of a node, or of a group's representative (EN-7); None when
    there are no coordinates to compare."""
    end: Node | GroupNode | None = session.get(Node, record_id)
    if end is None:
        group = session.get(GroupNode, record_id)
        if group is not None and group.representative_id:
            end = session.get(Node, group.representative_id)
    if end is None or not end.geometry:
        return None
    return Counter(row[0] for row in end.geometry), end.charge


def balance_warnings(session: Session, transition: Transition) -> list[Finding]:
    sides: list[tuple[Counter[str], int | None]] = []
    for end_id, direction in ((transition.source_id, JOINS), (transition.target_id, LEAVES)):
        found = _composition(session, end_id)
        if found is None:
            return []
        atoms, charge = found
        atoms = Counter(atoms)
        for entry in transition.species:
            if entry.direction != direction:
                continue
            species = _composition(session, entry.species_id)
            if species is None:
                return []
            for element, n in species[0].items():
                atoms[element] += n * entry.count
            charge = (
                None if charge is None or species[1] is None else charge + species[1] * entry.count
            )
        sides.append((atoms, charge))
    (before, charge_before), (after, charge_after) = sides
    problems = []
    if before - after:
        problems.append(f"{xyz.formula_of(before - after)} more before than after")
    if after - before:
        problems.append(f"{xyz.formula_of(after - before)} more after than before")
    if charge_before is not None and charge_after is not None and charge_before != charge_after:
        problems.append(f"charge {charge_before} before, {charge_after} after")
    if not problems:
        return []
    return [Finding("W-BALANCE", "does not balance: " + "; ".join(problems))]
