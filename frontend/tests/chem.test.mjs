import assert from 'node:assert/strict'
import { test } from 'node:test'
import {
  bonds,
  defaultRotation,
  elementColour,
  formatAtomList,
  fragment,
  hiddenAtoms,
  measure,
  parseAtomList,
  parseXyz,
  project,
} from '../src/chem.ts'

const WATER = `3
water, HF/STO-3G
O   0.000000   0.000000   0.117300
H   0.000000   0.757200  -0.469200
H   0.000000  -0.757200  -0.469200
`
// Methanol: C–H hydrogens and one O–H.
const METHANOL = parseXyz(`6

C  -0.046  0.663  0.000
O  -0.046 -0.758  0.000
H  -1.086  0.975  0.000
H   0.437  1.071  0.890
H   0.437  1.071 -0.890
H   0.861 -1.060  0.000
`)
const close = (actual, expected, tolerance) => assert.ok(Math.abs(actual - expected) <= tolerance, `${actual} is not ${expected}`)

test('parseXyz reads atom lines and skips the count, the comment and anything unreadable', () => {
  const atoms = parseXyz(WATER)
  assert.deepEqual(atoms[0], { element: 'O', x: 0, y: 0, z: 0.1173 })
  assert.equal(atoms.length, 3)
  // Windows line endings, lower-case and upper-case symbols, extra columns.
  assert.deepEqual(parseXyz('1\r\nx\r\n  cl 1 2 3 0.5\r\nRU -1e-1 2 3\r\n'), [
    { element: 'Cl', x: 1, y: 2, z: 3 },
    { element: 'Ru', x: -0.1, y: 2, z: 3 },
  ])
  // Not a symbol, an atom number, a number that is not one, too few columns.
  assert.deepEqual(parseXyz('6 0 0 0\nC 1 2\nC 1 2 x\nC1 0 0 0\nH 0 0 NaN\n'), [])
  assert.deepEqual(parseXyz(''), [])
})

test('bonds joins atoms closer than 1.2 times their covalent radii', () => {
  assert.deepEqual(bonds(parseXyz(WATER)), [
    [0, 1],
    [0, 2],
  ])
  const pairs = bonds(METHANOL)
  assert.equal(pairs.length, 5)
  for (const [i, j] of pairs) assert.ok(i < j)
  // Two atoms on top of each other (0.4 Å or less) are not a bond.
  assert.deepEqual(bonds(parseXyz('C 0 0 0\nC 0 0 0.3')), [])
  // An element without a radius counts as 1.5 Å.
  assert.deepEqual(bonds(parseXyz('Xe 0 0 0\nXe 0 0 3.5')), [[0, 1]])
  assert.deepEqual(bonds(parseXyz('Xe 0 0 0\nXe 0 0 3.7')), [])
})

test('elementColour has a colour for known elements and one for the rest', () => {
  assert.notEqual(elementColour('C'), elementColour('O'))
  assert.equal(elementColour('Xe'), elementColour('Unknown'))
})

test('measure gives distances in Å and angles and dihedrals in degrees', () => {
  const water = parseXyz(WATER)
  const oh = measure(water.slice(0, 2))
  assert.equal(oh.kind, 'distance')
  close(oh.value, Math.hypot(0.7572, 0.5865), 1e-12)
  const angle = measure([water[1], water[0], water[2]])
  assert.equal(angle.kind, 'angle')
  close(angle.value, 104.5, 0.2)
  assert.equal(measure(water.slice(0, 1)), null)
  assert.equal(measure([]), null)
  assert.equal(measure([...water, ...water]), null)
})

test('a straight angle is 180°, never NaN from rounding', () => {
  const line = parseXyz('C 0 0 0\nC 1.2 0 0\nC 2.4 0 0')
  close(measure([line[0], line[1], line[2]]).value, 180, 1e-9)
})

test('the dihedral follows the IUPAC sign and wraps to ±180°', () => {
  const at = (deg) => {
    const r = (deg * Math.PI) / 180
    return parseXyz(`C 1 0 0\nC 0 0 0\nC 0 0 1.5\nC ${Math.cos(r)} ${Math.sin(r)} 1.5`)
  }
  for (const deg of [0, 60, 120, 179, -60, -120]) close(measure(at(deg)).value, deg, 1e-9)
  close(Math.abs(measure(at(180)).value), 180, 1e-9)
  // Reversing the four atoms gives the same dihedral.
  close(measure([...at(75)].reverse()).value, 75, 1e-9)
})

