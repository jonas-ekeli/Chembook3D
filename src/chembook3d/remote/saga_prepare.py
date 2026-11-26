"""Standalone Linux helper for Saga. Run setup manually with a verified .inp path."""

import base64
import hashlib
import json
import os
import re
import secrets
import stat
import subprocess
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path

JOB = "min-go_PBEPBE-GD3MBJ_DZ_singlet_CONVER9.jd"
MARKER = ".prepare-minimum.json"
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
    if len(sys.argv) != 1:
        raise ValueError("Usage: python3 saga_prepare.py setup /path/to/min-test-.../NAME.inp")
    request = parse_request(sys.stdin.buffer.read(JSON_LIMIT + 1))
    policy = json.loads(read_file(control_dir() / MARKER, 4096))
    if isinstance(request, dict) and request.get("operation") == "prepare_minimum":
        result = prepare(request, policy)
    else:
        result = file_operation(request, policy)
    print(json.dumps({"ok": True, "result": result}))


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, KeyError, TypeError, subprocess.TimeoutExpired) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}))
        sys.exit(1)
