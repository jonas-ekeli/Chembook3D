import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { test } from 'node:test'
import { ApiError } from '../src/api.ts'
import { closesCycle, errorText, fileName, nameFilter, ranges, scientific, withDisplacements } from '../src/util.ts'

// The examples the backend's `api/downloads.py` is tested on too (tests/test_downloads.py).
const shared = JSON.parse(readFileSync(new URL('../../tests/fixtures/file-names.json', import.meta.url), 'utf8'))

test('fileName gives the backend’s name for every shared example', () => {
  assert.ok(shared.cases.length >= 20)
  for (const { text, name } of shared.cases) assert.equal(fileName(text, shared.fallback), name, JSON.stringify(text))
})

test('fileName never leaves a character Windows refuses, nor a trailing dot or space', () => {
  const texts = [...shared.cases.map((c) => c.text), 'a <b>.', ' x?y*z ', 'end.\u0000']
  for (const text of texts) {
    const name = fileName(text, 'fallback')
    // eslint-disable-next-line no-control-regex
    assert.doesNotMatch(name, /[<>:"/\\|?*\x00-\x1f\x7f]/)
    assert.doesNotMatch(name, /[. ]$/)
    assert.doesNotMatch(name, /^ /)
    // It is a fixed point: a name already safe is kept as it is.
    assert.equal(fileName(name, 'fallback'), name)
  }
})

test('closesCycle: a pathway closes a cycle only by ending at a node it visited', () => {
  assert.equal(closesCycle(['a', 'b', 'c', 'a']), true)
  assert.equal(closesCycle(['a', 'b', 'c']), false)
  assert.equal(closesCycle(['a']), false)
  assert.equal(closesCycle([]), false)
})

test('ranges writes sorted runs with an en dash, whatever the input order', () => {
  assert.equal(ranges([10, 3, 4, 5, 6, 7]), '3–7, 10')
  assert.equal(ranges([1, 3]), '1, 3')
  assert.equal(ranges([2, 1]), '1–2')
  assert.equal(ranges([]), '')
})

test('nameFilter: plain text matches anywhere, a glob must match the whole name, case ignored', () => {
  const plain = nameFilter(' TS ')
  assert.equal(plain('int2_ts1.log'), true)
  assert.equal(plain('int2.log'), false)
  const glob = nameFilter('*.LOG')
  assert.equal(glob('ts1.log'), true)
  assert.equal(glob('ts1.log.bak'), false)
  assert.equal(nameFilter('ts?.out')('TS1.out'), true)
  assert.equal(nameFilter('ts?.out')('TS12.out'), false)
  // Regular expression characters in the text are literal.
  assert.equal(nameFilter('a+b(1)*')('a+b(1).xyz'), true)
  assert.equal(nameFilter('a.b*')('axb.xyz'), false)
  assert.equal(nameFilter('')('anything'), true)
})

test('scientific: plain between 0.01 and 1000, else a power of ten with a real minus', () => {
  assert.equal(scientific(null), 'n/a')
  assert.equal(scientific(0), '0')
  assert.equal(scientific(12.345), '12.3')
  assert.equal(scientific(1234), '1.23 × 10³')
  assert.equal(scientific(0.00012), '1.20 × 10⁻⁴')
  assert.equal(scientific(-5e12), '-5.00 × 10¹²')
  assert.equal(scientific(Infinity), '∞')
  assert.equal(scientific(-Infinity), '−∞')
})

test('withDisplacements adds one vector per atom line and keeps the header', () => {
  const xyz = '2\ncomment\nH 0 0 0\nH 0 0 0.74\n'
  const out = withDisplacements(xyz, [
    [0, 0, -0.1],
    [0, 0, 0.1],
  ])
  assert.deepEqual(out.split('\n'), ['2', 'comment', 'H 0 0 0 0.00000 0.00000 -0.10000', 'H 0 0 0.74 0.00000 0.00000 0.10000', ''])
  // An atom without a vector keeps its line.
  assert.equal(withDisplacements('1\n\nC 0 0 0', []), '1\n\nC 0 0 0')
})

test('errorText shows the blockers only when asked and when the backend sent some', () => {
  const error = new ApiError(409, { message: 'Refused', blockers: ['used by TS1', 'used by int2'] })
  assert.equal(errorText(error), 'Refused')
  assert.equal(errorText(error, { blockers: true }), 'used by TS1; used by int2')
  assert.equal(errorText(new ApiError(400, 'Bad'), { blockers: true }), 'Bad')
  assert.equal(errorText('plain'), 'plain')
})
