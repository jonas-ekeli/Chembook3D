"""D266: restricted Gaussian preparation and submission helper for the local pilot."""

import hashlib
import hmac
import json
import math
import re
import sqlite3
import subprocess
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path

from chembook3d import xyz
from chembook3d.parsers.gaussian import keyword_of, split_route_words
from chembook3d.remote.saga_prepare import scheduler_outcome

SUBSYSTEM = "chembook3d-jobs-v1"
LOCAL_WORK = "/cluster/work/gaussian-pilot"
MAX_INPUT = 256_000
ATTEMPT = re.compile(r"[a-f0-9]{32}")
SAFE_STEM = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,79}")


def signature(key: bytes, payload: dict) -> str:
    data = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hmac.digest(key, data.encode(), "sha256").hex()


def _file(root: Path, name: str, suffix: str) -> Path:
    if (
        not isinstance(name, str)
        or not name.endswith(suffix)
        or not SAFE_STEM.fullmatch(name.removesuffix(suffix))
    ):
        raise ValueError(f"Choose a named {suffix} file in the approved directory.")
    file = root.resolve() / name
    if file.resolve() != file or not file.is_file():
        raise ValueError(f"Missing or linked file: {name}")
    return file


def _read_input(path: Path) -> str:
    with path.open("rb") as stream:
        data = stream.read(MAX_INPUT + 1)
    if not data or len(data) > MAX_INPUT or b"\0" in data:
        raise ValueError("Input is empty, binary, or too large for the pilot.")
    return data.decode("utf-8")


def _route_and_checkpoint(header: str, stem: str) -> tuple[str, str | None]:
    lines = header.splitlines()
    route = next((i for i, line in enumerate(lines) if line.lstrip().startswith("#")), None)
    if route is None:
        raise ValueError("Expected a Gaussian route.")
    checkpoint = None
    for line in lines[:route]:
        directive, _, value = line.strip().partition("=")
        if directive.lower() == "%chk":
            if checkpoint is not None or value not in {stem, stem + ".chk"}:
                raise ValueError("The checkpoint must name this attempt's own geometry.")
            checkpoint = value
        elif directive.lower() not in {"%mem", "%nprocshared", "%nproc"}:
            raise ValueError("Unsupported Gaussian directive for a fresh local optimization.")
    keywords = " ".join(lines[route:])
    if "%" in keywords or re.search(
        r"\b(?:TS|QST[23]|IRC|Scan|ModRedundant|Restart|AllCheck|External)\b", keywords, re.I
    ):
        raise ValueError("Select a job file for a fresh unconstrained optimization.")
    return keywords, checkpoint


def _verify_coordinates(lines: list[str], geometry: list[xyz.Atom]) -> None:
    generated = xyz.parse_xyz("\n".join(lines))
    if len(generated) != len(geometry) or any(
        a.element != b.element
        or math.dist((a.x, a.y, a.z), (b.x, b.y, b.z)) > 1e-5
        or not all(math.isfinite(v) for v in (b.x, b.y, b.z))
        for a, b in zip(geometry, generated, strict=True)
    ):
        raise ValueError("Generated coordinates do not match the selected XYZ geometry.")


