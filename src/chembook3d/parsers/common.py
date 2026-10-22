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
    counterpoise: int | None = None  # D161: Counterpoise=N, the number of fragments


# D158: the key in `ParsedStep.printed` (and the stored result's `printed`) of a frequency
# step the program judged not to be at a stationary point: the criteria lines not met, as
# printed. Gaussian prints the check after a frequency job; ORCA and xTB print none.
NOT_STATIONARY = "not_stationary"

# D162: the keys in `ParsedStep.printed` (and the stored result's `printed`) of the step's
# spin expectation value, as printed, the last one of the step: <S**2> (Gaussian's before
# annihilation, ORCA's "Expectation value of <S**2>"), Gaussian's after annihilation and
# ORCA's ideal S(S+1). Absent when the program printed none (a closed-shell calculation).
S2 = "s2"
S2_AFTER = "s2_after"
S2_IDEAL = "s2_ideal"
SPIN_KEYS = (S2, S2_AFTER, S2_IDEAL)

# D161: the key in `ParsedStep.printed` (and the stored result's `printed`) of a counterpoise
# job's basis set superposition error in hartree, as printed ("BSSE energy ="). The step's
# energy is then the counterpoise-corrected total; the complex's uncorrected energy is that
# total minus the BSSE (the BSSE is positive: the borrowed basis functions lower the energy).
BSSE = "bsse"


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
    # D133: the files beside the output its points were read from (ORCA's trajectories, by
    # name, with their contents to copy with the output), and those that were not there
    files: dict[str, bytes] = field(default_factory=dict, repr=False)
    missing: list[str] = field(default_factory=list)


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
    # D135: the coordinates a scan drives, as the file names them (atoms from 1: two for a
    # distance, three for an angle, four for a dihedral); empty when the file does not say.
    scan_coordinates: list[list[int]] = field(default_factory=list)
    # D112: an xTB scan path's stage per structure (empty when the file names none), and the
    # structure (0-based) its node takes when that is not the last one: the path's top.
    geometry_stages: list[int | None] = field(default_factory=list)
    node_geometry: int | None = None
    irc: IrcRun | None = None  # D131: an IRC step's points and directions
    scf_energy: float | None = None  # the step's energy: a correlated method's total (D153)
    # D153: the printed total `scf_energy` is (e.g. "E(B2PLYPD3)", "EUMP2", "CCSD(T)"); None
    # for an SCF energy. `correlated_missing`: a correlated method whose total was not read.
    energy_label: str | None = None
    correlated_missing: bool = False
    # D161: a counterpoise or ONIOM step whose own total (the counterpoise-corrected or the
    # ONIOM extrapolated energy) was not read: what is missing, for W-PARSE; it has no energy.
    energy_missing: str | None = None
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
    # D137: a joined scan's GEN basis set, known only by its fingerprints {element: hash}.
    basis_fingerprints: dict[str, str] = field(default_factory=dict)
    # D157: what held the structure while it was optimized, as short descriptions ("scan
    # D(3,4,9,10)", "frozen B(1,2)", "2 frozen atoms"): a Gaussian, ORCA or xTB scan, frozen
    # coordinates or frozen atoms, as the output file shows them. Such a step ends on no free
    # stationary point. An xTB scan path (D112) or a joined scan (D137) lists none: it is
    # imported as a path and never as an optimization.
    constraints: list[str] = field(default_factory=list)

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
    def constrained(self) -> bool:
        """D157: a scan, or an optimization with frozen coordinates or atoms."""
        return bool(self.constraints)

    @property
    def imaginary_count(self) -> int:
        return sum(1 for f in self.frequencies if f < 0)


_COORDINATE_LETTERS = {1: "X", 2: "B", 3: "A", 4: "D"}


def coordinate_name(atoms: list[str], letter: str | None = None) -> str:
    """D157: a coordinate as Gaussian's ModRedundant input names it, atoms from 1: `B(1,2)`,
    `A(1,2,3)`, `D(3,4,9,10)`, `X(5)` for one atom's position."""
    letter = (letter or _COORDINATE_LETTERS.get(len(atoms), "")).upper()
    return f"{letter}({','.join(atoms)})"


def add_constraint(step: ParsedStep, description: str) -> None:
    """D157: add one constraint to a step, once."""
    if description not in step.constraints:
        step.constraints.append(description)


@dataclass
class ParsedFile:
    program: str
    version: str | None
    steps: list[ParsedStep]
