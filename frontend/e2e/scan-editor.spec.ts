import { mkdirSync, readFileSync, writeFileSync } from 'node:fs'
import { join, resolve } from 'node:path'
import { expect, test, type Page } from '@playwright/test'
import { startPage } from './appBar.ts'

const E2E_DIR = process.env.E2E_DIR!
const FIXTURES = resolve(import.meta.dirname, '..', '..', 'tests', 'fixtures')

async function newInvestigation(page: Page, name: string) {
  await startPage(page)
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

test('the scan editor reverses, trims, splits and saves a scan, with undo', async ({ page }) => {
  // D135, T-UI-37
  test.slow()
  await newInvestigation(page, 'Scan editor')
  await page.getByRole('button', { name: 'Scan editor', exact: true }).click()
  const editor = page.getByRole('dialog', { name: 'Scan editor' })
  await expect(editor.getByText('No scans yet')).toBeVisible()

  await editor.getByRole('button', { name: 'Add files…' }).click()
  // wait for the first listing, so it cannot replace the folder typed below
  await expect(editor.getByLabel('Folder path')).not.toHaveValue('')
  await editor.getByLabel('Folder path').fill(join(FIXTURES, 'gaussian'))
  await editor.getByRole('button', { name: 'Go' }).click()
  await editor.getByRole('button', { name: /dvb_scan_relaxed\.log/ }).click()

  // The file's scan is listed with the coordinate it names, and starts the path.
  const scans = editor.getByRole('list', { name: 'Scans in the editor' })
  await expect(scans).toContainText('dvb_scan_relaxed.log')
  await expect(scans).toContainText('dihedral C3–C4–C9–C10, 180.0 → 540.0 °, 13 points')
  await expect(scans).toContainText('Gaussian B3LYP/STO-3G')
  await expect(editor.getByText('dvb_scan_relaxed.log starts the path.')).toBeVisible()
  await expect(editor.getByRole('img', { name: 'Scans by coordinate' })).toBeVisible()
  const slider = editor.getByLabel('Frame', { exact: true })
  const shown = editor.getByLabel('Frame shown')
  await expect(slider).toHaveAttribute('max', '12')
  await slider.fill('0')
  await expect(shown).toContainText('Frame 1 of 13 · dvb_scan_relaxed.log, step 1, point 1 · 180.0 °')
  // in the energy unit an earlier test may have chosen
  await expect(editor.getByLabel('Top of the path')).toContainText(
    /Top at frame 10: (5\.47 kcal\/mol|22\.88 kJ\/mol) above the first frame/,
  )

  // Reversed, the path starts at the scan's last point.
  const piece = editor.getByLabel('Piece 1')
  await piece.getByRole('button', { name: 'Reverse' }).click()
  await expect(shown).toContainText('Frame 1 of 13 · dvb_scan_relaxed.log, step 1, point 13 (reversed)')
  await expect(piece.getByLabel('First point')).toHaveValue('13')

  // Trimmed to seven points, then split in two at the frame shown: the split point is kept once.
  await piece.getByLabel('Last point').fill('7')
  await piece.getByLabel('Last point').press('Enter')
  await expect(slider).toHaveAttribute('max', '6')
  await slider.fill('3')
  await editor.getByLabel('Piece 1').getByRole('button', { name: 'Split here' }).click()
  await expect(editor.getByLabel('Piece 2')).toBeVisible()
  await expect(slider).toHaveAttribute('max', '6')
  const marks = editor.getByRole('list', { name: 'Joins and checks' })
  await expect(marks).toContainText('✓ frame 5 The pieces meet at the same structure, kept once.')

  // Undo takes back the split, then the trim.
  await editor.getByRole('button', { name: 'Undo' }).click()
  await expect(editor.getByLabel('Piece 2')).toHaveCount(0)
  await editor.getByRole('button', { name: 'Undo' }).click()
  await expect(slider).toHaveAttribute('max', '12')
  await editor.getByRole('button', { name: 'Redo' }).click()
  await expect(slider).toHaveAttribute('max', '6')

  // Saved as an xyz file with the level and where each structure came from.
  const downloading = page.waitForEvent('download')
  await editor.getByRole('button', { name: 'Save as…' }).click()
  const saved = await downloading
  expect(saved.suggestedFilename()).toBe('dvb_scan_relaxed_joined.xyz')
  const file = join(E2E_DIR, 'joined.xyz')
  await saved.saveAs(file)
  const lines = readFileSync(file, 'utf-8').split(/\r?\n/)
  expect(lines[1]).toContain('chembook3d joined scan 1 | program: Gaussian')
  expect(lines[1]).toContain('from: dvb_scan_relaxed.log, step 1, point 13 (reversed)')

  // Opened again, the saved file is one scan with the same seven points.
  await editor.getByRole('button', { name: 'Close' }).click()
  await page.getByRole('button', { name: 'Scan editor', exact: true }).click()
  const again = page.getByRole('dialog', { name: 'Scan editor' })
  await again.getByRole('button', { name: 'Add files…' }).click()
  await expect(again.getByLabel('Folder path')).not.toHaveValue('')
  await again.getByLabel('Folder path').fill(E2E_DIR)
  await again.getByRole('button', { name: 'Go' }).click()
  await again.getByRole('button', { name: /joined\.xyz/ }).click()
  await expect(again.getByRole('list', { name: 'Scans in the editor' })).toContainText('joined scan')
  await expect(again.getByLabel('Frame', { exact: true })).toHaveAttribute('max', '6')
  await again.getByRole('button', { name: 'Close' }).click()
  await expect(page.getByRole('dialog')).toHaveCount(0)
})

test('a scan opens in the scan editor from Inspect and from the step movie', async ({ page }) => {
  // D135, T-UI-37
  test.slow()
  await newInvestigation(page, 'Scan editor entries')
  await page.getByRole('button', { name: 'Import file…' }).click()
  const browse = page.getByRole('dialog', { name: 'Import calculation output' })
  await expect(browse.getByLabel('Folder path')).not.toHaveValue('')
  await browse.getByLabel('Folder path').fill(join(FIXTURES, 'gaussian'))
  await browse.getByRole('button', { name: 'Go' }).click()
  await expect(browse.getByRole('listitem').filter({ hasText: 'dvb_scan_unrelaxed.log' })).toBeVisible()
  // A name filter offers its files to the scan editor.
  await browse.getByLabel('Filter by name').fill('dvb_scan_relaxed')
  await expect(browse.getByRole('button', { name: 'Scan editor (1)' })).toBeVisible()
  await browse
    .getByRole('listitem')
    .filter({ hasText: 'dvb_scan_relaxed.log' })
    .getByRole('button', { name: 'Inspect' })
    .click()
  const inspect = page.getByRole('dialog', { name: 'Inspect dvb_scan_relaxed.log' })
  await inspect.getByRole('button', { name: 'Open in Scan editor' }).click()
  const editor = page.getByRole('dialog', { name: 'Scan editor' })
  await expect(editor.getByLabel('Frame', { exact: true })).toHaveAttribute('max', '12')
  await editor.getByRole('button', { name: 'Close' }).click()

  // An imported scan opens from its step movie, read from the investigation's copy.
  await page.getByRole('button', { name: 'Import file…' }).click()
  const dialog = page.getByRole('dialog')
  await dialog.getByLabel('Upload a file').setInputFiles(join(FIXTURES, 'gaussian', 'dvb_scan_relaxed.log'))
  await dialog.getByRole('button', { name: 'Import', exact: true }).click()
  const inspector = page.getByLabel('Node inspector')
  const movie = inspector.getByLabel('Optimization steps')
  await movie.getByLabel('Steps of').selectOption({ index: 1 })
  await movie.getByRole('button', { name: 'Open in Scan editor' }).click()
  const fromMovie = page.getByRole('dialog', { name: 'Scan editor' })
  await expect(fromMovie.getByRole('list', { name: 'Scans in the editor' })).toContainText('dvb_scan_relaxed.log')
  await expect(fromMovie.getByLabel('Frame', { exact: true })).toHaveAttribute('max', '12')
  await fromMovie.getByRole('button', { name: 'Close' }).click()
  await expect(page.getByRole('dialog')).toHaveCount(0)
})

test('a finer scan replaces the coarse points, and scans are compared at one coordinate', async ({ page }) => {
  // D136, T-UI-38
  test.slow()
  await newInvestigation(page, 'Finer scan')
  await page.getByRole('button', { name: 'Scan editor', exact: true }).click()
  const editor = page.getByRole('dialog', { name: 'Scan editor' })
  await editor.getByRole('button', { name: 'Add files…' }).click()
  await expect(editor.getByLabel('Folder path')).not.toHaveValue('')
  await editor.getByLabel('Folder path').fill(join(FIXTURES, 'gaussian'))
  await editor.getByRole('button', { name: 'Go' }).click()
  await editor.getByRole('button', { name: /dvb_scan_relaxed\.log/ }).click()
  const piece = editor.getByLabel('Piece 1')
  await piece.getByLabel('Last point').fill('7')
  await piece.getByLabel('Last point').press('Enter')
  await expect(editor.getByLabel('Frame', { exact: true })).toHaveAttribute('max', '6')

  // The seven points, 180° to 360°, saved; and every other one of them as a coarse scan.
  const folder = join(E2E_DIR, 'finer')
  mkdirSync(folder, { recursive: true })
  const downloading = page.waitForEvent('download')
  await editor.getByRole('button', { name: 'Save as…' }).click()
  const fine = join(folder, 'fine.xyz')
  await (await downloading).saveAs(fine)
  const lines = readFileSync(fine, 'utf-8').split(/\r?\n/)
  const blocks: string[][] = []
  for (let i = 0; i < lines.length && lines[i].trim(); ) {
    const n = Number(lines[i])
    blocks.push(lines.slice(i, i + n + 2))
    i += n + 2
  }
  expect(blocks).toHaveLength(7)
  writeFileSync(join(folder, 'coarse.xyz'), [0, 2, 4, 6].flatMap((k) => blocks[k]).join('\n') + '\n')
  await editor.getByRole('button', { name: 'Close' }).click()

  await page.getByRole('button', { name: 'Scan editor', exact: true }).click()
  const again = page.getByRole('dialog', { name: 'Scan editor' })
  await again.getByRole('button', { name: 'Add files…' }).click()
  await expect(again.getByLabel('Folder path')).not.toHaveValue('')
  await again.getByLabel('Folder path').fill(folder)
  await again.getByRole('button', { name: 'Go' }).click()
  await expect(again.getByRole('listitem').filter({ hasText: 'coarse.xyz' })).toBeVisible()
  await again.getByRole('button', { name: /coarse\.xyz/ }).click()
  await expect(again.getByLabel('Frame', { exact: true })).toHaveAttribute('max', '3')
  await again.getByRole('button', { name: 'Add files…' }).click()
  await again.getByRole('button', { name: /fine\.xyz/ }).click()

  // The finer scan runs over the same stretch, so it is offered rather than joined on.
  const offer = again.getByRole('button', { name: 'Use the 7 finer points of fine.xyz between 180.0 and 360.0 °' })
  await offer.click()
  await expect(again.getByLabel('Frame', { exact: true })).toHaveAttribute('max', '6')
  await expect(again.getByText('7 points of fine.xyz replace 2 of the path')).toBeVisible()

  // At 240° both scans have a point: the same structure.
  await again.getByLabel('Frame', { exact: true }).fill('2')
  await again.getByRole('button', { name: 'Compare scans here' }).click()
  const compared = again.getByRole('list', { name: 'Compared points' })
  await expect(compared).toContainText('fine.xyz, point 3 (the frame shown)')
  await expect(compared).toContainText('coarse.xyz, point 2 · 0.00 Å apart')
  await again.getByRole('button', { name: 'Stop comparing' }).click()
  await expect(compared).toHaveCount(0)

  await again.getByRole('button', { name: 'Undo' }).click()
  await expect(again.getByLabel('Frame', { exact: true })).toHaveAttribute('max', '3')
  await again.getByRole('button', { name: 'Close' }).click()
})

test('a joined scan is imported and shown on an edge, naming its program', async ({ page }) => {
  // D137, T-UI-39
  test.slow()
  await newInvestigation(page, 'Joined scan on edge')
  const ids: string[] = []
  for (const [label, x, y] of [
    ['Start', 0, 0],
    ['End', 700, 0],
  ] as const) {
    const response = await page.request.post('/api/nodes', { data: { label, pos_x: x, pos_y: y } })
    expect(response.ok()).toBe(true)
    ids.push((await response.json()).id)
  }
  expect((await page.request.post('/api/transitions', { data: { source_id: ids[0], target_id: ids[1] } })).ok()).toBe(true)
  await page.reload()
  await expect(page.locator('.react-flow__node')).toHaveCount(2)

  await page.getByRole('button', { name: 'Scan editor', exact: true }).click()
  const editor = page.getByRole('dialog', { name: 'Scan editor' })
  await editor.getByRole('button', { name: 'Add files…' }).click()
  await expect(editor.getByLabel('Folder path')).not.toHaveValue('')
  await editor.getByLabel('Folder path').fill(join(FIXTURES, 'gaussian'))
  await editor.getByRole('button', { name: 'Go' }).click()
  await editor.getByRole('button', { name: /dvb_scan_relaxed\.log/ }).click()
  const piece = editor.getByLabel('Piece 1')
  await piece.getByLabel('Last point').fill('7')
  await piece.getByLabel('Last point').press('Enter')
  await expect(editor.getByLabel('Frame', { exact: true })).toHaveAttribute('max', '6')

  // Imported onto the edge: the import dialog opens over the editor, which comes back after.
  await editor.getByLabel('Show the imported path on').selectOption({ label: 'On the edge Start → End' })
  await editor.getByRole('button', { name: 'Import…' }).click()
  const dialog = page.getByRole('dialog', { name: 'Import calculation output' })
  await expect(dialog).toContainText('dvb_scan_relaxed_joined.xyz')
  await dialog.getByRole('button', { name: 'Import', exact: true }).click()
  const chip = page.locator('.edge-label .scan-path-chip')
  await expect(chip).toHaveText(/^Gaussian path · top \+\d+\.\d+ (kcal\/mol|kJ\/mol)$/)
  await expect(page.getByRole('dialog', { name: 'Scan editor' })).toBeVisible()
  await expect(page.getByRole('dialog', { name: 'Scan editor' }).getByLabel('Frame', { exact: true })).toHaveAttribute(
    'max',
    '6',
  )
  await page.getByRole('dialog', { name: 'Scan editor' }).getByRole('button', { name: 'Close' }).click()
  // On the edge only: its box is off the canvas.
  await expect(page.locator('.react-flow__node')).toHaveCount(2)
})
