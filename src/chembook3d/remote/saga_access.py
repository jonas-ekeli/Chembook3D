"""Trusted keeper side of the preparation-only Saga deployment (D266)."""

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


async def prepare(connection, root: str) -> dict:
    try:
        reply = await connection.run(
            COMMAND, input='{"operation":"prepare_minimum"}\n', encoding="utf-8", timeout=105
        )
        if reply.exit_status in {3, 4}:
            return {
                "ok": False,
                "error": "Install the matching preparation helper from this checkout.",
            }
        answer = json.loads(reply.stdout)
        if reply.exit_status == 1 and answer.get("ok") is False:
            return {"ok": False, "error": str(answer.get("error", "Preparation failed."))[:500]}
        result = answer["result"]
        contents = result["input"].encode("utf-8")
        sha = hashlib.sha256(contents).hexdigest()
        if (
            reply.exit_status != 0
            or answer["ok"] is not True
            or result["state"] != "prepared"
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
    except (OSError, TimeoutError, asyncssh.Error):
        return {
            "ok": False,
            "error": "The preparation channel closed. Recheck the same enrollment.",
        }
