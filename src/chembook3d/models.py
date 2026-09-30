"""Database records for one investigation (docs/spec/05 §1): nodes, calculations with their
levels of theory, results and copied source files, named custom basis sets and dispersions,
reaction steps, branches, transitions and group nodes, and the change history."""

import uuid
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    JSON,
    CheckConstraint,
    Column,
    ForeignKey,
    Integer,
    String,
    Table,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def new_id() -> str:
    return uuid.uuid4().hex


def utcnow() -> datetime:
    """Naive UTC: SQLite has no time-zone type, so all stored times are UTC by convention."""
    return datetime.now(UTC).replace(tzinfo=None)


class Status(StrEnum):
    """D19: one status per node, transition and branch; set only by the user."""

    PLANNED = "planned"
    RUNNING_EXTERNALLY = "running_externally"
    DONE = "done"
    FAILED = "failed"
    REJECTED = "rejected"
    SUPERSEDED = "superseded"


class Role(StrEnum):
    """P1: drives imaginary-frequency warnings and direct-connection marking."""

    MINIMUM = "minimum"
    TRANSITION_STATE = "transition_state"
    UNSPECIFIED = "unspecified"


class NodeKind(StrEnum):
    """D69: a pathway node, or a free species (a substrate or a fragment that joins or leaves
    on a transition). A free species is not drawn on the canvas and has no step, branch,
    group or edges."""

    NODE = "node"
    SPECIES = "species"


class Base(DeclarativeBase):
    type_annotation_map = {dict[str, Any]: JSON, list[Any]: JSON}


class InvestigationInfo(Base):
    __tablename__ = "investigation_info"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class Node(Base):
    __tablename__ = "nodes"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    # Creation order. Timestamps can tie (the Windows clock ticks every ~15 ms) and ids are
    # random, so neither gives a stable order.
    seq: Mapped[int] = mapped_column(
        Integer, index=True, default=text("(SELECT COALESCE(MAX(seq), 0) + 1 FROM nodes)")
    )
    label: Mapped[str] = mapped_column(String(200), default="")
    kind: Mapped[str] = mapped_column(String(16), default=NodeKind.NODE, server_default="node")
    role: Mapped[str] = mapped_column(String(32), default=Role.UNSPECIFIED)
    charge: Mapped[int | None] = mapped_column(Integer, default=None)
    multiplicity: Mapped[int | None] = mapped_column(Integer, default=None)
    status: Mapped[str] = mapped_column(String(32), default=Status.PLANNED)
    tags: Mapped[list[Any]] = mapped_column(default=list)
    notes: Mapped[str] = mapped_column(Text, default="")
    # Geometry as [[element, x, y, z], ...] in Å; None until coordinates are entered.
    geometry: Mapped[list[Any] | None] = mapped_column(default=None)
    derived_from_id: Mapped[str | None] = mapped_column(
        ForeignKey("nodes.id", ondelete="SET NULL"), default=None
    )
    # Pathway structure (phase 3). A node has at most one step (FR-STEP-02) and at most one
    # branch (P5). Group members have no branch; `origin_branch_id` records the branch a member
    # came from, as information only (D13, P4).
    step_id: Mapped[str | None] = mapped_column(
        ForeignKey("reaction_steps.id", ondelete="SET NULL"), default=None, index=True
    )
    branch_id: Mapped[str | None] = mapped_column(
        ForeignKey("branches.id", ondelete="SET NULL"), default=None, index=True
    )
    group_id: Mapped[str | None] = mapped_column(
        ForeignKey("group_nodes.id", ondelete="SET NULL"), default=None, index=True
    )
    origin_branch_id: Mapped[str | None] = mapped_column(
        ForeignKey("branches.id", ondelete="SET NULL"), default=None
    )
    pos_x: Mapped[float] = mapped_column(default=0.0)
    pos_y: Mapped[float] = mapped_column(default=0.0)
    # The orientation saved from the 3D view as a unit quaternion [x, y, z, w] turning the
    # coordinates (about their centre) into the picture: x right, y up, z towards the viewer.
    # Structure-mode cards draw the node this way; None means the default (principal axes).
    view_rotation: Mapped[list[Any] | None] = mapped_column(default=None)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)

    calculations: Mapped[list["Calculation"]] = relationship(
        back_populates="node", cascade="all, delete-orphan", passive_deletes=True
    )


