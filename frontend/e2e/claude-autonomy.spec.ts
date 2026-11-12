import { expect, test } from '@playwright/test'
import { openDemo } from './helpers.ts'

// T-UI-93, D223: "Work on its own" in the Claude panel. Ticking it shows the warning first; Turn
// on shows the bar with the job limit chosen and starts a running Claude again, since its
// permissions are from its start; unticking stops it and brings the asking back.

test('Claude works on its own only after the warning, and stops when it is turned off', async ({ page }) => {
  await openDemo(page)
  await page.getByRole('button', { name: 'Claude', exact: true }).click()
  const panel = page.getByRole('complementary', { name: 'Claude' })
  const alone = panel.getByRole('checkbox', { name: 'Work on its own' })
  const bar = panel.getByRole('status', { name: 'Working on its own' })
  const state = async () => (await (await page.request.get('/api/claude/autonomy')).json()) as { on: boolean }
  await expect(alone).not.toBeChecked()
  await expect(panel).toContainText('asks before it changes anything')

  // The warning comes first, and Cancel leaves everything as it was.
  await alone.click()
  const warning = page.getByRole('dialog', { name: 'Let Claude work on its own?' })
  await expect(warning).toContainText('changes this investigation without asking you first')
  await expect(warning).toContainText('Claude can be wrong')
  await expect(warning).toContainText('deleting anything Claude did not make itself')
  await expect(warning.getByLabel('Longest local job Claude may start')).toHaveValue('3600')
  await expect(warning.getByRole('checkbox', { name: /Also start cloud sessions/ })).not.toBeChecked()
  await warning.getByRole('button', { name: 'Cancel' }).click()
  await expect(warning).toHaveCount(0)
  await expect(alone).not.toBeChecked()
  expect((await state()).on).toBe(false)

  // Turned on while Claude runs: it is started again, with the bar above it.
  await panel.getByRole('button', { name: 'New conversation' }).click()
  const terminal = panel.getByTestId('claude-terminal')
  await expect(terminal).toContainText('Fake Claude ready')
  await page.keyboard.type('before')
  await page.keyboard.press('Enter')
  await expect(terminal).toContainText('Fake Claude got: before')
  await alone.click()
  await warning.getByLabel('Longest local job Claude may start').selectOption({ label: '10 minutes' })
  await warning.getByRole('button', { name: 'Turn on' }).click()
  await expect(warning).toHaveCount(0)
  await expect(alone).toBeChecked()
  await expect(bar).toContainText('Claude is working on its own.')
  await expect(bar).toContainText('local jobs of up to 10 minutes')
  await expect(bar).not.toContainText('cloud sessions')
  await expect.poll(async () => await state()).toMatchObject({ on: true, job_limit: 600, cloud: false })
  // A new terminal: what was typed before is gone, and Claude runs again.
  await expect(terminal).not.toContainText('Fake Claude got: before')
  await expect(terminal).toContainText('Fake Claude ready')
  await expect(panel).toContainText('Claude Code is running')

  // It stays on over a reload of the page, as the app keeps it.
  await page.reload()
  await page.getByRole('button', { name: 'Claude', exact: true }).click()
  await expect(bar).toBeVisible()
  await expect(alone).toBeChecked()
  await expect(panel).toContainText('changes it without asking while it works on its own')

  // Turning it off stops Claude and brings the asking back.
  await panel.getByRole('button', { name: 'New conversation' }).click()
  await expect(terminal).toContainText('Fake Claude ready')
  await bar.getByRole('button', { name: 'Stop and turn off' }).click()
  await expect(bar).toHaveCount(0)
  await expect(alone).not.toBeChecked()
  await expect(panel).toContainText('Claude was stopped. It asks before each change again.')
  expect((await state()).on).toBe(false)
})
