import { join } from 'node:path'
import { expect, test, type Page } from '@playwright/test'
import { forgetView } from './demoView.ts'

// T-UI-51, D174 (UI5): one button and field system. Labels stay on one line at a 1280 px window,
// the canvas tools are icon and short label with the full name kept, a destructive button has
// the normal border, and keyboard focus shows a ring.

const E2E_DIR = process.env.E2E_DIR!

test.use({ viewport: { width: 1280, height: 800 } })

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

test('buttons keep one line, the canvas tools are short, focus shows a ring', async ({ page }) => {
  await openDemo(page)
  await page.getByRole('group', { name: 'Node A-S2', exact: true }).click()
  const head = page.locator('.inspector-head')
  await expect(head.getByRole('heading', { name: 'A-S2' })).toBeVisible()

  // One line each: as high as a control (30 px), however long the label.
  const buttons = [
    head.getByRole('button', { name: 'Use as energy reference' }),
    head.getByRole('button', { name: 'Import onto node…' }),
    head.getByRole('button', { name: 'Delete node' }),
    page.getByRole('button', { name: 'Fit to selection' }),
    page.getByRole('button', { name: 'Export image ▾' }),
  ]
  for (const button of buttons) expect((await button.boundingBox())!.height).toBeLessThanOrEqual(30.5)
  expect((await head.getByRole('heading', { name: 'A-S2' }).boundingBox())!.height).toBeLessThan(30)

  // The canvas tools: an icon and a short label; the full name in the tooltip.
  const fit = page.getByRole('button', { name: 'Fit to selection' })
  await expect(fit).toHaveText('Fit')
  await expect(fit).toHaveAttribute('title', 'Fit to selection')
  await expect(page.getByRole('button', { name: 'Export image ▾' })).toHaveText('Export ▾')

  // Destructive: red text on the border every button has.
  const border = (name: string) =>
    head.getByRole('button', { name }).evaluate((element) => getComputedStyle(element).borderTopColor)
  expect(await border('Delete node')).toBe(await border('Import onto node…'))

  // Keyboard focus: the accent ring.
  await fit.focus()
  await page.keyboard.press('Shift+Tab')
  await page.keyboard.press('Tab')
  await expect(fit).toBeFocused()
  await expect(fit).toHaveCSS('box-shadow', /0px 0px 0px 3px$/)
})
