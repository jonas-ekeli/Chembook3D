import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { expect, test, type Page } from '@playwright/test'
import { startPage } from './appBar.ts'
import { forgetView } from './demoView.ts'

// T-UI-58, D181 (UI9): the energy drawer's head is one compact row (Pathways with an icon,
// Profile | Table, the zoom as one group, Style…, one Export menu); on screen the profile's text
// is 1.25× the style's, a hovered level is drawn thicker with a tooltip, a click selects its node
// on the canvas and the canvas selection is marked; the saved figure has none of it.

const E2E_DIR = process.env.E2E_DIR!

async function openDemo(page: Page) {
  await forgetView(page.request, join(E2E_DIR, 'demo'))
  await page.request.put('/api/settings', { data: { energy_unit: 'kcal/mol' } })
  await startPage(page)
  await page.getByRole('button', { name: /^Open/ }).first().click()
  const dialog = page.getByRole('dialog')
  await expect(dialog.getByRole('list', { name: 'Folders' })).toBeVisible()
  await dialog.getByLabel('Folder path').fill(join(E2E_DIR, 'demo'))
  await dialog.getByRole('button', { name: 'Go' }).click()
  await dialog.getByRole('button', { name: 'Open', exact: true }).click()
  await expect(page.getByRole('dialog')).toHaveCount(0)
  await expect(page.getByRole('group', { name: 'Node T-S0', exact: true })).toBeVisible()
}

/** The figure as saved: the SVG export's text. */
async function savedSvg(page: Page): Promise<string> {
  const download = page.waitForEvent('download')
  await page.getByLabel('Energy drawer').getByRole('button', { name: 'Export ▾' }).click()
  await page.getByRole('menuitem', { name: 'Profile as SVG' }).click()
  return readFileSync(await (await download).path(), 'utf8')
}

