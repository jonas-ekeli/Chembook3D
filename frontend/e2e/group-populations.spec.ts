import { join } from 'node:path'
import { expect, test, type Locator, type Page } from '@playwright/test'
import { forgetView } from './demoView.ts'

// T-UI-73, FR-GRP-12, D197: a group's switch puts each member's population % on its card,
// on the group's head what they are of, and a column in the group's panel.
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
  await expect(canvasNode(page, 'T-S0')).toBeVisible()
}

function canvasNode(page: Page, label: string): Locator {
  return page.getByRole('group', { name: `Node ${label}`, exact: true })
}

test.afterEach(async ({ request }) => {
  await request.post('/api/investigations/close')
})

test('population % on the members of a group', async ({ page }) => {
  await openDemo(page)
  const canvas = await (await page.request.get('/api/canvas')).json()
  const idOf = (label: string) => canvas.nodes.find((n: { label: string }) => n.label === label).id
  const made = await page.request.post('/api/groups/reconnect', {
    data: { member_ids: [idOf('A-S1'), idOf('B-S1')], label: 'Pop' },
  })
  expect(made.ok()).toBe(true)
  const group = (await made.json()) as { id: string }
  try {
    await page.reload()
    const box = page.getByRole('group', { name: 'Group Pop' })
    await box.locator('.cgroup-head .cnode-label').click()
    const panel = page.getByLabel('Group inspector')
    // Saved first, then shown: the box follows the stored choice.
    const toggle = panel.getByLabel('Show populations on member cards')
    await toggle.click()
    await expect(toggle).toBeChecked()

    // The demo's A-S1 and B-S1 lie 1.10 kcal/mol apart: 86 % and 14 % at 298.15 K.
    await expect(box.locator('.cgroup-share')).toHaveText('%')
    const members = panel.getByRole('table')
    await expect(members.getByRole('row', { name: /A-S1/ })).toContainText('86 %')
    await expect(members.getByRole('row', { name: /B-S1/ })).toContainText('14 %')
    await box.getByRole('button', { name: 'Expand group' }).click()
    await expect(canvasNode(page, 'A-S1').locator('.cnode-share')).toHaveText('86 %')
    await expect(canvasNode(page, 'B-S1').locator('.cnode-share')).toHaveText('14 %')
    // Collapsed again, so the head is clear of the edges after a reload.
    await box.getByRole('button', { name: 'Collapse group' }).click()

    // Rejected, B-S1 is left out: A-S1 alone has no share.
    await page.request.patch(`/api/nodes/${idOf('B-S1')}`, { data: { status: 'rejected' } })
    await page.reload()
    await expect(box.locator('.cgroup-share')).toHaveText('% n/a')
    await box.locator('.cgroup-head .cnode-label').click()
    await expect(panel.getByRole('status', { name: 'Populations' })).toContainText('Fewer than two members')
    await page.request.patch(`/api/nodes/${idOf('B-S1')}`, { data: { status: 'done' } })

    // Off again, the cards show nothing.
    await page.reload()
    await box.locator('.cgroup-head .cnode-label').click()
    await panel.getByLabel('Show populations on member cards').click()
    await expect(panel.getByLabel('Show populations on member cards')).not.toBeChecked()
    await expect(box.locator('.cgroup-share')).toHaveCount(0)
  } finally {
    await page.request.post(`/api/groups/${group.id}/dissolve`, { data: { restore_branches: true } })
  }
})
