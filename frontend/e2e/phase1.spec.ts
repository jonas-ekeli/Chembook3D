import { join } from 'node:path'
import { expect, test, type Page } from '@playwright/test'
import { forgetView } from './demoView.ts'

const E2E_DIR = process.env.E2E_DIR!

const WATER = `3
water
O 0.000000 0.000000 0.117300
H 0.000000 0.757200 -0.469200
H 0.000000 -0.757200 -0.469200
`

async function browseTo(page: Page, folder: string) {
  const dialog = page.getByRole('dialog')
  await expect(dialog.getByRole('list', { name: 'Folders' })).toBeVisible()
  await dialog.getByLabel('Folder path').fill(folder)
  await dialog.getByRole('button', { name: 'Go' }).click()
  await expect(dialog.getByLabel('Folder path')).toHaveValue(folder)
}

async function openInvestigation(page: Page, folder: string) {
  await forgetView(page.request, folder)
  await page.goto('/')
  const open = page.getByRole('button', { name: /^Open/ }).first()
  await open.click()
  await browseTo(page, folder)
  await page.getByRole('dialog').getByRole('button', { name: 'Open', exact: true }).click()
  await expect(page.getByRole('dialog')).toHaveCount(0)
}

test('create an investigation, add a node by hand and edit its coordinates', async ({ page }) => {
  await page.goto('/')
  await page.getByRole('button', { name: 'New investigation…' }).click()
  await browseTo(page, E2E_DIR)
  await page.getByLabel('Investigation name').fill('UI test')
  await page.getByRole('dialog').getByRole('button', { name: 'Create' }).click()
  await expect(page.locator('.investigation')).toHaveText('UI test')

  // FR-NODE-01: a node needs no fields.
  await page.getByRole('button', { name: '+ Add node' }).click()
  const inspector = page.getByLabel('Node inspector')
  await expect(inspector.getByRole('heading', { name: 'Untitled node' })).toBeVisible()
  await inspector.getByLabel('Label').fill('INT1')
  await inspector.getByLabel('Label').press('Enter')
  await expect(inspector.getByRole('heading', { name: 'INT1' })).toBeVisible()

  // FR-NODE-03 and FR-3D-01: paste xyz, save, see the formula and the 3D view.
  await inspector.getByLabel('xyz text').fill(WATER)
  await inspector.getByRole('button', { name: 'Save coordinates' }).click()
  await expect(inspector.getByText('Formula: H2O')).toBeVisible()
  await expect(inspector.getByTestId('viewer3d').locator('canvas')).toBeVisible()

  // T-ID-07: an invalid line is named and nothing is saved.
  await inspector.getByLabel('xyz text').fill('O 0 0 0\nRu 0.0 abc 1.0\n')
  await inspector.getByRole('button', { name: 'Save coordinates' }).click()
  await expect(inspector.getByLabel('xyz errors')).toHaveText('Line 2: coordinates must be numbers')
  await inspector.getByRole('button', { name: 'Discard changes' }).click()
  await expect(inspector.getByText('Formula: H2O')).toBeVisible()

  // T-ID-01: no calculations, so the edit is in place and the history keeps the old geometry.
  await inspector.getByLabel('xyz text').fill(WATER.replace('0.117300', '0.120000'))
  await inspector.getByRole('button', { name: 'Save coordinates' }).click()
  const history = inspector.getByLabel('History')
  await expect(history.getByText('Coordinates changed (3 atoms → 3 atoms)')).toBeVisible()
  await expect(page.getByRole('list', { name: 'Nodes' }).getByRole('listitem')).toHaveCount(1)

  // T-ID-08, D90: with no calculations, saving empty text removes the coordinates, and
  // restoring the history entry brings them back.
  await inspector.getByLabel('xyz text').fill('')
  await inspector.getByRole('button', { name: 'Remove coordinates' }).click()
  await expect(history.getByText('Coordinates removed (3 atoms)')).toBeVisible()
  await expect(inspector.getByText('Formula: H2O')).toHaveCount(0)
  await expect(inspector.getByLabel('xyz text')).toHaveValue('')
  await history
    .getByRole('listitem')
    .filter({ hasText: 'Coordinates removed (3 atoms)' })
    .getByRole('button', { name: 'Restore old value' })
    .click()
  await expect(inspector.getByText('Formula: H2O')).toBeVisible()

  // FR-NODE-07 and FR-HIST-01: status changes are recorded.
  await inspector.getByLabel('Status').selectOption('done')
  await expect(history.getByText('Status: Planned → Done')).toBeVisible()

  // FR-HIST-03: restoring makes a new change.
  await history
    .getByRole('listitem')
    .filter({ hasText: 'Status: Planned → Done' })
    .getByRole('button', { name: 'Restore old value' })
    .click()
  await expect(history.getByText('Status: Done → Planned')).toBeVisible()

  // FR-NODE-02: notes render Markdown.
  await inspector.getByRole('button', { name: 'Edit notes' }).click()
  await inspector.getByLabel('Notes text').fill('Guess from **xTB**')
  await inspector.getByRole('button', { name: 'Save notes' }).click()
  await expect(inspector.locator('.markdown strong')).toHaveText('xTB')

  // FR-NODE-08: tags.
  await inspector.getByLabel('New tag').fill('A1')
  await inspector.getByLabel('New tag').press('Enter')
  await expect(inspector.locator('.tag')).toHaveText(['A1×'])

  // FR-HIST-02: history for the whole investigation.
  await page.getByRole('button', { name: 'History' }).click()
  await expect(page.getByRole('list', { name: 'History' }).getByRole('listitem')).toHaveCount(10)
})

