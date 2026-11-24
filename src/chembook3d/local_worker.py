"""Chembook3D local calculation worker (D213).

This file runs in the private tools environment (``local_tools.py``), not in the app's own
Python: the app copies it into ``local-jobs/<id>/worker.py`` and starts it as a detached
process, ``<tools python> -I worker.py <job folder> --lock <lock file>``, so the job keeps
running when the app stops. It may import only the standard library and the tools
environment's packages (numpy, geomeTRIC, tblite, Psi4; in the MACE environment, D237, also
PyTorch, ASE, Sella and mace-torch), never ``chembook3d``.

Files in the job folder:
- ``job.json`` (written by the app): method, charge, multiplicity, cores, memory, elements and
  coordinates in Angstrom, optional constraints; D240: a ``single_point`` job computes the
  energy of those coordinates only (Psi4), its level in ``job["single_point"]``.
- ``progress.json`` (rewritten here): state (queued, running), pid, cycle, energy, largest
  gradient component, and a heartbeat time renewed every few seconds while the process lives.
- ``trajectory.xyz``: one frame per optimizer step, the comment holding the energy (and, D220,
  a scan's point: ``cycle 4 energy -5.07 point 2``).
- ``scan-points.jsonl``: D220, a scan's points as each one ends (as ``result.json`` lists them).
- ``criteria.jsonl``: D220, one line per optimizer step after the first, the convergence criteria
  geomeTRIC judged that step's structure by (``record_criteria``), for the Inspect window.
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
EV_TO_HARTREE = 1 / 27.211386245988
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
    "mace-omol": ("MACE", "MACE-OMOL-0"),  # D237
    "mace-stub": ("stub", "mace-stub"),
    "psi4-sp": ("Psi4", "single point"),  # D240: the level is in the job
    "stub-sp": ("stub", "single point"),
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


def _mace(numbers, start, charge: int, multiplicity: int, job: dict):
    """D237: MACE-OMOL-0 (mace-torch's MACECalculator, its "omol" head, double precision on
    the CPU). The model reads the total charge and the spin multiplicity, as OMol25 was
    computed. Its Hessians come from differences of gradients, as the other methods' do: the
    model's own second derivative needs more memory than a computer may have (over 13 GB for
    73 atoms)."""
    import numpy as np
    import torch
    from ase import Atoms
    from mace.calculators import MACECalculator

    torch.set_num_threads(max(1, int(job.get("cores", 1))))
    calculator = MACECalculator(
        model_paths=job["mace_model"], device="cpu", default_dtype="float64", head="omol"
    )
    atoms = Atoms(numbers=list(numbers), positions=np.asarray(start) * BOHR_TO_ANGSTROM)
    atoms.info["charge"] = charge
    atoms.info["spin"] = multiplicity
    atoms.calc = calculator

    def mace(x):
        atoms.positions = np.asarray(x, float).reshape(-1, 3) * BOHR_TO_ANGSTROM
        energy = float(atoms.get_potential_energy()) * EV_TO_HARTREE
        forces = np.asarray(atoms.get_forces(), float)
        return energy, -forces * EV_TO_HARTREE * BOHR_TO_ANGSTROM

    return mace


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
    if method == "mace-omol":
        return _mace(numbers, start, charge, unpaired + 1, job)
    if method == "mace-stub":  # D237: the tests' MACE, run in the MACE environment's place
        return _stub(start, 0.98, 0.1, wait)
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
    if method in ("b97-3c", "r2scan-3c", "oniom", "psi4-sp"):
        names += ["psi4", "dftd4", "dftd3"]
    if method == "mace-omol":
        names += ["torch", "mace-torch", "ase"]
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
    every coordinate goes from its start to its end in the same number of steps (concerted).
    D219: a grid's points are every pair of its two coordinates' values, run row by row with
    every other row backwards, so each point starts from a neighbour."""
    import numpy as np

    if scan.get("grid"):
        return [[a, b] for a, b, _ in grid_cells(scan)]
    n = int(scan["points"])
    columns = [np.linspace(c["start"], c["end"], n) for c in scan["coordinates"]]
    return [[float(column[k]) for column in columns] for k in range(n)]


def grid_cells(scan: dict) -> list[tuple[float, float, list[int]]]:
    """D219: a grid's points in the order run: both values and the cell [i, j]."""
    import numpy as np

    first, second = scan["coordinates"]
    rows = np.linspace(first["start"], first["end"], int(first["points"]))
    columns = np.linspace(second["start"], second["end"], int(second["points"]))
    out = []
    for i, a in enumerate(rows):
        order = range(len(columns)) if i % 2 == 0 else range(len(columns) - 1, -1, -1)
        out += [(float(a), float(columns[j]), [i, j]) for j in order]
    return out


CRITERIA = "criteria.jsonl"
SCAN_POINTS = "scan-points.jsonl"


