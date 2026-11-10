import { expect, test } from '@playwright/test'
import { openDemo } from './helpers.ts'

// D92: the Claude panel runs `claude` (here tests/fake_claude.py, see playwright.config.ts) in a
// terminal beside the notebook: start, type, hide and show again without losing it, stop, and
// continue the last conversation.

test('Claude panel runs Claude Code beside the notebook', async ({ page }) => {
  await openDemo(page)
  const panel = page.getByRole('complementary', { name: 'Claude' })
  await expect(panel).toBeHidden()
  await page.getByRole('button', { name: 'Claude', exact: true }).click()
  await expect(panel).toBeVisible()
  await expect(panel).toContainText('runs here with your own Claude sign-in')

  await panel.getByRole('button', { name: 'New conversation' }).click()
  const terminal = panel.getByTestId('claude-terminal')
  await expect(terminal).toContainText('Fake Claude ready')
  await expect(panel).toContainText('Claude Code is running')
  await page.keyboard.type('which barrier is highest?')
  await page.keyboard.press('Enter')
  await expect(terminal).toContainText('Fake Claude got: which barrier is highest?')

  // Hiding keeps it running.
  await panel.getByRole('button', { name: 'Hide' }).click()
  await expect(panel).toBeHidden()
  await page.getByRole('button', { name: 'Claude', exact: true }).click()
  await expect(terminal).toContainText('Fake Claude got: which barrier is highest?')
  await terminal.click()
  await page.keyboard.type('exit')
  await page.keyboard.press('Enter')
  await expect(panel.getByRole('status')).toContainText('Claude Code has ended.')

  await panel.getByRole('button', { name: 'Continue last conversation' }).click()
  await expect(terminal).toContainText('Fake Claude ready')
  await panel.getByRole('button', { name: 'Stop' }).click()
  await expect(panel.getByRole('status')).toContainText('Claude Code was stopped.')
})
