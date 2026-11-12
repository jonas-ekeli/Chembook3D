import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { expect, test, type Page } from '@playwright/test'
import { E2E_DIR, canvasNode, demoFolder, openDemo, openFolder } from './helpers.ts'

// T-UI-94 (D226): what Claude asks the open page to do is done there with the page's own code,
// and the answer goes back: Tidy and its undo, showing a record, the view controls, groups and
// a sub-investigation's canvas. Claude is played by requests carrying its client name, as the
// MCP server sends them.
const CLAUDE = { 'X-Chembook-Client': 'claude' }
type Canvas = { nodes: { id: string; label: string; pos_x: number; pos_y: number }[] }
type Selection = {
  view: string
  nodes: { label: string }[]
  energy_view: { level: string | null; type: string | null; reference: { label: string } | null }
}

const card = canvasNode

async function ask(page: Page, what: string, data: Record<string, unknown> = {}) {
  const response = await page.request.post(`/api/page/${what}`, { data, headers: CLAUDE })
  const body = (await response.json()) as Record<string, unknown>
  return { status: response.status(), body, text: JSON.stringify(body) }
}

async function places(page: Page): Promise<Record<string, { id: string; x: number; y: number }>> {
  const canvas = (await (await page.request.get('/api/canvas')).json()) as Canvas
  return Object.fromEntries(canvas.nodes.map((n) => [n.label, { id: n.id, x: n.pos_x, y: n.pos_y }]))
}

const selection = async (page: Page) => (await (await page.request.get('/api/selection')).json()) as Selection

// Later tests start with no investigation open, even when this one fails.
test.afterEach(async ({ request }) => {
  await request.post('/api/investigations/close')
})

test('Claude tidies the canvas, shows a node and changes the view through the open page', async ({ page }) => {
  const demo = demoFolder()
  await page.request.post('/api/investigations/open', { data: { folder: demo } })
  const before = await places(page)
  const muddle: Record<string, { x: number; y: number }> = {
    'A-S1': { x: 240, y: 440 },
    'B-S1': { x: 300, y: 220 },
    'A-S2': { x: 500, y: 220 },
    'B-S2': { x: 480, y: 480 },
  }
  await page.request.put('/api/positions', {
    data: { positions: Object.fromEntries(Object.entries(muddle).map(([label, p]) => [before[label].id, p])) },
  })
  const muddled = await places(page)
  await openFolder(page, demo)
  await expect(card(page, 'T-S0')).toBeVisible()
  const toolbar = page.locator('.canvasbar')

  // Tidy: the page's own, so its cards move at once and its Undo tidy is offered.
  const tidied = await ask(page, 'tidy', { mode: 'columns' })
  expect(tidied.status, tidied.text).toBe(200)
  expect(tidied.body.moved as number).toBeGreaterThan(0)
  const after = await places(page)
  expect(after['A-S1'].x).toBe(after['B-S1'].x)
  expect(after['A-S2'].x).toBe(after['B-S2'].x)
  await expect(toolbar.getByRole('button', { name: 'Undo tidy' })).toBeVisible()
  expect((await ask(page, 'tidy', { mode: 'columns' })).body).toEqual({ moved: 0, note: 'Already tidy: nothing to move.' })

  const undone = await ask(page, 'tidy/undo')
  expect(undone.body, undone.text).toEqual({ undone: true })
  expect(await places(page)).toEqual(muddled)
  await expect(toolbar.getByRole('button', { name: 'Undo tidy' })).toHaveCount(0)
  const again = await ask(page, 'tidy/undo')
  expect(again.status).toBe(409)
  expect(again.text).toContain('There is no Tidy to undo')

  // Show a record: selected on the page, which reports it as the user's own click would.
  const shown = await ask(page, 'show', { id: before['A-S2'].id })
  expect(shown.body, shown.text).toEqual({ shown: 'A-S2', kind: 'node' })
  await expect.poll(async () => (await selection(page)).nodes.map((n) => n.label)).toEqual(['A-S2'])
  const unknown = await ask(page, 'show', { id: 'nothing' })
  expect(unknown.status).toBe(409)
  expect(unknown.text).toContain('No node, group, sub-investigation, edge or branch has this id')

  // The view controls: what the cards show, the energy type and the reference.
  const view = await ask(page, 'view', { mode: 'compact', energy_type: 'E', reference_id: before['A-S1'].id })
  expect(view.body, view.text).toEqual({ set: { mode: 'compact', energy_type: 'E', reference: 'A-S1' } })
  await expect(page.getByRole('button', { name: 'Compact', exact: true })).toHaveAttribute('aria-pressed', 'true')
  await expect(page.getByLabel('Energy type')).toHaveValue('E')
  await expect
    .poll(async () => {
      const s = await selection(page)
      return [s.energy_view.type, s.energy_view.reference?.label]
    })
    .toEqual(['E', 'A-S1'])
  const noLevel = await ask(page, 'view', { level: 'nope' })
  expect(noLevel.status).toBe(409)
  expect(noLevel.text).toContain('No level has this key. The levels with energies: ')

  // The drawer, on its Table tab.
  const drawer = page.getByLabel('Energy drawer')
  await expect(drawer).toBeHidden()
  expect((await ask(page, 'view', { drawer_tab: 'table' })).body).toEqual({ set: { drawer_tab: 'table', drawer: true } })
  await expect(drawer).toBeVisible()
  await expect(drawer.getByRole('button', { name: 'Table', exact: true })).toHaveAttribute('aria-pressed', 'true')
  expect((await ask(page, 'view', { drawer: false })).status).toBe(200)
  await expect(drawer).toBeHidden()

  // From another view the page goes to the canvas first.
  await page.getByRole('button', { name: 'History', exact: true }).click()
  await expect(card(page, 'T-S0')).toHaveCount(0)
  expect((await ask(page, 'show')).body).toEqual({ shown: 'the whole canvas' })
  await expect(card(page, 'T-S0')).toBeVisible()
})

