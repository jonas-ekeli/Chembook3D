import assert from 'node:assert/strict'
import { test } from 'node:test'
import { fixModeText } from '../src/localJobs.ts'

const job = (fix_mode) => ({ result: { fix_mode } })

test('fixModeText says what each try along the mode ended with and which was kept (D261)', () => {
  assert.equal(fixModeText({ result: {} }), null)
  const tries = [
    { shift: 0.1, converged: true, energy: -1.002, cycles: 5, imaginary: [-12.4] },
    { shift: -0.1, converged: true, energy: -1.004, cycles: 6, imaginary: [] },
    { shift: 0.3, converged: false, energy: -1.0, cycles: 300, imaginary: null },
  ]
  assert.equal(
    fixModeText(job({ tries, kept: 1, fixed: true, wavenumber: -27, start_energy: -1, change_kcal: -2.51 })),
    'Tried +0.1 Å: 1 imaginary (12i); -0.1 Å: no imaginary mode; +0.3 Å: did not converge. Kept -0.1 Å, the lowest without an imaginary mode, -2.51 kcal/mol from the start.',
  )
  assert.equal(
    fixModeText(job({ tries: tries.slice(0, 1), kept: 0, fixed: false, wavenumber: -27, start_energy: null, change_kcal: null })),
    'Tried +0.1 Å: 1 imaginary (12i). No try lost the imaginary mode; kept the lowest, +0.1 Å.',
  )
})
