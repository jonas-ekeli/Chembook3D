import { readFileSync } from 'node:fs'
import { expect, test, type Page } from '@playwright/test'
import { openDemo } from './helpers.ts'

// T-UI-62, D185: the theme setting (Light, Dark, Follow system), set on <html> before the app
// loads and following the system live; the dark colours; saved figures stay light.

const theme = (page: Page) => page.evaluate(() => document.documentElement.dataset.theme)
const colour = (page: Page, selector: string, property: 'color' | 'backgroundColor') =>
  page.locator(selector).first().evaluate((el, p) => getComputedStyle(el)[p], property)

test('a dark theme, following the system or chosen, with saved figures kept light', async ({ page }) => {
  await page.emulateMedia({ colorScheme: 'light' })
  try {
    await openDemo(page)
    expect(await theme(page)).toBe('light')
    expect(await colour(page, 'html', 'backgroundColor')).toBe('rgb(246, 247, 249)')

    // Settings, General: Follow system is the default.
    await page.getByRole('button', { name: 'Settings' }).click()
    const dialog = page.getByRole('dialog', { name: 'Settings' })
    const choice = dialog.getByRole('group', { name: 'Theme' })
    await expect(choice.getByRole('button', { name: 'Follow system' })).toHaveAttribute('aria-pressed', 'true')
    await choice.getByRole('button', { name: 'Dark' }).click()
    await expect(choice.getByRole('button', { name: 'Dark' })).toHaveAttribute('aria-pressed', 'true')
    await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark')
    expect((await (await page.request.get('/api/settings')).json()).theme).toBe('dark')
    await page.keyboard.press('Escape')
    await expect(dialog).toHaveCount(0)

    // The dark colours: the page, a node card and its text, React Flow's own.
    expect(await colour(page, 'html', 'backgroundColor')).toBe('rgb(14, 17, 22)')
    expect(await colour(page, '.cnode:not(.planned)', 'backgroundColor')).toBe('rgb(26, 32, 41)')
    expect(await colour(page, '.cnode', 'color')).toBe('rgb(230, 233, 239)')
    await expect(page.locator('.react-flow')).toHaveClass(/\bdark\b/)

    // Set before the app has loaded its settings, from the copy kept in the browser.
    await page.reload({ waitUntil: 'domcontentloaded' })
    expect(await theme(page)).toBe('dark')
    await expect(page.getByRole('group', { name: 'Node T-S0', exact: true })).toBeVisible()

    // The profile is drawn in the theme's colours on screen; the saved SVG keeps its own.
    await page.getByRole('button', { name: 'Profile and table' }).click()
    const drawer = page.getByLabel('Energy drawer')
    await drawer.getByLabel('Add branch pathway').selectOption({ label: 'B' })
    const chart = drawer.locator('.drawer-main').getByRole('img', { name: 'Energy profile' })
    await expect(chart.locator('line[stroke="var(--chart-grid)"]').first()).toBeAttached()
    const download = page.waitForEvent('download')
    await drawer.getByRole('button', { name: 'Export ▾' }).click()
    await page.getByRole('menuitem', { name: 'Profile as SVG' }).click()
    const svg = readFileSync(await (await download).path()).toString('utf8')
    expect(svg).toContain('fill="#ffffff"') // the white background
    expect(svg).toContain('stroke="#eaecf0"') // the light grid
    expect(svg).toContain('fill="#1c2430"') // the light text
    expect(svg).not.toContain('var(--')

    // Follow system: changes with the system's preference while the page is open.
    await page.getByRole('button', { name: 'Settings' }).click()
    await choice.getByRole('button', { name: 'Follow system' }).click()
    await expect(page.locator('html')).toHaveAttribute('data-theme', 'light')
    await page.emulateMedia({ colorScheme: 'dark' })
    await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark')
    await page.emulateMedia({ colorScheme: 'light' })
    await expect(page.locator('html')).toHaveAttribute('data-theme', 'light')
    await choice.getByRole('button', { name: 'Light' }).click()
    // The choice is in force once the app has mirrored it for index.html.
    await expect.poll(() => page.evaluate(() => localStorage.getItem('chembook3d.theme'))).toBe('light')
    // A listener of our own hears the system's change after the app's, which was added first.
    await page.evaluate(() => {
      const query = matchMedia('(prefers-color-scheme: dark)')
      const changed = new Promise<void>((resolve) => query.addEventListener('change', () => resolve(), { once: true }))
      Object.assign(window, { systemQuery: query, systemChanged: changed })
    })
    await page.emulateMedia({ colorScheme: 'dark' })
    await page.evaluate(() => (window as unknown as { systemChanged: Promise<void> }).systemChanged)
    expect(await theme(page)).toBe('light')
  } finally {
    await page.request.put('/api/settings', { data: { theme: 'system' } })
  }
})
