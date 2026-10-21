"""Energies at one (composite level, energy type) selection (docs/spec/02 §5, FR-EN-01…09).

- A value belongs to a node at a composite level `level // geometry level` (D31). An
  optimization is at its own geometry level. A single point takes the geometry level recorded
  on it (FR-CALC-04), otherwise the node's latest optimization level; with neither, its
  geometry level is unknown. Any other calculation (a frequency job) takes the node's latest
  optimization level, or its own level when the node has no optimization (A11).
- E is the calculation's final SCF energy. H, G and G_qh add a thermal correction from the
  frequency calculation on the same node at the geometry level (EN-4, D35); there is no
  fallback to another level or type (EN-3, D27). G_qh is recomputed from that step's
  frequencies (EN-5, EN-6, `thermochem`).
- With the 1 M standard state chosen in Settings (D95), G and G_qh of every node add
  RT ln(V_m / 1 L mol⁻¹) at the G_qh temperature (or an analysis's own); E and H never do.
- A group node's value is its representative's; with none, it has no value (EN-7).
- Values are only read from calculation results, never typed (EN-1), and are never used to
  pick nodes, representatives or pathways (EN-10).
"""

from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from chembook3d import settings as app_settings
from chembook3d import thermochem
from chembook3d.models import (
    Calculation,
    CalculationType,
    GroupNode,
    LevelOfTheory,
    Node,
    Role,
)
from chembook3d.parsers import joined
from chembook3d.services import levels
from chembook3d.services.records import RecordError

ENERGY_TYPES = ("E", "H", "G", "G_qh")
KEY_SEPARATOR = "~"


@dataclass(frozen=True, order=True)
class LevelKey:
    """A composite level: the level of the energy and the level the geometry came from."""

    level_id: str
    geometry_level_id: str | None

    def encode(self) -> str:
        return f"{self.level_id}{KEY_SEPARATOR}{self.geometry_level_id or ''}"

    @classmethod
    def decode(cls, text: str) -> "LevelKey":
        level_id, separator, geometry = text.partition(KEY_SEPARATOR)
        if not separator or not level_id:
            raise RecordError(f"unknown level '{text}'")
        return cls(level_id, geometry or None)


def key_label(session: Session, key: LevelKey) -> str:
    level = session.get(LevelOfTheory, key.level_id)
    if key.geometry_level_id is None:
        return f"{levels.label(level)} // unknown geometry level"
    return levels.composite_label(level, session.get(LevelOfTheory, key.geometry_level_id))


@dataclass
class Value:
    """One energy of one node or group in hartree, or None with the reason (`code` is a
    warning code such as W-NOFREQ when there is one)."""

    value: float | None
    code: str | None = None
    message: str | None = None
    energy_calculation_id: str | None = None
    thermo_calculation_id: str | None = None
    details: dict[str, Any] = field(default_factory=dict)
    # D154: what the frequency job at the geometry level says against the node's role (a
    # transition state without exactly one imaginary frequency, or without a frequency job; a
    # minimum with one), as {"code", "message"}; None when nothing.
    check: dict[str, str] | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "value": self.value,
            "code": self.code,
            "message": self.message,
            "energy_calculation_id": self.energy_calculation_id,
            "thermo_calculation_id": self.thermo_calculation_id,
            "details": self.details,
            "check": self.check,
        }


def _order(calculation: Calculation) -> tuple:
    return (calculation.created_at, calculation.step_index or 0)


