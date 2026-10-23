"""Conformer equilibrium (D171, FR-EQ-01…05): the populations of conformers the user chose,
the rates of the TSs between them, and how long the set takes to reach equilibrium.

- Members are nodes and groups the user chose (X5); a group counts with its members. A node
  marked as a TS is a barrier, every other node a conformer. Rejected, failed and superseded
  nodes are left out and a duplicate counts once (as D156). All must have the same atoms and
  charge (D171a).
- A TS joins the two conformers of the set it has edges to; an edge to a group counts for its
  representative (EN-7). Any other TS joins none; parallel TSs add their rates; a direct "no TS"
  edge joins nothing (D53, Q53, D171b).
- One composite level and energy type with no fallback (EN-3): a conformer without a value
  makes the result n/a; a TS without one joins nothing. G_qh at the analysis's temperature (S4).
- Each member has a count n (D171d): a conformer's weight is n·e^(−G/RT), and the rate from
  conformer i over a TS is (n_TS/n_i)·(k_BT/h)·e^(−(G_TS − G_i)/RT), so detailed balance holds.
  A TS below one of its conformers is taken at that conformer's energy (ΔG‡ 0 from there).
- The rate matrix of each set of conformers joined by links (master equation) gives its
  relaxation times, the time from the start conformer (unless chosen, the start that takes
  longest) until every population is within 1 percentage point of equilibrium, and the
  populations against time from it (D171f, A112).
- The result says what was computed, not whether the chemistry is right (X6).
"""

import math
from collections import Counter
from typing import Any

import numpy as np
from sqlalchemy import select
from sqlalchemy.orm import Session

from chembook3d import settings as app_settings
from chembook3d import xyz
from chembook3d.models import (
    ConformerEquilibrium,
    GroupNode,
    LevelOfTheory,
    Node,
    NodeKind,
    Role,
)
from chembook3d.services import geometry, history, thermo_settings
from chembook3d.services import transitions as transition_service
from chembook3d.services.energies import (
    ENERGY_TYPES,
    Energies,
    LevelKey,
    check_text,
    distinct_temperatures,
    key_label,
    same_temperature,
    temperatures_text,
)
from chembook3d.services.records import RecordError, get, number_value, text_value
from chembook3d.services.selectivity import LEFT_OUT, SAME_ENERGY
from chembook3d.units import BOLTZMANN, GAS_CONSTANT, J_PER_MOL_HARTREE, PLANCK

R_HARTREE = GAS_CONSTANT / J_PER_MOL_HARTREE  # hartree/K
K_OVER_H = BOLTZMANN / PLANCK  # 1/(K s)
KCAL = 1 / 627.5094740631  # hartree per kcal/mol
FIELDS = ("name", "members", "level", "energy_type", "temperature", "start_id", "notes")
MAX_COUNT = 100  # A112
WITHIN = 0.01  # A112: every population within 1 percentage point of equilibrium
RESOLVED = 1e-12  # A112: slower relaxation rates than this share of the fastest are not resolved
CURVE_POINTS = 80  # A112


def list_all(session: Session) -> list[ConformerEquilibrium]:
    return list(session.scalars(select(ConformerEquilibrium).order_by(ConformerEquilibrium.seq)))


def _snapshot(equilibrium: ConformerEquilibrium) -> dict[str, Any]:
    return {field: getattr(equilibrium, field) for field in FIELDS}


def _name(session: Session, value: Any, own_id: str | None = None) -> str:
    name = text_value("name", value).strip()
    if not name:
        raise RecordError("a conformer equilibrium needs a name")
    clash = session.scalar(select(ConformerEquilibrium).where(ConformerEquilibrium.name == name))
    if clash is not None and clash.id != own_id:
        raise RecordError(f"there is already a conformer equilibrium called “{name}”")
    return name


