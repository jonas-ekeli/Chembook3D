import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { expect, test, type Page } from '@playwright/test'
import { forgetView } from './demoView.ts'

// T-UI-11, FR-EN-10, FR-EN-11, D106: the profile style with its presets and preview, the saved
// images in that style, and the drawer made taller, its list hidden and the profile zoomed.
const E2E_DIR = process.env.E2E_DIR!

async function openDemo(page: Page) {
  await forgetView(page.request, join(E2E_DIR, 'demo'))
  await page.request.put('/api/settings', { data: { energy_unit: 'kcal/mol' } })
  await page.goto('/')
  await page.getByRole('button', { name: /^Open/ }).first().click()
  const dialog = page.getByRole('dialog')
  await expect(dialog.getByRole('list', { name: 'Folders' })).toBeVisible()
  await dialog.getByLabel('Folder path').fill(join(E2E_DIR, 'demo'))
  await dialog.getByRole('button', { name: 'Go' }).click()
  await dialog.getByRole('button', { name: 'Open', exact: true }).click()
  await expect(page.getByRole('dialog')).toHaveCount(0)
  await expect(page.getByRole('group', { name: 'Node T-S0', exact: true })).toBeVisible()
}

async function saved(page: Page, button: string): Promise<Buffer> {
  const download = page.waitForEvent('download')
  await page.getByLabel('Energy drawer').getByRole('button', { name: button }).click()
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
    const svg = (await saved(page, 'Save profile as SVG')).toString('utf8')
    expect(svg).toContain('width="1200"')
    expect(svg).toContain('Arial')
    expect(svg).not.toContain('relative to')
    expect(svg).not.toContain('style=') // the zoom on screen is not part of the figure
    const png = await saved(page, 'Save profile as PNG')
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
    await expect(drawer.getByLabel('Zoom level')).toHaveText('Fit')
    await drawer.getByRole('button', { name: 'Zoom in' }).click()
    await drawer.getByRole('button', { name: 'Zoom in' }).click()
    await page.screenshot({ path: 'test-results/profile-zoomed.png' })
    const level = await drawer.getByLabel('Zoom level').textContent()
    const zoom = Number(level!.replace(/[^\d]/g, '')) / 100
    expect((await chart.boundingBox())!.width).toBeCloseTo(1200 * zoom, 0)
    await drawer.getByRole('button', { name: 'Fit' }).click()
    await expect(drawer.getByLabel('Zoom level')).toHaveText('Fit')

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
