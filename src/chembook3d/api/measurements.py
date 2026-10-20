"""HTTP API for measurements across nodes (D151). The rules live in
`services/measurements.py`; these handlers only translate. Atoms are numbered from 1."""

from fastapi import APIRouter
from pydantic import BaseModel, Field

from chembook3d.api.routes import DbSession
from chembook3d.services import measurements as service
from chembook3d.services.records import RecordError

router = APIRouter(prefix="/api")

POINT = "a point is an atom (from 1) or a list of atoms whose middle counts, e.g. [62, 63]"
UNITS = {"distance": "Å", "angle": "°", "dihedral": "°", "vectors": "°"}
SYMBOLS = {"distance": "d", "angle": "∠", "dihedral": "τ", "vectors": "∠"}


class MeasurementIn(BaseModel):
    name: str | None = Field(None, description="Column name; default from the atoms")
    atoms: list[int | list[int]] | None = Field(
        None,
        description="2 points: a distance (Å); 3: an angle; 4: a dihedral (degrees, IUPAC "
        f"sign); {POINT}",
    )
    vectors: list[list[int | list[int]]] | None = Field(
        None,
        description="Instead of `atoms`: two vectors, each [from, to], for the angle between "
        f"them (0 to 180°), e.g. [[62, 63], [4, 1]]; {POINT}",
    )
    lines: bool = Field(
        False, description="vectors: the angle between the two lines, 0 to 90° (either way round)"
    )
    smarts: str | None = Field(
        None,
        description="Find the atoms in each node by this SMARTS pattern (bonds to a metal are "
        "single or ~; aromatic rings aromatic): `atoms` and `vectors` then number the pattern's "
        "atoms from 1. A node where it matches no place or several gets no value and a note.",
    )


class ConditionIn(BaseModel):
    measurement: int | str = Field(description="The measurement's number (from 1) or name")
    below: float | None = Field(None, description="Keep rows whose value is below this")
    above: float | None = Field(None, description="Keep rows whose value is above this")


class MeasureIn(BaseModel):
    node_ids: list[str] = Field(default_factory=list, description="Nodes to measure")
    group_ids: list[str] = Field(
        default_factory=list, description="Groups whose members are all measured"
    )
    measurements: list[MeasurementIn]
    reference_node_id: str | None = Field(
        None,
        description="Atoms are numbered as in this node; every other node's atoms are matched "
        "to it (as match_atoms does), so numbering may differ between nodes. Nodes with other "
        "atoms are skipped. Leave out: atoms are numbered as each node has them.",
    )
    level: str | None = Field(
        None,
        description="Only nodes whose geometry came from an optimization at this level (its "
        "id or label from list_levels)",
    )
    converged_only: bool = Field(
        False, description="Only nodes whose geometry came from a converged optimization"
    )
    keep: list[ConditionIn] = Field(
        default_factory=list,
        description="Keep only the rows meeting all of these, e.g. an angle below 20",
    )


class ColumnOut(BaseModel):
    name: str
    kind: str  # distance, angle, dihedral or vectors
    unit: str


class GeometryOut(BaseModel):
    level: str | None  # the optimization's level of theory
    level_id: str | None
    converged: bool | None
    program: str | None
    calculation_id: str | None


class RowOut(BaseModel):
    node_id: str
    label: str
    group: str | None
    geometry: GeometryOut | None  # None: not from an optimization (built or entered by hand)
    values: list[float | None]
    notes: list[str]


class SummaryOut(BaseModel):
    n: int
    mean: float | None
    sd: float | None
    min: float | None
    max: float | None
    min_node: str | None  # labels
    max_node: str | None


class SkippedOut(BaseModel):
    node_id: str
    label: str
    reason: str


class MeasureOut(BaseModel):
    columns: list[ColumnOut]
    rows: list[RowOut]
    summary: list[SummaryOut]
    by_level: dict[str, list[SummaryOut]]
    skipped: list[SkippedOut]
    notes: list[str]


