"""Chembook3D local calculation worker (D213).

This file runs in the private tools environment (``local_tools.py``), not in the app's own
Python: the app copies it into ``local-jobs/<id>/worker.py`` and starts it as a detached
process, ``<tools python> -I worker.py <job folder> --lock <lock file>``, so the job keeps
running when the app stops. It may import only the standard library and the tools
environment's packages (numpy, geomeTRIC, tblite, Psi4), never ``chembook3d``.

Files in the job folder:
- ``job.json`` (written by the app): method, charge, multiplicity, cores, memory, elements and
  coordinates in Angstrom, optional constraints.
- ``progress.json`` (rewritten here): state (queued, running), pid, cycle, energy, largest
  gradient component, and a heartbeat time renewed every few seconds while the process lives.
- ``trajectory.xyz``: one frame per optimizer step, the comment holding the energy.
- ``result.json``: written once at the end, with the outcome and the final geometry.
- ``cancel``: created by the app to ask the job to stop.
- ``worker.log``: the optimizer's log and any error.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import threading
import time
import traceback
from pathlib import Path

HARTREE_TO_KCAL = 627.509474
BOHR_TO_ANGSTROM = 0.529177210903
HEARTBEAT_S = 5.0
LOCK_POLL_S = 2.0
WORKER_VERSION = 1
RESULT_FORMAT = "chembook3d-local-result"  # what parsers/local_result.py recognises

ELEMENTS = (
    "H He Li Be B C N O F Ne Na Mg Al Si P S Cl Ar K Ca Sc Ti V Cr Mn Fe Co Ni Cu Zn Ga Ge As "
    "Se Br Kr Rb Sr Y Zr Nb Mo Tc Ru Rh Pd Ag Cd In Sn Sb Te I Xe Cs Ba La Ce Pr Nd Pm Sm Eu Gd "
    "Tb Dy Ho Er Tm Yb Lu Hf Ta W Re Os Ir Pt Au Hg Tl Pb Bi Po At Rn"
).split()

# What each method is called in the level of theory of the imported calculation.
METHOD_LABELS = {
    "gfn2": ("tblite", "GFN2-xTB"),
    "gxtb": ("xTB", "g-xTB"),
    "b97-3c": ("Psi4", "B97-3c"),
    "r2scan-3c": ("Psi4", "r2SCAN-3c"),
    "oniom": ("Psi4", "ONIOM(r2SCAN-3c:GFN2-xTB)"),
    "stub": ("stub", "stub"),
    "oniom-stub": ("stub", "ONIOM(stub:stub-low)"),
    "stub-saddle": ("stub", "stub-saddle"),
}


class Cancelled(Exception):
    pass


def now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def write_json(path: Path, data: dict) -> None:
    """Replace ``path`` atomically; Windows refuses while a reader holds it, so retry."""
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=1), encoding="utf-8")
    for attempt in range(50):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            time.sleep(0.05 * (attempt + 1))
    os.replace(tmp, path)


class Progress:
    """progress.json, shared by the optimizer and the heartbeat thread."""

    def __init__(self, folder: Path):
        self.path = folder / "progress.json"
        self.lock = threading.Lock()
        self.data = {
            "state": "queued",
            "pid": os.getpid(),
            "worker_version": WORKER_VERSION,
            "queued_at": now(),
            "started_at": None,
            "heartbeat_at": now(),
            "cycle": 0,
            "energy": None,
            "max_gradient": None,
            "energies": [],
        }
        self.stopped = threading.Event()

    def update(self, **values) -> None:
        with self.lock:
            self.data.update(values)
            self.data["heartbeat_at"] = now()
            write_json(self.path, self.data)

    def beat(self) -> None:
        while not self.stopped.wait(HEARTBEAT_S):
            try:
                self.update()
            except OSError:
                pass


class Queue:
    """One local job at a time on this computer: a lock file in the tools folder."""

    def __init__(self, path: Path | None):
        self.path = path
        self.handle = None

    def _try(self) -> bool:
        assert self.handle is not None
        try:
            if os.name == "nt":
                import msvcrt

                self.handle.seek(0)
                msvcrt.locking(self.handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except OSError:
            return False

    def wait(self, cancelled) -> None:
        if self.path is None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.handle = open(self.path, "a+")  # noqa: SIM115 - held for the job's lifetime
        while not self._try():
            if cancelled():
                raise Cancelled()
            time.sleep(LOCK_POLL_S)


def read_constraints(job: dict, scanned: list[tuple[str, list, float]] | None = None) -> str | None:
    """geomeTRIC's constraint text from job.json's constraints (atoms counted from 0), with a
    scan's coordinates held at one point's values (D218)."""
    c = job.get("constraints") or {}
    freeze, held = [], []
    for kind, atoms, value in scanned or []:
        held.append(f"{kind} {' '.join(str(int(i) + 1) for i in atoms)} {float(value):.6f}")
    if c.get("frozen"):
        freeze.append("xyz " + ",".join(str(i + 1) for i in c["frozen"]))
    for kind in ("distance", "angle", "dihedral"):
        for item in c.get(kind + "s", []):
            atoms = " ".join(str(i + 1) for i in item["atoms"])
            if item.get("value") is None:
                freeze.append(f"{kind} {atoms}")
            else:
                held.append(f"{kind} {atoms} {float(item['value']):.6f}")
    if not freeze and not held:
        return None
    text = ""
    if freeze:
        text += "$freeze\n" + "\n".join(freeze) + "\n"
    if held:
        text += "$set\n" + "\n".join(held) + "\n"
    return text


def _stub(start, shrink: float, weight: float, delay: float):
    """Test engine: harmonic bonds towards the starting distances (Bohr) times `shrink`, so the
    optimizer has a few steps to take; it needs no tools environment."""
    import numpy as np

    n = len(start)
    pairs = [(i, j) for i in range(n) for j in range(i + 1, n)]
    target = {p: shrink * np.linalg.norm(start[p[0]] - start[p[1]]) for p in pairs}

    def stub(x):
        if delay:
            time.sleep(delay)
        e, g = 0.0, np.zeros_like(x)
        for i, j in pairs:
            d = x[i] - x[j]
            r = np.linalg.norm(d)
            w = weight / target[(i, j)] ** 2
            e += w * (r - target[(i, j)]) ** 2
            f = 2 * w * (r - target[(i, j)]) * d / r
            g[i] += f
            g[j] -= f
        return e, g

    return stub


def _saddle(start, delay: float):
    """Test engine with a saddle point: harmonic bonds at the starting distances, except
    between atoms 1 and 2, which sit in a double well whose barrier top is 5 % longer than
    their starting distance (a TS search climbs to it; its imaginary mode is that bond)."""
    import numpy as np

    rest = _stub(start, 1.0, 0.1, 0.0)
    top, width, height = 1.05 * float(np.linalg.norm(start[0] - start[1])), 0.3, 0.5

    def saddle(x):
        if delay:
            time.sleep(delay)
        e, g = rest(x)
        d = x[0] - x[1]
        r = float(np.linalg.norm(d))
        # the harmonic term rest() put on this pair is taken off again
        r0 = top / 1.05
        w = 0.1 / r0**2
        e -= w * (r - r0) ** 2
        f = -2 * w * (r - r0) * d / r
        u = (r - top) ** 2 - width**2
        e += height * u**2
        f += height * 4 * u * (r - top) * d / r
        g = g.copy()
        g[0] += f
        g[1] -= f
        return e, g

    return saddle


def _gfn2(numbers, charge: int, unpaired: int):
    import numpy as np
    from tblite.interface import Calculator

    def gfn2(x):
        calc = Calculator("GFN2-xTB", numbers, x, charge=charge, uhf=unpaired)
        calc.set("verbosity", 0)
        res = calc.singlepoint()
        return float(res.get("energy")), np.array(res.get("gradient"))

    return gfn2


def read_xtb_gradient(text: str, atoms: int):
    """D216: the energy (Hartree) and gradient (Hartree/Bohr, atoms x 3) of xtb's Turbomole
    `gradient` file: after the last `cycle` line, one line of coordinates per atom, then one
    of gradient per atom (Fortran D exponents allowed)."""
    import numpy as np

    lines = text.splitlines()
    starts = [i for i, line in enumerate(lines) if "cycle" in line and "energy" in line.lower()]
    if not starts:
        raise ValueError("no cycle in the gradient file")
    head = lines[starts[-1]]
    energy = float(head.lower().split("energy =")[1].split()[0].replace("D", "E"))
    rows = lines[starts[-1] + 1 + atoms : starts[-1] + 1 + 2 * atoms]
    grad = np.array([[float(v.replace("D", "E")) for v in row.split()[:3]] for row in rows])
    if grad.shape != (atoms, 3):
        raise ValueError("the gradient file is cut short")
    return energy, grad


def _gxtb(numbers, charge: int, unpaired: int, job: dict, folder: Path):
    """D216: g-xTB through its xtb program (`--gxtb --grad`), one run per step in
    `gxtb-run/`, whose restart file starts the next step's SCF."""
    import subprocess

    binary = Path(job["gxtb"])
    run_dir = folder / "gxtb-run"
    run_dir.mkdir(exist_ok=True)
    symbols = [ELEMENTS[z - 1] for z in numbers]
    env = dict(os.environ)
    env["PATH"] = str(binary.parent) + os.pathsep + env.get("PATH", "")  # its DLLs on Windows
    flags = 0x08000000 if sys.platform == "win32" else 0  # CREATE_NO_WINDOW

    def gxtb(x):
        ang = x * BOHR_TO_ANGSTROM
        (run_dir / "coord.xyz").write_text(xyz_frame(symbols, ang, "chembook3d"), encoding="utf-8")
        (run_dir / "gradient").unlink(missing_ok=True)
        argv = [str(binary), "coord.xyz", "--gxtb", "--grad", "--chrg", str(charge)]
        if unpaired:  # --uhf always makes it unrestricted, so only for open shells
            argv += ["--uhf", str(unpaired)]
        with (run_dir / "xtb.out").open("w", encoding="utf-8") as out:
            done = subprocess.run(  # noqa: S603 - the pinned g-xTB program
                argv,
                cwd=run_dir,
                env=env,
                stdout=out,
                stderr=subprocess.STDOUT,
                creationflags=flags,
                check=False,
            )
        gradient = run_dir / "gradient"
        if done.returncode != 0 or not gradient.is_file():
            tail = (run_dir / "xtb.out").read_text(encoding="utf-8", errors="replace")[-600:]
            raise RuntimeError(f"g-xTB failed (exit {done.returncode}): {tail}")
        return read_xtb_gradient(gradient.read_text(encoding="utf-8"), len(symbols))

    return gxtb


