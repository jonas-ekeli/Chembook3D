"""Automatic, non-blocking findings (docs/spec/02 §4, D19). Warnings are computed from the
records each time and never change a status."""

from dataclasses import asdict, dataclass

from sqlalchemy.orm import object_session

from chembook3d import settings as app_settings
from chembook3d.models import Calculation, CalculationType, Node, Role
from chembook3d.services import energies, geometry, levels, thermo_settings

OPTIMIZATION_INCOMPLETE = "optimization-incomplete"  # system tag (D26)
FREQUENCY_TYPES = (CalculationType.FREQUENCY,)


@dataclass
class Finding:
    code: str
    message: str
    calculation_id: str | None = None

    def as_dict(self) -> dict:
        return asdict(self)


def calculation_warnings(
    calculation: Calculation, node: Node, tolerance: float | None = None
) -> list[Finding]:
    found = []
    if calculation.termination == "abnormal":
        found.append(Finding("W-TERM", "did not terminate normally", calculation.id))
    if calculation.parse_warnings:
        missing = ", ".join(calculation.parse_warnings)
        found.append(Finding("W-PARSE", f"not read from the file: {missing}", calculation.id))
    if calculation.constraints:
        # D157: a scan or an optimization held by frozen coordinates or atoms.
        held = ", ".join(calculation.constraints)
        found.append(
            Finding(
                "W-CONSTRAINED",
                f"scan or constrained job ({held}): its structure is no free stationary "
                "point and gives no geometry level",
                calculation.id,
            )
        )
    unmet = energies.not_stationary(calculation)
    if unmet is not None:
        # D158: Gaussian's check after the frequency job did not find a stationary point.
        level = levels.label(calculation.level) or "an unknown level"
        found.append(
            Finding(
                "W-FREQ-NONSTAT",
                f"the frequency job at {level} is not at a stationary point ({unmet})",
                calculation.id,
            )
        )
    if calculation.geometry and node.geometry:
        if tolerance is None:
            tolerance = app_settings.load().geometry_tolerance
        if not geometry.matches(calculation.geometry, node.geometry, tolerance):
            found.append(
                Finding("W-GEOM", "geometry does not match the node's geometry", calculation.id)
            )
    return found


def _frequency_jobs(node: Node) -> list[tuple[str, Calculation]]:
    """D158: the latest frequency job at each composite level of the node, with the level's
    label, as `energies.NodeEnergies` uses them (another state's are left out, D155)."""
    if not any(c.type in FREQUENCY_TYPES for c in node.calculations):
        return []
    session = object_session(node)
    settings = thermo_settings.settings(session)  # D159: the investigation's
    found = energies.NodeEnergies(node, settings.qh_temperature, settings.qh_cutoff)
    jobs = []
    for key, calculation in found.frequency_jobs():
        if calculation.result is None:
            continue
        if session is not None:
            label = energies.key_label(session, key)
        else:
            label = levels.label(calculation.level)
        jobs.append((label, calculation))
    return jobs


def node_warnings(node: Node) -> list[Finding]:
    found: list[Finding] = []
    calculations = list(node.calculations)
    if OPTIMIZATION_INCOMPLETE in node.tags:
        found.append(Finding("W-OPT-INC", "the optimization did not finish"))

    # D158: every frequency job the energies use (the latest per composite level) is judged
    # against the node's role, naming its level, so a good job at one level never hides a
    # wrong one at another. A job not at a stationary point is each calculation's own warning.
    frequencies = _frequency_jobs(node)
    for label, frequency in frequencies:
        for issue in energies.frequency_issues(node.role, frequency, f"at {label}"):
            if issue["code"] == "W-FREQ-NONSTAT":
                continue  # every frequency job's own, below
            message = issue["message"]
            if issue["code"] == "W-IMAG-MIN":
                message = f"minimum with {message}"
            elif issue["code"] == "W-IMAG-TS":
                message = f"transition state with {message.replace(', not 1', '')} (expected 1)"
            found.append(Finding(issue["code"], message, frequency.id))

    if not frequencies and node.role == Role.TRANSITION_STATE and calculations:
        if not any(c.type in FREQUENCY_TYPES for c in calculations):
            # D154: a TS with calculations but no frequency job is not shown to be one.
            found.append(Finding("W-TS-NOFREQ", "transition state with no frequency calculation"))

    states = {(c.charge, c.multiplicity) for c in calculations if c.charge is not None}
    own = (node.charge, node.multiplicity) if node.charge is not None else None
    if own is not None and states - {own}:
        # D155: those calculations are another electronic state and give no energies here.
        listed = ", ".join(f"charge {c} mult {m}" for c, m in sorted(states - {own}, key=str))
        found.append(
            Finding(
                "W-CHG",
                f"calculations for {listed} are left out of the energies of this node "
                f"(charge {own[0]} mult {own[1]})",
            )
        )
    elif len(states) > 1:
        listed = ", ".join(f"charge {c} mult {m}" for c, m in sorted(states, key=str))
        found.append(Finding("W-CHG", f"charge or multiplicity disagree ({listed})"))

    tolerance = app_settings.load().geometry_tolerance if calculations else None
    for calculation in calculations:
        found += calculation_warnings(calculation, node, tolerance)
    return found
