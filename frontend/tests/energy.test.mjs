import assert from 'node:assert/strict'
import { test } from 'node:test'
import {
  concentrationCorrection,
  convertEnergy,
  displayUnit,
  energyFactor,
  energyTypeName,
  formatDelta,
  HARTREE_IN_KCAL_MOL,
  pathChip,
  standardStateCorrection,
} from '../src/api.ts'

// The backend's `units.HARTREE_IN` and `units.DECIMALS`, as GET /settings sends them (P17).
const FACTORS = { hartree: 1, 'kcal/mol': 627.5094740631, 'kJ/mol': 2625.4996394799, eV: 27.211386245988 }
const DECIMALS = { 'kcal/mol': 2, 'kJ/mol': 2, eV: 3, hartree: 6 }
const settings = (unit) => ({ energy_unit: unit, energy_factors: FACTORS, energy_decimals: DECIMALS })
const close = (actual, expected, tolerance = 1e-12) =>
  assert.ok(Math.abs(actual - expected) <= tolerance, `${actual} is not ${expected}`)

test('convertEnergy uses the backend’s factor for each unit', () => {
  for (const [unit, factor] of Object.entries(FACTORS)) assert.equal(convertEnergy(0.01, unit, FACTORS), 0.01 * factor)
  // Round trip through any unit and back.
  for (const unit of Object.keys(FACTORS)) close(convertEnergy(0.0123, unit, FACTORS) / FACTORS[unit], 0.0123)
})

test('an unknown unit is an error, never hartree shown under another unit', () => {
  assert.throws(() => convertEnergy(0.01, 'kcal', FACTORS), /Unknown energy unit 'kcal'/)
  assert.throws(() => energyFactor('eV', {}), /Unknown energy unit/)
  assert.throws(() => energyFactor('kJ/mol', { 'kJ/mol': Number.NaN }), /Unknown energy unit/)
  // Not a property every object has.
  assert.throws(() => energyFactor('toString', FACTORS), /Unknown energy unit/)
})

test('before the settings load, energies are in kcal/mol with the backend’s factor', () => {
  assert.equal(energyFactor('kcal/mol', {}), HARTREE_IN_KCAL_MOL)
  assert.equal(HARTREE_IN_KCAL_MOL, FACTORS['kcal/mol'])
  assert.deepEqual(displayUnit(null), { unit: 'kcal/mol', factor: HARTREE_IN_KCAL_MOL })
  assert.deepEqual(displayUnit(undefined), { unit: 'kcal/mol', factor: HARTREE_IN_KCAL_MOL })
  assert.deepEqual(displayUnit(settings('eV')), { unit: 'eV', factor: FACTORS.eV })
  // 0.01 Eh is 6.28 kcal/mol, not "0.01" labelled kcal/mol.
  assert.equal(formatDelta(0.01, null), '6.28')
})

test('formatDelta: the unit’s decimals, n/a for no value, and never "-0.00"', () => {
  assert.equal(formatDelta(null, settings('kcal/mol')), 'n/a')
  assert.equal(formatDelta(0.01, settings('kcal/mol')), '6.28')
  assert.equal(formatDelta(0.01, settings('kJ/mol')), '26.25')
  assert.equal(formatDelta(0.01, settings('eV')), '0.272')
  assert.equal(formatDelta(-0.0123456789, settings('hartree')), '-0.012346')
  assert.equal(formatDelta(-1e-7, settings('kcal/mol')), '0.00')
  assert.equal(formatDelta(-1e-9, settings('hartree')), '0.000000')
  assert.equal(formatDelta(-0.01, settings('kcal/mol')), '-6.28')
})

test('pathChip names the lowest top and keeps its sign', () => {
  const path = (top, extra = {}) => ({ top, label: '', gate: null, reached_end: null, on_edge: false, program: 'xTB', ...extra })
  const one = pathChip([path(0.02)], settings('kcal/mol'))
  assert.equal(one.text, 'xTB path · top +12.55 kcal/mol')
  assert.equal(one.missed, false)
  const two = pathChip([path(0.02), path(-0.001, { gate: 'missed' })], settings('kcal/mol'))
  assert.equal(two.text, 'xTB paths (2) · lowest top -0.63 kcal/mol')
  assert.equal(two.missed, true)
  assert.equal(pathChip([path(null)], settings('kcal/mol')).text, 'xTB path · top n/a')
})

test('the 1 M standard-state shift matches the backend (1.894 kcal/mol at 298.15 K)', () => {
  // thermochem.standard_state_correction(298.15) from the backend.
  close(standardStateCorrection(298.15), 0.0030188045341027884, 1e-15)
  assert.equal((standardStateCorrection(298.15) * FACTORS['kcal/mol']).toFixed(3), '1.894')
  // RT ln(RT / p) grows with the temperature.
  assert.ok(standardStateCorrection(373.15) > standardStateCorrection(298.15))
})

test('the standard-state shift refuses a temperature at or below zero, as the backend does', () => {
  for (const t of [0, -10, Number.NaN, Infinity]) assert.throws(() => standardStateCorrection(t), RangeError)
})

test('a concentration adds RT ln(c / 1 M) to the 1 M shift, as the backend does', () => {
  const T = 298.15
  close(concentrationCorrection(T, { kind: '1 M' }), standardStateCorrection(T), 0)
  // concentrations.correction from the backend.
  close(concentrationCorrection(T, { kind: 'molar', molarity: 0.01 }), -0.0013293274684334929, 1e-15)
  close(concentrationCorrection(T, { kind: 'neat', solvent: 'THF', molarity: 12.3 }), 0.005388330181491548, 1e-15)
  // A tenfold change is RT ln 10 apart, whatever the starting value.
  const step = concentrationCorrection(T, { kind: 'molar', molarity: 2 }) - concentrationCorrection(T, { kind: 'molar', molarity: 0.2 })
  close(step * FACTORS['kcal/mol'], (8.31446261815323 * T * Math.log(10)) / 4184, 1e-9)
})

test('a molarity of zero, below zero or not a number is refused, never ln(0)', () => {
  for (const molarity of [0, -1, Number.NaN, Infinity])
    assert.throws(() => concentrationCorrection(298.15, { kind: 'molar', molarity }), RangeError)
  assert.throws(() => concentrationCorrection(298.15, { kind: 'neat', solvent: 'THF', molarity: 0 }), RangeError)
})

test('energyTypeName always names G’s standard state, and G_qh’s parameters', () => {
  assert.equal(energyTypeName('E'), 'E')
  assert.equal(energyTypeName('G'), 'G (1 atm)')
  assert.equal(energyTypeName('G', undefined, undefined, '1 M', [298.15, 373.15]), 'G (1 M, 298.15 K, 373.15 K)')
  assert.equal(energyTypeName('G_qh'), 'G_qh (1 atm)')
  assert.equal(energyTypeName('G_qh', 298.15, 100, '1 M'), 'G_qh (298.15 K, 100 cm⁻¹, 1 M)')
})
