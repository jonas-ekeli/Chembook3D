"""The Inspect window (D123c, FR-3D-11): an output or xyz file read without importing it.

The file is read with the import's parsers and shown as its job steps (route, charge and
multiplicity, termination, last SCF energy, imaginary frequencies), each step's structures with
their energies and convergence criteria (`trajectory.frames_of`, as the step movie plays them)
and its normal modes. Nothing is written to the investigation.

A job still running leaves its output cut off anywhere. The parsers stop at the last whole
block they can read; should one still fail on the cut, the file is read again without its last
lines, a few more each time, and the window says how much was left out.

Besides what the import reads, xyz files open too: one or many structures (`xtbopt.xyz`, a
CREST ensemble), with the energy their comment lines carry, and xTB's optimization log
`xtbopt.log`, whose cycles come with their energy change and gradient norm.
"""

from dataclasses import dataclass, field
from pathlib import Path

from chembook3d import xyz
from chembook3d.parsers import crest, xtb
from chembook3d.parsers.common import ParsedFile, ParsedStep
from chembook3d.services import imports, trajectory

# The largest file read (bytes): a long Gaussian optimization is tens of MB.
MAX_SIZE = 200_000_000
# How many of the last lines are left out, in turn, when a cut-off file cannot be read whole.
CUT_BACK = (1, 2, 5, 10, 20, 50, 100, 200, 500, 1000, 2000, 5000, 10000)


class InspectFailed(ValueError):
    """The file cannot be shown; the message says why."""


@dataclass
class Modes:
    xyz: str  # the structure the modes belong to
    frequencies: list[float]
    order: list[int]  # imaginary first (FR-3D-03)
    modes: list[list[list[float]]]


@dataclass
class Step:
    index: int
    job_type: str
    route: str
    method: str | None
    basis: str | None
    charge: int | None
    multiplicity: int | None
    termination: str
    energy: float | None  # hartree, the step's last SCF energy
    optimization_converged: bool | None
    frequencies: list[float]
    imaginary: int
    scan: str | None
    points: int
    frames: list[trajectory.Frame]
    modes: Modes | None
    missing: list[str]


@dataclass
class Inspection:
    program: str
    version: str | None
    steps: list[Step]
    warnings: list[str] = field(default_factory=list)


def _modes(step: ParsedStep) -> Modes | None:
    atoms = step.final_geometry
    if not step.frequencies or not step.normal_modes or not atoms:
        return None
    if len(step.normal_modes) != len(step.frequencies) or any(
        len(mode) != len(atoms) for mode in step.normal_modes
    ):
        return None
    frequencies = [float(f) for f in step.frequencies]
    return Modes(
        xyz=xyz.format_xyz(atoms),
        frequencies=frequencies,
        order=sorted(range(len(frequencies)), key=lambda i: (frequencies[i] >= 0, i)),
        modes=step.normal_modes,
    )


def _step(step: ParsedStep) -> Step:
    route = step.route
    frames = trajectory.frames_of(step) if step.geometries else []
    return Step(
        index=step.index,
        job_type=step.job_type,
        route=route.text if route else "",
        method=route.method if route else None,
        basis=(step.resolved_basis or route.basis) if route else None,
        charge=step.charge,
        multiplicity=step.multiplicity,
        termination=step.termination,
        energy=step.scf_energy,
        optimization_converged=step.optimization_converged,
        frequencies=list(step.frequencies),
        imaginary=step.imaginary_count,
        scan=step.scan,
        points=len({f.point for f in frames if f.point is not None}),
        frames=frames,
        modes=_modes(step),
        missing=list(step.missing),
    )


def _xyz_frames(text: str) -> tuple[list[tuple[str, list[xyz.Atom]]], bool]:
    """Every whole structure of an xyz file with its comment line, and whether the file ends
    inside one (a structure being written)."""
    lines = text.lstrip("﻿").splitlines()
    frames: list[tuple[str, list[xyz.Atom]]] = []
    i = 0
    while i < len(lines):
        if not lines[i].strip():
            i += 1
            continue
        header = lines[i].strip()
        if not header.isdigit():
            return frames, True
        count = int(header)
        if i + 2 + count > len(lines):
            return frames, True
        atoms = []
        for line in lines[i + 2 : i + 2 + count]:
            parts = line.split()
            element = xyz.element_of(parts[0]) if len(parts) >= 4 else None
            if element is None:
                return frames, True
            try:
                atoms.append(xyz.Atom(element, *(float(v) for v in parts[1:4])))
            except ValueError:
                return frames, True
        if len(atoms) != count:
            return frames, True
        frames.append((lines[i + 1], atoms))
        i += 2 + count
    return frames, False


