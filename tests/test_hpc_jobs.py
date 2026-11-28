"""Local cluster helper (D266): only fixed operator scripts run on synthetic inputs."""

import gzip
import hashlib
import io
import os
import re
import secrets
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

import pytest
from fastapi import HTTPException

from chembook3d import xyz
from chembook3d.api import hpc_jobs
from chembook3d.remote import checkpoint_ts
from chembook3d.remote.job_pilot import _freeze_optimization_distance, _verify_input, signature
from tests.fake_ssh_server import FakeSaga, enable_job_pilot
from tests.hpc_pilot_runner import checkpoint_ts_input, linked_minimum_input
from tests.preopt_fixture import WATER, water_fchk


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
    if "checkpoint_sha256" in record:
        fields["checkpoint_sha256"] = record["checkpoint_sha256"]
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
        {"job_file": "ts.jd"},
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


@pytest.fixture
def parent(pilot):
    return submit(pilot, prepare(pilot, job_file="linked-minimum.jd", freeze_distance=[1, 2]))


def prepare_ts(pilot, parent, **changes):
    params = {"parent_attempt": parent["attempt"], "mode_index": 1, "cores": 4, "hours": 2}
    path = pilot.work / parent["attempt"] / "submission" / "water.out"
    params["output_sha256"] = hashlib.sha256(
        path.read_bytes() if path.is_file() else b""
    ).hexdigest()
    return call(pilot, "prepare_ts", secrets.token_hex(16), **(params | changes))


def test_ts_handoff_rechecks_source_and_claims_scheduler_once(pilot, parent):
    source = pilot.work / parent["attempt"] / "submission"
    checkpoint = (source / "water.fchk.gz").read_bytes()
    modes = call(pilot, "inspect_modes", parent["attempt"])
    assert modes["parent_job_id"] == parent["job_id"]
    assert modes["freeze_distance"] == [1, 2]
    record = prepare_ts(pilot, parent)
    evidence = {
        **modes["modes"][0],
        "freeze_distance": [1, 2],
        "output_sha256": modes["output_sha256"],
    }
    assert record["mode_evidence"] == evidence
    assert record["parent_job_id"] == parent["job_id"]
    assert record["checkpoint_sha256"] == hashlib.sha256(checkpoint).hexdigest()
    assert record["input"] == checkpoint_ts_input("water-ts").replace("\n", os.linesep)

    folder = pilot.work / record["attempt"]
    (folder / "water-ts.chk").write_text("unrelated preview")
    assert not (folder / "submission").exists()
    unsigned_fields = {k: record[k] for k in ("revision", "cores", "hours")}
    with pytest.raises(ValueError, match="checkpoint"):
        call(pilot, "submit", record["attempt"], **unsigned_fields)
    with pytest.raises(ValueError, match="checkpoint"):
        submit(pilot, record, checkpoint_sha256="0" * 64)
    with ThreadPoolExecutor(max_workers=3) as pool:
        list(pool.map(lambda _: submit(pilot, record), range(3)))
    result = call(replace(pilot), "status", record["attempt"])
    assert result["state"] == "submitted" and result["job_id"] != parent["job_id"]
    assert result["mode_evidence"] == evidence and result["parent_attempt"] == parent["attempt"]
    staged = folder / "submission"
    assert (staged / "water-ts.fchk.gz").read_bytes() == checkpoint
    assert not (staged / "water-ts.chk").exists()
    assert (staged / "water-ts.inp").read_bytes() == record["input"].encode()
    assert (staged / "scheduler-calls.txt").read_text().splitlines() == ["water-ts.inp 1 4 2"]
    (source / "water.fchk.gz").unlink()
    assert submit(pilot, record) == result  # Recover the receipt without resubmission.


@pytest.mark.parametrize(
    "state", ["missing", "changed", "changed-output", "changed-input", "stale", "hardlink"]
)
def test_ts_preparation_cannot_submit_changed_or_missing_parent_artifacts(pilot, parent, state):
    record = prepare_ts(pilot, parent)
    folder = pilot.work / parent["attempt"] / "submission"
    checkpoint = folder / "water.fchk.gz"
    time = checkpoint.stat().st_mtime_ns
    if state == "missing":
        checkpoint.unlink()
    elif state == "changed":
        checkpoint.write_bytes(gzip.compress(b"different parent"))
        os.utime(checkpoint, ns=(time, time))
    elif state == "changed-output":
        path = folder / "water.out"
        path.write_bytes(b"\n" + path.read_bytes())
        os.utime(path, ns=(time, time))
    elif state == "changed-input":
        path = folder / "water.inp"
        path.write_bytes(path.read_bytes() + b"\n")
    elif state == "hardlink":
        try:
            (folder / "other.fchk.gz").hardlink_to(checkpoint)
        except PermissionError:
            pytest.skip("The local filesystem denied hardlink creation.")
    else:
        os.utime(checkpoint, (100, 100))
    with pytest.raises(ValueError):
        submit(pilot, record)
    assert call(pilot, "status", record["attempt"])["state"] == "prepared"
    assert not (pilot.work / record["attempt"] / "submission").exists()


