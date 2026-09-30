import { join } from 'node:path'
import { expect, test, type Locator, type Page } from '@playwright/test'

const E2E_DIR = process.env.E2E_DIR!

async function openDemo(page: Page) {
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
