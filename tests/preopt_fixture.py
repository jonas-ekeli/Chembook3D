"""Synthetic water parent result for the local Gaussian checkpoint handoff."""

import gzip
import os
from pathlib import Path

if __package__:
    from .gaussian_text import frequency_block, output, step
else:  # The fake submitter is also invoked as a script in the submission directory.
    from gaussian_text import frequency_block, output, step

WATER = [("O", 8, 0.0, 0.0, 0.0), ("H", 1, 0.76, 0.59, 0.0), ("H", 1, -0.76, 0.59, 0.0)]


def water_fchk(job_id: str) -> bytes:
    """Gaussian formatted-checkpoint scalar and array records for the synthetic parent."""
    numbers = [atom[1] for atom in WATER]
    bohr = [v / 0.529177210903 for atom in WATER for v in atom[2:]]
    lines = [
        f"Synthetic water parent {job_id}",
        f"{'Freq':10s}{'RB3LYP':30s}{'STO-3G':30s}",
        f"{'Number of atoms':40s}   I     {len(WATER):12d}",
        f"{'Charge':40s}   I     {0:12d}",
        f"{'Multiplicity':40s}   I     {1:12d}",
        f"{'Atomic numbers':40s}   I   N={len(numbers):12d}",
        "".join(f"{number:12d}" for number in numbers),
        f"{'Current cartesian coordinates':40s}   R   N={len(bohr):12d}",
    ]
    for start in range(0, len(bohr), 5):
        lines.append("".join(f"{v:16.8E}" for v in bohr[start : start + 5]))
    return ("\n".join(lines) + "\n").encode("ascii")


def write_completed_preopt(
    directory: Path, stem: str, job_id: str, pair: list[int], *, relevant: bool = True
) -> None:
    """Write the same frequencies for bond-directed and unrelated imaginary motion."""
    i, j = (n - 1 for n in pair)
    delta = tuple(b - a for a, b in zip(WATER[i][2:], WATER[j][2:], strict=True))
    motion = delta if relevant else (0.0, 0.0, 0.75)
    block = frequency_block([-425.0, 1600.0, 3600.0], len(WATER))
    for n, atom in enumerate(WATER):
        sign = -1 if n == i else int(n == j)
        imaginary = [sign * 0.5 * component for component in motion]
        vectors = [*imaginary, 0, 0.25, 0.2, -0.25, 0, 0.1]
        block[n - len(WATER)] = f" {n + 1:5d} {atom[1]:3d}" + "".join(f" {v:6.2f}" for v in vectors)
    common = "#p B3LYP/ChkBasis Geom=Check Guess=Read"
    text = output(
        step("#p HF/Gen SP", [WATER]),
        step(common + " SP", [WATER]),
        step(common + " Stable=(Opt,RRHF)", [WATER]),
        step(
            common + " Opt=(Tight,ModRedundant)",
            [WATER],
            converged=True,
            body=[
                " The following ModRedundant input section has been read:",
                f" B {pair[0]} {pair[1]} F",
                "",
            ],
        ),
        step(common + " Freq", [WATER], after=block),
    )
    (directory / (stem + ".out")).write_text(text, encoding="ascii")
    checkpoint = stem + ".fchk.gz"
    (directory / checkpoint).write_bytes(gzip.compress(water_fchk(job_id), mtime=0))
    log = directory / (stem + ".log")
    log.write_text(
        f"Starting job {job_id} on pilot-node on Saga at Tue Nov 24 09:00:00 CET 2026\n"
        f"JobName: {stem}\n"
        f"Checking normal termination:\n -> {stem}.out\n normally terminated\n"
        f"Copying './{stem}.out' to submission folder...\n"
        f"Copying './{checkpoint}' to submission folder...\n"
        f"Job exited normally.\nJob {job_id} completed at noon\n",
        encoding="ascii",
    )
    # UTC 09:00, an hour after the recorded CET start, independent of the CI clock.
    for name in (stem + ".out", checkpoint, log.name):
        os.utime(directory / name, (1795510800, 1795510800))
