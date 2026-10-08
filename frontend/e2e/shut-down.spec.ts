import { expect, test } from '@playwright/test'

// D107 (FR-RUN-04). The other tests need the backend running, so here the shut-down itself is
// answered by the test; tests/test_lifetime.py and tests/test_launcher.py stop real servers.

test('Shut down asks first, then the tab says Chembook3D has shut down', async ({ page }) => {
  await page.route('**/api/shutdown', (route) =>
    route.request().method() === 'GET'
      ? route.fulfill({ json: { launched: true, investigation: true, linked: true, claude_panel: true } })
      : route.fulfill({ json: { sync: 'Pushed to GitHub.' } }),
  )
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
