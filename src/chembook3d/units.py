"""Unit conversions and physical constants, defined once (P17, docs/spec/05 §5). Energies are
stored in hartree.

Values are CODATA 2018 SI, exactly as in Jonas's reference script
(docs/reference/thermochem_corr_G16.py), so the quasi-harmonic free energy reproduces it.
"""

HARTREE_IN = {
    "hartree": 1.0,
    "kcal/mol": 627.5094740631,
    "kJ/mol": 2625.4996394799,
    "eV": 27.211386245988,
}

# Decimals for relative energies in each display unit (edge labels, profiles, the table).
DECIMALS = {"kcal/mol": 2, "kJ/mol": 2, "eV": 3, "hartree": 6}

GAS_CONSTANT = 8.31446261815323  # J/(K mol)
BOLTZMANN = 1.380649e-23  # J/K
AVOGADRO = 6.02214076e23  # 1/mol
PLANCK = 6.62607015e-34  # J s
SPEED_OF_LIGHT_CM = 2.99792458e10  # cm/s
AMU_KG = 1.6605390666050e-27
ATM_PA = 101325.0
# J/mol per hartree, as the script defines it (4.184 J/cal × kcal/mol per hartree × 1000).
J_PER_MOL_HARTREE = 4.184 * HARTREE_IN["kcal/mol"] * 1000.0
