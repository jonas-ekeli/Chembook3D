"""D266 Gaussian jobs. Bind each app confirmation to the prepared input and destination."""

import asyncio
import hashlib
import json
import os
import re
import secrets
import tempfile
import threading
import time
from pathlib import Path
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from chembook3d.api import routes
from chembook3d.api.remote import _check, _saved
from chembook3d.api.routes import _investigation
from chembook3d.models import Calculation, InvestigationInfo, Node
from chembook3d.remote import checkpoint_ts, client, job_pilot, preopt_modes, saga_access, servers
from chembook3d.services import imports, irc_ends

router = APIRouter(prefix="/api/hpc-jobs")
_TS_PILOT = "local_ts"
_SAGA_TS = "saga_ts"
_SAGA_NODE = "saga_node"
_SAGA_PILOTS = {"saga_minimum", _SAGA_TS, _SAGA_NODE}
_HEX32 = r"^[a-f0-9]{32}$"


async def _no_arguments(request: Request, detail: str):
    if request.query_params or (await request.body()).strip() not in {b"", b"null", b"{}"}:
        raise HTTPException(422, detail)


def _target(server_id: str) -> list:
    server = _saved(server_id)
    return [server.host, server.port, server.username]


@router.post("/{server_id}/prepare-minimum")
async def prepare_saga_minimum(server_id: str, request: Request):
    """Prepare the minimum enrolled by the operator in a Saga login (D266)."""
    return await _prepare_saga(server_id, request, ts=False)


@router.post("/{server_id}/prepare-ts")
async def prepare_saga_ts(server_id: str, request: Request):
    """Prepare the archived TS input and checkpoint enrolled on the connected Saga account."""
    return await _prepare_saga(server_id, request, ts=True)


