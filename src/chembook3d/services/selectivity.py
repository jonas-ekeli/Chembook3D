"""Selectivity (D83, FR-SEL-01…06): ΔΔG‡ and the predicted product ratio from competing
transition states, traced back to the calculations.

- A selectivity has two or more named outcomes ("R" and "S", "Z" and "E"). Each is realised
  by TS nodes and groups; a group counts with all its members, as they are when computed.
- Every TS is valued at one composite level and energy type, with no fallback (EN-3, D27):
  one TS without a value makes the result n/a, with the reason.
- All TSs must have the same atoms and the same charge, or the selectivity is refused.
- With a reference (D103), each TS is balanced by the free species along its route from the
  reference, as its node card is (D69, D72): their energies are added to the TS's, and the
  atoms and charge compared are the TS's with its species. A TS not joined to the reference
  is refused; a species without a value makes the result n/a (EN-3).
- Curtin–Hammett: an outcome's weight is Σ e^(−G/RT) over its TSs ("boltzmann"), or only
  its lowest TS's ("lowest"). Both are computed; the chosen one leads.
- G_qh is recomputed at the selectivity's temperature from the stored frequencies (S4); E, H
  and G are used as read from the files, and the result says so.
- Outcomes and TSs are only ever chosen by the user, never from energies (X5, EN-10). The
  result says what was computed, not whether the chemistry is right (X6).
"""

import math
from collections import Counter
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from chembook3d import settings as app_settings
from chembook3d import xyz
from chembook3d.models import (
    GroupNode,
    LevelOfTheory,
    Node,
    NodeKind,
    Role,
    Selectivity,
    SelectivityMember,
    SelectivityOutcome,
)
from chembook3d.services import history
from chembook3d.services import species as species_service
from chembook3d.services.energies import ENERGY_TYPES, Energies, LevelKey, key_label
from chembook3d.services.records import RecordError, get, number_value, text_value
from chembook3d.units import GAS_CONSTANT, J_PER_MOL_HARTREE

R_HARTREE = GAS_CONSTANT / J_PER_MOL_HARTREE  # hartree/K, as the reference script's R
CONFORMERS = ("boltzmann", "lowest")
EXCESS = ("ee", "de", "none")
FIELDS = (
    "name",
    "level",
    "energy_type",
    "temperature",
    "conformers",
    "excess",
    "reference_id",
    "notes",
)


def list_all(session: Session) -> list[Selectivity]:
    return list(session.scalars(select(Selectivity).order_by(Selectivity.seq)))


def outcomes_value(selectivity: Selectivity) -> list[dict[str, Any]]:
    """The outcomes as written to the history and returned by the API."""
    return [
        {
            "id": outcome.id,
            "name": outcome.name,
            "members": [m.node_id or m.group_id for m in outcome.members],
            "experimental": outcome.experimental,
        }
        for outcome in selectivity.outcomes
    ]


def _snapshot(selectivity: Selectivity) -> dict[str, Any]:
    found = {field: getattr(selectivity, field) for field in FIELDS}
    found["outcomes"] = [
        {k: v for k, v in o.items() if k != "id"} for o in outcomes_value(selectivity)
    ]
    return found


def _name(session: Session, value: Any, own_id: str | None = None) -> str:
    name = text_value("name", value).strip()
    if not name:
        raise RecordError("a selectivity needs a name")
    clash = session.scalar(select(Selectivity).where(Selectivity.name == name))
    if clash is not None and clash.id != own_id:
        raise RecordError(f"there is already a selectivity called “{name}”")
    return name


def _field(session: Session, selectivity: Selectivity, field: str, value: Any) -> Any:
    if field == "name":
        return _name(session, value, selectivity.id)
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
    if field == "conformers":
        if value not in CONFORMERS:
            raise RecordError("conformers must be 'boltzmann' or 'lowest'")
        return value
    if field == "excess":
        if value not in EXCESS:
            raise RecordError("excess must be 'ee', 'de' or 'none'")
        return value
    if field == "reference_id":
        if value is None:
            return None
        record_id = text_value("reference", value)
        node = session.get(Node, record_id)
        if node is None:
            get(session, GroupNode, record_id, "Reference node or group")
        elif node.kind != NodeKind.NODE:
            raise RecordError("a free species cannot be the reference")
        return record_id
    if field == "notes":
        return text_value("notes", value)
    raise RecordError(f"'{field}' cannot be edited")