test('energy drawer: a compact head and a profile you can point at', async ({ page }) => {
  try {
    await openDemo(page)
    await page.getByRole('button', { name: 'Profile and table' }).click()
    const drawer = page.getByLabel('Energy drawer')
    await drawer.getByLabel('Add branch pathway').selectOption({ label: 'A' })
    await drawer.getByLabel('Add branch pathway').selectOption({ label: 'B' })
    await expect(drawer.getByRole('group', { name: 'Pathway B' })).toContainText('T-S0 → B-S1 → B-S2 → B-S3')
    const head = drawer.locator('.drawer-head')

    // The head: one row, the tabs Profile | Table, one Fit, no separate save buttons.
    const pathways = head.getByRole('button', { name: 'Pathways' })
    await expect(pathways).toHaveAttribute('aria-pressed', 'true')
    await expect(pathways.locator('svg')).toHaveCount(1)
    await expect(head.getByRole('group', { name: 'Drawer tab' }).getByRole('button')).toHaveText(['Profile', 'Table'])
    const zoom = head.getByRole('group', { name: 'Zoom' }).getByRole('button')
    await expect(zoom).toHaveText(['−', 'Fit', '+'])
    await expect(head.getByRole('button', { name: /^Save profile/ })).toHaveCount(0)
    await expect(head.getByRole('button', { name: 'Style…' })).toBeVisible()
    expect((await head.boundingBox())!.height).toBeLessThan(44)
    await head.getByRole('button', { name: 'Export ▾' }).click()
    await expect(page.getByRole('menu', { name: 'Export' }).getByRole('menuitem')).toHaveText([
      'Profile as PNG',
      'Profile as SVG',
      'Table as CSV',
    ])
    await page.keyboard.press('Escape')
    await expect(page.getByRole('menu', { name: 'Export' })).toHaveCount(0)

    // On screen the text is 1.25× the style's 11 (the title 2 more).
    const chart = drawer.locator('.drawer-main').getByRole('img', { name: 'Energy profile' })
    const title = chart.locator('text', { hasText: /^ΔG.* at / })
    await expect(title).toHaveAttribute('font-size', String(11 * 1.25 + 2))

    // Hovering B-S2's name: its level thicker, and a dark tooltip with its numbers.
    const level = (label: string) => chart.locator('g.profile-level', { has: page.locator('text', { hasText: label }) })
    await chart.locator('text', { hasText: /^B-S2/ }).hover()
    const tip = page.getByRole('tooltip')
    await expect(tip).toContainText('B-S2')
    await expect(tip).toContainText('TS · [2+2] TS · branch B')
    await expect(tip).toContainText('ΔG (rel. T-S0)17.30 kcal/mol')
    // D177: from the lowest point since the previous TS (B-S1, −2.10), as the table's column.
    await expect(tip).toContainText('ΔG‡ from B-S119.40 kcal/mol')
    await expect(tip).toContainText('Click to select it on the canvas')
    expect(await tip.evaluate((el) => getComputedStyle(el).backgroundColor)).toBe('rgb(28, 36, 48)')
    const bs2 = level('B-S2').locator('line.profile-level-line')
    await expect(bs2).toHaveClass(/hovered/)
    expect(Number(await bs2.getAttribute('stroke-width'))).toBeGreaterThan(3.5)
    // A minimum: a real minus sign and no barrier.
    await chart.locator('text', { hasText: /^A-S1$/ }).hover()
    await expect(tip).toContainText('A-S1')
    await expect(tip).toContainText('−3.20 kcal/mol')
    await expect(tip).not.toContainText('‡')
    await page.mouse.move(5, 5)
    await expect(tip).toHaveCount(0)

    // A click selects the node on the canvas; the canvas selection is marked on the chart.
    await chart.locator('text', { hasText: /^B-S2/ }).click()
    await expect(page.getByLabel('Node inspector').locator('.node-head')).toContainText('B-S2')
    await expect(chart.locator('.profile-selected')).toHaveCount(1)
    await expect(level('B-S2').locator('.profile-selected')).toHaveCount(1)
    await page.getByRole('group', { name: 'Node A-S1', exact: true }).click()
    await expect(level('A-S1').locator('.profile-selected')).toHaveCount(1)
    await expect(chart.locator('.profile-selected')).toHaveCount(1)

    // Zoomed, a click on a level still selects (the drag that pans starts only once it moves).
    await zoom.nth(2).click()
    await zoom.nth(2).click()
    await expect(zoom.nth(1)).toHaveText(/%$/)
    await chart.locator('text', { hasText: /^B-S2/ }).click()
    await expect(page.getByLabel('Node inspector').locator('.node-head')).toContainText('B-S2')
    // The level between − and + goes back to Fit, and from Fit to 100 %.
    await zoom.nth(1).click()
    await expect(zoom.nth(1)).toHaveText('Fit')
    await zoom.nth(1).click()
    await expect(zoom.nth(1)).toHaveText('100 %')
    await zoom.nth(1).click()
    await page.screenshot({ path: 'test-results/energy-drawer.png' })

    // The saved figure is the style's own, as the Style dialog previews it: the style's text
    // sizes and no hover, selection or screen marks, with a level hovered and B-S2 selected.
    await chart.locator('text', { hasText: /^B-S2/ }).hover()
    await expect(tip).toBeVisible()
    const svg = await savedSvg(page)
    expect(svg).toContain('font-size="13" font-weight="600"')
    expect(svg).not.toContain('15.75')
    for (const mark of ['profile-', 'transparent', 'style=', 'class=']) expect(svg).not.toContain(mark)
    await drawer.getByRole('button', { name: 'Style…' }).click()
    const dialog = page.getByRole('dialog', { name: 'Profile style' })
    const preview = await dialog
      .getByLabel('Preview')
      .getByRole('img', { name: 'Energy profile' })
      .evaluate((element) => {
        const copy = element.cloneNode(true) as SVGSVGElement
        copy.removeAttribute('style')
        copy.removeAttribute('class')
        copy.querySelectorAll('[data-check]').forEach((mark) => mark.remove())
        return new XMLSerializer().serializeToString(copy)
      })
    expect(svg).toBe(preview)
    await dialog.getByRole('button', { name: 'Cancel' }).click()

    // The Table tab keeps Export (the CSV) but not the zoom or Style….
    await head.getByRole('button', { name: 'Table', exact: true }).click()
    await expect(head.getByRole('group', { name: 'Zoom' })).toHaveCount(0)
    await expect(head.getByRole('button', { name: 'Style…' })).toHaveCount(0)
    const csv = page.waitForEvent('download')
    await head.getByRole('button', { name: 'Export ▾' }).click()
    await page.getByRole('menuitem', { name: 'Table as CSV' }).click()
    expect((await csv).suggestedFilename()).toBe('energy-table.csv')
  } finally {
    await forgetView(page.request, join(E2E_DIR, 'demo'))
  }
})

