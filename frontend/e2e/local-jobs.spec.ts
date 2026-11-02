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
  // D216: g-xTB is an optional install of its own.
  await expect(settings.getByLabel('g-xTB status')).toHaveText('Not installed')
  await expect(settings.getByRole('button', { name: 'Install g-xTB' })).toBeEnabled()
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

// T-UI-84, D214: an ONIOM refine suggests its DFT core from the metal, shows its size and link
// hydrogens, refuses a core that cuts a bond to the metal, and runs with the core chosen.
const BUTYL = [
  '16',
  'butyl',
  'Pd 0 0 0',
  'Cl 0 2.3 0',
  'Cl 0 -2.3 0',
  'C 2.05 0 0',
  'C 3.30 0.88 0',
  'C 4.55 0 0',
  'C 5.80 0.88 0',
  'H 2.05 -0.63 0.89',
  'H 2.05 -0.63 -0.89',
  'H 3.30 1.51 0.89',
  'H 3.30 1.51 -0.89',
  'H 4.55 -0.63 0.89',
  'H 4.55 -0.63 -0.89',
  'H 5.80 1.51 0.89',
  'H 5.80 1.51 -0.89',
  'H 6.83 0.52 0',
  '',
].join('\n')

test('an ONIOM refine starts from a suggested DFT core that can be changed', async ({ page }) => {
  test.slow()
  await startPage(page)
  await page.getByRole('button', { name: /^New/ }).first().click()
  const dialog = page.getByRole('dialog')
  await expect(dialog.getByRole('list', { name: 'Folders' })).toBeVisible()
  await dialog.getByLabel('Folder path').fill(E2E_DIR)
  await dialog.getByRole('button', { name: 'Go' }).click()
  await dialog.getByLabel('Investigation name').fill('Local ONIOM')
  await dialog.getByRole('button', { name: 'Create' }).click()
  await expect(page.locator('.investigation')).toHaveText('Local ONIOM')
  const created = await page.request.post('/api/nodes', { data: { label: 'butyl', xyz: BUTYL, charge: -1 } })
  expect(created.ok()).toBeTruthy()
  const id = (await created.json()).id
  await page.reload()

  await page.locator('.react-flow__node').filter({ hasText: 'butyl' }).click({ button: 'right' })
  await page.getByRole('menuitem', { name: 'Refine geometry…' }).click()
  const refine = page.getByRole('dialog', { name: 'Refine geometry of “butyl”' })
  await expect(refine.getByLabel('Method', { exact: true }).locator('option', { hasText: /^ONIOM\(r2SCAN-3c:GFN2-xTB\) · about/ })).toHaveCount(1)
  // A143: a structure with a metal starts on Quick DFT.
  await expect(refine.getByLabel('Method', { exact: true })).toHaveValue('oniom')
  await expect(refine.getByRole('group', { name: 'DFT core' })).toBeVisible()
  await refine.getByLabel('Method', { exact: true }).selectOption('oniom-stub')
  const core = refine.getByRole('group', { name: 'DFT core' })
  await expect(core.getByLabel('Core atoms')).toHaveValue('1-6, 8-13')
  await expect(core.getByLabel('Core size')).toHaveText('12 of 16 atoms and 1 link hydrogen in r2SCAN-3c; GFN2-xTB for the whole molecule.')
  await expect(core.getByTestId('viewer3d')).toBeVisible()

  // Leaving a chlorine out cuts its bond to the palladium: refused.
  await core.getByLabel('Core atoms').fill('1, 2, 4-6, 8-13')
  await expect(core.getByRole('alert').filter({ hasText: 'to a metal' })).toHaveCount(1)
  await expect(refine.getByRole('button', { name: 'Start' })).toBeDisabled()
  await core.getByLabel('Core atoms').fill('1-6, 8-13')
  await expect(core.getByLabel('Core size')).toBeVisible()
  await refine.getByRole('button', { name: 'Start' }).click()
  await expect(refine).toHaveCount(0)
  await expect(page.getByText(/The ONIOM\(stub:stub-low\), 12-atom core refine of “butyl” is done/)).toBeVisible({ timeout: 60_000 })
  const calcs = await (await page.request.get(`/api/nodes/${id}/calculations`)).json()
  expect(calcs).toHaveLength(1)
  expect(JSON.stringify(calcs[0])).toContain('12-atom core')
})

