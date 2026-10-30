import { join } from 'node:path'
import { expect, test, type Locator, type Page } from '@playwright/test'
import { forgetView } from './demoView.ts'

// T-UI-61, D184 (UI7): right-click menus on the canvas's nodes, groups, arrows, the empty canvas
// and a multi-selection, worked by pointer and keyboard; the Ctrl K command palette over nodes,
// branches, steps and commands; the canvas's single-key shortcuts. Every entry runs what the
// panels and bars already do.

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
  await expect(card(page, 'T-S0')).toBeVisible()
}

const card = (page: Page, label: string) => page.getByRole('group', { name: `Node ${label}`, exact: true })
const menu = (page: Page, name: string) => page.getByRole('menu', { name })
const entries = (list: Locator) => list.locator(':scope > [role^="menuitem"], :scope > .menu-entry > [role^="menuitem"]')

/** A spot of the empty canvas, scanning in from the top left. */
async function emptySpot(page: Page): Promise<{ x: number; y: number }> {
  const pane = page.locator('.react-flow__pane')
  return (await pane.evaluate((element) => {
    const { left, top, width, height } = element.getBoundingClientRect()
    for (let y = 40; y < height - 40; y += 40)
      for (let x = 40; x < width - 200; x += 40)
        if (document.elementFromPoint(left + x, top + y) === element) return { x: left + x, y: top + y }
    return null
  }))!
}

/** Right-clicks an arrow halfway along its line. */
async function rightClickEdge(page: Page, id: string) {
  await page.locator('.react-flow__controls-fitview').click()
  const path = page.locator(`.react-flow__edge[data-id="${id}"] .react-flow__edge-interaction`)
  const point = await path.evaluate((element) => {
    const line = element as SVGPathElement
    const middle = line.getPointAtLength(line.getTotalLength() / 2)
    const screen = middle.matrixTransform(line.getScreenCTM()!)
    return { x: screen.x, y: screen.y }
  })
  await page.mouse.click(point.x, point.y, { button: 'right' })
}

type Canvas = {
  nodes: { id: string; label: string; status: string }[]
  transitions: { id: string; source_id: string; target_id: string }[]
}

