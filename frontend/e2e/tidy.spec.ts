import { join } from 'node:path'
import { expect, test, type Page } from '@playwright/test'
import { forgetView } from './demoView.ts'

// T-UI-75 (D199): Tidy lays the canvas out in step columns for every branch, orders each column
// so the arrows do not cross, and leaves no card on another; Undo tidy puts every card back. On
// a selection it moves only the selected cards, clear of the ones that stay.
const E2E_DIR = process.env.E2E_DIR!
const DEMO = join(E2E_DIR, 'demo')

type Canvas = { nodes: { id: string; label: string; pos_x: number; pos_y: number }[] }
type Places = Record<string, { x: number; y: number }>
type Edges = { transitions: { source_id: string; target_id: string; source_side: string; target_side: string }[] }

const card = (page: Page, label: string) => page.getByRole('group', { name: `Node ${label}`, exact: true })

async function places(page: Page): Promise<Record<string, { id: string; x: number; y: number }>> {
  const canvas = (await (await page.request.get('/api/canvas')).json()) as Canvas
  return Object.fromEntries(canvas.nodes.map((n) => [n.label, { id: n.id, x: n.pos_x, y: n.pos_y }]))
}

/** The drawn cards that share any area on screen. */
async function overlapping(page: Page): Promise<string[]> {
  const boxes = await page.locator('[data-testid="canvas-node"]').evaluateAll((cards) =>
    cards.map((c) => {
      const r = c.getBoundingClientRect()
      return { name: c.querySelector('.cnode-label')?.textContent ?? '', x: r.x, y: r.y, w: r.width, h: r.height }
    }),
  )
  const found: string[] = []
  for (let i = 0; i < boxes.length; i++)
    for (let j = i + 1; j < boxes.length; j++) {
      const [a, b] = [boxes[i], boxes[j]]
      if (a.x < b.x + b.w && b.x < a.x + a.w && a.y < b.y + b.h && b.y < a.y + a.h) found.push(`${a.name} / ${b.name}`)
    }
  return found
}

let original: Places = {}

// The demo is shared: it gets its own layout back, and later tests start with it closed.
test.afterEach(async ({ request }) => {
  if (Object.keys(original).length) {
    await request.post('/api/investigations/open', { data: { folder: DEMO } })
    await request.put('/api/positions', { data: { positions: original } })
  }
  await request.post('/api/investigations/close')
})

