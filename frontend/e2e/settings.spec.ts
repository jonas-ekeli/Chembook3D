import { join } from 'node:path'
import { expect, test, type Page } from '@playwright/test'
import { forgetView } from './demoView.ts'

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
  await expect(page.getByRole('group', { name: 'Node T-S0', exact: true })).toBeVisible()
}

test('settings are a two-pane page that says where each setting is kept', async ({ page }) => {
  // T-UI-60, D183.
  await openDemo(page)
  const before = await (await page.request.get('/api/settings')).json()
  try {
    await page.getByRole('button', { name: 'Settings' }).click()
    const dialog = page.getByRole('dialog', { name: 'Settings' })
    const nav = dialog.getByRole('navigation', { name: 'Settings sections' })
    await expect(nav.locator('.settings-group-name')).toHaveText(['This computer', 'This investigation'])
    await expect(nav.getByRole('button')).toHaveText([
      'General',
      'Geometry matching',
      'Structures',
      'Steric maps',
      'CREST import',
      'Thermochemistry',
    ])
    await expect(nav.getByRole('button', { name: 'General' })).toHaveAttribute('aria-current', 'true')

    // Each section names its scope; the thermochemistry is the investigation's (D159).
    await expect(dialog.getByRole('heading', { name: 'General All investigations' })).toBeVisible()
    await expect(dialog.getByRole('heading', { name: 'Thermochemistry Saved in Demo investigation' })).toBeAttached()
    await expect(dialog.locator('.modal-note')).toContainText(
      'Thermochemistry is saved in Demo investigation and synced with it',
    )

    // A number is right-aligned with its unit inside the field.
    const tolerance = dialog.getByLabel('Same geometry')
    await expect(tolerance).toHaveValue(String(before.geometry_tolerance))
    await expect(tolerance).toHaveCSS('text-align', 'right')
    const field = (await tolerance.boundingBox())!
    const unit = (await dialog
      .locator('.number-setting', { has: page.getByLabel('Same geometry') })
      .locator('.unit')
      .boundingBox())!
    expect(unit.x).toBeGreaterThan(field.x + field.width / 2)
    expect(unit.x + unit.width).toBeLessThan(field.x + field.width)
    await expect(dialog.locator('.number-setting', { has: page.getByLabel('Cutoff (cm⁻¹)') })).toContainText('cm⁻¹')

    // The list moves the page to a section and marks it.
    await nav.getByRole('button', { name: 'Thermochemistry' }).click()
    await expect(dialog.getByLabel('Temperature (K)')).toBeInViewport()
    await expect(nav.getByRole('button', { name: 'Thermochemistry' })).toHaveAttribute('aria-current', 'true')

    // A long explanation is cut short; More… shows the rest in place.
    const thermo = dialog.getByLabel('Thermochemistry')
    await expect(thermo).toContainText('1.8943 kcal/mol at 298.15 K')
    await expect(thermo).not.toContainText('(D160)')
    await thermo.getByRole('button', { name: 'More…' }).click()
    await expect(thermo).toContainText('its own frequency job’s temperature (D160)')
    await thermo.getByRole('button', { name: 'Less' }).click()
    await expect(thermo).not.toContainText('(D160)')

    // The search finds settings by name and help, and the list follows it.
    const search = dialog.getByLabel('Search settings')
    await search.fill('duplicate')
    await expect(dialog.locator('.setting-name')).toHaveText(['Possible duplicate'])
    await expect(nav.getByRole('button')).toHaveText(['Geometry matching'])
    await search.fill('frequency job')
    await expect(dialog.locator('.setting-name')).toHaveText(['Standard state of G and G_qh'])
    await expect(thermo).toContainText('(D160)')
    await search.fill('steric')
    await expect(dialog.locator('.setting-name')).toHaveText(['Map colours'])
    await search.fill('no such setting')
    await expect(dialog).toContainText('No setting matches “no such setting”.')
    // Escape empties the search, then closes the dialog.
    await search.press('Escape')
    await expect(search).toHaveValue('')
    await expect(nav.getByRole('button')).toHaveCount(6)

    // A number still saves when its field loses focus.
    const conformers = dialog.getByLabel('Conformers kept on import')
    await conformers.fill('7')
    await conformers.press('Tab')
    await expect.poll(async () => (await (await page.request.get('/api/settings')).json()).crest_count).toBe(7)

    // The page fits a smaller window.
    for (const width of [1280, 900]) {
      await page.setViewportSize({ width, height: 800 })
      const box = (await dialog.boundingBox())!
      expect(box.x).toBeGreaterThanOrEqual(0)
      expect(box.x + box.width).toBeLessThanOrEqual(width)
      await expect(dialog.getByRole('button', { name: 'Done' })).toBeInViewport()
      await expect(search).toBeInViewport()
    }
    await search.press('Escape')
    await expect(dialog).toHaveCount(0)
  } finally {
    await page.request.put('/api/settings', { data: { crest_count: before.crest_count } })
  }
})
