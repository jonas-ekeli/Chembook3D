import { expect, test } from '@playwright/test'
import { startPage } from './appBar.ts'

// T-UI-83, D213: Refine geometry runs on this computer (here the worker's test method with the
// app's own Python, playwright.config.ts), Local jobs follows it, and its result is imported onto
// the node by itself; a running job can be cancelled.
const E2E_DIR = process.env.E2E_DIR!

const WATER = '3\nwater\nO 0.000000 0.000000 0.117300\nH 0.000000 0.757200 -0.469200\nH 0.000000 -0.757200 -0.469200\n'

test('a refine on this computer is followed under Local jobs and imported onto its node', async ({ page }) => {
  test.slow()
  await startPage(page)
  await page.getByRole('button', { name: /^New/ }).first().click()
  const dialog = page.getByRole('dialog')
  await expect(dialog.getByRole('list', { name: 'Folders' })).toBeVisible()
  await dialog.getByLabel('Folder path').fill(E2E_DIR)
  await dialog.getByRole('button', { name: 'Go' }).click()
  await dialog.getByLabel('Investigation name').fill('Local refine')
  await dialog.getByRole('button', { name: 'Create' }).click()
  await expect(page.locator('.investigation')).toHaveText('Local refine')
  const ids: Record<string, string> = {}
  for (const label of ['water', 'second']) {
    const response = await page.request.post('/api/nodes', { data: { label, xyz: WATER, pos_x: label === 'water' ? 0 : 400 } })
    expect(response.ok()).toBeTruthy()
    ids[label] = (await response.json()).id
  }
  await page.reload()

  // Settings shows the tools as installed (the test's stand-in) with the cores to use.
  await page.getByRole('button', { name: 'Settings' }).click()
  const settings = page.getByRole('dialog', { name: 'Settings' })
  await settings.getByRole('navigation', { name: 'Settings sections' }).getByRole('button', { name: 'Local calculations' }).click()
  await expect(settings.getByRole('heading', { name: 'Local calculations This computer' })).toBeVisible()
  await expect(settings.getByLabel('Local tools status')).toHaveText(/^Installed/)
  await expect(settings.getByLabel('Cores for local jobs')).toBeVisible()
  await page.keyboard.press('Escape')

  // Refine geometry… on the node's menu.
  await page.locator('.react-flow__node').filter({ hasText: 'water' }).click({ button: 'right' })
  await page.getByRole('menuitem', { name: 'Refine geometry…' }).click()
  const refine = page.getByRole('dialog', { name: 'Refine geometry of “water”' })
  await expect(refine.getByLabel('Charge')).toHaveValue('0')
  await expect(refine.getByLabel('Multiplicity')).toHaveValue('1')
  // The DFT methods come with a time estimate for the node's atoms.
  await expect(refine.getByLabel('Method', { exact: true }).locator('option', { hasText: /^B97-3c · about/ })).toHaveCount(1)
  await expect(refine.getByLabel('Method', { exact: true }).locator('option', { hasText: /^r2SCAN-3c · about/ })).toHaveCount(1)
  await refine.getByLabel('Method', { exact: true }).selectOption('stub')
  // Constraints: a frozen atom and a distance held at a target; a bad atom number is refused.
  await refine.getByLabel('Frozen atoms').fill('1')
  await refine.getByRole('button', { name: 'Hold a distance, angle or dihedral' }).click()
  const hold = refine.getByRole('group', { name: 'Held coordinate 1' })
  await hold.getByLabel('Atoms').fill('1 9')
  await expect(refine.getByRole('alert')).toHaveText('Atoms are numbered 1 to 3')
  await expect(refine.getByRole('button', { name: 'Start' })).toBeDisabled()
  await hold.getByLabel('Atoms').fill('1 2')
  await expect(hold.getByLabel('Target')).toHaveAttribute('placeholder', '0.958 Å')
  await hold.getByLabel('Target').fill('1.0')
  await refine.getByRole('button', { name: 'Start' }).click()
  await expect(refine).toHaveCount(0)
  await expect(page.getByText(/Refining “water” with stub on this computer/)).toBeVisible()

  // It finishes and is imported by itself: the node gets its optimization.
  await expect(page.getByText(/The stub refine of “water” is done and imported onto it/)).toBeVisible({ timeout: 60_000 })
  await page.getByRole('button', { name: /^Local jobs/ }).click()
  const jobs = page.getByRole('dialog', { name: 'Local jobs' })
  const row = jobs.getByRole('table', { name: 'Local jobs' }).getByRole('row').nth(1)
  await expect(row).toContainText('water')
  await expect(row).toContainText('Converged, imported')
  await expect(row).toContainText('ΔE')
  await expect(row).toContainText('with constraints')
  await row.getByRole('button', { name: 'Show node' }).click()
  await expect(page.getByLabel('Node inspector').getByRole('heading', { name: 'water' })).toBeVisible()
  const onNode = await (await page.request.get(`/api/nodes/${ids.water}/calculations`)).json()
  expect(onNode).toHaveLength(1)
  expect(onNode[0].program).toBe('stub')
  expect(onNode[0].constraints).toEqual(['1 frozen atom', 'constrained B(1,2)'])

  // A second refine is cancelled from the list while it runs.
  const started = await page.request.post('/api/local/jobs', { data: { node_id: ids.second, method: 'stub' } })
  expect(started.ok()).toBeTruthy()
  await page.getByRole('button', { name: /^Local jobs/ }).click()
  await expect(page.getByRole('button', { name: 'Local jobs · 1 running' })).toBeVisible()
  const running = jobs.getByRole('table', { name: 'Local jobs' }).getByRole('row').nth(1)
  await expect(running).toContainText('second')
  await running.getByRole('button', { name: 'Cancel' }).click()
  await expect(running).toContainText('Cancelled')
  await expect(page.getByRole('button', { name: 'Local jobs', exact: true })).toBeVisible()
})
