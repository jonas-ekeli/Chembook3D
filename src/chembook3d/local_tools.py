"""The tools for local calculations on this computer (D213, FR-LOC-01).

On the user's request the app downloads micromamba (pinned by SHA-256, like the cloud xtb
binaries of `cloud_templates.py`) and creates a private environment from conda-forge with
exactly the packages listed in `assets/local-tools/<platform>.txt` (Psi4, tblite, geomeTRIC,
dftd4, s-dftd3, gCP; made by `scripts/make_local_tools_lock.py`). Nothing needs an account and
nothing is added to the investigation or to the Chembook3D repository.

The folder is `<data dir>/tools` (``%LOCALAPPDATA%\\chembook3d\\tools`` on Windows,
``~/.local/share/chembook3d/tools`` on Linux), or `<config dir>/tools` when
CHEMBOOK3D_CONFIG_DIR is set (tests), or CHEMBOOK3D_TOOLS_DIR. It holds:
- ``micromamba[.exe]`` and ``install.log``;
- ``env/``: the environment; ``env/chembook3d-tools.json`` says which package list made it;
- ``local.json``: cores and memory a job may use on this computer;
- ``local-jobs.lock``: held by the running job, so jobs run one after another.

An update builds ``env-new/`` beside the old environment and swaps it in only when it works.
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
import threading
import time
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from platformdirs import user_data_dir

from chembook3d import settings as app_settings

MICROMAMBA_VERSION = "2.10.0-0"
MICROMAMBA_URL = (
    "https://github.com/mamba-org/micromamba-releases/releases/download/"
    f"{MICROMAMBA_VERSION}/micromamba-{{platform}}"
)
MICROMAMBA_SHA256 = {
    "win-64": "f8b4284bd4eb1134122eaf93b3ea6c7be567e596de5829e1a6f478e34e41974a",
    "linux-64": "701f39c06f8e6a670a0e186c8479b9e5c0502a2f0cda6cef65d83bdd2875e8bd",
}
LOCKS = Path(__file__).parent / "assets" / "local-tools"
MARKER = "chembook3d-tools.json"
SETTINGS = "local.json"
LOCK_FILE = "local-jobs.lock"
# What the environment is checked for after an install, and whose versions the section shows.
PACKAGES = ("psi4", "tblite", "geometric", "dftd4", "dftd3", "numpy")
CHECK = (
    "import json, sys\n"
    "from importlib import metadata\n"
    "import psi4, tblite.interface, geometric, dftd4.interface, dftd3.interface\n"
    "found = {'python': sys.version.split()[0], 'psi4': psi4.__version__}\n"
    "for name in ('tblite', 'geometric', 'dftd4', 'dftd3', 'numpy'):\n"
    "    found[name] = metadata.version(name)\n"
    "print(json.dumps(found))\n"
)
DOWNLOAD_TIMEOUT = 120


class ToolsError(RuntimeError):
    pass


def conda_platform() -> str | None:
    """The conda platform of this computer, when the tools are offered for it (Windows and
    Linux on x86-64; macOS is untested, NFR-PORT)."""
    machine = platform.machine().lower()
    if machine not in ("amd64", "x86_64"):
        return None
    if sys.platform == "win32":
        return "win-64"
    if sys.platform.startswith("linux"):
        return "linux-64"
    return None


def tools_dir() -> Path:
    override = os.environ.get("CHEMBOOK3D_TOOLS_DIR")
    if override:
        return Path(override)
    if os.environ.get("CHEMBOOK3D_CONFIG_DIR"):
        return app_settings.config_dir() / "tools"
    return Path(user_data_dir("chembook3d", appauthor=False)) / "tools"


def env_dir() -> Path:
    return tools_dir() / "env"


def lock_path() -> Path:
    return tools_dir() / LOCK_FILE


def package_list(conda: str | None = None) -> Path | None:
    conda = conda or conda_platform()
    if conda is None:
        return None
    path = LOCKS / f"{conda}.txt"
    return path if path.is_file() else None


def list_id(path: Path) -> str:
    """Names the package list an environment was made from (its hash)."""
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]


def env_python(env: Path | None = None) -> Path:
    env = env or env_dir()
    return env / "python.exe" if sys.platform == "win32" else env / "bin" / "python"


def env_path_dirs(env: Path | None = None) -> list[Path]:
    """The environment's program folders, first on PATH for a job (Psi4 runs mctc-gcp)."""
    env = env or env_dir()
    if sys.platform == "win32":
        return [
            env,
            env / "Library" / "mingw-w64" / "bin",
            env / "Library" / "usr" / "bin",
            env / "Library" / "bin",
            env / "Scripts",
            env / "bin",
        ]
    return [env / "bin"]


