import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { expect, test, type Page } from '@playwright/test'
import { startPage } from './appBar.ts'
import { forgetView } from './demoView.ts'

// T-UI-11, FR-EN-10, FR-EN-11, D106: the profile style with its presets and preview, the saved
// images in that style, and the drawer made taller, its list hidden and the profile zoomed.
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

async function saved(page: Page, item: string): Promise<Buffer> {
  const download = page.waitForEvent('download')
  await page.getByLabel('Energy drawer').getByRole('button', { name: 'Export ▾' }).click()
  await page.getByRole('menuitem', { name: item }).click()
  return readFileSync(await (await download).path())
}

test('profile style, saved images and zoom', async ({ page }) => {
  const screen = (await (await page.request.get('/api/settings')).json()).profile_presets.screen
  try {
    await openDemo(page)
    await page.getByRole('button', { name: 'Profile and table' }).click()
    const drawer = page.getByLabel('Energy drawer')
    await drawer.getByLabel('Add branch pathway').selectOption({ label: 'B' })
    const chart = drawer.locator('.drawer-main').getByRole('img', { name: 'Energy profile' })
    await expect(chart).toContainText('ΔG')
    await expect(chart.locator('line[stroke="#eaecf0"]').first()).toBeAttached() // grid lines

    // The dialog previews the drawer's profile; "Publication" drops the title and grid lines
    // and curves the connectors.
    await drawer.getByRole('button', { name: 'Style…' }).click()
    const dialog = page.getByRole('dialog', { name: 'Profile style' })
    const preview = dialog.getByLabel('Preview').getByRole('img', { name: 'Energy profile' })
    await expect(preview).toContainText('relative to T-S0')
    await dialog.getByRole('button', { name: 'Publication' }).click()
    await expect(preview).not.toContainText('relative to T-S0')
    await expect(preview.locator('line[stroke="#eaecf0"]')).toHaveCount(0)
    await expect(preview.locator('path[d*=" C "]').first()).toBeAttached()
    await expect(dialog.getByLabel('Font', { exact: true })).toHaveValue('arial')
    await dialog.getByLabel('Width').fill('1200')
    await dialog.getByLabel('Decimals').selectOption({ label: '2' })
    await dialog.getByLabel('Value written as').selectOption({ label: '(12.3)' })
    await page.screenshot({ path: 'test-results/profile-style-dialog.png' })
    // Nothing changes before Save.
    await expect(chart).toContainText('relative to T-S0')
    await dialog.getByRole('button', { name: 'Save' }).click()
    await expect(dialog).toHaveCount(0)

    await expect(chart).not.toContainText('relative to T-S0')
    await expect(chart).toHaveAttribute('width', '1200')
    await expect(chart).toContainText('(0.00)')
    await page.screenshot({ path: 'test-results/profile-publication.png' })
    const svg = (await saved(page, 'Profile as SVG')).toString('utf8')
    expect(svg).toContain('width="1200"')
    expect(svg).toContain('Arial')
    expect(svg).not.toContain('relative to')
    expect(svg).not.toContain('style=') // the zoom on screen is not part of the figure
    const png = await saved(page, 'Profile as PNG')
    expect(png.subarray(1, 4).toString()).toBe('PNG')
    expect(png.readUInt32BE(16)).toBe(4800) // width × 4

    // The style is the app's, kept over a reload (which reopens the drawer on pathway B, D105).
    await page.reload()
    await expect(chart).toHaveAttribute('width', '1200')

    // FR-EN-11: a taller drawer, the list hidden, then zoom.
    const before = (await drawer.boundingBox())!.height
    const edge = (await drawer.getByRole('separator', { name: 'Drawer height' }).boundingBox())!
    await page.mouse.move(edge.x + 200, edge.y + edge.height / 2)
    await page.mouse.down()
    await page.mouse.move(edge.x + 200, edge.y - 200, { steps: 5 })
    await page.mouse.up()
    expect((await drawer.boundingBox())!.height).toBeGreaterThan(before + 150)
    const fitWidth = (await chart.boundingBox())!.width
    await drawer.getByRole('button', { name: 'Pathways' }).click()
    await expect(drawer.getByLabel('Pathways', { exact: true })).toHaveCount(0)
    expect((await chart.boundingBox())!.width).toBeGreaterThan(fitWidth)
    // D181: the zoom's level between − and + is a button: Fit, or the zoom, which goes back to Fit.
    const zoomLevel = drawer.getByRole('group', { name: 'Zoom' }).getByRole('button').nth(1)
    await expect(zoomLevel).toHaveText('Fit')
    await drawer.getByRole('button', { name: 'Zoom in' }).click()
    await drawer.getByRole('button', { name: 'Zoom in' }).click()
    await page.screenshot({ path: 'test-results/profile-zoomed.png' })
    const level = await zoomLevel.textContent()
    const zoom = Number(level!.replace(/[^\d]/g, '')) / 100
    expect((await chart.boundingBox())!.width).toBeCloseTo(1200 * zoom, 0)
    await zoomLevel.click()
    await expect(zoomLevel).toHaveText('Fit')

    // The drawer's height and the hidden list are remembered by the browser.
    const taller = (await drawer.boundingBox())!.height
    await page.reload()
    await expect(chart).toBeVisible()
    expect((await drawer.boundingBox())!.height).toBeCloseTo(taller, 0)
    await expect(drawer.getByRole('button', { name: 'Pathways' })).toHaveAttribute('aria-pressed', 'false')

    // "Screen" brings back the look before styles.
    await drawer.getByRole('button', { name: 'Pathways' }).click()
    await expect(drawer.getByRole('group', { name: 'Pathway B' })).toBeVisible()
    await drawer.getByRole('button', { name: 'Style…' }).click()
    await dialog.getByRole('button', { name: 'Screen' }).click()
    await dialog.getByRole('button', { name: 'Save' }).click()
    await expect(chart).toContainText('relative to T-S0')
    await expect(chart).toHaveAttribute('width', '960')
  } finally {
    await page.request.put('/api/settings', { data: { profile_style: screen } })
    await forgetView(page.request, join(E2E_DIR, 'demo'))
  }
})

