"""Levels of theory (FR-CALC-02…05, D30, D31, D35, D59).

A level is program + method + basis + dispersion + solvation. GEN/GENECP basis sets and
IOp-modified dispersions are named once by the user and then recognised in later imports by
their parsed definition, so equality uses the name.
"""

import hashlib
import json
import re
from collections.abc import Callable, Container
from typing import Any, Generic, TypeVar

from sqlalchemy import select
from sqlalchemy.orm import Session, object_session

from chembook3d.models import (
    Calculation,
    CalculationType,
    CustomBasis,
    CustomDispersion,
    LevelOfTheory,
    Node,
)
from chembook3d.services import geometry, history, thermo_settings

T = TypeVar("T")
_UNSET: Any = object()


def _itself(calculation: Calculation) -> Calculation:
    return calculation


LEVEL_FIELDS = ("program", "method", "basis", "dispersion", "solvation_model", "solvent")
OPTIMIZATION_TYPES = (CalculationType.OPTIMIZATION, CalculationType.TS_OPTIMIZATION)


class NameConflict(ValueError):
    pass


_ONIOM = re.compile(r"^ONIOM\s*\(", re.IGNORECASE)

# Method names that begin with R or U without it being a reference prefix.
_NOT_A_PREFIX = ("REV", "UFF")


def canonical_method(method: str, multiplicity: int | None) -> str:
    """A12: Gaussian uses a restricted reference for singlets and an unrestricted one
    otherwise. A written R or U that only states that default is dropped, so the `RB3LYP` that
    Gaussian writes into the frequency step of an `opt freq` job is the same level as the
    `B3LYP` of its optimization. RO (restricted open shell) and a non-default prefix, such as
    U for a broken-symmetry singlet, stay part of the method."""
    upper = method.upper()
    if _ONIOM.match(method):
        # D161: each layer's method on its own (`ONIOM(RB3LYP/6-31G(d):UFF)` of a compound
        # job's frequency step is the `ONIOM(B3LYP/6-31G(d):UFF)` of its optimization).
        head, _, rest = method.partition("(")
        inside, _, tail = rest.rpartition(")")
        layers = []
        for layer in inside.split(":"):
            name, slash, basis = layer.partition("/")
            layers.append(canonical_method(name, multiplicity) + slash + basis)
        return f"{head}({':'.join(layers)}){tail}"
    if len(method) < 2 or upper.startswith("RO") or upper.startswith(_NOT_A_PREFIX):
        return method
    if (upper[0] == "R" and multiplicity == 1) or (
        upper[0] == "U" and multiplicity is not None and multiplicity > 1
    ):
        return method[1:]
    return method


# Methods that come with their own basis set: semi-empirical xTB (and GFN-FF), the composite
# "3c" methods and ONIOM, whose layers each name theirs (D161); a machine-learned potential such
# as MACE-OMOL-0 has none (D237).
_BUILT_IN_BASIS = re.compile(r"(-3C$|^GFN|XTB|^ONIOM\s*\(|^MACE)", re.IGNORECASE)


def needs_basis(program: str, method: str) -> bool:
    return program not in ("xTB", "CREST") and not _BUILT_IN_BASIS.search(method or "")


# D246: how far a method is from a full electronic-structure calculation, so a lower-ranked
# optimization run from a node's geometry is added beside it rather than continuing it. Force
# fields rank lowest, then machine-learned potentials, then semi-empirical and tight-binding
# methods; HF, DFT (the "3c" composites too), wavefunction methods and ONIOM rank highest.
_FORCE_FIELD = re.compile(r"^(UFF|MMFF|GAFF|AMBER|OPLS|DREIDING|GFN-?FF)", re.IGNORECASE)
_MACHINE_LEARNED = re.compile(r"^(MACE|ANI|AIMNET|UMA|ORB|MACE-STUB)", re.IGNORECASE)
_SEMI_EMPIRICAL = re.compile(
    r"^(GFN|G-?XTB|XTB|DFTB|PM[3-7]|AM1|MNDO|RM1|ZINDO|INDO|CNDO|STUB)", re.IGNORECASE
)
FORCE_FIELD, MACHINE_LEARNED, SEMI_EMPIRICAL, ELECTRONIC = 0, 1, 2, 3


