"""The concentration of a free species (D160): what its G and G_qh are taken at wherever it is
added to a balance (D69, D72), on top of the standard state.

- Unset (None), a species follows the investigation's standard state (D95, D159): 1 atm by
  default (D57), or 1 M.
- Set, its free energy is at that concentration in solution whatever the investigation's
  standard state: G(c) = G(1 atm) + RT ln(V_m / 1 L mol⁻¹) + RT ln(c / 1 M), at the
  temperature of the value (G at its frequency job's, G_qh at the G_qh temperature).
  "1 M" is c = 1; "neat" is the solvent's own molarity (a common solvent from `SOLVENTS`, or
  one given); "molar" is any concentration in mol/L (a substrate at 0.1 M).
- E and H never change.

Stored on the node as {"kind": "1 M"}, {"kind": "neat", "solvent": "THF", "molarity": 12.3}
or {"kind": "molar", "molarity": 0.5}; every change is in the history (field
"concentration").
"""

import math
from typing import Any

from chembook3d import thermochem
from chembook3d.units import GAS_CONSTANT, J_PER_MOL_HARTREE

KINDS = ("1 M", "neat", "molar")


class Invalid(ValueError):
    """A concentration the species cannot take; the message says why."""


# A104: neat molarities at 20 °C (density / molar mass), with the names the solvation models
# of Gaussian and ORCA give them, so a species can start from the level's solvent.
SOLVENTS: tuple[dict[str, Any], ...] = (
    {"name": "water", "molarity": 55.4, "names": ("water", "h2o")},
    {"name": "THF", "molarity": 12.3, "names": ("tetrahydrofuran", "thf")},
    {"name": "DCM", "molarity": 15.6, "names": ("dichloromethane", "ch2cl2", "dcm")},
    {"name": "chloroform", "molarity": 12.4, "names": ("chloroform", "chcl3", "trichloromethane")},
    {"name": "toluene", "molarity": 9.4, "names": ("toluene",)},
    {"name": "benzene", "molarity": 11.2, "names": ("benzene",)},
    {"name": "acetonitrile", "molarity": 19.1, "names": ("acetonitrile", "mecn", "ch3cn")},
    {"name": "methanol", "molarity": 24.7, "names": ("methanol", "meoh", "ch3oh")},
    {"name": "ethanol", "molarity": 17.1, "names": ("ethanol", "etoh")},
    {"name": "acetone", "molarity": 13.5, "names": ("acetone",)},
    {"name": "DMSO", "molarity": 14.1, "names": ("dimethylsulfoxide", "dmso")},
    {"name": "DMF", "molarity": 12.9, "names": ("n,n-dimethylformamide", "dmf")},
    {"name": "diethyl ether", "molarity": 9.6, "names": ("diethylether", "ether", "et2o")},
    {"name": "1,4-dioxane", "molarity": 11.7, "names": ("1,4-dioxane", "dioxane")},
    {"name": "n-hexane", "molarity": 7.6, "names": ("n-hexane", "hexane")},
)


def solvent(name: str | None) -> dict[str, Any] | None:
    """The table's entry for a solvent named as the table or a solvation model names it."""
    if not name:
        return None
    wanted = name.strip().lower()
    return next((s for s in SOLVENTS if wanted == s["name"].lower() or wanted in s["names"]), None)


def _number(value: Any, what: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value):
        raise Invalid(f"{what} must be a number")
    if value <= 0:
        raise Invalid(f"{what} must be above zero")
    return float(value)


def clean(value: Any) -> dict[str, Any] | None:
    """`value` as stored, or Invalid. None (follow the investigation) stays None."""
    if value is None:
        return None
    if not isinstance(value, dict) or value.get("kind") not in KINDS:
        raise Invalid("concentration must be null or have kind '1 M', 'neat' or 'molar'")
    kind = value["kind"]
    if kind == "1 M":
        return {"kind": "1 M"}
    if kind == "molar":
        return {"kind": "molar", "molarity": _number(value.get("molarity"), "The concentration")}
    name = value.get("solvent")
    if name is not None and not isinstance(name, str):
        raise Invalid("The solvent must be a name")
    if value.get("molarity") is not None:
        molarity = _number(value["molarity"], "The solvent's molarity")
        found = solvent(name)
        named = (found["name"] if found else (name or "").strip()) or None
        return {"kind": "neat", "solvent": named, "molarity": molarity}
    found = solvent(name)
    if found is None:
        known = ", ".join(s["name"] for s in SOLVENTS)
        raise Invalid(
            f"Unknown solvent '{name or ''}': give its molarity, or choose one of {known}"
        )
    return {"kind": "neat", "solvent": found["name"], "molarity": found["molarity"]}


def molarity(value: dict[str, Any]) -> float:
    return 1.0 if value["kind"] == "1 M" else float(value["molarity"])


def describe(value: dict[str, Any] | None, standard_state: str = "1 atm") -> str:
    """ "1 M", "neat THF, 12.3 M", "0.5 M"; unset, the investigation's standard state."""
    if value is None:
        return standard_state
    if value["kind"] == "1 M":
        return "1 M"
    if value["kind"] == "molar":
        return f"{value['molarity']:g} M"
    name = value.get("solvent") or "solvent"
    return f"neat {name}, {value['molarity']:g} M"


def correction(temperature: float, value: dict[str, Any]) -> float:
    """D160: G(c) − G(1 atm) of one molecule in hartree: the 1 M standard state (D95) plus
    RT ln(c / 1 M)."""
    shift = thermochem.standard_state_correction(temperature)
    return shift + GAS_CONSTANT * temperature * math.log(molarity(value)) / J_PER_MOL_HARTREE
