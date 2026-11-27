"""Operator-side export of a deliberately selected pre-optimization archive."""

import hashlib
import json
from pathlib import Path

import pytest

from chembook3d.cli import main
from chembook3d.remote.job_pilot import _freeze_optimization_distance
from tests.hpc_pilot_runner import checkpoint_ts_input, linked_minimum_input
from tests.preopt_fixture import write_completed_preopt


@pytest.fixture
def archive(tmp_path):
    parent = tmp_path / "water.inp"
    child = tmp_path / "water-ts.inp"
    ordinary = linked_minimum_input("water", ["O 0 0 0", "H 0.76 0.59 0", "H -0.76 0.59 0"])
    parent.write_text(_freeze_optimization_distance(ordinary, [1, 2]), encoding="ascii")
    write_completed_preopt(tmp_path, "water", "97001", [1, 2])
    child.write_bytes(checkpoint_ts_input("water-ts").replace("\n", "\r\n").encode("ascii"))
    return [
        str(parent),
        str(child),
        str(tmp_path / "export"),
        "--parent-job-id",
        "97001",
        "--mode-index",
        "1",
    ]


def test_export_pins_reviewed_archive_and_preserves_existing_bundle(archive, capsys):
    main(["saga-ts-bundle", *archive])
    manifest = json.loads(capsys.readouterr().out)
    target = Path(archive[2])
    assert {p.name for p in target.iterdir()} == {"review.json", "child.inp", "parent.fchk.gz"}
    canonical = json.dumps(manifest, sort_keys=True, separators=(",", ":")) + "\n"
    assert (target / "review.json").read_bytes() == canonical.encode()
    assert manifest["format"] == "chembook3d-saga-ts-v1"
    assert (
        manifest["parent_job_id"] == "97001" and manifest["checkpoint_name"] == "water-ts.fchk.gz"
    )
    assert manifest["freeze_distance"] == [1, 2] and manifest["frequency_cm1"] == -425.0
    assert manifest["mode_index"] == 1 and manifest["input_name"] == "water-ts.inp"
    assert (target / "child.inp").read_bytes() == checkpoint_ts_input("water-ts").encode()
    for filename, key in (("child.inp", "input_sha256"), ("parent.fchk.gz", "checkpoint_sha256")):
        assert hashlib.sha256((target / filename).read_bytes()).hexdigest() == manifest[key]
    assert (target / "parent.fchk.gz").read_bytes() == Path(archive[0]).with_suffix(
        ".fchk.gz"
    ).read_bytes()
    with pytest.raises(SystemExit):
        main(["saga-ts-bundle", *archive])
    assert (target / "review.json").read_bytes() == canonical.encode()


@pytest.mark.parametrize("problem", ["wrong-receipt", "mode", "unrelated", "checkpoint", "route"])
def test_refuses_unverified_export(archive, problem):
    parent = Path(archive[0])
    if problem == "wrong-receipt":
        archive[4] = "97002"
    elif problem == "mode":
        archive[-1] = "2"
    elif problem == "unrelated":
        write_completed_preopt(parent.parent, parent.stem, "97001", [1, 2], relevant=False)
    elif problem == "checkpoint":
        parent.with_suffix(".fchk.gz").write_bytes(b"other")
    else:
        Path(archive[1]).write_text(checkpoint_ts_input("different"), encoding="ascii")
    with pytest.raises(SystemExit) as refused:
        main(["saga-ts-bundle", *archive])
    assert refused.value.code == 1 and not Path(archive[2]).exists()