// T-UI-85, D215: a TS search from Refine geometry, with reacting atoms; the result's check of
// its imaginary mode is told when it is imported and shown under Local jobs.
// T-UI-86, D217: a cheap method's search starts from its own Hessian and ends with frequencies
// at that level (on by default), and the checked imaginary mode can be animated there.
test('a transition state search on this computer checks its imaginary mode', async ({ page }) => {
  test.slow()
  await startPage(page)
  await page.getByRole('button', { name: /^New/ }).first().click()
  const dialog = page.getByRole('dialog')
  await expect(dialog.getByRole('list', { name: 'Folders' })).toBeVisible()
  await dialog.getByLabel('Folder path').fill(E2E_DIR)
  await dialog.getByRole('button', { name: 'Go' }).click()
  await dialog.getByLabel('Investigation name').fill('Local TS')
  await dialog.getByRole('button', { name: 'Create' }).click()
  await expect(page.locator('.investigation')).toHaveText('Local TS')
  expect((await page.request.post('/api/nodes', { data: { label: 'saddle', xyz: WATER } })).ok()).toBeTruthy()
  await page.reload()

  await page.locator('.react-flow__node').filter({ hasText: 'saddle' }).click({ button: 'right' })
  await page.getByRole('menuitem', { name: 'Refine geometry…' }).click()
  const refine = page.getByRole('dialog', { name: 'Refine geometry of “saddle”' })
  await refine.getByLabel('Method', { exact: true }).selectOption('stub-saddle')
  await expect(refine.getByLabel('Reacting atoms')).toHaveCount(0)
  await refine.getByLabel('Search for a transition state').check()
  await expect(refine.getByText(/starting from a stub-saddle Hessian/)).toBeVisible()
  await expect(refine.getByLabel('Frequencies at the result')).toBeChecked()
  await refine.getByLabel('Reacting atoms').fill('1 7')
  await expect(refine.getByRole('alert')).toHaveText('Reacting atoms are numbered 1 to 3')
  await expect(refine.getByRole('button', { name: 'Start' })).toBeDisabled()
  await refine.getByLabel('Reacting atoms').fill('1 2')
  await refine.getByRole('button', { name: 'Start' }).click()
  await expect(refine).toHaveCount(0)
  await expect(page.getByText(/The stub-saddle TS search of “saddle” is done and imported .* stub-saddle check: one imaginary frequency \(-?\d+ cm⁻¹\), on the reacting atoms\./)).toBeVisible({
    timeout: 60_000,
  })
  await page.getByRole('button', { name: /^Local jobs/ }).click()
  const row = page.getByRole('dialog', { name: 'Local jobs' }).getByRole('table', { name: 'Local jobs' }).getByRole('row').nth(1)
  await expect(row).toContainText('TS search')
  await expect(row.getByLabel('TS check')).toContainText('on the reacting atoms')
  await expect(row.getByLabel('Frequencies')).toContainText('stub-saddle frequencies at the result (1 imaginary)')
  await row.getByRole('button', { name: 'Show mode' }).click()
  await expect(page.getByLabel('Imaginary mode').locator('canvas').first()).toBeVisible()
  await page.getByRole('button', { name: 'Hide mode' }).click()
  await expect(page.getByLabel('Imaginary mode')).toHaveCount(0)
  const nodes = await (await page.request.get('/api/nodes')).json()
  const saddle = nodes.find((n: { label: string }) => n.label === 'saddle')
  const calcs = await (await page.request.get(`/api/nodes/${saddle.id}/calculations`)).json()
  expect(calcs.map((c: { type: string }) => c.type)).toContain('frequency')
})

// T-UI-87, D218: a relaxed scan from Refine geometry, two coordinates driven together; it is
// imported as a node of its own beside the start node.
test('a relaxed scan on this computer comes in as a scan node', async ({ page }) => {
  test.slow()
  await startPage(page)
  await page.getByRole('button', { name: /^New/ }).first().click()
  const dialog = page.getByRole('dialog')
  await expect(dialog.getByRole('list', { name: 'Folders' })).toBeVisible()
  await dialog.getByLabel('Folder path').fill(E2E_DIR)
  await dialog.getByRole('button', { name: 'Go' }).click()
  await dialog.getByLabel('Investigation name').fill('Local scan')
  await dialog.getByRole('button', { name: 'Create' }).click()
  await expect(page.locator('.investigation')).toHaveText('Local scan')
  expect((await page.request.post('/api/nodes', { data: { label: 'start', xyz: WATER } })).ok()).toBeTruthy()
  await page.reload()

  await page.locator('.react-flow__node').filter({ hasText: 'start' }).click({ button: 'right' })
  await page.getByRole('menuitem', { name: 'Refine geometry…' }).click()
  const refine = page.getByRole('dialog', { name: 'Refine geometry of “start”' })
  await refine.getByLabel('Method', { exact: true }).selectOption('stub')
  await refine.getByLabel('Relaxed scan').check()
  await expect(refine.getByLabel('Search for a transition state')).toBeDisabled()
  const first = refine.getByRole('group', { name: 'Scanned coordinate 1' })
  await first.getByLabel('Scan atoms').fill('1 2')
  await expect(first.getByLabel('Scan from')).toHaveAttribute('placeholder', /^\d\.\d{3} Å$/)
  await expect(refine.getByRole('alert')).toHaveText('Scan of 1 2: give the value to scan to')
  await expect(refine.getByRole('button', { name: 'Start' })).toBeDisabled()
  await first.getByLabel('Scan to').fill('1.2')
  await refine.getByRole('button', { name: 'Scan another coordinate with it' }).click()
  const second = refine.getByRole('group', { name: 'Scanned coordinate 2' })
  await second.getByLabel('Scan atoms').fill('1 3')
  await second.getByLabel('Scan from').fill('1.1')
  await second.getByLabel('Scan to').fill('0.9')
  await refine.getByLabel('Scan points').fill('4')
  await expect(refine.getByText(/imported as a node of its own beside this one/)).toBeVisible()
  await refine.getByRole('button', { name: 'Start' }).click()
  await expect(refine).toHaveCount(0)
  await expect(page.getByText(/The stub scan of “start” is done and imported as a node of its own beside it .* 4 of 4 points converged/)).toBeVisible({
    timeout: 60_000,
  })
  await expect(page.locator('.react-flow__node').filter({ hasText: 'start scan B(1,2)+B(1,3)' })).toBeVisible()
  await page.getByRole('button', { name: /^Local jobs/ }).click()
  const row = page.getByRole('dialog', { name: 'Local jobs' }).getByRole('table', { name: 'Local jobs' }).getByRole('row').nth(1)
  await expect(row).toContainText('scan of 2 coordinates, 4 points')
  await expect(row.getByLabel('Scan')).toContainText('4 of 4 points converged')
})

