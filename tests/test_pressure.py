"""S01: guard printed G outside D57/D95's 1 atm input convention without normalizing it.

These use the real parser, stored results and energy/profile services directly, without an
HTTP client. G_qh remains the established independent 1 atm recomputation (D56–D58).
"""

import math
from copy import deepcopy

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from chembook3d import settings
from chembook3d.models import (
    Base,
    Calculation,
    CalculationResult,
    CalculationType,
    LevelOfTheory,
    Node,
    NodeKind,
    Role,
    Transition,
    TransitionSpecies,
)
from chembook3d.parsers import gaussian
from chembook3d.services import pathways, species, warnings
from chembook3d.services.energies import Energies, LevelKey
from chembook3d.units import GAS_CONSTANT, J_PER_MOL_HARTREE
from tests import gaussian_text as g


@pytest.fixture
def stored():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        level = LevelOfTheory(program="Gaussian", method="B3LYP", basis="def2SVP")
        session.add(level)
        session.flush()
        yield session, level, LevelKey(level.id, level.id)
    engine.dispose()


def frequency_node(stored, pressure=1.0, *, label="A", energy=-76.0, concentration=None):
    session, level, _ = stored
    thermo = g.THERMO.replace("1.00000 Atm.", f"{pressure:.5f} Atm.")
    step = gaussian.parse(
        g.output(
            g.step(
                "# B3LYP/def2SVP freq",
                [g.WATER],
                energy=energy,
                frequencies=[50.0, 100.0, 150.0],
                thermo=thermo,
            )
        )
    ).steps[0]
    assert step.thermo["pressure"] == pressure
    node = Node(
        label=label,
        role=Role.MINIMUM,
        kind=NodeKind.SPECIES if concentration is not None else NodeKind.NODE,
        concentration=concentration,
        charge=0,
        multiplicity=1,
    )
    node.calculations.append(
        Calculation(
            type=CalculationType.FREQUENCY,
            program="Gaussian",
            level=level,
            charge=0,
            multiplicity=1,
            termination="normal",
            result=CalculationResult(
                energy=step.scf_energy,
                frequencies=step.frequencies,
                imaginary_count=0,
                printed=step.printed,
                **step.thermo,
            ),
        )
    )
    session.add(node)
    session.flush()
    return node


def selected(stored, standard_state="1 atm", temperature=298.15):
    return Energies(
        stored[0], settings.Settings(standard_state=standard_state, qh_temperature=temperature)
    )


def expected_shift(temperature, standard_state, concentration):
    # Independent ideal-gas expression, in hartree; G follows its job T, G_qh the selection T.
    if concentration is None and standard_state == "1 atm":
        return 0.0
    c = concentration["molarity"] if concentration is not None else 1.0
    return (
        GAS_CONSTANT
        * temperature
        / J_PER_MOL_HARTREE
        * math.log(GAS_CONSTANT * temperature / 101325.0 * 1000.0 * c)
    )


@pytest.mark.parametrize("pressure", [0.1, 1.0, 10.0])
@pytest.mark.parametrize("standard_state", ["1 atm", "1 M"])
@pytest.mark.parametrize("concentration", [None, {"kind": "molar", "molarity": 0.25}])
def test_printed_g_source_pressure_is_guarded_before_any_conversion(
    stored, pressure, standard_state, concentration
):
    node = frequency_node(stored, pressure, concentration=concentration)
    calculation = node.calculations[0]
    result = calculation.result
    original = deepcopy({a: getattr(result, a) for a in result.__mapper__.columns.keys()})
    energies = selected(stored, standard_state, temperature=350.0)
    found = energies.value(node.id, stored[2], "G")
    assert found.energy_calculation_id == calculation.id
    assert found.thermo_calculation_id == calculation.id
    assert found.details["job_pressure"] == pressure
    assert found.details["job_temperature"] == 298.15
    assert any(i["code"] == "W-TEMP" for i in found.check["issues"])
    if pressure == 1.0:
        assert found.value == pytest.approx(
            result.energy + result.g_corr + expected_shift(298.15, standard_state, concentration),
            abs=1e-12,
        )
        assert found.code is None and found.message is None
        assert not any(i["code"] == "W-PRESSURE" for i in found.check["issues"])
        assert "G" in energies.options()[0]["types"]
    else:
        assert found.value is None
        assert found.code == "W-PRESSURE"
        assert f"{pressure:g} atm" in found.message
        assert "1 atm" in found.message and "conversion is not supported" in found.message
        assert {"code": found.code, "message": found.message} in found.check["issues"]
        assert found.as_dict()["message"] == found.message
        assert found.details["printed_correction"] == result.g_corr
        assert "standard_state_correction" not in found.details
        assert "standard_state" not in found.details
        assert energies.options()[0]["types"] == ["E", "H", "G_qh"]
    # Reading/guarding must never alter the imported numerical or verbatim source data.
    stored[0].flush()
    stored[0].expire(result)
    assert {a: getattr(result, a) for a in original} == original


