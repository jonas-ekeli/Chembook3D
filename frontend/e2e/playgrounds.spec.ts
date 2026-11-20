import { existsSync, readdirSync } from 'node:fs'
import { basename, join } from 'node:path'
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

// D235g, D235i, T-UI-101: "Make a playground first" on Claude's Work on its own warning opens a
// playground with the switch on there; Discard… sends a playground to the recycle bin, from its
// band (closing it) or from its row on the start page.

test('Claude is given a playground first, and playgrounds are discarded', async ({ page }) => {
  const stamp = Date.now()
  const folder = join(E2E_DIR, `alone-${stamp}`)
  const name = `Alone ${stamp}`
  expect((await page.request.post('/api/investigations', { data: { folder, name } })).ok()).toBeTruthy()
  await page.request.post('/api/nodes', { data: { label: 'INT1' } })
  await page.goto('/')
  await expect(page.locator('.investigation')).toHaveText(name)

  await page.getByRole('button', { name: 'Claude', exact: true }).click()
  const panel = page.getByRole('complementary', { name: 'Claude' })
  await panel.getByRole('checkbox', { name: 'Work on its own' }).click()
  const warning = page.getByRole('dialog', { name: 'Let Claude work on its own?' })
  await expect(warning).toContainText('Or make a playground first')
  await expect(warning).toContainText('a copy of the investigation is saved now')
  await warning.getByLabel('Longest local job Claude may start').selectOption({ label: '10 minutes' })
  await warning.getByRole('button', { name: 'Make a playground first' }).click()
  await expect(warning).toHaveCount(0)

  // The playground is open with the switch on there; the original's stays off, with no backup.
  const band = page.getByRole('note', { name: 'Playground' })
  await expect(band).toContainText(`Playground 1 is a playground of ${name}`)
  await expect(panel.getByRole('checkbox', { name: 'Work on its own' })).toBeChecked()
  await expect(panel.getByRole('status', { name: 'Working on its own' })).toContainText('local jobs of up to 10 minutes')
  expect(existsSync(join(folder, 'backups'))).toBe(false)
  // In the playground the warning offers no playground, and says none is needed.
  await panel.getByRole('button', { name: 'Stop and turn off' }).click()
  // The switch is off once the server has answered; ticked before that, the click turns it
  // off again instead of asking.
  await expect(panel.getByRole('checkbox', { name: 'Work on its own' })).not.toBeChecked()
  await panel.getByRole('checkbox', { name: 'Work on its own' }).click()
  await expect(warning).toContainText('changes this playground without asking you first')
  await expect(warning).toContainText('no backup is made')
  await expect(warning.getByRole('button', { name: 'Make a playground first' })).toHaveCount(0)
  await warning.getByRole('button', { name: 'Cancel' }).click()

  // Discard… from the band: named, sized, then gone, and the start page shows.
  await band.getByRole('button', { name: 'Discard…' }).click()
  const discard = page.getByRole('dialog', { name: 'Discard Playground 1?' })
  await expect(discard).toContainText(`Its original, ${name}, is not changed`)
  await expect(discard).toContainText(join(folder, 'playgrounds', 'Playground 1'))
  await discard.getByRole('button', { name: 'Discard', exact: true }).click()
  await expect(discard).toHaveCount(0)
  await expect(page.getByRole('heading', { name: 'Recently opened' })).toBeVisible()
  expect(existsSync(join(folder, 'playgrounds', 'Playground 1'))).toBe(false)
  expect(readdirSync(join(E2E_DIR, 'trash')).some((f) => f.startsWith('Playground 1-'))).toBe(true)
  const recent = page.getByRole('list', { name: 'Recently opened' })
  await expect(recent.getByRole('button', { name: `Playground 1, playground of ${name}` })).toHaveCount(0)

  // Discard… from a start-page row; Cancel first leaves it.
  const made = await page.request.post('/api/playgrounds', { data: { folder, name: 'Second' } })
  expect(made.ok()).toBeTruthy()
  await startPage(page)
  const row = recent.getByRole('listitem').filter({ hasText: 'Playground of' }).filter({ hasText: 'Second' })
  await row.getByRole('button', { name: 'More for Second' }).click()
  await page.getByRole('menuitem', { name: 'Discard…' }).click()
  const second = page.getByRole('dialog', { name: 'Discard Second?' })
  await second.getByRole('button', { name: 'Cancel' }).click()
  await expect(row).toHaveCount(1)
  await row.getByRole('button', { name: 'More for Second' }).click()
  await page.getByRole('menuitem', { name: 'Discard…' }).click()
  await second.getByRole('button', { name: 'Discard', exact: true }).click()
  await expect(row).toHaveCount(0)
  await expect(recent.getByRole('listitem').filter({ hasText: name }).first()).toBeVisible()
})

// D235h, T-UI-102: Merge… from the band opens the original with the review: the playground's new
// node ticked, a label changed on both sides a conflict to pick, a removal unticked; Merge brings
// the ticked ones and the playground then opens read-only.

