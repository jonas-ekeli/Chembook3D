import { copyFileSync, mkdirSync, readdirSync, readFileSync, writeFileSync } from 'node:fs'
import { join } from 'node:path'
import { expect, test, type Page } from '@playwright/test'
import { forgetView } from './demoView.ts'

// D122, T-SSH-06: log in to Saga (here tests/fake_ssh_server.py on port 8767, see
// playwright.config.ts) with password and one-time code, trusting its host key first; use the
// terminal; reload and find the same shell without logging in again; log out.
const E2E_DIR = process.env.E2E_DIR!
// Synthetic --Link1-- chains with a custom basis set, written by global-setup.ts
const CUSTOM = join(E2E_DIR, 'gaussian')
const WORK = join(E2E_DIR, 'saga', 'cluster', 'work', 'ru-caac')

test.describe.configure({ mode: 'serial' }) // the second test finds the server saved and trusted

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

async function newInvestigation(page: Page, name: string) {
  await page.goto('/')
  await page.getByRole('button', { name: /^New/ }).first().click()
  const dialog = page.getByRole('dialog')
  await expect(dialog.getByRole('list', { name: 'Folders' })).toBeVisible()
  await dialog.getByLabel('Folder path').fill(E2E_DIR)
  await dialog.getByRole('button', { name: 'Go' }).click()
  await expect(dialog.getByLabel('Folder path')).toHaveValue(E2E_DIR)
  await dialog.getByLabel('Investigation name').fill(name)
  await dialog.getByRole('button', { name: 'Create' }).click()
  await expect(page.locator('.investigation')).toHaveText(name)
}

async function nameCustom(dialog: ReturnType<Page['getByRole']>, label: string, value: string) {
  const field = dialog.getByLabel(label)
  await field.fill(value)
  await field.press('Enter')
}

