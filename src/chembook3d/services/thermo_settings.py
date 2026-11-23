"""The investigation's thermochemistry settings (D159): the G_qh temperature (K) and cutoff
(cm⁻¹) of D58 and the standard state of G and G_qh of D95. They are stored in the investigation,
so a copy synced to another computer gives the same G_qh, and every change is in its history
(record type "thermochemistry"). The app's settings keep the same three values only as the
default a new investigation starts from, and an investigation saved before D159 takes them once
when it is next opened (the repair `thermochemistry`), so nothing shifts.

Everything that values G or G_qh reads them through `settings(session)`: energies, profiles, the
energy table, selectivities and turnovers, IRC end points, the read-only copy and Claude's tools.

D252: the geometry tolerance (ID-7, the RMSD within which a calculation is at a geometry) is kept
the same way, in the same record, so which optimization a calculation is at does not depend on the
computer. Everything that matches geometries reads it through `tolerance(session)` or
`settings(session)`; an investigation saved before D252 takes the app's value once when it is next
opened (the repair `geometry_tolerance`).
"""

from dataclasses import replace
from typing import Any

from sqlalchemy.orm import Session

from chembook3d import settings as app_settings
from chembook3d.models import InvestigationInfo
from chembook3d.services import history

RECORD_TYPE = "thermochemistry"
RECORD_ID = "investigation"
FIELDS = ("qh_temperature", "qh_cutoff", "standard_state")
TOLERANCE = "geometry_tolerance"  # D252
# Every value the investigation keeps of its own.
SAVED = (*FIELDS, TOLERANCE)


class Invalid(ValueError):
    """A value the settings do not take; the message says why."""


def _info(session: Session) -> InvestigationInfo | None:
    return session.get(InvestigationInfo, 1)


def _is_set(info: InvestigationInfo | None) -> bool:
    return info is not None and all(getattr(info, f) is not None for f in SAVED)


def values(
    session: Session | None, defaults: app_settings.Settings | None = None
) -> dict[str, Any]:
    """The investigation's three values; any not set yet (the repair has not run), or all
    without a session, the app's default."""
    defaults = defaults or app_settings.load()
    info = _info(session) if session is not None else None
    found = {}
    for field in FIELDS:
        value = getattr(info, field, None) if info is not None else None
        found[field] = getattr(defaults, field) if value is None else value
    return found


def tolerance(session: Session | None, defaults: app_settings.Settings | None = None) -> float:
    """D252: the investigation's geometry tolerance (Å); the app's while it has none yet, or
    without a session."""
    info = _info(session) if session is not None else None
    value = getattr(info, TOLERANCE, None) if info is not None else None
    if value is None:
        return (defaults or app_settings.load()).geometry_tolerance
    return value


def settings(session: Session | None) -> app_settings.Settings:
    """The app's settings with the investigation's thermochemistry (D159) and geometry
    tolerance (D252) in place of the app-wide defaults. Without a session (no investigation),
    the app's settings as they are."""
    loaded = app_settings.load()
    if session is None:
        return loaded
    return replace(loaded, **values(session, loaded), geometry_tolerance=tolerance(session, loaded))


def new_values() -> dict[str, Any]:
    """What a new investigation starts with: the app's settings."""
    loaded = app_settings.load()
    return {**values(None, loaded), TOLERANCE: loaded.geometry_tolerance}


def check(field: str, value: Any) -> Any:
    """`value` cleaned for `field`, or Invalid."""
    if field == "standard_state":
        if value not in app_settings.STANDARD_STATES:
            raise Invalid(f"Unknown standard state '{value}'")
        return value
    if isinstance(value, bool) or not isinstance(value, int | float) or value != value:
        raise Invalid(f"{field} must be a number")
    if field == TOLERANCE and value <= 0:
        raise Invalid("The geometry tolerance must be above zero")
    if field == "qh_temperature" and value <= 0:
        raise Invalid("The G_qh temperature must be above zero")
    if field == "qh_cutoff" and value < 0:
        raise Invalid("The G_qh cutoff must not be negative")
    return float(value)


def seed(session: Session, source: str = "manual") -> bool:
    """Set the values the investigation does not have yet from the app's settings, recorded as
    one entry in the history naming only those; False when they were all set already."""
    info = _info(session)
    if info is None or _is_set(info):
        return False
    defaults = new_values()
    found = {f: defaults[f] for f in SAVED if getattr(info, f) is None}
    for field, value in found.items():
        setattr(info, field, value)
    history.record(
        session, RECORD_TYPE, RECORD_ID, "create", new={**found, "from": "app"}, source=source
    )
    return True


def update(session: Session, changes: dict[str, Any]) -> dict[str, Any]:
    """Set the values given (None leaves one as it is), each change one history entry; the
    investigation's values after it."""
    cleaned = {f: check(f, v) for f, v in changes.items() if f in SAVED and v is not None}
    info = _info(session)
    if info is None:
        raise Invalid("The investigation has no record of its own")
    seed(session)
    for field, value in cleaned.items():
        old = getattr(info, field)
        if old == value:
            continue
        setattr(info, field, value)
        history.record(session, RECORD_TYPE, RECORD_ID, "update", field, old, value)
    return values(session)
