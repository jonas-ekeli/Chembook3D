import { join } from 'node:path'
import { pathToFileURL } from 'node:url'
import { expect, test, type Locator, type Page } from '@playwright/test'
import { E2E_DIR, openDemo, investigationCommand, canvasNode } from './helpers.ts'

// T-UI-68 (D193): a sub-investigation is a box with a canvas of its own. Made from a node's menu,
// it is drawn where the node was; "Expand here" shows its inside on this canvas, Open switches
// the canvas to it (breadcrumb, faded stubs for the outside ends, Esc goes back up), and
// Dissolve puts everything back.

const card = canvasNode
const box = (page: Page, label: string): Locator =>
  page.getByRole('group', { name: `Sub-investigation ${label}`, exact: true })

type Canvas = {
  nodes: { id: string; label: string; canvas_id: string | null }[]
  sub_investigations: { id: string; label: string; node_count: number }[]
}

// Later tests start with no investigation open, even when this one fails.
test.afterEach(async ({ request }) => {
  await request.post('/api/investigations/close')
})

test('a sub-investigation: made from a node, expanded here, opened and dissolved', async ({ page }) => {
  await openDemo(page)
  const canvas = async (): Promise<Canvas> => (await page.request.get('/api/canvas')).json()
  // Move into new sub-investigation… from the node's menu: a name and a face.
  await card(page, 'A-S2').click({ button: 'right' })
  await page.getByRole('menuitem', { name: 'Move into new sub-investigation…' }).click()
  const dialog = page.getByRole('dialog', { name: 'Move into new sub-investigation' })
  await dialog.getByLabel('Sub-investigation name').fill('Side path')
  await expect(dialog.getByLabel('Face')).toHaveValue((await canvas()).nodes.find((n) => n.label === 'A-S2')!.id)
  await dialog.getByRole('button', { name: 'Create' }).click()
  await expect(dialog).toBeHidden()

  // The box stands where the node was, and holds it; its edges are drawn to the box.
  const side = box(page, 'Side path')
  await expect(side).toBeVisible()
  await expect(side).toContainText('1 node · 1 TS')
  await expect(card(page, 'A-S2')).toHaveCount(0)
  const inspector = page.getByLabel('Sub-investigation inspector')
  await expect(inspector.getByRole('heading', { name: 'Side path' })).toBeVisible()
  const sub = (await canvas()).sub_investigations.find((s) => s.label === 'Side path')!
  expect((await canvas()).nodes.find((n) => n.label === 'A-S2')!.canvas_id).toBe(sub.id)

  // Expand here: the inside is drawn in the box, on this canvas; Collapse closes it again.
  await side.getByRole('button', { name: 'Expand Side path here' }).click()
  await expect(card(page, 'A-S2')).toBeVisible()
  await expect(card(page, 'A-S1')).toBeVisible()
  await side.getByRole('button', { name: 'Collapse Side path' }).click()
  await expect(card(page, 'A-S2')).toHaveCount(0)

  // Open: the canvas is the box's, with a breadcrumb and faded stubs for the ends outside.
  await side.getByRole('button', { name: 'Open Side path' }).click()
  const crumbs = page.getByRole('navigation', { name: 'Canvas' })
  await expect(crumbs.getByRole('button', { name: 'Side path' })).toHaveAttribute('aria-current', 'location')
  await expect(card(page, 'A-S2')).toBeVisible()
  await expect(card(page, 'T-S0')).toHaveCount(0)
  await expect(page.getByRole('group', { name: 'Outside: A-S1' })).toBeVisible()

  // A stub opens the canvas its node is on and selects it.
  await page.getByRole('group', { name: 'Outside: A-S1' }).click()
  await expect(crumbs).toHaveCount(0)
  await expect(page.getByLabel('Node inspector').getByRole('heading', { name: 'A-S1' })).toBeVisible()

  // The open canvas is remembered; Esc with nothing selected goes back up.
  await box(page, 'Side path').dblclick()
  await expect(crumbs).toBeVisible()
  await expect.poll(async () => (await (await page.request.get('/api/view-state')).json()).canvas).toBe(sub.id)
  await page.reload()
  await expect(crumbs.getByRole('button', { name: 'Side path' })).toBeVisible()
  await page.locator('.react-flow__pane').click({ position: { x: 10, y: 10 } })
  await page.keyboard.press('Escape')
  await expect(crumbs).toHaveCount(0)
  await expect(card(page, 'T-S0')).toBeVisible()

  // Remove…: Dissolve puts the node back where the box was.
  await box(page, 'Side path').click()
  await inspector.getByRole('button', { name: 'Remove…' }).click()
  const remove = page.getByRole('dialog', { name: 'Remove sub-investigation?' })
  await expect(remove.getByRole('radio', { name: /Dissolve/ })).toBeChecked()
  await remove.getByRole('button', { name: 'Dissolve' }).click()
  await expect(box(page, 'Side path')).toHaveCount(0)
  await expect(card(page, 'A-S2')).toBeVisible()
  expect((await canvas()).nodes.find((n) => n.label === 'A-S2')!.canvas_id).toBeNull()
})

