"""D266 Gaussian jobs. Bind each app confirmation to the prepared input and destination."""

import hashlib
import json
import os
import re
import secrets
import tempfile
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator

from chembook3d.api.remote import _check, _saved
from chembook3d.api.routes import _investigation
from chembook3d.models import InvestigationInfo
from chembook3d.remote import client, job_pilot, servers

router = APIRouter(prefix="/api/hpc-jobs")


async def _no_arguments(request: Request, detail: str):
    if request.query_params or (await request.body()).strip() not in {b"", b"null", b"{}"}:
        raise HTTPException(422, detail)


def _target(server_id: str) -> list:
    server = _saved(server_id)
    return [server.host, server.port, server.username]


@router.post("/{server_id}/prepare-minimum")
async def prepare_saga_minimum(server_id: str, request: Request):
    """Prepare the minimum enrolled by the operator in a Saga login (D266)."""
    _check(request, page_only=False)
    investigation = _investigation(request)
    info = investigation.info()
    target = _target(server_id)
    await _no_arguments(request, "Only the saved server ID is accepted.")
    try:
        reply = await client.call("saga_prepare_minimum", server=server_id, timeout=115)
    except client.KeeperError as exc:
        if "Unknown operation" in str(exc):
            raise HTTPException(
                409, "The open HPC login uses an older keeper. See the Saga test procedure."
            ) from exc
        raise HTTPException(409, str(exc)) from exc
    result = dict(reply["result"])
    key_id = result.pop("key_id", None)
    if result.get("submission_enabled") is True:
        attempt = result["attempt"]
        revision = hashlib.sha256(result["input"].encode()).hexdigest()
        if info is None or not info.uuid:
            raise HTTPException(409, "The current investigation has no ID for this preparation.")
        if (
            reply.get("target") != target
            or not isinstance(key_id, str)
            or not re.fullmatch(r"[a-f0-9]{64}", key_id)
            or revision != result["revision"]
            or any(
                type(result.get(k)) is not int or result[k] != n
                for k, n in (("nodes", 1), ("cores", 40), ("hours", 24))
            )
            or not result["directory"].endswith("/prepare-" + attempt)
        ):
            raise HTTPException(
                409, "The enrolled preparation does not match the saved target or input."
            )
        record = {
            "pilot": "saga_minimum",
            "server_id": server_id,
            "target": target,
            "key_id": key_id,
            "folder": str(investigation.folder),
            "investigation_id": info.uuid,
            "directory": result["directory"] + "/submission",
            **{
                k: result[k] for k in ("input_name", "revision", "input", "nodes", "cores", "hours")
            },
        }
        _store_record(attempt, record)
    return result