@pytest.mark.parametrize("pressure", [0.1, 1.0, 10.0])
@pytest.mark.parametrize("standard_state", ["1 atm", "1 M"])
@pytest.mark.parametrize("concentration", [None, {"kind": "molar", "molarity": 0.25}])
def test_qh_is_independent_of_source_pressure_and_e_h_are_unchanged(
    stored, pressure, standard_state, concentration
):
    baseline = frequency_node(stored, 1.0, label="1 atm", concentration=concentration)
    node = frequency_node(stored, pressure, label="source", concentration=concentration)
    energies = selected(stored, standard_state, temperature=350.0)
    for energy_type in ("E", "H", "G_qh"):
        found = energies.value(node.id, stored[2], energy_type)
        before = energies.value(baseline.id, stored[2], energy_type)
        assert found.value == before.value
        assert found.code is None and found.message is None
        assert not any(i["code"] == "W-PRESSURE" for i in (found.check or {}).get("issues", []))
    qh = energies.value(node.id, stored[2], "G_qh")
    assert qh.value == pytest.approx(
        node.calculations[0].result.energy
        + qh.details["correction"]
        + expected_shift(350.0, standard_state, concentration),
        abs=1e-12,
    )
    assert qh.check is None


def test_legacy_missing_pressure_keeps_the_existing_input_convention(stored):
    node = frequency_node(stored)
    node.calculations[0].result.pressure = None
    energies = selected(stored, "1 M")
    result = node.calculations[0].result
    found = energies.value(node.id, stored[2], "G")
    assert found.value == pytest.approx(
        result.energy + result.g_corr + expected_shift(298.15, "1 M", None), abs=1e-12
    )
    assert found.check is None


@pytest.mark.parametrize("pressure", [None, 0.1, 1.0, 10.0])
def test_calculation_and_node_warnings_explain_why_printed_g_is_unavailable(stored, pressure):
    node = frequency_node(stored)
    calculation = node.calculations[0]
    calculation.result.pressure = pressure
    app_settings = settings.Settings()
    for found in (
        warnings.calculation_warnings(calculation, node, tolerance=0.001),
        warnings.node_warnings(node, app_settings),
    ):
        pressure_warnings = [w for w in found if w.code == "W-PRESSURE"]
        if pressure is None or pressure == 1.0:
            assert pressure_warnings == []
        else:
            (warning,) = pressure_warnings
            value = selected(stored).value(node.id, stored[2], "G")
            assert warning.message == value.message
            assert warning.calculation_id == calculation.id
            assert "printed G is unavailable" in warning.message
            assert "G_qh is unaffected" in warning.message
    # The pressure warning belongs to a frequency source, never a single point's energy.
    calculation.type = CalculationType.SINGLE_POINT
    assert not any(
        w.code == "W-PRESSURE"
        for w in warnings.calculation_warnings(calculation, node, tolerance=0.001)
    )


def test_pressure_of_the_frequency_source_controls_composite_printed_g(stored):
    node = frequency_node(stored, 10.0)
    session, _, key = stored
    other = LevelOfTheory(program="Gaussian", method="B3LYP", basis="def2TZVP")
    session.add(other)
    session.flush()
    sp = Calculation(
        node=node,
        type=CalculationType.SINGLE_POINT,
        program="Gaussian",
        level=other,
        geometry_level_id=key.level_id,
        result=CalculationResult(energy=-76.1, pressure=1.0),
    )
    session.add(sp)
    session.flush()
    composite = LevelKey(other.id, key.level_id)
    found = selected(stored, "1 M").value(node.id, composite, "G")
    assert found.value is None and found.code == "W-PRESSURE"
    assert found.energy_calculation_id == sp.id
    assert found.thermo_calculation_id == node.calculations[0].id
    assert found.details["job_pressure"] == 10.0


@pytest.mark.parametrize("pressure", [0.1, 1.0, 10.0])
def test_pressure_reason_reaches_profiles_and_table_and_species_balance(stored, pressure):
    session, _, key = stored
    a = frequency_node(stored, label="A")
    b = frequency_node(stored, pressure, label="B", energy=-76.1)
    reagent = frequency_node(stored, pressure, label="reagent", energy=-0.2)
    reagent.kind = NodeKind.SPECIES
    transition = Transition(source_node_id=a.id, target_node_id=b.id)
    transition.species.append(TransitionSpecies(species_id=reagent.id, direction="joins", count=1))
    session.add(transition)
    session.flush()
    energies = selected(stored)
    # A valid 1 atm node keeps G selectable at a mixed-pressure level; only the unsafe
    # node/species is unavailable, without falling back to another energy type.
    assert "G" in energies.options()[0]["types"]
    data = pathways.profiles(session, [[a.id, b.id]], a.id, key, "G", energies=energies)
    point = data["profiles"][0]["points"][1]
    table = pathways.table(
        session, [[a.id, b.id]], a.id, key, "G", "hartree", data=data, energies=energies
    )
    balance, reason = species.energy(session, energies, {reagent.id: 1}, key, "G")
    if pressure != 1.0:
        assert point["value"] is None and point["relative"] is None
        assert point["code"] == "W-PRESSURE"
        assert point["message"] in point["check"]["message"]
        assert point["message"] in table["rows"][1][-1]
        assert table["rows"][1][6] == "n/a"
        assert balance is None
        assert "reagent" in reason and f"{pressure:g} atm" in reason
        assert reason == point["species_message"]
    else:
        # D69/D95 unchanged: A + reagent -> B, so the reaction contains one fewer molecule.
        assert point["relative"] == pytest.approx(
            (-76.1 + 0.0034) - (-76.0 + 0.0034) - (-0.2 + 0.0034), abs=1e-12
        )
        assert point["check"] is None and reason is None
        assert table["rows"][1][6] == "-76.09660000"
        shifted = pathways.profiles(
            session, [[a.id, b.id]], a.id, key, "G", energies=selected(stored, "1 M")
        )["profiles"][0]["points"][1]
        assert shifted["relative"] == pytest.approx(
            point["relative"] - expected_shift(298.15, "1 M", None), abs=1e-12
        )
