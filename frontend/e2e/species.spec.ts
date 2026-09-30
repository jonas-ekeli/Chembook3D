import { expect, test, type Page } from '@playwright/test'

const E2E_DIR = process.env.E2E_DIR!

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

function xyz(...elements: string[]): string {
  return [String(elements.length), '', ...elements.map((e, i) => `${e} ${(i * 1.5).toFixed(3)} 0 0`)].join('\n')
}

test('free species join or leave on an edge and balance its atoms', async ({ page }) => {
  // FR-SPC-01, 03, 04, 07; D69
  await newInvestigation(page, 'Free species')
  const post = async (path: string, data: object) => (await page.request.post(`/api${path}`, { data })).json()
  const a = await post('/nodes', { label: 'Ru=CH2', xyz: xyz('Ru', 'C', 'H', 'H'), pos_x: 0, pos_y: 0 })
  const b = await post('/nodes', {
    label: 'pi-complex',
    xyz: xyz('Ru', 'C', 'C', 'C', 'C', 'H', 'H', 'H', 'H', 'H', 'H', 'H', 'H'),
    pos_x: 320,
    pos_y: 0,
  })
  await post('/transitions', { source_id: a.id, target_id: b.id })
  await post('/nodes', { label: 'propene', kind: 'species', xyz: xyz('C', 'C', 'C', 'H', 'H', 'H', 'H', 'H', 'H') })
  await page.reload()

  // The species is listed beside the canvas, not drawn on it.
  const list = page.getByRole('list', { name: 'Free species list' })
  await expect(list.getByRole('button', { name: /propene/ })).toContainText('C3H6')
  await expect(page.getByRole('group', { name: 'Node propene', exact: true })).toHaveCount(0)

  // Without the joining propene the edge does not balance (W-BALANCE).
  const edgeLabel = page.locator('.edge-label')
  await expect(edgeLabel.getByRole('img', { name: 'Does not balance' })).toBeVisible()
  await page.getByRole('group', { name: 'Node pi-complex', exact: true }).click()
  await page
    .getByLabel('Node inspector')
    .getByRole('region', { name: 'Transitions' })
    .getByRole('button', { name: 'from Ru=CH2' })
    .click()
  const inspector = page.getByLabel('Transition inspector')
  await expect(inspector.getByLabel('Transition warnings')).toContainText('C3H6 more after than before')

  const add = inspector.getByRole('group', { name: 'Add a free species' })
  await expect(add.getByLabel('Species to add')).toHaveValue(/.+/)
  await add.getByLabel('Joins or leaves').selectOption('joins')
  await add.getByRole('button', { name: 'Add' }).click()
  await expect(edgeLabel.locator('.chip')).toHaveText('+ propene')
  await expect(edgeLabel.getByRole('img', { name: 'Does not balance' })).toHaveCount(0)
  await expect(inspector.getByLabel('Transition warnings')).toHaveCount(0)

  // The chip opens the species, which lists the edge it joins on.
  await inspector.getByRole('button', { name: '+ propene' }).click()
  const species = page.getByLabel('Node inspector')
  await expect(species.getByRole('heading', { name: 'propene' })).toBeVisible()
  await expect(species.getByText('Free species', { exact: true })).toBeVisible()
  await expect(species.getByRole('region', { name: 'Transitions' })).toContainText('Ru=CH2 → pi-complex')
  await expect(species.getByRole('button', { name: 'Make it a node' })).toBeDisabled()

  // A new species from the outline, and a loose node turned into one.
  await page.getByRole('region', { name: 'Free species', exact: true }).getByRole('button', { name: '+ Species' }).click()
  await expect(page.getByLabel('Node inspector').getByRole('heading', { name: 'Species 2' })).toBeVisible()
  await expect(list.getByRole('listitem')).toHaveCount(2)
  const loose = await post('/nodes', { label: 'ethylene', xyz: xyz('C', 'C', 'H', 'H', 'H', 'H'), pos_x: 640, pos_y: 0 })
  await page.reload()
  await page.getByRole('group', { name: 'Node ethylene', exact: true }).click()
  await page.getByLabel('Node inspector').getByRole('button', { name: 'Make it a free species' }).click()
  await expect(page.getByRole('group', { name: 'Node ethylene', exact: true })).toHaveCount(0)
  await expect(list.getByRole('listitem')).toHaveCount(3)
  expect((await (await page.request.get(`/api/nodes/${loose.id}`)).json()).kind).toBe('species')
})
