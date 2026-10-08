import { join } from 'node:path'
import { expect, test, type Locator, type Page } from '@playwright/test'
import { forgetView } from './demoView.ts'

// T-UI-10, FR-CAN-08, D105: the demo opens on what it showed when it was last used.
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
}

function canvasNode(page: Page, label: string): Locator {
  return page.getByRole('group', { name: `Node ${label}`, exact: true })
}

// Later tests start from the demo's defaults, with no investigation open, even when this fails.
test.afterEach(async ({ request }) => {
  await request.put('/api/view-state', { data: {} })
  await request.post('/api/investigations/close')
})

test('the energy view, filters, groups and drawer are back after a reload and after opening again', async ({ page }) => {
  await forgetView(page.request, join(E2E_DIR, 'demo'))
  await openDemo(page)
  const canvas = await (await page.request.get('/api/canvas')).json()
  const idOf = (label: string) => canvas.nodes.find((n: { label: string }) => n.label === label).id
  const made = await page.request.post('/api/groups/reconnect', {
    data: { member_ids: [idOf('A-S2'), idOf('B-S2')], label: 'GV' },
  })
  expect(made.ok()).toBe(true)
  const group = (await made.json()) as { id: string }
  const historyLength = async () => ((await (await page.request.get('/api/history')).json()) as unknown[]).length
  try {
    await page.reload()
    await expect(canvasNode(page, 'T-S0')).toBeVisible()
    const before = await historyLength()
    const view = page.getByRole('group', { name: 'Energy view' })
    await view.getByLabel('Energy type').selectOption('G_qh')
    await view.getByLabel('Energies on edges').uncheck()
    await page.getByRole('button', { name: 'Energy', exact: true }).click()
    await canvasNode(page, 'T-S0').click()
    await page.getByLabel('Node inspector').getByRole('button', { name: 'Use as energy reference' }).click()
    await page.getByRole('button', { name: /^Filters/ }).click()
    await page.getByRole('dialog', { name: 'Filters' }).getByLabel('Failed').uncheck()
    await page.getByRole('dialog', { name: 'Filters' }).getByRole('button', { name: 'Close' }).click()
    await page.getByRole('group', { name: 'Group GV' }).getByRole('button', { name: 'Expand group' }).click()
    await expect(canvasNode(page, 'A-S2')).toBeVisible()
    await page.getByRole('button', { name: 'Profile and table' }).click()
    const drawer = page.getByLabel('Energy drawer')
    await drawer.getByLabel('Add branch pathway').selectOption({ label: 'B' })
    await expect(drawer.getByRole('group', { name: 'Pathway B' })).toContainText('T-S0 → B-S1')
    await drawer.getByRole('button', { name: 'Energy table' }).click()
    await expect(drawer.getByRole('table', { name: 'Energy table' })).toBeVisible()
    const shown = async () => {
      await expect(view.getByLabel('Energy type')).toHaveValue('G_qh')
      await expect(view.getByLabel('Energies on edges')).not.toBeChecked()
      await expect(canvasNode(page, 'A-S1')).toContainText('ΔG_qh')
      await expect(page.getByRole('button', { name: 'Filters (1 hidden) ▾' })).toBeVisible()
      await expect(page.getByRole('group', { name: 'Group GV' }).getByRole('button', { name: 'Collapse group' })).toBeVisible()
      await expect(canvasNode(page, 'A-S2')).toBeVisible()
      await expect(drawer.getByRole('group', { name: 'Pathway B' })).toContainText('T-S0 → B-S1')
      await expect(drawer.getByRole('table', { name: 'Energy table' })).toBeVisible()
      await expect(drawer.getByLabel('Reference node')).toHaveValue(idOf('T-S0'))
    }
    // Saved as each changed: a reload shows it all again.
    await expect.poll(async () => (await (await page.request.get('/api/view-state')).json()).drawer.tab).toBe('table')
    await page.reload()
    await shown()
    // So does closing the investigation and opening it again; none of it is in the history.
    await page.request.post('/api/investigations/close')
    await openDemo(page)
    await shown()
    expect(await historyLength()).toBe(before)
  } finally {
    await page.request.post(`/api/groups/${group.id}/dissolve`, { data: { restore_branches: true } })
  }
})
