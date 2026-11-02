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
    "b97-3c": ("Psi4", "B97-3c"),
    "r2scan-3c": ("Psi4", "r2SCAN-3c"),
    "stub": ("stub", "stub"),
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


def read_constraints(job: dict) -> str | None:
    """geomeTRIC's constraint text from job.json's constraints (atoms counted from 0)."""
    c = job.get("constraints") or {}
    freeze, held = [], []
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


def make_calculator(job: dict, numbers, folder: Path):
    """A function from coordinates (Bohr, n x 3) to (energy in Hartree, gradient Hartree/Bohr)."""
    import numpy as np

    method = job["method"]
    charge = int(job.get("charge", 0))
    unpaired = int(job.get("multiplicity", 1)) - 1
    cores = max(1, int(job.get("cores", 1)))

    if method == "stub":
        # Test engine: harmonic bonds towards the starting distances shortened by 2 %, so the
        # optimizer has a few steps to take; it needs no tools environment.
        start = np.array(job["coordinates"], float) / BOHR_TO_ANGSTROM
        n = len(start)
        pairs = [(i, j) for i in range(n) for j in range(i + 1, n)]
        target = {p: 0.98 * np.linalg.norm(start[p[0]] - start[p[1]]) for p in pairs}
        delay = float(job.get("stub_delay_s", 0))

        def stub(x):
            if delay:
                time.sleep(delay)
            e, g = 0.0, np.zeros_like(x)
            for i, j in pairs:
                d = x[i] - x[j]
                r = np.linalg.norm(d)
                w = 0.1 / target[(i, j)] ** 2
                e += w * (r - target[(i, j)]) ** 2
                f = 2 * w * (r - target[(i, j)]) * d / r
                g[i] += f
                g[j] -= f
            return e, g

        return stub

    if method == "gfn2":
        from tblite.interface import Calculator

        def gfn2(x):
            calc = Calculator("GFN2-xTB", numbers, x, charge=charge, uhf=unpaired)
            calc.set("verbosity", 0)
            res = calc.singlepoint()
            return float(res.get("energy")), np.array(res.get("gradient"))

        return gfn2

    if method in ("b97-3c", "r2scan-3c"):
        import psi4

        scratch = folder / "scratch"
        scratch.mkdir(exist_ok=True)
        psi4.core.set_output_file(str(folder / "psi4.out"), False)
        psi4.set_memory(int(job.get("memory_mb", 2000)) * 1_000_000)
        psi4.set_num_threads(cores)
        psi4.core.IOManager.shared_object().set_default_path(str(scratch))
        psi4.set_options(
            {
                "reference": "uks" if unpaired else "rks",
                "scf_type": "df",
                "maxiter": 200,
            }
        )
        symbols = [ELEMENTS[z - 1] for z in numbers]

        def dft(x):
            lines = [f"{charge} {unpaired + 1}"]
            lines += [
                f"{s} {a:.10f} {b:.10f} {c:.10f}" for s, (a, b, c) in zip(symbols, x, strict=True)
            ]
            lines += ["units bohr", "symmetry c1", "no_reorient", "no_com"]
            psi4.geometry("\n".join(lines))
            grad, wfn = psi4.gradient(method, return_wfn=True)
            energy = float(wfn.energy())
            psi4.core.clean()
            return energy, np.array(grad)

        return dft

    raise ValueError(f"unknown method {method!r}")


def versions(method: str) -> dict:
    from importlib import metadata

    found = {"python": sys.version.split()[0]}
    names = ["numpy", "geometric"]
    if method == "gfn2":
        names.append("tblite")
    if method in ("b97-3c", "r2scan-3c"):
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
        "level": label,
        "charge": int(job.get("charge", 0)),
        "multiplicity": int(job.get("multiplicity", 1)),
        "constraints": job.get("constraints") or None,
        "transition": bool(job.get("transition", False)),
    }
    t0 = time.time()
    energies: list[float] = []
    frames: list[list[list[float]]] = []
    last: dict = {"coords": start, "energy": None}
    try:
        queue = Queue(lock)
        queue.wait(cancel_file.exists)
        progress.update(state="running", started_at=now())
        result["versions"] = versions(method)
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
                energies.append(energy)
                ang = x * BOHR_TO_ANGSTROM
                frames.append(ang.round(6).tolist())
                last["coords"], last["energy"] = ang, energy
                with traj.open("a", encoding="utf-8") as fh:
                    fh.write(xyz_frame(symbols, ang, f"cycle {len(energies)} energy {energy:.10f}"))
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
        converged = True
        try:
            _redirect_geometric_log(log)
            geometric.optimize.run_optimizer(**options)
        except geometric.errors.GeomOptNotConvergedError:
            converged = False
        result["state"] = "converged" if converged else "not_converged"
    except Cancelled:
        result["state"] = "cancelled"
    except Exception as exc:  # noqa: BLE001 - every failure goes into result.json
        result["state"] = "failed"
        result["error"] = f"{type(exc).__name__}: {exc}"
        log.write(traceback.format_exc())
    result["cycles"] = len(energies)
    result["energies"] = energies
    result["frames"] = frames
    result["energy"] = last["energy"]
    result["elements"] = symbols
    result["coordinates"] = np.asarray(last["coords"]).round(8).tolist()
    result["elapsed_s"] = round(time.time() - t0, 1)
    result["finished_at"] = now()
    progress.stopped.set()
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
