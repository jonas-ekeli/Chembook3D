import { join } from 'node:path'
import { expect, test, type Locator, type Page } from '@playwright/test'
import { forgetView } from './demoView.ts'

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
  await expect(canvasNode(page, 'T-S0')).toBeVisible()
  await expect(page.locator('.edge-label').filter({ hasText: 'ΔG' }).first()).toBeVisible()
}

function canvasNode(page: Page, label: string): Locator {
  return page.getByRole('group', { name: `Node ${label}`, exact: true })
}

/** The atoms drawn on a card, as their positions in the sketch. */
async function atomPositions(card: Locator): Promise<string[]> {
  const circles = card.getByRole('img', { name: 'Structure' }).locator('circle')
  return circles.evaluateAll((all) => all.map((c) => `${c.getAttribute('cx')},${c.getAttribute('cy')}`).sort())
}

async function savedRotation(page: Page, label: string): Promise<number[] | null> {
  const canvas = await (await page.request.get('/api/canvas')).json()
  return canvas.nodes.find((n: { label: string }) => n.label === label).view_rotation
}

async function setHydrogens(page: Page, choice: string) {
  await page.getByRole('button', { name: 'Settings' }).click()
  const dialog = page.getByRole('dialog', { name: 'Settings' })
  await dialog.getByLabel('Hydrogens').selectOption({ label: choice })
  await expect(dialog.getByLabel('Hydrogens')).toHaveValue(
    { 'Show all': 'all', 'Hide those bonded to carbon': 'polar', 'Hide all': 'none' }[choice]!,
  )
  await dialog.getByRole('button', { name: 'Done' }).click()
}

test('save the 3D orientation for the structure card, keep it after a reload, then reset it', async ({ page }) => {
  await openDemo(page)
  await page.getByRole('button', { name: 'Structure' }).click()
  const card = canvasNode(page, 'T-S0')
  await expect(card.getByRole('img', { name: 'Structure' })).toBeVisible()
  const before = await atomPositions(card)

  await card.click()
  const inspector = page.getByLabel('Node inspector')
  await expect(inspector.getByRole('heading', { name: 'T-S0' })).toBeVisible()
  const orientation = inspector.getByLabel('Card orientation')
  await expect(orientation).toContainText('The card uses the default orientation.')
  await expect(orientation.getByRole('button', { name: 'Reset to default' })).toHaveCount(0)

  // Turn the molecule with the mouse, then save that orientation for the card.
  const viewer = inspector.getByTestId('viewer3d').locator('canvas')
  await expect(viewer).toBeVisible()
  const box = (await viewer.boundingBox())!
  await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2)
  await page.mouse.down()
  await page.mouse.move(box.x + box.width / 2 + 60, box.y + box.height / 2 + 45, { steps: 8 })
  await page.mouse.up()
  await orientation.getByRole('button', { name: 'Save orientation for card' }).click()
  await expect(orientation).toContainText('The card uses the saved orientation.')
  await expect.poll(() => savedRotation(page, 'T-S0')).not.toBeNull()
  await expect.poll(() => atomPositions(card)).not.toEqual(before)
  const saved = await atomPositions(card)

  // Saved in the investigation, so a reload draws the card the same way.
  await page.reload()
  await expect(canvasNode(page, 'T-S0')).toBeVisible()
  await page.getByRole('button', { name: 'Structure' }).click()
  await expect.poll(() => atomPositions(canvasNode(page, 'T-S0'))).toEqual(saved)

  await canvasNode(page, 'T-S0').click()
  await inspector.getByLabel('Card orientation').getByRole('button', { name: 'Reset to default' }).click()
  await expect(inspector.getByLabel('Card orientation')).toContainText('The card uses the default orientation.')
  await expect.poll(() => savedRotation(page, 'T-S0')).toBeNull()
  await expect.poll(() => atomPositions(canvasNode(page, 'T-S0'))).toEqual(before)
})

test('the hydrogens setting hides hydrogens on the structure cards', async ({ page }) => {
  await openDemo(page)
  await page.getByRole('button', { name: 'Structure' }).click()
  // T-S0 is ethylene (C2H4); A-S2 is a transition state drawn as water (H2O).
  const ethylene = canvasNode(page, 'T-S0')
  const water = canvasNode(page, 'A-S2')
  await expect.poll(async () => (await atomPositions(ethylene)).length).toBe(6)
  await expect.poll(async () => (await atomPositions(water)).length).toBe(3)
  try {
    await setHydrogens(page, 'Hide those bonded to carbon')
    await expect.poll(async () => (await atomPositions(ethylene)).length).toBe(2)
    await expect.poll(async () => (await atomPositions(water)).length).toBe(3) // O–H stays
    await setHydrogens(page, 'Hide all')
    await expect.poll(async () => (await atomPositions(water)).length).toBe(1)
    await ethylene.click()
    await expect(page.getByLabel('Node inspector').getByTestId('viewer3d').locator('canvas')).toBeVisible()
    await expect(page.getByLabel('Node inspector').locator('.viewer-block').getByRole('alert')).toHaveCount(0)
  } finally {
    // App settings are shared by all the UI tests.
    await page.request.put('/api/settings', { data: { hydrogens: 'all' } })
  }
})

