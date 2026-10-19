import { expect, test, type Locator, type Page } from '@playwright/test'

// T-UI-41, D139: change a structure in the Edit structure window, undo and redo, put another
// node's structure beside it, and save in place, as a new node, or not at all.
const E2E_DIR = process.env.E2E_DIR!

const ETHANE = `8

C    0.000  0.000  0.000
C    1.530  0.000  0.000
H   -0.363  1.028  0.000
H   -0.363 -0.514  0.890
H   -0.363 -0.514 -0.890
H    1.893 -1.028  0.000
H    1.893  0.514  0.890
H    1.893  0.514 -0.890
`

const WATER = `3

O 0.000 0.000 0.117
H 0.000 0.757 -0.469
H 0.000 -0.757 -0.469
`

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

type ApiNode = { id: string; label: string; formula: string | null; derived_from_id: string | null }

async function nodes(page: Page): Promise<ApiNode[]> {
  return (await page.request.get('/api/nodes')).json()
}

async function pick(dialog: Locator, atoms: string) {
  await dialog.getByLabel('Picked atoms').fill(atoms)
  await dialog.getByLabel('Picked atoms').press('Enter')
}

test('edit a structure in 3D, undo, add a node beside it and save', async ({ page }) => {
  test.slow() // software WebGL is slow on the Windows runners
  await newInvestigation(page, 'Structure editor test')
  for (const body of [
    { label: 'ethane', xyz: ETHANE, charge: 0, multiplicity: 1, pos_x: 100, pos_y: 100 },
    { label: 'solvent', xyz: WATER, pos_x: 400, pos_y: 100 },
  ]) {
    expect((await page.request.post('/api/nodes', { data: body })).ok()).toBeTruthy()
  }
  await page.reload()
  await canvasNode(page, 'ethane').click()
  await page.getByRole('button', { name: 'Edit in 3D…' }).click()

  const dialog = page.getByRole('dialog', { name: 'Edit structure: “ethane”' })
  await expect(dialog.getByTestId('viewer3d').locator('canvas')).toBeVisible()
  const summary = dialog.getByLabel('Structure summary')
  await expect(summary).toHaveText('C2H6 · 8 atoms')
  await expect(dialog.getByRole('button', { name: 'Save', exact: true })).toBeDisabled()

  // A distance: the smaller side moves; both sides are offered.
  await pick(dialog, '1 2')
  await expect(dialog.getByLabel('Picked', { exact: true })).toHaveText('C1–C2: 1.530 Å')
  const sides = dialog.getByRole('group', { name: 'Which side moves' })
  await expect(sides.getByText("C1's side (4 atoms)")).toBeVisible()
  await expect(sides.getByText("C2's side (4 atoms)")).toBeVisible()
  await dialog.getByLabel('New distance').fill('1.6')
  await dialog.getByRole('button', { name: 'Set', exact: true }).click()
  await expect(dialog.getByLabel('Last change')).toHaveText("Distance 1–2 set to 1.600 Å: moved atom 2's side (4 atoms)")
  await expect(dialog.getByLabel('Picked', { exact: true })).toHaveText('C1–C2: 1.600 Å')

  // A hydrogen replaced by a methyl, then undone and redone.
  await pick(dialog, '3')
  await dialog.getByLabel('Fragment').selectOption('Me')
  await dialog.getByRole('button', { name: 'Replace', exact: true }).click()
  await expect(summary).toHaveText('C3H8 · 11 atoms')
  await dialog.getByRole('button', { name: 'Undo' }).click()
  await expect(summary).toHaveText('C2H6 · 8 atoms')
  await dialog.getByRole('button', { name: 'Redo' }).click()
  await expect(summary).toHaveText('C3H8 · 11 atoms')

  // A dihedral: the smaller side turns.
  await pick(dialog, '3 1 2 6')
  await expect(sides.getByText("H6's side (4 atoms)")).toBeVisible()
  await dialog.getByLabel('New dihedral').fill('60')
  await dialog.getByRole('button', { name: 'Set', exact: true }).click()
  await expect(dialog.getByLabel('Picked', { exact: true })).toHaveText('C3–C1–C2–H6: 60.0 °')

  // Another node's structure beside it.
  await dialog.getByRole('button', { name: 'Clear' }).click()
  await dialog.getByLabel('Molecule', { exact: true }).selectOption({ label: 'solvent' })
  await dialog.getByRole('button', { name: 'Add', exact: true }).click()
  await expect(dialog.getByLabel('Last change')).toHaveText('Added “solvent” as atoms 12–14')
  await expect(summary).toHaveText('C3H10O · 14 atoms')
  await dialog.getByRole('button', { name: 'Save', exact: true }).click()
  await expect(dialog).toHaveCount(0)
  let all = await nodes(page)
  expect(all.find((n) => n.label === 'ethane')?.formula).toBe('C3H10O')

  // Deleting an H leaves an odd electron count for a singlet; saved as a new node.
  await page.getByRole('button', { name: 'Edit in 3D…' }).click()
  await expect(summary).toHaveText('C3H10O · 14 atoms')
  await pick(dialog, '14')
  await dialog.getByRole('button', { name: 'Delete', exact: true }).click()
  await expect(dialog.getByLabel('Electrons')).toContainText('cannot have multiplicity 1')
  await dialog.getByRole('button', { name: 'Save as a new node' }).click()
  await expect(dialog).toHaveCount(0)
  all = await nodes(page)
  const original = all.find((n) => n.label === 'ethane')!
  const derived = all.find((n) => n.label === 'ethane (derived)')!
  expect(original.formula).toBe('C3H10O')
  expect(derived.formula).toBe('C3H9O')
  expect(derived.derived_from_id).toBe(original.id)

  // Closing with changes asks first; discarding keeps the node as it was.
  await canvasNode(page, 'ethane').click()
  await page.getByRole('button', { name: 'Edit in 3D…' }).click()
  await pick(dialog, '1')
  await dialog.getByLabel('New element').fill('N')
  await dialog.getByRole('button', { name: 'Change', exact: true }).click()
  await expect(summary).toHaveText('C2H10NO · 14 atoms')
  await dialog.getByRole('button', { name: 'Close', exact: true }).click()
  const ask = page.getByRole('dialog', { name: 'Close without saving?' })
  await ask.getByRole('button', { name: 'Discard changes' }).click()
  await expect(page.getByRole('dialog')).toHaveCount(0)
  expect((await nodes(page)).find((n) => n.label === 'ethane')?.formula).toBe('C3H10O')
})
