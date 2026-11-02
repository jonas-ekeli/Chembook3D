import { join } from 'node:path'
import { expect, test, type Page } from '@playwright/test'
import { investigationCommand } from './appBar.ts'

function deferred() {
  let resolve!: () => void
  const promise = new Promise<void>((done) => { resolve = done })
  return { promise, resolve }
}

async function openFolder(page: Page, folder: string) {
  if (await page.locator('header .investigation').count()) await investigationCommand(page, 'Open investigation…')
  else await page.getByRole('button', { name: 'Open investigation…', exact: true }).click()
  const dialog = page.getByRole('dialog')
  await expect(dialog.getByRole('list', { name: 'Folders' })).toBeVisible()
  await dialog.getByLabel('Folder path').fill(folder)
  await dialog.getByRole('button', { name: 'Go' }).click()
  await dialog.getByRole('button', { name: 'Open', exact: true }).click()
  await expect(dialog).toHaveCount(0)
}

test('two tabs reconcile open, switch, stale records/view writes and close, including a start-page tab', async ({ page, context }) => {
  const stamp = Date.now()
  const a = join(process.env.E2E_DIR!, `identity-a-${stamp}`)
  const b = join(process.env.E2E_DIR!, `identity-b-${stamp}`)
  await page.request.post('/api/investigations', { data: { folder: a, name: 'Identity A' } })
  const madeA = await (await page.request.post('/api/nodes', { data: { label: 'Only in A' } })).json()
  await page.request.post('/api/investigations', { data: { folder: b, name: 'Identity B' } })
  await page.request.post('/api/nodes', { data: { label: 'Only in B' } })
  await page.request.put('/api/view-state', { data: { drawer: { open: false, tab: 'table', profile_id: null } } })
  await page.request.post('/api/investigations/close')
  const second = await context.newPage()
  const records = deferred()
  const save = deferred()
  const saveFinished = deferred()
  let heldRecords = false
  let fetchingRecords = false
  let heldSave = false
  let savedStatus = 0
  let oldRecordsFinished = false
  try {
    await page.goto('/')
    await second.goto('/')
    await expect(second.getByRole('button', { name: 'Open investigation…', exact: true })).toBeVisible()
    await openFolder(page, a)
    await expect(second.locator('header .investigation')).toHaveText('Identity A')
    await expect(second.getByRole('group', { name: 'Node Only in A', exact: true })).toBeVisible()
    await second.getByRole('group', { name: 'Node Only in A', exact: true }).click()
    await expect(second.getByLabel('Node inspector')).toContainText('Only in A')

    // Hold an actual A canvas response while live discovers B; release it after B is shown.
    await second.route('**/api/canvas', async (route) => {
      if (fetchingRecords) return route.continue()
      fetchingRecords = true
      const response = await route.fetch()
      heldRecords = true
      await records.promise
      await route.fulfill({ response })
      oldRecordsFinished = true
    })
    await page.request.patch(`/api/nodes/${madeA.id}`, { data: { notes: 'Force an external reload' } })
    await expect.poll(() => heldRecords).toBe(true)

    // The request was made by A's UI and retains A's expected header even when sent late.
    await second.route('**/api/view-state', async (route) => {
      if (route.request().method() !== 'PUT' || heldSave) return route.continue()
      heldSave = true
      expect(decodeURIComponent(route.request().headers()['x-chembook-investigation'])).toBe(a)
      await save.promise
      const response = await route.fetch()
      savedStatus = response.status()
      await route.fulfill({ response })
      saveFinished.resolve()
    })
    await second.getByRole('button', { name: 'Profile and table' }).click()
    await expect.poll(() => heldSave).toBe(true)
    await openFolder(page, b)
    await expect(second.locator('header .investigation')).toHaveText('Identity B')
    await expect(second.getByRole('group', { name: 'Node Only in B', exact: true })).toBeVisible()
    await expect(second.getByRole('group', { name: 'Node Only in A', exact: true })).toHaveCount(0)
    await expect(second.getByLabel('Node inspector')).toHaveCount(0)
    await expect(second.getByLabel('Energy drawer')).toHaveCount(0)
    records.resolve()
    save.resolve()
    await expect.poll(() => oldRecordsFinished).toBe(true)
    await expect.poll(() => savedStatus).toBe(409)
    // Status is known before fulfill finishes. Reloading then cancels the still-owned route.
    await saveFinished.promise
    await expect(second.getByRole('group', { name: 'Node Only in B', exact: true })).toBeVisible()
    expect((await (await page.request.get('/api/view-state')).json()).drawer.open).toBe(false)

    // A page reload while a notebook is open must also establish identity before settings.
    await second.reload()
    await expect(second.locator('header .investigation')).toHaveText('Identity B')
    await expect(second.getByRole('group', { name: 'Node Only in B', exact: true })).toBeVisible()
    await investigationCommand(page, 'Close investigation')
    await expect(second.getByRole('button', { name: 'Open investigation…', exact: true })).toBeVisible()
    await expect(second.locator('header .investigation')).toHaveCount(0)
  } finally {
    records.resolve()
    save.resolve()
    await second.unrouteAll({ behavior: 'wait' })
    await second.close()
    await page.request.post('/api/investigations/close')
  }
})

test('live closing before the close response retains the captured notebook sync outcome', async ({ page }) => {
  const folder = join(process.env.E2E_DIR!, `identity-close-${Date.now()}`)
  await page.request.post('/api/investigations', { data: { folder, name: 'Delayed close' } })
  await page.goto('/')
  await expect(page.locator('header .investigation')).toHaveText('Delayed close')
  const gate = deferred()
  let pending = false
  try {
    await page.route('**/api/investigations/close', async (route) => {
      // Close the real backend first. Its live response tells the UI about None while the
      // simulated push is still pending; only the operation's final sync outcome is mocked.
      await route.fetch()
      pending = true
      await gate.promise
      await route.fulfill({ json: { state: 'error', message: 'Push failed after closing' } })
    })
    await investigationCommand(page, 'Close investigation')
    await expect.poll(() => pending).toBe(true)
    await expect(page.getByRole('button', { name: 'Open investigation…', exact: true })).toBeVisible()
    gate.resolve()
    await expect(page.getByRole('alert')).toContainText('Push failed after closing')
  } finally {
    gate.resolve()
    await page.unrouteAll({ behavior: 'wait' })
    await page.request.post('/api/investigations/close')
  }
})
