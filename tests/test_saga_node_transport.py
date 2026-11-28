"""D271: the keeper binds a node parent to its request, state and enrolled job (T-SSH-42)."""

import asyncio
import hashlib
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import asyncssh
import pytest

from chembook3d.remote import job_pilot, keeper, saga_access, saga_prepare
from tests.hpc_pilot_runner import linked_minimum_input

ROOT = "/cluster/projects/project/operator/chembook3d-pilot"
TARGET = ["saga.sigma2.no", 22, "operator"]
POLICY = saga_access.NodePolicy(
    {
        "minimum": {"name": "min-project.jd", "sha256": "1" * 64},
        "ts": {"name": "ts-project.jd", "sha256": "2" * 64},
    },
    b"k" * 32,
)
REQUEST = {
    "request": "3" * 32,
    "attempt": "4" * 32,
    "geometry": "guess.xyz",
    "charge": 0,
    "multiplicity": 1,
    "freeze_distance": [1, 3],
}


def _preview(request=REQUEST, state="0 1"):
    text = linked_minimum_input("guess", ["O 0 0 0", "H 0 0 1", "H 0 1 0"]).replace("0 1", state)
    text = saga_prepare.freeze_distance(text, request["freeze_distance"])
    return {
        "kind": "parent",
        **request,
        "job_file": "min-project.jd",
        "job_sha256": "1" * 64,
        "generator_sha256": "5" * 64,
        "input_name": "guess.inp",
        "revision": hashlib.sha256(text.encode()).hexdigest(),
        "xyz_sha256": "6" * 64,
        "state": "prepared",
        "directory": ROOT + "/node-" + request["attempt"],
        "input": text,
        "limits": {"parent": 2, "child": 2},
    }


def responding(result):
    connection = AsyncMock()
    connection.run.return_value = SimpleNamespace(
        exit_status=0, stdout=json.dumps({"ok": True, "result": result})
    )
    return connection


def _rehash(result):
    return {**result, "revision": hashlib.sha256(result["input"].encode()).hexdigest()}


@pytest.mark.parametrize(
    "change",
    [
        {},
        {"charge": 1},
        {"attempt": "9" * 32},
        {"freeze_distance": [1, 2]},
        {"job_file": "other.jd"},
        {"job_sha256": "0" * 64},
        {"directory": ROOT + "/node-" + "9" * 32},
        {"input_name": "other.inp"},
        {"revision": "0" * 64},
        {"limits": {"parent": 9, "child": 2}},
        {"kind": "child"},
        {"state": "submitted"},
        {"key": "extra"},
        {"rehash": lambda r: r["input"].replace("\n0 1\n", "\n1 2\n", 1)},
        {"rehash": lambda r: r["input"].replace("B 1 3 F", "B 1 2 F")},
        {"rehash": lambda r: r["input"] + "B 2 3 F\n"},
    ],
)
def test_preview_must_echo_the_request_and_carry_the_node_state(change):
    result = _preview()
    if "rehash" in change:
        result = _rehash({**result, "input": change["rehash"](result)})
    else:
        result = {**result, **change}
    connection = responding(result)
    reply = asyncio.run(saga_access.prepare_node(connection, ROOT, POLICY, REQUEST))
    assert reply["ok"] is (not change), reply
    assert json.loads(connection.run.await_args.kwargs["input"]) == {
        "operation": "prepare_node",
        **REQUEST,
    }


def test_unconstrained_preview_has_no_frozen_distance():
    request = {k: v for k, v in REQUEST.items() if k != "freeze_distance"}
    text = linked_minimum_input("guess", ["O 0 0 0", "H 0 0 1", "H 0 1 0"])
    result = _rehash({**_preview(), "freeze_distance": None, "input": text})
    reply = asyncio.run(saga_access.prepare_node(responding(result), ROOT, POLICY, request))
    assert reply["ok"], reply
    frozen = _rehash({**result, "input": text.replace("Opt=Tight", "Opt=ModRedundant") + "B 1 2 F"})
    reply = asyncio.run(saga_access.prepare_node(responding(frozen), ROOT, POLICY, request))
    assert not reply["ok"]


