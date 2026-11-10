"""Check that a built wheel carries the web interface, as the release workflow builds it.

    uv run python scripts/check_wheel.py dist/chembook3d-<version>-py3-none-any.whl

The app serves a packaged `chembook3d/static/` before the checkout's `frontend/dist`
(`app.static_dir`), and exports the read-only copy from `chembook3d/static-snapshot/`
(`api/snapshot.py`). Neither folder is in the repository: the release workflow copies the
built frontend into them before `uv build`, and this check fails if that was missed.
"""

import re
import sys
import zipfile
from pathlib import Path

REQUIRED = ["chembook3d/static/index.html", "chembook3d/static-snapshot/snapshot.html"]


def problems(wheel: Path) -> list[str]:
    with zipfile.ZipFile(wheel) as archive:
        names = set(archive.namelist())
        found = [f"{name} is missing" for name in REQUIRED if name not in names]
        if "chembook3d/static/index.html" in names:
            html = archive.read("chembook3d/static/index.html").decode("utf-8")
            for asset in re.findall(r'(?:src|href)="/(assets/[^"]+)"', html):
                if f"chembook3d/static/{asset}" not in names:
                    found.append(f"chembook3d/static/{asset}, which index.html loads, is missing")
        return found


def main() -> None:
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    wheel = Path(sys.argv[1])
    found = problems(wheel)
    for problem in found:
        print(f"error: {problem}", file=sys.stderr)
    if found:
        sys.exit(1)
    print(f"{wheel.name} carries the web interface and the read-only copy's page.")


if __name__ == "__main__":
    main()
