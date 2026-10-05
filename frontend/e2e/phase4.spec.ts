import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { expect, test, type Locator, type Page } from '@playwright/test'

const E2E_DIR = process.env.E2E_DIR!

async function openDemo(page: Page) {
  // Values below are in kcal/mol; an earlier test may have left another unit set.
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

async function setUnit(page: Page, unit: string) {
  await page.getByRole('button', { name: 'Settings' }).click()
  const dialog = page.getByRole('dialog', { name: 'Settings' })
  await dialog.getByLabel('Energy unit').selectOption(unit)
  await expect(dialog.getByLabel('Energy unit')).toHaveValue(unit)
  await dialog.getByRole('button', { name: 'Done' }).click()
}

test('energy view: one level and type, ΔG on edges, energy mode', async ({ page }) => {
  // FR-EN-01, 02, 04, 07; FR-CAN-03, FR-CAN-04 (energy mode); T-EN-06, T-EN-07; T-UI-02
  await openDemo(page)
  const before = await (await page.request.get('/api/canvas')).json()
  const view = page.getByRole('group', { name: 'Energy view' })
  await expect(view.getByLabel('Level of theory')).toHaveValue(/.+/)
  await expect(view.getByLabel('Level of theory').locator('option')).toHaveText(['Gaussian B3LYP-GD3BJ/def2SVP'])
  // G is preferred (D32); G_qh is listed with its temperature and cutoff (D58).
  await expect(view.getByLabel('Energy type')).toHaveValue('G')
  await expect(view.getByLabel('Energy type').locator('option')).toHaveText([
    'E',
    'H',
    'G',
    'G_qh (298.15 K, 100 cm⁻¹)',
  ])

  // FR-EN-02: ΔG = G(target) − G(source) on each edge; A-S1 → A-S2 is 14.8 − (−3.2).
  await expect(page.locator('.edge-energy', { hasText: 'ΔG 18.00' })).toBeVisible()
  await view.getByLabel('Energies on edges').uncheck()
  await expect(page.locator('.edge-energy')).toHaveCount(0)
  await view.getByLabel('Energies on edges').check()

  // D43: energy mode shows ΔX from the reference node the user picks.
  // Without a reference, structure mode shows no energies at all.
  await page.getByRole('button', { name: 'Structure', exact: true }).click()
  await expect(canvasNode(page, 'A-S2').getByRole('img', { name: 'Structure' })).toBeVisible()
  await expect(canvasNode(page, 'A-S2').locator('.cnode-energy')).toHaveCount(0)
  await page.getByRole('button', { name: 'Energy', exact: true }).click()
  await expect(canvasNode(page, 'A-S2')).toContainText('ΔG: no reference')
  await canvasNode(page, 'T-S0').click()
  await page.getByLabel('Node inspector').getByRole('button', { name: 'Use as energy reference' }).click()
  await expect(canvasNode(page, 'A-S2')).toContainText('ΔG 14.80')
  await expect(canvasNode(page, 'B-S3')).toContainText('ΔG -6.00')

  // D73: structure mode shows the same ΔX below each structure, once a reference is chosen.
  await page.getByRole('button', { name: 'Structure', exact: true }).click()
  await expect(canvasNode(page, 'A-S2').getByRole('img', { name: 'Structure' })).toBeVisible()
  await expect(canvasNode(page, 'A-S2').locator('.cnode-energy')).toHaveText('ΔG 14.80')
  if (process.env.SHOT) await page.screenshot({ path: process.env.SHOT })
  await page.getByRole('button', { name: 'Energy', exact: true }).click()

  // T-EN-06: the unit changes what is shown, not what is stored.
  await setUnit(page, 'kJ/mol')
  await expect(canvasNode(page, 'A-S2')).toContainText('ΔG 61.92')
  await setUnit(page, 'kcal/mol')
  await expect(canvasNode(page, 'A-S2')).toContainText('ΔG 14.80')

  await view.getByLabel('Energy type').selectOption('E')
  await expect(page.locator('.edge-energy', { hasText: 'ΔE 18.00' })).toBeVisible()
  expect(await (await page.request.get('/api/canvas')).json()).toEqual(before)
})

test('a free species on one edge counts on every node card after it', async ({ page }) => {
  // T-SPC-06, D72: node cards use the balance along the route from the reference.
  await openDemo(page)
  const canvas = await (await page.request.get('/api/canvas')).json()
  const id = (label: string) => canvas.nodes.find((n: { label: string }) => n.label === label).id
  const edge = canvas.transitions.find(
    (t: { source_id: string; target_id: string }) => t.source_id === id('T-S0') && t.target_id === id('A-S1'),
  )
  // A species with no energies: every card after the edge it joins on has no balanced value.
  const species = await (await page.request.post('/api/nodes', { data: { label: 'substrate', kind: 'species' } })).json()
  try {
    await page.request.put(`/api/transitions/${edge.id}/species`, {
      data: { species_id: species.id, direction: 'joins', count: 1 },
    })
    await page.reload()
    await expect(canvasNode(page, 'T-S0')).toBeVisible()
    await page.getByRole('button', { name: 'Energy', exact: true }).click()
    await canvasNode(page, 'T-S0').click()
    await page.getByLabel('Node inspector').getByRole('button', { name: 'Use as energy reference' }).click()
    await expect(canvasNode(page, 'T-S0')).toContainText('ΔG 0.00')
    for (const label of ['A-S1', 'A-S2', 'A-S3']) await expect(canvasNode(page, label)).toContainText('ΔG n/a')
    await expect(canvasNode(page, 'B-S3')).toContainText('ΔG -6.00')
  } finally {
    await page.request.delete(`/api/nodes/${species.id}`)
  }
})

test('a line between collapsed groups shows the energy of the edge between their representatives', async ({ page }) => {
  // T-EN-14, D70, A24
  await openDemo(page)
  const canvas = await (await page.request.get('/api/canvas')).json()
  const id = (label: string) => canvas.nodes.find((n: { label: string }) => n.label === label).id
  const groups: string[] = []
  for (const [label, members] of [
    ['S1', ['A-S1', 'B-S1']],
    ['S2', ['A-S2', 'B-S2']],
  ] as const) {
    const made = await page.request.post('/api/groups/reconnect', { data: { member_ids: members.map(id), label } })
    groups.push((await made.json()).id)
  }
  try {
    await page.reload()
    const line = page.locator('.edge-label', { has: page.locator('.edge-count', { hasText: '2 edges' }) })
    const between = line.filter({ hasNot: page.getByText('no TS') })
    await expect(between).toHaveCount(1)
    await expect(between.locator('.edge-energy')).toHaveCount(0) // no representatives yet

    // A-S1 → A-S2 is ΔG 18.00; the line shows it once both representatives are on that edge.
    await page.request.patch(`/api/groups/${groups[0]}`, { data: { representative_id: id('A-S1') } })
    await page.reload()
    await expect(between.locator('.edge-energy')).toHaveCount(0) // S2 has none yet
    await page.request.patch(`/api/groups/${groups[1]}`, { data: { representative_id: id('A-S2') } })
    await page.reload()
    await expect(between.locator('.edge-energy')).toHaveText('ΔG 18.00')
    await expect(between).toContainText('2 edges')
  } finally {
    // Leave the demo as it was: members keep their branches.
    for (const group of groups) {
      await page.request.post(`/api/groups/${group}/dissolve`, { data: { restore_branches: true } })
    }
  }
})

test('profile along branches with a "no TS" connection, the energy table and exports', async ({ page }) => {
  // WF-08, FR-EN-05, 06, 09; T-BR-13 (profile); T-EN-08; T-UI-04 (profile image)
  await openDemo(page)
  await page.getByRole('button', { name: 'Profile and table' }).click()
  const drawer = page.getByLabel('Energy drawer')
  await drawer.getByLabel('Add branch pathway').selectOption({ label: 'A' })
  const a = drawer.getByRole('group', { name: 'Pathway A' })
  await expect(a).toContainText('T-S0 → A-S1')
  // A fork: the user chooses, the app never does (EN-10).
  const choices = a.getByRole('group', { name: 'Continue with' })
  await expect(choices.getByRole('button')).toHaveText(['A-S2', 'A-S3'])
  await choices.getByRole('button', { name: 'A-S3' }).click()
  await expect(a).toContainText('T-S0 → A-S1 → A-S3')
  await drawer.getByLabel('Add branch pathway').selectOption({ label: 'B' })
  await expect(drawer.getByRole('group', { name: 'Pathway B' })).toContainText('T-S0 → B-S1 → B-S2 → B-S3')
  await expect(drawer.getByLabel('Reference node')).toHaveValue(
    (await (await page.request.get('/api/canvas')).json()).nodes.find((n: { label: string }) => n.label === 'T-S0').id,
  )

  const chart = drawer.getByRole('img', { name: 'Energy profile' })
  await expect(chart).toContainText('-8.50')
  await expect(chart).toContainText('17.30')
  // FR-EN-09: direct connections (A-S1 → A-S3, and T-S0 into A-S1 and B-S1, all between
  // minima) are dotted "no TS" segments, never barriers.
  await expect(chart.locator('[data-direct="true"]')).toHaveCount(3)
  for (const segment of await chart.locator('[data-direct="true"]').all()) await expect(segment).toContainText('no TS')
  await page.screenshot({ path: 'test-results/phase4-profile.png' })

  for (const [button, name, check] of [
    ['Save profile as PNG', 'energy-profile.png', (b: Buffer) => b.subarray(1, 4).toString() === 'PNG'],
    ['Save profile as SVG', 'energy-profile.svg', (b: Buffer) => b.toString('utf8').includes('<svg')],
  ] as const) {
    const download = page.waitForEvent('download')
    await drawer.getByRole('button', { name: button }).click()
    const file = await download
    expect(file.suggestedFilename()).toBe(name)
    const bytes = readFileSync(await file.path())
    expect(bytes.length).toBeGreaterThan(2000)
    expect(check(bytes)).toBe(true)
  }

  await drawer.getByRole('button', { name: 'Energy table' }).click()
  const table = drawer.getByRole('table', { name: 'Energy table' })
  await expect(table.getByRole('row')).toHaveCount(7) // header + 6 nodes, each once
  const shown = await table.getByRole('row').evaluateAll((rows) =>
    rows.map((row) => Array.from(row.querySelectorAll('th, td')).map((cell) => cell.textContent ?? '')),
  )
  expect(shown[0].at(-1)).toBe('ΔG (kcal/mol)')
  expect(shown.find((row) => row[0] === 'A-S3')?.at(-1)).toBe('-8.50')

  // T-EN-08: the CSV holds the same text as the table on screen.
  const download = page.waitForEvent('download')
  await drawer.getByRole('button', { name: 'Export CSV' }).click()
  const file = await download
  expect(file.suggestedFilename()).toBe('energy-table.csv')
  const text = readFileSync(await file.path(), 'utf8').replace(/^﻿/, '')
  const rows = text
    .trim()
    .split('\r\n')
    .map((line) => line.split(','))
  expect(rows).toEqual(shown)
})

test('a catalytic cycle closes at the resting state', async ({ page }) => {
  // A13: a pathway may end at a node it visited, closing the cycle once.
  await openDemo(page)
  const canvas = await (await page.request.get('/api/canvas')).json()
  const id = (label: string) => canvas.nodes.find((n: { label: string }) => n.label === label).id
  const closing = await (
    await page.request.post('/api/transitions', { data: { source_id: id('B-S3'), target_id: id('T-S0') } })
  ).json()
  try {
    await page.reload()
    await expect(canvasNode(page, 'T-S0')).toBeVisible()
    await page.getByRole('button', { name: 'Profile and table' }).click()
    const drawer = page.getByLabel('Energy drawer')
    await drawer.getByLabel('Add branch pathway').selectOption({ label: 'B' })
    const b = drawer.getByRole('group', { name: 'Pathway B' })
    await expect(b).toContainText('T-S0 → B-S1 → B-S2 → B-S3 → T-S0 ↻')
    await expect(b).toContainText('Cycle closed.')
    await expect(b.getByRole('group', { name: 'Continue with' })).toHaveCount(0)

    const chart = drawer.getByRole('img', { name: 'Energy profile' })
    await expect(chart).toContainText('T-S0 ↻')

    await drawer.getByRole('button', { name: 'Energy table' }).click()
    await expect(drawer.getByRole('table', { name: 'Energy table' }).getByRole('row')).toHaveCount(5)
  } finally {
    await page.request.delete(`/api/transitions/${closing.id}`)
  }
})

test('the 1 M standard state is a setting the energy view names', async ({ page }) => {
  // D95, A42: off by default; once on, G and G_qh say 1 M and a badge shows it.
  await openDemo(page)
  const view = page.getByRole('group', { name: 'Energy view' })
  await expect(view.locator('.badge', { hasText: '1 M' })).toHaveCount(0)
  try {
    await page.getByRole('button', { name: 'Settings' }).click()
    const dialog = page.getByRole('dialog', { name: 'Settings' })
    await expect(dialog.getByLabel('Standard state')).toHaveValue('1 atm')
    await expect(dialog).toContainText('1.8943 kcal/mol at 298.15 K')
    await dialog.getByLabel('Standard state').selectOption('1 M')
    await expect(dialog.getByLabel('Standard state')).toHaveValue('1 M')
    await dialog.getByRole('button', { name: 'Done' }).click()

    await expect(view.getByLabel('Energy type').locator('option')).toHaveText([
      'E',
      'H',
      'G (1 M, 298.15 K)',
      'G_qh (298.15 K, 100 cm⁻¹, 1 M)',
    ])
    await expect(view.locator('.badge', { hasText: '1 M' })).toBeVisible()
    // One molecule on each side: the correction cancels on the edge.
    await expect(page.locator('.edge-energy', { hasText: 'ΔG 18.00' })).toBeVisible()
    await view.getByLabel('Energy type').selectOption('E')
    await expect(view.locator('.badge', { hasText: '1 M' })).toHaveCount(0)
    await view.getByLabel('Energy type').selectOption('G')
  } finally {
    await page.request.put('/api/settings', { data: { standard_state: '1 atm' } })
  }
})
