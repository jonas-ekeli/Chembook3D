import { execFileSync } from 'node:child_process'
import { mkdirSync, readFileSync, writeFileSync } from 'node:fs'
import { join } from 'node:path'
import { expect, test } from '@playwright/test'

// T-UI-19, D115: a scan path that came back from its cloud session is imported by itself as a
// node between its two ends, and the Cloud jobs list shows where each job stands.
const E2E_DIR = process.env.E2E_DIR!

const START = [
  ['O', 0.0, 0.0, 0.1173],
  ['H', 0.0, 0.7572, -0.4692],
  ['H', 0.0, -0.7572, -0.4692],
] as const
const END = [
  ['O', 0.0, 0.0, 0.1173],
  ['H', 0.0, 0.9, -0.2],
  ['H', 0.0, -0.7572, -0.4692],
] as const

function xyz(atoms: readonly (readonly [string, number, number, number])[], comment = ''): string {
  return `${atoms.length}\n${comment}\n${atoms.map(([e, x, y, z]) => `${e} ${x.toFixed(6)} ${y.toFixed(6)} ${z.toFixed(6)}`).join('\n')}\n`
}

/** Five structures from START to END with a top in the middle, as pathtools.py joins them. */
function path(): string {
  const energies = [-5.07, -5.06, -5.04, -5.05, -5.06]
  const call = 'xtb start.xyz --opt --input scan.inp --gfn 2 --chrg 0 --uhf 0'
  return energies
    .map((energy, i) => {
      const t = i / (energies.length - 1)
      const rows = START.map(([e, ...a], k) => [e, ...a.map((v, c) => v + t * (END[k][c + 1] as number - v))] as const)
      return xyz(rows as unknown as [string, number, number, number][], ` energy: ${energy.toFixed(10)} stage: 1 call: ${call}`)
    })
    .join('')
}

function git(cwd: string, ...args: string[]) {
  execFileSync('git', ['-c', 'user.name=Cloud', '-c', 'user.email=cloud@example.com', ...args], { cwd })
}

test('a finished scan path is imported by itself and listed under Cloud jobs', async ({ page }) => {
  test.slow() // a git push, a fetch and an import on a slow Windows runner
  const remote = join(E2E_DIR, 'scan-path-remote.git')
  mkdirSync(remote)
  git(remote, 'init', '-q', '--bare')
  git(remote, 'symbolic-ref', 'HEAD', 'refs/heads/main')

  await page.goto('/')
  await page.getByRole('button', { name: /^New/ }).first().click()
  const dialog = page.getByRole('dialog')
  await expect(dialog.getByRole('list', { name: 'Folders' })).toBeVisible()
  await dialog.getByLabel('Folder path').fill(E2E_DIR)
  await dialog.getByRole('button', { name: 'Go' }).click()
  await dialog.getByLabel('Investigation name').fill('Scan path jobs')
  await dialog.getByRole('button', { name: 'Create' }).click()
  await expect(page.locator('.investigation')).toHaveText('Scan path jobs')
  expect((await page.request.post('/api/sync/link', { data: { url: remote } })).ok()).toBe(true)

  const ids: string[] = []
  for (const [label, atoms, x] of [['A', START, 100], ['B', END, 700]] as const) {
    const response = await page.request.post('/api/nodes', {
      data: { label, xyz: xyz(atoms), charge: 0, multiplicity: 1, pos_x: x, pos_y: 100 },
    })
    ids.push(((await response.json()) as { id: string }).id)
  }
  expect((await page.request.post('/api/transitions', { data: { source_id: ids[0], target_id: ids[1] } })).ok()).toBe(true)
  const sent = await page.request.post('/api/scan-paths', {
    data: { start_id: ids[0], end_id: ids[1], pairs: [], held: [], solvent: null, start: false },
  })
  expect(sent.ok()).toBe(true)
  const jobId = ((await sent.json()) as { job: { id: string } }).job.id

  // As if its cloud session had started (D93) and then pushed its result to a branch.
  const folder = ((await (await page.request.get('/api/investigation')).json()) as { folder: string }).folder
  const record = join(folder, 'jobs', jobId, 'job.json')
  const job = JSON.parse(readFileSync(record, 'utf-8')) as Record<string, unknown>
  writeFileSync(
    record,
    JSON.stringify({ ...job, state: 'started', session_id: 'session_01E2EScanPath', session_url: 'https://claude.ai/code/session_01E2EScanPath' }),
  )
  const cloud = join(E2E_DIR, 'scan-path-cloud')
  git(E2E_DIR, 'clone', '-q', remote, cloud)
  git(cloud, 'checkout', '-q', '-b', 'claude/scan-path')
  mkdirSync(join(cloud, 'jobs', jobId, 'outputs'), { recursive: true })
  writeFileSync(join(cloud, 'jobs', jobId, 'outputs', 'path.xyz'), path())
  writeFileSync(
    join(cloud, 'jobs', jobId, 'result.json'),
    JSON.stringify({ job: jobId, status: 'done', summary: 'One scan of the O–H distance reached the end.', outputs: [] }),
  )
  git(cloud, 'add', '-A')
  git(cloud, 'commit', '-q', '-m', 'Scan path')
  git(cloud, 'push', '-q', 'origin', 'claude/scan-path')

  // Opening the investigation checks at once (then every few minutes).
  await page.reload()
  await expect(page.getByRole('status').filter({ hasText: 'Scan path imported as “Path A to B”' })).toBeVisible({
    timeout: 30_000,
  })
  await expect(page.getByRole('group', { name: 'Node Path A to B', exact: true })).toBeVisible()

  await page.getByRole('button', { name: 'Cloud jobs' }).click()
  const jobs = page.getByRole('dialog', { name: 'Cloud jobs' })
  const row = jobs.getByRole('row', { name: /A to B/ })
  await expect(row).toContainText('Scan path from “A” to “B”')
  await expect(row).toContainText('Imported')
  await expect(row).toContainText('One scan of the O–H distance reached the end.')
  await expect(row.getByRole('link', { name: 'Open' })).toHaveAttribute('href', 'https://claude.ai/code/session_01E2EScanPath')
  await row.getByRole('button', { name: 'Show node' }).click()
  await expect(jobs).toHaveCount(0)
  await expect(page.getByLabel('Node inspector')).toBeVisible()
  await expect(page.getByLabel('Node inspector').getByRole('heading', { name: 'Path A to B' })).toBeVisible()
})