test('right-click menus on a node, an arrow, the canvas and a selection', async ({ page }) => {
  await openDemo(page)
  const canvas: Canvas = await (await page.request.get('/api/canvas')).json()
  const id = (label: string) => canvas.nodes.find((n) => n.label === label)!.id
  await page.getByRole('button', { name: 'Energy', exact: true }).click()
  await card(page, 'T-S0').click()
  const inspector = page.getByLabel('Node inspector')
  await inspector.getByRole('button', { name: 'Use as energy reference' }).click()

  try {
    // A node's menu: what it is, its actions with their keys; the right-click selects it.
    await card(page, 'A-S2').click({ button: 'right' })
    const nodeMenu = menu(page, 'Node actions')
    await expect(nodeMenu).toBeVisible()
    await expect(nodeMenu.locator('.context-heading')).toHaveText('A-S2 · TS · [2+2] TS')
    await expect(entries(nodeMenu)).toHaveText([
      'Open 3D viewSpace',
      'Set as energy referenceR',
      'Import output onto node…I',
      'Edit structure in 3D…',
      'IRC…',
      'Start energy pathway here',
      'Add to group',
      'StatusPlanned',
      'Add pinned note',
      'Move to…',
      'Move into new sub-investigation…',
      'Copy xyzCtrl C',
      'Delete node…Del',
    ])
    await expect(inspector.getByRole('heading', { name: 'A-S2' })).toBeVisible()

    // The keyboard: the first entry has the focus, ↓ moves, Enter runs it.
    await expect(nodeMenu.getByRole('menuitem', { name: 'Open 3D view' })).toBeFocused()
    await page.keyboard.press('ArrowDown')
    await expect(nodeMenu.getByRole('menuitem', { name: 'Set as energy reference' })).toBeFocused()
    await page.keyboard.press('Enter')
    await expect(nodeMenu).toBeHidden()
    await expect(inspector.getByRole('button', { name: 'Energy reference' })).toBeDisabled()
    await expect(card(page, 'A-S2').locator('.cnode-energy')).toHaveText('ΔG 0.00')

    // Status ›: → opens its list on the status in force, ← goes back, a choice sets it.
    await card(page, 'A-S2').click({ button: 'right' })
    await expect(nodeMenu.getByRole('menuitem', { name: 'Energy reference' })).toBeDisabled()
    await nodeMenu.getByRole('menuitem', { name: 'Status' }).focus()
    await page.keyboard.press('ArrowRight')
    const statuses = menu(page, 'Status')
    await expect(statuses.getByRole('menuitemradio', { name: 'Planned' })).toBeFocused()
    await expect(statuses.getByRole('menuitemradio', { name: 'Planned' })).toHaveAttribute('aria-checked', 'true')
    await page.keyboard.press('ArrowLeft')
    await expect(statuses).toBeHidden()
    await expect(nodeMenu.getByRole('menuitem', { name: 'Status' })).toBeFocused()
    await page.keyboard.press('Enter')
    await page.keyboard.press('ArrowDown')
    await page.keyboard.press('Enter')
    await expect(card(page, 'A-S2').locator('.status')).toHaveText('Running externally')

    // Escape closes the menu; so do a click elsewhere and a scroll of the canvas.
    await card(page, 'A-S2').click({ button: 'right' })
    await page.keyboard.press('Escape')
    await expect(nodeMenu).toBeHidden()
    await card(page, 'A-S2').click({ button: 'right' })
    const spot = await emptySpot(page)
    await page.mouse.click(spot.x, spot.y)
    await expect(nodeMenu).toBeHidden()
    await card(page, 'A-S2').click({ button: 'right' })
    await page.mouse.move(spot.x, spot.y)
    await page.mouse.wheel(0, 120)
    await expect(nodeMenu).toBeHidden()

    // Entries run the panel's own actions: its delete asks first, its 3D view pops out.
    await card(page, 'A-S2').click({ button: 'right' })
    await nodeMenu.getByRole('menuitem', { name: 'Delete node…' }).click()
    const confirm = page.getByRole('dialog', { name: 'Delete node?' })
    await expect(confirm).toContainText('the node “A-S2”')
    await confirm.getByRole('button', { name: 'Cancel' }).click()
    await card(page, 'A-S2').click({ button: 'right' })
    await nodeMenu.getByRole('menuitem', { name: 'Open 3D view' }).click()
    const floating = page.getByRole('dialog', { name: '3D view window' })
    await expect(floating).toContainText('3D view: A-S2')
    await floating.getByRole('button', { name: 'Put back' }).click()

    // An arrow's menu: its ends, Status ›, Free species… (the panel's section) and Delete.
    const edge = canvas.transitions.find((t) => t.source_id === id('B-S1') && t.target_id === id('B-S2'))!
    await rightClickEdge(page, edge.id)
    const edgeMenu = menu(page, 'Transition actions')
    await expect(edgeMenu.locator('.context-heading')).toHaveText('B-S1 → B-S2 · transition')
    await expect(entries(edgeMenu)).toHaveText([
      'Go to start: B-S1',
      'Go to end: B-S2',
      'StatusPlanned',
      'Free species…',
      'Delete transition…Del',
    ])
    await edgeMenu.getByRole('menuitem', { name: 'Free species…' }).click()
    // The demo has no free species yet, so the section itself takes the focus.
    await expect(page.getByLabel('Transition inspector').getByLabel('Free species on this transition')).toBeFocused()
    await rightClickEdge(page, edge.id)
    await edgeMenu.getByRole('menuitem', { name: 'Delete transition…' }).click()
    await page.getByRole('dialog', { name: 'Delete transition?' }).getByRole('button', { name: 'Cancel' }).click()

    // The empty canvas's menu; Import file here… opens the import dialog.
    const empty = await emptySpot(page)
    await page.mouse.click(empty.x, empty.y, { button: 'right' })
    const paneMenu = menu(page, 'Canvas actions')
    await expect(entries(paneMenu)).toHaveText([
      'Add node here',
      'Import file here…',
      'Fit viewF',
      'Tidy canvas',
      'Search and commands…Ctrl K',
      'Keyboard shortcuts?',
    ])
    await paneMenu.getByRole('menuitem', { name: 'Import file here…' }).click()
    await expect(page.getByRole('dialog', { name: /Import/ })).toBeVisible()
    await page.keyboard.press('Escape')
    await expect(page.getByRole('dialog')).toHaveCount(0)

    // A multi-selection's menu offers the selection panel's actions, as the panel has them.
    await card(page, 'A-S2').click()
    await card(page, 'B-S2').click({ modifiers: ['Control'] })
    const selection = page.getByLabel('Selection inspector')
    const buttons = await selection.locator('.buttons > button').allTextContents()
    await card(page, 'B-S2').click({ button: 'right' })
    const selectionMenu = menu(page, 'Selection actions')
    await expect(selectionMenu.locator('.context-heading')).toHaveText('2 nodes selected')
    await expect(entries(selectionMenu)).toHaveText([...buttons, 'Move to…', 'Move into new sub-investigation…', 'Fit to selectionF', 'Tidy selection'])
    await expect(selectionMenu.getByRole('menuitem', { name: 'Scan path…' })).toBeDisabled()
    await selectionMenu.getByRole('menuitem', { name: 'Overlay in 3D' }).click()
    await expect(page.getByRole('dialog', { name: 'Overlay in 3D' })).toBeVisible()
    await page.keyboard.press('Escape')
    await expect(selection).toBeVisible()
  } finally {
    await page.request.patch(`/api/nodes/${id('A-S2')}`, { data: { status: 'planned' } })
  }
})

