/**
 * D199: Tidy lays the cards of the open canvas (or of the selection) out in reaction-step
 * columns, left to right in step order for every branch at once, as "Arrange branch" (D33) does
 * for one. Each column's order is chosen to cut down crossing arrows, the cards are placed level
 * with the cards they are joined to, and none overlap. Cards that are not tidied stay where they
 * are and the tidied ones are moved clear of them. Each box is a card, a group or a
 * sub-investigation at its measured size; what is inside a group or a box keeps its own layout.
 * Nothing here is stored but the positions, which are layout, not history.
 */

/** A card on the open canvas: where it is, its measured size, its reaction step's place in the
 * step order (null: none), and whether this tidy moves it. */
export type TidyBox = { id: string; x: number; y: number; w: number; h: number; step: number | null; moves: boolean }
/** An arrow between two cards. */
export type TidyLink = { source: string; target: string }
export type Positions = Record<string, { x: number; y: number }>

/** Room between columns, for the arrows and their labels (D98), and between cards in a column. */
export const COLUMN_GAP = 96
export const ROW_GAP = 32
/** How far a tidied card keeps from one that stays. */
const CLEAR = 24
/** Rounds of reordering the columns, and of moving the cards level with their neighbours. */
const ORDER_SWEEPS = 8
const LEVEL_SWEEPS = 6
/** The step place of boxes joined to no box with a step: after every step. */
const LAST = Number.MAX_SAFE_INTEGER

/** The new places of the boxes that move, round numbers, the layout's top-left corner where the
 * moving boxes' was. */
export function tidy(boxes: TidyBox[], links: TidyLink[]): Positions {
  const moving = boxes.filter((b) => b.moves)
  if (!moving.length) return {}
  const ids = new Set(moving.map((b) => b.id))
  const inside = links.filter((l) => l.source !== l.target && ids.has(l.source) && ids.has(l.target))

  const columns = columnsOf(moving, inside)
  orderColumns(columns, inside)

  // Each column as wide as its widest box.
  const left: number[] = []
  for (let c = 0; c < columns.length; c++)
    left[c] = c === 0 ? 0 : left[c - 1] + Math.max(...columns[c - 1].map((b) => b.w)) + COLUMN_GAP
  const top = levelRows(columns, inside)

  // Keep the layout's top-left corner where the moving boxes' was.
  const x0 = Math.min(...moving.map((b) => b.x))
  const y0 = Math.min(...moving.map((b) => b.y))
  const yMin = Math.min(...moving.map((b) => top.get(b.id)!))
  const placed = new Map<string, Rect>()
  columns.forEach((column, c) =>
    column.forEach((b) => placed.set(b.id, { x: x0 + left[c], y: y0 + top.get(b.id)! - yMin, w: b.w, h: b.h })),
  )

  clearOf(columns, placed, boxes.filter((b) => !b.moves))

  const result: Positions = {}
  for (const [id, r] of placed) result[id] = { x: Math.round(r.x), y: Math.round(r.y) }
  return result
}

type Rect = { x: number; y: number; w: number; h: number }

/**
 * The step columns. A box with a step is in that step's column; one without follows the box
 * with a step it is reached from, a column of its own after that step's (two boxes on from it,
 * two columns), else comes before the box with a step it leads to; the rest follow the last
 * step. Columns no box is in are left out.
 */