def _psi4(method: str, numbers, charge: int, unpaired: int, job: dict, folder: Path):
    import numpy as np

    scratch = folder / "scratch"  # PSI_SCRATCH: Psi4 wants it there when imported
    scratch.mkdir(exist_ok=True)
    import psi4

    psi4.core.set_output_file(str(folder / "psi4.out"), False)
    psi4.set_memory(int(job.get("memory_mb", 2000)) * 1_000_000)
    psi4.set_num_threads(max(1, int(job.get("cores", 1))))
    psi4.core.IOManager.shared_object().set_default_path(str(scratch))
    psi4.set_options(
        {
            "reference": "uks" if unpaired else "rks",
            "scf_type": "df",
            "maxiter": 200,
        }
    )
    symbols = [ELEMENTS[z - 1] for z in numbers]
    started = {"done": False}

    def dft(x):
        lines = [f"{charge} {unpaired + 1}"]
        lines += [
            f"{s} {a:.10f} {b:.10f} {c:.10f}" for s, (a, b, c) in zip(symbols, x, strict=True)
        ]
        lines += ["units bohr", "symmetry c1", "no_reorient", "no_com"]
        psi4.geometry("\n".join(lines))
        # From the second step on, the SCF starts from the last step's orbitals: a few
        # iterations instead of twenty. A start that fails falls back to a fresh guess.
        psi4.set_options({"guess": "read" if started["done"] else "sad"})
        try:
            grad, wfn = psi4.gradient(method, return_wfn=True)
        except psi4.SCFConvergenceError:
            if not started["done"]:
                raise
            psi4.set_options({"guess": "sad"})
            grad, wfn = psi4.gradient(method, return_wfn=True)
        started["done"] = True
        return float(wfn.energy()), np.array(grad)

    return dft


