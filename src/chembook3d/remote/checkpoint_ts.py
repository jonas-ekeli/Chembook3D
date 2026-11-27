"""Input and artifact checks for the loopback pilot's checkpoint-based TS job."""

import gzip
import io
import math
import os
import re
import stat
import zlib
from pathlib import Path
from typing import BinaryIO

from chembook3d import xyz
from chembook3d.parsers.gaussian import split_route_words
from chembook3d.parsers.xtb import BOHR_ANGSTROM
from chembook3d.services import geometry

CHECKPOINT_LIMIT = 64 * 1024 * 1024
UNCOMPRESSED_LIMIT = 256 * 1024 * 1024
GEOMETRY_TOLERANCE = 1e-4  # Å; the frequency output prints rounded Cartesian coordinates.
LINK = re.compile(r"^[ \t]*--link1--[ \t]*$", re.I | re.M)


def read_snapshot(path: Path, limit: int) -> tuple[bytes, os.stat_result]:
    """Read a bounded, stable regular file without following links into other attempts."""
    message = f"Missing, linked, changed or oversized file: {path.name}"
    try:
        before = path.lstat()
        if (
            path.resolve(strict=True) != path.absolute()
            or not stat.S_ISREG(before.st_mode)
            or before.st_nlink != 1
            or not 0 < before.st_size <= limit
        ):
            raise ValueError(message)
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
        with os.fdopen(os.open(path, flags | getattr(os, "O_BINARY", 0)), "rb") as stream:
            opened = os.fstat(stream.fileno())
            if not os.path.samestat(before, opened) or not stat.S_ISREG(opened.st_mode):
                raise ValueError(message)
            data = stream.read(before.st_size + 1)
            after = os.fstat(stream.fileno())
        current = path.lstat()
        if (
            path.resolve(strict=True) != path.absolute()
            or not os.path.samestat(after, current)
            or any(
                (s.st_size, s.st_mtime_ns, s.st_ctime_ns, s.st_nlink)
                != (len(data), before.st_mtime_ns, before.st_ctime_ns, 1)
                for s in (before, opened, after, current)
            )
        ):
            raise ValueError(message)
        return data, after
    except (OSError, RuntimeError) as exc:
        raise ValueError(message) from exc


def _sections(step: str) -> list[str]:
    return re.split(r"\n(?:[ \t]*\n)+", step.strip())


def parent_geometry(text: str, stem: str) -> tuple[list[xyz.Atom], list[int], list[str], set[str]]:
    """Extract the one supported freeze from the exact previously approved submission."""
    steps = LINK.split(text.replace("\r\n", "\n"))
    if len(steps) != 5:
        raise ValueError("Choose a submitted five-step pre-optimization with a frozen distance.")
    optimization = _sections(steps[3])
    if len(optimization) != 4 or not re.search(r"\bModRedundant\b", optimization[0], re.I):
        raise ValueError("The parent optimization must have one frozen distance.")
    freeze = re.fullmatch(r"B ([1-9][0-9]*) ([1-9][0-9]*) F", optimization[3])
    if freeze is None:
        raise ValueError("The parent optimization must have one frozen distance.")
    pair = [int(i) for i in freeze.groups()]
    molecule = _sections(steps[0])[2].splitlines()
    atoms = xyz.parse_xyz("\n".join(molecule[1:]))
    if pair[0] == pair[1] or max(pair) > len(atoms):
        raise ValueError("The frozen distance must reference two distinct parent atoms.")
    header = _sections(steps[4])[0].splitlines()
    if not any(line.strip().lower() == f"%chk={stem}".lower() for line in header):
        raise ValueError("The parent frequency must write the expected local checkpoint basename.")
    offset = next(i for i, line in enumerate(header) if line.lstrip().startswith("#"))
    words = [w.upper() for w in split_route_words(" ".join(header[offset:]))]
    settings = set(words)
    required = {"FREQ", "GEOM=CHECK", "GUESS=READ"}
    if len(words) != len(settings) or not required.issubset(settings):
        raise ValueError("Expected an unambiguous parent checkpoint frequency route.")
    settings -= required
    methods = [w for w in settings if re.fullmatch(r"[A-Z][A-Z0-9-]*/CHKBASIS", w)]
    if len(methods) != 1 or any(not _project_option(w) for w in settings.difference(methods)):
        raise ValueError("Unsupported method or additional setting in the parent frequency route.")
    return atoms, pair, molecule[0].split(), settings


