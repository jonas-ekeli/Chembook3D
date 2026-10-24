import { join } from 'node:path'
import { expect, test, type Locator, type Page } from '@playwright/test'
import { forgetView } from './demoView.ts'

// T-UI-63, D187: the app stays locked to the window. Every pane scrolls on its own and the page
// is never taller than the window, so the wheel anywhere leaves the page at the top.

const E2E_DIR = process.env.E2E_DIR!

test.use({ viewport: { width: 1100, height: 600 } })

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

/** How far the page itself could scroll, and how far it has. */
function pageScroll(page: Page) {
  return page.evaluate(() => ({
    extra: document.scrollingElement!.scrollHeight - window.innerHeight,
    top: window.scrollY,
  }))
}

async function wheelOver(page: Page, target: Locator) {
  const box = (await target.boundingBox())!
  await page.mouse.move(box.x + box.width / 2, box.y + Math.min(box.height / 2, 20))
  await page.mouse.wheel(0, 2000)
  // Let the wheel's scroll land before reading it.
  await page.waitForTimeout(150)
  expect(await pageScroll(page)).toEqual({ extra: 0, top: 0 })
}

test.afterEach(async ({ request }) => {
  await request.post('/api/investigations/close')
})

test('the wheel never scrolls the page past the app', async ({ page }) => {
  await openDemo(page)
  const outline = page.getByRole('complementary', { name: 'Outline' })
  const nodes = outline.getByRole('list', { name: 'Nodes' })
  await expect(nodes.getByRole('button', { name: /^A-S2/ })).toBeVisible()
  // The node list is longer than its box here, the case that used to stretch the page.
  expect(await nodes.evaluate((list) => list.scrollHeight > list.clientHeight)).toBe(true)
  expect(await pageScroll(page)).toEqual({ extra: 0, top: 0 })

  await wheelOver(page, nodes)
  await wheelOver(page, outline)
  // The outline took the wheel itself.
  const moved = await outline.evaluate(
    (aside) => aside.scrollTop + aside.querySelector('[aria-label="Nodes"]')!.scrollTop,
  )
  expect(moved).toBeGreaterThan(0)
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