test('pop the 3D view out into a window, move and resize it, then put it back (D82)', async ({ page }) => {
  await openDemo(page)
  await canvasNode(page, 'T-S0').click()
  const inspector = page.getByLabel('Node inspector')
  await expect(inspector.getByTestId('viewer3d').locator('canvas')).toBeVisible()
  // Marks the 3D canvas, to show the same viewer moves to the window instead of being rebuilt.
  await inspector.getByTestId('viewer3d').locator('canvas').evaluate((c) => c.setAttribute('data-mark', 'kept'))

  await inspector.getByRole('button', { name: 'Pop out the 3D view' }).click()
  const popout = page.getByRole('dialog', { name: '3D view window' })
  await expect(popout).toBeVisible()
  await expect(popout).toContainText('3D view: T-S0')
  await expect(inspector).toContainText('The 3D view is in its own window.')
  await expect(inspector.getByTestId('viewer3d')).toHaveCount(0)
  const canvas = popout.getByTestId('viewer3d').locator('canvas')
  await expect(canvas).toHaveAttribute('data-mark', 'kept')

  // Measuring and the card orientation work there as in the side panel.
  await popout.getByLabel('Atom numbers').fill('1 2')
  await popout.getByRole('button', { name: 'Measure' }).click()
  await expect(popout.getByLabel('Measurement')).toContainText('Distance')
  await expect(popout.getByLabel('Card orientation')).toContainText('The card uses the default orientation.')

  // Resize from the lower right corner: the window and the 3D canvas grow.
  const before = (await popout.boundingBox())!
  const canvasBefore = (await canvas.boundingBox())!
  const grip = (await popout.getByRole('separator', { name: 'Resize' }).boundingBox())!
  await page.mouse.move(grip.x + grip.width / 2, grip.y + grip.height / 2)
  await page.mouse.down()
  await page.mouse.move(grip.x + grip.width / 2 - 120, grip.y + grip.height / 2 + 150, { steps: 6 })
  await page.mouse.up()
  await expect.poll(async () => (await popout.boundingBox())!.height).toBeCloseTo(before.height + 150, 0)
  await expect.poll(async () => (await popout.boundingBox())!.width).toBeCloseTo(before.width - 120, 0)
  await expect.poll(async () => (await canvas.boundingBox())!.height).toBeGreaterThan(canvasBefore.height + 100)

  // Drag it by its title bar (to the right, over the side panel, clear of the canvas nodes).
  const moved = (await popout.boundingBox())!
  const title = popout.getByText('3D view: T-S0')
  const titleBox = (await title.boundingBox())!
  await page.mouse.move(titleBox.x + 10, titleBox.y + titleBox.height / 2)
  await page.mouse.down()
  await page.mouse.move(titleBox.x + 150, titleBox.y + titleBox.height / 2 + 40, { steps: 6 })
  await page.mouse.up()
  await expect.poll(async () => (await popout.boundingBox())!.x).toBeCloseTo(moved.x + 140, 0)
  await expect.poll(async () => (await popout.boundingBox())!.y).toBeCloseTo(moved.y + 40, 0)
  const placed = (await popout.boundingBox())!

  // Selecting another node keeps the window, in the same place, showing that node.
  await canvasNode(page, 'A-S2').click()
  await expect(inspector.getByRole('heading', { name: 'A-S2' })).toBeVisible()
  await expect(popout).toContainText('3D view: A-S2')
  await expect(popout.getByTestId('viewer3d').locator('canvas')).toBeVisible()
  expect(await popout.boundingBox()).toEqual(placed)

  // The button in the view's corner puts it back in the side panel.
  await popout.getByRole('button', { name: 'Put the 3D view back' }).click()
  await expect(popout).toHaveCount(0)
  await expect(inspector.getByTestId('viewer3d').locator('canvas')).toBeVisible()
  await expect(inspector).not.toContainText('The 3D view is in its own window.')

  // Popping out again opens where it was left; the title bar's button also puts it back.
  await inspector.getByRole('button', { name: 'Pop out the 3D view' }).click()
  await expect(popout).toBeVisible()
  expect(await popout.boundingBox()).toEqual(placed)
  await popout.getByRole('button', { name: 'Put back', exact: true }).click()
  await expect(popout).toHaveCount(0)
  await expect(inspector.getByTestId('viewer3d').locator('canvas')).toBeVisible()
})
