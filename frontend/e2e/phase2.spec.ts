import { readFile } from 'node:fs/promises'
import { join, resolve } from 'node:path'
import { expect, test, type Page } from '@playwright/test'

const E2E_DIR = process.env.E2E_DIR!
const FIXTURES = resolve(import.meta.dirname, '..', '..', 'tests', 'fixtures', 'gaussian')
// Synthetic --Link1-- chains with a custom basis set, written by global-setup.ts
const CUSTOM = join(E2E_DIR, 'gaussian')

async function newInvestigation(page: Page, name: string) {
  await page.goto('/')
  const button = page.getByRole('button', { name: /^New/ }).first()
  await button.click()
  const dialog = page.getByRole('dialog')
  await expect(dialog.getByRole('list', { name: 'Folders' })).toBeVisible()
  await dialog.getByLabel('Folder path').fill(E2E_DIR)
  await dialog.getByRole('button', { name: 'Go' }).click()
  await expect(dialog.getByLabel('Folder path')).toHaveValue(E2E_DIR)
  await dialog.getByLabel('Investigation name').fill(name)
  await dialog.getByRole('button', { name: 'Create' }).click()
  await expect(page.locator('.investigation')).toHaveText(name)
}

async function nameCustom(dialog: ReturnType<Page['getByRole']>, label: string, value: string) {
  const field = dialog.getByLabel(label)
  await field.fill(value)
  await field.press('Enter')
}

function elementsRow(dialog: ReturnType<Page['getByRole']>, element: string) {
  return dialog.getByRole('row').filter({ has: dialog.page().getByLabel(`Include ${element}`, { exact: true }) })
}

test('import a Gaussian TS job, then a single point onto it', async ({ page }) => {
  await newInvestigation(page, 'Import test')

  // WF-04, FR-IMP-05: the preview shows every step and asks for the custom names first.
  await page.getByRole('button', { name: 'Import file…' }).click()
  const dialog = page.getByRole('dialog')
  await dialog.getByLabel('Upload a file').setInputFiles(join(CUSTOM, 'MeI_TS.out'))
  const steps = dialog.getByRole('table', { name: 'Job steps' })
  await expect(steps.getByRole('row')).toHaveCount(6)
  await expect(steps.getByRole('row').nth(4)).toContainText('TS optimization')
  await expect(dialog.getByRole('button', { name: 'Import', exact: true })).toBeDisabled()
  await nameCustom(dialog, 'Name for the custom basis set', 'modDZ')
  await nameCustom(dialog, 'Name for the custom dispersion', 'GD3MBJ')
  await expect(steps.getByRole('row').nth(5)).toContainText('Gaussian PBEPBE-GD3MBJ/modDZ')
  await dialog.getByLabel('Origin device').fill('cluster')
  await dialog.getByLabel('Origin device').blur()
  await dialog.getByRole('button', { name: 'Import', exact: true }).click()

  const inspector = page.getByLabel('Node inspector')
  await expect(inspector.getByRole('heading', { name: 'MeI_TS' })).toBeVisible()
  await expect(inspector.getByText('Formula: CH3I')).toBeVisible()
  const calculations = inspector.getByRole('list', { name: 'Calculations' })
  await expect(calculations.getByRole('listitem')).toHaveCount(2)
  await expect(calculations).toContainText('1 imag.')

  // FR-FILE-02: the file's origin is kept with the copy.
  await calculations.getByRole('button', { name: /Frequency/ }).click()
  await expect(inspector.getByLabel('Device or server')).toHaveValue('cluster')
  await expect(inspector.getByRole('link', { name: 'Download copy' })).toBeVisible()

  // T-IMP-04 with a separate file: the composite level SP // opt; the dispersion is recognised.
  await inspector.getByRole('button', { name: 'Import onto node…' }).click()
  await dialog.getByLabel('Upload a file').setInputFiles(join(CUSTOM, 'MeI_TS_QZ.out'))
  await expect(dialog.getByText('recognised as GD3MBJ')).toBeVisible()
  await nameCustom(dialog, 'Name for the custom basis set', 'modQZ')
  await expect(dialog.getByRole('table', { name: 'Job steps' })).toContainText(
    'Gaussian PBEPBE-GD3MBJ/modQZ // Gaussian PBEPBE-GD3MBJ/modDZ',
  )
  await dialog.getByRole('button', { name: 'Import', exact: true }).click()
  await expect(calculations.getByRole('listitem')).toHaveCount(3)
  await expect(calculations).toContainText('modQZ // Gaussian PBEPBE-GD3MBJ/modDZ')
  await expect(page.getByRole('list', { name: 'Nodes' }).getByRole('listitem')).toHaveCount(1)
})

