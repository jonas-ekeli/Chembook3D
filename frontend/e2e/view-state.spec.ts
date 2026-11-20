import { expect, test } from '@playwright/test'
import { openDemo, canvasNode } from './helpers.ts'

// T-UI-10, FR-CAN-08, D105: the demo opens on what it showed when it was last used.

test('the energy view, filters, groups and drawer are back after a reload and after opening again', async ({ page }) => {
  await openDemo(page)
  const canvas = await (await page.request.get('/api/canvas')).json()
  const idOf = (label: string) => canvas.nodes.find((n: { label: string }) => n.label === label).id
  const made = await page.request.post('/api/groups/reconnect', {
    data: { member_ids: [idOf('A-S2'), idOf('B-S2')], label: 'GV' },
  })
  expect(made.ok()).toBe(true)
  const historyLength = async () => ((await (await page.request.get('/api/history')).json()) as unknown[]).length
  await page.reload()
  await expect(canvasNode(page, 'T-S0')).toBeVisible()
  const before = await historyLength()
  const view = page.getByRole('group', { name: 'Energy view' })
  await view.getByLabel('Energy type').selectOption('G_qh')
  await view.getByLabel('Show on arrows').uncheck()
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
  await drawer.getByRole('button', { name: 'Table', exact: true }).click()
  await expect(drawer.getByRole('table', { name: 'Energy table' })).toBeVisible()
  const shown = async () => {
    await expect(view.getByLabel('Energy type')).toHaveValue('G_qh')
    await expect(view.getByLabel('Show on arrows')).not.toBeChecked()
    await expect(canvasNode(page, 'A-S1')).toContainText('ΔG_qh')
    await expect(page.getByRole('button', { name: 'Filters (1 hidden)' })).toBeVisible()
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
})

test('what is opened before the saved view arrives is kept', async ({ page }) => {
  // D105: on a slow computer the saved view can arrive after the canvas could be used. Nothing
  // is drawn until it has, so it cannot close the drawer just opened or drop its new pathway.
  await openDemo(page)
  await page.route('**/api/view-state', async (route) => {
    if (route.request().method() === 'GET') await new Promise((done) => setTimeout(done, 1500))
    await route.continue()
  })
  const restored = page.waitForResponse((r) => r.url().includes('/api/view-state') && r.request().method() === 'GET')
  await page.reload()
  await expect(canvasNode(page, 'T-S0')).toBeVisible()
  await page.getByRole('button', { name: 'Profile and table' }).click()
  const drawer = page.getByLabel('Energy drawer')
  await drawer.getByLabel('Add branch pathway').selectOption({ label: 'B' })
  const b = drawer.getByRole('group', { name: 'Pathway B' })
  await expect(b).toContainText('T-S0 → B-S1')
  await restored
  // The view's answer has been handled: the drawer and its pathway are still there.
  await expect(page.getByRole('button', { name: 'Profile and table' })).toHaveAttribute('aria-pressed', 'true')
  await expect(b).toBeVisible()
  await expect(b).toContainText('T-S0 → B-S1')
})

test('a pathway is not shown as lost while the canvas is still on its way', async ({ page }) => {
  // D221: a pathway is "lost" when a node on it is not on the canvas. After a reload the saved
  // profile can arrive before the canvas does, and every pathway then looked lost for a moment
  // (three "lost" pathways in energy-drawer.spec, T-UI-91, on slow runners).
  await openDemo(page)
  await page.getByRole('button', { name: 'Profile and table' }).click()
  const drawer = page.getByLabel('Energy drawer')
  await drawer.getByLabel('Add branch pathway').selectOption({ label: 'B' })
  await expect(drawer.getByRole('group', { name: 'Pathway B' })).toContainText('T-S0 → B-S1')
  await expect.poll(async () => (await (await page.request.get('/api/view-state')).json()).drawer.open).toBe(true)
  // Whether a lost pathway is ever drawn, however briefly, from the first moment of the page.
  await page.addInitScript(() => {
    const seen = window as unknown as { lostSeen: boolean }
    seen.lostSeen = false
    new MutationObserver(() => {
      if (document.querySelector('[aria-label="Pathway that lost a node"]')) seen.lostSeen = true
    }).observe(document, { childList: true, subtree: true })
  })
  await page.route('**/api/canvas', async (route) => {
    await new Promise((done) => setTimeout(done, 1500))
    await route.continue()
  })
  const profiles = page.waitForResponse((r) => new URL(r.url()).pathname === '/api/energy-profiles')
  const canvas = page.waitForResponse((r) => new URL(r.url()).pathname === '/api/canvas')
  await page.reload()
  // The profiles are in well before the held-back canvas.
  await (await profiles).finished()
  await (await canvas).finished()
  await expect(page.getByLabel('Energy drawer').getByRole('group', { name: 'Pathway B' })).toContainText('T-S0 → B-S1')
  expect(await page.evaluate(() => (window as unknown as { lostSeen: boolean }).lostSeen)).toBe(false)
})
