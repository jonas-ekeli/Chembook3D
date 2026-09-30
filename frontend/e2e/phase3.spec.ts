import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { expect, test, type Locator, type Page } from '@playwright/test'

const E2E_DIR = process.env.E2E_DIR!
// Synthetic Gaussian files with a custom basis set, written by global-setup.ts
const CUSTOM = join(E2E_DIR, 'gaussian')

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
  // The energies arrive last; until they have, the canvas may still redraw and drop a click.
  await expect(page.locator('.edge-label').filter({ hasText: 'ΔG' }).first()).toBeVisible()
}

async function newInvestigation(page: Page, name: string) {
  await page.goto('/')
  await page.getByRole('button', { name: /^New/ }).first().click()
  const dialog = page.getByRole('dialog')
  await expect(dialog.getByRole('list', { name: 'Folders' })).toBeVisible()
  await dialog.getByLabel('Folder path').fill(E2E_DIR)
  await dialog.getByRole('button', { name: 'Go' }).click()
  await expect(dialog.getByLabel('Folder path')).toHaveValue(E2E_DIR)
  await dialog.getByLabel('Investigation name').fill(name)
  await dialog.getByRole('button', { name: 'Create' }).click()
  await expect(page.locator('.investigation')).toHaveText(name)
}

function canvasNode(page: Page, label: string): Locator {
  return page.getByRole('group', { name: `Node ${label}`, exact: true })
}

async function canvasRecords(page: Page) {
  const response = await page.request.get('/api/canvas')
  return response.json()
}

test('build steps, branches and transitions on the canvas', async ({ page }) => {
  // WF-06, FR-STEP-01, FR-BR-01/02/04, FR-EDGE-01/03, FR-CAN-01
  await newInvestigation(page, 'Canvas test')
  const steps = page.getByRole('region', { name: 'Reaction steps' })
  for (const name of ['S0 alkylidene', 'S1 π-complex']) {
    await steps.getByLabel('New step name').fill(name)
    await steps.getByRole('button', { name: 'Add' }).click()
  }
  await expect(steps.getByLabel('Name of step 2')).toHaveValue('S1 π-complex')

  await page.getByRole('button', { name: '+ Branch' }).click()
  const branchInspector = page.getByLabel('Branch inspector')
  await branchInspector.getByLabel('Name').fill('T')
  await branchInspector.getByLabel('Name').press('Enter')
  await expect(page.getByRole('list', { name: 'Branch list' })).toContainText('T')

  // WF-02: double-click the canvas to add a node there.
  await page.locator('.react-flow__pane').dblclick({ position: { x: 200, y: 200 } })
  const inspector = page.getByLabel('Node inspector')
  await expect(inspector.getByRole('heading', { name: 'Untitled node' })).toBeVisible()
  await inspector.getByLabel('Label').fill('T-S0')
  await inspector.getByLabel('Label').press('Enter')
  await inspector.getByLabel('Step').selectOption({ label: 'S0 alkylidene' })
  await inspector.getByLabel('Branch', { exact: true }).selectOption({ label: 'T' })
  await expect(canvasNode(page, 'T-S0')).toContainText('S0 alkylidene')

  await page.locator('.react-flow__pane').dblclick({ position: { x: 480, y: 200 } })
  // Wait for the new node's inspector, or the label would rename T-S0 instead.
  await expect(inspector.getByRole('heading', { name: 'Untitled node' })).toBeVisible()
  await inspector.getByLabel('Label').fill('T-S1')
  await inspector.getByLabel('Label').press('Enter')
  await expect(canvasNode(page, 'T-S1')).toBeVisible()

  // Positions persist (FR-CAN-01).
  const before = (await canvasRecords(page)).nodes.find((n: { label: string }) => n.label === 'T-S1')
  // Several mouse moves, like a real drag: React Flow ignores the move that starts it.
  const box = (await canvasNode(page, 'T-S1').boundingBox())!
  await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2)
  await page.mouse.down()
  await page.mouse.move(box.x + box.width / 2 + 40, box.y + box.height / 2 + 130, { steps: 8 })
  await page.mouse.up()
  await expect
    .poll(async () => (await canvasRecords(page)).nodes.find((n: { label: string }) => n.label === 'T-S1').pos_y)
    .not.toBe(before.pos_y)

  // Drag from one node's handle to the other's: a transition. Neither end is a TS, so it is a
  // direct connection marked "no TS" (D53).
  await canvasNode(page, 'T-S0')
    .locator('.react-flow__handle-right')
    .dragTo(canvasNode(page, 'T-S1').locator('.react-flow__handle-left'))
  await expect(page.locator('.edge-label .no-ts')).toHaveCount(1)
  await canvasNode(page, 'T-S1').click()
  await inspector.getByLabel('Role').selectOption('transition_state')
  await expect(page.locator('.edge-label .no-ts')).toHaveCount(0)

  // FR-BR-02: split T at T-S0 into two child branches; FR-BR-04: the lineage shows it.
  await canvasNode(page, 'T-S0').click()
  await inspector.getByRole('button', { name: 'Split into branches…' }).click()
  await page.getByRole('dialog').getByRole('button', { name: 'Create 2 branches' }).click()
  const branchList = page.getByRole('list', { name: 'Branch list' })
  await expect(branchList.getByRole('button')).toHaveCount(3)
  await branchList.getByRole('button', { name: /T1/ }).click()
  await expect(branchInspector.getByLabel('Lineage')).toContainText('T1 → T')
  await expect(branchInspector.getByLabel('Lineage')).toContainText('Split from T-S0')
})