def worker_python() -> Path | None:
    """The Python a job runs with: the environment's, or CHEMBOOK3D_LOCAL_PYTHON (tests)."""
    override = os.environ.get("CHEMBOOK3D_LOCAL_PYTHON")
    if override:
        return Path(override)
    python = env_python()
    return python if python.is_file() and (env_dir() / MARKER).is_file() else None


# ---------- cores and memory ----------


def physical_cores() -> int:
    """Physical cores where the system says, else logical cores halved (hyper-threading)."""
    logical = os.cpu_count() or 1
    try:
        if sys.platform.startswith("linux"):
            seen = set()
            core = package = None
            for line in Path("/proc/cpuinfo").read_text().splitlines():
                if line.startswith("physical id"):
                    package = line.split(":")[1].strip()
                elif line.startswith("core id"):
                    core = line.split(":")[1].strip()
                    seen.add((package, core))
            if seen:
                return len(seen)
    except OSError:
        pass
    return max(1, logical // 2) if logical >= 4 else logical


def total_memory_mb() -> int | None:
    try:
        if sys.platform == "win32":
            import ctypes

            class Status(ctypes.Structure):
                _fields_ = [
                    ("length", ctypes.c_ulong),
                    ("load", ctypes.c_ulong),
                    ("total", ctypes.c_ulonglong),
                    ("free", ctypes.c_ulonglong),
                    ("page_total", ctypes.c_ulonglong),
                    ("page_free", ctypes.c_ulonglong),
                    ("virtual_total", ctypes.c_ulonglong),
                    ("virtual_free", ctypes.c_ulonglong),
                    ("extended", ctypes.c_ulonglong),
                ]

            status = Status()
            status.length = ctypes.sizeof(Status)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status))  # type: ignore[attr-defined]
            return int(status.total // 1_000_000)
        pages = os.sysconf("SC_PHYS_PAGES")
        return int(pages * os.sysconf("SC_PAGE_SIZE") // 1_000_000)
    except (AttributeError, OSError, ValueError):
        return None


def default_cores() -> int:
    """A143: physical cores minus one, so the computer stays usable."""
    return max(1, physical_cores() - 1)


def default_memory_mb() -> int:
    """Half the memory, at most 16 GB, at least 1 GB."""
    total = total_memory_mb() or 4000
    return max(1000, min(16000, total // 2))


@dataclass
class Resources:
    cores: int
    memory_mb: int


def resources() -> Resources:
    data = _local_settings()
    cores, memory = data.get("cores"), data.get("memory_mb")
    return Resources(
        cores=cores if isinstance(cores, int) and cores >= 1 else default_cores(),
        memory_mb=memory if isinstance(memory, int) and memory >= 500 else default_memory_mb(),
    )


def _local_settings() -> dict[str, Any]:
    try:
        data = json.loads((tools_dir() / SETTINGS).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _save_local_settings(data: dict[str, Any]) -> None:
    tools_dir().mkdir(parents=True, exist_ok=True)
    (tools_dir() / SETTINGS).write_text(json.dumps(data), encoding="utf-8")


def save_resources(cores: int | None, memory_mb: int | None) -> Resources:
    """None puts the default back."""
    data = {k: v for k, v in _local_settings().items() if k not in ("cores", "memory_mb")}
    if cores is not None:
        data["cores"] = max(1, min(int(cores), os.cpu_count() or 1))
    if memory_mb is not None:
        data["memory_mb"] = max(500, int(memory_mb))
    _save_local_settings(data)
    return resources()


def calibration() -> dict[str, float]:
    """Seconds per step this computer took, by method, scaled to 70 atoms on 4 cores."""
    found = _local_settings().get("calibration")
    if not isinstance(found, dict):
        return {}
    return {k: float(v) for k, v in found.items() if isinstance(v, int | float) and v > 0}


def save_calibration(method: str, seconds: float) -> None:
    data = _local_settings()
    found = data.get("calibration") if isinstance(data.get("calibration"), dict) else {}
    found[method] = round(seconds, 3)
    data["calibration"] = found
    _save_local_settings(data)


# ---------- installing ----------


@dataclass
class Install:
    """The install or update running in this app, if any (only one at a time)."""

    state: str = "idle"  # idle, running, done, failed
    step: str = ""
    started: float | None = None
    finished: float | None = None
    error: str | None = None
    log: list[str] = field(default_factory=list)


class Installer:
    """Runs the install in a thread of the app; its state is kept in memory."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.current = Install()
        self.thread: threading.Thread | None = None

    def start(self, busy: bool = False) -> Install:
        """`busy`: a local job is running, so the environment must not be replaced."""
        conda = conda_platform()
        packages = package_list(conda)
        if conda is None or packages is None:
            raise ToolsError("Local calculations are offered on Windows and Linux (x86-64) only")
        if busy:
            raise ToolsError("Wait for the running local job to finish, or cancel it, first")
        with self.lock:
            if self.current.state == "running":
                return self.current
            self.current = Install(state="running", step="Starting", started=time.time())
            self.thread = threading.Thread(
                target=self._run, args=(conda, packages), name="local-tools", daemon=True
            )
            self.thread.start()
            return self.current

    def _say(self, step: str) -> None:
        with self.lock:
            self.current.step = step
            self.current.log.append(step)

    def _run(self, conda: str, packages: Path) -> None:
        try:
            install(conda, packages, self._say)
        except Exception as exc:  # noqa: BLE001 - shown in the settings section
            with self.lock:
                self.current.state = "failed"
                self.current.error = str(exc) or type(exc).__name__
                self.current.finished = time.time()
            return
        with self.lock:
            self.current.state = "done"
            self.current.step = "Installed"
            self.current.finished = time.time()

    def wait(self, timeout: float | None = None) -> None:
        thread = self.thread
        if thread is not None:
            thread.join(timeout)


def _download(url: str, target: Path, sha256: str) -> None:
    request = urllib.request.Request(url, headers={"User-Agent": "chembook3d"})
    digest = hashlib.sha256()
    part = target.with_name(target.name + ".part")
    with (
        urllib.request.urlopen(request, timeout=DOWNLOAD_TIMEOUT) as response,
        part.open("wb") as fh,
    ):  # noqa: S310 - pinned https address
        while chunk := response.read(1 << 20):
            digest.update(chunk)
            fh.write(chunk)
    if digest.hexdigest() != sha256:
        part.unlink(missing_ok=True)
        raise ToolsError(f"The download of {url} does not match its pinned SHA-256")
    os.replace(part, target)


def micromamba_path() -> Path:
    return tools_dir() / ("micromamba.exe" if sys.platform == "win32" else "micromamba")


def micromamba(conda: str) -> list[str]:
    """The command that runs micromamba, downloaded first when the pinned one is not there."""
    path = micromamba_path()
    pinned = MICROMAMBA_SHA256[conda]
    if not (path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest() == pinned):
        _download(MICROMAMBA_URL.format(platform=conda), path, pinned)
        if sys.platform != "win32":
            path.chmod(0o755)
    return [str(path)]


def _hidden() -> dict[str, Any]:
    """No console window for a program started from the app on Windows."""
    if sys.platform == "win32":
        return {"creationflags": subprocess.CREATE_NO_WINDOW}  # type: ignore[attr-defined]
    return {}


def _clean_env() -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k not in ("PYTHONPATH", "PYTHONHOME")}
    env["PYTHONNOUSERSITE"] = "1"
    return env


def check(env: Path) -> dict[str, str]:
    """The versions of the tools in `env`, by importing them there."""
    run_env = _clean_env()
    run_env["PATH"] = os.pathsep.join(
        [str(p) for p in env_path_dirs(env)] + [run_env.get("PATH", "")]
    )
    result = subprocess.run(
        [str(env_python(env)), "-I", "-c", CHECK],
        cwd=env,  # Psi4 leaves a timer.dat where it runs
        capture_output=True,
        text=True,
        env=run_env,
        timeout=600,
        **_hidden(),
    )
    if result.returncode != 0:
        tail = (result.stderr or result.stdout).strip().splitlines()[-3:]
        raise ToolsError("The tools do not start: " + " ".join(tail))
    return json.loads(result.stdout.strip().splitlines()[-1])


def install(conda: str, packages: Path, say=lambda step: None) -> dict[str, Any]:
    """Download micromamba, build the environment in env-new/ and swap it in."""
    folder = tools_dir()
    folder.mkdir(parents=True, exist_ok=True)
    say("Downloading micromamba")
    command = micromamba(conda)
    new = folder / "env-new"
    root = folder / "mamba-root"
    shutil.rmtree(new, ignore_errors=True)
    say("Downloading and installing Psi4, tblite and geomeTRIC from conda-forge (5–10 min)")
    env = _clean_env()
    env["MAMBA_ROOT_PREFIX"] = str(root)
    with (folder / "install.log").open("w", encoding="utf-8") as log:
        result = subprocess.run(
            [*command, "create", "-y", "-q", "-r", str(root), "-p", str(new)]
            + ["--file", str(packages)],
            stdout=log,
            stderr=subprocess.STDOUT,
            env=env,
            **_hidden(),
        )
    if result.returncode != 0:
        lines = (folder / "install.log").read_text(encoding="utf-8", errors="replace").splitlines()
        shutil.rmtree(new, ignore_errors=True)
        raise ToolsError("micromamba failed: " + " ".join(lines[-3:]))
    say("Checking the tools")
    found = check(new)
    marker = {
        "list": list_id(packages),
        "platform": conda,
        "installed": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "versions": found,
    }
    (new / MARKER).write_text(json.dumps(marker, indent=1), encoding="utf-8")
    say("Replacing the old tools")
    old = folder / "env"
    if old.exists():
        trash = folder / "env-old"
        shutil.rmtree(trash, ignore_errors=True)
        os.replace(old, trash)
        shutil.rmtree(trash, ignore_errors=True)
    os.replace(new, old)
    shutil.rmtree(root / "pkgs", ignore_errors=True)  # the download cache: about 1 GB
    return marker


def status(installer: Installer | None = None) -> dict[str, Any]:
    """What the settings section shows."""
    conda = conda_platform()
    packages = package_list(conda)
    marker: dict[str, Any] | None = None
    try:
        marker = json.loads((env_dir() / MARKER).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        marker = None
    current = installer.current if installer else Install()
    res = resources()
    return {
        "supported": conda is not None and packages is not None,
        "platform": conda,
        "folder": str(tools_dir()),
        # CHEMBOOK3D_LOCAL_PYTHON (tests) counts as installed tools
        "installed": marker is not None or worker_python() is not None,
        "up_to_date": bool(marker and packages and marker.get("list") == list_id(packages)),
        "versions": (marker or {}).get("versions") or {},
        "installed_at": (marker or {}).get("installed"),
        "install": {
            "state": current.state,
            "step": current.step,
            "error": current.error,
            "seconds": round((current.finished or time.time()) - current.started)
            if current.started
            else None,
        },
        "cores": res.cores,
        "memory_mb": res.memory_mb,
        "default_cores": default_cores(),
        "default_memory_mb": default_memory_mb(),
        "logical_cores": os.cpu_count() or 1,
        "total_memory_mb": total_memory_mb(),
    }


def main(argv: list[str] | None = None) -> int:
    """`python -m chembook3d.local_tools install`: install the tools without the app (CI)."""
    args = sys.argv[1:] if argv is None else argv
    if args != ["install"]:
        print("usage: python -m chembook3d.local_tools install")
        return 2
    conda = conda_platform()
    packages = package_list(conda)
    if conda is None or packages is None:
        print("Local calculations are offered on Windows and Linux (x86-64) only")
        return 1
    marker = install(conda, packages, lambda step: print(step, flush=True))
    print(json.dumps(marker, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