def record_criteria(store) -> None:
    """D220: hand `store` the five criteria geomeTRIC checks each new structure against, as it
    works them out in ``Optimizer.evaluateStep`` (the gradient with any held coordinates
    projected out, the displacement after aligning on the structure before), each as
    [name, value, threshold, unit]. Its log prints them only with colour codes."""
    import geometric.optimize
    from geometric.step import calc_drms_dmax

    optimizer = geometric.optimize.Optimizer
    original = getattr(optimizer.evaluateStep, "chembook3d_original", optimizer.evaluateStep)

    def evaluate_step(self):
        try:
            p = self.params
            grms, gmax = self.calcGradNorm()
            drms, dmax = calc_drms_dmax(self.X, self.Xprev)
            store(
                [
                    ["Energy change", float(self.E - self.Eprev), p.Convergence_energy, "Eh"],
                    ["RMS gradient", float(grms), p.Convergence_grms, "Eh/bohr"],
                    ["Maximum gradient", float(gmax), p.Convergence_gmax, "Eh/bohr"],
                    ["RMS displacement", float(drms), p.Convergence_drms, "Å"],
                    ["Maximum displacement", float(dmax), p.Convergence_dmax, "Å"],
                ]
            )
        except Exception:  # noqa: BLE001, S110 - a view only; never stop the optimization
            pass
        return original(self)

    evaluate_step.chembook3d_original = original
    optimizer.evaluateStep = evaluate_step


def xyz_frame(symbols, coords_ang, comment: str) -> str:
    lines = [str(len(symbols)), comment]
    lines += [
        f"{s:<2} {a:14.8f} {b:14.8f} {c:14.8f}"
        for s, (a, b, c) in zip(symbols, coords_ang, strict=True)
    ]
    return "\n".join(lines) + "\n"


# ---------- D238: a TS guess between two minima ----------

TS_IMAGES = 7  # IDPP images between the two ends
END_FMAX = 0.02  # eV/Å: the ends' minima (about Gaussian's default largest force)
SADDLE_FMAX = 0.01  # eV/Å: the saddle point
SADDLE_STEPS = 300
HUMP_KCAL = 1.0  # a second maximum of the band counts when it stands this far above its valleys
WAVENUMBER = 5140.4872  # cm-1 per sqrt(Hartree / (Bohr² amu))
# D244: a smaller imaginary wavenumber is noise of a partial Hessian, not a mode. It was 50; a
# Ru=C rotation TS (DFT -36 cm-1) has a real MACE mode of -16 cm-1, and five Ru saddles checked
# with no cut-off showed no spurious mode.
SPURIOUS_CM = 10.0
REGION_BONDS = 2  # D244: the mode check's region, in bonds out from the reacting atoms
BOND_SCALE = 1.25  # bonded: closer than this times the sum of covalent radii


def engine_calculator(calculate, cancelled):
    """An ASE calculator of a worker engine (Bohr and Hartree in, eV and Å out)."""
    import numpy as np
    from ase.calculators.calculator import Calculator, all_changes

    class EngineCalculator(Calculator):
        implemented_properties = ["energy", "forces"]

        def calculate(self, atoms=None, properties=("energy",), system_changes=all_changes):
            super().calculate(atoms, properties, system_changes)
            if cancelled():
                raise Cancelled()
            energy, grad = calculate(np.asarray(self.atoms.positions) / BOHR_TO_ANGSTROM)
            grad = np.asarray(grad, float).reshape(-1, 3)
            self.results = {
                "energy": float(energy) / EV_TO_HARTREE,
                "forces": -grad / EV_TO_HARTREE / BOHR_TO_ANGSTROM,
            }

    return EngineCalculator()


def aligned(moving, reference):
    """`moving` (n x 3) turned and shifted onto `reference` by least squares (Kabsch)."""
    import numpy as np

    a = np.asarray(moving, float)
    b = np.asarray(reference, float)
    ca, cb = a.mean(axis=0), b.mean(axis=0)
    u, _, vt = np.linalg.svd((a - ca).T @ (b - cb))
    d = np.sign(np.linalg.det(u @ vt))
    rotation = u @ np.diag([1.0, 1.0, d]) @ vt
    return (a - ca) @ rotation + cb


def bonded(symbols, coords) -> list[tuple[int, int]]:
    """Pairs closer than BOND_SCALE times their covalent radii (geomeTRIC's radii)."""
    import numpy as np
    from geometric.molecule import Elements, Radii  # covalent radii in Å from H on

    x = np.asarray(coords, float)
    radii = [Radii[Elements.index(s) - 1] for s in symbols]
    out = []
    for i in range(len(x)):
        for j in range(i + 1, len(x)):
            if np.linalg.norm(x[i] - x[j]) < BOND_SCALE * (radii[i] + radii[j]):
                out.append((i, j))
    return out


def changed_bonds(symbols, before, after) -> list[str]:
    """D244: the bonds (by `bonded`) that `after` has and `before` lacks (+) or the reverse (-),
    as "+C1–C2" with atoms counted from 1."""
    old, new = set(bonded(symbols, before)), set(bonded(symbols, after))
    return [
        f"{sign}{symbols[i]}{i + 1}–{symbols[j]}{j + 1}"
        for sign, pairs in (("+", new - old), ("-", old - new))
        for i, j in sorted(pairs)
    ]


