import { readFileSync } from 'node:fs'
import { expect, test, type Locator, type Page } from '@playwright/test'

// T-UI-17, D113: two structures of the same atoms numbered differently are matched, reviewed
// side by side, and the end is saved in the start's order.
const E2E_DIR = process.env.E2E_DIR!

// Glycerol, the first conformer of the CREST fixture.
const GLYCEROL = [
  ['C', -0.0332, 0.7994, -0.5076],
  ['C', -1.2461, -0.1282, -0.6919],
  ['C', 1.2859, 0.0096, -0.6494],
  ['O', -0.0918, 1.3936, 0.7707],
  ['O', 1.4328, -0.939, 0.3852],
  ['O', -1.3669, -0.9917, 0.4109],
  ['H', -0.5409, -1.4994, 0.4729],
  ['H', -0.6544, 0.8197, 1.3149],
  ['H', 1.4764, -0.4464, 1.2156],
  ['H', -0.0489, 1.5988, -1.2563],
  ['H', -2.1661, 0.4616, -0.7178],
  ['H', -1.1543, -0.6846, -1.6333],
  ['H', 2.1279, 0.712, -0.6458],
  ['H', 1.2934, -0.5480, -1.5877],
] as const

function xyz(atoms: readonly (readonly [string, number, number, number])[]): string {
  return `${atoms.length}\n\n${atoms.map(([e, x, y, z]) => `${e} ${x} ${y} ${z}`).join('\n')}\n`
}

/** The atoms in reverse order, turned 90° about z and shifted. */
const reversed = [...GLYCEROL].reverse().map(([e, x, y, z]) => [e, -y + 2, x - 1, z + 3] as const)

function canvasNode(page: Page, label: string): Locator {
  return page.getByRole('group', { name: `Node ${label}`, exact: true })
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

test('match the atoms of two structures numbered differently', async ({ page }) => {
  test.slow() // software WebGL is slow on the Windows runners
  await newInvestigation(page, 'Atom match test')
  for (const body of [
    { label: 'start', xyz: xyz(GLYCEROL), pos_x: 100, pos_y: 100 },
    { label: 'end', xyz: xyz(reversed), pos_x: 400, pos_y: 100 },
  ]) {
    const response = await page.request.post('/api/nodes', { data: body })
    expect(response.ok()).toBeTruthy()
  }
  await page.reload()
  await canvasNode(page, 'start').click()
  await canvasNode(page, 'end').click({ modifiers: ['Control'] })
  const inspector = page.getByLabel('Selection inspector')
  await inspector.getByRole('button', { name: 'Match atoms…' }).click()

  const dialog = page.getByRole('dialog', { name: 'Match atoms' })
  await expect(dialog.getByLabel('Match summary')).toHaveText(
    'Atoms matched: no bond forms or breaks (RMSD 0.00 Å after fitting).',
  )
  await expect(dialog.getByLabel('Doubts')).toHaveCount(0)
  await expect(dialog.getByTestId('viewer3d')).toHaveCount(2)
  await expect(dialog.getByTestId('viewer3d').first().locator('canvas')).toBeVisible()

  // The end saved in the start's order: the same elements and, turned back, the same atoms.
  const download = page.waitForEvent('download')
  await dialog.getByRole('button', { name: 'Download renumbered end' }).click()
  const saved = readFileSync(await (await download).path(), 'utf8').trim().split('\n').slice(2)
  expect(saved.map((line) => line.split(/\s+/)[0])).toEqual(GLYCEROL.map(([e]) => e))
  const first = saved[0].split(/\s+/).slice(1).map(Number)
  expect(first[0]).toBeCloseTo(GLYCEROL[0][1], 3)
  expect(first[2]).toBeCloseTo(GLYCEROL[0][3], 3)

  // Swapping the ends matches the other way round; they already share nothing else.
  await dialog.getByRole('button', { name: 'Swap ends' }).click()
  await expect(dialog.getByText('The atoms of “start” are matched to the numbering of “end”.')).toBeVisible()
  await expect(dialog.getByLabel('Match summary')).toContainText('no bond forms or breaks')
  await dialog.getByRole('button', { name: 'Close' }).click()
  await expect(dialog).toHaveCount(0)
})