test('an arrow can leave from and arrive at any side of a node', async ({ page }) => {
  // D76, A29
  await newInvestigation(page, 'Sides test')
  const inspector = page.getByLabel('Node inspector')
  for (const [label, x, y] of [['Upper', 200, 200], ['Lower', 480, 200]] as const) {
    await page.locator('.react-flow__pane').dblclick({ position: { x, y } })
    await expect(inspector.getByRole('heading', { name: 'Untitled node' })).toBeVisible()
    await inspector.getByLabel('Label').fill(label)
    await inspector.getByLabel('Label').press('Enter')
    await expect(canvasNode(page, label)).toBeVisible()
  }
  const sides = async () => {
    const [t] = (await canvasRecords(page)).transitions
    return t ? `${t.source_side}>${t.target_side}` : null
  }

  // Drag from the bottom of one node to the top of the other.
  await canvasNode(page, 'Upper')
    .locator('.react-flow__handle-bottom')
    .dragTo(canvasNode(page, 'Lower').locator('.react-flow__handle-top'))
  await expect.poll(sides).toBe('bottom>top')
  await expect(page.locator('.react-flow__edge')).toHaveCount(1)

  // Drag the arrow's end to another side of the same node.
  const end = (await page.locator('.react-flow__edgeupdater-target').boundingBox())!
  const left = (await canvasNode(page, 'Lower').locator('.react-flow__handle-left').boundingBox())!
  await page.mouse.move(end.x + end.width / 2, end.y + end.height / 2)
  await page.mouse.down()
  await page.mouse.move(left.x + left.width / 2, left.y + left.height / 2, { steps: 10 })
  await page.mouse.up()
  await expect.poll(sides).toBe('bottom>left')

  // Or choose the sides in the transition's panel.
  await canvasNode(page, 'Lower').click()
  await inspector.getByRole('region', { name: 'Transitions' }).getByRole('button', { name: 'from Upper' }).click()
  const panel = page.getByLabel('Transition inspector')
  await expect(panel.getByLabel('Arrow arrives at')).toHaveValue('left')
  await panel.getByLabel('Arrow leaves from').selectOption('right')
  await expect.poll(sides).toBe('right>left')
})

