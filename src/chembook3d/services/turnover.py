"""Turnover (D86, FR-TOF-01…05): the TOF of a closed catalytic cycle from the energetic-span
model (Kozuch and Shaik, Acc. Chem. Res. 2011, 44, 101).

- A turnover is one closed pathway (A13): its last node returns to an earlier one. The cycle
  runs from that node round to it again; points before it (an activation, say) are left out.
- Every point is valued at one composite level and energy type, with no fallback (EN-3, D27),
  relative to the cycle's first node and balanced by the free species along the way (D69), so
  the closing point is the reaction energy ΔG_r. One point without a value makes it n/a.
- TOF = (k_BT/h)·(e^(−ΔG_r/RT) − 1) / Σ_ij e^((T_i − I_j − δG'_ij)/RT), with δG'_ij = ΔG_r
  when TS i comes after intermediate j in the cycle and 0 otherwise. The degree of TOF control
  of each TS and intermediate is its share of that sum; the TDTS and TDI have the largest.
  δE = T_TDTS − I_TDI, plus ΔG_r when the TDTS comes before the TDI.
- A point is a TS when its node (or a group's representative, EN-7) is marked as one; every
  other point is an intermediate. A "no TS" connection (D53) adds no barrier, so the TOF is
  an upper bound there, and the result says so.
- G_qh is recomputed at the turnover's temperature (as S4). The app never picks the pathway
  from energies (X5), and the result says what was computed, not whether it is right (X6).
"""

import math
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from chembook3d import settings as app_settings
from chembook3d.models import GroupNode, LevelOfTheory, Node, NodeKind, Role, Turnover
from chembook3d.services import history, pathways
from chembook3d.services import species as species_service
from chembook3d.services import transitions as transition_service
from chembook3d.services.energies import ENERGY_TYPES, Energies, LevelKey, key_label
from chembook3d.services.records import RecordError, get, number_value, text_value
from chembook3d.units import BOLTZMANN, GAS_CONSTANT, J_PER_MOL_HARTREE, PLANCK

R_HARTREE = GAS_CONSTANT / J_PER_MOL_HARTREE  # hartree/K, as the reference script's R
K_OVER_H = BOLTZMANN / PLANCK  # 1/(K s)
FIELDS = ("name", "path", "level", "energy_type", "temperature", "compare_id", "notes")


def list_all(session: Session) -> list[Turnover]:
    return list(session.scalars(select(Turnover).order_by(Turnover.seq)))


def _snapshot(turnover: Turnover) -> dict[str, Any]:
    return {field: getattr(turnover, field) for field in FIELDS}


def _name(session: Session, value: Any, own_id: str | None = None) -> str:
    name = text_value("name", value).strip()
    if not name:
        raise RecordError("a turnover needs a name")
    clash = session.scalar(select(Turnover).where(Turnover.name == name))
    if clash is not None and clash.id != own_id:
        raise RecordError(f"there is already a turnover called “{name}”")
    return name


def _path(session: Session, value: Any) -> list[str]:
    """T1: a closed pathway along existing transitions, or none yet."""
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise RecordError("the pathway must be a list of node and group ids")
    if not value:
        return []
    for record_id in value:
        node = session.get(Node, record_id)
        if node is not None and node.kind != NodeKind.NODE:
            raise RecordError("a free species cannot be a point of the cycle")
    pathways.resolve(session, value)
    if not pathways.closed(value):
        raise RecordError(
            "A turnover needs a closed cycle: a pathway whose last node returns to an earlier "
            "one (A13)"
        )
    return list(value)


def _field(session: Session, turnover: Turnover, field: str, value: Any) -> Any:
    if field == "name":
        return _name(session, value, turnover.id)
    if field == "path":
        return _path(session, value)
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
    if field == "compare_id":
        if value is None:
            return None
        other = get(session, Turnover, text_value("compare_id", value), "Turnover")
        if other.id == turnover.id:
            raise RecordError("a turnover cannot be compared with itself")
        return other.id
    if field == "notes":
        return text_value("notes", value)
    raise RecordError(f"'{field}' cannot be edited")


