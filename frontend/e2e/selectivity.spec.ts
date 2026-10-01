import { join } from 'node:path'
import { expect, test, type Locator, type Page } from '@playwright/test'

// T-UI-06, FR-SEL-01…06, D83: a selectivity from two TSs selected on the canvas, its result at
// two temperatures and with only the lowest TS, an experiment, the history, and deleting it.
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

test('selectivity from two transition states', async ({ page }) => {
  await openDemo(page)
  // The demo's two [2+2] TSs: B-S2 lies 2.50 kcal/mol above A-S2 in G and in G_qh.
  await canvasNode(page, 'A-S2').click()
  await canvasNode(page, 'B-S2').click({ modifiers: ['Control'] })
  const selection = page.getByLabel('Selection inspector')
  await selection.getByRole('button', { name: 'Selectivity…' }).click()

  await expect(page.getByRole('button', { name: 'Analyses' })).toHaveAttribute('aria-pressed', 'true')
  const editor = page.getByLabel('Selectivity', { exact: true })
  await expect(editor.getByRole('heading', { name: 'Selectivity 1' })).toBeVisible()
  await expect(editor.getByLabel('Selectivity level')).toHaveValue(/.+/)
  await expect(editor.getByLabel('Selectivity energy type')).toHaveValue('G_qh')
  const names = editor.getByLabel('Outcome name')
  await expect(names).toHaveCount(2)
  await expect(names.nth(0)).toHaveValue('A-S2')
  await expect(names.nth(1)).toHaveValue('B-S2')

  // S5: e^(−2.50/RT) at 298.15 K is 98.6 : 1.4, ee 97.1 %.
  const result = editor.getByRole('region', { name: 'Selectivity result' })
  const headline = result.getByLabel('Predicted ratio')
  await expect(headline).toContainText('A-S2 : B-S2 = 98.6 : 1.4')
  await expect(headline).toContainText('ee 97.1 % (A-S2)')
  await expect(headline).toContainText('Boltzmann sum')
  await expect(result.getByRole('table', { name: 'Outcomes' })).toContainText('2.50')
  await expect(result).toContainText('Boltzmann factors at 298.15 K (the G_qh temperature in Settings)')

  // Renaming an outcome.
  await names.nth(0).fill('A')
  await names.nth(0).press('Tab')
  await expect(headline).toContainText('A : B-S2 = 98.6 : 1.4')
  await names.nth(1).fill('B')
  await names.nth(1).press('Tab')
  await expect(headline).toContainText('A : B = 98.6 : 1.4')

  // S4: at 233.15 K, G_qh is recomputed and the ratio sharpens.
  await editor.getByLabel('Selectivity temperature').fill('233.15')
  await editor.getByLabel('Selectivity temperature').press('Enter')
  await expect(headline).toContainText('A : B = 99.5 : 0.5')
  await expect(result).toContainText('Boltzmann factors at 233.15 K')
  await editor.getByLabel('Selectivity temperature').fill('')
  await editor.getByLabel('Selectivity temperature').press('Enter')
  await expect(headline).toContainText('A : B = 98.6 : 1.4')

  // S2: only the lowest TS of each outcome (one each here, so the same ratio).
  await editor.getByLabel('Conformers').selectOption('lowest')
  await expect(headline).toContainText('Lowest TS only')

  // S6: an experiment of 90 : 10 is ΔΔG‡ 1.30 kcal/mol.
  await editor.getByLabel('Experimental amount of A').fill('90')
  await editor.getByLabel('Experimental amount of A').press('Enter')
  await expect(result).toContainText('Give an experimental amount for every outcome')
  await editor.getByLabel('Experimental amount of B').fill('10')
  await editor.getByLabel('Experimental amount of B').press('Enter')
  const outcomes = result.getByRole('table', { name: 'Outcomes' })
  await expect(outcomes).toContainText('90.0 %')
  await expect(outcomes).toContainText('1.30')
  await expect(result.getByLabel('Experiment')).toHaveText('Experiment: A : B = 90.0 : 10.0, ee 80.0 % (A)')

  // The transition states are listed with their share and lead back to the canvas.
  const states = result.getByRole('table', { name: 'Transition states' })
  await expect(states.getByRole('button', { name: 'B-S2' })).toBeVisible()

  // S7: in the history.
  await page.getByRole('button', { name: 'History' }).click()
  const history = page.getByRole('list', { name: 'History' })
  await expect(history).toContainText('Created selectivity “Selectivity 1”')
  await expect(history).toContainText('Outcomes: A-S2, B-S2 → A, B-S2')
  await expect(history).toContainText('Conformers: Boltzmann sum → lowest TS only')

  // Deleting it leaves the TSs as they are.
  await page.getByRole('button', { name: 'Analyses' }).click()
  await editor.getByRole('button', { name: 'Delete…' }).click()
  await page.getByRole('dialog', { name: 'Delete selectivity' }).getByRole('button', { name: 'Delete' }).click()
  await expect(page.getByText('No analyses yet.')).toBeVisible()
  await states.waitFor({ state: 'detached' })
  await page.getByRole('button', { name: 'Canvas' }).click()
  await expect(canvasNode(page, 'B-S2')).toBeVisible()
})
