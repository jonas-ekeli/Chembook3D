import { join, resolve } from 'node:path'
import { expect, test, type Page } from '@playwright/test'

const E2E_DIR = process.env.E2E_DIR!
const FIXTURES = resolve(import.meta.dirname, '..', '..', 'tests', 'fixtures')

async function openDemo(page: Page) {
  await page.goto('/')
  await page.getByRole('button', { name: /^Open/ }).first().click()
  const dialog = page.getByRole('dialog')
  await expect(dialog.getByRole('list', { name: 'Folders' })).toBeVisible()
  await dialog.getByLabel('Folder path').fill(join(E2E_DIR, 'demo'))
  await dialog.getByRole('button', { name: 'Go' }).click()
  await dialog.getByRole('button', { name: 'Open', exact: true }).click()
  await expect(page.getByRole('dialog')).toHaveCount(0)
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

test('the resume overview lists where the investigation stands and opens each item', async ({ page }) => {
  // T-UI-01, FR-OV-01, WF-10: shown on opening, with nothing selected.
  await openDemo(page)
  const overview = page.getByLabel('Overview', { exact: true })
  await expect(overview.getByRole('heading', { name: 'Overview' })).toBeVisible()

  const branches = overview.getByRole('region', { name: 'Branch status' })
  await expect(
    branches.getByRole('row').filter({ has: page.getByRole('button', { name: 'A', exact: true }) }),
  ).toContainText('1 planned')
  await expect(branches.getByRole('row').filter({ hasText: 'No branch' })).toBeVisible()

  const planned = overview.getByRole('list', { name: 'Planned' })
  await expect(planned).toContainText('A-S2')
  await expect(planned).toContainText('T-S0 → A-S1')
  const direct = overview.getByRole('list', {
    name: 'Direct connections (no TS)',
  })
  await expect(direct).toContainText('A-S1 → A-S3')
  await expect(overview.getByRole('region', { name: 'Step notes' })).toContainText('[2+2] TS')
  await expect(overview.getByRole('region', { name: 'Recent changes' }).getByRole('listitem').first()).toBeVisible()

  // Every item opens its record.
  await planned.getByRole('button', { name: 'A-S2', exact: true }).click()
  await expect(page.getByLabel('Node inspector').getByRole('heading', { name: 'A-S2' })).toBeVisible()

  await page.getByRole('navigation', { name: 'Views' }).getByRole('button', { name: 'Overview' }).click()
  await direct.getByRole('button', { name: 'A-S1 → A-S3' }).click()
  await expect(page.getByLabel('Transition inspector')).toBeVisible()

  await page.getByRole('navigation', { name: 'Views' }).getByRole('button', { name: 'Overview' }).click()
  await branches.getByRole('button', { name: 'B', exact: true }).click()
  await expect(page.getByLabel('Branch inspector').getByRole('heading', { name: 'B' })).toBeVisible()

  // Recent changes filter by date.
  await page.getByRole('navigation', { name: 'Views' }).getByRole('button', { name: 'Overview' }).click()
  await overview.getByLabel('Changes since').fill('2999-01-01')
  await expect(overview.getByText('No changes recorded yet.')).toBeVisible()
})

test('import a CREST ensemble as a group and remove a member', async ({ page }) => {
  // T-IMP-09, FR-IMP-10, FR-IMP-11
  await newInvestigation(page, 'CREST test')
  await page.getByRole('button', { name: 'Import file…' }).click()
  const dialog = page.getByRole('dialog')
  await dialog.getByLabel('Upload a file').setInputFiles(join(FIXTURES, 'crest', 'crest_conformers.xyz'))

  const conformers = dialog.getByRole('table', { name: 'Conformers' })
  await expect(conformers.getByRole('row')).toHaveCount(75)
  await expect(dialog.getByText('(10 of 74, 14 atoms each)')).toBeVisible()
  await dialog.getByLabel('Number of conformers to keep').fill('4')
  await dialog.getByRole('button', { name: 'Tick' }).click()
  await expect(dialog.getByText('(4 of 74, 14 atoms each)')).toBeVisible()
  const ticked = conformers.getByRole('checkbox', { checked: true })
  await expect(ticked).toHaveCount(4)
  // The box follows the preview, which comes back from the backend: click, then wait for it.
  await ticked.last().click()
  await expect(dialog.getByText('(3 of 74, 14 atoms each)')).toBeVisible()

  await dialog.getByLabel('Charge').fill('0')
  await dialog.getByLabel('Charge').blur()
  await dialog.getByLabel('Multiplicity').fill('1')
  await dialog.getByLabel('Multiplicity').blur()
  await dialog.getByLabel('Node label').fill('ens')
  await dialog.getByLabel('Node label').blur()
  await dialog.getByRole('button', { name: 'Import', exact: true }).click()
  await expect(page.getByRole('dialog')).toHaveCount(0)

  const group = page.getByLabel('Group inspector')
  await expect(group.getByRole('heading', { name: 'ens' })).toBeVisible()
  await expect(group.getByRole('heading', { name: 'Members (3)' })).toBeVisible()

  // Removing a member deletes its node, after a confirmation.
  await group
    .getByRole('button', { name: /^Remove ens-\d+ from the group$/ })
    .first()
    .click()
  await expect(page.getByRole('dialog')).toContainText('is removed from the group and deleted, with its 1 calculation.')
  await page.getByRole('dialog').getByRole('button', { name: 'Remove and delete' }).click()
  await expect(group.getByRole('heading', { name: 'Members (2)' })).toBeVisible()
})
