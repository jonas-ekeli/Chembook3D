"""Evidence for choosing a mode from a submitted local pilot pre-optimization."""

import hashlib
import math
import re
from pathlib import Path

import numpy as np

from chembook3d import xyz
from chembook3d.parsers import gaussian, saga_job
from chembook3d.remote.checkpoint_ts import LINK, parent_geometry, read_snapshot
from chembook3d.services.geometry import matches
from chembook3d.thermochem import LINEAR_POINT_GROUPS

_NORMAL_END = re.compile(r"^ Normal termination of Gaussian [^\r\n]+", re.M)
_MODE_LINE = re.compile(r"^ Frequencies\s+--\s+(.+)$")
_STAGES = ("SP", "SP", "STABLE", "OPT", "FREQ")
# D269: floor for a mode the agent may pick as a TS guess. The pair's relative motion lies
# within 60° of the bond, and the stretch is at least a quarter of the largest atom's motion.
MIN_ALIGNMENT = 0.5
MIN_BOND_SHARE = 0.25


def _frequency_frame(lines: list[str], elements: list[str]) -> list[list[float]]:
    # Gaussian prints normal displacements in its standard axes when it uses symmetry.
    # Under NoSymm, only the input (or Z-matrix) orientation may be present.
    frames = {}
    for n, line in enumerate(lines):
        if _MODE_LINE.match(line):
            break
        if line.strip() in {"Standard orientation:", "Input orientation:", "Z-Matrix orientation:"}:
            frames[line.strip()] = n
    for name in ("Standard orientation:", "Input orientation:", "Z-Matrix orientation:"):
        if name in frames:
            first = frames[name] + 5
            break
    else:
        raise ValueError("The frequency step did not print a Cartesian orientation for its modes.")
    coordinates = []
    for index, symbol in enumerate(elements, start=1):
        row = lines[first + index - 1].split()
        if len(row) != 6 or row[:2] != [str(index), str(xyz.ELEMENTS.index(symbol) + 1)]:
            raise ValueError("Frequency geometry atom order differs from the submitted parent.")
        coords = [float(v) for v in row[3:]]
        if not all(math.isfinite(v) for v in coords):
            raise ValueError("Frequency geometry contains non-finite coordinates.")
        coordinates.append(coords)
    if set(lines[first + len(elements)].strip()) != {"-"}:
        raise ValueError("Frequency geometry atom count differs from the submitted parent.")
    return coordinates


def _projection(
    index: int,
    frequency: float,
    unit: list[float],
    first: list[str],
    second: list[str],
    largest: float,
) -> dict:
    """Bond evidence for one imaginary mode; `largest` is its largest atom displacement."""
    delta = [float(b) - float(a) for a, b in zip(first, second, strict=True)]
    length = math.hypot(*delta)
    projection = math.fsum(d * u for d, u in zip(delta, unit, strict=True))
    # Each printed component was rounded by at most half a unit in its last place.
    # Propagate both atoms' rounding through the dot product to distinguish motion from zero.
    resolution = 0.0
    for a, b, u in zip(first, second, unit, strict=True):
        for value in (a, b):
            decimals = len(value.partition(".")[2])
            resolution += abs(u) * 0.5 * 10.0**-decimals
    alignment = min(1.0, abs(projection) / length) if length else 0.0
    share = abs(projection) / largest if largest else 0.0
    return {
        "mode_index": index,
        "frequency_cm1": frequency,
        "relative_displacement": length,
        "bond_projection": projection,
        "alignment": alignment,
        "bond_share": share,
        "eligible": abs(projection) > resolution
        and alignment >= MIN_ALIGNMENT
        and share >= MIN_BOND_SHARE,
    }


def _mode_count(coordinates: list[list[float]], point_group: str | None) -> int:
    # Gaussian's full point group identifies a linear rotor even for an almost-linear
    # printed geometry. With symmetry disabled, check collinearity in Cartesian space.
    n = len(coordinates)
    group = (point_group or "").upper()
    if n == 2 or group in LINEAR_POINT_GROUPS:
        return 3 * n - 5
    if group in {"", "C1"}:
        centered = np.asarray(coordinates) - np.mean(coordinates, axis=0)
        axis = np.linalg.svd(centered, full_matrices=False)[2][0]
        perpendicular = centered - np.outer(centered @ axis, axis)
        if np.linalg.norm(perpendicular, axis=1).max() <= 2e-6:  # six-decimal XYZ precision
            return 3 * n - 5
    return 3 * n - 6