async def _prepare_saga(server_id: str, request: Request, *, ts: bool):
    _check(request, page_only=False)
    investigation = _investigation(request)
    info = investigation.info()
    target = _target(server_id)
    await _no_arguments(request, "Only the saved server ID is accepted.")
    if ts and (info is None or not info.uuid):
        raise HTTPException(409, "The current investigation has no ID for this preparation.")
    try:
        if ts:
            reply = await client.call(
                "saga_prepare_ts", server=server_id, target=target, timeout=115
            )
        else:
            reply = await client.call("saga_prepare_minimum", server=server_id, timeout=115)
    except client.KeeperError as exc:
        if "Unknown operation" in str(exc):
            raise HTTPException(
                409, "The open HPC login uses an older keeper. See the Saga test procedure."
            ) from exc
        raise HTTPException(409, str(exc)) from exc
    if not isinstance(reply.get("result"), dict):
        raise HTTPException(409, "The keeper returned an invalid Saga preparation.")
    result = dict(reply["result"])
    key_id = result.pop("key_id", None)
    if ts:
        try:
            if (
                result.keys()
                != saga_access.TS_PREVIEW_FIELDS | {"submission_enabled", *saga_access.RESOURCES}
                or result["submission_enabled"] is not True
                or not isinstance(result["attempt"], str)
                or not job_pilot.ATTEMPT.fullmatch(result["attempt"])
                or not isinstance(result["directory"], str)
                or not re.fullmatch(
                    rf"/cluster/projects/[A-Za-z0-9_-]+/{re.escape(target[2])}/chembook3d-pilot/"
                    rf"ts-{result['attempt']}",
                    result["directory"],
                )
            ):
                raise ValueError
            saga_access.validate_ts_preview(result)
        except ValueError as exc:
            raise HTTPException(409, "The keeper returned an invalid Saga TS preparation.") from exc
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
                for k, n in saga_access.RESOURCES.items()
            )
            or not result["directory"].endswith(("/ts-" if ts else "/prepare-") + attempt)
        ):
            raise HTTPException(
                409, "The enrolled preparation does not match the saved target or input."
            )
        record = {
            "pilot": _SAGA_TS if ts else "saga_minimum",
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
        if ts:
            record.update({k: result[k] for k in ("review", "review_sha256", "checkpoint_sha256")})
        _store_record(attempt, record)
    return result


class SeedIn(BaseModel):
    """D271f: the frame or calculation the agent started from, or how it built the guess."""

    model_config = ConfigDict(extra="forbid", strict=True)
    source: str = Field(min_length=1, max_length=300)
    reason: str = Field(min_length=1, max_length=1000)
    calculation_id: str | None = Field(default=None, min_length=1, max_length=32)
    neighbour_ids: list[Annotated[str, Field(min_length=1, max_length=32)]] = Field(
        default_factory=list, max_length=8
    )
    atom_mapping: list[Annotated[int, Field(ge=1)]] | None = Field(
        default=None, min_length=1, max_length=5000
    )

    @field_validator("atom_mapping")
    @classmethod
    def _permutation(cls, mapping: list[int] | None) -> list[int] | None:
        if mapping is not None and sorted(mapping) != list(range(1, len(mapping) + 1)):
            raise ValueError("The atom mapping must number each atom once, from 1.")
        return mapping


class PrepareNodeIn(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    node_id: str = Field(min_length=1, max_length=32)
    geometry: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,79}\.xyz$")
    freeze_distance: list[Annotated[int, Field(ge=1)]] | None = Field(
        default=None, min_length=2, max_length=2
    )
    request_id: str | None = Field(default=None, pattern=_HEX32)
    seed: SeedIn

    @field_validator("freeze_distance")
    @classmethod
    def _distinct_atoms(cls, pair: list[int] | None) -> list[int] | None:
        if pair is not None and pair[0] == pair[1]:
            raise ValueError("Specify two distinct atom indices.")
        return pair


def _node_request_path(request_id: str) -> Path:
    if not re.fullmatch(_HEX32, request_id):
        raise HTTPException(422, "Invalid node request ID.")
    # Build the filename from the 128-bit ID itself, as _record_path does.
    return servers.folder() / "saga-node-requests" / f"{int(request_id, 16):032x}.json"


def _open_node_request(body: PrepareNodeIn, opened: dict) -> str:
    """D271: one request per node and electronic state, the frame for the helper's limits."""
    if body.request_id is None:
        return secrets.token_hex(16)
    try:
        record = json.loads(_node_request_path(body.request_id).read_text("utf-8"))
    except FileNotFoundError as exc:
        raise HTTPException(404, "Unknown node request. Leave request_id out to open one.") from exc
    if any(record.get(k) != opened[k] for k in ("server_id", "target", "investigation_id")):
        raise HTTPException(409, "Open the investigation and server of this node request.")
    if record["node_id"] != opened["node_id"]:
        raise HTTPException(409, "This node request belongs to another node.")
    if (record["charge"], record["multiplicity"]) != (opened["charge"], opened["multiplicity"]):
        raise HTTPException(
            409,
            "The node's charge or multiplicity changed since this request was opened. "
            "Leave request_id out to open a new request.",
        )
    return body.request_id


def _store_node_request(request_id: str, opened: dict) -> None:
    path = _node_request_path(request_id)
    path.parent.mkdir(exist_ok=True, parents=True)
    try:
        with path.open("x", encoding="utf-8") as stream:
            json.dump({"request_id": request_id, **opened}, stream)
    except FileExistsError:
        pass  # Opened by an earlier parent; _open_node_request checked it.


def _seed(db, node: Node, seed: SeedIn) -> dict:
    """D271f: the seed as the review and the imported calculations name it."""
    if seed.calculation_id is not None and db.get(Calculation, seed.calculation_id) is None:
        raise HTTPException(404, "Unknown seed calculation.")
    if len(set(seed.neighbour_ids)) != len(seed.neighbour_ids) or node.id in seed.neighbour_ids:
        raise HTTPException(422, "Name each adjacent node once, and not the node itself.")
    neighbours = []
    for neighbour_id in seed.neighbour_ids:
        neighbour = db.get(Node, neighbour_id)
        if neighbour is None:
            raise HTTPException(404, "Unknown adjacent node.")
        neighbours.append({"id": neighbour.id, "label": neighbour.label})
    return {
        "source": seed.source,
        "reason": seed.reason,
        "calculation_id": seed.calculation_id,
        "neighbours": neighbours,
        "atom_mapping": seed.atom_mapping,
    }


def _seed_text(seed: dict, lead: str = "Seed") -> str:
    text = f"{lead}: {seed['source']}. Why: {seed['reason']}"
    if seed["calculation_id"]:
        text += f". Seed calculation {seed['calculation_id']}"
    if seed["neighbours"]:
        text += ". Adjacent nodes " + ", ".join(n["label"] or n["id"] for n in seed["neighbours"])
    if seed["atom_mapping"]:
        text += f". Atom mapping for {len(seed['atom_mapping'])} atoms"
    return text


def _node_state(investigation, server_id: str, info) -> dict:
    return {
        "server_id": server_id,
        "target": _target(server_id),
        "investigation_id": info.uuid,
        "folder": str(investigation.folder),
    }


@router.post("/{server_id}/prepare-node")
async def prepare_saga_node(server_id: str, body: PrepareNodeIn, request: Request):
    """D271: prepare a parent for node X from the agent's XYZ with the enrolled minimum job."""
    _check(request, page_only=False)
    investigation = _investigation(request)
    info = investigation.info()
    if info is None or not info.uuid:
        raise HTTPException(409, "The current investigation has no ID for this preparation.")
    opened = _node_state(investigation, server_id, info)
    target = opened["target"]
    with investigation.sessions.begin() as db:
        node = db.get(Node, body.node_id)
        if node is None:
            raise HTTPException(404, "Unknown node.")
        if node.charge is None or node.multiplicity is None:
            raise HTTPException(
                409, "Set the node's charge and multiplicity first: the helper checks them (D271a)."
            )
        opened.update(
            node_id=node.id,
            node_label=node.label,
            charge=node.charge,
            multiplicity=node.multiplicity,
        )
        seed = _seed(db, node, body.seed)
    request_id = _open_node_request(body, opened)
    attempt = secrets.token_hex(16)
    wire = {
        "request": request_id,
        "attempt": attempt,
        "geometry": body.geometry,
        "charge": opened["charge"],
        "multiplicity": opened["multiplicity"],
    }
    if body.freeze_distance is not None:
        wire["freeze_distance"] = body.freeze_distance
    try:
        reply = await client.call(
            "saga_prepare_node", server=server_id, target=target, request=wire, timeout=115
        )
    except client.KeeperError as exc:
        if "Unknown operation" in str(exc):
            raise HTTPException(
                409, "The open HPC login uses an older keeper. See the Saga test procedure."
            ) from exc
        raise HTTPException(409, str(exc)) from exc
    try:
        result = dict(reply["result"])
        key_id = result.pop("key_id")
        contents = result["input"]
        if (
            result.keys()
            != saga_access.NODE_PREVIEW_FIELDS | {"submission_enabled", *saga_access.RESOURCES}
            or result["submission_enabled"] is not True
            or reply.get("target") != target
            or not isinstance(key_id, str)
            or not re.fullmatch(r"[a-f0-9]{64}", key_id)
            or any(result[k] != v for k, v in wire.items())
            or result["freeze_distance"] != body.freeze_distance
            or hashlib.sha256(contents.encode()).hexdigest() != result["revision"]
            or any(
                type(result[k]) is not int or result[k] != n
                for k, n in saga_access.RESOURCES.items()
            )
            or not re.fullmatch(
                rf"/cluster/projects/[A-Za-z0-9_-]+/{re.escape(target[2])}/chembook3d-pilot/"
                rf"node-{attempt}",
                result["directory"],
            )
            or set(saga_access.electrons_in_steps(contents))
            != {(opened["charge"], opened["multiplicity"])}
        ):
            raise ValueError
    except (AttributeError, KeyError, TypeError, ValueError) as exc:
        raise HTTPException(409, "The keeper returned an invalid Saga node preparation.") from exc
    _store_node_request(request_id, opened)
    record = {
        **opened,
        "pilot": _SAGA_NODE,
        "key_id": key_id,
        "request_id": request_id,
        "directory": result["directory"] + "/submission",
        "seed": seed,
        **{
            k: result[k]
            for k in (
                "input_name",
                "revision",
                "input",
                "geometry",
                "freeze_distance",
                "job_file",
                "job_sha256",
                "limits",
                *saga_access.RESOURCES,
            )
        },
    }
    _store_record(attempt, record)
    return {**result, "node_id": opened["node_id"]}


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


_Sha256 = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]


