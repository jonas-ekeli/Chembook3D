"""Prepare an operator-selected archived TS replay on the workstation (05 §9)."""

import hashlib
import json
import re
import tempfile
from pathlib import Path

from chembook3d.remote import checkpoint_ts, preopt_modes
from chembook3d.remote.job_pilot import MAX_INPUT, SAFE_STEM


def export_bundle(
    parent: Path, child: Path, job_id: str, mode_index: int, destination: Path
) -> dict:
    """Verify the archive and export only the exact bytes the site operator will enroll."""
    try:
        return _export_bundle(parent, child, job_id, mode_index, destination)
    except (IndexError, KeyError, StopIteration) as exc:
        raise ValueError(
            "The archived files are incomplete or not in the expected Gaussian format."
        ) from exc


def _operator_path(path: Path) -> Path:
    # The operator names these files, so resolve their folder (``..``, mapped or subst drives,
    # 8.3 names). read_snapshot still refuses a linked file itself.
    try:
        return path.absolute().parent.resolve(strict=True) / path.name
    except (OSError, RuntimeError) as exc:
        raise ValueError(f"Missing folder for {path.name}.") from exc


def _export_bundle(
    parent: Path, child: Path, job_id: str, mode_index: int, destination: Path
) -> dict:
    parent, child = _operator_path(parent), _operator_path(child)
    if any(p.suffix != ".inp" or not SAFE_STEM.fullmatch(p.stem) for p in (parent, child)):
        raise ValueError("Choose two named Gaussian .inp files with supported basenames.")
    if parent.stem == child.stem or not re.fullmatch(r"[1-9][0-9]{0,19}", job_id):
        raise ValueError("Choose a distinct TS input name and the parent's Slurm job ID.")
    if destination.exists() or destination.is_symlink():
        raise ValueError("Choose a new destination directory for the enrollment files.")
    log_path = parent.with_suffix(".log")
    raw_log, _ = checkpoint_ts.read_snapshot(log_path, 1024**2)
    log = raw_log.decode("utf-8").replace("\r\n", "\n")
    _, copied = preopt_modes.completed_copyback(log, log_path, parent.stem, job_id)
    if not copied:
        raise ValueError("The parent log must confirm formatted checkpoint copy-back.")
    source, _ = checkpoint_ts.read_snapshot(parent, MAX_INPUT)
    submitted = source.decode("utf-8")
    _, pair, charge_spin, settings = checkpoint_ts.parent_geometry(submitted, parent.stem)
    # Download times belong to the local archive. The operator selects the original receipt;
    # compare its scientific artifacts directly and bind the resulting snapshots by digest.
    output, _ = checkpoint_ts.read_snapshot(parent.with_suffix(".out"), 64 * 1024**2)
    evidence = preopt_modes.preoptimization_modes(
        output.decode("utf-8"), submitted, parent.stem, pair
    )
    mode = next((m for m in evidence["modes"] if m["mode_index"] == mode_index), None)
    if mode is None or not mode["eligible"]:
        raise ValueError(
            "Select an imaginary mode along the frozen bond: alignment at least 0.5 and "
            "bond share at least 0.25 (D269)."
        )
    checkpoint, _ = checkpoint_ts.read_snapshot(
        parent.with_suffix(".fchk.gz"), checkpoint_ts.CHECKPOINT_LIMIT
    )
    checkpoint_ts.validate_checkpoint(checkpoint, evidence["frequency_geometry"], charge_spin)
    contents, _ = checkpoint_ts.read_snapshot(child, MAX_INPUT)
    contents = contents.replace(b"\r\n", b"\n")
    if b"\x00" in contents or b"\r" in contents:
        raise ValueError("Invalid text in the TS input.")
    checkpoint_ts.verify_ts_input(contents.decode("utf-8"), child.stem, charge_spin, settings)
    review = {
        "format": "chembook3d-saga-ts-v1",
        "parent_job_id": job_id,
        "parent_stem": parent.stem,
        "input_name": child.name,
        "checkpoint_name": child.stem + ".fchk.gz",
        "input_sha256": hashlib.sha256(contents).hexdigest(),
        "checkpoint_sha256": hashlib.sha256(checkpoint).hexdigest(),
        "output_sha256": hashlib.sha256(output).hexdigest(),
        "freeze_distance": pair,
        "mode_index": mode_index,
        "frequency_cm1": mode["frequency_cm1"],
        "bond_projection": mode["bond_projection"],
        "alignment": mode["alignment"],
    }
    with tempfile.TemporaryDirectory(dir=destination.parent) as temp:
        staged = Path(temp) / "enrollment"
        staged.mkdir(mode=0o700)
        (staged / "child.inp").write_bytes(contents)
        (staged / "parent.fchk.gz").write_bytes(checkpoint)
        manifest = json.dumps(review, sort_keys=True, separators=(",", ":"), allow_nan=False)
        (staged / "review.json").write_bytes((manifest + "\n").encode("utf-8"))
        staged.rename(destination)
    return review
