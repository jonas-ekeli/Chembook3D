"""Standalone Linux helper for Saga. Run setup manually with a verified .inp path."""

import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import selectors
import signal
import stat
import subprocess
import sys
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path

JOB = "min-go_PBEPBE-GD3MBJ_DZ_singlet_CONVER9.jd"
MARKER = ".prepare-minimum.json"
SUBMIT_MARKER = ".submit-minimum.json"
TS_MARKER = ".submit-ts.json"
CHECKPOINT_LIMIT = 64 * 1024 * 1024
TS_IDENTITY = ("attempt", "revision", "checkpoint_sha256", "review_sha256")
RESOURCES = {"nodes": 1, "cores": 40, "hours": 24}
WRAPPER = "tools/uibkvant-tools/runnable/submit_job_g16-C.01"
OUTPUT_LIMIT = 65_536
SUBMIT_TIMEOUT = 60
SLURM_REFUSAL = re.compile(
    rb"(?:invalid (?:account|partition|qos|allocation|time limit|node count)\b|"
    rb"job violates accounting/qos policy\b|requested node configuration is not available\b|"
    rb"access/permission denied\b)",
    re.I,
)
LIMIT = 256_000
FILE_LIMIT = 1024**2
JSON_LIMIT = 6 * FILE_LIMIT + 32_768  # One MiB of JSON-escaped text and its path/header
MAX_LISTED = 2000
XYZ_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,79}\.xyz")
PROJECTS = Path("/cluster/projects")


def digest(data):
    return hashlib.sha256(data).hexdigest()


def read_file(path, limit=LIMIT):
    if path.is_symlink():
        raise ValueError("Expected a regular file: " + path.name)
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    with os.fdopen(os.open(path, flags | getattr(os, "O_BINARY", 0)), "rb") as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ValueError("Expected a regular file: " + path.name)
        data = stream.read(limit + 1)
    if not 0 < len(data) <= limit:
        raise ValueError("Missing or oversized file: " + path.name)
    return data


def atomic_write(path, data):
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as stream:
        temporary = Path(stream.name)
        try:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        except BaseException:
            stream.close()
            temporary.unlink()
            raise
    try:
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def control_dir():
    return Path.home() / ".local/lib/chembook3d"


def check_root(root):
    if root.name != "chembook3d-pilot" or root != root.resolve(strict=True):
        raise ValueError("Select a canonical chembook3d-pilot directory.")
    if (
        len(root.parents) < 3
        or root.parents[2] != PROJECTS
        or not all(re.fullmatch(r"[a-zA-Z0-9_-]+", part) for part in root.parts[-3:])
    ):
        raise ValueError("The reference must be in a test directory under the project pilot.")


def inputs(root, reference_dir, stem):
    check_root(root)
    if not re.fullmatch(r"min-test-[a-zA-Z0-9_-]+", reference_dir) or not re.fullmatch(
        r"[a-zA-Z0-9][a-zA-Z0-9_-]{0,79}", stem
    ):
        raise ValueError("Expected the named geometry in the manual min-test directory.")
    baseline = root / reference_dir
    descriptions = root.parent / "jobdescriptions"
    if baseline.is_symlink() or descriptions.is_symlink():
        raise ValueError("The reference and job directories must not be links.")
    generator = Path.home() / "tools/scripts/submit_all_DFT.sh"
    if root in generator.resolve(strict=True).parents:
        raise ValueError("The generator must be installed outside the pilot.")
    return baseline / (stem + ".xyz"), descriptions / JOB, generator


def install(reference):
    if reference.suffix != ".inp" or reference.is_symlink() or reference.parent.is_symlink():
        raise ValueError("Choose the .inp from the successful manual minimum test.")
    reference = reference.resolve(strict=True)
    root = reference.parent.parent
    geometry, job, generator = inputs(root, reference.parent.name, reference.stem)
    policy = {
        "work_root": str(root),
        "reference_dir": reference.parent.name,
        "stem": reference.stem,
        "xyz_sha256": digest(read_file(geometry)),
        "job_sha256": digest(read_file(job)),
        "generator_sha256": digest(read_file(generator)),
        "reference_sha256": digest(read_file(reference)),
        "attempt": secrets.token_hex(16),
    }
    inbox = root / "geometries"
    if inbox.is_symlink():
        raise ValueError("The geometries directory must be inside the pilot.")
    inbox.mkdir(mode=0o700, exist_ok=True)
    private = control_dir()
    private.mkdir(parents=True, mode=0o700, exist_ok=True)
    if (
        private.resolve() != private
        or private.stat().st_uid != os.getuid()
        or private.stat().st_mode & 0o022
    ):
        raise ValueError("Choose a user-owned, non-writable-by-others helper directory.")
    atomic_write(private / "saga_prepare.py", read_file(Path(__file__)))
    atomic_write(private / MARKER, json.dumps(policy).encode())
    return policy["reference_sha256"]