def calculation_key(
    calculation: Calculation,
    node_geometry_level_id: str | None,
    tolerance: float | None = None,
) -> LevelKey | None:
    if calculation.level_id is None:
        return None
    if calculation.type in levels.OPTIMIZATION_TYPES:
        return LevelKey(calculation.level_id, calculation.level_id)
    geometry = calculation.geometry_level_id
    if geometry is None and calculation.geometry:
        # D100: the optimization that ended on this calculation's geometry.
        found = levels.optimization_at(calculation.node, calculation.geometry, tolerance)
        geometry = found.level_id if found else None
        if geometry is None and calculation.type == CalculationType.SINGLE_POINT:
            return LevelKey(calculation.level_id, None)  # unknown until set by hand (D100)
    if geometry is None:
        geometry = node_geometry_level_id  # no coordinates printed (A15)
    if geometry is None:
        if calculation.type == CalculationType.SINGLE_POINT:
            return LevelKey(calculation.level_id, None)
        geometry = calculation.level_id  # A11: taken to be at its own geometry level
    return LevelKey(calculation.level_id, geometry)


def quasi_harmonic(
    calculation: Calculation, temperature: float, cutoff: float
) -> thermochem.QuasiHarmonic:
    result = calculation.result
    if result is None or not result.frequencies:
        raise thermochem.NotComputable("W-NOFREQ", "the calculation has no frequencies")
    return thermochem.quasi_harmonic(
        frequencies=list(result.frequencies),
        scf_energy=result.energy,
        molecular_mass=result.molecular_mass,
        multiplicity=calculation.multiplicity,
        rotational_temperatures=result.rotational_temperatures,
        symmetry_number=result.symmetry_number,
        point_group=result.point_group,
        temperature=temperature,
        cutoff=cutoff,
    )


def _state(calculation: Calculation) -> tuple[int, int | None] | None:
    """D155: a calculation's charge and multiplicity, None when the file did not say."""
    if calculation.charge is None:
        return None
    return (calculation.charge, calculation.multiplicity)


def _state_text(state: tuple[int | None, int | None]) -> str:
    return f"charge {state[0]}, multiplicity {state[1]}"


