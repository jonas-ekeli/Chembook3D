"""D266 local pilot. An app-confirmed revision is the only route to submission."""

import hashlib
import json
import secrets
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from chembook3d.api.remote import _check, _saved
from chembook3d.api.routes import _investigation
from chembook3d.remote import client, job_pilot, servers

router = APIRouter(prefix="/api/hpc-jobs")


class PrepareIn(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    geometry: str = Field(max_length=84)
    job_file: str = Field(max_length=83)
    cores: int = Field(ge=1)
    hours: int = Field(ge=1)


def _profile(server_id: str) -> tuple[list, bytes]:
    server = _saved(server_id)
    target = [server.host, server.port, server.username]
    try:
        profile = json.loads((servers.folder() / "local-job-pilot.json").read_text("utf-8"))
        key = bytes.fromhex(profile["key"])
        if (
            server.host in {"127.0.0.1", "::1"}
            and profile["server_id"] == server_id
            and profile["target"] == target
            and len(key) == 32
        ):
            return target, key
    except (OSError, ValueError, TypeError, KeyError):
        pass
    raise HTTPException(409, "Configure the loopback job pilot for this server first.")


def _record_path(attempt: str) -> Path:
    if not job_pilot.ATTEMPT.fullmatch(attempt):
        raise HTTPException(422, "Invalid prepared attempt ID.")
    return servers.folder() / "prepared-jobs" / f"{attempt}.json"


def _record(server_id: str, attempt: str, target: list, key: bytes) -> dict:
    try:
        record = json.loads(_record_path(attempt).read_text("utf-8"))
    except FileNotFoundError as exc:
        raise HTTPException(404, "Unknown prepared job.") from exc
    if (
        record["server_id"] != server_id
        or record["target"] != target
        or record["key_id"] != hashlib.sha256(key).hexdigest()
    ):
        raise HTTPException(409, "The pilot configuration has changed since preparation.")
    return record


def _summary(record: dict) -> str:
    return (
        f"Submit {record['geometry']} using {record['job_file']} on {record['server_id']} "
        f"(local pilot), 1 node, {record['cores']} cores, {record['hours']} h. "
        f"Directory {record['directory']}. Prepared input SHA-256 {record['revision']}"
    )


def describe_submission(session, params: dict) -> tuple[str, str]:
    server_id = params["server_id"]
    record = _record(server_id, params["attempt"], *_profile(server_id))
    if Path(session.get_bind().url.database).parent != Path(record["folder"]):
        raise HTTPException(409, "Open the investigation that prepared this job.")
    if hashlib.sha256(record["input"].encode()).hexdigest() != record["revision"]:
        raise HTTPException(409, "Prepared input no longer matches its recorded revision.")
    return _summary(record), record["input"]


async def _call(server_id: str, request: dict, target: list, key: bytes) -> dict:
    envelope = {"request": request, "signature": job_pilot.signature(key, request)}
    try:
        response = await client.call(
            "job_pilot", server=server_id, target=target, envelope=envelope, timeout=100
        )
    except client.KeeperError as exc:
        detail = str(exc)
        if request["operation"] == "submit":
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
    target, key = _profile(server_id)
    attempt = secrets.token_hex(16)
    result = await _call(
        server_id, {"operation": "prepare", "attempt": attempt, **body.model_dump()}, target, key
    )
    revision = hashlib.sha256(result["input"].encode()).hexdigest()
    if revision != result["revision"] or result["state"] != "prepared":
        raise HTTPException(409, "Helper did not return a verified prepared input.")
    record = {
        **body.model_dump(),
        "input": result["input"],
        "revision": revision,
        "directory": result["directory"],
        "folder": folder,
        "server_id": server_id,
        "target": target,
        "key_id": hashlib.sha256(key).hexdigest(),
    }
    path = _record_path(attempt)
    path.parent.mkdir(exist_ok=True, parents=True)
    with path.open("x", encoding="utf-8") as stream:
        json.dump(record, stream)
    return result


async def _action(request: Request, server_id: str, attempt: str, operation: str, approval=None):
    _check(request, page_only=False)
    target, key = _profile(server_id)
    record = _record(server_id, attempt, target, key)
    if str(_investigation(request).folder) != record["folder"]:
        raise HTTPException(409, "Open the investigation that prepared this job.")
    payload = {"operation": operation, "attempt": attempt}
    if operation == "submit":
        if approval is None or approval.summary != _summary(record):
            raise HTTPException(409, "The prepared job no longer matches the confirmation.")
        payload.update({k: record[k] for k in ("revision", "cores", "hours")})
    return await _call(server_id, payload, target, key)


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
