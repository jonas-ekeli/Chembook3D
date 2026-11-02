"""Write the pinned package lists of the local tools environment (D213).

The app installs Psi4, tblite, geomeTRIC and the dispersion and gCP programs from conda-forge
into its own folder (`local_tools.py`). It installs exactly the packages in
`src/chembook3d/assets/local-tools/<platform>.txt`, with their MD5 sums, so every computer
gets the same build and nothing is solved on the user's computer. This script makes those
lists with micromamba's solver; run it when the tools should be updated, then commit the lists:

    uv run python scripts/make_local_tools_lock.py path/to/micromamba
"""

import json
import subprocess
import sys
import tempfile
from pathlib import Path

PLATFORMS = ("win-64", "linux-64")
# The packages asked for; everything else comes in as their dependencies.
SPECS = [
    "python=3.12",
    "psi4=1.11",
    "dftd4-python",
    "simple-dftd3",
    "dftd3-python",
    "gcp-correction",
    "tblite-python",
    "geometric=1.1",
]
OUT = Path(__file__).resolve().parent.parent / "src" / "chembook3d" / "assets" / "local-tools"


def solve(micromamba: str, platform: str) -> list[dict]:
    with tempfile.TemporaryDirectory() as root:
        result = subprocess.run(
            [micromamba, "create", "--dry-run", "--json", "-y", "-r", root]
            + ["-p", str(Path(root) / "env"), "--platform", platform]
            + ["-c", "conda-forge", "--override-channels", *SPECS],
            capture_output=True,
            text=True,
            check=True,
        )
    return json.loads(result.stdout)["actions"]["LINK"]


def main() -> None:
    micromamba = sys.argv[1] if len(sys.argv) > 1 else "micromamba"
    OUT.mkdir(parents=True, exist_ok=True)
    for platform in PLATFORMS:
        packages = solve(micromamba, platform)
        wanted = {spec.split("=")[0] for spec in SPECS}
        chosen = [p for p in packages if p["name"] in wanted]
        lines = [
            "# Chembook3D local tools (D213), made by scripts/make_local_tools_lock.py",
            "# " + ", ".join(f"{p['name']} {p['version']}" for p in chosen),
            f"# platform: {platform}",
            "@EXPLICIT",
        ]
        lines += [f"{p['url']}#{p['md5']}" for p in packages]
        (OUT / f"{platform}.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
        print(platform, len(packages), "packages")


if __name__ == "__main__":
    main()