test('Tidy lays the canvas out in step columns; Undo tidy and a selection', async ({ page }) => {
  await forgetView(page.request, DEMO)
  // Muddle the mechanism first: rotamer B above A in one column and below it in the next (two
  // crossing arrows), and B-S3 half over A-S3.
  await page.request.post('/api/investigations/open', { data: { folder: DEMO } })
  const before = await places(page)
  original = Object.fromEntries(Object.values(before).map((p) => [p.id, { x: p.x, y: p.y }]))
  const muddle: Record<string, { x: number; y: number }> = {
    'A-S1': { x: 240, y: 440 },
    'B-S1': { x: 240, y: 220 },
    'A-S2': { x: 500, y: 220 },
    'B-S2': { x: 480, y: 480 },
    'A-S3': { x: 720, y: 300 },
    'B-S3': { x: 820, y: 330 },
  }
  await page.request.put('/api/positions', {
    data: { positions: Object.fromEntries(Object.entries(muddle).map(([label, p]) => [before[label].id, p])) },
  })
  const muddled = await places(page)
  await page.request.post('/api/investigations/close')

  await page.goto('/')
  await page.getByRole('button', { name: /^Open/ }).first().click()
  const dialog = page.getByRole('dialog')
  await dialog.getByLabel('Folder path').fill(DEMO)
  await dialog.getByRole('button', { name: 'Go' }).click()
  await dialog.getByRole('button', { name: 'Open', exact: true }).click()
  await expect(page.getByRole('dialog')).toHaveCount(0)
  await expect(card(page, 'T-S0')).toBeVisible()
  expect(await overlapping(page)).toContain('A-S3 / B-S3')

  // Tidy with nothing selected: the whole canvas.
  const toolbar = page.locator('.canvasbar')
  await expect(toolbar.getByRole('button', { name: 'Undo tidy' })).toHaveCount(0)
  await toolbar.getByRole('button', { name: 'Tidy ▾' }).click()
  await page.getByRole('menuitem', { name: 'In step columns' }).click()
  await expect.poll(async () => (await places(page))['B-S3'].x).not.toBe(muddled['B-S3'].x)
  const tidied = await places(page)
  // One column per step, in step order, for both branches.
  const x = (label: string) => tidied[label].x
  expect(x('A-S1')).toBe(x('B-S1'))
  expect(x('A-S2')).toBe(x('B-S2'))
  expect(x('A-S3')).toBe(x('B-S3'))
  expect(x('T-S0')).toBeLessThan(x('A-S1'))
  expect(x('A-S1')).toBeLessThan(x('A-S2'))
  expect(x('A-S2')).toBeLessThan(x('A-S3'))
  // A above B in every column, or B above A in every one: no arrows cross.
  const above = ['S1', 'S2', 'S3'].map((s) => tidied[`A-${s}`].y < tidied[`B-${s}`].y)
  expect(new Set(above).size).toBe(1)
  // Nothing on anything, on screen.
  await expect.poll(() => overlapping(page)).toEqual([])

  // Undo tidy: every card back where it was, and the button gone.
  await toolbar.getByRole('button', { name: 'Undo tidy' }).click()
  await expect.poll(async () => (await places(page))['B-S3']).toEqual(muddled['B-S3'])
  expect(await places(page)).toEqual(muddled)
  await expect(toolbar.getByRole('button', { name: 'Undo tidy' })).toHaveCount(0)

  // Tidy selection from the right-click menu: only the selected cards move, into step order,
  // and clear of the cards that stay.
  // Each clicked where the other does not cover it: A-S3's top-left corner, B-S3's bottom-right.
  await card(page, 'A-S3').click({ position: { x: 6, y: 6 } })
  const corner = async () => {
    const { width, height } = (await card(page, 'B-S3').boundingBox())!
    return { x: width - 6, y: height - 6 }
  }
  await card(page, 'B-S3').click({ modifiers: ['Control'], position: await corner() })
  await card(page, 'B-S3').click({ button: 'right', position: await corner() })
  await page.getByRole('menu', { name: 'Selection actions' }).getByRole('menuitem', { name: 'Tidy selection' }).click()
  await page.getByRole('menu', { name: 'Tidy selection' }).getByRole('menuitem', { name: 'In step columns' }).click()
  await expect.poll(async () => (await places(page))['B-S3']).not.toEqual(muddled['B-S3'])
  const selected = await places(page)
  for (const label of Object.keys(muddled))
    if (label !== 'A-S3' && label !== 'B-S3') expect(selected[label], label).toEqual(muddled[label])
  expect(selected['A-S3'].x).toBe(selected['B-S3'].x)
  // Earlier tests leave cards of their own on the shared demo, which may lie on each other and
  // stay put: only the two tidied cards must be clear.
  const tidiedCards = async () =>
    (await overlapping(page)).filter((pair) => pair.split(' / ').some((name) => name === 'A-S3' || name === 'B-S3'))
  await expect.poll(tidiedCards).toEqual([])

  // Moving a card by hand ends what Undo tidy would put back.
  await expect(toolbar.getByRole('button', { name: 'Undo tidy' })).toBeVisible()
  const t0 = (await card(page, 'T-S0').boundingBox())!
  await page.mouse.move(t0.x + 30, t0.y + 10)
  await page.mouse.down()
  await page.mouse.move(t0.x + 30, t0.y - 60, { steps: 5 })
  await page.mouse.up()
  await expect(toolbar.getByRole('button', { name: 'Undo tidy' })).toHaveCount(0)
})

