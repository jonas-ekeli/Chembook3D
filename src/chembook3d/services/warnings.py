"""Automatic, non-blocking findings (docs/spec/02 §4, D19). Warnings are computed from the
records each time and never change a status."""

from dataclasses import asdict, dataclass

from chembook3d import settings as app_settings
from chembook3d.models import Calculation, CalculationType, Node, Role
from chembook3d.services import geometry

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
    if calculation.geometry and node.geometry:
        if tolerance is None:
            tolerance = app_settings.load().geometry_tolerance
        if not geometry.matches(calculation.geometry, node.geometry, tolerance):
            found.append(
                Finding("W-GEOM", "geometry does not match the node's geometry", calculation.id)
            )
    return found


def node_warnings(node: Node) -> list[Finding]:
    found: list[Finding] = []
    calculations = list(node.calculations)
    if OPTIMIZATION_INCOMPLETE in node.tags:
        found.append(Finding("W-OPT-INC", "the optimization did not finish"))

    frequency = next(
        (
            c
            for c in sorted(calculations, key=lambda c: c.created_at, reverse=True)
            if c.type in FREQUENCY_TYPES and c.result and c.result.imaginary_count is not None
        ),
        None,
    )
    if frequency is not None:
        count = frequency.result.imaginary_count
        if node.role == Role.MINIMUM and count >= 1:
            found.append(
                Finding("W-IMAG-MIN", f"minimum with {count} imaginary frequencies", frequency.id)
            )
        if node.role == Role.TRANSITION_STATE and count != 1:
            found.append(
                Finding(
                    "W-IMAG-TS",
                    f"transition state with {count} imaginary frequencies (expected 1)",
                    frequency.id,
                )
            )

    states = {(c.charge, c.multiplicity) for c in calculations if c.charge is not None}
    if node.charge is not None and node.multiplicity is not None and states:
        states.add((node.charge, node.multiplicity))
    if len(states) > 1:
        listed = ", ".join(f"charge {c} mult {m}" for c, m in sorted(states))
        found.append(Finding("W-CHG", f"charge or multiplicity disagree ({listed})"))

    tolerance = app_settings.load().geometry_tolerance if calculations else None
    for calculation in calculations:
        found += calculation_warnings(calculation, node, tolerance)
    return found
