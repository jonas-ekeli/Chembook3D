import assert from 'node:assert/strict'
import { test } from 'node:test'
import { initialTicks, pick, setTick, unpicked } from '../src/mergeTicks.ts'

// D235h: ticking a change brings what it needs; unticking one unticks what needs it.
const change = (id, kind = 'added', requires = [], extra = {}) => ({
  id,
  record_type: id.split(':')[0],
  record_id: id.split(':')[1],
  kind,
  label: id,
  summary: '',
  fields: [],
  conflict: kind === 'conflict' ? 'changed on both sides' : null,
  ticked: kind === 'added' || kind === 'changed',
  requires,
  blocked: null,
  ...extra,
})

const changes = [
  change('node:a'),
  change('node:b', 'conflict'),
  change('transition:e', 'added', ['node:a', 'node:b']),
  change('calculation:c', 'added', ['node:a']),
  change('node:gone', 'removed'),
  change('node:held', 'removed', [], { blocked: 'the original added a calculation to it since' }),
]

test('removals and conflicts start unticked; conflicts wait for a side', () => {
  const ticks = initialTicks(changes)
  assert.deepEqual([...ticks.ticked].sort(), ['calculation:c', 'node:a', 'transition:e'])
  assert.deepEqual(unpicked(changes, ticks).map((c) => c.id), ['node:b'])
})

test('unticking a node unticks what needs it; ticking an edge brings its nodes', () => {
  let ticks = initialTicks(changes)
  const off = setTick(changes, ticks, 'node:a', false)
  assert.deepEqual([...off.ticks.ticked], [])
  assert.deepEqual(off.alsoUnticked.sort(), ['calculation:c', 'transition:e'])
  ticks = off.ticks
  const on = setTick(changes, ticks, 'transition:e', true)
  assert.deepEqual([...on.ticks.ticked].sort(), ['node:a', 'node:b', 'transition:e'])
  assert.equal(on.ticks.picks['node:b'], 'playground')
  assert.deepEqual(on.alsoTicked.sort(), ['node:a', 'node:b'])
})

test("picking the original's side unticks a conflict and what needs it", () => {
  let ticks = setTick(changes, initialTicks(changes), 'transition:e', true).ticks
  const kept = pick(changes, ticks, 'node:b', 'original')
  assert.equal(kept.ticks.picks['node:b'], 'original')
  assert.ok(!kept.ticks.ticked.has('node:b') && !kept.ticks.ticked.has('transition:e'))
  assert.deepEqual(unpicked(changes, kept.ticks), [])
  ticks = setTick(changes, kept.ticks, 'node:held', true).ticks
  assert.ok(!ticks.ticked.has('node:held'))
})