def humps(energies, least: float = HUMP_KCAL) -> list[int]:
    """The images that are maxima of a band (energies in kcal/mol) by their prominence: each
    stands at least `least` above the higher of its two valleys, a valley being the lowest point
    between it and the nearest higher image (or the band's end) on that side."""
    found = []
    n = len(energies)
    for k in range(1, n - 1):
        if not (energies[k] >= energies[k - 1] and energies[k] > energies[k + 1]):
            continue
        left = k - 1
        while left > 0 and energies[left] <= energies[k]:
            left -= 1
        right = k + 1
        while right < n - 1 and energies[right] <= energies[k]:
            right += 1
        valleys = (min(energies[left:k]), min(energies[k + 1 : right + 1]))
        if energies[k] - max(valleys) >= least:
            found.append(k)
    return found


def stretch(x, mode, pair) -> float:
    """How much a (normalized) mode stretches the bond between a pair of atoms: the change of
    its length along the mode, first order."""
    import numpy as np

    x = np.asarray(x, float)
    m = np.asarray(mode, float)
    a, b = (int(i) for i in pair)
    unit = (x[a] - x[b]) / np.linalg.norm(x[a] - x[b])
    return abs(float(unit @ (m[a] - m[b])))


def partial_mode_check(symbols, x, calculate, region, reacting, progress=None) -> dict:
    """D238: the Hessian of the `region` atoms only (central differences of their gradient,
    the rest held), mass-weighted: its imaginary wavenumbers (cm-1, noise under SPURIOUS_CM
    left out), the first one's mode over the whole molecule (zero outside the region) and its
    share on the `reacting` atoms."""
    import numpy as np
    from geometric.molecule import PeriodicTable

    region = sorted(set(int(a) for a in region))
    columns = [3 * a + k for a in region for k in range(3)]
    flat = np.asarray(x, float).reshape(-1)
    size = len(columns)
    hessian = np.zeros((size, size))
    for row, c in enumerate(columns):
        plus, minus = flat.copy(), flat.copy()
        plus[c] += HESSIAN_STEP
        minus[c] -= HESSIAN_STEP
        g_plus = np.asarray(calculate(plus.reshape(-1, 3))[1], float).reshape(-1)[columns]
        g_minus = np.asarray(calculate(minus.reshape(-1, 3))[1], float).reshape(-1)[columns]
        hessian[row] = (g_plus - g_minus) / (2 * HESSIAN_STEP)
        if progress is not None and (row % 6 == 5 or row == size - 1):
            progress.update(phase=f"Mode check {row + 1}/{size}")
    hessian = (hessian + hessian.T) / 2
    if len(region) == len(symbols):
        # the whole molecule: translations and rotations projected out, as for a full Hessian
        return {**mode_check(symbols, x, hessian, reacting), "region": region, "partial": False}
    masses = np.repeat([PeriodicTable[symbols[a]] for a in region], 3)
    weighted = hessian / np.sqrt(np.outer(masses, masses))
    values, vectors = np.linalg.eigh(weighted)
    wavenumbers = np.sign(values) * np.sqrt(np.abs(values)) * WAVENUMBER
    imaginary = [round(float(w), 1) for w in wavenumbers if w < -SPURIOUS_CM]
    out: dict = {
        "imaginary": imaginary,
        "lowest": [round(float(w), 1) for w in wavenumbers[:3]],
        "region": region,
        "partial": True,
    }
    if imaginary:
        cartesian = vectors[:, 0] / np.sqrt(masses)
        mode = np.zeros((len(symbols), 3))
        mode[region] = (cartesian / np.linalg.norm(cartesian)).reshape(-1, 3)
        out["atoms"] = moving_atoms(mode)
        out["mode"] = mode.round(5).tolist()
        if reacting:
            weight = (mode**2).sum(axis=1)
            share = float(weight[list(reacting)].sum() / weight.sum())
            out["share_on_reacting"] = round(share, 3)
            out["on_reacting"] = share >= MODE_SHARE
    return out