def _project_option(word: str) -> bool:
    """Allow only the forms of numerical/grid/dispersion settings used by this pilot."""
    if word in {"NOSYMM", "INT=ULTRAFINE", "CPHF=(GRID=FINEGRID)"}:
        return True
    if re.fullmatch(r"EMPIRICALDISPERSION=GD3(?:BJ)?", word):
        return True
    if re.fullmatch(r"IOP\(3/\d+=\d+(?:,3/\d+=\d+)*\)", word):
        return True
    if word.startswith("SCF=(") and word.endswith(")"):
        return all(
            re.fullmatch(r"NOSYM|NOVARACC|(?:VSHIFT|CONVER|MAXCYCLE)=\d+", item)
            for item in word[5:-1].split(",")
        )
    return False


def _fchk_fields(stream: BinaryIO, atoms: int) -> dict[bytes, list[bytes]]:
    """Scan fixed-width FChk records, retaining only electronic state and Cartesian geometry."""
    # None denotes a scalar. Array dimensions come from the verified parent, never the file.
    expected = {
        b"Number of atoms": (b"I", None),
        b"Charge": (b"I", None),
        b"Multiplicity": (b"I", None),
        b"Atomic numbers": (b"I", atoms),
        b"Current cartesian coordinates": (b"R", 3 * atoms),
    }
    values = {}
    label, kind, remaining, size = b"", b"", 0, 0
    for index, raw in enumerate(iter(lambda: stream.readline(4097), b"")):
        size += len(raw)
        if size > UNCOMPRESSED_LIMIT:
            raise ValueError("The expanded checkpoint exceeds the local pilot limit.")
        if len(raw) > 4096:
            raise ValueError("Invalid formatted checkpoint record length.")
        if index < 2:  # Free title and calculation/method header.
            continue
        line = raw.rstrip(b"\r\n")
        if remaining:
            if kind in (b"I", b"R"):
                if line.translate(None, b" \t0123456789-+.EeDd"):
                    raise ValueError("Malformed or truncated formatted checkpoint numeric array.")
                tokens = line.split()
                count = len(tokens)
                if label in expected:
                    values[label].extend(tokens)
            else:
                # Gaussian character, Hollerith and logical arrays are fixed-width fields.
                width = {b"C": 12, b"H": 8, b"L": 1}[kind]
                count = (len(line) + width - 1) // width
            if not 0 < count <= remaining:
                raise ValueError("Malformed or truncated formatted checkpoint array.")
            remaining -= count
            continue
        header = re.fullmatch(rb"(.{40}) +([IRCHL]) +(?:(N=) *)?(.+)", line)
        if header is None:
            raise ValueError("Malformed formatted checkpoint field header.")
        label, kind, array, value = header.groups()
        label = label.rstrip()
        count = None
        if array:
            if not re.fullmatch(rb"\s*\d+\s*", value):
                raise ValueError("Invalid formatted checkpoint array size.")
            count = remaining = int(value)
        if label in expected:
            if label in values or (kind, count) != expected[label]:
                raise ValueError("Duplicate or incorrectly shaped formatted checkpoint field.")
            values[label] = [] if array else [value.strip()]
    if remaining or values.keys() != expected.keys():
        raise ValueError("Incomplete formatted checkpoint geometry or electronic state.")
    return values


def _fchk_geometry(stream: BinaryIO, reference: geometry.Rows, charge_spin: list[str]) -> None:
    atoms = len(reference)
    values = _fchk_fields(stream, atoms)
    for key, number in zip(
        (b"Number of atoms", b"Charge", b"Multiplicity"),
        (atoms, *map(int, charge_spin)),
        strict=True,
    ):
        if int(values[key][0]) != number:
            raise ValueError(
                "Checkpoint atom count, charge or multiplicity differs from the parent."
            )
    numbers = [int(token) for token in values[b"Atomic numbers"]]
    if numbers != [xyz.ELEMENTS.index(row[0]) + 1 for row in reference]:
        raise ValueError("Checkpoint atom order differs from the parent frequency geometry.")
    coords = [
        float(token.replace(b"D", b"E")) * BOHR_ANGSTROM
        for token in values[b"Current cartesian coordinates"]
    ]
    if not all(map(math.isfinite, coords)):
        raise ValueError("Invalid checkpoint Cartesian coordinates.")
    origin = coords[:3]
    moving = []
    for i, row in enumerate(reference):
        point = coords[3 * i : 3 * i + 3]
        radius_error = abs(math.dist(point, origin) - math.dist(row[1:], reference[0][1:]))
        if radius_error > 2 * GEOMETRY_TOLERANCE:
            raise ValueError("Checkpoint coordinates differ from the parent frequency geometry.")
        moving.append([row[0], *(v - centre for v, centre in zip(point, origin, strict=True))])
    # FChk and the frequency table can use different axes; retain atom order and handedness.
    placed = geometry.place(reference, moving)
    if any(
        math.dist(row[1:], ref[1:]) > GEOMETRY_TOLERANCE
        for row, ref in zip(placed.rows, reference, strict=True)
    ):
        raise ValueError("Checkpoint coordinates differ from the parent frequency geometry.")