def method_rank(fields: dict[str, str] | None) -> int:
    """D246: FORCE_FIELD < MACHINE_LEARNED < SEMI_EMPIRICAL < ELECTRONIC for a level's method
    (the test-only "stub" ranks as semi-empirical)."""
    method = ((fields or {}).get("method") or "").strip()
    if _FORCE_FIELD.match(method):
        return FORCE_FIELD
    if _MACHINE_LEARNED.match(method):
        return MACHINE_LEARNED
    if _SEMI_EMPIRICAL.match(method) or (fields or {}).get("program") in ("xTB", "CREST"):
        return SEMI_EMPIRICAL
    return ELECTRONIC


def get_or_create(session: Session, fields: dict[str, str]) -> LevelOfTheory:
    values = {key: (fields.get(key) or "").strip() for key in LEVEL_FIELDS}
    query = select(LevelOfTheory).filter_by(**values)
    level = session.scalars(query).first()
    if level is None:
        level = LevelOfTheory(**values)
        session.add(level)
        session.flush()
    return level


def fields_of(level: LevelOfTheory | None) -> dict[str, str] | None:
    if level is None:
        return None
    return {key: getattr(level, key) for key in LEVEL_FIELDS}


def label(level: LevelOfTheory | None) -> str | None:
    """E.g. `Gaussian PBEPBE-GD3MBJ/modDZ` or `Gaussian B3LYP/DEF2SVP SMD(water)`."""
    if level is None:
        return None
    method = level.method or "?"
    if level.dispersion:
        method += f"-{level.dispersion}"
    text = f"{level.program} {method}"
    if level.basis or needs_basis(level.program, level.method):
        text += f"/{level.basis or '?'}"
    if level.solvation_model:
        text += f" {level.solvation_model}({level.solvent})"
    return text


def composite_label(level: LevelOfTheory | None, geometry_level: LevelOfTheory | None) -> str:
    """D31: a single point is written with the level its geometry came from."""
    if level is None:
        return "unknown level"
    if geometry_level is None or geometry_level.id == level.id:
        return label(level) or ""
    return f"{label(level)} // {label(geometry_level)}"


def is_geometry_source(calculation: Calculation) -> bool:
    """D100, D157: an optimization that can give a geometry level: one with a level that no
    scan, frozen coordinate or frozen atom held."""
    return (
        calculation.type in OPTIMIZATION_TYPES
        and calculation.level is not None
        and not calculation.constraints
    )


def optimizations(node: Node) -> list[Calculation]:
    """The node's optimizations that can give a geometry level (D157: never a constrained
    one), oldest first."""
    found = [c for c in node.calculations if is_geometry_source(c)]
    return sorted(found, key=lambda c: (c.created_at, c.step_index or 0))


def _unheld_frequency(calculation: Calculation) -> bool:
    return (
        calculation.type == CalculationType.FREQUENCY
        and calculation.level is not None
        and not calculation.constraints
        and bool(calculation.geometry)
    )


def geometry_sources(node: Node) -> list[Calculation]:
    """D100: the calculations a geometry level comes from, oldest first: the node's
    optimizations (D157: never a held one). D204: a node with none, whose optimization was run
    elsewhere and only its frequency job imported, takes its frequency jobs; one whose
    stationary-point check failed still does, and W-FREQ-NONSTAT marks its values (D158)."""
    found = optimizations(node)
    if found:
        return found
    found = [c for c in node.calculations if _unheld_frequency(c)]
    return sorted(found, key=lambda c: (c.created_at, c.step_index or 0))