def ts_guess(job, calculate, symbols, folder: Path, progress, cancelled, record) -> dict:
    """D238: a TS guess between the job's two ends (start in job.json's coordinates, the end in
    ts_guess.end, same atom order): each end optimized to a minimum (Sella), an IDPP band of
    TS_IMAGES images between them scored with the method, Sella's saddle search (order 1,
    internal coordinates) from the band's highest image, and a check of the saddle's Hessian on
    the reacting region. `record(coords_ang, energy)` keeps each step of the saddle search.
    Returns what result.json's `ts_guess` holds, with the saddle's coordinates (Å)."""
    import numpy as np
    from ase import Atoms
    from ase.mep import NEB
    from sella import Sella

    spec = job["ts_guess"]
    numbers = [ELEMENTS.index(s) + 1 for s in symbols]
    warnings: list[str] = []
    out: dict = {"warnings": warnings}

    def atoms_at(coords):
        found = Atoms(numbers=numbers, positions=np.asarray(coords, float))
        found.calc = engine_calculator(calculate, cancelled)
        return found

    def kcal(atoms) -> float:
        return float(atoms.get_potential_energy()) * EV_TO_HARTREE * HARTREE_TO_KCAL

    log = str(folder / "sella.log")
    ends = {}
    for which, coords in (("start", job["coordinates"]), ("end", spec["end"])):
        progress.update(phase=f"Optimizing the {which} (minimum)")
        atoms = atoms_at(coords)
        if spec.get("optimize_ends", True):
            optimizer = Sella(atoms, order=0, internal=True, logfile=log)
            done = bool(optimizer.run(fmax=END_FMAX, steps=SADDLE_STEPS))
        else:
            done = True
        moved = float(
            np.sqrt(
                ((aligned(atoms.positions, coords) - np.asarray(coords)) ** 2).sum(axis=1).mean()
            )
        )
        ends[which] = {
            "coordinates": np.asarray(atoms.positions).round(6).tolist(),
            "energy": float(atoms.get_potential_energy()) * EV_TO_HARTREE,
            "converged": done,
            "rmsd_from_node": round(moved, 3),
        }
        if not done:
            warnings.append(f"The {which} did not reach a minimum in {SADDLE_STEPS} steps.")
        changed = changed_bonds(symbols, coords, atoms.positions)
        if changed:
            # D244: the method does not keep this end as a minimum (a pi-complex closing to the
            # metallacycle): the band and saddle would join the wrong structures, so the
            # node's own structure stands for this end.
            warnings.append(
                f"The {which}'s {spec['method_label']} minimum changed its bonds "
                f"({', '.join(changed)}): the method does not keep this structure as a minimum, so "
                "the node's own structure is used for this end. Check the guess against it."
            )
            atoms.positions = np.asarray(coords, float)
            ends[which].update(
                coordinates=np.asarray(atoms.positions).round(6).tolist(),
                energy=float(atoms.get_potential_energy()) * EV_TO_HARTREE,
                rmsd_from_node=0.0,
                kept_node=True,
                changed_bonds=changed,
            )
        (folder / f"{which}-minimum.xyz").write_text(
            xyz_frame(symbols, atoms.positions, f"{which} minimum, {spec['method_label']}"),
            encoding="utf-8",
        )
    out["ends"] = ends
    first = np.asarray(ends["start"]["coordinates"])
    last = aligned(ends["end"]["coordinates"], first)

    progress.update(phase=f"IDPP band of {TS_IMAGES} images")
    images = [atoms_at(first)]
    images += [atoms_at(first) for _ in range(TS_IMAGES)]
    images.append(atoms_at(last))
    NEB(images, method="improvedtangent").interpolate(method="idpp")  # only interpolated
    band = []
    for k, image in enumerate(images):
        progress.update(phase=f"Scoring the band {k + 1}/{len(images)}")
        band.append(kcal(image))
    lowest = min(band[0], band[-1])
    relative = [round(e - lowest, 2) for e in band]
    tops = humps(relative)
    top = max(range(1, len(images) - 1), key=lambda k: band[k])
    out["band"] = {"kcal": relative, "top": top, "maxima": tops}
    with (folder / "band.xyz").open("w", encoding="utf-8") as fh:
        for k, image in enumerate(images):
            fh.write(xyz_frame(symbols, image.positions, f"image {k} {relative[k]:.2f} kcal/mol"))
    if len(tops) > 1:
        warnings.append(
            f"The straight path between the ends has {len(tops)} maxima (images "
            f"{', '.join(str(k) for k in tops)}): there may be an intermediate between them. The "
            "guess starts from the highest; consider a structure in between."
        )
    if top in (1, len(images) - 2) and band[top] - max(band[0], band[-1]) < HUMP_KCAL:
        warnings.append("The band has no clear maximum between the ends.")

    progress.update(phase="Saddle search (Sella)")
    saddle = atoms_at(images[top].positions)
    optimizer = Sella(saddle, order=1, internal=True, logfile=log)

    def keep():
        record(np.asarray(saddle.positions), float(saddle.get_potential_energy()) * EV_TO_HARTREE)

    optimizer.attach(keep, interval=1)
    converged = bool(optimizer.run(fmax=SADDLE_FMAX, steps=SADDLE_STEPS))
    out["converged"] = converged
    if not converged:
        warnings.append(f"The saddle search did not converge in {SADDLE_STEPS} steps.")
    x = np.asarray(saddle.positions)
    reacting = [int(a) for a in spec.get("reacting") or []]
    pairs = bonded(symbols, x)
    region = set(reacting)
    for _ in range(REGION_BONDS):  # D244: two bonds out (was one)
        region |= {k for i, j in pairs if i in region or j in region for k in (i, j)}
    out["mode_check"] = partial_mode_check(
        symbols, x / BOHR_TO_ANGSTROM, calculate, sorted(region), reacting, progress
    )
    check = out["mode_check"]
    changing = [list(b) for b in spec.get("formed", []) + spec.get("broken", [])]
    if changing and check.get("mode"):
        # The mode is on the bonds that change when it stretches one of them more than any
        # other bond of the region: the share of its amplitude is swayed by hydrogens, which
        # move most in Cartesian terms even when a C–C bond is what forms.
        inside = set(check["region"])
        others = [p for p in pairs if p[0] in inside and p[1] in inside]
        others = [p for p in others if sorted(p) not in [sorted(c) for c in changing]]
        mine = max(stretch(x, check["mode"], c) for c in changing)
        theirs = max((stretch(x, check["mode"], p) for p in others), default=0.0)
        check["stretch"] = {"changing": round(mine, 3), "other": round(theirs, 3)}
        check["on_reacting"] = mine >= theirs
    if len(check["imaginary"]) != 1:
        warnings.append(
            f"The saddle has {len(check['imaginary'])} imaginary modes on the reacting region, "
            "not one."
        )
    elif reacting and not check.get("on_reacting"):
        warnings.append("Its imaginary mode is not on the bonds that change.")
    out["bonds"] = [
        {
            "atoms": [int(a), int(b)],
            "start": round(float(np.linalg.norm(first[a] - first[b])), 3),
            "saddle": round(float(np.linalg.norm(x[a] - x[b])), 3),
            "end": round(float(np.linalg.norm(last[a] - last[b])), 3),
        }
        for a, b in changing
    ]
    out["saddle_kcal"] = round(kcal(saddle) - lowest, 2)
    out["coordinates"] = x.round(8).tolist()
    progress.update(phase=None)
    return out


