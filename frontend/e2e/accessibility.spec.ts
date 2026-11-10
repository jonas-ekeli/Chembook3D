import { join } from 'node:path'
import AxeBuilder from '@axe-core/playwright'
import { expect, test, type Page } from '@playwright/test'
import { startPage } from './appBar.ts'
import { forgetView } from './demoView.ts'

// axe-core's checks on the start page and on an open canvas (the demo investigation, with its
// energies drawn): no violation of impact "critical" or "serious" is allowed.
const E2E_DIR = process.env.E2E_DIR!

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
  const folder = join(E2E_DIR, 'demo')
  await forgetView(page.request, folder)
  expect((await page.request.post('/api/investigations/open', { data: { folder } })).ok()).toBeTruthy()
  await page.goto('/')
  await expect(page.getByRole('group', { name: 'Node T-S0', exact: true })).toBeVisible()
  await expect(page.locator('.edge-label').filter({ hasText: 'ΔG' }).first()).toBeVisible()
  expect(await violations(page)).toEqual([])
  await page.request.post('/api/investigations/close')
})