test('reconnect branches as a group, pick a representative, see its structure, then dissolve it', async ({ page }) => {
  // WF-07, FR-GRP-01/02/05, T-BR-04, T-BR-10, T-BR-14 (dissolve)
  await openDemo(page)
  await canvasNode(page, 'A-S3').click()
  await expect(page.getByLabel('Node inspector').getByRole('heading', { name: 'A-S3' })).toBeVisible()
  await canvasNode(page, 'B-S3').click({ modifiers: ['Control'] })
  const selection = page.getByLabel('Selection inspector')
  await expect(selection.getByRole('heading')).toHaveText('2 nodes selected')
  await selection.getByRole('button', { name: 'Reconnect as group…' }).click()
  const dialog = page.getByRole('dialog', { name: 'Reconnect as group' })
  await expect(dialog).toContainText('Incoming branches: A, B')
  await dialog.getByLabel('Group label').fill('G3')
  await dialog.getByLabel('Outgoing branch name').fill('R')
  await dialog.getByRole('button', { name: 'Reconnect' }).click()

  const group = page.getByLabel('Group inspector')
  await expect(group.getByRole('heading', { name: 'G3' })).toBeVisible()
  await expect(canvasNode(page, 'A-S3')).toHaveCount(0) // inside the collapsed group
  const box = page.getByRole('group', { name: 'Group G3' })
  await expect(box).toContainText('2 members')
  await expect(box).toContainText('no representative')

  // T-BR-19, D68: in structure mode a collapsed group shows its representative's structure, and none
  // until one is picked.
  await page.getByRole('button', { name: 'Structure' }).click()
  await expect(box.getByRole('img', { name: 'Structure' })).toHaveCount(0)

  // FR-GRP-02: the user picks the representative; the app never does.
  await group.getByLabel('Representative: B-S3').check()
  await expect(box).toContainText('★ B-S3')
  await expect(box.getByRole('img', { name: 'Structure' })).toBeVisible()
  await page.getByRole('button', { name: 'Compact' }).click()
  await expect(box.getByRole('img', { name: 'Structure' })).toHaveCount(0)
  await box.getByRole('button', { name: 'Expand group' }).click()
  await expect(canvasNode(page, 'A-S3')).toBeVisible()
  await expect(page.getByRole('list', { name: 'Branch list' })).toContainText('R')

  // D54, P4: dissolve and put members back in the branches they came from.
  await canvasNode(page, 'T-S0').click()
  await page.getByRole('group', { name: 'Group G3' }).click({ position: { x: 20, y: 10 } })
  await group.getByRole('button', { name: 'Remove group…' }).click()
  const remove = page.getByRole('dialog', { name: 'Remove group?' })
  await expect(remove).toContainText('the 2 members stay as nodes')
  await remove.getByRole('button', { name: 'Dissolve group' }).click()
  await expect(page.getByRole('group', { name: 'Group G3' })).toHaveCount(0)
  await canvasNode(page, 'A-S3').click()
  await expect(page.getByLabel('Node inspector').getByLabel('Branch', { exact: true })).toHaveValue(
    (await canvasRecords(page)).branches.find((b: { name: string }) => b.name === 'A').id,
  )
})