// T-UI-64, D188: a node label longer than the pathways list is wide wraps in the list, so the
// pathway's own buttons stay beside its name instead of under the profile; the node panel's
// head keeps its buttons in the window the same way.
test('energy drawer: a long node label wraps instead of hiding the pathway buttons', async ({ page }) => {
  const long = 'X2Prime_guess_from_scan_TSGO_part2_CAAC_iPr_otBu_conformer7'
  let id = ''
  try {
    await openDemo(page)
    const canvas = await (await page.request.get('/api/canvas')).json()
    id = canvas.nodes.find((n: { label: string }) => n.label === 'B-S1').id
    await page.request.patch(`/api/nodes/${id}`, { data: { label: long } })
    await page.getByRole('group', { name: 'Node T-S0', exact: true }).click()
    await page.getByRole('button', { name: 'Profile and table' }).click()
    const drawer = page.getByLabel('Energy drawer')
    await drawer.getByRole('button', { name: 'Start at selected node', exact: true }).click()
    const pathway = drawer.getByRole('group', { name: /^Pathway / })
    const choice = pathway.getByRole('group', { name: 'Continue with' }).getByRole('button', { name: long })
    await expect(choice).toBeVisible()

    const right = async (locator: typeof drawer) => {
      const box = (await locator.boundingBox())!
      return box.x + box.width
    }
    const list = await right(drawer.getByLabel('Pathways', { exact: true }))
    expect(list).toBeLessThanOrEqual((await drawer.locator('.drawer-main').boundingBox())!.x + 1)
    for (const button of [
      pathway.getByRole('button', { name: 'Undo step' }),
      pathway.getByRole('button', { name: 'Sterics' }),
      pathway.getByRole('button', { name: /^Remove pathway/ }),
      choice,
    ])
      expect(await right(button)).toBeLessThanOrEqual(list)
    // Taking the long node: its link in the pathway wraps too.
    await choice.click()
    const nodes = pathway.locator('.pathway-nodes')
    await expect(nodes).toContainText(long)
    expect(await right(pathway.getByRole('button', { name: /^Remove pathway/ }))).toBeLessThanOrEqual(list)
    expect(await right(nodes.getByRole('button', { name: long }))).toBeLessThanOrEqual(list)

    // T-S0's panel lists a transition to the long node; the head's buttons stay in the window.
    const panel = page.getByLabel('Node inspector')
    await expect(panel.getByLabel('Transitions')).toContainText(long)
    const width = page.viewportSize()!.width
    expect(await right(panel.getByRole('button', { name: 'More node actions' }))).toBeLessThanOrEqual(width)
    expect(await right(panel.getByLabel('Transitions'))).toBeLessThanOrEqual(width)
  } finally {
    if (id) await page.request.patch(`/api/nodes/${id}`, { data: { label: 'B-S1' } })
    await forgetView(page.request, join(E2E_DIR, 'demo'))
  }
})