def _set_outcomes(session: Session, selectivity: Selectivity, value: Any) -> None:
    """Replace the outcomes: [{"name", "members": [node or group ids], "experimental"}]."""
    if not isinstance(value, list):
        raise RecordError("outcomes must be a list")
    outcomes = []
    names: set[str] = set()
    for position, entry in enumerate(value):
        if not isinstance(entry, dict):
            raise RecordError("each outcome needs a name and members")
        name = text_value("an outcome's name", entry.get("name", "")).strip()
        if not name:
            raise RecordError("each outcome needs a name")
        if name in names:
            raise RecordError(f"there are two outcomes called “{name}”")
        names.add(name)
        experimental = entry.get("experimental")
        if experimental is not None:
            experimental = number_value("an experimental amount", experimental)
            if experimental < 0:
                raise RecordError("an experimental amount cannot be negative")
        members = entry.get("members", [])
        if not isinstance(members, list):
            raise RecordError("an outcome's members must be a list")
        outcome = SelectivityOutcome(position=position, name=name, experimental=experimental)
        seen: set[str] = set()
        for index, record_id in enumerate(members):
            if not isinstance(record_id, str) or record_id in seen:
                raise RecordError(f"“{name}” lists the same member twice")
            seen.add(record_id)
            node = session.get(Node, record_id)
            if node is not None:
                if node.kind != NodeKind.NODE:
                    raise RecordError("a free species cannot be part of an outcome")
                outcome.members.append(SelectivityMember(position=index, node_id=node.id))
                continue
            group = get(session, GroupNode, record_id, "Node or group")
            outcome.members.append(SelectivityMember(position=index, group_id=group.id))
        outcomes.append(outcome)
    selectivity.outcomes.clear()
    session.flush()
    selectivity.outcomes.extend(outcomes)


def create(session: Session, fields: dict[str, Any]) -> Selectivity:
    selectivity = Selectivity(name=_name(session, fields.get("name", "")))
    for field, value in fields.items():
        if field == "outcomes":
            continue
        setattr(selectivity, field, _field(session, selectivity, field, value))
    session.add(selectivity)
    session.flush()
    _set_outcomes(session, selectivity, fields.get("outcomes", []))
    session.flush()
    history.record(session, "selectivity", selectivity.id, "create", new=_snapshot(selectivity))
    return selectivity


def update(session: Session, selectivity_id: str, changes: dict[str, Any]) -> Selectivity:
    """S7: every changed field is one history entry; the outcomes count as one field."""
    selectivity = get(session, Selectivity, selectivity_id, "Selectivity")
    for field, value in changes.items():
        if field == "outcomes":
            old = _snapshot(selectivity)["outcomes"]
            _set_outcomes(session, selectivity, value)
            session.flush()
            new = _snapshot(selectivity)["outcomes"]
        else:
            old = getattr(selectivity, field, None)
            new = _field(session, selectivity, field, value)
            setattr(selectivity, field, new)
        if old != new:
            history.record(session, "selectivity", selectivity.id, "update", field, old, new)
    session.flush()
    return selectivity


def delete(session: Session, selectivity_id: str) -> None:
    selectivity = get(session, Selectivity, selectivity_id, "Selectivity")
    history.record(session, "selectivity", selectivity.id, "delete", old=_snapshot(selectivity))
    session.delete(selectivity)
    session.flush()


# ---------- the result (S2–S6) ----------


def _name_of(node: Node) -> str:
    return node.label or "Untitled node"


