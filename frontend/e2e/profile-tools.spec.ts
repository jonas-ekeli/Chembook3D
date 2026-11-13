import { expect, test, type Page } from '@playwright/test'
import { openDemo } from './helpers.ts'

// T-UI-96 (D227): a saved energy profile Claude makes, changes or has deleted shows in the
// open drawer at once, with no reload. Claude is played by requests carrying its client name,
// as the MCP server sends them.
const CLAUDE = { 'X-Chembook-Client': 'claude' }
type Canvas = { nodes: { id: string; label: string }[] }
type Profile = { id: string; name: string }

test.afterEach(async ({ request }) => {
  await request.post('/api/investigations/close')
})

async function idsOf(page: Page, labels: string[]): Promise<string[]> {
  const canvas = (await (await page.request.get('/api/canvas')).json()) as Canvas
  return labels.map((label) => canvas.nodes.find((n) => n.label === label)!.id)
}

test('the open drawer shows what Claude does to the saved profiles', async ({ page }) => {
  await openDemo(page)
  const a = await idsOf(page, ['T-S0', 'A-S1', 'A-S2', 'A-S3'])
  const b = await idsOf(page, ['T-S0', 'B-S1', 'B-S2', 'B-S3'])
  await page.getByRole('button', { name: 'Profile and table' }).click()
  const drawer = page.getByLabel('Energy drawer')
  const picker = drawer.getByRole('combobox', { name: 'Open profile' })
  const pathways = drawer.getByRole('group', { name: /^Pathway / })
  await expect(picker).toBeDisabled()
  await expect(pathways).toHaveCount(0)

  // Made by Claude: it is in the picker and, being the only one, open.
  const made = await page.request.post('/api/energy-profiles', {
    data: { name: 'Rotamer A', paths: [{ ids: a }] },
    headers: CLAUDE,
  })
  expect(made.status(), await made.text()).toBe(201)
  const profile = (await made.json()) as Profile
  await expect(picker).toHaveValue(profile.id)
  await expect(picker.locator('option')).toHaveText(['Rotamer A'])
  await expect(pathways).toHaveCount(1)

  // Changed by Claude while it is open: a second pathway and another name.
  const changed = await page.request.patch(`/api/energy-profiles/${profile.id}`, {
    data: { name: 'Both rotamers', paths: [{ ids: a }, { ids: b }] },
    headers: CLAUDE,
  })
  expect(changed.status(), await changed.text()).toBe(200)
  await expect(picker.locator('option')).toHaveText(['Both rotamers'])
  await expect(pathways).toHaveCount(2)
  await expect(picker).toHaveValue(profile.id)

  // A pathway that follows no transition is refused, and the drawer stays as it is.
  const gap = await page.request.patch(`/api/energy-profiles/${profile.id}`, {
    data: { paths: [{ ids: [a[1], b[1]] }] },
    headers: CLAUDE,
  })
  expect(gap.status()).toBe(422)
  expect(await gap.text()).toContain('No transition joins')

  // What the user does in the drawer is kept when Claude adds a profile beside it.
  await pathways.first().getByRole('checkbox').uncheck()
  await expect
    .poll(async () => {
      const stored = (await (await page.request.get('/api/energy-profiles')).json()) as { paths: { shown: boolean }[] }[]
      return stored[0].paths.map((p) => p.shown)
    })
    .toEqual([false, true])
  const copy = await page.request.post(`/api/energy-profiles/${profile.id}/duplicate`, { headers: CLAUDE })
  const copied = (await copy.json()) as Profile
  await expect(picker.locator('option')).toHaveText(['Both rotamers', 'Both rotamers (copy)'])
  await expect(picker).toHaveValue(profile.id)
  await expect(pathways.first().getByRole('checkbox')).not.toBeChecked()

  // Deleting a profile is asked in the page, also for the copy Claude made.
  await page.request.post('/api/confirmations', {
    data: { action: 'delete_energy_profile', params: { energy_profile_id: copied.id }, reason: 'Only a trial.' },
    headers: CLAUDE,
  })
  const dialog = page.getByRole('dialog', { name: 'Claude asks to delete' })
  await expect(dialog).toContainText('Delete the energy profile “Both rotamers (copy)”')
  await dialog.getByRole('button', { name: 'Confirm' }).click()
  await expect(dialog).toHaveCount(0)
  await expect(picker.locator('option')).toHaveText(['Both rotamers'])
  await expect(pathways).toHaveCount(2)
})
