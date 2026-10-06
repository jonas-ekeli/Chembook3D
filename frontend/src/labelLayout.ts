/**
 * Where each edge's label is drawn on the canvas (D98): on its own line where that is free,
 * else slid along the line, else beside it with a short leader, so labels neither cover one
 * another nor hide behind node cards. Everything is in flow coordinates.
 */

export type Point = { x: number; y: number }
export type Rect = { x: number; y: number; w: number; h: number }
/** A cubic Bézier: start, two control points, end. */
export type Curve = [Point, Point, Point, Point]

export type LabelRequest = { id: string; curve: Curve; w: number; h: number }
/** The label's centre and size, and the point on its line it belongs to. */
export type Placement = { x: number; y: number; w: number; h: number; anchor: Point }

/** Space kept around node cards and between labels. */
const CARD_GAP = 4
const LABEL_GAP = 2
/** How far along the line a label may slide either way, as a fraction of the line. */
const SLIDE = 0.4
const SLIDE_STEPS = 8
/** How far beside its line a label may go, in steps of its own size. */
const AWAY_X = 4
const AWAY_Y = 6
/** Moving off the line costs more than sliding along it. */
const OFF_LINE_COST = 1.5
/** A label whose leader would start under a card is hard to trace back to its line. */
const HIDDEN_ANCHOR_COST = 300
/** Any covered area outweighs any distance. */
const OVERLAP_COST = 1000

export function pointAt([p0, p1, p2, p3]: Curve, t: number): Point {
  const u = 1 - t
  const a = u * u * u
  const b = 3 * u * u * t
  const c = 3 * u * t * t
  const d = t * t * t
  return { x: a * p0.x + b * p1.x + c * p2.x + d * p3.x, y: a * p0.y + b * p1.y + c * p2.y + d * p3.y }
}

/** The curve of an SVG path of the form "M x,y C x,y x,y x,y" (what React Flow's getBezierPath returns). */
export function curveOf(path: string): Curve | null {
  const numbers = path.match(/-?\d*\.?\d+(?:e[-+]?\d+)?/gi)?.map(Number)
  if (!numbers || numbers.length !== 8 || numbers.some((n) => !Number.isFinite(n))) return null
  return [
    { x: numbers[0], y: numbers[1] },
    { x: numbers[2], y: numbers[3] },
    { x: numbers[4], y: numbers[5] },
    { x: numbers[6], y: numbers[7] },
  ]
}

function overlap(a: Rect, b: Rect, gap: number): number {
  const w = Math.min(a.x + a.w, b.x + b.w) + gap - Math.max(a.x, b.x)
  const h = Math.min(a.y + a.h, b.y + b.h) + gap - Math.max(a.y, b.y)
  return w > 0 && h > 0 ? w * h : 0
}

function length(curve: Curve): number {
  let total = 0
  let last = curve[0]
  for (let i = 1; i <= 8; i++) {
    const p = pointAt(curve, i / 8)
    total += Math.hypot(p.x - last.x, p.y - last.y)
    last = p
  }
  return total
}

type Candidate = { x: number; y: number; anchor: Point; cost: number }

function candidates(curve: Curve, w: number, h: number, hidden: (p: Point) => boolean): Candidate[] {
  const middle = pointAt(curve, 0.5)
  const stepX = Math.max(24, w / 2 + LABEL_GAP)
  const stepY = h + LABEL_GAP
  const out: Candidate[] = []
  for (let k = 0; k <= SLIDE_STEPS; k++) {
    for (const sign of k ? [-1, 1] : [1]) {
      const anchor = pointAt(curve, 0.5 + (sign * k * SLIDE) / SLIDE_STEPS)
      const slid = Math.hypot(anchor.x - middle.x, anchor.y - middle.y)
      const anchorCost = hidden(anchor) ? HIDDEN_ANCHOR_COST : 0
      for (let i = -AWAY_X; i <= AWAY_X; i++) {
        for (let j = -AWAY_Y; j <= AWAY_Y; j++) {
          const dx = i * stepX
          const dy = j * stepY
          const away = Math.hypot(dx, dy)
          out.push({ x: anchor.x + dx, y: anchor.y + dy, anchor, cost: slid + OFF_LINE_COST * away + (away ? anchorCost : 0) })
        }
      }
    }
  }
  return out.sort((a, b) => a.cost - b.cost)
}

/**
 * Places the labels one at a time, shortest line first (it has the least room to move): each
 * takes the free spot nearest the middle of its line, or, with none free, the one covering
 * the least. `obstacles` are the node cards.
 */
