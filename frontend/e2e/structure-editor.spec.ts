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

// T-UI-42, D140: freeze an atom, hold a distance, change the held value and minimize.
test('hold, freeze and minimize', async ({ page }) => {
  test.slow() // software WebGL is slow on the Windows runners
  await newInvestigation(page, 'Minimize test')
  const made = await page.request.post('/api/nodes', {
    data: { label: 'ethane', xyz: ETHANE, charge: 0, multiplicity: 1, pos_x: 100, pos_y: 100 },
  })
  expect(made.ok()).toBeTruthy()
  await page.reload()
  await canvasNode(page, 'ethane').click()
  await page.getByRole('button', { name: 'Edit in 3D…' }).click()
  const dialog = page.getByRole('dialog', { name: 'Edit structure: “ethane”' })
  await expect(dialog.getByTestId('viewer3d').locator('canvas')).toBeVisible()
  const moves = dialog.getByRole('group', { name: 'What Minimize moves' })
  await expect(moves.getByLabel('Everything')).toBeChecked()

  // A hydrogen frozen; the C–C distance stretched, then held at 1.6 Å in the list.
  await pick(dialog, '3')
  await dialog.getByRole('button', { name: 'Freeze', exact: true }).click()
  const held = dialog.getByRole('list', { name: 'Held' })
  await expect(held).toContainText('H3 frozen')
  await pick(dialog, '1 2')
  await dialog.getByLabel('New distance').fill('1.75')
  await dialog.getByRole('button', { name: 'Hold', exact: true }).click()
  await expect(dialog.getByLabel('Last change')).toHaveText('Distance 1–2 held at 1.750 Å')
  await dialog.getByLabel('New distance').fill('1.75')
  await dialog.getByRole('button', { name: 'Set', exact: true }).click()
  await expect(dialog.getByLabel('Picked', { exact: true })).toHaveText('C1–C2: 1.750 Å')
  await expect(moves.getByText('The 4 changed atoms and two bonds round them')).toBeChecked()
  await held.getByLabel('Held distance C1–C2').fill('1.6')
  await held.getByLabel('Held distance C1–C2').press('Enter')
  await expect(dialog.getByLabel('Last change')).toHaveText('Distance 1–2 held at 1.600 Å')

  await dialog.getByRole('button', { name: 'Minimize', exact: true }).click()
  await expect(dialog.getByLabel('Last change')).toContainText('Minimized 7 atoms with UFF')
  await expect(dialog.getByLabel('Picked', { exact: true })).toHaveText(/^C1–C2: 1\.(59|60|61)\d Å$/)
  await dialog.getByRole('button', { name: 'Undo' }).click()
  await expect(dialog.getByLabel('Picked', { exact: true })).toHaveText('C1–C2: 1.750 Å')
  await dialog.getByRole('button', { name: 'Redo' }).click()
  await dialog.getByRole('button', { name: 'Save', exact: true }).click()
  await expect(dialog).toHaveCount(0)

  const saved = (await nodes(page)).find((n) => n.label === 'ethane')!
  const xyz = ((await (await page.request.get(`/api/nodes/${saved.id}`)).json()) as { xyz: string }).xyz
  const atoms = xyz.trim().split('\n').slice(2).map((line) => line.trim().split(/\s+/).slice(1).map(Number))
  expect(Math.hypot(...atoms[0].map((v, k) => v - atoms[1][k]))).toBeCloseTo(1.6, 1)
  expect(atoms[2]).toEqual([-0.363, 1.028, 0])
})

// T-UI-43, D141: a node without coordinates built from SMILES, a SMILES fragment, a lone atom
// and its hydrogens.
test('build a structure from nothing and from SMILES', async ({ page }) => {
  test.slow() // software WebGL is slow on the Windows runners
  await newInvestigation(page, 'SMILES test')
  const made = await page.request.post('/api/nodes', { data: { label: 'new', pos_x: 100, pos_y: 100 } })
  expect(made.ok()).toBeTruthy()
  await page.reload()
  await canvasNode(page, 'new').click()
  await page.getByRole('button', { name: 'Edit in 3D…' }).click()
  const dialog = page.getByRole('dialog', { name: 'Edit structure: “new”' })
  const summary = dialog.getByLabel('Structure summary')
  await expect(summary).toHaveText('No atoms · 0 atoms')

  await dialog.getByLabel('SMILES', { exact: true }).fill('CCO')
  await dialog.getByRole('button', { name: 'Add SMILES' }).click()
  await expect(summary).toHaveText('C2H6O · 9 atoms')
  await expect(dialog.getByTestId('viewer3d').locator('canvas')).toBeVisible()

  await pick(dialog, '4')
  await dialog.getByLabel('Fragment', { exact: true }).selectOption('*')
  await dialog.getByLabel('Fragment SMILES').fill('*c1ccccc1')
  await dialog.getByRole('button', { name: 'Replace', exact: true }).click()
  await expect(dialog.getByLabel('Last change')).toHaveText('H 4 replaced by *c1ccccc1')
  await expect(summary).toHaveText('C8H10O · 19 atoms')

  await dialog.getByRole('button', { name: 'Clear' }).click()
  await dialog.getByLabel('Lone atom').fill('N')
  await dialog.getByRole('button', { name: 'Add atom', exact: true }).click()
  await expect(dialog.getByLabel('Last change')).toHaveText('Added N as atom 20')
  await pick(dialog, '20')
  await dialog.getByRole('button', { name: 'Add hydrogens' }).click()
  await expect(dialog.getByLabel('Last change')).toHaveText('Added 3 H to atom 20')
  await expect(summary).toHaveText('C8H13NO · 23 atoms')

  await dialog.getByRole('button', { name: 'Save', exact: true }).click()
  await expect(dialog).toHaveCount(0)
  expect((await nodes(page)).find((n) => n.label === 'new')?.formula).toBe('C8H13NO')
})