test('add a node to a group and cycle its members through a column, a row and the grid', async ({ page }) => {
  // FR-GRP-06/07, T-BR-15, T-BR-16, A20, A21
  await openDemo(page)
  await canvasNode(page, 'A-S3').click()
  await expect(page.getByLabel('Node inspector').getByRole('heading', { name: 'A-S3' })).toBeVisible()
  await canvasNode(page, 'B-S3').click({ modifiers: ['Control'] })
  const selection = page.getByLabel('Selection inspector')
  await selection.getByRole('button', { name: 'Reconnect as group…' }).click()
  const reconnect = page.getByRole('dialog', { name: 'Reconnect as group' })
  await reconnect.getByLabel('Group label').fill('G4')
  await reconnect.getByLabel('Create an outgoing branch whose parents are the incoming branches').uncheck()
  await reconnect.getByRole('button', { name: 'Reconnect' }).click()
  const inspector = page.getByLabel('Group inspector')
  await expect(inspector.getByRole('heading', { name: 'G4' })).toBeVisible()
  try {
    // A20: the group and a node selected together offer "Add to group".
    const box = page.getByRole('group', { name: 'Group G4' })
    await canvasNode(page, 'A-S1').click({ modifiers: ['Control'] })
    await expect(selection.getByRole('heading')).toHaveText('1 node and 1 group selected')
    await expect(selection.getByRole('button', { name: 'Reconnect as group…' })).toBeDisabled()
    await selection.getByRole('button', { name: 'Add to “G4”…' }).click()
    const add = page.getByRole('dialog', { name: 'Add to group' })
    await expect(add).toContainText('A-S1 becomes a member of “G4”')
    await add.getByRole('button', { name: 'Add 1 node' }).click()
    await expect(inspector.getByRole('heading', { name: 'G4' })).toBeVisible()
    await expect(box).toContainText('3 members')
    await expect(canvasNode(page, 'A-S1')).toHaveCount(0) // inside the collapsed group

    // A21: a button in the expanded group cycles grid, column, row and back to the grid.
    await box.getByRole('button', { name: 'Expand group' }).click()
    const members = ['A-S1', 'A-S3', 'B-S3'].map((label) => canvasNode(page, label))
    for (const member of members) await expect(member).toBeVisible()
    const places = () => Promise.all(members.map(async (m) => (await m.boundingBox())!))
    await box.getByRole('button', { name: 'Stack members vertically' }).click()
    await expect(box.getByRole('button', { name: 'Line members up horizontally' })).toBeVisible()
    await expect(async () => {
      const [a, b, c] = await places()
      expect(Math.abs(a.x - b.x) + Math.abs(b.x - c.x)).toBeLessThan(2)
      expect(b.y).toBeGreaterThan(a.y + a.height - 2)
      expect(c.y).toBeGreaterThan(b.y + b.height - 2)
    }).toPass()
    await box.getByRole('button', { name: 'Line members up horizontally' }).click()
    await expect(box.getByRole('button', { name: 'Arrange members in a grid' })).toBeVisible()
    await expect(async () => {
      const [a, b, c] = await places()
      expect(Math.abs(a.y - b.y) + Math.abs(b.y - c.y)).toBeLessThan(2)
      expect(b.x).toBeGreaterThan(a.x + a.width - 2)
      expect(c.x).toBeGreaterThan(b.x + b.width - 2)
    }).toPass()
    // Stored with the group, so it is still there after reopening.
    await expect(async () => {
      const group = (await canvasRecords(page)).groups.find((g: { label: string }) => g.label === 'G4')
      expect(group.layout).toBe('horizontal')
    }).toPass()
    await box.getByRole('button', { name: 'Arrange members in a grid' }).click()
    await expect(box.getByRole('button', { name: 'Stack members vertically' })).toBeVisible()
    await expect(async () => {
      const [a, b, c] = await places() // three members: two columns, two rows
      expect(Math.abs(a.y - b.y)).toBeLessThan(2)
      expect(b.x).toBeGreaterThan(a.x + a.width - 2)
      expect(Math.abs(a.x - c.x)).toBeLessThan(2)
      expect(c.y).toBeGreaterThan(a.y + a.height - 2)
    }).toPass()
    await expect(async () => {
      const group = (await canvasRecords(page)).groups.find((g: { label: string }) => g.label === 'G4')
      expect(group.layout).toBe('grid')
    }).toPass()

    // Dissolve, with members back in their branches.
    await canvasNode(page, 'T-S0').click()
    await expect(page.getByLabel('Node inspector').getByRole('heading', { name: 'T-S0' })).toBeVisible()
    await box.click({ position: { x: 20, y: 10 } })
    // An expectation (unlike a click) gives up before the test's time runs out, so the clean-up
    // below still has a page to work with.
    await expect(inspector.getByRole('button', { name: 'Remove group…' })).toBeVisible()
    await inspector.getByRole('button', { name: 'Remove group…' }).click()
    const remove = page.getByRole('dialog', { name: 'Remove group?' })
    await remove.getByRole('button', { name: 'Dissolve group' }).click()
    await expect(box).toHaveCount(0)
  } finally {
    // Leave the demo as it was for the tests after this one, even if a step above failed.
    const left = (await canvasRecords(page)).groups.find((g: { label: string }) => g.label === 'G4')
    if (left) await page.request.post(`/api/groups/${left.id}/dissolve`, { data: { restore_branches: true } })
  }
})