class PrepareTSIn(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    cores: int = Field(ge=1)
    hours: int = Field(ge=1)
    mode_index: int = Field(ge=1)
    output_sha256: _Sha256


_JOB_ID_PATTERN = r"^[1-9][0-9]{0,19}$"
_JobId = Annotated[str, Field(pattern=_JOB_ID_PATTERN)]
_AtomPair = Annotated[list[Annotated[int, Field(ge=1)]], Field(min_length=2, max_length=2)]


class _ModeEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)
    mode_index: int = Field(ge=1)
    frequency_cm1: float = Field(lt=0)
    bond_projection: float
    relative_displacement: float = Field(ge=0)
    alignment: float = Field(ge=0, le=1)
    bond_share: float | None = Field(default=None, ge=0)  # D269; absent in older records
    eligible: bool


class _SelectedMode(_ModeEvidence):
    freeze_distance: _AtomPair
    output_sha256: _Sha256


class _InspectedModes(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    attempt: str
    parent_job_id: _JobId
    freeze_distance: _AtomPair
    output_sha256: _Sha256
    modes: list[_ModeEvidence]


class _TSDetails(BaseModel):
    model_config = ConfigDict(strict=True)
    parent_attempt: str
    parent_job_id: _JobId
    checkpoint_name: str
    checkpoint_sha256: _Sha256
    mode_index: int = Field(ge=1)
    mode_evidence: _SelectedMode
    directory: str
    revision: _Sha256
    cores: int = Field(ge=1)
    hours: int = Field(ge=1)


def _ts_details(result: dict) -> dict:
    try:
        return _TSDetails.model_validate(result).model_dump()
    except ValidationError as exc:
        raise HTTPException(409, "The helper returned invalid TS preparation metadata.") from exc


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


def _read_published(path: Path) -> dict:
    """Read a record another request has just published.

    On Windows a concurrent rename briefly holds the destination open, so an immediate
    read can fail with a sharing violation (PermissionError). Retry for about a second.
    """
    for delay in (0.02, 0.05, 0.1, 0.2, 0.3, 0.4):
        try:
            return json.loads(path.read_text("utf-8"))
        except PermissionError:
            if os.name != "nt":
                raise
            time.sleep(delay)
    return json.loads(path.read_text("utf-8"))


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
            if _read_published(path) != record:
                raise HTTPException(
                    409, "This attempt already belongs to another investigation or preparation."
                ) from None
    finally:
        temporary.unlink(missing_ok=True)


def _record(
    server_id: str,
    attempt: str,
    folder: Path,
    investigation_id: str | None,
    *,
    require_identity: bool = False,
) -> tuple[dict, bytes | None]:
    try:
        record = json.loads(_record_path(attempt).read_text("utf-8"))
    except FileNotFoundError as exc:
        raise HTTPException(404, "Unknown prepared job.") from exc
    target = _target(server_id)
    if record["server_id"] != server_id or record["target"] != target:
        raise HTTPException(409, "The pilot configuration has changed since preparation.")
    owner = record.get("investigation_id")
    if folder != Path(record["folder"]) or (owner and owner != investigation_id):
        raise HTTPException(409, "Open the investigation that prepared this job.")
    pilot = record.get("pilot")
    if not owner and (require_identity or pilot in {*_SAGA_PILOTS, _TS_PILOT}):
        raise HTTPException(409, "This prepared job has no recorded investigation ID.")
    if hashlib.sha256(record["input"].encode()).hexdigest() != record["revision"]:
        raise HTTPException(409, "Prepared input no longer matches its recorded revision.")
    if pilot == _SAGA_TS:
        try:
            saga_access.validate_ts_preview({**record, "state": "prepared"})
        except ValueError as exc:
            raise HTTPException(409, "The TS review no longer matches its preparation.") from exc
    if pilot in _SAGA_PILOTS:
        return record, None
    key = _local_key(server_id, target)
    if record["key_id"] != hashlib.sha256(key).hexdigest():
        raise HTTPException(409, "The pilot configuration has changed since preparation.")
    return record, key


def _node_summary(record: dict) -> str:
    host, port, username = record["target"]
    if record.get("kind") == "child":
        mode = record["mode_evidence"]
        first, second = mode["freeze_distance"]
        return (
            f"Submit the unconstrained TS optimization and frequency {record['input_name']} "
            f"for node {record['node_label'] or record['node_id']} (charge {record['charge']}, "
            f"multiplicity {record['multiplicity']}) on {record['server_id']} "
            f"({username}@{host}, port {port}), {record['nodes']} node, {record['cores']} "
            f"cores, {record['hours']} h. Parent attempt {record['parent_attempt']} (Slurm job "
            f"{record['parent_job_id']}). Mode {record['mode_index']} "
            f"({mode['frequency_cm1']:g} cm⁻¹), atoms {first} and {second}, bond projection "
            f"{mode['bond_projection']:g}, alignment {mode['alignment']:g}, bond share "
            f"{mode['bond_share']:g}. Parent output SHA-256 {record['output_sha256']}. "
            f"Checkpoint {record['checkpoint_name']} SHA-256 {record['checkpoint_sha256']}. "
            f"Job description {record['job_file']} SHA-256 {record['job_sha256']}, steps 4 and "
            f"5. Node request {record['request_id']}: at most {record['limits']['child']} TS "
            f"children are started. {_seed_text(record['seed'], 'Parent seed')}. Directory "
            f"{record['directory']}. Prepared input SHA-256 {record['revision']}"
        )
    if pair := record.get("freeze_distance"):
        what = (
            f"Constrained parent from {record['geometry']}: freeze the distance between atoms "
            f"{pair[0]} and {pair[1]} at its starting value"
        )
    else:
        what = f"Unconstrained project minimum from {record['geometry']}"
    limits = record["limits"]
    return (
        f"Submit {record['input_name']} for node {record['node_label'] or record['node_id']} "
        f"(charge {record['charge']}, multiplicity {record['multiplicity']}) on "
        f"{record['server_id']} ({username}@{host}, port {port}), {record['nodes']} node, "
        f"{record['cores']} cores, {record['hours']} h. {what}. Job description "
        f"{record['job_file']} SHA-256 {record['job_sha256']}. Node request "
        f"{record['request_id']}: at most {limits['parent']} parents and {limits['child']} TS "
        f"children are started. {_seed_text(record['seed'])}. Directory {record['directory']}. "
        f"Prepared input SHA-256 {record['revision']}"
    )


def _summary(record: dict) -> str:
    if record.get("pilot") == _SAGA_NODE:
        return _node_summary(record)
    if record.get("pilot") in _SAGA_PILOTS:
        host, port, username = record["target"]
        summary = (
            f"Submit {record['input_name']} on {record['server_id']} ({username}@{host}, "
            f"port {port}), {record['nodes']} node, {record['cores']} cores, {record['hours']} h. "
            f"Directory {record['directory']}. Prepared input SHA-256 {record['revision']}"
        )
        if record["pilot"] == _SAGA_TS:
            review = record["review"]
            first, second = review["freeze_distance"]
            summary += (
                f". Checkpoint {review['checkpoint_name']} SHA-256 {record['checkpoint_sha256']}. "
                f"Parent {review['parent_stem']} (Slurm job {review['parent_job_id']}). "
                f"Mode {review['mode_index']} ({review['frequency_cm1']:g} cm⁻¹), "
                f"atoms {first} and {second}, bond projection {review['bond_projection']:g}, "
                f"alignment {review['alignment']:g}. "
                f"Parent output SHA-256 {review['output_sha256']}. "
                f"Review SHA-256 {record['review_sha256']}"
            )
        return summary
    if record.get("pilot") == _TS_PILOT:
        host, port, username = record["target"]
        mode = record["mode_evidence"]
        first, second = mode["freeze_distance"]
        return (
            f"Submit the TS optimization on {record['server_id']} ({username}@{host}, "
            f"port {port}), 1 node, {record['cores']} cores, {record['hours']} h. "
            f"Directory {record['directory']}. Parent attempt {record['parent_attempt']} "
            f"(Slurm job {record['parent_job_id']}). Mode {record['mode_index']} "
            f"({mode['frequency_cm1']:g} cm⁻¹), atoms {first} and {second}, "
            f"bond projection {mode['bond_projection']:g}"
            + (
                f", alignment {mode['alignment']:g}, bond share {mode['bond_share']:g}"
                if mode.get("bond_share") is not None
                else ""
            )
            + ". Parent output SHA-256 "
            f"{mode['output_sha256']}. "
            f"Checkpoint {record['checkpoint_name']} SHA-256 {record['checkpoint_sha256']}. "
            f"Prepared input SHA-256 {record['revision']}"
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
    server_id: str, request: dict, target: list, key: bytes | None, key_id=None, directory=None
) -> dict:
    try:
        if key is None:
            ts = request["operation"] in {"submit_ts", "ts_status"}
            node = request["operation"] in {"submit_node", "node_status"}
            op = "saga_node_job" if node else "saga_ts_job" if ts else "saga_minimum_job"
            response = await client.call(
                op,
                server=server_id,
                target=target,
                key_id=key_id,
                request=request,
                **({"directory": directory} if ts or node else {}),
                timeout=115,
            )
        else:
            envelope = {"request": request, "signature": job_pilot.signature(key, request)}
            response = await client.call(
                "job_pilot", server=server_id, target=target, envelope=envelope, timeout=100
            )
    except client.KeeperError as exc:
        detail = str(exc)
        if request["operation"] in {"submit", "submit_minimum", "submit_ts", "submit_node"}:
            detail += " Check the same prepared attempt after reconnecting."
        raise HTTPException(409, detail) from exc
    result = response.get("result")
    if not isinstance(result, dict):
        raise HTTPException(409, "The helper did not return a job result.")
    if result.get("attempt") != request["attempt"]:
        raise HTTPException(409, "The helper returned a different attempt.")
    if job_id := result.get("job_id"):
        if (
            not isinstance(job_id, str)
            or not re.fullmatch(_JOB_ID_PATTERN, job_id)
            or not isinstance(result.get("directory"), str)
        ):
            raise HTTPException(409, "The helper returned an invalid submission receipt.")
        result["follow_up"] = {
            "server_id": server_id,
            "job_id": job_id,
            "path": result["directory"],
        }
    return result


@router.post("/{server_id}/prepare")
async def prepare(server_id: str, body: PrepareIn, request: Request):
    _check(request, page_only=False)
    investigation = _investigation(request)
    info = investigation.info()
    if info is None or not info.uuid:
        raise HTTPException(409, "The current investigation has no ID for this preparation.")
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
        "folder": str(investigation.folder),
        "investigation_id": info.uuid,
        "server_id": server_id,
        "target": target,
        "key_id": hashlib.sha256(key).hexdigest(),
    }
    _store_record(attempt, record)
    return result


