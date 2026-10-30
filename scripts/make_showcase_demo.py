"""Build the showcase investigation from published Ru olefin metathesis data (D170).

    uv run python scripts/make_showcase_demo.py <new-folder> [--cache <folder>] [--copy <file>]

The calculations are those of Occhipinti, Nascimento, Foscato, Fogg and Jensen, "The Janus face
of high trans-effect carbenes in olefin metathesis: gateway to both productivity and
decomposition", Chem. Sci. 2022, 13, 5107 (doi:10.1039/D2SC00855F), deposited in ioChem-BD as
the collection beta_H_elimination_NHC_vs_CAAC (doi:10.19061/iochem-bd-6-113) under CC BY 4.0.
They are not in this repository: `showcase_manifest.json` pins each file by its ioChem-BD
handle and SHA-256, and this script downloads them into a cache (once, about 40 MB) and builds
the investigation through the app's own HTTP API, so the import is the batch import of a
folder of ioChem-BD CML files (D186) and everything after it is what a user's clicks do.

What it builds: the Hoveyda–Grubbs-type catalysts with H2IMes and with the CAAC C1Ph, as one
branch each, from the methylidene 2 through ethylene metathesis (π complex 3, [2+2] TS,
metallacyclobutane 4) and the decomposition by β-hydride elimination (TS4-5, allyl hydride 5,
TS5-6, propene complex 6). C1Ph's two alkylidene rotamers (2' and 2, 3' and 3) close the
metathesis cycle; its conformers of TS4-5, 5, TS5-6 and 6 are groups. Ethylene is a free
species. On top: the energy profiles, the energetic span of the cycle, the selectivity between
metathesis and decomposition at 4, the rotamer equilibrium of 2' and 2, steric maps of the two
carbenes, and notes. There is no IRC in the deposit and none was run (D172), nor any scan of our
own (D206). With --copy it also writes the investigation's read-only copy (D79), which needs the
interface built (scripts/build_frontend.py).
"""

import argparse
import hashlib
import json
import sys
import time
import urllib.request
from pathlib import Path

from platformdirs import user_cache_dir

from chembook3d.units import HARTREE_IN

MANIFEST = Path(__file__).with_name("showcase_manifest.json")
OPT = "_opt-freq.cml"  # each structure's optimization and frequency job; "_sp-…" its single points
USER_AGENT = "Chembook3D showcase demo (https://github.com/jonas-ekeli/Chembook3D)"
TRIES = 4

SOURCE = (
    "Occhipinti, Nascimento, Foscato, Fogg, Jensen, Chem. Sci. 2022, 13, 5107 "
    "(doi:10.1039/D2SC00855F); data: ioChem-BD, doi:10.19061/iochem-bd-6-113, CC BY 4.0"
)
DZ = "cc-pVDZ(-PP)"  # cc-pVDZ, and cc-pVDZ-PP with ECP28MDF on Ru: the optimizations
QZ = "cc-pVQZ(-PP)"  # the single points
DISPERSION = "D3M(BJ)"  # GD3BJ with the modified parameters set by IOp(3/174-178)

# The reaction steps, in order (D77: a node's stage).
STEPS = [
    "Methylidene 2",
    "Ethylene complex 3",
    "[2+2] TS",
    "Metallacyclobutane 4",
    "β-H elimination TS4-5",
    "Allyl hydride 5",
    "Propene formation TS5-6",
    "Propene complex 6",
]
BRANCHES = {"H2IMes": "#2563eb", "C1Ph": "#dc2626"}

