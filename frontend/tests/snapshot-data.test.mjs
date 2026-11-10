import assert from 'node:assert/strict'
import { gzipSync } from 'node:zlib'
import { afterEach, beforeEach, test } from 'node:test'
import {
  DATA_ELEMENT,
  energyView,
  openingView,
  readSnapshot,
  referenceIds,
  rememberView,
  sharedSettings,
  tableCsv,
} from '../src/snapshot/data.ts'
import { SCREEN_STYLE } from '../src/profileStyle.ts'

/** A small copy as `api/snapshot.py` writes it, with only the parts these tests read. */
function copy(extra = {}) {
  const relative = { r1: { n1: { value: 0 } }, r2: { n1: { value: 0.01 } } }
  return {
    format: 'chembook3d-snapshot',
    version: 1,
    app_version: '0.0.0',
    copy_id: 'copy-1',
    exported_at: '2026-11-10T00:00:00Z',
    investigation: { name: 'Test' },
    settings: {
      energy_unit: 'kJ/mol',
      energy_factors: { 'kJ/mol': 2625.4996394799 },
      energy_decimals: { 'kJ/mol': 2 },
      qh_temperature: 298.15,
      qh_cutoff: 100,
      standard_state: '1 M',
      hydrogens: 'polar',
    },
    canvas: { nodes: [], groups: [{ id: 'g1' }], sub_investigations: [{ id: 's1' }] },
    note_images: {},
    overview: {},
    calculations: {},
    modes: {},
    energy_options: {},
    views: {
      'L1|G': { values: { n1: 1 }, edges: {}, relative, notes: [{ code: 'W-TEMP', message: 'm' }] },
      'L1|E': { values: {}, edges: {}, relative },
    },
    paths: [{ ids: ['n1', 'n2'] }],
    reference_id: 'r1',
    profiles: {},
    ...extra,
  }
}

// The reader's choices are kept in the browser's localStorage, which Node has not got (or only
// behind a flag), so the tests give one; defined, not assigned, in case Node has a getter.
const setGlobal = (name, value) => Object.defineProperty(globalThis, name, { value, configurable: true, writable: true })
let stored
beforeEach(() => {
  stored = new Map()
  setGlobal('localStorage', {
    getItem: (key) => (stored.has(key) ? stored.get(key) : null),
    setItem: (key, value) => stored.set(key, String(value)),
  })
})
afterEach(() => {
  delete globalThis.localStorage
  delete globalThis.document
})

test('readSnapshot unpacks the gzip-compressed JSON in base64 that the page holds', async () => {
  const data = copy()
  const text = gzipSync(Buffer.from(JSON.stringify(data))).toString('base64')
  setGlobal('document', { getElementById: (id) => (id === DATA_ELEMENT ? { textContent: `\n ${text} \n` } : null) })
  assert.deepEqual(await readSnapshot(), data)
})

test('readSnapshot refuses an empty page, the unfilled template and other data', async () => {
  setGlobal('document', { getElementById: () => null })
  await assert.rejects(readSnapshot(), /holds no investigation/)
  setGlobal('document', { getElementById: () => ({ textContent: '__DATA__' }) })
  await assert.rejects(readSnapshot(), /holds no investigation/)
  const other = gzipSync(Buffer.from(JSON.stringify({ format: 'something else' }))).toString('base64')
  setGlobal('document', { getElementById: () => ({ textContent: other }) })
  await assert.rejects(readSnapshot(), /no Chembook3D investigation/)
})

test('sharedSettings keeps the copy’s energy settings and fills what a copy never uses', () => {
  const settings = sharedSettings(copy())
  assert.equal(settings.energy_unit, 'kJ/mol')
  assert.deepEqual(settings.energy_units, ['kJ/mol'])
  assert.equal(settings.standard_state, '1 M')
  assert.deepEqual(settings.thermo_defaults, { qh_temperature: 298.15, qh_cutoff: 100, standard_state: '1 M' })
  assert.equal(settings.theme, 'light')
  assert.deepEqual(settings.profile_style, SCREEN_STYLE)
  assert.deepEqual(settings.recent, [])
})

test('a copy made before D95 is at 1 atm', () => {
  const data = copy()
  delete data.settings.standard_state
  const settings = sharedSettings(data)
  assert.equal(settings.standard_state, '1 atm')
  assert.equal(settings.thermo_defaults.standard_state, '1 atm')
})

test('a copy without a saved view opens on the energy view of its reference, or compact', () => {
  const view = openingView(copy({ copy_id: undefined }))
  assert.equal(view.mode, 'energy')
  assert.equal(view.referenceId, 'r1')
  assert.equal(view.profileReferenceId, 'r1') // copies made before D205 share the cards'
  assert.equal(view.energyType, 'G')
  assert.equal(view.hydrogens, 'polar')
  assert.equal(view.canvas, null)
  assert.equal(openingView(copy({ reference_id: null })).mode, 'compact')
  assert.equal(openingView(copy({ profile_reference_id: null })).profileReferenceId, null)
})

