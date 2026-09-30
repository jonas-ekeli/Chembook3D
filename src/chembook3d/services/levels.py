"""Levels of theory (FR-CALC-02…05, D30, D31, D35, D59).

A level is program + method + basis + dispersion + solvation. GEN/GENECP basis sets and
IOp-modified dispersions are named once by the user and then recognised in later imports by
their parsed definition, so equality uses the name.
"""

import hashlib
import json
import re
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from chembook3d.models import CalculationType, CustomBasis, CustomDispersion, LevelOfTheory, Node
from chembook3d.services import history

LEVEL_FIELDS = ("program", "method", "basis", "dispersion", "solvation_model", "solvent")
OPTIMIZATION_TYPES = (CalculationType.OPTIMIZATION, CalculationType.TS_OPTIMIZATION)


class NameConflict(ValueError):
    pass


# Method names that begin with R or U without it being a reference prefix.
_NOT_A_PREFIX = ("REV", "UFF")


def canonical_method(method: str, multiplicity: int | None) -> str:
    """A12: Gaussian uses a restricted reference for singlets and an unrestricted one
    otherwise. A written R or U that only states that default is dropped, so the `RB3LYP` that
    Gaussian writes into the frequency step of an `opt freq` job is the same level as the
    `B3LYP` of its optimization. RO (restricted open shell) and a non-default prefix, such as
    U for a broken-symmetry singlet, stay part of the method."""
    upper = method.upper()
    if len(method) < 2 or upper.startswith("RO") or upper.startswith(_NOT_A_PREFIX):
        return method
    if (upper[0] == "R" and multiplicity == 1) or (
        upper[0] == "U" and multiplicity is not None and multiplicity > 1
    ):
        return method[1:]
    return method


# Methods that come with their own basis set: semi-empirical xTB (and GFN-FF) and the
# composite "3c" methods.
_BUILT_IN_BASIS = re.compile(r"(-3C$|^GFN|XTB)", re.IGNORECASE)


def needs_basis(program: str, method: str) -> bool:
    return program not in ("xTB", "CREST") and not _BUILT_IN_BASIS.search(method or "")


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


def node_geometry_level(node: Node | None) -> LevelOfTheory | None:
    """FR-CALC-04: the level of the latest optimization on the node."""
    if node is None:
        return None
    optimizations = [
        c for c in node.calculations if c.type in OPTIMIZATION_TYPES and c.level is not None
    ]
    if not optimizations:
        return None
    latest = max(optimizations, key=lambda c: (c.created_at, c.step_index or 0))
    return latest.level


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