class CalculationType(StrEnum):
    """FR-CALC-01."""

    OPTIMIZATION = "optimization"
    TS_OPTIMIZATION = "ts_optimization"
    FREQUENCY = "frequency"
    SINGLE_POINT = "single_point"
    CONFORMER_SEARCH = "conformer_search"
    OTHER = "other"


class LevelOfTheory(Base):
    """FR-CALC-02: two levels are equal only if all five parts match. A custom basis set or
    IOp-modified dispersion is stored by its user-given name (FR-CALC-03, D59). Empty text
    means "none" (no dispersion, gas phase)."""

    __tablename__ = "levels_of_theory"
    __table_args__ = (
        UniqueConstraint("program", "method", "basis", "dispersion", "solvation_model", "solvent"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    program: Mapped[str] = mapped_column(String(32))
    method: Mapped[str] = mapped_column(String(64), default="")
    basis: Mapped[str] = mapped_column(String(64), default="")
    dispersion: Mapped[str] = mapped_column(String(64), default="")
    solvation_model: Mapped[str] = mapped_column(String(32), default="")
    solvent: Mapped[str] = mapped_column(String(64), default="")


class CustomBasis(Base):
    """A named GEN/GENECP basis (FR-CALC-03). `definition` holds the basis and ECP text per
    element as Gaussian printed it; `fingerprints` its hashes, used to recognise the same
    basis in later imports, also for molecules with other elements."""

    __tablename__ = "custom_bases"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(64), unique=True)
    description: Mapped[str] = mapped_column(Text, default="")
    definition: Mapped[dict[str, Any]] = mapped_column(default=dict)
    fingerprints: Mapped[dict[str, Any]] = mapped_column(default=dict)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class CustomDispersion(Base):
    """A named IOp-modified dispersion, e.g. GD3BJ with IOp(3/174–178) = "GD3MBJ" (D59)."""

    __tablename__ = "custom_dispersions"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(64), unique=True)
    base: Mapped[str] = mapped_column(String(64), default="")
    iops: Mapped[dict[str, Any]] = mapped_column(default=dict)
    description: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class SourceFile(Base):
    """A copy of an imported file inside the investigation (D16, FR-FILE-01/02). The copy
    lives at `stored_path`, relative to the investigation folder with '/' separators, and is
    never modified. One file can hold several calculations (one per job step)."""

    __tablename__ = "source_files"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    stored_path: Mapped[str] = mapped_column(String(512))
    original_name: Mapped[str] = mapped_column(String(255), default="")
    origin_device: Mapped[str] = mapped_column(String(200), default="")
    origin_path: Mapped[str] = mapped_column(Text, default="")
    checksum: Mapped[str] = mapped_column(String(64), index=True)  # SHA-256
    size: Mapped[int] = mapped_column(Integer, default=0)
    imported_at: Mapped[datetime] = mapped_column(default=utcnow)


