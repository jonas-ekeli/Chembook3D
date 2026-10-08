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
# D84: colours of the steric maps, low to high (the difference map keeps blue–grey–red).
STERIC_COLOURS = ("blue", "green-yellow-red", "rainbow", "viridis", "grey")
# D95: the standard state of G and G_qh, the 1 atm ideal gas of the files and the reference
# script (D57) or 1 mol/L, which adds RT ln(V_m / 1 L mol⁻¹) to each molecule.
STANDARD_STATES = ("1 atm", "1 M")
# D97, A44: suffixes taken off an output's file name before it is matched to a node label in a
# batch import (* and ? as in the file filter, D78).
BATCH_SUFFIXES = ("_SP*", "_freq", "_opt", "_irc")

# D106: how energy profiles are drawn, in the app and in the PNG and SVG saved from them. Each
# key takes one of the listed choices or a number within its bounds; "Screen" is the look
# before D106 and the default. `edge_tags` shows the "no TS" tag on a direct connection (D108).
PROFILE_CHOICES: dict[str, tuple[str, ...]] = {
    "font": ("system", "arial", "helvetica", "times"),
    "colours": ("branch", "colour-blind", "grey", "black"),
    "text": ("soft", "black"),
    "connector": ("straight", "curved"),
    "dash": ("solid", "dashed", "dotted", "by-pathway"),
    "value_position": ("above", "below", "hidden"),
    "name_position": ("below", "above", "bottom", "hidden"),  # bottom: along the x axis, D109
    "brackets": ("none", "round", "square"),
    "decimals": ("unit", "0", "1", "2", "3"),
    "legend": ("top-right", "top-left", "hidden"),
    "background": ("white", "none"),
}
PROFILE_NUMBERS: dict[str, tuple[float, float]] = {
    "width": (300, 4000),
    "height": (150, 3000),
    "font_size": (6, 32),
    "level_width": (6, 200),
    "level_thickness": (0.5, 12),
    "connector_width": (0.25, 8),
    "png_scale": (1, 8),
}
PROFILE_FLAGS = ("title", "grid", "y_axis", "step_names", "edge_tags")
PROFILE_PRESETS: dict[str, dict] = {
    "screen": {
        "width": 960,
        "height": 360,
        "font": "system",
        "font_size": 11,
        "colours": "branch",
        "text": "soft",
        "level_width": 60,
        "level_thickness": 3.5,
        "connector": "straight",
        "dash": "solid",
        "connector_width": 1.4,
        "value_position": "above",
        "name_position": "below",
        "brackets": "none",
        "decimals": "unit",
        "title": True,
        "legend": "top-right",
        "grid": True,
        "y_axis": True,
        "step_names": True,
        "edge_tags": True,
        "background": "white",
        "png_scale": 2,
    },
    "publication": {
        "width": 960,
        "height": 480,
        "font": "arial",
        "font_size": 12,
        "colours": "colour-blind",
        "text": "black",
        "level_width": 50,
        "level_thickness": 3,
        "connector": "curved",
        "dash": "solid",
        "connector_width": 1.5,
        "value_position": "above",
        "name_position": "below",
        "brackets": "none",
        "decimals": "1",
        "title": False,
        "legend": "top-left",
        "grid": False,
        "y_axis": True,
        "step_names": True,
        "edge_tags": True,
        "background": "none",
        "png_scale": 4,
    },
}


def config_dir() -> Path:
    override = os.environ.get("CHEMBOOK3D_CONFIG_DIR")
    return Path(override) if override else Path(user_config_dir("chembook3d", appauthor=False))


@dataclass
class Settings:
    energy_unit: str = "kcal/mol"
    recent: list[str] = field(default_factory=list)
    last_device: str = ""  # FR-FILE-02: origin device defaults to the last one used
    # D78: the import file browser starts in the folder a file was last picked from
    last_import_folder: str = ""
    geometry_tolerance: float = GEOMETRY_TOLERANCE
    duplicate_tolerance: float = DUPLICATE_TOLERANCE
    # D58: G_qh temperature (K) and cutoff (cm⁻¹); every G_qh value is shown with them.
    qh_temperature: float = DEFAULT_TEMPERATURE
    qh_cutoff: float = DEFAULT_CUTOFF
    standard_state: str = "1 atm"
    crest_count: int = CREST_COUNT
    hydrogens: str = "all"
    steric_colours: str = "blue"
    batch_suffixes: list[str] = field(default_factory=lambda: list(BATCH_SUFFIXES))
    profile_style: dict = field(default_factory=lambda: dict(PROFILE_PRESETS["screen"]))


def clean_profile_style(data: object) -> dict:
    """A full profile style (D106) from `data`: each known key with a valid value is kept (a
    number is clamped to its bounds), every other key takes the "Screen" default."""
    data = data if isinstance(data, dict) else {}
    style = dict(PROFILE_PRESETS["screen"])
    for key, choices in PROFILE_CHOICES.items():
        if data.get(key) in choices:
            style[key] = data[key]
    for key, (low, high) in PROFILE_NUMBERS.items():
        value = data.get(key)
        if isinstance(value, int | float) and not isinstance(value, bool) and value == value:
            value = min(high, max(low, value))
            style[key] = round(value) if key in ("width", "height", "png_scale") else value
    for key in PROFILE_FLAGS:
        if isinstance(data.get(key), bool):
            style[key] = data[key]
    return style


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
    if data.get("standard_state") in STANDARD_STATES:
        settings.standard_state = data["standard_state"]
    if data.get("steric_colours") in STERIC_COLOURS:
        settings.steric_colours = data["steric_colours"]
    suffixes = data.get("batch_suffixes")
    if isinstance(suffixes, list) and all(isinstance(s, str) for s in suffixes):
        settings.batch_suffixes = [s.strip() for s in suffixes if s.strip()]
    if "profile_style" in data:
        settings.profile_style = clean_profile_style(data["profile_style"])
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
    used yet or it no longer exists (D78)."""
    folder = settings.last_import_folder
    return folder if folder and Path(folder).is_dir() else ""
