"""Trusted keeper side of the preparation-only Saga deployment (D266)."""

import base64
import hashlib
import json
import posixpath
import re
import shlex
from pathlib import Path

import asyncssh

from chembook3d.remote import saga_prepare


def is_saga(host: str) -> bool:
    host = host.lower()
    return host in {"127.0.0.1", "::1", "saga.sigma2.no"} or host.endswith(".saga.sigma2.no")


# The local package pins exactly the standalone code the operator installs on Saga.
# No request data participates in building the executable, its arguments or its path.
_sha = hashlib.sha256(Path(saga_prepare.__file__).read_bytes().replace(b"\r\n", b"\n")).hexdigest()
LAUNCHER = f"""\
import hashlib
import sys
from pathlib import Path
path = Path.home() / ".local/lib/chembook3d/saga_prepare.py"
try:
    with path.open("rb") as stream:
        source = stream.read(65537).replace(b"\\r\\n", b"\\n")
except OSError:
    sys.exit(3)
if hashlib.sha256(source).hexdigest() != "{_sha}":
    sys.exit(4)
exec(compile(source, str(path), "exec"), {{"__name__": "__main__"}})
"""
COMMAND = "python3 -I -S -c " + shlex.quote(LAUNCHER)


async def enrolled_root(sftp, username: str) -> str | None:
    marker = ".local/lib/chembook3d/" + saga_prepare.MARKER
    try:
        attrs = await sftp.lstat(marker)
    except asyncssh.SFTPNoSuchFile:
        return None
    if attrs.type != asyncssh.FILEXFER_TYPE_REGULAR or attrs.size is None or attrs.size > 4096:
        raise ValueError("Invalid Saga preparation enrollment.")
    async with sftp.open(marker, "rb") as stream:
        data = await stream.read(4097)
    try:
        if len(data) > 4096:
            raise ValueError
        root = json.loads(data)["work_root"]
        pattern = r"/cluster/projects/[a-zA-Z0-9_-]+/" + re.escape(username) + "/chembook3d-pilot"
        if not re.fullmatch(pattern, root) or await sftp.realpath(root) != root:
            raise ValueError
    except (KeyError, TypeError, ValueError, RecursionError) as exc:
        raise ValueError("Invalid Saga preparation enrollment.") from exc
    return root


async def _request(connection, request: dict) -> dict:
    try:
        reply = await connection.run(
            COMMAND,
            input=json.dumps(request, separators=(",", ":")) + "\n",
            encoding="utf-8",
            timeout=105,
        )
        if reply.exit_status in {3, 4}:
            return {
                "ok": False,
                "error": "Install the matching Saga helper from this checkout.",
            }
        answer = json.loads(reply.stdout)
        if reply.exit_status == 1 and answer.get("ok") is False:
            return {"ok": False, "error": str(answer.get("error", "Saga request failed."))[:500]}
        if (
            reply.exit_status != 0
            or answer["ok"] is not True
            or not isinstance(answer["result"], dict)
        ):
            raise ValueError
        return {"ok": True, "result": answer["result"]}
    except (AttributeError, KeyError, TypeError, ValueError, RecursionError):
        return {"ok": False, "error": "The Saga helper returned an invalid response."}
    except (OSError, TimeoutError, asyncssh.Error):
        return {"ok": False, "error": "The Saga helper channel closed before the result arrived."}


async def prepare(connection, root: str) -> dict:
    answer = await _request(connection, {"operation": "prepare_minimum"})
    if not answer["ok"]:
        return answer
    try:
        result = answer["result"]
        contents = result["input"].encode("utf-8")
        sha = hashlib.sha256(contents).hexdigest()
        if (
            result["state"] != "prepared"
            or not 0 < len(contents) <= saga_prepare.LIMIT
            or not sha == result["sha256"] == result["reference_sha256"]
            or posixpath.dirname(result["directory"]) != root
            or not re.fullmatch(r"prepare-[a-f0-9]{32}", posixpath.basename(result["directory"]))
            or not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_-]{0,79}\.inp", result["input_name"])
        ):
            raise ValueError
        return {"ok": True, "result": result}
    except (AttributeError, KeyError, TypeError, ValueError, RecursionError):
        return {"ok": False, "error": "The Saga helper returned an invalid preparation result."}


async def file_request(connection, request: dict, root: str) -> dict:
    """Use only the pinned launcher. Treat a partial or malformed reply as failure."""
    operation = request["operation"]
    if operation not in {"list_files", "read_file", "create_xyz"}:
        return {"ok": False, "error": "Unknown Saga file operation."}
    answer = await _request(connection, request)
    if not answer["ok"]:
        return answer
    try:
        result = answer["result"]
        path = result["path"]
        if posixpath.normpath(path) != path or (path != root and not path.startswith(root + "/")):
            raise ValueError
        if operation == "list_files":
            entries = result["entries"]
            if (
                result["agent_read_root"] != root
                or not isinstance(entries, list)
                or len(entries) > 2000
            ):
                raise ValueError
            for entry in entries:
                name = entry["name"]
                if not isinstance(name, str) or name in {"", ".", ".."} or "/" in name:
                    raise ValueError
                if entry["kind"] not in {"directory", "file", "outside"}:
                    raise ValueError
        elif operation == "read_file":
            size = result["size"]
            if type(size) is not int or size < 0 or type(result["modified"]) is not int:
                raise ValueError
            if not re.fullmatch(r"[0-9a-f]{64}", result["version"]):
                raise ValueError
            offset = request["offset"]
            start = max(size + offset, 0) if offset < 0 else min(offset, size)
            count = min(request["length"], size - start)
            if type(result["offset"]) is not int or result["offset"] != start:
                raise ValueError
            data = base64.b64decode(result["data"], validate=True)
            if len(data) != count:
                raise ValueError
        elif path != request["path"] or type(result["written"]) is not bool:
            raise ValueError
        elif result["written"] and result["size"] != len(
            request["text"].replace("\r\n", "\n").encode("utf-8")
        ):
            raise ValueError
        return {**result, "ok": True}
    except (AttributeError, KeyError, TypeError, ValueError, RecursionError):
        return {"ok": False, "error": "The Saga helper returned an invalid file result."}
