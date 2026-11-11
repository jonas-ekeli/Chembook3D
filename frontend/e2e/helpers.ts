import { cpSync, mkdirSync, mkdtempSync } from 'node:fs'
import { basename, join } from 'node:path'
import { expect, test, type Locator, type Page } from '@playwright/test'

// Helpers shared by the UI tests. Each test that opens the demo gets its own copy of it, so a
// test that changes the demo, or fails half way, cannot change what a later test sees.

export const E2E_DIR = process.env.E2E_DIR!

/** The demo investigation as scripts/make_demo.py made it (global-setup.ts). Tests never open
 * it: openDemo opens a copy. */
export const DEMO_TEMPLATE = join(E2E_DIR, 'demo-template')

/** The same demo with branch A's transition state as water, another molecule than its neighbours
 * (make_demo.py --mismatch): for the tests of what the app says when atoms do not match. */
export const DEMO_MISMATCH_TEMPLATE = join(E2E_DIR, 'demo-mismatch-template')

// Present only while an app has the investigation open (P22, investigation.py's LOCK_NAME and
// OWNER_NAME); a copy must not carry them.
const NOT_COPIED = new Set(['.chembook3d.lock', '.chembook3d.owner'])

const copies = new WeakMap<object, string>()

/**
 * The running test's own copy of the demo, made the first time the test asks for it. Later calls
 * in the same test (also from its hooks) give the same folder, so a test can open it again;
 * `template` counts only in the first.
 */
export function demoFolder(template: string = DEMO_TEMPLATE): string {
  const info = test.info()
  let folder = copies.get(info)
  if (folder === undefined) {
    const parent = join(E2E_DIR, 'demo-copies')
    mkdirSync(parent, { recursive: true })
    folder = join(mkdtempSync(join(parent, 'test-')), 'demo')
    cpSync(template, folder, { recursive: true, filter: (source) => !NOT_COPIED.has(basename(source)) })
    copies.set(info, folder)
  }
  return folder
}

/**
 * In the folder dialog (Open, New), goes to `folder` and waits until its listing is drawn.
 * Before that, Open or Create would act on the folder listed before.
 */
export async function browseTo(page: Page, folder: string) {
  const dialog = page.getByRole('dialog')
  const list = dialog.getByRole('list', { name: 'Folders' })
  // The first listing must be in before a path is typed, or it would replace it.
  await expect(list).toBeVisible()
  const listed = page.waitForResponse((response) => {
    const url = new URL(response.url())
    return url.pathname === '/api/folders' && url.searchParams.get('path') === folder
  })
  await dialog.getByLabel('Folder path').fill(folder)
  await dialog.getByRole('button', { name: 'Go' }).click()
  const listing = (await (await listed).json()) as { entries: { name: string }[] }
  await expect(dialog.getByLabel('Folder path')).toHaveValue(folder)
  const names = listing.entries.map((entry) => entry.name)
  if (names.length) await expect(list.getByRole('listitem')).toContainText(names)
  else await expect(list.getByText('No subfolders')).toBeVisible()
}

/** Opens the investigation in `folder` from the start page, as a user would. */
export async function openFolder(page: Page, folder: string) {
  await startPage(page)
  await page.getByRole('button', { name: /^Open/ }).first().click()
  await browseTo(page, folder)
  await page.getByRole('dialog').getByRole('button', { name: 'Open', exact: true }).click()
  await expect(page.getByRole('dialog')).toHaveCount(0)
}

/**
 * Opens the test's own copy of the demo (demoFolder) in kcal/mol and waits until its canvas and
 * energies are drawn. Returns the folder.
 */
export async function openDemo(page: Page, template: string = DEMO_TEMPLATE): Promise<string> {
  const folder = demoFolder(template)
  // The demo's values in the tests are in kcal/mol, the default unit; app settings are shared by
  // all the tests, and one may have left another unit set.
  await page.request.put('/api/settings', { data: { energy_unit: 'kcal/mol' } })
  const energies = page.waitForResponse((response) => new URL(response.url()).pathname === '/api/energies/view')
  await openFolder(page, folder)
  await expect(canvasNode(page, 'T-S0')).toBeVisible()
  // The energies arrive last; until they are drawn, the canvas may still redraw and drop a click.
  await (await energies).finished()
  await pageSettled(page)
  return folder
}

