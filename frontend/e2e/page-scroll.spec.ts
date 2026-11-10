import { expect, test, type Locator, type Page } from '@playwright/test'
import { openDemo, pageSettled } from './helpers.ts'

// T-UI-63, D187: the app stays locked to the window. Every pane scrolls on its own and the page
// is never taller than the window, so the wheel anywhere leaves the page at the top.

test.use({ viewport: { width: 1100, height: 600 } })

/** How far the page itself could scroll, and how far it has. */
function pageScroll(page: Page) {
  return page.evaluate(() => ({
    extra: document.scrollingElement!.scrollHeight - window.innerHeight,
    top: window.scrollY,
  }))
}

// The wheel events the page has had, counted by a listener added as the page loads: one added
// just before a wheel may not be known yet to the part of the browser that scrolls, which then
// does not pass the wheel to the page at all.
const wheels = (page: Page) => page.evaluate(() => (window as unknown as { wheels?: number }).wheels ?? 0)

async function wheelOver(page: Page, target: Locator) {
  // Outside the window the wheel would reach nothing at all.
  await expect(target).toBeInViewport()
  const box = (await target.boundingBox())!
  await page.mouse.move(box.x + box.width / 2, box.y + Math.min(box.height / 2, 20))
  const before = await wheels(page)
  await page.mouse.wheel(0, 2000)
  // The page has had the wheel and done what it does with it before reading the scroll.
  await expect.poll(() => wheels(page)).toBe(before + 1)
  await pageSettled(page)
  expect(await pageScroll(page)).toEqual({ extra: 0, top: 0 })
}

test.afterEach(async ({ request }) => {
  await request.post('/api/investigations/close')
})

test('the wheel never scrolls the page past the app', async ({ page }) => {
  await page.addInitScript(() => {
    const counted = window as unknown as { wheels: number }
    counted.wheels = 0
    window.addEventListener('wheel', () => (counted.wheels += 1), { capture: true, passive: true })
  })
  await openDemo(page)
  const outline = page.getByRole('complementary', { name: 'Outline' })
  const nodes = outline.getByRole('list', { name: 'Nodes' })
  await expect(nodes.getByRole('button', { name: /^A-S2/ })).toBeVisible()
  // The node list is longer than its box here, the case that used to stretch the page.
  expect(await nodes.evaluate((list) => list.scrollHeight > list.clientHeight)).toBe(true)
  expect(await pageScroll(page)).toEqual({ extra: 0, top: 0 })

  await wheelOver(page, outline)
  // The outline took the wheel itself.
  await expect.poll(() => outline.evaluate((aside) => aside.scrollTop)).toBeGreaterThan(0)
  // So does the node list, below the window's edge until the outline scrolled.
  await nodes.scrollIntoViewIfNeeded()
  await wheelOver(page, nodes)
  await expect.poll(() => nodes.evaluate((list) => list.scrollTop)).toBeGreaterThan(0)
  await wheelOver(page, page.locator('header').first())
  await wheelOver(page, page.locator('.react-flow__pane'))

  await nodes.getByRole('button', { name: /^A-S2/ }).click()
  await expect(page.getByRole('heading', { name: 'A-S2' })).toBeVisible()
  await wheelOver(page, page.getByRole('heading', { name: 'A-S2' }))

  for (const view of ['History', 'Analyses']) {
    await page.getByRole('button', { name: view, exact: true }).click()
    await wheelOver(page, page.locator('main').first())
  }
})
