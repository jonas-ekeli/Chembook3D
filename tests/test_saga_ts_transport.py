"""Bind the enrolled Saga TS archive and its receipt to the approved target (D266)."""

import asyncio
import hashlib
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, call

import asyncssh
import pytest

from chembook3d.remote import job_pilot, keeper, saga_access, saga_prepare

ROOT = "/cluster/projects/project/operator/chembook3d-pilot"
TARGET = ["saga.sigma2.no", 22, "operator"]


@pytest.fixture
def archive():
    preview = "%chk=child\n# opt=(ts,readfc)\n"
    revision = hashlib.sha256(preview.encode()).hexdigest()
    review = {
        "format": "chembook3d-saga-ts-v1",
        "parent_job_id": "123",
        "parent_stem": "parent",
        "input_name": "child.inp",
        "checkpoint_name": "child.fchk.gz",
        "input_sha256": revision,
        "checkpoint_sha256": "b" * 64,
        "output_sha256": "c" * 64,
        "freeze_distance": [1, 3],
        "mode_index": 2,
        "frequency_cm1": -507.1,
        "bond_projection": -1.0,
        "alignment": 0.91,
    }
    policy = saga_access.TSSubmissionPolicy(
        "a" * 32,
        revision,
        b"k" * 32,
        review["checkpoint_sha256"],
        hashlib.sha256(saga_prepare.review_bytes(review)).hexdigest(),
    )
    result = {
        **policy.pins(),
        "directory": ROOT + "/ts-" + policy.attempt,
        "input": preview,
        "input_name": "child.inp",
        "review": review,
        "state": "prepared",
    }
    return policy, result


def responding(result):
    connection = AsyncMock()
    connection.run.return_value = SimpleNamespace(
        exit_status=0, stdout=json.dumps({"ok": True, "result": result})
    )
    return connection


@pytest.mark.parametrize(
    "change",
    [
        {},
        {"input_name": "../child.inp"},
        {"input": "different"},
        {"checkpoint_sha256": "1" * 64},
        {"directory": "/other/ts-" + "a" * 32},
        {"review_sha256": "2" * 64},
        {"key": "extra"},
        {"review": {"parent_job_id": "999"}},
        {"state": "submitted"},
    ],
)
def test_prepare_validates_remote_preview_and_uses_only_enrolled_pins(archive, change):
    policy, result = archive
    if "review" in change:
        change = {"review": result["review"] | change["review"]}
    connection = responding(result | change)
    reply = asyncio.run(saga_access.prepare_ts(connection, ROOT, policy))
    assert reply["ok"] is (not change)
    if reply["ok"]:
        assert reply["result"] == result
    assert connection.run.await_args.args == (saga_access.COMMAND,)
    assert json.loads(connection.run.await_args.kwargs["input"]) == {
        "operation": "prepare_ts",
        **policy.pins(),
    }


@pytest.mark.parametrize(
    "change",
    [
        {"operation": "submit_minimum"},
        {"attempt": "f" * 32},
        {"revision": "1" * 64},
        {"checkpoint_sha256": "1" * 64},
        {"review_sha256": "2" * 64},
        {"nodes": True},
        {"signature": "caller supplied"},
        {"checkpoint_name": "another.fchk.gz"},
    ],
)
def test_signing_requires_exact_ts_pins_and_resources(archive, change):
    policy, _ = archive
    connection = AsyncMock()
    request = {"operation": "submit_ts", **policy.pins(), **saga_access.RESOURCES, **change}
    reply = asyncio.run(saga_access.enrolled_job(connection, ROOT, policy, request))
    assert not reply["ok"]
    connection.run.assert_not_awaited()


@pytest.mark.parametrize("operation", ["submit_ts", "ts_status"])
@pytest.mark.parametrize(
    "change",
    [
        {},
        {"review_sha256": "2" * 64},
        {"checkpoint_sha256": "1" * 64},
        {"directory": ROOT},
        {"job_id": None},
    ],
)
def test_receipt_is_bound_to_review_and_confirmed_ts_directory(archive, operation, change):
    policy, prepared = archive
    request = {"operation": operation, **policy.pins()}
    if operation == "submit_ts":
        request.update(saga_access.RESOURCES)
    connection = responding(
        {
            **policy.pins(),
            **saga_access.RESOURCES,
            "directory": prepared["directory"] + "/submission",
            "state": "submitted",
            "job_id": "241",
            "detail": None,
            **change,
        }
    )
    reply = asyncio.run(saga_access.enrolled_job(connection, ROOT, policy, request))
    assert reply["ok"] is (not change)
    wire = json.loads(connection.run.await_args.kwargs["input"])
    if operation == "submit_ts":
        assert wire.pop("signature") == job_pilot.signature(policy.key, request)
    assert wire == request


