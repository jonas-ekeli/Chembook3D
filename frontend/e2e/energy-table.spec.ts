import { join } from 'node:path'
import { expect, test, type Page } from '@playwright/test'
import { forgetView } from './demoView.ts'

// T-UI-54, D177 (UI4): the drawer's energy table puts the relative energies first: Label (with a
// TS tag) · Step · Branch · ΔX · ΔX‡ of step, then the absolute values under one header, then
// Free species and Check; a constant Level and Thermochemistry are a caption; the header stays;
// the row of the node selected on the canvas is highlighted.

const E2E_DIR = process.env.E2E_DIR!

async function openDemo(page: Page) {
  await forgetView(page.request, join(E2E_DIR, 'demo'))
  // Values below are in kcal/mol; an earlier test may have left another unit set.
  await page.request.put('/api/settings', { data: { energy_unit: 'kcal/mol' } })
  await page.goto('/')
  await page.getByRole('button', { name: /^Open/ }).first().click()
  const dialog = page.getByRole('dialog')
  await expect(dialog.getByRole('list', { name: 'Folders' })).toBeVisible()
  await dialog.getByLabel('Folder path').fill(join(E2E_DIR, 'demo'))
  await dialog.getByRole('button', { name: 'Go' }).click()
  await dialog.getByRole('button', { name: 'Open', exact: true }).click()
  await expect(page.getByRole('dialog')).toHaveCount(0)
  await expect(page.getByRole('group', { name: 'Node T-S0', exact: true })).toBeVisible()
}

test('energy table: relative energies first, barrier of each step, selected row highlighted', async ({ page }) => {
  await openDemo(page)
  await page.getByRole('button', { name: 'Profile and table' }).click()
  const drawer = page.getByLabel('Energy drawer')
  await drawer.getByLabel('Add branch pathway').selectOption({ label: 'A' })
  await drawer.getByLabel('Add branch pathway').selectOption({ label: 'B' })
  await expect(drawer.getByRole('group', { name: 'Pathway B' })).toContainText('T-S0 → B-S1 → B-S2 → B-S3')
  await drawer.getByRole('button', { name: 'Energy table' }).click()
  const table = drawer.getByRole('table', { name: 'Energy table' })
  const head = table.locator('thead tr').last().locator('th')
  await expect(head).toHaveText([
    'Label',
    'Step',
    'Branch',
    'ΔG',
    'ΔG‡ of step',
    'E',
    'H',
    /^G 1 atm/,
    /^G_qh /,
    'Free species',
    'Check',
  ])
  await expect(head.nth(3)).toHaveAttribute('title', 'ΔG 1 atm (kcal/mol)')
  await expect(head.nth(4)).toHaveAttribute('title', /lowest state before it on the pathway since the previous TS/)
  await expect(table.locator('thead tr.group-row th')).toHaveText(['kcal/mol, relative to T-S0', 'Absolute (hartree)'])
  // Level and Thermochemistry are the same on every row, so they are the caption.
  await expect(drawer.locator('.energy-caption')).toHaveText(
    "Gaussian B3LYP-GD3BJ/def2SVP · Gaussian's RRHO; scale 1 · ΔG 1 atm (kcal/mol)",
  )

  const row = (label: string) => table.locator('tbody tr').filter({ has: page.getByRole('cell', { name: label }) })
  const ts = table.locator('tbody tr').filter({ hasText: 'B-S2' })
  await expect(ts.locator('.ts-mark')).toHaveText('TS')
  await expect(ts.locator('td').nth(3)).toHaveText('17.30')
  await expect(ts.locator('td').nth(3)).toHaveCSS('font-size', '14px')
  await expect(ts.locator('td').nth(3)).toHaveCSS('font-weight', '700')
  // From the lowest point before it since the previous TS (B-S1, −2.10), not the reference.
  await expect(ts.locator('td').nth(4)).toHaveText('19.40')
  await expect(row('A-S1').locator('td').nth(3)).toHaveText('−3.20') // a real minus sign
  await expect(row('A-S1').locator('td').nth(4)).toHaveText('')
  await expect(row('A-S1').locator('.ts-mark')).toHaveCount(0)
  await expect(row('A-S1').locator('.swatch')).toBeVisible()
  await expect(table.locator('tbody td.abs').first()).toHaveCSS('font-family', /monospace/)
  // The bug of the generic .check rule: the Check cells stay table cells.
  await expect(table.locator('tbody td.check').first()).toHaveCSS('display', 'table-cell')
  await expect(table.locator('thead')).toHaveCSS('position', 'sticky')
  await expect(drawer.getByRole('button', { name: 'Export CSV' })).toHaveAttribute('title', /own column order/)

  // The row of the node selected on the canvas.
  await expect(table.locator('tbody tr[aria-current="true"]')).toHaveCount(0)
  await page.getByRole('group', { name: 'Node B-S2', exact: true }).click()
  await expect(table.locator('tbody tr[aria-current="true"]')).toHaveCount(1)
  await expect(ts).toHaveClass(/\bselected\b/)

  // The relative and barrier columns follow the energy type shown.
  await page.getByRole('group', { name: 'Energy view' }).getByLabel('Energy type').selectOption('E')
  await expect(head.nth(3)).toHaveText('ΔE')
  await expect(head.nth(4)).toHaveText('ΔE‡ of step')
  await expect(ts.locator('td').nth(4)).not.toHaveText('')
})
