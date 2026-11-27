"""Bind the enrolled Saga TS archive and its receipt to the approved target (D266)."""

import asyncio
import hashlib
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, call

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
