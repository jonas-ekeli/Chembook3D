import { join } from 'node:path'
import { expect, test } from '@playwright/test'
import { startPage } from './appBar.ts'

// D182 (UI10), T-UI-59: the start page lists recent investigations by name with their folder,
// when they were opened and their size; empty views explain themselves and offer the first step.

const E2E_DIR = process.env.E2E_DIR!

test('the start page names recent investigations, and empty views offer a first step', async ({ page }) => {
  const stamp = Date.now()
  const folder = join(E2E_DIR, `start-${stamp}`)
  const name = `Start page ${stamp}`
  const created = await page.request.post('/api/investigations', { data: { folder, name } })
  expect(created.ok()).toBeTruthy()
  const branch = await (await page.request.post('/api/branches', { data: { name: 'A' } })).json()
  for (const label of ['A-S1', 'A-S2'])
    await page.request.post('/api/nodes', { data: { label, branch_id: branch.id } })
  // Written when it was opened, brought up to date when it is closed.
  await startPage(page)

  for (const card of ['New investigation…', 'Open investigation…', 'Open from GitHub…'])
    await expect(page.getByRole('button', { name: card, exact: true })).toBeVisible()
  await expect(page.getByRole('button', { name: 'Open investigation…' })).toHaveAccessibleDescription(
    'A folder on this computer',
  )
  const recent = page.getByRole('list', { name: 'Recently opened' })
  const row = recent.getByRole('button', { name: new RegExp(`^${name}`) })
  await expect(row).toHaveAttribute('title', folder)
  await expect(row.locator('.recent-folder')).toHaveText(folder)
  await expect(row).toContainText('just now')
  await expect(row).toContainText('2 nodes · 1 branch')
  await expect(page.getByRole('button', { name: 'Scan editor', exact: true })).toBeVisible()
  await expect(page.getByRole('button', { name: 'Shut down', exact: true })).toBeVisible()

  // The row opens it, and its drawer offers the branch's pathway.
  // The drawer is opened once the investigation's remembered view (D105) is in place.
  const restored = page.waitForResponse((r) => r.url().endsWith('/api/view-state') && r.request().method() === 'GET')
  await row.click()
  await restored
  await expect(page.locator('.investigation')).toHaveText(name)
  const drawer = page.getByRole('region', { name: 'Energy drawer' })
  await expect(page.getByRole('group', { name: 'Node A-S1', exact: true })).toBeVisible()
  await page.getByRole('button', { name: 'Profile and table' }).click()
  const noPathways = drawer.getByRole('region', { name: 'No pathways yet' })
  await expect(noPathways.getByRole('button', { name: 'Start at the selected node' })).toBeDisabled()
  await noPathways.getByRole('button', { name: 'Pathway of A' }).click()
  await expect(noPathways).toHaveCount(0)
  await expect(drawer.getByRole('button', { name: 'Remove pathway A' })).toBeVisible()

  // Analyses: none yet, and its buttons start one as the side list's New does.
  await page.getByRole('button', { name: 'Analyses' }).click()
  const none = page.getByRole('region', { name: 'No analyses yet' })
  await expect(none).toContainText('turnover frequency of a closed catalytic cycle')
  await none.getByRole('button', { name: 'New turnover' }).click()
  await expect(none).toHaveCount(0)
  await expect(page.getByRole('navigation', { name: 'Analyses' }).getByRole('button', { name: 'Turnover 1' })).toBeVisible()

  // An empty canvas: its buttons work, and a double-click on it still adds a node there.
  await page.request.post('/api/investigations', { data: { folder: `${folder}-empty`, name: 'Empty start' } })
  await page.goto('/')
  const empty = page.getByRole('region', { name: 'An empty canvas' })
  await empty.getByRole('button', { name: 'Import output files…' }).click()
  await expect(page.getByRole('dialog', { name: /^Import/ })).toBeVisible()
  await page.keyboard.press('Escape')
  await expect(page.getByRole('dialog')).toHaveCount(0)
  // The pane gets the double-click on the title: the empty state lets it through.
  const title = (await empty.getByRole('heading', { name: 'An empty canvas' }).boundingBox())!
  await page.mouse.dblclick(title.x + title.width / 2, title.y + title.height / 2)
  const inspector = page.getByLabel('Node inspector')
  await expect(inspector.getByRole('heading', { name: 'Untitled node' })).toBeVisible()
  await expect(empty).toHaveCount(0)

  await page.request.post('/api/investigations', { data: { folder: `${folder}-first`, name: 'First node' } })
  await page.goto('/')
  await empty.getByRole('button', { name: 'Add the first node' }).click()
  await expect(inspector.getByRole('heading', { name: 'Untitled node' })).toBeVisible()
  await expect(empty).toHaveCount(0)
})