def _preoptimization(request: Request, server_id: str, parent_attempt: str) -> tuple[dict, bytes]:
    _check(request, page_only=False)
    investigation = _investigation(request)
    info = investigation.info()
    parent, key = _record(
        server_id,
        parent_attempt,
        investigation.folder,
        info.uuid if info else None,
        require_identity=True,
    )
    saga = parent.get("pilot") == _SAGA_NODE and parent.get("kind", "parent") == "parent"
    if not parent.get("freeze_distance") or not (
        saga or (key is not None and parent.get("pilot") is None)
    ):
        raise HTTPException(
            409, "Choose a constrained pre-optimization on the local pilot or a Saga node parent."
        )
    return parent, key


async def _saga_parent_evidence(
    server_id: str, attempt: str, parent: dict, folder: Path
) -> tuple[dict, str]:
    """D271b: copy the Saga parent's results through the scoped helper reads and inspect them
    as the local pilot does. The copies keep the remote modification times, which tie the
    output and checkpoint to the wrapper log of the recorded Slurm job."""
    status = await _call(
        server_id,
        {"operation": "node_status", "attempt": attempt, "revision": parent["revision"]},
        parent["target"],
        None,
        parent["key_id"],
        parent["directory"],
    )
    job_id = status.get("job_id")
    if status.get("state") != "submitted" or not job_id:
        raise HTTPException(409, "Choose a submitted Saga parent with a recorded Slurm receipt.")
    stem = parent["input_name"][:-4]
    submission = folder / "submission"
    submission.mkdir()
    names = [stem + suffix for suffix in (".log", ".out", ".fchk.gz")]
    try:
        reply = await client.call(
            "download",
            server=server_id,
            paths=[f"{parent['directory']}/{name}" for name in names],
            folder=str(submission),
            within=True,
            timeout=600,
        )
    except client.KeeperError as exc:
        raise HTTPException(409, str(exc)) from exc
    if reply.get("failed"):
        failure = reply["failed"][0]
        raise HTTPException(409, f"Could not copy {failure['path']}: {failure['reason']}")
    copied = {Path(item["local"]).name: item for item in reply.get("files", [])}
    if set(copied) != set(names):
        raise HTTPException(409, "The parent's log, output and checkpoint were not all copied.")
    for item in copied.values():
        local = Path(item["local"])
        if local.parent != submission or type(item.get("modified")) is not int:
            raise HTTPException(409, "The keeper returned an invalid copy of the parent files.")
        os.utime(local, (item["modified"], item["modified"]))
    try:
        inspection = preopt_modes.inspect_preoptimization(
            submission,
            stem,
            job_id,
            parent["freeze_distance"],
            submitted_input=parent["input"].encode(),
        )
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    return inspection, job_id