# ---------- D239: an optional DFT correction of the TS guess ----------

CORRECT_STEP = 0.1  # Å between the frames along the forming bond
CORRECT_FRAMES = 6  # the saddle's own length and five more toward the longer end
CORRECT_FMAX = 0.03  # eV/Å: each frame's minimum with the bond held
CORRECT_STEPS = 200
FLAT_KCAL = 0.5  # single points spanning less than this are too flat to pick a maximum from
CLEAR_KCAL = 0.3  # D244: the picked frame must stand this far above both neighbours
CORRECT_LEVEL = "PBE-D3M(BJ)/def2-SVP"
STUB_PEAK = 0.2  # Å past the saddle: where the tests' stand-in single points peak


def correction_single_point(job: dict, numbers, folder: Path):
    """D239: the correction's single points, Hartree from coordinates in Å: PBE with
    D3(BJ) in its modified damping, def2-SVP (def2's ECPs for the heavy atoms) and density
    fitting, through Psi4; each frame's SCF starts from the last frame's orbitals."""
    return psi4_single_point(job, numbers, folder, "pbe-d3mbj", "def2-svp")


def psi4_single_point(
    job: dict, numbers, folder: Path, name: str, basis: str | None, scf_type: str = "df"
):
    """Psi4 single points, Hartree from coordinates in Å: the method `name` (e.g. `pbe-d3mbj`,
    `r2scan-3c`) with `basis` (None for a 3c method, which brings its own), RKS or UKS by the
    multiplicity, density fitting unless `scf_type` says otherwise (then without Psi4's
    density-fitted first guess); each SCF after the first starts from the last one's orbitals
    (D239, D240)."""
    import numpy as np

    scratch = folder / "scratch"
    scratch.mkdir(exist_ok=True)
    import psi4

    charge = int(job.get("charge", 0))
    unpaired = int(job.get("multiplicity", 1)) - 1
    psi4.core.set_output_file(str(folder / "psi4.out"), False)
    psi4.set_memory(int(job.get("memory_mb", 2000)) * 1_000_000)
    psi4.set_num_threads(max(1, int(job.get("cores", 1))))
    psi4.core.IOManager.shared_object().set_default_path(str(scratch))
    options = {"reference": "uks" if unpaired else "rks", "scf_type": scf_type, "maxiter": 200}
    if scf_type != "df":
        # Psi4 otherwise converges with density fitting first (DF_SCF_GUESS), which writes the
        # range-separated exchange integrals to disk all the same
        options["df_scf_guess"] = False
    if basis:
        options["basis"] = basis
    psi4.set_options(options)
    symbols = [ELEMENTS[z - 1] for z in numbers]
    started = {"done": False}

    def energy_of(x) -> float:
        lines = [f"{charge} {unpaired + 1}"]
        lines += [
            f"{s} {a:.10f} {b:.10f} {c:.10f}"
            for s, (a, b, c) in zip(symbols, np.asarray(x, float), strict=True)
        ]
        lines += ["units angstrom", "symmetry c1", "no_reorient", "no_com"]
        psi4.geometry("\n".join(lines))
        psi4.set_options({"guess": "read" if started["done"] else "sad"})
        try:
            energy = psi4.energy(name)
        except psi4.SCFConvergenceError:
            if not started["done"]:
                raise
            psi4.set_options({"guess": "sad"})
            energy = psi4.energy(name)
        started["done"] = True
        return float(energy)

    return energy_of


def single_point_energy(job: dict, numbers, folder: Path, x) -> float:
    """D240: the energy (Hartree) of the structure `x` (Å) at the job's single point level:
    Psi4, energy only; the tests' `stub-sp` gives the stub engine's energy instead."""
    import numpy as np

    spec = job["single_point"]
    if job["method"] == "stub-sp":
        start = np.asarray(x, float) / BOHR_TO_ANGSTROM
        energy, _ = _stub(start, 0.98, 0.1, float(job.get("stub_delay_s", 0)))(start)
        return float(energy) - 1.0
    return psi4_single_point(
        job, numbers, folder, spec["psi4"], spec.get("basis"), spec.get("scf_type") or "df"
    )(x)


def forming_bond(bonds: list[dict], x, mode) -> dict:
    """D239: of the bonds that change, the one the saddle's imaginary mode stretches most (the
    one that changes most between the ends when there is no mode)."""
    import numpy as np

    x = np.asarray(x, float)

    def along(bond: dict) -> float:
        a, b = bond["atoms"]
        if mode is None:
            return abs(bond["end"] - bond["start"])
        m = np.asarray(mode, float)
        unit = (x[a] - x[b]) / np.linalg.norm(x[a] - x[b])
        return abs(float(unit @ (m[a] - m[b])))

    return max(bonds, key=along)