// T-UI-79 (D207): Tidy with springs (ELK's stress layout) draws a catalytic cycle as a loop: every
// card about as far from the middle as the others, every arrow about as long, nothing on anything;
// each arrow leaves and arrives on the sides that face each other; Undo tidy puts every card and
// every side back.
test('Tidy with springs draws a cycle as a loop', async ({ page }) => {
  original = {}
  const folder = join(E2E_DIR, `springs-${Date.now()}`)
  await page.request.post('/api/investigations', { data: { folder, name: 'Springs' } })
  const labels = ['14e', 'π', 'TS1', 'MCB', 'TS2', 'product π']
  const ids: string[] = []
  // All on one row, out of order: the cycle tangled up on itself.
  for (const [i, label] of labels.entries()) {
    const made = await page.request.post('/api/nodes', { data: { label, pos_x: [0, 600, 240, 960, 480, 720][i], pos_y: 0 } })
    ids.push(((await made.json()) as { id: string }).id)
  }
  for (let i = 0; i < ids.length; i++)
    await page.request.post('/api/transitions', { data: { source_id: ids[i], target_id: ids[(i + 1) % ids.length] } })
  await page.request.post('/api/nodes', { data: { label: 'scratch', pos_x: 240, pos_y: 10 } })
  await page.goto('/')
  await expect(card(page, '14e')).toBeVisible()
  const before = await places(page)

  const toolbar = page.locator('.canvasbar')
  await toolbar.getByRole('button', { name: 'Tidy ▾' }).click()
  await page.getByRole('menuitem', { name: 'With springs', exact: true }).click()
  await expect(toolbar.getByRole('button', { name: 'Undo tidy' })).toBeVisible()
  await expect.poll(async () => (await places(page))['MCB']).not.toEqual(before['MCB'])
  const after = await places(page)
  const centre = (label: string) => ({ x: after[label].x + 94, y: after[label].y + 39 })
  const mid = labels.reduce((m, l) => ({ x: m.x + centre(l).x / 6, y: m.y + centre(l).y / 6 }), { x: 0, y: 0 })
  const radii = labels.map((l) => Math.hypot(centre(l).x - mid.x, centre(l).y - mid.y))
  expect(Math.max(...radii) / Math.min(...radii)).toBeLessThan(1.5)
  const arrows = labels.map((l, i) => {
    const [a, b] = [centre(l), centre(labels[(i + 1) % 6])]
    return Math.hypot(a.x - b.x, a.y - b.y)
  })
  expect(Math.max(...arrows) / Math.min(...arrows)).toBeLessThan(1.5)
  await expect.poll(() => overlapping(page)).toEqual([])
  // Each arrow on the sides facing the other card: across when the cards are further apart
  // across than up and down.
  const facing = (from: string, to: string) => {
    const [a, b] = [centre(from), centre(to)]
    const across = Math.abs(b.x - a.x) - 188
    const upDown = Math.abs(b.y - a.y) - 78
    if (across >= upDown) return b.x >= a.x ? ['right', 'left'] : ['left', 'right']
    return b.y >= a.y ? ['bottom', 'top'] : ['top', 'bottom']
  }
  const label = new Map(Object.entries(after).map(([l, p]) => [p.id, l]))
  const sides = async () =>
    ((await (await page.request.get('/api/canvas')).json()) as Edges).transitions.map((t) => ({
      from: label.get(t.source_id)!,
      to: label.get(t.target_id)!,
      sides: [t.source_side, t.target_side],
    }))
  await expect.poll(async () => (await sides()).every((t) => t.sides.join() === facing(t.from, t.to).join())).toBe(true)
  expect(new Set((await sides()).map((t) => t.sides.join())).size).toBeGreaterThan(1)

  await toolbar.getByRole('button', { name: 'Undo tidy' }).click()
  await expect.poll(async () => (await places(page))['MCB']).toEqual(before['MCB'])
  expect(await places(page)).toEqual(before)
  await expect.poll(async () => (await sides()).every((t) => t.sides.join() === 'right,left')).toBe(true)

  // D209: a longer arrow length in the settings spreads the loop out.
  await page.getByRole('button', { name: 'Settings' }).click()
  const settings = page.getByRole('dialog', { name: 'Settings' })
  await settings.getByRole('navigation', { name: 'Settings sections' }).getByRole('button', { name: 'Canvas' }).click()
  const room = settings.getByLabel('Arrow length for Tidy with springs (px)')
  await expect(room).toHaveValue('220')
  await room.fill('600')
  await room.blur()
  await expect.poll(async () => ((await (await page.request.get('/api/settings')).json()) as { tidy_arrow_room: number }).tidy_arrow_room).toBe(600)
  await page.keyboard.press('Escape')
  await expect(settings).toHaveCount(0)
  try {
    await toolbar.getByRole('button', { name: 'Tidy ▾' }).click()
    await page.getByRole('menuitem', { name: 'With springs', exact: true }).click()
    await expect.poll(async () => (await places(page))['MCB']).not.toEqual(before['MCB'])
    const longer = await places(page)
    const length = (p: typeof longer) =>
      labels.reduce((sum, l, i) => {
        const [a, b] = [p[l], p[labels[(i + 1) % 6]]]
        return sum + Math.hypot(a.x - b.x, a.y - b.y)
      }, 0)
    expect(length(longer) / length(after)).toBeGreaterThan(1.5)
  } finally {
    await page.request.put('/api/settings', { data: { tidy_arrow_room: 220 } })
  }
})

