/** D193: which canvas a record is on, and how the canvas open in the app shows it. A
 * sub-investigation is a box with a canvas of its own: closed, expanded in place ("Expand
 * here"), or opened as the whole canvas ("Open"). These helpers are shared by the app, the
 * canvas and the read-only copy. */
import type { Canvas, Node, SubInvestigation } from './api'

/** The main canvas. */
export const MAIN = null

export function subsOf(canvas: Pick<Canvas, 'sub_investigations'>): SubInvestigation[] {
  return canvas.sub_investigations ?? []
}

/** The canvas a node, group or box is on (a box: the one it sits in); undefined if unknown. */
export function canvasOf(canvas: Canvas, id: string): string | null | undefined {
  const node = canvas.nodes.find((n) => n.id === id)
  if (node) return node.canvas_id ?? null
  const group = canvas.groups.find((g) => g.id === id)
  if (group) return group.canvas_id ?? null
  const sub = subsOf(canvas).find((s) => s.id === id)
  if (sub) return sub.parent_id
  return undefined
}

/** The boxes from the main canvas down to `id`, outermost first (the breadcrumb). */
export function chainOf(subs: SubInvestigation[], id: string | null): SubInvestigation[] {
  const byId = new Map(subs.map((s) => [s.id, s]))
  const found: SubInvestigation[] = []
  let current = id
  while (current !== null && !found.some((s) => s.id === current)) {
    const sub = byId.get(current)
    if (!sub) break
    found.unshift(sub)
    current = sub.parent_id
  }
  return found
}

/** Whether canvas `id` is `box` or inside it, at any depth. */
export function isWithin(subs: SubInvestigation[], id: string | null, box: string): boolean {
  return chainOf(subs, id).some((s) => s.id === box)
}

/** The canvases drawn: the open one and, inside it, every expanded box's, at any depth. */
export function visibleCanvases(subs: SubInvestigation[], open: string | null, expanded: Set<string>): Set<string | null> {
  const visible = new Set<string | null>([open])
  let grew = true
  while (grew) {
    grew = false
    for (const sub of subs)
      if (!visible.has(sub.id) && expanded.has(sub.id) && visible.has(sub.parent_id)) {
        visible.add(sub.id)
        grew = true
      }
  }
  return visible
}

/** How a record on canvas `id` is shown: on its own card ("self"), by the closed box that holds
 * it on a canvas drawn ("box", the outermost such box), or not at all, being outside the open
 * canvas ("outside"). */
export type Place = { kind: 'self' } | { kind: 'box'; id: string } | { kind: 'outside' }

export function placeOf(subs: SubInvestigation[], visible: Set<string | null>, id: string | null): Place {
  if (visible.has(id)) return { kind: 'self' }
  // The outermost box on a drawn canvas, walking out from the record's own canvas.
  const chain = chainOf(subs, id)
  for (const sub of chain) if (visible.has(sub.parent_id)) return { kind: 'box', id: sub.id }
  return { kind: 'outside' }
}

/** A canvas's name: the box's label, or "Main canvas". */
export function canvasName(subs: SubInvestigation[], id: string | null): string {
  if (id === null) return 'Main canvas'
  const sub = subs.find((s) => s.id === id)
  return sub ? subName(sub) : 'Main canvas'
}

export const subName = (sub: SubInvestigation) => sub.label || 'Sub-investigation'

/** "in Ru-14e rotations", or "" on the main canvas: where a record is, for lists and pickers. */
export function whereText(canvas: Canvas, id: string): string {
  const on = canvasOf(canvas, id)
  return on ? `in ${canvasName(subsOf(canvas), on)}` : ''
}

/** What a box holds, as its card's line: "7 nodes · 3 TSs · 1 group". */
export function contentsText(sub: SubInvestigation): string {
  const parts = [`${sub.node_count} node${sub.node_count === 1 ? '' : 's'}`]
  if (sub.ts_count) parts.push(`${sub.ts_count} TS${sub.ts_count === 1 ? '' : 's'}`)
  if (sub.group_count) parts.push(`${sub.group_count} group${sub.group_count === 1 ? '' : 's'}`)
  if (sub.sub_count) parts.push(`${sub.sub_count} sub-investigation${sub.sub_count === 1 ? '' : 's'}`)
  return parts.join(' · ')
}

/** The nodes inside a box at any depth, group members among them, in canvas order. */
export function nodesInside(canvas: Canvas, id: string): Node[] {
  const subs = subsOf(canvas)
  return canvas.nodes.filter((n) => isWithin(subs, n.canvas_id ?? null, id))
}