function overlap(a: { x: number; y: number; width: number; height: number }, b: typeof a): boolean {
  return a.x < b.x + b.width && b.x < a.x + a.width && a.y < b.y + b.height && b.y < a.y + a.height
}

test('"no TS" tags can be hidden and the title keeps clear of the legend', async ({ page }) => {
  // T-UI-12, D108
  const screen = (await (await page.request.get('/api/settings')).json()).profile_presets.screen
  try {
    // A narrow figure, where the title runs into the legend's corner.
    await page.request.put('/api/settings', { data: { profile_style: { ...screen, width: 320 } } })
    await openDemo(page)
    await page.getByRole('button', { name: 'Profile and table' }).click()
    const drawer = page.getByLabel('Energy drawer')
    await drawer.getByLabel('Add branch pathway').selectOption({ label: 'A' })
    await drawer.getByLabel('Add branch pathway').selectOption({ label: 'B' })
    const chart = drawer.locator('.drawer-main').getByRole('img', { name: 'Energy profile' })
    await expect(chart.locator('[data-direct="true"]').first()).toContainText('no TS')
    const title = chart.locator('text', { hasText: 'relative to T-S0' })
    const legend = chart.getByLabel('Legend')
    for (const line of [title, chart.locator('text', { hasText: /^ΔG.* at / })])
      expect(overlap((await line.boundingBox())!, (await legend.boundingBox())!)).toBe(false)
    await page.screenshot({ path: 'test-results/profile-title-legend.png' })

    await drawer.getByRole('button', { name: 'Style…' }).click()
    const dialog = page.getByRole('dialog', { name: 'Profile style' })
    await dialog.getByLabel('“no TS” tags').uncheck()
    await dialog.getByRole('button', { name: 'Save' }).click()
    await expect(dialog).toHaveCount(0)
    // The connector stays dotted; only the tag goes.
    await expect(chart.locator('[data-direct="true"]').first()).toBeAttached()
    await expect(chart).not.toContainText('no TS')
    expect((await (await page.request.get('/api/settings')).json()).profile_style.edge_tags).toBe(false)

    // T-UI-14, D110: A-S1 (-3.20) and B-S1 (-2.10) lie close in one column. Their values stay
    // above the levels and their names below, stacked in the order of the levels.
    const at = async (text: RegExp) => {
      const found = chart.locator('text', { hasText: text })
      return {
        x: Number(await found.getAttribute('x')),
        y: Number(await found.getAttribute('y')),
        anchor: await found.getAttribute('text-anchor'),
      }
    }
    const stacked = [await at(/^-2\.10$/), await at(/^-3\.20$/), await at(/^B-S1$/), await at(/^A-S1$/)]
    for (const label of stacked) expect([label.anchor, label.x]).toEqual(['middle', stacked[0].x])
    for (let n = 1; n < stacked.length; n++) expect(stacked[n].y).toBeGreaterThan(stacked[n - 1].y)

    // T-UI-15, D111: both pathways start at T-S0, so its level and value are in the text colour.
    const start = chart.locator('text', { hasText: /^T-S0$/ })
    await expect(start).toHaveCount(1)
    const zero = chart.locator('text', { hasText: /^\(?[+−-]?0\.00\)?$/ })
    await expect(zero).toHaveCount(1)
    expect(await zero.evaluate((el) => el.getAttribute('fill'))).toBe('#1c2430')
    for (const profile of await chart.locator('g[aria-label^="Profile "]').all())
      expect(await profile.locator('line').first().getAttribute('stroke')).toBe('#1c2430')

    // T-UI-13, D109: node names along the bottom, above the step names, not by the levels.
    await expect(chart.locator('text', { hasText: /^B-S2 ‡$/ })).toHaveCount(1)
    const byLevel = Number(await chart.locator('text', { hasText: /^B-S2 ‡$/ }).getAttribute('y'))
    await drawer.getByRole('button', { name: 'Style…' }).click()
    await dialog.getByLabel('Node name').selectOption({ label: 'Along the bottom' })
    await dialog.getByRole('button', { name: 'Save' }).click()
    await expect(dialog).toHaveCount(0)
    const name = chart.locator('text', { hasText: /^B-S2 ‡$/ })
    await expect(name).toHaveCount(1)
    const step = Number(await chart.locator('text', { hasText: /^\[2\+2\] TS$/ }).getAttribute('y'))
    const atBottom = Number(await name.getAttribute('y'))
    expect(atBottom).toBeGreaterThan(byLevel)
    expect(atBottom).toBeLessThan(step)
    // A–S1 and B–S1 share a column: one line each, starting just under the plot (D110).
    await expect(chart.locator('text', { hasText: /^[AB]-S1$/ })).toHaveCount(2)
    // Which of the two comes last depends on the order of the pathways, so take the lower one.
    // In this narrow figure the step names wrap (D198), so measure from them, not the bottom edge.
    const lowest = Math.max((await at(/^A-S1$/)).y, (await at(/^B-S1$/)).y)
    const stepTop = (await at(/^π-complex$/)).y
    expect(stepTop - lowest).toBeGreaterThan(0)
    expect(stepTop - lowest).toBeLessThan(30)
    await page.screenshot({ path: 'test-results/profile-names-bottom.png' })
  } finally {
    await page.request.put('/api/settings', { data: { profile_style: screen } })
    await forgetView(page.request, join(E2E_DIR, 'demo'))
  }
})

