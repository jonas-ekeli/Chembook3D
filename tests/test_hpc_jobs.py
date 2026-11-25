"""Local cluster helper (D266): only fixed operator scripts run on synthetic inputs."""

import secrets
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

import pytest

from chembook3d.remote.job_pilot import signature
from tests.fake_ssh_server import FakeSaga, enable_job_pilot


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


@pytest.mark.skipif(sys.platform == "win32", reason="symlink creation requires Windows privilege")
def test_link_to_geometry_outside_the_area(pilot, tmp_path):
    outside = tmp_path / "outside.xyz"
    outside.write_text("1\n\nHe 0 0 0\n")
    (pilot.geometries / "link.xyz").symlink_to(outside)
    with pytest.raises(ValueError, match="linked"):
        prepare(pilot, geometry="link.xyz")


@pytest.mark.parametrize(
    ("mode", "expected"),
    [("ok", "submitted"), ("reject", "rejected"), ("lost-receipt", "uncertain")],
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
