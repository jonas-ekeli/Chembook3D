"""What a frequency job's thermochemistry is made of (D163): the model behind the G its
program printed and the frequency scale factor. "G" is not the same quantity in every program:
Gaussian prints pure RRHO, ORCA Grimme's quasi-RRHO entropy (unless its output says Quasi RRHO
False), xTB its own modified RRHO. G_qh is always the app's own (Jonas's script, D56–D58) from
the frequencies as stored; ORCA prints its frequencies already scaled by its ScalFreq, Gaussian
and xTB print them unscaled. Nothing is stored here: the values are the parsers' (the result's
`printed`, `parsers.common.G_MODEL` and `FREQUENCY_SCALE`), and a frequency job imported before
D163 falls back to its program's model and a scale of 1."""

from dataclasses import dataclass
from typing import Any

from chembook3d.models import Calculation
from chembook3d.parsers.common import FREQUENCY_SCALE, G_MODEL

# D163: what each model's printed G is, as the panels and tooltips say it.
MODELS = {
    "RRHO": "rigid rotor, harmonic oscillator",
    "qRRHO": "Grimme's quasi-RRHO entropy (low modes treated as free rotors)",
    "mRRHO": "xTB's modified RRHO (low modes mixed with free rotors)",
}
PROGRAM_MODELS = {"Gaussian": "RRHO", "ORCA": "qRRHO", "xTB": "mRRHO"}
# D163, A107: programs whose printed frequencies already carry the scale factor.
SCALED_FREQUENCIES = {"ORCA"}
# D163: scale factors closer than this are the same.
SCALE_TOLERANCE = 5e-5


def _number(text: Any) -> float | None:
    try:
        return float(text)
    except (TypeError, ValueError):
        return None


@dataclass(frozen=True)
class Thermo:
    program: str
    model: str | None  # the printed G's model: RRHO, qRRHO, mRRHO; None for another program
    scale: float  # the scale factor the program's own thermochemistry used
    frequencies_scaled: bool  # the stored frequencies carry it (ORCA)
    read: bool  # the factor was read from the file (False: imported before D163, taken as 1)

    @property
    def qh_scale(self) -> float:
        """The scale factor of the frequencies G_qh is computed from (as stored)."""
        return self.scale if self.frequencies_scaled else 1.0

    def scale_for(self, energy_type: str) -> float:
        """The scale factor behind a value of this type: the program's for its printed H and
        G, the stored frequencies' for G_qh."""
        return self.qh_scale if energy_type == "G_qh" else self.scale

    @property
    def model_label(self) -> str:
        """ "Gaussian's RRHO", "ORCA's qRRHO"; the program alone for one with no model known."""
        return label_of(self.as_dict())

    def as_dict(self) -> dict[str, Any]:
        return {
            "program": self.program,
            "model": self.model,
            "model_text": MODELS.get(self.model or ""),
            "scale": self.scale,
            "frequencies_scaled": self.frequencies_scaled,
            "qh_scale": self.qh_scale,
        }


def of(calculation: Calculation | None) -> Thermo | None:
    """A frequency job's thermochemistry model and scale; None for a calculation with neither
    frequencies nor a printed free energy."""
    result = calculation.result if calculation is not None else None
    if result is None or (not result.frequencies and result.g_corr is None):
        return None
    printed = result.printed or {}
    scale = _number(printed.get(FREQUENCY_SCALE))
    return Thermo(
        program=calculation.program,
        model=printed.get(G_MODEL) or PROGRAM_MODELS.get(calculation.program),
        scale=scale if scale is not None and scale > 0 else 1.0,
        frequencies_scaled=calculation.program in SCALED_FREQUENCIES,
        read=scale is not None,
    )


def label_of(found: dict[str, Any]) -> str:
    """A model as `Thermo.as_dict` gives it, named: "Gaussian's RRHO"."""
    return f"{found['program']}'s {found['model']}" if found.get("model") else found["program"]


def scale_text(scale: float) -> str:
    """0.97 → "0.97", 1 → "1": as few decimals as the factor needs, at most four."""
    return f"{scale:.4f}".rstrip("0").rstrip(".")


def same_scale(a: float, b: float) -> bool:
    return abs(a - b) < SCALE_TOLERANCE


def _scaled(scale: float) -> str:
    return "unscaled" if same_scale(scale, 1.0) else f"scaled by {scale_text(scale)}"


def scale_issue(mine: float, others: list[float], energy_type: str) -> dict[str, str]:
    """D163: W-SCALE, the frequencies behind this value scaled otherwise than other nodes' at
    the same composite level."""
    listed = " or ".join(_scaled(s) for s in sorted({round(s, 4) for s in others}))
    what = "the frequencies G_qh is computed from" if energy_type == "G_qh" else "the frequencies"
    return {
        "code": "W-SCALE",
        "message": f"{what} are {_scaled(mine)} here, but {listed} on other nodes at this "
        "level (mixed frequency scale factors)",
    }


def model_issue(mine: Thermo, others: list[Thermo]) -> dict[str, str]:
    """D163: W-G-MODEL, this printed G from another model than other nodes' at the level."""
    listed = " and ".join(sorted({t.model_label for t in others}))
    return {
        "code": "W-G-MODEL",
        "message": f"this G is {mine.model_label} as printed, but other nodes at this level have "
        f"{listed} (G from different thermochemistry models; G_qh is computed one way for all)",
    }
