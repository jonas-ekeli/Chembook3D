import { join } from 'node:path'
import { expect, test, type Page } from '@playwright/test'
import { startPage } from './appBar.ts'
import { forgetView } from './demoView.ts'

// After a change, the app fetches the energy view once (it was fetched twice, the first answer
// dropped), and the investigation's history only while History is shown.
const E2E_DIR = process.env.E2E_DIR!

async function openDemo(page: Page) {
  await forgetView(page.request, join(E2E_DIR, 'demo'))
  await startPage(page)
  await page.getByRole('button', { name: /^Open/ }).first().click()
  const dialog = page.getByRole('dialog')
  await expect(dialog.getByRole('list', { name: 'Folders' })).toBeVisible()
  await dialog.getByLabel('Folder path').fill(join(E2E_DIR, 'demo'))
  await dialog.getByRole('button', { name: 'Go' }).click()
  await dialog.getByRole('button', { name: 'Open', exact: true }).click()
  await expect(page.getByRole('dialog')).toHaveCount(0)
  await expect(page.getByRole('group', { name: 'Node T-S0', exact: true })).toBeVisible()
}

test('a change fetches the energy view once, and the history only when shown', async ({ page }) => {
  await openDemo(page)
  const asked: string[] = []
  page.on('request', (request) => {
    const path = new URL(request.url()).pathname
    if (['/api/canvas', '/api/history', '/api/energies/view'].includes(path)) asked.push(path)
  })
  const count = (path: string) => asked.filter((p) => p === path).length

  // A change made elsewhere (as Claude's are) reloads the open tab.
  const canvas = await (await page.request.get('/api/canvas')).json()
  const node = canvas.nodes.find((n: { label: string }) => n.label === 'T-S0')
  const reloaded = page.waitForResponse((r) => new URL(r.url()).pathname === '/api/canvas')
  const changed = await page.request.patch(`/api/nodes/${node.id}`, { data: { notes: `${node.notes} ` } })
  expect(changed.ok()).toBe(true)
  await reloaded
  await expect.poll(() => count('/api/energies/view')).toBe(1)
  await page.waitForTimeout(1000) // a second fetch came after the options arrived
  expect(count('/api/energies/view')).toBe(1)
  expect(count('/api/history')).toBe(0)

  await page.getByRole('button', { name: 'History', exact: true }).click()
  await expect(page.getByRole('heading', { name: 'Investigation history' })).toBeVisible()
  await expect.poll(() => count('/api/history')).toBe(1)

  await page.request.patch(`/api/nodes/${node.id}`, { data: { notes: node.notes } })
  await expect.poll(() => count('/api/history')).toBe(2) // shown, so fetched again
})