class PrepareIn(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    geometry: str = Field(max_length=84)
    job_file: str = Field(max_length=83)
    cores: int = Field(ge=1)
    hours: int = Field(ge=1)
    freeze_distance: list[Annotated[int, Field(ge=1)]] | None = Field(
        default=None, min_length=2, max_length=2
    )

    @field_validator("freeze_distance")
    @classmethod
    def _distinct_atoms(cls, pair: list[int] | None) -> list[int] | None:
        if pair is not None and pair[0] == pair[1]:
            raise ValueError("Specify two distinct atom indices.")
        return pair


def _local_key(server_id: str, target: list) -> bytes:
    try:
        profile = json.loads((servers.folder() / "local-job-pilot.json").read_text("utf-8"))
        key = bytes.fromhex(profile["key"])
        if (
            target[0] in {"127.0.0.1", "::1"}
            and profile["server_id"] == server_id
            and profile["target"] == target
            and len(key) == 32
        ):
            return key
    except (OSError, ValueError, TypeError, KeyError):
        pass
    raise HTTPException(409, "Configure the loopback job pilot for this server first.")


def _record_path(attempt: str) -> Path:
    if not job_pilot.ATTEMPT.fullmatch(attempt):
        raise HTTPException(422, "Invalid prepared attempt ID.")
    # Generate the filename from the 128-bit ID, preserving existing zero-padded names.
    return servers.folder() / "prepared-jobs" / f"{int(attempt, 16):032x}.json"


def _store_record(attempt: str, record: dict) -> None:
    """Publish the entire preview once. A repeat cannot replace another investigation's claim."""
    path = _record_path(attempt)
    path.parent.mkdir(exist_ok=True, parents=True)
    fd, name = tempfile.mkstemp(dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(record, stream)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            if os.name == "nt":
                os.rename(temporary, path)  # Windows rename refuses an existing destination.
            else:
                os.link(temporary, path)
        except FileExistsError:
            if json.loads(path.read_text("utf-8")) != record:
                raise HTTPException(
                    409, "This attempt already belongs to another investigation or preparation."
                ) from None
    finally:
        temporary.unlink(missing_ok=True)


def _record(
    server_id: str, attempt: str, folder: Path, investigation_id: str | None
) -> tuple[dict, bytes | None]:
    try:
        record = json.loads(_record_path(attempt).read_text("utf-8"))
    except FileNotFoundError as exc:
        raise HTTPException(404, "Unknown prepared job.") from exc
    target = _target(server_id)
    if record["server_id"] != server_id or record["target"] != target:
        raise HTTPException(409, "The pilot configuration has changed since preparation.")
    saga = record.get("pilot") == "saga_minimum"
    if folder != Path(record["folder"]) or (
        saga and record["investigation_id"] != investigation_id
    ):
        raise HTTPException(409, "Open the investigation that prepared this job.")
    if hashlib.sha256(record["input"].encode()).hexdigest() != record["revision"]:
        raise HTTPException(409, "Prepared input no longer matches its recorded revision.")
    if saga:
        return record, None
    key = _local_key(server_id, target)
    if record["key_id"] != hashlib.sha256(key).hexdigest():
        raise HTTPException(409, "The pilot configuration has changed since preparation.")
    return record, key


def _summary(record: dict) -> str:
    if record.get("pilot") == "saga_minimum":
        host, port, username = record["target"]
        return (
            f"Submit {record['input_name']} on {record['server_id']} ({username}@{host}, "
            f"port {port}), {record['nodes']} node, {record['cores']} cores, {record['hours']} h. "
            f"Directory {record['directory']}. Prepared input SHA-256 {record['revision']}"
        )
    freeze = ""
    if pair := record.get("freeze_distance"):
        first, second = pair
        freeze = f"Freeze the distance between atoms {first} and {second} at its starting value. "
    return (
        f"Submit {record['geometry']} using {record['job_file']} on {record['server_id']} "
        f"(local pilot), 1 node, {record['cores']} cores, {record['hours']} h. "
        f"{freeze}Directory {record['directory']}. Prepared input SHA-256 {record['revision']}"
    )


def describe_submission(session, params: dict) -> tuple[str, str]:
    if params.keys() != {"server_id", "attempt"}:
        raise HTTPException(422, "Only the server ID and prepared attempt are accepted.")
    folder = Path(session.get_bind().url.database).parent
    info = session.get(InvestigationInfo, 1)
    record, _key = _record(
        params["server_id"], params["attempt"], folder, info.uuid if info else None
    )
    return _summary(record), record["input"]


async def _call(
    server_id: str, request: dict, target: list, key: bytes | None, key_id=None
) -> dict:
    try:
        if key is None:
            response = await client.call(
                "saga_minimum_job",
                server=server_id,
                target=target,
                key_id=key_id,
                request=request,
                timeout=115,
            )
        else:
            envelope = {"request": request, "signature": job_pilot.signature(key, request)}
            response = await client.call(
                "job_pilot", server=server_id, target=target, envelope=envelope, timeout=100
            )
    except client.KeeperError as exc:
        detail = str(exc)
        if request["operation"] in {"submit", "submit_minimum"}:
            detail += " Check the same prepared attempt after reconnecting."
        raise HTTPException(409, detail) from exc
    result = response["result"]
    if result["attempt"] != request["attempt"]:
        raise HTTPException(409, "The helper returned a different attempt.")
    if result.get("job_id"):
        result["follow_up"] = {
            "server_id": server_id,
            "job_id": result["job_id"],
            "path": result["directory"],
        }
    return result


@router.post("/{server_id}/prepare")
async def prepare(server_id: str, body: PrepareIn, request: Request):
    _check(request, page_only=False)
    folder = str(_investigation(request).folder)
    target = _target(server_id)
    key = _local_key(server_id, target)
    attempt = secrets.token_hex(16)
    options = body.model_dump(exclude_none=True)
    result = await _call(
        server_id, {"operation": "prepare", "attempt": attempt, **options}, target, key
    )
    revision = hashlib.sha256(result["input"].encode()).hexdigest()
    if (
        revision != result["revision"]
        or result["state"] != "prepared"
        or result.get("freeze_distance") != body.freeze_distance
    ):
        raise HTTPException(409, "Helper did not return a verified prepared input.")
    record = {
        **options,
        "input": result["input"],
        "revision": revision,
        "directory": result["directory"],
        "folder": folder,
        "server_id": server_id,
        "target": target,
        "key_id": hashlib.sha256(key).hexdigest(),
    }
    _store_record(attempt, record)
    return result


async def _action(request: Request, server_id: str, attempt: str, operation: str, approval=None):
    _check(request, page_only=False)
    await _no_arguments(request, "Only the server ID and prepared attempt are accepted.")
    investigation = _investigation(request)
    info = investigation.info()
    record, key = _record(server_id, attempt, investigation.folder, info.uuid if info else None)
    payload = {"operation": operation, "attempt": attempt}
    if operation == "submit":
        if (
            approval is None
            or approval.summary != _summary(record)
            or approval.preview != record["input"]
        ):
            raise HTTPException(409, "The prepared job no longer matches the confirmation.")
        payload.update({k: record[k] for k in ("revision", "cores", "hours")})
    if record.get("pilot") == "saga_minimum":
        if operation == "submit":
            payload.update(operation="submit_minimum", nodes=record["nodes"])
        else:
            payload.update(operation="minimum_status", revision=record["revision"])
    result = await _call(server_id, payload, record["target"], key, record["key_id"])
    if "freeze_distance" in record:
        result["freeze_distance"] = record["freeze_distance"]
    return result


@router.get("/{server_id}/attempts/{attempt}")
async def status(server_id: str, attempt: str, request: Request):
    return await _action(request, server_id, attempt, "status")


@router.post("/{server_id}/attempts/{attempt}/submit")
async def submit(server_id: str, attempt: str, request: Request):
    from chembook3d.api.live import confirmed_action

    approval = confirmed_action.get()
    if (
        approval is None
        or approval.action != "submit_prepared_job"
        or approval.params.get("server_id") != server_id
        or approval.params.get("attempt") != attempt
    ):
        raise HTTPException(403, "Confirm this prepared revision in the app to submit it.")
    return await _action(request, server_id, attempt, "submit", approval)