function columnsOf(boxes: TidyBox[], links: TidyLink[]): TidyBox[][] {
  const key = new Map<string, [number, number]>()
  for (const b of boxes) if (b.step !== null) key.set(b.id, [b.step, 0])
  // From a box with a step, forward along the arrows from it and back along those into it.
  const spread = (forward: boolean, from: Iterable<string>, anyHops = false) => {
    const queue = [...from]
    while (queue.length) {
      const id = queue.shift()!
      const [step, hops] = key.get(id)!
      if (!anyHops && (forward ? hops < 0 : hops > 0)) continue
      for (const l of links) {
        const next = forward ? (l.source === id ? l.target : null) : l.target === id ? l.source : null
        if (next === null || key.has(next)) continue
        key.set(next, [step, hops + (forward ? 1 : -1)])
        queue.push(next)
      }
    }
  }
  spread(true, key.keys())
  spread(false, key.keys())
  // Boxes joined to no box with a step: after the last step, from the leftmost on.
  for (const b of [...boxes].sort((p, q) => p.x - q.x || p.y - q.y)) {
    if (key.has(b.id)) continue
    key.set(b.id, [LAST, 0])
    spread(true, [b.id], true)
  }
  const name = ([step, hops]: [number, number]) => `${step}:${hops}`
  const order = [...new Map([...key.values()].map((k) => [name(k), k])).values()]
    .sort((p, q) => p[0] - q[0] || p[1] - q[1])
    .map(name)
  const columns: TidyBox[][] = order.map(() => [])
  for (const b of boxes) columns[order.indexOf(name(key.get(b.id)!))].push(b)
  // To start with, each column in the order the boxes are now, top to bottom.
  for (const column of columns) column.sort((p, q) => p.y + p.h / 2 - (q.y + q.h / 2) || p.x - q.x)
  return columns
}

/** Neighbours of each box through the arrows, either way. */
function neighbours(links: TidyLink[]): Map<string, string[]> {
  const found = new Map<string, string[]>()
  const add = (a: string, b: string) => found.set(a, [...(found.get(a) ?? []), b])
  for (const l of links) {
    add(l.source, l.target)
    add(l.target, l.source)
  }
  return found
}

/** Arrows crossing between neighbouring columns, with each box at its place in its column. */
export function crossings(columns: { id: string }[][], links: TidyLink[]): number {
  const at = new Map<string, { c: number; i: number }>()
  columns.forEach((column, c) => column.forEach((b, i) => at.set(b.id, { c, i })))
  const spans: { c: number; a: number; b: number }[] = []
  for (const l of links) {
    const s = at.get(l.source)
    const t = at.get(l.target)
    if (!s || !t || Math.abs(s.c - t.c) !== 1) continue
    const [from, to] = s.c < t.c ? [s, t] : [t, s]
    spans.push({ c: from.c, a: from.i, b: to.i })
  }
  let count = 0
  for (let i = 0; i < spans.length; i++)
    for (let j = i + 1; j < spans.length; j++) {
      const p = spans[i]
      const q = spans[j]
      if (p.c === q.c && (p.a - q.a) * (p.b - q.b) < 0) count++
    }
  return count
}

/**
 * Orders each column by where its neighbours are (the barycentre heuristic), sweeping left to
 * right and back, and keeps the order with the fewest crossings. A box with no neighbour on the
 * side swept from keeps its place.
 */
function orderColumns(columns: TidyBox[][], links: TidyLink[]) {
  const next = neighbours(links)
  const columnOf = new Map<string, number>()
  columns.forEach((column, c) => column.forEach((b) => columnOf.set(b.id, c)))
  let best = columns.map((column) => [...column])
  let fewest = crossings(columns, links)
  for (let sweep = 0; sweep < ORDER_SWEEPS && fewest > 0; sweep++) {
    const rightward = sweep % 2 === 0
    const place = new Map<string, number>()
    const measure = (c: number) =>
      columns[c].forEach((b, i) => place.set(b.id, i - (columns[c].length - 1) / 2))
    columns.forEach((_, c) => measure(c))
    const range = rightward ? columns.map((_, c) => c) : columns.map((_, c) => columns.length - 1 - c)
    for (const c of range) {
      const centre = new Map<string, number>()
      for (const b of columns[c]) {
        const from = (next.get(b.id) ?? []).filter((n) => {
          const nc = columnOf.get(n)!
          return rightward ? nc < c : nc > c
        })
        centre.set(b.id, from.length ? from.reduce((sum, n) => sum + place.get(n)!, 0) / from.length : place.get(b.id)!)
      }
      // A stable sort: boxes at the same centre keep their order.
      columns[c].sort((p, q) => centre.get(p.id)! - centre.get(q.id)!)
      measure(c)
    }
    const count = crossings(columns, links)
    if (count < fewest) {
      fewest = count
      best = columns.map((column) => [...column])
    }
  }
  best.forEach((column, c) => (columns[c] = column))
}

