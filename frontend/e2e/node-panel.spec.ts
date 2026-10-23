import { join } from 'node:path'
import { expect, test, type Page } from '@playwright/test'
import { forgetView } from './demoView.ts'

// T-UI-56, D179 (UI3): the node panel leads with what the node is and its numbers. A sticky
// header holds the TS tag and the name on one line, two actions and a ⋯ menu for the rare ones,
// a meta row, the stat strip (the card's ΔG, the imaginary frequencies, the absolute energy) and
// a jump bar that scrolls the one panel to each section.

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

test('the node panel: header, stat strip, ⋯ menu by keyboard, jump bar', async ({ page }) => {
  await openDemo(page)
  await page.getByRole('button', { name: 'Energy', exact: true }).click()
  await card(page, 'T-S0').click()
  const inspector = page.getByLabel('Node inspector')
  await inspector.getByRole('button', { name: 'Use as energy reference' }).click()
  await card(page, 'A-S2').click()

  // The header: TS tag and the name in 20 px on one line, and what the node is in one row.
  const head = inspector.locator('.node-head')
  await expect(head).toHaveCSS('position', 'sticky')
  await expect(head.locator('.ts-mark')).toHaveText('TS')
  const title = head.getByRole('heading', { name: 'A-S2' })
  await expect(title).toHaveCSS('font-size', '20px')
  await expect(title).toHaveCSS('white-space', 'nowrap')
  await expect(title).toHaveCSS('text-overflow', 'ellipsis')
  expect((await title.boundingBox())!.height).toBeLessThan(30)
  const meta = head.locator('.node-meta')
  await expect(meta).toContainText('Planned')
  await expect(meta).toContainText('Branch A')
  await expect(meta).toContainText('Step [2+2] TS')
  await expect(meta.getByText('2 calculations', { exact: true })).toBeVisible()

  // The stat strip: the card's ΔG from the reference, the TS's one imaginary frequency, and the
  // absolute G at the level.
  const stats = head.getByRole('definition')
  await expect(head.getByRole('term').first()).toHaveText('ΔG (rel. T-S0)')
  await expect(stats.first()).toHaveText('14.80 kcal/mol')
  await expect(card(page, 'A-S2').locator('.cnode-energy')).toHaveText('ΔG 14.80')
  await expect(stats.nth(1)).toHaveText('1')
  await expect(stats.nth(1)).not.toHaveClass(/warn/)
  await expect(head.getByRole('term').nth(2)).toContainText('G (')
  await expect(stats.nth(2)).toHaveText(/^−\d+\.\d{8} Eh$/)

  // The ⋯ menu: Enter opens it on its first item, ↓ moves, Escape closes it onto the button; a
  // click outside closes it too.
  const more = head.getByRole('button', { name: 'More node actions' })
  await more.focus()
  await page.keyboard.press('Enter')
  const menu = page.getByRole('menu', { name: 'More node actions' })
  await expect(menu.getByRole('menuitem')).toHaveText([/^IRC…/, 'Split into branches…', 'Delete node'])
  await expect(menu.getByRole('menuitem', { name: 'IRC…' })).toBeFocused()
  await page.keyboard.press('ArrowDown')
  await expect(menu.getByRole('menuitem', { name: 'Split into branches…' })).toBeFocused()
  await page.keyboard.press('Escape')
  await expect(menu).toHaveCount(0)
  await expect(more).toBeFocused()
  await more.click()
  await expect(menu).toBeVisible()
  await title.click()
  await expect(menu).toHaveCount(0)

  // The jump bar: Calculations scrolls its section to just under the header and is marked.
  const jump = head.getByRole('navigation', { name: 'Sections' })
  await expect(jump.getByRole('link')).toHaveText(['Details', '3D & xyz', 'Calculations2', 'Transitions2', 'Notes', 'History'])
  await jump.getByRole('link', { name: /^Calculations/ }).click()
  await expect(jump.getByRole('link', { name: /^Calculations/ })).toHaveAttribute('aria-current', 'location')
  const section = inspector.getByRole('region', { name: 'Calculations section' })
  await expect
    .poll(async () => {
      const top = (await section.boundingBox())!.y
      const bottom = (await head.boundingBox())!.y + (await head.boundingBox())!.height
      return top >= bottom - 1 && top < bottom + 40
    })
    .toBe(true)
  await jump.getByRole('link', { name: 'Details' }).click()
  await expect(jump.getByRole('link', { name: 'Details' })).toHaveAttribute('aria-current', 'location')
  await expect.poll(() => inspector.getByLabel('Label', { exact: true }).isVisible()).toBe(true)
})

test('the stat strip flags a TS whose frequency job has no imaginary frequency', async ({ page }) => {
  await openDemo(page)
  await card(page, 'B-S1').click()
  const inspector = page.getByLabel('Node inspector')
  const imaginary = inspector.locator('.node-head').getByRole('definition').nth(1)
  await expect(imaginary).toHaveText('0')
  await inspector.getByLabel('Role').selectOption('transition_state')
  try {
    // W-IMAG-TS on its frequency job: amber, with a ⚠ and what a TS needs.
    await expect(imaginary).toHaveText('0 ⚠ TS expects 1')
    await expect(imaginary).toHaveClass(/warn/)
    await expect(inspector.locator('.node-head .ts-mark')).toHaveText('TS')
  } finally {
    await inspector.getByLabel('Role').selectOption('minimum')
  }
  await expect(imaginary).toHaveText('0')
})
