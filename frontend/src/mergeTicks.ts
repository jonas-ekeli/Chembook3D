// D235h: the tick boxes of a playground's merge review. A ticked change brings what it needs
// (an edge its nodes, a calculation its node); unticking a change unticks what needs it. A conflict
// is ticked only once the playground's version is picked, and a blocked change never is.

import type { MergeChange } from './api'

export type Pick = 'playground' | 'original'

export type Ticks = {
  ticked: Set<string>
  /** The side picked for each conflict; a conflict with none blocks Merge. */
  picks: Record<string, Pick>
}

export function initialTicks(changes: MergeChange[]): Ticks {
  const ticked = new Set(changes.filter((c) => c.ticked && !c.blocked && c.kind !== 'conflict').map((c) => c.id))
  return { ticked, picks: {} }
}

/** `id` and everything it needs, through what those need in turn. */
function needed(byId: Map<string, MergeChange>, id: string): string[] {
  const seen = new Set<string>()
  const walk = (at: string) => {
    if (seen.has(at)) return
    seen.add(at)
    for (const next of byId.get(at)?.requires ?? []) walk(next)
  }
  walk(id)
  return [...seen]
}

/** Every ticked change that needs `id`, directly or through another. */
function needing(changes: MergeChange[], ticked: Set<string>, id: string): string[] {
  const out = new Set<string>()
  const walk = (at: string) => {
    for (const c of changes) {
      if (ticked.has(c.id) && !out.has(c.id) && c.requires.includes(at)) {
        out.add(c.id)
        walk(c.id)
      }
    }
  }
  walk(id)
  return [...out]
}

/** Tick or untick one change; returns the new ticks and the labels of the others that moved with it. */
export function setTick(
  changes: MergeChange[],
  ticks: Ticks,
  id: string,
  on: boolean,
): { ticks: Ticks; alsoTicked: string[]; alsoUnticked: string[] } {
  const byId = new Map(changes.map((c) => [c.id, c]))
  const ticked = new Set(ticks.ticked)
  const picks = { ...ticks.picks }
  const label = (other: string) => byId.get(other)?.label ?? other
  if (on) {
    const brought = needed(byId, id).filter((other) => !byId.get(other)?.blocked)
    const alsoTicked = brought.filter((other) => other !== id && !ticked.has(other)).map(label)
    for (const other of brought) {
      ticked.add(other)
      if (byId.get(other)?.kind === 'conflict') picks[other] = 'playground'
    }
    return { ticks: { ticked, picks }, alsoTicked, alsoUnticked: [] }
  }
  const dropped = needing(changes, ticked, id)
  ticked.delete(id)
  for (const other of dropped) {
    ticked.delete(other)
    if (byId.get(other)?.kind === 'conflict') picks[other] = 'original'
  }
  if (byId.get(id)?.kind === 'conflict') picks[id] = 'original'
  return { ticks: { ticked, picks }, alsoTicked: [], alsoUnticked: dropped.map(label) }
}

/** A conflict's side picked: the playground's ticks it (with what it needs), the original's unticks it. */
export function pick(changes: MergeChange[], ticks: Ticks, id: string, side: Pick) {
  const moved = setTick(changes, ticks, id, side === 'playground')
  moved.ticks.picks[id] = side
  return moved
}

/** The conflicts still waiting for a side. */
export function unpicked(changes: MergeChange[], ticks: Ticks): MergeChange[] {
  return changes.filter((c) => c.kind === 'conflict' && !c.blocked && ticks.picks[c.id] === undefined)
}