test('hiddenAtoms: all, none, or only hydrogens bonded to carbon alone', () => {
  assert.deepEqual([...hiddenAtoms(METHANOL, 'all')], [])
  assert.deepEqual([...hiddenAtoms(METHANOL, 'none')].sort(), [2, 3, 4, 5])
  assert.deepEqual([...hiddenAtoms(METHANOL, 'polar')].sort(), [2, 3, 4])
  // A hydride on a metal and a lone H stay in "polar".
  const hydride = parseXyz('Ru 0 0 0\nH 0 0 1.6\nH 5 5 5')
  assert.deepEqual([...hiddenAtoms(hydride, 'polar')], [])
  // An agostic C–H···M (bonded to C and to the metal) stays.
  const agostic = parseXyz('C 0 0 0\nH 0 0 1.09\nRu 0 1.2 2.6')
  assert.deepEqual(bonds(agostic), [
    [0, 1],
    [1, 2],
  ])
  assert.deepEqual([...hiddenAtoms(agostic, 'polar')], [])
})

test('parseAtomList reads numbers and upward ranges in the order given', () => {
  assert.deepEqual(parseAtomList('1-4, 7 5;9'), { numbers: [1, 2, 3, 4, 7, 5, 9] })
  assert.deepEqual(parseAtomList('3–5'), { numbers: [3, 4, 5] })
  assert.deepEqual(parseAtomList('  '), { numbers: [] })
  assert.deepEqual(parseAtomList('2-2'), { numbers: [2] })
  assert.match(parseAtomList('5-3').error, /not a range/)
  assert.match(parseAtomList('0-3').error, /not a range/)
  assert.match(parseAtomList('0').error, /not an atom number/)
  assert.match(parseAtomList('1.5').error, /“1.5”/)
  assert.match(parseAtomList('C1').error, /not an atom number/)
})

test('formatAtomList and parseAtomList round-trip', () => {
  assert.equal(formatAtomList([1, 2, 3, 4, 7, 5]), '1-4, 7, 5')
  assert.equal(formatAtomList([1, 2, 4, 5]), '1, 2, 4, 5')
  assert.equal(formatAtomList([]), '')
  for (const numbers of [[1, 2, 3, 4, 7, 5], [10, 11, 12, 1, 2, 3, 3], [8], [3, 2, 1]])
    assert.deepEqual(parseAtomList(formatAtomList(numbers)), { numbers })
})

test('fragment follows bonds from an atom without passing a cut atom', () => {
  // Ru(H)(CO): cutting the metal leaves CO as a ligand.
  const atoms = parseXyz('Ru 0 0 0\nH 0 0 -1.6\nC 0 0 1.9\nO 0 0 3.05')
  assert.deepEqual(fragment(atoms, 2, [0]), [2, 3])
  assert.deepEqual(fragment(atoms, 1, [0]), [1])
  assert.deepEqual(fragment(atoms, 0, []), [0, 1, 2, 3])
  // Methanol cut at the C–O bond: the O side.
  assert.deepEqual(fragment(METHANOL, 1, [0]), [1, 5])
})

const det3 = (m) =>
  m[0][0] * (m[1][1] * m[2][2] - m[1][2] * m[2][1]) -
  m[0][1] * (m[1][0] * m[2][2] - m[1][2] * m[2][0]) +
  m[0][2] * (m[1][0] * m[2][1] - m[1][1] * m[2][0])

test('the default rotation is a unit quaternion and a rotation, never a mirror image', () => {
  assert.deepEqual(defaultRotation([]), [0, 0, 0, 1])
  for (const atoms of [METHANOL, parseXyz(WATER)]) {
    const q = defaultRotation(atoms)
    close(Math.hypot(...q), 1, 1e-12)
    const [x, y, z, w] = q
    const matrix = [
      [1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
      [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
      [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)],
    ]
    close(det3(matrix), 1, 1e-9)
  }
})

test('project keeps distances and puts the longest direction across the picture', () => {
  const chain = parseXyz('C 0 0 0\nC 0 0 1.5\nC 0 0 3\nC 0.1 0 4.5')
  const shown = project(chain)
  assert.equal(shown.length, 4)
  // A rotation keeps every distance.
  for (let i = 0; i < chain.length; i++)
    for (let j = i + 1; j < chain.length; j++) {
      const before = measure([chain[i], chain[j]]).value
      const after = Math.hypot(shown[i].x - shown[j].x, shown[i].y - shown[j].y, shown[i].depth - shown[j].depth)
      close(after, before, 1e-9)
    }
  // The chain lies along x in the picture and is centred.
  const xs = shown.map((p) => p.x)
  assert.ok(Math.max(...xs) - Math.min(...xs) > 4.4)
  close(
    shown.reduce((s, p) => s + p.x, 0),
    0,
    1e-9,
  )
  // A saved rotation is used as given: the identity leaves the centred coordinates.
  const same = project(chain, [0, 0, 0, 1])
  close(same[0].depth, -2.25, 1e-12)
  assert.deepEqual(project([], null), [])
})