def prepare(request, policy):
    if request != {"operation": "prepare_minimum"}:
        raise ValueError("Only the enrolled minimum preparation is available.")
    root = Path(policy["work_root"])
    stem = policy["stem"]
    geometry, job, generator = inputs(root, policy["reference_dir"], stem)
    xyz = read_file(geometry)
    if digest(xyz) != policy["xyz_sha256"] or digest(read_file(job)) != policy["job_sha256"]:
        raise ValueError("The enrolled geometry or job description has changed.")
    if digest(read_file(generator)) != policy["generator_sha256"]:
        raise ValueError("The enrolled generator has changed.")
    if not re.fullmatch(r"[a-f0-9]{32}", policy["attempt"]):
        raise ValueError("Invalid enrollment.")
    folder = root / ("prepare-" + policy["attempt"])
    claim = control_dir() / (policy["attempt"] + ".claim")
    receipt = control_dir() / (policy["attempt"] + ".done")
    expected = policy["reference_sha256"]
    path = folder / (stem + ".inp")
    try:
        claim.touch(mode=0o600, exist_ok=False)
    except FileExistsError:
        if (
            folder.is_symlink()
            or not folder.is_dir()
            or not receipt.is_file()
            or read_file(receipt) != expected.encode()
        ):
            raise ValueError(
                "Preparation is in progress or incomplete. Inspect the attempt at " + str(folder)
            ) from None
    else:
        if folder.exists() or folder.is_symlink() or receipt.exists():
            raise ValueError("Existing or incomplete attempt. Inspect " + str(folder))
        folder.mkdir(mode=0o700)
        with (folder / geometry.name).open("xb") as stream:
            stream.write(xyz)
        result = subprocess.run(
            [str(generator), str(job), geometry.name, "nosub"],
            cwd=folder,
            timeout=90,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        if result.returncode:
            raise ValueError(
                "The preparation wrapper failed. Inspect the .accLog in " + str(folder)
            )
    data = read_file(path)
    if digest(data) != expected:
        raise ValueError(
            "Generated input differs from the enrolled manual reference. Inspect " + str(folder)
        )
    contents = data.decode("utf-8")
    if not receipt.exists():
        atomic_write(receipt, expected.encode())
    return {
        "state": "prepared",
        "directory": str(folder),
        "input_name": path.name,
        "sha256": expected,
        "reference_sha256": expected,
        "input": contents,
    }


def checked_control():
    directory = control_dir()
    attrs = directory.stat()
    if (
        directory.resolve(strict=True) != directory
        or not stat.S_ISDIR(attrs.st_mode)
        or attrs.st_uid != os.getuid()
        or attrs.st_mode & 0o022
    ):
        raise ValueError("Submission requires the operator's protected helper directory.")
    return directory


def private_read(path, limit=4096):
    attrs = path.lstat()
    if not stat.S_ISREG(attrs.st_mode) or attrs.st_uid != os.getuid() or attrs.st_mode & 0o077:
        raise ValueError("Expected a private operator-owned control file.")
    return read_file(path, limit)


def submission_wrapper(root):
    home = Path.home()
    try:
        path = (home / WRAPPER).resolve(strict=True)
    except RuntimeError as exc:  # Symlink loop on Python 3.8
        raise ValueError("Cannot resolve the configured submission wrapper.") from exc
    attrs = path.stat()
    # The observed Saga installation inherits group write inside a 2700 home.
    # Apply the exception only below the canonical, operator-only home directory.
    home_attrs = home.lstat()
    private_home = (
        home in path.parents
        and home == home.resolve(strict=True)
        and home_attrs.st_uid == os.getuid()
        and not home_attrs.st_mode & 0o077
    )
    forbidden_write = 0o002 if private_home else 0o022
    if (
        root in path.parents
        or not stat.S_ISREG(attrs.st_mode)
        or attrs.st_uid not in {0, os.getuid()}
        or attrs.st_mode & forbidden_write
        or (attrs.st_mode & 0o020 and attrs.st_nlink != 1)
        or not os.access(path, os.X_OK)
    ):
        raise ValueError("Expected a trusted executable outside the pilot.")
    for parent in path.parents:
        attrs = parent.stat()
        sticky_root = attrs.st_uid == 0 and attrs.st_mode & stat.S_ISVTX
        if parent == home:
            forbidden_write = 0o022
        if attrs.st_uid not in {0, os.getuid()} or (
            attrs.st_mode & forbidden_write and not sticky_root
        ):
            raise ValueError("The submission wrapper must be in a trusted installation.")
    return {"wrapper": str(path), "wrapper_sha256": digest(read_file(path, FILE_LIMIT))}


def enrolled_submission(policy):
    if not (
        re.fullmatch(r"[a-f0-9]{32}", policy["attempt"])
        and re.fullmatch(r"[a-f0-9]{64}", policy["reference_sha256"])
        and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,79}", policy["stem"])
    ):
        raise ValueError("Invalid preparation enrollment.")
    return {"attempt": policy["attempt"], "revision": policy["reference_sha256"], **RESOURCES}