def _point(p: int | list[int]) -> list[int]:
    atoms = [p] if isinstance(p, int) else list(p)
    if not atoms:
        raise RecordError("a point needs at least one atom")
    if any(a < 1 for a in atoms):
        raise RecordError("atoms are numbered from 1")
    return [a - 1 for a in atoms]


def _named(p: int | list[int]) -> str:
    return str(p) if isinstance(p, int) else "mid(" + ",".join(str(a) for a in p) + ")"


def _measurement(m: MeasurementIn) -> service.Measurement:
    if (m.atoms is None) == (m.vectors is None):
        raise RecordError("give a measurement's `atoms` or its `vectors`, not both")
    if m.vectors is not None:
        if len(m.vectors) != 2 or any(len(v) != 2 for v in m.vectors):
            raise RecordError("give `vectors` as two vectors, each [from, to]")
        given = [*m.vectors[0], *m.vectors[1]]
        kind = service.kind_of(4, True)
        name = f"∠({_named(given[0])}→{_named(given[1])}, {_named(given[2])}→{_named(given[3])})"
    else:
        given = list(m.atoms or [])
        kind = service.kind_of(len(given), False)
        name = f"{SYMBOLS[kind]}({'–'.join(_named(p) for p in given)})"
    if m.smarts:
        name += f" in {m.smarts}"
    return service.Measurement(
        name=m.name or name,
        kind=kind,
        points=[_point(p) for p in given],
        smarts=m.smarts or None,
        lines=m.lines,
    )


def _condition(c: ConditionIn, measurements: list[service.Measurement]) -> service.Condition:
    if isinstance(c.measurement, int):
        index = c.measurement - 1
    else:
        names = [m.name for m in measurements]
        if c.measurement not in names:
            raise RecordError(f"there is no measurement named “{c.measurement}”")
        index = names.index(c.measurement)
    return service.Condition(index, c.below, c.above)


def _summary(s: service.Summary, labels: dict[str, str]) -> SummaryOut:
    return SummaryOut(
        n=s.n,
        mean=s.mean,
        sd=s.sd,
        min=s.min,
        max=s.max,
        min_node=labels.get(s.min_node_id or ""),
        max_node=labels.get(s.max_node_id or ""),
    )


@router.post("/measurements", response_model=MeasureOut)
def measure(body: MeasureIn, session: DbSession):
    """D151: the measurements on each node's geometry, one row per node with where the
    geometry came from, and n, mean, SD, min and max per column. Nothing is stored."""
    wanted = [_measurement(m) for m in body.measurements]
    result = service.table(
        session,
        wanted,
        body.node_ids,
        body.group_ids,
        body.reference_node_id,
        body.level,
        body.converged_only,
        [_condition(c, wanted) for c in body.keep],
    )
    labels = {r.node.id: r.node.label or "Untitled node" for r in result.rows}
    return MeasureOut(
        columns=[ColumnOut(name=m.name, kind=m.kind, unit=UNITS[m.kind]) for m in wanted],
        rows=[
            RowOut(
                node_id=r.node.id,
                label=labels[r.node.id],
                group=(r.group.label or "Untitled group") if r.group else None,
                geometry=None
                if r.source.calculation_id is None
                else GeometryOut(
                    level=r.source.level,
                    level_id=r.source.level_id,
                    converged=r.source.converged,
                    program=r.source.program,
                    calculation_id=r.source.calculation_id,
                ),
                values=r.values,
                notes=r.notes,
            )
            for r in result.rows
        ],
        summary=[_summary(s, labels) for s in result.summary],
        by_level={k: [_summary(s, labels) for s in v] for k, v in result.by_level.items()},
        skipped=[
            SkippedOut(node_id=s.node.id, label=s.node.label or "Untitled node", reason=s.reason)
            for s in result.skipped
        ],
        notes=result.notes,
    )
