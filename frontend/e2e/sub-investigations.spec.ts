import { join } from 'node:path'
import { expect, test, type Locator, type Page } from '@playwright/test'
import { forgetView } from './demoView.ts'

// T-UI-68 (D193): a sub-investigation is a box with a canvas of its own. Made from a node's menu,
// it is drawn where the node was; "Expand here" shows its inside on this canvas, Open switches
// the canvas to it (breadcrumb, faded stubs for the outside ends, Esc goes back up), and
// Dissolve puts everything back.
const E2E_DIR = process.env.E2E_DIR!

async function openDemo(page: Page) {
  await forgetView(page.request, join(E2E_DIR, 'demo'))
  await page.goto('/')
  await page.getByRole('button', { name: /^Open/ }).first().click()
  const dialog = page.getByRole('dialog')
  await expect(dialog.getByRole('list', { name: 'Folders' })).toBeVisible()
  await dialog.getByLabel('Folder path').fill(join(E2E_DIR, 'demo'))
  await dialog.getByRole('button', { name: 'Go' }).click()
  await dialog.getByRole('button', { name: 'Open', exact: true }).click()
  await expect(page.getByRole('dialog')).toHaveCount(0)
  await expect(card(page, 'T-S0')).toBeVisible()
}

const card = (page: Page, label: string): Locator => page.getByRole('group', { name: `Node ${label}`, exact: true })
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
  try {
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
  } finally {
    // Leave the shared demo as it was.
    for (const left of (await canvas()).sub_investigations)
      await page.request.post(`/api/sub-investigations/${left.id}/dissolve`)
  }
})
