import { execFileSync } from 'node:child_process'
import { join, resolve } from 'node:path'
import { expect, test } from '@playwright/test'
import { startPage } from './appBar.ts'

// NFR-PERF-01: no time targets are set, so this reports how long the canvas takes to draw the
// stress case (tests/stress.py: 500 nodes, 1,000 edges) after a click on it in the start page.
// The times are printed and added to the test's annotations; the bound is generous on purpose.
const E2E_DIR = process.env.E2E_DIR!
const FOLDER = join(E2E_DIR, 'stress')
const BOUND = 30_000 // ms; a report, not a target

test.beforeAll(() => {
  const root = resolve(import.meta.dirname, '..', '..')
  execFileSync('uv', ['run', 'python', '-m', 'tests.stress', FOLDER], { cwd: root, stdio: 'inherit' })
})

test('the canvas of a 500-node investigation is drawn', async ({ page }) => {
  test.setTimeout(3 * BOUND)
  // Opened once, so the start page lists it.
  expect((await page.request.post('/api/investigations/open', { data: { folder: FOLDER } })).ok()).toBeTruthy()
  await startPage(page)
  const row = page.getByRole('list', { name: 'Recently opened' }).getByRole('button', { name: /^Stress 500/ })
  await expect(row).toBeVisible()

  const started = performance.now()
  await row.click()
  await expect(page.getByRole('group', { name: 'Node A-1', exact: true })).toBeVisible({ timeout: BOUND })
  const firstNode = performance.now() - started
  await expect(page.locator('.edge-label').filter({ hasText: 'ΔG' }).first()).toBeVisible({ timeout: BOUND })
  const energies = performance.now() - started
  const cards = await page.locator('.react-flow__node').count()

  const report = `stress case: first node card after ${Math.round(firstNode)} ms, energies on the edges after ${Math.round(energies)} ms, ${cards} cards drawn`
  console.log(report)
  test.info().annotations.push({ type: 'NFR-PERF-01', description: report })
  expect(cards).toBeGreaterThanOrEqual(500)
  expect(energies).toBeLessThan(BOUND)
  await page.request.post('/api/investigations/close')
})
