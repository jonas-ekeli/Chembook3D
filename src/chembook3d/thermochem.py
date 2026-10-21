"""Quasi-harmonic free energy (G_qh) after Truhlar, reproducing Jonas's reference script
`thermochem_corr_G16` (docs/reference, D41, D49, D56–D58; docs/spec/05 §4).

- Only real frequencies are used; imaginary ones are left out and counted.
- ZPE and the vibrational thermal energy use the unmodified frequencies.
- Vibrational entropy raises every frequency below the cutoff to the cutoff (Truhlar).
- Translational entropy is for an ideal gas at 1 atm: no concentration correction (D57).
  The 1 M standard state (D95) is an option added on top by `services/energies`, with
  `standard_state_correction`; it is not part of the script.
- Linear molecules, which the script does not support (D58), get the linear rigid rotor
  (D164): one rotational temperature, U_rot = RT and S_rot = R(ln(T/(σΘ_rot)) + 1), with
  the 3N − 5 vibrations the file lists. Everything else is as for a non-linear molecule.

Each formula is written in the same form and order as the script, so the results agree with
it to the last digits rather than only within the test tolerance. The linear rotor is ours
(D164) and leaves the non-linear path untouched.
"""

import math
import sys
from dataclasses import dataclass

from chembook3d.units import (
    AMU_KG,
    ATM_PA,
    AVOGADRO,
    BOLTZMANN,
    GAS_CONSTANT,
    J_PER_MOL_HARTREE,
    PLANCK,
    SPEED_OF_LIGHT_CM,
)

DEFAULT_TEMPERATURE = 298.15  # K
DEFAULT_CUTOFF = 100.0  # cm⁻¹
LINEAR_POINT_GROUPS = {"D*H", "C*V"}