# key in the manifest: (label, branch, step, role)
MIN, TS = "minimum", "transition_state"
NODES = {
    "H2IMes_2": ("2 H2IMes", "H2IMes", 0, MIN),
    "H2IMes_3": ("3 H2IMes", "H2IMes", 1, MIN),
    "H2IMes_TS4-3": ("TS4-3 H2IMes", "H2IMes", 2, TS),
    "H2IMes_4": ("4 H2IMes", "H2IMes", 3, MIN),
    "H2IMes_TS4-5": ("TS4-5 H2IMes", "H2IMes", 4, TS),
    "H2IMes_5": ("5 H2IMes", "H2IMes", 5, MIN),
    "H2IMes_TS5-6": ("TS5-6 H2IMes", "H2IMes", 6, TS),
    "H2IMes_6": ("6 H2IMes", "H2IMes", 7, MIN),
    "C1Ph_2p": ("2' C1Ph", "C1Ph", 0, MIN),
    "C1Ph_2": ("2 C1Ph", "C1Ph", 0, MIN),
    "C1Ph_TS2p-2": ("TS2'-2 C1Ph", "C1Ph", 0, TS),
    "C1Ph_3p": ("3' C1Ph", "C1Ph", 1, MIN),
    "C1Ph_3": ("3 C1Ph", "C1Ph", 1, MIN),
    "C1Ph_TS3p-3": ("TS3'-3 C1Ph", "C1Ph", 1, TS),
    "C1Ph_TS3p-4_1": ("TS3'-4 C1Ph 1", "C1Ph", 2, TS),
    "C1Ph_TS3p-4_2": ("TS3'-4 C1Ph 2", "C1Ph", 2, TS),
    "C1Ph_4": ("4 C1Ph", "C1Ph", 3, MIN),
    "C1Ph_TS4-3_1": ("TS4-3 C1Ph 1", "C1Ph", 2, TS),
    "C1Ph_TS4-3_2": ("TS4-3 C1Ph 2", "C1Ph", 2, TS),
    # The deposit's TS4-5 C1Ph C is left out: its single points were run on a structure
    # 0.002 Å from its optimization's, so they would go to a derived node (ID-5) and C would
    # have no energy at the paper's level (A139).
    **{f"C1Ph_TS4-5_{c}": (f"TS4-5 C1Ph {c}", "C1Ph", 4, TS) for c in "ABD"},
    **{f"C1Ph_5_{c}": (f"5 C1Ph {c}", "C1Ph", 5, MIN) for c in "ABCD"},
    **{f"C1Ph_TS5-6_{c}": (f"TS5-6 C1Ph {c}", "C1Ph", 6, TS) for c in "ABCD"},
    **{f"C1Ph_6_{c}": (f"6 C1Ph {c}", "C1Ph", 7, MIN) for c in "ABD"},
}
ETHYLENE = "ethylene"

# Conformers kept as groups: label, members.
GROUPS = {
    "C1Ph_TS3p-4": ("TS3'-4 C1Ph", ["C1Ph_TS3p-4_1", "C1Ph_TS3p-4_2"]),
    "C1Ph_TS4-3": ("TS4-3 C1Ph", ["C1Ph_TS4-3_1", "C1Ph_TS4-3_2"]),
    "C1Ph_TS4-5": ("TS4-5 C1Ph", [f"C1Ph_TS4-5_{c}" for c in "ABD"]),
    "C1Ph_5": ("5 C1Ph", [f"C1Ph_5_{c}" for c in "ABCD"]),
    "C1Ph_TS5-6": ("TS5-6 C1Ph", [f"C1Ph_TS5-6_{c}" for c in "ABCD"]),
    "C1Ph_6": ("6 C1Ph", [f"C1Ph_6_{c}" for c in "ABD"]),
}

