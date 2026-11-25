import { mkdirSync } from 'node:fs'
import { join } from 'node:path'
import { expect, test } from '@playwright/test'
import { browseTo, E2E_DIR, startPage } from './helpers.ts'

// D263a, D263f, D263h, T-UI-119: "Build mechanism from folder…" in the Claude panel's menu asks
// for the folder, the job limit (4 hours) and the literature; Start shows the Work on its own
// warning, which makes a playground; there the switch is on at that limit and Claude is
// started with `/build-mechanism <folder>` typed in. The start page's card makes a new
// investigation first and then asks the same.

test('Build mechanism from folder makes a playground and starts Claude with the playbook', async ({ page }) => {
  const stamp = Date.now()
  const folder = join(E2E_DIR, `build-${stamp}`)
  const name = `Build ${stamp}`
  const outputs = join(E2E_DIR, `Ru outputs ${stamp}`)
  mkdirSync(outputs)
  expect((await page.request.post('/api/investigations', { data: { folder, name } })).ok()).toBeTruthy()
  await page.goto('/')
  await expect(page.locator('.investigation')).toHaveText(name)

  await page.getByRole('button', { name: 'Claude', exact: true }).click()
  const panel = page.getByRole('complementary', { name: 'Claude' })
  await panel.getByRole('button', { name: 'More for Claude' }).click()
  await page.getByRole('menuitem', { name: 'Build mechanism from folder…' }).click()
  const start = page.getByRole('dialog', { name: 'Build mechanism from folder' })
  await expect(start).toContainText('a playground of this investigation')
  await expect(start.getByLabel('Longest local job Claude may start')).toHaveValue(String(4 * 3600))
  await expect(start.getByRole('checkbox', { name: 'Use the literature where it helps' })).toBeChecked()
  await expect(start.getByRole('button', { name: 'Start…' })).toBeDisabled()

  // The folder from the folder picker; the literature left out.
  await start.getByRole('button', { name: 'Browse…' }).click()
  await browseTo(page, outputs)
  await page.getByRole('dialog', { name: 'Folder of outputs' }).getByRole('button', { name: 'Use this folder' }).click()
  await expect(start.getByLabel('Folder of outputs')).toHaveValue(outputs)
  await start.getByRole('checkbox', { name: 'Use the literature where it helps' }).uncheck()
  const typed = `/build-mechanism "${outputs}" --no-literature`
  await expect(start).toContainText(typed)
  await start.getByRole('button', { name: 'Start…' }).click()

  // The warning of Work on its own comes first, with the build's limit; no "Turn on here".
  const warning = page.getByRole('dialog', { name: 'Let Claude work on its own?' })
  await expect(warning).toContainText('Claude can be wrong')
  await expect(warning).toContainText('Claude builds in a playground')
  await expect(warning).toContainText('Longest local job Claude may start: 4 hours')
  await expect(warning.getByRole('button', { name: /Turn on/ })).toHaveCount(0)
  await warning.getByRole('button', { name: 'Make a playground and start' }).click()
  await expect(warning).toHaveCount(0)

  // In the playground, the switch is on at 4 hours and Claude got the command.
  await expect(page.getByRole('note', { name: 'Playground' })).toContainText(`Playground 1 is a playground of ${name}`)
  await expect(panel.getByRole('checkbox', { name: 'Work on its own' })).toBeChecked()
  await expect(panel.getByRole('status', { name: 'Working on its own' })).toContainText('local jobs of up to 4 hours')
  const state = (await (await page.request.get('/api/claude/autonomy')).json()) as { on: boolean; job_limit: number }
  expect(state).toMatchObject({ on: true, job_limit: 4 * 3600 })
  const terminal = panel.getByTestId('claude-terminal')
  await expect(terminal).toContainText(`Fake Claude got: ${typed}`)
  await expect(panel).toContainText('Claude Code is running')
  await panel.getByRole('button', { name: 'Stop', exact: true }).click()

  // The start page's card: a new investigation, then the same start dialog for it.
  await startPage(page)
  await page.getByRole('button', { name: 'Build mechanism from folder…', exact: true }).click()
  await browseTo(page, E2E_DIR)
  const picker = page.getByRole('dialog', { name: 'New investigation' })
  await picker.getByLabel('Investigation name').fill(`Built ${stamp}`)
  await picker.getByRole('button', { name: 'Create' }).click()
  await expect(page.locator('.investigation')).toHaveText(`Built ${stamp}`)
  await expect(start).toContainText('a playground of this investigation')
  await start.getByRole('button', { name: 'Cancel' }).click()
  await expect(start).toHaveCount(0)
})