def _count(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value != int(value):
        raise RecordError("a count must be a whole number")
    if not 1 <= value <= MAX_COUNT:
        raise RecordError(f"a count must be from 1 to {MAX_COUNT}")
    return int(value)


def _members(session: Session, value: Any) -> list[dict[str, Any]]:
    """[{"id", "count"}]; a plain id counts once. Nodes and groups, no free species, each once."""
    if not isinstance(value, list):
        raise RecordError("the members must be a list of node and group ids")
    found = []
    seen: set[str] = set()
    for entry in value:
        if isinstance(entry, str):
            entry = {"id": entry}
        if not isinstance(entry, dict) or not isinstance(entry.get("id"), str):
            raise RecordError("each member needs the id of a node or group")
        record_id = entry["id"]
        if record_id in seen:
            raise RecordError("the same member is listed twice")
        seen.add(record_id)
        node = session.get(Node, record_id)
        if node is None:
            get(session, GroupNode, record_id, "Node or group")
        elif node.kind != NodeKind.NODE:
            raise RecordError("a free species cannot be a conformer or a barrier")
        found.append({"id": record_id, "count": _count(entry.get("count", 1))})
    return found


def _field(session: Session, equilibrium: ConformerEquilibrium, field: str, value: Any) -> Any:
    if field == "name":
        return _name(session, value, equilibrium.id)
    if field == "members":
        return _members(session, value)
    if field == "level":
        if value is None:
            return None
        LevelKey.decode(text_value("level", value))
        return value
    if field == "energy_type":
        if value not in ENERGY_TYPES:
            raise RecordError(f"unknown energy type '{value}'")
        return value
    if field == "temperature":
        if value is None:
            return None
        temperature = number_value("temperature", value)
        if temperature <= 0:
            raise RecordError("the temperature must be above zero")
        return temperature
    if field == "start_id":
        if value is None:
            return None
        node = get(session, Node, text_value("start", value), "Start conformer")
        return node.id
    if field == "notes":
        return text_value("notes", value)
    raise RecordError(f"'{field}' cannot be edited")


def create(session: Session, fields: dict[str, Any]) -> ConformerEquilibrium:
    equilibrium = ConformerEquilibrium(name=_name(session, fields.get("name", "")))
    session.add(equilibrium)
    session.flush()
    for field, value in fields.items():
        setattr(equilibrium, field, _field(session, equilibrium, field, value))
    session.flush()
    history.record(session, "equilibrium", equilibrium.id, "create", new=_snapshot(equilibrium))
    return equilibrium


def update(session: Session, equilibrium_id: str, changes: dict[str, Any]) -> ConformerEquilibrium:
    """Every changed field is one history entry (as S7)."""
    equilibrium = get(session, ConformerEquilibrium, equilibrium_id, "Conformer equilibrium")
    for field, value in changes.items():
        old = getattr(equilibrium, field, None)
        new = _field(session, equilibrium, field, value)
        setattr(equilibrium, field, new)
        if old != new:
            history.record(session, "equilibrium", equilibrium.id, "update", field, old, new)
    session.flush()
    return equilibrium


def delete(session: Session, equilibrium_id: str) -> None:
    equilibrium = get(session, ConformerEquilibrium, equilibrium_id, "Conformer equilibrium")
    history.record(session, "equilibrium", equilibrium.id, "delete", old=_snapshot(equilibrium))
    session.delete(equilibrium)
    session.flush()


# ---------- the kinetics (D171e–g), on plain numbers so it can be tested alone ----------


def eyring(barrier: float, temperature: float) -> float:
    """k = (k_BT/h)·e^(−ΔG‡/RT) in 1/s, κ = 1 (A112); the barrier in hartree."""
    return K_OVER_H * temperature * math.exp(-barrier / (R_HARTREE * temperature))


def rate_matrix(
    values: list[float],
    counts: list[int],
    links: list[tuple[int, int, float, int]],
    temperature: float,
) -> np.ndarray:
    """K with dP/dt = K·P: K[j, i] is the rate from conformer i to j. `links` are
    (i, j, G_TS, n_TS); parallel links add (D171b, D171d). A TS below one of its conformers
    counts at that conformer's energy, so detailed balance holds."""
    size = len(values)
    matrix = np.zeros((size, size))
    for i, j, top, n_ts in links:
        height = max(top, values[i], values[j])
        for a, b in ((i, j), (j, i)):
            rate = n_ts / counts[a] * eyring(height - values[a], temperature)
            matrix[b, a] += rate
            matrix[a, a] -= rate
    return matrix


def shares(values: list[float], counts: list[int], temperature: float) -> list[float]:
    """Boltzmann shares n·e^(−G/RT), normalised (D171d, e)."""
    rt = R_HARTREE * temperature
    low = min(values)
    weights = [n * math.exp(-(v - low) / rt) for v, n in zip(values, counts, strict=True)]
    total = sum(weights)
    return [w / total for w in weights]


class Relaxation:
    """The master equation of one connected set solved by its symmetric form: with π the
    equilibrium shares, S = π^(−1/2)·K·π^(1/2) is symmetric (detailed balance), so P(t) =
    π^(1/2)·V·e^(Λt)·Vᵀ·π^(−1/2)·P(0)."""

    def __init__(self, matrix: np.ndarray, equilibrium: list[float]):
        self.pi = np.array(equilibrium)
        root = np.sqrt(self.pi)
        symmetric = matrix * root[np.newaxis, :] / root[:, np.newaxis]
        symmetric = (symmetric + symmetric.T) / 2
        rates, vectors = np.linalg.eigh(symmetric)
        order = np.argsort(-rates)  # 0 (equilibrium) first, then ever faster decays
        self.rates = -rates[order]
        self.vectors = vectors[:, order]
        self.root = root
        fastest = float(self.rates[-1]) if len(self.rates) > 1 else 0.0
        self.resolved = [float(r) for r in self.rates[1:] if r > RESOLVED * fastest]
        self.unresolved = len(self.rates) - 1 - len(self.resolved)

    def populations(self, start: int, time: float) -> np.ndarray:
        weights = self.vectors[start, :] / self.root[start]
        decay = np.exp(-np.maximum(self.rates, 0.0) * time)
        decay[0] = 1.0
        return self.root * (self.vectors @ (decay * weights))

    def deviation(self, start: int, time: float) -> float:
        return float(np.max(np.abs(self.populations(start, time) - self.pi)))

    def time_to_equilibrium(self, start: int) -> float | None:
        """The first time after which every population stays within WITHIN of equilibrium;
        None when the set relaxes too slowly to resolve."""
        if self.deviation(start, 0.0) <= WITHIN:
            return 0.0
        if not self.resolved or self.unresolved:
            return None
        slowest, fastest = 1 / min(self.resolved), 1 / max(self.resolved)
        grid = np.logspace(math.log10(fastest) - 3, math.log10(slowest) + 3, 600)
        above = [t for t in grid if self.deviation(start, t) > WITHIN]
        if not above:
            return float(grid[0])
        low = above[-1]
        high = next((t for t in grid if t > low), None)
        if high is None:  # still away from equilibrium at the end: not resolved
            return None
        for _ in range(60):
            middle = math.sqrt(low * high)
            if self.deviation(start, middle) > WITHIN:
                low = middle
            else:
                high = middle
        return high

    def curve(self, start: int, until: float) -> dict[str, Any]:
        fastest = 1 / max(self.resolved)
        times = np.logspace(math.log10(fastest / 100), math.log10(until), CURVE_POINTS)
        rows = [self.populations(start, t) for t in times]
        return {
            "times": [float(t) for t in times],
            "populations": [[float(max(row[i], 0.0)) for row in rows] for i in range(len(self.pi))],
        }


def bottlenecks(
    values: list[float], links: list[tuple[int, int, float, int]], start: int
) -> list[float | None]:
    """D171g: for each conformer, the highest point on the way from `start` whose highest point
    is lowest (a minimax path), or None when no link reaches it."""
    best: list[float | None] = [None] * len(values)
    best[start] = values[start]
    todo = {start}
    while todo:
        here = min(todo, key=lambda i: best[i])
        todo.discard(here)
        for i, j, top, _ in links:
            if here not in (i, j):
                continue
            other = j if here == i else i
            height = max(best[here], top, values[other])
            if best[other] is None or height < best[other] - 1e-15:
                best[other] = height
                todo.add(other)
    return best


# ---------- the result (D171) ----------


def _name_of(node: Node) -> str:
    return node.label or "Untitled node"


def _expand(session: Session, equilibrium: ConformerEquilibrium, notes: list[str]):
    """(node, count) for every live member, each node once; groups count with their members."""
    found: list[tuple[Node, int]] = []
    seen: set[str] = set()
    for entry in equilibrium.members:
        node = session.get(Node, entry["id"])
        if node is not None:
            nodes = [node]
        else:
            group = session.get(GroupNode, entry["id"])
            if group is None:
                notes.append("A member was deleted from the investigation and is left out.")
                continue
            nodes = list(
                session.scalars(select(Node).where(Node.group_id == group.id).order_by(Node.seq))
            )
            if not nodes:
                notes.append(f"Group “{group.label or 'Group'}” has no members.")
        for member in nodes:
            if member.id in seen:
                continue
            seen.add(member.id)
            if member.status in LEFT_OUT:
                notes.append(f"“{_name_of(member)}” is {member.status} and left out.")
                continue
            found.append((member, entry["count"]))
    return found


def _check_atoms(nodes: list[Node]) -> str | None:
    """D171a: conformers and their TSs are one molecule: the same atoms and charge."""
    for node in nodes:
        if not node.geometry:
            return f"“{_name_of(node)}” has no structure, so its atoms cannot be compared."
    first = nodes[0]
    atoms = Counter(row[0] for row in first.geometry)
    for node in nodes[1:]:
        other = Counter(row[0] for row in node.geometry)
        if other != atoms:
            return (
                f"Conformers and their TSs must have the same atoms: “{_name_of(first)}” is "
                f"{xyz.formula_of(atoms)}, “{_name_of(node)}” is {xyz.formula_of(other)}."
            )
    charged = [n for n in nodes if n.charge is not None]
    for node in charged[1:]:
        if node.charge != charged[0].charge:
            return (
                f"Conformers and their TSs must have the same charge: “{_name_of(charged[0])}” "
                f"has {charged[0].charge}, “{_name_of(node)}” has {node.charge}."
            )
    return None


def _drop_duplicates(rows: list[dict[str, Any]], nodes: dict[str, Node], tolerance: float):
    """As D156: a member with the same charge and multiplicity as an earlier one of its kind,
    within the duplicate tolerance in geometry and 0.1 kcal/mol in value, is the same structure
    counted twice. Returns the rows kept, {dropped id: kept id} and the notes."""
    kept: list[dict[str, Any]] = []
    twins: dict[str, str] = {}
    notes = []
    for row in rows:
        node = nodes[row["id"]]
        twin = None
        for other_row in kept:
            other = nodes[other_row["id"]]
            if (
                other_row["is_ts"] != row["is_ts"]
                or (other.charge, other.multiplicity) != (node.charge, node.multiplicity)
                or abs(other_row["value"] - row["value"]) > SAME_ENERGY
            ):
                continue
            rmsd = geometry.aligned_rmsd(node.geometry, other.geometry)
            if rmsd is not None and rmsd <= tolerance:
                twin = (other_row, rmsd)
                break
        if twin is None:
            kept.append(row)
            continue
        twins[row["id"]] = twin[0]["id"]
        notes.append(
            f"“{row['label']}” duplicates “{twin[0]['label']}” (RMSD {twin[1]:.3f} Å, the same "
            "energy) and counts once."
        )
    return kept, twins, notes


def result(
    session: Session,
    equilibrium: ConformerEquilibrium,
    settings: app_settings.Settings | None = None,
) -> dict[str, Any]:
    settings = settings or thermo_settings.settings(session)  # D159: the investigation's
    temperature = equilibrium.temperature or settings.qh_temperature
    found: dict[str, Any] = {
        "status": "ok",
        "message": None,
        "level": equilibrium.level,
        "level_label": None,
        "energy_type": equilibrium.energy_type,
        "temperature": temperature,
        "temperature_from_settings": equilibrium.temperature is None,
        "cutoff": settings.qh_cutoff,
        "standard_state": settings.standard_state,
        "job_temperatures": [],  # D160: of the frequency jobs H and G are read from
        "unit": settings.energy_unit,
        "lowest_id": None,
        "start_id": None,
        "conformers": [],
        "barriers": [],
        "sets": [],
        "notes": [],
    }
    notes: list[str] = found["notes"]

    def stop(status: str, message: str) -> dict[str, Any]:
        found["status"] = status
        found["message"] = message
        return found

    members = _expand(session, equilibrium, notes)
    conformers = [(n, c) for n, c in members if n.role != Role.TRANSITION_STATE]
    if len(conformers) < 2:
        return stop("incomplete", "Add at least two conformers (and the TSs between them).")
    for node, _ in conformers:
        if node.role != Role.MINIMUM:
            notes.append(
                f"“{_name_of(node)}” is not marked as a minimum; it counts as a conformer."
            )
    refused = _check_atoms([n for n, _ in members])
    if refused:
        return stop("refused", refused)
    if equilibrium.level is None:
        return stop("incomplete", "Choose a level of theory.")
    key = LevelKey.decode(equilibrium.level)
    if session.get(LevelOfTheory, key.level_id) is None or (
        key.geometry_level_id and session.get(LevelOfTheory, key.geometry_level_id) is None
    ):
        return stop("n/a", "This level of theory is no longer in the investigation.")
    found["level_label"] = key_label(session, key)

    # EN-3: one level and type, no fallback; G_qh at this analysis's temperature (S4).
    energies = Energies(session, settings, temperature=temperature)
    rows: list[dict[str, Any]] = []
    missing = []
    job_temperatures = set()
    for node, count in members:
        is_ts = node.role == Role.TRANSITION_STATE
        value = energies.value(node.id, key, equilibrium.energy_type)
        shown = check_text(value.check, ("W-TEMP",))  # D154; D160: W-TEMP once, below
        if shown is not None:
            notes.append(f"“{_name_of(node)}”: {shown}.")
        if value.details.get("job_temperature") is not None:
            job_temperatures.add(value.details["job_temperature"])
        if value.value is None:
            if is_ts:
                notes.append(
                    f"“{_name_of(node)}” has no {equilibrium.energy_type} at this level "
                    f"({value.message}), so it joins no conformers."
                )
            else:
                missing.append(f"“{_name_of(node)}” ({value.message})")
            continue
        rows.append(
            {
                "id": node.id,
                "label": _name_of(node),
                "is_ts": is_ts,
                "count": count,
                "value": value.value,
                "check": value.check,
            }
        )
    if equilibrium.energy_type in ("H", "G"):
        found["job_temperatures"] = distinct_temperatures(job_temperatures)
    if missing:
        return stop("n/a", f"No {equilibrium.energy_type} at this level for {', '.join(missing)}.")
    nodes = {n.id: n for n, _ in members}
    rows, twins, dropped = _drop_duplicates(rows, nodes, settings.duplicate_tolerance)
    notes.extend(dropped)
    if equilibrium.energy_type == "E":
        notes.append("E is the electronic energy, with no thermal correction.")
    elif equilibrium.energy_type in ("H", "G"):
        other = [t for t in found["job_temperatures"] if not same_temperature(t, temperature)]
        if other:
            notes.append(
                f"W-TEMP: {equilibrium.energy_type} is read from the files at the job "
                f"temperature ({temperatures_text(other)}); only the Boltzmann factors and "
                f"rates use {temperature:g} K. G_qh is recomputed at {temperature:g} K."
            )

    states = [r for r in rows if not r["is_ts"]]
    tops = [r for r in rows if r["is_ts"]]
    index = {r["id"]: i for i, r in enumerate(states)}
    values = [r["value"] for r in states]
    counts = [r["count"] for r in states]
    lowest = min(range(len(states)), key=lambda i: values[i])
    found["lowest_id"] = states[lowest]["id"]

    def conformer_at(record_id: str) -> int | None:
        """D171b: the conformer an edge end stands for: the node, or a group's representative
        (EN-7), with a duplicate standing for the one kept."""
        group = session.get(GroupNode, record_id)
        if group is not None:
            record_id = group.representative_id
        record_id = twins.get(record_id, record_id)
        return index.get(record_id)

    # D171b: links from the edges the user drew; direct edges join nothing (Q53).
    links: list[tuple[int, int, float, int]] = []
    for top in tops:
        ends: set[int] = set()
        for transition in transition_service.touching(session, [top["id"]]):
            if transition.status in LEFT_OUT:
                continue
            source, target = transition.source_id, transition.target_id
            other = target if source == top["id"] else source
            at = conformer_at(other)
            if at is not None:
                ends.add(at)
        top["joins"] = sorted(states[i]["id"] for i in ends)
        if len(ends) != 2:
            notes.append(
                f"“{top['label']}” has edges to {len(ends)} of the conformers, not two, so it "
                "joins none."
            )
            continue
        a, b = sorted(ends)
        links.append((a, b, top["value"], top["count"]))
        top["link"] = (a, b)
    joined = {frozenset((a, b)) for a, b, _, _ in links}
    direct: set[frozenset[int]] = set()
    for transition in transition_service.touching(session, [s["id"] for s in states]):
        if transition.status in LEFT_OUT or transition_service.is_ts(
            transition_service.endpoint(session, transition.source_id)
        ):
            continue
        if transition_service.is_ts(transition_service.endpoint(session, transition.target_id)):
            continue
        a, b = conformer_at(transition.source_id), conformer_at(transition.target_id)
        pair = frozenset((a, b))
        if a is None or b is None or a == b or pair in joined or pair in direct:
            continue
        direct.add(pair)
        notes.append(
            f"“{states[min(a, b)]['label']}” and “{states[max(a, b)]['label']}” are joined only "
            "by a direct edge (no TS, D53), so no exchange is computed between them."
        )

    # D171d, e: populations and rates.
    population = shares(values, counts, temperature)
    for state, share in zip(states, population, strict=True):
        state["relative"] = state["value"] - values[lowest]
        state["population"] = 100.0 * share
        state["set"] = None
        state["effective_barrier"] = None
        state["barrier_from_id"] = None
    rt = R_HARTREE * temperature
    for top in tops:
        top["relative"] = top["value"] - values[lowest]
        top.setdefault("joins", [])
        top["directions"] = []
        if "link" not in top:
            continue
        a, b = top.pop("link")
        height = max(top["value"], values[a], values[b])
        if height > top["value"]:
            below = a if values[a] > top["value"] else b
            notes.append(
                f"“{top['label']}” is {(height - top['value']) / KCAL:.2f} kcal/mol below "
                f"“{states[below]['label']}”, so it counts at that conformer's energy (ΔG‡ 0 "
                "from there)."
            )
        for i in (a, b):
            barrier = height - values[i]
            rate = top["count"] / counts[i] * eyring(barrier, temperature)
            top["directions"].append(
                {
                    "from_id": states[i]["id"],
                    "to_id": states[b if i == a else a]["id"],
                    "barrier": barrier,
                    "rate": rate,
                    "half_life": math.log(2) / rate,
                }
            )

    # D171f, g: each set of conformers joined by links, its relaxation and time course.
    parent = list(range(len(states)))

    def root(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for a, b, _, _ in links:
        parent[root(a)] = root(b)
    groups: dict[int, list[int]] = {}
    for i in range(len(states)):
        groups.setdefault(root(i), []).append(i)
    start = index.get(twins.get(equilibrium.start_id, equilibrium.start_id) or "", None)
    if equilibrium.start_id is not None and start is None:
        notes.append("The start conformer is not one of the conformers; the slowest start is used.")
    found["start_id"] = states[start]["id"] if start is not None else None
    ordered = sorted(groups.values(), key=lambda g: (lowest not in g, min(values[i] for i in g)))
    for number, members_of in enumerate(ordered):
        for i in members_of:
            states[i]["set"] = number
        entry: dict[str, Any] = {
            "conformer_ids": [states[i]["id"] for i in members_of],
            "start_id": None,
            "start_chosen": False,
            "relaxation_times": [],
            "unresolved": 0,
            "slowest": None,
            "equilibrium_time": None,
            "curve": None,
        }
        found["sets"].append(entry)
        local = {i: k for k, i in enumerate(members_of)}
        bottom = min(members_of, key=lambda i: values[i])
        heights = bottlenecks(values, links, bottom)
        for i in members_of:
            if i != bottom and heights[i] is not None:
                states[i]["effective_barrier"] = heights[i] - values[bottom]
                states[i]["barrier_from_id"] = states[bottom]["id"]
        if len(members_of) < 2:
            continue
        inner = [(local[a], local[b], top, n) for a, b, top, n in links if a in local]
        matrix = rate_matrix(
            [values[i] for i in members_of], [counts[i] for i in members_of], inner, temperature
        )
        pi = shares([values[i] for i in members_of], [counts[i] for i in members_of], temperature)
        solved = Relaxation(matrix, pi)
        entry["relaxation_times"] = [1 / r for r in solved.resolved]
        entry["unresolved"] = solved.unresolved
        entry["slowest"] = 1 / min(solved.resolved) if solved.resolved else None
        # A112: from the chosen start, else from the start that takes longest.
        if start in local:
            first, when = start, solved.time_to_equilibrium(local[start])
        else:
            times = {i: solved.time_to_equilibrium(local[i]) for i in members_of}
            first = max(members_of, key=lambda i: math.inf if times[i] is None else times[i])
            when = times[first]
        entry["start_id"] = states[first]["id"]
        entry["start_chosen"] = start in local
        entry["equilibrium_time"] = when
        if solved.resolved:
            until = 10 * (when or 0.0) or 10 * entry["slowest"]
            entry["curve"] = solved.curve(local[first], until)
        if solved.unresolved:
            notes.append(
                f"Some relaxation in the set of “{states[first]['label']}” is more than 10¹² "
                "times slower than the fastest and is not resolved."
            )
    others = [s for s in ordered[1:]]
    if others:
        names = ", ".join("“" + "”, “".join(states[i]["label"] for i in g) + "”" for g in others)
        notes.append(
            f"No computed path of TSs joins {names} to “{states[lowest]['label']}”: their shares "
            "come from the energies alone, and how fast they exchange is not known."
        )
    factor = math.exp(KCAL / rt)
    notes.append(
        f"An error of 1 kcal/mol in a ΔG changes a ratio by a factor of {factor:.1f} at "
        f"{temperature:g} K, and a rate by the same factor."
    )
    found["conformers"] = states
    found["barriers"] = tops
    return found
