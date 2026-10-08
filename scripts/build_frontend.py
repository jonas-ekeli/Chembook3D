"""Build the web interface: `npm ci` and `npm run build` in frontend/.

Runs the same way in Windows PowerShell, Linux and WSL:

    uv run python scripts/build_frontend.py

Before building it checks that the Node.js it finds belongs to this system. Under WSL the
Windows PATH is appended to the Linux one, so without a Linux Node.js the Windows npm runs
instead. It starts its scripts through CMD.EXE, which cannot work in a folder inside WSL, so
the build fails with "UNC paths are not supported".
"""

import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import NoReturn

from chembook3d.cli import running_in_wsl
from chembook3d.launcher import write_stamp

FRONTEND = Path(__file__).resolve().parents[1] / "frontend"


def fail(message: str) -> NoReturn:
    print(f"error: {message}", file=sys.stderr)
    sys.exit(1)


def node_supported(version: str) -> bool:
    """Vite 8 needs Node.js 20.19+ or 22.12+ (CI builds with 22)."""
    match = re.match(r"v?(\d+)\.(\d+)", version.strip())
    if match is None:
        return True  # unknown format: let npm decide
    major, minor = int(match[1]), int(match[2])
    return (major, minor) >= (22, 12) or (major == 20 and minor >= 19)


def check_tools() -> str:
    npm = shutil.which("npm")
    node = shutil.which("node")
    if npm is None or node is None:
        fail("Node.js was not found. Install Node.js 22 (see README.md, 'Install the tools').")

    if running_in_wsl():
        windows_tools = [p for p in (npm, node) if p.startswith("/mnt/")]
        if windows_tools:
            fail(
                f"this is WSL, but {windows_tools[0]} is the Windows Node.js. Install Node.js "
                "inside WSL (see README.md, 'WSL'), open a new terminal, and check that "
                "`which npm` prints a path that does not start with /mnt/."
            )

    version = subprocess.run([node, "--version"], capture_output=True, text=True).stdout.strip()
    if not node_supported(version):
        fail(f"Node.js {version} is too old. Install Node.js 22 (see README.md).")
    return npm


def main() -> None:
    npm = check_tools()
    for args in (["ci"], ["run", "build"]):
        print(f"> npm {' '.join(args)}", flush=True)
        result = subprocess.run([npm, *args], cwd=FRONTEND)
        if result.returncode != 0:
            sys.exit(result.returncode)
    # D107: the launcher rebuilds only when frontend/ differs from what this build came from.
    write_stamp(FRONTEND.parent)
    print(f"Built {FRONTEND / 'dist'}. Start the app with: uv run chembook3d")


if __name__ == "__main__":
    main()