def _comment_energy(comment: str) -> float | None:
    """`energy: -15.4 …` as xTB writes it, or a bare first number as CREST does."""
    keys, _ = xtb._comment(comment)
    if "energy" in keys:
        return xtb._float(keys["energy"])
    first = comment.split()[0] if comment.split() else None
    return xtb._float(first)


def _structures(text: str, name: str) -> Inspection:
    frames, cut = _xyz_frames(text)
    if not frames:
        raise InspectFailed(f"No structures were found in {name or 'the file'}.")
    first = [a.element for a in frames[0][1]]
    kept = [f for f in frames if [a.element for a in f[1]] == first]
    step = ParsedStep(
        index=1,
        geometries=[atoms for _, atoms in kept],
        geometry_energies=[_comment_energy(comment) for comment, _ in kept],
        termination="abnormal" if cut else "normal",
    )
    warnings = []
    if len(kept) < len(frames):
        warnings.append(
            f"{len(frames) - len(kept)} structures have other atoms than the first; not shown."
        )
    if cut:
        warnings.append(f"The file ends inside structure {len(frames) + 1}, which is not shown.")
    program = crest.PROGRAM if crest.looks_like_ensemble(text) else "XYZ"
    inspection = Inspection(program=program, version=None, steps=[_step(step)], warnings=warnings)
    inspection.steps[0].job_type = "structures"
    return inspection


def _parse(text: str, name: str, folder: Path | None) -> ParsedFile | crest.Ensemble:
    if xtb.is_optimization_log(text):
        outputs = imports.scan_outputs(folder, xtb.wrote_optimization_log) if folder else []
        try:
            return xtb.parse_optimization_log(text, name, outputs)
        except xtb.NotXtbOutput as exc:
            raise imports.ImportFailed(str(exc)) from exc
    return imports.read_file(text, name, folder)


def _lines_kept(text: str, cut: int) -> str:
    lines = text.splitlines(keepends=True)
    return "".join(lines[: max(len(lines) - cut, 0)])


def parse_text(
    text: str, name: str = "", folder: Path | None = None
) -> tuple[ParsedFile | crest.Ensemble, list[str]]:
    """The file parsed as the import reads it, a cut-off end left out when the parsers fail on
    it, with a warning saying so. Also read by the scan editor (D135)."""
    warnings: list[str] = []
    try:
        parsed = _parse(text, name, folder)
    except imports.ImportFailed:
        raise
    except Exception:  # a cut the parser did not expect: read less of it
        for cut in CUT_BACK:
            try:
                parsed = _parse(_lines_kept(text, cut), name, folder)
            except Exception:  # noqa: S112 - the next try leaves out more
                continue
            warnings.append(
                f"The end of the file could not be read (the job may still be writing it); "
                f"the last {cut} lines are left out."
            )
            break
        else:
            raise InspectFailed(f"{name or 'The file'} could not be read.") from None
    return parsed, warnings


def read(text: str, name: str = "", folder: Path | None = None) -> Inspection:
    """`folder` is where the file is, for the xTB output beside an xTB log (D112, D123c)."""
    try:
        parsed, warnings = parse_text(text, name, folder)
    except imports.ImportFailed as exc:
        if text.lstrip("﻿").split("\n", 1)[0].strip().isdigit():
            return _structures(text, name)
        raise InspectFailed(str(exc)) from exc
    if isinstance(parsed, crest.Ensemble):
        return _structures(text, name)
    steps = [_step(step) for step in parsed.steps]
    if steps and steps[-1].termination != "normal":
        warnings.append(
            "The last step has not ended normally: the job is still running, or it stopped."
        )
    return Inspection(
        program=parsed.program, version=parsed.version, steps=steps, warnings=warnings
    )


def read_text_of(path: Path) -> str:
    """A file's text, refused when too large (MAX_SIZE) or not text."""
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise InspectFailed(f"Cannot read {path}") from exc
    if size > MAX_SIZE:
        raise InspectFailed(f"{path.name} is {size / 1e6:.0f} MB, more than the window reads.")
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise InspectFailed(f"Cannot read {path}") from exc
    if b"\x00" in data[:20000]:
        raise InspectFailed(f"{path.name} is not a text file.")
    return data.decode("utf-8", errors="replace")


def read_path(path: Path, name: str | None = None) -> Inspection:
    return read(read_text_of(path), name or path.name, path.parent)
