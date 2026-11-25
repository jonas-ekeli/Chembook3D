import { expect, test } from '@playwright/test'
import { E2E_DIR, canvasNode, newInvestigation, startPage } from './helpers.ts'

// T-UI-83, D213: Refine geometry runs on this computer (here the worker's test method with the
// app's own Python, playwright.config.ts), Local jobs follows it, and its result is imported onto
// the node by itself; a running job can be cancelled.

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
  // T-UI-105, D237: MACE-OMOL-0 too, offered only once its licence is accepted.
  await expect(settings.getByLabel('MACE status')).toHaveText('Not installed')
  const installMace = settings.getByRole('button', { name: 'Install MACE-OMOL-0' })
  await expect(installMace).toBeDisabled()
  await expect(settings.getByRole('link', { name: 'Academic Software License (ASL)' })).toHaveAttribute(
    'href',
    'https://github.com/gabor1/ASL',
  )
  await settings.getByRole('checkbox', { name: /I accept the model’s/ }).check()
  await expect(installMace).toBeEnabled()
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

// T-UI-89, D220: a local job is followed in the Inspect window while it runs: its steps so far in
// 3D with their energies and convergence criteria, read again every 5 s until the job ends.
test('a running local job is inspected as it runs', async ({ page }) => {
  test.slow()
  await startPage(page)
  await page.getByRole('button', { name: /^New/ }).first().click()
  const dialog = page.getByRole('dialog')
  await expect(dialog.getByRole('list', { name: 'Folders' })).toBeVisible()
  await dialog.getByLabel('Folder path').fill(E2E_DIR)
  await dialog.getByRole('button', { name: 'Go' }).click()
  await dialog.getByLabel('Investigation name').fill('Local inspect')
  await dialog.getByRole('button', { name: 'Create' }).click()
  await expect(page.locator('.investigation')).toHaveText('Local inspect')
  const node = await page.request.post('/api/nodes', { data: { label: 'water', xyz: WATER } })
  expect(node.ok()).toBeTruthy()
  // Each step of the test method waits 1 s here (playwright.config.ts), so the job is seen running.
  const started = await page.request.post('/api/local/jobs', { data: { node_id: (await node.json()).id, method: 'stub' } })
  expect(started.ok()).toBeTruthy()
  await page.reload()

  await page.getByRole('button', { name: 'Local jobs · 1 running' }).click()
  const jobs = page.getByRole('dialog', { name: 'Local jobs' })
  const row = jobs.getByRole('table', { name: 'Local jobs' }).getByRole('row').nth(1)
  await expect(row).toContainText(/Running, step [2-9]/, { timeout: 30_000 })
  await row.getByRole('button', { name: 'Inspect' }).click()

  const window = page.getByRole('dialog', { name: 'Inspect water · stub' })
  await expect(window.getByLabel('Job status')).toContainText('Running')
  await expect(window.getByRole('checkbox', { name: 'Every 5 s' })).toBeChecked()
  await expect(window.getByRole('button', { name: 'Import this file' })).toHaveCount(0)
  const shown = window.getByLabel('Structure shown')
  await expect(shown).toContainText(/Structure (\d+) of \1/)
  const before = Number((await shown.textContent())!.match(/of (\d+)/)![1])
  // The criteria geomeTRIC judged the step by, against its thresholds.
  const criteria = window.getByRole('table', { name: 'Convergence criteria' })
  await expect(criteria).toContainText('RMS gradient')
  await expect(criteria).toContainText('Maximum displacement')

  // Read again by itself: more steps, and once the job ends, its result and no more reading.
  await expect(window.getByLabel('Job status')).toContainText('Converged', { timeout: 60_000 })
  const after = Number((await shown.textContent())!.match(/of (\d+)/)![1])
  expect(after).toBeGreaterThan(before)
  await expect(window.getByRole('checkbox', { name: 'Every 5 s' })).not.toBeChecked()
  await expect(window.getByRole('table', { name: 'Job steps' })).toContainText('✓')
  await window.getByRole('button', { name: 'Close' }).click()
  await expect(jobs).toBeVisible()
})

