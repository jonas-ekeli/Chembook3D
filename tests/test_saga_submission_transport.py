"""D266: bind the forwarded approval and its receipt to the same Saga attempt."""

import asyncio
import hashlib
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import asyncssh
import pytest

from chembook3d.remote import saga_access


@pytest.mark.parametrize(
    "change",
    [{"attempt": "b" * 32}, {"nodes": True}, {"hours": 25}, {"signature": "extra"}],
)
def test_keeper_does_not_sign_a_different_submission(change):
    policy = saga_access.SubmissionPolicy("a" * 32, "c" * 64, b"k" * 32)
    request = {
        "operation": "submit_minimum",
        "attempt": policy.attempt,
        "revision": policy.revision,
        **saga_access.RESOURCES,
        **change,
    }
    connection = AsyncMock()
    reply = asyncio.run(saga_access.minimum_job(connection, "/pilot", policy, request))
    assert not reply["ok"]
    connection.run.assert_not_awaited()


@pytest.mark.parametrize(
    "change",
    [
        {"attempt": "b" * 32},
        {"directory": "/other"},
        {"job_id": None},
        {"job_id": "invalid"},
        {"job_id": "0"},
        {"state": "uncertain"},
        {"cores": 39},
    ],
)
def test_invalid_helper_receipt_cannot_establish_submission(change):
    policy = saga_access.SubmissionPolicy("a" * 32, "c" * 64, b"k" * 32)
    request = {
        "operation": "minimum_status",
        "attempt": policy.attempt,
        "revision": policy.revision,
    }
    result = {
        "attempt": policy.attempt,
        "revision": policy.revision,
        "state": "submitted",
        "directory": f"/pilot/prepare-{policy.attempt}/submission",
        "job_id": "12345",
        "detail": None,
        **saga_access.RESOURCES,
        **change,
    }
    connection = AsyncMock()
    connection.run.return_value = SimpleNamespace(
        exit_status=0, stdout=json.dumps({"ok": True, "result": result})
    )
    reply = asyncio.run(saga_access.minimum_job(connection, "/pilot", policy, request))
    assert not reply["ok"]
    assert "invalid submission result" in reply["error"]
    assert json.loads(connection.run.await_args.kwargs["input"]) == request


def test_missing_preparation_policy_cannot_expose_the_submission_key():
    sftp = AsyncMock()
    sftp.lstat.side_effect = [asyncssh.SFTPNoSuchFile("missing"), SimpleNamespace()]
    with pytest.raises(ValueError, match="Missing preparation enrollment"):
        asyncio.run(saga_access.enrolled_root(sftp, "operator"))
    sftp.open.assert_not_called()


def test_raw_preparation_cannot_advertise_submission():
    sha = hashlib.sha256(b"input").hexdigest()
    result = {
        "state": "prepared",
        "directory": "/pilot/prepare-" + "a" * 32,
        "input_name": "minimum.inp",
        "input": "input",
        "sha256": sha,
        "reference_sha256": sha,
        "submission_enabled": True,
    }
    connection = AsyncMock()
    connection.run.return_value = SimpleNamespace(
        exit_status=0, stdout=json.dumps({"ok": True, "result": result})
    )
    reply = asyncio.run(saga_access.prepare(connection, "/pilot"))
    assert not reply["ok"]
    assert "invalid preparation result" in reply["error"]


def test_keeper_refuses_group_readable_submission_key():
    sftp = AsyncMock()
    sftp.lstat.return_value = SimpleNamespace(
        type=asyncssh.FILEXFER_TYPE_REGULAR, size=240, permissions=0o640
    )
    with pytest.raises(ValueError, match="private to its owner"):
        asyncio.run(saga_access.submission_policy(sftp))
    sftp.open.assert_not_called()