def create(session: Session, fields: dict[str, Any]) -> Turnover:
    turnover = Turnover(name=_name(session, fields.get("name", "")))
    session.add(turnover)
    session.flush()
    for field, value in fields.items():
        setattr(turnover, field, _field(session, turnover, field, value))
    session.flush()
    history.record(session, "turnover", turnover.id, "create", new=_snapshot(turnover))
    return turnover


def update(session: Session, turnover_id: str, changes: dict[str, Any]) -> Turnover:
    """Every changed field is one history entry (as S7)."""
    turnover = get(session, Turnover, turnover_id, "Turnover")
    for field, value in changes.items():
        old = getattr(turnover, field, None)
        new = _field(session, turnover, field, value)
        setattr(turnover, field, new)
        if old != new:
            history.record(session, "turnover", turnover.id, "update", field, old, new)
    session.flush()
    return turnover


def delete(session: Session, turnover_id: str) -> None:
    turnover = get(session, Turnover, turnover_id, "Turnover")
    history.record(session, "turnover", turnover.id, "delete", old=_snapshot(turnover))
    session.delete(turnover)
    session.flush()


# ---------- the result (T1–T5) ----------


def _log_sum(exponents: list[float]) -> float:
    """ln Σ e^x, written so that no term overflows."""
    top = max(exponents)
    return top + math.log(sum(math.exp(x - top) for x in exponents))


def _exp(x: float) -> float:
    return math.inf if x > 709 else math.exp(x)


def _role(session: Session, point: dict[str, Any]) -> str | None:
    """The role of a point's node, or of a group's representative (EN-7)."""
    if point["kind"] == "node":
        return session.get(Node, point["id"]).role
    group = session.get(GroupNode, point["id"])
    representative = session.get(Node, group.representative_id) if group.representative_id else None
    return representative.role if representative else None


def span_model(
    values: list[float], is_ts: list[bool], reaction: float, temperature: float
) -> dict[str, Any]:
    """T2: the energetic-span model for the states of one cycle in order (hartree, relative to
    the first) and the reaction energy ΔG_r (the first state one turnover later)."""
    rt = R_HARTREE * temperature
    states = range(len(values))
    tss = [i for i in states if is_ts[i]]
    intermediates = [j for j in states if not is_ts[j]]
    exponent = {
        (i, j): (values[i] - values[j] - (reaction if i > j else 0.0)) / rt
        for i in tss
        for j in intermediates
    }
    total = _log_sum(list(exponent.values()))
    control = [0.0 for _ in states]
    for i in tss:
        control[i] = math.exp(_log_sum([exponent[i, j] for j in intermediates]) - total)
    for j in intermediates:
        control[j] = math.exp(_log_sum([exponent[i, j] for i in tss]) - total)
    tdts = max(tss, key=lambda i: control[i])
    tdi = max(intermediates, key=lambda j: control[j])
    span = values[tdts] - values[tdi] + (reaction if tdts < tdi else 0.0)
    # (e^x − 1) with x = −ΔG_r/RT, in logs so a very exergonic cycle does not overflow.
    x = -reaction / rt
    prefactor = K_OVER_H * temperature
    if x > 0:
        tof = prefactor * _exp(x + math.log1p(-math.exp(-x)) - total)
    elif x < 0:
        tof = -prefactor * _exp(math.log(-math.expm1(x)) - total)
    else:
        tof = 0.0
    return {
        "tof": tof,
        "tof_span": prefactor * _exp(-span / rt),
        "span": span,
        "control": control,
        "tdts": tdts,
        "tdi": tdi,
    }


