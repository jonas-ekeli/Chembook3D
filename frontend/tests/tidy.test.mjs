import assert from 'node:assert/strict'
import { test } from 'node:test'
import { COLUMN_GAP, crossings, facingSides, isotonic, ROW_GAP, settle, tidy } from '../src/tidy.ts'

const box = (id, step, extra = {}) => ({ id, x: 0, y: 0, w: 120, h: 60, step, moves: true, ...extra })
const link = (source, target) => ({ source, target })
const CLEAR = 24 // tidy.ts keeps tidied boxes this far from one that stays
const apart = (a, b, gap) =>
  a.x + a.w + gap <= b.x || b.x + b.w + gap <= a.x || a.y + a.h + gap <= b.y || b.y + b.h + gap <= a.y

/** The rectangles of `boxes` at the places `tidy` gave the moving ones. */
function placedRects(boxes, positions) {
  return boxes.map((b) => ({ id: b.id, w: b.w, h: b.h, ...(positions[b.id] ?? { x: b.x, y: b.y }) }))
}

test('nothing moves when no box is tidied', () => {
  assert.deepEqual(tidy([box('a', 0, { moves: false })], []), {})
  assert.deepEqual(tidy([], []), {})
})

test('one column per reaction step, left to right in step order', () => {
  const boxes = [box('c', 2, { x: 500, y: 30 }), box('a', 0, { x: 40, y: 10 }), box('b', 1, { x: 900, y: 20 })]
  const at = tidy(boxes, [link('a', 'b'), link('b', 'c')])
  assert.equal(at.a.x, 40) // the layout keeps the moving boxes' top-left corner
  assert.equal(at.b.x, 40 + 120 + COLUMN_GAP)
  assert.equal(at.c.x, 40 + 2 * (120 + COLUMN_GAP))
  // A chain is drawn level.
  assert.equal(at.a.y, at.b.y)
  assert.equal(at.b.y, at.c.y)
  assert.equal(Math.min(at.a.y, at.b.y, at.c.y), 10)
})

test('a column is as wide as its widest box', () => {
  const at = tidy([box('a', 0), box('w', 0, { w: 300, y: 100 }), box('b', 1)], [link('a', 'b')])
  assert.equal(at.b.x - at.a.x, 300 + COLUMN_GAP)
})

test('a box without a step follows the box it is reached from, or comes before the one it leads to', () => {
  const boxes = [box('s0', 0), box('free', null, { x: 999 }), box('s1', 1), box('pre', null, { x: -50 })]
  const at = tidy(boxes, [link('s0', 'free'), link('free', 's1'), link('pre', 's0')])
  assert.ok(at.pre.x < at.s0.x)
  assert.ok(at.s0.x < at.free.x)
  assert.ok(at.free.x < at.s1.x)
})

test('boxes joined to no step go after the last step', () => {
  const at = tidy([box('s0', 0), box('s1', 1), box('loose', null, { x: -500 })], [link('s0', 's1')])
  assert.ok(at.loose.x > at.s1.x)
})

test('boxes in a column keep ROW_GAP between them and never overlap', () => {
  const boxes = [box('r', 0), ...['a', 'b', 'c', 'd'].map((id, i) => box(id, 1, { y: i * 10, h: 40 + i * 20 }))]
  const at = tidy(boxes, ['a', 'b', 'c', 'd'].map((id) => link('r', id)))
  const column = placedRects(boxes, at)
    .filter((r) => r.id !== 'r')
    .sort((p, q) => p.y - q.y)
  for (let i = 1; i < column.length; i++) assert.ok(column[i].y >= column[i - 1].y + column[i - 1].h + ROW_GAP - 1)
})

test('the order in each column removes crossings where it can', () => {
  // a1 → b2 and a2 → b1, as drawn now, cross once; tidy reorders a column to untangle them.
  const boxes = [box('a1', 0, { y: 0 }), box('a2', 0, { y: 100 }), box('b1', 1, { y: 0 }), box('b2', 1, { y: 100 })]
  const links = [link('a1', 'b2'), link('a2', 'b1')]
  assert.equal(crossings([['a1', 'a2'].map((id) => ({ id })), ['b1', 'b2'].map((id) => ({ id }))], links), 1)
  const at = tidy(boxes, links)
  const order = (ids) => ids.map((id) => ({ id })).sort((p, q) => at[p.id].y - at[q.id].y)
  assert.equal(crossings([order(['a1', 'a2']), order(['b1', 'b2'])], links), 0)
})

test('crossings counts only arrows between neighbouring columns', () => {
  const columns = [[{ id: 'a' }, { id: 'b' }], [{ id: 'c' }, { id: 'd' }], [{ id: 'e' }]]
  assert.equal(crossings(columns, [link('a', 'd'), link('b', 'c')]), 1)
  assert.equal(crossings(columns, [link('a', 'c'), link('b', 'd')]), 0)
  // Either direction counts the same; a skip of a column and an unknown box count nothing.
  assert.equal(crossings(columns, [link('d', 'a'), link('c', 'b')]), 1)
  assert.equal(crossings(columns, [link('a', 'e'), link('b', 'zz')]), 0)
})

