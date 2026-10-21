"""Spin contamination (D162): the spin expectation value ⟨S²⟩ a calculation printed against
S(S+1) of its multiplicity. Gaussian's value before annihilation is the one judged (the one
the energy belongs to); the value after annihilation is only shown. Nothing is stored here: the
values are the printed ones kept in the result's `printed` (`parsers.common.S2`…)."""

from dataclasses import dataclass
from typing import Any

from chembook3d.models import Calculation
from chembook3d.parsers.common import S2, S2_AFTER, S2_IDEAL

# D162, A106: ⟨S²⟩ further from S(S+1) than this fraction of it is contaminated; for a singlet,
# whose S(S+1) is 0, a ⟨S²⟩ above SINGLET_LIMIT is (a broken-symmetry singlet among them).
RELATIVE = 0.10
SINGLET_LIMIT = 0.1


def ideal(multiplicity: int) -> float:
    """S(S+1) for a multiplicity 2S + 1."""
    s = (multiplicity - 1) / 2
    return s * (s + 1)


def _number(text: Any) -> float | None:
    try:
        return float(text)
    except (TypeError, ValueError):
        return None


@dataclass
class Spin:
    s2: float  # Gaussian's before annihilation, ORCA's expectation value
    after: float | None  # Gaussian's after annihilation
    ideal: float | None  # S(S+1); None when the multiplicity is not known
    multiplicity: int | None

    @property
    def contaminated(self) -> bool:
        if self.ideal is None:
            return False
        if self.ideal == 0:
            return self.s2 > SINGLET_LIMIT
        return abs(self.s2 - self.ideal) > RELATIVE * self.ideal

    def value_text(self) -> str:
        """The value as shown: 1.0123 before annihilation (0.7612 after), or 0.7782."""
        if self.after is None:
            return f"{self.s2:.4f}"
        return f"{self.s2:.4f} before annihilation ({self.after:.4f} after)"

    def expected_text(self) -> str:
        if self.multiplicity is not None:
            expected = f"multiplicity {self.multiplicity}"
        else:
            expected = "the program's S(S+1)"
        if self.ideal == 0:
            return f"expected 0 for a singlet (more than {SINGLET_LIMIT} is contaminated)"
        return f"expected {self.ideal:.4f} for {expected}"

    def as_dict(self) -> dict[str, Any]:
        return {
            "s2": self.s2,
            "after": self.after,
            "ideal": self.ideal,
            "multiplicity": self.multiplicity,
            "contaminated": self.contaminated,
        }


def read(printed: dict[str, Any] | None, multiplicity: int | None) -> Spin | None:
    """The spin a step or result printed, judged against `multiplicity` (else the S(S+1) the
    program printed beside it, as ORCA does); None when it printed no ⟨S²⟩."""
    printed = printed or {}
    s2 = _number(printed.get(S2))
    if s2 is None:
        return None
    expected = ideal(multiplicity) if multiplicity else _number(printed.get(S2_IDEAL))
    return Spin(s2, _number(printed.get(S2_AFTER)), expected, multiplicity or None)


def of(calculation: Calculation, multiplicity: int | None = None) -> Spin | None:
    """A calculation's spin, judged against its own multiplicity (the file's), else the one
    given (the node's)."""
    if calculation.result is None:
        return None
    return read(calculation.result.printed, calculation.multiplicity or multiplicity)


def contamination(calculation: Calculation, multiplicity: int | None = None) -> Spin | None:
    """The calculation's spin when it is contaminated (W-SPIN), else None."""
    found = of(calculation, multiplicity)
    return found if found is not None and found.contaminated else None


def message(found: Spin, what: str = "") -> str:
    """W-SPIN's text: "spin contamination: ⟨S²⟩ of the energy calculation is 1.0123 before
    annihilation (0.7612 after), expected 0.7500 for multiplicity 2"."""
    subject = f"⟨S²⟩ {what} is" if what else "⟨S²⟩ ="
    return f"spin contamination: {subject} {found.value_text()}, {found.expected_text()}"
