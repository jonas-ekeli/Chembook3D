import { join } from 'node:path'
import { expect, test, type Locator, type Page } from '@playwright/test'
import { forgetView } from './demoView.ts'

// T-UI-52, D175 (UI1): node cards read at the zoom the canvas opens at. The energy sits in the
// title row with a real minus sign, a TS carries an amber "TS" tag, a planned node is dashed,
// the status is a dot and a word, and the connection handles show only on a hovered or selected
// card, or on every card while an arrow is being drawn.

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

const card = (page: Page, label: string) => page.getByRole('group', { name: `Node ${label}`, exact: true })
const handleOpacity = (node: Locator) =>
  node.locator('.react-flow__handle-right').evaluate((element) => getComputedStyle(element).opacity)
const dot = (node: Locator) =>
  node.locator('.status').evaluate((element) => getComputedStyle(element, '::before').backgroundColor)

test('node cards: energy in the title row, TS tag, dashed planned nodes, handles on hover', async ({ page }) => {
  await openDemo(page)
  await page.getByRole('button', { name: 'Energy', exact: true }).click()
  await card(page, 'T-S0').click()
  await page.getByLabel('Node inspector').getByRole('button', { name: 'Use as energy reference' }).click()
  await page.locator('.react-flow__pane').click({ position: { x: 10, y: 10 } })

  // The energy in the title row: "ΔG" and the number, with a real minus sign.
  await expect(card(page, 'A-S2').locator('.cnode-head .cnode-energy')).toHaveText('ΔG 14.80')
  await expect(card(page, 'B-S3').locator('.cnode-head .cnode-energy')).toHaveText('ΔG −6.00')
  await expect(card(page, 'A-S2').locator('.cnode-head .cnode-energy')).toHaveCSS('font-size', '15px')
  await expect(card(page, 'A-S2').locator('.cnode-label')).toHaveCSS('font-size', '14px')
  expect((await card(page, 'A-S2').locator('.cnode').boundingBox())!.width).toBeCloseTo(188 * (await zoom(page)), 0)

  // A TS is tagged "TS"; a minimum is not.
  await expect(card(page, 'A-S2').locator('.ts-mark')).toHaveText('TS')
  await expect(card(page, 'A-S1').locator('.ts-mark')).toHaveCount(0)

  // Planned (A-S2) is dashed; done (A-S1) is not; the status dots differ.
  await expect(card(page, 'A-S2').locator('.cnode')).toHaveCSS('border-top-style', 'dashed')
  await expect(card(page, 'A-S1').locator('.cnode')).toHaveCSS('border-top-style', 'solid')
  expect(await dot(card(page, 'A-S2'))).not.toBe(await dot(card(page, 'A-S1')))

  // Handles: hidden, shown under the pointer and on the selected card.
  await expect.poll(() => handleOpacity(card(page, 'A-S1'))).toBe('0')
  await card(page, 'A-S1').hover()
  await expect.poll(() => handleOpacity(card(page, 'A-S1'))).toBe('1')
  await card(page, 'B-S3').click()
  await page.mouse.move(5, 5)
  await expect.poll(() => handleOpacity(card(page, 'B-S3'))).toBe('1')
  await expect.poll(() => handleOpacity(card(page, 'A-S1'))).toBe('0')

  // While an arrow is drawn, every card shows its handles, so the far end can be found.
  const from = (await card(page, 'A-S1').locator('.react-flow__handle-right').boundingBox())!
  await card(page, 'A-S1').hover()
  await page.mouse.move(from.x + from.width / 2, from.y + from.height / 2)
  await page.mouse.down()
  await page.mouse.move(from.x + 30, from.y + 70, { steps: 5 })
  await expect.poll(() => handleOpacity(card(page, 'A-S3'))).toBe('1')
  // Let go over the empty canvas: no transition is made.
  const edges = await page.locator('.react-flow__edge').count()
  await page.mouse.up()
  await expect.poll(() => handleOpacity(card(page, 'A-S3'))).toBe('0')
  await expect(page.locator('.react-flow__edge')).toHaveCount(edges)
})

/** The canvas's zoom, from React Flow's viewport transform. */
async function zoom(page: Page): Promise<number> {
  const transform = await page.locator('.react-flow__viewport').evaluate((element) => getComputedStyle(element).transform)
  return Number(transform.match(/^matrix\(([^,]+)/)![1])
}