test('the command palette and the canvas keys', async ({ page }) => {
  await openDemo(page)
  await page.getByRole('button', { name: 'Energy', exact: true }).click()
  await card(page, 'T-S0').click()
  await page.getByLabel('Node inspector').getByRole('button', { name: 'Use as energy reference' }).click()

  // The app bar shows the way in and its key.
  const search = page.getByRole('button', { name: 'Search and commands' })
  await expect(search).toContainText('Ctrl K')
  await search.click()
  const palette = page.getByRole('dialog', { name: 'Command palette' })
  await expect(palette.getByRole('combobox')).toBeFocused()
  await page.keyboard.press('Escape')
  await expect(palette).toBeHidden()

  // Ctrl K, "ts": both TS nodes first, with their step, branch and ΔG; Enter selects the first.
  await page.keyboard.press('Control+k')
  await page.keyboard.type('ts')
  const nodes = palette.getByRole('group', { name: 'Nodes' }).getByRole('option')
  await expect(nodes.nth(0)).toContainText('A-S2')
  await expect(nodes.nth(0)).toContainText('[2+2] TS · branch A · ΔG 14.80')
  await expect(nodes.nth(1)).toContainText('B-S2')
  await expect(palette.getByRole('group').first()).toHaveAttribute('aria-label', 'Nodes')
  await expect(nodes.nth(0)).toHaveAttribute('aria-selected', 'true')
  await page.keyboard.press('ArrowDown')
  await expect(nodes.nth(1)).toHaveAttribute('aria-selected', 'true')
  await page.keyboard.press('ArrowUp')
  await page.keyboard.press('Enter')
  await expect(palette).toBeHidden()
  await expect(page.getByLabel('Node inspector').getByRole('heading', { name: 'A-S2' })).toBeVisible()

  // A command: a settings section, opened where it is.
  await page.keyboard.press('Control+k')
  await page.keyboard.type('thermo')
  await expect(palette.getByRole('option').first()).toContainText('Settings: Thermochemistry')
  await page.keyboard.press('Enter')
  const settings = page.getByRole('dialog', { name: 'Settings' })
  await expect(
    settings.getByRole('navigation', { name: 'Settings sections' }).getByRole('button', { name: 'Thermochemistry' }),
  ).toHaveAttribute('aria-current', 'true')
  await page.keyboard.press('Escape')

  // Ctrl K works from a field too, never under a dialog.
  await page.getByLabel('Search nodes', { exact: true }).click()
  await page.keyboard.press('Control+k')
  await expect(palette).toBeVisible()
  await page.keyboard.type('B-S3')
  await page.keyboard.press('Enter')
  await expect(page.getByLabel('Node inspector').getByRole('heading', { name: 'B-S3' })).toBeVisible()

  // Single keys while the canvas has the focus: the node view, the drawer, the shortcut list.
  await card(page, 'A-S2').click()
  const view = page.getByRole('group', { name: 'Node view' })
  await page.keyboard.press('c')
  await expect(view.getByRole('button', { name: 'Compact' })).toHaveAttribute('aria-pressed', 'true')
  await page.keyboard.press('e')
  await expect(view.getByRole('button', { name: 'Energy' })).toHaveAttribute('aria-pressed', 'true')
  const drawer = page.getByRole('button', { name: 'Profile and table' })
  await page.keyboard.press('p')
  await expect(drawer).toHaveAttribute('aria-pressed', 'true')
  await page.keyboard.press('p')
  await expect(drawer).toHaveAttribute('aria-pressed', 'false')
  await page.keyboard.press('r')
  await expect(page.getByLabel('Node inspector').getByRole('button', { name: 'Energy reference' })).toBeDisabled()
  await page.keyboard.press('Shift+?')
  const list = page.getByRole('dialog', { name: 'Keyboard shortcuts' })
  await expect(list).toContainText('Search nodes, branches, steps and commands')
  await page.keyboard.press('Escape')
  await expect(list).toBeHidden()
  await card(page, 'A-S2').click()
  await page.keyboard.press('Delete')
  await page.getByRole('dialog', { name: 'Delete node?' }).getByRole('button', { name: 'Cancel' }).click()

  // Keys typed into a field stay there.
  const field = page.getByLabel('Search nodes', { exact: true })
  await field.fill('')
  await field.pressSequentially('sc')
  await expect(field).toHaveValue('sc')
  await expect(view.getByRole('button', { name: 'Energy' })).toHaveAttribute('aria-pressed', 'true')
  await field.fill('')

  // A terminal keeps Ctrl K (the shell's "delete to the end of the line").
  await page.getByRole('button', { name: 'Claude', exact: true }).click()
  const panel = page.getByRole('complementary', { name: 'Claude' })
  await panel.getByRole('button', { name: 'New conversation' }).click()
  const terminal = panel.getByTestId('claude-terminal')
  await expect(terminal).toContainText('Fake Claude ready')
  await terminal.click()
  await page.keyboard.press('Control+k')
  await expect(palette).toBeHidden()
  await page.keyboard.type('exit')
  await page.keyboard.press('Enter')
  await panel.getByRole('button', { name: 'Hide' }).click()
})

