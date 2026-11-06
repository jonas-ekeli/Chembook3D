import { join } from 'node:path'
import { expect, test } from '@playwright/test'
import { investigationCommand } from './appBar.ts'

// D107 (FR-RUN-04). The other tests need the backend running, so here the shut-down itself is
// answered by the test; tests/test_lifetime.py and tests/test_launcher.py stop real servers.

test('Shut down asks first, then the tab says Chembook3D has shut down', async ({ page }) => {
  await page.route('**/api/shutdown', (route) =>
    route.request().method() === 'GET'
      ? route.fulfill({ json: { launched: true, investigation: true, linked: true, claude_panel: true } })
      : route.fulfill({ json: { sync: 'Pushed to GitHub.' } }),
  )
  // The start page has the button; with an investigation open it is in the investigation menu (D180).
  await page.request.post('/api/investigations/close')
  await page.goto('/')
  const button = page.getByRole('button', { name: 'Shut down', exact: true })
  await button.click()
  const dialog = page.getByRole('dialog', { name: 'Shut down Chembook3D?' })
  await expect(dialog).toContainText('The investigation is closed and pushed to GitHub, then Chembook3D stops.')
  await expect(dialog).toContainText('The conversation in the Claude panel stops too.')
  await expect(dialog).toContainText('Start it again with the Chembook3D shortcut.')
  await dialog.getByRole('button', { name: 'Cancel' }).click()
  await expect(dialog).toBeHidden()

  await button.click()
  await dialog.getByRole('button', { name: 'Shut down', exact: true }).click()
  await expect(page.getByRole('heading', { name: 'Chembook3D has shut down' })).toBeVisible()
  await expect(page.getByText('You can close this tab.')).toBeVisible()
  await expect(page.getByText('Sync: Pushed to GitHub.')).toBeVisible()
})

test('a launched tab shows why it was not updated, and when another tab shut the app down', async ({ page }) => {
  let stopping = false
  await page.route('**/api/presence?*', async (route) => {
    await new Promise((resolve) => setTimeout(resolve, 200)) // the real one is a long poll
    await route.fulfill({
      json: { launched: true, stopping, notices: ['Not updated: the checkout has local changes.'], sync: null },
    })
  })
  await page.goto('/')
  const notice = page.getByRole('status', { name: 'Launcher' })
  await expect(notice).toHaveText(/Not updated: the checkout has local changes\./)
  await notice.getByRole('button', { name: 'Dismiss' }).click()
  await expect(notice).toBeHidden()

  stopping = true
  await expect(page.getByRole('heading', { name: 'Chembook3D has shut down' })).toBeVisible()
})

test('Shut down in an open investigation stays in its dialog until the app has stopped', async ({ page }) => {
  // The backend closes the investigation before its slow push, and the tab hears of the close
  // first. It used to go to the start page then, where a second Shut down stopped the server
  // under the first one (its answer was cancelled at the graceful-shutdown timeout).
  const folder = join(process.env.E2E_DIR!, `shut-down-open-${Date.now()}`)
  await page.request.post('/api/investigations', { data: { folder, name: 'Shut down' } })
  let answer!: () => void
  const answered = new Promise<void>((resolve) => (answer = resolve))
  await page.route('**/api/shutdown', async (route) => {
    if (route.request().method() === 'GET') return route.continue()
    await page.request.post('/api/investigations/close') // what the real one does first
    await answered // ... then it pushes, and stops
    await route.fulfill({ json: { sync: 'Pushed to GitHub.' } })
  })
  await page.goto('/')
  await investigationCommand(page, 'Shut down Chembook3D')
  const dialog = page.getByRole('dialog', { name: 'Shut down Chembook3D?' })
  const closeHeard = page.waitForResponse(
    async (response) => response.url().includes('/api/live') && (await response.json()).investigation === null,
  )
  await dialog.getByRole('button', { name: 'Shut down', exact: true }).click()
  await closeHeard
  // The tab used to go to the start page here; give it the time it took, then look.
  await page.waitForTimeout(1000)
  await expect(dialog.getByRole('button', { name: 'Shutting down…' })).toBeDisabled()
  await expect(page.locator('main.start')).toHaveCount(0)

  answer()
  await expect(page.getByRole('heading', { name: 'Chembook3D has shut down' })).toBeVisible()
  await expect(page.getByText('Sync: Pushed to GitHub.')).toBeVisible()
})