class Calculation(Base):
    __tablename__ = "calculations"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    node_id: Mapped[str] = mapped_column(ForeignKey("nodes.id", ondelete="CASCADE"), index=True)
    type: Mapped[str] = mapped_column(String(32), default=CalculationType.OTHER)
    program: Mapped[str] = mapped_column(String(32), default="")
    program_version: Mapped[str] = mapped_column(String(32), default="")
    level_id: Mapped[str | None] = mapped_column(
        ForeignKey("levels_of_theory.id", ondelete="SET NULL"), default=None
    )
    # FR-CALC-04: for single points, the level the node's geometry was optimized at.
    geometry_level_id: Mapped[str | None] = mapped_column(
        ForeignKey("levels_of_theory.id", ondelete="SET NULL"), default=None
    )
    # The level as parsed from the file; `level_id` may later point to a user override (P13).
    parsed_level: Mapped[dict[str, Any] | None] = mapped_column(default=None)
    route: Mapped[str] = mapped_column(Text, default="")
    title: Mapped[str] = mapped_column(Text, default="")
    step_index: Mapped[int | None] = mapped_column(Integer, default=None)  # 1-based, in file
    step_count: Mapped[int | None] = mapped_column(Integer, default=None)
    termination: Mapped[str] = mapped_column(String(16), default="unknown")
    charge: Mapped[int | None] = mapped_column(Integer, default=None)
    multiplicity: Mapped[int | None] = mapped_column(Integer, default=None)
    # The geometry this calculation ended on, [[element, x, y, z], ...] (W-GEOM, INV-6).
    geometry: Mapped[list[Any] | None] = mapped_column(default=None)
    parse_warnings: Mapped[list[Any]] = mapped_column(default=list)  # W-PARSE fields
    source_file_id: Mapped[str | None] = mapped_column(
        ForeignKey("source_files.id", ondelete="SET NULL"), default=None, index=True
    )
    notes: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(default=utcnow)

    node: Mapped[Node] = relationship(back_populates="calculations")
    level: Mapped[LevelOfTheory | None] = relationship(foreign_keys=[level_id])
    geometry_level: Mapped[LevelOfTheory | None] = relationship(foreign_keys=[geometry_level_id])
    source_file: Mapped[SourceFile | None] = relationship()
    result: Mapped["CalculationResult | None"] = relationship(
        back_populates="calculation", cascade="all, delete-orphan", passive_deletes=True
    )


class CalculationResult(Base):
    """Parsed results of one calculation (docs/spec/05 §1), in hartree, K and atm. Printed
    thermochemistry lines are kept verbatim in `printed`."""

    __tablename__ = "calculation_results"

    calculation_id: Mapped[str] = mapped_column(
        ForeignKey("calculations.id", ondelete="CASCADE"), primary_key=True
    )
    energy: Mapped[float | None] = mapped_column(default=None)  # final SCF energy
    zpe: Mapped[float | None] = mapped_column(default=None)
    e_corr: Mapped[float | None] = mapped_column(default=None)
    h_corr: Mapped[float | None] = mapped_column(default=None)
    g_corr: Mapped[float | None] = mapped_column(default=None)
    e_zpe: Mapped[float | None] = mapped_column(default=None)
    e_thermal: Mapped[float | None] = mapped_column(default=None)
    h: Mapped[float | None] = mapped_column(default=None)
    g: Mapped[float | None] = mapped_column(default=None)
    temperature: Mapped[float | None] = mapped_column(default=None)
    pressure: Mapped[float | None] = mapped_column(default=None)
    molecular_mass: Mapped[float | None] = mapped_column(default=None)
    symmetry_number: Mapped[int | None] = mapped_column(Integer, default=None)
    point_group: Mapped[str | None] = mapped_column(String(16), default=None)
    rotational_constants: Mapped[list[Any] | None] = mapped_column(default=None)  # GHz
    rotational_temperatures: Mapped[list[Any] | None] = mapped_column(default=None)  # K
    frequencies: Mapped[list[Any]] = mapped_column(default=list)  # cm⁻¹, imaginary negative
    # Large; loaded only when used (the 3D mode animation in phase 3).
    reduced_masses: Mapped[list[Any]] = mapped_column(default=list, deferred=True)
    normal_modes: Mapped[list[Any]] = mapped_column(
        default=list, deferred=True
    )  # [mode][atom][xyz]
    imaginary_count: Mapped[int | None] = mapped_column(Integer, default=None)
    optimization_converged: Mapped[bool | None] = mapped_column(default=None)
    geometry_count: Mapped[int] = mapped_column(Integer, default=0)
    printed: Mapped[dict[str, Any]] = mapped_column(default=dict)

    calculation: Mapped[Calculation] = relationship(back_populates="result")


class ReactionStep(Base):
    """A conceptual position in the mechanism shared by all branches (D3, P2), in a
    user-defined order (FR-STEP-01)."""

    __tablename__ = "reaction_steps"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    position: Mapped[int] = mapped_column(Integer, default=0)
    name: Mapped[str] = mapped_column(String(200), default="")
    notes: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


branch_parents = Table(
    "branch_parents",
    Base.metadata,
    Column("branch_id", ForeignKey("branches.id", ondelete="CASCADE"), primary_key=True),
    Column("parent_id", ForeignKey("branches.id", ondelete="CASCADE"), primary_key=True),
)