# Edges (node or group keys), with ethylene joining (+) or leaving (-).
EDGES = [
    ("H2IMes_2", "H2IMes_3", "+"),
    ("H2IMes_3", "H2IMes_TS4-3", None),
    ("H2IMes_TS4-3", "H2IMes_4", None),
    ("H2IMes_4", "H2IMes_TS4-5", None),
    ("H2IMes_TS4-5", "H2IMes_5", None),
    ("H2IMes_5", "H2IMes_TS5-6", None),
    ("H2IMes_TS5-6", "H2IMes_6", None),
    # C1Ph: ethylene binds to 2', metathesis through 4 gives 3, which loses ethylene; the
    # alkylidene turns back from 2 to 2', which closes the cycle.
    ("C1Ph_2p", "C1Ph_3p", "+"),
    ("C1Ph_3p", "C1Ph_TS3p-4", None),
    ("C1Ph_TS3p-4", "C1Ph_4", None),
    ("C1Ph_4", "C1Ph_TS4-3", None),
    ("C1Ph_TS4-3", "C1Ph_3", None),
    ("C1Ph_3", "C1Ph_2", "-"),
    ("C1Ph_2", "C1Ph_TS2p-2", None),
    ("C1Ph_TS2p-2", "C1Ph_2p", None),
    ("C1Ph_3p", "C1Ph_TS3p-3", None),
    ("C1Ph_TS3p-3", "C1Ph_3", None),
    ("C1Ph_4", "C1Ph_TS4-5", None),
    ("C1Ph_TS4-5", "C1Ph_5", None),
    ("C1Ph_5", "C1Ph_TS5-6", None),
    ("C1Ph_TS5-6", "C1Ph_6", None),
]
CYCLE = [
    "C1Ph_2p",
    "C1Ph_3p",
    "C1Ph_TS3p-4",
    "C1Ph_4",
    "C1Ph_TS4-3",
    "C1Ph_3",
    "C1Ph_2",
    "C1Ph_TS2p-2",
    "C1Ph_2p",
]
# The energy profile's pathways to the propene complex 6: H2IMes' from its methylidene 2, C1Ph's
# from 2', where its cycle starts, so that it follows the cycle as far as 4.
PATHWAYS = {
    "H2IMes": [
        "H2IMes_2",
        "H2IMes_3",
        "H2IMes_TS4-3",
        "H2IMes_4",
        "H2IMes_TS4-5",
        "H2IMes_5",
        "H2IMes_TS5-6",
        "H2IMes_6",
    ],
    "C1Ph": [
        "C1Ph_2p",
        "C1Ph_3p",
        "C1Ph_TS3p-4",
        "C1Ph_4",
        "C1Ph_TS4-5",
        "C1Ph_5",
        "C1Ph_TS5-6",
        "C1Ph_6",
    ],
}
# Where each card goes on the canvas (node or group keys), as a user would drag them: C1Ph's
# cycle as a loop, 2' → 3' → 4 along the top and 4 → 3 → 2 back along the bottom, the rotamer
# TSs between, its decomposition from 4 to the right; H2IMes below, in a row.
COLUMN, ROW = 240, 170
C1PH_PLACES = {
    "C1Ph_2p": (0, 0),
    "C1Ph_3p": (1, 0),
    "C1Ph_TS3p-4": (2, 0),
    "C1Ph_TS2p-2": (0, 1),
    "C1Ph_TS3p-3": (1, 1),
    "C1Ph_4": (3, 1),
    "C1Ph_2": (0, 2),
    "C1Ph_3": (1, 2),
    "C1Ph_TS4-3": (2, 2),
    "C1Ph_TS4-5": (4, 1),
    "C1Ph_5": (5, 1),
    "C1Ph_TS5-6": (6, 1),
    "C1Ph_6": (7, 1),
}
PLACES = {**C1PH_PLACES, **{key: (i, 4) for i, key in enumerate(PATHWAYS["H2IMes"])}}
DECOMPOSITION_COLOUR = "#d97706"  # C1Ph's decomposition pathway, apart from its cycle

# At 4, productive metathesis (back to 3 and on) against decomposition by β-H elimination.
SELECTIVITIES = {
    "H2IMes": ("H2IMes_TS4-3", "H2IMes_TS4-5"),
    "C1Ph": ("C1Ph_TS4-3", "C1Ph_TS4-5"),
}
# The paper's: PBE-D3M(BJ)/QZ single points at the PBE/DZ geometries.
LEVEL = (
    f"Gaussian PBEPBE-{DISPERSION}/{QZ} IEFPCM(chloroform) // "
    f"Gaussian PBEPBE/{DZ} IEFPCM(chloroform)"
)