@router.get("/{server_id}/attempts/{parent_attempt}/modes")
async def inspect_preoptimization_modes(server_id: str, parent_attempt: str, request: Request):
    parent, key = _preoptimization(request, server_id, parent_attempt)
    await _no_arguments(request, "Only the server ID and parent attempt are accepted.")
    if parent.get("pilot") == _SAGA_NODE:
        with tempfile.TemporaryDirectory(prefix="chembook3d-parent-") as folder:
            inspection, job_id = await _saga_parent_evidence(
                server_id, parent_attempt, parent, Path(folder)
            )
        return _InspectedModes.model_validate(
            {
                "attempt": parent_attempt,
                "parent_job_id": job_id,
                **{k: inspection[k] for k in ("freeze_distance", "output_sha256", "modes")},
            }
        ).model_dump()
    result = await _call(
        server_id, {"operation": "inspect_modes", "attempt": parent_attempt}, parent["target"], key
    )
    try:
        inspection = _InspectedModes.model_validate(result)
    except ValidationError as exc:
        raise HTTPException(
            409, "The helper returned invalid pre-optimization mode evidence."
        ) from exc
    if inspection.freeze_distance != parent["freeze_distance"] or len(
        {mode.mode_index for mode in inspection.modes}
    ) != len(inspection.modes):
        raise HTTPException(409, "The mode evidence does not match the prepared pre-optimization.")
    return inspection.model_dump()