// T-UI-80 (D208): Tidy with springs, in place, starts from where the cards are: a cycle drawn
// round with one card dragged far out settles into an even loop in the same order round its
// middle, about where it was, with nothing on anything.
test('Tidy with springs in place keeps the cards near their places', async ({ page }) => {
  original = {}
  const folder = join(E2E_DIR, `settle-${Date.now()}`)
  await page.request.post('/api/investigations', { data: { folder, name: 'Settle' } })
  const labels = ['14e', 'π', 'TS1', 'MCB', 'TS2', 'product π']
  const ids: string[] = []
  // Round a circle, MCB pulled far out.
  const at = (i: number) => {
    const r = i === 3 ? 1400 : 500
    return { x: 2000 + r * Math.cos((i * Math.PI) / 3), y: 1500 + r * Math.sin((i * Math.PI) / 3) }
  }
  for (const [i, label] of labels.entries()) {
    const { x, y } = at(i)
    const made = await page.request.post('/api/nodes', { data: { label, pos_x: Math.round(x), pos_y: Math.round(y) } })
    ids.push(((await made.json()) as { id: string }).id)
  }
  for (let i = 0; i < ids.length; i++)
    await page.request.post('/api/transitions', { data: { source_id: ids[i], target_id: ids[(i + 1) % ids.length] } })
  await page.goto('/')
  await expect(card(page, '14e')).toBeVisible()
  const before = await places(page)

  const toolbar = page.locator('.canvasbar')
  await toolbar.getByRole('button', { name: 'Tidy ▾' }).click()
  await page.getByRole('menuitem', { name: 'With springs, in place' }).click()
  await expect(toolbar.getByRole('button', { name: 'Undo tidy' })).toBeVisible()
  await expect.poll(async () => (await places(page))['MCB']).not.toEqual(before['MCB'])
  const after = await places(page)
  const centre = (p: Record<string, { x: number; y: number }>, label: string) => ({ x: p[label].x + 94, y: p[label].y + 39 })
  const middle = (p: Record<string, { x: number; y: number }>) =>
    labels.reduce((m, l) => ({ x: m.x + centre(p, l).x / 6, y: m.y + centre(p, l).y / 6 }), { x: 0, y: 0 })
  // The middle stays put, and the cards keep their order round it.
  const [m0, m1] = [middle(before), middle(after)]
  expect(Math.hypot(m1.x - m0.x, m1.y - m0.y)).toBeLessThan(150)
  const turn = (p: Record<string, { x: number; y: number }>, m: { x: number; y: number }) => {
    const angles = labels.map((l) => Math.atan2(centre(p, l).y - m.y, centre(p, l).x - m.x))
    const first = angles.indexOf(Math.min(...angles))
    return [...labels.keys()].sort((a, b) => angles[a] - angles[b]).map((i) => (i - first + 6) % 6)
  }
  expect(turn(after, m1)).toEqual(turn(before, m0))
  const arrows = labels.map((l, i) => {
    const [a, b] = [centre(after, l), centre(after, labels[(i + 1) % 6])]
    return Math.hypot(a.x - b.x, a.y - b.y)
  })
  expect(Math.max(...arrows) / Math.min(...arrows)).toBeLessThan(1.5)
  await expect.poll(() => overlapping(page)).toEqual([])
})