class Sources(Generic[T]):
    """D253: the one rule for which geometry a calculation is at, shared by the import preview,
    the commit and energies. The geometries a node's calculations can be at are its free
    optimizations (D100, D157), with the free ones written or planned before it in the same
    file added in order; on a node with none, its frequency jobs that were not held (D204),
    which drop out once an optimization is added. Each entry carries a value: the calculation,
    or whatever the caller pairs with a step it has not written yet."""

    def __init__(
        self,
        node: Node | None,
        value: Callable[[Calculation], T],
        leave_out: Container[str] = (),
    ):
        """`leave_out`: calculations taken as gone (an import being undone)."""
        self._free: list[tuple[Any, T]] = []
        self._frequencies: list[tuple[Any, T]] = []
        if node is None:
            return
        self._free = [(c.geometry, value(c)) for c in optimizations(node) if c.id not in leave_out]
        found = sorted(
            (c for c in node.calculations if _unheld_frequency(c) and c.id not in leave_out),
            key=lambda c: (c.created_at, c.step_index or 0),
        )
        self._frequencies = [(c.geometry, value(c)) for c in found]

    def add(self, rows: Any, value: T) -> None:
        """A free optimization written (or planned) after the node's own."""
        self._free.append((rows, value))

    @property
    def entries(self) -> list[tuple[Any, T]]:
        return self._free or self._frequencies

    def geometries(self) -> list[Any]:
        """Where the entries ended: the geometries a node holds besides its own (D246)."""
        return [rows for rows, _ in self.entries if rows]

    def at(self, rows: Any, tolerance: float) -> T | None:
        """The latest entry whose geometry `rows` match (D100)."""
        if not rows:
            return None
        for ended, value in reversed(self.entries):
            if geometry.matches(ended, rows, tolerance):
                return value
        return None

    def node_source(self, node_rows: Any, tolerance: float) -> T | None:
        """FR-CALC-04: the one the node's geometry came from; the latest when the node's
        geometry matches none (records made before D100)."""
        found = self.at(node_rows, tolerance)
        if found is not None:
            return found
        return self.entries[-1][1] if self.entries else None

    def source(
        self,
        kind: CalculationType,
        rows: Any,
        node_rows: Any,
        tolerance: float,
        node_source: Any = _UNSET,
    ) -> T | None:
        """The entry a single point or frequency job is at: the latest that ended on its own
        coordinates. A single point whose coordinates match none is at an unknown geometry
        (D100, SP-GEOM, set by hand). One that printed no coordinates is at the node's
        geometry (A15), and so is a frequency job whose coordinates match none (A11): the one
        the node's geometry came from (`node_source`, worked out here unless given)."""
        if rows:
            if kind == CalculationType.FREQUENCY and not self._free:
                # D204: on a node without optimizations a frequency job is its own geometry
                # source: at its own level (A11), never at another frequency job's.
                return None
            found = self.at(rows, tolerance)
            if found is not None or kind == CalculationType.SINGLE_POINT:
                return found
        if node_source is _UNSET:
            return self.node_source(node_rows, tolerance)
        return node_source


def optimization_at(
    node: Node | None, rows: list[Any] | None, tolerance: float | None = None
) -> Calculation | None:
    """D100: the optimization on the node that ended on this geometry, the latest if several
    did (D204: or the frequency job at it, on a node without optimizations). A single point
    or frequency job takes its geometry level from it."""
    if node is None or not rows:
        return None
    if tolerance is None:
        tolerance = thermo_settings.tolerance(object_session(node))
    return Sources(node, _itself).at(rows, tolerance)


def pre_optimization_at(node: Node, rows: list[Any] | None, tolerance: float) -> Calculation | None:
    """D100, D165: the optimization on the node that ended on this geometry, the latest if
    several did, held or not. A constrained or restrained pre-optimization (D157) gives no
    geometry level, but a free optimization at another level that starts where it ended
    still continues it."""
    if not rows:
        return None
    found = [
        c
        for c in node.calculations
        if c.type in OPTIMIZATION_TYPES
        and c.level is not None
        and geometry.matches(c.geometry, rows, tolerance)
    ]
    found.sort(key=lambda c: (c.created_at, c.step_index or 0))
    return found[-1] if found else None