// T-UI-77, A138: a pathway that closes a cycle shows Turnover beside Undo step, Sterics and the
// remove button; its name keeps its width and the buttons wrap to the next line instead.
test('energy drawer: a pathway name is not squeezed by its buttons', async ({ page }) => {
  let closing = ''
  try {
    await openDemo(page)
    const canvas = await (await page.request.get('/api/canvas')).json()
    const id = (label: string) => canvas.nodes.find((n: { label: string }) => n.label === label).id
    const response = await page.request.post('/api/transitions', { data: { source_id: id('B-S3'), target_id: id('T-S0') } })
    closing = (await response.json()).id
    await page.getByRole('group', { name: 'Node B-S3', exact: true }).click()
    await page.getByRole('button', { name: 'Profile and table' }).click()
    const drawer = page.getByLabel('Energy drawer')
    await drawer.getByRole('button', { name: 'Start at selected node', exact: true }).click()
    const pathway = drawer.getByRole('group', { name: /^Pathway / })
    // Single edges are followed: B-S3 → T-S0, then B-S1 on to the cycle's close at B-S3.
    await pathway.getByRole('group', { name: 'Continue with' }).getByRole('button', { name: 'B-S1' }).click()
    await expect(pathway).toContainText('Cycle closed.')
    await expect(pathway.getByRole('button', { name: 'Turnover' })).toBeVisible()

    const name = pathway.locator('.pathway-head strong')
    await expect(name).toHaveText('B-S3 → B-S3')
    const box = (await name.boundingBox())!
    const lineHeight = parseFloat(await name.evaluate((el) => getComputedStyle(el).lineHeight))
    expect(box.height).toBeLessThan(lineHeight * 1.5) // one line
    const list = (await drawer.getByLabel('Pathways', { exact: true }).boundingBox())!
    for (const label of ['Undo step', 'Turnover', 'Sterics']) {
      const button = (await pathway.getByRole('button', { name: label }).boundingBox())!
      expect(button.x + button.width).toBeLessThanOrEqual(list.x + list.width)
    }
  } finally {
    if (closing) await page.request.delete(`/api/transitions/${closing}`)
    await forgetView(page.request, join(E2E_DIR, 'demo'))
  }
})

// T-UI-65, D189: Undo step offers every way on from the node before, so a fork can be taken
// the other way, and an edge drawn after a pathway stopped is offered when you come back.
test('energy drawer: a pathway can always go on after Undo step or a new edge', async ({ page }) => {
  let closing = ''
  try {
    await openDemo(page)
    await page.getByRole('button', { name: 'Profile and table' }).click()
    const drawer = page.getByLabel('Energy drawer')
    await drawer.getByLabel('Add branch pathway').selectOption({ label: 'A' })
    const a = drawer.getByRole('group', { name: 'Pathway A' })
    const choices = a.getByRole('group', { name: 'Continue with' })
    await choices.getByRole('button', { name: 'A-S3' }).click()
    await expect(a).toContainText('T-S0 → A-S1 → A-S3')
    await expect(choices).toHaveCount(0)

    // Back to the fork: both ways are offered again, and the other one can be taken.
    await a.getByRole('button', { name: 'Undo step' }).click()
    await expect(a.locator('.pathway-nodes')).toHaveText('T-S0 → A-S1')
    await expect(choices.getByRole('button')).toHaveText(['A-S2', 'A-S3'])
    await choices.getByRole('button', { name: 'A-S2' }).click()
    await expect(a).toContainText('T-S0 → A-S1 → A-S2')

    // One step back from a single edge offers that edge too.
    while ((await a.locator('.pathway-nodes').textContent()) !== 'T-S0 → A-S1') {
      const before = await a.locator('.pathway-nodes').textContent()
      await a.getByRole('button', { name: 'Undo step' }).click()
      await expect(a.locator('.pathway-nodes')).not.toHaveText(before!)
    }
    await a.getByRole('button', { name: 'Undo step' }).click()
    await expect(a.locator('.pathway-nodes')).toHaveText('T-S0')
    await expect(choices.getByRole('button')).toHaveText(['A-S1'])

    // Pathway B stops at B-S3; an edge drawn from there later is offered after a reload.
    await drawer.getByLabel('Add branch pathway').selectOption({ label: 'B' })
    const b = drawer.getByRole('group', { name: 'Pathway B' })
    await expect(b).toContainText('T-S0 → B-S1 → B-S2 → B-S3')
    await expect(b.getByRole('group', { name: 'Continue with' })).toHaveCount(0)
    const canvas = await (await page.request.get('/api/canvas')).json()
    const id = (label: string) => canvas.nodes.find((n: { label: string }) => n.label === label).id
    const response = await page.request.post('/api/transitions', { data: { source_id: id('B-S3'), target_id: id('T-S0') } })
    closing = (await response.json()).id
    await page.reload()
    // D105: the drawer comes back open, with its pathways.
    const again = page.getByLabel('Energy drawer').getByRole('group', { name: 'Pathway B' })
    const onward = again.getByRole('group', { name: 'Continue with' })
    await expect(onward.getByRole('button')).toHaveText(['T-S0'])
    await onward.getByRole('button', { name: 'T-S0' }).click()
    await expect(again).toContainText('Cycle closed.')
  } finally {
    if (closing) await page.request.delete(`/api/transitions/${closing}`)
    await forgetView(page.request, join(E2E_DIR, 'demo'))
  }
})

