import { copyFileSync, mkdirSync, readdirSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import { join, resolve } from 'node:path'
import { expect, test, type Page } from '@playwright/test'
import { forgetView } from './demoView.ts'

// D122, T-SSH-06: log in to Saga (here tests/fake_ssh_server.py on port 8767, see
// playwright.config.ts) with password and one-time code, trusting its host key first; use the
// terminal; reload and find the same shell without logging in again; log out.
const E2E_DIR = process.env.E2E_DIR!
// Synthetic --Link1-- chains with a custom basis set, written by global-setup.ts
const CUSTOM = join(E2E_DIR, 'gaussian')
const WORK = join(E2E_DIR, 'saga', 'cluster', 'work', 'ru-caac')
const FIXTURES = resolve(import.meta.dirname, '..', '..', 'tests', 'fixtures')

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

  // D126, T-UI-29: Claude writes only after the go-ahead in this tab; what it writes is listed.
  const write = (path: string) =>
    page.request.post('/api/server-files/saga/write', { data: { path, text: '#P Opt\n' } })
  expect((await write('claude-off.gjf')).status()).toBe(409)
  const writes = panel.getByTestId('server-writes')
  await writes.getByLabel('Let Claude write new files here').click()
  await expect(writes).toContainText('Claude may write new files in /cluster/work/ru-caac and below.')
  expect((await write('inputs/TS1_opt.gjf')).status()).toBe(200)
  await expect(writes.getByText('Written by Claude (1)')).toBeVisible()
  await writes.getByText('Written by Claude (1)').click()
  await expect(writes).toContainText('/cluster/work/ru-caac/inputs/TS1_opt.gjf')
  expect(readFileSync(join(WORK, 'inputs', 'TS1_opt.gjf'), 'utf-8')).toBe('#P Opt\n')
  await writes.getByRole('button', { name: 'Stop' }).click()
  await expect(writes.getByLabel('Let Claude write new files here')).not.toBeChecked()
  expect((await write('after-stop.gjf')).status()).toBe(409)
  rmSync(join(WORK, 'inputs'), { recursive: true }) // the next test lists the directory

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

  // D123a, T-SSH-09: X11 ticked takes effect in a new shell, which says no X server answers.
  const x11 = panel.getByTestId('server-x11')
  await x11.getByLabel('X11 windows').check()
  await x11.getByLabel('X11 display').fill('127.0.0.1:99.0')
  await x11.getByRole('button', { name: 'Save' }).click()
  await expect(x11).toContainText('Takes effect in a new shell.')
  page.once('dialog', (dialog) => void dialog.accept())
  await x11.getByRole('button', { name: 'New shell' }).click()
  await expect(x11).toContainText('No X server answers on 127.0.0.1:99.0')
  await expect(terminal).toContainText('Welcome to the fake Saga.')
  await x11.getByLabel('X11 windows').uncheck()
  await x11.getByRole('button', { name: 'Save' }).click()
  page.once('dialog', (dialog) => void dialog.accept())
  await x11.getByRole('button', { name: 'New shell' }).click()
  await expect(x11).not.toContainText('X server')
  await expect(x11).not.toContainText('new shell')

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
  // The button follows the terminal's cd, not the directory at login.
  const sendButton = coordinates.getByRole('button', { name: 'Send to Saga' })
  await expect(sendButton).toBeEnabled()
  await expect(sendButton).toHaveAttribute('title', /into \/cluster\/work\/ru-caac on Saga/)
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

test('inspect a running job from the Saga terminal, then import it', async ({ page }) => {
  // D123c, T-UI-27: `inspect FILE` in the terminal opens the Inspect window; Refresh reads the
  // job again as it runs; "Import this file" goes on to the import.
  mkdirSync(WORK, { recursive: true })
  const full = readFileSync(join(FIXTURES, 'gaussian', 'aminationTS-full-unfrz-c1.log'), 'utf-8')
  const lines = full.split('\n')
  const running = join(WORK, 'TS1.log')
  writeFileSync(running, lines.slice(0, Math.floor(lines.length / 3)).join('\n'))
  await newInvestigation(page, 'Saga inspect test')
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
  await expect(panel.getByTestId('server-cwd')).toContainText('inspect FILE')
  await terminal.click()
  await page.keyboard.type('cd /cluster/work/ru-caac')
  await page.keyboard.press('Enter')
  await expect(panel.getByTestId('server-cwd')).toContainText('/cluster/work/ru-caac')
  await page.keyboard.type('inspect TS1.log')
  await page.keyboard.press('Enter')

  const window = page.getByRole('dialog', { name: 'Inspect TS1.log' })
  await expect(window).toContainText('saga:/cluster/work/ru-caac/TS1.log')
  await expect(window).toContainText('has not ended normally')
  const steps = window.getByRole('table', { name: 'Job steps' })
  await expect(steps.getByRole('row')).toHaveCount(2) // the header and one step
  await expect(steps).toContainText('TS optimization')
  await expect(steps).toContainText('not (yet)')
  const shown = window.getByLabel('Structure shown')
  await expect(shown).toContainText(/Structure (\d+) of \1/) // on the last structure
  await expect(window.getByRole('table', { name: 'Convergence criteria' })).toContainText('Maximum force')
  if (process.env.SHOT) await page.screenshot({ path: process.env.SHOT })

  // The job ends: Refresh reads it again, now with its frequency step and the imaginary mode.
  writeFileSync(running, full)
  await window.getByRole('button', { name: 'Refresh' }).click()
  await expect(steps.getByRole('row')).toHaveCount(3)
  await expect(window).not.toContainText('has not ended normally')
  await steps.getByRole('row', { name: /Frequency/ }).click()
  const mode = window.getByLabel('Animate mode')
  await expect(mode.locator('option').nth(1)).toHaveText(/^Mode \d+: \d+\.\di cm⁻¹$/)
  await mode.selectOption({ index: 1 })

  await window.getByRole('button', { name: 'Import this file' }).click()
  const dialog = page.getByRole('dialog', { name: 'Import calculation output' })
  await expect(dialog.getByRole('table', { name: 'Job steps' })).toBeVisible()
  await expect(dialog.getByLabel('Origin device')).toHaveValue('Saga')
  await dialog.getByRole('button', { name: 'Cancel' }).click()
  await expect(page.getByRole('dialog')).toHaveCount(0)
  expect(readdirSync(WORK)).toContain('TS1.log')

  // A file on this computer opens from the import dialog's file browser.
  await page.getByRole('button', { name: 'Import file…' }).click()
  const browse = page.getByRole('dialog', { name: 'Import calculation output' })
  // wait for the first listing, so it cannot replace the folder typed below
  await expect(browse.getByLabel('Folder path')).not.toHaveValue('')
  await browse.getByLabel('Folder path').fill(WORK)
  await browse.getByRole('button', { name: 'Go' }).click()
  await browse.getByRole('listitem').filter({ hasText: 'TS1.log' }).getByRole('button', { name: 'Inspect' }).click()
  await expect(window).toContainText(running)
  await expect(steps.getByRole('row')).toHaveCount(3)
  await window.getByRole('button', { name: 'Close' }).click()
  await expect(page.getByRole('dialog')).toHaveCount(0)

  await panel.getByRole('button', { name: 'Log out' }).click()
  await expect(panel).toContainText('Not logged in')
})