@pytest.mark.parametrize(
    "change",
    [
        {"target": [TARGET[0], 2222, TARGET[2]]},
        {"key_id": "0" * 64},
        {"directory": ROOT + "/unreviewed/submission"},
        {"path": ROOT},
    ],
)
def test_keeper_rejects_changed_enrollment_before_submission(
    tmp_path, monkeypatch, archive, change
):
    policy, prepared = archive
    worker = keeper.Keeper(tmp_path)
    connection = AsyncMock()
    worker.sessions["saga"] = keeper.Session(
        dict(zip(("host", "port", "username"), TARGET, strict=True)),
        connection,
        since=0.0,
        used=0.0,
        saga_work=ROOT,
    )
    monkeypatch.setattr(keeper, "refresh_saga_scope", AsyncMock())
    monkeypatch.setattr(saga_access, "submission_policy", AsyncMock(return_value=policy))
    msg = {
        "op": "saga_ts_job",
        "token": worker.token,
        "server": "saga",
        "target": TARGET,
        "key_id": policy.key_id,
        "directory": prepared["directory"] + "/submission",
        "request": {"operation": "submit_ts", **policy.pins(), **saga_access.RESOURCES},
        **change,
    }
    reply = asyncio.run(worker.dispatch(msg["op"], msg))
    assert not reply["ok"]
    connection.run.assert_not_awaited()


def test_missing_minimum_markers_still_protect_the_separate_ts_key():
    sftp = AsyncMock()
    sftp.lstat.side_effect = [
        asyncssh.SFTPNoSuchFile("missing"),
        asyncssh.SFTPNoSuchFile("missing"),
        SimpleNamespace(),
    ]
    with pytest.raises(ValueError, match="Missing preparation enrollment"):
        asyncio.run(saga_access.enrolled_root(sftp, "operator"))
    assert sftp.lstat.await_args_list == [
        call(saga_access.CONTROL + marker)
        for marker in (saga_prepare.MARKER, saga_prepare.SUBMIT_MARKER, saga_prepare.TS_MARKER)
    ]
    sftp.open.assert_not_called()


def test_group_readable_ts_marker_refused_before_reading_signing_key():
    sftp = AsyncMock()
    sftp.lstat.return_value = SimpleNamespace(
        type=asyncssh.FILEXFER_TYPE_REGULAR, size=600, permissions=0o640
    )
    with pytest.raises(ValueError, match="private to its owner"):
        asyncio.run(saga_access.submission_policy(sftp, ts=True))
    sftp.open.assert_not_called()


def _retired_record(policy) -> dict:
    return {
        "work_root": ROOT,
        "minimum_attempt": "1" * 32,
        "minimum_sha256": "3" * 64,
        **policy.pins(),
        **saga_access.RESOURCES,
        "wrapper": "/home/operator/wrapper",
        "wrapper_sha256": "4" * 64,
        "key_id": policy.key_id,
        "retired": True,
    }


def _sftp(record: dict, permissions=0o600):
    data = json.dumps(record).encode()
    sftp = AsyncMock()
    sftp.lstat.return_value = SimpleNamespace(
        type=asyncssh.FILEXFER_TYPE_REGULAR, size=len(data), permissions=permissions
    )
    stream = AsyncMock()
    stream.read.return_value = data
    sftp.open = MagicMock()
    sftp.open.return_value.__aenter__.return_value = stream
    return sftp


def test_retired_ts_policy_has_pins_and_key_id_but_no_key(archive):
    policy, _ = archive
    sftp = _sftp(_retired_record(policy))
    retired = asyncio.run(saga_access.retired_ts_policy(sftp, policy.attempt))
    assert retired.pins() == policy.pins() and retired.key_id == policy.key_id
    assert not hasattr(retired, "key")
    assert sftp.lstat.await_args == call(
        saga_access.CONTROL + "ts-" + policy.attempt + ".retired.json"
    )


@pytest.mark.parametrize(
    "change",
    [
        {"key": "6b" * 32},
        {"retired": False},
        {"attempt": "f" * 32},
        {"key_id": "short"},
        {"hours": 25},
        {"review_sha256": None},
    ],
)
def test_retired_ts_policy_must_be_the_exact_keyless_record(archive, change):
    policy, _ = archive
    sftp = _sftp(_retired_record(policy) | change)
    with pytest.raises(ValueError, match="retired"):
        asyncio.run(saga_access.retired_ts_policy(sftp, policy.attempt))


