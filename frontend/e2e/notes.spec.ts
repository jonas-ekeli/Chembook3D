import { writeFileSync } from 'node:fs'
import { join } from 'node:path'
import { pathToFileURL } from 'node:url'
import { expect, test, type Locator, type Page } from '@playwright/test'
import { forgetView } from './demoView.ts'

// T-UI-08, FR-NOTE-01…05, D85: a note pinned to a node's card, with formatted text, an SVG
// pasted as text, a PNG chosen from disk, an empty clipboard, HTML that tries to run a script,
// collapsing, moving it to another corner, the history, the read-only copy, and deleting it.
const E2E_DIR = process.env.E2E_DIR!

// A drawing as ChemDraw's "Save As SVG" writes one, with a script and a handler to be removed.
const SVG = `<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" width="120" height="40" viewBox="0 0 120 40" onload="window.svgRan = 1">
<script>window.svgRan = 2</script>
<path d="M10 30 L40 10 L70 30" stroke="black" fill="none" stroke-width="2"/>
<text x="80" y="25" font-size="14">Ru</text>
</svg>`
// 2 × 2 red pixels
const PNG = Buffer.from(
  'iVBORw0KGgoAAAANSUhEUgAAAAIAAAACCAIAAAD91JpzAAAAEElEQVR4nGP4z8AARAwQCgAf7gP9i18U1AAAAABJRU5ErkJggg==',
  'base64',
)