@pytest.mark.parametrize(
    "state", ["wrong-id", "wrong-copy", "failed-copy", "invalid-gzip", "empty-gzip", "truncated"]
)
def test_ts_preparation_needs_attributed_intact_checkpoint(pilot, parent, state):
    folder = pilot.work / parent["attempt"] / "submission"
    log, checkpoint = folder / "water.log", folder / "water.fchk.gz"
    if state in ("invalid-gzip", "empty-gzip", "truncated"):
        stamp = checkpoint.stat().st_mtime_ns
        data = {
            "invalid-gzip": b"x",
            "empty-gzip": gzip.compress(b""),
            "truncated": checkpoint.read_bytes()[:-1],
        }
        checkpoint.write_bytes(data[state])
        os.utime(checkpoint, ns=(stamp, stamp))
    else:
        text = log.read_text()
        if state == "wrong-id":
            text = text.replace("Starting job " + parent["job_id"], "Starting job 111")
        elif state == "wrong-copy":
            text = text.replace("water.fchk.gz", "another.fchk.gz")
        else:
            text += "File './water.fchk.gz' could not be copied back home!\n"
        log.write_text(text)
        stamp = checkpoint.stat().st_mtime_ns
        os.utime(log, ns=(stamp, stamp))
    with pytest.raises(ValueError):
        prepare_ts(pilot, parent)


@pytest.mark.parametrize(
    "changes",
    [
        {"mode_index": 0},
        {"mode_index": True},
        {"mode_index": 2},
        {"output_sha256": "1" * 64},
        {"parent_attempt": "../bad"},
        {"checkpoint": "other"},
        {"job_file": "other.jd"},
    ],
)
def test_ts_source_selection_is_narrow(pilot, parent, changes):
    with pytest.raises(ValueError):
        prepare_ts(pilot, parent, **changes)


def test_ts_requires_submitted_frozen_parent(pilot):
    single = prepare(pilot)
    ordinary = prepare(pilot, job_file="linked-minimum.jd")
    frozen = prepare(pilot, job_file="linked-minimum.jd", freeze_distance=[1, 2])
    for record in (submit(pilot, single), submit(pilot, ordinary), frozen):
        with pytest.raises(ValueError, match="submitted|frozen"):
            prepare_ts(pilot, record)


def test_ts_submission_refuses_existing_staging_directory(pilot, parent):
    child = prepare_ts(pilot, parent)
    staging = pilot.work / child["attempt"] / "submission"
    staging.mkdir()
    (staging / "water-ts.chk").write_bytes(b"older checkpoint with wrapper lookup priority")
    assert submit(pilot, child)["state"] == "rejected"
    assert not (staging / "scheduler-calls.txt").exists()
    assert not (staging / "water-ts.inp").exists()


@pytest.mark.skipif(sys.platform == "win32", reason="symlink creation requires Windows privilege")
def test_ts_checkpoint_link_under_same_name_is_refused(pilot, parent):
    folder = pilot.work / parent["attempt"] / "submission"
    checkpoint = folder / "water.fchk.gz"
    other = folder.with_name("different.fchk.gz")
    checkpoint.rename(other)
    checkpoint.symlink_to(other)
    with pytest.raises(ValueError, match="linked"):
        prepare_ts(pilot, parent)


@pytest.mark.parametrize(
    ("original", "invalid"),
    [
        ("%chk=child", "%oldchk=outside\n%chk=child"),
        ("%chk=child", "%chk=child.chk"),
        ("Geom=Check", "Geom=AllCheck"),
        ("Guess=Read", "Guess=Mix"),
        ("ChkBasis", "Gen"),
        ("B3LYP", "HF"),
        ("NoLinear", "ModRedundant"),
        ("NoEigenTest", "ReadFC"),
        ("Freq", "IRC"),
        ("Freq", "Freq IOP(1/8=2)"),
        ("Freq", "Freq\n@extra"),
        ("0 1", "0 3"),
        ("0 1", "0 1\nHe 0 0 0"),
        ("0 1", "0 1\n\nB 1 2 F"),
    ],
)
def test_checkpoint_ts_validator_restricts_both_steps(original, invalid):
    source = checkpoint_ts_input("child")
    with pytest.raises(ValueError):
        checkpoint_ts.verify_ts_input(
            source.replace(original, invalid, 1), "child", ["0", "1"], {"B3LYP/CHKBASIS"}
        )