def _job_reply(**change):
    return {
        "request": REQUEST["request"],
        "attempt": REQUEST["attempt"],
        "revision": "7" * 64,
        **saga_access.RESOURCES,
        "directory": ROOT + "/node-" + REQUEST["attempt"] + "/submission",
        "state": "submitted",
        "job_id": "241",
        "detail": None,
        **change,
    }


@pytest.mark.parametrize(
    "change",
    [
        {},
        {"directory": ROOT + "/node-" + "9" * 32 + "/submission"},
        {"revision": "0" * 64},
        {"request": "bad"},
        {"cores": 80},
        {"job_id": None},
        {"state": "prepared"},
    ],
)
def test_submission_is_signed_and_its_receipt_bound(change):
    request = {
        "operation": "submit_node",
        "attempt": REQUEST["attempt"],
        "revision": "7" * 64,
        **saga_access.RESOURCES,
    }
    connection = responding(_job_reply(**change))
    reply = asyncio.run(saga_access.node_job(connection, ROOT, POLICY, request))
    assert reply["ok"] is (not change), reply
    wire = json.loads(connection.run.await_args.kwargs["input"])
    assert wire.pop("signature") == job_pilot.signature(POLICY.key, request)
    assert wire == request


@pytest.mark.parametrize(
    "change",
    [
        {"operation": "submit_ts"},
        {"attempt": "../" + "a" * 29},
        {"revision": "short"},
        {"nodes": 2},
        {"signature": "caller supplied"},
    ],
)
def test_node_job_refuses_other_requests_before_running(change):
    request = {
        "operation": "submit_node",
        "attempt": REQUEST["attempt"],
        "revision": "7" * 64,
        **saga_access.RESOURCES,
        **change,
    }
    connection = AsyncMock()
    reply = asyncio.run(saga_access.node_job(connection, ROOT, POLICY, request))
    assert not reply["ok"]
    connection.run.assert_not_awaited()


def _sftp(record, permissions=0o600):
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


def _marker(**change):
    return {
        "work_root": ROOT,
        "minimum_attempt": "1" * 32,
        "jobs": POLICY.jobs,
        "generator_sha256": "5" * 64,
        **saga_access.RESOURCES,
        "limits": {"parent": 2, "child": 2},
        "wrapper": "/cluster/home/operator/wrapper",
        "wrapper_sha256": "8" * 64,
        "key": POLICY.key.hex(),
        **change,
    }


def test_node_policy_reads_the_private_marker():
    assert asyncio.run(saga_access.node_policy(_sftp(_marker()))) == POLICY
    for change in (
        {"key": "short"},
        {"hours": 48},
        {"limits": {"parent": 3, "child": 2}},
        {"jobs": {"minimum": POLICY.jobs["minimum"]}},
        {"jobs": {**POLICY.jobs, "ts": {"name": "../ts.jd", "sha256": "2" * 64}}},
    ):
        with pytest.raises(ValueError, match="node job enrollment"):
            asyncio.run(saga_access.node_policy(_sftp(_marker(**change))))
    with pytest.raises(ValueError, match="private to its owner"):
        asyncio.run(saga_access.node_policy(_sftp(_marker(), permissions=0o640)))


def _worker(tmp_path, monkeypatch, connection):
    worker = keeper.Keeper(tmp_path)
    worker.sessions["saga"] = keeper.Session(
        dict(zip(("host", "port", "username"), TARGET, strict=True)),
        connection,
        since=0.0,
        used=0.0,
        saga_work=ROOT,
    )
    monkeypatch.setattr(keeper, "refresh_saga_scope", AsyncMock())
    monkeypatch.setattr(keeper.Session, "files", AsyncMock())
    monkeypatch.setattr(saga_access, "node_policy", AsyncMock(return_value=POLICY))
    return worker


def test_keeper_prepares_and_adds_only_the_key_digest(tmp_path, monkeypatch):
    worker = _worker(tmp_path, monkeypatch, responding(_preview()))
    msg = {"op": "saga_prepare_node", "token": worker.token, "server": "saga", "target": TARGET}
    reply = asyncio.run(worker.dispatch(msg["op"], {**msg, "request": REQUEST}))
    assert reply["ok"], reply
    assert reply["result"]["key_id"] == POLICY.key_id and reply["target"] == TARGET
    assert POLICY.key.hex() not in json.dumps(reply)
    for change in (
        {"target": [TARGET[0], 2222, TARGET[2]]},
        {"request": {**REQUEST, "operation": "submit_node"}},
        {"extra": True},
    ):
        worker = _worker(tmp_path, monkeypatch, AsyncMock())
        reply = asyncio.run(worker.dispatch(msg["op"], {**msg, "request": REQUEST, **change}))
        assert not reply["ok"]
        worker.sessions["saga"].conn.run.assert_not_awaited()