async function openDemo(page: Page) {
  await forgetView(page.request, join(E2E_DIR, 'demo'))
  await page.request.put('/api/settings', { data: { energy_unit: 'kcal/mol' } })
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

/** Which corner of the card its note sits outside of, from where both are drawn. */
async function corner(page: Page, label: string): Promise<string> {
  const card = await canvasNode(page, label).locator('.cnode').boundingBox()
  const box = await canvasNode(page, label).getByTestId('canvas-note').boundingBox()
  if (!card || !box) return 'not drawn'
  const vertical = box.y + box.height < card.y + 20 ? 'top' : box.y > card.y + card.height - 20 ? 'bottom' : 'middle'
  const horizontal = box.x > card.x + card.width - 20 ? 'right' : box.x + box.width < card.x + 20 ? 'left' : 'inside'
  return `${vertical}-${horizontal}`
}

/** Paste as the browser would, with the given clipboard contents. */
async function paste(target: Locator, data: Record<string, string>) {
  await target.evaluate((element, entries) => {
    const transfer = new DataTransfer()
    for (const [type, value] of Object.entries(entries)) transfer.setData(type, value)
    element.dispatchEvent(new ClipboardEvent('paste', { clipboardData: transfer, bubbles: true, cancelable: true }))
  }, data)
}

async function loaded(images: Locator, count: number) {
  await expect(images).toHaveCount(count)
  for (let i = 0; i < count; i++) {
    await expect.poll(() => images.nth(i).evaluate((img: HTMLImageElement) => img.complete && img.naturalWidth)).toBeGreaterThan(0)
  }
}

// Later tests start with no investigation open, even when this one fails.
test.afterEach(async ({ request }) => {
  await request.post('/api/investigations/close')
})

test('a note pinned to a node card, with text and pictures', async ({ page, context }) => {
  await openDemo(page)
  await canvasNode(page, 'A-S2').click()
  const inspector = page.getByLabel('Node inspector')
  await inspector.getByRole('button', { name: 'Add pinned note' }).click()

  const dialog = page.getByRole('dialog', { name: 'New note on A-S2' })
  await dialog.getByLabel('Title').fill('Rotamer check')
  const text = dialog.getByRole('textbox', { name: 'Note text' })
  await text.click()
  await dialog.getByRole('button', { name: 'Bold' }).click()
  await page.keyboard.type('Keep syn')
  await dialog.getByRole('button', { name: 'Bold' }).click()
  await page.keyboard.type(' CAAC rotamer.')
  await page.keyboard.press('Enter')

  // SVG markup on the clipboard as text becomes a picture, cleaned on the server.
  await paste(text, { 'text/plain': SVG })
  await expect(text.locator('img[data-note-image]')).toHaveCount(1)
  // A PNG chosen from disk.
  await dialog.getByLabel('Insert picture').setInputFiles({ name: 'red.png', mimeType: 'image/png', buffer: PNG })
  await loaded(text.locator('img[data-note-image]'), 2)
  // Nothing a page can read: what ChemDraw's own copy looks like to a browser.
  await paste(text, {})
  await expect(dialog.getByRole('status')).toContainText('Edit › Copy As › PNG')
  // HTML that tries to run something keeps only its formatting.
  await paste(text, { 'text/html': '<i>see SI</i><img src=x onerror="window.htmlRan = 1"><script>window.htmlRan = 2</script>' })
  await expect(text.locator('i', { hasText: 'see SI' })).toBeVisible()

  await dialog.getByRole('radio', { name: 'Blue' }).click()
  await dialog.getByRole('button', { name: 'Save note' }).click()
  await expect(dialog).toHaveCount(0)

  // On the canvas, at the card's top right corner, with both pictures.
  const note = canvasNode(page, 'A-S2').getByTestId('canvas-note')
  await expect(note).toHaveCount(1)
  await expect(note).toHaveClass(/note-blue/)
  await expect(note.locator('b', { hasText: 'Keep syn' })).toBeVisible()
  await expect(note).toContainText('CAAC rotamer.')
  await expect(note).toContainText('see SI')
  await loaded(note.locator('img'), 2)
  await expect.poll(() => corner(page, 'A-S2')).toBe('top-right')
  expect(await page.evaluate(() => [(window as { svgRan?: number }).svgRan, (window as { htmlRan?: number }).htmlRan])).toEqual([
    undefined,
    undefined,
  ])
  // The stored SVG kept its drawing and lost its script.
  const svgId = await note.locator('img').first().evaluate((img: HTMLImageElement) => img.src.split('/').pop()!)
  const svg = await (await page.request.get(`/api/note-images/${svgId}`)).text()
  expect(svg).toContain('>Ru</text>')
  expect(svg).not.toContain('svgRan')

  // Collapsing is saved: it stays collapsed after a reload.
  await note.getByRole('button', { name: 'Collapse note' }).click()
  await expect(note.locator('img')).toHaveCount(0)
  await expect(note).toContainText('Rotamer check')
  await expect.poll(async () => (await (await page.request.get('/api/notes')).json())[0].collapsed).toBe(true)
  await page.reload()
  await expect(canvasNode(page, 'A-S2').getByTestId('canvas-note').getByRole('button', { name: 'Expand note' })).toBeVisible()
  await canvasNode(page, 'A-S2').getByTestId('canvas-note').getByRole('button', { name: 'Expand note' }).click()

  // Double-click to edit: move it to the bottom left corner.
  await canvasNode(page, 'A-S2').getByTestId('canvas-note').locator('.cnote-body').dblclick()
  const edit = page.getByRole('dialog', { name: 'Note on A-S2' })
  await edit.getByLabel(/^Corner/).selectOption('bottom-left')
  await edit.getByRole('button', { name: 'Save note' }).click()
  await expect(edit).toHaveCount(0)
  // The canvas draws the move once its new data arrives, so wait for it.
  await expect.poll(() => corner(page, 'A-S2')).toBe('bottom-left')

  // The node's history shows it; the panel reads it in full.
  await canvasNode(page, 'A-S2').click()
  await expect(inspector.getByRole('region', { name: 'Pinned notes' })).toContainText('CAAC rotamer.')
  const history = inspector.getByRole('list', { name: 'History' })
  await expect(history).toContainText('Pinned note “Rotamer check” moved: top right → bottom left')
  await expect(history).toContainText('Pinned a note “Rotamer check”')

  // The read-only copy holds the note and its pictures, and opens with no server.
  const exported = await page.request.post('/api/snapshot', { data: {} })
  const path = join(E2E_DIR, 'notes-copy.html')
  writeFileSync(path, await exported.body())
  const shared = await context.newPage()
  await shared.goto(pathToFileURL(path).href)
  const sharedNote = canvasNode(shared, 'A-S2').getByTestId('canvas-note')
  await expect(sharedNote).toContainText('CAAC rotamer.')
  await loaded(sharedNote.locator('img'), 2)
  await expect(sharedNote.locator('img').first()).toHaveAttribute('src', /^data:image\/svg\+xml;base64,/)
  // Collapsing works there too, without saving anything.
  await sharedNote.getByRole('button', { name: 'Collapse note' }).click()
  await expect(sharedNote.locator('img')).toHaveCount(0)
  await canvasNode(shared, 'A-S2').click()
  await expect(shared.getByLabel('Node inspector').getByRole('region', { name: 'Pinned notes' })).toContainText('Keep syn')
  await shared.close()

  // Deleting it.
  await inspector.getByRole('region', { name: 'Pinned notes' }).getByRole('button', { name: 'Edit…' }).click()
  await edit.getByRole('button', { name: 'Delete…' }).click()
  await edit.getByRole('button', { name: 'Delete this note' }).click()
  await expect(edit).toHaveCount(0)
  await expect(canvasNode(page, 'A-S2').getByTestId('canvas-note')).toHaveCount(0)
  await expect(history).toContainText('Deleted the pinned note “Rotamer check”')

})

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

type SavedNote = { id: string; placement: string; offset_x: number; offset_y: number; width: number; height: number | null }

test('a note floats apart from its card, joined by a line, and is resized', async ({ page }) => {
  // T-UI-09, FR-NOTE-06, D87
  await openDemo(page)
  const canvas = await (await page.request.get('/api/canvas')).json()
  const target = canvas.nodes.find((n: { label: string }) => n.label === 'T-S0')
  const created = await page.request.post(`/api/nodes/${target.id}/notes`, {
    data: { title: 'Apart', body: '<p>Floating beside the card</p>' },
  })
  const id = ((await created.json()) as SavedNote).id
  const saved = async () => ((await (await page.request.get('/api/notes')).json()) as SavedNote[]).find((n) => n.id === id)!
  await page.reload()
  const note = canvasNode(page, 'T-S0').getByTestId('canvas-note')
  await expect(note).toContainText('Floating beside the card')
  await expect(canvasNode(page, 'T-S0').getByTestId('note-line')).toHaveCount(0)

  // Detached: it moves a little away from the corner, joined to it by a line.
  await note.getByRole('button', { name: 'Detach note' }).click()
  await expect(canvasNode(page, 'T-S0').getByTestId('note-line')).toHaveCount(1)
  await expect.poll(async () => (await saved()).placement).toBe('line')
  await expect.poll(() => corner(page, 'T-S0')).toBe('top-right')

  // Dragged by its head, further up and to the right.
  const before = await saved()
  await dragBy(page, note.locator('.cnote-title'), 120, -80)
  await expect.poll(async () => (await saved()).offset_x).toBeGreaterThan(before.offset_x + 40)
  expect((await saved()).offset_y).toBeGreaterThan(before.offset_y + 25)
  const line = canvasNode(page, 'T-S0').getByTestId('note-line').locator('line')
  expect(Number(await line.getAttribute('x2'))).toBeGreaterThan(40)

  // Resized from its outer corner: wider and taller.
  const size = (await note.boundingBox())!
  await dragBy(page, note.getByRole('separator', { name: 'Resize note' }), 80, -60)
  await expect.poll(async () => (await saved()).height).not.toBeNull()
  const resized = await saved()
  expect(resized.width).toBeGreaterThan(before.width + 25)
  await expect.poll(async () => (await note.boundingBox())!.height).toBeGreaterThan(size.height + 20)

  // From the editor: floating with no line, then back on the corner, fitting its text.
  await note.locator('.cnote-body').dblclick()
  const edit = page.getByRole('dialog', { name: 'Note on T-S0' })
  await edit.getByLabel('Placement').selectOption('free')
  await edit.getByRole('button', { name: 'Save note' }).click()
  await expect(edit).toHaveCount(0)
  await expect(canvasNode(page, 'T-S0').getByTestId('note-line')).toHaveCount(0)
  await expect(note).toBeVisible()
  await expect.poll(async () => (await saved()).placement).toBe('free')

  await note.locator('.cnote-body').dblclick()
  await edit.getByLabel('Placement').selectOption('corner')
  await edit.getByLabel('Height').selectOption('')
  await edit.getByRole('button', { name: 'Save note' }).click()
  await expect(edit).toHaveCount(0)
  await expect.poll(async () => [(await saved()).placement, (await saved()).height]).toEqual(['corner', null])
  await expect.poll(() => corner(page, 'T-S0')).toBe('top-right')

  // The history has none of it: where a note is drawn is layout (A37).
  await canvasNode(page, 'T-S0').click()
  const history = page.getByLabel('Node inspector').getByRole('list', { name: 'History' })
  await expect(history).toContainText('Pinned a note “Apart”')
  await expect(history).not.toContainText('Placement')
  expect((await page.request.delete(`/api/notes/${id}`)).ok()).toBe(true)
})
