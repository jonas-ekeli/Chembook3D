import { join } from 'node:path'
import { expect, test, type Page } from '@playwright/test'

// T-MCP-11, D91: what Claude changes through `chembook3d mcp` shows at once, the tab reports
// what is selected, and a delete Claude asks for happens only on the user's Confirm. Claude is
// played here by requests carrying its client name, as the MCP server sends them.
const E2E_DIR = process.env.E2E_DIR!
const CLAUDE = { 'X-Chembook-Client': 'claude' }

function canvasNode(page: Page, label: string) {
  return page.getByRole('group', { name: `Node ${label}`, exact: true })
}

test.afterEach(async ({ request }) => {
  await request.post('/api/investigations/close')
})

test('Claude works in the open investigation, and deletes only when confirmed', async ({ page }) => {
  const folder = join(E2E_DIR, `claude-${Date.now()}`)
  await page.request.post('/api/investigations', { data: { folder, name: 'Claude' } })
  await page.goto('/')
  await expect(page.getByRole('button', { name: 'Close' })).toBeVisible()

  // A change made elsewhere appears without reloading the page.
  const made = await page.request.post('/api/nodes', { data: { label: 'From Claude', pos_x: 0, pos_y: 0 }, headers: CLAUDE })
  const node = (await made.json()) as { id: string }
  await expect(canvasNode(page, 'From Claude')).toBeVisible()
  await page.request.patch(`/api/nodes/${node.id}`, { data: { label: 'Renamed by Claude' }, headers: CLAUDE })
  await expect(canvasNode(page, 'Renamed by Claude')).toBeVisible()

  // What is selected is what Claude reads as "this node".
  await canvasNode(page, 'Renamed by Claude').click()
  await expect
    .poll(async () => ((await (await page.request.get('/api/selection')).json()) as { nodes: { label: string }[] }).nodes)
    .toEqual([expect.objectContaining({ label: 'Renamed by Claude' })])

  const ask = () =>
    page.request.post('/api/confirmations', {
      data: { action: 'delete_node', params: { node_id: node.id }, reason: 'It duplicates INT2.' },
      headers: CLAUDE,
    })

  // Refused: nothing changes.
  await ask()
  let dialog = page.getByRole('dialog', { name: 'Claude asks to delete' })
  await expect(dialog).toContainText('Delete node “Renamed by Claude”.')
  await expect(dialog).toContainText("Claude's reason: It duplicates INT2.")
  await dialog.getByRole('button', { name: 'Refuse' }).click()
  await expect(dialog).toHaveCount(0)
  await expect(canvasNode(page, 'Renamed by Claude')).toBeVisible()

  // Confirmed: the app deletes it, and the history says Claude did.
  await ask()
  dialog = page.getByRole('dialog', { name: 'Claude asks to delete' })
  await dialog.getByRole('button', { name: 'Confirm' }).click()
  await expect(dialog).toHaveCount(0)
  await expect(canvasNode(page, 'Renamed by Claude')).toHaveCount(0)
  await page.getByRole('button', { name: 'History', exact: true }).click()
  await expect(page.locator('.history-view .badge', { hasText: 'claude' }).first()).toBeVisible()
})
