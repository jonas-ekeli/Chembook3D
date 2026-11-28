"""D271: the Saga helper prepares and submits parents for one node request (T-SSH-42)."""

import hmac
import json
import os
import sys
from pathlib import Path

import pytest

from chembook3d.parsers.gaussian import keyword_of, split_route_words
from chembook3d.remote import job_pilot
from chembook3d.remote import saga_prepare as helper
from tests.hpc_pilot_runner import NODE_GENERATOR as GENERATOR
from tests.hpc_pilot_runner import linked_minimum_input

linux = pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Saga runs Linux")
XYZ = "3\nguess from the adjacent intermediates\nO 0 0 0\nH 0 0.757 0.587\nH 0 -0.757 0.587\n"
ATOMS = [("O", [0.0, 0.0, 0.0]), ("H", [0.0, 0.757, 0.587]), ("H", [0.0, -0.757, 0.587])]
WRAPPER = (
    b"#!/bin/sh\n"
    b'echo "$*" >> "$HOME/wrapper-calls"\n'
    b'set -- ./*; [ "$#" = 1 ] || exit 1\n'
    b'echo "Submitted batch job 4321"\n'
)


def generated(state="0 1", stem="guess"):
    return linked_minimum_input(stem, XYZ.splitlines()[2:]).replace("0 1", state)


def test_check_node_input_accepts_the_project_minimum_and_its_state():
    helper.check_node_input(generated(), ATOMS, "guess", 0, 1)
    helper.check_node_input(generated("1 2"), ATOMS, "guess", 1, 2)
    with pytest.raises(ValueError, match="Step 1 of the generated input has charge 0 and"):
        helper.check_node_input(generated(), ATOMS, "guess", 0, 3)
    text = generated()
    third = text.split("--Link1--\n")
    third[2] = third[2].replace("0 1", "-1 2")
    with pytest.raises(ValueError, match="Step 3 .* charge -1 and multiplicity 2"):
        helper.check_node_input("--Link1--\n".join(third), ATOMS, "guess", 0, 1)


@pytest.mark.parametrize(
    ("before", "after"),
    [
        ("%chk=guess\n#p HF", "%chk=other\n#p HF"),
        ("%chk=guess\n#p HF", "%oldchk=parent\n%chk=guess\n#p HF"),
        ("# Opt=Tight", "# Opt=(TS,Tight)"),
        ("# Opt=Tight", "# Opt=ModRedundant"),
        ("# Freq", "# Opt"),
        ("# SP\n\nStart", "# SP Geom=Check\n\nStart"),
        ("O 0 0 0\n", "O 0 0 0.01\n"),
        ("H 0 -0.757 0.587\n", ""),
        ("Continue\n\n0 1\n\n--", "Continue\n\n0 1\nH 0 0 0\n\n--"),
        ("--Link1--\n", "@extra\n--Link1--\n"),
    ],
)
def test_check_node_input_refuses_changed_steps(before, after):
    text = generated()
    assert before in text
    with pytest.raises(ValueError):
        helper.check_node_input(text.replace(before, after, 1), ATOMS, "guess", 0, 1)


def test_check_node_input_needs_five_steps():
    one = generated().split("--Link1--\n")[0]
    with pytest.raises(ValueError, match="five-step"):
        helper.check_node_input(one, ATOMS, "guess", 0, 1)


@pytest.mark.parametrize(
    "route",
    [
        "#p B3LYP/ChkBasis Geom=Check Guess=Read Opt=(Tight, MaxCycles = 50) Int=(Grid=Ultrafine)",
        "#p Opt=(RecalcFC=5) Freq=(NoRaman)",
        "# HF/6-31G(d,p) SCF=(XQC,MaxCycle=300)",
    ],
)
def test_route_parsing_matches_the_gaussian_parser(route):
    words = helper.route_words(route)
    assert words == split_route_words(route.replace(" = ", "="))
    assert [helper.route_keyword(word) for word in words] == [keyword_of(w) for w in words]


