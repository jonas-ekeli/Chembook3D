import { join } from 'node:path'
import { expect, test, type Page } from '@playwright/test'

const E2E_DIR = process.env.E2E_DIR!
// Synthetic --Link1-- chains with a custom basis set, written by global-setup.ts
const CUSTOM = join(E2E_DIR, 'gaussian')
// TS searches that read the basis set from another job's checkpoint (D104)
const CHECKPOINT = join(E2E_DIR, 'checkpoint')

async function openFolder(page: Page, folder: string, file: RegExp) {
  await page.getByRole('button', { name: 'Import file…' }).click()
  const dialog = page.getByRole('dialog')
  // wait for the first listing, so it cannot replace the folder typed below
  await expect(dialog.getByLabel('Folder path')).not.toHaveValue('')
  await dialog.getByLabel('Folder path').fill(folder)
  await dialog.getByRole('button', { name: 'Go' }).click()
  await expect(dialog.getByRole('button', { name: file })).toBeVisible()
  await dialog.getByRole('button', { name: 'Import this folder…' }).click()
  return page.getByRole('dialog', { name: 'Import a folder of results' })
}

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

test('a folder of results is imported at once', async ({ page }) => {
  // D97, T-IMP-12
  await newInvestigation(page, 'Batch test')
  await page.getByRole('button', { name: 'Import file…' }).click()
  let dialog = page.getByRole('dialog')
  // wait for the first listing, so it cannot replace the folder typed below
  await expect(dialog.getByLabel('Folder path')).not.toHaveValue('')
  await dialog.getByLabel('Folder path').fill(CUSTOM)
  await dialog.getByRole('button', { name: 'Go' }).click()
  await expect(dialog.getByRole('button', { name: /MeI_TS_QZ\.out/ })).toBeVisible()
  await dialog.getByRole('button', { name: 'Import this folder…' }).click()

  dialog = page.getByRole('dialog', { name: 'Import a folder of results' })
  const files = dialog.getByRole('table', { name: 'Files' })
  await expect(files.getByRole('row', { name: 'MeI_TS_QZ.out' })).toContainText('Adds to the node from MeI_TS.out')
  await expect(files.getByRole('row', { name: 'MeI_TS_QZ.out' })).toContainText('same geometry')
  await expect(files.getByRole('row', { name: 'MeI_min.out' })).toContainText('New node “MeI_min”')
  const importButton = dialog.getByRole('button', { name: 'Import 3 files' })
  await expect(importButton).toBeDisabled()

  // Each custom basis set and the dispersion are named once for the whole folder.
  const basis = dialog.getByLabel('Name for the custom basis set')
  await expect(basis).toHaveCount(2)
  for (const [n, name] of ['modDZ', 'modQZ'].entries()) {
    await basis.nth(n).fill(name)
    await basis.nth(n).press('Enter')
  }
  await dialog.getByLabel('Name for the custom dispersion').fill('GD3MBJ')
  await dialog.getByLabel('Name for the custom dispersion').press('Enter')
  await expect(dialog.getByRole('list', { name: 'Needed before import' })).toHaveCount(0)

  // A file can be left out.
  // The box shows the server's answer, so it changes once the preview is back.
  await dialog.getByLabel('Import MeI_min.out').click()
  await expect(dialog.getByLabel('Import MeI_min.out')).not.toBeChecked()
  await expect(dialog.getByRole('button', { name: 'Import 2 files' })).toBeEnabled()
  await dialog.getByRole('button', { name: 'Import 2 files' }).click()
  await expect(page.getByRole('dialog')).toHaveCount(0)
  await expect(page.getByText(/Imported 2 files from/)).toBeVisible()

  const inspector = page.getByLabel('Node inspector')
  await expect(inspector.getByRole('heading', { name: 'MeI_TS' })).toBeVisible()
  await expect(inspector.getByRole('list', { name: 'Calculations' })).toContainText('PBEPBE-GD3MBJ/modQZ')
  await page.getByRole('button', { name: 'History' }).click()
  await expect(page.getByText(/Imported 2 files from .*: MeI_TS\.out, MeI_TS_QZ\.out/)).toBeVisible()
})

test('files that name no basis set get one for the whole folder', async ({ page }) => {
  // D104, T-IMP-20
  await newInvestigation(page, 'Checkpoint basis test')
  const dialog = await openFolder(page, CHECKPOINT, /TS_b_chk\.out/)
  const files = dialog.getByRole('table', { name: 'Files' })
  const box = dialog.getByRole('region', { name: 'Files without a basis set' })
  await expect(box.getByRole('heading')).toHaveText('2 files name no basis set')
  await expect(files.getByRole('row', { name: 'TS_a_chk.out' })).toContainText('no basis set')

  await box.getByLabel('Basis set for files without one').fill('def2-SVP')
  await box.getByRole('button', { name: 'Apply to all' }).click()
  await expect(files.getByRole('row', { name: 'TS_a_chk.out' })).toContainText('PBEPBE-GD3BJ/def2-SVP')
  await expect(files.getByRole('row', { name: 'TS_b_chk.out' })).toContainText('PBEPBE-GD3BJ/def2-SVP')
  await expect(files.getByRole('row', { name: 'TS_a_chk.out' })).not.toContainText('no basis set')

  // One file can have its own.
  await dialog.getByLabel('Basis set for TS_b_chk.out').fill('def2-TZVP')
  await dialog.getByLabel('Basis set for TS_b_chk.out').press('Enter')
  await expect(files.getByRole('row', { name: 'TS_b_chk.out' })).toContainText('PBEPBE-GD3BJ/def2-TZVP')

  await dialog.getByRole('button', { name: 'Import 2 files' }).click()
  await expect(page.getByRole('dialog')).toHaveCount(0)
  await expect(page.getByText(/Imported 2 files from/)).toBeVisible()
  await page.getByText('TS_a_chk', { exact: true }).first().click()
  const calculations = page.getByLabel('Node inspector').getByRole('list', { name: 'Calculations' })
  await expect(calculations).toContainText('PBEPBE-GD3BJ/def2-SVP')
  await expect(calculations.getByText('basis given').first()).toBeVisible()
})
