import { expect, test, type Locator, type Page } from '@playwright/test'

// T-UI-18, D114: a scan path between two nodes joined by an edge, one of them a TS guess.
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

test('a scan path to a TS guess asks for its coordinates and sends the job', async ({ page }) => {
  test.slow() // software WebGL is slow on the Windows runners
  await newInvestigation(page, 'Scan path test')
  // The end is the start numbered backwards with one O–H stretched to a partial bond: a guess.
  const stretched = reversed.map((atom, i) => (i === reversed.length - 7 ? (['H', atom[1] + 0.45, atom[2] + 0.45, atom[3]] as const) : atom))
  const ids: string[] = []
  for (const body of [
    { label: 'start', xyz: xyz(GLYCEROL), pos_x: 100, pos_y: 100, charge: 0, multiplicity: 1 },
    { label: 'guess', xyz: xyz(stretched), pos_x: 400, pos_y: 100, role: 'transition_state' },
  ]) {
    const response = await page.request.post('/api/nodes', { data: body })
    expect(response.ok()).toBeTruthy()
    ids.push(((await response.json()) as { id: string }).id)
  }
  const edge = await page.request.post('/api/transitions', { data: { source_id: ids[0], target_id: ids[1] } })
  expect(edge.ok()).toBeTruthy()
  await page.reload()
  await canvasNode(page, 'guess').click()
  await canvasNode(page, 'start').click({ modifiers: ['Control'] })
  await page.getByLabel('Selection inspector').getByRole('button', { name: 'Scan path…' }).click()

  const dialog = page.getByRole('dialog', { name: 'Scan path' })
  // The edge runs from start to guess, whatever the order of selection.
  await expect(dialog.getByLabel('Scan path plan')).toContainText('From “start” to “guess”')
  await expect(dialog.getByLabel('Match summary')).toContainText('1 bond breaks')
  await expect(dialog.getByText('“guess” is a TS guess with no imaginary mode to read')).toBeVisible()
  const send = dialog.getByRole('button', { name: 'Send' })
  await expect(send).toBeDisabled()
  const held = dialog.getByLabel('Held at the end')
  const boxes = held.getByRole('checkbox')
  expect(await boxes.count()).toBeGreaterThan(0)
  await boxes.first().check()
  await expect(send).toBeEnabled()
  await boxes.first().uncheck()
  // A coordinate typed by hand is held too.
  await dialog.getByLabel('Atoms to hold').fill('1 2 3')
  await dialog.getByRole('button', { name: 'Add', exact: true }).click()
  await expect(held.getByRole('checkbox', { name: 'Hold angle 1–2–3' })).toBeChecked()
  await expect(dialog.getByLabel('Solvent')).toHaveValue('')
  await dialog.getByLabel('Solvent').selectOption('toluene')
  await send.click()
  // The demo investigation is not linked to GitHub: the job is kept and the reason given.
  await expect(dialog.getByRole('alert')).toContainText('is saved but could not be started')
  const jobs = (await (await page.request.get('/api/jobs')).json()) as { kind: string; scan_path: { solvent: string; held: { atoms: number[] }[] } }[]
  expect(jobs).toHaveLength(1)
  expect(jobs[0].kind).toBe('scan_path')
  expect(jobs[0].scan_path.solvent).toBe('toluene')
  expect(jobs[0].scan_path.held.map((h) => h.atoms)).toEqual([[1, 2, 3]])
})

test('a coordinate typed by hand is held at a TS that is the start of the path', async ({ page }) => {
  test.slow() // software WebGL is slow on the Windows runners
  await newInvestigation(page, 'Scan path from a TS')
  const stretched = reversed.map((atom, i) => (i === reversed.length - 7 ? (['H', atom[1] + 0.45, atom[2] + 0.45, atom[3]] as const) : atom))
  const ids: string[] = []
  for (const body of [
    { label: 'guess', xyz: xyz(stretched), pos_x: 100, pos_y: 100, role: 'transition_state', charge: 0, multiplicity: 1 },
    { label: 'product', xyz: xyz(GLYCEROL), pos_x: 400, pos_y: 100, charge: 0, multiplicity: 1 },
  ]) {
    const response = await page.request.post('/api/nodes', { data: body })
    expect(response.ok()).toBeTruthy()
    ids.push(((await response.json()) as { id: string }).id)
  }
  const edge = await page.request.post('/api/transitions', { data: { source_id: ids[0], target_id: ids[1] } })
  expect(edge.ok()).toBeTruthy()
  await page.reload()
  await canvasNode(page, 'guess').click()
  await canvasNode(page, 'product').click({ modifiers: ['Control'] })
  await page.getByLabel('Selection inspector').getByRole('button', { name: 'Scan path…' }).click()

  const dialog = page.getByRole('dialog', { name: 'Scan path' })
  await expect(dialog.getByLabel('Scan path plan')).toContainText('From “guess” to “product”')
  const held = dialog.getByLabel('Held at the start')
  await expect(held).toBeVisible()
  const atoms = dialog.getByLabel('Atoms to hold')
  await atoms.fill('2 3')
  await atoms.press('Enter')
  const typed = held.getByRole('checkbox', { name: 'Hold distance 2–3' })
  await expect(typed).toBeChecked()
  await expect(dialog.getByRole('alert')).toHaveCount(0)
  // The same bond typed the other way round is not listed twice.
  await typed.uncheck()
  await atoms.fill('3 2')
  await atoms.press('Enter')
  await expect(held.getByRole('checkbox', { name: /^Hold distance (2–3|3–2)$/ })).toHaveCount(1)
  await expect(typed).toBeChecked()
  await expect(dialog.getByRole('button', { name: 'Send' })).toBeEnabled()
})

test('two conformers get a warning that no bond changes', async ({ page }) => {
  // T-UI-20, D116: the same structure numbered backwards and turned: nothing forms or breaks.
  test.slow() // software WebGL is slow on the Windows runners
  await newInvestigation(page, 'Scan path between conformers')
  const ids: string[] = []
  for (const body of [
    { label: 'one', xyz: xyz(GLYCEROL), pos_x: 100, pos_y: 100, charge: 0, multiplicity: 1 },
    { label: 'two', xyz: xyz(reversed), pos_x: 400, pos_y: 100, charge: 0, multiplicity: 1 },
  ]) {
    const response = await page.request.post('/api/nodes', { data: body })
    expect(response.ok()).toBeTruthy()
    ids.push(((await response.json()) as { id: string }).id)
  }
  const edge = await page.request.post('/api/transitions', { data: { source_id: ids[0], target_id: ids[1] } })
  expect(edge.ok()).toBeTruthy()
  await page.reload()
  await canvasNode(page, 'one').click()
  await canvasNode(page, 'two').click({ modifiers: ['Control'] })
  await page.getByLabel('Selection inspector').getByRole('button', { name: 'Scan path…' }).click()

  const dialog = page.getByRole('dialog', { name: 'Scan path' })
  await expect(dialog.getByLabel('Scan path plan')).toContainText('From “one” to “two”')
  await expect(dialog.getByLabel('Scan path warning')).toContainText('No bond forms or breaks')
  await expect(dialog.getByRole('button', { name: 'Send' })).toBeEnabled()
})