def _engine(method: str, numbers, start, job: dict, folder: Path, delay: bool = True):
    """One method on one system (atomic numbers, starting coordinates in Bohr); `delay` lets a
    test engine wait as the job asks (not while it computes a Hessian)."""
    wait = float(job.get("stub_delay_s", 0)) if delay else 0.0
    charge = int(job.get("charge", 0))
    unpaired = int(job.get("multiplicity", 1)) - 1
    if method == "stub":
        return _stub(start, 0.98, 0.1, wait)
    if method == "stub-saddle":
        return _saddle(start, wait)
    if method == "stub-low":  # a second, different test engine for ONIOM's low level
        return _stub(start, 0.95, 0.05, 0.0)
    if method == "gfn2":
        return _gfn2(numbers, charge, unpaired)
    if method == "gxtb":
        return _gxtb(numbers, charge, unpaired, job, folder)
    if method in ("b97-3c", "r2scan-3c"):
        return _psi4(method, numbers, charge, unpaired, job, folder)
    raise ValueError(f"unknown method {method!r}")


def model_coordinates(x, core, links):
    """The ONIOM model system: the core atoms, then one H per cut bond (inner, outer, g) at
    inner + g (outer - inner)."""
    import numpy as np

    caps = [x[a] + g * (x[b] - x[a]) for a, b, g in links]
    return np.vstack([x[core]] + ([np.array(caps)] if caps else []))