def ts_correction(
    job, calculate, symbols, saddle, found, folder: Path, progress, cancelled, single_point
) -> dict:
    """D239: the TS guess corrected toward the DFT barrier: from the saddle (Å), the forming
    bond is held at its length and at CORRECT_FRAMES - 1 more lengths CORRECT_STEP apart toward
    the end where it is longer, each frame minimized with the method (BFGS, the bond held,
    each from the frame before), a single point of `single_point` on each, and the highest one
    picked. Returns what result.json's `ts_guess.correction` holds, with the picked frame's
    coordinates (Å) and the method's energy there (Hartree)."""
    import numpy as np
    from ase import Atoms
    from ase.constraints import FixBondLength
    from ase.optimize import BFGS

    warnings: list[str] = []
    out: dict = {"level": CORRECT_LEVEL, "warnings": warnings}
    bonds = found.get("bonds") or []
    if not bonds:
        warnings.append(
            "No bond forms or breaks between the ends, so there is no bond to correct along."
        )
        return out
    x = np.asarray(saddle, float)
    bond = forming_bond(bonds, x, (found.get("mode_check") or {}).get("mode"))
    a, b = (int(i) for i in bond["atoms"])
    here = float(np.linalg.norm(x[a] - x[b]))
    far = max(float(bond["start"]), float(bond["end"]))
    sign = 1.0 if far >= here else -1.0
    values = [here + sign * k * CORRECT_STEP for k in range(CORRECT_FRAMES)]
    values = [v for v in values if sign * (far - v) > -1e-9] or [here]
    out["bond"] = [a, b]
    numbers = [ELEMENTS.index(s) + 1 for s in symbols]
    log = str(folder / "correction.log")
    previous = x
    frames = []
    for k, value in enumerate(values):
        atoms = Atoms(numbers=numbers, positions=previous)
        atoms.set_distance(a, b, value, fix=0.5)
        atoms.set_constraint(FixBondLength(a, b))
        atoms.calc = engine_calculator(calculate, cancelled)
        progress.update(phase=f"Correction: frame {k + 1}/{len(values)} at {value:.2f} Å")
        converged = bool(BFGS(atoms, logfile=log).run(fmax=CORRECT_FMAX, steps=CORRECT_STEPS))
        energy = float(atoms.get_potential_energy()) * EV_TO_HARTREE
        previous = np.asarray(atoms.positions)
        if cancelled():
            raise Cancelled()
        progress.update(phase=f"Correction: {CORRECT_LEVEL} single point {k + 1}/{len(values)}")
        if job["ts_guess"].get("correct") == "stub":  # tests: a maximum STUB_PEAK past the saddle
            point = -((value - (here + sign * STUB_PEAK)) ** 2)
        else:
            point = single_point(previous)
        frames.append(
            {
                "value": round(value, 3),
                "converged": converged,
                "energy": energy,
                "single_point": point,
                "coordinates": previous.round(6).tolist(),
            }
        )
    picked = pick_frame(frames, warnings)
    out["picked"] = picked
    out["coordinates"] = frames[picked]["coordinates"]
    out["energy"] = frames[picked]["energy"]
    out["frames"] = [{k: v for k, v in f.items() if k != "coordinates"} for f in frames]
    progress.update(phase=None)
    return out


def pick_frame(frames: list[dict], warnings: list[str]) -> int:
    """D239: the frame with the highest single point (each frame given its `kcal` from the
    lowest), with a warning when it is no clear maximum: the single points too flat, the highest
    at either end, or (D244) standing less than CLEAR_KCAL above a neighbour; or its minimum did
    not converge."""
    lowest = min(f["single_point"] for f in frames)
    for f in frames:
        f["kcal"] = round((f["single_point"] - lowest) * HARTREE_TO_KCAL, 2)
    picked = max(range(len(frames)), key=lambda i: frames[i]["single_point"])
    span = max(f["kcal"] for f in frames)
    if span < FLAT_KCAL:
        warnings.append(
            f"The single points span only {span:.1f} kcal/mol along the bond: too flat to place "
            "the barrier; the highest is kept anyway."
        )
    elif picked == len(frames) - 1:
        warnings.append(
            f"The highest single point is the last frame ({frames[picked]['value']:.2f} Å): the "
            "barrier may lie further along the bond."
        )
    elif picked == 0:
        warnings.append(
            "The highest single point is at the saddle's own bond length: the correction does "
            "not move it, and the barrier may lie at a shorter bond."
        )
    else:
        # D244: a plateau spans more than FLAT_KCAL yet has no maximum to pick; on five Ru TSs
        # the picked frame stood 0.10-0.22 kcal/mol above its neighbours and moved the bond
        # away from DFT in three
        rise = min(
            frames[picked]["single_point"] - frames[k]["single_point"]
            for k in (picked - 1, picked + 1)
        )
        rise *= HARTREE_TO_KCAL
        if rise < CLEAR_KCAL:
            warnings.append(
                f"The highest single point stands only {rise:.2f} kcal/mol above a neighbouring "
                "frame: the maximum is not resolved along the bond, and the raw guess is the "
                "better start."
            )
    if not frames[picked]["converged"]:
        warnings.append(f"The picked frame's minimum did not converge in {CORRECT_STEPS} steps.")
    return picked


