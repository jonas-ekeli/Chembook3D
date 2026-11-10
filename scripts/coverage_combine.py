"""Join the data files of a coverage run into `.coverage`, then `uv run coverage report`.

    uv run coverage run -m pytest -W error -n auto
    uv run python scripts/coverage_combine.py
    uv run coverage report

Every process the tests start writes a data file of its own (`[tool.coverage.run]` in
pyproject.toml). `coverage combine` would join them too, but the local worker runs from a copy
in each job's folder (`local-jobs/<job>/worker.py`, made from `local_worker.py` by
`local_jobs.py`), which it cannot count as `local_worker.py`; this does.
"""

import re
import sys
from pathlib import Path

from coverage import CoverageData

ROOT = Path(__file__).resolve().parents[1]
WORKER = str(ROOT / "src" / "chembook3d" / "local_worker.py")
COPY = re.compile(r"[\\/]local-jobs[\\/][^\\/]+[\\/]worker\.py$")


def as_source(path: str) -> str:
    return WORKER if COPY.search(path) else path


def main() -> None:
    parts = sorted(ROOT.glob(".coverage.*"))
    if not parts:
        sys.exit("No .coverage.* files here: run `uv run coverage run -m pytest` first.")
    combined = CoverageData(str(ROOT / ".coverage"))
    combined.erase()
    for part in parts:
        data = CoverageData(str(part))
        data.read()
        combined.update(data, map_path=as_source)
    combined.write()
    for part in parts:
        part.unlink()
    print(f"Joined {len(parts)} data files into {ROOT / '.coverage'}")


if __name__ == "__main__":
    main()