class NodeEnergies:
    """Every energy one node has, by composite level."""

    def __init__(
        self, node: Node, temperature: float, cutoff: float, standard_state: str = "1 atm"
    ):
        self.node = node
        self.temperature = temperature
        self.cutoff = cutoff
        self.standard_state = standard_state
        tolerance = app_settings.load().geometry_tolerance
        geometry = levels.node_geometry_level(node, tolerance)
        self.by_key: dict[LevelKey, list[Calculation]] = {}
        # D155: calculations for another charge or multiplicity than the node's are another
        # electronic state; they stay on the node (W-CHG) but give none of its energies.
        self.other_state: dict[LevelKey, tuple[int, int | None]] = {}
        own = (node.charge, node.multiplicity) if node.charge is not None else None
        for calculation in sorted(node.calculations, key=_order):
            if calculation.type == CalculationType.OTHER and calculation.route.startswith(
                joined.ROUTE
            ):
                continue  # D137: a joined scan's energies stay on its own chart
            key = calculation_key(calculation, geometry.id if geometry else None, tolerance)
            if key is None or calculation.result is None:
                continue
            state = _state(calculation)
            if own is not None and state is not None and state != own:
                self.other_state[key] = state
                continue
            self.by_key.setdefault(key, []).append(calculation)
        self._qh: dict[str, thermochem.QuasiHarmonic | thermochem.NotComputable] = {}

    def energy(self, key: LevelKey) -> Calculation | None:
        """The latest calculation with an SCF energy at this composite level; none when they
        are for different charges or multiplicities (D155: never the latest of two states)."""
        found = [c for c in self.by_key.get(key, []) if c.result.energy is not None]
        if len({_state(c) for c in found} - {None}) > 1:
            return None
        return found[-1] if found else None

    def _no_energy(self, key: LevelKey) -> Value:
        """Why there is no energy at this level (D155: another electronic state's)."""
        found = [c for c in self.by_key.get(key, []) if c.result.energy is not None]
        states = sorted({_state(c) for c in found} - {None}, key=str)
        if len(states) > 1:
            listed = " and ".join(_state_text(s) for s in states)
            return Value(None, "W-CHG", f"the calculations at this level are for {listed}")
        if key in self.other_state and not found:
            return Value(
                None,
                "W-CHG",
                f"the calculations at this level are for {_state_text(self.other_state[key])}, "
                f"not the node's {_state_text((self.node.charge, self.node.multiplicity))}",
            )
        return Value(None, None, "no energy at this level")

    def thermo_source(self, key: LevelKey) -> Calculation | None:
        """EN-4: the latest frequency calculation on this node at the geometry level."""
        if key.geometry_level_id is None:
            return None
        at_geometry = LevelKey(key.geometry_level_id, key.geometry_level_id)
        found = [
            c
            for c in self.by_key.get(at_geometry, [])
            if c.type == CalculationType.FREQUENCY
            and (c.result.frequencies or c.result.g_corr is not None)
        ]
        return found[-1] if found else None

    def qh(self, calculation: Calculation) -> thermochem.QuasiHarmonic:
        if calculation.id not in self._qh:
            try:
                self._qh[calculation.id] = quasi_harmonic(
                    calculation, self.temperature, self.cutoff
                )
            except thermochem.NotComputable as exc:
                self._qh[calculation.id] = exc
        found = self._qh[calculation.id]
        if isinstance(found, thermochem.NotComputable):
            raise found
        return found

    def check(self, key: LevelKey) -> dict[str, str] | None:
        """D154: the frequency job behind this composite level (at its geometry level; the
        node's latest when that is not known) against the node's role. Shown wherever the value
        is used (profiles, the table, selectivities, turnovers) and never changes it (D19)."""
        role = self.node.role
        if role not in (Role.TRANSITION_STATE, Role.MINIMUM):
            return None
        if key.geometry_level_id is not None:
            frequency = self.thermo_source(key)
        else:
            frequency = next(
                (
                    c
                    for c in sorted(self.node.calculations, key=_order, reverse=True)
                    if c.type == CalculationType.FREQUENCY and c.result is not None
                ),
                None,
            )
        if frequency is None:
            if role == Role.TRANSITION_STATE:
                return {
                    "code": "W-TS-NOFREQ",
                    "message": "no frequency calculation at the geometry level shows it is a "
                    "transition state",
                }
            return None
        count = frequency.result.imaginary_count
        if count is None or (count == 1) == (role == Role.TRANSITION_STATE):
            return None
        imaginary = [f for f in frequency.result.frequencies if f < 0]
        shown = ", ".join(f"{f:.1f}" for f in imaginary)
        plural = "frequency" if count == 1 else "frequencies"
        if role == Role.TRANSITION_STATE:
            message = f"{count} imaginary {plural} at the geometry level, not 1"
        else:
            message = f"{count} imaginary {plural} at the geometry level"
        return {
            "code": "W-IMAG-TS" if role == Role.TRANSITION_STATE else "W-IMAG-MIN",
            "message": f"{message} ({shown} cm⁻¹)" if shown else message,
        }

    def value(self, key: LevelKey, energy_type: str) -> Value:
        found = self._value(key, energy_type)
        found.check = self.check(key)
        return found

    def _value(self, key: LevelKey, energy_type: str) -> Value:
        source = self.energy(key)
        if source is None:
            return self._no_energy(key)
        energy = source.result.energy
        if energy_type == "E":
            return Value(energy, energy_calculation_id=source.id)
        if key.geometry_level_id is None:
            return Value(
                None,
                "W-NOFREQ",
                "the geometry level of this single point is not known",
                source.id,
            )
        thermo = self.thermo_source(key)
        if thermo is None:
            return Value(
                None,
                "W-NOFREQ",
                "no frequency calculation at the geometry level",
                source.id,
            )
        if None not in (_state(source), _state(thermo)) and _state(source) != _state(thermo):
            # D155: only for a node whose own state is not known; otherwise both match it.
            return Value(
                None,
                "W-CHG",
                f"the energy is for {_state_text(_state(source))} and the frequency calculation "
                f"for {_state_text(_state(thermo))}",
                source.id,
                thermo.id,
            )
        result = thermo.result
        details: dict[str, Any] = {
            "job_temperature": result.temperature,
            "job_pressure": result.pressure,
        }
        if energy_type in ("H", "G"):
            correction = result.h_corr if energy_type == "H" else result.g_corr
            if correction is None:
                return Value(
                    None,
                    "W-PARSE",
                    f"the frequency calculation has no printed {energy_type} correction",
                    source.id,
                    thermo.id,
                )
            if energy_type == "G":
                correction += self._standard_state(details)
            return Value(energy + correction, None, None, source.id, thermo.id, details)
        try:
            qh = self.qh(thermo)
        except thermochem.NotComputable as exc:
            return Value(None, exc.code, exc.message, source.id, thermo.id, details)
        details.update(
            temperature=qh.temperature,
            cutoff=qh.cutoff,
            correction=qh.g_corr,
            printed_correction=result.g_corr,
            raised_modes=qh.raised_modes,
            imaginary_excluded=qh.imaginary_excluded,
        )
        shift = self._standard_state(details)
        return Value(energy + qh.g_corr + shift, None, None, source.id, thermo.id, details)

    def _standard_state(self, details: dict[str, Any]) -> float:
        """D95: the 1 atm → 1 M shift of a free energy (0 at 1 atm), noted in `details`."""
        if self.standard_state != "1 M":
            return 0.0
        shift = thermochem.standard_state_correction(self.temperature)
        details.update(
            standard_state="1 M",
            standard_state_correction=shift,
            standard_state_temperature=self.temperature,
        )
        return shift

    def available(self) -> dict[LevelKey, set[str]]:
        found: dict[LevelKey, set[str]] = {}
        for key in self.by_key:
            if self.energy(key) is None:
                continue
            types = found.setdefault(key, set())
            for energy_type in ENERGY_TYPES:
                if self.value(key, energy_type).value is not None:
                    types.add(energy_type)
        return found


