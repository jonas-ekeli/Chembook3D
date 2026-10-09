import { join, resolve } from 'node:path'
import { expect, test, type Page } from '@playwright/test'

const E2E_DIR = process.env.E2E_DIR!
const FIXTURES = resolve(import.meta.dirname, '..', '..', 'tests', 'fixtures')

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

function dihedral(text: string | null): number {
  const match = /Dihedral (-?\d+\.\d)°/.exec(text ?? '')
  expect(match).not.toBeNull()
  return Number(match![1])
}

test('play a relaxed scan, its converged points only, with the measurement following', async ({ page }) => {
  // D101, FR-3D-08
  test.slow()
  await newInvestigation(page, 'Scan movie')
  await page.getByRole('button', { name: 'Import file…' }).click()
  const dialog = page.getByRole('dialog')
  await dialog.getByLabel('Upload a file').setInputFiles(join(FIXTURES, 'gaussian', 'dvb_scan_relaxed.log'))
  await dialog.getByRole('button', { name: 'Import', exact: true }).click()
  const inspector = page.getByLabel('Node inspector')
  await expect(inspector.getByRole('heading', { name: 'dvb_scan_relaxed' })).toBeVisible()

  const movie = inspector.getByLabel('Optimization steps')
  await movie.getByLabel('Steps of').selectOption({ index: 1 })
  const shown = movie.getByLabel('Structure shown')
  const slider = movie.getByLabel('Structure', { exact: true })
  // A scan opens on its converged points, one per scan point, and plays.
  await expect(movie.getByLabel('Converged points only')).toBeChecked()
  await expect(slider).toHaveAttribute('max', '12')
  await expect(movie.getByRole('button', { name: 'Pause' })).toBeVisible()
  await movie.getByRole('button', { name: 'Pause' }).click()
  await slider.fill('0')
  await expect(shown).toContainText('Structure 1 of 61 · scan point 1 of 13 · converged · E = -382.308267 Eh')
  await expect(movie.getByRole('img', { name: 'Energy per structure' })).toBeVisible()
  await expect(inspector.getByRole('alert')).toHaveCount(0)

  // The scanned dihedral, measured on the structure shown, moves 30° per point.
  await inspector.getByLabel('Atom numbers').fill('10 9 4 3')
  await inspector.getByLabel('Atom numbers').press('Enter')
  const measurement = inspector.getByLabel('Measurement')
  await expect(measurement).toContainText('Dihedral')
  const first = dihedral(await measurement.textContent())
  await movie.getByRole('button', { name: 'Next structure' }).click()
  await expect(shown).toContainText('scan point 2 of 13')
  await expect.poll(async () => dihedral(await measurement.textContent())).not.toBe(first)
  const second = dihedral(await measurement.textContent())
  expect(Math.abs(((second - first + 540) % 360) - 180)).toBeCloseTo(30, 0)

  // Every structure of the scan, then the movie travels with the pop-out.
  await movie.getByLabel('Converged points only').uncheck()
  await expect(slider).toHaveAttribute('max', '60')
  await expect(measurement).toContainText('Dihedral') // the picked atoms are kept
  await inspector.getByRole('button', { name: 'Pop out the 3D view' }).click()
  const window = page.locator('.popout-window')
  await expect(window.getByLabel('Optimization steps')).toBeVisible()
  await expect(window.getByTestId('viewer3d').locator('canvas')).toBeVisible()
  await window.getByLabel('Steps of').selectOption({ label: 'No step movie' })
  await expect(window.getByLabel('Structure shown')).toHaveCount(0)
  await expect(window.getByLabel('Atom numbers')).toBeVisible()
})

test('an xTB scan plays, and "Use this structure" changes a path node in place', async ({ page }) => {
  // D112, T-UI-16
  test.slow()
  await newInvestigation(page, 'xTB scan path')
  await page.getByRole('button', { name: 'Import file…' }).click()
  const dialog = page.getByRole('dialog')
  await dialog.getByLabel('Upload a file').setInputFiles(join(FIXTURES, 'xtb', 'dce_scan', 'xtbscan.log'))
  await dialog.getByRole('button', { name: 'Import', exact: true }).click()
  const inspector = page.getByLabel('Node inspector')
  await expect(inspector.getByRole('heading', { name: 'xtbscan' })).toBeVisible()
  const coordinates = async () => (await inspector.getByLabel('xyz text').inputValue()).split('\n')[2]
  const before = await coordinates()

  const movie = inspector.getByLabel('Optimization steps')
  await movie.getByLabel('Steps of').selectOption({ index: 1 })
  const shown = movie.getByLabel('Structure shown')
  const slider = movie.getByLabel('Structure', { exact: true })
  // Every structure is a converged point: nothing to filter.
  await expect(slider).toHaveAttribute('max', '12')
  await expect(movie.getByLabel('Converged points only')).toHaveCount(0)
  await movie.getByRole('button', { name: 'Pause' }).click()
  await slider.fill('7')
  await movie.getByRole('button', { name: 'Next structure' }).click()
  await expect(shown).toContainText('Structure 9 of 13 · scan point 9 of 13 · converged')
  await expect(movie.getByText('changes this node')).toBeVisible()
  await movie.getByRole('button', { name: 'Use this structure' }).click()
  await expect(page.getByRole('status')).toContainText("The node's geometry is now structure 9.")
  await expect.poll(coordinates).not.toBe(before)
  await expect(page.locator('.react-flow__node')).toHaveCount(1)
  // The history names the scan's changed coordinates and energy without listing them, so the
  // inspector, and the 3D view in it, keep their width (a long line once made the view black).
  const history = inspector.getByRole('list', { name: 'History' })
  await expect(history).toContainText('Calculation coordinates changed (8 atoms → 8 atoms)')
  await expect(history).toContainText(/Energy: -15\.\d{6} Eh → -15\.\d{6} Eh/)
  const width = async (locator: typeof inspector) => (await locator.boundingBox())!.width
  expect(await width(inspector.getByTestId('viewer3d'))).toBeLessThanOrEqual(await width(inspector))

  // On a node with a Gaussian scan, the structure goes to a derived node (ID-4).
  await page.getByRole('button', { name: 'Import file…' }).click()
  await dialog.getByLabel('Upload a file').setInputFiles(join(FIXTURES, 'gaussian', 'dvb_scan_relaxed.log'))
  await dialog.getByRole('button', { name: 'Import', exact: true }).click()
  await expect(inspector.getByRole('heading', { name: 'dvb_scan_relaxed' })).toBeVisible()
  await movie.getByLabel('Steps of').selectOption({ index: 1 })
  await movie.getByRole('button', { name: 'Pause' }).click()
  await expect(movie.getByText('makes a derived node')).toBeVisible()
  await movie.getByRole('button', { name: 'Use this structure' }).click()
  await expect(page.getByRole('status')).toContainText('saved as a new node derived from “dvb_scan_relaxed”')
  await expect(page.locator('.react-flow__node')).toHaveCount(3)
})