test('long names along the bottom wrap inside their columns', async ({ page }) => {
  // T-UI-74, D198: names wider than their column wrap onto more lines instead of running into
  // the next column's.
  const screen = (await (await page.request.get('/api/settings')).json()).profile_presets.screen
  const long: Record<string, string> = {
    'A-S1': 'A_pi_complex_side_on_conformer_2',
    'B-S1': 'B_pi_complex_side_on_conformer_1',
    'B-S2': 'B_cycloaddition_scan_top_TSGO',
    'B-S3': 'B_metallacyclobutane_cis_opt',
  }
  const renamed: { id: string; label: string }[] = []
  try {
    await page.request.put('/api/settings', {
      data: { profile_style: { ...screen, width: 480, name_position: 'bottom' } },
    })
    await openDemo(page)
    for (const node of (await (await page.request.get('/api/nodes')).json()) as { id: string; label: string }[])
      if (long[node.label]) {
        await page.request.patch(`/api/nodes/${node.id}`, { data: { label: long[node.label] } })
        renamed.push(node)
      }
    expect(renamed).toHaveLength(4)
    await page.getByRole('button', { name: 'Profile and table' }).click()
    const drawer = page.getByLabel('Energy drawer')
    await drawer.getByLabel('Add branch pathway').selectOption({ label: 'A' })
    await drawer.getByLabel('Add branch pathway').selectOption({ label: 'B' })
    const chart = drawer.locator('.drawer-main').getByRole('img', { name: 'Energy profile' })
    const names = Object.values(long).map((name) => chart.locator('text', { hasText: name }))
    const steps = [/^alkylidene$/, /^π-complex$/, /^\[2\+2\] TS$/, /^metallacyclobutane$/].map((name) =>
      chart.locator('text', { hasText: name }),
    )
    for (const name of names) {
      await expect(name).toHaveCount(1)
      expect(await name.locator('tspan').count()).toBeGreaterThan(1)
    }
    const boxes: { x: number; y: number; width: number; height: number }[] = []
    for (const line of [...names, ...steps]) boxes.push((await line.boundingBox())!)
    // Lines in neighbouring columns never share any width where they share height.
    for (const a of boxes)
      for (const b of boxes) {
        const sameColumn = Math.abs(a.x + a.width / 2 - (b.x + b.width / 2)) < 1
        if (!sameColumn && a.y < b.y + b.height && b.y < a.y + a.height)
          expect(a.x + a.width <= b.x + 0.5 || b.x + b.width <= a.x + 0.5).toBe(true)
      }
    // The two names in the π-complex column stack, the step name under both.
    const column = [names[1], names[0], steps[1]].map((line) => boxes[[...names, ...steps].indexOf(line)])
    const [upper, lower] = column[0].y < column[1].y ? [column[0], column[1]] : [column[1], column[0]]
    expect(lower.y).toBeGreaterThanOrEqual(upper.y + upper.height - 2)
    expect(column[2].y).toBeGreaterThan(lower.y + lower.height - 2)
    await page.screenshot({ path: 'test-results/profile-names-wrap.png' })
  } finally {
    for (const node of renamed) await page.request.patch(`/api/nodes/${node.id}`, { data: { label: node.label } })
    await page.request.put('/api/settings', { data: { profile_style: screen } })
    await forgetView(page.request, join(E2E_DIR, 'demo'))
  }
})
