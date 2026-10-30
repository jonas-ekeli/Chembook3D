import { join } from 'node:path'
import { expect, test, type Page } from '@playwright/test'
import { forgetView } from './demoView.ts'
import { showOverview } from './appBar.ts'

// T-UI-57, D180 (UI2): one 44 px app bar and, in the canvas view only, a 40 px canvas toolbar.
// The investigation's commands are in the menu on its name, Shut down last and in red; the view
// tabs are Canvas, History and Analyses; at narrow widths the bars keep one row.

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

const height = async (page: Page, selector: string) => (await page.locator(selector).boundingBox())!.height

test('the app bar, the investigation menu and the canvas toolbar', async ({ page }) => {
  await openDemo(page)
  const bar = page.getByRole('banner')
  const toolbar = page.locator('.canvasbar')
  expect(await height(page, 'header.appbar')).toBeCloseTo(44, 0)
  expect(await height(page, '.canvasbar')).toBeCloseTo(40, 0)

  // The view tabs: the Overview is the side panel's page, not a tab.
  const tabs = page.getByRole('navigation', { name: 'Views' }).getByRole('button')
  await expect(tabs).toHaveText(['Canvas', 'History', 'Analyses'])
  for (const name of ['Scan editor', 'Cloud jobs', 'Claude', 'Not synced', 'Settings'])
    await expect(bar.getByRole('button', { name, exact: true })).toBeVisible()

  // The canvas toolbar holds the canvas's controls, Fit, Tidy and Export among them.
  for (const name of ['Compact', 'Energy', 'Structure', 'Filters', 'Fit to selection', 'Tidy', 'Export image ▾', 'Profile and table'])
    await expect(toolbar.getByRole('button', { name, exact: true })).toBeVisible()
  await expect(toolbar.getByRole('group', { name: 'Energy view' }).getByLabel('Show on arrows')).toBeChecked()

  // The investigation menu: by keyboard it opens on its first item; Escape closes it back onto
  // the button, and a click outside closes it.
  const name = bar.locator('.investigation')
  await expect(name).toHaveText('Demo investigation')
  await expect(name).toHaveAccessibleName('Demo investigation, investigation menu')
  await name.focus()
  await page.keyboard.press('Enter')
  const menu = page.getByRole('menu', { name: 'Investigation' })
  await expect(menu.getByRole('menuitem')).toHaveText([
    'New investigation…',
    'Open investigation…',
    'Close investigation',
    'Sync with GitHub…',
    'Share read-only copy…',
    'Basis sets',
    'Shut down Chembook3D',
  ])
  await expect(menu.getByRole('separator')).toHaveCount(2)
  await expect(menu.getByRole('menuitem', { name: 'New investigation…' })).toBeFocused()
  await page.keyboard.press('ArrowUp')
  const shutDown = menu.getByRole('menuitem', { name: 'Shut down Chembook3D' })
  await expect(shutDown).toBeFocused()
  await expect(shutDown).toHaveClass(/danger/)
  await page.keyboard.press('Escape')
  await expect(menu).toBeHidden()
  await expect(name).toBeFocused()
  await name.click()
  await expect(menu).toBeVisible()
  await page.getByLabel('Overview', { exact: true }).getByRole('heading', { name: 'Overview' }).click()
  await expect(menu).toBeHidden()

  // Shut down asks first, as on the start page.
  await name.click()
  await shutDown.click()
  const ask = page.getByRole('dialog', { name: 'Shut down Chembook3D?' })
  await ask.getByRole('button', { name: 'Cancel' }).click()
  await expect(ask).toBeHidden()

  // Selecting a node leaves the Overview; a click on the empty canvas brings it back.
  await page.getByRole('group', { name: 'Node A-S2', exact: true }).click()
  await expect(page.getByLabel('Node inspector')).toBeVisible()
  await showOverview(page)

  // Other views keep the app bar where it was and have no canvas toolbar.
  const tabBox = await page.getByRole('navigation', { name: 'Views' }).boundingBox()
  await page.getByRole('button', { name: 'History', exact: true }).click()
  await expect(toolbar).toHaveCount(0)
  expect(await page.getByRole('navigation', { name: 'Views' }).boundingBox()).toEqual(tabBox)
  expect(await height(page, 'header.appbar')).toBeCloseTo(44, 0)
  await page.getByRole('button', { name: 'Canvas', exact: true }).click()

  // At 1280 px with the Claude panel open, both bars keep one row: the toolbar's rarer labels
  // give way to their icons, and every button keeps its name.
  await page.setViewportSize({ width: 1280, height: 800 })
  await bar.getByRole('button', { name: 'Claude', exact: true }).click()
  await expect(page.getByRole('complementary', { name: 'Claude' })).toBeVisible()
  expect(await height(page, 'header.appbar')).toBeCloseTo(44, 0)
  expect(await height(page, '.canvasbar')).toBeCloseTo(40, 0)
  const fit = toolbar.getByRole('button', { name: 'Fit to selection' })
  expect((await fit.boundingBox())!.width).toBeLessThan(40)
  const last = (await toolbar.getByRole('button', { name: 'Profile and table' }).boundingBox())!
  expect(last.x + last.width).toBeLessThanOrEqual(1280)
  expect(last.y).toBeLessThan(44 + 40)
})