// T-UI-69 (D193): everything else names where a node is and opens its canvas: the overview's
// open items, the outline (its Sub-investigations section and the node search), Ctrl K, and the
// box's own card counts what is open inside.
test('the overview, outline and Ctrl K find things inside a sub-investigation', async ({ page }) => {
  await openDemo(page)
  const canvas = async (): Promise<Canvas> => (await page.request.get('/api/canvas')).json()
  const id = (await canvas()).nodes.find((n) => n.label === 'A-S2')!.id
  const made = await page.request.post('/api/sub-investigations', { data: { label: 'Side path', ids: [id] } })
  expect(made.ok()).toBe(true)
  await page.reload()
  const crumbs = page.getByRole('navigation', { name: 'Canvas' })
  // The card counts what is open inside (A-S2 is planned).
  await expect(box(page, 'Side path')).toContainText(/\d+ open/)

  // The overview names where an open item is, and opens its canvas.
  const item = page
    .getByLabel('Overview')
    .getByRole('listitem')
    .filter({ has: page.getByRole('button', { name: 'A-S2', exact: true }) })
    .first()
  await expect(item).toContainText('in Side path')
  await item.getByRole('button', { name: 'A-S2', exact: true }).click()
  await expect(crumbs.getByRole('button', { name: 'Side path' })).toBeVisible()
  await expect(page.getByLabel('Node inspector').getByRole('heading', { name: 'A-S2' })).toBeVisible()

  // The outline lists the sub-investigations; Main canvas goes back.
  const outline = page.getByRole('region', { name: 'Sub-investigations' })
  await outline.getByRole('button', { name: 'Main canvas' }).click()
  await expect(crumbs).toHaveCount(0)
  await outline.getByRole('button', { name: /Side path/ }).click()
  await expect(crumbs).toBeVisible()
  await outline.getByRole('button', { name: 'Main canvas' }).click()

  // The node search shows where a node is, and opens it there.
  const row = page.getByRole('list', { name: 'Nodes' }).getByRole('button', { name: /A-S2/ })
  await expect(row).toContainText('in Side path')

  // Ctrl K lists sub-investigations to open.
  await page.locator('.react-flow__pane').click({ position: { x: 10, y: 10 } })
  await page.keyboard.press('Control+k')
  await page.keyboard.type('side')
  const palette = page.getByRole('dialog', { name: 'Command palette' })
  const subs = palette.getByRole('group', { name: 'Sub-investigations' }).getByRole('option')
  await expect(subs.first()).toContainText('Side path')
  await subs.first().click()
  await expect(palette).toBeHidden()
  await expect(crumbs.getByRole('button', { name: 'Side path' })).toBeVisible()

  // The read-only copy opens on the box the app had open, and the reader can leave it.
  await expect.poll(async () => (await (await page.request.get('/api/view-state')).json()).canvas).not.toBeNull()
  await investigationCommand(page, 'Share read-only copy…')
  const share = page.getByRole('dialog', { name: 'Share a read-only copy' })
  const download = page.waitForEvent('download')
  await share.getByRole('button', { name: 'Save file' }).click()
  const path = join(E2E_DIR, 'copy-sub.html')
  await (await download).saveAs(path)
  await page.goto(pathToFileURL(path).href)
  await expect(crumbs.getByRole('button', { name: 'Side path' })).toBeVisible()
  await expect(card(page, 'A-S2')).toBeVisible()
  await crumbs.getByRole('button', { name: 'Main canvas' }).click()
  await expect(box(page, 'Side path')).toBeVisible()
  await box(page, 'Side path').dblclick()
  await expect(card(page, 'A-S2')).toBeVisible()
  await page.goto('/')
})