def oniom(real, high, low_model, core, links):
    """Two-layer subtractive ONIOM, E = E_low(real) + E_high(model) - E_low(model) (D214).
    `real` and `low_model` are the low level on the whole molecule and on the model, `high`
    the high level on the model. A link atom's gradient goes to the atoms of its cut bond:
    1 - g to the inner one, g to the outer one (the chain rule of its position)."""
    import numpy as np

    core = list(core)

    def calculate(x):
        e_real, g_real = real(x)
        m = model_coordinates(x, core, links)
        e_high, g_high = high(m)
        e_low, g_low = low_model(m)
        energy = e_real + e_high - e_low
        diff = np.asarray(g_high) - np.asarray(g_low)
        grad = np.array(g_real, float).reshape(-1, 3).copy()
        grad[core] += diff[: len(core)]
        for (a, b, g), d in zip(links, diff[len(core) :], strict=True):
            grad[a] += (1 - g) * d
            grad[b] += g * d
        return energy, grad

    return calculate


HESSIAN_STEP = 0.005  # Bohr, central differences of the gradient
MODE_SHARE = 0.5  # the reacting atoms carry at least this share of the mode: it stays on them


def numerical_hessian(calculate, x, progress=None, what="Hessian"):
    """Hartree/Bohr^2 from central differences of the gradient, symmetrised."""
    import numpy as np

    n = x.size
    hessian = np.zeros((n, n))
    flat = x.reshape(-1)
    for k in range(n):
        plus, minus = flat.copy(), flat.copy()
        plus[k] += HESSIAN_STEP
        minus[k] -= HESSIAN_STEP
        g_plus = np.asarray(calculate(plus.reshape(-1, 3))[1], float).reshape(-1)
        g_minus = np.asarray(calculate(minus.reshape(-1, 3))[1], float).reshape(-1)
        hessian[k] = (g_plus - g_minus) / (2 * HESSIAN_STEP)
        if progress is not None and (k % 6 == 5 or k == n - 1):
            progress.update(phase=f"{what} {k + 1}/{n}")
    return (hessian + hessian.T) / 2


def moving_atoms(mode, share: float = 0.3, limit: int = 8) -> list[int]:
    """The atoms (from 0) a normal mode moves most: at least `share` of the largest, at most
    `limit`, largest first."""
    import numpy as np

    size = np.linalg.norm(np.asarray(mode, float).reshape(-1, 3), axis=1)
    order = [int(i) for i in np.argsort(-size)]
    return [i for i in order[:limit] if size[i] >= share * size[order[0]]]


def harmonic(symbols, x, hessian):
    """The harmonic frequencies (cm-1, imaginary ones negative, lowest first) of a Hessian at
    `x` (Bohr) and its normal modes as normalised Cartesian displacements [mode][atom][xyz],
    translations and rotations projected out."""
    import numpy as np
    from geometric.normal_modes import frequency_analysis

    freqs, modes, _ = frequency_analysis(np.asarray(x).reshape(-1), hessian, elem=list(symbols))
    return np.asarray(freqs, float), np.asarray(modes, float).reshape(len(freqs), -1, 3)