def checkpoint_bytes(path: Path, inspection: dict, charge_spin: list[str]) -> bytes:
    if not inspection["checkpoint_copy_announced"]:
        raise ValueError("The parent wrapper did not confirm checkpoint copy-back.")
    data, attrs = read_snapshot(path, CHECKPOINT_LIMIT)
    if not inspection["start_timestamp"] <= attrs.st_mtime <= inspection["log_mtime"]:
        raise ValueError("The checkpoint timestamp does not match the completed parent run.")
    validate_checkpoint(data, inspection["frequency_geometry"], charge_spin)
    return data


def validate_checkpoint(data: bytes, reference: geometry.Rows, charge_spin: list[str]) -> None:
    try:
        with gzip.GzipFile(fileobj=io.BytesIO(data)) as stream:
            _fchk_geometry(stream, reference, charge_spin)
    except (OSError, EOFError, zlib.error) as exc:
        raise ValueError("The formatted checkpoint is not a complete gzip file.") from exc


def verify_ts_input(text: str, stem: str, charge_spin: list[str], settings: set[str]) -> None:
    """Allow the two local operator routes, reading only the receiving checkpoint."""
    steps = LINK.split(text.replace("\r\n", "\n"))
    if len(steps) != 2:
        raise ValueError("Expected a TS optimization followed by one linked frequency.")
    for index, step in enumerate(steps):
        sections = _sections(step)
        if (
            len(sections) != 3
            or len(sections[1].splitlines()) != 1
            or sections[2].split() != charge_spin
            or re.search(r"^[ \t]*(?:@|%|--link1--)", "\n".join(sections[1:]), re.I | re.M)
        ):
            raise ValueError("Each TS step needs a title and the parent's charge and multiplicity.")
        header = sections[0].splitlines()
        offset = next((i for i, line in enumerate(header) if line.lstrip().startswith("#")), None)
        if offset is None:
            raise ValueError("Expected a Gaussian TS route.")
        checkpoint = False
        for directive in header[:offset]:
            key, _, value = directive.strip().partition("=")
            if key.lower() == "%chk" and value == stem and not checkpoint:
                checkpoint = True
            elif not re.fullmatch(
                r"%(?:nproc(?:shared)?=[1-9]\d*|mem=[1-9]\d*(?:[kmgt][bw])?)",
                directive.strip(),
                re.I,
            ):
                raise ValueError("Unsupported TS directive or checkpoint basename.")
        if not checkpoint:
            raise ValueError("Both TS steps must use the receiving checkpoint basename.")
        words = [word.upper() for word in split_route_words(" ".join(header[offset:]))]
        required = settings | {"GEOM=CHECK", "GUESS=READ"}
        if index == 0:
            opt = [w for w in words if w.startswith("OPT=(") and w.endswith(")")]
            options = opt[0][5:-1].split(",") if len(opt) == 1 else []
            allowed = {"RECALCFC=30", "MAXCYCLES=150", "TS", "NOLINEAR", "NOEIGENTEST", "TIGHT"}
            if len(options) != len(allowed) or set(options) != allowed:
                raise ValueError("Expected the operator's unconstrained TS optimizer settings.")
            required.add(opt[0])
            if "IOP(1/8=2)" in words:
                required.add("IOP(1/8=2)")
        else:
            required.add("FREQ")
        if len(words) != len(required) or set(words) != required:
            raise ValueError("Expected the parent method, ChkBasis, Geom=Check and Guess=Read.")