def _table(found: dict[str, Any], points: list[dict[str, Any]], unit: str) -> dict[str, Any]:
    """T4: one row per state of the cycle and one for its closing point, already formatted so
    the screen and the CSV show the same text (as T-EN-08)."""
    columns = [
        "Label",
        "Kind",
        "Step",
        "Level",
        "Free species",
        f"Δ{found['energy_type']} ({unit})",
        "Degree of TOF control (%)",
        "Determining",
    ]
    rows = []
    for index, point in enumerate(points):
        closing = index == len(points) - 1
        state = None if closing else found["points"][index]
        determining = ""
        if state is not None and index == found["tdts"]:
            determining = "TDTS"
        elif state is not None and index == found["tdi"]:
            determining = "TDI"
        rows.append(
            [
                point["label"] + (" (cycle closed)" if closing else ""),
                "" if closing else ("TS" if state["is_ts"] else "intermediate"),
                point["step_name"] or "",
                found["level_label"],
                species_service.text(point["species"]),
                pathways.relative_text(point["relative"], unit),
                "" if closing else f"{100.0 * state['control']:.1f}",
                determining,
            ]
        )
    return {"columns": columns, "rows": rows}


def result(
    session: Session,
    turnover: Turnover,
    settings: app_settings.Settings | None = None,
    *,
    compare: bool = True,
) -> dict[str, Any]:
    settings = settings or app_settings.load()
    temperature = turnover.temperature or settings.qh_temperature
    found: dict[str, Any] = {
        "status": "ok",
        "message": None,
        "level": turnover.level,
        "level_label": None,
        "energy_type": turnover.energy_type,
        "temperature": temperature,
        "temperature_from_settings": turnover.temperature is None,
        "cutoff": settings.qh_cutoff,
        "unit": settings.energy_unit,
        "cycle": [],  # ids from the closing node round to it again
        "points": [],  # one per state of the cycle (the closing repeat left out)
        "reaction": None,
        "tof": None,
        "tof_span": None,
        "span": None,
        "tdts": None,  # index into points
        "tdi": None,
        "profile": None,
        "table": None,
        "comparison": None,
        "notes": [],
    }
    notes: list[str] = found["notes"]

    def stop(status: str, message: str) -> dict[str, Any]:
        found["status"] = status
        found["message"] = message
        return found

    path = list(turnover.path)
    if not path:
        return stop(
            "incomplete",
            "Choose a closed pathway: take a branch's, or use “Turnover” on a closed pathway "
            "in the energy drawer.",
        )
    if any(session.get(Node, i) is None and session.get(GroupNode, i) is None for i in path):
        return stop("refused", "A node on the pathway was deleted; choose the pathway again.")
    try:
        pathways.resolve(session, path)
    except RecordError as error:
        return stop("refused", f"{error}. Choose the pathway again.")
    if not pathways.closed(path):
        return stop("refused", "The pathway does not close a cycle (A13).")
    start = path.index(path[-1])
    cycle = path[start:]
    found["cycle"] = cycle
    if start > 0:
        before = [transition_service.endpoint(session, i) for i in path[:start]]
        names = ", ".join(f"“{e.label or 'Untitled node'}”" for e in before)
        notes.append(f"{names} come before the cycle closes and are left out.")
    if turnover.level is None:
        return stop("incomplete", "Choose a level of theory.")
    key = LevelKey.decode(turnover.level)
    if session.get(LevelOfTheory, key.level_id) is None or (
        key.geometry_level_id and session.get(LevelOfTheory, key.geometry_level_id) is None
    ):
        return stop("n/a", "This level of theory is no longer in the investigation.")
    found["level_label"] = key_label(session, key)

    # T1: one level and type, no fallback, relative to the cycle's first node and balanced
    # by the free species along the cycle (D69), so the closing point is ΔG_r.
    energies = Energies(session, settings, temperature=temperature)
    data = pathways.profiles(
        session, [cycle], cycle[0], key, turnover.energy_type, energies=energies
    )
    found["profile"] = data
    points = data["profiles"][0]["points"]
    segments = data["profiles"][0]["segments"]
    missing = []
    job_temperatures = set()
    for point in points[:-1]:
        role = _role(session, point)
        found["points"].append(
            {
                "id": point["id"],
                "label": point["label"],
                "kind": point["kind"],
                "is_ts": role == Role.TRANSITION_STATE,
                "relative": point["relative"],
                "control": None,
            }
        )
        if role == Role.UNSPECIFIED:
            notes.append(
                f"“{point['label']}” is not marked as a minimum or a transition state; it "
                "counts as an intermediate."
            )
    for point in points:
        if point["relative"] is None:
            reason = point["message"] if point["value"] is None else point["species_message"]
            entry = f"“{point['label']}” ({reason or 'no value'})"
            if entry not in missing:
                missing.append(entry)
        elif point["details"].get("job_temperature") is not None:
            job_temperatures.add(point["details"]["job_temperature"])
    if data["reference_value"] is None:
        first = points[0]
        return stop(
            "n/a",
            f"No {turnover.energy_type} at this level for “{first['label']}” "
            f"({first['message'] or 'no value'}).",
        )
    if missing:
        return stop("n/a", f"No {turnover.energy_type} at this level for {', '.join(missing)}.")
    for segment, (a, b) in zip(segments, zip(points, points[1:], strict=False), strict=True):
        if segment["direct"]:
            notes.append(
                f"“{a['label']}” → “{b['label']}” has no TS (D53): it adds no barrier, so the "
                "TOF is an upper bound."
            )
    if turnover.energy_type == "E":
        notes.append("E is the electronic energy, with no thermal correction.")
    elif turnover.energy_type in ("H", "G"):
        other = sorted(t for t in job_temperatures if abs(t - temperature) > 1e-6)
        if other:
            shown = ", ".join(f"{t:g} K" for t in other)
            notes.append(
                f"{turnover.energy_type} is read from the files at the job temperature "
                f"({shown}); only the rate expression uses {temperature:g} K. "
                f"G_qh is recomputed at {temperature:g} K."
            )

    states = found["points"]
    if not any(s["is_ts"] for s in states):
        return stop(
            "refused",
            "The cycle has no transition state, so its energetic span is not defined. Mark the "
            "TSs on the cycle as transition states.",
        )
    if all(s["is_ts"] for s in states):
        return stop("refused", "The cycle has no intermediate, only transition states.")

    reaction = points[-1]["relative"]
    found["reaction"] = reaction
    model = span_model(
        [s["relative"] for s in states], [s["is_ts"] for s in states], reaction, temperature
    )
    for state, control in zip(states, model["control"], strict=True):
        state["control"] = control
    for field in ("tof", "tof_span", "span", "tdts", "tdi"):
        found[field] = model[field]
    if reaction >= 0:
        notes.append(
            f"ΔG_r is not negative, so the cycle as drawn does not turn over forward: the TOF is "
            f"{'zero' if reaction == 0 else 'negative (the reverse cycle runs)'}."
        )
    found["table"] = _table(found, points, settings.energy_unit)

    if compare and turnover.compare_id is not None:
        other = session.get(Turnover, turnover.compare_id)
        found["comparison"] = _compare(session, found, other, settings)
    return found


