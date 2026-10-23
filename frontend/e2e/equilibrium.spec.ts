import { join } from 'node:path'
import { expect, test, type Locator, type Page } from '@playwright/test'
import { forgetView } from './demoView.ts'

// T-UI-49, FR-EQ-01…06, D171: a conformer equilibrium from two minima selected on the canvas,
// a count, members added from a branch, the history, and deleting it.
const E2E_DIR = process.env.E2E_DIR!

async function openDemo(page: Page) {
  await forgetView(page.request, join(E2E_DIR, 'demo'))
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

test('conformer equilibrium from two minima', async ({ page }) => {
  await openDemo(page)
  // The demo's A-S1 and A-S3 lie 5.30 kcal/mol apart, joined only by a direct edge (no TS).
  await canvasNode(page, 'A-S1').click()
  await canvasNode(page, 'A-S3').click({ modifiers: ['Control'] })
  await page.getByLabel('Selection inspector').getByRole('button', { name: 'Conformer equilibrium…' }).click()

  await expect(page.getByRole('button', { name: 'Analyses' })).toHaveAttribute('aria-pressed', 'true')
  const editor = page.getByLabel('Conformer equilibrium', { exact: true })
  await expect(editor.getByRole('heading', { name: 'Conformer equilibrium 1' })).toBeVisible()
  await expect(editor.getByLabel('Equilibrium energy type')).toHaveValue('G_qh')
  const result = editor.getByRole('region', { name: 'Conformer equilibrium result' })
  const populations = result.getByRole('table', { name: 'Populations' })
  // e^(−5.30/RT) at 298.15 K: 0.0130 %.
  await expect(populations.getByRole('row', { name: /A-S1/ })).toContainText('0.0130 %')
  await expect(populations.getByRole('row', { name: /A-S3/ })).toContainText('100.0 %')
  await expect(result).toContainText('joined only by a direct edge (no TS, D53)')

  // D171d: A-S1 counted twice doubles its weight.
  const count = editor.getByLabel('Count of A-S1')
  await count.fill('2')
  await count.press('Tab')
  await expect(populations.getByRole('row', { name: /A-S1/ })).toContainText('0.0261 %')

  // Members from branch A bring its TS, whose placeholder structure is another molecule.
  await editor.getByLabel('Add members').selectOption({ label: 'A' })
  await expect(editor.getByRole('table', { name: 'Members' })).toContainText('A-S2')
  await expect(result).toContainText('must have the same atoms')
  await editor.getByRole('button', { name: 'Remove A-S2' }).click()
  await expect(populations.getByRole('row', { name: /A-S1/ })).toContainText('0.0261 %')

  // In the history.
  await page.getByRole('button', { name: 'History' }).click()
  const history = page.getByRole('list', { name: 'History' })
  await expect(history).toContainText('Created conformer equilibrium “Conformer equilibrium 1”')
  await expect(history).toContainText('Conformers and TSs: A-S1, A-S3 → A-S1 ×2, A-S3')

  // Deleting it leaves the nodes as they are.
  await page.getByRole('button', { name: 'Analyses' }).click()
  await editor.getByRole('button', { name: 'Delete…' }).click()
  await page
    .getByRole('dialog', { name: 'Delete conformer equilibrium' })
    .getByRole('button', { name: 'Delete' })
    .click()
  await expect(page.getByText('No analyses yet.')).toBeVisible()
  await page.getByRole('button', { name: 'Canvas' }).click()
  await expect(canvasNode(page, 'A-S1')).toBeVisible()
})
