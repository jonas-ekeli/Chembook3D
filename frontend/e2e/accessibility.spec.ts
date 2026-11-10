import AxeBuilder from '@axe-core/playwright'
import { expect, test, type Page } from '@playwright/test'
import { openDemo, startPage } from './helpers.ts'

// axe-core's checks on the start page and on an open canvas (the test's own copy of the demo,
// with its energies drawn): no violation of impact "critical" or "serious" is allowed.

async function violations(page: Page): Promise<string[]> {
  const results = await new AxeBuilder({ page }).analyze()
  return results.violations
    .filter((v) => v.impact === 'critical' || v.impact === 'serious')
    .map((v) => `${v.impact} ${v.id}: ${v.help} (${v.nodes.map((n) => n.target.join(' ')).join(', ')})`)
}

test('the start page has no critical or serious accessibility violations', async ({ page }) => {
  await startPage(page)
  await expect(page.getByRole('button', { name: 'Open investigation…' })).toBeVisible()
  expect(await violations(page)).toEqual([])
})

test('an open canvas has no critical or serious accessibility violations', async ({ page }) => {
  await openDemo(page)
  expect(await violations(page)).toEqual([])
})