@pytest.mark.parametrize("opt", ["Opt=Tight", "Opt", "opt=(tight,maxcycles=80)"])
@pytest.mark.parametrize("newline", ["\n", "\r\n"])
def test_freeze_matches_the_local_pilot(opt, newline):
    text = generated().replace("Opt=Tight", opt).replace("\n", newline)
    frozen = helper.freeze_distance(text, [1, 3])
    assert frozen == job_pilot._freeze_optimization_distance(text, [1, 3])
    assert f"{newline}B 1 3 F{newline}" in frozen


def test_parse_xyz_is_strict():
    assert helper.parse_xyz(XYZ.encode()) == ATOMS
    for data in (
        b"",
        b"two\n\nH 0 0 0\n",
        b"2\n\nH 0 0 0\n",
        b"1\n\nH 0 0\n",
        b"1\n\nh 0 0 0\n",
        b"1\n\nH 0 0 nan\n",
        b"1\n\nH 0 0 1e400\n",
        b"1\n\nH 0 0 0\n1\n\nH 0 0 0\n",
        b"\xff\n",
    ):
        with pytest.raises(ValueError):
            helper.parse_xyz(data)


@pytest.fixture
def pilot(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir(mode=0o700)
    monkeypatch.setenv("HOME", str(home))
    root = tmp_path.resolve() / "projects/project/operator/chembook3d-pilot"
    (root / "geometries").mkdir(parents=True)
    monkeypatch.setattr(helper, "PROJECTS", root.parents[2])
    control = helper.control_dir()
    control.mkdir(parents=True, mode=0o700)
    policy = {
        "work_root": str(root),
        "stem": "sample",
        "attempt": "1" * 32,
        "reference_sha256": "a" * 64,
    }
    helper.atomic_write(control / helper.MARKER, json.dumps(policy).encode())
    jobs = root.parent / "jobdescriptions"
    jobs.mkdir()
    (jobs / "min-project.jd").write_text("0 1 project minimum\n")
    (jobs / "ts-project.jd").write_text("0 1 project TS\n")
    generator = home / "tools/scripts/submit_all_DFT.sh"
    generator.parent.mkdir(parents=True)
    generator.write_bytes(GENERATOR)
    generator.chmod(0o700)
    wrapper = home / helper.WRAPPER
    wrapper.parent.mkdir(parents=True)
    wrapper.write_bytes(WRAPPER)
    wrapper.chmod(0o700)
    (root / "geometries/guess.xyz").write_text(XYZ)
    return type(
        "Pilot",
        (),
        {"home": home, "root": root, "control": control, "policy": policy, "jobs": jobs},
    )


def _calls(path: Path) -> list[str]:
    return path.read_text().splitlines() if path.exists() else []


def prepare(pilot, attempt="2" * 32, request="3" * 32, **changes):
    fields = {
        "operation": "prepare_node",
        "request": request,
        "attempt": attempt,
        "geometry": "guess.xyz",
        "charge": 0,
        "multiplicity": 1,
        "freeze_distance": [1, 3],
        **changes,
    }
    fields = {k: v for k, v in fields.items() if v is not None}
    return helper.node_operation(fields, pilot.policy)


def submit(pilot, prepared, *, key=None, **changes):
    request = {
        "operation": "submit_node",
        "attempt": prepared["attempt"],
        "revision": prepared["revision"],
        **helper.RESOURCES,
        **changes,
    }
    enabled = json.loads((pilot.control / helper.NODE_MARKER).read_bytes())
    signed = json.dumps(request, sort_keys=True, separators=(",", ":")).encode()
    request["signature"] = hmac.digest(bytes.fromhex(key or enabled["key"]), signed, "sha256").hex()
    return helper.node_operation(request, pilot.policy)


def status(pilot, prepared):
    request = {"operation": "node_status", "attempt": prepared["attempt"]}
    return helper.node_operation({**request, "revision": prepared["revision"]}, pilot.policy)


@linux
def test_operator_enrolls_the_project_job_descriptions(pilot):
    with pytest.raises(ValueError, match="not enrolled the project job descriptions"):
        prepare(pilot)
    jobs = helper.enroll_jobs("min-project.jd", "ts-project.jd")
    marker = pilot.control / helper.NODE_MARKER
    enabled = json.loads(marker.read_bytes())
    assert marker.stat().st_mode & 0o077 == 0
    assert enabled["jobs"] == jobs
    assert jobs["minimum"] == {
        "name": "min-project.jd",
        "sha256": helper.digest((pilot.jobs / "min-project.jd").read_bytes()),
    }
    assert enabled["limits"] == {"parent": 2, "child": 2}
    assert {k: enabled[k] for k in helper.RESOURCES} == helper.RESOURCES
    (pilot.jobs / "min-project.jd").write_text("0 1 changed minimum\n")
    assert helper.enroll_jobs("min-project.jd", "ts-project.jd") != jobs
    assert json.loads(marker.read_bytes())["key"] == enabled["key"]
    for names in (("../min-project.jd", "ts-project.jd"), ("min-project.jd", "missing.jd")):
        with pytest.raises((ValueError, OSError)):
            helper.enroll_jobs(*names)


@linux
def test_parent_is_generated_checked_frozen_and_recovered(pilot):
    helper.enroll_jobs("min-project.jd", "ts-project.jd")
    prepared = prepare(pilot)
    text = prepared["input"]
    assert prepared["state"] == "prepared" and prepared["kind"] == "parent"
    assert prepared["directory"] == str(pilot.root / ("node-" + "2" * 32))
    assert prepared["input_name"] == "guess.inp" and prepared["job_file"] == "min-project.jd"
    assert prepared["revision"] == helper.digest(text.encode())
    assert prepared["xyz_sha256"] == helper.digest(XYZ.encode())
    assert text == helper.freeze_distance(generated(), [1, 3])
    assert "Opt=(Tight,ModRedundant)" in text and "\nB 1 3 F\n" in text
    assert (Path(prepared["directory"]) / "guess.xyz").read_text() == XYZ
    # A lost reply: the same attempt returns the same preview without running the generator.
    assert prepare(pilot) == prepared
    assert _calls(pilot.home / "generator-calls") == ["guess.xyz"]
    with pytest.raises(ValueError, match="different arguments"):
        prepare(pilot, freeze_distance=[1, 2])
    assert status(pilot, prepared)["state"] == "prepared"
    unconstrained = prepare(pilot, attempt="4" * 32, freeze_distance=None)
    assert "ModRedundant" not in unconstrained["input"]
    assert unconstrained["freeze_distance"] is None


@linux
@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"charge": 1, "multiplicity": 2}, "the node has charge 1 and multiplicity 2"),
        ({"geometry": "missing.xyz"}, "No such file|Missing"),
        ({"geometry": "../guess.xyz"}, "Invalid node request"),
        ({"freeze_distance": [1, 4]}, "atom numbers"),
        ({"freeze_distance": [2, 2]}, "Invalid node request"),
        ({"charge": True}, "Invalid node request"),
        ({"extra": 1}, "Unexpected fields"),
        ({"request": "../" + "a" * 29}, "Invalid node request"),
    ],
)
def test_parent_preparation_refuses_wrong_state_or_arguments(pilot, change, message):
    helper.enroll_jobs("min-project.jd", "ts-project.jd")
    with pytest.raises((ValueError, OSError), match=message):
        prepare(pilot, **change)
    assert not (pilot.control / ("node-" + "2" * 32 + ".done")).exists()