test('groups holding one conformer per branch read as one path, and a branch filter unpacks them', async ({ page }) => {
  // T-BR-17, D66, A23-A25
  await newInvestigation(page, 'Conformer groups')
  const post = async (path: string, data: object) => (await page.request.post(`/api${path}`, { data })).json()
  const branches = [await post('/branches', { name: 'c1' }), await post('/branches', { name: 'c2' })]
  const ids: Record<string, string> = {}
  const species = ['IM1', 'TS1', 'IM2']
  for (const [column, name] of species.entries()) {
    const role = name.startsWith('TS') ? 'transition_state' : 'minimum'
    for (const [i, branch] of branches.entries()) {
      const label = `${name}-${i + 1}`
      ids[label] = (await post('/nodes', { label, role, branch_id: branch.id, pos_x: column * 320, pos_y: i * 120 })).id
    }
    const group = await post('/groups/reconnect', { member_ids: [ids[`${name}-1`], ids[`${name}-2`]], label: name })
    await page.request.patch(`/api/groups/${group.id}`, { data: { pos_x: column * 320, pos_y: 0 } })
  }
  for (const n of [1, 2]) {
    await post('/transitions', { source_id: ids[`IM1-${n}`], target_id: ids[`TS1-${n}`] })
    await post('/transitions', { source_id: ids[`TS1-${n}`], target_id: ids[`IM2-${n}`] })
  }
  await page.reload()

  // Collapsed, the groups read as one path: one line between each pair, standing for two edges.
  for (const name of species) await expect(page.getByRole('group', { name: `Group ${name}` })).toBeVisible()
  await expect(page.getByRole('group', { name: 'Group TS1' }).locator('.ts-mark')).toBeVisible()
  await expect(page.locator('.react-flow__edge')).toHaveCount(2)
  await expect(page.locator('.edge-count')).toHaveText(['2 edges', '2 edges'])

  // Filtered to branch c1, each group is drawn as its c1 conformer, joined by its own edges.
  await page.getByRole('button', { name: /^Filters/ }).click()
  const filters = page.getByRole('dialog', { name: 'Filters' })
  await filters.getByRole('group', { name: 'Branch' }).getByLabel('c2', { exact: true }).uncheck()
  for (const name of species) {
    await expect(page.getByRole('group', { name: `Group ${name}` })).toHaveCount(0)
    await expect(canvasNode(page, `${name}-1`)).toBeVisible()
    await expect(canvasNode(page, `${name}-2`)).toHaveCount(0)
  }
  await expect(page.locator('.react-flow__edge')).toHaveCount(2)
  await expect(page.locator('.edge-count')).toHaveCount(0)
  await filters.getByRole('button', { name: 'Show all' }).click()
  await expect(page.getByRole('group', { name: 'Group IM1' })).toBeVisible()
})

test('view modes and filters never change stored data', async ({ page }) => {
  // T-UI-02, FR-CAN-04 (compact and structure), FR-CAN-05
  await openDemo(page)
  const before = await canvasRecords(page)

  await page.getByRole('button', { name: 'Structure' }).click()
  await expect(canvasNode(page, 'A-S1').getByRole('img', { name: 'Structure' })).toBeVisible()
  await page.getByRole('button', { name: 'Compact' }).click()
  await expect(canvasNode(page, 'A-S1').getByRole('img', { name: 'Structure' })).toHaveCount(0)

  await page.getByRole('button', { name: /^Filters/ }).click()
  const filters = page.getByRole('dialog', { name: 'Filters' })
  await filters.getByRole('group', { name: 'Branch' }).getByLabel('A', { exact: true }).uncheck()
  await expect(canvasNode(page, 'A-S1')).toHaveCount(0)
  await expect(canvasNode(page, 'B-S1')).toBeVisible()
  await filters.getByRole('group', { name: 'Status' }).getByLabel('Planned').uncheck()
  await expect(canvasNode(page, 'B-S2')).toHaveCount(0)
  await expect(canvasNode(page, 'B-S1')).toBeVisible()
  await expect(page.getByRole('button', { name: 'Filters (2 hidden) ▾' })).toBeVisible()

  expect(await canvasRecords(page)).toEqual(before)
  await filters.getByRole('button', { name: 'Show all' }).click()
  await expect(canvasNode(page, 'A-S1')).toBeVisible()
})

