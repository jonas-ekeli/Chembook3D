"""Local cluster helper (D266): only fixed operator scripts run on synthetic inputs."""

import hashlib
import re
import secrets
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

import pytest
from fastapi import HTTPException

from chembook3d import xyz
from chembook3d.api import hpc_jobs
from chembook3d.remote.job_pilot import _freeze_optimization_distance, _verify_input, signature
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


def test_freeze_distance_changes_only_the_linked_optimization(pilot):
    options = {"job_file": "linked-minimum.jd"}
    ordinary = prepare(pilot, **options)["input"].split("--Link1--")
    record = prepare(pilot, **options, freeze_distance=[1, 2])
    constrained = record["input"].split("--Link1--")

    assert record["freeze_distance"] == [1, 2]
    assert record["revision"] == hashlib.sha256(record["input"].encode()).hexdigest()
    assert len(constrained) == len(ordinary) == 5
    assert constrained[:3] == ordinary[:3] and constrained[4] == ordinary[4]
    assert "modredundant" not in "".join(ordinary).lower()
    original_sections = re.split(r"(?:\r?\n){2,}", ordinary[3].strip())
    modified_sections = re.split(r"(?:\r?\n){2,}", constrained[3].strip())
    route, title, molecule, constraints = modified_sections
    assert (title, molecule, constraints) == (original_sections[1], "0 1", "B 1 2 F")
    opt = re.search(r"\bOpt=\(([^)]+)\)", route, re.I)
    assert opt is not None
    assert {item.strip().lower() for item in opt.group(1).split(",")} == {"tight", "modredundant"}
    assert (route[: opt.start()] + "Opt=Tight" + route[opt.end() :]) == original_sections[0]


@pytest.mark.parametrize(
    ("opt", "replacement", "newline"),
    [
        ("Opt", "Opt=ModRedundant", "\n"),
        (
            "Opt=(RecalcFC=50,MaxCycles=150,Tight)",
            "Opt=(RecalcFC=50,MaxCycles=150,Tight,ModRedundant)",
            "\r\n",
        ),
    ],
)
def test_freeze_distance_preserves_existing_opt_options_and_line_endings(opt, replacement, newline):
    original = linked_minimum_input("water", ["O 0 0 0", "H 0 0 1", "H 0 1 0"])
    original = original.replace("Opt=Tight", opt).replace("\n", newline)
    steps = original.split("--Link1--")
    steps[3] = steps[3].replace(opt, replacement) + f"B 2 3 F{newline}{newline}"

    assert _freeze_optimization_distance(original, [2, 3]) == "--Link1--".join(steps)


def test_freeze_distance_rejects_multiple_optimization_keywords():
    text = linked_minimum_input("water", ["O 0 0 0", "H 0 0 1", "H 0 1 0"])
    text = text.replace("Opt=Tight", "Opt Opt=Tight")

    with pytest.raises(ValueError, match="optimization keyword"):
        _freeze_optimization_distance(text, [1, 2])


@pytest.mark.parametrize(
    "pair",
    [[], [1], [1, 2, 3], [True, 2], [1.0, 2], ["1", 2], [1, 1], [0, 2], [1, 4]],
)
def test_helper_rejects_invalid_freeze_distance(pilot, pair):
    with pytest.raises(ValueError):
        prepare(pilot, job_file="linked-minimum.jd", freeze_distance=pair)


@pytest.mark.parametrize("option", ["Cartesian", "Z-Matrix", "ReadFreeze"])
def test_freeze_refuses_unsupported_optimizer_settings(option):
    original = linked_minimum_input("water", ["O 0 0 0", "H 0 0 1", "H 0 1 0"])
    text = original.replace("Opt=Tight", f"Opt=({option},Tight)")
    with pytest.raises(ValueError, match=option.upper()):
        _freeze_optimization_distance(text, [1, 2])


def test_helper_rejects_freeze_distance_for_single_step_minimum(pilot):
    with pytest.raises(ValueError, match="linked"):
        prepare(pilot, freeze_distance=[1, 2])


@pytest.mark.skipif(sys.platform == "win32", reason="symlink creation requires Windows privilege")
def test_link_to_geometry_outside_the_area(pilot, tmp_path):
    outside = tmp_path / "outside.xyz"
    outside.write_text("1\n\nHe 0 0 0\n")
    (pilot.geometries / "link.xyz").symlink_to(outside)
    with pytest.raises(ValueError, match="linked"):
        prepare(pilot, geometry="link.xyz")


@pytest.mark.parametrize(
    ("mode", "expected", "preparation"),
    [
        ("ok", "submitted", {}),
        ("reject", "rejected", {}),
        ("lost-receipt", "uncertain", {}),
        ("conflicting-receipt", "uncertain", {}),
        pytest.param(
            "ok",
            "submitted",
            {"job_file": "linked-minimum.jd", "freeze_distance": [1, 2]},
            id="freeze-distance",
        ),
    ],
)
def test_one_wrapper_invocation_across_retries_and_helper_restart(
    pilot, mode, expected, preparation
):
    helper = replace(pilot, submitter=(*pilot.submitter[:-1], mode))
    record = prepare(helper, **preparation)
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