NOTE = (
    "<p><b>Published data.</b> The structures and energies are from {source}. The optimizations "
    "and frequencies are PBE/{dz} in PCM chloroform; the single points PBE-{disp}/{qz} and "
    "M06-L-D3/{qz} at those geometries. Free energies are quasi-harmonic (100 cm⁻¹) at 1 M.</p>"
    "<p>The deposit has no IRC, and none was run for this demo: the transition states are joined "
    "to their minima as the paper joins them.</p>"
)
FREQUENCY_ONLY = (
    "<p>The deposit holds only this structure's frequency job, run from its optimization's "
    "checkpoint, so the frequency job gives the geometry level of its single points (D204). "
    "Gaussian's stationary-point check failed on displacement although the forces converged, "
    "which is what W-FREQ-NONSTAT says.</p>"
)


# ---------- downloads ----------


def load_manifest(path: Path = MANIFEST) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def default_cache() -> Path:
    return Path(user_cache_dir("chembook3d", appauthor=False)) / "showcase"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _download(url: str, target: Path) -> None:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    part = target.with_name(target.name + ".part")
    for attempt in range(TRIES):
        try:
            with urllib.request.urlopen(request, timeout=180) as response, part.open("wb") as out:
                while block := response.read(1 << 20):
                    out.write(block)
            part.replace(target)
            return
        except OSError:
            part.unlink(missing_ok=True)
            if attempt == TRIES - 1:
                raise
            time.sleep(2 ** (attempt + 1))


def fetch(entry: dict, folder: Path) -> Path:
    """The cached copy of one manifest file, downloaded and checked against its SHA-256."""
    target = folder / entry["file"]
    if target.exists() and _sha256(target) == entry["sha256"]:
        return target
    folder.mkdir(parents=True, exist_ok=True)
    _download(entry["url"], target)
    found = _sha256(target)
    if found != entry["sha256"]:
        target.unlink()
        raise RuntimeError(
            f"{entry['url']} has changed since the manifest was written "
            f"(SHA-256 {found}, expected {entry['sha256']})"
        )
    return target


def fetch_all(manifest: dict, folder: Path) -> None:
    files = manifest["files"]
    for number, entry in enumerate(files, start=1):
        print(f"[{number}/{len(files)}] {entry['file']}", flush=True)
        fetch(entry, folder)
    # Only the manifest's files, so the folder's batch import sees nothing else.
    for path in folder.iterdir():
        if path.name not in {entry["file"] for entry in files}:
            raise RuntimeError(f"{folder} holds {path.name}, which is not in the manifest")


# ---------- the investigation ----------


class Api:
    """The app's HTTP API in this process; every call must succeed."""

    def __init__(self, client):
        self.client = client

    def __call__(self, method: str, path: str, body=None, **params):
        response = self.client.request(method, f"/api{path}", json=body, params=params or None)
        if response.status_code >= 400:
            raise RuntimeError(f"{method} {path}: {response.status_code} {response.text}")
        return response.json() if response.content else None


def _names(plan: dict) -> dict:
    """Names for the custom basis sets and dispersions the files define (D59). The
    optimizations' jobs are titled after the structure, the single points' end in "_SP"."""
    options: dict = {"basis_names": {}, "dispersion_names": {}}
    for request in plan["names"]:
        if request["kind"] == "basis":
            single_point = "_SP" in request["title"].upper()
            options["basis_names"][request["key"]] = QZ if single_point else DZ
        else:
            options["dispersion_names"][request["key"]] = DISPERSION
    return options


