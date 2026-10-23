import { join } from 'node:path'
import { expect, test, type Page } from '@playwright/test'
import { forgetView } from './demoView.ts'

// T-UI-53, D176 (UI6): one pill per arrow. ΔX into a transition state reads "ΔG‡" on an amber
// pill, other edges "ΔG"; the warning is a "!" badge and "no TS" a tag inside the pill; the
// pill's outline is the transition's status (solid done, dashed planned).

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
}

type Canvas = {
  nodes: { id: string; label: string }[]
  transitions: { id: string; source_id: string; target_id: string; status: string }[]
}

test('arrow labels: barriers as ΔG‡, warning and "no TS" inside one pill, status as its outline', async ({ page }) => {
  await openDemo(page)
  const canvas: Canvas = await (await page.request.get('/api/canvas')).json()
  const id = (label: string) => canvas.nodes.find((n) => n.label === label)!.id
  const edge = (a: string, b: string) =>
    canvas.transitions.find((t) => t.source_id === id(a) && t.target_id === id(b))!
  const pill = (transitionId: string) => page.locator(`.edge-label[data-edge="${transitionId}"] .edge-pill`)

  // Into the TS A-S2: a barrier, amber, 12 px semibold.
  const barrier = pill(edge('A-S1', 'A-S2').id)
  await expect(barrier.locator('.edge-energy')).toHaveText('ΔG‡ 18.00')
  await expect(barrier).toHaveClass(/\bbarrier\b/)
  await expect(barrier).toHaveCSS('font-size', '12px')
  await expect(barrier).toHaveCSS('font-weight', '600')
  // Out of it: a reaction energy, with a real minus sign, and its W-BALANCE warning as a badge.
  const out = pill(edge('A-S2', 'A-S3').id)
  await expect(out.locator('.edge-energy')).toHaveText('ΔG −23.30')
  await expect(out).not.toHaveClass(/\bbarrier\b/)
  const badge = out.getByRole('img', { name: 'Does not balance' })
  await expect(badge).toHaveText('!')
  await expect(badge).toHaveAttribute('title', /^W-BALANCE/)
  // A direct connection: "no TS" inside the pill, never a barrier (D53).
  const direct = pill(edge('A-S1', 'A-S3').id)
  await expect(direct.locator('.no-ts')).toHaveText('no TS')
  await expect(direct.locator('.edge-energy')).toHaveText('ΔG −5.30')
  await expect(direct).not.toHaveClass(/\bbarrier\b/)
  await expect(page.locator('.edge-label .status-dot')).toHaveCount(0)

  // The status is the outline (and the tooltip and spoken name): dashed planned, solid done.
  const step = edge('A-S2', 'A-S3')
  expect(step.status).toBe('planned')
  await expect(out).toHaveCSS('border-top-style', 'dashed')
  await expect(out).toHaveAttribute('title', 'Transition: Planned')
  await expect(out.locator('.visually-hidden')).toHaveText('Planned')
  await page.request.patch(`/api/transitions/${step.id}`, { data: { status: 'done' } })
  const made: string[] = []
  let group: string | null = null
  try {
    await page.reload()
    await expect(out).toHaveCSS('border-top-style', 'solid')
    await expect(out).toHaveAttribute('title', 'Transition: Done')

    // D111: an arrow into a group counts it as a TS only through a TS representative.
    for (const [label, role] of [
      ['TS-arrow', 'transition_state'],
      ['Min-arrow', 'minimum'],
    ]) {
      const node = await page.request.post('/api/nodes', { data: { label, role, pos_x: 1200, pos_y: 0 } })
      expect(node.ok()).toBe(true)
      made.push((await node.json()).id)
    }
    const grouped = await page.request.post('/api/groups/reconnect', { data: { member_ids: made, label: 'G-arrow' } })
    expect(grouped.ok()).toBe(true)
    group = (await grouped.json()).id as string
    const drawn = await page.request.post('/api/transitions', { data: { source_id: id('A-S3'), target_id: group } })
    expect(drawn.ok()).toBe(true)
    const into = pill((await drawn.json()).id)
    await page.reload()
    await expect(into.locator('.edge-energy-type')).toHaveText('ΔG')
    await expect(into.locator('.no-ts')).toBeVisible()
    await page.request.patch(`/api/groups/${group}`, { data: { representative_id: made[0] } })
    await page.reload()
    await expect(into.locator('.edge-energy-type')).toHaveText('ΔG‡')
    await expect(into).toHaveClass(/\bbarrier\b/)
    await expect(into.locator('.no-ts')).toHaveCount(0)
  } finally {
    await page.request.patch(`/api/transitions/${step.id}`, { data: { status: 'planned' } })
    // The group with its members and the new arrow, so the demo is as it was.
    if (group) await page.request.delete(`/api/groups/${group}`)
    else for (const node of made) await page.request.delete(`/api/nodes/${node}`)
  }
})