def submit_policy(path):
    try:
        policy = json.loads(private_read(path))
    except FileNotFoundError:
        raise ValueError("The operator has not enabled minimum submission.") from None
    if not isinstance(policy, dict) or not re.fullmatch(r"[a-f0-9]{64}", policy.get("key", "")):
        raise ValueError("The enrolled submission policy is invalid.")
    return policy


@contextmanager
def policy_lock(control, lock_name):
    # A permanent lock file serializes concurrent enables across login nodes. Exclusive
    # flock on NFS needs a descriptor opened for writing.
    import fcntl

    lock = control / lock_name
    flags = os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK
    with os.fdopen(os.open(lock, flags, 0o600), "wb") as guard:
        attrs = os.fstat(guard.fileno())
        if not stat.S_ISREG(attrs.st_mode) or attrs.st_uid != os.getuid() or attrs.st_mode & 0o077:
            raise ValueError("Expected a private submission policy lock.")
        fcntl.flock(guard, fcntl.LOCK_EX)
        yield


def enable_submit():
    control = checked_control()
    with policy_lock(control, ".submit-minimum.lock"):
        policy = json.loads(private_read(control / MARKER))
        expected = enrolled_submission(policy)
        root = Path(policy["work_root"])
        check_root(root)
        expected.update(submission_wrapper(root))
        marker = control / SUBMIT_MARKER
        try:
            current = json.loads(private_read(marker))
        except FileNotFoundError:
            current = {}
        if not isinstance(current, dict):
            raise ValueError("The enrolled submission policy is invalid.")
        if current.get("attempt") == expected["attempt"]:
            if any(current.get(k) != v for k, v in expected.items()) or not re.fullmatch(
                r"[a-f0-9]{64}", current.get("key", "")
            ):
                raise ValueError("The enrolled submission wrapper or policy has changed.")
        else:
            atomic_write(marker, json.dumps({**expected, "key": secrets.token_hex(32)}).encode())
            with open_directory(control, readable=True) as directory:
                os.fsync(directory)
    return expected["attempt"]


def scheduler_output(args, cwd):
    """Keep both streams bounded, including when a descendant inherits a pipe."""
    deadline = time.monotonic() + SUBMIT_TIMEOUT
    with subprocess.Popen(
        args,
        cwd=cwd,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    ) as process:
        try:
            outputs = [bytearray(), bytearray()]
            with selectors.DefaultSelector() as selector:
                for i, pipe in enumerate((process.stdout, process.stderr)):
                    selector.register(pipe, selectors.EVENT_READ, outputs[i])
                size = 0
                while selector.get_map():
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise ValueError("Submission wrapper timed out.")
                    for event, _ in selector.select(remaining):
                        block = os.read(event.fd, 16_384)
                        size += len(block)
                        if size > OUTPUT_LIMIT:
                            raise ValueError("Submission wrapper output exceeded its limit.")
                        if block:
                            event.data.extend(block)
                        else:
                            selector.unregister(event.fileobj)
            process.wait(timeout=max(0, deadline - time.monotonic()))
            return tuple(bytes(output) for output in outputs)
        except BaseException:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            raise


def scheduler_outcome(stdout, stderr):
    # Only the site's exact Slurm receipt on stdout identifies an accepted job. The
    # wrapper may exit zero on sbatch failure, so process exit status is insufficient.
    lines = stdout.splitlines()
    receipts = [line for line in lines if line.startswith(b"Submitted batch job")]
    prefix = b"sbatch: error: Batch job submission failed: "
    failures = [
        line[len(prefix) :] for line in lines + stderr.splitlines() if line.startswith(prefix)
    ]
    stderr_receipt = any(line.startswith(b"Submitted batch job") for line in stderr.splitlines())
    if len(receipts) == 1 and not failures and not stderr_receipt:
        match = re.fullmatch(rb"Submitted batch job ([1-9][0-9]{0,19})", receipts[0])
        if match:
            return "submitted", match[1].decode("ascii"), None
    # sbatch also uses this prefix for transport failures after sending a request.
    # Classify only recognized scheduler refusals as definitive.
    if (
        not receipts
        and not stderr_receipt
        and failures
        and all(SLURM_REFUSAL.match(message) for message in failures)
    ):
        return "rejected", None, "Slurm reported that batch job submission failed."
    return "uncertain", None, "The wrapper did not return one unambiguous Slurm receipt."


def prepared_snapshot(folder, name, control, record):
    if private_read(control / (record["attempt"] + ".done")) != record["revision"].encode():
        raise ValueError("Missing a matching preparation receipt.")
    with open_directory(folder) as directory:
        data = read_file(Path("/proc/self/fd") / str(directory) / name)
    if digest(data) != record["revision"]:
        raise ValueError("The prepared input changed. Review a new preparation.")
    return data