def mode_check(symbols, x, hessian, reacting=None) -> dict:
    """D215: the harmonic frequencies of a Hessian (cm-1), its imaginary ones, the atoms the
    first imaginary mode moves (and that mode, D217, to animate), and its share on the
    reacting atoms."""
    import numpy as np

    freqs, modes = harmonic(symbols, x, hessian)
    imaginary = [round(float(f), 1) for f in freqs if f < 0]
    out: dict = {"imaginary": imaginary, "lowest": [round(float(f), 1) for f in freqs[:3]]}
    if imaginary:
        mode = np.asarray(modes[0], float).reshape(-1, 3)
        out["atoms"] = moving_atoms(mode)
        out["mode"] = mode.round(5).tolist()
        if reacting:
            weight = (mode**2).sum(axis=1)
            share = float(weight[list(reacting)].sum() / weight.sum())
            out["share_on_reacting"] = round(share, 3)
            out["on_reacting"] = share >= MODE_SHARE
    return out


def make_calculator(job: dict, numbers, folder: Path):
    """A function from coordinates (Bohr, n x 3) to (energy in Hartree, gradient Hartree/Bohr)."""
    import numpy as np

    method = job["method"]
    start = np.array(job["coordinates"], float) / BOHR_TO_ANGSTROM
    if method not in ("oniom", "oniom-stub"):
        return _engine(method, numbers, start, job, folder)
    layers = job["oniom"]
    core = [int(i) for i in layers["core"]]
    links = [(int(a), int(b), float(g)) for a, b, g in layers["links"]]
    model_numbers = np.concatenate([np.asarray(numbers)[core], np.ones(len(links), int)])
    model_start = model_coordinates(start, core, links)
    return oniom(
        _engine(layers["low"], numbers, start, job, folder),
        _engine(layers["high"], model_numbers, model_start, job, folder),
        _engine(layers["low"], model_numbers, model_start, job, folder),
        core,
        links,
    )


def versions(method: str) -> dict:
    from importlib import metadata

    found = {"python": sys.version.split()[0]}
    names = ["numpy", "geometric"]
    if method in ("gfn2", "oniom"):
        names.append("tblite")
    if method in ("b97-3c", "r2scan-3c", "oniom"):
        names += ["psi4", "dftd4", "dftd3"]
    for name in names:
        try:
            found[name] = metadata.version(name)
        except metadata.PackageNotFoundError:
            try:
                found[name] = str(__import__(name).__version__)
            except Exception:  # noqa: BLE001 - a version is only informative
                pass
    return found


def scan_values(scan: dict) -> list[list[float]]:
    """D218: per point of a relaxed scan, the value of each of its coordinates (Å or degrees):
    every coordinate goes from its start to its end in the same number of steps (concerted)."""
    import numpy as np

    n = int(scan["points"])
    columns = [np.linspace(c["start"], c["end"], n) for c in scan["coordinates"]]
    return [[float(column[k]) for column in columns] for k in range(n)]


def xyz_frame(symbols, coords_ang, comment: str) -> str:
    lines = [str(len(symbols)), comment]
    lines += [
        f"{s:<2} {a:14.8f} {b:14.8f} {c:14.8f}"
        for s, (a, b, c) in zip(symbols, coords_ang, strict=True)
    ]
    return "\n".join(lines) + "\n"