@linux
def test_changed_job_generator_or_geometry_is_refused(pilot):
    helper.enroll_jobs("min-project.jd", "ts-project.jd")
    (pilot.jobs / "min-project.jd").write_text("0 1 perturb\n")
    with pytest.raises(ValueError, match="enroll it again"):
        prepare(pilot)
    helper.enroll_jobs("min-project.jd", "ts-project.jd")
    with pytest.raises(ValueError, match="coordinates do not match"):
        prepare(pilot)
    # A failed check leaves the attempt claimed for inspection; it is never reused.
    with pytest.raises(ValueError, match="in progress or incomplete"):
        prepare(pilot)
    (pilot.jobs / "min-project.jd").write_text("0 1 project minimum\n")
    helper.enroll_jobs("min-project.jd", "ts-project.jd")
    generator = pilot.home / "tools/scripts/submit_all_DFT.sh"
    generator.write_bytes(GENERATOR + b"# changed\n")
    with pytest.raises(ValueError, match="generator changed"):
        prepare(pilot, attempt="5" * 32)
    generator.write_bytes(GENERATOR)
    link = pilot.root / "geometries/linked.xyz"
    link.symlink_to(pilot.root / "geometries/guess.xyz")
    with pytest.raises(ValueError, match="regular file"):
        prepare(pilot, attempt="6" * 32, geometry="linked.xyz")


