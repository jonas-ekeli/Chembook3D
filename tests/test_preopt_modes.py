"""Scientific eligibility and copied-output attribution for a synthetic parent job."""

import hashlib
import os

import pytest

from chembook3d import xyz
from chembook3d.remote.job_pilot import _freeze_optimization_distance
from chembook3d.remote.preopt_modes import _frequency_evidence, inspect_preoptimization
from tests.gaussian_text import frequency_block, orientation
from tests.hpc_pilot_runner import linked_minimum_input
from tests.preopt_fixture import WATER, write_completed_preopt


@pytest.fixture
def parent(tmp_path):
    folder = tmp_path / "submission"
    folder.mkdir()
    minimum = linked_minimum_input("water", ["O 0 0 0", "H 0.76 0.59 0", "H -0.76 0.59 0"])
    (folder / "water.inp").write_text(_freeze_optimization_distance(minimum, [1, 2]), "ascii")
    write_completed_preopt(folder, "water", "123", [1, 2])
    return folder


def inspect(parent):
    return inspect_preoptimization(parent, "water", "123", [1, 2])


def replace(path, old, new, *, last=False):
    # Retain the fixture's known copy-back times to isolate the condition under test.
    timestamp = path.stat().st_mtime_ns
    text = path.read_text("ascii")
    assert old in text
    text = new.join(text.rsplit(old, 1)) if last else text.replace(old, new)
    path.write_text(text, "ascii")
    os.utime(path, ns=(timestamp, timestamp))


def test_mode_requires_motion_along_the_pair(parent):
    evidence = inspect(parent)
    assert (
        evidence["output_sha256"] == hashlib.sha256((parent / "water.out").read_bytes()).hexdigest()
    )
    assert evidence["freeze_distance"] == [1, 2]
    assert evidence["frequency_geometry"] == [[symbol, *coords] for symbol, _, *coords in WATER]
    assert evidence["checkpoint_copy_announced"]
    (mode,) = evidence["modes"]
    assert mode["mode_index"] == 1 and mode["frequency_cm1"] == -425.0
    assert mode["bond_projection"] == pytest.approx(0.96, abs=0.01)
    assert mode["alignment"] > 0.999 and mode["eligible"]
    write_completed_preopt(parent, "water", "123", [1, 2], relevant=False)
    (other,) = inspect(parent)["modes"]
    assert other["frequency_cm1"] == mode["frequency_cm1"]
    assert other["relative_displacement"] > 0
    assert other["alignment"] == other["bond_projection"] == 0.0
    assert not other["eligible"]


def test_another_negative_frequency_retains_its_original_index(parent):
    replace(parent / "water.out", "3600.0000", "-360.0000")
    first, second = inspect(parent)["modes"]
    assert first["eligible"]
    assert (second["mode_index"], second["frequency_cm1"]) == (3, -360.0)
    assert not second["eligible"] and second["relative_displacement"] == 0


def test_completed_parent_with_only_real_frequencies_has_no_candidate(parent):
    replace(parent / "water.out", "-425.0000", "425.0000")
    assert inspect(parent)["modes"] == []


def test_first_rows_of_second_frequency_block_are_mode_numbers():
    # A four-atom molecule has six vibrational modes across two Gaussian blocks.
    coords = [(0, 0, 0), (0, 1, 0), (1, 0, 0), (0, 0, 1)]
    frame = orientation([("O", 8, *point) for point in coords])
    lines = frame + frequency_block([-420, -210, -80, -10, 215, 900], 4) + [""]
    modes = _frequency_evidence(lines, ["O"] * 4, [1, 2], [xyz.Atom("O", *p) for p in coords])[
        "modes"
    ]
    assert [m["mode_index"] for m in modes] == [1, 2, 3, 4]
    assert modes[-1]["frequency_cm1"] == -10