class NotComputable(ValueError):
    """G_qh cannot be computed from this frequency calculation. `code` is the warning."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class QuasiHarmonic:
    """All energies in hartree, entropies in hartree/K."""

    temperature: float
    cutoff: float
    g_corr: float  # G_qh − E(SCF), the script's "Thermic correction"
    h_corr: float
    zpe: float
    entropy: float
    s_vib: float
    s_rot: float
    s_trans: float
    s_elec: float
    real_modes: int
    raised_modes: int  # real modes below the cutoff, raised for the entropy
    imaginary_excluded: int
    linear: bool = False  # D164: the linear rigid rotor was used


def _zpe(frequencies: list[float]) -> float:
    energy = [
        0.5 * GAS_CONSTANT * PLANCK * SPEED_OF_LIGHT_CM * freq / (BOLTZMANN * J_PER_MOL_HARTREE)
        for freq in frequencies
    ]
    return sum(energy)


def _vibrational_energy(frequencies: list[float], temperature: float) -> float:
    factor = [
        (PLANCK * freq * SPEED_OF_LIGHT_CM) / (BOLTZMANN * temperature) for freq in frequencies
    ]
    if any(entry > math.log(sys.float_info.max) for entry in factor):
        raise NotComputable("W-QH", "the temperature is too low to compute G_qh")
    energy = [
        fac * GAS_CONSTANT * temperature / J_PER_MOL_HARTREE * (0.5 + (1.0 / (math.exp(fac) - 1.0)))
        for fac in factor
    ]
    return sum(energy)


def _translational_entropy(molecular_mass: float, temperature: float) -> float:
    lmda = ((2.0 * math.pi * molecular_mass * AMU_KG * BOLTZMANN * temperature) ** 0.5) / PLANCK
    conc = ATM_PA / (temperature * GAS_CONSTANT)
    ndens = conc * AVOGADRO
    return GAS_CONSTANT * (2.5 + math.log(lmda**3 / ndens)) / J_PER_MOL_HARTREE


def _rotational_entropy(temperature: float, rotemp: list[float], symmetry_number: int) -> float:
    qrot = math.pi * temperature**3 / (rotemp[0] * rotemp[1] * rotemp[2])
    qrot = qrot**0.5
    return GAS_CONSTANT * (math.log(qrot / symmetry_number) + 1.5) / J_PER_MOL_HARTREE


def _linear_rotational_entropy(temperature: float, rotemp: float, symmetry_number: int) -> float:
    """D164: the linear rigid rotor, q_rot = T / (σΘ_rot), S_rot = R(ln q_rot + 1)."""
    qrot = temperature / rotemp
    return GAS_CONSTANT * (math.log(qrot / symmetry_number) + 1.0) / J_PER_MOL_HARTREE


def _linear_temperature(rotemp: list[float]) -> float | None:
    """D164: a linear molecule's one rotational temperature: the single value Gaussian and
    ORCA print, or the equal non-zero ones of a list that also has the zero about the axis.
    None when the values do not describe a linear rotor."""
    nonzero = [value for value in rotemp if value != 0.0]
    if not nonzero or any(not math.isclose(v, nonzero[0], rel_tol=1e-3) for v in nonzero):
        return None
    return nonzero[0]


def _rrho_entropy(frequency: float, temperature: float) -> float:
    factor = PLANCK * frequency * SPEED_OF_LIGHT_CM / (BOLTZMANN * temperature)
    return (
        factor * GAS_CONSTANT / (math.exp(factor) - 1.0)
        - GAS_CONSTANT * math.log(1.0 - math.exp(-factor))
    ) / J_PER_MOL_HARTREE


def quasi_harmonic(
    *,
    frequencies: list[float],
    scf_energy: float | None,
    molecular_mass: float | None,
    multiplicity: int | None,
    rotational_temperatures: list[float] | None,
    symmetry_number: int | None,
    point_group: str | None = None,
    temperature: float = DEFAULT_TEMPERATURE,
    cutoff: float = DEFAULT_CUTOFF,
) -> QuasiHarmonic:
    """G_qh correction of one frequency job step. Raises NotComputable with the reason."""
    if temperature <= 0 or cutoff < 0:
        raise NotComputable(
            "W-QH", "the temperature must be above zero and the cutoff not negative"
        )
    # D164: a linear point group, or the one rotational temperature printed for one.
    linear = (point_group or "").upper() in LINEAR_POINT_GROUPS or (
        rotational_temperatures is not None and len(rotational_temperatures) == 1
    )
    linear_temperature = None
    if rotational_temperatures is not None:
        if linear:
            linear_temperature = _linear_temperature(rotational_temperatures)
            if linear_temperature is None:
                raise NotComputable(
                    "W-PARSE", "linear molecule: no single rotational temperature was read"
                )
        elif len(rotational_temperatures) != 3:
            raise NotComputable(
                "W-PARSE", "G_qh needs three rotational temperatures for a non-linear molecule"
            )
    real = [f for f in frequencies if f >= 0]
    imaginary = len(frequencies) - len(real)
    if not real:
        raise NotComputable("W-NOFREQ", "the frequency calculation has no real frequencies")
    missing = [
        name
        for name, value in (
            ("SCF energy", scf_energy),
            ("molecular mass", molecular_mass),
            ("multiplicity", multiplicity),
            ("rotational temperatures", rotational_temperatures),
            ("rotational symmetry number", symmetry_number),
        )
        if value is None
    ]
    if missing:
        raise NotComputable(
            "W-PARSE", f"G_qh needs values not read from the file: {', '.join(missing)}"
        )
    assert scf_energy is not None and molecular_mass is not None
    assert multiplicity is not None and rotational_temperatures is not None
    assert symmetry_number is not None

    t = temperature
    zpe = _zpe(real)
    if linear_temperature is not None:
        return _linear(
            real=real,
            imaginary=imaginary,
            zpe=zpe,
            scf_energy=scf_energy,
            molecular_mass=molecular_mass,
            multiplicity=multiplicity,
            rotemp=linear_temperature,
            symmetry_number=symmetry_number,
            temperature=t,
            cutoff=cutoff,
        )
    u_rot = 1.5 * GAS_CONSTANT * t / J_PER_MOL_HARTREE if zpe != 0.0 else 0.0
    u_vib = _vibrational_energy(real, t)
    if rotational_temperatures == [0.0, 0.0, 0.0] or zpe == 0.0:
        s_rot = 0.0
    else:
        s_rot = _rotational_entropy(t, rotational_temperatures, symmetry_number)
    u_trans = 1.5 * GAS_CONSTANT * t / J_PER_MOL_HARTREE
    s_trans = _translational_entropy(molecular_mass, t)
    s_elec = GAS_CONSTANT * math.log(multiplicity) / J_PER_MOL_HARTREE
    # Truhlar: modes below the cutoff count as the cutoff, for the entropy only (D56).
    s_vib = sum(_rrho_entropy(cutoff if f < cutoff else f, t) for f in real)

    pv = GAS_CONSTANT * t / J_PER_MOL_HARTREE
    h = scf_energy + u_rot + u_vib + u_trans + pv
    s = s_rot + s_vib + s_trans + s_elec
    g = h - t * s
    return QuasiHarmonic(
        temperature=t,
        cutoff=cutoff,
        g_corr=g - scf_energy,
        h_corr=h - scf_energy,
        zpe=zpe,
        entropy=s,
        s_vib=s_vib,
        s_rot=s_rot,
        s_trans=s_trans,
        s_elec=s_elec,
        real_modes=len(real),
        raised_modes=sum(1 for f in real if f < cutoff),
        imaginary_excluded=imaginary,
    )


def _linear(
    *,
    real: list[float],
    imaginary: int,
    zpe: float,
    scf_energy: float,
    molecular_mass: float,
    multiplicity: int,
    rotemp: float,
    symmetry_number: int,
    temperature: float,
    cutoff: float,
) -> QuasiHarmonic:
    """D164: as `quasi_harmonic` for a linear molecule; only the rotor differs, U_rot = RT
    (two rotations) and the linear S_rot. The vibrations are the 3N − 5 the file lists."""
    t = temperature
    u_rot = GAS_CONSTANT * t / J_PER_MOL_HARTREE
    u_vib = _vibrational_energy(real, t)
    s_rot = _linear_rotational_entropy(t, rotemp, symmetry_number)
    u_trans = 1.5 * GAS_CONSTANT * t / J_PER_MOL_HARTREE
    s_trans = _translational_entropy(molecular_mass, t)
    s_elec = GAS_CONSTANT * math.log(multiplicity) / J_PER_MOL_HARTREE
    s_vib = sum(_rrho_entropy(cutoff if f < cutoff else f, t) for f in real)  # D56

    pv = GAS_CONSTANT * t / J_PER_MOL_HARTREE
    h = scf_energy + u_rot + u_vib + u_trans + pv
    s = s_rot + s_vib + s_trans + s_elec
    g = h - t * s
    return QuasiHarmonic(
        temperature=t,
        cutoff=cutoff,
        g_corr=g - scf_energy,
        h_corr=h - scf_energy,
        zpe=zpe,
        entropy=s,
        s_vib=s_vib,
        s_rot=s_rot,
        s_trans=s_trans,
        s_elec=s_elec,
        real_modes=len(real),
        raised_modes=sum(1 for f in real if f < cutoff),
        imaginary_excluded=imaginary,
        linear=True,
    )


def standard_state_correction(temperature: float) -> float:
    """D95: G(1 M) − G(1 atm) of one molecule in hartree, RT ln(V_m / 1 L mol⁻¹) with the
    ideal-gas molar volume V_m = RT / (1 atm) at the same temperature (1.894 kcal/mol at
    298.15 K)."""
    if temperature <= 0:
        raise ValueError("the temperature must be above zero")
    molar_volume_litres = GAS_CONSTANT * temperature / ATM_PA * 1000.0
    return GAS_CONSTANT * temperature * math.log(molar_volume_litres) / J_PER_MOL_HARTREE