class Energies:
    """Energies of every node and group in the investigation, for one request."""

    def __init__(
        self,
        session: Session,
        settings: app_settings.Settings | None = None,
        temperature: float | None = None,
    ):
        """`temperature` overrides the G_qh temperature setting (a selectivity's own, S4); it
        is also the temperature of the 1 M standard state (D95)."""
        settings = settings or app_settings.load()
        self.session = session
        self.temperature = settings.qh_temperature if temperature is None else temperature
        self.cutoff = settings.qh_cutoff
        self.standard_state = settings.standard_state
        query = select(Node).options(
            selectinload(Node.calculations).selectinload(Calculation.result)
        )
        self.nodes = {
            n.id: NodeEnergies(n, self.temperature, self.cutoff, self.standard_state)
            for n in session.scalars(query)
        }
        self.groups = {g.id: g for g in session.scalars(select(GroupNode))}

    def options(self) -> list[dict[str, Any]]:
        """FR-EN-01: composite levels with the energy types that have at least one value."""
        found: dict[LevelKey, set[str]] = {}
        for node in self.nodes.values():
            for key, types in node.available().items():
                found.setdefault(key, set()).update(types)
        listed = [
            {
                "key": key.encode(),
                "label": key_label(self.session, key),
                "types": [t for t in ENERGY_TYPES if t in types],
            }
            for key, types in found.items()
        ]
        return sorted(listed, key=lambda o: o["label"])

    def value(self, record_id: str, key: LevelKey, energy_type: str) -> Value:
        if energy_type not in ENERGY_TYPES:
            raise RecordError(f"unknown energy type '{energy_type}'")
        if record_id in self.nodes:
            return self.nodes[record_id].value(key, energy_type)
        group = self.groups.get(record_id)
        if group is None:
            raise RecordError("not a node or group node")
        if group.representative_id is None or group.representative_id not in self.nodes:
            return Value(None, None, "the group has no representative (EN-7)")
        found = self.nodes[group.representative_id].value(key, energy_type)
        found.details = {**found.details, "representative_id": group.representative_id}
        return found