HARTREE_KCAL = 627.509474


def fix_mode(fix: dict, start, optimize_from, frequencies_at, progress) -> dict:
    """D261: the tries of a "Fix imaginary mode" job. The structure (Å) is displaced along the
    imaginary mode so that the atom moving most moves `shifts[0]` Å, both ways; each is
    optimized (`optimize_from`, which returns the try's frames, end and state) and, once
    converged, its frequencies computed (`frequencies_at`, Bohr). When a shift leaves no try
    without an imaginary mode, the next one is tried. The lowest-energy try without an
    imaginary mode is kept, else the lowest converged one, else the last; its frames and
    frequencies go in the result (`kept_state`)."""
    import numpy as np

    mode = np.asarray(fix["mode"], float)
    largest = float(np.linalg.norm(mode, axis=1).max()) or 1.0
    tries: list[dict] = []
    states: list[dict] = []
    for shift in fix["shifts"]:
        for sign in (1, -1):
            progress.update(phase=f"Try {len(tries) + 1}: {sign * shift:+g} Å along the mode")
            x_start = np.asarray(start, float) + sign * shift * mode / largest
            state = optimize_from(x_start)
            done = state["converged"]
            found = frequencies_at(state["coords"] / BOHR_TO_ANGSTROM) if done else None
            imaginary = [w for w in found["wavenumbers"] if w < 0] if found is not None else None
            states.append({**state, "frequencies": found})
            tries.append(
                {
                    "shift": round(sign * shift, 4),
                    "converged": done,
                    "energy": state["energy"],
                    "cycles": len(state["lists"][0]),
                    "imaginary": imaginary,
                }
            )
        if any(t["converged"] and t["imaginary"] == [] for t in tries):
            break
    progress.update(phase=None)

    def pick(candidates: list[int]) -> int | None:
        known = [i for i in candidates if tries[i]["energy"] is not None]
        return min(known, key=lambda i: tries[i]["energy"]) if known else None

    clean = [i for i, t in enumerate(tries) if t["converged"] and t["imaginary"] == []]
    converged = [i for i, t in enumerate(tries) if t["converged"]]
    kept = pick(clean)
    if kept is None:
        kept = pick(converged)
    if kept is None:
        kept = len(tries) - 1
    start_energy = fix.get("start_energy")
    energy = tries[kept]["energy"]
    return {
        "wavenumber": fix.get("wavenumber"),
        "tries": tries,
        "kept": kept,
        "fixed": kept in clean,
        "start_energy": start_energy,
        "change_kcal": round((energy - start_energy) * HARTREE_KCAL, 3)
        if energy is not None and start_energy is not None
        else None,
        "kept_state": states[kept],
    }


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
    criteria: list[list] = []  # D220: per frame, the criteria geomeTRIC judged it by
    point = {"index": 1}
    t_run = None
    try:
        queue = Queue(lock)
        queue.wait(cancel_file.exists)
        progress.update(state="running", started_at=now())
        t_run = time.time()  # D240: the time computing, without the wait for other jobs
        result["versions"] = versions(method)
        if job.get("gxtb_version"):
            result["versions"]["xtb"] = job["gxtb_version"]
        if method == "mace-omol":  # D237: the version a MACE level is read with
            result["versions"]["mace"] = result["versions"].get("mace-torch")
        if job.get("kind") == "single_point":
            # D240: the energy of the structure as it is; nothing moves
            energy = single_point_energy(job, numbers, folder, start)
            energies.append(energy)
            frames.append(start.round(6).tolist())
            criteria.append([])
            last["energy"] = energy
            progress.update(cycle=1, energy=energy, energies=energies)
            result["single_point"] = job["single_point"]
            result["state"] = "converged"
            found = sys.modules.get("psi4")  # it has no package metadata for versions()
            if found is not None and not result["versions"].get("psi4"):
                result["versions"]["psi4"] = str(getattr(found, "__version__", ""))
        elif job.get("kind") == "ts_guess":
            calculate = make_calculator(job, numbers, folder)
            # D238: a TS guess between two minima instead of one optimization
            traj = folder / "trajectory.xyz"
            traj.write_text("", encoding="utf-8")

            def record(coords, energy):
                energies.append(energy)
                frames.append(np.asarray(coords).round(6).tolist())
                criteria.append([])
                with traj.open("a", encoding="utf-8") as fh:
                    fh.write(
                        xyz_frame(symbols, coords, f"cycle {len(energies)} energy {energy:.10f}")
                    )
                last["coords"], last["energy"] = np.asarray(coords), energy
                progress.update(cycle=len(energies), energy=energy, energies=energies[-200:])

            found = ts_guess(job, calculate, symbols, folder, progress, cancel_file.exists, record)
            last["coords"] = np.asarray(found.pop("coordinates"))
            result["ts_guess"] = found
            result["ts_check"] = {"end": found["mode_check"], "level": f"{label}, reacting region"}
            result["state"] = "converged" if found["converged"] else "not_converged"
            if job["ts_guess"].get("correct"):
                # D239: the optional correction, kept beside the raw guess
                single_point = None
                if job["ts_guess"]["correct"] != "stub":
                    single_point = correction_single_point(job, numbers, folder)
                found["correction"] = ts_correction(
                    job,
                    calculate,
                    symbols,
                    last["coords"],
                    found,
                    folder,
                    progress,
                    cancel_file.exists,
                    single_point,
                )
        else:
            calculate = make_calculator(job, numbers, folder)
            import geometric
            import geometric.engine
            import geometric.errors
            import geometric.molecule
            import geometric.optimize

            traj = folder / "trajectory.xyz"
            traj.write_text("", encoding="utf-8")
            judged = folder / CRITERIA
            judged.write_text("", encoding="utf-8")

            def store(found: list) -> None:
                if not frames:
                    return
                criteria[-1] = [[name, round(v, 10), t, unit] for name, v, t, unit in found]
                with judged.open("a", encoding="utf-8") as fh:
                    fh.write(
                        json.dumps({"frame": len(frames) - 1, "criteria": criteria[-1]}) + "\n"
                    )

            record_criteria(store)

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
                        criteria.append([])
                        comment = f"cycle {len(energies)} energy {energy:.10f}"
                        if job.get("scan"):  # D220: its point, for the Inspect window
                            comment += f" point {point['index']}"
                        with traj.open("a", encoding="utf-8") as fh:
                            fh.write(xyz_frame(symbols, ang, comment))
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
                cells = [cell for _, _, cell in grid_cells(scan)] if scan.get("grid") else None
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
                    finally:
                        _close_geometric_logs()
                    points.append(
                        {
                            "values": [round(v, 6) for v in value],
                            "frame": len(frames) - 1,
                            "energy": last["energy"],
                            "converged": done,
                        }
                    )
                    if cells is not None:
                        points[-1]["cell"] = cells[k]
                    with (folder / SCAN_POINTS).open("a", encoding="utf-8") as fh:
                        fh.write(json.dumps(points[-1]) + "\n")  # D220: for the Inspect window
                    converged = converged and done
                result["scan"] = {**scan, "points": points}
                progress.update(phase=None)
            elif job.get("fix_mode") and frequency_engine is not None:
                # D261: displaced both ways along the imaginary mode, each optimized and its
                # frequencies computed; the lower result without an imaginary mode is kept.
                lists = (frames, energies, criteria, frame_points)

                def optimize_from(x_start) -> dict:
                    for kept in lists:
                        kept.clear()
                    traj.write_text("", encoding="utf-8")  # the Inspect window shows this try
                    judged.write_text("", encoding="utf-8")
                    last["coords"], last["energy"] = x_start, None
                    molecule.xyzs = [x_start.copy()]
                    options["customengine"] = Engine(molecule)
                    done = True
                    try:
                        geometric.optimize.run_optimizer(**options)
                    except geometric.errors.GeomOptNotConvergedError:
                        done = False
                    finally:
                        _close_geometric_logs()
                    return {
                        "converged": done,
                        "lists": [list(values) for values in lists],
                        "coords": np.asarray(last["coords"], float).copy(),
                        "energy": last["energy"],
                        "files": [traj.read_text(encoding="utf-8"), judged.read_text("utf-8")],
                    }

                def frequencies_at(x):
                    final = numerical_hessian(frequency_engine, x, progress, "Frequencies")
                    freqs, modes = harmonic(symbols, x, final)
                    return {
                        "level": result["level"],
                        "energy": last["energy"],
                        "wavenumbers": freqs.round(2).tolist(),
                        "modes": modes.round(5).tolist(),
                    }

                found = fix_mode(job["fix_mode"], start, optimize_from, frequencies_at, progress)
                kept = found.pop("kept_state")
                for target, values in zip(lists, kept["lists"], strict=True):
                    target[:] = values
                traj.write_text(kept["files"][0], encoding="utf-8")
                judged.write_text(kept["files"][1], encoding="utf-8")
                last["coords"], last["energy"] = kept["coords"], kept["energy"]
                converged = kept["converged"]
                if kept["frequencies"] is not None:
                    result["frequencies"] = kept["frequencies"]
                result["fix_mode"] = found
                frequency_engine = None  # the kept try's frequencies are in already
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
    result["criteria"] = criteria
    if job.get("scan"):
        result["frame_points"] = frame_points
    result["energy"] = last["energy"]
    result["elements"] = symbols
    result["coordinates"] = np.asarray(last["coords"]).round(8).tolist()
    result["elapsed_s"] = round(time.time() - t0, 1)
    if t_run is not None:
        result["run_s"] = round(time.time() - t_run, 1)
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


def _close_geometric_logs() -> None:
    """D218: close the log files geomeTRIC's run opened, so the next point's run can back its
    log up (Windows cannot rename a file that is still open)."""
    import logging

    for name in ("", "geometric", "geometric.nifty"):
        lg = logging.getLogger(name)
        for handler in list(lg.handlers):
            if isinstance(handler, logging.FileHandler):
                handler.close()
                lg.removeHandler(handler)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("folder", type=Path)
    parser.add_argument("--lock", type=Path, default=None)
    args = parser.parse_args(argv)
    folder = args.folder.resolve()
    return run(folder, args.lock)


if __name__ == "__main__":
    sys.exit(main())