test('Claude opens and closes groups and a sub-investigation on the open page', async ({ page }) => {
  const folder = join(E2E_DIR, `page-commands-${Date.now()}`)
  await page.request.post('/api/investigations', { data: { folder, name: 'Page' } })
  const ids: string[] = []
  for (const [i, label] of ['a1', 'a2', 'b1', 'b2', 'c1'].entries()) {
    const made = await page.request.post('/api/nodes', { data: { label, pos_x: i * 300, pos_y: 0 } })
    ids.push(((await made.json()) as { id: string }).id)
  }
  const grouped = async (members: string[], label: string) => {
    const made = await page.request.post('/api/groups/reconnect', { data: { member_ids: members, label } })
    return ((await made.json()) as { id: string }).id
  }
  const ga = await grouped(ids.slice(0, 2), 'GA')
  await grouped(ids.slice(2, 4), 'GB')
  const sub = (await (
    await page.request.post('/api/sub-investigations', { data: { label: 'Side path', ids: [ids[4]] } })
  ).json()) as { id: string }
  await page.goto('/')
  await expect(page.getByRole('group', { name: 'Group GA' })).toBeVisible()
  await expect(card(page, 'a1')).toHaveCount(0)

  const opened = await ask(page, 'groups', { open: true })
  expect(opened.body, opened.text).toEqual({ expanded: ['GA', 'GB'] })
  for (const label of ['a1', 'a2', 'b1', 'b2']) await expect(card(page, label)).toBeVisible()
  expect((await ask(page, 'groups', { open: false, ids: [ga] })).body).toEqual({ collapsed: ['GA'] })
  await expect(card(page, 'a1')).toHaveCount(0)
  await expect(card(page, 'b1')).toBeVisible()
  const unknown = await ask(page, 'groups', { open: true, ids: ['nothing'] })
  expect(unknown.status).toBe(409)
  expect(unknown.text).toContain('No group or sub-investigation has the id nothing')

  // A sub-investigation's canvas, and back.
  const crumbs = page.getByRole('navigation', { name: 'Canvas' })
  expect((await ask(page, 'canvas', { sub_id: sub.id })).body).toEqual({ canvas: 'Side path' })
  await expect(crumbs.getByRole('button', { name: 'Side path' })).toHaveAttribute('aria-current', 'location')
  await expect(card(page, 'c1')).toBeVisible()
  await expect(page.getByRole('group', { name: 'Group GB' })).toHaveCount(0)
  expect((await ask(page, 'canvas')).body).toEqual({ canvas: 'the main canvas' })
  await expect(page.getByRole('group', { name: 'Group GB' })).toBeVisible()
  expect((await ask(page, 'canvas', { sub_id: 'nothing' })).text).toContain('No sub-investigation has this id')
})