test.describe('in an investigation of its own', () => {
  test.afterEach(async ({ request }) => {
    await request.post('/api/investigations/close')
  })

  test('a group’s menu, Add node here, Add to group, Copy xyz and Delete', async ({ page, context }) => {
    const folder = join(E2E_DIR, `commands-${Date.now()}`)
    await page.request.post('/api/investigations', { data: { folder, name: 'Commands' } })
    const xyz = '3\nwater\nO 0 0 0\nH 0.96 0 0\nH -0.24 0.93 0\n'
    const make = async (label: string, x: number) =>
      (await (await page.request.post('/api/nodes', { data: { label, xyz, pos_x: x, pos_y: 0 } })).json()) as {
        id: string
        xyz: string
      }
    const a = await make('C1', 0)
    const b = await make('C2', 0)
    const c = await make('C3', 1000)
    await page.request.post('/api/groups/reconnect', { data: { member_ids: [a.id, b.id], label: 'Pair', outgoing: null } })
    await page.goto('/')
    const group = page.getByRole('group', { name: 'Group Pair', exact: true })
    await expect(group).toBeVisible()

    // The group's menu, from its head.
    await group.locator('.cgroup-head').click({ button: 'right' })
    const groupMenu = menu(page, 'Group actions')
    await expect(groupMenu.locator('.context-heading')).toHaveText('Pair · group · 2 members')
    await expect(entries(groupMenu)).toHaveText([
      'Expand group',
      'Set as energy referenceR',
      'Start energy pathway here',
      'Order members…',
      'Overlay members',
      'Compare sterics',
      'Move to…',
      'Move into new sub-investigation…',
      'Remove group…Del',
    ])
    await groupMenu.getByRole('menuitem', { name: 'Expand group' }).click()
    await expect(card(page, 'C1')).toBeVisible()
    await group.locator('.cgroup-head').click({ button: 'right' })
    await groupMenu.getByRole('menuitem', { name: 'Remove group…' }).click()
    await page.getByRole('dialog', { name: 'Remove group?' }).getByRole('button', { name: 'Cancel' }).click()

    // Add to group ›: the selection panel's Add to group, with the group and the node.
    await card(page, 'C3').click({ button: 'right' })
    await menu(page, 'Node actions').getByRole('menuitem', { name: 'Add to group' }).click()
    await menu(page, 'Add to group').getByRole('menuitem', { name: 'Pair' }).click()
    const adding = page.getByRole('dialog', { name: 'Add to group' })
    await adding.getByRole('button', { name: 'Add 1 node' }).click()
    await expect(group).toContainText('3 members')

    // Copy xyz puts the stored coordinates on the clipboard.
    await context.grantPermissions(['clipboard-read', 'clipboard-write'])
    await card(page, 'C3').click({ button: 'right' })
    await menu(page, 'Node actions').getByRole('menuitem', { name: 'Copy xyz' }).click()
    // Windows' clipboard turns line ends into \r\n.
    await expect
      .poll(async () => (await page.evaluate(() => navigator.clipboard.readText())).replace(/\r\n/g, '\n'))
      .toBe(c.xyz)

    // Add node here puts a node under the pointer; Delete node… asks, then deletes it.
    const spot = await emptySpot(page)
    await page.mouse.click(spot.x, spot.y + 200, { button: 'right' })
    await menu(page, 'Canvas actions').getByRole('menuitem', { name: 'Add node here' }).click()
    const added = card(page, 'Untitled node')
    await expect(added).toBeVisible()
    const box = (await added.boundingBox())!
    expect(Math.abs(box.x - spot.x)).toBeLessThan(5)
    await added.click({ button: 'right' })
    await menu(page, 'Node actions').getByRole('menuitem', { name: 'Delete node…' }).click()
    await page.getByRole('dialog', { name: 'Delete node?' }).getByRole('button', { name: 'Delete' }).click()
    await expect(added).toHaveCount(0)
  })
})