class Branch(Base):
    """A named, coloured lineage (D12). Parents: one for a split, several for the outgoing
    branch of a reconnection (02 §2)."""

    __tablename__ = "branches"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    seq: Mapped[int] = mapped_column(
        Integer, default=text("(SELECT COALESCE(MAX(seq), 0) + 1 FROM branches)")
    )
    name: Mapped[str] = mapped_column(String(200), default="")
    colour: Mapped[str] = mapped_column(String(16), default="#2459c6")
    status: Mapped[str] = mapped_column(String(32), default=Status.PLANNED)
    notes: Mapped[str] = mapped_column(Text, default="")
    # FR-BR-02: the node whose branch was split into this one.
    # use_alter breaks the nodes ↔ branches foreign-key cycle for table sorting only.
    split_node_id: Mapped[str | None] = mapped_column(
        ForeignKey("nodes.id", ondelete="SET NULL", use_alter=True), default=None
    )
    created_at: Mapped[datetime] = mapped_column(default=utcnow)

    parents: Mapped[list["Branch"]] = relationship(
        secondary=branch_parents,
        primaryjoin=lambda: Branch.id == branch_parents.c.branch_id,
        secondaryjoin=lambda: Branch.id == branch_parents.c.parent_id,
        order_by=lambda: Branch.seq,
    )


group_incoming_branches = Table(
    "group_incoming_branches",
    Base.metadata,
    Column("group_id", ForeignKey("group_nodes.id", ondelete="CASCADE"), primary_key=True),
    Column("branch_id", ForeignKey("branches.id", ondelete="CASCADE"), primary_key=True),
)


class GroupNode(Base):
    """A canvas node holding several member nodes and at most one representative chosen by
    the user (D18, FR-GRP-02). Used for reconnections (D13) and later CREST ensembles (D46)."""

    __tablename__ = "group_nodes"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    seq: Mapped[int] = mapped_column(
        Integer, default=text("(SELECT COALESCE(MAX(seq), 0) + 1 FROM group_nodes)")
    )
    label: Mapped[str] = mapped_column(String(200), default="")
    notes: Mapped[str] = mapped_column(Text, default="")
    step_id: Mapped[str | None] = mapped_column(
        ForeignKey("reaction_steps.id", ondelete="SET NULL"), default=None
    )
    representative_id: Mapped[str | None] = mapped_column(
        ForeignKey("nodes.id", ondelete="SET NULL", use_alter=True), default=None
    )
    outgoing_branch_id: Mapped[str | None] = mapped_column(
        ForeignKey("branches.id", ondelete="SET NULL"), default=None
    )
    pos_x: Mapped[float] = mapped_column(default=0.0)
    pos_y: Mapped[float] = mapped_column(default=0.0)
    # How expanded members are laid out: "grid", "vertical" or "horizontal" (A21).
    layout: Mapped[str] = mapped_column(String(16), default="grid", server_default="grid")
    created_at: Mapped[datetime] = mapped_column(default=utcnow)

    incoming_branches: Mapped[list[Branch]] = relationship(
        secondary=group_incoming_branches, order_by=lambda: Branch.seq
    )