def _frequency_evidence(
    lines: list[str],
    elements: list[str],
    pair: list[int],
    optimized: list[xyz.Atom],
    point_group: str | None = None,
) -> dict:
    coordinates = _frequency_frame(lines, elements)
    frame = [[symbol, *v] for symbol, v in zip(elements, coordinates, strict=True)]
    if not all(math.isfinite(v) for a in optimized for v in (a.x, a.y, a.z)):
        raise ValueError("The converged frozen geometry contains non-finite coordinates.")
    if not matches(
        [[a.element, a.x, a.y, a.z] for a in optimized],
        frame,
        2e-6,  # Each Cartesian coordinate is printed to six decimal places in angstroms.
    ):
        raise ValueError("The frequency geometry differs from the converged frozen geometry.")
    i, j = (atom - 1 for atom in pair)
    bond = [b - a for a, b in zip(coordinates[i], coordinates[j], strict=True)]
    length = math.hypot(*bond)
    if not math.isfinite(length) or not length:
        raise ValueError("Cannot determine a bond direction from the frequency geometry.")
    unit = [v / length for v in bond]
    modes, count = [], 0
    atomic_numbers = [str(xyz.ELEMENTS.index(symbol) + 1) for symbol in elements]
    for n, line in enumerate(lines):
        if not (match := _MODE_LINE.match(line)):
            continue
        frequencies = [float(v) for v in match[1].split()]
        if not 1 <= len(frequencies) <= 3 or not all(map(math.isfinite, frequencies)):
            raise ValueError("Incomplete or invalid Gaussian frequency block.")
        numbers = [str(k) for k in range(count + 1, count + len(frequencies) + 1)]
        if n < 2 or numbers not in [lines[n - 2].split(), lines[n - 1].split()]:
            raise ValueError("Gaussian mode numbers are missing or out of sequence.")
        header = next(
            (
                k
                for k in range(n + 1, min(n + 15, len(lines)))
                if lines[k].split()[:2] == ["Atom", "AN"]
            ),
            None,
        )
        if header is None or any(_MODE_LINE.match(s) for s in lines[n + 1 : header]):
            raise ValueError("The frequency block is missing its Cartesian displacement table.")
        rows = []
        for k, number in enumerate(atomic_numbers, start=1):
            fields = lines[header + k].split()
            values = fields[2:]
            if (
                fields[:2] != [str(k), number]
                or len(values) != 3 * len(frequencies)
                or any(not re.fullmatch(r"[+-]?\d{1,3}\.\d{1,12}", v) for v in values)
            ):
                raise ValueError("Incomplete displacement table or mismatched atom order.")
            rows.append(values)
        if re.match(r"^\s*\d+\s+\d+\s+[+-]?\d+\.\d+", lines[header + len(rows) + 1]):
            raise ValueError("Displacement table atom count differs from the submitted parent.")
        for offset, frequency in enumerate(frequencies):
            count += 1
            if frequency < 0:
                col = slice(3 * offset, 3 * offset + 3)
                largest = max(math.hypot(*map(float, row[col])) for row in rows)
                modes.append(
                    _projection(count, frequency, unit, rows[i][col], rows[j][col], largest)
                )
    if not count:
        raise ValueError("The final step did not print frequencies and displacements.")
    expected = _mode_count(coordinates, point_group)
    if count != expected:
        raise ValueError(
            f"Expected {expected} vibrational frequencies for this geometry, found {count}."
        )
    return {"frequency_geometry": frame, "modes": modes}