export function placeLabels(labels: LabelRequest[], obstacles: Rect[]): Map<string, Placement> {
  const order = labels
    .map((label) => ({ label, length: length(label.curve) }))
    .sort((a, b) => a.length - b.length || (a.label.id < b.label.id ? -1 : a.label.id > b.label.id ? 1 : 0))
  const placed: Rect[] = []
  const result = new Map<string, Placement>()
  for (const { label } of order) {
    const { curve, w, h } = label
    // Only cards and labels within reach of this label's spots can matter.
    const xs = curve.map((p) => p.x)
    const ys = curve.map((p) => p.y)
    const reachX = (AWAY_X + 1) * Math.max(24, w / 2 + LABEL_GAP) + w
    const reachY = (AWAY_Y + 1) * (h + LABEL_GAP) + h
    const area: Rect = {
      x: Math.min(...xs) - reachX,
      y: Math.min(...ys) - reachY,
      w: Math.max(...xs) - Math.min(...xs) + 2 * reachX,
      h: Math.max(...ys) - Math.min(...ys) + 2 * reachY,
    }
    const cards = obstacles.filter((r) => overlap(r, area, 0) > 0)
    const near = placed.filter((r) => overlap(r, area, 0) > 0)
    const covered = (c: Point) => {
      const box = { x: c.x - w / 2, y: c.y - h / 2, w, h }
      let sum = 0
      for (const r of cards) sum += overlap(box, r, CARD_GAP)
      for (const r of near) sum += overlap(box, r, LABEL_GAP)
      return sum
    }
    const middle = pointAt(curve, 0.5)
    let best: Candidate = { ...middle, anchor: middle, cost: Infinity }
    if (covered(middle) === 0) {
      best.cost = 0
    } else {
      const hidden = (p: Point) => cards.some((r) => p.x > r.x && p.x < r.x + r.w && p.y > r.y && p.y < r.y + r.h)
      for (const c of candidates(curve, w, h, hidden)) {
        if (c.cost >= best.cost) break
        const cost = c.cost + OVERLAP_COST * covered(c)
        if (cost < best.cost) best = { ...c, cost }
      }
    }
    placed.push({ x: best.x - w / 2, y: best.y - h / 2, w, h })
    result.set(label.id, { x: best.x, y: best.y, w, h, anchor: best.anchor })
  }
  return result
}

/**
 * Where a leader from `anchor` meets the edge of the label box centred at `centre`, or null
 * when the anchor is inside the box (the label sits on its line and needs none).
 */
export function leaderEnd(anchor: Point, centre: Point, w: number, h: number): Point | null {
  const dx = anchor.x - centre.x
  const dy = anchor.y - centre.y
  if (Math.abs(dx) <= w / 2 && Math.abs(dy) <= h / 2) return null
  const scale = Math.min(dx ? w / 2 / Math.abs(dx) : Infinity, dy ? h / 2 / Math.abs(dy) : Infinity)
  return { x: centre.x + dx * scale, y: centre.y + dy * scale }
}

/**
 * Collects every drawn edge's label and hands out their placements. Edges register their
 * line and measured label size as they render; one placement pass runs per animation frame
 * after anything changed, and each edge re-renders only when its own placement moves.
 */
export class LabelPlacer {
  private requests = new Map<string, LabelRequest>()
  private placements = new Map<string, Placement>()
  private listeners = new Set<() => void>()
  private frame: number | null = null
  private obstacleKey = ''
  private obstacles: () => Rect[]

  constructor(obstacles: () => Rect[]) {
    this.obstacles = obstacles
  }

  subscribe = (listener: () => void) => {
    this.listeners.add(listener)
    return () => {
      this.listeners.delete(listener)
    }
  }

  get = (id: string): Placement | undefined => this.placements.get(id)

  set(request: LabelRequest) {
    const old = this.requests.get(request.id)
    if (old && sameRequest(old, request)) return
    this.requests.set(request.id, request)
    this.schedule()
  }

  remove(id: string) {
    if (this.requests.delete(id)) this.schedule()
  }

  /** The cards may have moved (a drag, a new measurement): place again if they did. */
  cardsChanged() {
    const key = JSON.stringify(this.obstacles())
    if (key !== this.obstacleKey) this.schedule()
  }

  dispose() {
    if (this.frame !== null) cancelAnimationFrame(this.frame)
    this.frame = null
  }

  private schedule() {
    if (this.frame !== null) return
    this.frame = requestAnimationFrame(() => {
      this.frame = null
      this.run()
    })
  }

  private run() {
    const obstacles = this.obstacles()
    this.obstacleKey = JSON.stringify(obstacles)
    const next = placeLabels([...this.requests.values()], obstacles)
    let changed = next.size !== this.placements.size
    for (const [id, p] of next) {
      const old = this.placements.get(id)
      if (old && Math.abs(old.x - p.x) < 0.5 && Math.abs(old.y - p.y) < 0.5 && old.w === p.w && old.h === p.h)
        next.set(id, old)
      else changed = true
    }
    this.placements = next
    if (changed) for (const listener of this.listeners) listener()
  }
}

function sameRequest(a: LabelRequest, b: LabelRequest): boolean {
  if (a.w !== b.w || a.h !== b.h) return false
  return a.curve.every((p, i) => Math.abs(p.x - b.curve[i].x) < 0.01 && Math.abs(p.y - b.curve[i].y) < 0.01)
}