test('a saved basis set can be inspected and downloaded', async ({ page }) => {
  // D94
  await newInvestigation(page, 'Basis set test')
  await page.getByRole('button', { name: 'Import file…' }).click()
  const importer = page.getByRole('dialog')
  await importer.getByLabel('Upload a file').setInputFiles(join(CUSTOM, 'MeI_TS.out'))
  await nameCustom(importer, 'Name for the custom basis set', 'modDZ')
  await nameCustom(importer, 'Name for the custom dispersion', 'GD3MBJ')
  await importer.getByRole('button', { name: 'Import', exact: true }).click()
  const inspector = page.getByLabel('Node inspector')
  await expect(inspector.getByRole('heading', { name: 'MeI_TS' })).toBeVisible()

  // from the calculation: its custom basis set opens in the dialog
  await inspector.getByRole('list', { name: 'Calculations' }).getByRole('button', { name: /Frequency/ }).click()
  await inspector.getByRole('button', { name: 'modDZ', exact: true }).click()
  let dialog = page.getByRole('dialog', { name: 'Custom basis sets' })
  const elements = dialog.getByRole('table', { name: 'Elements' })
  await expect(elements.getByRole('row')).toHaveCount(4)
  await expect(elements.getByRole('row').nth(3)).toContainText('(3s2p)/[1s1p]')
  await expect(elements.getByRole('row').nth(3)).toContainText('28 core electrons')
  await dialog.getByRole('button', { name: 'Close' }).click()

  // from the top bar, with every function and the file
  await page.getByRole('button', { name: 'Basis sets' }).click()
  dialog = page.getByRole('dialog', { name: 'Custom basis sets' })
  await expect(dialog.getByRole('list', { name: 'Saved basis sets' })).toContainText('H C I')
  await elementsRow(dialog, 'I').getByRole('button', { name: 'Show functions' }).click()
  await expect(dialog.getByLabel('I functions')).toContainText('I-ECP     3     28')
  await dialog.getByLabel('Include I').uncheck()
  await expect(dialog).toContainText('H C; use with Gen.')
  const [file] = await Promise.all([
    page.waitForEvent('download'),
    dialog.getByRole('button', { name: 'Download Gaussian file (.gbs)' }).click(),
  ])
  expect(file.suggestedFilename()).toBe('modDZ.gbs')
  const text = await readFile(await file.path(), 'utf-8')
  expect(text).toContain('! Elements: H C\n')
  expect(text).not.toContain('ECP')

  // the node link selects the node and closes the dialog
  await dialog.getByRole('button', { name: 'MeI_TS' }).click()
  await expect(page.getByRole('dialog')).toHaveCount(0)
  await expect(inspector.getByRole('heading', { name: 'MeI_TS' })).toBeVisible()
})

test('cancelling an import writes nothing', async ({ page }) => {
  // T-IMP-07
  await newInvestigation(page, 'Cancel test')
  await page.getByRole('button', { name: 'Import file…' }).click()
  const dialog = page.getByRole('dialog')
  await dialog.getByLabel('Upload a file').setInputFiles(join(FIXTURES, 'aminationTS-full-unfrz-c1_sp_tzpop.log'))
  await expect(dialog.getByLabel('Import preview')).toBeVisible()
  await dialog.getByRole('button', { name: 'Cancel' }).click()
  await expect(page.getByRole('dialog')).toHaveCount(0)
  await expect(page.getByText('No nodes yet.')).toBeVisible()
  await page.getByRole('button', { name: 'History' }).click()
  await expect(page.getByText('No changes recorded yet.')).toBeVisible()
})

test('a late preview answer does not undo a name given after it', async ({ page }) => {
  // Naming the basis set and then the dispersion sends two previews; if the first answers last,
  // the dispersion used to show as unnamed again (seen on the Windows runners).
  await newInvestigation(page, 'Preview order test')
  await page.getByRole('button', { name: 'Import file…' }).click()
  const dialog = page.getByRole('dialog')
  await dialog.getByLabel('Upload a file').setInputFiles(join(CUSTOM, 'MeI_TS.out'))
  const steps = dialog.getByRole('table', { name: 'Job steps' })
  await expect(steps.getByRole('row')).toHaveCount(6)
  let first = true
  await page.route('**/preview', async (route) => {
    if (first) {
      first = false
      await new Promise((r) => setTimeout(r, 1000))
    }
    await route.continue()
  })
  let answers = 0
  page.on('response', (response) => {
    if (response.url().endsWith('/preview')) answers += 1
  })
  await nameCustom(dialog, 'Name for the custom basis set', 'modDZ')
  await nameCustom(dialog, 'Name for the custom dispersion', 'GD3MBJ')
  await expect.poll(() => answers).toBe(2)
  await page.waitForTimeout(500) // let the app handle the late answer
  await expect(steps.getByRole('row').nth(5)).toContainText('Gaussian PBEPBE-GD3MBJ/modDZ', { timeout: 1000 })
  await dialog.getByRole('button', { name: 'Cancel' }).click()
})