def _members(session: Session, outcome: SelectivityOutcome) -> tuple[list, list[str]]:
    """(node, group or None) for every TS of the outcome, each node once; and notes."""
    found: list[tuple[Node, GroupNode | None]] = []
    notes = []
    seen: set[str] = set()
    for member in outcome.members:
        if member.node_id is not None:
            pairs = [(session.get(Node, member.node_id), None)]
        else:
            group = session.get(GroupNode, member.group_id)
            nodes = session.scalars(
                select(Node).where(Node.group_id == group.id).order_by(Node.seq)
            ).all()
            if not nodes:
                label = group.label or "Group"
                notes.append(f"Group “{label}” in “{outcome.name}” has no members.")
            pairs = [(node, group) for node in nodes]
        for node, group in pairs:
            if node is not None and node.id not in seen:
                seen.add(node.id)
                found.append((node, group))
    return found, notes


def _log_weight(values: list[float], base: float, temperature: float) -> float:
    """ln Σ e^(−(G − base)/RT), written so that no term underflows to zero."""
    rt = R_HARTREE * temperature
    lowest = min(values)
    return -(lowest - base) / rt + math.log(sum(math.exp(-(v - lowest) / rt) for v in values))


def _formula(counts: dict[str, int]) -> str:
    """A formula that may lack atoms, when a balance takes away more than a TS has."""
    found = xyz.formula_of(Counter({e: n for e, n in counts.items() if n > 0}))
    lacking = Counter({e: -n for e, n in counts.items() if n < 0})
    return f"{found} − {xyz.formula_of(lacking)}" if lacking else found


def _check_atoms(
    session: Session, rows: list[tuple[Node, str]], balances: dict[str, species_service.Balance]
) -> str | None:
    """S3: every TS has the same atoms and charge, with the free species of its balance when
    there is a reference (D103). The reason it is refused, or None."""
    counts: dict[str, dict[str, int]] = {}
    charges: dict[str, int | None] = {}
    named: dict[str, str] = {}
    for node, _ in rows:
        if not node.geometry:
            return f"“{_name_of(node)}” has no structure, so its atoms cannot be compared."
        atoms = dict(Counter(row[0] for row in node.geometry))
        charge = node.charge
        balance = balances.get(node.id, {})
        for species_id, count in balance.items():
            species = session.get(Node, species_id)
            if not species.geometry:
                return (
                    f"The free species “{species_service.label(species)}” has no structure, "
                    "so its atoms cannot be compared."
                )
            for element, n in Counter(row[0] for row in species.geometry).items():
                atoms[element] = atoms.get(element, 0) + count * n
            charge = (
                None
                if charge is None or species.charge is None
                else (charge + count * species.charge)
            )
        counts[node.id] = {e: n for e, n in atoms.items() if n}
        charges[node.id] = charge
        text = species_service.text(species_service.describe(session, balance))
        named[node.id] = f"“{_name_of(node)}” {text}" if text else f"“{_name_of(node)}”"
    with_species = " with their free species" if balances else ""
    first = rows[0][0]
    for node, _ in rows[1:]:
        if counts[node.id] != counts[first.id]:
            return (
                f"The transition states{with_species} must have the same atoms: "
                f"{named[first.id]} is {_formula(counts[first.id])}, "
                f"{named[node.id]} is {_formula(counts[node.id])}."
            )
    charged = [node for node, _ in rows if charges[node.id] is not None]
    for node in charged[1:]:
        if charges[node.id] != charges[charged[0].id]:
            return (
                f"The transition states{with_species} must have the same charge: "
                f"{named[charged[0].id]} has {charges[charged[0].id]}, "
                f"{named[node.id]} has {charges[node.id]}."
            )
    return None


def _experiment(outcomes: list[dict[str, Any]], temperature: float) -> str | None:
    """S6: the experimental ratio as percentages, and the ΔΔG‡ it corresponds to."""
    amounts = [o["experimental"] for o in outcomes]
    if all(a is None for a in amounts):
        return None
    if any(a is None for a in amounts) or sum(amounts) <= 0:
        return "Give an experimental amount for every outcome to compare with the experiment."
    total = sum(amounts)
    largest = max(amounts)
    for outcome, amount in zip(outcomes, amounts, strict=True):
        outcome["experimental_percent"] = 100.0 * amount / total
        outcome["experimental_ddg"] = (
            -R_HARTREE * temperature * math.log(amount / largest) if amount > 0 else None
        )
    return None


