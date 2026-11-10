"""Every fixture output through the parsers with other line endings and cut off anywhere.

The parsers are called directly (`imports.read_file`, the import's own entry point, and the
readers it does not reach), so a crash surfaces here instead of being retried with fewer lines
as the Inspect window does (`inspection.parse_text`).
"""

import random
from pathlib import Path

import pytest

from chembook3d import xyz
from chembook3d.parsers import crest, orca, xtb
from chembook3d.services import imports

FIXTURES = Path(__file__).parent / "fixtures"
NOT_OUTPUTS = {".txt", ".md", ".csv", ".thch", ".inp", ".json"}  # licences, notes, reference data
IRC = FIXTURES / "orca" / "irc"
TRAJECTORIES = ["orca_IRC_F_trj.xyz", "orca_IRC_B_trj.xyz"]
# The errors a reader declares for a file it cannot read; anything else is a crash.
DECLARED = (imports.ImportFailed, xtb.NotXtbOutput, xyz.XyzParseError)
EVEN_CUTS = 20
RANDOM_CUTS = 5


def outputs() -> list[str]:
    return sorted(
        p.relative_to(FIXTURES).as_posix()
        for p in FIXTURES.rglob("*")
        if p.is_file() and p.suffix not in NOT_OUTPUTS
    )


def decode(data: bytes) -> str:
    return data.decode("utf-8", errors="replace")  # as the import and Inspect read files


def parse(text: str, name: str, folder: Path | None = None):
    """The reader the app uses for the file: xTB's optimization log (Inspect, D123c), a single
    structure (`xyz`, as a drawn or opened structure), or the import's reader for the rest."""
    if xtb.is_optimization_log(text):
        return xtb.parse_optimization_log(text, name, [])
    if name.endswith(".xyz") and not (
        xtb.looks_like_structures(text) or crest.looks_like_ensemble(text)
    ):
        return xyz.parse_xyz(text)
    return imports.read_file(text, name, folder)


def readable(relative: str) -> bool:
    """The ORCA IRC trajectories are read only beside their output, below."""
    return Path(relative).name not in TRAJECTORIES


@pytest.mark.parametrize("relative", [r for r in outputs() if readable(r)])
def test_crlf_line_ends_read_the_same_as_lf(relative):
    data = (FIXTURES / relative).read_bytes().replace(b"\r\n", b"\n")  # stored as LF anyway
    name = Path(relative).name
    lf = parse(decode(data), name)
    crlf = parse(decode(data.replace(b"\n", b"\r\n")), name)
    assert crlf == lf


def test_an_orca_irc_with_crlf_trajectories_reads_the_same(tmp_path):
    # D133: the trajectories beside the output are read too, so their line ends count.
    found = {}
    for ending in (b"\n", b"\r\n"):
        folder = tmp_path / ("crlf" if ending == b"\r\n" else "lf")
        folder.mkdir()
        for name in ["output.out", *TRAJECTORIES]:
            (folder / name).write_bytes((IRC / name).read_bytes().replace(b"\n", ending))
        text = decode((folder / "output.out").read_bytes())
        found[ending] = imports.read_file(text, "output.out", folder)
    lf, crlf = (found[e].steps[-1].irc for e in (b"\n", b"\r\n"))
    assert lf is not None and lf.points
    # The trajectories' bytes are kept as read, to copy with the output; all else is equal.
    assert crlf.files == {n: f.replace(b"\n", b"\r\n") for n, f in lf.files.items()}
    crlf.files = lf.files
    assert found[b"\r\n"] == found[b"\n"]


def cuts(size: int, seed: str) -> list[int]:
    """EVEN_CUTS evenly spaced byte offsets and RANDOM_CUTS random ones (fixed seed)."""
    rng = random.Random(seed)
    even = [size * i // (EVEN_CUTS + 1) for i in range(1, EVEN_CUTS + 1)]
    return sorted({*even, *(rng.randrange(1, size) for _ in range(RANDOM_CUTS))})


@pytest.mark.parametrize("relative", [r for r in outputs() if readable(r)])
def test_a_cut_off_file_is_read_or_refused_never_crashes(relative):
    # A cut can fall inside a line, a number or a UTF-8 character. The reader may give what it
    # read so far or refuse with its own error; any other exception fails the test.
    data = (FIXTURES / relative).read_bytes()
    name = Path(relative).name
    for size in cuts(len(data), relative):
        try:
            parse(decode(data[:size]), name)
        except DECLARED:
            pass


def test_cut_off_orca_irc_trajectories_never_crash(tmp_path):
    # D133: a job still running leaves its trajectories cut off too.
    text = decode((IRC / "output.out").read_bytes())
    for trajectory in TRAJECTORIES:
        data = (IRC / trajectory).read_bytes()
        for size in cuts(len(data), trajectory):
            folder = tmp_path / f"{trajectory}-{size}"
            folder.mkdir()
            for name in TRAJECTORIES:
                kept = data[:size] if name == trajectory else (IRC / name).read_bytes()
                (folder / name).write_bytes(kept)
            parsed = imports.read_file(text, "output.out", folder)
            assert parsed.steps[-1].irc is not None


def test_a_normal_modes_table_cut_inside_a_number():
    # Found by the sweep: ORCA's NORMAL MODES table cut just after a value's minus sign
    # raised ValueError on float("-"). The table now ends at the cut and its incomplete
    # columns are dropped.
    text = decode((FIXTURES / "orca" / "dvb_ir.out").read_bytes())
    table = text.index("NORMAL MODES")
    cut = text.index("  -0.", text.index("\n   54 ", table)) + len("  -")
    assert text[:cut].endswith(" -")
    step = orca.parse(text[:cut]).steps[-1]
    assert step.termination != "normal"
    assert step.frequencies and step.normal_modes == []
