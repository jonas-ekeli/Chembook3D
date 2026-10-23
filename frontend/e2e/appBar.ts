import { expect, type Page } from '@playwright/test'

/**
 * D180: the investigation's own commands (New, Open, Close, Sync with GitHub, Share read-only copy,
 * Basis sets, Shut down) are in the menu on its name in the app bar. This opens the menu and
 * chooses one.
 */
export async function investigationCommand(page: Page, name: string) {
  await page.locator('header .investigation').click()
  await page.getByRole('menu', { name: 'Investigation' }).getByRole('menuitem', { name, exact: true }).click()
}

/** D180: the Overview is what the side panel shows with nothing selected; a click on an empty
 * spot of the canvas clears the selection. */
export async function showOverview(page: Page) {
  const pane = page.locator('.react-flow__pane')
  const box = (await pane.boundingBox())!
  // The first spot, scanning in from the top left, where the pane itself is under the pointer.
  const spot = await pane.evaluate((element, { width, height }) => {
    const { left, top } = element.getBoundingClientRect()
    for (let y = 20; y < height - 20; y += 40)
      for (let x = 20; x < width - 20; x += 40)
        if (document.elementFromPoint(left + x, top + y) === element) return { x, y }
    return null
  }, box)
  await pane.click({ position: spot! })
  await expect(page.getByLabel('Overview', { exact: true }).getByRole('heading', { name: 'Overview' })).toBeVisible()
}

/** D180: New and Open are on the start page, and in the investigation menu once one is open;
 * this shows the start page, closing whatever investigation an earlier test left open. */
export async function startPage(page: Page) {
  await page.request.post('/api/investigations/close')
  await page.goto('/')
}