def _excess(selectivity: Selectivity, outcomes: list[dict[str, Any]]) -> dict | None:
    """S5: for two outcomes, the ee (or de): the difference of the two percentages."""
    if selectivity.excess == "none" or len(outcomes) != 2:
        return None
    first, second = outcomes

    def difference(key: str) -> dict[str, Any] | None:
        a, b = first[key], second[key]
        if a is None or b is None:
            return None
        return {"value": abs(a - b), "major": first["name"] if a >= b else second["name"]}

    return {
        "label": selectivity.excess,
        "boltzmann": difference("boltzmann_percent"),
        "lowest": difference("lowest_percent"),
        "experimental": difference("experimental_percent"),
    }


def result(
    session: Session, selectivity: Selectivity, settings: app_settings.Settings | None = None
) -> dict[str, Any]:
    settings = settings or app_settings.load()
    temperature = selectivity.temperature or settings.qh_temperature
    found: dict[str, Any] = {
        "status": "ok",
        "message": None,
        "level": selectivity.level,
        "level_label": None,
        "energy_type": selectivity.energy_type,
        "temperature": temperature,
        "temperature_from_settings": selectivity.temperature is None,
        "cutoff": settings.qh_cutoff,
        "standard_state": settings.standard_state,
        "conformers": selectivity.conformers,
        "reference_id": selectivity.reference_id,
        "reference_label": None,
        "outcomes": [],
        "excess": None,
        "notes": [],
    }
    notes: list[str] = found["notes"]

    def stop(status: str, message: str) -> dict[str, Any]:
        found["status"] = status
        found["message"] = message
        found["excess"] = _excess(selectivity, found["outcomes"])
        return found

    rows: list[tuple[Node, str]] = []  # every TS with its outcome's id
    owner: dict[str, str] = {}
    for outcome in selectivity.outcomes:
        members, member_notes = _members(session, outcome)
        notes.extend(member_notes)
        entry = {
            "id": outcome.id,
            "name": outcome.name,
            "experimental": outcome.experimental,
            "members": [],
            "boltzmann_ddg": None,
            "boltzmann_percent": None,
            "lowest_ddg": None,
            "lowest_percent": None,
            "experimental_percent": None,
            "experimental_ddg": None,
        }
        for node, group in members:
            entry["members"].append(
                {
                    "node_id": node.id,
                    "label": _name_of(node),
                    "group_id": group.id if group else None,
                    "group_label": (group.label or "Group") if group else None,
                    "role": node.role,
                    "value": None,
                    "relative": None,
                    "share": None,
                    "share_in_outcome": None,
                    "species": [],
                    "species_value": None,
                    "message": None,
                }
            )
            if node.role != Role.TRANSITION_STATE:
                notes.append(f"“{_name_of(node)}” is not marked as a transition state.")
            if node.id in owner and owner[node.id] != outcome.name:
                return stop(
                    "refused",
                    f"“{_name_of(node)}” is in both “{owner[node.id]}” and “{outcome.name}”.",
                )
            owner[node.id] = outcome.name
            rows.append((node, outcome.id))
        found["outcomes"].append(entry)

    if len(found["outcomes"]) >= 2:
        message = _experiment(found["outcomes"], temperature)
        if message:
            notes.append(message)
    if len(found["outcomes"]) < 2 or any(not o["members"] for o in found["outcomes"]):
        return stop("incomplete", "Add at least two outcomes, each with a transition state.")
    # D103: the balance of every TS along its route from the reference (D72).
    balances: dict[str, species_service.Balance] = {}
    if selectivity.reference_id is not None:
        reference = session.get(Node, selectivity.reference_id) or session.get(
            GroupNode, selectivity.reference_id
        )
        if reference is None:
            return stop(
                "n/a",
                "The reference is no longer in the investigation. Choose another, or none.",
            )
        found["reference_label"] = reference.label or (
            "Group" if isinstance(reference, GroupNode) else "Untitled node"
        )
        graph = species_service.balances_from(session, reference.id)
        apart = [_name_of(node) for node, _ in rows if node.id not in graph]
        if apart:
            names = ", ".join(f"“{name}”" for name in apart)
            verb, own = ("is", "its") if len(apart) == 1 else ("are", "their")
            return stop(
                "refused",
                f"{names} {verb} not joined to the reference “{found['reference_label']}” by "
                f"transitions, so {own} free species are not known. Choose another reference, "
                "or none.",
            )
        balances = {node.id: graph[node.id] for node, _ in rows}
        for outcome in found["outcomes"]:
            for member in outcome["members"]:
                member["species"] = species_service.describe(session, balances[member["node_id"]])
    refused = _check_atoms(session, rows, balances)
    if refused:
        return stop("refused", refused)
    if selectivity.level is None:
        return stop("incomplete", "Choose a level of theory.")
    key = LevelKey.decode(selectivity.level)
    if session.get(LevelOfTheory, key.level_id) is None or (
        key.geometry_level_id and session.get(LevelOfTheory, key.geometry_level_id) is None
    ):
        return stop("n/a", "This level of theory is no longer in the investigation.")
    found["level_label"] = key_label(session, key)

    # S3, S4: one level and type, no fallback; G_qh at this selectivity's temperature.
    energies = Energies(session, settings, temperature=temperature)
    missing = []
    job_temperatures = set()
    for outcome in found["outcomes"]:
        for member in outcome["members"]:
            value = energies.value(member["node_id"], key, selectivity.energy_type)
            member["value"] = value.value
            if value.value is None:
                member["message"] = value.message
                missing.append(f"“{member['label']}” ({value.message})")
                continue
            if value.details.get("job_temperature") is not None:
                job_temperatures.add(value.details["job_temperature"])
            balance = balances.get(member["node_id"], {})
            added, message = species_service.energy(
                session, energies, balance, key, selectivity.energy_type
            )
            if message is not None:
                member["value"] = None
                member["message"] = message
                missing.append(f"“{member['label']}” ({message})")
            elif balance:
                member["species_value"] = added
                member["value"] = value.value + added
    if missing:
        return stop("n/a", f"No {selectivity.energy_type} at this level for {', '.join(missing)}.")
    if selectivity.energy_type == "E":
        notes.append("E is the electronic energy, with no thermal correction.")
    elif selectivity.energy_type in ("H", "G"):
        other = sorted(t for t in job_temperatures if abs(t - temperature) > 1e-6)
        if other:
            shown = ", ".join(f"{t:g} K" for t in other)
            notes.append(
                f"{selectivity.energy_type} is read from the files at the job temperature "
                f"({shown}); only the Boltzmann factors use {temperature:g} K. "
                f"G_qh is recomputed at {temperature:g} K."
            )

    # S2, S5: ΔΔG‡ from the lowest outcome and the predicted ratio, both ways.
    every = [m["value"] for o in found["outcomes"] for m in o["members"]]
    base = min(every)
    rt = R_HARTREE * temperature
    for method in CONFORMERS:
        logs = []
        for outcome in found["outcomes"]:
            values = [m["value"] for m in outcome["members"]]
            if method == "lowest":
                values = [min(values)]
            logs.append(_log_weight(values, base, temperature))
        top = max(logs)
        total = sum(math.exp(w - top) for w in logs)
        for outcome, weight in zip(found["outcomes"], logs, strict=True):
            outcome[f"{method}_ddg"] = -rt * (weight - top)
            outcome[f"{method}_percent"] = 100.0 * math.exp(weight - top) / total
    total_weight = _log_weight(every, base, temperature)
    for outcome in found["outcomes"]:
        values = [m["value"] for m in outcome["members"]]
        own = _log_weight(values, base, temperature)
        for member in outcome["members"]:
            weight = -(member["value"] - base) / rt
            member["relative"] = member["value"] - base
            member["share"] = 100.0 * math.exp(weight - total_weight)
            member["share_in_outcome"] = 100.0 * math.exp(weight - own)
    found["excess"] = _excess(selectivity, found["outcomes"])
    return found