test('import outputs from the directory the Saga terminal is in', async ({ page }) => {
  // D122e, T-SSH-07
  mkdirSync(WORK, { recursive: true })
  for (const name of ['MeI_TS.out', 'MeI_TS_QZ.out', 'MeI_min.out']) copyFileSync(join(CUSTOM, name), join(WORK, name))
  writeFileSync(join(WORK, 'MeI_TS.gjf'), '#P PBEPBE/Gen Opt\n\nMeI\n\n0 1\n')
  await newInvestigation(page, 'Saga import test')
  await page.getByRole('button', { name: 'Saga', exact: true }).click()
  const panel = page.getByRole('complementary', { name: 'Saga' })
  await panel.getByRole('button', { name: 'Log in' }).click()
  const login = panel.getByRole('group', { name: 'Log in to Saga' })
  await login.getByLabel('Password:').fill('correct horse')
  await login.getByRole('button', { name: 'Send' }).click()
  await login.getByLabel('Verification code:').fill('123456')
  await login.getByRole('button', { name: 'Send' }).click()
  const terminal = panel.getByTestId('server-terminal')
  await expect(terminal).toContainText('Welcome to the fake Saga.')
  await terminal.click()
  await page.keyboard.type('cd /cluster/work/ru-caac')
  await page.keyboard.press('Enter')
  await expect(panel.getByTestId('server-cwd')).toContainText('/cluster/work/ru-caac')

  // One output goes through the import dialog, its origin kept as saga:/path.
  await panel.getByRole('button', { name: 'Import from here' }).click()
  let files = page.getByRole('dialog', { name: 'Import from Saga' })
  await expect(files).toContainText('/cluster/work/ru-caac')
  await expect(files.getByLabel('MeI_TS.out')).toBeVisible()
  await expect(files.getByLabel('MeI_TS.gjf')).toHaveCount(0)
  await expect(files).toContainText('Show every file (1 more)')
  await files.getByLabel('MeI_TS.out').check()
  await files.getByRole('button', { name: 'Import', exact: true }).click()
  let dialog = page.getByRole('dialog')
  await expect(dialog.getByRole('table', { name: 'Job steps' })).toBeVisible()
  await expect(dialog.getByLabel('Origin device')).toHaveValue('Saga')
  await nameCustom(dialog, 'Name for the custom basis set', 'modDZ')
  await nameCustom(dialog, 'Name for the custom dispersion', 'GD3MBJ')
  await dialog.getByRole('button', { name: 'Import', exact: true }).click()
  const inspector = page.getByLabel('Node inspector')
  await expect(inspector.getByRole('heading', { name: 'MeI_TS' })).toBeVisible()
  const calculations = inspector.getByRole('list', { name: 'Calculations' })
  await calculations.getByRole('button', { name: /Frequency/ }).click()
  await expect(inspector.getByLabel('Device or server')).toHaveValue('Saga')
  await expect(inspector.getByLabel('Original path')).toHaveValue('saga:/cluster/work/ru-caac/MeI_TS.out')

  // Several go through the batch import, which matches each to a node.
  await panel.getByRole('button', { name: 'Import from here' }).click()
  files = page.getByRole('dialog', { name: 'Import from Saga' })
  await files.getByLabel('MeI_TS_QZ.out').check()
  await files.getByLabel('MeI_min.out').check()
  await files.getByRole('button', { name: 'Batch import (2)' }).click()
  dialog = page.getByRole('dialog', { name: 'Import a folder of results' })
  await expect(dialog).toContainText('saga:/cluster/work/ru-caac')
  const table = dialog.getByRole('table', { name: 'Files' })
  await expect(table.getByRole('row', { name: 'MeI_TS_QZ.out' })).toContainText('Adds to “MeI_TS”')
  await expect(table.getByRole('row', { name: 'MeI_min.out' })).toContainText('New node “MeI_min”')
  await nameCustom(dialog, 'Name for the custom basis set', 'modQZ')
  await dialog.getByRole('button', { name: 'Import 2 files' }).click()
  await expect(page.getByRole('status').getByText(/Imported 2 files from saga:/)).toBeVisible()

  // Importing changed nothing on the server.
  expect(readdirSync(WORK).sort()).toEqual(['MeI_TS.gjf', 'MeI_TS.out', 'MeI_TS_QZ.out', 'MeI_min.out'])

  // D122f, T-SSH-08: the node's structure goes back as MeI_TS.xyz, replacing a file only when asked.
  await page.getByRole('list', { name: 'Nodes' }).getByText('MeI_TS', { exact: true }).click()
  await expect(inspector.getByRole('heading', { name: 'MeI_TS' })).toBeVisible()
  const coordinates = inspector.getByRole('region', { name: 'Coordinates' })
  await coordinates.getByRole('button', { name: 'Send to Saga' }).click()
  await expect(coordinates).toContainText('Wrote /cluster/work/ru-caac/MeI_TS.xyz on Saga')
  const sent = join(WORK, 'MeI_TS.xyz')
  expect(readFileSync(sent, 'utf-8')).toContain('MeI_TS')
  writeFileSync(sent, 'my own edit\n')
  await coordinates.getByRole('button', { name: 'Send to Saga' }).click()
  const ask = page.getByRole('dialog', { name: 'Replace the file on Saga?' })
  await expect(ask).toContainText('/cluster/work/ru-caac/MeI_TS.xyz is already there')
  await ask.getByRole('button', { name: 'Keep it' }).click()
  expect(readFileSync(sent, 'utf-8')).toBe('my own edit\n')
  await coordinates.getByRole('button', { name: 'Send to Saga' }).click()
  await ask.getByRole('button', { name: 'Replace' }).click()
  await expect(coordinates).toContainText('Replaced /cluster/work/ru-caac/MeI_TS.xyz on Saga')
  expect(readFileSync(sent, 'utf-8')).toContain('MeI_TS')

  await panel.getByRole('button', { name: 'Log out' }).click()
  await expect(panel).toContainText('Not logged in')
})
