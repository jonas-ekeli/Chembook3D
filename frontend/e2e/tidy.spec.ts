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
  await toolbar.getByRole('button', { name: 'Tidy', exact: true }).click()
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