def continued_by(calculation: Calculation, tolerance: float | None = None) -> Calculation | None:
    """D168: the free optimization on the calculation's node that started where this
    optimization ended, which makes this one the node's pre-optimization (D100; a held one,
    D165). None for anything else."""
    node = calculation.node
    if node is None or calculation.type not in OPTIMIZATION_TYPES or not calculation.geometry:
        return None
    following = [
        c
        for c in node.calculations
        if c.id != calculation.id and c.start_geometry and is_geometry_source(c)
    ]
    if not following:
        return None
    if tolerance is None:
        tolerance = thermo_settings.tolerance(object_session(node))
    # D246: an optimization added beside the node's geometry continues nothing.
    line = node_line(node, tolerance)
    if line is not None:
        following = [c for c in following if c.id in line]
    return next(
        (
            c
            for c in following
            if geometry.matches(c.start_geometry, calculation.geometry, tolerance)
        ),
        None,
    )


def node_line(node: Node, tolerance: float) -> set[str] | None:
    """D246: the free optimizations the node's geometry came through: the one that ended on
    it and, going back, each that ended where one of them started (its pre-optimizations,
    D100). An optimization outside it was added beside the node's geometry, at another level.
    None for a node whose geometry no optimization ended on (records made before D100)."""
    free = [c for c in node.calculations if is_geometry_source(c) and c.geometry]
    line = {c.id for c in free if geometry.matches(c.geometry, node.geometry, tolerance)}
    if not line:
        return None
    grown = True
    while grown:
        starts = [c.start_geometry for c in free if c.id in line and c.start_geometry]
        added = {
            c.id
            for c in free
            if c.id not in line and any(geometry.matches(c.geometry, s, tolerance) for s in starts)
        }
        line |= added
        grown = bool(added)
    return line


def node_geometry_optimization(
    node: Node | None, tolerance: float | None = None
) -> Calculation | None:
    """FR-CALC-04, D100: the optimization the node's geometry came from. A lower-level
    pre-optimization that a later optimization continued from is not it, whatever order the
    two were imported in. A node whose geometry matches none of its optimizations (records
    made before D100) falls back on its latest optimization."""
    if node is None:
        return None
    if tolerance is None:
        tolerance = thermo_settings.tolerance(object_session(node))
    return Sources(node, _itself).node_source(node.geometry, tolerance)


def geometry_at_level(
    node: Node, geometry_level_id: str | None, tolerance: float | None = None
) -> list[Any] | None:
    """D254: the node's geometry its values at this geometry level are computed on: the node's
    own when that came from this level (or no level is given, or none of its geometries is at
    it), else the latest of its geometries at the level (one added beside it, D246)."""
    if geometry_level_id is None or not node.geometry:
        return node.geometry
    if tolerance is None:
        tolerance = thermo_settings.tolerance(object_session(node))
    at_level = [
        rows
        for rows, value in Sources(node, _itself).entries
        if rows and value.level_id == geometry_level_id
    ]
    if not at_level or any(geometry.matches(rows, node.geometry, tolerance) for rows in at_level):
        return node.geometry
    return at_level[-1]


def node_geometries(node: Node, tolerance: float | None = None) -> list[Calculation]:
    """D246: the optimized geometries a node holds, one optimization each (the latest of those
    that ended on the same geometry): the node's own first, then those added beside it or its
    pre-optimizations, oldest first. A single point can be run at any of them."""
    if tolerance is None:
        tolerance = thermo_settings.tolerance(object_session(node))
    found: list[Calculation] = []
    for calculation in reversed(optimizations(node)):
        if calculation.geometry and not any(
            geometry.matches(calculation.geometry, kept.geometry, tolerance) for kept in found
        ):
            found.append(calculation)
    found.reverse()
    found.sort(key=lambda c: not geometry.matches(c.geometry, node.geometry, tolerance))
    return found


