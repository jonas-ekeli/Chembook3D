import hashlib
import json
import os
import shutil
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from chembook3d.remote import saga_prepare as helper

XYZ = b"2\nsynthetic geometry\nH 0 0 0\nH 0 0 0.74\n"
REFERENCE = b"# HF/STO-3G Opt\n\nSynthetic hydrogen reference\n\n0 1\nH 0 0 0\nH 0 0 0.74\n\n"
REQUEST = {"operation": "prepare_minimum"}


def sha256(data):
    return hashlib.sha256(data).hexdigest()


@pytest.fixture
def enrollment(tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    root = tmp_path.resolve() / "projects" / "project" / "operator" / "chembook3d-pilot"
    monkeypatch.setattr(helper, "PROJECTS", root.parents[2])
    baseline = root / "min-test-reference"
    baseline.mkdir(parents=True)
    geometry = baseline / "sample.xyz"
    geometry.write_bytes(XYZ)
    reference = baseline / "sample.inp"
    reference.write_bytes(REFERENCE)
    job = root.parent / "jobdescriptions" / helper.JOB
    job.parent.mkdir()
    job.write_bytes(b"synthetic job description\n")
    generator = home / "tools/scripts/submit_all_DFT.sh"
    generator.parent.mkdir(parents=True)
    generator.write_bytes(b"synthetic generator\n")
    attempt = "19" * 16
    policy = {
        "work_root": str(root),
        "reference_dir": baseline.name,
        "stem": geometry.stem,
        "xyz_sha256": sha256(XYZ),
        "job_sha256": sha256(job.read_bytes()),
        "generator_sha256": sha256(generator.read_bytes()),
        "reference_sha256": sha256(REFERENCE),
        "attempt": attempt,
    }
    control = helper.control_dir()
    control.mkdir(parents=True, mode=0o700)
    return SimpleNamespace(
        root=root,
        baseline=baseline,
        geometry=geometry,
        reference=reference,
        job=job,
        generator=generator,
        policy=policy,
        folder=root / f"prepare-{attempt}",
        marker=control / helper.MARKER,
        claim=control / f"{attempt}.claim",
        receipt=control / f"{attempt}.done",
    )


def mock_generator(monkeypatch, enrollment, contents=REFERENCE, *, returncode=0):
    def run(*args, cwd, **kwargs):
        if contents is not None:
            (cwd / enrollment.reference.name).write_bytes(contents)
        return subprocess.CompletedProcess(args[0], returncode)

    wrapper = Mock(side_effect=run)
    monkeypatch.setattr(helper.subprocess, "run", wrapper)
    return wrapper


def test_prepare_uses_pinned_geometry_and_nosub_then_reuses_matching_result(
    enrollment, monkeypatch
):
    wrapper = mock_generator(monkeypatch, enrollment)

    result = helper.prepare(REQUEST, enrollment.policy)
    assert result == {
        "state": "prepared",
        "directory": str(enrollment.folder),
        "input_name": "sample.inp",
        "sha256": sha256(REFERENCE),
        "reference_sha256": sha256(REFERENCE),
        "input": REFERENCE.decode(),
    }
    assert (enrollment.folder / "sample.xyz").read_bytes() == XYZ
    assert enrollment.receipt.read_bytes() == sha256(REFERENCE).encode()
    assert helper.prepare(REQUEST, enrollment.policy) == result
    wrapper.assert_called_once_with(
        [str(enrollment.generator), str(enrollment.job), "sample.xyz", "nosub"],
        cwd=enrollment.folder,
        timeout=90,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )


@pytest.mark.parametrize(
    ("contents", "returncode", "error"),
    [
        (b"synthetic input with a different line\n", 0, "differs"),
        (b"", 0, "Missing or oversized file"),
        (None, 0, None),
        (REFERENCE, 7, "wrapper failed"),
    ],
)
def test_unsuccessful_output_cannot_create_receipt_or_run_again(
    enrollment, monkeypatch, contents, returncode, error
):
    wrapper = mock_generator(monkeypatch, enrollment, contents, returncode=returncode)

    if error is None:
        with pytest.raises(FileNotFoundError):
            helper.prepare(REQUEST, enrollment.policy)
    else:
        with pytest.raises(ValueError, match=error):
            helper.prepare(REQUEST, enrollment.policy)
    assert not enrollment.receipt.exists()
    with pytest.raises(ValueError, match="in progress or incomplete"):
        helper.prepare(REQUEST, enrollment.policy)
    wrapper.assert_called_once()


def test_retry_revalidates_the_generated_input(enrollment, monkeypatch):
    wrapper = mock_generator(monkeypatch, enrollment)
    helper.prepare(REQUEST, enrollment.policy)
    (enrollment.folder / "sample.inp").write_bytes(b"changed after preparation\n")

    with pytest.raises(ValueError, match="differs"):
        helper.prepare(REQUEST, enrollment.policy)
    wrapper.assert_called_once()


@pytest.mark.parametrize("returncode", [0, 7])
def test_removing_attempt_directory_cannot_replay_claimed_attempt(
    enrollment, monkeypatch, returncode
):
    wrapper = mock_generator(monkeypatch, enrollment, returncode=returncode)

    if returncode:
        with pytest.raises(ValueError, match="wrapper failed"):
            helper.prepare(REQUEST, enrollment.policy)
    else:
        helper.prepare(REQUEST, enrollment.policy)
    assert enrollment.claim.is_file()
    shutil.rmtree(enrollment.folder)

    with pytest.raises(ValueError, match="in progress or incomplete"):
        helper.prepare(REQUEST, enrollment.policy)
    wrapper.assert_called_once()
    assert not enrollment.folder.exists()


@pytest.mark.parametrize("file", ["geometry", "job", "generator"])
def test_changed_pinned_inputs_are_rejected_before_attempt(enrollment, monkeypatch, file):
    wrapper = mock_generator(monkeypatch, enrollment)
    getattr(enrollment, file).write_bytes(b"different contents\n")

    with pytest.raises(ValueError, match="has changed"):
        helper.prepare(REQUEST, enrollment.policy)
    wrapper.assert_not_called()
    assert not enrollment.claim.exists()
    assert not enrollment.folder.exists()


@pytest.mark.parametrize(
    "request_data",
    [None, {}, {"operation": "submit"}, {**REQUEST, "job": "untrusted.jd"}],
)
def test_only_the_exact_prepare_request_is_allowed(enrollment, monkeypatch, request_data):
    wrapper = mock_generator(monkeypatch, enrollment)

    with pytest.raises(ValueError, match="Only the enrolled minimum"):
        helper.prepare(request_data, enrollment.policy)
    wrapper.assert_not_called()
    assert not enrollment.claim.exists()
    assert not enrollment.folder.exists()


@pytest.mark.parametrize(
    ("field", "value", "error"),
    [
        ("work_root", "pilot-with-wrong-name", "canonical chembook3d-pilot"),
        ("reference_dir", "min-test-ref/../another", "manual min-test directory"),
        ("stem", "sample.xyz", "manual min-test directory"),
        ("stem", "../sample", "manual min-test directory"),
        ("attempt", "../outside", "Invalid enrollment"),
    ],
)
def test_enrollment_paths_stay_in_the_expected_layout(enrollment, monkeypatch, field, value, error):
    wrapper = mock_generator(monkeypatch, enrollment)
    if field == "work_root":
        value = str(enrollment.root.with_name(value))
        Path(value).mkdir()

    with pytest.raises(ValueError, match=error):
        helper.prepare(REQUEST, {**enrollment.policy, field: value})
    wrapper.assert_not_called()


@pytest.mark.skipif(os.name != "posix", reason="Symlink permissions vary on Windows")
@pytest.mark.parametrize("entry", ["baseline", "job_dir", "geometry", "job", "generator", "folder"])
def test_linked_path_components_are_rejected(enrollment, monkeypatch, tmp_path, entry):
    wrapper = mock_generator(monkeypatch, enrollment)
    link = enrollment.job.parent if entry == "job_dir" else getattr(enrollment, entry)
    destination = tmp_path / "linked-entry"
    if entry == "folder":
        destination.mkdir()
        (destination / "sample.inp").write_bytes(REFERENCE)
        enrollment.receipt.write_bytes(sha256(REFERENCE).encode())
    else:
        link.rename(destination)
    link.symlink_to(destination, target_is_directory=destination.is_dir())

    with pytest.raises(ValueError, match="must not be links|regular file|incomplete"):
        helper.prepare(REQUEST, enrollment.policy)
    wrapper.assert_not_called()


@pytest.mark.skipif(os.name != "posix", reason="Symlink permissions vary on Windows")
def test_generator_cannot_resolve_to_a_file_inside_pilot(enrollment, monkeypatch):
    wrapper = mock_generator(monkeypatch, enrollment)
    generator_directory = enrollment.generator.parent
    destination = enrollment.root / "different-scripts"
    generator_directory.rename(destination)
    generator_directory.symlink_to(destination, target_is_directory=True)

    with pytest.raises(ValueError, match="generator must be installed outside the pilot"):
        helper.prepare(REQUEST, enrollment.policy)
    wrapper.assert_not_called()
    assert not enrollment.claim.exists()


@pytest.mark.skipif(os.name != "posix", reason="Symlink permissions vary on Windows")
@pytest.mark.parametrize("entry", ["receipt", "output"])
def test_retry_rejects_links_even_with_matching_bytes(enrollment, monkeypatch, tmp_path, entry):
    wrapper = mock_generator(monkeypatch, enrollment)
    helper.prepare(REQUEST, enrollment.policy)
    link = enrollment.receipt if entry == "receipt" else enrollment.folder / "sample.inp"
    destination = tmp_path / "linked-file"
    link.rename(destination)
    link.symlink_to(destination)

    with pytest.raises(ValueError, match="regular file|incomplete|link"):
        helper.prepare(REQUEST, enrollment.policy)
    wrapper.assert_called_once()


@pytest.mark.skipif(os.name != "posix", reason="Setup checks POSIX ownership and permissions")
def test_install_pins_selected_reference_and_places_helper_in_operator_home(enrollment):
    control = enrollment.marker.parent
    control.rmdir()

    assert helper.install(enrollment.reference) == sha256(REFERENCE)
    policy = json.loads(enrollment.marker.read_bytes())
    attempt = policy.pop("attempt")
    assert len(attempt) == 32 and all(ch in "0123456789abcdef" for ch in attempt)
    assert policy == {key: value for key, value in enrollment.policy.items() if key != "attempt"}
    installed = control / "saga_prepare.py"
    assert installed.read_bytes() == Path(helper.__file__).read_bytes()
    assert control.stat().st_uid == os.getuid()
    assert control.stat().st_mode & 0o022 == 0
    assert enrollment.marker.stat().st_mode & 0o077 == 0
    assert (enrollment.root / "geometries").is_dir()
    assert not (enrollment.root / f"prepare-{attempt}").exists()


@pytest.mark.skipif(os.name != "posix", reason="Setup checks POSIX ownership and permissions")
def test_install_rejects_control_directory_writable_by_others(enrollment):
    enrollment.marker.parent.chmod(0o770)

    with pytest.raises(ValueError, match="non-writable-by-others|private"):
        helper.install(enrollment.reference)
    assert not enrollment.marker.exists()


def test_install_rejects_reference_outside_project_root(enrollment, monkeypatch, tmp_path):
    monkeypatch.setattr(helper, "PROJECTS", tmp_path / "different-projects")

    with pytest.raises(ValueError, match="under the project pilot"):
        helper.install(enrollment.reference)
    assert not enrollment.marker.exists()


def test_prepare_rejects_enrollment_outside_project_root(enrollment, monkeypatch, tmp_path):
    wrapper = mock_generator(monkeypatch, enrollment)
    monkeypatch.setattr(helper, "PROJECTS", tmp_path / "different-projects")

    with pytest.raises(ValueError, match="under the project pilot"):
        helper.prepare(REQUEST, enrollment.policy)
    wrapper.assert_not_called()
    assert not enrollment.claim.exists()


def test_concurrent_invocation_does_not_run_the_generator_twice(enrollment, monkeypatch):
    entered = Event()
    release = Event()

    def run(*args, cwd, **kwargs):
        entered.set()
        assert release.wait(5)
        (cwd / "sample.inp").write_bytes(REFERENCE)
        return subprocess.CompletedProcess(args[0], 0)

    wrapper = Mock(side_effect=run)
    monkeypatch.setattr(helper.subprocess, "run", wrapper)
    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(helper.prepare, REQUEST, enrollment.policy)
        try:
            assert entered.wait(5)
            second = executor.submit(helper.prepare, REQUEST, enrollment.policy)
            with pytest.raises(ValueError, match="in progress or incomplete"):
                second.result(timeout=5)
        finally:
            release.set()
        assert first.result(timeout=5) == helper.prepare(REQUEST, enrollment.policy)
    wrapper.assert_called_once()


def run_cli(enrollment, request, script_path=helper.__file__):
    enrollment.marker.write_text(json.dumps(enrollment.policy), encoding="utf-8")
    return subprocess.run(
        [sys.executable, script_path],
        input=request,
        capture_output=True,
        timeout=15,
        check=False,
    )


@pytest.mark.parametrize(
    ("request_data", "error"),
    [(b'{"operation":"submit"}', "Only the enrolled minimum"), (b" " * 257, "too large")],
)
def test_command_emits_only_one_json_error_line(enrollment, request_data, error):
    result = run_cli(enrollment, request_data)

    assert result.returncode == 1
    assert result.stderr == b""
    message = json.loads(result.stdout)
    assert len(result.stdout.splitlines()) == 1
    assert message["ok"] is False
    assert error in message["error"]
    assert not enrollment.folder.exists()


@pytest.mark.skipif(os.name != "posix", reason="The real generator fixture uses /bin/sh")
def test_command_emits_one_json_success_line_without_generator_output(enrollment):
    enrollment.generator.write_bytes(
        b"#!/bin/sh\n"
        b'[ $# -eq 3 ] && [ -f "$1" ] && [ "$2" = sample.xyz ] '
        b'&& [ -f "$2" ] && [ "$3" = nosub ] || exit 1\n'
        b"echo noise on stdout\n"
        b"echo noise on stderr >&2\n"
        b"cat > sample.inp <<'REFERENCE'\n" + REFERENCE + b"REFERENCE\n"
    )
    enrollment.generator.chmod(0o700)
    enrollment.policy["generator_sha256"] = sha256(enrollment.generator.read_bytes())
    script = enrollment.marker.with_name("saga_prepare.py")
    source = Path(helper.__file__).read_text(encoding="utf-8")
    project_root = 'PROJECTS = Path("/cluster/projects")'
    assert project_root in source
    script.write_text(
        source.replace(project_root, f"PROJECTS = Path({str(enrollment.root.parents[2])!r})"),
        encoding="utf-8",
    )

    result = run_cli(enrollment, json.dumps(REQUEST).encode(), script)

    assert result.returncode == 0, result.stdout
    assert result.stderr == b""
    assert len(result.stdout.splitlines()) == 1
    assert json.loads(result.stdout) == {
        "ok": True,
        "result": {
            "state": "prepared",
            "directory": str(enrollment.folder),
            "input_name": "sample.inp",
            "sha256": sha256(REFERENCE),
            "reference_sha256": sha256(REFERENCE),
            "input": REFERENCE.decode(),
        },
    }