test('Claude has the page draw the canvas and a profile, saved in the exports folder', async ({ page }) => {
  // T-UI-95 (D226): the images are the page's own (its Export menus' code), handed to the app,
  // which saves them as new files in the investigation; nothing is downloaded in the browser.
  const demo = await openDemo(page)
  await expect(card(page, 'T-S0')).toBeVisible()
  let downloads = 0
  page.on('download', () => (downloads += 1))
  const saved = (file: string) => readFileSync(join(demo, file))

  const svg = await ask(page, 'canvas-image', { what: 'full', format: 'svg' })
  expect(svg.body, svg.text).toEqual({ file: 'exports/canvas-full.svg' })
  const whole = saved('exports/canvas-full.svg').toString('utf8')
  expect(whole.startsWith('<svg')).toBe(true)
  expect(whole).toContain('A-S2')
  const png = await ask(page, 'canvas-image', { what: 'viewport', format: 'png' })
  expect(png.body, png.text).toEqual({ file: 'exports/canvas-viewport.png' })
  expect(saved('exports/canvas-viewport.png').subarray(1, 4).toString('latin1')).toBe('PNG')
  // The same again is a second file, never a replacement.
  expect((await ask(page, 'canvas-image', { what: 'full', format: 'svg' })).body).toEqual({
    file: 'exports/canvas-full 2.svg',
  })

  // A saved profile, drawn by the drawer without it being open.
  const ids = await places(page)
  const path = ['T-S0', 'A-S1', 'A-S2', 'A-S3'].map((label) => ids[label].id)
  const made = async (name: string, paths: unknown[]) =>
    ((await (await page.request.post('/api/energy-profiles', { data: { name, paths } })).json()) as { id: string }).id
  const main = await made('Main', [{ ids: path, branch_id: null }])
  const empty = await made('Empty', [])
  const figure = await ask(page, 'profile-image', { profile_id: main, format: 'svg' })
  expect(figure.body, figure.text).toEqual({ file: 'exports/energy-profile Main.svg' })
  const drawn = saved('exports/energy-profile Main.svg').toString('utf8')
  expect(drawn).toContain('<svg')
  expect(drawn).toContain('A-S2')
  await expect(page.getByLabel('Energy drawer')).toBeHidden()
  // With no profile named: the one the drawer has, here as PNG.
  const again = await ask(page, 'profile-image', { format: 'png' })
  expect(again.body, again.text).toEqual({ file: 'exports/energy-profile Main.png' })
  expect(saved('exports/energy-profile Main.png').subarray(1, 4).toString('latin1')).toBe('PNG')
  const nothing = await ask(page, 'profile-image', { profile_id: empty })
  expect(nothing.status).toBe(409)
  expect(nothing.text).toContain('The open profile shows no pathway')
  expect((await ask(page, 'profile-image', { profile_id: 'nope' })).text).toContain('No saved energy profile has this id')

  // The read-only copy needs no page: the app writes it.
  const copy = await page.request.post('/api/snapshot/save', { data: {}, headers: CLAUDE })
  const { file } = (await copy.json()) as { file: string }
  expect(file).toMatch(/^exports\/.+ read-only \d{4}-\d{2}-\d{2}\.html$/)
  expect(saved(file).toString('utf8')).toContain('<!doctype html>')
  expect(downloads).toBe(0)
})