test('a playground is merged into its original after a review', async ({ page }) => {
  const stamp = Date.now()
  const folder = join(E2E_DIR, `merge-${stamp}`)
  const name = `Merge ${stamp}`
  expect((await page.request.post('/api/investigations', { data: { folder, name } })).ok()).toBeTruthy()
  const both = await (await page.request.post('/api/nodes', { data: { label: 'INT1' } })).json()
  const kept = await (await page.request.post('/api/nodes', { data: { label: 'Kept' } })).json()
  expect((await page.request.post('/api/playgrounds', { data: { folder } })).ok()).toBeTruthy()
  // In the playground: a new node, INT1 renamed, Kept removed.
  await page.request.post('/api/nodes', { data: { label: 'From the playground' } })
  await page.request.patch(`/api/nodes/${both.id}`, { data: { label: 'INT1 (playground)' } })
  await page.request.delete(`/api/nodes/${kept.id}`)
  // In the original, INT1 renamed too.
  await page.request.post('/api/investigations/open', { data: { folder } })
  await page.request.patch(`/api/nodes/${both.id}`, { data: { label: 'INT1 (original)' } })
  await page.request.post('/api/investigations/open', { data: { folder: join(folder, 'playgrounds', 'Playground 1') } })
  await page.goto('/')

  const band = page.getByRole('note', { name: 'Playground' })
  // The answer to Merge… arrives only after the live link has shown the original: it still opens
  // the review (a slower machine saw "The investigation changed" instead, 2026-11-17).
  await page.route('**/api/playgrounds/merge/start', async (route) => {
    const response = await route.fetch()
    await expect(page.locator('.investigation')).toHaveText(name)
    await route.fulfill({ response })
  })
  await band.getByRole('button', { name: 'Merge…' }).click()
  const review = page.getByRole('dialog', { name: `Merge Playground 1 into ${name}` })
  await expect(review).toBeVisible()
  await expect(page.locator('.investigation')).toHaveText(name) // the original is open behind it
  await expect(band).toHaveCount(0)
  await expect(review.getByRole('checkbox', { name: 'From the playground' })).toBeChecked()
  await expect(review.getByRole('checkbox', { name: 'Kept' })).not.toBeChecked()
  const conflict = review.getByRole('radiogroup', { name: /which version/ })
  await expect(conflict).toHaveCount(1)
  await expect(review.getByRole('button', { name: 'Merge', exact: true })).toBeDisabled()
  await expect(review).toContainText('Pick a version for 1 conflict first')
  await conflict.getByRole('radio', { name: "Playground's" }).check()
  await review.getByRole('button', { name: 'Preview' }).click()
  await expect(review.getByRole('status')).toContainText('Merging would add 1, change 1 and remove 0 records')
  await review.getByRole('button', { name: 'Merge', exact: true }).click()
  await expect(review).toHaveCount(0)
  await expect(page.getByText(/Merged Playground 1: 1 added, 1 changed, 0 removed/)).toBeVisible()

  // The original has the playground's node and label, and still has Kept.
  const labels = (await (await page.request.get('/api/nodes')).json()).map((n: { label: string }) => n.label)
  expect(labels.sort()).toEqual(['From the playground', 'INT1 (playground)', 'Kept'])

  // The playground now opens read-only, without Merge.
  await startPage(page)
  const recent = page.getByRole('list', { name: 'Recently opened' })
  const row = recent.getByRole('button', { name: `Playground 1, playground of ${name}` })
  await expect(row).toContainText('merged just now')
  await row.click()
  await expect(band).toContainText('read-only')
  await expect(band.getByRole('button', { name: 'Merge…' })).toHaveCount(0)
  const refused = await page.request.post('/api/nodes', { data: { label: 'Too late' } })
  expect(refused.status()).toBe(409)
  expect((await refused.json()).detail).toContain('is read-only')
})

