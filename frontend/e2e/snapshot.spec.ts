import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { pathToFileURL } from 'node:url'
import { expect, test, type Locator, type Page } from '@playwright/test'

const E2E_DIR = process.env.E2E_DIR!

async function openDemo(page: Page) {
  await page.request.put('/api/settings', { data: { energy_unit: 'kcal/mol' } })
  await page.goto('/')
  await page.getByRole('button', { name: /^Open/ }).first().click()
  const dialog = page.getByRole('dialog')
  await expect(dialog.getByRole('list', { name: 'Folders' })).toBeVisible()
  await dialog.getByLabel('Folder path').fill(join(E2E_DIR, 'demo'))
  await dialog.getByRole('button', { name: 'Go' }).click()
  await dialog.getByRole('button', { name: 'Open', exact: true }).click()
  await expect(page.getByRole('dialog')).toHaveCount(0)
  await expect(canvasNode(page, 'T-S0')).toBeVisible()
}

function canvasNode(page: Page, label: string): Locator {
  return page.getByRole('group', { name: `Node ${label}`, exact: true })
}

test('a read-only copy opens from disk with no server and changes nothing', async ({ page, context }) => {
  // T-SHARE-01, T-SHARE-02; FR-SHARE-01…04, D79, A30
  await openDemo(page)
  const before = await (await page.request.get('/api/canvas')).json()
  await canvasNode(page, 'T-S0').click()
  await page.getByLabel('Node inspector').getByRole('button', { name: 'Use as energy reference' }).click()

  // With no pathway in the drawer, the copy has one per branch.
  await page.getByRole('button', { name: 'Share read-only copy…' }).click()
  const dialog = page.getByRole('dialog', { name: 'Share a read-only copy' })
  await expect(dialog).toContainText('one pathway per branch')
  const download = page.waitForEvent('download')
  await dialog.getByRole('button', { name: 'Save file' }).click()
  const file = await download
  expect(file.suggestedFilename()).toMatch(/^Demo investigation read-only \d{4}-\d{2}-\d{2}\.html$/)
  const path = join(E2E_DIR, 'copy.html')
  await file.saveAs(path)
  await expect(dialog).toHaveCount(0)
  // FR-SHARE-04: no folder of this computer is in the file.
  const text = readFileSync(path, 'utf8')
  expect(text).not.toContain(E2E_DIR)
  expect(text).not.toContain(E2E_DIR.replaceAll('\\', '/'))

  // Opened from disk, in a page that is refused every request but the file itself.
  const shared = await context.newPage()
  const requests: string[] = []
  shared.on('request', (request) => requests.push(request.url()))
  const errors: string[] = []
  shared.on('pageerror', (error) => errors.push(error.message))
  await shared.goto(pathToFileURL(path).href)
  await expect(shared).toHaveTitle(/Demo investigation/)
  await expect(shared.getByText('Read-only copy', { exact: true })).toBeVisible()
  // The reference chosen when exporting is kept; energy mode shows ΔG from it.
  await expect(canvasNode(shared, 'A-S2')).toContainText('ΔG 14.80')
  await expect(canvasNode(shared, 'B-S3')).toContainText('ΔG -6.00')
  await expect(shared.locator('.edge-energy', { hasText: 'ΔG 18.00' })).toBeVisible()

  // Nothing edits: no add, import, delete or save buttons, no editable fields.
  for (const name of ['+ Add node', 'Import file…', 'Settings', 'Share read-only copy…']) {
    await expect(shared.getByRole('button', { name })).toHaveCount(0)
  }
  await canvasNode(shared, 'A-S2').click()
  const inspector = shared.getByLabel('Node inspector')
  await expect(inspector.getByRole('heading', { name: 'A-S2' })).toBeVisible()
  await expect(inspector.getByRole('button', { name: /Delete|Import|Edit/ })).toHaveCount(0)
  await expect(inspector.getByRole('combobox')).toHaveCount(0)
  await expect(inspector.getByTestId('viewer3d')).toBeVisible()
  // The 3D view pops out and goes back as in the app (D82).
  await inspector.getByRole('button', { name: 'Pop out the 3D view' }).click()
  const popout = shared.getByRole('dialog', { name: '3D view window' })
  await expect(popout.getByTestId('viewer3d')).toBeVisible()
  await popout.getByRole('button', { name: 'Put back', exact: true }).click()
  await expect(popout).toHaveCount(0)
  await expect(inspector.getByTestId('viewer3d')).toBeVisible()
  await expect(inspector.getByRole('list', { name: 'Calculations' }).getByRole('listitem')).toHaveCount(2)
  await inspector.getByRole('list', { name: 'Calculations' }).getByRole('button').first().click()
  await expect(inspector).toContainText('E(SCF)')

  // Energy type and the profile of each branch, computed when exporting.
  const view = shared.getByRole('group', { name: 'Energy view' })
  await view.getByLabel('Energy type').selectOption('E')
  await expect(shared.locator('.edge-energy', { hasText: 'ΔE 18.00' })).toBeVisible()
  await view.getByLabel('Energy type').selectOption('G')
  await shared.getByRole('button', { name: 'Profile and table' }).click()
  const drawer = shared.getByLabel('Energy drawer')
  await expect(drawer.getByRole('group', { name: 'Pathway B' })).toContainText('T-S0 → B-S1 → B-S2 → B-S3')
  await expect(drawer.getByRole('img', { name: 'Energy profile' })).toContainText('-6.00')
  await drawer.getByRole('button', { name: 'Energy table' }).click()
  // Branch A's pathway stops where it forks (EN-10), so only branch B reaches B-S3.
  const table = drawer.getByRole('table', { name: 'Energy table' })
  await expect(table.getByRole('row').filter({ hasText: 'B-S3' })).toContainText('-6.00')
  const csv = shared.waitForEvent('download')
  await drawer.getByRole('button', { name: 'Export CSV' }).click()
  expect(readFileSync(await (await csv).path(), 'utf8')).toContain('B-S3')

  // Structure mode draws every card from the copy.
  await shared.getByRole('button', { name: 'Structure', exact: true }).click()
  await expect(canvasNode(shared, 'A-S2').getByRole('img', { name: 'Structure' })).toBeVisible()

  expect(errors).toEqual([])
  expect(requests.filter((url) => !/^(file|data|blob):/.test(url))).toEqual([])
  // The investigation itself is unchanged.
  expect(await (await page.request.get('/api/canvas')).json()).toEqual(before)
})