// T-UI-88, D219: a 2D grid scan of water's two O–H bonds; it comes in as a node whose step
// movie shows the energy surface, and a cell shows its point.
test('a 2D grid scan shows its energy surface', async ({ page }) => {
  test.slow()
  await startPage(page)
  await page.getByRole('button', { name: /^New/ }).first().click()
  const dialog = page.getByRole('dialog')
  await expect(dialog.getByRole('list', { name: 'Folders' })).toBeVisible()
  await dialog.getByLabel('Folder path').fill(E2E_DIR)
  await dialog.getByRole('button', { name: 'Go' }).click()
  await dialog.getByLabel('Investigation name').fill('Local grid')
  await dialog.getByRole('button', { name: 'Create' }).click()
  await expect(page.locator('.investigation')).toHaveText('Local grid')
  expect((await page.request.post('/api/nodes', { data: { label: 'start', xyz: WATER } })).ok()).toBeTruthy()
  await page.reload()

  await page.locator('.react-flow__node').filter({ hasText: 'start' }).click({ button: 'right' })
  await page.getByRole('menuitem', { name: 'Refine geometry…' }).click()
  const refine = page.getByRole('dialog', { name: 'Refine geometry of “start”' })
  await refine.getByLabel('Method', { exact: true }).selectOption('stub')
  await refine.getByLabel('Relaxed scan').check()
  await refine.getByLabel('2D grid').check()
  await expect(refine.getByRole('group', { name: /^Scanned coordinate/ })).toHaveCount(2)
  await expect(refine.getByRole('button', { name: 'Scan another coordinate with it' })).toHaveCount(0)
  const first = refine.getByRole('group', { name: 'Scanned coordinate 1' })
  await first.getByLabel('Scan atoms').fill('1 2')
  await first.getByLabel('Scan from').fill('0.9')
  await first.getByLabel('Scan to').fill('1.2')
  await first.getByLabel('Grid points').fill('3')
  const second = refine.getByRole('group', { name: 'Scanned coordinate 2' })
  await second.getByLabel('Scan atoms').fill('1 3')
  await second.getByLabel('Scan from').fill('0.9')
  await second.getByLabel('Scan to').fill('1.2')
  await second.getByLabel('Grid points').fill('31')
  await expect(refine.getByRole('alert')).toContainText('2 to 30 points')
  await second.getByLabel('Grid points').fill('3')
  await refine.getByRole('button', { name: 'Start' }).click()
  await expect(refine).toHaveCount(0)
  await expect(page.getByText(/The stub grid scan of “start” is done .* 9 of 9 points converged/)).toBeVisible({ timeout: 60_000 })

  await page.locator('.react-flow__node').filter({ hasText: 'start grid B(1,2) × B(1,3)' }).click()
  const movie = page.getByLabel('Node inspector').getByLabel('Optimization steps')
  await movie.getByLabel('Steps of').selectOption({ index: 1 })
  const surface = movie.getByRole('figure', { name: 'Energy surface' })
  await expect(surface).toBeVisible()
  await expect(surface.getByRole('button', { name: /^Point \d+:/ })).toHaveCount(9)
  await surface.getByRole('button', { name: /^Point 1: B\(1,2\) 0\.90 Å, B\(1,3\) 0\.90 Å/ }).click()
  await expect(movie.locator('.surface-current')).toHaveCount(1)
  await expect(movie.getByRole('button', { name: 'Open in Scan editor' })).toHaveCount(0)
})
