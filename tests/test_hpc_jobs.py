"""Local cluster helper (D266): only fixed operator scripts run on synthetic inputs."""

import hashlib
import secrets
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

import pytest
from fastapi import HTTPException

from chembook3d import xyz
from chembook3d.api import hpc_jobs
from chembook3d.remote.job_pilot import _verify_input, signature
from tests.fake_ssh_server import FakeSaga, enable_job_pilot
from tests.hpc_pilot_runner import linked_minimum_input


@pytest.fixture
def pilot(tmp_path):
    fake = FakeSaga(tmp_path / "ssh", port=8767)
    enable_job_pilot(fake, tmp_path / "config", "saga")
    return fake.job_pilot


def call(helper, operation, attempt, **fields):
    req = {"operation": operation, "attempt": attempt, **fields}
    return helper.handle({"request": req, "signature": signature(helper.key, req)})


def prepare(helper, **params):
    fields = {"job_file": "minimum.jd", "geometry": "water.xyz", "cores": 40, "hours": 24}
    return call(helper, "prepare", secrets.token_hex(16), **(fields | params))


def submit(helper, record, **changes):
    fields = {k: record[k] for k in ("cores", "hours", "revision")}
    return call(helper, "submit", record["attempt"], **(fields | changes))


def test_record_filename_preserves_canonical_attempt_id():
    attempt = "000000000000000000000000000000ab"
    assert hpc_jobs._record_path(attempt).name == attempt + ".json"
    for invalid in (attempt.upper(), "ab", attempt + "0"):
        with pytest.raises(HTTPException) as refused:
            hpc_jobs._record_path(invalid)
        assert refused.value.status_code == 422


def test_repeated_saga_preparation_cannot_rebind_another_investigation(tmp_path):
    attempt = secrets.token_hex(16)
    owners = [tmp_path / "first", tmp_path / "second"]
    input_text = "#p HF/STO-3G Opt\n\nWater\n"
    shared = {
        "server_id": "saga",
        "target": ["saga.sigma2.no", 22, ""],
        "pilot": "saga_minimum",
        "investigation_id": "original-investigation",
        "input": input_text,
        "revision": hashlib.sha256(input_text.encode()).hexdigest(),
    }

    def claim(owner):
        try:
            hpc_jobs._store_record(attempt, {**shared, "folder": str(owner)})
        except HTTPException as exc:
            assert exc.status_code == 409
            return False
        return True

    with ThreadPoolExecutor(max_workers=8) as pool:
        claims = list(pool.map(claim, owners * 8))
    winner = owners[0] if claims[0] else owners[1]
    assert claims == [owner == winner for owner in owners * 8]
    record, _key = hpc_jobs._record("saga", attempt, winner, shared["investigation_id"])
    assert record == {**shared, "folder": str(winner)}
    with pytest.raises(HTTPException, match="Open the investigation") as other:
        hpc_jobs._record(
            "saga",
            attempt,
            next(owner for owner in owners if owner != winner),
            shared["investigation_id"],
        )
    assert other.value.status_code == 409
    with pytest.raises(HTTPException, match="Open the investigation"):
        hpc_jobs._record("saga", attempt, winner, "new-investigation-in-the-same-folder")


@pytest.mark.parametrize(
    "changes",
    [
        {"geometry": "../water.xyz"},
        {"job_file": "../minimum.jd"},
        {"job_file": "other.jd"},
        {"cores": 41},
        {"hours": 25},
        {"hours": True},
        {"command": "unlisted"},
    ],
)
def test_helper_checks_its_own_allowed_inputs(pilot, changes):
    with pytest.raises(ValueError):
        prepare(pilot, **changes)


def test_helper_refuses_unsigned_or_changed_approval(pilot):
    record = prepare(pilot)
    req = {
        "operation": "submit",
        "attempt": record["attempt"],
        "revision": record["revision"],
        "cores": 40,
        "hours": 24,
    }
    with pytest.raises(ValueError, match="authorization"):
        pilot.handle({"request": req, "signature": "0" * 64})
    with pytest.raises(ValueError, match="authorization"):
        pilot.handle({"request": req, "signature": "é" * 64})
    with pytest.raises(ValueError, match="resources"):
        submit(pilot, record, hours=23)
    with pytest.raises(ValueError, match="revision"):
        submit(pilot, record, revision="0" * 64)
    attempt = pilot.work / record["attempt"]
    (attempt / "water.inp").write_text("changed", encoding="ascii")
    with pytest.raises(ValueError, match="changed"):
        submit(pilot, record)
    assert not (attempt / "submission").exists()