@linux
def test_signed_submission_runs_once_and_counts_toward_the_limit(pilot):
    helper.enroll_jobs("min-project.jd", "ts-project.jd")
    first = prepare(pilot)
    with pytest.raises(ValueError, match="Invalid submission authorization"):
        submit(pilot, first, key="00" * 32)
    with pytest.raises(ValueError, match="match the prepared node attempt"):
        submit(pilot, first, revision="0" * 64)
    with pytest.raises(ValueError, match="match the prepared node attempt"):
        submit(pilot, first, cores=80)
    receipt = submit(pilot, first)
    assert receipt["state"] == "submitted" and receipt["job_id"] == "4321"
    assert receipt["request"] == "3" * 32
    assert receipt["directory"] == first["directory"] + "/submission"
    staged = Path(receipt["directory"])
    assert [p.name for p in staged.iterdir()] == ["guess.inp"]
    assert (staged / "guess.inp").read_text() == first["input"]
    # A lost reply: the same submission and its status return the recorded receipt.
    assert submit(pilot, first) == receipt
    assert status(pilot, first) == receipt
    assert _calls(pilot.home / "wrapper-calls") == ["guess.inp -n 1 -c 40 -t 24"]
    second = prepare(pilot, attempt="4" * 32)
    third = prepare(pilot, attempt="5" * 32)
    assert submit(pilot, second)["state"] == "submitted"
    with pytest.raises(ValueError, match="already started 2 parents"):
        submit(pilot, third)
    with pytest.raises(ValueError, match="started its 2 constrained parents"):
        prepare(pilot, attempt="6" * 32)
    # Another node request has its own limit.
    other = prepare(pilot, attempt="6" * 32, request="7" * 32, charge=0)
    assert submit(pilot, other)["state"] == "submitted"
    assert len(_calls(pilot.home / "wrapper-calls")) == 3


@linux
def test_request_keeps_its_electronic_state(pilot):
    helper.enroll_jobs("min-project.jd", "ts-project.jd")
    prepare(pilot)
    (pilot.jobs / "min-project.jd").write_text("1 2 project minimum\n")
    helper.enroll_jobs("min-project.jd", "ts-project.jd")
    with pytest.raises(ValueError, match="opened for charge 0 and multiplicity 1"):
        prepare(pilot, attempt="4" * 32, charge=1, multiplicity=2)
    assert prepare(pilot, attempt="4" * 32, request="5" * 32, charge=1, multiplicity=2)


@linux
def test_changed_prepared_input_or_wrapper_blocks_submission(pilot):
    helper.enroll_jobs("min-project.jd", "ts-project.jd")
    prepared = prepare(pilot)
    path = Path(prepared["directory"]) / "guess.inp"
    path.write_text(prepared["input"] + "\n")
    with pytest.raises(ValueError, match="prepared input changed"):
        submit(pilot, prepared)
    path.write_text(prepared["input"])
    wrapper = pilot.home / helper.WRAPPER
    wrapper.write_bytes(WRAPPER + b"# changed\n")
    with pytest.raises(ValueError, match="wrapper has changed"):
        submit(pilot, prepared)
    assert not _calls(pilot.home / "wrapper-calls")
    assert status(pilot, prepared)["state"] == "prepared"


@linux
def test_node_enrollment_must_belong_to_this_pilot(pilot):
    helper.enroll_jobs("min-project.jd", "ts-project.jd")
    marker = pilot.control / helper.NODE_MARKER
    enabled = json.loads(marker.read_bytes())
    for change in ({"minimum_attempt": "9" * 32}, {"limits": {"parent": 5, "child": 2}}):
        helper.atomic_write(marker, json.dumps({**enabled, **change}).encode())
        with pytest.raises(ValueError, match="different pilot or is invalid"):
            prepare(pilot)
    helper.atomic_write(marker, json.dumps(enabled).encode())
    os.chmod(marker, 0o640)
    with pytest.raises(ValueError, match="private"):
        prepare(pilot)
