"""Population % on a group's members (D197): the Boltzmann shares of a group's members at the
energy view's level and type, for the groups that show them. The rules are the conformer
equilibrium's (D171a, D156): rejected, failed and superseded members are left out, a duplicate
counts once, and the members must be one molecule in one spin state. A group of TSs gives each
TS's share of the flux over them instead (Curtin–Hammett)."""

from typing import Any

from sqlalchemy.orm import Session

from chembook3d import settings as app_settings
from chembook3d.models import GroupNode, Node, Role
from chembook3d.services import groups as group_service
from chembook3d.services.energies import Energies
from chembook3d.services.equilibrium import check_atoms, drop_duplicates, shares
from chembook3d.services.selectivity import LEFT_OUT


def _label(node: Node) -> str:
    return node.label or "Untitled node"


def of_group(
    session: Session,
    group: GroupNode,
    values: dict[str, Any],
    energy_type: str,
    temperature: float,
    tolerance: float,
) -> dict[str, Any]:
    """`values` are the energy view's ValueOut by id. Percentages are of the members counted;
    a member not counted has None and why."""
    found: dict[str, Any] = {
        "status": "ok",
        "message": None,
        "kind": "conformers",
        "type": energy_type,
        "temperature": temperature,
        "counted": 0,
        "total": 0,
        "members": {},
        "notes": [],
    }
    members = group_service.members(session, group.id)
    found["total"] = len(members)

    def stop(status: str, message: str) -> dict[str, Any]:
        found.update(status=status, message=message)
        for member in members:  # a member's own reason stays
            found["members"].setdefault(member.id, {"percent": None, "message": message})
        return found

    live = []
    for member in members:
        if member.status in LEFT_OUT:
            found["members"][member.id] = {"percent": None, "message": f"{member.status}, left out"}
        else:
            live.append(member)
    if not live:
        return stop("incomplete", "No member counts: all are rejected, failed or superseded.")
    ts = [m.role == Role.TRANSITION_STATE for m in live]
    if any(ts) and not all(ts):
        return stop(
            "refused",
            "The group mixes transition states and minima, so its members have no shares.",
        )
    if all(ts):
        found["kind"] = "transition_states"
    refused = check_atoms(live)
    if refused is not None:
        return stop("refused", refused)
    spins = {m.multiplicity for m in live if m.multiplicity is not None}
    if len(spins) > 1:
        return stop(
            "refused",
            "The members are in different spin states, so their shares are not one population.",
        )
    rows = []
    for member in live:
        own = values.get(member.id)
        if own is None or own.value is None:
            message = (own.message if own is not None else None) or "no value at this level"
            found["members"][member.id] = {"percent": None, "message": message}
            continue
        rows.append(
            {"id": member.id, "label": _label(member), "value": own.value, "is_ts": all(ts)}
        )
    rows, twins, notes = drop_duplicates(rows, {m.id: m for m in live}, tolerance)
    found["notes"].extend(notes)
    labels = {m.id: _label(m) for m in members}
    for dropped, kept in twins.items():
        found["members"][dropped] = {
            "percent": None,
            "message": f"duplicates “{labels[kept]}”, counted once",
        }
    found["counted"] = len(rows)
    if len(rows) < 2:
        return stop("incomplete", "Fewer than two members have a value here.")
    for row, share in zip(
        rows, shares([r["value"] for r in rows], [1] * len(rows), temperature), strict=True
    ):
        found["members"][row["id"]] = {"percent": 100 * share, "message": None}
    if energy_type in ("E", "H"):
        found["notes"].append(
            f"From {energy_type}, so these are Boltzmann weights without entropy, not "
            "populations; choose G or G_qh for populations."
        )
    if found["kind"] == "transition_states":
        found["notes"].append(
            "A group of transition states: each share is the part of the flux that goes over "
            "that TS (Curtin–Hammett), not a population."
        )
    return found


def of_groups(
    session: Session,
    energies: Energies,
    values: dict[str, Any],
    energy_type: str,
    settings: app_settings.Settings,
) -> dict[str, dict[str, Any]]:
    """Every group that shows its members' populations, at the energy view's temperature."""
    return {
        group.id: of_group(
            session,
            group,
            values,
            energy_type,
            energies.temperature,
            settings.duplicate_tolerance,
        )
        for group in energies.groups.values()
        if group.show_populations
    }
