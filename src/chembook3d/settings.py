"""App-wide settings and the recently opened list, kept in the user's config folder
(docs/spec/06 §3). Investigation-specific values live in the investigation's database."""

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path

from platformdirs import user_config_dir

from chembook3d.thermochem import DEFAULT_CUTOFF, DEFAULT_TEMPERATURE

ENERGY_UNITS = ("kcal/mol", "kJ/mol", "eV", "hartree")  # P14; kcal/mol default (D28)
MAX_RECENT = 10
# D62: RMSD in Å after alignment. A step's results attach to a node only
# within GEOMETRY_TOLERANCE (ID-7); another node within DUPLICATE_TOLERANCE is flagged as a
# possible duplicate (ID-8).
GEOMETRY_TOLERANCE = 0.001
DUPLICATE_TOLERANCE = 0.05
CREST_COUNT = 10  # D34: the lowest N conformers of a CREST ensemble are ticked (A16)
# Hydrogens drawn in the 3D views and on structure-mode cards: all of them, only those not
# bonded to carbon (hydrides, O–H, N–H), or none.
HYDROGEN_MODES = ("all", "polar", "none")


def config_dir() -> Path:
    override = os.environ.get("CHEMBOOK3D_CONFIG_DIR")
    return Path(override) if override else Path(user_config_dir("chembook3d", appauthor=False))


@dataclass
class Settings:
    energy_unit: str = "kcal/mol"
    recent: list[str] = field(default_factory=list)
    last_device: str = ""  # FR-FILE-02: origin device defaults to the last one used
    # D77: the import file browser starts in the folder a file was last picked from
    last_import_folder: str = ""
    geometry_tolerance: float = GEOMETRY_TOLERANCE
    duplicate_tolerance: float = DUPLICATE_TOLERANCE
    # D58: G_qh temperature (K) and cutoff (cm⁻¹); every G_qh value is shown with them.
    qh_temperature: float = DEFAULT_TEMPERATURE
    qh_cutoff: float = DEFAULT_CUTOFF
    crest_count: int = CREST_COUNT
    hydrogens: str = "all"


def _path() -> Path:
    return config_dir() / "settings.json"


def load() -> Settings:
    try:
        data = json.loads(_path().read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return Settings()
    settings = Settings()
    if data.get("energy_unit") in ENERGY_UNITS:
        settings.energy_unit = data["energy_unit"]
    if isinstance(data.get("recent"), list):
        settings.recent = [p for p in data["recent"] if isinstance(p, str)][:MAX_RECENT]
    for key in ("last_device", "last_import_folder"):
        if isinstance(data.get(key), str):
            setattr(settings, key, data[key])
    for key in ("geometry_tolerance", "duplicate_tolerance", "qh_temperature"):
        value = data.get(key)
        if isinstance(value, int | float) and not isinstance(value, bool) and value > 0:
            setattr(settings, key, float(value))
    count = data.get("crest_count")
    if isinstance(count, int) and not isinstance(count, bool) and count >= 1:
        settings.crest_count = count
    if data.get("hydrogens") in HYDROGEN_MODES:
        settings.hydrogens = data["hydrogens"]
    cutoff = data.get("qh_cutoff")
    if isinstance(cutoff, int | float) and not isinstance(cutoff, bool) and cutoff >= 0:
        settings.qh_cutoff = float(cutoff)
    return settings


def save(settings: Settings) -> None:
    _path().parent.mkdir(parents=True, exist_ok=True)
    _path().write_text(json.dumps(asdict(settings), indent=2), encoding="utf-8")


def remember_recent(folder: Path) -> None:
    settings = load()
    path = str(Path(folder).resolve())
    settings.recent = [path] + [p for p in settings.recent if p != path]
    settings.recent = settings.recent[:MAX_RECENT]
    save(settings)


def remember_import_folder(folder: Path) -> None:
    settings = load()
    settings.last_import_folder = str(folder)
    save(settings)


def import_folder(settings: Settings) -> str:
    """The folder to start the import file browser in, or "" (the home folder) when none was
    used yet or it no longer exists (D77)."""
    folder = settings.last_import_folder
    return folder if folder and Path(folder).is_dir() else ""
