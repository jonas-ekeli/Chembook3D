import { mkdirSync } from 'node:fs'
import { join } from 'node:path'
import { expect, test, type Page } from '@playwright/test'
import { forgetView } from './demoView.ts'

// D122, T-SSH-06: log in to Saga (here tests/fake_ssh_server.py on port 8767, see
// playwright.config.ts) with password and one-time code, trusting its host key first; use the
// terminal; reload and find the same shell without logging in again; log out.
const E2E_DIR = process.env.E2E_DIR!

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

test('log in to Saga with password and code, and come back to the same shell', async ({ page }) => {
  mkdirSync(join(E2E_DIR, 'saga', 'cluster', 'work', 'ru-caac'), { recursive: true })
  await openDemo(page)
  await page.getByRole('button', { name: 'Saga', exact: true }).click()
  const panel = page.getByRole('complementary', { name: 'Saga' })
  await expect(panel).toBeVisible()
  await expect(panel).toContainText('Not logged in')

  await panel.getByLabel('Host').fill('127.0.0.1')
  await panel.getByLabel('Port').fill('8767')
  await panel.getByLabel('User name').fill('jonas')
  await panel.getByRole('button', { name: 'Save' }).click()
  await expect(panel.getByRole('button', { name: 'Save' })).toHaveCount(0)
  await panel.getByRole('button', { name: 'Log in' }).click()

  const login = panel.getByRole('group', { name: 'Log in to Saga' })
  await expect(login).toContainText('SHA256:')
  await login.getByRole('button', { name: 'Trust and continue' }).click()
  await expect(login.getByLabel('Password:')).toHaveAttribute('type', 'password')
  await login.getByLabel('Password:').fill('correct horse')
  await login.getByRole('button', { name: 'Send' }).click()
  await login.getByLabel('Verification code:').fill('123456')
  await login.getByRole('button', { name: 'Send' }).click()

  const terminal = panel.getByTestId('server-terminal')
  await expect(terminal).toContainText('Welcome to the fake Saga.')
  await expect(panel).toContainText('jonas@127.0.0.1:8767')
  await terminal.click()
  await page.keyboard.type('cd /cluster/work/ru-caac')
  await page.keyboard.press('Enter')
  await expect(panel.getByTestId('server-cwd')).toContainText('/cluster/work/ru-caac')

  // The page goes away and comes back: still logged in, same shell, same directory.
  await page.reload()
  await openDemo(page)
  await page.getByRole('button', { name: 'Saga', exact: true }).click()
  await expect(panel.getByTestId('server-cwd')).toContainText('/cluster/work/ru-caac')
  await expect(terminal).toContainText('Welcome to the fake Saga.')
  await terminal.click()
  await page.keyboard.type('pwd')
  await page.keyboard.press('Enter')
  await expect(terminal).toContainText('jonas@login-1 ru-caac$ pwd')

  // The Claude tab is beside it.
  await panel.getByRole('tab', { name: 'Claude' }).click()
  await expect(page.getByRole('complementary', { name: 'Claude' })).toContainText('Claude')
  await page.getByRole('complementary', { name: 'Claude' }).getByRole('tab', { name: /Saga/ }).click()

  await panel.getByRole('button', { name: 'Log out' }).click()
  await expect(panel).toContainText('Not logged in')
  await expect(panel.getByRole('button', { name: 'Log in' })).toBeVisible()
})