test('the file browser starts where the last file was picked and filters by name', async ({ page }) => {
  // D78
  await newInvestigation(page, 'Browser test')
  await page.getByRole('button', { name: 'Import file…' }).click()
  const dialog = page.getByRole('dialog')
  const path = dialog.getByLabel('Folder path')
  const files = dialog.getByRole('list', { name: 'Files' })
  // wait for the first listing, so it cannot replace the folder typed below
  await expect(path).not.toHaveValue('')
  await path.fill(CUSTOM)
  await dialog.getByRole('button', { name: 'Go' }).click()
  await expect(files.getByRole('listitem')).toHaveCount(3)

  const filter = dialog.getByLabel('Filter by name')
  await filter.fill('qz')
  await expect(files.getByRole('listitem')).toHaveCount(1)
  await expect(files).toContainText('MeI_TS_QZ.out')
  await filter.fill('SPQZ')
  await expect(files).toContainText('No names here match “SPQZ”.')
  // with * or ? the pattern must match the whole name, as in a Linux shell
  await filter.fill('mei_*.OUT')
  await expect(files.getByRole('listitem')).toHaveCount(3)
  await filter.fill('*_qz*')
  await expect(files.getByRole('listitem')).toHaveCount(1)
  await filter.fill('MeI_TS?out')
  await expect(files.getByRole('listitem')).toHaveCount(1)
  await expect(files).toContainText('MeI_TS.out')
  await filter.fill('MeI')
  await expect(files.getByRole('listitem')).toHaveCount(3)
  await filter.fill('MeI*')
  await expect(files.getByRole('listitem')).toHaveCount(3)
  await filter.fill('*MeI')
  await expect(files).toContainText('No names here match “*MeI”.')
  // Escape clears the filter and leaves the dialog open
  await filter.press('Escape')
  await expect(filter).toHaveValue('')
  await expect(files.getByRole('listitem')).toHaveCount(3)

  await filter.fill('QZ')
  await files.getByRole('button', { name: /MeI_TS_QZ\.out/ }).click()
  await expect(dialog.getByLabel('Import preview')).toBeVisible()
  await dialog.getByRole('button', { name: 'Cancel' }).click()

  // Opened again, it starts in that folder with an empty filter.
  await page.getByRole('button', { name: 'Import file…' }).click()
  await expect(path).toHaveValue(CUSTOM)
  await expect(filter).toHaveValue('')
  await expect(files.getByRole('listitem')).toHaveCount(3)
  await dialog.getByRole('button', { name: 'Cancel' }).click()
})

test('an import is undone from the node history', async ({ page }) => {
  // D102, T-IMP-18
  await newInvestigation(page, 'Undo test')
  await page.getByRole('button', { name: 'Import file…' }).click()
  const dialog = page.getByRole('dialog')
  await dialog.getByLabel('Upload a file').setInputFiles(join(FIXTURES, 'aminationTS-full-unfrz-c1.log'))
  await expect(dialog.getByLabel('Import preview')).toBeVisible()
  await dialog.getByRole('button', { name: 'Import', exact: true }).click()
  const inspector = page.getByLabel('Node inspector')
  const calculations = inspector.getByRole('list', { name: 'Calculations' })
  await expect(calculations.getByRole('listitem')).toHaveCount(2)

  await inspector.getByRole('button', { name: 'Import onto node…' }).click()
  await dialog.getByLabel('Upload a file').setInputFiles(join(FIXTURES, 'aminationTS-full-unfrz-c1_sp_tzpop.log'))
  await expect(dialog.getByLabel('Import preview')).toBeVisible()
  await dialog.getByRole('button', { name: 'Import', exact: true }).click()
  await expect(calculations.getByRole('listitem')).toHaveCount(3)

  // The newest line is the single point's; undoing it leaves the TS job as it was.
  const history = inspector.getByRole('list', { name: 'History' })
  await history.getByRole('button', { name: 'Undo import' }).first().click()
  const undo = page.getByRole('dialog', { name: 'Undo import?' })
  await expect(undo).toContainText('Removes 1 calculation (single point)')
  await undo.getByRole('button', { name: 'Undo import' }).click()
  await expect(undo).toHaveCount(0)
  await expect(calculations.getByRole('listitem')).toHaveCount(2)
  await expect(history).toContainText('Undid the import of aminationTS-full-unfrz-c1_sp_tzpop.log')

  // Undoing the import that made the node deletes it.
  await history.getByRole('button', { name: 'Undo import' }).first().click()
  await expect(undo).toContainText('Deletes the node it made: “aminationTS-full-unfrz-c1”')
  await undo.getByRole('button', { name: 'Undo import' }).click()
  await expect(page.getByText('No nodes yet.')).toBeVisible()
  await page.getByRole('button', { name: 'History' }).click()
  await expect(page.getByRole('list', { name: 'History' })).toContainText(
    'Undid the import of aminationTS-full-unfrz-c1.log',
  )
})