def _import(api: Api, folder: Path) -> dict[str, str]:
    """D97, D186: the folder as one batch, each optimization a new node named by its key and
    its single points onto it. Returns each key's node id."""
    plan = api("POST", "/batch-imports", {"folder": str(folder), "recursive": False})
    optimizations = {
        Path(row["path"]).name.removesuffix(OPT): row["id"]
        for row in plan["rows"]
        if row["path"].endswith(OPT)
    }
    rows = {}
    for row in plan["rows"]:
        name = Path(row["path"]).name
        if name.endswith(OPT):
            choice: dict = {"label": name.removesuffix(OPT)}
            # A frequency job run from the optimization's checkpoint names no basis set (D104).
            if row.get("basis_missing"):
                choice["basis"] = DZ
        else:
            # Each single point onto the node of the structure it was run on.
            choice = {"target": f"file:{optimizations[name.rsplit('_sp-', 1)[0]]}"}
        rows[row["id"]] = choice
    options = {**_names(plan), "rows": rows, "missing_basis": QZ}
    preview = api("POST", f"/batch-imports/{plan['token']}/preview", options)
    if preview.get("blockers"):
        raise RuntimeError(f"The batch import is blocked: {preview['blockers']}")
    api("POST", f"/batch-imports/{plan['token']}/commit", options)
    return {node["label"]: node["id"] for node in api("GET", "/nodes")}


def build(api: Api, folder: Path) -> dict:
    """The investigation, from the downloaded files in `folder`; returns the drawer's pathways
    and reference, for the read-only copy."""
    api("PUT", "/settings", {"qh_temperature": 298.15, "qh_cutoff": 100.0, "standard_state": "1 M"})
    ids = _import(api, folder)
    missing = [key for key in [*NODES, ETHYLENE] if key not in ids]
    if missing:
        raise RuntimeError(f"Not imported as planned: {missing or sorted(ids)}")

    step_ids = [api("POST", "/steps", {"name": name})["id"] for name in STEPS]
    branch_ids = {
        name: api("POST", "/branches", {"name": name, "colour": colour})["id"]
        for name, colour in BRANCHES.items()
    }
    for key, (label, branch, step, role) in NODES.items():
        api(
            "PATCH",
            f"/nodes/{ids[key]}",
            {
                "label": label,
                "role": role,
                "status": "done",
                "step_id": step_ids[step],
                "branch_id": branch_ids[branch],
            },
        )
    api("PATCH", f"/nodes/{ids[ETHYLENE]}", {"label": "ethylene", "role": MIN, "status": "done"})
    api("PUT", f"/nodes/{ids[ETHYLENE]}/kind", {"kind": "species"})

    for key, (label, members) in GROUPS.items():
        body = {"member_ids": [ids[m] for m in members], "label": label}
        group = api("POST", "/groups/reconnect", body)
        ids[key] = group["id"]

    for source, target, ethylene in EDGES:
        edge = api("POST", "/transitions", {"source_id": ids[source], "target_id": ids[target]})
        api("PATCH", f"/transitions/{edge['id']}", {"status": "done"})
        if ethylene:
            api(
                "PUT",
                f"/transitions/{edge['id']}/species",
                {
                    "species_id": ids[ETHYLENE],
                    "direction": "joins" if ethylene == "+" else "leaves",
                },
            )
    positions = {ids[k]: {"x": c * COLUMN, "y": r * ROW} for k, (c, r) in PLACES.items()}
    api("PUT", "/positions", {"positions": positions})
    return _analyses(api, ids, branch_ids)


