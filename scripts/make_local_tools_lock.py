"""Write the pinned package lists of the local tools environment (D213).

The app installs Psi4, tblite, geomeTRIC and the dispersion and gCP programs from conda-forge
into its own folder (`local_tools.py`). It installs exactly the packages in
`src/chembook3d/assets/local-tools/<platform>.txt`, with their MD5 sums, so every computer
gets the same build and nothing is solved on the user's computer. This script makes those
lists with micromamba's solver; run it when the tools should be updated, then commit the lists:

    uv run python scripts/make_local_tools_lock.py path/to/micromamba

D237: `--mace` writes the optional MACE-OMOL-0 environment's lists instead: `mace-<platform>.txt`
(the same tools plus PyTorch on the CPU, ASE and MACE's own dependencies, from conda-forge) and
`mace-wheels.txt`, the few packages conda-forge lacks on one of the platforms (mace-torch,
matscipy, Sella with JAX), pinned to one version each with the SHA-256 of every wheel PyPI has
for Python 3.12 on Windows and Linux (x86-64). pip installs them with `--require-hashes
--no-deps --only-binary :all:`, so nothing is solved or built on the user's computer either.
"""

import json
import os
import subprocess
import sys
import tempfile
import urllib.request
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
# D237: the MACE environment adds these to SPECS (everything mace-torch imports).
MACE_SPECS = [
    "pytorch=2.9.*=cpu*",
    "ase=3.29",
    "e3nn=0.4.4",
    "torch-ema",
    "opt_einsum",
    "sympy",
    "scipy",
    "prettytable",
    "h5py",
    "torchmetrics",
    "python-hostlist",
    "configargparse",
    "gitpython",
    "pyyaml",
    "tqdm",
    "python-lmdb",
    "orjson",
    "matplotlib-base",
    "pandas",
    "packaging",
    "ml_dtypes",
    "pip",
]
# D237: from PyPI, as conda-forge lacks them on Windows (Sella there needs jaxlib).
WHEELS = {
    "mace-torch": "0.3.17",
    "matscipy": "1.3.1",
    "sella": "2.6.0",
    "jax": "0.12.0.1",
    "jaxlib": "0.12.0.1",
}
WHEEL_TAGS = ("none-any", "cp312-cp312-win_amd64", "cp312-cp312-manylinux")
OUT = Path(__file__).resolve().parent.parent / "src" / "chembook3d" / "assets" / "local-tools"


def solve(micromamba: str, platform: str, specs: list[str]) -> list[dict]:
    with tempfile.TemporaryDirectory() as root:
        result = subprocess.run(
            [micromamba, "create", "--dry-run", "--json", "-y", "-r", root]
            + ["-p", str(Path(root) / "env"), "--platform", platform]
            + ["-c", "conda-forge", "--override-channels", *specs],
            capture_output=True,
            text=True,
            check=True,
            # conda-forge's Windows builds ask for a Windows version, which Linux has none of
            env={**os.environ, "CONDA_OVERRIDE_WIN": "10.0"},
        )
    return json.loads(result.stdout)["actions"]["LINK"]


def wheel_lines() -> list[str]:
    """One requirement per pinned wheel, with the SHA-256 of each file pip may pick."""
    lines = []
    for name, version in WHEELS.items():
        url = f"https://pypi.org/pypi/{name}/{version}/json"
        with urllib.request.urlopen(url, timeout=60) as response:  # noqa: S310
            files = json.load(response)["urls"]
        hashes = [
            f["digests"]["sha256"]
            for f in files
            if f["packagetype"] == "bdist_wheel"
            and any(t in f["filename"] for t in WHEEL_TAGS)
            and "aarch64" not in f["filename"]
        ]
        if not hashes:
            raise SystemExit(f"no wheel of {name} {version} for these platforms")
        lines.append(f"{name}=={version} " + " ".join(f"--hash=sha256:{h}" for h in hashes))
    return lines


def main() -> None:
    args = [a for a in sys.argv[1:] if a != "--mace"]
    mace = "--mace" in sys.argv[1:]
    micromamba = args[0] if args else "micromamba"
    OUT.mkdir(parents=True, exist_ok=True)
    specs = SPECS + MACE_SPECS if mace else SPECS
    for platform in PLATFORMS:
        packages = solve(micromamba, platform, specs)
        wanted = {spec.split("=")[0] for spec in (["python", "pytorch", "ase"] if mace else SPECS)}
        chosen = [p for p in packages if p["name"] in wanted]
        title = "MACE-OMOL-0 environment (D237)" if mace else "local tools (D213)"
        lines = [
            f"# Chembook3D {title}, made by scripts/make_local_tools_lock.py",
            "# " + ", ".join(f"{p['name']} {p['version']}" for p in chosen),
            f"# platform: {platform}",
            "@EXPLICIT",
        ]
        lines += [f"{p['url']}#{p['md5']}" for p in packages]
        name = f"mace-{platform}.txt" if mace else f"{platform}.txt"
        (OUT / name).write_text("\n".join(lines) + "\n", encoding="utf-8")
        print(platform, len(packages), "packages")
    if mace:
        lines = [
            "# Chembook3D MACE-OMOL-0 environment (D237): wheels from PyPI, installed with",
            "# pip install --require-hashes --no-deps --only-binary :all: -r mace-wheels.txt",
            *wheel_lines(),
        ]
        (OUT / "mace-wheels.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
