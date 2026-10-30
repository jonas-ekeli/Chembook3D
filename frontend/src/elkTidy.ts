/**
 * D207: Tidy with springs, for big canvases and catalytic cycles: the Eclipse Layout Kernel's
 * stress layout (elkjs, EPL-2.0 or GPL-3.0-or-later). Stress majorization is the "balls on
 * springs" family done exactly: each arrow pulls its two boxes to one length and every pair of
 * boxes keeps a distance by how many arrows apart they are, so a cycle comes out as a loop.
 * Only the boxes that move go to ELK, at their measured sizes; the result keeps the moving boxes'
 * top-left corner and is then settled clear of the boxes that stay (`settle`). ELK is loaded only
 * when first used, through the app, so neither its main bundle nor the read-only copy carries it.
 * D208: in place, ELK starts from where the boxes are (its interactive mode) instead of a layout
 * of its own, so they settle near their places, and the middle of the boxes stays where it was.
 */
import type { ElkExtendedEdge, ElkNode } from 'elkjs/lib/elk-api'
import { COLUMN_GAP, settle, type Positions, type Rect, type TidyBox, type TidyLink } from './tidy'

/** Room between two joined boxes for the arrow's labels (D98: energies, species, scan chips). */
const LABEL_ROOM = 220

const OPTIONS = {
  'elk.algorithm': 'stress',
  'elk.spacing.componentComponent': String(COLUMN_GAP),
}

/** An arrow's length, centre to centre: half of each box's diagonal plus room for its labels,
 * so wide group boxes get arrows as long as small cards do between their edges (D208). */
function edgeLength(a: TidyBox, b: TidyBox) {
  return Math.round((Math.hypot(a.w, a.h) + Math.hypot(b.w, b.h)) / 2 + LABEL_ROOM)
}

let loading: Promise<{ layout: (graph: ElkNode) => Promise<ElkNode> }> | null = null
function elk() {
  loading ??= import('elkjs/lib/elk.bundled.js').then(({ default: ELK }) => new ELK())
  return loading
}

export async function springTidy(boxes: TidyBox[], links: TidyLink[], inPlace = false): Promise<Positions> {
  const moving = boxes.filter((b) => b.moves)
  if (!moving.length) return {}
  const byId = new Map(moving.map((b) => [b.id, b]))
  const edges: ElkExtendedEdge[] = links
    .filter((l) => l.source !== l.target && byId.has(l.source) && byId.has(l.target))
    .map((l, i) => ({
      id: `e${i}`,
      sources: [l.source],
      targets: [l.target],
      layoutOptions: { 'elk.stress.desiredEdgeLength': String(edgeLength(byId.get(l.source)!, byId.get(l.target)!)) },
    }))
  const children: ElkNode[] = moving.map((b) => ({ id: b.id, x: b.x, y: b.y, width: b.w, height: b.h }))
  const layoutOptions = inPlace ? { ...OPTIONS, 'elk.interactive': 'true' } : OPTIONS
  const laid = await (await elk()).layout({ id: 'root', layoutOptions, children, edges })
  const corners = new Map((laid.children ?? []).map((c) => [c.id, { x: c.x ?? 0, y: c.y ?? 0 }]))

  // ELK moves its result to its own origin: put it back at the moving boxes' top-left corner, or
  // in place at the middle of their centres.
  const anchor = (place: (b: TidyBox) => { x: number; y: number }) =>
    inPlace
      ? {
          x: moving.reduce((sum, b) => sum + place(b).x + b.w / 2, 0) / moving.length,
          y: moving.reduce((sum, b) => sum + place(b).y + b.h / 2, 0) / moving.length,
        }
      : { x: Math.min(...moving.map((b) => place(b).x)), y: Math.min(...moving.map((b) => place(b).y)) }
  const from = anchor((b) => b)
  const to = anchor((b) => corners.get(b.id) ?? b)
  const placed = new Map<string, Rect>()
  for (const b of moving) {
    const c = corners.get(b.id)
    placed.set(b.id, c ? { x: from.x + c.x - to.x, y: from.y + c.y - to.y, w: b.w, h: b.h } : { ...b })
  }
  settle(placed, boxes.filter((b) => !b.moves))
  const result: Positions = {}
  for (const [id, r] of placed) result[id] = { x: Math.round(r.x), y: Math.round(r.y) }
  return result
}
