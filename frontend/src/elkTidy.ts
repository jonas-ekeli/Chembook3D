/**
 * D207: Tidy with springs, for big canvases and catalytic cycles: the Eclipse Layout Kernel's
 * stress layout (elkjs, EPL-2.0 or GPL-3.0-or-later). Stress majorization is the "balls on
 * springs" family done exactly: each arrow pulls its two boxes to one length and every pair of
 * boxes keeps a distance by how many arrows apart they are, so a cycle comes out as a loop.
 * Only the boxes that move go to ELK, at their measured sizes; the result keeps the moving boxes'
 * top-left corner and is then settled clear of the boxes that stay (`settle`). ELK is loaded only
 * when first used, through the app, so neither its main bundle nor the read-only copy carries it.
 */
import type { ElkExtendedEdge, ElkNode } from 'elkjs/lib/elk-api'
import { COLUMN_GAP, settle, type Positions, type Rect, type TidyBox, type TidyLink } from './tidy'

/** The arrows' length the layout aims at, between box centres. */
const EDGE_LENGTH = 300

const OPTIONS = {
  'elk.algorithm': 'stress',
  'elk.stress.desiredEdgeLength': String(EDGE_LENGTH),
  'elk.spacing.componentComponent': String(COLUMN_GAP),
}

let loading: Promise<{ layout: (graph: ElkNode) => Promise<ElkNode> }> | null = null
function elk() {
  loading ??= import('elkjs/lib/elk.bundled.js').then(({ default: ELK }) => new ELK())
  return loading
}

export async function springTidy(boxes: TidyBox[], links: TidyLink[]): Promise<Positions> {
  const moving = boxes.filter((b) => b.moves)
  if (!moving.length) return {}
  const ids = new Set(moving.map((b) => b.id))
  const edges: ElkExtendedEdge[] = links
    .filter((l) => l.source !== l.target && ids.has(l.source) && ids.has(l.target))
    .map((l, i) => ({ id: `e${i}`, sources: [l.source], targets: [l.target] }))
  const children: ElkNode[] = moving.map((b) => ({ id: b.id, x: b.x, y: b.y, width: b.w, height: b.h }))
  const laid = await (await elk()).layout({ id: 'root', layoutOptions: OPTIONS, children, edges })
  const corners = new Map((laid.children ?? []).map((c) => [c.id, { x: c.x ?? 0, y: c.y ?? 0 }]))

  // Keep the moving boxes' top-left corner, then clear of each other and of the boxes that stay.
  const x0 = Math.min(...moving.map((b) => b.x))
  const y0 = Math.min(...moving.map((b) => b.y))
  const lx = Math.min(...[...corners.values()].map((c) => c.x))
  const ly = Math.min(...[...corners.values()].map((c) => c.y))
  const placed = new Map<string, Rect>()
  for (const b of moving) {
    const c = corners.get(b.id) ?? { x: b.x - x0 + lx, y: b.y - y0 + ly }
    placed.set(b.id, { x: x0 + c.x - lx, y: y0 + c.y - ly, w: b.w, h: b.h })
  }
  settle(placed, boxes.filter((b) => !b.moves))
  const result: Positions = {}
  for (const [id, r] of placed) result[id] = { x: Math.round(r.x), y: Math.round(r.y) }
  return result
}