def _verify_input(text: str, geometry: list[xyz.Atom], stem: str) -> None:
    """Check a trusted generator's ordinary minimum or its five-step linked minimum."""
    steps = re.split(r"^[ \t]*--link1--[ \t]*$", text.replace("\r\n", "\n"), flags=re.I | re.M)
    linked = len(steps) > 1
    stages = ("SP", "SP", "STABLE", "OPT", "FREQ")
    if linked and len(steps) != len(stages):
        raise ValueError("Unsupported sequence of Gaussian Link1 steps.")
    checkpoint = None
    charge_spin = []
    for index, step in enumerate(steps):
        sections = re.split(r"\n(?:[ \t]*\n)+", step.strip())
        if len(sections) < 3:
            raise ValueError("Generated input lacks a Gaussian route, title or molecule.")
        if re.search(r"^[ \t]*(?:@|--link1--)", step, re.I | re.M) or any(
            re.search(r"^[ \t]*%", section, re.M) for section in sections[1:]
        ):
            raise ValueError("Expected a self-contained Gaussian input.")
        route, current_chk = _route_and_checkpoint(sections[0], stem)
        words = split_route_words(re.sub(r"\s*=\s*", "=", route))
        basis_from_checkpoint = any(w.upper().split("/")[-1] == "CHKBASIS" for w in words)
        keys = {}
        for word in words:
            key, options = keyword_of(word)
            if key in {"GUESS", "GEOM"} and key in keys:
                raise ValueError("Conflicting geometry or guess directives.")
            keys[key] = options
        if linked:
            if keys.keys() & set(stages) != {stages[index]}:
                raise ValueError("Expected SP, SP, stability, optimization and frequency in order.")
            if current_chk is None or (index and current_chk != checkpoint):
                raise ValueError("Every linked step must use the same attempt-local checkpoint.")
        elif "OPT" not in keys:
            raise ValueError("Expected a Gaussian optimization route.")
        molecule = sections[2].splitlines()
        if not re.fullmatch(r"\s*[+-]?\d+\s+[1-9]\d*\s*", molecule[0]):
            raise ValueError("Expected charge and multiplicity before the generated coordinates.")
        if index == 0:
            checkpoint = current_chk
            charge_spin = molecule[0].split()
            if basis_from_checkpoint or any(
                opt in {"CHECK", "TCHECK", "READ"}
                for k in ("GEOM", "GUESS")
                for opt in keys.get(k, [])
            ):
                raise ValueError("A fresh unconstrained optimization must start from XYZ.")
            _verify_coordinates(molecule[1:], geometry)
        elif (
            not basis_from_checkpoint
            or keys.get("GEOM") != ["CHECK"]
            or keys.get("GUESS") != ["READ"]
            or molecule[0].split() != charge_spin
            or len(molecule) != 1
            or len(sections) != 3
        ):
            raise ValueError(
                "Linked steps must reuse this basis, geometry, guess, charge and multiplicity."
            )


def _freeze_optimization_distance(text: str, pair: list[int]) -> str:
    """Edit the fourth step of an already verified project minimum (05 §9)."""
    links = list(re.finditer(r"^[ \t]*--link1--[ \t]*\r?\n", text, re.I | re.M))
    if len(links) != 4:
        raise ValueError("A distance freeze requires the five-step linked minimum.")
    start, end = links[2].end(), links[3].start()
    step = text[start:end]
    # Locate only the route. Titles and basis data may also contain keyword text.
    route_start = re.search(r"^[ \t]*#", step, re.M).start()
    route, rest = re.split(r"(?=\r?\n[ \t]*\r?\n)", step[route_start:], maxsplit=1)
    # Match only the supported top-level Opt, Opt=Tight and Opt=(...) forms.
    words = split_route_words(re.sub(r"\s*=\s*", "=", route))
    opt_words = [word for word in words if keyword_of(word)[0] == "OPT"]
    matches = list(
        re.finditer(
            r"(?<![^\s#])(opt)(?:[ \t]*=[ \t]*(?:\(([^()\r\n]+)\)|([a-z0-9]+)))?"
            r"(?=[\s#]|$)(?!\s*[=(])",
            route,
            re.I,
        )
    )
    if len(matches) != 1 or opt_words != [re.sub(r"\s*=\s*", "=", matches[0][0])]:
        raise ValueError("Expected one supported optimization keyword for the distance freeze.")
    # The pilot knows the optimizer settings of the project's pre-optimization.
    # Other options can change the coordinate system or require extra input sections.
    for option in keyword_of(opt_words[0])[1]:
        if not re.fullmatch(r"TIGHT|(?:RECALCFC|MAXCYCLES)=[0-9]+", option):
            raise ValueError(f"Unsupported optimization option for the distance freeze: {option}")
    match = matches[0]
    existing = match[2] or match[3]
    options = f"({existing},ModRedundant)" if existing else "ModRedundant"
    route = route[: match.start()] + f"{match[1]}={options}" + route[match.end() :]
    newline = "\r\n" if "\r\n" in step else "\n"
    constraint = f"B {pair[0]} {pair[1]} F"
    revised = step[:route_start] + route + rest.rstrip(" \t\r\n") + newline * 2
    return text[:start] + revised + constraint + newline * 2 + text[end:]