// T-UI-66, D190: saved energy profiles. The first pathway makes "Profile 1"; each pathway has
// "Show on the figure" and any number are drawn together; New, Rename, Duplicate and Delete;
// the drawer comes back on the profile it had open, and the Analyses view opens any of them.
test('energy drawer: saved profiles with pathways shown on the figure', async ({ page }) => {
  try {
    await openDemo(page)
    await page.getByRole('button', { name: 'Profile and table' }).click()
    const drawer = page.getByLabel('Energy drawer')
    const picker = drawer.getByRole('combobox', { name: 'Open profile' })
    await expect(picker).toBeDisabled()
    await expect(picker).toHaveText('No saved profiles')

    await drawer.getByLabel('Add branch pathway').selectOption({ label: 'A' })
    await expect(picker.locator('option:checked')).toHaveText('Profile 1')
    await drawer.getByLabel('Add branch pathway').selectOption({ label: 'B' })
    const b = drawer.getByRole('group', { name: 'Pathway B' })
    await expect(b).toContainText('T-S0 → B-S1 → B-S2 → B-S3')
    const chart = drawer.locator('.drawer-main').getByRole('img', { name: 'Energy profile' })
    // Both pathways drawn together: A's A-S1 and B's TS at 17.30.
    await expect(chart.locator('text', { hasText: /^A-S1/ })).toHaveCount(1)
    await expect(chart).toContainText('17.30')

    // Left off the figure, B stays in the list; ticked again, it is back.
    const showB = drawer.getByLabel('Show B on the figure')
    await showB.uncheck()
    await expect(chart).not.toContainText('17.30')
    await expect(chart.locator('text', { hasText: /^A-S1/ })).toHaveCount(1)
    await expect(b).toBeVisible()
    await drawer.getByLabel('Show A on the figure').uncheck()
    await expect(drawer.getByText('No pathway shown')).toBeVisible()
    await drawer.getByLabel('Show A on the figure').check()
    await showB.check()
    await expect(chart).toContainText('17.30')

    // A second profile, renamed, with B only.
    await drawer.getByRole('button', { name: 'New', exact: true }).click()
    await expect(picker.locator('option:checked')).toHaveText('Profile 2')
    await expect(drawer.getByRole('group', { name: /^Pathway / })).toHaveCount(0)
    await drawer.getByLabel('Add branch pathway').selectOption({ label: 'B' })
    await drawer.getByRole('button', { name: 'Profile actions' }).click()
    await page.getByRole('menuitem', { name: 'Rename' }).click()
    await drawer.getByLabel('Profile name').fill('Only B')
    await drawer.getByLabel('Profile name').press('Enter')
    await expect(picker.locator('option:checked')).toHaveText('Only B')

    // Switching back finds Profile 1 as it was left.
    await picker.selectOption({ label: 'Profile 1' })
    await expect(drawer.getByRole('group', { name: /^Pathway / })).toHaveCount(2)
    await picker.selectOption({ label: 'Only B' })
    await expect(drawer.getByRole('group', { name: /^Pathway / })).toHaveCount(1)

    // After a reload the drawer opens on the profile it had open.
    await page.reload()
    await expect(picker.locator('option:checked')).toHaveText('Only B')

    // The Analyses view lists the profiles and opens one in the drawer.
    await page.getByRole('button', { name: 'Analyses' }).click()
    const list = page.getByRole('navigation', { name: 'Analyses' }).getByRole('list', { name: 'Energy profiles' })
    await expect(list.getByRole('listitem')).toHaveText(['Profile 1 2 of 2 pathways shown', 'Only B 1 of 1 pathway shown'])
    await list.getByRole('button', { name: 'Profile 1' }).click()
    await expect(picker.locator('option:checked')).toHaveText('Profile 1')
    await expect(drawer.getByRole('group', { name: /^Pathway / })).toHaveCount(2)

    // Duplicate, then delete the copy.
    await drawer.getByRole('button', { name: 'Profile actions' }).click()
    await page.getByRole('menuitem', { name: 'Duplicate' }).click()
    await expect(picker.locator('option:checked')).toHaveText('Profile 1 (copy)')
    await expect(drawer.getByRole('group', { name: /^Pathway / })).toHaveCount(2)
    await drawer.getByRole('button', { name: 'Profile actions' }).click()
    await page.getByRole('menuitem', { name: 'Delete…' }).click()
    const dialog = page.getByRole('dialog', { name: 'Delete profile' })
    await expect(dialog).toContainText('“Profile 1 (copy)”')
    await dialog.getByRole('button', { name: 'Delete' }).click()
    await expect(picker.locator('option')).toHaveText(['Profile 1', 'Only B'])
  } finally {
    await forgetView(page.request, join(E2E_DIR, 'demo'))
  }
})

