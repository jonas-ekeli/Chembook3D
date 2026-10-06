import { join } from 'node:path'
import { expect, test, type Locator, type Page } from '@playwright/test'

// Nodes drawn inside an expanded group: a floating note on a member moves without moving the
// canvas, and the labels of edges ending at a member are drawn over those edges.
const E2E_DIR = process.env.E2E_DIR!

async function openDemo(page: Page) {
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

/** Drags from the middle of `handle` by (dx, dy) screen px. */
async function dragBy(page: Page, handle: Locator, dx: number, dy: number) {
  const box = (await handle.boundingBox())!
  const x = box.x + box.width / 2
  const y = box.y + box.height / 2
  await page.mouse.move(x, y)
  await page.mouse.down()
  await page.mouse.move(x + dx, y + dy, { steps: 8 })
  await page.mouse.up()
}

const viewport = (page: Page) => page.locator('.react-flow__viewport').evaluate((el) => (el as HTMLElement).style.transform)

type Saved = { id: string; offset_x: number; offset_y: number; height: number | null }

// Later tests start with no investigation open, even when this one fails.
test.afterEach(async ({ request }) => {
  await request.post('/api/investigations/close')
})

test('notes and edges of the nodes in an expanded group', async ({ page }) => {
  await openDemo(page)
  const canvas = await (await page.request.get('/api/canvas')).json()
  const idOf = (label: string) => canvas.nodes.find((n: { label: string }) => n.label === label).id
  const made = await page.request.post('/api/groups/reconnect', {
    data: { member_ids: [idOf('A-S2'), idOf('B-S2')], label: 'GM' },
  })
  expect(made.ok()).toBe(true)
  const group = (await made.json()) as { id: string }
  const created = await page.request.post(`/api/nodes/${idOf('A-S2')}/notes`, {
    data: { title: 'Member note', body: '<p>On a member</p>', corner: 'bottom-left', placement: 'line', offset_x: 40, offset_y: 40 },
  })
  const note = (await created.json()) as Saved
  const saved = async () => ((await (await page.request.get('/api/notes')).json()) as Saved[]).find((n) => n.id === note.id)!
  try {
    await page.reload()
    const box = page.getByRole('group', { name: 'Group GM' })
    await box.getByRole('button', { name: 'Expand group' }).click()
    const member = canvasNode(page, 'A-S2')
    await expect(member).toBeVisible()

    // Dragging the floating note by its head moves the note, not the canvas.
    const card = member.getByTestId('canvas-note')
    await expect(card).toContainText('On a member')
    const before = await viewport(page)
    await dragBy(page, card.locator('.cnote-title'), -100, 60)
    await expect.poll(async () => (await saved()).offset_x).toBeGreaterThan(note.offset_x + 40)
    expect(await viewport(page)).toBe(before)

    // So does resizing it.
    await dragBy(page, card.getByRole('separator', { name: 'Resize note' }), -60, 40)
    await expect.poll(async () => (await saved()).height).not.toBeNull()
    expect(await viewport(page)).toBe(before)

    // The label of an edge ending at a member is drawn over that edge: at the label's middle
    // (where the edge runs) the topmost element is the label.
    const transitions = canvas.transitions as { id: string; source_id: string; target_id: string }[]
    const edge = transitions.find((t) => t.source_id === idOf('A-S1') && t.target_id === idOf('A-S2'))!
    const label = page.locator(`.edge-label[data-edge="${edge.id}"]`)
    await expect(label).toBeVisible()
    const onTop = await label.evaluate((el) => {
      const element = el as HTMLElement
      element.style.pointerEvents = 'auto'
      const r = element.getBoundingClientRect()
      const hit = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2)
      element.style.pointerEvents = ''
      return !!hit && element.contains(hit)
    })
    expect(onTop).toBe(true)
  } finally {
    await page.request.delete(`/api/notes/${note.id}`)
    await page.request.post(`/api/groups/${group.id}/dissolve`, { data: { restore_branches: true } })
  }
})

test('edge labels in an expanded group keep clear of each other and of the cards', async ({ page }) => {
  await openDemo(page)
  const canvas = await (await page.request.get('/api/canvas')).json()
  const idOf = (label: string) => canvas.nodes.find((n: { label: string }) => n.label === label).id
  const members = ['A-S1', 'A-S2', 'A-S3'].map(idOf)
  const made = await page.request.post('/api/groups/reconnect', { data: { member_ids: members, label: 'GL' } })
  expect(made.ok()).toBe(true)
  const group = (await made.json()) as { id: string }
  try {
    // One column: the edges leave and enter the members' sides, so their middles (where the
    // labels used to be drawn) lie behind the cards, and the labels piled up there (D98).
    expect((await page.request.patch(`/api/groups/${group.id}`, { data: { layout: 'vertical' } })).ok()).toBe(true)
    await page.reload()
    await page.getByRole('group', { name: 'Group GL' }).getByRole('button', { name: 'Expand group' }).click()
    for (const label of ['A-S1', 'A-S2', 'A-S3']) await expect(canvasNode(page, label)).toBeVisible()
    const transitions = canvas.transitions as { id: string; source_id: string; target_id: string }[]
    const inside = transitions.filter((t) => members.includes(t.source_id) && members.includes(t.target_id)).map((t) => t.id)
    expect(inside).toHaveLength(3)
    for (const id of inside) await expect(page.locator(`.edge-label[data-edge="${id}"]`)).toBeVisible()
    await expect(page.locator('.edge-label').filter({ hasText: 'ΔG' }).first()).toBeVisible()

    const clashes = () =>
      page.evaluate((ids) => {
        type Box = { name: string; r: DOMRect }
        const box = (el: Element, name: string): Box => ({ name, r: el.getBoundingClientRect() })
        const labels = ids.map((id) => box(document.querySelector(`.edge-label[data-edge="${id}"]`)!, `label ${id}`))
        const cards = [...document.querySelectorAll('.react-flow__node-structure')].map((el) =>
          box(el, el.getAttribute('aria-label') ?? 'card'),
        )
        const meet = (a: DOMRect, b: DOMRect) =>
          Math.min(a.right, b.right) - Math.max(a.left, b.left) > 0.5 &&
          Math.min(a.bottom, b.bottom) - Math.max(a.top, b.top) > 0.5
        const found: string[] = []
        labels.forEach((a, i) => {
          for (const b of [...labels.slice(i + 1), ...cards]) if (meet(a.r, b.r)) found.push(`${a.name} / ${b.name}`)
        })
        return found
      }, inside)
    await expect.poll(clashes).toEqual([])
  } finally {
    await page.request.post(`/api/groups/${group.id}/dissolve`, { data: { restore_branches: true } })
  }
})