def node_geometry_level(node: Node | None, tolerance: float | None = None) -> LevelOfTheory | None:
    found = node_geometry_optimization(node, tolerance)
    return found.level if found else None


def all_levels(session: Session) -> list[LevelOfTheory]:
    return list(session.scalars(select(LevelOfTheory)))


# ---------- named custom basis sets (FR-CALC-03) ----------


def definition_key(value: Any) -> str:
    """A short stable key for a parsed definition, used to pair names with definitions."""
    text = json.dumps(value, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def find_basis(session: Session, fingerprints: dict[str, str]) -> CustomBasis | None:
    """A registered basis that defines every element of this one identically."""
    for basis in session.scalars(select(CustomBasis).order_by(CustomBasis.created_at)):
        if all(basis.fingerprints.get(el) == h for el, h in fingerprints.items()):
            return basis
    return None


def basis_by_name(session: Session, name: str) -> CustomBasis | None:
    return session.scalars(select(CustomBasis).where(CustomBasis.name == name)).first()


def check_basis_name(
    session: Session, name: str, fingerprints: dict[str, str]
) -> CustomBasis | None:
    """Raise NameConflict if `name` already stands for a different definition of one of these
    elements. Returns the existing basis of that name, if any."""
    existing = basis_by_name(session, name)
    if existing is not None:
        clash = [el for el, h in fingerprints.items() if existing.fingerprints.get(el, h) != h]
        if clash:
            raise NameConflict(
                f"'{name}' is already the name of a basis set that defines "
                f"{', '.join(clash)} differently"
            )
    return existing


def register_basis(
    session: Session, name: str, definition: dict[str, str], fingerprints: dict[str, str]
) -> CustomBasis:
    """Name a basis, or add elements this import brings to an existing name."""
    name = name.strip()
    existing = check_basis_name(session, name, fingerprints)
    if existing is not None:
        added = {el: t for el, t in definition.items() if el not in existing.definition}
        if added:
            existing.definition = {**existing.definition, **added}
            existing.fingerprints = {
                **existing.fingerprints,
                **{el: fingerprints[el] for el in added},
            }
            history.record(
                session,
                "custom_basis",
                existing.id,
                "update",
                "elements",
                None,
                sorted(added),
                source="import",
            )
        return existing
    basis = CustomBasis(name=name, definition=definition, fingerprints=fingerprints)
    session.add(basis)
    session.flush()
    history.record(
        session,
        "custom_basis",
        basis.id,
        "create",
        new={"name": name, "elements": sorted(definition)},
        source="import",
    )
    return basis


# ---------- named dispersions (D59) ----------


def find_dispersion(session: Session, base: str, iops: dict[str, str]) -> CustomDispersion | None:
    query = select(CustomDispersion).where(CustomDispersion.base == base)
    for dispersion in session.scalars(query):
        if dispersion.iops == iops:
            return dispersion
    return None


def check_dispersion_name(
    session: Session, name: str, base: str, iops: dict[str, str]
) -> CustomDispersion | None:
    existing = session.scalars(
        select(CustomDispersion).where(CustomDispersion.name == name.strip())
    ).first()
    if existing is not None and (existing.base != base or existing.iops != iops):
        raise NameConflict(f"'{name.strip()}' is already the name of a different dispersion")
    return existing


def register_dispersion(
    session: Session, name: str, base: str, iops: dict[str, str]
) -> CustomDispersion:
    name = name.strip()
    existing = check_dispersion_name(session, name, base, iops)
    if existing is not None:
        return existing
    dispersion = CustomDispersion(name=name, base=base, iops=iops)
    session.add(dispersion)
    session.flush()
    history.record(
        session,
        "custom_dispersion",
        dispersion.id,
        "create",
        new={"name": name, "base": base, "iops": iops},
        source="import",
    )
    return dispersion