@pytest.mark.parametrize(
    ("mid_y", "point_group", "expected"),
    [
        (0.6, None, 3),  # distinctly bent
        (0.0, None, 4),  # linear with no printed group
        (0.000001, "C1", 4),  # within Cartesian print precision with symmetry disabled
        (0.000001, "C2V", 3),  # Gaussian distinguished a bent structure
        (0.00001, "D*H", 4),  # Gaussian treated the rotor as linear
    ],
)
def test_complete_frequency_spectrum(mid_y, point_group, expected):
    coords = [(-1, 0, 0), (0, mid_y, 0), (1, 0, 0)]
    atoms = [xyz.Atom("O", *p) for p in coords]
    frame = orientation([("O", 8, *p) for p in coords])
    frequencies = [-210.0, 180.0, 230.0, 250.0][:expected]
    complete = frame + frequency_block(frequencies, 3) + [""]
    assert _frequency_evidence(complete, ["O"] * 3, [1, 2], atoms, point_group)["modes"]
    incomplete = frame + frequency_block(frequencies[:-1], 3) + [""]
    with pytest.raises(ValueError, match=f"Expected {expected} vibrational frequencies"):
        _frequency_evidence(incomplete, ["O"] * 3, [1, 2], atoms, point_group)


@pytest.mark.parametrize(
    ("old", "new"),
    [
        ("Starting job 123", "Starting job 124"),
        ("JobName: water", "JobName: old"),
        ("Job exited normally.", "Job was cancelled."),
        (" normally terminated", " abnormal termination"),
        ("Copying './water.out'", "Copying './old.out'"),
        (
            "Job exited normally.",
            "File './water.out' could not be copied back home!\nJob exited normally.",
        ),
    ],
)
def test_inspection_requires_matching_successful_copyback(parent, old, new):
    replace(parent / "water.log", old, new)
    with pytest.raises(ValueError, match="wrapper"):
        inspect(parent)


def test_failed_checkpoint_copy_reports_modes_but_marks_checkpoint_unavailable(parent):
    replace(
        parent / "water.log",
        "Job exited normally.",
        "File './water.fchk.gz' could not be copied back home!\nJob exited normally.",
    )
    assert not inspect(parent)["checkpoint_copy_announced"]


@pytest.mark.parametrize("timestamp", [1, 1795510801])
def test_stale_or_post_wrapper_output_is_not_evidence(parent, timestamp):
    os.utime(parent / "water.out", (timestamp, timestamp))
    with pytest.raises(ValueError, match="modification time"):
        inspect(parent)


@pytest.mark.parametrize(
    ("old", "new", "detail"),
    [
        ("Normal termination of Gaussian", "Error termination of Gaussian", "terminated"),
        ("Stable=(Opt,RRHF)", "SP", "stability"),
        ("B3LYP/ChkBasis", "HF/ChkBasis", "approved project input"),
        ("Charge =  0 Multiplicity = 1", "Charge =  0 Multiplicity = 3", "multiplicity"),
        (" Optimization completed.\n    -- Stationary point found.", "", "converge"),
        (" B 1 2 F", " B 1 3 F", "bond"),
        (" Atom  AN", "Missing table", "table|terminated"),
        ("     2   1   0.38", "     1   1   0.38", "order"),
        ("   -0.760000", "   -0.750000", "geometry"),
        (" Frequencies --", " Lost --", "frequencies"),
    ],
)
def test_rejects_incomplete_or_wrong_gaussian_evidence(parent, old, new, detail):
    replace(parent / "water.out", old, new, last=True)
    with pytest.raises(ValueError, match=detail):
        inspect(parent)


def test_another_printed_orientation_supplies_the_displacement_axes(parent):
    path = parent / "water.out"
    text = path.read_text("ascii")
    begin = text.rindex("                          Input orientation:")
    end = text.index(" SCF Done", begin)
    frame = text[begin:end].replace("Input orientation:", "Standard orientation:")
    # Rotate about x. The unchanged imaginary displacements now project only on x.
    frame = frame.replace("0.590000    0.000000", "0.000000    0.590000")
    replace(path, text[begin:end], text[begin:end] + frame, last=True)
    (mode,) = inspect(parent)["modes"]
    assert mode["bond_projection"] == pytest.approx(0.601, abs=0.002)


def test_zero_to_print_precision_is_ineligible(parent):
    path = parent / "water.out"
    replace(path, "-0.38  -0.29", "-0.29   0.38")
    replace(path, " 0.38   0.29", " 0.29  -0.38")
    (mode,) = inspect(parent)["modes"]
    assert 0 < abs(mode["bond_projection"]) < 0.01
    assert not mode["eligible"]


def test_rejects_link_to_other_result(parent, tmp_path):
    output = parent / "water.out"
    try:
        os.link(output, tmp_path / "other.out")
    except PermissionError:
        pytest.skip("This filesystem does not permit hard links.")
    with pytest.raises(ValueError, match="linked"):
        inspect(parent)
