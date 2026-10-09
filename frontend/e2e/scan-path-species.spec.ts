import { expect, test, type Locator, type Page } from '@playwright/test'

// T-UI-23, D120: a scan path along an edge where a free species joins. The complex is made up:
// a ruthenium alkylidene with two chlorides, and ethylene bound side-on.
const E2E_DIR = process.env.E2E_DIR!

const ALKYLIDENE = [
  ['C', -0.6, 0.0, 1.75],
  ['H', -0.1, 0.0, 2.72],
  ['H', -1.68, 0.0, 1.85],
  ['Ru', 0.0, 0.0, 0.0],
  ['Cl', 0.0, 0.0, -2.35],
  ['Cl', -2.3, 0.3, -0.3],
] as const
const ETHYLENE = [
  ['H', 21.2461, -2.6764, 4.1929],
  ['C', 21.1706, -3.13, 3.1812],
  ['H', 20.946, -2.32, 2.4542],
  ['C', 22.0418, -4.1643, 2.8188],
  ['H', 22.8018, -4.5233, 3.5458],
  ['H', 22.5016, -4.167, 1.8071],
] as const
const COMPLEX = [
  ['C', 2.1, 0.6761, 0.1812],
  ['C', 2.1, -0.6761, -0.1812],
  ['C', -0.6, 0.0, 1.75],
  ['H', -0.1, 0.0, 2.72],
  ['H', -1.68, 0.0, 1.85],
  ['Ru', 0.0, 0.0, 0.0],
  ['Cl', 0.0, 0.0, -2.35],
  ['Cl', -2.3, 0.3, -0.3],
  ['H', 2.45, 0.9745, 1.1929],
  ['H', 2.45, 1.4403, -0.5458],
  ['H', 2.45, -1.4403, 0.5458],
  ['H', 2.45, -0.9745, -1.1929],
] as const

function xyz(atoms: readonly (readonly [string, number, number, number])[]): string {
  return `${atoms.length}\n\n${atoms.map(([e, x, y, z]) => `${e} ${x} ${y} ${z}`).join('\n')}\n`
}

function canvasNode(page: Page, label: string): Locator {
  return page.getByRole('group', { name: `Node ${label}`, exact: true })
}

test('a species that joins is shown apart and can be set farther out', async ({ page }) => {
  test.slow() // software WebGL is slow on the Windows runners
  await page.goto('/')
  await page.getByRole('button', { name: /^New/ }).first().click()
  const created = page.getByRole('dialog')
  await created.getByLabel('Folder path').fill(E2E_DIR)
  await created.getByRole('button', { name: 'Go' }).click()
  await expect(created.getByLabel('Folder path')).toHaveValue(E2E_DIR)
  await created.getByLabel('Investigation name').fill('Scan path with ethylene')
  await created.getByRole('button', { name: 'Create' }).click()
  await expect(page.locator('.investigation')).toHaveText('Scan path with ethylene')

  const ids: string[] = []
  for (const body of [
    { label: 'alkylidene', xyz: xyz(ALKYLIDENE), pos_x: 100, pos_y: 100, charge: 0, multiplicity: 1 },
    { label: 'π-complex', xyz: xyz(COMPLEX), pos_x: 400, pos_y: 100, charge: 0, multiplicity: 1 },
    { label: 'ethylene', xyz: xyz(ETHYLENE), kind: 'species', charge: 0, multiplicity: 1 },
  ]) {
    const response = await page.request.post('/api/nodes', { data: body })
    expect(response.ok()).toBeTruthy()
    ids.push(((await response.json()) as { id: string }).id)
  }
  const edge = await page.request.post('/api/transitions', { data: { source_id: ids[0], target_id: ids[1] } })
  expect(edge.ok()).toBeTruthy()
  const edgeId = ((await edge.json()) as { id: string }).id
  const attached = await page.request.put(`/api/transitions/${edgeId}/species`, { data: { species_id: ids[2], direction: 'joins', count: 1 } })
  expect(attached.ok()).toBeTruthy()
  await page.reload()
  await canvasNode(page, 'alkylidene').click()
  await canvasNode(page, 'π-complex').click({ modifiers: ['Control'] })
  await page.getByLabel('Selection inspector').getByRole('button', { name: 'Scan path…' }).click()

  const dialog = page.getByRole('dialog', { name: 'Scan path' })
  await expect(dialog.getByLabel('Scan path plan')).toContainText('From “alkylidene” to “π-complex”')
  await expect(dialog.getByLabel('Match summary')).toContainText('2 bonds form')
  const summary = dialog.getByLabel('Species summary')
  await expect(summary).toContainText('“ethylene” joins: the path starts with it apart')
  await expect(summary).toContainText('from Ru4 through its centre until it is 4.0 Å from the rest')
  await expect(summary).toContainText('Ru4–C8')
  await expect(dialog.getByLabel('Species').locator('canvas')).toHaveCount(2)
  const contact = dialog.getByLabel('Closest contact')
  await contact.fill('6')
  await contact.press('Enter')
  await expect(summary).toContainText('until it is 6.0 Å from the rest')
  // Sending waits for the brief (D120's second part).
  await expect(dialog.getByRole('button', { name: 'Send' })).toBeDisabled()
})