@pytest.mark.parametrize(
    "change",
    [
        {},
        {"key_id": "0" * 64},
        {"directory": ROOT + "/node-" + "9" * 32 + "/submission"},
        {"policy": None},
    ],
)
def test_keeper_signs_only_for_the_current_enrollment_and_destination(
    tmp_path, monkeypatch, change
):
    connection = responding(_job_reply())
    worker = _worker(tmp_path, monkeypatch, connection)
    if "policy" in change:
        monkeypatch.setattr(saga_access, "node_policy", AsyncMock(return_value=None))
    request = {
        "operation": "submit_node",
        "attempt": REQUEST["attempt"],
        "revision": "7" * 64,
        **saga_access.RESOURCES,
    }
    msg = {
        "op": "saga_node_job",
        "token": worker.token,
        "server": "saga",
        "target": TARGET,
        "key_id": change.get("key_id", POLICY.key_id),
        "directory": change.get("directory", _job_reply()["directory"]),
        "request": request,
    }
    reply = asyncio.run(worker.dispatch(msg["op"], msg))
    assert reply["ok"] is (not change), reply
    if change:
        connection.run.assert_not_awaited()


CHILD_REQUEST = {
    "request": "3" * 32,
    "attempt": "5" * 32,
    "parent_attempt": "4" * 32,
    "charge": 0,
    "multiplicity": 1,
    "mode_index": 1,
    "output_sha256": "6" * 64,
    "checkpoint_sha256": "7" * 64,
}


def _child_preview(state="0 1"):
    steps = []
    for route in ("Opt=(RecalcFC=30,MaxCycles=150,TS,NoLinear,NoEigenTest,Tight)", "Freq"):
        steps.append(
            f"%chk=guess-ts\n#p B3LYP/ChkBasis Geom=Check Guess=Read\n# {route}"
            f"\n\nContinue\n\n{state}\n\n"
        )
    text = "--Link1--\n".join(steps)
    return {
        "kind": "child",
        **CHILD_REQUEST,
        "geometry": "guess-ts.xyz",
        "freeze_distance": [1, 3],
        "parent_job_id": "241",
        "checkpoint_name": "guess-ts.fchk.gz",
        "job_file": "ts-project.jd",
        "job_sha256": "2" * 64,
        "generator_sha256": "5" * 64,
        "input_name": "guess-ts.inp",
        "revision": hashlib.sha256(text.encode()).hexdigest(),
        "state": "prepared",
        "directory": ROOT + "/node-" + CHILD_REQUEST["attempt"],
        "input": text,
        "limits": {"parent": 2, "child": 2},
    }


@pytest.mark.parametrize(
    "change",
    [
        {},
        {"parent_attempt": "9" * 32},
        {"checkpoint_sha256": "0" * 64},
        {"mode_index": 2},
        {"job_file": "min-project.jd"},
        {"checkpoint_name": "other.fchk.gz"},
        {"parent_job_id": "0"},
        {"xyz_sha256": "1" * 64},
        {"rehash": lambda r: r["input"].replace("\n0 1\n", "\n0 3\n", 1)},
        {"rehash": lambda r: r["input"].replace("%chk=guess-ts", "%chk=other", 1)},
        {"rehash": lambda r: r["input"] + "B 1 3 F\n"},
    ],
)
def test_child_preview_echoes_parent_mode_and_digests(change):
    result = _child_preview()
    if "rehash" in change:
        result = _rehash({**result, "input": change["rehash"](result)})
    else:
        result = {**result, **change}
    connection = responding(result)
    reply = asyncio.run(saga_access.prepare_node(connection, ROOT, POLICY, CHILD_REQUEST))
    assert reply["ok"] is (not change), reply
    assert json.loads(connection.run.await_args.kwargs["input"]) == {
        "operation": "prepare_node_child",
        **CHILD_REQUEST,
    }