// T-UI-90, D220: a running 2D grid scan is inspected point by point: the map fills in as its
// points converge, the ones not run yet left empty.
test('a running grid scan fills in its map in the Inspect window', async ({ page }) => {
  test.slow()
  await startPage(page)
  await page.getByRole('button', { name: /^New/ }).first().click()
  const dialog = page.getByRole('dialog')
  await expect(dialog.getByRole('list', { name: 'Folders' })).toBeVisible()
  await dialog.getByLabel('Folder path').fill(E2E_DIR)
  await dialog.getByRole('button', { name: 'Go' }).click()
  await dialog.getByLabel('Investigation name').fill('Grid inspect')
  await dialog.getByRole('button', { name: 'Create' }).click()
  await expect(page.locator('.investigation')).toHaveText('Grid inspect')
  const node = await page.request.post('/api/nodes', { data: { label: 'w', xyz: WATER } })
  const oh = { kind: 'distance', atoms: [0, 1], start: 0.9, end: 1.2, points: 3 }
  const scan = { grid: true, points: 6, coordinates: [oh, { ...oh, atoms: [0, 2], points: 2 }] }
  const started = await page.request.post('/api/local/jobs', {
    data: { node_id: (await node.json()).id, method: 'stub', scan },
  })
  expect(started.ok()).toBeTruthy()
  await page.reload()

  await page.getByRole('button', { name: 'Local jobs · 1 running' }).click()
  const row = page.getByRole('dialog', { name: 'Local jobs' }).getByRole('table', { name: 'Local jobs' }).getByRole('row').nth(1)
  await expect(row).toContainText(/Running, point [2-5] of 6/, { timeout: 60_000 })
  await row.getByRole('button', { name: 'Inspect' }).click()
  const window = page.getByRole('dialog', { name: 'Inspect w · stub' })
  const map = window.getByRole('figure', { name: 'Energy surface' })
  await expect(map).toBeVisible()
  await expect(map.getByRole('button', { name: /^Point 1: .*above the lowest/ })).toHaveCount(1)
  await expect(map.getByRole('button', { name: /^Point 6: .*not run yet/ })).toHaveCount(1)
  await expect(window.getByLabel('Structure shown')).toContainText(/scan point \d of 6/)
  await expect(window.getByLabel('Job status')).toContainText('Converged', { timeout: 90_000 })
  await expect(map.getByRole('button', { name: /not run yet/ })).toHaveCount(0)
  await expect(map.getByRole('button', { name: /above the lowest/ })).toHaveCount(6)
  // A cell shows its point's structure.
  await map.getByRole('button', { name: /^Point 4:/ }).click()
  await expect(window.getByLabel('Structure shown')).toContainText('scan point 4 of 6')
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
  // The UI tests run the stub engine with a 1 s sleep per energy (CHEMBOOK3D_LOCAL_STUB_DELAY in
  // playwright.config.ts), and the 9 points of this grid take about 50 energies: about 55 s of
  // planned work on every run, which a 60 s wait left almost no room for.
  test.setTimeout(180_000)
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
  await expect(page.getByText(/The stub grid scan of “start” is done .* 9 of 9 points converged/)).toBeVisible({ timeout: 120_000 })

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

// T-UI-106, D238: a TS guess between two structures joined by an edge, from the selection panel:
// the dialog shows the match and the bond that changes, and asks for MACE-OMOL-0 when it is not
// installed (the run itself is the backend's test, T-LOC-35).
const STRETCHED = '3\nstretched\nO 0.000000 0.000000 0.117300\nH 0.000000 2.100000 -1.000000\nH 0.000000 -0.757200 -0.469200\n'

test('a TS guess between two minima is planned from the selection panel', async ({ page }) => {
  test.slow()
  await newInvestigation(page, 'Local TS guess')
  const ids: string[] = []
  for (const [label, xyz, x] of [
    ['bound', WATER, 0],
    ['apart', STRETCHED, 400],
  ] as const) {
    const response = await page.request.post('/api/nodes', { data: { label, xyz, pos_x: x, pos_y: 100, charge: 0, multiplicity: 1 } })
    expect(response.ok()).toBeTruthy()
    ids.push(((await response.json()) as { id: string }).id)
  }
  expect((await page.request.post('/api/transitions', { data: { source_id: ids[0], target_id: ids[1] } })).ok()).toBeTruthy()
  await page.reload()
  await canvasNode(page, 'apart').click()
  await canvasNode(page, 'bound').click({ modifiers: ['Control'] })
  await page.getByLabel('Selection inspector').getByRole('button', { name: 'TS guess on this computer…' }).click()

  const dialog = page.getByRole('dialog', { name: 'TS guess on this computer' })
  // The edge runs from bound to apart, whatever the order of selection.
  await expect(dialog.getByLabel('TS guess plan')).toContainText('From “bound” to “apart”')
  await expect(dialog.getByLabel('Match summary')).toContainText('1 bond breaks')
  await expect(dialog.getByLabel('Reacting atoms')).toContainText('O1–H2 (breaks)')
  // T-UI-107, D239: the PBE correction is offered and off unless ticked.
  await expect(dialog.getByRole('checkbox', { name: 'Correct the saddle with PBE single points' })).not.toBeChecked()
  // MACE-OMOL-0 is not installed here: Start waits for it, and Settings is one click away.
  await expect(dialog.getByRole('button', { name: 'Start' })).toBeDisabled()
  await dialog.getByRole('button', { name: 'Install it in Settings' }).click()
  await expect(page.getByRole('dialog', { name: 'Settings' }).getByLabel('MACE status')).toHaveText('Not installed')
})

// T-UI-109, D242: a TS guess's calculation keeps the check of its saddle, a Hessian of the reacting
// region only: its record says so and that no ZPE or G comes from it, and the 3D view animates its
// mode. The result is written here as the worker writes it (the run is T-LOC-35's).
const GUESS = {
  format: 'chembook3d-local-result',
  program: 'MACE',
  method: 'mace-omol',
  level: 'MACE MACE-OMOL-0',
  state: 'converged',
  transition: true,
  charge: 0,
  multiplicity: 1,
  elements: ['O', 'H', 'H'],
  frames: [
    [
      [0, 0, 0.1173],
      [0, 1.4, -0.7],
      [0, -0.7572, -0.4692],
    ],
  ],
  energies: [-76.3],
  energy: -76.3,
  ts_guess: {
    mode_check: {
      imaginary: [-202.4],
      lowest: [-202.4, 310.2, 905.6],
      region: [0, 1, 2],
      partial: true,
      on_reacting: true,
      mode: [
        [0, -0.1, 0.05],
        [0, 0.98, -0.1],
        [0, 0.02, 0.01],
      ],
    },
  },
}

test("a TS guess's mode check stays on its calculation", async ({ page }) => {
  await newInvestigation(page, 'Mode check')
  await page.getByRole('button', { name: 'Import file…' }).click()
  const dialog = page.getByRole('dialog')
  await dialog.getByLabel('Upload a file').setInputFiles({ name: 'ts-guess.json', mimeType: 'application/json', buffer: Buffer.from(JSON.stringify(GUESS, null, 1)) })
  await dialog.getByRole('button', { name: 'Import', exact: true }).click()
  const inspector = page.getByLabel('Node inspector')
  await inspector.getByLabel('Calculations').getByRole('button').first().click()
  await expect(inspector.getByLabel('Mode check')).toHaveText(
    'MACE MACE-OMOL-0 a Hessian of 3 atoms around the reacting ones, the rest held: one imaginary mode (202i cm⁻¹), on the bonds that change. A check of the guess only, so no ZPE or G comes from it. Its mode is under Vibrations in 3D.',
  )
  const modes = inspector.getByLabel('Animate mode')
  await expect(modes.locator('option')).toHaveText(['No animation', 'Mode 1: 202.4i cm⁻¹'])
  await modes.selectOption({ index: 1 })
  await expect(inspector.getByTestId('viewer3d').locator('canvas')).toBeVisible()
  await expect(inspector.getByRole('alert')).toHaveCount(0)
})

// T-UI-108, D240: single points on this computer, from the node's menu and for several nodes from
// the selection panel; each comes back as a single-point calculation on its node.
test('single points on this computer are added to their nodes', async ({ page }) => {
  test.slow()
  await newInvestigation(page, 'Local single points')
  for (const [label, x] of [
    ['first', 0],
    ['second', 400],
  ] as const) {
    const response = await page.request.post('/api/nodes', { data: { label, xyz: WATER, pos_x: x, pos_y: 100, charge: 0, multiplicity: 1 } })
    expect(response.ok()).toBeTruthy()
  }
  await page.reload()

  await canvasNode(page, 'first').click({ button: 'right' })
  await page.getByRole('menuitem', { name: 'Single point on this computer…' }).click()
  const one = page.getByRole('dialog', { name: 'Single point of “first”' })
  // The default level is PBE-D3M(BJ)/def2-TZVP, with an estimate for the node's atoms.
  await expect(one.getByLabel('Level', { exact: true })).toHaveValue('pbe-tzvp')
  await expect(one.getByLabel('Level', { exact: true }).locator('option', { hasText: /^r2SCAN-3c · about/ })).toHaveCount(1)
  await one.getByLabel('Level', { exact: true }).selectOption('r2scan-3c')
  await expect(one.getByLabel('Level help')).toContainText('Lost the barrier')
  await expect(one.getByLabel('Structures')).toContainText('first · 3 atoms · charge 0, multiplicity 1')
  await one.getByRole('button', { name: 'Cancel' }).click()

  await canvasNode(page, 'first').click()
  await canvasNode(page, 'second').click({ modifiers: ['Control'] })
  await page.getByLabel('Selection inspector').getByRole('button', { name: 'Single points on this computer…' }).click()
  const both = page.getByRole('dialog', { name: 'Single points of 2 structures' })
  await both.getByLabel('Level', { exact: true }).selectOption('stub')
  await expect(both.getByLabel('Estimate')).toContainText('in all, one after another')
  await both.getByRole('button', { name: 'Start 2' }).click()
  await expect(both).toHaveCount(0)
  await expect(page.getByText(/2 single points with stub-sp\/def2-SVP started on this computer/)).toBeVisible()

  // Both finish and are added to their nodes by themselves.
  await page.getByRole('button', { name: /^Local jobs/ }).click()
  const rows = page.getByRole('dialog', { name: 'Local jobs' }).getByRole('table', { name: 'Local jobs' })
  await expect(rows.getByText('Done, imported')).toHaveCount(2, { timeout: 60_000 })
  await expect(rows.getByText(/single point/).first()).toBeVisible()
})

// T-UI-112, D258: Refine geometry starts from a pasted structure instead of the node's
// coordinates; a structure with the atoms in another order is refused in the dialog.
test('a refine starts from a pasted structure', async ({ page }) => {
  test.slow()
  await newInvestigation(page, 'Local start structure')
  const made = await page.request.post('/api/nodes', { data: { label: 'water', xyz: WATER, charge: 0, multiplicity: 1 } })
  expect(made.ok()).toBeTruthy()
  const id = (await made.json()).id
  await page.reload()

  await canvasNode(page, 'water').click({ button: 'right' })
  await page.getByRole('menuitem', { name: 'Refine geometry…' }).click()
  const refine = page.getByRole('dialog', { name: 'Refine geometry of “water”' })
  await refine.getByLabel('Method', { exact: true }).selectOption('stub')
  await expect(refine.getByLabel('Start from')).toHaveValue('node')
  await refine.getByLabel('Start from').selectOption('xyz')
  await refine.getByLabel('Start structure').fill('H 0 0.75 -0.47\nO 0 0 0.12\nH 0 -0.75 -0.47\n')
  await expect(refine.getByRole('alert')).toHaveText(/Atom 1 is H; in this node it is O/)
  await expect(refine.getByRole('button', { name: 'Start' })).toBeDisabled()
  await refine.getByLabel('Start structure').fill('O 0 0 0.15\nH 0 0.80 -0.45\nH 0 -0.70 -0.50\n')
  await refine.getByRole('button', { name: 'Start' }).click()
  await expect(refine).toHaveCount(0)

  await page.getByRole('button', { name: /^Local jobs/ }).click()
  const row = page.getByRole('dialog', { name: 'Local jobs' }).getByRole('table', { name: 'Local jobs' }).getByRole('row').nth(1)
  await expect(row).toContainText('from a structure')
  await expect(row).toContainText('Converged, imported', { timeout: 60_000 })
  const jobs = await (await page.request.get('/api/local/jobs')).json()
  expect(jobs[0].start).toEqual({ from: 'structure', label: 'XYZ text' })
  const calculations = await (await page.request.get(`/api/nodes/${id}/calculations`)).json()
  expect(calculations).toHaveLength(1)
})

// T-UI-113, D259: a calculation is removed from its record after a confirm that lists the
// calculations sitting on it, which go with it.
test('a calculation is removed with those sitting on it', async ({ page }) => {
  test.slow()
  await newInvestigation(page, 'Remove a calculation')
  const made = await page.request.post('/api/nodes', { data: { label: 'water', xyz: WATER, charge: 0, multiplicity: 1 } })
  const id = (await made.json()).id
  const started = await page.request.post('/api/local/jobs', { data: { node_id: id, method: 'stub', frequencies: true } })
  expect(started.ok()).toBeTruthy()
  const job = (await started.json()).id
  await expect
    .poll(async () => (await (await page.request.get(`/api/local/jobs/${job}`)).json()).status, { timeout: 60_000 })
    .toBe('converged')
  expect((await page.request.post(`/api/local/jobs/${job}/import`)).ok()).toBeTruthy()
  await page.reload()

  await canvasNode(page, 'water').click()
  const list = page.getByLabel('Node inspector').getByRole('list', { name: 'Calculations' })
  await expect(list.getByRole('listitem')).toHaveCount(2)
  await list.getByRole('button', { name: /^Optimization/ }).click()
  await list.getByRole('button', { name: 'Remove…' }).click()
  const confirm = page.getByRole('dialog', { name: 'Remove calculation?' })
  const removed = confirm.getByRole('list', { name: 'Calculations removed' }).getByRole('listitem')
  await expect(removed).toHaveCount(2)
  await expect(removed.nth(0)).toContainText('the optimization at')
  await expect(removed.nth(1)).toContainText('the frequency at')
  await expect(confirm).toContainText('so its copy goes too')
  await confirm.getByRole('button', { name: 'Remove', exact: true }).click()
  await expect(confirm).toHaveCount(0)
  await expect(page.getByLabel('Node inspector').getByText('No calculations yet.')).toBeVisible()
  const history = await (await page.request.get('/api/history', { params: { record_id: id } })).json()
  expect(history.some((e: { action: string }) => e.action === 'remove_calculations')).toBeTruthy()
})
