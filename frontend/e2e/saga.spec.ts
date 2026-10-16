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
  // D127, T-UI-30: the button says HPC until logged in; its tab chooses the cluster, Saga first.
  await page.getByRole('button', { name: 'HPC', exact: true }).click()
  const panel = page.locator('aside.claude-panel')
  await expect(page.getByRole('complementary', { name: 'HPC' })).toBeVisible()
  await expect(panel.getByLabel('Cluster')).toHaveValue('saga')
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
  // Logged in: the button and the tab take the cluster's name, and "+" can add another.
  await expect(page.getByRole('complementary', { name: 'Saga' })).toBeVisible()
  await expect(page.getByRole('button', { name: 'Saga', exact: true })).toBeVisible()
  await expect(page.getByRole('button', { name: 'HPC', exact: true })).toHaveCount(0)
  await expect(panel.getByRole('tab', { name: '+' })).toBeVisible()
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
  await expect(page.getByRole('complementary', { name: 'HPC' })).toBeVisible()
  await expect(page.getByRole('button', { name: 'HPC', exact: true })).toBeVisible()

  // Another cluster, ready-filled from Sigma2's; then off the list again.
  await panel.getByLabel('Cluster').selectOption({ label: 'Add a cluster…' })
  await expect(panel.getByRole('group', { name: 'Sigma2 clusters' }).getByRole('button')).toHaveText(['Betzy', 'Olivia'])
  await panel.getByRole('button', { name: 'Betzy' }).click()
  await expect(panel.getByRole('textbox', { name: 'Host' })).toHaveValue('betzy.sigma2.no')
  await panel.getByRole('button', { name: 'Add cluster' }).click()
  await expect(panel.getByLabel('Cluster').locator('option:checked')).toHaveText('Betzy')
  await expect(panel.getByRole('button', { name: 'Log in' })).toBeVisible()
  page.once('dialog', (dialog) => void dialog.accept())
  await panel.getByRole('button', { name: 'Remove from the list' }).click()
  await expect(panel.getByLabel('Cluster').locator('option')).toHaveText(['Saga', 'Add a cluster…'])
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
  await page.getByRole('button', { name: 'HPC', exact: true }).click()
  const panel = page.locator('aside.claude-panel')
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
  // The name filter hides files without the text (D78 as in the file browser); Escape clears it.
  const filter = files.getByLabel('Filter by name')
  await filter.fill('qz')
  await expect(files.getByLabel('MeI_TS_QZ.out')).toBeVisible()
  await expect(files.getByLabel('MeI_min.out')).toHaveCount(0)
  await expect(files.getByLabel('MeI_TS.out')).toHaveCount(0)
  await files.getByLabel('MeI_TS_QZ.out').check()
  await filter.fill('*.gjf')
  await expect(files).toContainText('Show every file (1 more)')
  await expect(files.getByLabel('MeI_TS_QZ.out')).toBeChecked()
  await filter.press('Escape')
  await expect(files).toBeVisible()
  await expect(filter).toHaveValue('')
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
  await page.getByRole('button', { name: 'HPC', exact: true }).click()
  const panel = page.locator('aside.claude-panel')
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