def _compare(
    session: Session, found: dict[str, Any], other: Turnover, settings: app_settings.Settings
) -> dict[str, Any]:
    """T5: the TOF ratio of two cycles read as a selectivity: this one's share, and the
    effective ΔΔG‡ = RT ln(TOF_this / TOF_other)."""
    theirs = result(session, other, settings, compare=False)
    comparison: dict[str, Any] = {
        "id": other.id,
        "name": other.name,
        "tof": theirs["tof"],
        "ratio": None,
        "percent": None,
        "ddg": None,
        "message": None,
    }
    if theirs["status"] != "ok":
        comparison["message"] = f"“{other.name}” has no result: {theirs['message']}"
        return comparison
    same = (
        theirs["level"] == found["level"]
        and theirs["energy_type"] == found["energy_type"]
        and abs(theirs["temperature"] - found["temperature"]) < 1e-9
    )
    if not same:
        comparison["message"] = (
            f"“{other.name}” is at another level of theory, energy type or temperature; "
            "turnovers are compared only at the same ones (EN-3)."
        )
        return comparison
    mine, yours = found["tof"], theirs["tof"]
    if not (mine > 0 and yours > 0 and math.isfinite(mine) and math.isfinite(yours)):
        comparison["message"] = "Both cycles need a positive, finite TOF to be compared."
        return comparison
    ratio = mine / yours
    comparison["ratio"] = ratio
    comparison["percent"] = 100.0 * mine / (mine + yours)
    comparison["ddg"] = R_HARTREE * found["temperature"] * math.log(ratio)
    return comparison
