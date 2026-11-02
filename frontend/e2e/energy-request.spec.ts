import { join } from 'node:path'
import { expect, test, type Page } from '@playwright/test'
import { forgetView } from './demoView.ts'

const demo = join(process.env.E2E_DIR!, 'demo')

function deferred() {
  let resolve!: () => void
  const promise = new Promise<void>((done) => { resolve = done })
  return { promise, resolve }
}

async function openProfile(page: Page) {
  await forgetView(page.request, demo)
  // Supply a second choice without importing another scientific fixture. Its request is
  // deliberately refused below; the test concerns ownership of the previous real result.
  await page.route('**/api/energies/options', async (route) => {
    const response = await route.fetch()
    const options = await response.json()
    options.levels = options.levels.map((level: object) => ({ ...level, bsse: true }))
    options.levels.push({ ...options.levels[0], key: 'pending-level', label: 'Pending second level' })
    await route.fulfill({ response, json: options })
  })
  await page.request.post('/api/investigations/open', { data: { folder: demo } })
  await page.goto('/')
  await expect(page.getByRole('group', { name: 'Node T-S0', exact: true })).toBeVisible()
  const energy = page.getByRole('group', { name: 'Energy view' })
  await energy.getByLabel('Energy type').selectOption('E')
  await page.getByRole('button', { name: 'Profile and table' }).click()
  const drawer = page.getByLabel('Energy drawer')
  await drawer.getByLabel('Add branch pathway').selectOption({ label: 'A' })
  await expect(drawer.getByRole('img', { name: 'Energy profile' })).toBeVisible()
  return { drawer, energy }
}

async function exportsUnavailable(page: Page) {
  await page.getByLabel('Energy drawer').getByRole('button', { name: 'Export ▾' }).click()
  for (const name of ['Profile as PNG', 'Profile as SVG', 'Table as CSV']) {
    await expect(page.getByRole('menuitem', { name })).toBeDisabled()
  }
  await page.keyboard.press('Escape')
}

for (const choice of ['type', 'level', 'reference', 'pathways', 'BSSE'] as const) {
  test(`energy results and exports belong to the ${choice} request while pending and after failure`, async ({ page }) => {
    const gate = deferred()
    let requests = 0
    try {
      const { drawer, energy } = await openProfile(page)
      const originalLevel = await energy.getByLabel('Level of theory').inputValue()
      await page.route(/\/api\/energies\/(profile|table)$/, async (route) => {
        requests += 1
        await gate.promise
        await route.fulfill({ status: 500, json: { detail: `Rejected ${choice} request` } })
      })
      if (choice === 'type') await energy.getByLabel('Energy type').selectOption('G')
      if (choice === 'level') await energy.getByLabel('Level of theory').selectOption('pending-level')
      if (choice === 'reference') await drawer.getByLabel('Reference node').selectOption({ label: 'A-S1' })
      if (choice === 'pathways') await drawer.getByLabel('Add branch pathway').selectOption({ label: 'B' })
      if (choice === 'BSSE') await energy.getByLabel('With BSSE').check()
      await expect.poll(() => requests).toBeGreaterThanOrEqual(2)
      await expect(drawer.getByRole('status')).toHaveText('Loading energy results…')
      await expect(drawer.getByRole('img', { name: 'Energy profile' })).toHaveCount(0)
      await drawer.getByRole('button', { name: 'Table', exact: true }).click()
      await expect(drawer.getByRole('table', { name: 'Energy table' })).toHaveCount(0)
      await exportsUnavailable(page)
      gate.resolve()
      await expect(drawer.getByRole('alert')).toHaveText(`Rejected ${choice} request`)
      await expect(drawer.getByRole('table', { name: 'Energy table' })).toHaveCount(0)
      await exportsUnavailable(page)

      // A new successful choice also removes the previous request's error.
      await page.unroute(/\/api\/energies\/(profile|table)$/)
      if (choice === 'level') await energy.getByLabel('Level of theory').selectOption(originalLevel)
      await energy.getByLabel('Energy type').selectOption('H')
      await expect(drawer.getByRole('table', { name: 'Energy table' })).toBeVisible()
      await expect(drawer.getByRole('alert')).toHaveCount(0)
    } finally {
      gate.resolve()
      await page.unrouteAll({ behavior: 'wait' })
      await forgetView(page.request, demo)
    }
  })
}

test('a late profile/table and CSV response cannot replace or export a newer scientific choice', async ({ page }) => {
  const gate = deferred()
  const csvGate = deferred()
  let pending = 0
  let finished = 0
  let csvPending = false
  const downloads: string[] = []
  page.on('download', (download) => downloads.push(download.suggestedFilename()))
  try {
    const { drawer, energy } = await openProfile(page)
    await page.route('**/api/energies/table.csv', async (route) => {
      const response = await route.fetch()
      csvPending = true
      await csvGate.promise
      await route.fulfill({ response })
    })
    await drawer.getByRole('button', { name: 'Export ▾' }).click()
    await page.getByRole('menuitem', { name: 'Table as CSV' }).click()
    await expect.poll(() => csvPending).toBe(true)

    await page.route(/\/api\/energies\/(profile|table)$/, async (route) => {
      if (route.request().postDataJSON().type !== 'G') return route.continue()
      const response = await route.fetch()
      pending += 1
      await gate.promise
      await route.fulfill({ response })
      finished += 1
    })
    await energy.getByLabel('Energy type').selectOption('G')
    await expect.poll(() => pending).toBe(2)
    await energy.getByLabel('Energy type').selectOption('H')
    await drawer.getByRole('button', { name: 'Table', exact: true }).click()
    const table = drawer.getByRole('table', { name: 'Energy table' })
    await expect(table.locator('thead tr').last().locator('th').nth(3)).toHaveText('ΔH')
    // Compare the same DOM representation on both sides: innerText inserts layout tabs and
    // line breaks between table cells, whereas Playwright's text assertion uses textContent.
    const cells = table.locator('th, td')
    const current = await cells.allTextContents()
    gate.resolve()
    csvGate.resolve()
    await expect.poll(() => finished).toBe(2)
    await expect(cells).toHaveText(current)
    expect(downloads).toEqual([])
  } finally {
    gate.resolve()
    csvGate.resolve()
    await page.unrouteAll({ behavior: 'wait' })
    await forgetView(page.request, demo)
  }
})

test('a CSV failure keeps the matching scientific result and figure exports available', async ({ page }) => {
  try {
    const { drawer } = await openProfile(page)
    await page.route('**/api/energies/table.csv', (route) => route.fulfill({ status: 500, json: { detail: 'CSV failed' } }))
    await drawer.getByRole('button', { name: 'Export ▾' }).click()
    await page.getByRole('menuitem', { name: 'Table as CSV' }).click()
    await expect(drawer.getByRole('alert')).toContainText('Request failed (HTTP 500)')
    await expect(drawer.getByRole('img', { name: 'Energy profile' })).toBeVisible()
    await drawer.getByRole('button', { name: 'Table', exact: true }).click()
    await expect(drawer.getByRole('table', { name: 'Energy table' })).toBeVisible()
    await drawer.getByRole('button', { name: 'Export ▾' }).click()
    await expect(page.getByRole('menuitem', { name: 'Profile as SVG' })).toBeEnabled()
    await page.keyboard.press('Escape')
  } finally {
    await page.unrouteAll({ behavior: 'wait' })
    await forgetView(page.request, demo)
  }
})