test('reopening the investigation already shown keeps what was selected meanwhile', async ({ page }) => {
  // The open dialog closes before the backend answers; that answer used to clear the selection
  // (and made "arrange a branch" below lose its branch on slow runners).
  await openDemo(page)
  await page.route('**/api/investigations/open', async (route) => {
    await new Promise((r) => setTimeout(r, 1000))
    await route.continue()
  })
  const answered = page.waitForResponse('**/api/investigations/open')
  await openDemo(page)
  await page.getByRole('list', { name: 'Branch list' }).getByRole('button', { name: /^A/ }).click()
  await answered
  await page.waitForTimeout(500) // let the app handle the answer
  await expect(page.getByLabel('Branch inspector')).toBeVisible({ timeout: 1000 })
})

test('a late canvas answer does not drop the node just added', async ({ page }) => {
  // Windows CI: the answer to an earlier canvas fetch arrived after a later one, so the canvas
  // lost the node just added and its inspector disappeared.
  await newInvestigation(page, 'Late answer')
  let delivered: () => void = () => undefined
  const late = new Promise<void>((resolve) => (delivered = resolve))
  let delayed = false
  await page.route('**/api/canvas', async (route) => {
    if (delayed) return route.continue()
    delayed = true
    const response = await route.fetch() // answered now (one node), delivered after the next
    await new Promise((r) => setTimeout(r, 1500))
    await route.fulfill({ response })
    delivered()
  })
  await page.getByRole('button', { name: '+ Add node' }).click()
  await page.getByRole('button', { name: '+ Add node' }).click()
  await late
  await page.waitForTimeout(500) // let the app handle the late answer
  await expect(page.getByLabel('Node inspector')).toBeVisible({ timeout: 1000 })
  await expect(page.getByRole('list', { name: 'Nodes' }).getByRole('listitem')).toHaveCount(2)
})

test('arrange a branch and inspect a transition', async ({ page }) => {
  // FR-CAN-06, FR-EDGE-03, T-BR-12 (the delete confirmation lists the edges)
  await openDemo(page)
  await page.getByRole('list', { name: 'Branch list' }).getByRole('button', { name: /^A/ }).click()
  await page.getByLabel('Branch inspector').getByRole('button', { name: 'Arrange branch' }).click()
  await expect
    .poll(async () => {
      const nodes = (await canvasRecords(page)).nodes.filter((n: { label: string }) => /^A-S/.test(n.label))
      return new Set(nodes.map((n: { pos_y: number }) => n.pos_y)).size
    })
    .toBe(1)

  await page.locator('.edge-label .no-ts').first().waitFor()
  // Wait until the canvas has drawn the new positions too, or the click can land on a moving node
  // and count as a drag.
  await expect
    .poll(async () => {
      const boxes = await Promise.all(['A-S1', 'A-S2', 'A-S3'].map((l) => canvasNode(page, l).boundingBox()))
      return new Set(boxes.map((b) => (b ? Math.round(b.y) : null))).size
    })
    .toBe(1)
  await canvasNode(page, 'A-S2').click()
  const inspector = page.getByLabel('Node inspector')
  const transitions = inspector.getByRole('region', { name: 'Transitions' })
  await expect(transitions.getByRole('listitem')).toHaveCount(2)
  await transitions.getByRole('button', { name: 'from A-S1' }).click()
  await expect(page.getByLabel('Transition inspector')).toContainText('A-S1 → A-S2')
  await expect(page.getByLabel('Transition inspector')).toContainText('Through a transition-state node')

  await canvasNode(page, 'A-S2').click()
  await inspector.getByRole('button', { name: 'Delete node' }).click()
  const confirm = page.getByRole('dialog', { name: 'Delete node?' })
  await expect(confirm.getByRole('list', { name: 'Transitions deleted with the node' }).getByRole('listitem')).toHaveText([
    'A-S1 → A-S2',
    'A-S2 → A-S3',
  ])
  await confirm.getByRole('button', { name: 'Cancel' }).click()
})

