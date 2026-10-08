import { join } from 'node:path'
import { expect, test, type Locator, type Page } from '@playwright/test'
import { forgetView } from './demoView.ts'

// T-UI-07, FR-TOF-01…05, D86: a turnover from a closed pathway in the energy drawer, its TDTS
// and TDI, the table, a second turnover from a branch, the comparison, the history, deleting.
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

test('turnover of a closed cycle', async ({ page }) => {
  await openDemo(page)
  const canvas = await (await page.request.get('/api/canvas')).json()
  const id = (label: string) => canvas.nodes.find((n: { label: string }) => n.label === label).id
  // A13: B-S3 back to T-S0 closes branch B's pathway into a cycle.
  const closing = await (
    await page.request.post('/api/transitions', { data: { source_id: id('B-S3'), target_id: id('T-S0') } })
  ).json()
  try {
    await page.reload()
    await expect(canvasNode(page, 'T-S0')).toBeVisible()
    await page.getByRole('button', { name: 'Profile and table' }).click()
    const drawer = page.getByLabel('Energy drawer')
    await drawer.getByLabel('Add branch pathway').selectOption({ label: 'B' })
    const b = drawer.getByRole('group', { name: 'Pathway B' })
    await expect(b).toContainText('T-S0 → B-S1 → B-S2 → B-S3 → T-S0 ↻')
    await b.getByRole('button', { name: 'Turnover' }).click()

    await expect(page.getByRole('button', { name: 'Analyses' })).toHaveAttribute('aria-pressed', 'true')
    const editor = page.getByLabel('Turnover', { exact: true })
    await expect(editor.getByRole('heading', { name: 'B', exact: true })).toBeVisible()
    await expect(editor.getByLabel('Pathway', { exact: true })).toHaveText('T-S0 → B-S1 → B-S2 → B-S3 → T-S0 ↻')
    await expect(editor.getByLabel('Turnover level')).toHaveValue(/.+/)
    await expect(editor.getByLabel('Turnover energy type')).toHaveValue('G_qh')

    // The demo's G values (kcal/mol): T-S0 0, B-S1 −2.1, B-S2 17.3 (TS), B-S3 −6.0. The TS
    // comes before B-S3 in the next turnover, so δE = 17.3 + 6.0 = 23.3 with ΔG_r = 0.
    await editor.getByLabel('Turnover energy type').selectOption('G')
    const result = editor.getByRole('region', { name: 'Turnover result' })
    const span = result.getByLabel('Energetic span')
    await expect(span).toContainText('δE = 23.30 kcal/mol')
    await expect(span.getByRole('button', { name: 'B-S2' })).toBeVisible()
    await expect(span.getByRole('button', { name: 'B-S3' })).toBeVisible()
    await expect(span).toContainText('= 0.00 kcal/mol')
    // No free species join or leave, so ΔG_r is zero and the cycle does not turn over.
    await expect(result.getByLabel('TOF', { exact: true })).toContainText('TOF = 0 s⁻¹')
    await expect(result.getByLabel('Result notes')).toContainText('ΔG_r is not negative')

    const chart = result.getByRole('img', { name: 'Energy profile' })
    await expect(chart).toContainText('B-S2 ‡ TDTS')
    await expect(chart).toContainText('B-S3 TDI')
    const table = result.getByRole('table', { name: 'Degree of TOF control' })
    await expect(table.getByRole('row')).toHaveCount(6)
    await expect(table.getByRole('row', { name: /B-S2/ })).toContainText('100.0')
    await expect(table.getByRole('row', { name: /B-S3/ })).toContainText('TDI')
    await expect(table).toContainText('T-S0 (cycle closed)')
    if (process.env.SHOT) await page.screenshot({ path: process.env.SHOT, fullPage: true })

    // A second turnover from the branch, compared with the first.
    await page.getByRole('button', { name: 'New turnover' }).click()
    await expect(editor.getByRole('heading', { name: 'Turnover 2' })).toBeVisible()
    await expect(editor.getByRole('region', { name: 'Turnover result' })).toContainText('Choose a closed pathway')
    await editor.getByLabel("Take a branch's pathway").selectOption({ label: 'B' })
    await expect(editor.getByLabel('Pathway', { exact: true })).toHaveText('T-S0 → B-S1 → B-S2 → B-S3 → T-S0 ↻')
    await editor.getByLabel('Turnover energy type').selectOption('G')
    await expect(editor.getByLabel('Energetic span')).toContainText('δE = 23.30 kcal/mol')
    await editor.getByLabel('Compare with').selectOption({ label: 'B' })
    await expect(editor.getByLabel('Comparison')).toContainText('Both cycles need a positive, finite TOF')
    // A branch whose pathway does not close is refused with the reason.
    await editor.getByLabel("Take a branch's pathway").selectOption({ label: 'T' })
    await expect(editor.getByRole('alert')).toContainText('does not close a cycle')

    // In the history.
    await page.getByRole('button', { name: 'History' }).click()
    const history = page.getByRole('list', { name: 'History' })
    await expect(history).toContainText('Created turnover “B”')
    await expect(history).toContainText('Pathway: none → T-S0 → B-S1 → B-S2 → B-S3 → T-S0')
    await expect(history).toContainText('Energy: G_qh → G')

    // Deleting both leaves the nodes as they are.
    await page.getByRole('button', { name: 'Analyses' }).click()
    for (const name of ['Turnover 2', 'B']) {
      await page.getByRole('navigation', { name: 'Analyses' }).getByRole('button', { name, exact: true }).click()
      await expect(editor.getByRole('heading', { name, exact: true })).toBeVisible()
      await editor.getByRole('button', { name: 'Delete…' }).click()
      await page.getByRole('dialog', { name: 'Delete turnover' }).getByRole('button', { name: 'Delete' }).click()
      await expect(page.getByRole('dialog')).toHaveCount(0)
    }
    await expect(page.getByText('No analyses yet.')).toBeVisible()
    await page.getByRole('button', { name: 'Canvas' }).click()
    await expect(canvasNode(page, 'B-S2')).toBeVisible()
  } finally {
    for (const turnover of await (await page.request.get('/api/turnovers')).json()) {
      await page.request.delete(`/api/turnovers/${turnover.id}`)
    }
    await page.request.delete(`/api/transitions/${closing.id}`)
  }
})