@dataclass(frozen=True)
class JobPilot:
    """Paths, tools and limits come from the operator, never from a client request.

    The helper owns its state directory and each attempt directory. Its job directory
    contains only the approved optimization .jd files. remote_work is the SFTP path
    corresponding to work (the loopback SSH fixture has a chroot).
    """

    state: Path
    work: Path
    remote_work: str
    geometries: Path
    jobs: Path
    generator: tuple[str, ...]
    submitter: tuple[str, ...]
    key: bytes
    max_cores: int
    max_hours: int

    def handle(self, envelope: dict) -> dict:
        if not isinstance(envelope, dict) or envelope.keys() != {"request", "signature"}:
            raise ValueError("Invalid pilot request authorization.")
        payload = envelope.get("request")
        mac = envelope.get("signature")
        if (
            not isinstance(payload, dict)
            or not isinstance(mac, str)
            or not re.fullmatch(r"[a-f0-9]{64}", mac)
            or not hmac.compare_digest(signature(self.key, payload), mac)
        ):
            raise ValueError("Invalid pilot request authorization.")
        attempt = payload.get("attempt")
        if not isinstance(attempt, str) or not ATTEMPT.fullmatch(attempt):
            raise ValueError("Invalid prepared attempt ID.")
        operation = payload.get("operation")
        fields = {
            "prepare": {"job_file", "geometry", "cores", "hours"},
            "submit": {"revision", "cores", "hours"},
            "status": set(),
        }
        if operation == "prepare" and "freeze_distance" in payload:
            fields["prepare"].add("freeze_distance")
        if (
            not isinstance(operation, str)
            or operation not in fields
            or payload.keys()
            != fields[operation]
            | {
                "attempt",
                "operation",
            }
        ):
            raise ValueError("Unsupported pilot operation or argument.")
        self.state.mkdir(mode=0o700, parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.state / "attempts.sqlite", timeout=10)) as db:
            db.row_factory = sqlite3.Row
            db.execute("""CREATE TABLE IF NOT EXISTS attempts (
                attempt TEXT PRIMARY KEY, job_file TEXT, geometry TEXT, cores INTEGER,
                hours INTEGER, revision TEXT, state TEXT, job_id TEXT, detail TEXT)""")
            if operation == "prepare":
                return self._prepare(db, attempt, payload)
            row = db.execute("SELECT * FROM attempts WHERE attempt=?", (attempt,)).fetchone()
            if row is None:
                raise ValueError("Unknown prepared attempt.")
            if operation == "submit":
                self._submit(db, row, payload)
            return self._status(db, attempt)

    def _status(self, db: sqlite3.Connection, attempt: str) -> dict:
        result = dict(db.execute("SELECT * FROM attempts WHERE attempt=?", (attempt,)).fetchone())
        result["directory"] = f"{self.remote_work.rstrip('/')}/{attempt}/submission"
        if result["state"] == "uncertain":
            result["detail"] = result["detail"] or (
                "Submission started. Check this attempt for a receipt before taking further action."
            )
        return result

    def _prepare(self, db: sqlite3.Connection, attempt: str, request: dict) -> dict:
        cores, hours = request["cores"], request["hours"]
        if type(cores) is not int or not 1 <= cores <= self.max_cores:
            raise ValueError("Core count exceeds the configured range.")
        if type(hours) is not int or not 1 <= hours <= self.max_hours:
            raise ValueError("Wall time exceeds the configured range.")
        jd = _file(self.jobs, request["job_file"], ".jd")
        geometry = _file(self.geometries, request["geometry"], ".xyz")
        atoms = xyz.parse_xyz(_read_input(geometry))
        if not all(math.isfinite(v) for a in atoms for v in (a.x, a.y, a.z)):
            raise ValueError("XYZ coordinates must be finite.")
        pair = request.get("freeze_distance")
        if "freeze_distance" in request and (
            not isinstance(pair, list)
            or len(pair) != 2
            or any(type(i) is not int or not 1 <= i <= len(atoms) for i in pair)
            or pair[0] == pair[1]
        ):
            raise ValueError(
                "Choose two distinct atom indices from the XYZ geometry, starting at 1."
            )
        folder = self.work / attempt
        folder.mkdir(mode=0o700, parents=False, exist_ok=False)
        (folder / geometry.name).write_text(xyz.format_xyz(atoms), encoding="ascii", newline="\n")
        try:
            result = subprocess.run(
                [*self.generator, str(jd), geometry.name, "nosub"],
                cwd=folder,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=60,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ValueError("The generator did not finish preparing this input.") from exc
        if result.returncode:
            raise ValueError("Preparation failed. Inspect the generator's .accLog in the attempt.")
        path = _file(folder, geometry.stem + ".inp", ".inp")
        contents = _read_input(path)
        _verify_input(contents, atoms, geometry.stem)
        if pair is not None:
            contents = _freeze_optimization_distance(contents, pair)
            data = contents.encode()
            if len(data) > MAX_INPUT:
                raise ValueError("Constrained input is too large for the pilot.")
            path.write_bytes(data)
        revision = hashlib.sha256(contents.encode()).hexdigest()
        with db:
            db.execute(
                "INSERT INTO attempts VALUES (?,?,?,?,?,?,'prepared',NULL,NULL)",
                (attempt, jd.name, geometry.name, cores, hours, revision),
            )
        result = {**self._status(db, attempt), "input": contents}
        if pair is not None:
            result["freeze_distance"] = pair
        return result

    def _submit(self, db: sqlite3.Connection, row: sqlite3.Row, request: dict) -> None:
        if any(request[k] != row[k] for k in ("revision", "cores", "hours")):
            raise ValueError("Authorization does not match the prepared revision and resources.")
        if row["state"] != "prepared":
            return
        attempt = row["attempt"]
        folder = self.work / attempt
        path = _file(folder, Path(row["geometry"]).stem + ".inp", ".inp")
        snapshot = _read_input(path).encode()
        if hashlib.sha256(snapshot).hexdigest() != row["revision"]:
            raise ValueError("The prepared input changed. Prepare a new attempt for review.")
        # Commit before crossing the scheduler boundary. Even process death cannot replay it.
        with db:
            claimed = db.execute(
                "UPDATE attempts SET state='uncertain' WHERE attempt=? AND state='prepared'",
                (attempt,),
            ).rowcount
        if not claimed:
            return
        # The generator's preview can be inspected throughout. Submit the checked bytes
        # from an exclusive directory with no stale output, job file or checkpoint.
        folder = folder / "submission"
        try:
            folder.mkdir(mode=0o700)
            with (folder / path.name).open("xb") as input_file:
                input_file.write(snapshot)
        except OSError:
            with db:
                db.execute(
                    "UPDATE attempts SET state='rejected',detail=? WHERE attempt=?",
                    (
                        "Could not stage the approved input. The submitter was never started.",
                        attempt,
                    ),
                )
            return
        args = [
            *self.submitter,
            path.name,
            "-n",
            "1",
            "-c",
            str(row["cores"]),
            "-t",
            str(row["hours"]),
        ]
        state, job_id, detail = "uncertain", None, None
        try:
            result = subprocess.run(args, cwd=folder, capture_output=True, timeout=60, check=False)
            state, job_id, detail = scheduler_outcome(result.stdout, result.stderr)
        except (OSError, subprocess.TimeoutExpired):
            detail = "The submitter did not return a verifiable receipt."
        with db:
            db.execute(
                "UPDATE attempts SET state=?,job_id=?,detail=? WHERE attempt=?",
                (state, job_id, detail, attempt),
            )
