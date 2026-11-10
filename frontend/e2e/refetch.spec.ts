import { expect, test } from '@playwright/test'
import { openDemo, pageSettled } from './helpers.ts'

// After a change, the app fetches the energy view once (it was fetched twice, the first answer
// dropped), and the investigation's history only while History is shown.

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
  const answered = (path: string) => page.waitForResponse((r) => new URL(r.url()).pathname === path)
  const reloaded = answered('/api/canvas')
  const options = answered('/api/energies/options')
  const changed = await page.request.patch(`/api/nodes/${node.id}`, { data: { notes: `${node.notes} ` } })
  expect(changed.ok()).toBe(true)
  await reloaded
  await expect.poll(() => count('/api/energies/view')).toBe(1)
  // A second fetch came once the options had arrived; it would have been asked for by now.
  await (await options).finished()
  await pageSettled(page)
  expect(count('/api/energies/view')).toBe(1)
  expect(count('/api/history')).toBe(0)

  await page.getByRole('button', { name: 'History', exact: true }).click()
  await expect(page.getByRole('heading', { name: 'Investigation history' })).toBeVisible()
  await expect.poll(() => count('/api/history')).toBe(1)

  await page.request.patch(`/api/nodes/${node.id}`, { data: { notes: node.notes } })
  await expect.poll(() => count('/api/history')).toBe(2) // shown, so fetched again
})
