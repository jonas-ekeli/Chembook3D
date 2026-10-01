import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { expect, test, type Locator, type Page } from '@playwright/test'

// T-UI-05, FR-STER-01…05, D81: a steric profile set up on one node, shared with a copy of it,
// and compared with maps, a difference map and a CSV.
const E2E_DIR = process.env.E2E_DIR!

// Ni(CO)3 with an NHC (SIPr), from morfeus's test data (tests/fixtures/sterics, MIT). SambVca:
// %V_bur 36.1 with atoms 1-7 (Ni and the three CO) left out.
const NHC = readFileSync(resolve(import.meta.dirname, '..', '..', 'tests', 'fixtures', 'sterics', '2.xyz'), 'utf8')

/** The same structure turned 90° about z and shifted: the frame must give the same values. */
function turned(text: string): string {
  const lines = text.split(/\r?\n/)
  return lines
    .map((line, i) => {
      const parts = line.trim().split(/\s+/)
      if (i < 2 || parts.length < 4) return line
      const [x, y, z] = parts.slice(1, 4).map(Number)
      return `${parts[0]} ${(-y + 2).toFixed(6)} ${(x - 1).toFixed(6)} ${(z + 3).toFixed(6)}`
    })
    .join('\n')
}

function canvasNode(page: Page, label: string): Locator {
  return page.getByRole('group', { name: `Node ${label}`, exact: true })
}

/** The colour at the left end of a map's colour bar, under the map. */
async function lowestColour(map: Locator): Promise<number[]> {
  return map.evaluate((canvas: HTMLCanvasElement) => {
    const scale = canvas.width / canvas.getBoundingClientRect().width
    const size = canvas.getBoundingClientRect().width
    const pixel = canvas.getContext('2d')!.getImageData(Math.round(20 * scale), Math.round((size + 15) * scale), 1, 1)
    return Array.from(pixel.data.slice(0, 3))
  })
}

async function near(colour: Promise<number[]>, expected: number[]): Promise<boolean> {
  const got = await colour
  return got.every((value, i) => Math.abs(value - expected[i]) <= 4)
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

test('buried volume and steric maps from a saved profile', async ({ page }) => {
  test.slow() // software WebGL is slow on the Windows runners
  await newInvestigation(page, 'Sterics test')
  for (const body of [
    { label: 'NHC', xyz: NHC, pos_x: 100, pos_y: 100 },
    { label: 'NHC turned', xyz: turned(NHC), pos_x: 400, pos_y: 100 },
  ]) {
    const response = await page.request.post('/api/nodes', { data: body })
    expect(response.ok()).toBeTruthy()
  }
  await page.reload()

  // A new profile with SambVca's defaults, then the node's atoms.
  await canvasNode(page, 'NHC').click()
  const section = page.getByLabel('Node inspector').getByRole('region', { name: 'Sterics' })
  await expect(section).toContainText('No steric profile yet')
  await section.getByRole('button', { name: 'New profile…' }).click()
  const settings = page.getByRole('dialog', { name: 'New steric profile' })
  await expect(settings.getByLabel('Sphere radius')).toHaveValue('3.5')
  await settings.getByLabel('Profile name').fill('Ni pocket')
  await settings.getByRole('button', { name: 'Create' }).click()
  const atoms = page.getByRole('dialog', { name: 'Atoms of “NHC” in “Ni pocket”' })
  await atoms.getByLabel('Centre atoms').fill('1')
  await atoms.getByLabel('z-axis atoms').fill('15')
  await atoms.getByLabel('xz-plane atoms').fill('14')
  await atoms.getByLabel('Left out atoms').fill('1-7')
  await atoms.getByRole('button', { name: 'Save atoms' }).click()
  await expect(atoms).toHaveCount(0)
  await expect(section).toContainText('Centre 1 · z-axis 15 · xz-plane 14 · left out 1-7')
  await expect(section).toContainText('Not computed yet')
  await section.getByRole('button', { name: 'Compute' }).click()
  await expect(section.getByLabel('Buried volume')).toContainText('%V_bur 36.1')
  await expect(section.getByLabel('Quadrants')).toContainText('NE')
  const map = section.getByRole('img', { name: 'Steric map of NHC' })
  await expect(map).toBeVisible()

  // D84: the map colours are an app setting, changed beside the map; the colour bar starts
  // with the scheme's lowest colour (blue #cde2fb, green #1a9850).
  await expect.poll(() => near(lowestColour(map), [0xcd, 0xe2, 0xfb])).toBe(true)
  await section.getByLabel('Map colours').selectOption('green-yellow-red')
  await expect.poll(() => near(lowestColour(map), [0x1a, 0x98, 0x50])).toBe(true)
  await expect.poll(async () => (await (await page.request.get('/api/settings')).json()).steric_colours).toBe(
    'green-yellow-red',
  )

  // A changed setting puts the result out of date until it is recomputed.
  await section.getByRole('button', { name: 'Settings…' }).click()
  const edit = page.getByRole('dialog', { name: 'Steric profile “Ni pocket”' })
  await edit.getByLabel('Sphere radius').fill('3')
  await edit.getByRole('button', { name: 'Save' }).click()
  await expect(section.getByLabel('Out of date')).toContainText("the profile's settings changed")
  await section.getByRole('button', { name: 'Settings…' }).click()
  await edit.getByLabel('Sphere radius').fill('3.5')
  await edit.getByRole('button', { name: 'Save' }).click()
  await expect(section.getByLabel('Out of date')).toHaveCount(0)

  // The turned copy has the same elements in order, so it can share the atom numbers.
  await canvasNode(page, 'NHC turned').click()
  await expect(section).toContainText('This node is not in “Ni pocket” yet.')
  await section.getByRole('button', { name: 'Use the atoms of NHC' }).click()
  await section.getByRole('button', { name: 'Compute' }).click()
  await expect(section.getByLabel('Buried volume')).toContainText('%V_bur 36.1')

  // Compare the two: the same values, maps on one scale, and a flat difference.
  await canvasNode(page, 'NHC').click({ modifiers: ['Control'] })
  await page.getByLabel('Selection inspector').getByRole('button', { name: 'Compare sterics' }).click()
  const compare = page.getByRole('dialog', { name: 'Compare sterics' })
  const table = compare.getByLabel('Buried volume table')
  await expect(table.getByRole('row', { name: /^NHC turned/ })).toContainText('36.1')
  await expect(table.getByRole('row', { name: /^NHC 36/ })).toContainText('36.1')
  await expect(compare.getByRole('img', { name: /^Steric map of/ })).toHaveCount(2)
  await compare.getByRole('button', { name: 'Show difference' }).click()
  await expect(compare.getByRole('img', { name: /^Difference map of/ })).toBeVisible()
  const download = page.waitForEvent('download')
  await compare.getByRole('button', { name: 'Save CSV' }).click()
  const saved = readFileSync(await (await download).path(), 'utf8')
  expect(saved.split('\n').filter((line) => line.includes(',36.1,'))).toHaveLength(2)
  await compare.getByLabel('Map colours').selectOption('blue')
  await compare.getByRole('button', { name: 'Close' }).click()
})