def test_retired_ts_policy_checks_attempt_privacy_and_absence(archive):
    policy, _ = archive
    sftp = _sftp(_retired_record(policy), permissions=0o640)
    with pytest.raises(ValueError, match="private to its owner"):
        asyncio.run(saga_access.retired_ts_policy(sftp, policy.attempt))
    sftp.open.assert_not_called()
    for attempt in ("../" + "a" * 29, "A" * 32, None):
        with pytest.raises(ValueError, match="attempt"):
            asyncio.run(saga_access.retired_ts_policy(sftp, attempt))
    sftp.lstat.side_effect = asyncssh.SFTPNoSuchFile("missing")
    assert asyncio.run(saga_access.retired_ts_policy(sftp, policy.attempt)) is None


def _retired(policy):
    return saga_access.RetiredTSPolicy(
        policy.attempt,
        policy.revision,
        policy.checkpoint_sha256,
        policy.review_sha256,
        policy.key_id,
    )


def test_retired_policy_reads_status_and_cannot_sign(archive):
    policy, prepared = archive
    retired = _retired(policy)
    submit = {"operation": "submit_ts", **policy.pins(), **saga_access.RESOURCES}
    connection = AsyncMock()
    reply = asyncio.run(saga_access.enrolled_job(connection, ROOT, retired, submit))
    assert not reply["ok"] and "retired" in reply["error"]
    connection.run.assert_not_awaited()
    status = {"operation": "ts_status", **policy.pins()}
    connection = responding(
        {
            **policy.pins(),
            **saga_access.RESOURCES,
            "directory": prepared["directory"] + "/submission",
            "state": "rejected",
            "job_id": None,
            "detail": "Retired by the operator before submission.",
        }
    )
    reply = asyncio.run(saga_access.enrolled_job(connection, ROOT, retired, status))
    assert reply["ok"] and reply["result"]["state"] == "rejected"
    assert json.loads(connection.run.await_args.kwargs["input"]) == status


@pytest.mark.parametrize(
    "change",
    [
        {},
        {"key_id": "0" * 64},
        {"directory": ROOT + "/ts-" + "c" * 32 + "/submission"},
        {"operation": "submit_ts"},
        {"missing": True},
    ],
)
def test_keeper_sends_only_unsigned_status_for_a_retired_attempt(
    tmp_path, monkeypatch, archive, change
):
    old, prepared = archive
    current = saga_access.TSSubmissionPolicy(
        "c" * 32, old.revision, b"n" * 32, old.checkpoint_sha256, "5" * 64
    )
    worker = keeper.Keeper(tmp_path)
    connection = responding(
        {
            **old.pins(),
            **saga_access.RESOURCES,
            "directory": prepared["directory"] + "/submission",
            "state": "submitted",
            "job_id": "241",
            "detail": None,
        }
    )
    worker.sessions["saga"] = keeper.Session(
        dict(zip(("host", "port", "username"), TARGET, strict=True)),
        connection,
        since=0.0,
        used=0.0,
        saga_work=ROOT,
    )
    lookup = AsyncMock(return_value=None if change.get("missing") else _retired(old))
    monkeypatch.setattr(keeper, "refresh_saga_scope", AsyncMock())
    monkeypatch.setattr(saga_access, "submission_policy", AsyncMock(return_value=current))
    monkeypatch.setattr(saga_access, "retired_ts_policy", lookup)
    monkeypatch.setattr(keeper.Session, "files", AsyncMock())
    operation = change.get("operation", "ts_status")
    request = {"operation": operation, **old.pins()}
    if operation == "submit_ts":
        request.update(saga_access.RESOURCES)
    msg = {
        "op": "saga_ts_job",
        "token": worker.token,
        "server": "saga",
        "target": TARGET,
        "key_id": change.get("key_id", old.key_id),
        "directory": change.get("directory", prepared["directory"] + "/submission"),
        "request": request,
    }
    reply = asyncio.run(worker.dispatch(msg["op"], msg))
    assert reply["ok"] is (not change), reply
    if not change:
        assert lookup.await_args.args[1] == old.attempt
        assert json.loads(connection.run.await_args.kwargs["input"]) == request
        assert reply["result"]["job_id"] == "241"
    else:
        connection.run.assert_not_awaited()
    if operation == "submit_ts":
        lookup.assert_not_awaited()