def test_checkpoint_handoff_preserves_parent_model_and_optimizer_options():
    extras = (
        "NoSymm Int=UltraFine EmpiricalDispersion=GD3 IOP(3/174=2) "
        "CPHF=(Grid=FineGrid) SCF=(Conver=7,MaxCycle=8,NoVarAcc)"
    )
    parent = linked_minimum_input("water", ["O 0 0 0", "H 0 0 1", "H 0 1 0"])
    parent = _freeze_optimization_distance(parent, [1, 2]).replace("# Freq", f"# {extras} Freq")
    _, _, charge_spin, settings = checkpoint_ts.parent_geometry(parent, "water")
    child = checkpoint_ts_input("child").replace("Geom=Check", f"{extras} Geom=Check")
    child = child.replace("# Opt=", "# IOP(1/8=2) Opt=")
    checkpoint_ts.verify_ts_input(child, "child", charge_spin, settings)
    for wrong in (
        child.replace("NoSymm", "", 1),
        child.replace("B3LYP", "HF"),
        child.replace("Conver=7", "Conver=8"),
        child.replace("IOP(1/8=2)", "IOP(1/8=3)"),
        child.replace("IOP(3/174=2)", "IOP(3/174=3)"),
    ):
        with pytest.raises(ValueError):
            checkpoint_ts.verify_ts_input(wrong, "child", charge_spin, settings)


def test_checkpoint_limit_checks_expanded_size_without_staging(tmp_path, monkeypatch):
    file = tmp_path / "parent.fchk.gz"
    file.write_bytes(gzip.compress(b"123456789"))
    monkeypatch.setattr(checkpoint_ts, "UNCOMPRESSED_LIMIT", 8)
    with pytest.raises(ValueError, match="expanded"):
        checkpoint_ts.checkpoint_bytes(
            file,
            {
                "checkpoint_copy_announced": True,
                "start_timestamp": 0,
                "log_mtime": file.stat().st_mtime,
                "frequency_geometry": [[row[0], *row[2:]] for row in WATER],
            },
            ["0", "1"],
        )


@pytest.mark.parametrize(
    "change",
    [
        "atom count",
        "atomic order",
        "charge",
        "spin",
        "geometry",
        "duplicate",
        "truncated",
        "array size",
        "wrong type",
        "surplus",
        "missing",
        "nonfinite",
        "text only",
    ],
)
def test_ts_preparation_rejects_mismatched_or_malformed_formatted_checkpoint(pilot, parent, change):
    checkpoint = pilot.work / parent["attempt"] / "submission" / "water.fchk.gz"
    lines = water_fchk(parent["job_id"]).splitlines()
    if change == "atom count":
        lines[2] = lines[2][:-1] + b"4"
    elif change == "atomic order":
        lines[6] = b" 1 8 1"
    elif change == "charge":
        lines[3] = lines[3][:-1] + b"1"
    elif change == "spin":
        lines[4] = lines[4][:-1] + b"3"
    elif change == "geometry":
        lines[8] = b"0 0 0 2.3 4.6"
    elif change == "duplicate":
        lines.append(lines[3])
    elif change == "truncated":
        lines.pop()
    elif change == "array size":
        lines[7] = lines[7][:-1] + b"8"
    elif change == "wrong type":
        lines[5] = lines[5].replace(b"I   N=", b"R   N=")
    elif change == "surplus":
        lines.append(b" 1.5")
    elif change == "missing":
        del lines[3]
    elif change == "nonfinite":
        lines[8] = b"1e600 0 0 2.3 4.6"
    else:
        lines = [b"another job's checkpoint"]
    stamp = checkpoint.stat().st_mtime_ns
    checkpoint.write_bytes(gzip.compress(b"\n".join(lines)))
    os.utime(checkpoint, ns=(stamp, stamp))
    with pytest.raises(ValueError):
        prepare_ts(pilot, parent)


def test_formatted_checkpoint_allows_different_axes_but_preserves_handedness():
    reference = [
        ["O", 0.0, 0.0, 0.0],
        ["H", 0.76, 0.59, 0.0],
        ["H", -0.76, 0.59, 0.0],
        ["C", 0.0, 0.0, 1.1],
    ]
    lines = water_fchk("123").splitlines()
    lines[2] = lines[2][:-1] + b"4"
    lines[5] = lines[5][:-1] + b"4"
    lines[6] = b" 8 1 1 6"
    lines[7] = lines[7][:-2] + b"12"
    for reflect in (False, True):
        points = [
            v / 0.529177210903
            for _, x, y, z in reference
            for v in (-y + 2, x - 3, (z if not reflect else -z) + 4)
        ]
        lines[8:] = [
            "".join(f"{v:16.8E}" for v in points[k : k + 5]).encode() for k in range(0, 12, 5)
        ]
        data = io.BytesIO(b"\n".join(lines))
        if reflect:
            with pytest.raises(ValueError, match="coordinates differ"):
                checkpoint_ts._fchk_geometry(data, reference, ["0", "1"])
        else:
            checkpoint_ts._fchk_geometry(data, reference, ["0", "1"])