class Transition(Base):
    """A directed edge between two nodes or group nodes (D3, FR-EDGE-01). Each end is either a
    node or a group. Whether it is a direct connection ("no TS", D53) is computed from the
    endpoints' roles, so it follows role changes."""

    __tablename__ = "transitions"
    __table_args__ = (
        CheckConstraint("(source_node_id IS NULL) != (source_group_id IS NULL)", name="one_source"),
        CheckConstraint("(target_node_id IS NULL) != (target_group_id IS NULL)", name="one_target"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    seq: Mapped[int] = mapped_column(
        Integer, default=text("(SELECT COALESCE(MAX(seq), 0) + 1 FROM transitions)")
    )
    source_node_id: Mapped[str | None] = mapped_column(
        ForeignKey("nodes.id", ondelete="CASCADE"), default=None, index=True
    )
    source_group_id: Mapped[str | None] = mapped_column(
        ForeignKey("group_nodes.id", ondelete="CASCADE"), default=None, index=True
    )
    target_node_id: Mapped[str | None] = mapped_column(
        ForeignKey("nodes.id", ondelete="CASCADE"), default=None, index=True
    )
    target_group_id: Mapped[str | None] = mapped_column(
        ForeignKey("group_nodes.id", ondelete="CASCADE"), default=None, index=True
    )
    status: Mapped[str] = mapped_column(String(32), default=Status.PLANNED)
    notes: Mapped[str] = mapped_column(Text, default="")
    # D76: the side of each box the arrow leaves from and arrives at; display only.
    source_side: Mapped[str] = mapped_column(String(8), default="right", server_default="right")
    target_side: Mapped[str] = mapped_column(String(8), default="left", server_default="left")
    created_at: Mapped[datetime] = mapped_column(default=utcnow)

    species: Mapped[list["TransitionSpecies"]] = relationship(
        back_populates="transition",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by=lambda: (TransitionSpecies.created_at, TransitionSpecies.id),
    )

    @property
    def source_id(self) -> str:
        return self.source_node_id or self.source_group_id  # type: ignore[return-value]

    @property
    def target_id(self) -> str:
        return self.target_node_id or self.target_group_id  # type: ignore[return-value]


class TransitionSpecies(Base):
    """D69: a free species that joins (association) or leaves (dissociation) on a transition,
    `count` times. Profiles, the energy table and edge differences add or subtract its energy
    so every point on a pathway has the same atoms."""

    __tablename__ = "transition_species"
    __table_args__ = (
        UniqueConstraint("transition_id", "species_id", name="one_entry_per_species"),
        CheckConstraint("direction IN ('joins', 'leaves')", name="direction_known"),
        CheckConstraint("count >= 1", name="count_positive"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    transition_id: Mapped[str] = mapped_column(
        ForeignKey("transitions.id", ondelete="CASCADE"), index=True
    )
    species_id: Mapped[str] = mapped_column(ForeignKey("nodes.id", ondelete="CASCADE"), index=True)
    direction: Mapped[str] = mapped_column(String(8))
    count: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)

    transition: Mapped[Transition] = relationship(back_populates="species")


class HistoryEntry(Base):
    """FR-HIST-01: append-only record of every change."""

    __tablename__ = "history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    timestamp: Mapped[datetime] = mapped_column(default=utcnow, index=True)
    record_type: Mapped[str] = mapped_column(String(32))
    record_id: Mapped[str] = mapped_column(String(32), index=True)
    # create | update | delete, and split | reconnect | add_members | dissolve for the
    # structural actions that INV-3 requires to be recorded as one explicit entry each.
    action: Mapped[str] = mapped_column(String(16))
    field: Mapped[str | None] = mapped_column(String(64), default=None)
    old_value: Mapped[Any] = mapped_column(JSON, nullable=True, default=None)
    new_value: Mapped[Any] = mapped_column(JSON, nullable=True, default=None)
    source: Mapped[str] = mapped_column(String(16), default="manual")  # manual | import


class AlignmentSet(Base):
    """D80, FR-3D-07: a named set of alignment atoms ("Ru–CAAC core"), one list per node, for
    overlays. Layout-like data: saved in the investigation but not in the history (A31)."""

    __tablename__ = "alignment_sets"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(200), unique=True)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)

    entries: Mapped[list["AlignmentSetAtoms"]] = relationship(
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by=lambda: (AlignmentSetAtoms.created_at, AlignmentSetAtoms.node_id),
    )


class AlignmentSetAtoms(Base):
    """One node's alignment atoms in a set: 1-based numbers of the full structure, paired in
    order with the other nodes' lists. Deleting the node removes it from the set."""

    __tablename__ = "alignment_set_atoms"

    set_id: Mapped[str] = mapped_column(
        ForeignKey("alignment_sets.id", ondelete="CASCADE"), primary_key=True
    )
    node_id: Mapped[str] = mapped_column(
        ForeignKey("nodes.id", ondelete="CASCADE"), primary_key=True, index=True
    )
    atoms: Mapped[list[Any]] = mapped_column(default=list)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