test('the copy holds the pathways in the drawer', async ({ page }) => {
  // A30
  await openDemo(page)
  await page.getByRole('button', { name: 'Profile and table' }).click()
  const drawer = page.getByLabel('Energy drawer')
  await drawer.getByLabel('Add branch pathway').selectOption({ label: 'B' })
  await expect(drawer.getByRole('group', { name: 'Pathway B' })).toContainText('T-S0 → B-S1 → B-S2 → B-S3')
  await page.getByRole('button', { name: 'Share read-only copy…' }).click()
  const dialog = page.getByRole('dialog', { name: 'Share a read-only copy' })
  await expect(dialog).toContainText('the pathway in “Profile and table”')
  const download = page.waitForEvent('download')
  await dialog.getByRole('button', { name: 'Save file' }).click()
  const path = join(E2E_DIR, 'copy-b.html')
  await (await download).saveAs(path)

  await page.goto(pathToFileURL(path).href)
  await page.getByRole('button', { name: 'Profile and table' }).click()
  const shared = page.getByLabel('Energy drawer')
  await expect(shared.getByRole('group', { name: /^Pathway / })).toHaveCount(1)
  await expect(shared.getByRole('group', { name: 'Pathway B' })).toBeVisible()
  // Any node on the pathway can be the reference.
  await shared.getByLabel('Reference node').selectOption({ label: 'B-S1' })
  await expect(canvasNode(page, 'B-S1')).toBeVisible()
  await page.getByRole('button', { name: 'Energy', exact: true }).click()
  await expect(canvasNode(page, 'B-S1')).toContainText('ΔG 0.00')
})