// D235j, T-UI-103: Undo merge on the merge's line of the original's history lists what it does,
// puts the original back and lets the playground be changed and merged again.
test("a playground's merge is undone from the original's history", async ({ page, baseURL }) => {
  const stamp = Date.now()
  const folder = join(E2E_DIR, `undo-merge-${stamp}`)
  const name = `Undo merge ${stamp}`
  expect((await page.request.post('/api/investigations', { data: { folder, name } })).ok()).toBeTruthy()
  const node = await (await page.request.post('/api/nodes', { data: { label: 'INT1' } })).json()
  expect((await page.request.post('/api/playgrounds', { data: { folder } })).ok()).toBeTruthy()
  await page.request.post('/api/nodes', { data: { label: 'From the playground' } })
  await page.request.patch(`/api/nodes/${node.id}`, { data: { label: 'INT1 (playground)' } })
  // Merged through the routes, as the review would with its default ticks.
  const playground = join(folder, 'playgrounds', 'Playground 1')
  const origin = { Origin: baseURL ?? '' }
  const started = await page.request.post('/api/playgrounds/merge/start', {
    data: { folder: playground },
    headers: origin,
  })
  expect(started.ok()).toBeTruthy()
  const review = (await started.json()).review as { sections: { changes: { id: string; ticked: boolean }[] }[] }
  const ticked = review.sections.flatMap((s) => s.changes.filter((c) => c.ticked).map((c) => c.id))
  const merged = await page.request.post('/api/playgrounds/merge', { data: { folder: playground, ticked }, headers: origin })
  expect(merged.ok()).toBeTruthy()

  await page.goto('/')
  await page.getByRole('button', { name: 'History', exact: true }).click()
  const history = page.getByRole('list', { name: 'History' })
  await expect(history).toContainText('Merged from playground “Playground 1”: 1 added, 1 changed, 0 removed')
  await history.getByRole('button', { name: 'Undo merge' }).click()
  const undo = page.getByRole('dialog', { name: 'Undo the merge of Playground 1?' })
  const what = undo.getByRole('list', { name: 'What the undo does' })
  await expect(what).toContainText('Removes 1 record the merge added: “From the playground”')
  await expect(what).toContainText('Puts back the label “INT1 (playground)” had before the merge')
  await undo.getByRole('button', { name: 'Undo merge' }).click()
  await expect(undo).toHaveCount(0)
  await expect(page.getByText(/Undid the merge of Playground 1\. Playground 1 can be changed and merged again\. A copy from before is in backups as investigation\.sqlite\.undo-merge-/)).toBeVisible()
  await expect(history).toContainText('Undid the merge from playground “Playground 1”')
  await expect(history.getByRole('button', { name: 'Undo merge' })).toHaveCount(0)
  const labels = (await (await page.request.get('/api/nodes')).json()).map((n: { label: string }) => n.label)
  expect(labels).toEqual(['INT1'])

  // The playground opens for changes again, with Merge….
  await startPage(page)
  const recent = page.getByRole('list', { name: 'Recently opened' })
  const row = recent.getByRole('button', { name: `Playground 1, playground of ${name}` })
  await expect(row).not.toContainText('merged')
  await row.click()
  const band = page.getByRole('note', { name: 'Playground' })
  await expect(band).not.toContainText('read-only')
  await expect(band.getByRole('button', { name: 'Merge…' })).toBeVisible()
})

// D236, T-UI-104: Make its own investigation… from the band moves the playground out of its
// original into the folder picked (next to the original by default) and opens it there under the
// new name, without the band; the start page no longer lists it under the original.

test('a playground is made its own investigation', async ({ page }) => {
  const stamp = Date.now()
  const folder = join(E2E_DIR, `detach-${stamp}`)
  const name = `Detach ${stamp}`
  expect((await page.request.post('/api/investigations', { data: { folder, name } })).ok()).toBeTruthy()
  expect((await page.request.post('/api/playgrounds', { data: { folder } })).ok()).toBeTruthy()
  await page.request.post('/api/nodes', { data: { label: 'Tried here' } })
  await page.goto('/')

  const band = page.getByRole('note', { name: 'Playground' })
  await band.getByRole('button', { name: 'Make its own investigation…' }).click()
  const dialog = page.getByRole('dialog', { name: 'Make Playground 1 its own investigation' })
  await expect(dialog).toContainText(`moves out of ${name}`)
  await expect(dialog).toContainText('can no longer be merged')
  await expect(dialog.getByRole('textbox', { name: 'Investigation name' })).toHaveValue(`${name} (Playground 1)`)
  // next to the original (Windows may give the temporary folder's long name, so only its end)
  await expect(dialog.getByRole('textbox', { name: 'Folder path' })).toHaveValue(new RegExp(`${basename(E2E_DIR)}$`))
  const study = `Study ${stamp}`
  await dialog.getByRole('textbox', { name: 'Investigation name' }).fill(study)
  await dialog.getByRole('button', { name: 'Move and open' }).click()
  await expect(dialog).toHaveCount(0)

  await expect(page.locator('.investigation')).toHaveText(study)
  await expect(band).toHaveCount(0)
  await expect(page.getByRole('group', { name: 'Node Tried here', exact: true })).toBeVisible()
  await expect(page.getByText('Playground 1 is now its own investigation, in')).toContainText(study)
  expect(existsSync(join(folder, 'playgrounds', 'Playground 1'))).toBe(false)
  expect(existsSync(join(E2E_DIR, study, 'investigation.sqlite'))).toBe(true)
  expect(existsSync(join(E2E_DIR, study, 'playground'))).toBe(false)

  await startPage(page)
  const recent = page.getByRole('list', { name: 'Recently opened' })
  await expect(recent.getByRole('button', { name: `Playground 1, playground of ${name}` })).toHaveCount(0)
  await expect(recent.getByRole('listitem').filter({ hasText: study }).first()).toBeVisible()
  await expect(recent.getByRole('listitem').filter({ hasText: name }).first()).toBeVisible()
})