test('inspect several jobs at once and step through them', async ({ page }) => {
  // D129, T-UI-32: `inspect *.log` opens the matching files as one list in the Inspect window,
  // stepped through with Next and Previous; so do several files chosen in "Import from here" and
  // the files a name filter matches in the file browser.
  const batch = join(WORK, 'batch')
  mkdirSync(batch, { recursive: true })
  copyFileSync(join(FIXTURES, 'gaussian', 'aminationTS-full-unfrz-c1.log'), join(batch, 'a1.log'))
  copyFileSync(join(FIXTURES, 'gaussian', 'dvb_scan_relaxed.log'), join(batch, 'a2.log'))
  copyFileSync(join(FIXTURES, 'gaussian', 'aziridinium-phos-full-c1.log'), join(batch, 'b1.out'))
  await newInvestigation(page, 'Saga inspect several')
  await page.getByRole('button', { name: 'HPC', exact: true }).click()
  const panel = page.locator('aside.claude-panel')
  await panel.getByRole('button', { name: 'Log in' }).click()
  const login = panel.getByRole('group', { name: 'Log in to Saga' })
  await login.getByLabel('Password:').fill('correct horse')
  await login.getByRole('button', { name: 'Send' }).click()
  await login.getByLabel('Verification code:').fill('123456')
  await login.getByRole('button', { name: 'Send' }).click()
  const terminal = panel.getByTestId('server-terminal')
  await expect(terminal).toContainText('Welcome to the fake Saga.')
  await terminal.click()
  await page.keyboard.type('cd /cluster/work/ru-caac/batch')
  await page.keyboard.press('Enter')
  await expect(panel.getByTestId('server-cwd')).toContainText('/cluster/work/ru-caac/batch')
  await page.keyboard.type('inspect *.log')
  await page.keyboard.press('Enter')

  const files = page.getByRole('group', { name: 'Files to inspect' })
  await expect(page.getByRole('dialog', { name: 'Inspect a1.log' })).toBeVisible()
  await expect(files).toContainText('1 of 2')
  await expect(files.getByRole('button', { name: '◀ Previous file' })).toBeDisabled()
  const steps = page.getByRole('table', { name: 'Job steps' })
  await expect(steps).toContainText('TS optimization')
  await files.getByRole('button', { name: 'Next file ▶' }).click()
  const second = page.getByRole('dialog', { name: 'Inspect a2.log' })
  await expect(second).toContainText('saga:/cluster/work/ru-caac/batch/a2.log')
  await expect(files).toContainText('2 of 2')
  await expect(files.getByRole('button', { name: 'Next file ▶' })).toBeDisabled()
  await expect(second.getByLabel('Converged points only')).toBeChecked()
  if (process.env.SHOT) await page.screenshot({ path: process.env.SHOT })
  await files.getByLabel('File', { exact: true }).selectOption('a1.log')
  await expect(page.getByRole('dialog', { name: 'Inspect a1.log' })).toContainText('batch/a1.log')
  await page.getByRole('dialog', { name: 'Inspect a1.log' }).getByRole('button', { name: 'Close' }).click()
  await expect(page.getByRole('dialog')).toHaveCount(0)

  // Several files chosen in "Import from here"
  await panel.getByRole('button', { name: 'Import from here' }).click()
  const remote = page.getByRole('dialog', { name: 'Import from Saga' })
  await remote.getByLabel('a2.log').check()
  await remote.getByLabel('b1.out').check()
  await remote.getByRole('button', { name: 'Inspect (2)' }).click()
  await expect(page.getByRole('dialog', { name: 'Inspect a2.log' })).toBeVisible()
  await files.getByRole('button', { name: 'Next file ▶' }).click()
  await expect(page.getByRole('dialog', { name: 'Inspect b1.out' })).toContainText('batch/b1.out')
  await page.getByRole('dialog').getByRole('button', { name: 'Close' }).click()
  await expect(page.getByRole('dialog')).toHaveCount(0)

  // The files a name filter matches in the file browser on this computer
  await page.getByRole('button', { name: 'Import file…' }).click()
  const browse = page.getByRole('dialog', { name: 'Import calculation output' })
  // wait for the first listing, so it cannot replace the folder typed below
  await expect(browse.getByLabel('Folder path')).not.toHaveValue('')
  await browse.getByLabel('Folder path').fill(batch)
  await browse.getByRole('button', { name: 'Go' }).click()
  await expect(browse.getByRole('listitem').filter({ hasText: 'b1.out' })).toBeVisible()
  await browse.getByLabel('Filter by name').fill('*.log')
  await browse.getByRole('button', { name: 'Inspect the 2 matching files' }).click()
  await expect(page.getByRole('dialog', { name: 'Inspect a1.log' })).toContainText(join(batch, 'a1.log'))
  await expect(files).toContainText('1 of 2')
  await page.getByRole('dialog').getByRole('button', { name: 'Close' }).click()
  await expect(page.getByRole('dialog')).toHaveCount(0)

  await panel.getByRole('button', { name: 'Log out' }).click()
  await expect(panel).toContainText('Not logged in')
})