test('3D: measure atoms, animate the imaginary mode, overlay two nodes', async ({ page }) => {
  // T-UI-03, FR-3D-02…04 (copy xyz is in phase1.spec.ts)
  // Three 3D views in software WebGL are slow on the Windows runners.
  test.slow()
  await newInvestigation(page, '3D test')
  await page.getByRole('button', { name: 'Import file…' }).click()
  const dialog = page.getByRole('dialog')
  await dialog.getByLabel('Upload a file').setInputFiles(join(CUSTOM, 'MeI_TS.out'))
  for (const [label, value] of [
    ['Name for the custom basis set', 'modDZ'],
    ['Name for the custom dispersion', 'GD3MBJ'],
  ]) {
    await dialog.getByLabel(label).fill(value)
    await dialog.getByLabel(label).press('Enter')
  }
  await dialog.getByRole('button', { name: 'Import', exact: true }).click()
  const inspector = page.getByLabel('Node inspector')
  await expect(inspector.getByRole('heading', { name: 'MeI_TS' })).toBeVisible()

  await inspector.getByLabel('Atom numbers').fill('1 2')
  await inspector.getByRole('button', { name: 'Measure', exact: true }).click()
  await expect(inspector.getByLabel('Measurement')).toContainText(/Distance \d+\.\d{3} Å/)
  await inspector.getByLabel('Atom numbers').fill('1 2 3')
  await inspector.getByLabel('Atom numbers').press('Enter')
  await expect(inspector.getByLabel('Measurement')).toContainText(/Angle \d+\.\d°/)

  const modes = inspector.getByLabel('Animate mode')
  await expect(modes.locator('option').nth(1)).toHaveText(/^Mode 1: \d+\.\di cm⁻¹$/)
  await modes.selectOption({ index: 1 })
  await expect(inspector.getByLabel('Measure')).toHaveCount(0) // measuring pauses while animating
  await expect(inspector.getByTestId('viewer3d').locator('canvas')).toBeVisible()
  await expect(inspector.getByRole('alert')).toHaveCount(0)
  await modes.selectOption({ label: 'No animation' })
  await expect(inspector.getByLabel('Atom numbers')).toBeVisible()

  // FR-3D-04: a copy of the node with coordinates edited by hand, overlaid on the original.
  const xyz = await inspector.getByLabel('xyz text').inputValue()
  await page.getByRole('button', { name: '+ Add node' }).click()
  // Wait for the new node's inspector, or the label would rename MeI_TS instead.
  await expect(inspector.getByRole('heading', { name: 'Untitled node' })).toBeVisible()
  await inspector.getByLabel('Label').fill('copy')
  await inspector.getByLabel('Label').press('Enter')
  await expect(inspector.getByRole('heading', { name: 'copy' })).toBeVisible()
  await inspector.getByLabel('xyz text').fill(xyz)
  await inspector.getByRole('button', { name: 'Save coordinates' }).click()
  await expect(inspector.getByText(/^Formula:/)).toContainText('CH3I')
  await canvasNode(page, 'MeI_TS').click({ modifiers: ['Control'] })
  await page.getByLabel('Selection inspector').getByRole('button', { name: 'Overlay in 3D' }).click()
  const overlay = page.getByRole('dialog', { name: 'Overlay in 3D' })
  await expect(overlay.getByLabel('Overlay legend')).toContainText('0.000 Å')
  await expect(overlay.getByTestId('viewer3d').locator('canvas')).toBeVisible()
})

test('export the canvas as an image', async ({ page }) => {
  // T-UI-04 (canvas image), FR-CAN-07, P18: PNG and SVG
  await openDemo(page)
  for (const [item, name, check] of [
    ['Whole canvas as PNG', 'canvas-full.png', (b: Buffer) => b.subarray(1, 4).toString() === 'PNG'],
    ['Visible area as SVG', 'canvas-viewport.svg', (b: Buffer) => b.toString('utf8').includes('<svg')],
  ] as const) {
    await page.getByRole('button', { name: 'Export image ▾' }).click()
    const download = page.waitForEvent('download')
    await page.getByRole('menuitem', { name: item }).click()
    const file = await download
    expect(file.suggestedFilename()).toBe(name)
    const bytes = readFileSync(await file.path())
    expect(bytes.length).toBeGreaterThan(2000)
    expect(check(bytes)).toBe(true)
  }
})
