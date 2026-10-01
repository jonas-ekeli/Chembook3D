import { readFileSync } from 'node:fs'
import { expect, test, type Locator, type Page } from '@playwright/test'

// T-UI-03, FR-3D-04, FR-3D-07, D80: overlays on typed atoms, saved as an alignment set.
const E2E_DIR = process.env.E2E_DIR!

const CORE = [
  ['C', 0.0, 0.0, 0.0],
  ['H', 0.63, 0.63, 0.63],
  ['F', -0.8, -0.8, 0.8],
  ['Cl', -1.02, 1.02, -1.02],
  ['Br', 1.12, -1.12, -1.12],
] as const

function xyz(atoms: readonly (readonly [string, number, number, number])[]): string {
  return `${atoms.length}\n\n${atoms.map(([e, x, y, z]) => `${e} ${x} ${y} ${z}`).join('\n')}\n`
}

/** Turned 90° about z and shifted, so only an alignment puts it back. */
const turned = CORE.map(([e, x, y, z]) => [e, -y + 2, x - 1, z + 3] as const)

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

test('overlay three structures on chosen atoms and save the set', async ({ page }) => {
  test.slow() // software WebGL is slow on the Windows runners
  await newInvestigation(page, 'Overlay test')
  const bodies = [
    { label: 'core', xyz: xyz(CORE), pos_x: 100, pos_y: 100 },
    { label: 'turned', xyz: xyz(turned), pos_x: 400, pos_y: 100 },
    // An extra atom first shifts the numbering: the core is atoms 2-6 here.
    { label: 'with olefin', xyz: xyz([['O', 9, 9, 9], ...turned]), pos_x: 700, pos_y: 100 },
  ]
  for (const body of bodies) {
    const response = await page.request.post('/api/nodes', { data: body })
    expect(response.ok()).toBeTruthy()
  }
  await page.reload()
  await canvasNode(page, 'core').click()
  await canvasNode(page, 'turned').click({ modifiers: ['Control'] })
  await canvasNode(page, 'with olefin').click({ modifiers: ['Control'] })
  await page.getByLabel('Selection inspector').getByRole('button', { name: 'Overlay in 3D' }).click()
  const dialog = page.getByRole('dialog', { name: 'Overlay in 3D' })

  // The atoms differ, so each structure gets its own list, paired in order.
  await expect(dialog.getByLabel('Align on')).toHaveValue('atoms')
  await dialog.getByLabel('Atoms of core').fill('1-5')
  await dialog.getByLabel('Atoms of turned').fill('1-5')
  await dialog.getByLabel('Atoms of with olefin').fill('1, 2, 3')
  await dialog.getByRole('button', { name: 'Align', exact: true }).click()
  await expect(dialog.getByRole('alert')).toContainText('3 atoms are listed, but the reference has 5')
  await dialog.getByLabel('Atoms of with olefin').fill('2-6')
  await dialog.getByLabel('Atoms of with olefin').press('Enter')
  const legend = dialog.getByLabel('Overlay legend')
  await expect(legend.getByRole('row', { name: /turned/ })).toContainText('0.000 Å')
  await expect(legend.getByRole('row', { name: /with olefin/ })).toContainText('0.000 Å')
  await expect(dialog.getByRole('alert')).toHaveCount(0)
  await expect(dialog.getByTestId('viewer3d').locator('canvas')).toBeVisible()

  // Hiding a structure, and the aligned structures as one .xyz.
  await dialog.getByLabel('Show turned').uncheck()
  await expect(dialog.getByLabel('Show turned')).not.toBeChecked()
  const download = page.waitForEvent('download')
  await dialog.getByRole('button', { name: 'Save .xyz' }).click()
  const saved = readFileSync(await (await download).path(), 'utf8')
  expect(saved.split('\n').filter((line) => /^\d+$/.test(line.trim()))).toEqual(['5', '5', '6'])

  // Saved as a set, the lists come back in a new overlay.
  await dialog.getByRole('button', { name: 'Save as new set…' }).click()
  await dialog.getByLabel('Set name').fill('Core')
  await dialog.getByRole('button', { name: 'Save set' }).click()
  await expect(dialog.getByText('Saved “Core”.')).toBeVisible()
  await dialog.getByRole('button', { name: 'Close' }).click()
  await page.getByLabel('Selection inspector').getByRole('button', { name: 'Overlay in 3D' }).click()
  await dialog.getByLabel('Alignment set').selectOption({ label: 'Core' })
  await expect(dialog.getByLabel('Atoms of with olefin')).toHaveValue('2-6')
  await expect(dialog.getByLabel('Overlay legend').getByRole('row', { name: /with olefin/ })).toContainText('0.000 Å')

  // Picking shows the structure alone; the measuring row is replaced by the picking note.
  await dialog.getByRole('button', { name: 'Pick' }).first().click()
  await expect(dialog.getByLabel('Picking')).toContainText('Click the atoms of core')
  await dialog.getByRole('button', { name: 'Done' }).click()
  await expect(dialog.getByLabel('Picking')).toHaveCount(0)
})