def _analyses(api: Api, ids: dict[str, str], branch_ids: dict[str, str]) -> dict:
    """The paper's level and free energy, a representative for each group, the energy
    profile, the turnover of C1Ph's cycle, the selectivities at 4, the rotamer equilibrium,
    the steric maps, the notes and the view the investigation opens on."""
    options = api("GET", "/energies/options")["levels"]
    level = next(o["key"] for o in options if o["label"] == LEVEL)
    values = api("GET", "/energies/view", level=level, type="G_qh")["values"]

    def g_qh(key: str) -> float:
        value = values[ids[key]]["value"]
        return value if value is not None else float("inf")

    # EN-7: a group's energy is its representative's; the lowest conformer at the level.
    for key, (_, members) in GROUPS.items():
        api("PATCH", f"/groups/{ids[key]}", {"representative_id": ids[min(members, key=g_qh)]})

    # One profile per catalyst: the two have different atoms, so each is read against its own
    # methylidene 2 (the view opens on C1Ph's; for H2IMes', take "2 H2IMes" as the reference).
    # C1Ph's two pathways leave out the branch, so the drawer names them by their ends, and
    # the decomposition gets a colour of its own (D195).
    c1ph_paths = [
        {"ids": [ids[k] for k in CYCLE], "branch_id": None, "shown": True},
        {
            "ids": [ids[k] for k in PATHWAYS["C1Ph"]],
            "branch_id": None,
            "shown": True,
            "colour": DECOMPOSITION_COLOUR,
        },
    ]
    profile = api(
        "POST",
        "/energy-profiles",
        {"name": "C1Ph: cycle and decomposition", "paths": c1ph_paths},
    )
    h2imes = [ids[k] for k in PATHWAYS["H2IMes"]]
    api(
        "POST",
        "/energy-profiles",
        {
            "name": "H2IMes: metathesis and decomposition",
            "paths": [{"ids": h2imes, "branch_id": branch_ids["H2IMes"], "shown": True}],
        },
    )
    turnover = api(
        "POST",
        "/turnovers",
        {
            "name": "Ethylene metathesis, C1Ph",
            "path": [ids[k] for k in CYCLE],
            "level": level,
            "energy_type": "G_qh",
            "notes": (
                "Degenerate metathesis (ethylene in, ethylene out), so ΔG_r = 0 and the TOF is "
                "zero; the energetic span and the TDTS and TDI still say what limits turnover."
            ),
        },
    )
    for branch, (productive, decomposition) in SELECTIVITIES.items():
        api(
            "POST",
            "/selectivities",
            {
                "name": f"Metathesis or decomposition at 4, {branch}",
                "level": level,
                "energy_type": "G_qh",
                "outcomes": [
                    {"name": "Metathesis (TS4-3)", "members": [ids[productive]]},
                    {"name": "β-H elimination (TS4-5)", "members": [ids[decomposition]]},
                ],
            },
        )
    api(
        "POST",
        "/equilibria",
        {
            "name": "Alkylidene rotamers 2' and 2, C1Ph",
            "members": [ids["C1Ph_2p"], ids["C1Ph_2"], ids["C1Ph_TS2p-2"]],  # D171a: the TS
            "level": level,
            "energy_type": "G_qh",
        },
    )
    _sterics(api, [ids["H2IMes_2"], ids["C1Ph_2"]])
    _notes(api, ids, turnover["id"])
    api(
        "PUT",
        "/view-state",
        {
            "level": level,
            "energy_type": "G_qh",
            "reference_id": ids["C1Ph_2"],
            "drawer": {"open": True, "tab": "profile", "profile_id": profile["id"]},
        },
    )
    return {"paths": c1ph_paths, "reference_id": ids["C1Ph_2"]}


def _notes(api: Api, ids: dict[str, str], turnover_id: str) -> None:
    """The source and the absent IRC, floating in the space below C1Ph's 4; the turnover's
    result below 2; and why the TS4-5 structures carry W-FREQ-NONSTAT (D85, D87)."""
    body = NOTE.format(source=SOURCE, dz=DZ, qz=QZ, disp=DISPERSION)
    api(
        "POST",
        f"/nodes/{ids['C1Ph_4']}/notes",
        {
            "title": "Source",
            "body": body,
            "width": 320,
            "corner": "bottom-right",
            "placement": "line",
            "offset_x": 40,
            "offset_y": 60,
        },
    )

    result = api("GET", f"/turnovers/{turnover_id}/result")
    determining = {row[-1]: row[0] for row in result["table"]["rows"] if row[-1]}
    span = result["span"] * HARTREE_IN["kcal/mol"]
    api(
        "POST",
        f"/nodes/{ids['C1Ph_2']}/notes",
        {
            "title": "Turnover",
            "body": (
                f"<p>In the energetic span of the cycle (Analyses › Turnover), "
                f"{determining['TDTS']} is the TDTS and {determining['TDI']} the TDI: "
                f"δE = {span:.1f} kcal/mol. The alkylidene has to turn back from 2 to 2′ "
                "before the next ethylene binds.</p>"
            ),
            "width": 300,
            "corner": "bottom-right",
            "placement": "line",
            "offset_x": -150,  # under the card rather than off the canvas's left edge
            "offset_y": 40,
        },
    )
    for key in ("H2IMes_TS4-5", "C1Ph_TS4-5_A", "C1Ph_TS4-5_B"):
        api(
            "POST",
            f"/nodes/{ids[key]}/notes",
            {"title": "Frequency job only", "body": FREQUENCY_ONLY, "collapsed": True},
        )


