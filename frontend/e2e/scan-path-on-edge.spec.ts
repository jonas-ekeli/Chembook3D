import { join, resolve } from 'node:path'
import { expect, test, type Page } from '@playwright/test'

const E2E_DIR = process.env.E2E_DIR!
const FIXTURES = resolve(import.meta.dirname, '..', '..', 'tests', 'fixtures')
const SCREENSHOTS = process.env.SCREENSHOTS

async function newInvestigation(page: Page, name: string) {
  await page.goto('/')
  await page.getByRole('button', { name: /^New/ }).first().click()
  const dialog = page.getByRole('dialog')
  await expect(dialog.getByRole('list', { name: 'Folders' })).toBeVisible()
  await dialog.getByLabel('Folder path').fill(E2E_DIR)
  await dialog.getByRole('button', { name: 'Go' }).click()
  await expect(dialog.getByLabel('Folder path')).toHaveValue(E2E_DIR)
  await dialog.getByLabel('Investigation name').fill(name)
  await dialog.getByRole('button', { name: 'Create' }).click()
  await expect(page.locator('.investigation')).toHaveText(name)
}

test('a scan path shown as a chip on its edge, opened from there', async ({ page }) => {
  // D121, A68, T-UI-25
  test.slow()
  await newInvestigation(page, 'Path on edge')
  const ids: string[] = []
  for (const [label, x, y] of [
    ['Start', 0, 0],
    ['End', 700, 0],
  ] as const) {
    const response = await page.request.post('/api/nodes', { data: { label, pos_x: x, pos_y: y } })
    expect(response.ok()).toBe(true)
    ids.push((await response.json()).id)
  }
  expect((await page.request.post('/api/transitions', { data: { source_id: ids[0], target_id: ids[1] } })).ok()).toBe(true)
  await page.reload()
  await expect(page.locator('.react-flow__node')).toHaveCount(2)

  // A path imported from a file starts on the canvas, unlinked; its panel shows it on an edge.
  await page.getByRole('button', { name: 'Import file…' }).click()
  const dialog = page.getByRole('dialog')
  await dialog.getByLabel('Upload a file').setInputFiles(join(FIXTURES, 'xtb', 'dce_scan', 'xtbscan.log'))
  await dialog.getByRole('button', { name: 'Import', exact: true }).click()
  const inspector = page.getByLabel('Node inspector')
  await expect(inspector.getByRole('heading', { name: 'xtbscan' })).toBeVisible()
  const section = inspector.getByLabel('Scan path on an edge')
  await section.getByLabel('Show path on edge').selectOption({ label: 'Start → End' })
  await expect(page.getByRole('status')).toContainText('Shown as a chip on its edge.')
  const chip = page.locator('.edge-label .scan-path-chip')
  await expect(chip).toHaveText(/^xTB path · top \+\d+\.\d+ kcal\/mol$/)
  const before = await chip.textContent()
  await expect(page.locator('.react-flow__node')).toHaveCount(3)

  // On the edge only: the box leaves the canvas and the node list marks it.
  await section.getByLabel('On that edge only').click()
  await expect(section.getByLabel('On that edge only')).toBeChecked()
  await expect(page.locator('.react-flow__node')).toHaveCount(2)
  await expect(page.getByRole('list', { name: 'Nodes' }).getByText('on edge')).toBeVisible()
  if (SCREENSHOTS) await page.screenshot({ path: join(SCREENSHOTS, 'scan-path-chip.png') })

  // The chip opens the edge's panel with the path's movie; removing a point there acts on the
  // path node and the chip follows the new top.
  await chip.click()
  const edge = page.getByLabel('Transition inspector')
  const paths = edge.getByLabel('Scan paths')
  await expect(paths.getByRole('button', { name: 'xtbscan' })).toBeVisible()
  await expect(paths.getByText('no quality check')).toBeVisible()
  const movie = paths.getByLabel('Optimization steps')
  await expect(movie.getByLabel('Structure shown')).toContainText('of 13')
  await movie.getByRole('button', { name: 'Pause' }).click()
  await movie.getByLabel('Structure', { exact: true }).fill('4')
  await movie.getByRole('button', { name: 'Remove this point' }).click()
  await expect(page.getByRole('status')).toContainText('Point 5 removed from the path.')
  await expect(chip).not.toHaveText(before!)
  if (SCREENSHOTS) await page.screenshot({ path: join(SCREENSHOTS, 'scan-path-edge-panel.png') })

  // Deleting the edge keeps the path node, unlinked, back on the canvas.
  await edge.getByRole('button', { name: 'Delete transition' }).click()
  const confirm = page.getByRole('dialog')
  await expect(confirm.getByLabel('Scan paths kept')).toContainText('xtbscan')
  await confirm.getByRole('button', { name: 'Delete', exact: true }).click()
  await expect(page.locator('.react-flow__node')).toHaveCount(3)
  await expect(chip).toHaveCount(0)
})
