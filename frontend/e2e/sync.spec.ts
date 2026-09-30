import { execFileSync } from 'node:child_process'
import { mkdirSync, writeFileSync } from 'node:fs'
import { join } from 'node:path'
import { expect, test, type Page } from '@playwright/test'

const E2E_DIR = process.env.E2E_DIR!

function git(cwd: string, ...args: string[]) {
  execFileSync('git', ['-c', 'user.name=Test', '-c', 'user.email=test@example.com', ...args], { cwd })
}

/** An empty repository, standing in for a new private one on GitHub. */
function emptyRepository(name: string): string {
  const path = join(E2E_DIR, name)
  mkdirSync(path)
  git(path, 'init', '-q', '--bare')
  git(path, 'symbolic-ref', 'HEAD', 'refs/heads/main')
  return path
}

async function chooseFolder(page: Page, parent: string, name: string, button: string) {
  const dialog = page.getByRole('dialog')
  await expect(dialog.getByRole('list', { name: 'Folders' })).toBeVisible()
  await dialog.getByLabel('Folder path').fill(parent)
  await dialog.getByRole('button', { name: 'Go' }).click()
  await expect(dialog.getByLabel('Folder path')).toHaveValue(parent)
  await dialog.getByLabel('Investigation name').fill(name)
  await dialog.getByRole('button', { name: button }).click()
}

async function newInvestigation(page: Page, name: string) {
  await page.goto('/')
  await page.getByRole('button', { name: /^New/ }).first().click()
  await chooseFolder(page, E2E_DIR, name, 'Create')
  await expect(page.locator('.investigation')).toHaveText(name)
}

test('link to an empty repository, sync, and keep a copy when both changed', async ({ page }) => {
  // FR-SYNC-02, 05, 06; D71
  const remote = emptyRepository('sync-remote.git')
  await newInvestigation(page, 'Synced')
  await page.getByRole('button', { name: 'Sync with GitHub…' }).click()
  const link = page.getByRole('dialog', { name: 'Sync with GitHub' })
  await link.getByLabel('Repository address').fill(remote)
  await link.getByRole('button', { name: 'Link and push' }).click()
  const sync = page.getByRole('button', { name: 'Sync with GitHub' })
  await expect(sync).toHaveText('Sync · Up to date')

  await page.request.post('/api/nodes', { data: { label: 'made here' } })
  await sync.click()
  await expect(sync).toHaveText('Sync · Up to date')
  await expect(page.locator('.banner')).toContainText('Up to date with GitHub')

  // Another computer pushes while this one has an unsynced change.
  await page.request.post('/api/nodes', { data: { label: 'not pushed' } })
  const other = join(E2E_DIR, 'sync-other')
  execFileSync('git', ['clone', '-q', remote, other])
  writeFileSync(join(other, 'note.txt'), 'from the other computer\n')
  git(other, 'add', 'note.txt')
  git(other, 'commit', '-q', '-m', 'Sync from the other computer')
  git(other, 'push', '-q', 'origin', 'HEAD')

  await sync.click()
  const conflict = page.getByRole('dialog', { name: 'This computer and GitHub both have changes' })
  await expect(conflict.getByRole('row', { name: /GitHub/ })).toContainText('Sync from the other computer')
  await conflict.getByRole('button', { name: "Keep this computer's copy" }).click()
  await expect(conflict).toHaveCount(0)
  await expect(page.locator('.investigation')).toHaveText('Synced')
  await expect(sync).toHaveText('Sync · Up to date')
  await expect(page.getByRole('group', { name: 'Node not pushed', exact: true })).toBeVisible()
})

test('open an investigation from GitHub into a new folder', async ({ page }) => {
  // FR-SYNC-03
  const remote = emptyRepository('clone-remote.git')
  await newInvestigation(page, 'Original')
  await page.request.post('/api/nodes', { data: { label: 'first node' } })
  await page.getByRole('button', { name: 'Sync with GitHub…' }).click()
  const link = page.getByRole('dialog', { name: 'Sync with GitHub' })
  await link.getByLabel('Repository address').fill(remote)
  await link.getByRole('button', { name: 'Link and push' }).click()
  await expect(page.getByRole('button', { name: 'Sync with GitHub' })).toHaveText('Sync · Up to date')
  await page.getByRole('button', { name: 'Close', exact: true }).click()

  await page.getByRole('button', { name: 'Open from GitHub…' }).click()
  const address = page.getByRole('dialog', { name: 'Open from GitHub' })
  await address.getByLabel('Repository address').fill(remote)
  await address.getByRole('button', { name: 'Choose folder…' }).click()
  await expect(page.getByRole('dialog').getByLabel('Investigation name')).toHaveValue('clone-remote')
  await chooseFolder(page, E2E_DIR, 'cloned', 'Copy from GitHub')
  await expect(page.locator('.investigation')).toHaveText('Original')
  await expect(page.locator('.investigation')).toHaveAttribute('title', join(E2E_DIR, 'cloned'))
  await expect(page.getByRole('group', { name: 'Node first node', exact: true })).toBeVisible()
  await expect(page.getByRole('button', { name: 'Sync with GitHub' })).toHaveText('Sync · Up to date')
})
