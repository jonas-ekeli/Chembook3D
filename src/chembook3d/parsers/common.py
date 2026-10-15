"""What every output parser returns (docs/spec/05 §3): one ParsedStep per job step, with
the level of theory in a Route. Energies stay in hartree exactly as printed; what cannot be
read is listed in ParsedStep.missing so the import can report it as W-PARSE (FR-IMP-04)."""

from dataclasses import dataclass, field
from typing import Any

from chembook3d.xyz import Atom


@dataclass
class Route:
    """The level of theory and job type of a step, as written in the input."""

    text: str
    method: str | None = None
    basis: str | None = None  # as written, upper case; GEN, GENECP and CHKBASIS included
    job_type: str | None = None
    compound_freq: bool = False  # `opt freq`: Gaussian runs the freq as a second step
    dispersion: str | None = None
    iops: dict[str, str] = field(default_factory=dict)
    solvation_model: str | None = None
    solvent: str | None = None
    irc: list[str] | None = None  # D131: an IRC job's options as written (upper case)


@dataclass
class Criterion:
    """D123b: one convergence criterion an optimizer printed for a structure, as Molden shows
    it: the value, the threshold it must get below (None for one only reported) and whether
    the program counted it as met."""

    name: str
    value: float
    threshold: float | None
    converged: bool | None
    unit: str = ""


@dataclass
class IrcPoint:
    """D131: one point of an IRC as the program reported it."""

    path: int  # 1, or 2 for the second direction of a job that runs both ways
    number: int  # 0 is the TS the IRC starts from
    structure: int  # its index in ParsedStep.geometries
    energy: float | None  # hartree
    coordinate: float | None  # the program's own reaction coordinate from the TS, as printed


@dataclass
class IrcRun:
    """D131: what an IRC job step did: the direction it was asked to follow ("forward",
    "reverse" or "both"), the direction of each path it printed, its points and how each path
    ended: "minimum" (the program found one), "max_points" (it stopped at its maximum number
    of points), "complete" (it ended for another reason it printed) or "stopped" (the file
    ends before the path does: an error, or a job still running)."""

    requested: str
    directions: dict[int, str] = field(default_factory=dict)
    points: list[IrcPoint] = field(default_factory=list)
    endings: dict[int, str] = field(default_factory=dict)
    ts_energy: float | None = None
    max_points: int | None = None


@dataclass
class ParsedStep:
    index: int  # 1-based position in the file
    route: Route | None = None
    title: str | None = None
    charge: int | None = None
    multiplicity: int | None = None
    geometries: list[list[Atom]] = field(default_factory=list)
    # D101: per structure in `geometries`, the SCF energy computed on it and, in a scan, the
    # point it belongs to (1-based); `converged_geometries` holds the indexes (0-based) of the
    # structures on which an optimization, or one scan point's optimization, converged.
    # Structures a step only inherited (Geom=Check) have no entries.
    geometry_energies: list[float | None] = field(default_factory=list)
    geometry_points: list[int | None] = field(default_factory=list)
    converged_geometries: list[int] = field(default_factory=list)
    # D123b: per structure in `geometries`, the convergence criteria the optimizer printed for
    # it (an empty list where it printed none). Empty when no structure has any.
    geometry_criteria: list[list[Criterion]] = field(default_factory=list)
    scan: str | None = None  # "relaxed" (an optimization per point) or "rigid"
    # D112: an xTB scan path's stage per structure (empty when the file names none), and the
    # structure (0-based) its node takes when that is not the last one: the path's top.
    geometry_stages: list[int | None] = field(default_factory=list)
    node_geometry: int | None = None
    irc: IrcRun | None = None  # D131: an IRC step's points and directions
    scf_energy: float | None = None
    optimization_converged: bool | None = None  # None when the step is not an optimization
    termination: str = "abnormal"  # normal | abnormal (05 §3.1: missing means abnormal)
    # GEN/GENECP basis read from the input: {element: definition text}, and its header.
    basis_definition: dict[str, str] | None = None
    frequencies: list[float] = field(default_factory=list)
    reduced_masses: list[float] = field(default_factory=list)
    normal_modes: list[list[list[float]]] = field(default_factory=list)  # [mode][atom][xyz]
    thermo: dict[str, Any] = field(default_factory=dict)
    printed: dict[str, str] = field(default_factory=dict)  # thermochemistry lines verbatim
    missing: list[str] = field(default_factory=list)
    # Filled in by carrying the basis forward (CHKBASIS reads it from the checkpoint).
    resolved_basis: str | None = None
    resolved_basis_definition: dict[str, str] | None = None

    @property
    def job_type(self) -> str:
        return self.route.job_type if self.route and self.route.job_type else "other"

    @property
    def final_geometry(self) -> list[Atom] | None:
        """The structure the step leaves its node on: the last one, or a path's chosen point."""
        if not self.geometries:
            return None
        return self.geometries[self.node_geometry if self.node_geometry is not None else -1]

    @property
    def imaginary_count(self) -> int:
        return sum(1 for f in self.frequencies if f < 0)


@dataclass
class ParsedFile:
    program: str
    version: str | None
    steps: list[ParsedStep]