test('join scans from the Saga terminal, refresh a running one and save it to Saga', async ({ page }) => {
  // D138, T-UI-40: `scans FILE` opens the scan editor; Refresh reads a running scan again and the
  // path follows it; "Add from Saga…" adds more; Save to Saga never replaces a file there; and
  // "Import from here" opens the chosen files in the editor too.
  const scans = join(WORK, 'scans')
  mkdirSync(scans, { recursive: true })
  const full = readFileSync(join(FIXTURES, 'gaussian', 'dvb_scan_relaxed.log'), 'utf-8')
  const lines = full.split('\n')
  const running = join(scans, 'scanA.log')
  writeFileSync(running, lines.slice(0, Math.floor(lines.length / 2)).join('\n'))
  copyFileSync(join(FIXTURES, 'orca', 'dvb_scan_unrelaxed.out'), join(scans, 'scanB.out'))
  await newInvestigation(page, 'Saga scan editor')
  await page.getByRole('button', { name: 'HPC', exact: true }).click()
  const panel = page.locator('aside.claude-panel')
  await panel.getByRole('button', { name: 'Log in' }).click()
  const login = panel.getByRole('group', { name: 'Log in to Saga' })
  await login.getByLabel('Password:').fill('correct horse')
  await login.getByRole('button', { name: 'Send' }).click()
  await login.getByLabel('Verification code:').fill('123456')
  await login.getByRole('button', { name: 'Send' }).click()
  const terminal = panel.getByTestId('server-terminal')
  await expect(terminal).toContainText('Welcome to the fake Saga.')
  await expect(panel.getByTestId('server-cwd')).toContainText('scans FILE…')
  await terminal.click()
  await page.keyboard.type('cd /cluster/work/ru-caac/scans')
  await page.keyboard.press('Enter')
  await expect(panel.getByTestId('server-cwd')).toContainText('/cluster/work/ru-caac/scans')
  await page.keyboard.type('scans scanA.log')
  await page.keyboard.press('Enter')

  const editor = page.getByRole('dialog', { name: 'Scan editor' })
  const list = editor.getByRole('list', { name: 'Scans in the editor' })
  await expect(list).toContainText('scanA.log')
  await expect(list.getByTitle('saga:/cluster/work/ru-caac/scans/scanA.log')).toBeVisible()
  const slider = editor.getByLabel('Frame', { exact: true })
  await expect(slider).toHaveAttribute('max', '5') // six points so far

  // The scan runs on: Refresh reads it again and the path's end follows it.
  writeFileSync(running, full)
  await editor.getByRole('button', { name: 'Refresh' }).click()
  await expect(slider).toHaveAttribute('max', '12')
  await expect(list).toContainText('13 points')

  // More from the server's directory
  await editor.getByRole('button', { name: 'Add from Saga…' }).click()
  const add = page.getByRole('dialog', { name: 'Add scans from Saga' })
  await add.getByLabel('scanB.out').check()
  await add.getByRole('button', { name: 'Add', exact: true }).click()
  await expect(add).toHaveCount(0)
  await expect(list).toContainText('scanB.out')
  await expect(slider).toHaveAttribute('max', '12') // another level: listed, not joined

  // Saved into the terminal's directory, never over a file there
  await editor.getByRole('button', { name: 'Save to Saga' }).click()
  await expect(editor).toContainText('Saved to Saga as /cluster/work/ru-caac/scans/scanA_joined.xyz')
  await editor.getByRole('button', { name: 'Save to Saga' }).click()
  await expect(editor).toContainText(
    'Saved to Saga as /cluster/work/ru-caac/scans/scanA_joined_v2.xyz (that name was taken)',
  )
  const saved = readFileSync(join(scans, 'scanA_joined.xyz'), 'utf-8')
  expect(saved.split('\n')[1]).toContain('chembook3d joined scan 1')
  expect(readFileSync(join(scans, 'scanA_joined_v2.xyz'), 'utf-8')).toBe(saved)
  await editor.getByRole('button', { name: 'Close' }).click()
  await expect(page.getByRole('dialog')).toHaveCount(0)

  // "Import from here" opens the chosen files in the editor.
  await panel.getByRole('button', { name: 'Import from here' }).click()
  const remote = page.getByRole('dialog', { name: 'Import from Saga' })
  await remote.getByLabel('scanA.log').check()
  await remote.getByRole('button', { name: 'Scan editor', exact: true }).click()
  await expect(list).toContainText('scanA.log')
  await expect(slider).toHaveAttribute('max', '12')
  await editor.getByRole('button', { name: 'Close' }).click()
  await expect(page.getByRole('dialog')).toHaveCount(0)

  await panel.getByRole('button', { name: 'Log out' }).click()
  await expect(panel).toContainText('Not logged in')
})
