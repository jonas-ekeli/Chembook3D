import assert from 'node:assert/strict'
import { test } from 'node:test'
import { textWidth, tickDecimals, ticks, valueRange, wrapText } from '../src/profileAxis.ts'

test('the value range always holds the reference (zero) and pads 12% each way', () => {
  const near = ({ lo, hi }, [a, b]) => assert.ok(Math.abs(lo - a) < 1e-12 && Math.abs(hi - b) < 1e-12, `${lo}..${hi}`)
  near(valueRange([10, 20]), [-2.4, 22.4])
  near(valueRange([-5, -15]), [-16.8, 1.8])
  // No values, or only zero: a span of 1 is padded, so the axis never collapses to a point.
  assert.deepEqual(valueRange([]), { lo: -0.12, hi: 0.12 })
  assert.deepEqual(valueRange([0, 0]), { lo: -0.12, hi: 0.12 })
})

test('ticks are round, evenly spaced and inside the range', () => {
  for (const [lo, hi] of [
    [-2.4, 22.4],
    [-16.8, 1.8],
    [-0.12, 0.12],
    [0.001, 0.0042],
    [-1234.5, 98765],
    [3, 3],
  ]) {
    const found = ticks(lo, hi)
    assert.ok(found.length >= 1, `${lo}..${hi} has ticks`)
    assert.ok(found.length <= 11, `${lo}..${hi} has few ticks`)
    for (const t of found) assert.ok(t >= lo - 1e-9 && t <= hi + 1e-9, `${t} in ${lo}..${hi}`)
    const steps = found.slice(1).map((t, i) => t - found[i])
    for (const s of steps) assert.ok(Math.abs(s - steps[0]) < 1e-9 * Math.max(1, Math.abs(steps[0])))
    // A step is 1, 2, 2.5 or 5 times a power of ten.
    if (steps.length) {
      const mantissa = steps[0] / 10 ** Math.floor(Math.log10(steps[0]))
      assert.ok([1, 2, 2.5, 5].some((m) => Math.abs(m - mantissa) < 1e-9), `step ${steps[0]}`)
    }
  }
  assert.deepEqual(ticks(-2.4, 22.4), [0, 5, 10, 15, 20])
  assert.deepEqual(ticks(-0.12, 0.12), [-0.1, -0.05, 0, 0.05, 0.1])
})

test('ticks have no floating-point noise, so they print as typed', () => {
  for (const t of ticks(-0.3, 0.7)) assert.equal(String(t).length <= 5, true, String(t))
  assert.deepEqual(ticks(0.1, 0.35), [0.1, 0.15, 0.2, 0.25, 0.3, 0.35])
})

test('tickDecimals is the fewest decimals that write every tick exactly', () => {
  assert.equal(tickDecimals([0, 5, 10]), 0)
  assert.equal(tickDecimals([0, 2.5, 5]), 1)
  assert.equal(tickDecimals([-0.1, -0.05, 0]), 2)
  assert.equal(tickDecimals([]), 0)
  assert.equal(tickDecimals([1 / 3]), 6)
  for (const [lo, hi] of [
    [-2.4, 22.4],
    [0.001, 0.0042],
  ]) {
    const found = ticks(lo, hi)
    const d = tickDecimals(found)
    for (const t of found) assert.equal(Number(t.toFixed(d)), t)
  }
})

// Without a browser the width is estimated: 0.6 of the font size per character.
test('textWidth estimates without a canvas', () => {
  assert.equal(textWidth('abcde', 10, 'serif'), 30)
  assert.equal(textWidth('', 10, 'serif'), 0)
})

test('wrapText keeps text that fits on one line', () => {
  assert.deepEqual(wrapText('int2', 100, 10, 'serif'), ['int2'])
  assert.deepEqual(wrapText('', 1, 10, 'serif'), [''])
})

test('wrapText breaks after spaces and punctuation, and the lines give the text back', () => {
  const text = 'Ru-CAAC oxidative_addition TS1/TS2 step.three'
  const lines = wrapText(text, 60, 10, 'serif') // ten characters a line
  assert.equal(lines.join(''), text)
  assert.ok(lines.length > 1)
  for (const line of lines) assert.ok(textWidth(line.trimEnd(), 10, 'serif') <= 60, `"${line}" fits`)
  assert.deepEqual(wrapText('alpha beta gamma', 36, 10, 'serif'), ['alpha ', 'beta ', 'gamma'])
})

test('wrapText cuts inside a word only when the word alone is wider than a line', () => {
  assert.deepEqual(wrapText('abcdefghij', 24, 10, 'serif'), ['abcd', 'efgh', 'ij'])
  assert.deepEqual(wrapText('ab cd', 24, 10, 'serif'), ['ab ', 'cd'])
  // A word too long for any line fills the line it starts on, then goes on to the next.
  assert.deepEqual(wrapText('ab cdefghij', 24, 10, 'serif'), ['ab c', 'defg', 'hij'])
  // A line narrower than one character still holds one, so wrapping always ends.
  assert.deepEqual(wrapText('abc', 1, 10, 'serif'), ['a', 'b', 'c'])
})