def stage_and_submit(folder, name, data, wrapper, checkpoint=None):
    # Hold the directories by descriptor across staging and launch. A pre-existing
    # directory (including a link or matching checkpoint) consumes this claim safely.
    try:
        with open_directory(folder, readable=True) as parent:
            attrs = os.fstat(parent)
            if attrs.st_uid != os.getuid() or attrs.st_mode & 0o022:
                return "rejected", None, "The preparation directory must be operator-controlled."
            os.mkdir("submission", mode=0o700, dir_fd=parent)
            fd = os.open("submission", os.O_RDONLY | os.O_NOFOLLOW | os.O_DIRECTORY, dir_fd=parent)
            try:
                cwd = Path("/proc/self/fd") / str(fd)
                files = [(name, data)]
                if checkpoint is not None:
                    files.append(checkpoint)
                for filename, contents in files:
                    with (cwd / filename).open("xb") as stream:
                        stream.write(contents)
                        stream.flush()
                        os.fsync(stream.fileno())
                os.fsync(fd)
                os.fsync(parent)
                args = [
                    wrapper,
                    name,
                    "-n",
                    str(RESOURCES["nodes"]),
                    "-c",
                    str(RESOURCES["cores"]),
                    "-t",
                    str(RESOURCES["hours"]),
                ]
                try:
                    return scheduler_outcome(*scheduler_output(args, cwd))
                except (OSError, ValueError, subprocess.TimeoutExpired):
                    return "uncertain", None, "The wrapper did not return a verifiable receipt."
            finally:
                os.close(fd)
    except OSError:
        return "rejected", None, "Could not stage the approved input in a fresh directory."


def authorize_minimum(request, expected, enabled):
    if any(type(enabled.get(k)) is not type(v) or enabled[k] != v for k, v in expected.items()):
        raise ValueError("Submission is not enabled for this preparation.")
    keys = {"operation", "attempt", "revision"}
    submit = request["operation"] == "submit_minimum"
    if not submit and request["operation"] != "minimum_status":
        raise ValueError("Unsupported minimum operation.")
    if submit:
        keys = keys.union(RESOURCES, {"signature"})
    if set(request) != keys or any(
        type(request[k]) is not type(expected[k]) or request[k] != expected[k]
        for k in keys - {"operation", "signature"}
    ):
        raise ValueError("Request must match the enrolled attempt, input revision and resources.")
    if submit:
        verify_signature(request, enabled["key"])
    return submit


def verify_signature(request, key):
    signed = {k: v for k, v in request.items() if k != "signature"}
    payload = json.dumps(signed, sort_keys=True, separators=(",", ":")).encode()
    mac = hmac.digest(bytes.fromhex(key), payload, "sha256").hex()
    supplied = request["signature"]
    if (
        not isinstance(supplied, str)
        or not re.fullmatch(r"[a-f0-9]{64}", supplied)
        or not hmac.compare_digest(mac, supplied)
    ):
        raise ValueError("Invalid submission authorization.")


def saved_outcome(receipt, claim):
    uncertain = {
        "state": "uncertain",
        "job_id": None,
        "detail": "Submission was claimed. Reconcile the attempt before taking further action.",
    }
    try:
        stored = json.loads(private_read(receipt))
    except FileNotFoundError:
        return uncertain if os.path.lexists(claim) else None
    except (OSError, ValueError):
        return uncertain
    if not isinstance(stored, dict) or stored.keys() != {"state", "job_id", "detail"}:
        return uncertain
    if stored["detail"] is not None and not isinstance(stored["detail"], str):
        return uncertain
    if stored["state"] == "submitted":
        if not isinstance(stored["job_id"], str) or not re.fullmatch(
            r"[1-9][0-9]{0,19}", stored["job_id"]
        ):
            return uncertain
    elif stored["state"] not in ("rejected", "uncertain") or stored["job_id"] is not None:
        return uncertain
    return stored


def minimum_operation(request, policy):
    control = checked_control()
    if json.loads(private_read(control / MARKER)) != policy:
        raise ValueError("The preparation enrollment changed.")
    expected = enrolled_submission(policy)
    enabled = submit_policy(control / SUBMIT_MARKER)
    submit = authorize_minimum(request, expected, enabled)

    attempt = expected["attempt"]
    root = Path(policy["work_root"])
    folder = root / ("prepare-" + attempt)
    record = {**expected, "directory": str(folder / "submission"), "job_id": None, "detail": None}
    receipt = control / (attempt + ".submit.done")
    claim = control / (attempt + ".submit.claim")
    stored = saved_outcome(receipt, claim)
    if stored is not None:
        return {**record, **stored}
    check_root(root)
    name = policy["stem"] + ".inp"
    data = prepared_snapshot(folder, name, control, record)
    if not submit:
        return {**record, "state": "prepared"}
    pin = submission_wrapper(root)
    if any(enabled.get(k) != v for k, v in pin.items()):
        raise ValueError("The enrolled submission wrapper has changed.")
    return claim_and_submit(record, receipt, claim, folder, name, data, pin["wrapper"])


def claim_and_submit(record, receipt, claim, folder, name, data, wrapper, checkpoint=None):
    with open_directory(claim.parent, readable=True) as directory:
        try:
            fd = os.open(claim.name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600, dir_fd=directory)
        except FileExistsError:
            return {**record, **saved_outcome(receipt, claim)}
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
        os.fsync(directory)
        state, job_id, detail = stage_and_submit(folder, name, data, wrapper, checkpoint)
        outcome = {"state": state, "job_id": job_id, "detail": detail}
        atomic_write(receipt, json.dumps(outcome).encode())
        os.fsync(directory)
    return {**record, **outcome}


