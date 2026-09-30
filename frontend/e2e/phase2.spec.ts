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
  // D77
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
  await expect(files).toContainText('No names here contain “SPQZ”.')
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