/**
 * The top of each box: each column in its order with at least ROW_GAP between boxes, each box as
 * close as it can be to level with the boxes it is joined to (their middles), sweeping the columns
 * back and forth. One column is fitted at a time by isotonic regression (pool adjacent violators),
 * which keeps the order and the gaps.
 */
function levelRows(columns: TidyBox[][], links: TidyLink[]): Map<string, number> {
  const next = neighbours(links)
  const columnOf = new Map<string, number>()
  columns.forEach((column, c) => column.forEach((b) => columnOf.set(b.id, c)))
  const top = new Map<string, number>()
  const heights = new Map<string, number>()
  for (const column of columns) {
    let y = 0
    for (const b of column) {
      top.set(b.id, y)
      heights.set(b.id, b.h)
      y += b.h + ROW_GAP
    }
  }
  const middle = (id: string) => top.get(id)! + heights.get(id)! / 2
  for (let sweep = 0; sweep < LEVEL_SWEEPS; sweep++) {
    const rightward = sweep % 2 === 0
    const range = rightward ? columns.map((_, c) => c) : columns.map((_, c) => columns.length - 1 - c)
    for (const c of range) {
      const column = columns[c]
      // The first sweep looks only left, so the first column sets the rows.
      const wanted = column.map((b) => {
        const joined = (next.get(b.id) ?? []).filter((n) => columnOf.get(n) !== c && (sweep > 0 || columnOf.get(n)! < c))
        return joined.length
          ? { y: joined.reduce((sum, n) => sum + middle(n), 0) / joined.length - b.h / 2, weight: 1 }
          : { y: top.get(b.id)!, weight: 0.05 }
      })
      let offset = 0
      const offsets = column.map((b) => {
        const here = offset
        offset += b.h + ROW_GAP
        return here
      })
      const fitted = isotonic(
        wanted.map((w, i) => w.y - offsets[i]),
        wanted.map((w) => w.weight),
      )
      column.forEach((b, i) => top.set(b.id, fitted[i] + offsets[i]))
    }
  }
  return top
}

/** The non-decreasing values closest to `values` in the weighted least-squares sense. */
export function isotonic(values: number[], weights: number[]): number[] {
  const blocks: { value: number; weight: number; count: number }[] = []
  values.forEach((value, i) => {
    blocks.push({ value, weight: weights[i], count: 1 })
    while (blocks.length > 1 && blocks[blocks.length - 2].value > blocks[blocks.length - 1].value) {
      const b = blocks.pop()!
      const a = blocks[blocks.length - 1]
      const weight = a.weight + b.weight
      a.value = (a.value * a.weight + b.value * b.weight) / weight
      a.weight = weight
      a.count += b.count
    }
  })
  return blocks.flatMap((b) => Array<number>(b.count).fill(b.value))
}

const overlaps = (a: Rect, b: Rect, margin: number) =>
  a.x < b.x + b.w + margin && b.x < a.x + a.w + margin && a.y < b.y + b.h + margin && b.y < a.y + a.h + margin

/**
 * Moves tidied boxes clear of the boxes that stay: a box on one is moved down past it, and the
 * boxes below it in its column follow, keeping their gaps.
 */
function clearOf(columns: TidyBox[][], placed: Map<string, Rect>, staying: Rect[]) {
  if (!staying.length) return
  for (const column of columns) {
    let below = -Infinity
    for (const b of column) {
      const r = placed.get(b.id)!
      r.y = Math.max(r.y, below)
      for (let moved = true, rounds = 0; moved && rounds < 1000; rounds++) {
        moved = false
        for (const s of staying)
          if (overlaps(r, s, CLEAR)) {
            r.y = s.y + s.h + CLEAR
            moved = true
          }
      }
      below = r.y + r.h + ROW_GAP
    }
  }
}
