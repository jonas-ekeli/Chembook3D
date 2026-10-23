import { join } from 'node:path'
import { expect, test, type Page } from '@playwright/test'
import { startPage } from './appBar.ts'
import { forgetView } from './demoView.ts'

// T-UI-55, D178 (UI8): a quieter outline. Section heads with a count fold their section; step
// rows read as text with their actions only under the pointer or the keyboard; steps move by
// their buttons, Alt+↑/↓ or the grip; one line per node with its branch colour, a status dot,
// the TS tag and the formula.

const E2E_DIR = process.env.E2E_DIR!

async function openDemo(page: Page) {
  await forgetView(page.request, join(E2E_DIR, 'demo'))
  await page.goto('/')
  await page.getByRole('button', { name: /^Open/ }).first().click()
  const dialog = page.getByRole('dialog')
  await expect(dialog.getByRole('list', { name: 'Folders' })).toBeVisible()
  await dialog.getByLabel('Folder path').fill(join(E2E_DIR, 'demo'))
  await dialog.getByRole('button', { name: 'Go' }).click()
  await dialog.getByRole('button', { name: 'Open', exact: true }).click()
  await expect(page.getByRole('dialog')).toHaveCount(0)
}

async function newInvestigation(page: Page, name: string) {
  await startPage(page)
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

// Later tests start with no investigation open, even when this one fails.
test.afterEach(async ({ request }) => {
  await request.post('/api/investigations/close')
})

test('outline: node rows on one line, section counts that fold and stay folded', async ({ page }) => {
  await openDemo(page)
  const outline = page.getByRole('complementary', { name: 'Outline' })
  await expect(outline.getByRole('list', { name: 'Nodes' }).getByRole('button', { name: /^A-S2/ })).toBeVisible()
  const canvas = await (await page.request.get('/api/canvas')).json()
  const colourOf = (name: string) => canvas.branches.find((b: { name: string }) => b.name === name).colour

  // Heads: a count pill each, and the old add buttons by their names.
  const toggle = (name: string) => outline.getByRole('button', { name: new RegExp(`^${name}`) })
  await expect(toggle('Nodes')).toHaveText(`Nodes${canvas.nodes.length}`)
  await expect(toggle('Branches')).toHaveText(`Branches${canvas.branches.length}`)
  await expect(toggle('Free species')).toHaveAttribute('aria-expanded', 'true')
  await expect(outline.getByRole('button', { name: '+ Branch' })).toHaveText('Branch')
  await expect(outline.getByRole('button', { name: '+ Species' })).toBeVisible()
  await expect(outline.getByRole('button', { name: 'Import…' })).toBeVisible()
  await expect(outline.getByRole('button', { name: 'Import file…' })).toHaveText('Import…')

  // One line per node: branch bar, status dot, label with the TS tag, formula at the right.
  const nodes = outline.getByRole('list', { name: 'Nodes' })
  const row = (label: string) => nodes.getByRole('button', { name: new RegExp(`^${label}\\b`) })
  const ts = row('A-S2')
  await expect(ts.locator('.ts-mark')).toHaveText('TS')
  await expect(row('A-S1').locator('.ts-mark')).toHaveCount(0)
  await expect(ts.locator('.node-formula')).toHaveText('H2O')
  const bar = await ts.locator('.branch-bar').evaluate((el) => getComputedStyle(el).backgroundColor)
  const swatch = await page.evaluate((c) => {
    const probe = document.createElement('span')
    probe.style.color = c
    document.body.append(probe)
    const rgb = getComputedStyle(probe).color
    probe.remove()
    return rgb
  }, colourOf('A'))
  expect(bar).toBe(swatch)
  const dot = (label: string) => row(label).locator('.status-dot').evaluate((el) => getComputedStyle(el).backgroundColor)
  expect(await dot('A-S1')).not.toBe(await dot('A-S2')) // done green, planned grey, as on the cards
  await expect(row('A-S1')).toHaveAccessibleName(/Done$/)
  const box = await row('A-S1').boundingBox()
  expect(box!.height).toBeLessThan(36)

  // The search field filters, and the count says how many of all are shown.
  await outline.getByLabel('Search nodes').fill('S2')
  await expect(toggle('Nodes')).toHaveText(`Nodes2/${canvas.nodes.length}`)
  await outline.getByLabel('Search nodes').fill('')

  // A folded section hides its rows and stays folded after a reload.
  await toggle('Branches').click()
  await expect(toggle('Branches')).toHaveAttribute('aria-expanded', 'false')
  await expect(outline.getByRole('list', { name: 'Branch list' })).toBeHidden()
  await page.reload()
  await expect(toggle('Branches')).toHaveAttribute('aria-expanded', 'false')
  await expect(outline.getByRole('list', { name: 'Branch list' })).toBeHidden()
  await toggle('Branches').click()
  await expect(outline.getByRole('list', { name: 'Branch list' })).toBeVisible()
})

test('outline: step rows read as text; actions on hover and focus; move by keys and by dragging', async ({ page }) => {
  await newInvestigation(page, 'Outline steps')
  const steps = page.getByRole('region', { name: 'Reaction steps' })
  const field = steps.getByLabel('New step name')
  for (const [i, name] of ['alkylidene', 'π-complex', 'metallacycle'].entries()) {
    await field.fill(name)
    await field.press('Enter')
    await expect(steps.getByLabel(`Name of step ${i + 1}`)).toHaveValue(name)
  }
  // The head's + adds what is typed, or goes to the field.
  await field.fill('product')
  await steps.getByRole('button', { name: 'Add a step' }).click()
  await expect(steps.getByLabel('Name of step 4')).toHaveValue('product')
  await expect(steps.getByRole('button', { name: /^Reaction steps/ })).toHaveText('Reaction steps4')
  await steps.getByRole('button', { name: 'Add a step' }).click()
  await expect(field).toBeFocused()
  await expect(field).toHaveCSS('border-top-style', 'dashed')

  // A name is borderless text until hovered; the row's actions show only under the pointer.
  const name2 = steps.getByLabel('Name of step 2')
  await page.mouse.move(800, 500)
  await expect(name2).toHaveCSS('border-top-color', 'rgba(0, 0, 0, 0)')
  const remove = steps.getByRole('button', { name: 'Delete π-complex' })
  await expect(remove).toBeHidden()
  await name2.hover()
  await expect(remove).toBeVisible()
  await expect(steps.getByRole('button', { name: 'Move π-complex up' })).toBeVisible()
  await page.mouse.move(800, 500)
  await expect(remove).toBeHidden()

  // With the keyboard in the row they show too; Alt+↓ moves the step and keeps the focus.
  await name2.focus()
  await expect(remove).toBeVisible()
  await page.keyboard.press('Alt+ArrowDown')
  await expect(steps.getByLabel('Name of step 3')).toHaveValue('π-complex')
  await expect(steps.getByLabel('Name of step 3')).toBeFocused()
  await page.keyboard.press('Tab')
  await expect(steps.getByRole('button', { name: 'Move π-complex up' })).toBeFocused()
  await page.keyboard.press('Alt+ArrowUp')
  await expect(steps.getByLabel('Name of step 2')).toHaveValue('π-complex')
  await expect(steps.getByRole('button', { name: 'Move π-complex up' })).toBeFocused()
  await page.keyboard.press('Enter')
  await expect(steps.getByLabel('Name of step 1')).toHaveValue('π-complex')
  await expect(steps.getByLabel('Name of step 1')).toBeFocused() // its ↑ is disabled now

  // Dragging a step by its grip puts it where it is dropped.
  await steps.getByLabel('Name of step 4').hover()
  const grip = (await steps.locator('.grip').nth(3).boundingBox())!
  const first = (await steps.getByRole('listitem').nth(0).boundingBox())!
  await page.mouse.move(grip.x + grip.width / 2, grip.y + grip.height / 2)
  await page.mouse.down()
  await page.mouse.move(grip.x + 5, grip.y - 10, { steps: 5 })
  await page.mouse.move(first.x + 40, first.y + 4, { steps: 5 })
  await page.mouse.up()
  const names = async () => Promise.all([1, 2, 3, 4].map((i) => steps.getByLabel(`Name of step ${i}`).inputValue()))
  await expect.poll(names).toEqual(['product', 'π-complex', 'alkylidene', 'metallacycle'])

  // Delete still asks first.
  await steps.getByLabel('Name of step 1').hover()
  await steps.getByRole('button', { name: 'Delete product' }).click()
  const confirm = page.getByRole('dialog', { name: 'Delete reaction step?' })
  await confirm.getByRole('button', { name: 'Cancel' }).click()
  await expect(steps.getByLabel('Name of step 1')).toHaveValue('product')
  await steps.getByLabel('Name of step 1').hover()
  await steps.getByRole('button', { name: 'Delete product' }).click()
  await confirm.getByRole('button', { name: 'Delete step' }).click()
  await expect(steps.getByLabel('Name of step 1')).toHaveValue('π-complex')
  await expect(steps.getByLabel('Name of step 4')).toHaveCount(0)
})