// T-UI-44, D142: draw in Ketcher, convert to 3D as a molecule and as a fragment with an
// attachment point; the drawing is kept for the next Draw….
const CF3 = `
  Ketcher

  4  3  0  0  0  0  0  0  0  0999 V2000
    0.0000    0.0000    0.0000 C   0  0  0  0  0  0  0  0  0  0  0  0
    0.8660    0.5000    0.0000 F   0  0  0  0  0  0  0  0  0  0  0  0
   -0.8660    0.5000    0.0000 F   0  0  0  0  0  0  0  0  0  0  0  0
    0.0000   -1.0000    0.0000 F   0  0  0  0  0  0  0  0  0  0  0  0
  1  2  1  0     0  0
  1  3  1  0     0  0
  1  4  1  0     0  0
M  APO  1   1   1
M  END
`

// The drawer puts Ketcher's instance on the window, as Ketcher's own examples do.
declare global {
  interface Window {
    ketcher?: { getSmiles(): Promise<string>; setMolecule(structure: string): Promise<void> }
  }
}

async function drawer(page: Page): Promise<Locator> {
  const drawn = page.getByRole('dialog', { name: 'Draw', exact: true })
  await expect(drawn.getByRole('button', { name: 'Convert to 3D' })).toBeEnabled({ timeout: 60_000 })
  return drawn
}

test('draw in Ketcher and convert to 3D', async ({ page }) => {
  test.slow() // Ketcher and its WebAssembly load on first use
  await newInvestigation(page, 'Drawing test')
  const made = await page.request.post('/api/nodes', { data: { label: 'drawn', pos_x: 100, pos_y: 100 } })
  expect(made.ok()).toBeTruthy()
  await page.reload()
  await canvasNode(page, 'drawn').click()
  await page.getByRole('button', { name: 'Edit in 3D…' }).click()
  const dialog = page.getByRole('dialog', { name: 'Edit structure: “drawn”' })
  const summary = dialog.getByLabel('Structure summary')

  await dialog.getByRole('button', { name: 'Draw…' }).click()
  let drawn = await drawer(page)
  // Ketcher's own toolbar: benzene from the templates, put on the canvas.
  await drawn.getByTestId('template-0').click()
  await drawn.locator('.ketcher-drawer-editor').click({ position: { x: 600, y: 400 } })
  await expect.poll(() => page.evaluate(() => window.ketcher?.getSmiles())).toBe('C1C=CC=CC=1')
  await drawn.getByRole('button', { name: 'Convert to 3D' }).click()
  await expect(drawn).toHaveCount(0)
  await expect(dialog.getByLabel('Last change')).toHaveText('Added the drawing as atoms 1–12')
  await expect(summary).toHaveText('C6H6 · 12 atoms')

  // The drawing is still there; a fragment drawn with an attachment point replaces H 7.
  await pick(dialog, '7')
  await dialog.getByRole('button', { name: 'Draw…' }).click()
  drawn = await drawer(page)
  await expect.poll(() => page.evaluate(() => window.ketcher?.getSmiles())).toBe('C1C=CC=CC=1')
  await page.evaluate((molfile) => window.ketcher?.setMolecule(molfile), CF3)
  await drawn.getByRole('button', { name: 'Convert to 3D' }).click()
  await expect(drawn).toHaveCount(0)
  await expect(dialog.getByLabel('Last change')).toHaveText('H 7 replaced by the drawn fragment')
  await expect(summary).toHaveText('C7H5F3 · 15 atoms')

  await dialog.getByRole('button', { name: 'Save', exact: true }).click()
  await expect(dialog).toHaveCount(0)
  expect((await nodes(page)).find((n) => n.label === 'drawn')?.formula).toBe('C7H5F3')
})

// T-UI-45, D143: the structure into the drawer in 2D, changed there, and put back.
test('draw from the structure and put it back', async ({ page }) => {
  test.slow() // Ketcher and its WebAssembly load on first use
  await newInvestigation(page, 'Redraw test')
  const body = { label: 'ethane', xyz: ETHANE, charge: 0, multiplicity: 1, pos_x: 100, pos_y: 100 }
  expect((await page.request.post('/api/nodes', { data: body })).ok()).toBeTruthy()
  await page.reload()
  await canvasNode(page, 'ethane').click()
  await page.getByRole('button', { name: 'Edit in 3D…' }).click()
  const dialog = page.getByRole('dialog', { name: 'Edit structure: “ethane”' })
  const summary = dialog.getByLabel('Structure summary')
  await expect(summary).toHaveText('C2H6 · 8 atoms')

  await dialog.getByRole('button', { name: 'Draw from this structure…' }).click()
  const drawn = await drawer(page)
  await expect.poll(() => page.evaluate(() => window.ketcher?.getSmiles())).toBe('CC')
  // Unchanged, it is refused; with an OH it goes back on.
  await drawn.getByRole('button', { name: 'Convert to 3D' }).click()
  await expect(drawn.getByRole('alert')).toHaveText('The drawing has not changed')
  await page.evaluate(() => window.ketcher?.setMolecule('CCO'))
  await drawn.getByRole('button', { name: 'Convert to 3D' }).click()
  await expect(drawn).toHaveCount(0)
  await expect(dialog.getByLabel('Last change')).toHaveText(/^Drawing put on: 4 atoms added, 3 deleted, 1 changed/)
  await expect(summary).toHaveText('C2H6O · 9 atoms')

  await dialog.getByRole('button', { name: 'Save', exact: true }).click()
  await expect(dialog).toHaveCount(0)
  expect((await nodes(page)).find((n) => n.label === 'ethane')?.formula).toBe('C2H6O')
})