@pytest.mark.parametrize("mode", ["missing", "wrong-geometry"])
def test_zero_prepare_exit_is_not_enough(pilot, mode):
    helper = replace(pilot, generator=(*pilot.generator[:-1], mode))
    with pytest.raises(ValueError, match="Missing|coordinates"):
        prepare(helper)


@pytest.mark.parametrize(
    "directive", ["%oldchk=prior", "%chk=../prior", "%chk=other.chk", "--Link1--"]
)
def test_unsupported_checkpoint_or_linked_input(pilot, directive):
    (pilot.jobs / "minimum.jd").write_text(directive + "\n#p HF/STO-3G Opt", encoding="ascii")
    with pytest.raises(ValueError):
        prepare(pilot)


@pytest.mark.parametrize("keywords", ["Opt=(TS,CalcFC)", "Opt Guess=Read", "Opt=ModRedundant"])
def test_first_workflow_requires_a_fresh_optimization(pilot, keywords):
    (pilot.jobs / "minimum.jd").write_text("#p HF/STO-3G " + keywords, encoding="ascii")
    with pytest.raises(ValueError, match="unconstrained"):
        prepare(pilot)


@pytest.mark.parametrize(
    ("before", "after"),
    [
        ("HF/Gen", "HF/ChkBasis"),
        ("# SP", "# SP Guess=Read"),
        ("# SP", "# SP Guess=(Mix,Read)"),
        ("# SP", "# SP\n@prior-file"),
        ("O 0 0 0", "O 0 0 1"),
        ("****", "****\n@basis-from-disk"),
        ("****", "****\n--Link1-- trailing data"),
        ("--Link1--\n%chk=water", "--Link1--\n%chk=water.chk"),
        ("--Link1--\n%chk=water", "--Link1--\n%oldchk=outside\n%chk=water"),
        ("--Link1--\n%chk=water", "--Link1--\n%chk=../water"),
        ("Geom=Check", "Geom=AllCheck"),
        ("B3LYP/ChkBasis", "B3LYP/STO-3G"),
        ("Geom=Check", "Geom=Connectivity Geom=Check"),
        ("Continue\n\n0 1", "Continue\n\n0 2"),
        ("Continue\n\n0 1", "Continue\n\n0 1\nHe 0 0 0"),
        ("Opt=Tight", "Stable=Opt"),
        ("Opt=Tight", "Opt=(Tight,ModRedundant)"),
        ("Freq", "IRC"),
    ],
)
def test_linked_generation_rejects_changed_step(before, after):
    rows = ["O 0 0 0", "H 0 0 1", "H 0 1 0"]
    original = linked_minimum_input("water", rows)
    assert before in original
    with pytest.raises(ValueError):
        _verify_input(original.replace(before, after, 1), xyz.parse_xyz("\n".join(rows)), "water")


@pytest.mark.skipif(sys.platform == "win32", reason="symlink creation requires Windows privilege")
def test_link_to_geometry_outside_the_area(pilot, tmp_path):
    outside = tmp_path / "outside.xyz"
    outside.write_text("1\n\nHe 0 0 0\n")
    (pilot.geometries / "link.xyz").symlink_to(outside)
    with pytest.raises(ValueError, match="linked"):
        prepare(pilot, geometry="link.xyz")


@pytest.mark.parametrize(
    ("mode", "expected"),
    [
        ("ok", "submitted"),
        ("reject", "rejected"),
        ("lost-receipt", "uncertain"),
        ("conflicting-receipt", "uncertain"),
    ],
)
def test_one_wrapper_invocation_across_retries_and_helper_restart(pilot, mode, expected):
    helper = replace(pilot, submitter=(*pilot.submitter[:-1], mode))
    record = prepare(helper)
    with ThreadPoolExecutor(max_workers=3) as pool:
        list(pool.map(lambda _: submit(helper, record), range(3)))
    # New helper instance reading the same durable state; a lost reply is recovered by ID.
    reopened = replace(helper)
    result = call(reopened, "status", record["attempt"])
    assert result["state"] == expected and bool(result["job_id"]) == (expected == "submitted")
    assert submit(reopened, record) == result
    work = helper.work / record["attempt"] / "submission"
    assert (work / "water.inp").read_bytes() == record["input"].encode()
    assert (work / "scheduler-calls.txt").read_text().splitlines() == ["water.inp 1 40 24"]