// T-UI-70, D194: a pathway's step moved along the axis takes the steps after it along, the steps
// before it stay put; it is saved with the profile, goes into the saved figure, and moves back.
test('energy drawer: a step moved along the axis takes the later steps with it', async ({ page }) => {
  try {
    await openDemo(page)
    await page.getByRole('button', { name: 'Profile and table' }).click()
    const drawer = page.getByLabel('Energy drawer')
    await drawer.getByLabel('Add branch pathway').selectOption({ label: 'A' })
    await drawer.getByLabel('Add branch pathway').selectOption({ label: 'B' })
    const b = drawer.getByRole('group', { name: 'Pathway B' })
    await expect(b).toContainText('T-S0 → B-S1 → B-S2 → B-S3')
    const chart = drawer.locator('.drawer-main').getByRole('img', { name: 'Energy profile' })
    const xOf = async (name: RegExp) => Number(await chart.locator('text', { hasText: name }).first().getAttribute('x'))
    await expect(chart.locator('text', { hasText: /^B-S3/ })).toHaveCount(1)
    const before = { s0: await xOf(/^T-S0/), s1: await xOf(/^B-S1/), s2: await xOf(/^B-S2/), s3: await xOf(/^B-S3/) }

    const move = b.getByRole('group', { name: 'Move B along the axis' })
    const step = move.getByLabel('Step to move')
    await expect(move.getByRole('button', { name: 'Move back' })).toBeDisabled()
    await step.selectOption({ label: '3. B-S2' })
    await move.getByRole('button', { name: 'Move on' }).click()
    await move.getByRole('button', { name: 'Move on' }).click()
    await expect(step.locator('option:checked')).toHaveText('3. B-S2 (+2)')
    await expect(b.locator('.pathway-nodes')).toContainText('B-S1 → +2 B-S2')
    // The figure gains two columns, so every column is narrower.
    await expect.poll(() => xOf(/^B-S2/)).toBeGreaterThan(before.s2)
    const after = { s0: await xOf(/^T-S0/), s1: await xOf(/^B-S1/), s2: await xOf(/^B-S2/), s3: await xOf(/^B-S3/) }
    // One column apart before; after, B-S2 is two more columns on and B-S3 still one after it.
    expect(before.s2 - before.s1).toBeCloseTo(before.s1 - before.s0, 0)
    const column = after.s1 - after.s0
    expect(after.s2 - after.s1).toBeCloseTo(3 * column, 0)
    expect(after.s3 - after.s2).toBeCloseTo(column, 0)

    // Saved with the profile and in the saved figure.
    await page.reload()
    await expect(b.getByRole('group', { name: 'Move B along the axis' }).locator('option', { hasText: '(+2)' })).toHaveCount(1)
    const svg = await savedSvg(page)
    expect(svg).toContain('B-S2')

    // Back one column, then every step back where the pathway puts it.
    await step.selectOption({ label: '3. B-S2 (+2)' })
    await move.getByRole('button', { name: 'Move back' }).click()
    await expect(step.locator('option:checked')).toHaveText('3. B-S2 (+1)')
    await move.getByRole('button', { name: 'Reset' }).click()
    await expect(step.locator('option:checked')).toHaveText('3. B-S2')
    await expect(move.getByRole('button', { name: 'Reset' })).toHaveCount(0)
    await expect.poll(() => xOf(/^B-S2/)).toBeCloseTo(before.s2, 0)
  } finally {
    await forgetView(page.request, join(E2E_DIR, 'demo'))
  }
})