def review_bytes(review):
    return (
        json.dumps(review, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n"
    ).encode()


def validate_ts_review(review):
    fields = {
        "format",
        "parent_job_id",
        "parent_stem",
        "input_name",
        "checkpoint_name",
        "input_sha256",
        "checkpoint_sha256",
        "output_sha256",
        "freeze_distance",
        "mode_index",
        "frequency_cm1",
        "bond_projection",
        "alignment",
    }
    stem = r"[A-Za-z0-9][A-Za-z0-9_-]{0,79}"
    if not isinstance(review, dict) or set(review) != fields:
        raise ValueError("Expected the complete archived TS review.")
    strings = {
        "parent_job_id": r"[1-9][0-9]{0,19}",
        "parent_stem": stem,
        "input_name": stem + r"\.inp",
        "input_sha256": r"[0-9a-f]{64}",
        "output_sha256": r"[0-9a-f]{64}",
        "checkpoint_sha256": r"[0-9a-f]{64}",
    }
    if (
        review["format"] != "chembook3d-saga-ts-v1"
        or any(
            not isinstance(review[k], str) or not re.fullmatch(v, review[k])
            for k, v in strings.items()
        )
        or review["checkpoint_name"] != review["input_name"][:-4] + ".fchk.gz"
    ):
        raise ValueError("Invalid archived TS review identity or checkpoint basename.")
    pair = review["freeze_distance"]
    if (
        not isinstance(pair, list)
        or len(pair) != 2
        or any(type(atom) is not int or atom < 1 for atom in pair)
        or pair[0] == pair[1]
        or type(review["mode_index"]) is not int
        or review["mode_index"] < 1
        or any(
            type(review[k]) not in (int, float)
            or not -sys.float_info.max <= review[k] <= sys.float_info.max
            for k in ("frequency_cm1", "bond_projection", "alignment")
        )
        or review["frequency_cm1"] >= 0
        or not 0 <= review["alignment"] <= 1
    ):
        raise ValueError("Invalid frozen atom pair or imaginary-mode evidence.")


def ts_checkpoint_directives(data, input_name):
    # Scientific route/geometry/mode checks run on the workstation. Here enforce
    # the receiving basename before allowing a site wrapper to use the archive.
    text = data.decode("utf-8").replace("\r\n", "\n")
    steps = re.split(r"^[ \t]*--link1--[ \t]*$", text, flags=re.I | re.M)
    if len(steps) != 2:
        raise ValueError("Expected two linked TS input steps.")
    for step in steps:
        directives = [line.strip() for line in step.splitlines() if line.lstrip().startswith("%")]
        checks = []
        for directive in directives:
            key, _, value = directive.partition("=")
            if key.lower() == "%chk":
                checks.append(value)
            elif not re.fullmatch(
                r"%(?:nproc(?:shared)?=[1-9]\d*|mem=[1-9]\d*(?:[kmgt][bw])?)", directive, re.I
            ):
                raise ValueError("Unsupported TS input directive.")
        if checks != [input_name[:-4]]:
            raise ValueError("Both TS steps must use the receiving checkpoint basename.")


def ts_bundle(directory, private=False):
    with open_directory(directory) as fd:
        path = Path("/proc/self/fd") / str(fd)
        read = private_read if private else read_file
        manifest = read(path / "review.json", 4096)
        review = json.loads(manifest, object_pairs_hook=distinct_fields)
        validate_ts_review(review)
        if manifest != review_bytes(review):
            raise ValueError("Expected the canonical archived TS review.")
        data = read(path / "child.inp", LIMIT)
        checkpoint = read(path / "parent.fchk.gz", CHECKPOINT_LIMIT)
    if digest(data) != review["input_sha256"] or digest(checkpoint) != review["checkpoint_sha256"]:
        raise ValueError("The archived TS input or checkpoint differs from the review.")
    ts_checkpoint_directives(data, review["input_name"])
    return review, data, checkpoint


def enroll_ts(source):
    control = checked_control()
    review, data, checkpoint = ts_bundle(source.absolute())
    with policy_lock(control, ".submit-ts.lock"):
        minimum = private_read(control / MARKER)
        policy = json.loads(minimum)
        enrolled_submission(policy)
        root = Path(policy["work_root"])
        check_root(root)
        expected = {
            "work_root": str(root),
            "minimum_attempt": policy["attempt"],
            "minimum_sha256": digest(minimum),
            "revision": review["input_sha256"],
            "checkpoint_sha256": review["checkpoint_sha256"],
            "review_sha256": digest(review_bytes(review)),
            **RESOURCES,
            **submission_wrapper(root),
        }
        marker = control / TS_MARKER
        if os.path.lexists(marker):
            current = submit_policy(marker)
            if current.get("minimum_attempt") == expected["minimum_attempt"]:
                if any(current.get(k) != v for k, v in expected.items()):
                    raise ValueError("A different TS pair or wrapper is enrolled for this pilot.")
                if not re.fullmatch(r"[0-9a-f]{32}", current["attempt"]):
                    raise ValueError("Invalid TS enrollment.")
                # Retrying enrollment cannot repair or replace an existing authorization.
                original, _, _ = ts_bundle(control / ("ts-" + current["attempt"]), private=True)
                if review != original:
                    raise ValueError("The enrolled TS review changed.")
                return current["attempt"]
        attempt = secrets.token_hex(16)
        folder = control / ("ts-" + attempt)
        folder.mkdir(mode=0o700)
        for name, contents in (
            ("child.inp", data),
            ("parent.fchk.gz", checkpoint),
            ("review.json", review_bytes(review)),
        ):
            atomic_write(folder / name, contents)
        with open_directory(folder, readable=True) as directory:
            os.fsync(directory)
        marker_bytes = json.dumps({**expected, "attempt": attempt, "key": secrets.token_hex(32)})
        atomic_write(marker, marker_bytes.encode())
        with open_directory(control, readable=True) as directory:
            os.fsync(directory)
    return attempt


def authorize_ts(request, enabled):
    identity = {k: enabled[k] for k in TS_IDENTITY}
    if (
        not re.fullmatch(r"[0-9a-f]{32}", identity["attempt"])
        or any(not re.fullmatch(r"[0-9a-f]{64}", identity[k]) for k in TS_IDENTITY[1:])
        or any(type(enabled.get(k)) is not int or enabled[k] != v for k, v in RESOURCES.items())
    ):
        raise ValueError("Invalid TS enrollment.")
    operation = request["operation"]
    if operation not in ("prepare_ts", "ts_status", "submit_ts"):
        raise ValueError("Unsupported TS operation.")
    expected = dict(identity, operation=operation)
    if operation == "submit_ts":
        expected.update(RESOURCES)
        if "signature" not in request:
            raise ValueError("Missing TS submission authorization.")
        expected["signature"] = request["signature"]
    if set(request) != set(expected) or any(
        type(request[k]) is not type(v) or request[k] != v for k, v in expected.items()
    ):
        raise ValueError("Request must match the enrolled TS review and resources.")
    if operation == "submit_ts":
        verify_signature(request, enabled["key"])
    return identity


def prepare_ts_snapshot(folder, name, data, done, identity):
    with policy_lock(done.parent, ".submit-ts.lock"):
        if not os.path.lexists(done):
            with open_directory(folder.parent, readable=True) as parent:
                os.mkdir(folder.name, 0o700, dir_fd=parent)
                fd = os.open(
                    folder.name, os.O_DIRECTORY | os.O_NOFOLLOW | os.O_RDONLY, dir_fd=parent
                )
                try:
                    atomic_write(Path("/proc/self/fd") / str(fd) / name, data)
                    os.fsync(fd)
                finally:
                    os.close(fd)
                os.fsync(parent)
            atomic_write(done, identity["revision"].encode())
            with open_directory(done.parent, readable=True) as directory:
                os.fsync(directory)


def ts_operation(request, policy):
    control = checked_control()
    if not os.path.lexists(control / TS_MARKER):
        raise ValueError("The operator has not enrolled a Saga TS replay.")
    enabled = submit_policy(control / TS_MARKER)
    minimum = private_read(control / MARKER)
    root = Path(policy["work_root"])
    if (
        json.loads(minimum) != policy
        or digest(minimum) != enabled.get("minimum_sha256")
        or enabled.get("work_root") != str(root)
    ):
        raise ValueError("The TS replay belongs to a different minimum enrollment.")
    check_root(root)
    identity = authorize_ts(request, enabled)
    attempt = identity["attempt"]
    folder = root / ("ts-" + attempt)
    record = {
        **identity,
        **RESOURCES,
        "directory": str(folder / "submission"),
        "job_id": None,
        "detail": None,
    }
    claim = control / ("ts-" + attempt + ".submit.claim")
    receipt = control / ("ts-" + attempt + ".submit.done")
    operation = request["operation"]
    stored = saved_outcome(receipt, claim)
    if stored is not None:
        if operation == "prepare_ts":
            raise ValueError(
                "Submission has already been claimed. Use get_prepared_job for " + attempt
            )
        return {**record, **stored}
    review, data, checkpoint = ts_bundle(control / ("ts-" + attempt), private=True)
    if (
        digest(review_bytes(review)) != identity["review_sha256"]
        or review["input_sha256"] != identity["revision"]
        or review["checkpoint_sha256"] != identity["checkpoint_sha256"]
    ):
        raise ValueError("The private TS bundle differs from the enrollment.")
    name = review["input_name"]
    done = control / ("ts-" + attempt + ".done")
    if operation == "prepare_ts":
        prepare_ts_snapshot(folder, name, data, done, identity)
    if private_read(done) != identity["revision"].encode():
        raise ValueError("Missing a matching TS preparation receipt.")
    with open_directory(folder) as directory:
        snapshot = read_file(Path("/proc/self/fd") / str(directory) / name)
    if digest(snapshot) != identity["revision"]:
        raise ValueError("The prepared TS input has changed.")
    if operation == "prepare_ts":
        return {
            **identity,
            "state": "prepared",
            "directory": str(folder),
            "input_name": name,
            "input": snapshot.decode("utf-8"),
            "review": review,
        }
    if operation == "ts_status":
        return {**record, "state": "prepared"}
    pin = submission_wrapper(root)
    if any(enabled.get(k) != v for k, v in pin.items()):
        raise ValueError("The enrolled submission wrapper has changed.")
    return claim_and_submit(
        record,
        receipt,
        claim,
        folder,
        name,
        snapshot,
        pin["wrapper"],
        (review["checkpoint_name"], checkpoint),
    )


class Outside(ValueError):
    pass


def inside(root, path):
    if path != root and root not in path.parents:
        raise Outside("This path is outside the enrolled pilot directory.")
    return path


def resolved_inside(root, path):
    try:
        return inside(root, path.resolve())
    except RuntimeError as exc:  # Symlink loop on Python 3.8
        raise ValueError("Cannot resolve this path.") from exc


@contextmanager
def open_directory(path, readable=False):
    """Pin each directory by descriptor. A symlink swapped into a resolved path fails closed."""
    flags = os.O_DIRECTORY | os.O_NOFOLLOW
    fd = os.open("/", flags | os.O_PATH)
    try:
        parts = path.parts[1:]
        for index, part in enumerate(parts):
            access = os.O_RDONLY if readable and index + 1 == len(parts) else os.O_PATH
            child = os.open(part, flags | access, dir_fd=fd)
            os.close(fd)
            fd = child
        yield fd
    finally:
        os.close(fd)


@contextmanager
def open_entry(path):
    # Linux O_PATH can inspect a device or FIFO without opening it for I/O.
    with open_directory(path.parent) as parent:
        fd = os.open(path.name, os.O_PATH | os.O_NOFOLLOW, dir_fd=parent)
        try:
            yield fd
        finally:
            os.close(fd)


def list_directory(root, path):
    path = resolved_inside(root, path)
    entries = []
    with open_directory(path, readable=True) as directory, os.scandir(directory) as listing:
        for item in listing:
            try:
                attrs = item.stat(follow_symlinks=False)
                if stat.S_ISLNK(attrs.st_mode):
                    target = resolved_inside(root, path / item.name)
                    with open_entry(target) as fd:
                        attrs = os.fstat(fd)
                if stat.S_ISDIR(attrs.st_mode):
                    kind = "directory"
                elif stat.S_ISREG(attrs.st_mode):
                    kind = "file"
                else:
                    continue
                entries.append(
                    {
                        "name": item.name,
                        "kind": kind,
                        "size": attrs.st_size,
                        "modified": int(attrs.st_mtime),
                    }
                )
            except Outside:
                entries.append({"name": item.name, "kind": "outside"})
            except (OSError, ValueError):
                continue  # Broken, vanished, or unreadable link
            if len(entries) >= MAX_LISTED:
                break
    return {"path": str(path), "entries": entries, "agent_read_root": str(root)}


def file_version(attrs):
    fields = (attrs.st_dev, attrs.st_ino, attrs.st_size, attrs.st_mtime_ns, attrs.st_ctime_ns)
    return digest(":".join(map(str, fields)).encode("ascii"))


def read_range(root, path, offset, length):
    if type(offset) is not int or type(length) is not int or not 0 <= length <= FILE_LIMIT:
        raise ValueError("Expected an integer offset and a length between 0 and 1 MiB.")
    path = resolved_inside(root, path)
    with open_entry(path) as fd:
        attrs = os.fstat(fd)
        if not stat.S_ISREG(attrs.st_mode):
            raise ValueError("Expected a regular file.")
        size = attrs.st_size
        version = file_version(attrs)
        start = max(size + offset, 0) if offset < 0 else min(offset, size)
        count = min(length, size - start)
        data = b""
        if count:
            # Reopen the inspected inode for reading, even if its name has since changed.
            with open("/proc/self/fd/" + str(fd), "rb") as stream:
                stream.seek(start)
                data = stream.read(count)
            if file_version(os.fstat(fd)) != version:
                raise ValueError("The file changed during the read.")
    return {
        "path": str(path),
        "size": size,
        "modified": int(attrs.st_mtime),
        "version": version,
        "offset": start,
        "data": base64.b64encode(data).decode("ascii"),
    }


def free_xyz(directory, path):
    stem = path.stem
    start = 2
    version = re.fullmatch(r"(.+)_v(\d+)", stem)
    if version:
        stem, start = version[1], int(version[2]) + 1
    for number in range(start, start + 100):
        suffix = "_v" + str(number)
        if len(suffix) >= 80:
            return None
        candidate = stem[: 80 - len(suffix)] + suffix + ".xyz"
        try:
            os.stat(candidate, dir_fd=directory, follow_symlinks=False)
        except FileNotFoundError:
            return str(path.with_name(candidate))
    return None


def create_xyz(root, path, text):
    inbox = root / "geometries"
    if inbox not in path.parents:
        raise Outside("Agent file creation on the pilot is limited to its geometries directory.")
    if path.parent != inbox or not XYZ_NAME.fullmatch(path.name):
        raise Outside("Agent writes on this deployment are limited to flat, named XYZ files.")
    if not isinstance(text, str) or "\0" in text:
        raise ValueError("Only text can be written.")
    data = text.replace("\r\n", "\n").encode("utf-8")
    if len(data) > FILE_LIMIT:
        raise ValueError("A geometry file may be at most 1 MiB.")
    with open_directory(path.parent) as directory:
        name = ".pending-" + secrets.token_hex(16)
        fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644, dir_fd=directory)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            try:
                # Publish complete data in one exclusive step; even a dangling link is taken.
                os.link(
                    name,
                    path.name,
                    src_dir_fd=directory,
                    dst_dir_fd=directory,
                    follow_symlinks=False,
                )
            except FileExistsError:
                return {"path": str(path), "written": False, "free": free_xyz(directory, path)}
        finally:
            os.unlink(name, dir_fd=directory)
    return {"path": str(path), "written": True, "size": len(data)}


