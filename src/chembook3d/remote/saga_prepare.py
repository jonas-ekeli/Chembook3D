"""Standalone Saga-side helper (D266). Run setup manually with a verified .inp path."""

import hashlib
import json
import os
import re
import secrets
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

JOB = "min-go_PBEPBE-GD3MBJ_DZ_singlet_CONVER9.jd"
MARKER = ".prepare-minimum.json"
LIMIT = 256_000
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


def inputs(root, reference_dir, stem):
    if root.name != "chembook3d-pilot" or root != root.resolve(strict=True):
        raise ValueError("Select a canonical chembook3d-pilot directory.")
    if (
        len(root.parents) < 3
        or root.parents[2] != PROJECTS
        or not all(re.fullmatch(r"[a-zA-Z0-9_-]+", part) for part in root.parts[-3:])
    ):
        raise ValueError("The reference must be in a test directory under the project pilot.")
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


def main():
    if len(sys.argv) == 3 and sys.argv[1] == "setup":
        print("Enrolled manual reference SHA-256:", install(Path(sys.argv[2])))
        return
    if len(sys.argv) != 1:
        raise ValueError("Usage: python3 saga_prepare.py setup /path/to/min-test-.../NAME.inp")
    raw = sys.stdin.buffer.read(257)
    if len(raw) > 256:
        raise ValueError("Request is too large.")
    request = json.loads(raw)
    policy = json.loads(read_file(control_dir() / MARKER, 4096))
    print(json.dumps({"ok": True, "result": prepare(request, policy)}))


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, KeyError, TypeError, subprocess.TimeoutExpired) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}))
        sys.exit(1)