// T-UI-71, D195: a click on a pathway's swatch opens its colours; the colour picked is drawn,
// saved with the profile and in the saved figure, and "Branch colour" goes back to the branch's.
test('energy drawer: a pathway takes the colour picked on its swatch', async ({ page }) => {
  try {
    await openDemo(page)
    await page.getByRole('button', { name: 'Profile and table' }).click()
    const drawer = page.getByLabel('Energy drawer')
    await drawer.getByLabel('Add branch pathway').selectOption({ label: 'A' })
    await drawer.getByLabel('Add branch pathway').selectOption({ label: 'B' })
    const chart = drawer.locator('.drawer-main').getByRole('img', { name: 'Energy profile' })
    const line = chart.locator('g[aria-label="Profile B"] path').first()
    const branch = await line.getAttribute('stroke')
    const swatch = drawer.getByRole('button', { name: 'Colour of B' })
    await expect(swatch.locator('.swatch')).toHaveCSS('background-color', hexToRgb(branch!))

    await swatch.click()
    const pop = drawer.getByRole('dialog', { name: 'Colour of B' })
    await expect(pop.getByRole('button', { name: 'Branch colour' })).toBeDisabled()
    await pop.getByRole('radio', { name: 'Colour 2' }).click()
    await expect(pop).toHaveCount(0)
    await expect(line).toHaveAttribute('stroke', '#d55e00')
    await expect(swatch.locator('.swatch')).toHaveCSS('background-color', 'rgb(213, 94, 0)')
    // A's colour is its branch's still.
    await expect(chart.locator('g[aria-label="Profile A"] path').first()).not.toHaveAttribute(
      'stroke',
      '#d55e00',
    )

    // Kept with the profile, and in the saved figure.
    await page.reload()
    await expect(line).toHaveAttribute('stroke', '#d55e00')
    expect(await savedSvg(page)).toContain('#d55e00')

    // Any colour through the browser's colour field, then back to the branch's.
    await swatch.click()
    await expect(pop.getByRole('radio', { name: 'Colour 2' })).toHaveAttribute('aria-checked', 'true')
    await pop.getByLabel('Any colour').fill('#123456')
    await expect(line).toHaveAttribute('stroke', '#123456')
    await pop.getByRole('button', { name: 'Branch colour' }).click()
    await expect(line).toHaveAttribute('stroke', branch!)
    await expect(pop).toHaveCount(0)
  } finally {
    await forgetView(page.request, join(E2E_DIR, 'demo'))
  }
})

function hexToRgb(hex: string): string {
  const n = parseInt(hex.slice(1), 16)
  return `rgb(${(n >> 16) & 255}, ${(n >> 8) & 255}, ${n & 255})`
}