def preoptimization_modes(text: str, submitted: str, stem: str, pair: list[int]) -> dict:
    atoms, frozen, charge_spin, _ = parent_geometry(submitted, stem)
    if pair != frozen:
        raise ValueError("Expected the frozen atom pair in the submitted parent.")
    inputs = LINK.split(submitted.replace("\r\n", "\n"))
    endings = list(_NORMAL_END.finditer(text))
    if (
        len(endings) != 5
        or text[endings[-1].end() :].strip()
        or re.search(r"^[ \t]*Error termination\b", text, re.M)
    ):
        raise ValueError("Expected five normally terminated Gaussian parent steps.")
    steps = gaussian.parse(text).steps
    if len(steps) != 5:
        raise ValueError("Expected five Gaussian parent steps.")
    start = 0
    for step, expected, end, input_step in zip(steps, _STAGES, endings, inputs, strict=True):
        chunk = text[start : end.start()]
        start = end.end()
        words = gaussian.split_route_words(step.route.text if step.route else "")
        keys = {gaussian.keyword_of(word)[0] for word in words}
        if (
            step.termination != "normal"
            or keys.intersection(_STAGES) != {expected}
            or not re.search(r"^[ \t]*-{10,}[ \t]*\r?\n #", chunk, re.M)
        ):
            raise ValueError("Expected normally terminated SP, SP, stability, Opt and Freq steps.")
        header = re.split(r"\n[ \t]*\n", input_step.strip(), maxsplit=1)[0]
        approved_words = gaussian.split_route_words(header[header.index("#") :])
        if sorted(w.upper() for w in words) != sorted(w.upper() for w in approved_words):
            raise ValueError("The printed parent route differs from the approved project input.")
        if (step.charge, step.multiplicity) != tuple(map(int, charge_spin)):
            raise ValueError("The reported parent charge or multiplicity differs from its input.")
    opt = steps[3]
    if (
        opt.job_type != "optimization"
        or not opt.optimization_converged
        or not opt.converged_geometries
    ):
        raise ValueError("The constrained parent optimization did not converge.")
    bond_names = {
        f"frozen {letter}({a},{b})".upper() for a, b in (pair, pair[::-1]) for letter in ("B", "R")
    }
    if len(opt.constraints) != 1 or opt.constraints[0].upper() not in bond_names:
        raise ValueError("The optimized frozen bond does not match the selected parent distance.")
    optimized = opt.geometries[opt.converged_geometries[-1]]
    elements = [atom.element for atom in atoms]
    return _frequency_evidence(
        text[endings[3].end() :].splitlines(),
        elements,
        pair,
        optimized,
        steps[4].thermo.get("point_group"),
    )


def completed_copyback(log: str, log_path: Path, stem: str, job_id: str) -> tuple[float, bool]:
    report = saga_job.parse(log, job_id, log_path.as_posix())
    start = report.start_timestamp()
    if (
        report.job_name != stem
        or report.state != "completed"
        or report.wrapper_termination != "normal"
        or not report.copy_announced
        or start is None
    ):
        raise ValueError("The wrapper log must confirm this job and its copied output.")
    copy = f"Copying './{stem}.fchk.gz' to submission folder..."
    failure = f"File './{stem}.fchk.gz' could not be copied back home!"
    lines = {line.strip() for line in log[saga_job.START.search(log).end() :].splitlines()}
    return start, copy in lines and failure not in lines


def inspect_preoptimization(
    submission: Path,
    stem: str,
    job_id: str,
    pair: list[int],
    *,
    submitted_input: bytes | None = None,
) -> dict:
    """Read the parent's copied output and report every imaginary mode at the frozen geometry."""
    if (
        submission.name != "submission"
        or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,79}", stem)
        or not re.fullmatch(r"[0-9]+", job_id)
    ):
        raise ValueError("Expected the named geometry and receipt from the parent submission.")
    log_path = submission / (stem + ".log")
    try:
        contents, log_meta = read_snapshot(log_path, 1024**2)
        log = contents.decode("utf-8").replace("\r\n", "\n")
        start, checkpoint_copy = completed_copyback(log, log_path, stem, job_id)
        data, meta = read_snapshot(submission / (stem + ".out"), 64 * 1024**2)
        if not start <= meta.st_mtime <= log_meta.st_mtime:
            raise ValueError("Cannot attribute the copied output's modification time to this job.")
        if submitted_input is None:
            submitted_input, _ = read_snapshot(submission / (stem + ".inp"), 256_000)
        evidence = preoptimization_modes(
            data.decode("utf-8"), submitted_input.decode("utf-8"), stem, pair
        )
    except (IndexError, UnicodeError, OSError) as exc:
        raise ValueError("Parent result files are missing, incomplete or unreadable.") from exc

    return {
        "freeze_distance": list(pair),
        "output_sha256": hashlib.sha256(data).hexdigest(),
        **evidence,
        "checkpoint_copy_announced": checkpoint_copy,
        "start_timestamp": start,
        "log_mtime": log_meta.st_mtime,
    }