test('editing coordinates of a node with a calculation creates a derived node', async ({ page }) => {
  // T-ID-02, ID-5
  await openInvestigation(page, join(E2E_DIR, 'demo'))
  await page.getByRole('list', { name: 'Nodes' }).getByRole('button', { name: /ethylene opt/ }).click()
  const inspector = page.getByLabel('Node inspector')
  await expect(inspector.getByText('1 calculation', { exact: true })).toBeVisible()
  const original = await inspector.getByLabel('xyz text').inputValue()

  // D90: the coordinates of a node with calculations cannot be removed.
  await inspector.getByLabel('xyz text').fill('')
  await expect(inspector.getByRole('button', { name: 'Save as derived node' })).toBeDisabled()
  await expect(inspector.getByRole('note')).toContainText('cannot be removed')

  await inspector.getByLabel('xyz text').fill(original.replace('0.66750000', '0.67000000'))
  await inspector.getByRole('button', { name: 'Save as derived node' }).click()

  await expect(inspector.getByRole('heading', { name: 'ethylene opt (derived)' })).toBeVisible()
  await expect(page.getByRole('status')).toContainText('derived from “ethylene opt”')
  await expect(inspector.getByText('0 calculations')).toBeVisible()
  await inspector.getByRole('button', { name: 'ethylene opt', exact: true }).click()

  await expect(inspector.getByRole('heading', { name: 'ethylene opt', exact: true })).toBeVisible()
  await expect(inspector.getByLabel('xyz text')).toHaveValue(original)
  await expect(inspector.getByText('Derived nodes:')).toBeVisible()
})

test('xyz can be copied and saved as a file', async ({ page }) => {
  // FR-3D-06
  await openInvestigation(page, join(E2E_DIR, 'demo'))
  await page.getByRole('list', { name: 'Nodes' }).getByRole('button', { name: /water guess/ }).click()
  const inspector = page.getByLabel('Node inspector')
  await inspector.getByRole('button', { name: 'Copy' }).click()
  const copied = await page.evaluate(() => navigator.clipboard.readText())
  // The Windows clipboard turns line ends into CRLF.
  expect(copied.split(/\r?\n/).slice(0, 2)).toEqual(['3', 'water guess'])

  const download = page.waitForEvent('download')
  await inspector.getByRole('link', { name: 'Save .xyz' }).click()
  expect((await download).suggestedFilename()).toBe('water guess.xyz')

  // Primes stay in the file name, so TS1-2, TS1-2' and TS1-2'' save as three files.
  for (const name of ["TS1-2", "TS1-2'", "TS1-2''"]) {
    const node = await (await page.request.post('/api/nodes', { data: { label: name, xyz: WATER } })).json()
    const saved = page.waitForEvent('download')
    await page.evaluate((id) => {
      const link = document.createElement('a')
      link.href = `/api/nodes/${id}/xyz`
      link.download = ''
      link.click()
    }, node.id as string)
    expect((await saved).suggestedFilename()).toBe(`${name}.xyz`)
  }
})

test('deleting a node asks first and lists what goes', async ({ page }) => {
  // NFR-UX-01
  await openInvestigation(page, join(E2E_DIR, 'demo'))
  await page.getByRole('button', { name: /ethylene opt/ }).first().click()
  await page.getByRole('button', { name: 'Delete node' }).click()
  const dialog = page.getByRole('dialog', { name: 'Delete node?' })
  await expect(dialog).toContainText('1 calculation on it')
  await dialog.getByRole('button', { name: 'Cancel' }).click()
  await expect(page.getByLabel('Node inspector')).toBeVisible()

  await page.getByRole('button', { name: /empty planned node/ }).click()
  await page.getByRole('button', { name: 'Delete node' }).click()
  await page.getByRole('dialog').getByRole('button', { name: 'Delete' }).click()
  await expect(page.getByRole('button', { name: /empty planned node/ })).toHaveCount(0)
})

test('the energy unit setting is kept', async ({ page }) => {
  // FR-SET-01
  await openInvestigation(page, join(E2E_DIR, 'demo'))
  await page.getByRole('button', { name: 'Settings' }).click()
  // Reload only once the setting is saved.
  const saved = page.waitForResponse((r) => r.url().endsWith('/api/settings') && r.request().method() === 'PUT')
  await page.getByLabel('Energy unit').selectOption('kJ/mol')
  expect((await saved).ok()).toBe(true)
  await page.getByRole('dialog').getByRole('button', { name: 'Done' }).click()
  await page.reload()
  await page.getByRole('button', { name: 'Settings' }).click()
  await expect(page.getByLabel('Energy unit')).toHaveValue('kJ/mol')
})