def file_operation(request, policy):
    fields = {
        "list_files": {"operation", "path"},
        "read_file": {"operation", "path", "offset", "length"},
        "create_xyz": {"operation", "path", "text"},
    }
    operation = request.get("operation") if isinstance(request, dict) else None
    if not isinstance(operation, str) or operation not in fields:
        raise ValueError(
            "Only the enrolled minimum preparation and pilot file operations are available."
        )
    if set(request) != fields[operation]:
        raise ValueError("Unexpected fields for " + operation + ".")
    raw = request["path"]
    if not isinstance(raw, str) or not raw.startswith("/") or "\0" in raw:
        raise Outside("Expected an absolute path in the enrolled pilot directory.")
    if len(os.fsencode(raw)) > 4095:
        raise ValueError("Path is too long.")
    path = Path(raw)
    if ".." in path.parts or (operation == "create_xyz" and str(path) != raw):
        raise Outside("Expected a path within the enrolled pilot directory.")
    root = Path(policy["work_root"])
    check_root(root)
    if operation == "create_xyz":
        return create_xyz(root, path, request["text"])
    inside(root, path)
    if operation == "list_files":
        return list_directory(root, path)
    return read_range(root, path, request["offset"], request["length"])


def distinct_fields(pairs):
    result = dict(pairs)
    if len(pairs) != len(result):
        raise ValueError("Duplicate request field.")
    return result