// T-UI-82 (D211, D212): in an opened sub-investigation each outside end's stub sits level with the
// card it joins (coming in on the left, going out on the right), not stacked at the top, and its
// arrow meets the card on the side facing the stub, whatever sides the transition keeps.
test('stubs of an opened sub-investigation sit level with the cards they join', async ({ page }) => {
  const folder = join(E2E_DIR, `stubs-${Date.now()}`)
  await page.request.post('/api/investigations', { data: { folder, name: 'Stubs' } })
  const node = async (label: string, x: number, y: number) =>
    ((await (await page.request.post('/api/nodes', { data: { label, pos_x: x, pos_y: y } })).json()) as { id: string }).id
  const inner = [await node('top', 0, 0), await node('middle', 0, 500), await node('bottom', 0, 1000)]
  const into = await node('into bottom', -600, 1000)
  const outOf = await node('out of middle', 600, 500)
  await page.request.post('/api/transitions', { data: { source_id: inner[0], target_id: inner[1] } })
  await page.request.post('/api/transitions', { data: { source_id: inner[1], target_id: inner[2] } })
  const coming = ((await (await page.request.post('/api/transitions', { data: { source_id: into, target_id: inner[2] } })).json()) as { id: string }).id
  // Sides kept for the main canvas: bottom to top.
  const kept = await page.request.patch(`/api/transitions/${coming}`, { data: { source_side: 'bottom', target_side: 'top' } })
  expect(kept.ok()).toBe(true)
  await page.request.post('/api/transitions', { data: { source_id: inner[1], target_id: outOf } })
  const made = await page.request.post('/api/sub-investigations', { data: { label: 'Detour', ids: inner } })
  expect(made.ok()).toBe(true)
  await page.goto('/')
  await box(page, 'Detour').getByRole('button', { name: 'Open Detour' }).click()
  await expect(card(page, 'bottom')).toBeVisible()

  // All measured at once, as the view may still be moving after opening.
  const middles = async () =>
    page.evaluate(() =>
      Object.fromEntries(
        [...document.querySelectorAll('.react-flow__node')].map((n) => {
          const r = n.getBoundingClientRect()
          return [n.getAttribute('aria-label'), { x: r.x + r.width / 2, y: r.y + r.height / 2, height: r.height }]
        }),
      ),
    )
  let last = ''
  await expect
    .poll(async () => {
      const now = JSON.stringify(await middles())
      const still = now === last
      last = now
      return still
    })
    .toBe(true)
  const at = JSON.parse(last) as Awaited<ReturnType<typeof middles>>
  const [stubIn, stubOut] = [at['Outside: into bottom'], at['Outside: out of middle']]
  const [bottom, middle] = [at['Node bottom'], at['Node middle']]
  expect(Math.abs(stubIn.y - bottom.y)).toBeLessThan(bottom.height / 3)
  expect(Math.abs(stubOut.y - middle.y)).toBeLessThan(middle.height / 3)
  expect(stubIn.x).toBeLessThan(bottom.x)
  expect(stubOut.x).toBeGreaterThan(middle.x)

  // The arrow from the stub ends on the bottom card's left side, not its top.
  const end = await page.locator(`[data-testid="rf__edge-${coming}"] .react-flow__edge-path`).evaluate((p) => {
    const path = p as SVGPathElement
    const point = path.getPointAtLength(path.getTotalLength())
    const m = path.getScreenCTM()!
    return { x: m.a * point.x + m.c * point.y + m.e, y: m.b * point.x + m.d * point.y + m.f }
  })
  const target = await page.evaluate(() => {
    const r = document.querySelector('[aria-label="Node bottom"]')!.getBoundingClientRect()
    return { left: r.left, top: r.top, bottom: r.bottom }
  })
  expect(Math.abs(end.x - target.left)).toBeLessThan(12)
  expect(end.y).toBeGreaterThan(target.top + 4)
  expect(end.y).toBeLessThan(target.bottom - 4)
})
