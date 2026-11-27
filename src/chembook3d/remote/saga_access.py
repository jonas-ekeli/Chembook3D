"""Trusted keeper side of the enrolled Saga workflow (D266)."""

import base64
import hashlib
import json
import posixpath
import re
import shlex
from dataclasses import dataclass, field
from pathlib import Path

import asyncssh

from chembook3d.remote import job_pilot, saga_prepare


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
CONTROL = ".local/lib/chembook3d/"
SUBMISSION_MARKER = ".submit-minimum.json"


async def _policy(sftp, name: str) -> dict | None:
    marker = CONTROL + name
    try:
        attrs = await sftp.lstat(marker)
    except asyncssh.SFTPNoSuchFile:
        return None
    if attrs.type != asyncssh.FILEXFER_TYPE_REGULAR or attrs.size is None or attrs.size > 4096:
        raise ValueError("Invalid Saga enrollment file.")
    if name == SUBMISSION_MARKER and (attrs.permissions is None or attrs.permissions & 0o077):
        raise ValueError("The Saga submission policy must be private to its owner.")
    async with sftp.open(marker, "rb") as stream:
        data = await stream.read(4097)
    try:
        policy = json.loads(data)
        if len(data) > 4096 or not isinstance(policy, dict):
            raise ValueError
        return policy
    except (ValueError, RecursionError) as exc:
        raise ValueError("Invalid Saga enrollment file.") from exc


async def enrolled_root(sftp, username: str) -> str | None:
    policy = await _policy(sftp, saga_prepare.MARKER)
    if policy is None:
        # A surviving signing key still needs protection when a preparation
        # enrollment is missing, including on a new login or saved host alias.
        try:
            await sftp.lstat(CONTROL + SUBMISSION_MARKER)
        except asyncssh.SFTPNoSuchFile:
            return None
        raise ValueError("Missing preparation enrollment for this Saga submission policy.")
    try:
        root = policy["work_root"]
        pattern = r"/cluster/projects/[a-zA-Z0-9_-]+/" + re.escape(username) + "/chembook3d-pilot"
        if not re.fullmatch(pattern, root) or await sftp.realpath(root) != root:
            raise ValueError
    except (KeyError, TypeError, ValueError, RecursionError) as exc:
        raise ValueError("Invalid Saga preparation enrollment.") from exc
    return root


@dataclass(frozen=True)
class SubmissionPolicy:
    attempt: str
    revision: str
    key: bytes = field(repr=False)

    @property
    def key_id(self) -> str:
        return hashlib.sha256(self.key).hexdigest()


RESOURCES = {"nodes": 1, "cores": 40, "hours": 24}


def _resources_match(data: dict) -> bool:
    return all(type(data.get(k)) is int and data[k] == v for k, v in RESOURCES.items())


async def submission_policy(sftp) -> SubmissionPolicy | None:
    policy = await _policy(sftp, SUBMISSION_MARKER)
    if policy is None:
        return None
    try:
        if (
            not job_pilot.ATTEMPT.fullmatch(policy["attempt"])
            or not re.fullmatch(r"[0-9a-f]{64}", policy["revision"])
            or not re.fullmatch(r"[0-9a-f]{64}", policy["key"])
            or not _resources_match(policy)
        ):
            raise ValueError
        return SubmissionPolicy(policy["attempt"], policy["revision"], bytes.fromhex(policy["key"]))
    except (KeyError, ValueError, TypeError):
        raise ValueError("Invalid Saga submission enrollment.") from None


async def _request(connection, request: dict) -> dict:
    try:
        reply = await connection.run(
            COMMAND,
            input=json.dumps(request, separators=(",", ":")) + "\n",
            encoding="utf-8",
            timeout=105,
        )
        if reply.exit_status == 3:
            return {
                "ok": False,
                "error": "Saga could not read ~/.local/lib/chembook3d/saga_prepare.py. "
                "Check the installed helper on the connected account.",
            }
        if reply.exit_status == 4:
            return {
                "ok": False,
                "error": f"The running keeper expects Saga helper SHA-256 {_sha}. "
                "Compare the installed helper with the workstation checkout. "
                "If those match, restart the keeper using the Saga test procedure.",
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
            result.keys()
            != {"state", "directory", "input_name", "sha256", "reference_sha256", "input"}
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


async def minimum_job(connection, root: str, policy: SubmissionPolicy, request: dict) -> dict:
    """Sign only the fixed submit operation for this enrollment. Status needs no signature."""
    if not isinstance(request, dict) or request.get("operation") not in (
        "submit_minimum",
        "minimum_status",
    ):
        return {"ok": False, "error": "Unknown Saga job operation."}
    submitting = request["operation"] == "submit_minimum"
    fields = {"operation", "attempt", "revision"}
    if submitting:
        fields.update(RESOURCES)
    if (
        request.keys() != fields
        or request["attempt"] != policy.attempt
        or request["revision"] != policy.revision
        or (submitting and not _resources_match(request))
    ):
        return {"ok": False, "error": "The request does not match the enrolled minimum."}
    if submitting:
        request = {**request, "signature": job_pilot.signature(policy.key, request)}
    answer = await _request(connection, request)
    if not answer["ok"]:
        return answer
    result = answer["result"]
    expected_dir = posixpath.join(root, "prepare-" + policy.attempt, "submission")
    try:
        state = result["state"]
        if (
            result.keys()
            != {"attempt", "revision", "state", "directory", "job_id", "detail", *RESOURCES}
            or result["attempt"] != policy.attempt
            or result["revision"] != policy.revision
            or result["directory"] != expected_dir
            or not _resources_match(result)
            or state not in ("prepared", "submitted", "rejected", "uncertain")
            or (submitting and state == "prepared")
            or (result["detail"] is not None and not isinstance(result["detail"], str))
        ):
            raise ValueError
        if state == "submitted":
            if not re.fullmatch(r"[1-9][0-9]{0,19}", result["job_id"]):
                raise ValueError
        elif result["job_id"] is not None:
            raise ValueError
    except (KeyError, TypeError, ValueError):
        return {"ok": False, "error": "The Saga helper returned an invalid submission result."}
    return answer


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