def parse_request(raw):
    if len(raw) > JSON_LIMIT or (len(raw) > 256 and not raw.strip()):
        raise ValueError("Request is too large.")
    try:
        request = json.loads(raw, object_pairs_hook=distinct_fields)
    except RecursionError as exc:
        raise ValueError("Invalid request.") from exc
    if (
        isinstance(request, dict)
        and request.get("operation") == "prepare_minimum"
        and len(raw) > 256
    ):
        raise ValueError("Request is too large.")
    return request


def main():
    if len(sys.argv) == 3 and sys.argv[1] == "setup":
        print("Enrolled manual reference SHA-256:", install(Path(sys.argv[2])))
        return
    if sys.argv[1:] == ["enable-submit"]:
        print("Enabled minimum submission for preparation", enable_submit())
        return
    if len(sys.argv) == 3 and sys.argv[1] == "enroll-ts":
        print("Enrolled archived TS replay", enroll_ts(Path(sys.argv[2])))
        return
    if sys.argv[1:] == ["check-wrapper"]:
        policy = json.loads(private_read(checked_control() / MARKER))
        root = Path(policy["work_root"])
        check_root(root)
        print(json.dumps({"ok": True, **submission_wrapper(root)}))
        return
    if len(sys.argv) != 1:
        raise ValueError(
            "Usage: python3 saga_prepare.py [setup /path/to/min-test-.../NAME.inp "
            "| check-wrapper | enable-submit | enroll-ts /path/to/review-bundle]"
        )
    request = parse_request(sys.stdin.buffer.read(JSON_LIMIT + 1))
    policy = json.loads(read_file(control_dir() / MARKER, 4096))
    if isinstance(request, dict) and request.get("operation") == "prepare_minimum":
        result = prepare(request, policy)
    elif isinstance(request, dict) and request.get("operation") in (
        "submit_minimum",
        "minimum_status",
    ):
        result = minimum_operation(request, policy)
    elif isinstance(request, dict) and request.get("operation") in (
        "prepare_ts",
        "submit_ts",
        "ts_status",
    ):
        result = ts_operation(request, policy)
    else:
        result = file_operation(request, policy)
    print(json.dumps({"ok": True, "result": result}))


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, KeyError, TypeError, subprocess.TimeoutExpired) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}))
        sys.exit(1)