@router.post("/{server_id}/attempts/{parent_attempt}/prepare-ts")
async def prepare_ts_from_preoptimization(
    server_id: str, parent_attempt: str, body: PrepareTSIn, request: Request
):
    parent, key = _preoptimization(request, server_id, parent_attempt)
    if request.query_params:
        raise HTTPException(
            422, "Pass the mode index, output digest, cores and hours in the request body."
        )
    if parent.get("pilot") == _SAGA_NODE:
        return await _prepare_saga_child(server_id, parent_attempt, parent, body)
    attempt = secrets.token_hex(16)
    result = await _call(
        server_id,
        {
            "operation": "prepare_ts",
            "attempt": attempt,
            "parent_attempt": parent_attempt,
            **body.model_dump(),
        },
        parent["target"],
        key,
    )
    details = _ts_details(result)
    evidence = details["mode_evidence"]
    contents = result.get("input")
    if (
        result.get("state") != "prepared"
        or result.get("job_id") is not None
        or not isinstance(contents, str)
        or not contents
        or hashlib.sha256(contents.encode()).hexdigest() != details["revision"]
        or details["directory"] != f"{job_pilot.LOCAL_WORK}/{attempt}/submission"
        or details["parent_attempt"] != parent_attempt
        or details["checkpoint_name"] != job_pilot.ts_stem(parent["geometry"]) + ".fchk.gz"
        or any(details[field] != getattr(body, field) for field in ("cores", "hours", "mode_index"))
        or evidence["mode_index"] != body.mode_index
        or evidence["eligible"] is not True
        or evidence["freeze_distance"] != parent["freeze_distance"]
        or evidence["output_sha256"] != body.output_sha256
    ):
        raise HTTPException(409, "The helper did not return a verified TS preparation.")
    record = {
        **{k: parent[k] for k in ("folder", "investigation_id", "server_id", "target", "key_id")},
        **details,
        "pilot": _TS_PILOT,
        "input": contents,
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
        if record.get("pilot") == _TS_PILOT:
            payload["checkpoint_sha256"] = record["checkpoint_sha256"]
    if record.get("pilot") == _SAGA_NODE:
        payload["revision"] = record["revision"]
        if operation == "submit":
            payload.update(operation="submit_node", **saga_access.RESOURCES)
        else:
            payload["operation"] = "node_status"
    elif record.get("pilot") in _SAGA_PILOTS:
        ts = record["pilot"] == _SAGA_TS
        if operation == "submit":
            payload.update(operation="submit_ts" if ts else "submit_minimum", nodes=record["nodes"])
        else:
            payload.update(
                operation="ts_status" if ts else "minimum_status", revision=record["revision"]
            )
        if ts:
            payload.update({k: record[k] for k in ("review_sha256", "checkpoint_sha256")})
    result = await _call(
        server_id, payload, record["target"], key, record["key_id"], record["directory"]
    )
    if record.get("pilot") == _SAGA_TS and any(
        result.get(k) != record[k]
        for k in (
            "directory",
            "revision",
            "review_sha256",
            "checkpoint_sha256",
            *saga_access.RESOURCES,
        )
    ):
        raise HTTPException(409, "The returned Saga TS attempt no longer matches its preparation.")
    if record.get("pilot") == _SAGA_NODE and (
        result.get("request") != record["request_id"]
        or any(
            result.get(k) != record[k] for k in ("directory", "revision", *saga_access.RESOURCES)
        )
    ):
        raise HTTPException(
            409, "The returned Saga node attempt no longer matches its preparation."
        )
    if record.get("pilot") == _TS_PILOT and any(
        record[k] != v for k, v in _ts_details(result).items()
    ):
        raise HTTPException(409, "The returned TS attempt no longer matches its preparation.")
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


async def _prepare_saga_child(server_id: str, parent_attempt: str, parent: dict, body):
    """D271b: the unconstrained TS child of a Saga node parent, from a mode the agent chose."""
    if (body.cores, body.hours) != (saga_access.RESOURCES["cores"], saga_access.RESOURCES["hours"]):
        raise HTTPException(422, "Saga node calculations use 1 node, 40 cores and 24 hours.")
    with tempfile.TemporaryDirectory(prefix="chembook3d-parent-") as folder:
        inspection, job_id = await _saga_parent_evidence(
            server_id, parent_attempt, parent, Path(folder)
        )
        if body.output_sha256 != inspection["output_sha256"]:
            raise HTTPException(409, "The parent output changed. Inspect the modes again.")
        mode = next((m for m in inspection["modes"] if m["mode_index"] == body.mode_index), None)
        if not mode or not mode["eligible"]:
            raise HTTPException(
                409,
                "Select an imaginary mode along the frozen bond: alignment at least 0.5 and "
                "bond share at least 0.25 (D269).",
            )
        stem = parent["input_name"][:-4]
        _atoms, _pair, charge_spin, settings = checkpoint_ts.parent_geometry(parent["input"], stem)
        try:
            checkpoint = checkpoint_ts.checkpoint_bytes(
                Path(folder) / "submission" / (stem + ".fchk.gz"), inspection, charge_spin
            )
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
    evidence = {**mode, "output_sha256": inspection["output_sha256"]}
    evidence["freeze_distance"] = inspection["freeze_distance"]
    attempt = secrets.token_hex(16)
    wire = {
        "request": parent["request_id"],
        "attempt": attempt,
        "parent_attempt": parent_attempt,
        "charge": parent["charge"],
        "multiplicity": parent["multiplicity"],
        "mode_index": body.mode_index,
        "output_sha256": body.output_sha256,
        "checkpoint_sha256": hashlib.sha256(checkpoint).hexdigest(),
    }
    try:
        reply = await client.call(
            "saga_prepare_node",
            server=server_id,
            target=parent["target"],
            request=wire,
            timeout=115,
        )
    except client.KeeperError as exc:
        raise HTTPException(409, str(exc)) from exc
    child_stem = stem[:77] + "-ts"
    try:
        result = dict(reply["result"])
        key_id = result.pop("key_id")
        contents = result["input"]
        if (
            result.keys()
            != saga_access.NODE_CHILD_FIELDS | {"submission_enabled", *saga_access.RESOURCES}
            or result["submission_enabled"] is not True
            or reply.get("target") != parent["target"]
            or key_id != parent["key_id"]
            or any(result[k] != v for k, v in wire.items())
            or result["parent_job_id"] != job_id
            or result["input_name"] != child_stem + ".inp"
            or result["checkpoint_name"] != child_stem + ".fchk.gz"
            or result["directory"]
            != parent["directory"].removesuffix(f"node-{parent_attempt}/submission")
            + f"node-{attempt}"
            or hashlib.sha256(contents.encode()).hexdigest() != result["revision"]
        ):
            raise ValueError
        checkpoint_ts.verify_ts_input(contents, child_stem, charge_spin, settings)
    except ValueError as exc:
        raise HTTPException(409, f"The keeper returned an invalid Saga TS child: {exc}") from exc
    except (AttributeError, KeyError, TypeError) as exc:
        raise HTTPException(409, "The keeper returned an invalid Saga TS child.") from exc
    record = {
        **{
            k: parent[k]
            for k in (
                "server_id",
                "target",
                "investigation_id",
                "folder",
                "node_id",
                "node_label",
                "charge",
                "multiplicity",
                "key_id",
                "request_id",
                "limits",
                "seed",
            )
        },
        "pilot": _SAGA_NODE,
        "kind": "child",
        "directory": result["directory"] + "/submission",
        "parent_attempt": parent_attempt,
        "parent_job_id": job_id,
        "mode_index": body.mode_index,
        "mode_evidence": _SelectedMode.model_validate(evidence).model_dump(),
        **{
            k: result[k]
            for k in (
                "input_name",
                "revision",
                "input",
                "job_file",
                "job_sha256",
                "checkpoint_name",
                "checkpoint_sha256",
                "output_sha256",
                *saga_access.RESOURCES,
            )
        },
    }
    _store_record(attempt, record)
    return {**result, "mode_evidence": record["mode_evidence"], "node_id": parent["node_id"]}


class SagaImportIn(BaseModel):
    """D271e: the ordinary import choices for a Saga node output; the node is the request's."""

    model_config = ConfigDict(extra="forbid", strict=True)
    preview: bool = False
    step: int | None = Field(default=None, ge=1)
    # D271e: into node X, so the TS child continues the parent's geometry there by default.
    create_derived: bool = False
    beside: bool | None = None
    duplicate_action: Literal["attach", "new"] | None = None
    duplicate_node_id: str | None = Field(default=None, max_length=32)
    basis_names: dict[str, str] = Field(default_factory=dict)
    dispersion_names: dict[str, str] = Field(default_factory=dict)


_importing = threading.Lock()  # two calls for one attempt import it once


def _import_marker(attempt: str) -> Path:
    if not job_pilot.ATTEMPT.fullmatch(attempt):
        raise HTTPException(422, "Invalid prepared attempt ID.")
    return servers.folder() / "saga-node-imports" / f"{int(attempt, 16):032x}.json"


def _imported(db, attempt: str) -> list[str]:
    """The calculations an earlier import of this attempt made that are still there."""
    try:
        marker = json.loads(_import_marker(attempt).read_text("utf-8"))
    except FileNotFoundError:
        return []
    return [c for c in marker["calculation_ids"] if db.get(Calculation, c) is not None]


async def _saga_finished_output(
    server_id: str, attempt: str, record: dict, folder: Path
) -> tuple[str, Path]:
    """D271e: copy a finished attempt's wrapper log and output through the scoped helper
    reads. The log must name this Slurm job, report it completed with a normally terminated
    output copied home, and the output must date from the run."""
    status = await _call(
        server_id,
        {"operation": "node_status", "attempt": attempt, "revision": record["revision"]},
        record["target"],
        None,
        record["key_id"],
        record["directory"],
    )
    job_id = status.get("job_id")
    if (
        status.get("state") != "submitted"
        or not job_id
        or status.get("request") != record["request_id"]
        or status.get("directory") != record["directory"]
    ):
        raise HTTPException(409, "Choose a submitted Saga attempt with a recorded Slurm receipt.")
    stem = record["input_name"][:-4]
    submission = folder / "submission"
    submission.mkdir()
    names = [stem + ".log", stem + ".out"]
    try:
        reply = await client.call(
            "download",
            server=server_id,
            paths=[f"{record['directory']}/{name}" for name in names],
            folder=str(submission),
            within=True,
            timeout=600,
        )
    except client.KeeperError as exc:
        raise HTTPException(409, str(exc)) from exc
    if reply.get("failed"):
        failure = reply["failed"][0]
        raise HTTPException(409, f"Could not copy {failure['path']}: {failure['reason']}")
    copied = {Path(item["local"]).name: item for item in reply.get("files", [])}
    if set(copied) != set(names):
        raise HTTPException(409, "The wrapper log and output were not both copied.")
    for item in copied.values():
        local = Path(item["local"])
        if local.parent != submission or type(item.get("modified")) is not int:
            raise HTTPException(409, "The keeper returned an invalid copy of the attempt files.")
        os.utime(local, (item["modified"], item["modified"]))
    log_path, output = submission / names[0], submission / names[1]
    try:
        contents, log_meta = checkpoint_ts.read_snapshot(log_path, 1024**2)
        log = contents.decode("utf-8").replace("\r\n", "\n")
        start, _ = preopt_modes.completed_copyback(log, log_path, stem, job_id)
        _, meta = checkpoint_ts.read_snapshot(output, 64 * 1024**2)
        if not start <= meta.st_mtime <= log_meta.st_mtime:
            raise ValueError("Cannot attribute the copied output's modification time to this job.")
    except (UnicodeError, OSError) as exc:
        raise HTTPException(409, "The wrapper log or output is missing or unreadable.") from exc
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    return job_id, output


def _provenance(attempt: str, job_id: str, record: dict) -> str:
    """D271e: what the imported calculations record about where they came from."""
    if record.get("kind") == "child":
        mode = record["mode_evidence"]
        what = (
            f"TS child attempt {attempt} (Slurm job {job_id}) of parent attempt "
            f"{record['parent_attempt']} (Slurm job {record['parent_job_id']}), mode "
            f"{record['mode_index']} ({mode['frequency_cm1']:g} cm⁻¹) along atoms "
            f"{mode['freeze_distance'][0]} and {mode['freeze_distance'][1]}"
        )
        seed = _seed_text(record["seed"], "Parent seed")
    else:
        what = f"Parent attempt {attempt} (Slurm job {job_id})"
        if pair := record.get("freeze_distance"):
            what += f", distance between atoms {pair[0]} and {pair[1]} frozen"
        seed = _seed_text(record["seed"])
    return (
        f"Saga node calculation (D271). {what}. Node request {record['request_id']}. {seed}. "
        f"Job description {record['job_file']} SHA-256 {record['job_sha256']}. Not yet checked "
        "as a verified TS."
    )


@router.post("/{server_id}/attempts/{attempt}/import")
async def import_saga_node_result(
    server_id: str, attempt: str, request: Request, body: SagaImportIn | None = None
):
    """D271e: import a finished Saga node parent or TS child into the request's node as an
    ordinary calculation, with its provenance in the calculations' notes. `preview` answers
    with the ordinary import plan and writes nothing."""
    _check(request, page_only=False)
    body = body or SagaImportIn()
    investigation = _investigation(request)
    info = investigation.info()
    record, _ = _record(
        server_id,
        attempt,
        investigation.folder,
        info.uuid if info else None,
        require_identity=True,
    )
    if record.get("pilot") != _SAGA_NODE:
        raise HTTPException(409, "Only a Saga node parent or TS child imports here (D271e).")
    with investigation.sessions.begin() as db:
        if _imported(db, attempt):
            raise HTTPException(409, "This attempt's output is imported already.")
        if db.get(Node, record["node_id"]) is None:
            raise HTTPException(409, "The node of this request is gone.")
    server = _saved(server_id)
    with tempfile.TemporaryDirectory(prefix="chembook3d-saga-node-") as folder:
        job_id, output = await _saga_finished_output(server_id, attempt, record, Path(folder))
        provenance = _provenance(attempt, job_id, record)
        options = routes._options(
            routes.ImportOptionsIn(
                target_node_id=record["node_id"],
                **body.model_dump(exclude={"preview"}),
            )
        )

        def stage_and_import():
            staging = imports.Staging()
            try:
                staged = staging.add(
                    output.read_bytes(),
                    output.name,
                    servers.origin_path(server, f"{record['directory']}/{output.name}"),
                    beside=output.parent,
                    device=server.name,
                )
                if body.preview:
                    with investigation.sessions.begin() as db:
                        found = irc_ends.plan(db, investigation.folder, staged, options)
                    found.pop("token", None)  # this route stages again; commit_import is not used
                    return found
                with _importing, investigation.sessions.begin() as db:
                    if _imported(db, attempt):
                        raise HTTPException(409, "This attempt's output is imported already.")
                    routed, _ = irc_ends.routed(db, investigation.folder, staged, options)
                    committed = imports.commit(db, investigation.folder, staged, routed)
                    for calculation_id in committed.calculation_ids:
                        calculation = db.get(Calculation, calculation_id)
                        calculation.notes = "\n\n".join(
                            n for n in (calculation.notes, provenance) if n
                        )
                    db.flush()
                    marker = _import_marker(attempt)
                    marker.parent.mkdir(exist_ok=True, parents=True)
                    marker.write_text(
                        json.dumps({"calculation_ids": list(committed.calculation_ids)}),
                        encoding="utf-8",
                    )
                return {
                    "attempt": attempt,
                    "job_id": job_id,
                    "node_id": committed.node_id,
                    "derived_node_id": committed.derived_node_id,
                    "calculation_ids": list(committed.calculation_ids),
                    "provenance": provenance,
                }
            except imports.ImportBlocked as exc:
                raise HTTPException(422, {"blockers": exc.blockers, "message": str(exc)}) from exc
            except imports.ImportFailed as exc:
                raise HTTPException(422, str(exc)) from exc
            finally:
                staging.clear()

        return await asyncio.to_thread(stage_and_import)
