import assert from 'node:assert/strict'
import { test } from 'node:test'
import { awaitingLocalImport, fixModeText, followModeText } from '../src/localJobs.ts'

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

test('followModeText says how each end relaxed and what it matches; nothing is imported (D263c)', () => {
  const ends = { '+': { shift: 0.1, converged: true, cycles: 14, energy: 0 }, '-': { shift: -0.1, converged: false, cycles: 40, energy: 0 } }
  const job = {
    kind: 'follow_mode',
    node_label: 'TS',
    follow_mode: { ts_label: 'TS' },
    result: { follow_mode: { wavenumber: -500, shift: 0.1, steps: 40, ends } },
    importable: false,
    nothing_to_import: true,
  }
  assert.equal(followModeText({ result: {} }), null)
  assert.equal(followModeText(job), 'Followed the mode of “TS”: + end converged after 14 steps, − end stopped after 40 steps.')
  const end = (matches, files) => ({ shift: 0.1, converged: true, cycles: 1, energy: 0, formed: [], broken: [], matches, files })
  const a = { node_id: 'a', label: 'A', role: 'minimum', rmsd: 0.123 }
  const b = { node_id: 'b', label: 'B', role: 'minimum', rmsd: 0.2 }
  const one = { outcome: 'one_end', ends: { '+': end([a]), '-': end([]) } }
  assert.equal(followModeText(job, one), 'Followed the mode of “TS”: + end matches “A” (0.12 Å), − end matches nothing.')
  const links = { outcome: 'links', ends: { '+': end([a]), '-': end([], [{ path: 'b.log', step: 2, rmsd: 0.3 }]) } }
  assert.equal(
    followModeText(job, links),
    'Followed the mode of “TS”: + end matches “A” (0.12 Å), − end matches b.log (step 2) (0.30 Å). It links them (A184).',
  )
  const two = { outcome: 'ambiguous', ends: { '+': end([a, b]), '-': end([b]) } }
  assert.match(followModeText(job, two), /\+ end matches 2 structures, nearest “A” \(0\.12 Å\)/)
  assert.equal(awaitingLocalImport({ ...job, importable: true, import_error: null }), false)
})
