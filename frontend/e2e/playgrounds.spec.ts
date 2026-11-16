import { join } from 'node:path'
import { expect, test } from '@playwright/test'
import { E2E_DIR, startPage } from './helpers.ts'

// D235, T-UI-100: New playground… on a recent row makes a working copy and opens it with the
// playground band; Sync and Cloud jobs are gone there; the start page lists it under its original.

test('a playground is made from the start page and listed under its original', async ({ page }) => {
  const stamp = Date.now()
  const folder = join(E2E_DIR, `playground-${stamp}`)
  const name = `Original ${stamp}`
  expect((await page.request.post('/api/investigations', { data: { folder, name } })).ok()).toBeTruthy()
  await page.request.post('/api/nodes', { data: { label: 'INT1' } })
  await startPage(page)

  const recent = page.getByRole('list', { name: 'Recently opened' })
  const row = recent.getByRole('listitem').filter({ hasText: name }).first()
  await row.getByRole('button', { name: `More for ${name}` }).click()
  await page.getByRole('menuitem', { name: 'New playground…' }).click()
  const dialog = page.getByRole('dialog', { name: 'New playground' })
  await expect(dialog.getByRole('textbox', { name: 'Playground name' })).toHaveValue('Playground 1')
  await expect(dialog).toContainText(`${folder}/playgrounds/`)
  await dialog.getByRole('button', { name: 'Make and open' }).click()
  await expect(dialog).toHaveCount(0)

  // Open: the band says what it is, its name says so too, and there is nothing to sync.
  const band = page.getByRole('note', { name: 'Playground' })
  await expect(band).toContainText(`Playground 1 is a playground of ${name}`)
  await expect(band).toContainText('made just now')
  await expect(page.locator('.investigation')).toHaveText(`${name} (Playground 1)`)
  await expect(page.getByRole('group', { name: 'Node INT1', exact: true })).toBeVisible()
  await expect(page.getByRole('button', { name: 'Cloud jobs' })).toHaveCount(0)
  await expect(page.getByRole('button', { name: 'Not synced' })).toHaveCount(0)
  await page.getByRole('button', { name: `${name} (Playground 1), investigation menu` }).click()
  await expect(page.getByRole('menuitem', { name: /Sync with GitHub/ })).toHaveCount(0)
  await page.keyboard.press('Escape')

  // A node made here stays here: the original opens as it was, without the band.
  await page.request.post('/api/nodes', { data: { label: 'Only in the playground' } })
  await band.getByRole('button', { name: 'Open original' }).click()
  await expect(page.locator('.investigation')).toHaveText(name)
  await expect(band).toHaveCount(0)
  await expect(page.getByRole('group', { name: 'Node INT1', exact: true })).toBeVisible()
  await expect(page.getByRole('group', { name: 'Node Only in the playground', exact: true })).toHaveCount(0)
  await page.request.post('/api/nodes', { data: { label: 'INT2' } })

  // The start page: the playground under its original, not a recent row of its own.
  await startPage(page)
  const playground = recent.getByRole('button', { name: `Playground 1, playground of ${name}` })
  await expect(playground).toContainText('made just now')
  await expect(playground).toContainText('original changed 1 thing since')
  const rows = await recent.getByRole('listitem').allTextContents()
  const at = rows.findIndex((text) => text.includes(name) && !text.includes('Playground of'))
  expect(rows[at + 1]).toContain('Playground 1')
  const settings = await (await page.request.get('/api/settings')).json()
  expect(settings.recent.filter((f: string) => f.includes(String(stamp)))).toEqual([folder])

  // The next one is offered as Playground 2.
  await row.getByRole('button', { name: `More for ${name}` }).click()
  await page.getByRole('menuitem', { name: 'New playground…' }).click()
  await expect(dialog.getByRole('textbox', { name: 'Playground name' })).toHaveValue('Playground 2')
  await dialog.getByRole('button', { name: 'Cancel' }).click()

  await playground.click()
  await expect(band).toBeVisible()
  await expect(page.getByRole('group', { name: 'Node Only in the playground', exact: true })).toBeVisible()
})
