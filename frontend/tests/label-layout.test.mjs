import assert from 'node:assert/strict'
import { afterEach, test } from 'node:test'
import { curveOf, LabelPlacer, leaderEnd, placeLabels, pointAt } from '../src/labelLayout.ts'

const straight = (x0, y0, x1, y1) => [
  { x: x0, y: y0 },
  { x: x0 + (x1 - x0) / 3, y: y0 + (y1 - y0) / 3 },
  { x: x0 + (2 * (x1 - x0)) / 3, y: y0 + (2 * (y1 - y0)) / 3 },
  { x: x1, y: y1 },
]
const boxOf = (p) => ({ x: p.x - p.w / 2, y: p.y - p.h / 2, w: p.w, h: p.h })
const overlaps = (a, b) => a.x < b.x + b.w && b.x < a.x + a.w && a.y < b.y + b.h && b.y < a.y + a.h

test('pointAt gives the ends at 0 and 1 and the middle of a straight line at 0.5', () => {
  const curve = straight(0, 0, 300, 90)
  assert.deepEqual(pointAt(curve, 0), { x: 0, y: 0 })
  assert.deepEqual(pointAt(curve, 1), { x: 300, y: 90 })
  const middle = pointAt(curve, 0.5)
  assert.ok(Math.abs(middle.x - 150) < 1e-9 && Math.abs(middle.y - 45) < 1e-9)
})

test('curveOf reads React Flow’s Bézier path and nothing else', () => {
  assert.deepEqual(curveOf('M0,0 C50,0 50,100 100,100'), [
    { x: 0, y: 0 },
    { x: 50, y: 0 },
    { x: 50, y: 100 },
    { x: 100, y: 100 },
  ])
  assert.deepEqual(curveOf('M-1.5,2e2 C.5,-3 4,5 6,7')?.[0], { x: -1.5, y: 200 })
  assert.equal(curveOf('M0,0 L10,10'), null)
  assert.equal(curveOf(''), null)
  assert.equal(curveOf('M0,0 C1,2 3,4 5,6 7,8'), null)
})

test('a label with room sits on the middle of its line', () => {
  const placed = placeLabels([{ id: 'e', curve: straight(0, 0, 400, 0), w: 60, h: 20 }], [])
  assert.deepEqual(placed.get('e'), { x: 200, y: 0, w: 60, h: 20, anchor: { x: 200, y: 0 } })
})

test('labels on crossing lines do not cover one another', () => {
  const labels = [
    { id: 'a', curve: straight(0, 0, 400, 0), w: 80, h: 20 },
    { id: 'b', curve: straight(200, -200, 200, 200), w: 80, h: 20 },
    { id: 'c', curve: straight(0, 10, 400, -10), w: 80, h: 20 },
  ]
  const placed = placeLabels(labels, [])
  assert.equal(placed.size, 3)
  const boxes = [...placed.values()].map(boxOf)
  for (let i = 0; i < boxes.length; i++)
    for (let j = i + 1; j < boxes.length; j++) assert.ok(!overlaps(boxes[i], boxes[j]), `labels ${i} and ${j}`)
})

test('a label moves off a card covering the middle of its line, and keeps its anchor on the line', () => {
  const card = { x: 170, y: -40, w: 60, h: 80 }
  const curve = straight(0, 0, 400, 0)
  const p = placeLabels([{ id: 'e', curve, w: 50, h: 16 }], [card]).get('e')
  assert.ok(!overlaps(boxOf(p), card))
  assert.ok(Math.abs(p.anchor.y) < 1e-9 && p.anchor.x >= 0 && p.anchor.x <= 400)
  // The placement does not depend on the order the labels were asked for in.
  const others = [
    { id: 'f', curve: straight(0, 30, 400, 30), w: 50, h: 16 },
    { id: 'e', curve, w: 50, h: 16 },
  ]
  assert.deepEqual(placeLabels(others, [card]), placeLabels([...others].reverse(), [card]))
})

test('leaderEnd is null inside the label and on its edge otherwise', () => {
  assert.equal(leaderEnd({ x: 5, y: 5 }, { x: 0, y: 0 }, 40, 20), null)
  assert.deepEqual(leaderEnd({ x: 100, y: 0 }, { x: 0, y: 0 }, 40, 20), { x: 20, y: 0 })
  assert.deepEqual(leaderEnd({ x: 0, y: -50 }, { x: 0, y: 0 }, 40, 20), { x: 0, y: -10 })
  // On the way to a corner it leaves through the nearer edge.
  const end = leaderEnd({ x: 100, y: 100 }, { x: 10, y: 10 }, 40, 20)
  assert.deepEqual(end, { x: 20, y: 20 })
})

// The placer runs once per animation frame; the tests run the frame by hand.
let frames = []
const originalRequest = globalThis.requestAnimationFrame
const originalCancel = globalThis.cancelAnimationFrame
function fakeFrames() {
  frames = []
  globalThis.requestAnimationFrame = (run) => frames.push(run)
  globalThis.cancelAnimationFrame = (id) => (frames[id - 1] = null)
}
const runFrames = () => {
  const now = frames
  frames = []
  for (const run of now) run?.()
}
afterEach(() => {
  globalThis.requestAnimationFrame = originalRequest
  globalThis.cancelAnimationFrame = originalCancel
})

test('LabelPlacer places once per frame and tells listeners only when a placement moves', () => {
  fakeFrames()
  let cards = []
  const placer = new LabelPlacer(() => cards)
  let told = 0
  const stop = placer.subscribe(() => told++)
  placer.set({ id: 'e', curve: straight(0, 0, 400, 0), w: 60, h: 20 })
  placer.set({ id: 'f', curve: straight(0, 100, 400, 100), w: 60, h: 20 })
  assert.equal(frames.length, 1)
  runFrames()
  assert.equal(told, 1)
  const first = placer.get('e')
  assert.deepEqual({ x: first.x, y: first.y }, { x: 200, y: 0 })

  // The same request again changes nothing and asks for no frame.
  placer.set({ id: 'e', curve: straight(0, 0, 400, 0.001), w: 60, h: 20 })
  assert.equal(frames.length, 0)
  // Cards that did not move ask for no frame either.
  placer.cardsChanged()
  assert.equal(frames.length, 0)

  // A card on the label moves it.
  cards = [{ x: 150, y: -30, w: 100, h: 60 }]
  placer.cardsChanged()
  runFrames()
  assert.equal(told, 2)
  assert.notDeepEqual(placer.get('e'), first)
  // Placing again with nothing changed keeps the same objects and tells nobody.
  const kept = placer.get('f')
  placer.cardsChanged()
  placer.set({ id: 'e', curve: straight(0, 0, 400, 0), w: 61, h: 20 })
  runFrames()
  assert.equal(placer.get('f'), kept)

  placer.remove('e')
  runFrames()
  assert.equal(placer.get('e'), undefined)
  stop()
  const before = told
  placer.remove('f')
  runFrames()
  assert.equal(told, before)
  placer.set({ id: 'g', curve: straight(0, 0, 10, 0), w: 5, h: 5 })
  placer.dispose()
  runFrames()
  assert.equal(placer.get('g'), undefined)
})