def ligand_atoms(xyz: str) -> dict[str, list[int]]:
    """SambVca's setting for the carbene ligand of a methylidene RuCl2(L)(=CH2): the sphere on
    Ru, the z-axis to the carbene carbon (the Ru-bound C with a nitrogen), the xz-plane through
    the methylidene carbon (the Ru-bound C with two hydrogens), and every atom outside the
    ligand left out (atoms from 1, D75)."""
    import numpy as np

    from chembook3d.services.atom_matching import bonds

    rows = [line.split() for line in xyz.strip().splitlines()[2:]]
    elements = [row[0] for row in rows]
    adjacency = bonds(np.array([row[1:4] for row in rows], dtype=float), elements)
    ru = elements.index("Ru")

    def neighbours(i: int) -> list[int]:
        return [int(j) for j in np.nonzero(adjacency[i])[0]]

    bound = [j for j in neighbours(ru) if elements[j] == "C"]
    carbene = next(j for j in bound if any(elements[k] == "N" for k in neighbours(j)))
    methylidene = next(j for j in bound if [elements[k] for k in neighbours(j)].count("H") == 2)
    ligand, stack = {carbene}, [carbene]
    while stack:
        for j in neighbours(stack.pop()):
            if j != ru and j not in ligand:
                ligand.add(j)
                stack.append(j)
    return {
        "centre": [ru + 1],
        "z_axis": [carbene + 1],
        "xz_plane": [methylidene + 1],
        "excluded": [i + 1 for i in range(len(elements)) if i not in ligand and i != ru],
    }


def _sterics(api: Api, node_ids: list[str]) -> None:
    """%V_bur and steric maps of the two carbenes at the methylidene 2 (D81), SambVca's
    defaults: a 3.5 Å sphere, Bondi radii × 1.17, no hydrogens."""
    atoms = {i: ligand_atoms(api("GET", f"/nodes/{i}")["xyz"]) for i in node_ids}
    profile = api("POST", "/steric-profiles", {"name": "H2IMes and C1Ph at the methylidene 2"})
    api("PATCH", f"/steric-profiles/{profile['id']}", {"atoms": atoms})
    api("POST", f"/steric-profiles/{profile['id']}/compute", {"node_ids": node_ids, "maps": True})


def main(argv: list[str]) -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("folder", type=Path, help="a new folder for the investigation")
    parser.add_argument("--cache", type=Path, default=None, help="where downloads are kept")
    parser.add_argument(
        "--copy", type=Path, default=None, help="also write its read-only copy (D79) to this file"
    )
    args = parser.parse_args(argv)
    if args.folder.exists() and any(args.folder.iterdir()):
        sys.exit(f"{args.folder} is not empty")
    files = (args.cache or default_cache()) / "6-113"
    fetch_all(load_manifest(), files)

    from fastapi.testclient import TestClient

    from chembook3d.app import create_app

    with TestClient(create_app()) as client:
        api = Api(client)
        api(
            "POST",
            "/investigations",
            {"folder": str(args.folder), "name": "Ru metathesis (showcase)"},
        )
        try:
            shown = build(api, files)
            if args.copy:
                # The drawer's pathways and reference, as the app's Share button sends them.
                response = client.post("/api/snapshot", json=shown)
                if response.status_code >= 400:
                    raise RuntimeError(f"The read-only copy: {response.text}")
                args.copy.write_bytes(response.content)
        finally:
            api("POST", "/investigations/close")
    print(f"Showcase investigation created in {args.folder}")


if __name__ == "__main__":
    main(sys.argv[1:])