/** Makes a new investigation called `name` in E2E_DIR from the start page, and opens it. */
export async function newInvestigation(page: Page, name: string) {
  await startPage(page)
  await page.getByRole('button', { name: /^New/ }).first().click()
  await browseTo(page, E2E_DIR)
  const dialog = page.getByRole('dialog')
  await dialog.getByLabel('Investigation name').fill(name)
  await dialog.getByRole('button', { name: 'Create' }).click()
  await expect(page.locator('.investigation')).toHaveText(name)
}

/** Names a custom basis set or dispersion in the import preview, as typing it and Enter do. */
export async function nameCustom(dialog: Locator, label: string, value: string) {
  const field = dialog.getByLabel(label)
  await field.fill(value)
  await field.press('Enter')
}

/** D180: New and Open are on the start page, and in the investigation menu once one is open;
 * this shows the start page, closing whatever investigation an earlier test left open. */
export async function startPage(page: Page) {
  await page.request.post('/api/investigations/close')
  await page.goto('/')
}

/**
 * D180: the investigation's own commands (New, Open, Close, Sync with GitHub, Share read-only copy,
 * Basis sets, Shut down) are in the menu on its name in the app bar. This opens the menu and
 * chooses one.
 */
export async function investigationCommand(page: Page, name: string) {
  await page.locator('header .investigation').click()
  await page.getByRole('menu', { name: 'Investigation' }).getByRole('menuitem', { name, exact: true }).click()
}

/** D180: the Overview is what the side panel shows with nothing selected; a click on an empty
 * spot of the canvas clears the selection. */
export async function showOverview(page: Page) {
  const pane = page.locator('.react-flow__pane')
  const box = (await pane.boundingBox())!
  // The first spot, scanning in from the top left, where the pane itself is under the pointer.
  const spot = await pane.evaluate((element, { width, height }) => {
    const { left, top } = element.getBoundingClientRect()
    for (let y = 20; y < height - 20; y += 40)
      for (let x = 20; x < width - 20; x += 40)
        if (document.elementFromPoint(left + x, top + y) === element) return { x, y }
    return null
  }, box)
  await pane.click({ position: spot! })
  await expect(page.getByLabel('Overview', { exact: true }).getByRole('heading', { name: 'Overview' })).toBeVisible()
}

/** A node's card on the canvas. */
export function canvasNode(page: Page, label: string): Locator {
  return page.getByRole('group', { name: `Node ${label}`, exact: true })
}

/**
 * Fits the canvas to its contents and waits until `target` is in view.
 *
 * A new group is placed right of its members, which for the last step column is past the
 * canvas's right edge (under the inspector), and it grows to the right when expanded or laid out
 * in a row. Playwright would scroll the canvas to reach something there, but React Flow scrolls
 * its wrapper straight back, so the click lands on whatever is then under the pointer: often the
 * pane, which clears the selection and leaves the action undone. Fit the view first, as a user
 * would bring it into sight, and click only once the target is in view (short of sub-pixel
 * rounding).
 */
export async function bringIntoView(page: Page, target: Locator) {
  await page.getByRole('button', { name: 'Fit View', exact: true }).click()
  await expect(target).toBeInViewport({ ratio: 0.99 })
}

/** Clicks something on the canvas once it is in view (see bringIntoView). */
export async function pressInView(page: Page, target: Locator, options?: Parameters<Locator['click']>[0]) {
  await bringIntoView(page, target)
  await target.click(options)
}

/** Drags `handle` by (dx, dy) with several mouse moves, as a real drag: React Flow ignores the
 * move that starts it. */
export async function dragBy(page: Page, handle: Locator, dx: number, dy: number) {
  const box = (await handle.boundingBox())!
  const x = box.x + box.width / 2
  const y = box.y + box.height / 2
  await page.mouse.move(x, y)
  await page.mouse.down()
  await page.mouse.move(x + dx, y + dy, { steps: 8 })
  await page.mouse.up()
}

/** A promise the test resolves itself, to hold an answer back until it chooses. */
export function deferred() {
  let resolve!: () => void
  const promise = new Promise<void>((done) => {
    resolve = done
  })
  return { promise, resolve }
}

/**
 * Waits until the page has done everything it had queued and has drawn a frame since. Call it once
 * the answer that would cause a change has arrived (page.waitForResponse, then
 * response.finished()), before asserting that the change did not happen: whatever the app does
 * with that answer is then on the screen, and any request it makes in turn has been sent.
 */
export async function pageSettled(page: Page) {
  await page.evaluate(
    () => new Promise<void>((resolve) => requestAnimationFrame(() => requestIdleCallback(() => resolve()))),
  )
}