test('the view saved in the copy is shown, without boxes the copy has not got', () => {
  const saved = {
    mode: 'structure',
    level: 'L1',
    energy_type: 'E',
    edge_energies: false,
    filters: { branches: ['b'], statuses: [], steps: [] },
    expanded: ['g1', 's1', 'gone'],
    canvas: 'gone',
    drawer: { open: true, tab: 'table' },
  }
  const view = openingView(copy({ view: saved }))
  assert.equal(view.mode, 'structure')
  assert.equal(view.level, 'L1')
  assert.equal(view.edgeEnergies, false)
  assert.deepEqual(view.expanded, ['g1', 's1'])
  assert.equal(view.canvas, null)
  assert.equal(view.drawerOpen, true)
  assert.equal(view.drawerTab, 'table')
  assert.equal(openingView(copy({ view: { ...saved, canvas: 's1' } })).canvas, 's1')
})

test('the reader’s own choices round-trip through their browser and win over the saved view', () => {
  const data = copy()
  const chosen = { ...openingView(data), mode: 'compact', referenceId: 'r2', drawerTab: 'table', hydrogens: 'none', canvas: 's1' }
  rememberView(data, chosen)
  assert.deepEqual(openingView(data), chosen)
  // Another copy does not see them.
  assert.equal(openingView(copy({ copy_id: 'copy-2' })).referenceId, 'r1')
})

test('stored choices that do not fit the copy are left out, one by one', () => {
  const data = copy()
  stored.set(
    'chembook3d.copy.copy-1',
    JSON.stringify({
      mode: 'huge',
      referenceId: 'not-a-reference',
      profileReferenceId: 'n2',
      filters: { branches: ['b'], statuses: 'x', steps: [] },
      expanded: [1, 2],
      drawerTab: 'chart',
      hydrogens: 'some',
      drawerOpen: 'yes',
      edgeEnergies: false,
    }),
  )
  const view = openingView(data)
  assert.equal(view.mode, 'energy')
  assert.equal(view.referenceId, 'r1')
  assert.equal(view.profileReferenceId, 'n2') // on a pathway, so the drawer can use it
  assert.deepEqual(view.filters, { branches: [], statuses: [], steps: [] })
  assert.deepEqual(view.expanded, [])
  assert.equal(view.drawerTab, 'profile')
  assert.equal(view.hydrogens, 'polar')
  assert.equal(view.drawerOpen, false)
  assert.equal(view.edgeEnergies, false)
})

test('unreadable or blocked storage leaves the copy as it was made', () => {
  const data = copy()
  const plain = openingView(data)
  stored.set('chembook3d.copy.copy-1', '{not json')
  assert.deepEqual(openingView(data), plain)
  stored.set('chembook3d.copy.copy-1', '42')
  assert.deepEqual(openingView(data), plain)
  setGlobal('localStorage', {
    getItem: () => {
      throw new Error('blocked')
    },
    setItem: () => {
      throw new Error('blocked')
    },
  })
  assert.deepEqual(openingView(data), plain)
  assert.doesNotThrow(() => rememberView(data, plain))
})

test('energyView puts the view together for a level, type and reference', () => {
  const data = copy()
  const view = energyView(data, 'L1', 'G', 'r2')
  assert.equal(view.reference_id, 'r2')
  assert.deepEqual(view.relative, { n1: { value: 0.01 } })
  assert.deepEqual(view.values, { n1: 1 })
  assert.equal(view.notes.length, 1)
  assert.deepEqual(view.populations, {}) // copies made before D197 have none
  // A reference the copy has no values for, or none: no relative energies.
  assert.equal(energyView(data, 'L1', 'G', 'zz').reference_id, null)
  assert.deepEqual(energyView(data, 'L1', 'E', null).relative, {})
  assert.deepEqual(energyView(data, 'L1', 'E', null).notes, [])
  assert.equal(energyView(data, 'L2', 'G', 'r1'), null)
})

test('referenceIds are the references of the copy’s views', () => {
  assert.deepEqual([...referenceIds(copy())].sort(), ['r1', 'r2'])
  assert.deepEqual([...referenceIds(copy({ views: {} }))], [])
})

test('tableCsv quotes as the app’s export does, with a byte order mark and CRLF', () => {
  const csv = tableCsv({ columns: ['Label', 'ΔG'], rows: [['TS1, "a"', '12.3'], ['int2\nline', '-1.0']] })
  assert.equal(csv, '﻿Label,ΔG\r\n"TS1, ""a""",12.3\r\n"int2\nline",-1.0\r\n')
  assert.equal(tableCsv({ columns: [], rows: [] }), '﻿\r\n')
})
