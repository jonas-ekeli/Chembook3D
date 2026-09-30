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
)
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

    def as_dict(self) -> dict[str, Any]:
        return {
            "value": self.value,
            "code": self.code,
            "message": self.message,
            "energy_calculation_id": self.energy_calculation_id,
            "thermo_calculation_id": self.thermo_calculation_id,
            "details": self.details,
        }


def _order(calculation: Calculation) -> tuple:
    return (calculation.created_at, calculation.step_index or 0)


def calculation_key(
    calculation: Calculation, node_geometry_level_id: str | None
) -> LevelKey | None:
    if calculation.level_id is None:
        return None
    if calculation.type in levels.OPTIMIZATION_TYPES:
        return LevelKey(calculation.level_id, calculation.level_id)
    geometry = calculation.geometry_level_id or node_geometry_level_id
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


class NodeEnergies:
    """Every energy one node has, by composite level."""

    def __init__(self, node: Node, temperature: float, cutoff: float):
        self.node = node
        self.temperature = temperature
        self.cutoff = cutoff
        geometry = levels.node_geometry_level(node)
        self.by_key: dict[LevelKey, list[Calculation]] = {}
        for calculation in sorted(node.calculations, key=_order):
            key = calculation_key(calculation, geometry.id if geometry else None)
            if key is not None and calculation.result is not None:
                self.by_key.setdefault(key, []).append(calculation)
        self._qh: dict[str, thermochem.QuasiHarmonic | thermochem.NotComputable] = {}

    def energy(self, key: LevelKey) -> Calculation | None:
        """The latest calculation with an SCF energy at this composite level."""
        found = [c for c in self.by_key.get(key, []) if c.result.energy is not None]
        return found[-1] if found else None

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

    def value(self, key: LevelKey, energy_type: str) -> Value:
        source = self.energy(key)
        if source is None:
            return Value(None, None, "no energy at this level")
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
        return Value(energy + qh.g_corr, None, None, source.id, thermo.id, details)

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

    def __init__(self, session: Session, settings: app_settings.Settings | None = None):
        settings = settings or app_settings.load()
        self.session = session
        self.temperature = settings.qh_temperature
        self.cutoff = settings.qh_cutoff
        query = select(Node).options(
            selectinload(Node.calculations).selectinload(Calculation.result)
        )
        self.nodes = {
            n.id: NodeEnergies(n, self.temperature, self.cutoff) for n in session.scalars(query)
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
