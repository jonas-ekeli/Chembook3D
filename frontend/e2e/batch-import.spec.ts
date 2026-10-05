import { join } from 'node:path'
import { expect, test, type Page } from '@playwright/test'

const E2E_DIR = process.env.E2E_DIR!
// Synthetic --Link1-- chains with a custom basis set, written by global-setup.ts
const CUSTOM = join(E2E_DIR, 'gaussian')

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