def run(folder: Path, lock: Path | None) -> int:
    import numpy as np

    job = json.loads((folder / "job.json").read_text(encoding="utf-8"))
    progress = Progress(folder)
    progress.update()
    beat = threading.Thread(target=progress.beat, daemon=True)
    beat.start()
    cancel_file = folder / "cancel"
    log = open(folder / "worker.log", "a", encoding="utf-8")  # noqa: SIM115
    method = job["method"]
    symbols = list(job["elements"])
    numbers = np.array([ELEMENTS.index(s) + 1 for s in symbols])
    start = np.array(job["coordinates"], float)
    program, label = METHOD_LABELS.get(method, (method, method))
    result = {
        "format": RESULT_FORMAT,
        "worker_version": WORKER_VERSION,
        "method": method,
        "program": program,
        "level": job.get("level") or label,
        "charge": int(job.get("charge", 0)),
        "multiplicity": int(job.get("multiplicity", 1)),
        "constraints": job.get("constraints") or None,
        "transition": bool(job.get("transition", False)),
    }
    if job.get("oniom"):
        result["oniom"] = job["oniom"]
    t0 = time.time()
    energies: list[float] = []
    frames: list[list[list[float]]] = []
    last: dict = {"coords": start, "energy": None}
    frame_points: list[int] = []  # D218: per frame, the scan point it belongs to (from 1)
    point = {"index": 1}
    try:
        queue = Queue(lock)
        queue.wait(cancel_file.exists)
        progress.update(state="running", started_at=now())
        result["versions"] = versions(method)
        if job.get("gxtb_version"):
            result["versions"]["xtb"] = job["gxtb_version"]
        calculate = make_calculator(job, numbers, folder)

        import geometric
        import geometric.engine
        import geometric.errors
        import geometric.molecule
        import geometric.optimize

        traj = folder / "trajectory.xyz"
        traj.write_text("", encoding="utf-8")

        class Engine(geometric.engine.Engine):
            def calc_new(self, coords, dirname):
                if cancel_file.exists():
                    raise Cancelled()
                x = np.asarray(coords, float).reshape(-1, 3)
                energy, grad = calculate(x)
                grad = np.asarray(grad, float).reshape(-1, 3)
                ang = x * BOHR_TO_ANGSTROM
                # D218: a scan point starts on the last one's structure; it is kept once.
                again = bool(frames) and np.allclose(ang, last["coords"], atol=1e-7)
                if not again:
                    energies.append(energy)
                    frames.append(ang.round(6).tolist())
                    frame_points.append(point["index"])
                    with traj.open("a", encoding="utf-8") as fh:
                        fh.write(
                            xyz_frame(symbols, ang, f"cycle {len(energies)} energy {energy:.10f}")
                        )
                last["coords"], last["energy"] = ang, energy
                progress.update(
                    cycle=len(energies),
                    energy=energy,
                    max_gradient=float(np.abs(grad).max()),
                    energies=energies[-200:],
                )
                return {"energy": energy, "gradient": grad.ravel()}

        molecule = geometric.molecule.Molecule()
        molecule.elem = symbols
        molecule.xyzs = [start.copy()]
        engine = Engine(molecule)
        options = {
            "customengine": engine,
            "input": str(folder / "geometric"),
            "maxiter": int(job.get("maxiter", 300)),
            "convergence_set": job.get("convergence", "GAU"),
            "transition": bool(job.get("transition", False)),
        }
        text = read_constraints(job)
        if text:
            (folder / "constraints.txt").write_text(text, encoding="utf-8")
            options["constraints"] = str(folder / "constraints.txt")
        if job.get("hessian"):
            options["hessian"] = job["hessian"]
        hessian_engine = None
        frequency_engine = None
        if job.get("frequencies"):
            # D217: frequencies at the job's own level once it has converged (cheap methods
            # only: GFN2-xTB, g-xTB), imported as a frequency calculation on the result.
            own = _engine(method, numbers, start / BOHR_TO_ANGSTROM, job, folder, delay=False)

            def frequency_engine(x, raw=own):
                if cancel_file.exists():
                    raise Cancelled()
                return raw(x)

        if job.get("transition"):
            # D215: geomeTRIC starts the TS search from a Hessian; a cheap one (GFN2-xTB on the
            # whole molecule, by finite differences) instead of one at the job's own level.
            raw = _engine(
                job.get("ts_hessian", "gfn2"),
                numbers,
                start / BOHR_TO_ANGSTROM,
                job,
                folder,
                delay=False,
            )

            def hessian_engine(x, raw=raw):
                if cancel_file.exists():
                    raise Cancelled()
                return raw(x)

            x0 = start / BOHR_TO_ANGSTROM
            first = numerical_hessian(hessian_engine, x0, progress, "Starting Hessian")
            np.savetxt(folder / "hessian.txt", first)
            options["hessian"] = "file:" + str(folder / "hessian.txt")
            result["ts_check"] = {"start": mode_check(symbols, x0, first, job.get("reacting"))}
            result["ts_check"]["start"].pop("mode", None)  # only the final mode is shown
            progress.update(phase=None)
        converged = True
        _redirect_geometric_log(log)
        scan = job.get("scan")
        if scan:
            # D218: a relaxed scan, one constrained optimization per point, each started from
            # the one before; a point that does not converge is kept and the scan goes on.
            values = scan_values(scan)
            points = []
            for k, value in enumerate(values):
                point["index"] = k + 1
                progress.update(phase=f"Point {k + 1} of {len(values)}")
                held = [
                    (c["kind"], c["atoms"], v)
                    for c, v in zip(scan["coordinates"], value, strict=True)
                ]
                text = read_constraints(job, held)
                (folder / "constraints.txt").write_text(text or "", encoding="utf-8")
                molecule.xyzs = [np.asarray(last["coords"], float).copy()]
                options["customengine"] = Engine(molecule)
                options["constraints"] = str(folder / "constraints.txt")
                done = True
                try:
                    geometric.optimize.run_optimizer(**options)
                except geometric.errors.GeomOptNotConvergedError:
                    done = False
                points.append(
                    {
                        "values": [round(v, 6) for v in value],
                        "frame": len(frames) - 1,
                        "energy": last["energy"],
                        "converged": done,
                    }
                )
                converged = converged and done
            result["scan"] = {**scan, "points": points}
            progress.update(phase=None)
        else:
            try:
                geometric.optimize.run_optimizer(**options)
            except geometric.errors.GeomOptNotConvergedError:
                converged = False
        result["state"] = "converged" if converged else "not_converged"
        x1 = np.asarray(last["coords"]) / BOHR_TO_ANGSTROM
        if frequency_engine is not None and frames and converged:
            final = numerical_hessian(frequency_engine, x1, progress, "Frequencies")
            freqs, modes = harmonic(symbols, x1, final)
            result["frequencies"] = {
                "level": result["level"],
                "energy": last["energy"],
                "wavenumbers": freqs.round(2).tolist(),
                "modes": modes.round(5).tolist(),
            }
            if hessian_engine is not None:  # the TS check is read from the same Hessian
                result["ts_check"]["end"] = mode_check(symbols, x1, final, job.get("reacting"))
                result["ts_check"]["level"] = label
        elif hessian_engine is not None and frames:
            final = numerical_hessian(hessian_engine, x1, progress, "Final Hessian")
            result["ts_check"]["end"] = mode_check(symbols, x1, final, job.get("reacting"))
            name = job.get("ts_hessian", "gfn2")
            result["ts_check"]["level"] = METHOD_LABELS.get(name, (name, name))[1]
        progress.update(phase=None)
    except Cancelled:
        result["state"] = "cancelled"
    except Exception as exc:  # noqa: BLE001 - every failure goes into result.json
        result["state"] = "failed"
        result["error"] = f"{type(exc).__name__}: {exc}"
        log.write(traceback.format_exc())
    result["cycles"] = len(energies)
    result["energies"] = energies
    result["frames"] = frames
    if job.get("scan"):
        result["frame_points"] = frame_points
    result["energy"] = last["energy"]
    result["elements"] = symbols
    result["coordinates"] = np.asarray(last["coords"]).round(8).tolist()
    result["elapsed_s"] = round(time.time() - t0, 1)
    result["finished_at"] = now()
    progress.stopped.set()
    shutil.rmtree(folder / "scratch", ignore_errors=True)  # Psi4's integrals and orbitals
    shutil.rmtree(folder / "gxtb-run", ignore_errors=True)  # g-xTB's last run
    write_json(folder / "result.json", result)
    progress.update(state="finished")
    log.close()
    return 0 if result["state"] in ("converged", "not_converged") else 1


def _redirect_geometric_log(log) -> None:
    """Send geomeTRIC's log to worker.log instead of the (absent) console."""
    import logging

    handler = logging.StreamHandler(log)
    handler.setFormatter(logging.Formatter("%(message)s"))
    for name in ("geometric", "geometric.nifty"):
        lg = logging.getLogger(name)
        lg.handlers = [handler]
        lg.setLevel(logging.INFO)
        lg.propagate = False


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("folder", type=Path)
    parser.add_argument("--lock", type=Path, default=None)
    args = parser.parse_args(argv)
    folder = args.folder.resolve()
    return run(folder, args.lock)


if __name__ == "__main__":
    sys.exit(main())
