import { execFileSync } from 'node:child_process'
import { mkdirSync } from 'node:fs'
import { join } from 'node:path'
import { expect, test } from '@playwright/test'

// D93, A40: a cloud job's `claude --cloud` launch stops at a question (here the stand-in's
// folder trust question, see playwright.config.ts). The app answers nothing itself: the Claude
// panel opens with the launch's screen, and the user answers it there.
const E2E_DIR = process.env.E2E_DIR!

const WATER = '3\nwater\nO 0.0 0.0 0.1173\nH 0.0 0.7572 -0.4692\nH 0.0 -0.7572 -0.4692\n'

test('a question before a cloud job starts is answered in the Claude panel', async ({ page }) => {
  // The launch alone waits LAUNCH_QUIET (5 s) of a still screen, after a push; on a slow Windows
  // runner the whole test can pass 30 s.
  test.slow()
  const remote = join(E2E_DIR, 'cloud-remote.git')
  mkdirSync(remote)
  execFileSync('git', ['init', '-q', '--bare'], { cwd: remote })
  execFileSync('git', ['symbolic-ref', 'HEAD', 'refs/heads/main'], { cwd: remote })

  await page.goto('/')
  await page.getByRole('button', { name: /^New/ }).first().click()
  const dialog = page.getByRole('dialog')
  await expect(dialog.getByRole('list', { name: 'Folders' })).toBeVisible()
  await dialog.getByLabel('Folder path').fill(E2E_DIR)
  await dialog.getByRole('button', { name: 'Go' }).click()
  await dialog.getByLabel('Investigation name').fill('Cloud job')
  await dialog.getByRole('button', { name: 'Create' }).click()
  await expect(page.locator('.investigation')).toHaveText('Cloud job')

  expect((await page.request.post('/api/sync/link', { data: { url: remote } })).ok()).toBe(true)
  const node = await page.request.post('/api/nodes', {
    data: { label: 'TS guess', xyz: WATER, charge: 0, multiplicity: 1 },
  })
  const job = await page.request.post('/api/jobs', {
    data: { name: 'Scan', instructions: 'GFN2-xTB relaxed scan.', nodes: [{ node_id: (await node.json()).id }] },
  })
  const jobId = (await job.json()).id as string
  // As Claude's start_cloud_job does: it answers once Claude Code waits for an answer.
  const started = await page.request.post(`/api/jobs/${jobId}/start`, { timeout: 120_000 })
  expect((await started.json()).status).toBe('waiting_for_answer')

  const panel = page.getByRole('complementary', { name: 'Claude' })
  await expect(panel).toBeVisible({ timeout: 15_000 }) // opened by the question
  const question = panel.getByRole('region', { name: 'Claude Code asks before starting the cloud job' })
  await expect(question).toContainText('Before it starts the cloud job “Scan”')
  const screen = question.getByTestId('launch-question')
  await expect(screen).toContainText('Yes, I trust this folder')
  await screen.click()
  await page.keyboard.press('Enter')
  await expect(question).toContainText('The cloud session for “Scan” has started.')
  await expect(question.getByRole('link', { name: 'Open it on claude.ai' })).toHaveAttribute(
    'href',
    /^https:\/\/claude\.ai\/code\/session_01FakeCloudJob42/,
  )
  const status = await (await page.request.get(`/api/jobs/${jobId}?refresh=false`)).json()
  expect(status.status).toBe('running')
  await question.getByRole('button', { name: 'Close' }).click()
  await expect(question).toHaveCount(0)
})