test('tidied boxes are moved clear of boxes that stay, and the result is whole numbers', () => {
  const staying = box('stay', null, { x: 0, y: 0, w: 400, h: 200, moves: false })
  const boxes = [staying, box('a', 0, { x: 10, y: 10 }), box('b', 1, { x: 10, y: 10 }), box('c', 1, { x: 10, y: 90 })]
  const at = tidy(boxes, [link('a', 'b'), link('a', 'c')])
  assert.equal(at.stay, undefined)
  const rects = placedRects(boxes, at)
  for (const r of rects.slice(1)) {
    assert.ok(apart(r, staying, CLEAR - 1e-9), `${r.id} clear of the box that stays`)
    assert.ok(Number.isInteger(r.x) && Number.isInteger(r.y))
  }
  for (let i = 1; i < rects.length; i++) for (let j = i + 1; j < rects.length; j++) assert.ok(apart(rects[i], rects[j], 0))
})

test('tidy is deterministic and ignores arrows to boxes that stay and loops', () => {
  const boxes = [box('a', 0), box('b', 1, { y: 50 }), box('s', null, { moves: false, x: 2000 })]
  const links = [link('a', 'b'), link('b', 's'), link('a', 'a')]
  assert.deepEqual(tidy(boxes, links), tidy(boxes, links))
})

test('isotonic gives the closest non-decreasing values', () => {
  assert.deepEqual(isotonic([1, 2, 3], [1, 1, 1]), [1, 2, 3])
  assert.deepEqual(isotonic([3, 1], [1, 1]), [2, 2])
  assert.deepEqual(isotonic([3, 1], [3, 1]), [2.5, 2.5])
  assert.deepEqual(isotonic([1, 5, 3, 3, 6], [1, 1, 1, 1, 1]), [1, 11 / 3, 11 / 3, 11 / 3, 6])
  assert.deepEqual(isotonic([], []), [])
  const fitted = isotonic([5, 4, 3, 2, 1], [1, 2, 3, 4, 5])
  for (let i = 1; i < fitted.length; i++) assert.ok(fitted[i] >= fitted[i - 1])
  // The weighted mean is kept.
  const mean = (values) => values.reduce((s, v, i) => s + v * (i + 1), 0)
  assert.ok(Math.abs(mean(fitted) - mean([5, 4, 3, 2, 1])) < 1e-9)
})

test('settle pushes overlapping boxes apart and clear of boxes that stay', () => {
  const placed = new Map([
    ['a', { x: 0, y: 0, w: 100, h: 50 }],
    ['b', { x: 10, y: 5, w: 100, h: 50 }],
    ['c', { x: 20, y: 10, w: 100, h: 50 }],
  ])
  const staying = [{ x: 0, y: 0, w: 60, h: 60 }]
  settle(placed, staying)
  const moved = [...placed.values()]
  for (let i = 0; i < moved.length; i++) {
    assert.ok(apart(moved[i], staying[0], CLEAR - 1e-9))
    for (let j = i + 1; j < moved.length; j++) assert.ok(apart(moved[i], moved[j], CLEAR - 1e-9))
  }
})

test('settle pushes two tall boxes side by side apart sideways, the shorter way out', () => {
  const a = { x: 0, y: 0, w: 100, h: 200 }
  const b = { x: 90, y: 0, w: 100, h: 200 }
  settle(new Map([['a', a], ['b', b]]), [])
  assert.equal(a.y, 0)
  assert.equal(b.y, 0)
  assert.ok(a.x < 0 && b.x > 90)
  assert.ok(apart(a, b, CLEAR - 1e-9))
})

test('settle leaves boxes that are already apart where they are', () => {
  const placed = new Map([
    ['a', { x: 0, y: 0, w: 100, h: 50 }],
    ['b', { x: 400, y: 0, w: 100, h: 50 }],
  ])
  settle(placed, [{ x: 0, y: 300, w: 50, h: 50 }])
  assert.deepEqual(placed.get('a'), { x: 0, y: 0, w: 100, h: 50 })
  assert.deepEqual(placed.get('b'), { x: 400, y: 0, w: 100, h: 50 })
})

test('facingSides picks the sides that face each other', () => {
  const a = { x: 0, y: 0, w: 100, h: 50 }
  assert.deepEqual(facingSides(a, { x: 300, y: 0, w: 100, h: 50 }), { source_side: 'right', target_side: 'left' })
  assert.deepEqual(facingSides(a, { x: -300, y: 20, w: 100, h: 50 }), { source_side: 'left', target_side: 'right' })
  assert.deepEqual(facingSides(a, { x: 0, y: 300, w: 100, h: 50 }), { source_side: 'bottom', target_side: 'top' })
  assert.deepEqual(facingSides(a, { x: 30, y: -300, w: 100, h: 50 }), { source_side: 'top', target_side: 'bottom' })
  // Swapping the two boxes swaps the sides.
  const b = { x: 150, y: 400, w: 80, h: 40 }
  const there = facingSides(a, b)
  const back = facingSides(b, a)
  assert.deepEqual([back.source_side, back.target_side], [there.target_side, there.source_side])
})
