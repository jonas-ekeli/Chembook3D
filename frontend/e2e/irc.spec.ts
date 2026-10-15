import { join } from 'node:path'
import { expect, test, type Page } from '@playwright/test'

const E2E_DIR = process.env.E2E_DIR!
// A TS with its IRC both ways and a job on the forward end point, written by global-setup.ts
const IRC = join(E2E_DIR, 'irc')

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

test("a TS node's IRC is imported, inspected and removed in its own window", async ({ page }) => {
  // D131, T-UI-34
  test.slow()
  await newInvestigation(page, 'IRC test')
  await page.getByRole('button', { name: 'Import file…' }).click()
  let dialog = page.getByRole('dialog')
  await dialog.getByLabel('Upload a file').setInputFiles(join(IRC, 'TS.out'))
  await dialog.getByRole('button', { name: 'Import', exact: true }).click()
  const inspector = page.getByLabel('Node inspector')
  await expect(inspector.getByRole('heading', { name: 'TS' })).toBeVisible()
  await expect(inspector.getByText('2 calculations', { exact: true })).toBeVisible()
  await expect(inspector.getByLabel('IRC', { exact: true })).toContainText('IRC: none yet')
  await inspector.getByRole('button', { name: 'IRC…' }).click()

  const window = page.getByRole('dialog', { name: 'IRC of “TS”' })
  await expect(window.getByText('Import the IRC outputs to see the path.')).toBeVisible()
  // The forward IRC, picked from this computer.
  await window.getByLabel('Forward direction').getByRole('button', { name: 'Import IRC…' }).click()
  dialog = page.getByRole('dialog', { name: 'Add an IRC path to the IRC of “TS”' })
  await expect(dialog.getByLabel('Folder path')).not.toHaveValue('')
  await dialog.getByLabel('Folder path').fill(IRC)
  await dialog.getByRole('button', { name: 'Go' }).click()
  await dialog.getByRole('button', { name: /irc-forward\.out/ }).click()
  await expect(dialog.getByText('The node itself does not change')).toBeVisible()
  await expect(dialog.getByText('Added to the IRC')).toBeVisible()
  await dialog.getByRole('button', { name: 'Import', exact: true }).click()
  const forward = window.getByLabel('Forward direction')
  await expect(forward.getByText('irc-forward.out')).toBeVisible()
  await expect(forward.getByRole('heading', { level: 3 })).toContainText('6 points, stopped at its maximum of 10 points')

  // The reverse IRC, uploaded.
  await window.getByLabel('Reverse direction').getByRole('button', { name: 'Import IRC…' }).click()
  dialog = page.getByRole('dialog', { name: 'Add an IRC path to the IRC of “TS”' })
  await dialog.getByLabel('Upload a file').setInputFiles(join(IRC, 'irc-reverse.out'))
  await dialog.getByRole('button', { name: 'Import', exact: true }).click()
  const reverse = window.getByLabel('Reverse direction')
  await expect(reverse.getByRole('heading', { level: 3 })).toContainText('5 points, reached a minimum')

  // The path through the TS, left to right, with its points played in the 3D view.
  await expect(window.getByRole('img', { name: 'IRC energy along the path' })).toBeVisible()
  const shown = window.getByLabel('Point shown')
  await expect(shown).toContainText('Reverse point 5')
  await window.getByLabel('Point', { exact: true }).fill('5')
  await expect(shown).toContainText('TS · s = 0.00 amu½·bohr')
  await window.getByRole('button', { name: 'Next structure' }).click()
  await expect(shown).toContainText('Forward point 1')
  await expect(shown).toContainText('ΔE = -0.25 kcal/mol')
  await window.getByLabel('Structures shown').selectOption({ label: 'Every structure of irc-reverse.out' })
  await expect(shown).toContainText('Structure 1 of')

  // The job on the forward end point, with its energies relative to the TS.
  await forward.getByRole('button', { name: 'Import end-point job…' }).click()
  dialog = page.getByRole('dialog', { name: 'Add a job on the forward end point to the IRC of “TS”' })
  await dialog.getByLabel('Upload a file').setInputFiles(join(IRC, 'end-forward.out'))
  await dialog.getByRole('button', { name: 'Import', exact: true }).click()
  await expect(forward.getByText('Optimized, starting 0.000 Å from the last IRC point; no imaginary frequency.')).toBeVisible()
  const energies = window.getByLabel('IRC energies')
  await expect(energies.getByRole('row', { name: /Forward end point, optimized/ })).toContainText('-12.55')
  await expect(energies.getByRole('row', { name: /Forward IRC end/ })).toContainText('-9.04')

  // Removing a file is the undo of its import.
  await reverse.getByRole('button', { name: 'Remove…' }).click()
  await reverse.getByRole('button', { name: 'Remove', exact: true }).click()
  await expect(reverse.getByText('irc-reverse.out')).toHaveCount(0)
  await expect(shown).toContainText('TS')
  await window.getByRole('button', { name: 'Close' }).click()

  // The node is as it was: its own calculations only, and one line about its IRC.
  await expect(inspector.getByLabel('IRC', { exact: true })).toContainText('IRC: forward ✓, reverse –, end points 1 of 2')
  await expect(inspector.getByText('2 calculations', { exact: true })).toBeVisible()
})
